"""
ZeroLang type checker

Type definitions and type checking pass for the AST.
"""

import struct as _struct
from enum import IntEnum, unique
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@unique
class ZTypeType(IntEnum):
    """
    TypeType - types of types
    """

    NULL = 0  # function that returns nothing
    GENERIC_PARAM = 3  # a generic type parameter (e.g., t in t: Any.generic)

    # user defined types
    UNIT = 50
    FUNCTION = 51
    RECORD = 52
    CLASS = 53
    VARIANT = 54
    UNION = 55
    ENUM = 56
    PROTOCOL = 57
    FACET = 58

    DATA = 60  # constant array data
    TAG = 61  # tag discriminator type

    # system types (set during resolution of native types from system.z)
    NEVER = 70  # never type (non-completing expression)


@unique
class ZSubType(IntEnum):
    """Sub-classification for system types that share a ZTypeType.

    For example, string is a CLASS but needs special handling for
    memory management. The subtype distinguishes it without changing
    the typetype (so all CLASS-level checks still work).
    """

    NONE = 0
    STRING = 1  # string class — z_string_t* with z_string_free destructor
    STRINGVIEW = 2  # stringview class — z_stringview_t (borrowed view of bytes)


@unique
class ZControlKind(IntEnum):
    """Identifies compiler control flow functions"""

    NONE = 0
    RETURN = 1
    BREAK = 2
    CONTINUE = 3
    ERROR = 4
    PANIC = 5


@unique
class ZBuiltinFunc(IntEnum):
    """Identifies native functions that need special emitter handling
    beyond standard mangled-name dispatch (e.g. extra header includes,
    runtime-error plumbing). Stamped on the function's ZType at
    typecheck time; read by zemitterc to decide what to pull in.
    """

    NONE = 0
    PARSE_F64 = 1  # stringview.parseF64 — needs errno.h / stdlib.h (strtod)
    ENV_NAMES = 2  # os.envNames — needs string.h (strchr / strlen)


@unique
class ZOwnership(IntEnum):
    """
    Ownership

    OWNED: the variable owns the instance and is responsible for its lifetime.
    BORROWED: the variable has a temporary reference; it does not own the instance.
    """

    OWNED = 0
    BORROWED = 1


@unique
class ZLockState(IntEnum):
    """
    Lock state — set on `ZLockInfo.lock_type` when an entry holds a lock.
    Absence of a lock is represented by `ZEntry.lock = None`.

    EXCLUSIVE: exclusive lock, no other references allowed.
    SHARED: shared lock, other shared references allowed but no mutation.
    """

    EXCLUSIVE = 1
    SHARED = 2


@unique
class ZParamOwnership(IntEnum):
    """
    Parameter ownership annotation for function parameters and return types (v2)

    TAKE: caller transfers ownership to callee (default for owned params).
    BORROW: callee gets a borrowed reference; caller retains ownership.
    LOCK: callee locks the argument for the duration of the call.
    """

    TAKE = 0
    BORROW = 1
    LOCK = 2


# module-level counters for auto-incrementing IDs
_next_type_id: int = 0


def _alloc_type_id() -> int:
    """Allocate the next auto-incrementing type ID."""
    global _next_type_id
    tid = _next_type_id
    _next_type_id += 1
    return tid


# global registry of ZType objects, keyed by type_id. Populated by
# ZType.__post_init__ so every constructed ZType is reachable by its
# integer key. Used by cross-type ID refs (parent_id today; intended to
# generalise to return_type, generic_origin, etc.) so the in-memory type
# graph mirrors the SQL-friendly id-only form the bootstrap-lint enforces.
_types_by_id: "dict[int, ZType]" = {}


def _type_by_id(tid: int) -> "Optional[ZType]":
    """Look up a ZType by its type_id, or None if unknown."""
    return _types_by_id.get(tid)


# monotonic counter for child-name identities on ZType. Globally unique
_next_child_id: int = 0


def _alloc_child_id() -> int:
    """Allocate the next auto-incrementing child ID."""
    global _next_child_id
    cid = _next_child_id
    _next_child_id += 1
    return cid


@dataclass
class ZType:
    """
    ZType - describes a type

    For functions, children contains parameters.
    The return type is stored in the dedicated return_type field.

    For records, children contains fields and methods.

    For units, children contains the unit's definitions.

    Per-(parent, child-name) metadata such as param ownership
    annotations and field defaults lives on ZTypeChild rows in the
    `ZTyping.type_child` table; ZType only carries type-identity data.

    is_valtype indicates whether this type is a value type (records,
    numerics, enums, variants) vs a reference type (classes, unions).
    Value types are copied on assignment; reference types have ownership
    semantics.
    """

    type_id: int = field(default_factory=_alloc_type_id, init=False)

    name: str
    typetype: ZTypeType
    subtype: ZSubType = ZSubType.NONE

    # Upper bound on a synthetic GENERIC_PARAM marker. Carries one of:
    #   - generic-param constraint (the bound in `<T: SomeConstraint>`)
    #   - typedef wrapper marker's base type
    #   - as-block custom tag-data type
    bound_id: Optional[int] = field(default=None, init=False)

    # Set on tag RECORDs (typetype=RECORD, is_tag_generic_origin or name
    # starts with "tag__"): the DATA type this tag RECORD belongs to.
    data_owner_id: Optional[int] = field(default=None, init=False)

    # Set on FUNCTION ZTypes that are a type's method (via _set_child): the
    # type_id of the enclosing record/class/etc. -1 when none (top-level
    # function). Lets the emitter recover a method's enclosing type by id
    # instead of re-resolving the enclosing name.
    enclosing_type_id: int = field(default=-1, init=False)

    # parallel name→id map for children. Lazily populated by child_id_for;
    # never pre-seeded. Globally-unique ids consumed by `ZTyping.type_child`
    # rows and by narrowing entries that reference a child by id rather
    # than by string.
    children_id_map: "dict[str, int]" = field(default_factory=dict, init=False)

    # for function types
    return_type: "Optional[ZType]" = field(default=None, init=False)
    # ownership annotation on the return type (if any)
    return_ownership: "Optional[ZParamOwnership]" = field(default=None, init=False)
    # name of the parameter whose declared TYPE was 'this' (receiver)
    this_param_name: "Optional[str]" = field(default=None, init=False)

    isgeneric: bool = False

    is_valtype: Optional[bool] = field(default=None, init=False)

    # generic type parameters: param name -> constraint ZType (for template types)
    generic_params: "dict[str, ZType]" = field(default_factory=dict, init=False)

    # for monomorphized types: points to the original template type.
    # None for non-monomorphized types AND for variant-tag discriminator
    # types — the latter are flagged separately via `is_tag_generic_origin`.
    generic_origin: "Optional[ZType]" = field(default=None, init=False)
    # variant-tag discriminator marker
    is_tag_generic_origin: bool = field(default=False, init=False)

    # names of generic params that are numeric (constraint is a numeric type)
    numeric_generic_params: "set[str]" = field(default_factory=set, init=False)

    # default types for generic params: param name → default ZType
    generic_defaults: "dict[str, ZType]" = field(default_factory=dict, init=False)

    # compile-time constant value. Carries either:
    #   - the literal value of an as-block constant ('max: 1024'), or
    #   - the integer value of a numeric generic-arg carrier (e.g.
    #     the '8' wrapper in 'array<i64, 8>') used both for emit and
    #     as a monomorphization-key discriminator.
    const_value: "Optional[int | float | str]" = field(default=None, init=False)

    # for typedef types: points to the immediate base type being wrapped
    typedef_base: "Optional[ZType]" = field(default=None, init=False)

    # memory management metadata (set by type checker after resolution).
    # `destructor_name is not None` is the authoritative "needs destructor"
    # signal
    destructor_name: Optional[str] = field(default=None, init=False)
    is_heap_allocated: bool = field(default=False, init=False)

    # True when the type has owned fields requiring cleanup (string, list, box,
    # map, or other types with destructors). Set after children are resolved.
    needs_field_cleanup: bool = field(default=False, init=False)

    # nullable pointer option: monomorphized option(reftype) emitted as bare pointer
    is_nullable_ptr: bool = field(default=False, init=False)

    # box type: monomorphized box(valtype) emitted as heap-allocated pointer
    # For box(reftype), the box is transparent (passthrough to inner type)
    is_box: bool = field(default=False, init=False)

    # native type: compiler-provided implementation (system types like i64, string, etc.)
    is_native: bool = field(default=False, init=False)

    # compiler control kine functions (return|break|continue etc)
    control_kind: ZControlKind = field(default=ZControlKind.NONE, init=False)

    # native functions that need special emitter handling
    # (extra header includes etc); stamped in _resolve_function_type.
    builtin_func: ZBuiltinFunc = field(default=ZBuiltinFunc.NONE, init=False)

    # public/private access control: maps external name -> internal name for
    # publicly accessible members. None = all-public (default). Set during type
    # resolution when public: unit { ... } is declared in the as block.
    public_members: "Optional[dict[str, str]]" = field(default=None, init=False)

    # True if the type's 'create' method is disabled — either by the user
    # writing 'create: null' in the 'as' block, or by the compiler for types
    # where bare-name construction is not meaningful (unions and variants
    # require subtype selection). When set, the unified call dispatch reports
    # a targeted error instead of falling through to 'cannot call' generic.
    create_disabled: bool = field(default=False, init=False)

    # auto-generated equality: True when == and != are compiler-synthesized
    # (structural equality for records, tag+payload for variants)
    is_autogen_eq: bool = field(default=False, init=False)

    # simple equality: True when byte representation fully determines equality
    # (no floats, no user overrides recursively). Emission strategy (memcmp vs
    # field-by-field) is decided by the emitter based on estimated type size.
    is_simple_eq: bool = field(default=False, init=False)

    # compiler-generated raw allocator for this type
    meta_create: Optional["ZType"] = field(default=None, init=False)

    # internal marker: this ZType is a placeholder produced when the
    # type checker first encounters a `zast.TypeOfExpr` (an AST node
    # the generator desugarer emits for promoted-local field types in
    # synth classes). The placeholder is bound as the class's field
    # type until the first `this.field = <rhs>` reassignment in the
    # synth `.call` body, where `_check_reassignment_inner` swaps
    # every reference to the resolved RHS type. Carries the source
    # TypeOfExpr's nodeid so the typechecker can update
    # `node_type[<that-nodeid>]` at swap time. Never set on types
    # that originate from user-written source.
    is_typeof_placeholder: bool = field(default=False, init=False)
    typeof_source_nodeid: int = field(default=-1, init=False)

    # compiler-internal "literal type" marker. True on the
    # LITERAL_INT / LITERAL_FLOAT singletons: provisional types worn
    # by bare numeric literals (and constant-folded results) until a
    # surrounding typed location coerces them, or the
    # default-resolution late pass folds them to i64/u64/f64. Never
    # set on types reachable from user source.
    is_literal: bool = field(default=False, init=False)

    # element type for DATA types. The DATA's children
    # are value-carrier RECORDs whose `name` is the literal value (e.g.
    # the children of `primes: data { 2 3 5 }` are RECORDs with names
    # "2", "3", "5", not the numeric type itself); element_type is the
    # only authoritative pointer to the underlying numeric ZType.
    element_type: Optional["ZType"] = field(default=None, init=False)

    # For DATA-typed ZTypes: label -> literal value string. Carries the
    # per-element value so the custom-tag layer can detect duplicates
    # without relying on child names (children are now typed as the
    # element type, so their names no longer carry the literal value).
    data_values: "Dict[str, str]" = field(default_factory=dict, init=False)

    # For DATA-typed ZTypes: set True by the typechecker when any access
    # to the block requires the runtime static array — variable
    # indexing (`.index <var>`), iteration, `.array` materialisation,
    # passing the block as a value. Foldable accesses (named label,
    # numeric ordinal, `.length`, `.tag`) leave this False. The emitter
    # skips `static const T[]` emission when False — every access
    # carries node_const_value already.
    runtime_indexed: bool = field(default=False, init=False)

    # C identifier for this type (set by type checker, used by emitter)
    # For type definitions: "z_point_t", "z_list_i64_t", etc.
    # For function types: "z_math_add", "z_point_distance", etc.
    cname: str = field(default="", init=False)

    # C identifier base: cname without the trailing "_t" for type definitions
    # ("z_point"); identical to cname for function types. Helper C names derive
    # from it as f"{cname_base}_{suffix}" (e.g. "z_point_meta_create",
    # "z_List_i64_get", "z_point_destroy"). The emitter reads this and never
    # regenerates a C name from a name-string.
    cname_base: str = field(default="", init=False)

    def __post_init__(self) -> None:
        _types_by_id[self.type_id] = self

    def bound_type(self) -> "Optional[ZType]":
        """Resolve the bound_id cross-ref to a ZType, or None if unset."""
        if self.bound_id is None:
            return None
        return _types_by_id.get(self.bound_id)

    def data_owner_type(self) -> "Optional[ZType]":
        """Resolve the data_owner_id cross-ref to a ZType, or None if unset."""
        if self.data_owner_id is None:
            return None
        return _types_by_id.get(self.data_owner_id)

    def child_id_for(self, name: str) -> int:
        """Return the monotonic id for this child name on this type, minting
        one if absent. Stable per ZType instance per process. Does not
        require `name` to currently be present in `children` — the id is an
        identity for the name on this type, independent of whether the child
        entry exists yet.
        """
        cid = self.children_id_map.get(name)
        if cid is None:
            cid = _alloc_child_id()
            self.children_id_map[name] = cid
        return cid

    def __repr__(self) -> str:
        return f"ZType(name={self.name!r}, typetype={self.typetype!r}, cname={self.cname!r}, type_id={self.type_id})"


_next_variable_id: int = 0


def _alloc_variable_id() -> int:
    """Allocate the next auto-incrementing variable ID."""
    global _next_variable_id
    vid = _next_variable_id
    _next_variable_id += 1
    return vid


@unique
class ZScopeKind(IntEnum):
    """Kind of scope in the symbol table."""

    BLOCK = 0  # language construct (function, do, for, if, with, match, arm)
    CALL = 1  # call-scoped lock boundary
    OVERLAY = 2  # per-statement state change


_next_scope_id: int = 0


def _alloc_scope_id() -> int:
    """Allocate the next auto-incrementing scope ID."""
    global _next_scope_id
    sid = _next_scope_id
    _next_scope_id += 1
    return sid


_next_entry_id: int = 0


def _alloc_entry_id() -> int:
    """Allocate the next auto-incrementing entry ID."""
    global _next_entry_id
    eid = _next_entry_id
    _next_entry_id += 1
    return eid


@unique
class ZLockHolderKind(IntEnum):
    """Categorises what kind of entity holds a lock.

    Each kind maps `ZLockHolder.id` to a different id-space:
    - VAR: ZVariable.variable_id (a borrow-binding variable)
    - CALL: AST nodeid of the call expression that acquired the lock
    - FOR: AST nodeid of the for-loop that owns the iteration lock
    """

    VAR = 0
    CALL = 1
    FOR = 2


@dataclass(frozen=True)
class ZLockHolder:
    """Tagged identifier for a lock holder. Replaces the prior free-form
    string (`variable name | "call:{nodeid}" | "__for"`)."""

    kind: ZLockHolderKind
    id: int


@dataclass
class ZLockInfo:
    """Lock state on a variable — stored on ZEntry, not on ZVariable.

    `path` is the addressable lock target as a tuple `(root, f1, f2, ...)`.
    `ZEntry.name` always equals `path[0]` so scope-chain lookup remains a
    simple linear scan keyed by root. The full tuple is consulted to
    apply the prefix-overlap conflict rule.

    `holder` is a tagged `ZLockHolder` distinguishing a borrow-binding
    variable, a call site, or a for-loop sentinel.
    """

    lock_type: ZLockState  # EXCLUSIVE or SHARED
    holder: ZLockHolder
    path: Tuple[str, ...] = ()


@dataclass
class ZEntry:
    """A single entry in a scope's environment.

    Represents either a definition (introduces a name) or a shadow/overlay
    (modifies state of a name from an outer scope).

    `entry_id` is a monotonic per-process identity used as the SQL primary
    key and as the hot-path identity for the scope chain.
    """

    entry_id: int = field(default_factory=_alloc_entry_id, init=False)

    name: str
    ztype: ZType
    is_definition: bool
    # for runtime variables (None for type/function definitions and lock-only overlays)
    var: "Optional[ZVariable]" = None
    # lock state (one lock per variable per scope)
    lock: Optional[ZLockInfo] = None
    # narrowing state (for match/if arms)
    narrowed_subtype: Optional[str] = None  # "ok", "err" — narrowed in match arm
    # id parallel to narrowed_subtype, minted via the outer
    # union/variant's child_id_for(subtype_name).
    narrowed_subtype_id: Optional[int] = None
    excluded_subtypes: "Optional[frozenset[str]]" = None  # subtypes ruled out
    # id parallel to excluded_subtypes; same cardinality by construction.
    excluded_subtype_ids: "Optional[frozenset[int]]" = None
    # original union/variant type when ztype is the narrowed payload — the
    # emitter uses this to generate the C-level unwrap (original is still the
    # storage type, narrowed is the typecheck-visible type).
    original_ztype: "Optional[ZType]" = None
    # taken state
    is_taken: bool = False
    taken_at: Optional[Tuple[int, int, int]] = None


@dataclass
class ZExprResult:
    """Result of checking an expression: the resolved type plus any
    borrow/private intent that the enclosing assignment should consume.

    `borrow_target` is the addressable lock path (root + descents) of the
    source the result borrows from, e.g. `("rec", "field")`. None when
    no borrow lock is pending.
    """

    ztype: Optional[ZType] = None
    borrow_target: Optional[Tuple[str, ...]] = None
    private_access: bool = False


# C reserved words a local variable name must not collide with. A variable
# whose zerolang name is one of these is emitted as `v_<name>`.
_C_RESERVED_WORDS = frozenset(
    (
        "main",
        "break",
        "continue",
        "return",
        "switch",
        "case",
        "default",
        "if",
        "else",
        "for",
        "while",
        "do",
        "int",
        "float",
        "double",
        "char",
        "void",
        "struct",
        "union",
        "enum",
        "static",
        "const",
        "auto",
        "register",
        "extern",
        "volatile",
        "signed",
        "unsigned",
        "long",
        "short",
        "sizeof",
        "typedef",
        "goto",
        "abs",
        "exit",
    )
)


def mangle_var_name(name: str) -> str:
    """The single canonical local-variable name mangler: prefix a C reserved
    word with `v_`, otherwise pass through unchanged. Called once at the
    `define_var` chokepoint to set `ZVariable.cname`; the emitter reads the
    stored cname rather than re-deriving it."""
    if name in _C_RESERVED_WORDS:
        return f"v_{name}"
    return name


def mangle_func_name(name: str) -> str:
    """The single canonical function/global name mangler: `z_` prefix with dots
    flattened to underscores (`main` is the C entry point and keeps its bare
    `z_main`). Emittable functions read their stored `ZType.cname`; this covers
    the residual sites with only a name string (natives, constant/data symbols,
    unresolved-call fallbacks)."""
    if name == "main":
        return "z_main"
    return "z_" + name.replace(".", "_")


@dataclass
class ZVariable:
    """
    ZVariable - per-binding ownership info, attached to ZEntry.var for
    named runtime bindings (parameters, locals, with-bindings, for-loop
    iterators, synth temps). Expression-level results are carried on
    ZExprResult, not here.

    Lock state is tracked via ZEntry.lock in the scope chain, not here.
    """

    variable_id: int = field(default_factory=_alloc_variable_id, init=False)
    ztype: ZType
    ownership: ZOwnership
    # C identifier for this binding, set once at the `define_var` chokepoint via
    # `mangle_var_name`. The emitter reads this rather than re-mangling the name.
    cname: str = field(default="", init=False)
    # private access: variable declared with .private type, bypasses public_members
    is_private_access: bool = False
    # escape-analysis: name of the function-local source this variable
    # borrows from (set on `x: y.borrow`, label-form borrows, and borrowed
    # protocol/facet wrappers). None for parameters (whose ownership is
    # BORROWED by default but whose borrow origin is outside this function).
    borrow_origin: Optional[str] = None
    # provenance: None for variables declared in user source; pass-name string
    # for variables synthesised by a compiler pass. Surfaces in SQL dumps.
    synth_origin: Optional[str] = None


_next_conformance_id: int = 0


def _alloc_conformance_id() -> int:
    """Allocate the next auto-incrementing conformance ID."""
    global _next_conformance_id
    cid = _next_conformance_id
    _next_conformance_id += 1
    return cid


_conformances_by_id: "dict[int, ZConformance]" = {}


def _conformance_by_id(cid: int) -> "Optional[ZConformance]":
    return _conformances_by_id.get(cid)


@dataclass
class ZConformance:
    """A protocol/facet conformance: an impl type satisfying a spec type under
    a label (`impl: record { ... } as { <label>: <spec> }`). A first-class
    entity (own monotonic id, dumpable to SQL) so the C names of the generated
    conformance helpers are computed once here and read by the emitter, not
    rebuilt inline. Stores ids (not ZTypes) per the id-keyed convention; the
    helper C names are pre-composed off the impl type's `cname_base`."""

    impl_type_id: int
    spec_type_id: int
    label: str
    is_facet: bool
    conformance_id: int = field(default_factory=_alloc_conformance_id, init=False)
    # spec method name -> the wrapper function's C name. Stored as strings (not
    # synth ZTypes) so recording a conformance allocates no type_id and cannot
    # perturb collision-suffix (`_{type_id}`) naming of other types.
    method_wrapper_cnames: Dict[str, str] = field(default_factory=dict, init=False)
    # pre-composed helper C names (these helpers have no ZType of their own)
    vtable_cname: str = ""
    create_cname: str = ""
    create_owned_cname: str = ""
    destroy_cname: str = ""

    def __post_init__(self) -> None:
        _conformances_by_id[self.conformance_id] = self


# Numeric type suffix sets used both by `parse_number` (to peel an
# inline suffix off the lexeme) and by `numeric_literal_form` (to
# detect whether a literal carries an explicit suffix vs. is bare).
_NUMERIC_SUFFIXES_4 = ("i128", "u128", "f128")
_NUMERIC_SUFFIXES_3 = ("i16", "i32", "i64", "u16", "u32", "u64", "f32", "f64", "c32")
_NUMERIC_SUFFIXES_2 = ("i8", "u8", "c8")


def numeric_literal_form(numstr: str) -> Tuple[bool, str]:
    """Classify a numeric literal lexeme.

    Returns `(has_explicit_suffix, base_flavour)` where:
    - `has_explicit_suffix` is True iff the lexeme ends in a known
      numeric type suffix (e.g. `100u8`, `0xffu64`). The dotted form
      `100.u8` is NOT inline — the lexer separates it into two atoms.
    - `base_flavour` describes the LITERAL's lexical shape, ignoring
      any suffix: `"dec"` for a plain decimal integer, `"nondec"` for
      `0b`/`0o`/`0x`-prefixed integers, `"float"` for anything with a
      `.` or `e`. Drives default-resolution: dec→i64, nondec→u64,
      float→f64.
    """
    rest = numstr
    has_suffix = False
    if rest[-4:] in _NUMERIC_SUFFIXES_4:
        has_suffix = True
        rest = rest[:-4]
    elif rest[-3:] in _NUMERIC_SUFFIXES_3:
        has_suffix = True
        rest = rest[:-3]
    elif rest[-2:] in _NUMERIC_SUFFIXES_2:
        has_suffix = True
        rest = rest[:-2]
    prefix = rest[:2].lower()
    if "." in rest:
        return has_suffix, "float"
    if prefix in ("0b", "0o", "0x"):
        return has_suffix, "nondec"
    if "e" in rest or "E" in rest:
        return has_suffix, "float"
    return has_suffix, "dec"


# Default concrete numeric type per literal base flavour. Used by the
# default-resolution late pass when a literal escapes typecheck without
# having been coerced by a typed location.
LITERAL_DEFAULT_BY_BASE: Dict[str, str] = {
    "dec": "i64",
    "nondec": "u64",
    "float": "f64",
}


def _make_literal_ztype(name: str) -> ZType:
    """Construct one of the compiler-internal LITERAL_* singletons."""
    t = ZType(name=name, typetype=ZTypeType.RECORD)
    t.is_literal = True
    return t


# Singleton literal types worn by bare numeric atoms (and folded
# constant expressions) during typecheck. They never originate from
# user source and never reach the emitter — the
# default-resolution late pass rewrites them to a concrete numeric
# type before typecheck returns.
LITERAL_INT: ZType = _make_literal_ztype("literal_int")
LITERAL_FLOAT: ZType = _make_literal_ztype("literal_float")


# Mantissa width (significand bits, including the implicit bit) per
# float type. Used by `int_fits_float` to test exact representability.
# f128 here assumes IEEE 754 binary128 (113 explicit + 1 implicit);
# platforms with 80-bit extended floats won't match this, but
# zerolang's emitted C uses long double whose width is platform-
# dependent — explicit suffixes remain the user's escape hatch.
_FLOAT_MANTISSA_BITS: Dict[str, int] = {
    "f32": 24,
    "f64": 53,
    "f128": 113,
}


def int_fits_float(value: int, float_type_name: str) -> bool:
    """True iff `value` is exactly representable as a float of the
    given type. An integer N is exactly representable in a float with
    M mantissa bits iff `abs(N)` with all trailing power-of-two
    factors stripped fits in M bits."""
    mant = _FLOAT_MANTISSA_BITS.get(float_type_name)
    if mant is None:
        return False
    if value == 0:
        return True
    abs_val = abs(value)
    while abs_val % 2 == 0:
        abs_val //= 2
    return abs_val.bit_length() <= mant


# Dispatch table for float→float round-trip exact-representability
# checks. Keyed by spec type-name string (not a ZType.name), so dict
# lookup keeps this off the bootstrap-lint's literal-name-compare
# radar. f128 is intentionally absent — punt to explicit suffixes.
_FLOAT_ROUNDTRIP_PACK: Dict[str, str] = {
    "f64": "",  # always exact (Python float IS f64)
    "f32": ">f",  # big-endian IEEE 754 single
}


def float_fits_float(value: float, float_type_name: str) -> bool:
    """True iff `value` (a Python f64) is exactly representable as a
    float of the given type. f64→f64 always succeeds. f64→f32 requires
    that the round-trip through C's `float` (single precision)
    preserves the value bit-for-bit."""
    fmt = _FLOAT_ROUNDTRIP_PACK.get(float_type_name)
    if fmt is None:
        return False
    if fmt == "":
        return True
    return _struct.unpack(fmt, _struct.pack(fmt, value))[0] == value


NUMERIC_RANGES: Dict[str, Tuple[int, int]] = {
    "i8": (-128, 127),
    "i16": (-32768, 32767),
    "i32": (-2147483648, 2147483647),
    "i64": (-9223372036854775808, 9223372036854775807),
    "i128": (-(2**127), 2**127 - 1),
    "u8": (0, 255),
    "u16": (0, 65535),
    "u32": (0, 4294967295),
    "u64": (0, 18446744073709551615),
    "u128": (0, 2**128 - 1),
    "c8": (0, 255),
    "c32": (0, 4294967295),
}


def parse_literal_value(numstr: str) -> "Optional[int | float]":
    """Parse a numeric literal lexeme into its unbounded Python value
    (int or float), ignoring any inline / dotted type suffix. Returns
    None if the lexeme is malformed.

    Used by the literal-type infrastructure during typecheck: the
    `LITERAL_INT` / `LITERAL_FLOAT` atom carries this value as its
    `node_const_value`, and range-checking happens later at the
    coercion boundary (`_coerce_literal_by_id`). Bypassing
    `parse_number`'s range check is the whole point — a literal like
    `18446744073709551615` (u64::MAX) doesn't fit i64 but must still
    be representable so it can flow into a u64-typed location."""
    # Reuse `parse_number` for the heavy lifting: it returns the
    # unbounded Python value in the 2nd tuple slot even when its
    # 3rd-slot error is a "value out of range for {default_type}"
    # complaint (which we want to ignore here — range-checking is the
    # coercion boundary's job, not the atom's). Only when the value
    # didn't parse at all (zero sentinel paired with an "Invalid
    # numeric literal" error) do we return None.
    _typename, value, err = parse_number(numstr)
    if err is not None and value == 0 and "out of range" not in err:
        # parse_number's sentinel for an unparseable lexeme.
        return None
    if type(value) is int or type(value) is float:
        return value
    return None


def parse_number(numstr: str) -> Tuple[str, float, Optional[str]]:
    """
    Parse a number identifier returning (type_name, value, error).

    Used in tests only.
    """
    rest = numstr
    numtype: Optional[str] = None
    t = rest[-4:]
    if t in ("i128", "u128", "f128"):
        numtype = t
        rest = rest[:-4]
    if numtype is None:
        t = rest[-3:]
        if t in ("i16", "i32", "i64", "u16", "u32", "u64", "f32", "f64", "c32"):
            numtype = t
            rest = rest[:-3]
    if numtype is None:
        t = rest[-2:]
        if t in ("i8", "u8", "c8"):
            numtype = t
            rest = rest[:-2]

    if "." in rest:
        if numtype is None:
            numtype = "f64"
        elif numtype[0] != "f":
            return (
                numtype,
                0,
                "Numeric type specifier must be float for literals with decimal points",
            )
    elif not numtype:
        # Default type by prefix: char literals (`0c<char>`) default to
        # c32 (Unicode codepoint), everything else defaults to i64.
        if rest[:2] == "0c":
            numtype = "c32"
        else:
            numtype = "i64"

    rest = rest.replace("_", "")
    prefix = rest[:2]
    base = 10
    if prefix == "0b":
        base = 2
        rest = rest[2:]
    elif prefix == "0o":
        base = 8
        rest = rest[2:]
    elif prefix == "0x":
        base = 16
        rest = rest[2:]
    elif prefix == "0c":
        # Character literal: the body is the character(s) whose codepoint
        # is the value. Spec restricts the shorthand to a single
        # identifier character (other forms — `0c\n`, `0c\xHH`, `0c#`,
        # `0c\u{...}` — go through the dedicated lexer path which is
        # not yet wired in; only the single-identifier-char shorthand
        # is supported here today). Default type is c32; c8 can be
        # selected via the `.c8` suffix.
        body = rest[2:]
        if numtype is None:
            numtype = "c32"
        if len(body) != 1:
            return (
                numtype,
                0,
                f"Character literal must be exactly one character: {numstr}",
            )
        i = ord(body)
        if numtype in NUMERIC_RANGES:
            lo, hi = NUMERIC_RANGES[numtype]
            if i < lo or i > hi:
                return (
                    numtype,
                    i,
                    f"Value {i} out of range for {numtype} ({lo}..{hi})",
                )
        return numtype, i, None

    if numtype[0] == "f":
        if base != 10:
            return (numtype, 0, f"Base must be 10 for float: {numstr}")
        f = float(rest)
        return numtype, f, None

    try:
        i = int(rest, base=base)
    except ValueError:
        return (numtype, 0, f"Invalid numeric literal: {numstr}")
    if numtype in NUMERIC_RANGES:
        lo, hi = NUMERIC_RANGES[numtype]
        if i < lo or i > hi:
            return (numtype, i, f"Value {i} out of range for {numtype} ({lo}..{hi})")
    return numtype, i, None
