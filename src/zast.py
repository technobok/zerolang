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
# import ztypes
import zvfs
from zvfs import DEntryID
from zlexer import Token, TT


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
    YIELD = 41
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

    # Type-position node. Internal-only -- produced by the
    # generator desugarer in src/zgenerator.py to defer a synth-
    # class field's declared type to the typechecker. Never
    # produced by the parser.
    TYPEOFEXPR = 70

    # ATOM's:
    # ATOM = 80
    # ATOMEXPR = 81
    # Expression is also an ATOM for AST due to AtomExpr
    ATOMID = 82  # a reference
    LABELVALUE = 83  # label value (:x)

    ATOMSTRING = 84
    STRINGCHUNK = 85  # literal text segment inside an interpolated string

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


@dataclass(frozen=True)
class Program:
    """
    Program - top level, holds all Units and streams (files).

    Parser output, immutable post-parse (`@dataclass(frozen=True)`).
    Typecheck output lives on `ztyping.ZTyping`.

    Two siblings carry `is_error: bool` to discriminate parser
    results: `Program.is_error = False`, `Error.is_error = True`.
    Callers do `if obj.is_error:` to distinguish without
    `isinstance`. This is parser-discriminator state, not typecheck
    state — typecheck never writes to it.
    """

    is_error: bool = field(default=False, init=False)
    vfs: zvfs.ZVfs  # vfs for reading source files. Needed to report errors
    # File-level units, keyed by unit name. `mainunitname` points at
    # the entry unit. The dict shape mirrors how the parser
    # materialises imports.
    units: Dict[str, "Unit"]
    mainunitname: str


def clone_function(func: "Function") -> "Function":
    """Deep clone a Function AST subtree for monomorphization.

    Walks the parsed-AST per `NodeType` and reconstructs every node
    via its dataclass constructor. Each clone gets a fresh `nodeid`
    via the default_factory so it does not collide with the original
    in nodeid-keyed side-tables. `synth_origin` is preserved as a
    constructor kwarg. Tokens (`start`) are shared by reference;
    re-issuing them would break diagnostics that point at the
    original source.
    """
    return cast("Function", _clone_node(func))


def _clone_list(items):
    """Clone every Node in a list, returning a fresh list. Loop form
    rather than a comprehension because bootstrap-lint caps list
    comprehensions and the AST-clone visitor is on the no-new-comps
    side of the ratchet (Python idiom we're moving away from for
    self-hosting portability). Untyped parameter because callers
    pass `List[<NodeSubclass>]` for various subclasses; the
    invariant-typed `List[Node]` declaration would not accept those
    under Python's strict invariant container rules."""
    out = []
    for item in items:
        out.append(_clone_node(item))
    return out


def _clone_dict(items):
    """Clone every Node in a Dict[str, Node], returning a fresh dict.
    Loop form for the same reason as `_clone_list`; untyped for the
    same dict-invariance reason."""
    out = {}
    for k, v in items.items():
        out[k] = _clone_node(v)
    return out


def _clone_node(node: "Node") -> "Node":
    """Recursive AST clone, dispatching on `node.nodetype`. Mutable
    container fields (List, Dict) are reconstructed; non-Node
    children (Tokens, strings, bools) are shared by reference."""
    nt = node.nodetype
    so = node.synth_origin
    if nt == NodeType.UNIT:
        u = cast(Unit, node)
        return Unit(
            body=cast("Dict[str, TypeDefinition]", _clone_dict(u.body)),
            start=u.start,
            synth_origin=so,
        )
    if nt == NodeType.FUNCTION:
        fn = cast(Function, node)
        return Function(
            returntype=cast(
                Optional[Path],
                _clone_node(fn.returntype) if fn.returntype is not None else None,
            ),
            parameters=cast("Dict[str, Path]", _clone_dict(fn.parameters)),
            body=cast(
                Optional[Statement],
                _clone_node(fn.body) if fn.body is not None else None,
            ),
            is_native=fn.is_native,
            as_items=_clone_dict(fn.as_items),
            start=fn.start,
            synth_origin=so,
        )
    if nt in (
        NodeType.RECORD,
        NodeType.CLASS,
        NodeType.UNION,
        NodeType.VARIANT,
        NodeType.ENUM,
        NodeType.PROTOCOL,
        NodeType.FACET,
    ):
        od = cast(ObjectDef, node)
        return ObjectDef(
            nodetype=od.nodetype,
            is_items=_clone_dict(od.is_items),
            as_items=_clone_dict(od.as_items),
            is_native=od.is_native,
            start=od.start,
            synth_origin=so,
        )
    if nt == NodeType.EXPRESSION:
        e = cast(Expression, node)
        return Expression(
            expression=cast(ExpressionSubTypes, _clone_node(e.expression)),
            start=e.start,
            synth_origin=so,
        )
    if nt == NodeType.IF:
        ifn = cast(If, node)
        return If(
            clauses=cast("typing.List[IfClause]", _clone_list(ifn.clauses)),
            elseclause=cast(
                Optional[Statement],
                _clone_node(ifn.elseclause) if ifn.elseclause is not None else None,
            ),
            start=ifn.start,
            synth_origin=so,
        )
    if nt == NodeType.IFCLAUSE:
        ic = cast(IfClause, node)
        return IfClause(
            conditions=cast("Dict[str, Operation]", _clone_dict(ic.conditions)),
            statement=cast(Statement, _clone_node(ic.statement)),
            start=ic.start,
            synth_origin=so,
        )
    if nt == NodeType.NAMEDOPERATION:
        no = cast(NamedOperation, node)
        return NamedOperation(
            name=no.name,
            valtype=cast(Operation, _clone_node(no.valtype)),
            start=no.start,
            synth_origin=so,
        )
    if nt == NodeType.CASE:
        cn = cast(Case, node)
        return Case(
            subject=cast(Operation, _clone_node(cn.subject)),
            clauses=cast("typing.List[CaseClause]", _clone_list(cn.clauses)),
            elseclause=cast(
                Optional[Statement],
                _clone_node(cn.elseclause) if cn.elseclause is not None else None,
            ),
            start=cn.start,
            synth_origin=so,
        )
    if nt == NodeType.CASECLAUSE:
        cc = cast(CaseClause, node)
        return CaseClause(
            name=cc.name,
            match=cast(AtomId, _clone_node(cc.match)),
            statement=cast(Statement, _clone_node(cc.statement)),
            start=cc.start,
            synth_origin=so,
        )
    if nt == NodeType.FOR:
        fr = cast(For, node)
        return For(
            conditions=cast("Dict[str, Operation]", _clone_dict(fr.conditions)),
            loop=cast(
                Optional[Statement],
                _clone_node(fr.loop) if fr.loop is not None else None,
            ),
            postconditions=cast(
                "typing.List[Operation]", _clone_list(fr.postconditions)
            ),
            start=fr.start,
            synth_origin=so,
        )
    if nt == NodeType.DO:
        d = cast(Do, node)
        return Do(
            statement=cast(Statement, _clone_node(d.statement)),
            start=d.start,
            synth_origin=so,
        )
    if nt == NodeType.WITH:
        w = cast(With, node)
        return With(
            name=w.name,
            value=cast(Expression, _clone_node(w.value)),
            doexpr=cast(Expression, _clone_node(w.doexpr)),
            start=w.start,
            synth_origin=so,
        )
    if nt == NodeType.CALL:
        c = cast(Call, node)
        return Call(
            callable=cast(Path, _clone_node(c.callable)),
            arguments=cast("typing.List[NamedOperation]", _clone_list(c.arguments)),
            start=c.start,
            synth_origin=so,
        )
    if nt == NodeType.DATA:
        dn = cast(Data, node)
        return Data(
            data=cast("typing.List[NamedOperation]", _clone_list(dn.data)),
            start=dn.start,
            synth_origin=so,
        )
    if nt == NodeType.YIELD:
        y = cast(Yield, node)
        return Yield(
            expr=cast(Expression, _clone_node(y.expr)),
            start=y.start,
            synth_origin=so,
        )
    if nt == NodeType.BINOP:
        bo = cast(BinOp, node)
        return BinOp(
            lhs=cast(Operation, _clone_node(bo.lhs)),
            operator=cast(AtomId, _clone_node(bo.operator)),
            rhs=cast(Path, _clone_node(bo.rhs)),
            start=bo.start,
            synth_origin=so,
        )
    if nt == NodeType.TYPEOFEXPR:
        toe = cast(TypeOfExpr, node)
        return TypeOfExpr(
            source=cast(Operation, _clone_node(toe.source)),
            start=toe.start,
            synth_origin=so,
        )
    if nt == NodeType.STATEMENT:
        s = cast(Statement, node)
        return Statement(
            statements=cast("typing.List[StatementLine]", _clone_list(s.statements)),
            start=s.start,
            synth_origin=so,
        )
    if nt == NodeType.STATEMENTLINE:
        sl2 = cast(StatementLine, node)
        return StatementLine(
            statementline=cast(
                "typing.Union[Assignment, Reassignment, Swap, Expression]",
                _clone_node(sl2.statementline),
            ),
            start=sl2.start,
            synth_origin=so,
        )
    if nt == NodeType.ASSIGNMENT:
        a = cast(Assignment, node)
        return Assignment(
            name=a.name,
            value=cast(Expression, _clone_node(a.value)),
            start=a.start,
            synth_origin=so,
        )
    if nt == NodeType.REASSIGNMENT:
        ra = cast(Reassignment, node)
        return Reassignment(
            topath=cast(Path, _clone_node(ra.topath)),
            value=cast(Expression, _clone_node(ra.value)),
            start=ra.start,
            synth_origin=so,
        )
    if nt == NodeType.SWAP:
        sw = cast(Swap, node)
        return Swap(
            lhs=cast(Path, _clone_node(sw.lhs)),
            rhs=cast(Path, _clone_node(sw.rhs)),
            start=sw.start,
            synth_origin=so,
        )
    if nt == NodeType.DOTTEDPATH:
        dp = cast(DottedPath, node)
        return DottedPath(
            parent=cast(Path, _clone_node(dp.parent)),
            child=cast(AtomId, _clone_node(dp.child)),
            start=dp.start,
            synth_origin=so,
        )
    if nt == NodeType.ATOMID:
        ai = cast(AtomId, node)
        return AtomId(name=ai.name, start=ai.start, synth_origin=so)
    if nt == NodeType.LABELVALUE:
        lv = cast(LabelValue, node)
        return LabelValue(name=lv.name, start=lv.start, synth_origin=so)
    if nt == NodeType.STRINGCHUNK:
        sc = cast(StringChunk, node)
        return StringChunk(
            text=sc.text,
            chunk_kind=sc.chunk_kind,
            start=sc.start,
            synth_origin=so,
        )
    if nt == NodeType.ATOMSTRING:
        as_ = cast(AtomString, node)
        return AtomString(
            stringparts=_clone_list(as_.stringparts),
            start=as_.start,
            synth_origin=so,
        )
    if nt == NodeType.ERROR:
        er = cast(Error, node)
        return Error(
            err=er.err,
            msg=er.msg,
            note=er.note,
            hint=er.hint,
            start=er.start,
            synth_origin=so,
        )
    raise AssertionError(f"_clone_node: unhandled NodeType {nt}")


# a typesafe node id
NodeID = NewType("NodeID", int)

# Module-level Node id generator
_next_node_id = count()


@dataclass(frozen=True)
class Node:
    """
    Node - the parent of the Ast Node type hierarchy. Do not instantiate
    directly
    """

    nodeid: NodeID = field(
        default_factory=cast(Callable[[], NodeID], _next_node_id.__next__),
        init=False,
    )
    nodetype: NodeType
    start: Token  # start location in the source for this Node
    # provenance: None for nodes parsed from user source; pass-name
    # string for nodes synthesised by a compiler pass. Surfaces in
    # SQL dumps. `kw_only=True` so synthesis passes can set it via
    # the constructor without disturbing the positional argument
    # order of Node subclasses — Node is frozen, so synth_origin
    # must land at construction time.
    synth_origin: Optional[str] = field(default=None, kw_only=True)


@dataclass(frozen=True)
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
    "ObjectDef",
    "Expression",
    "Operation",
    "LabelValue",
]


@dataclass(frozen=True)
class Unit(Node):
    """
    Unit Node (unit or unitfile)
    """

    nodetype: NodeType = field(default=NodeType.UNIT, init=False)
    # type definitions and generic parameters all included here
    # body: Dict[str, typing.Union["Definition", "Unit"]]
    body: Dict[str, TypeDefinition]  # xxTypeDefinition?


@dataclass(frozen=True)
class Function(Node):
    """
    Function Node (or spec)
    """

    nodetype: NodeType = field(default=NodeType.FUNCTION, init=False)
    returntype: Optional["Path"]  # really a Typeref
    # parameters - normal (non-generic) parameters. Each path may
    # carry a `.take` / `.borrow` / `.lock` suffix (recognised by
    # the type checker during resolution).
    parameters: Dict[
        str, "Path"
    ]  # really, a TyperefOrNum            # xxTypeDefinition?
    body: Optional["Statement"]  # None for Spec
    # native function: body is compiler-provided (not a spec)
    is_native: bool = False
    # 'as' clause: generic parameters and static functions —
    # heterogeneous Dict[str, Node] (Path, Function, etc.)
    as_items: Dict[str, "Node"] = field(default_factory=dict)

    def as_functions(self) -> Dict[str, "Function"]:
        """Static functions in the function's `as` block."""
        return {
            n: cast("Function", v)
            for n, v in self.as_items.items()
            if v.nodetype == NodeType.FUNCTION
        }

    def as_paths(self) -> Dict[str, "Path"]:
        """`as`-block members that are paths (typically generic
        parameter declarations)."""
        return {
            n: cast("Path", v)
            for n, v in self.as_items.items()
            if v.nodetype != NodeType.FUNCTION
        }


@dataclass(frozen=True)
class ObjectDef(Node):
    """
    Unified type-definition node. `nodetype` discriminates which
    kind of object this is:

    Shape mirrors the grammar:

        item: keyword [ "is" ] "{" is_items "}" [ "as" "{" as_items "}" ]

    Both `is_items` and `as_items` are heterogeneous dicts holding
    the labelled members of each block. Each value is one of:

    - `Path` (typeref / generic param / `:LabelValue`)
    - `Function` (instance or static method)
    - `Unit` (inline subunit, e.g. `public: unit { ... }`)

    Per nodetype:
    - RECORD / CLASS: `is_items` are fields + methods;
      `as_items` are static members + protocol conformances
    - VARIANT / UNION: `is_items` are arms + methods;
      `as_items` adds the optional `.tag` discriminator
    - ENUM: `is_items` are values
    - PROTOCOL / FACET: `is_items` are generic params + spec
      methods; `as_items` static members

    Field-type ownership annotations (e.g. `x: Foo.lock`) ride on
    the path stored in `is_items` and are recognised by the type
    checker via `_strip_path_ownership` in ztypecheck.py.

    `is_native` flags compiler-provided implementations.
    """

    is_items: Dict[str, "Node"] = field(default_factory=dict)
    as_items: Dict[str, "Node"] = field(default_factory=dict)
    is_native: bool = False

    # ---- Filtered accessors (helpers for kind-specific iteration) ----
    # These return derived dicts filtered by nodetype — call them
    # when you want only methods or only paths; iterate `is_items`
    # / `as_items` directly when you want everything.

    def functions(self) -> Dict[str, "Function"]:
        """Methods declared in the `is` block."""
        return {
            n: cast("Function", v)
            for n, v in self.is_items.items()
            if v.nodetype == NodeType.FUNCTION
        }

    def as_functions(self) -> Dict[str, "Function"]:
        """Static methods declared in the `as` block."""
        return {
            n: cast("Function", v)
            for n, v in self.as_items.items()
            if v.nodetype == NodeType.FUNCTION
        }

    def is_paths(self) -> Dict[str, "Path"]:
        """`is`-block members that are paths (fields/arms/values) —
        every entry except methods."""
        return {
            n: cast("Path", v)
            for n, v in self.is_items.items()
            if v.nodetype != NodeType.FUNCTION
        }

    def as_paths(self) -> Dict[str, "Path"]:
        """`as`-block members that are paths (statics/conformances/
        the optional `.tag`) — every entry except static methods."""
        return {
            n: cast("Path", v)
            for n, v in self.as_items.items()
            if v.nodetype != NodeType.FUNCTION
        }


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
    "Yield",
]


# Operation - real Node not required
@dataclass(frozen=True)
class Operation(Node):
    """
    Operation - parent of Path and BinOp
    """


@dataclass(frozen=True)
class Path(Operation):
    """
    Path - parent of both DottedPath and Atom
    Also a typeref and a typeref_or_num
    """


@dataclass(frozen=True)
class Atom(Path):
    """
    Atom Node

    Parent of: Expression (because of AtomExpr), AtomId, AtomString
    """

    # nodetype: NodeType = field(default=NodeType.ATOM, init=False)
    # atom: typing.Union["AtomExpr", "AtomId", "AtomString"]


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class If(Node):
    """
    If Node
    """

    nodetype: NodeType = field(default=NodeType.IF, init=False)
    clauses: typing.List["IfClause"]
    elseclause: Optional["Statement"]


@dataclass(frozen=True)
class IfClause(Node):
    """
    IfClause Node - represents one condition set and statement for If/Case
    """

    nodetype: NodeType = field(default=NodeType.IFCLAUSE, init=False)
    # name bindings or 'when' arguments (start with space)
    conditions: Dict[str, "Operation"]  # xxTypeDefinition?
    statement: "Statement"  # then statement to execute. Should be optional?


@dataclass(frozen=True)
class NamedOperation(Node):
    """
    NamedOperation Node - a named Operation for Call, Data...
    """

    nodetype: NodeType = field(default=NodeType.NAMEDOPERATION, init=False)
    name: Optional[str]  # start points here if provided
    valtype: "Operation"


@dataclass(frozen=True)
class Case(Node):
    """
    Case Node - represents top Case statement
    """

    nodetype: NodeType = field(default=NodeType.CASE, init=False)
    # subject of the Case clause from 'in'
    subject: "Operation"
    clauses: typing.List["CaseClause"]
    elseclause: Optional["Statement"]


@dataclass(frozen=True)
class CaseClause(Node):
    """
    CaseClause Node - represents one condition and statement for Case
    """

    nodetype: NodeType = field(default=NodeType.CASECLAUSE, init=False)
    # name bindings or 'of' arguments (start with space)
    name: str  # xxTypeDefinition?
    match: "AtomId"
    statement: "Statement"  # then statement to execute


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Do(Node):
    """
    Do Node
    """

    nodetype: NodeType = field(default=NodeType.DO, init=False)
    statement: "Statement"


@dataclass(frozen=True)
class With(Node):
    """
    With Node - scoped definition with do expression
    'with' label operation 'do' expression
    """

    nodetype: NodeType = field(default=NodeType.WITH, init=False)
    name: str
    value: "Expression"
    doexpr: "Expression"


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
    PANIC = 19  # panic call (runtime terminal error)


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Data(Node):
    """
    Data Node
    """

    nodetype: NodeType = field(default=NodeType.DATA, init=False)
    data: typing.List["NamedOperation"]  # data, change to dict?


@dataclass(frozen=True)
class Yield(Node):
    """Yield Node — a suspension point in a generator function.

    Statement form:
        yield <expr>            # StatementLine wraps Expression(Yield)
    Expression form:
        <name>: yield <expr>    # Assignment(value = Expression(Yield))

    The wrapping context distinguishes the two forms; the Yield node
    itself is identical. `expr` carries the value being yielded out.
    """

    nodetype: NodeType = field(default=NodeType.YIELD, init=False)
    expr: "Expression"


@dataclass(frozen=True)
class BinOp(Operation):
    """
    BinOp - binary operation
    Left recursive
    """

    nodetype: NodeType = field(default=NodeType.BINOP, init=False)
    lhs: "Operation"
    operator: "AtomId"
    rhs: "Path"


@dataclass(frozen=True)
class TypeOfExpr(Path):
    """
    TypeOfExpr - type-position node whose declared type is the
    resolved type of the embedded `source` expression.

    Internal-only. Produced by the generator desugarer in
    src/zgenerator.py to declare a yield-crossing local's
    synth-class field type without pre-typecheck guessing. Never
    produced by the parser; users cannot write this directly.

    The embedded source references locals/parameters in the
    enclosing function body, so resolution is deferred until the
    typechecker has typed that body. Treated as a Path for AST
    purposes (it appears in the same syntactic slot as a class
    field's declared type).
    """

    nodetype: NodeType = field(default=NodeType.TYPEOFEXPR, init=False)
    source: "Operation"


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Assignment(Node):
    """
    Assignment Node - create a new variable definition
    """

    nodetype: NodeType = field(default=NodeType.ASSIGNMENT, init=False)
    name: str  # also in start     # xxTypeDefinition?
    value: "Expression"  # source expression


@dataclass(frozen=True)
class Reassignment(Node):
    """
    Reassignment Node - update/change an existing variable
    """

    nodetype: NodeType = field(default=NodeType.REASSIGNMENT, init=False)
    topath: "Path"
    value: "Expression"  # source expression


@dataclass(frozen=True)
class Swap(Node):
    """
    Swap Node - swap two owned reference types
    """

    nodetype: NodeType = field(default=NodeType.SWAP, init=False)
    lhs: "Path"
    rhs: "Path"


@dataclass(frozen=True)
class DottedPath(Path):
    """
    DottedPath Node
    Note that a simple Atom is also a Path
    """

    nodetype: NodeType = field(default=NodeType.DOTTEDPATH, init=False)
    parent: "Path"
    child: "AtomId"


@dataclass(frozen=True)
class AtomId(Atom):
    """
    AtomId Node
    """

    nodetype: NodeType = field(default=NodeType.ATOMID, init=False)
    name: str  # this is also in the start token


@dataclass(frozen=True)
class LabelValue(AtomId):
    """Label value (:x) — shorthand for x: x where x doesn't bind to itself."""

    nodetype: NodeType = field(default=NodeType.LABELVALUE, init=False)


@dataclass(frozen=True)
class StringChunk(Node):
    """
    A literal text segment of an interpolated string. Carries the
    chunk's text plus its source position (`start: Token`, inherited
    from Node). `chunk_kind` is the lexer token type that produced
    the chunk (TT.STRMID for plain text, TT.STRCHR for an escape
    like `\\n`, TT.EOL for a literal newline) — preserved so the
    emitter can decide how to format each chunk.
    """

    nodetype: NodeType = field(default=NodeType.STRINGCHUNK, init=False)
    text: str
    chunk_kind: TT


@dataclass(frozen=True)
class AtomString(Atom):
    """
    AtomString Node — an Atom comprising the ordered parts of an
    interpolated string literal. Each part is either:

    - `StringChunk` — a literal text segment
    - `Expression` — an embedded `\\{...}` interpolation

    Parts are stored in source order so downstream consumers can
    walk them sequentially.
    """

    nodetype: NodeType = field(default=NodeType.ATOMSTRING, init=False)
    stringparts: typing.List["Node"]


def node_children(node: "Node") -> "typing.List[Node]":
    """Return all direct child Nodes of `node`. Drives generic walkers
    (zsqldump, ztypecheck consistency check) without
    `__dataclass_fields__` reflection.

    Tokens embedded in `node` (e.g. `node.start`, AtomString stringparts)
    are NOT included; use `node_tokens` for the latter.
    """
    nt = node.nodetype
    out: "typing.List[Node]" = []
    if nt == NodeType.UNIT:
        out.extend(cast(Unit, node).body.values())
        return out
    if nt == NodeType.FUNCTION:
        fn = cast(Function, node)
        if fn.returntype is not None:
            out.append(fn.returntype)
        out.extend(fn.parameters.values())
        if fn.body is not None:
            out.append(fn.body)
        out.extend(fn.as_items.values())
        return out
    if nt in (
        NodeType.RECORD,
        NodeType.CLASS,
        NodeType.UNION,
        NodeType.VARIANT,
        NodeType.ENUM,
        NodeType.PROTOCOL,
        NodeType.FACET,
    ):
        od = cast(ObjectDef, node)
        out.extend(od.is_items.values())
        out.extend(od.as_items.values())
        return out
    if nt == NodeType.EXPRESSION:
        out.append(cast(Expression, node).expression)
        return out
    if nt == NodeType.IF:
        ifn = cast(If, node)
        out.extend(ifn.clauses)
        if ifn.elseclause is not None:
            out.append(ifn.elseclause)
        return out
    if nt == NodeType.IFCLAUSE:
        ic = cast(IfClause, node)
        out.extend(ic.conditions.values())
        out.append(ic.statement)
        return out
    if nt == NodeType.NAMEDOPERATION:
        out.append(cast(NamedOperation, node).valtype)
        return out
    if nt == NodeType.CASE:
        cn = cast(Case, node)
        out.append(cn.subject)
        out.extend(cn.clauses)
        if cn.elseclause is not None:
            out.append(cn.elseclause)
        return out
    if nt == NodeType.CASECLAUSE:
        cc = cast(CaseClause, node)
        out.append(cc.match)
        out.append(cc.statement)
        return out
    if nt == NodeType.FOR:
        fn2 = cast(For, node)
        out.extend(fn2.conditions.values())
        if fn2.loop is not None:
            out.append(fn2.loop)
        out.extend(fn2.postconditions)
        return out
    if nt == NodeType.DO:
        out.append(cast(Do, node).statement)
        return out
    if nt == NodeType.WITH:
        w = cast(With, node)
        out.append(w.value)
        out.append(w.doexpr)
        return out
    if nt == NodeType.CALL:
        c = cast(Call, node)
        out.append(c.callable)
        out.extend(c.arguments)
        return out
    if nt == NodeType.DATA:
        out.extend(cast(Data, node).data)
        return out
    if nt == NodeType.YIELD:
        out.append(cast(Yield, node).expr)
        return out
    if nt == NodeType.BINOP:
        bo = cast(BinOp, node)
        out.append(bo.lhs)
        out.append(bo.operator)
        out.append(bo.rhs)
        return out
    if nt == NodeType.TYPEOFEXPR:
        out.append(cast(TypeOfExpr, node).source)
        return out
    if nt == NodeType.STATEMENT:
        out.extend(cast(Statement, node).statements)
        return out
    if nt == NodeType.STATEMENTLINE:
        out.append(cast(StatementLine, node).statementline)
        return out
    if nt == NodeType.ASSIGNMENT:
        out.append(cast(Assignment, node).value)
        return out
    if nt == NodeType.REASSIGNMENT:
        ra = cast(Reassignment, node)
        out.append(ra.topath)
        out.append(ra.value)
        return out
    if nt == NodeType.SWAP:
        sw = cast(Swap, node)
        out.append(sw.lhs)
        out.append(sw.rhs)
        return out
    if nt == NodeType.DOTTEDPATH:
        dp = cast(DottedPath, node)
        out.append(dp.parent)
        out.append(dp.child)
        return out
    if nt == NodeType.ATOMSTRING:
        out.extend(cast(AtomString, node).stringparts)
        return out
    if nt in (
        NodeType.ATOMID,
        NodeType.LABELVALUE,
        NodeType.STRINGCHUNK,
        NodeType.ERROR,
    ):
        # leaf nodes — no Node children
        return out
    return out


def node_tokens(node: "Node") -> "typing.List[Token]":
    """Return Tokens directly embedded in `node` (other than `node.start`,
    which every Node has and callers should collect separately).

    No Node currently embeds Tokens directly — kept as a stable hook
    for future cases.
    """
    return []


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
