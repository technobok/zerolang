"""
AST content hasher for code deduplication.

Produces a deterministic hash of a Function AST node based on its structure
and resolved types, excluding function name and 'this' parameter type name.
Used during monomorphization to detect identical function bodies.
"""

import hashlib
from typing import cast

import zast
from zast import NodeType
from zlexer import Token
from ztypes import ZType, ZTypeType


def hash_function(func: zast.Function) -> str:
    """Hash a typechecked Function node, returning a hex digest.

    Hashes parameter types, return type, and body structure.
    Function name is excluded.
    """
    h = hashlib.sha256()

    # hash parameter types (order matters)
    for pname, ppath in func.parameters.items():
        h.update(pname.encode())
        h.update(b":")
        if ppath.type:
            h.update(_hash_type(ppath.type).encode())

    # hash return type
    h.update(b"|RET|")
    if func.returntype and func.returntype.type:
        h.update(_hash_type(func.returntype.type).encode())

    # hash body
    h.update(b"|BODY|")
    if func.is_native:
        h.update(b"|NATIVE|")
    elif func.body:
        h.update(_hash_node(func.body).encode())

    return h.hexdigest()


def _hash_type(ztype: ZType) -> str:
    """Hash a ZType by name and typetype."""
    return f"{ztype.typetype.name}:{ztype.name}"


def _hash_type_structure(ztype: ZType) -> str:
    """Hash a ZType structurally (excludes the type name).

    Hashes typetype plus the structure of children (their types),
    so two structurally identical types hash the same even with different names.
    """
    h = hashlib.sha256()
    h.update(ztype.typetype.name.encode())
    for cname, ctype in ztype.children.items():
        if ctype.typetype == ZTypeType.FUNCTION:
            continue
        h.update(cname.encode())
        h.update(b":")
        h.update(_hash_type(ctype).encode())
    return h.hexdigest()


def _hash_node(node: zast.Node) -> str:
    """Recursively hash an AST node by structure and types."""
    handler = _node_handlers.get(node.nodetype)
    if handler:
        return handler(node)  # type: ignore[arg-type]
    # fallback: hash nodetype + type
    h = hashlib.sha256()
    h.update(node.nodetype.name.encode())
    if node.type:
        h.update(_hash_type(node.type).encode())
    return h.hexdigest()


def _hash_statement(node: zast.Statement) -> str:
    h = hashlib.sha256()
    h.update(b"STATEMENT")
    for sline in node.statements:
        h.update(_hash_statementline(sline).encode())
    return h.hexdigest()


def _hash_statementline(node: zast.StatementLine) -> str:
    h = hashlib.sha256()
    h.update(b"SLINE")
    inner = node.statementline
    if inner.nodetype == NodeType.ASSIGNMENT:
        h.update(_hash_assignment(cast(zast.Assignment, inner)).encode())
    elif inner.nodetype == NodeType.REASSIGNMENT:
        h.update(_hash_reassignment(cast(zast.Reassignment, inner)).encode())
    elif inner.nodetype == NodeType.SWAP:
        h.update(_hash_swap(cast(zast.Swap, inner)).encode())
    elif inner.nodetype == NodeType.EXPRESSION:
        h.update(_hash_expression(cast(zast.Expression, inner)).encode())
    return h.hexdigest()


def _hash_assignment(node: zast.Assignment) -> str:
    h = hashlib.sha256()
    h.update(b"ASSIGN")
    h.update(node.name.encode())
    h.update(_hash_expression(node.value).encode())
    return h.hexdigest()


def _hash_reassignment(node: zast.Reassignment) -> str:
    h = hashlib.sha256()
    h.update(b"REASSIGN")
    h.update(_hash_path(node.topath).encode())
    h.update(_hash_expression(node.value).encode())
    return h.hexdigest()


def _hash_swap(node: zast.Swap) -> str:
    h = hashlib.sha256()
    h.update(b"SWAP")
    h.update(_hash_path(node.lhs).encode())
    h.update(_hash_path(node.rhs).encode())
    return h.hexdigest()


def _hash_expression(node: zast.Expression) -> str:
    h = hashlib.sha256()
    h.update(b"EXPR")
    if node.type:
        h.update(_hash_type(node.type).encode())
    inner = node.expression
    if inner.nodetype == NodeType.CALL:
        h.update(_hash_call(cast(zast.Call, inner)).encode())
    elif inner.nodetype == NodeType.IF:
        h.update(_hash_if(cast(zast.If, inner)).encode())
    elif inner.nodetype == NodeType.FOR:
        h.update(_hash_for(cast(zast.For, inner)).encode())
    elif inner.nodetype == NodeType.DO:
        h.update(_hash_do(cast(zast.Do, inner)).encode())
    elif inner.nodetype == NodeType.CASE:
        h.update(_hash_case(cast(zast.Case, inner)).encode())
    elif inner.nodetype == NodeType.DATA:
        h.update(_hash_data(cast(zast.Data, inner)).encode())
    elif inner.nodetype == NodeType.WITH:
        h.update(_hash_with(cast(zast.With, inner)).encode())
    else:
        h.update(_hash_operation(cast(zast.Operation, inner)).encode())
    return h.hexdigest()


def _hash_path(path: zast.Path) -> str:
    if path.nodetype == NodeType.DOTTEDPATH:
        return _hash_dottedpath(cast(zast.DottedPath, path))
    if path.nodetype == NodeType.ATOMID:
        return _hash_atomid(cast(zast.AtomId, path))
    if path.nodetype == NodeType.LABELVALUE:
        return _hash_atomid(cast(zast.AtomId, path))
    if path.nodetype == NodeType.ATOMSTRING:
        return _hash_atomstring(cast(zast.AtomString, path))
    if path.nodetype == NodeType.EXPRESSION:
        return _hash_expression(cast(zast.Expression, path))
    if path.nodetype == NodeType.BINOP:
        return _hash_binop(cast(zast.BinOp, path))
    # fallback
    return _hash_node(path)


def _hash_operation(node: zast.Operation) -> str:
    if node.nodetype == NodeType.BINOP:
        return _hash_binop(cast(zast.BinOp, node))
    # All Path subclasses (DottedPath, AtomId, AtomString, Expression, LabelValue)
    return _hash_path(cast(zast.Path, node))


def _hash_call(node: zast.Call) -> str:
    h = hashlib.sha256()
    h.update(b"CALL")
    if node.type:
        h.update(_hash_type(node.type).encode())
    h.update(_hash_path(node.callable).encode())
    for arg in node.arguments:
        h.update(_hash_namedop(arg).encode())
    return h.hexdigest()


def _hash_namedop(node: zast.NamedOperation) -> str:
    h = hashlib.sha256()
    h.update(b"NAMEDOP")
    if node.name:
        h.update(node.name.encode())
    h.update(_hash_operation(node.valtype).encode())
    return h.hexdigest()


def _hash_if(node: zast.If) -> str:
    h = hashlib.sha256()
    h.update(b"IF")
    for clause in node.clauses:
        for cname, cop in clause.conditions.items():
            h.update(cname.encode())
            h.update(_hash_operation(cop).encode())
        h.update(_hash_statement(clause.statement).encode())
    if node.elseclause:
        h.update(b"ELSE")
        h.update(_hash_statement(node.elseclause).encode())
    return h.hexdigest()


def _hash_for(node: zast.For) -> str:
    h = hashlib.sha256()
    h.update(b"FOR")
    for cname, cop in node.conditions.items():
        h.update(cname.encode())
        h.update(_hash_operation(cop).encode())
    if node.loop:
        h.update(_hash_statement(node.loop).encode())
    for pc in node.postconditions:
        h.update(_hash_operation(pc).encode())
    return h.hexdigest()


def _hash_do(node: zast.Do) -> str:
    h = hashlib.sha256()
    h.update(b"DO")
    h.update(_hash_statement(node.statement).encode())
    return h.hexdigest()


def _hash_case(node: zast.Case) -> str:
    h = hashlib.sha256()
    h.update(b"CASE")
    h.update(_hash_operation(node.subject).encode())
    for clause in node.clauses:
        h.update(clause.name.encode())
        h.update(_hash_atomid(clause.match).encode())
        h.update(_hash_statement(clause.statement).encode())
    if node.elseclause:
        h.update(b"ELSE")
        h.update(_hash_statement(node.elseclause).encode())
    return h.hexdigest()


def _hash_data(node: zast.Data) -> str:
    h = hashlib.sha256()
    h.update(b"DATA")
    if node.type:
        h.update(_hash_type(node.type).encode())
    for item in node.data:
        h.update(_hash_namedop(item).encode())
    return h.hexdigest()


def _hash_with(node: zast.With) -> str:
    h = hashlib.sha256()
    h.update(b"WITH")
    h.update(node.name.encode())
    h.update(_hash_expression(node.value).encode())
    h.update(_hash_expression(node.doexpr).encode())
    return h.hexdigest()


def _hash_binop(node: zast.BinOp) -> str:
    h = hashlib.sha256()
    h.update(b"BINOP")
    h.update(node.operator.name.encode())
    if node.type:
        h.update(_hash_type(node.type).encode())
    h.update(_hash_operation(node.lhs).encode())
    h.update(_hash_path(node.rhs).encode())
    return h.hexdigest()


def _hash_dottedpath(node: zast.DottedPath) -> str:
    h = hashlib.sha256()
    h.update(b"DOT")
    if node.type:
        h.update(_hash_type(node.type).encode())
    h.update(_hash_path(node.parent).encode())
    h.update(node.child.name.encode())
    return h.hexdigest()


def _hash_atomid(node: zast.AtomId) -> str:
    h = hashlib.sha256()
    h.update(b"ATOMID")
    h.update(node.name.encode())
    if node.type:
        h.update(_hash_type(node.type).encode())
    return h.hexdigest()


def _hash_atomstring(node: zast.AtomString) -> str:
    h = hashlib.sha256()
    h.update(b"ATOMSTR")
    for part in node.stringparts:
        if part.is_node:
            h.update(_hash_expression(cast(zast.Expression, part)).encode())
        else:
            # Token — hash its string content
            h.update(cast(Token, part).tokstr.encode())
    return h.hexdigest()


_node_handlers = {
    NodeType.STATEMENT: _hash_statement,
    NodeType.STATEMENTLINE: _hash_statementline,
    NodeType.ASSIGNMENT: _hash_assignment,
    NodeType.EXPRESSION: _hash_expression,
    NodeType.CALL: _hash_call,
    NodeType.IF: _hash_if,
    NodeType.FOR: _hash_for,
    NodeType.DO: _hash_do,
    NodeType.CASE: _hash_case,
    NodeType.DATA: _hash_data,
    NodeType.BINOP: _hash_binop,
    NodeType.DOTTEDPATH: _hash_dottedpath,
    NodeType.ATOMID: _hash_atomid,
    NodeType.ATOMSTRING: _hash_atomstring,
    NodeType.NAMEDOPERATION: _hash_namedop,
    NodeType.SWAP: _hash_swap,
    NodeType.REASSIGNMENT: _hash_reassignment,
}
