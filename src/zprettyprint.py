#!/usr/bin/python3
"""
ZeroLang pretty (?) printer
"""

from typing import List, Dict

import zast
from zast import Node, NodeType, Token


def pprintprogram(program: zast.Program) -> None:
    """
    pprintprogram
    """
    o: List[str] = []
    o.append(f"Mainunit: {program.mainunitname}\n")
    for name, unit in program.units.items():
        o.append(f"\n{name}: ")
        o.append(pprintnode(unit, depth=0))

    print("".join(o))


# def pprintOLD(node: Node) -> None:
#     """
#     pprint - pretty print a node (top level, usually a Unit)
#     """
#     o: List[str] = []
#     o = pprintnode(node, output=o, depth=0)
#     print("".join(o))


def pprintnode(node: Node, depth: int) -> str:
    """
    pprintnode - pretty print a node
    """
    f = nodehandler.get(node.nodetype)
    if f:
        return f(node, depth)

    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    print(f"Cannot handle node type {node.nodetype.name}")
    return ""


def _pprintunit(node: Node, depth: int) -> str:
    if not isinstance(node, zast.Unit):
        raise Exception("Error: wrong node type")
    unit = node
    sep = depth * "  "

    # s = f"{sep}*UNIT {{\n"
    s = "*UNIT {\n" # no sep, we are always after a name
    # sepinner = (depth + 1) * "  "

    # output.append(sepinner)
    s += _pprintunitbody(unit.body, depth + 1)
    # _pprintnamedexpressionlist(node.body, output, depth + 1)

    # sep = depth * "  "
    # output.append(f"\n{sep}}}")
    s += f"\n{sep}}}\n"
    return s


def _pprintunitbody(body: Dict[str, zast.TypeDefinition], depth: int) -> str:
    sep = depth * "  "
    o: List[str] = []
    for name, unitordefinition in body.items():
        s = f"{sep}{name}: "
        s += pprintnode(unitordefinition, depth)
        o.append(s)
    return "\n".join(o)


def _pprintrecord(node: Node, depth: int) -> str:
    if not isinstance(node, zast.Record):
        raise Exception("Error: wrong node type")
    sep = depth * "  "

    o: List[str] = []
    o.append("*RECORD {")
    sepinner = (depth + 1) * "  "

    # output.append(sepinner)
    for name, path in node.items.items():
        s = f"{sepinner}{name}: "
        s += pprintnode(path, depth + 1)
        o.append(s)

    for name, f in node.functions.items():
        s = (f"{sepinner}{name}: ")
        s += pprintnode(f, depth + 1)
        o.append(s)

    for path in node.implements:
        s = (f"{sepinner}IMPLEMENTS: ")
        s += pprintnode(path, depth + 1)
        o.append(s)

    # _pprintnamedexpressionlist(node.body, output, depth + 1)

    # sep = depth * "  "
    # output.append(f"\n{sep}}}")
    o.append(f"{sep}}}")
    return "\n".join(o)


# def _pprintnamedexpressionlist(
#     node: List[zast.Definition], output: List[str], depth: int
# ) -> None:
#     """
#     """
#     sep = depth * "  "
#     for ne in node:
#         _pprintdefinition(ne, output, depth)
#         output.append(f"\n{sep}")


def _pprintnamedoperationlist(
    namedoperations: List[zast.NamedOperation], depth: int
) -> str:
    """ """
    o: List[str] = []
    for namedop in namedoperations:
        o.append("\n")
        o.append(_pprintnamedoperation(namedop, depth + 1))
    return "".join(o)


def _pprintifclause(ifclause: zast.IfClause, depth: int) -> str:
    """ """
    o: List[str] = []
    for n, c in ifclause.conditions.items():
        o.append("\n")
        o.append(f"{n}:")
        o.append(pprintnode(c, depth + 1))
    o.append(_pprintstatement(ifclause.statement, depth + 1))
    return "".join(o)


# def _pprintdefinition(node: Node, output: List[str], depth: int) -> None:
#     """
#     """
#     if not isinstance(node, zast.Definition):
#         raise Exception("Error: wrong node type")
#     sep = depth * "  "
#     output.append(f"{sep}{node.name}: ")
#     pprintnode(node.definition, output, depth + 1)


def _pprintnamedoperation(node: Node, depth: int) -> str:
    """ """
    if not isinstance(node, zast.NamedOperation):
        raise Exception("Error: wrong node type")
    o: List[str] = []
    sep = depth * "  "
    o.append(f"{sep}{node.name}:")
    o.append(pprintnode(node.valtype, depth + 1))
    return "".join(o)


# def _pprintblock(node: Node, output: List[str], depth: int):
#     # if node.nodetype != NodeType.BLOCK:
#     if not isinstance(node, zast.Block):
#         raise Exception("Error: wrong node type")
#     errstr = ""
#     if node.error:
#         errstr = " ERROR!"
#     l = len(node.members)
#     output.append(f"*BLOCK({l}){errstr} {{")
#     sepinner = (depth + 1) * "  "
#     # if node.errornode:    # there is no errornode here...
#     #     output.append(sepinner)
#     #     _pprinterror(node.errornode, output, depth + 1)
#     for m in node.members:
#         output.append("\n" + sepinner)
#         pprintnode(m, output, depth + 1)
#     if node.members:
#         sep = depth * "  "
#         output.append(f"\n{sep}")
#     output.append("}")


# def _pprintdefinition(node: Node, output: List[str], depth: int):
#     # if node.nodetype != NodeType.DEFINITION:
#     if not isinstance(node, zast.Definition):
#         raise Exception("Error: wrong node type")
#     errstr = ""
#     if node.error:
#         errstr = " ERROR!"
#     output.append(f"*DEFINITION{errstr} {{\n")
#     sepinner = (depth + 1) * "  "
#     output.append(sepinner)
#     if node.name:
#         output.append(f"{node.name.token} ")
#     if node.expression:
#         pprintnode(node.expression, output, depth + 1)
#     sep = depth * "  "
#     output.append(f"\n{sep}}}")


def _pprintassignment(node: Node, depth: int) -> str:
    # if node.nodetype != NodeType.ASSIGNMENT:
    if not isinstance(node, zast.Assignment):
        raise Exception("Error: wrong node type")
    o: List[str] = []
    o.append(f"*ASSIGNMENT {{ {node.name}:\n")
    sepinner = (depth + 1) * "  "
    o.append(sepinner)
    # _pprintdottedpath(node.lhs, output, depth)
    o.append(pprintnode(node.value, depth + 1))
    sep = depth * "  "
    o.append(f"\n{sep}}}")
    return "".join(o)


# def _pprintbinop(node: Node, output: List[str], depth: int) -> None:
#     if not isinstance(node, zast.Binop):
#         raise Exception("Error: wrong node type")
#     output.append(f"*BINOP {{\n")
#     sepinner = (depth + 1) * "  "

#     if node.lhs:
#         # _pprintatom(node.lhs, output, depth)
#         # output.append(f"*ATOM*\n")
#         output.append(sepinner)
#         pprintnode(node.lhs, output, depth + 1)

#     if node.operator:
#         output.append(f" {node.operator.token} ")

#     if node.rhs:
#         # an atom
#         pprintnode(node.rhs, output, depth + 1)

#     sep = depth * "  "
#     output.append(f"\n{sep}}}")


def _pprintcall(node: Node, depth: int) -> str:
    if not isinstance(node, zast.Call):
        raise Exception("Error: wrong node type")
    o: List[str] = []
    o.append("*CALL (")
    sepinner = (depth + 1) * "  "

    o.append("\n" + sepinner)
    o.append(pprintnode(node.callable, depth + 1))

    if len(node.arguments) != 0:
        o.append("\n" + sepinner + "ARGS*(")
        o.append(_pprintnamedoperationlist(node.arguments, depth + 2))
        o.append("\n" + sepinner + ")")
    return "".join(o)


def _pprintfunction(node: Node, depth: int) -> str:
    if not isinstance(node, zast.Function):
        raise Exception("Error: wrong node type")
    sepinner = (depth + 1) * "  "
    o: List[str] = []
    o.append("*FUNCTION {")

    if node.returntype:
        s = f"{sepinner}*RETURN("
        # _pprintdottedpath(node.result, output, depth + 1)
        s += pprintnode(node.returntype, depth + 1)
        # output.append("\n")
        s += ")"
        o.append(s)

    if node.yieldtype:
        s = f"{sepinner}*YIELD("
        # _pprintdottedpath(node.result, output, depth + 1)
        s += pprintnode(node.yieldtype, depth + 1)
        s += ")"
        o.append(s)

    if node.parameters:
        s = f"{sepinner}*PARAMS {{\n"
        s += _pprintparameters(node.parameters, depth + 2)
        s += "\n" + sepinner + "}"
        o.append(s)

    if node.body:
        s = f"{sepinner}*BODY{{\n"
        s+= pprintnode(node.body, depth + 2)
        s += "\n" + sepinner + "}"
        o.append(s)

    s = "\n".join(o)
    s += "\n" + (depth * "  ") + "}"
    return s


def _pprintparameters(
    params: Dict[str, zast.Path], depth: int
) -> str:

    o: List[str] = []
    sep = depth * "  "
    for name, paramtype in params.items():
        s = f"{sep}{name}:"
        s += pprintnode(paramtype, depth + 1)
        o.append(s)
    return "\n".join(o)


def _pprintexpression(node: Node, depth: int) -> str:
    if not isinstance(node, zast.Expression):
        raise Exception("Error: wrong node type")
    return pprintnode(node.expression, depth + 1)


def _pprintbinop(node: Node, depth: int) -> str:
    if not isinstance(node, zast.BinOp):
        raise Exception("Error: wrong node type")

    s = pprintnode(node.lhs, depth + 1)
    s += f" {node.operator.name} "
    s += pprintnode(node.rhs, depth + 1)
    return s


# def _pprintasop(node: Node, depth: int) -> str:
#     if not isinstance(node, zast.AsOp):
#         raise Exception("Error: wrong node type")
#
#     s = pprintnode(node.lhs, depth + 1)
#     s += " as "
#     s += pprintnode(node.rhs, depth + 1)
#     return s


def _pprintif(node: Node, depth: int) -> str:
    if not isinstance(node, zast.If):
        raise Exception("Error: wrong node type")

    o: List[str] = []
    o.append("*if {")
    sepinner = (depth + 1) * "  "

    o.append("*CONDITIONS(")
    for clause in node.clauses:
        o.append(_pprintifclause(clause, depth + 1))
    o.append(") ")

    o.append("\n" + sepinner + "ELSE*{")
    if node.elseclause:
        o.append(pprintnode(node.elseclause, depth + 1))
        o.append("\n" + sepinner + "}")

    o.append("\n" + sepinner + "}")
    return "\n".join(o)


def _pprintfor(node: Node, depth: int) -> str:
    if not isinstance(node, zast.For):
        raise Exception("Error: wrong node type")

    o: List[str] = []
    o.append("*for {")
    sepinner = (depth + 1) * "  "

    o.append("*CONDITIONS(")
    for n, op in node.conditions.items():
        o.append(f"{n}:")
        o.append(pprintnode(op, depth + 1))
    o.append(") ")

    if node.loop:
        o.append(f"\n{sepinner}LOOP*{{")
        o.append(pprintnode(node.loop, depth + 1))
        o.append(f"\n{sepinner}}}")

    if node.postconditions:
        o.append("*POSTCONDITIONS(")
        for op in node.postconditions:
            o.append(pprintnode(op, depth + 1))
        o.append(") ")

    o.append("\n" + sepinner + "}")
    return "\n".join(o)

def _pprintdo(node: Node, depth: int) -> str:
    if not isinstance(node, zast.Do):
        raise Exception("Error: wrong node type")

    o: List[str] = []
    o.append("*do {")
    sepinner = (depth + 1) * "  "

    o.append(f"\n{sepinner}*STATEMENT {{")
    o.append(pprintnode(node.statement, depth + 1))
    o.append(f"\n{sepinner}}}")

    o.append("\n" + sepinner + "}")
    return "\n".join(o)


# def _pprintargument(node: Node, output: List[str], depth: int):
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.Argument):
#         raise Exception("Error: wrong node type")
#     sepinner = (depth + 1) * "  "
#     errstr = ""
#     output.append(f"*ARG (\n")
#     sepinner = (depth + 1) * "  "
#     output.append(sepinner)
#     if node.name:
#         output.append(f"{node.name.token} ")
#     if node.errornode:
#         output.append(sepinner)
#         _pprinterror(node.errornode, output, depth)
#     if node.operation:
#         # should always have an operation if not error
#         pprintnode(node.operation, output, depth + 1)
#     sep = depth * "  "
#     output.append(f"\n{sep})")


def _pprintatomid(node: Node, depth: int) -> str:
    del depth
    # if node.nodetype != NodeType.MEMBER:
    if not isinstance(node, zast.AtomId):
        raise Exception("Error: wrong node type")
    return f"*ATOMID({node.name})"


def _pprintatomnumber(node: Node, depth: int) -> str:
    del depth
    # if node.nodetype != NodeType.MEMBER:
    if not isinstance(node, zast.AtomNumber):
        raise Exception("Error: wrong node type")

    s = f"*ATOMNUMBER({node.start.tokstr})"
    return s


def _pprintatomstring(node: Node, depth: int) -> str:
    # if node.nodetype != NodeType.MEMBER:
    if not isinstance(node, zast.AtomString):
        raise Exception("Error: wrong node type")

    o: List[str] = []
    o.append('*ATOMSTRING("')
    sepinner = (depth + 1) * "  "
    del sepinner
    # output.append(sepinner)
    for s in node.stringparts:
        if isinstance(s, Token):
            o.append(f'"{s.tokstr}"')
        else:
            o.append("(")
            o.append(pprintnode(s, depth))
            o.append(")")
    o.append('")')
    return "".join(o)


# def _pprintatomexpression(node: Node, output: List[str], depth: int) -> None:
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.AtomExpr):
#         raise Exception("Error: wrong node type")
#     output.append("*ATOMEXPR(\n")
#     # sepinner = (depth + 1) * "  "
#     # output.append(sepinner)
#     sepinner = (depth + 1) * "  "
#     output.append(sepinner)
#     if node.expression:
#         pprintnode(node.expression, output, depth + 1)
#     # output.append(")")

#     sep = depth * "  "
#     output.append(f"\n{sep})")


# def _pprintatomblock(node: Node, output: List[str], depth: int):
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.AtomBlock):
#         raise Exception("Error: wrong node type")
#     errstr = ""
#     output.append(f"*ATOMBLOCK ")
#     # sepinner = (depth + 1) * "  "
#     # output.append(sepinner)

#     # nb: don't indent, use our indent already (we are just an atom)
#     _pprintblock(node.block, output, depth)
#     # sep = depth * "  "
#     # output.append(f"{sep}}}")

#     # output.append(")")


# def _pprinterror(node: Node, output: List[str], depth: int):
#     # if node.nodetype != NodeType.ERROR:
#     if not isinstance(node, zast.Error):
#         raise Exception("Error: wrong node type")
#     output.append(f"*ERROR({node.message} ")
#     # output.append(f"At: {node.token.lineno}:{node.token.colno})\n")
#     output.append(f"At: {node.token!r})\n")


def _pprintdottedpath(node: Node, depth: int) -> str:
    if not isinstance(node, zast.DottedPath):
        raise Exception(f"Error: wrong node type got {node!r}")

    o: List[str] = []
    o.append("*DOTTEDPATH(")
    # sepinner = (depth + 1) * "  "
    o.append(pprintnode(node.parent, depth + 1))
    o.append(f" . {node.child.name}")
    o.append(")")
    return "".join(o)


def _pprintstatement(node: Node, depth: int) -> str:
    """ """
    if not isinstance(node, zast.Statement):
        raise Exception("Error: wrong node type")

    o: List[str] = []
    sep = depth * "  "
    o.append(f"{sep}*STATEMENT {{\n")
    sepinner = (depth + 1) * "  "

    for n in node.statements:
        s = sepinner + pprintnode(n, depth + 1)
        o.append(s)

    o.append(f"{sep}}}")
    return "\n".join(o)


nodehandler: dict = {
    NodeType.UNIT: _pprintunit,
    # NodeType.BLOCK: _pprintblock,
    NodeType.STATEMENT: _pprintstatement,
    # NodeType.DEFINITION: _pprintdefinition,
    # NodeType.DEFINITION: _pprintdefinition,
    NodeType.ASSIGNMENT: _pprintassignment,
    NodeType.CALL: _pprintcall,
    NodeType.FUNCTION: _pprintfunction,
    # NodeType.PROTOCOL: _pprintprotocol,
    NodeType.RECORD: _pprintrecord,
    # NodeType.CLASS: _pprintclass,
    # NodeType.UNION: _pprintunion,
    # NodeType.VARIANT: _pprintvariant,
    # NodeType.ENUM: _pprintenum,
    NodeType.IF: _pprintif,
    NodeType.FOR: _pprintfor,
    NodeType.DO: _pprintdo,
    # NodeType.BINOP: _pprintbinop,
    # NodeType.DOTTEDPATH: _pprintdottedpath,
    # NodeType.ATOMBLOCK: _pprintatomblock,
    # NodeType.ATOMEXPR: _pprintatomexpression,
    NodeType.ATOMID: _pprintatomid,
    NodeType.ATOMNUMBER: _pprintatomnumber,
    NodeType.ATOMSTRING: _pprintatomstring,
    # NodeType.ARGUMENT: _pprintargument,
    NodeType.NAMEDOPERATION: _pprintnamedoperation,
    NodeType.EXPRESSION: _pprintexpression,
    # NodeType.OPERATION: _pprintoperation,
    NodeType.BINOP: _pprintbinop,
    NodeType.DOTTEDPATH: _pprintdottedpath,
}


# ----------------------------------------------------------------------
# old prettyprinter to text and html


# def printtokensastext(filename: str):
#     """
#     print each token from the supplied filename in turn
#     """
#     print(f"File: {filename}")
#     with open(filename, "r", encoding="utf8") as f:
#         l = Lexer(f, 0)
#         while True:
#             # for x in range(201):
#             tok = l.acceptany()
#             print(repr(tok))

#             if tok.toktype == TT.EOF:
#                 print("Got eof")
#                 break


# HTMLHEAD = """
# <!DOCTYPE html>
# <html>
# <head>
#     <meta charset="utf-8" />
#     <title>File: {filename}</title>
# """
# STYLE = """
#     <style type="text/css">
# pre {
#     counter-reset: line;
#     padding: 0.3em;
# }
# code {
#     counter-increment: line;
#     padding: 1px;
# }
# code:before {
#     content: counter(line);
#     display: inline-block;
#     width: 2em;
#     color: #93a1a1;
#     //background-color: #eee8d5;
#     background-color: #f5f5f5;
#     border-right: 1px solid #ddd;
#     margin-right: 0.1em;
#     text-align: right;
#     padding-right: .5em;
# }
# pre.zerosource { font-family: monospace; color: #586e75; background-color: #f5f5f5; }
# .tok_escapedchar { color: #cb4b16; }
# .Todo { color: #d33682; font-weight: bold; }
# .linenr { color: #93a1a1; background-color: #eee8d5; padding-bottom: 1px; }
# .tok_comment { color: #8a8a8a; font-style: italic; }
# .tok_number { color: #d52a2a; }
# .tok_strbeg { color: #1c7d4d; }
# .tok_strmid { color: #1c7d4d; }
# .tok_strchr { color: #cb4b16; }
# .tok_strvar { color: #cb4b16; }
# .tok_strend { color: #1c7d4d; }
# .tok_id { color: #268bd2; }
# .tok_keyword { color: #859900; }
# .tok_def { color: #3f6ec6; }
# .tok_separator { color: #36464e; }
# .tok_dot { color: #36464e; }
# .tok_error { color: #36464e; background-color: #d52a2a}
#     </style>
# </head>
# """
# HTMLBODY = """
# <body>
# <h1>{filename}</h1>
# <pre class="zerosource">\
# """

# HTMLFOOT = """\
# </pre>
# </body>
# </html>
# """


# def printtokensashtml(filename: str):
#     """
#     dump the list of tokens as html

#     Copy to a file and view in browser

#     This wont work so well now since some tokens (eg. whitespace) are skipped
#     """
#     print(HTMLHEAD.format(filename=filename))
#     print(STYLE)
#     print(HTMLBODY.format(filename=filename))
#     with open(filename, "r", encoding="utf8") as f:
#         l = Lexer(f, 0)
#         line = ["<code>"]
#         while True:
#             tok = l.acceptany()
#             if tok.toktype == TT.EOF:
#                 line.append("</code>")
#                 if len(line) > 2:
#                     # shouldn't happen. File should end with NL
#                     print("".join(line))
#                 break

#             # assert len(tokval) > 0  # only EOF can be zero length token
#             if len(tok.token) == 0:
#                 print(f"0 length token {tok.toktype} at {tok.lineno}:{tok.colno}")
#                 assert len(tok.token) > 0  # only EOF can be zero length token

#             if tok.toktype == TT.EOL:
#                 line.append("</code>")
#                 print("".join(line))
#                 line = ["<code>"]  # new line
#             else:
#                 tokclass = "tok_" + tok.toktype.name.lower()
#                 # print(tokclass)
#                 line.append(
#                     '<span title="{0}" class="{1}">{2}</span>'.format(
#                         tok.toktype.name, tokclass, html.escape(tok.token)
#                     )
#                 )

#     print(HTMLFOOT.format(filename=filename))


# # def main():
# #     """
# #     main
# #     """
# #     if len(sys.argv) != 2:
# #         print("Usage: lexer filename")
# #         sys.exit(1)
# #     filename = sys.argv[1]
# #     # printtokensashtml(filename)
# #     printtokensastext(filename)


# # if __name__ == "__main__":
# #     import sys

# #     main()
