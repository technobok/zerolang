"""
AST Nodes and types
"""

import copy

# import threading
from dataclasses import dataclass, field
from enum import IntEnum, unique
import typing
from typing import Optional, Dict, NewType, cast, Callable
from itertools import count

# from collections import OrderedDict  # pylint: disable=W0611
# import ztypes
import zvfs
from zvfs import DEntryID
from zlexer import Token, TT
from ztypes import ZType, ZParamOwnership, ZOwnership


@unique
class ERR(IntEnum):
    """
    Error codes grouped by category.

    E0001-E0099: Parser/syntax errors
    E0100-E0199: Type resolution errors
    E0200-E0299: Ownership errors
    E0300-E0399: Call/argument errors
    E0400-E0499: Generic/monomorphization errors
    """

    # --- Parser/syntax errors (E0001-E0099) ---
    FILENOTFOUND = 2  # E0002
    DUPLICATEDEF = 3  # E0003
    BADUNITNAME = 4  # E0004
    BADUNIT = 5  # E0005
    BADFUNCTION = 6  # E0006
    IOERROR = 7  # E0007
    EXPECTEDDEF = 10  # E0010
    EXPECTEDEXP = 12  # E0012
    EXPECTEDOP = 13  # E0013
    EXPECTEDTYPEDEF = 14  # E0014
    EXPECTEDSTATEMENT = 15  # E0015
    BADARGUMENT = 17  # E0017
    BADARGUMENTBLOCK = 18  # E0018
    BADEXPRESSION = 19  # E0019
    BADOPERATION = 20  # E0020
    BADSTRING = 21  # E0021
    BADPATH = 22  # E0022
    BADREFERENCE = 23  # E0023
    BADCALL = 24  # E0024
    BADPARAMETER = 25  # E0025
    BADPARAMETERBLOCK = 26  # E0026
    BADOBJECTBLOCK = 27  # E0027
    BADITEM = 28  # E0028
    BADTHEN = 29  # E0029
    BADELSE = 30  # E0030
    BADCASE = 31  # E0031
    BADFOR = 32  # E0032
    BADDATA = 34  # E0034
    BADSTATEMENT = 35  # E0035

    # --- Type resolution errors (E0100-E0199) ---
    TYPEERROR = 100  # E0100: general type error
    REFNOTFOUND = 8  # E0008 (legacy; undefined identifier)

    # --- Ownership errors (E0200-E0299) ---
    OWNERERROR = 200  # E0200: general ownership error

    # --- Call/argument errors (E0300-E0399) ---
    CALLERROR = 300  # E0300: general call error

    # --- Generic/monomorphization errors (E0400-E0499) ---
    GENERICERROR = 400  # E0400: general generic error

    # --- Internal compiler error ---
    COMPILERERROR = 1  # E0001: should not happen


_ERROR_TOKEN = Token(toktype=TT.EOL, tokstr="", fsno=DEntryID(0), lineno=0, colno=0)


# ANSI color codes
_RED = "\033[1;31m"
_BLUE = "\033[1;34m"
_GREEN = "\033[1;32m"
_CYAN = "\033[1;36m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def errortomessage(err: "Error", vfs: zvfs.ZVfs, color: bool = False) -> str:
    """Format an error in rustc-style format.

    Example output:
        error[E0008]: undefined identifier 'x'
         --> test.z:5:10
          |
        5 |     print x
          |           ^ not found
          |
          = note: ...
          = hint: did you mean 'y'?
    """
    # color helpers
    red = _RED if color else ""
    blue = _BLUE if color else ""
    green = _GREEN if color else ""
    cyan = _CYAN if color else ""
    bold = _BOLD if color else ""
    reset = _RESET if color else ""

    code = f"E{err.err.value:04d}"
    result = []

    # first line: error[E0008]: message
    result.append(f"{red}error[{code}]{reset}: {bold}{err.msg}{reset}")

    if err.loc:
        loc = err.loc
        path = vfs.pathfromprovider(loc.fsno)

        # location line
        result.append(f" {blue}-->{reset} {path}:{loc.lineno}:{loc.colno}")

        # source line with gutter
        line = vfs.getline(loc.fsno, loc.lineno)
        if line:
            gutter = f"{blue}{loc.lineno:>4} |{reset}"
            empty_gutter = f"{blue}     |{reset}"
            result.append(empty_gutter)
            result.append(f"{gutter} {line.rstrip()}")
            underline = (
                (" " * loc.colno)
                + f"{red}"
                + ("^" * max(len(loc.tokstr), 1))
                + f"{reset}"
            )
            result.append(f"{empty_gutter}{underline}")

    # note and hint
    if err.note:
        gutter = f"{blue}     ={reset}"
        result.append(f"{gutter} {cyan}note{reset}: {err.note}")
    if err.hint:
        gutter = f"{blue}     ={reset}"
        result.append(f"{gutter} {green}hint{reset}: {err.hint}")

    return "\n".join(result)


@unique
class NodeType(IntEnum):
    """
    NodeType - AST node type marker
    """

    # PROGRAM = 0
    UNIT = 1
    # BLOCK = 2

    # STATEMENTS's:
    # DEFINITION = 10
    # TYPEREF = 13
    # TYPEREFORNUM = 14

    # TYPEPATH = 15

    # + EXPRESSION

    # EXPRESSION's:
    # + OPERATION

    FUNCTION = 21
    PROTOCOL = 22
    RECORD = 23
    CLASS = 24
    UNION = 25
    VARIANT = 26
    # SPEC = 27
    ENUM = 28
    FACET = 29
    WITH = 37

    # PARAMETER = 29

    EXPRESSION = 30
    IF = 31  # executable component
    IFCLAUSE = 32
    FOR = 33
    DO = 34  # executable component
    CASE = 35
    CASECLAUSE = 36
    CALL = 38
    # ARRAY = 38
    # LIST = 39
    DATA = 40
    # SEQUENCEITEM = 40
    # OPERATION = 41
    NAMEDOPERATION = 42

    # StatementLine
    STATEMENT = 50
    STATEMENTLINE = 51
    ASSIGNMENT = 55
    REASSIGNMENT = 56
    SWAP = 57

    # OPERATION's:
    # + PATH
    # PATH = 60
    DOTTEDPATH = 60
    BINOP = 61
    # BINOPARG = 61
    # ASARG = 62

    # ATOM's:
    # ATOM = 80
    # ATOMEXPR = 81
    # Expression is also an ATOM for AST due to AtomExpr
    ATOMID = 82  # a reference
    LABELVALUE = 83  # label value (:x)

    ATOMSTRING = 84

    ERROR = 99


# @unique
# class Context(IntEnum):
#    """
#    Context - context for a Name definition

#    """
#    # # NONE = 0   # for code statements that are not definitions and have a blank name?
#    # SYSTEM = 1  # system types and functions eg. u8, u8.add, io.print
#    # UNIT = 2  # top (unit) level variable (must be static)
#    # UNITGEN = 3  # generic parameter for a subunit
#    # LOCAL = 4  # local variable in code
#    # FUNC = 5  # non-generic parameter in a function, record etc definition
#    # FUNCGEN = 6  # generic parameter in a function, record etc definition
#    # CALL = 7  # non-generic parameter in a call
#    # # is this iorequired?
#    # CALLGEN = 8  # generic parameter in a call
#    # # need taker type parameters and argument too?

#    NORMAL = 0  # any normal definition (top level, local, parameter, argument etc)
#    GENERIC = 1 # a generic parameter in function, spec, record, class, unit...
#    #BUILTIN ????


@dataclass
class Program:
    """
    Program - top level, hold all Units, streams (files), types for the program
    It is not a Node itself.

    """

    is_error: bool = field(default=False, init=False)
    vfs: zvfs.ZVfs  # vfs for reading source files. Needed to report errors
    units: Dict[
        str, "Unit"
    ]  # TODO: change this into a single top level unit (not a dict)
    mainunitname: str

    # monomorphized generic types: list of (mono_ztype, original_ast_node) tuples
    # populated by the type checker after monomorphization
    mono_types: typing.List = field(default_factory=list)

    # monomorphized generic functions: list of (mono_ztype, cloned_function) tuples
    # populated by the type checker after function generic monomorphization
    mono_functions: typing.List = field(default_factory=list)

    # dedup aliases: {qualified_alias_name: qualified_canonical_name}
    # populated by type checker during monomorphization dedup
    func_aliases: Dict[str, str] = field(default_factory=dict)

    # cloned methods per mono type: {mono_name: {mname: Function}}
    cloned_methods: Dict[str, Dict[str, "Function"]] = field(default_factory=dict)

    # resolved type names: {qualified_name: ZType}
    # populated by type checker after resolution
    resolved: Dict[str, "ZType"] = field(default_factory=dict)

    # Phase 7c: SymbolTable attached by typecheck() so the SQL dumper can
    # emit scope/entry/variable rows. Typed as Optional[object] to avoid a
    # zast <-> zenv import cycle; the dumper uses getattr / duck-typing.
    symbol_table: "Optional[object]" = field(default=None, init=False)

    # Phase 7d: Unit AST nodeid → resolved unit ZType. Attached by
    # typecheck() as a snapshot of TypeChecker.unit_types_by_id. Used by
    # the SQL dumper to populate `unit.unit_type_id`.
    unit_types_by_id: Dict[int, "ZType"] = field(default_factory=dict, init=False)


def clone_function(func: "Function") -> "Function":
    """Deep copy a Function AST node for monomorphization."""
    return copy.deepcopy(func)


# a typesafe node id
NodeID = NewType("NodeID", int)


@dataclass
class Node:
    """
    Node - the parent of the Ast Node type hierarchy. Do not instantiate
    directly
    """

    is_node: bool = field(default=True, init=False)
    nodeid: NodeID = field(
        default_factory=cast(Callable[[], NodeID], count().__next__), init=False
    )
    nodetype: NodeType
    # symbol holding the specific instance where this reference is defined
    # filled in typechecking pass... what is this for?
    # definition: Optional[ZSymbol] = field(default=None, init=False)
    # type of this Node, filled in typechecking pass
    # TODO: maybe Union(None, ZType, ZTypeCheckInProgress)
    type: Optional[ZType] = field(default=None, init=False)
    # compile-time constant value, filled during type checking (constant folding)
    # int for integer arithmetic, float for float arithmetic, bool for comparisons
    # str for string constants in 'as' sections
    const_value: Optional[typing.Union[int, float, bool, str]] = field(
        default=None, init=False
    )

    start: Token  # start location in the source for this Node


@dataclass
class Error(Node):
    """
    Error Node — represents a parse or compile error.

    err = ERR numeric error code
    msg = error message
    note = optional context note (e.g. "ownership was transferred here")
    hint = optional suggestion (e.g. "did you mean 'y'?")

    The 'start' field (inherited from Node) serves as the error location.
    Use the 'loc' property for backward compatibility.
    """

    nodetype: NodeType = field(default=NodeType.ERROR, init=False)
    is_error: bool = field(default=True, init=False)
    err: ERR = ERR.COMPILERERROR
    msg: str = ""
    note: Optional[str] = None
    hint: Optional[str] = None

    @property
    def loc(self) -> Optional[Token]:
        """Backward-compatible alias for start. Returns None if start is the sentinel."""
        return self.start if self.start is not _ERROR_TOKEN else None


# TypeDefinition - one of the following, real Node is not needed
TypeDefinition = typing.Union[
    "Unit",
    "Function",
    "Record",
    "Class",
    "Variant",
    "Union",
    "Enum",
    "Protocol",
    "Facet",
    "Expression",
    "Operation",
    "LabelValue",
]


@dataclass
class Unit(Node):
    """
    Unit Node (unit or unitfile)
    """

    nodetype: NodeType = field(default=NodeType.UNIT, init=False)
    # type definitions and generic parameters all included here
    # body: Dict[str, typing.Union["Definition", "Unit"]]
    body: Dict[str, TypeDefinition]  # xxTypeDefinition?


@dataclass
class Function(Node):
    """
    Function Node (or spec)
    """

    nodetype: NodeType = field(default=NodeType.FUNCTION, init=False)
    returntype: Optional["Path"]  # really a Typeref
    # parameters - normal (non-generic) parameters
    parameters: Dict[
        str, "Path"
    ]  # really, a TyperefOrNum            # xxTypeDefinition?
    body: Optional["Statement"]  # None for Spec
    # ownership annotations: param name -> ZParamOwnership (v2)
    param_ownership: Dict[str, "ZParamOwnership"] = field(default_factory=dict)
    # ownership annotation on the return type (if any)
    return_ownership: Optional["ZParamOwnership"] = None
    # native function: body is compiler-provided (not a spec)
    is_native: bool = False
    # 'as' clause: generic parameters and static functions
    as_items: Dict[str, "Path"] = field(default_factory=dict)
    as_functions: Dict[str, "Function"] = field(default_factory=dict)


@dataclass
class Record(Node):
    """
    Record Definition Node
    """

    nodetype: NodeType = field(default=NodeType.RECORD, init=False)
    items: Dict[str, "Path"]  # generic and normal fields, a TyperefOrNum
    implements: typing.List[
        "Path"
    ]  # 'is' interfaces satisfied by this record, a Typeref
    functions: Dict[str, "Function"]
    as_items: Dict[str, "Path"]
    as_functions: Dict[str, "Function"]
    is_native: bool = False  # native type: instance state is compiler-provided
    # field name -> ZParamOwnership (only LOCK currently allowed on fields)
    field_ownership: Dict[str, "ZParamOwnership"] = field(default_factory=dict)


@dataclass
class Class(Node):
    """
    Class Definition Node
    """

    nodetype: NodeType = field(default=NodeType.CLASS, init=False)
    items: Dict[str, "Path"]  # generic and normal, a TyperefOrNum
    implements: typing.List[
        "Path"
    ]  # 'is' interfaces satisfied by this record, a Typeref
    functions: Dict[str, "Function"]
    as_items: Dict[str, "Path"]
    as_functions: Dict[str, "Function"]
    is_native: bool = False  # native type: instance state is compiler-provided
    # field name -> ZParamOwnership (only LOCK currently allowed on fields)
    field_ownership: Dict[str, "ZParamOwnership"] = field(default_factory=dict)


@dataclass
class Union(Node):
    """
    Union Definition Node
    NB: name clash with typing.Union
    """

    nodetype: NodeType = field(default=NodeType.UNION, init=False)
    items: Dict[str, "Path"]  # generic and normal (???) a TyperefOrNum
    implements: typing.List[
        "Path"
    ]  # 'is' interfaces satisfied by this record, a Typeref
    functions: Dict[str, "Function"]
    tag: Optional["Path"]  # a Typeref
    as_items: Dict[str, "Path"]
    as_functions: Dict[str, "Function"]
    is_native: bool = False  # native type: instance state is compiler-provided


@dataclass
class Variant(Node):
    """
    Variant Definition Node
    """

    nodetype: NodeType = field(default=NodeType.VARIANT, init=False)
    items: Dict[str, "Path"]  # generic and normal (???) as TyperefOrNum
    implements: typing.List[
        "Path"
    ]  # 'is' interfaces satisfied by this record, a Typeref
    functions: Dict[str, "Function"]
    tag: Optional["Path"]  # a Typeref
    as_items: Dict[str, "Path"]
    as_functions: Dict[str, "Function"]
    is_native: bool = False  # native type: instance state is compiler-provided


@dataclass
class Enum(Node):
    """
    Enum Definition Node
    """

    nodetype: NodeType = field(default=NodeType.ENUM, init=False)
    # if Path provided, must evaluate to num type that matches tag
    # otherwise, Path will just refer to itself...:
    # value is AtomId and AtomId.name == same as str)
    items: Dict[str, "Path"]
    implements: typing.List[
        "Path"
    ]  # 'is' interfaces satisfied by this record, a Typeref
    functions: Dict[str, "Function"]
    tag: Optional["Path"]  # must be a simple numeric type (including char), a Typeref
    as_items: Dict[str, "Path"]
    as_functions: Dict[str, "Function"]


@dataclass
class Protocol(Node):
    """
    Protocol Definition Node
    """

    nodetype: NodeType = field(default=NodeType.PROTOCOL, init=False)
    parameters: Dict[str, "Path"]  # generic only, a TyperefOrNum
    # specs (to be implimented by target) and self contained functions
    specs: Dict[str, "Function"]
    includes: typing.List["Path"]  # interfaces satisfied by this record, a Typeref
    as_items: Dict[str, "Path"] = field(default_factory=dict)
    as_functions: Dict[str, "Function"] = field(default_factory=dict)
    is_native: bool = False


@dataclass
class Facet(Node):
    """
    Facet Definition Node - value-type interface (like Protocol but valtype)
    """

    nodetype: NodeType = field(default=NodeType.FACET, init=False)
    parameters: Dict[str, "Path"]  # generic only, a TyperefOrNum
    specs: Dict[str, "Function"]  # specs (to be implemented by conforming valtypes)
    includes: typing.List["Path"]  # interfaces satisfied by this facet
    as_items: Dict[str, "Path"] = field(default_factory=dict)
    as_functions: Dict[str, "Function"] = field(default_factory=dict)
    is_native: bool = False


ExpressionSubTypes = typing.Union[
    "If",
    "For",
    "Do",
    "With",
    "Case",
    "Data",
    "Operation",
    "Call",  # "Array", "List"
    "Reassignment",
    "Swap",
]


# Operation - real Node not required
@dataclass
class Operation(Node):
    """
    Operation - parent of Path and BinOp
    """


@dataclass
class Path(Operation):
    """
    Path - parent of both DottedPath and Atom
    Also a typeref and a typeref_or_num
    """


@dataclass
class Atom(Path):
    """
    Atom Node

    Parent of: Expression (because of AtomExpr), AtomId, AtomString
    """

    # nodetype: NodeType = field(default=NodeType.ATOM, init=False)
    # atom: typing.Union["AtomExpr", "AtomId", "AtomString"]


@dataclass
class Expression(Atom):
    """
    Expression Node
    Parent for all expressions
    if case for do call data operation

    Note that an Expression is an Atom only because of AtomExpr (which is not a
    separate AST node, it is just slightly different syntax for an Expression)
    """

    is_expression: bool = field(default=True, init=False)
    nodetype: NodeType = field(default=NodeType.EXPRESSION, init=False)
    expression: ExpressionSubTypes
    # set by the type checker for control flow expressions (break, continue, error)
    # Uses int to avoid forward reference to CallKind; values match CallKind enum
    call_kind: int = field(default=0, init=False)


@dataclass
class If(Node):
    """
    If Node
    """

    nodetype: NodeType = field(default=NodeType.IF, init=False)
    clauses: typing.List["IfClause"]
    elseclause: Optional["Statement"]
    # set by type checker: variables taken in some arm (name, type) for post-block cleanup
    taken_vars: typing.List[typing.Tuple[str, "Optional[ZType]"]] = field(
        default_factory=list, init=False
    )


@dataclass
class IfClause(Node):
    """
    IfClause Node - represents one condition set and statement for If/Case
    """

    nodetype: NodeType = field(default=NodeType.IFCLAUSE, init=False)
    # name bindings or 'when' arguments (start with space)
    conditions: Dict[str, "Operation"]  # xxTypeDefinition?
    statement: "Statement"  # then statement to execute. Should be optional?


@dataclass
class NamedOperation(Node):
    """
    NamedOperation Node - a named Operation for Call, Data...
    """

    nodetype: NodeType = field(default=NodeType.NAMEDOPERATION, init=False)
    name: Optional[str]  # start points here if provided
    valtype: "Operation"
    # Protocol auto-projection stamps (set by _check_call when the
    # argument is a concrete type conforming to a protocol parameter).
    # None when no projection is required. `projected_label` is the
    # conformance label on the implementor type (e.g., the `:reader`
    # label on `file`).
    projected_protocol: "Optional[ZType]" = field(default=None, init=False)
    projected_label: Optional[str] = field(default=None, init=False)
    # "borrow" or "take" — selected based on the parameter's declared
    # ownership.
    projected_kind: Optional[str] = field(default=None, init=False)


@dataclass
class Case(Node):
    """
    Case Node - represents top Case statement
    """

    nodetype: NodeType = field(default=NodeType.CASE, init=False)
    # subject of the Case clause from 'in'
    subject: "Operation"
    clauses: typing.List["CaseClause"]
    elseclause: Optional["Statement"]
    # set by type checker: subject was .take'd in at least one arm
    subject_taken: bool = field(default=False, init=False)
    # set by type checker: variables taken in some arm (name, type) for post-block cleanup
    taken_vars: typing.List[typing.Tuple[str, "Optional[ZType]"]] = field(
        default_factory=list, init=False
    )


@dataclass
class CaseClause(Node):
    """
    CaseClause Node - represents one condition and statement for Case
    """

    nodetype: NodeType = field(default=NodeType.CASECLAUSE, init=False)
    # name bindings or 'of' arguments (start with space)
    name: str  # xxTypeDefinition?
    match: "AtomId"
    statement: "Statement"  # then statement to execute


@dataclass
class For(Node):
    """
    For Node

    conditions/loop/postconditions are all optional but require at least 1
    condition or a loop
    """

    nodetype: NodeType = field(default=NodeType.FOR, init=False)
    # bindings or 'while' arguments (start with space); can be empty
    conditions: Dict[str, "Operation"]
    loop: Optional["Statement"]  # loop body, (optional)
    # can be empty, no bindings, Operation - must be bool condition
    postconditions: typing.List["Operation"]

    # set by type checker: named bindings whose operation returns option
    # (re-evaluated each iteration, auto-unwrapped, terminates on none)
    iterator_bindings: typing.Set[str] = field(default_factory=set, init=False)


@dataclass
class Do(Node):
    """
    Do Node
    """

    nodetype: NodeType = field(default=NodeType.DO, init=False)
    statement: "Statement"

    # set by type checker: True if the block contains a break expression
    has_break: bool = field(default=False, init=False)


@dataclass
class With(Node):
    """
    With Node - scoped definition with do expression
    'with' label operation 'do' expression
    """

    nodetype: NodeType = field(default=NodeType.WITH, init=False)
    name: str
    value: "Expression"
    doexpr: "Expression"
    # set by the type checker: ownership of the `name` binding. BORROWED for
    # bare-name / dotted-path / .borrow RHS (borrow-by-default); OWNED for
    # call/constructor/.take RHS. Controls destructor emission.
    ownership: Optional[ZOwnership] = field(default=None, init=False)
    # set by the type checker: if non-None, emit `name` as an alias for this
    # C-level expression (bare identifier or `r.f.g`) instead of a real local.
    alias_of: Optional[str] = field(default=None, init=False)


@unique
class CallKind(IntEnum):
    """Classification of a Call node, set by the type checker."""

    UNKNOWN = 0
    REGULAR = 1  # regular function call
    RETURN = 2  # return statement
    RECORD_CREATE = 3  # record construction
    CLASS_CREATE = 4  # class construction
    UNION_CREATE = 5  # union subtype construction
    PROTOCOL_CREATE = 6  # protocol.create/take from: expr
    PROTOCOL_BORROW = 7  # protocol.borrow from: expr
    FACET_CREATE = 8  # facet.create/take from: expr
    FACET_BORROW = 9  # facet.borrow from: expr
    TYPEDEF_CREATE = 10  # typedef.create/take from: expr
    TYPEDEF_BORROW = 11  # typedef.borrow from: expr
    CALLABLE = 12  # callable object dispatch (object with 'call' method)
    UNIT_INSTANTIATE = 13  # generic unit instantiation: (myunit t: i64)
    BOX_CREATE = 14  # box from: val (valtype boxing)
    BOX_PASSTHROUGH = 15  # box from: val (reftype passthrough — just take ownership)
    BREAK = 16  # break statement
    CONTINUE = 17  # continue statement
    ERROR = 18  # error statement


@dataclass
class Call(Operation):
    """
    Call Node - represents an executable call and also a type reference (in
    type context).

    Subclasses Operation because grammar `operation = binop | (term binop)`
    — the `(term binop)` alternative is materialised as a Call with a
    single unnamed argument, so any site accepting an Operation must also
    accept that Call form.
    """

    nodetype: NodeType = field(default=NodeType.CALL, init=False)
    callable: "Path"
    # requires at least one argument (otherwise it is an operation
    # even though it could still be a call with 0 args)
    arguments: typing.List["NamedOperation"]

    # set by type checker to classify the call for the emitter
    call_kind: CallKind = field(default=CallKind.UNKNOWN, init=False)

    # for CALLABLE kind: the type name of the callable object (to construct C method name)
    callable_type_name: Optional[str] = field(default=None, init=False)


@dataclass
class Data(Node):
    """
    Data Node
    """

    nodetype: NodeType = field(default=NodeType.DATA, init=False)
    data: typing.List["NamedOperation"]  # data, change to dict?


@dataclass
class BinOp(Operation):
    """
    BinOp - binary operation
    Left recursive
    """

    nodetype: NodeType = field(default=NodeType.BINOP, init=False)
    lhs: "Operation"
    operator: "AtomId"
    rhs: "Path"


@dataclass
class Statement(Node):
    """
    Statement Node
    A code block. Either a statement block in braces or a single Operation

    Syntactically, a single expression is an Operation, but this can be stored
    in an Assignment Expression (with no Label/Atomid)
    """

    nodetype: NodeType = field(default=NodeType.STATEMENT, init=False)
    # must retain order, definitions may have empty names
    statements: typing.List["StatementLine"]


@dataclass
class StatementLine(Node):
    """
    StatementLine Node
    A line in a code block.
    break continue return assignment expression
    """

    nodetype: NodeType = field(default=NodeType.STATEMENTLINE, init=False)
    statementline: typing.Union[
        "Assignment",
        "Reassignment",
        "Swap",
        "Expression",
    ]


@dataclass
class Assignment(Node):
    """
    Assignment Node - create a new variable definition
    """

    nodetype: NodeType = field(default=NodeType.ASSIGNMENT, init=False)
    name: str  # also in start     # xxTypeDefinition?
    value: "Expression"  # source expression
    # set by the type checker: if non-None, emit `name` as an alias for this
    # C-level expression (bare identifier or `r.f.g`) instead of a real local.
    # Only set when the source path is stable for the binding's lifetime
    # (borrow-locked or take-invalidated) and no reftype pointer is
    # dereferenced along the way.
    alias_of: Optional[str] = field(default=None, init=False)


@dataclass
class Reassignment(Node):
    """
    Reassignment Node - update/change an existing variable
    """

    nodetype: NodeType = field(default=NodeType.REASSIGNMENT, init=False)
    topath: "Path"
    value: "Expression"  # source expression


@dataclass
class Swap(Node):
    """
    Swap Node - swap two owned reference types
    """

    nodetype: NodeType = field(default=NodeType.SWAP, init=False)
    lhs: "Path"
    rhs: "Path"


@dataclass
class DottedPath(Path):
    """
    DottedPath Node
    Note that a simple Atom is also a Path
    """

    nodetype: NodeType = field(default=NodeType.DOTTEDPATH, init=False)
    parent: "Path"
    child: "AtomId"
    parent_tagged_type: "Optional[ZType]" = field(default=None, init=False)
    narrowed_subtype: "Optional[str]" = field(default=None, init=False)
    # Phase 7b: child id stamped at typecheck against parent's ZType. -1
    # when unstamped — emitter falls back to name lookup in that case.
    child_id: int = field(default=-1, init=False)


@dataclass
class AtomId(Atom):
    """
    AtomId Node
    """

    nodetype: NodeType = field(default=NodeType.ATOMID, init=False)
    name: str  # this is also in the start token
    # narrowing stamp — set by typecheck when this AtomId references a
    # variable narrowed in an enclosing match arm. The emitter reads these
    # to decide whether to emit a payload-unwrap in place of the bare name.
    narrowed_subtype: "Optional[str]" = field(default=None, init=False)
    original_ztype: "Optional[ZType]" = field(default=None, init=False)
    # Phase 7b: child id against a contextually-known parent type (e.g.
    # the scrutinee's union type inside a match clause). -1 when unstamped.
    child_id: int = field(default=-1, init=False)


@dataclass
class LabelValue(AtomId):
    """Label value (:x) — shorthand for x: x where x doesn't bind to itself."""

    nodetype: NodeType = field(default=NodeType.LABELVALUE, init=False)


@dataclass
class AtomString(Atom):
    """
    AtomString Node
    An Atom comprising a sequence of string part tokens
    atomstring and atomstringraw
    """

    nodetype: NodeType = field(default=NodeType.ATOMSTRING, init=False)
    # bit messy... Token for literal parts, Expression for strexpr
    stringparts: typing.List[typing.Union["Token", "Expression"]]


# class NodeTable:
#     """
#     NodeTable - table of all ast nodes for a program
#     """

#     def __init__(self):
#         self._table: List[Node] = []
#         self._lock = threading.Lock()

#     def __getitem__(self, index: NodeID) -> Node:
#         """
#         return a node by its index/id
#         """
#         return self._table[index]

#     def _append(self, node: Node) -> NodeID:
#         """
#         _append - append a new Node into the table returning its id

#         This method is locked to prevent a race if threaded
#         """
#         with self._lock:
#             idx = NodeID(len(self._table))
#             self._table.append(node)  # will be appended at idx
#         return idx

#     def unit(
#         self,
#         token: Token,
#         definitions: Dict[str, Expression],
#     ) -> NodeID:
#         """
#         unit - create and add a unit.

#         Return the NodeID of the created node
#         """
#         node = Unit(
#             token=token,
#             definitions=definitions,
#         )
#         return self._append(node)
