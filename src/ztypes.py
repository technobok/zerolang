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


@dataclass
class ZType:
    """
    ZType - describes a type

    For functions, children contains parameters keyed by name, plus
    a special ":return" entry for the return type.

    For records, children contains fields and methods.

    For units, children contains the unit's exported definitions.

    param_ownership maps parameter names (and ":return") to their
    ownership annotation (take/borrow/lock). Only populated for
    FUNCTION types.

    is_valtype indicates whether this type is a value type (records,
    numerics, enums, variants) vs a reference type (classes, unions).
    Value types are copied on assignment; reference types have ownership
    semantics.
    """

    nodeid: int = field(default_factory=_alloc_type_id, init=False)

    name: str
    typetype: ZTypeType
    parent: "Optional[ZType]"

    # plain dict (insertion-ordered since Python 3.7+, replaces OrderedDict)
    children: "dict[str, ZType]" = field(default_factory=dict, init=False)

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

    # for monomorphized types: points to the original template type (or "tag" sentinel)
    generic_origin: "Optional[ZType | str]" = field(default=None, init=False)

    # for monomorphized types: maps param name → concrete ZType
    generic_args: "dict[str, ZType]" = field(default_factory=dict, init=False)

    # names of generic params that are numeric (constraint is a numeric type)
    numeric_generic_params: "set[str]" = field(default_factory=set, init=False)

    # for numeric generic value-carrying ZTypes: the constant integer value
    numeric_value: "Optional[int]" = field(default=None, init=False)

    # for typedef types: points to the immediate base type being wrapped
    typedef_base: "Optional[ZType]" = field(default=None, init=False)

    # memory management metadata (set by type checker after resolution)
    needs_destructor: bool = field(default=False, init=False)
    destructor_name: Optional[str] = field(default=None, init=False)
    is_heap_allocated: bool = field(default=False, init=False)

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
