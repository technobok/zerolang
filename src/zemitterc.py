#!/usr/bin/python3
"""
ZeroLang emitter to C
"""

from typing import List, Dict, Set, Optional, Sequence
from dataclasses import dataclass, field
from collections import OrderedDict

import zast
from zast import AstNode, NodeType
from zlexer import Token
from ztokentype import TT
import ztype

TYPEMAP = {
    # ztype: ctype
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "i128": "int128_t",
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "u128": "uint128_t",
    "f32": "float",  # cannot guarantee float bitsizes?
    "f64": "double",
    "f128": "long double",
    "null": "void",
    "record": "struct",
    "string": "char",  # XXX needs [] after type name...
}

FLAGOUTPUT = {
    # flag: output code
    "stdio": "#include <stdio.h>\n",
    "stdint": "#include <stdint.h>\n",
}


class CState:
    """
    State storage for C Compiler
    """

    def __init__(self):
        self.flags: Set[str] = set()
        self.error = False
        self.envstack: List[Dict[str, ztype.Value]] = []
        # TODO: add builtins and lock it
        # TODO: prevent popping builtins and top level
        self.pushenv()  # top level, globals

    def setflag(self, flagname: str):
        """
        output flags, for required headers etc
        """
        self.flags.add(flagname)

    def depth(self):
        """
        return depth in call stack, 0 for top level
        """
        return len(self.envstack)

    def pushenv(self):
        """
        create/return/add a new env to the bottom of the call stack
        """
        d: Dict[str, ztype.Value] = {}
        self.envstack.append(d)
        return d

    def popenv(self):
        """
        pop the lowest env off of the call stack
        """
        return self.envstack.pop()

    def define(self, name: str, value: ztype.Value):
        """
        Add a definition to the current (lowest) env
        """
        if not name:
            raise ValueError("Must supply a name to Define ")
        d = self.envstack[-1]
        if name in d:
            raise ValueError(f"Duplicate definition of {name}")
        d[name] = value

    def find(self, name: str) -> Optional[ztype.Value]:
        """
        find a symbol definition from inner to outermost env.

        Return Value if found, None if not
        """
        for e in reversed(self.envstack):
            r = e.get(name, None)
            if r:
                return r
        return None

    @staticmethod
    def mangle(zname: str) -> str:
        """
        'mangle' a zname into something that can be used by C

        TODO: look in (current?) scope to ensure no collisions on the C name?
        """
        if zname == "main":
            return "main_"
        return zname

    def ctype(self, typ: ztype.Type, name: str) -> str:
        """
        Convert (compile) a ztype into a C type (definition) given a type and
        a variable name
        """
        # if typ.typetype == TypeType.PRIMITIVE:
        cname = self.mangle(name)
        ctypename = ""
        # typ = value.valuetype
        if isinstance(typ, ztype.Float):
            ctypename = TYPEMAP[typ.typename]
            return f"{ctypename} {cname}"
        if isinstance(typ, ztype.Integer):
            self.setflag("stdint")
            ctypename = TYPEMAP[typ.typename]
            return f"{ctypename} {cname}"
        if isinstance(typ, ztype.NullType):
            ctypename = TYPEMAP[typ.typename]
            return f"{ctypename} {cname}"
        if isinstance(typ, ztype.Function):
            parts = []
            parts.append(self.ctype(typ=typ.result, name=name))
            parts.append("(")
            for argname, argtype in typ.arguments.items():
                parts.append(self.ctype(typ=argtype, name=argname))
            parts.append(")")
            return "".join(parts)

        raise Exception("Cannot handle type {otype}")


@dataclass
class Fragment:
    """
    Fragment of generated code / type from the AST Node
    """

    value: ztype.Value
    parts: List[str] = field(default_factory=list)


def emit(name: Optional[str], node: AstNode) -> str:
    """
    emit - emit C text for a top level AstNode (Unit)
    TODO: should emit for a whole Program

    name - unit name (filename), required
    """
    state = CState()
    # TODO: do a first pass to get the top level definitions
    # then a second pass to do generation

    if not name:
        raise ValueError("Name of unit is required")

    frag = _emitunit(name, node, state)
    parts: List[str] = []
    parts.append(f"/* unit: {name} */\n\n")
    for f in state.flags:
        if f in FLAGOUTPUT:
            parts.append(FLAGOUTPUT[f])
        else:
            raise ValueError(f"Unknown flag: {f}")
    if len(state.flags) > 0:
        parts.append("\n")
    parts.extend(frag.parts)

    parts.append(
        """
int main(int argc, char *argv[]) {
  main_();
}
"""
    )

    return "".join(parts)


def _emitnode(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    """
    emitnode - emit C code for an AstNode
    """
    f = nodehandler.get(node.nodetype)
    if not f:
        raise Exception(f"No handler for node type {node.nodetype}")
    return f(name, node=node, state=state)


def _emitunit(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.UNIT:
    if not isinstance(node, zast.Unit):
        raise TypeError(f"Expected zast.Unit, got {node.nodetype}")

    if node.error:
        print("Error: error in Unit AST")

    if node.errornode:
        return _emiterror(name, node.errornode, state)

    return _emitblock(name, node=node.block, state=state)


def _emitblock(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.BLOCK:
    if not isinstance(node, zast.Block):
        raise TypeError(f"Expected zast.Block, got {node.nodetype}")

    if node.error:
        print("Error: error in Block AST")

    parts: List[str] = []
    # storage for most recent result - return the last
    value: ztype.Value = ztype.NULLVALUE
    for m in node.members:
        frag = _emitnode(name, m, state=state)
        parts.extend(frag.parts)
        value = frag.value
    return Fragment(value=value, parts=parts)


def _emitdefinition(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.DEFINITION:
    if not isinstance(node, zast.Definition):
        raise ValueError("Error: wrong node type")
    if node.error:
        raise ValueError("Error: error in Definition AST")

    if not node.expression:
        raise ValueError("Error: Bad operation")

    frag = _emitnode(name, node.expression, state=state)
    value = frag.value
    depth = state.depth()
    if depth == 1:
        if not value.constant:
            raise ValueError("Error: top level definitions must be constant")
        if not node.name:
            raise ValueError("Error: top level definitions must be named")

    indent = "  " * (depth - 1)
    parts: List[str] = [indent]
    if node.name:
        # make new mapping in the state
        # strip colon from end (always must be present)
        n = node.name.token
        state.define(name=n, value=frag.value)
        parts.append(state.ctype(typ=value.valuetype, name=n))
        parts.append(" = ")

    parts.extend(frag.parts)
    parts.append(";\n")

    return Fragment(parts=parts, value=value)


def _emitassignment(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.ASSIGNMENT:
    if not isinstance(node, zast.Assignment):
        raise ValueError("Error: wrong node type")
    if node.error:
        raise ValueError("Error: error in Definition AST")

    if not node.expression:
        raise ValueError("Error: Bad operation")

    frag = _emitnode(name, node.expression, state=state)
    value = frag.value
    depth = state.depth()
    if depth == 1:
        if not value.constant:
            raise ValueError("Error: can not have top level assignment")

    indent = "  " * (depth - 1)
    parts: List[str] = [indent]
    if node.name:
        # ensure name already exists

        # make new mapping in the state
        # strip colon from end (always must be present)
        n = node.name.token[:-1]
        state.define(name=n, value=frag.value)
        parts.append(state.ctype(typ=value.valuetype, name=n))
        parts.append(" = ")

    parts.extend(frag.parts)
    parts.append(";\n")

    return Fragment(parts=parts, value=value)


def _emitbinop(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    if not isinstance(node, zast.Binop):
        raise ValueError("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in Member AST")

    # if node.errornode:
    #     output.append(sepinner)
    #     _pprinterror(node.errornode, output, depth)

    if node.lhs:
        parts: List[str] = ["("]
        f = _emitnode(name="", node=node.lhs, state=state)
        parts.extend(f.parts)

        if node.operator:
            op = node.operator.token
            if op in ("+", "-", "*", "/"):
                parts.append(f" {op} ")
            else:
                raise ValueError(f"Error: cannot handle {op} operator (yet)")
        else:
            raise ValueError("Error: missing operator in binop")

        if node.rhs:
            # an atom
            f2 = _emitnode(name="", node=node.rhs, state=state)
            parts.extend(f2.parts)
        else:
            raise ValueError("Error: missing RHS in binop")
        parts.append(")")

        # check lhs and rhs types are the same (relax this in the future to be
        # compatible types instead?)
        if f.value.valuetype != f2.value.valuetype:
            raise ValueError("Error: LHS and RHS types must be equivalent")

        # TODO: and they must be simple types?

        return Fragment(parts=parts, value=f.value)

    raise ValueError("Error: missing LHS in binop")


def _emitcall(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.CALL:
    if not isinstance(node, zast.Call):
        raise ValueError("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in Call AST")

    # if node.errornode:
    #     _pprinterror(node.errornode, output, depth)

    c = node.callable

    if not isinstance(c, zast.AtomId):
        raise ValueError("Error: can only handle AtomId callables (so far)")

    if c.dottedids:
        raise ValueError(
            "Error: can only handle AtomId callables without dottedids (so far)"
        )

    sym = c.name.token
    builtin = builtinhandler.get(sym, None)
    if builtin:
        return builtin(name, node.arguments, state)
    # TODO: look sym up in env
    # TODO: and check for builtins

    parts: List[str] = []
    arguments: Dict[str, ztype.Type] = OrderedDict()
    t = ztype.Function(arguments=arguments, result=ztype.NULLTYPE)
    parts.extend(state.ctype(typ=t, name=sym))

    # node.callable must be a atom id? .... Anonymous function?
    # ensure it is callable...
    # f = _emitnode(node.callable, state)
    # parts.extend(f.parts)

    # TODO: do argument values correctly
    # for a in node.arguments:
    #     fa = _emitargument(a, state)

    # TODO: set returntype / value correctly
    return Fragment(parts=parts, value=ztype.NULLVALUE)


def _emitargument(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.MEMBER:
    if not isinstance(node, zast.Argument):
        raise Exception("Error: wrong node type")
    sepinner = (depth + 1) * "  "
    errstr = ""
    if node.error:
        errstr = " ERROR!"
    output.append(f"*ARG{errstr} (\n")
    sepinner = (depth + 1) * "  "
    output.append(sepinner)
    if node.name:
        output.append(f"{node.name.token} ")
    if node.errornode:
        output.append(sepinner)
        _pprinterror(node.errornode, output, depth)
    if node.operation:
        # should always have an operation if not error
        pprintnode(node.operation, output, depth + 1)
    sep = depth * "  "
    output.append(f"\n{sep})")


def _emitatomid(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.ATOMID:
    if not isinstance(node, zast.AtomId):
        raise ValueError("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in AtomId AST")

    # if node.errornode:
    #     _pprinterror(node.errornode, output, depth)

    sym = node.name.token
    value = state.find(sym)
    if not value:
        raise Exception("ERROR: Cannot find symbol {sym}")

    if node.dottedids:
        raise Exception("Cannot handle dottedids on atomids yet")

    # cname = " " + state.mangle(sym) + " "
    cname = state.mangle(sym)

    # sep = depth * "  "
    # output.append(f"{sep}}}\n")
    return Fragment(value=value, parts=[cname])


def _emitatomnumber(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # pylint: disable=unused-argument
    # don't need state for atomnumber
    # if node.nodetype != NodeType.MEMBER:
    if not isinstance(node, zast.AtomNumber):
        raise Exception("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in AtomNumber AST")

    n = ztype.parse_number(node.number.token)

    if node.dottedids:
        # output.append(f").{d.token}")
        raise Exception("Cannot handle dottedids on numeric literals yet")

    parts: List[str] = [_emit_numeric_literal(n)]

    return Fragment(value=n, parts=parts)


def _emitatomstring(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # pylint: disable=unused-argument
    # don't need state for atomstring
    # if node.nodetype != NodeType.STRING:
    if not isinstance(node, zast.AtomString):
        raise Exception("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in AtomString AST")

    stringparts: List[str] = []

    for s in node.stringparts:
        # TODO: handle non-ASCII characters properly
        # TODO: convert escape codes that are different into c style
        # TODO: handle variable interpolation
        stringparts.append(s.token)

    if node.dottedids:
        # output.append(f").{d.token}")
        raise Exception("Cannot handle dottedids on string literals yet")

    string = "".join(stringparts)
    value = ztype.StringValue(constant=True, string=string)
    parts = ['"' + string + '"']
    return Fragment(value=value, parts=parts)


def _emitatomexpression(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.ATOMEXPR:
    if not isinstance(node, zast.AtomExpr):
        raise Exception("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in AtomExpr AST")

    if not node.expression:
        raise ValueError("Error: missing expression in atomexpression")

    f = _emitnode(name, node.expression, state)
    parts: List[str] = ["("]
    parts.extend(f.parts)
    parts.append(")")

    if node.dottedids:
        # output.append(f").{d.token}")
        raise Exception("Cannot handle dottedids on expression atoms yet")

    return Fragment(value=f.value, parts=parts)


def _emitatomblock(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.ATOMBLOCK:
    if not isinstance(node, zast.AtomBlock):
        raise Exception("Error: wrong node type")

    if node.error:
        raise ValueError("Error: error in AtomBlock AST")

    if not node.block:
        raise ValueError("Error: missing block in AtomBlock")

    # nb: don't indent, use our indent already (we are just an atom)

    f = _emitblock(name, node.block, state)

    parts: List[str] = ["{\n"]
    parts.extend(f.parts)
    parts.append("\n}")

    if node.dottedids:
        # output.append(f").{d.token}")
        raise Exception("Cannot handle dottedids on block atoms yet")

    return Fragment(value=f.value, parts=parts)


def _emiterror(name: Optional[str], node: AstNode, state: CState) -> Fragment:
    # if node.nodetype != NodeType.ERROR:
    if not isinstance(node, zast.Error):
        raise Exception("Error: wrong node type")
    state.error = True
    print(f"ERROR: {node.message} At: {node.token!r} For: {name}")
    return Fragment(value=ztype.NULLVALUE, parts=[])


nodehandler: dict = {
    NodeType.UNIT: _emitunit,
    NodeType.BLOCK: _emitblock,
    NodeType.DEFINITION: _emitdefinition,
    NodeType.ASSIGNMENT: _emitassignment,
    NodeType.BINOP: _emitbinop,
    NodeType.CALL: _emitcall,
    NodeType.ARGUMENT: _emitargument,
    NodeType.ATOMBLOCK: _emitatomblock,
    NodeType.ATOMEXPR: _emitatomexpression,
    NodeType.ATOMID: _emitatomid,
    NodeType.ATOMNUMBER: _emitatomnumber,
    NodeType.ATOMSTRING: _emitatomstring,
    NodeType.ERROR: _emiterror,
}


def _emit_numeric_literal(num: ztype.NumericValue) -> str:
    """
    Convert a ztype.NumericValue into a C literal string
    """
    if not num.constant:
        raise ValueError("Number is not a constant")
    r = ""
    base = 10
    if isinstance(num, ztype.IntegerValue):
        if num.base:
            # always?
            base = num.base

    if base in (2, 16):
        base = 16  # binary and hex are output as hex
        r = "0x"
    elif base == 8:
        r = "0"

    if isinstance(num, ztype.FloatValue):
        r += f"{num.number:f}"
    elif isinstance(num, ztype.IntegerValue):
        if base == 8:
            r += f"{num.number:o}"
        elif base == 16:
            r += f"{num.number:x}"
        else:
            r += f"{num.number}"
    else:
        raise ValueError("ERROR: Unknow numeric type")

    return r


# ----- Builtins ----------------------------------------------------


def _emitbuiltinfunction(
    name: Optional[str], arguments: Sequence[zast.Argument], state: CState
) -> Fragment:
    """
    TODO: change all names to Optional[Token]. Need to ensure this is a DEFINITION
    """
    # out (default)
    # of
    # in
    # do (required)
    parts = []
    #if not name or name.toktype != TT.DEFINITION:
    if not name:
        raise ValueError("Function requires a Definition name")

    parts.append(f"/* arguments for [{name}] */\n")
    for a in arguments:
        if a.name:
            parts.append(f"ARG: {a.name.token}\n")
        else:
            parts.append("ARG: [UNNAMED PARAM]\n")
    return Fragment(value=ztype.NULLVALUE, parts=parts)


def _emitbuiltinif(
    name: Optional[Token], arguments: Sequence[zast.Argument], state: CState
) -> Fragment:
    return Fragment(value=None, parts=None)


def _emitbuiltindo(
    name: Optional[Token], arguments: Sequence[zast.Argument], state: CState
) -> Fragment:
    return Fragment(value=None, parts=None)


builtinhandler: dict = {
    "function": _emitbuiltinfunction,
    "if": _emitbuiltinif,
    "do": _emitbuiltindo,
}

