"""
ZeroLang type checker

Type definitions and type checking pass for the AST.
"""

from enum import IntEnum, unique
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple


@unique
class ZTypeType(IntEnum):
    """
    TypeType - types of types
    """

    NULL = 0  # function that returns nothing
    GENERIC_CALL = 2
    GENERIC_PARAM = 3  # a generic type parameter (e.g., t in t: any.generic)

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
    TAG = 61  # tag discriminator type (placeholder until generics)

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
    """Identifies compiler control flow functions (return, break, continue, error)."""

    NONE = 0
    RETURN = 1
    BREAK = 2
    CONTINUE = 3
    ERROR = 4
    PANIC = 5


@unique
class ZOwnership(IntEnum):
    """
    Ownership - 2-state model (v2)

    OWNED: the variable owns the instance and is responsible for its lifetime.
    BORROWED: the variable has a temporary reference; it does not own the instance.
    """

    OWNED = 0
    BORROWED = 1


@unique
class ZLockState(IntEnum):
    """
    Lock state - orthogonal to ownership (v2)

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


# plain int alias for type IDs (replaces NewType for self-hosting simplicity)
TypeID = int

# module-level counters for auto-incrementing IDs
_next_type_id: int = 0


def _alloc_type_id() -> int:
    """Allocate the next auto-incrementing type ID."""
    global _next_type_id
    tid = _next_type_id
    _next_type_id += 1
    return tid


# monotonic counter for child-name identities on ZType. Globally unique so a
# child_id never collides across parents and can be used directly as a SQL key.
# Per-process only — not persisted across compiler invocations.
_next_child_id: int = 0


def _alloc_child_id() -> int:
    """Allocate the next auto-incrementing child ID."""
    global _next_child_id
    cid = _next_child_id
    _next_child_id += 1
    return cid


# Tag-origin types (variant tag discriminator instances) are flagged by
# `ZType.is_tag_generic_origin = True`. `generic_origin` stays None on
# those — the boolean is the SQL-shaped marker, replacing the old
# `_TagOrigin` sentinel that lived in `generic_origin`.


@dataclass
class ZType:
    """
    ZType - describes a type

    For functions, children contains parameters keyed by name.
    The return type is stored in the dedicated return_type field.

    For records, children contains fields and methods.

    For units, children contains the unit's exported definitions.

    param_ownership maps parameter names to their
    ownership annotation (take/borrow/lock). Only populated for
    FUNCTION types.

    is_valtype indicates whether this type is a value type (records,
    numerics, enums, variants) vs a reference type (classes, unions).
    Value types are copied on assignment; reference types have ownership
    semantics.
    """

    is_ztype: bool = field(default=True, init=False)
    nodeid: int = field(default_factory=_alloc_type_id, init=False)

    name: str
    typetype: ZTypeType
    parent: "Optional[ZType]"
    subtype: ZSubType = ZSubType.NONE

    # parallel name→id map for children. Lazily populated by child_id_for;
    # never pre-seeded. Globally-unique ids consumed by `Typing.type_child`
    # rows and by narrowing entries that reference a child by id rather
    # than by string. Pre-F5.H this paralleled a `children` dict on the
    # ZType; F5.H.5 removed that dict — children now live on
    # `Typing.type_child` and `child_id_for` exists solely as the id
    # allocator that keeps the SQL row's `child_name_id` stable.
    children_id_map: "dict[str, int]" = field(default_factory=dict, init=False)

    # return type for function types (None for non-functions or void functions)
    return_type: "Optional[ZType]" = field(default=None, init=False)
    # ownership annotation on the return type (if any)
    return_ownership: "Optional[ZParamOwnership]" = field(default=None, init=False)
    # name of the parameter whose declared TYPE was the `this` keyword —
    # i.e. the receiver-bound parameter. Set during _resolve_function_type.
    # Both `:this` (param named "this") and `h: this` (param named "h")
    # populate this; downstream code checks against this field instead of
    # hardcoding the literal string "this", so the named-binding form
    # works equivalently to the unnamed shorthand.
    this_param_name: "Optional[str]" = field(default=None, init=False)

    isgeneric: bool = False
    isliteral: bool = False

    # ownership annotations for function parameters and return type
    param_ownership: "dict[str, ZParamOwnership]" = field(
        default_factory=dict, init=False
    )

    # value type vs reference type classification
    is_valtype: Optional[bool] = field(default=None, init=False)

    # default values for parameters/fields: name → C-level default expression
    param_defaults: "dict[str, str]" = field(default_factory=dict, init=False)

    # generic type parameters: param name → constraint ZType (for template types)
    generic_params: "dict[str, ZType]" = field(default_factory=dict, init=False)

    # for monomorphized types: points to the original template type.
    # None for non-monomorphized types AND for variant-tag discriminator
    # types — the latter are flagged separately via `is_tag_generic_origin`.
    generic_origin: "Optional[ZType]" = field(default=None, init=False)
    # variant-tag discriminator marker (replaces the old TAG_ORIGIN sentinel
    # that used to live in `generic_origin`).
    is_tag_generic_origin: bool = field(default=False, init=False)

    # F5.H.5: monomorphized type generic args (param name → concrete ZType)
    # live on `Typing.type_generic_arg`; query via `typing.generic_arg_of`
    # / `typing.generic_args_of`.

    # names of generic params that are numeric (constraint is a numeric type)
    numeric_generic_params: "set[str]" = field(default_factory=set, init=False)

    # default types for generic params: param name → default ZType
    generic_defaults: "dict[str, ZType]" = field(default_factory=dict, init=False)

    # for numeric generic value-carrying ZTypes: the constant integer value
    numeric_value: "Optional[int]" = field(default=None, init=False)

    # compile-time constant value (for 'as' section constants)
    const_value: "Optional[int | float | str]" = field(default=None, init=False)

    # for typedef types: points to the immediate base type being wrapped
    typedef_base: "Optional[ZType]" = field(default=None, init=False)

    # memory management metadata (set by type checker after resolution)
    needs_destructor: bool = field(default=False, init=False)
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

    # control flow kind: identifies system control flow functions
    control_kind: ControlKind = field(default=ControlKind.NONE, init=False)

    # public/private access control: maps external name → internal name for
    # publicly accessible members. None = all-public (default). Set during type
    # resolution when public: unit { ... } is declared in the as block.
    public_members: "Optional[dict[str, str]]" = field(default=None, init=False)

    # fields declared with .private type: set of field names that grant private
    # access to the referenced type. Set during type resolution.
    private_fields: "set[str]" = field(default_factory=set, init=False)

    # set of field names declared with the .lock type modifier — the field
    # stores a locked reference to external data. Lock fields are immutable
    # after construction and only permitted on classes.
    lock_field_names: "set[str]" = field(default_factory=set, init=False)

    # True iff this type has at least one .lock field (classes only).
    has_lock_fields: bool = field(default=False, init=False)

    # set of arm names declared with the .lock type modifier on a union —
    # the arm holds a locked reference into a parent rather than owning its
    # payload. The destructor releases the lock without freeing the payload;
    # the union's lifetime cannot exceed the locked source's lifetime.
    # Unions only — variant arms must be inline-stored valtypes.
    lock_arm_names: "set[str]" = field(default_factory=set, init=False)

    # True iff the type's 'create' method is disabled — either by the user
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

    # internal metadata: compiler-generated raw allocator for this type
    meta_create: Optional["ZType"] = field(default=None, init=False)

    # internal metadata: tag discriminator enum for union/variant types
    tag_type: Optional["ZType"] = field(default=None, init=False)

    # internal metadata: element type for data types
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

    def __repr__(self) -> str:
        return f"ZType(name={self.name!r}, typetype={self.typetype!r}, cname={self.cname!r}, nodeid={self.nodeid})"


# plain int alias for variable IDs (replaces NewType for self-hosting simplicity)
VariableID = int

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


# module-level counter for scope IDs
_next_scope_id: int = 0


def _alloc_scope_id() -> int:
    """Allocate the next auto-incrementing scope ID."""
    global _next_scope_id
    sid = _next_scope_id
    _next_scope_id += 1
    return sid


# monotonic counter for symbol-table Entry identities. Globally unique;
# per-process only (not persisted across compiler invocations).
_next_entry_id: int = 0


def _alloc_entry_id() -> int:
    """Allocate the next auto-incrementing entry ID."""
    global _next_entry_id
    eid = _next_entry_id
    _next_entry_id += 1
    return eid


@dataclass
class LockInfo:
    """Lock state on a variable — stored on Entry, not on ZVariable.

    `path` is the addressable lock target as a tuple `(root, f1, f2, ...)`.
    `Entry.name` always equals `path[0]` so scope-chain lookup remains a
    simple linear scan keyed by root. The full tuple is consulted to
    apply the prefix-overlap conflict rule.
    """

    lock_type: ZLockState  # EXCLUSIVE or SHARED
    holder: str  # borrow variable name or call identifier
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
    # F4.1: marks the receiver-bound parameter Entry (the parameter whose
    # declared type is the `this` keyword). Lets the emitter detect the
    # receiver via Entry instead of comparing the parameter name to the
    # literal "this".
    is_receiver: bool = False


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


class TypeTable:
    """
    TypeTable - table of all types for a program.
    Single-threaded — no locking needed.
    """

    def __init__(self) -> None:
        self._table: List[ZType] = []

    def __getitem__(self, index: int) -> ZType:
        return self._table[index]

    def _append(self, typeitem: ZType) -> int:
        idx = len(self._table)
        self._table.append(typeitem)
        return idx

    def add(self, name: str, typetype: ZTypeType) -> int:
        t = ZType(name=name, typetype=typetype, parent=None)
        return self._append(t)


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
