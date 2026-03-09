"""
AST Nodes and types
"""

# import threading
from dataclasses import dataclass, field
from enum import IntEnum, unique
import typing
from typing import Optional, Dict, NewType, cast, Callable
from itertools import count

# from collections import OrderedDict  # pylint: disable=W0611
# import ztypechecker
import zvfs
from zlexer import Token
from ztypechecker import ZType


@unique
class ERR(IntEnum):
    """
    List of numeric error codes
    """

    COMPILERERROR = 1  # something that shouldn't happen. error in compiler
    FILENOTFOUND = 2
    DUPLICATEDEF = 3
    BADUNITNAME = 4
    BADUNIT = 5
    BADFUNCTION = 6
    IOERROR = 7
    REFNOTFOUND = 8
    # requested parser production not found
    # parser can recover from these and end the current producion
    # or try another one. Do NOT return this if tokens have been consumed
    # (convert to a different fatal error)
    # PRODUCTIONNOTFOUND = 9 # use None return instead
    EXPECTEDDEF = 10
    EXPECTEDEXP = 12
    EXPECTEDOP = 13
    EXPECTEDTYPEDEF = 14
    EXPECTEDSTATEMENT = 15

    BADARGUMENT = 17
    BADARGUMENTBLOCK = 18
    BADEXPRESSION = 19
    BADOPERATION = 20
    BADSTRING = 21
    BADPATH = 22
    BADREFERENCE = 23
    BADCALL = 24
    BADPARAMETER = 25
    BADPARAMETERBLOCK = 26
    BADOBJECTBLOCK = 27
    BADITEM = 28
    BADTHEN = 29
    BADELSE = 30
    BADCASE = 31
    BADFOR = 32
    BADDATA = 34
    BADSTATEMENT = 35


@dataclass
class Error:
    """
    Error - is not an AST Node

    err = ERR numeric error code
    msg = parser error message
    """

    err: ERR
    msg: str
    loc: Optional[Token]


def errortomessage(err: Error, vfs: zvfs.ZVfs) -> str:
    """
    errortomessage - convert an error to a message that can be printed to the
    console
    """
    result = []
    if err.loc:
        loc = err.loc
        result.append(f"ERROR: {err.err.name} {err.msg}")
        path = vfs.pathfromprovider(loc.fsno)
        result.append(f'In file "{path}", line {loc.lineno}, column {loc.colno}')
        line = vfs.getline(loc.fsno, loc.lineno)
        if line:
            result.append(line.rstrip())
            marker = (" " * (loc.colno - 1)) + ("^" * len(loc.tokstr))
            result.append(marker)
    else:
        result.append(f"ERROR: {err.err.name}")
        result.append(err.msg)

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
    ATOMNUMBER = 83
    ATOMSTRING = 84


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

    vfs: zvfs.ZVfs  # vfs for reading source files. Needed to report errors
    units: Dict[
        str, "Unit"
    ]  # TODO: change this into a single top level unit (not a dict)
    mainunitname: str


# a typesafe node id
NodeID = NewType("NodeID", int)


@dataclass
class Node:
    """
    Node - the parent of the Ast Node type hierarchy. Do not instantiate
    directly
    """

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

    start: Token  # start location in the source for this Node


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
    # parameters - both normal and generic in same frame
    parameters: Dict[
        str, "Path"
    ]  # really, a TyperefOrNum            # xxTypeDefinition?
    body: Optional["Statement"]  # None for Spec


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


@dataclass
class Facet(Node):
    """
    Facet Definition Node - similar to protocol/record
    """

    nodetype: NodeType = field(default=NodeType.FACET, init=False)
    items: Dict[str, "Path"]
    implements: typing.List["Path"]
    functions: Dict[str, "Function"]
    as_items: Dict[str, "Path"]
    as_functions: Dict[str, "Function"]


ExpressionSubTypes = typing.Union[
    "If",
    "For",
    "Do",
    "With",
    "Case",
    "Data",
    "Operation",
    "Call",  # "Array", "List"
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

    Parent of: Expression (because of AtomExpr), AtomId, AtomNumber, AtomString
    """

    # nodetype: NodeType = field(default=NodeType.ATOM, init=False)
    # atom: typing.Union["AtomExpr", "AtomId", "AtomNumber", "AtomString"]


@dataclass
class Expression(Atom):
    """
    Expression Node
    Parent for all expressions
    if case for do call data operation

    Note that an Expression is an Atom only because of AtomExpr (which is not a
    separate AST node, it is just slightly different syntax for an Expression)
    """

    nodetype: NodeType = field(default=NodeType.EXPRESSION, init=False)
    expression: ExpressionSubTypes


@dataclass
class If(Node):
    """
    If Node
    """

    nodetype: NodeType = field(default=NodeType.IF, init=False)
    clauses: typing.List["IfClause"]
    elseclause: Optional["Statement"]


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


@dataclass
class Do(Node):
    """
    Do Node
    """

    nodetype: NodeType = field(default=NodeType.DO, init=False)
    statement: "Statement"


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


@dataclass
class Call(Node):
    """
    Call Node - represents and executable call and also a type reference (in
    type context)
    """

    nodetype: NodeType = field(default=NodeType.CALL, init=False)
    callable: "Path"
    # requires at least one argument (otherwise it is an operation
    # even though it could still be a call with 0 args)
    arguments: typing.List["NamedOperation"]


@dataclass
class Data(Node):
    """
    Data Node
    """

    nodetype: NodeType = field(default=NodeType.DATA, init=False)
    # generic, can be a generic reference or constant numeric expression
    subtype: Optional["Path"]  # inferred if None, a Typeref
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


@dataclass
class AtomId(Atom):
    """
    AtomId Node

    canbemoduleref is True by default but for single Id's used as call values -
    these cannot be module references (because modules are not first class
    values). This prevents the parser from attempting to find a module of this
    name.

    """

    nodetype: NodeType = field(default=NodeType.ATOMID, init=False)
    name: str  # this is also in the start token
    canbemoduleref: bool


@dataclass
class AtomNumber(Atom):
    """
    AtomNumber Node - Numeric Literal. Literal value is stored in the type.
    """

    nodetype: NodeType = field(default=NodeType.ATOMNUMBER, init=False)
    # start has number id token


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
