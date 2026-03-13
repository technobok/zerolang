"""
ZeroLang type checker

Type definitions and type checking pass for the AST.
"""

import threading
from enum import IntEnum, unique
from dataclasses import dataclass, field
from typing import Optional, List, NewType, cast, Callable, Tuple
from collections import OrderedDict
from itertools import count


@unique
class ZTypeType(IntEnum):
    """
    TypeType - types of types
    """

    NULL = 0  # function that returns nothing
    GENERIC_CALL = 2

    # user defined types
    UNIT = 50
    FUNCTION = 51
    RECORD = 52
    CLASS = 53
    VARIANT = 54
    UNION = 55
    ENUM = 56
    PROTOCOL = 57

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


# a typesafe type id
TypeID = NewType("TypeID", int)


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

    nodeid: TypeID = field(
        default_factory=cast(Callable[[], TypeID], count().__next__), init=False
    )

    name: str
    typetype: ZTypeType
    parent: "Optional[ZType]"

    children: "OrderedDict[str, ZType]" = field(default_factory=OrderedDict, init=False)

    isgeneric: bool = False
    isliteral: bool = False

    # ownership annotations for function parameters and return type
    param_ownership: "dict[str, ZParamOwnership]" = field(
        default_factory=dict, init=False
    )

    # value type vs reference type classification
    is_valtype: Optional[bool] = field(default=None, init=False)


# a typesafe variable id
VariableID = NewType("VariableID", int)


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

    variableid: VariableID = field(
        default_factory=cast(Callable[[], VariableID], count().__next__), init=False
    )
    ztype: ZType
    ownership: ZOwnership
    named: ZNaming
    # locks held ON this variable by other variables
    locks: List[LockEntry] = field(default_factory=list)
    # names of variables this variable holds locks on (for cleanup on scope exit)
    held_locks: List[str] = field(default_factory=list)


class TypeTable:
    """
    TypeTable - table of all types for a program
    """

    def __init__(self) -> None:
        self._table: List[ZType] = []
        self._lock = threading.Lock()

    def __getitem__(self, index: TypeID) -> ZType:
        return self._table[index]

    def _append(self, typeitem: ZType) -> TypeID:
        with self._lock:
            idx = TypeID(len(self._table))
            self._table.append(typeitem)
        return idx

    def add(self, name: str, typetype: ZTypeType) -> TypeID:
        t = ZType(name=name, typetype=typetype, parent=None)
        return self._append(t)


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
        if t in ("i16", "i32", "i64", "u16", "u32", "u64", "f32", "f64"):
            numtype = t
            rest = rest[:-3]
    if numtype is None:
        t = rest[-2:]
        if t in ("i8", "u8"):
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

    i = int(rest, base=base)
    return numtype, i, None
