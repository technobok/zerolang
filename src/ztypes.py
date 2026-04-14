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
    STRINGVIEW = 2  # stringview record — z_stringview_t, born-borrowed valtype


@unique
class ControlKind(IntEnum):
    """Identifies compiler control flow functions (return, break, continue, error)."""

    NONE = 0
    RETURN = 1
    BREAK = 2
    CONTINUE = 3
    ERROR = 4


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


class _TagOrigin:
    """Sentinel for generic_origin when the origin is a tag discriminator type."""

    is_ztype: bool = False
    name: str = "tag"
    nodeid: int = -1

    def __repr__(self) -> str:
        return "TAG_ORIGIN"


TAG_ORIGIN = _TagOrigin()


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

    # plain dict (insertion-ordered since Python 3.7+, replaces OrderedDict)
    children: "dict[str, ZType]" = field(default_factory=dict, init=False)

    # return type for function types (None for non-functions or void functions)
    return_type: "Optional[ZType]" = field(default=None, init=False)
    # ownership annotation on the return type (if any)
    return_ownership: "Optional[ZParamOwnership]" = field(default=None, init=False)

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

    # for monomorphized types: points to the original template type (or TAG_ORIGIN sentinel)
    generic_origin: "Optional[ZType | _TagOrigin]" = field(default=None, init=False)

    # for monomorphized types: maps param name → concrete ZType
    generic_args: "dict[str, ZType]" = field(default_factory=dict, init=False)

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
    # after construction and force the enclosing record to be born-borrowed.
    lock_field_names: "set[str]" = field(default_factory=set, init=False)

    # True iff this record has at least one .lock field. Implies the type is
    # born-borrowed and propagates the borrowed restriction.
    has_lock_fields: bool = field(default=False, init=False)

    # True iff every constructor of this record returns this.borrow (the type
    # is born-borrowed). Instances are BORROWED from the moment of creation.
    is_born_borrowed: bool = field(default=False, init=False)

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


@dataclass
class LockEntry:
    """
    A single lock held on a variable.

    lock_type: EXCLUSIVE or SHARED
    holder: name of the variable that holds this lock
    """

    lock_type: ZLockState
    holder: str


@dataclass
class ZVariable:
    """
    ZVariable - type + ownership + lock info for a variable/expression
    """

    variableid: int = field(default_factory=_alloc_variable_id, init=False)
    ztype: ZType
    ownership: ZOwnership
    named: ZNaming
    # locks held ON this variable by other variables
    locks: List[LockEntry] = field(default_factory=list)
    # names of variables this variable holds locks on (for cleanup on scope exit)
    held_locks: List[str] = field(default_factory=list)
    # private access: variable declared with .private type, bypasses public_members
    is_private_access: bool = False


class TypeState:
    """
    TypeState - tracks compile-time type narrowing at each program point.

    Flow typing for union/variant types: within match arms, after
    diverging match arms, and on subtype assignment. TypeState is
    intra-function only and does not cross function call boundaries.

    Immutable-style: narrow/exclude/merge return new TypeState instances.

    _refined maps variable names to their narrowed ZType (either a specific
    subtype ZType or the full union/variant when partial exclusion applies).

    _excluded maps variable names to sets of excluded subtype names.
    When all subtypes except one are excluded, the variable collapses to
    that single remaining subtype in _refined.
    """

    __slots__ = ("_refined", "_excluded", "_narrowed_subtype", "_unreachable")

    def __init__(
        self,
        refined: "Optional[dict[str, ZType]]" = None,
        excluded: "Optional[dict[str, frozenset[str]]]" = None,
        narrowed_subtype: "Optional[dict[str, str]]" = None,
        unreachable: bool = False,
    ) -> None:
        self._refined: dict[str, ZType] = refined if refined is not None else {}
        self._excluded: dict[str, frozenset[str]] = (
            excluded if excluded is not None else {}
        )
        # maps variable name -> subtype name (e.g., "x" -> "ok")
        self._narrowed_subtype: dict[str, str] = (
            narrowed_subtype if narrowed_subtype is not None else {}
        )
        self._unreachable: bool = unreachable

    @property
    def unreachable(self) -> bool:
        return self._unreachable

    def lookup(self, name: str) -> "Optional[ZType]":
        """Return refined type for name, or None to fall back to declared type."""
        if self._unreachable:
            return None
        return self._refined.get(name)

    def is_excluded(self, name: str, subtype_name: str) -> bool:
        """Check if a subtype has been excluded for a variable."""
        return subtype_name in self._excluded.get(name, frozenset())

    def get_subtype_name(self, name: str) -> "Optional[str]":
        """Return the subtype name a variable is narrowed to, or None."""
        return self._narrowed_subtype.get(name)

    def narrow(
        self, name: str, to_type: "ZType", subtype_name: str = ""
    ) -> "TypeState":
        """Return new TypeState with name narrowed to to_type."""
        new_refined = dict(self._refined)
        new_refined[name] = to_type
        new_subtypes = dict(self._narrowed_subtype)
        if subtype_name:
            new_subtypes[name] = subtype_name
        return TypeState(
            new_refined, dict(self._excluded), new_subtypes, self._unreachable
        )

    def exclude(self, name: str, subtype_name: str, full_type: "ZType") -> "TypeState":
        """Return new TypeState excluding a subtype from name's known type.

        If only one subtype remains after exclusion, collapses to that subtype.
        """
        # Collect all subtypes of the full union/variant
        all_subtypes = _union_subtype_names(full_type)

        # Determine currently known possible subtypes
        known_subtype = self._narrowed_subtype.get(name)
        if known_subtype:
            # Already narrowed to a single named subtype
            if subtype_name == known_subtype:
                return TypeState(
                    dict(self._refined),
                    dict(self._excluded),
                    dict(self._narrowed_subtype),
                    unreachable=True,
                )
            # Excluding a different subtype — no effect
            return self

        # Accumulate exclusions
        prev_excluded = self._excluded.get(name, frozenset())
        new_excluded_set = prev_excluded | {subtype_name}

        remaining = {k: v for k, v in all_subtypes.items() if k not in new_excluded_set}

        if not remaining:
            return TypeState(
                dict(self._refined),
                dict(self._excluded),
                dict(self._narrowed_subtype),
                unreachable=True,
            )

        new_refined = dict(self._refined)
        new_excluded = dict(self._excluded)
        new_subtypes = dict(self._narrowed_subtype)
        new_excluded[name] = frozenset(new_excluded_set)

        if len(remaining) == 1:
            # Collapse to single remaining subtype
            sname, single_type = next(iter(remaining.items()))
            new_refined[name] = single_type
            new_subtypes[name] = sname

        return TypeState(new_refined, new_excluded, new_subtypes, self._unreachable)

    def reset(self, name: str) -> "TypeState":
        """Return new TypeState with narrowing removed for name."""
        if (
            name not in self._refined
            and name not in self._excluded
            and name not in self._narrowed_subtype
        ):
            return self
        new_refined = dict(self._refined)
        new_refined.pop(name, None)
        new_excluded = dict(self._excluded)
        new_excluded.pop(name, None)
        new_subtypes = dict(self._narrowed_subtype)
        new_subtypes.pop(name, None)
        return TypeState(new_refined, new_excluded, new_subtypes, self._unreachable)

    def mark_unreachable(self) -> "TypeState":
        """Return new TypeState marked as unreachable (all paths diverged)."""
        return TypeState(
            dict(self._refined),
            dict(self._excluded),
            dict(self._narrowed_subtype),
            unreachable=True,
        )

    def copy(self) -> "TypeState":
        """Return a shallow copy."""
        return TypeState(
            dict(self._refined),
            dict(self._excluded),
            dict(self._narrowed_subtype),
            self._unreachable,
        )


def _union_subtype_names(full_type: "ZType") -> "dict[str, ZType]":
    """Extract subtype name -> ZType mapping from a union/variant type."""
    return {
        k: v
        for k, v in full_type.children.items()
        if v.typetype
        not in (ZTypeType.FUNCTION, ZTypeType.DATA, ZTypeType.TAG, ZTypeType.ENUM)
        and getattr(v, "generic_origin", None) is not TAG_ORIGIN
    }


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
