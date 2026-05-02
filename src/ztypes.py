"""
ZeroLang type checker

Type definitions and type checking pass for the AST.
"""

from enum import IntEnum, unique
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@unique
class ZTypeType(IntEnum):
    """
    TypeType - types of types
    """

    NULL = 0  # function that returns nothing
    GENERIC_CALL = 2
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
class ControlKind(IntEnum):
    """Identifies compiler control flow functions"""

    NONE = 0
    RETURN = 1
    BREAK = 2
    CONTINUE = 3
    ERROR = 4
    PANIC = 5


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
    Lock state - orthogonal to ownership

    UNLOCKED: no lock held.
    EXCLUSIVE: exclusive lock, no other references allowed.
    SHARED: shared lock, other shared references allowed but no mutation.
    """

    UNLOCKED = 0
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


@unique
class ZNaming(IntEnum):
    """
    Naming - naming info related to variable/expression
    """

    ANONYMOUS = 0
    NAMED = 1


# module-level counters for auto-incrementing IDs
_next_type_id: int = 0


def _alloc_type_id() -> int:
    """Allocate the next auto-incrementing type ID."""
    global _next_type_id
    tid = _next_type_id
    _next_type_id += 1
    return tid


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
    annotations and field defaults lives on TypeChild rows in the
    `Typing.type_child` table; ZType only carries type-identity data.

    is_valtype indicates whether this type is a value type (records,
    numerics, enums, variants) vs a reference type (classes, unions).
    Value types are copied on assignment; reference types have ownership
    semantics.
    """

    nodeid: int = field(default_factory=_alloc_type_id, init=False)

    name: str
    typetype: ZTypeType
    parent: "Optional[ZType]"
    subtype: ZSubType = ZSubType.NONE

    # parallel name→id map for children. Lazily populated by child_id_for;
    # never pre-seeded. Globally-unique ids consumed by `Typing.type_child`
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
    control_kind: ControlKind = field(default=ControlKind.NONE, init=False)

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

    # element type for DATA types. The DATA's children
    # are value-carrier RECORDs whose `name` is the literal value (e.g.
    # the children of `primes: data { 2 3 5 }` are RECORDs with names
    # "2", "3", "5", not the numeric type itself); element_type is the
    # only authoritative pointer to the underlying numeric ZType.
    element_type: Optional["ZType"] = field(default=None, init=False)

    # C identifier for this type (set by type checker, used by emitter)
    # For type definitions: "z_point_t", "z_list_i64_t", etc.
    # For function types: "z_math_add", "z_point_distance", etc.
    cname: str = field(default="", init=False)

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

    def child_name_for(self, cid: int) -> Optional[str]:
        """Reverse lookup of `child_id_for`: return the name that minted
        `cid` on this type, or None if no name maps to `cid`. Linear scan
        over `children_id_map`; per-parent maps are small."""
        for name, mapped_id in self.children_id_map.items():
            if mapped_id == cid:
                return name
        return None

    def __repr__(self) -> str:
        return f"ZType(name={self.name!r}, typetype={self.typetype!r}, cname={self.cname!r}, nodeid={self.nodeid})"


_next_variable_id: int = 0


def _alloc_variable_id() -> int:
    """Allocate the next auto-incrementing variable ID."""
    global _next_variable_id
    vid = _next_variable_id
    _next_variable_id += 1
    return vid


@unique
class ScopeKind(IntEnum):
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
class LockHolderKind(IntEnum):
    """Categorises what kind of entity holds a lock.

    Each kind maps `LockHolder.id` to a different id-space:
    - VAR: ZVariable.variableid (a borrow-binding variable)
    - CALL: AST nodeid of the call expression that acquired the lock
    - FOR: AST nodeid of the for-loop that owns the iteration lock
    """

    VAR = 0
    CALL = 1
    FOR = 2


@dataclass(frozen=True)
class LockHolder:
    """Tagged identifier for a lock holder. Replaces the prior free-form
    string (`variable name | "call:{nodeid}" | "__for"`)."""

    kind: LockHolderKind
    id: int


@dataclass
class LockInfo:
    """Lock state on a variable — stored on Entry, not on ZVariable.

    `path` is the addressable lock target as a tuple `(root, f1, f2, ...)`.
    `Entry.name` always equals `path[0]` so scope-chain lookup remains a
    simple linear scan keyed by root. The full tuple is consulted to
    apply the prefix-overlap conflict rule.

    `holder` is a tagged `LockHolder` distinguishing a borrow-binding
    variable, a call site, or a for-loop sentinel.
    """

    lock_type: ZLockState  # EXCLUSIVE or SHARED
    holder: LockHolder
    path: Tuple[str, ...] = ()


@dataclass
class Entry:
    """A single entry in a scope's environment.

    Represents either a definition (introduces a name) or a shadow/overlay
    (modifies state of a name from an outer scope).

    `entry_id` is a monotonic per-process identity (Phase 7c) for SQL
    dumps and future hot-path migrations.
    """

    entry_id: int = field(default_factory=_alloc_entry_id, init=False)

    name: str
    ztype: ZType
    is_definition: bool
    # for runtime variables (None for type/function definitions and lock-only overlays)
    var: "Optional[ZVariable]" = None
    # lock state (one lock per variable per scope)
    lock: Optional[LockInfo] = None
    # narrowing state (for match/if arms)
    narrowed_subtype: Optional[str] = None  # "ok", "err" — narrowed in match arm
    # Phase 7c: id parallel to narrowed_subtype. Minted via the outer
    # union/variant's child_id_for(subtype_name). String remains
    # authoritative; id is the hot-path key for future migrations.
    narrowed_subtype_id: Optional[int] = None
    excluded_subtypes: "Optional[frozenset[str]]" = None  # subtypes ruled out
    # Phase 7c: id parallel to excluded_subtypes. Same cardinality as the
    # string set by construction (child_id_for is globally monotonic).
    excluded_subtype_ids: "Optional[frozenset[int]]" = None
    # original union/variant type when ztype is the narrowed payload — the
    # emitter uses this to generate the C-level unwrap (original is still the
    # storage type, narrowed is the typecheck-visible type).
    original_ztype: "Optional[ZType]" = None
    # taken state
    is_taken: bool = False
    taken_at: Optional[Tuple[int, int, int]] = None


@dataclass
class ExprResult:
    """Result of checking an expression: the resolved type plus any
    borrow/private intent that the enclosing assignment should consume.

    `borrow_target` is the addressable lock path (root + descents) of the
    source the result borrows from, e.g. `("rec", "field")`. None when
    no borrow lock is pending.
    """

    ztype: Optional[ZType] = None
    borrow_target: Optional[Tuple[str, ...]] = None
    private_access: bool = False


@dataclass
class ZVariable:
    """
    ZVariable - type + ownership info for a variable/expression.
    Lock state is tracked via Entry.lock in the scope chain, not here.
    """

    variableid: int = field(default_factory=_alloc_variable_id, init=False)
    ztype: ZType
    ownership: ZOwnership
    named: ZNaming
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
