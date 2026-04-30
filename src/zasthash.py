"""
AST content hasher for code deduplication.

Produces a deterministic hash of a Function AST node based on its structure
and resolved types, excluding function name and 'this' parameter type name.
Used during monomorphization to detect identical function bodies.

After Step 6.9.b stripped `Node.type`, the per-node resolved types are
read from a `node_types: Dict[int, ZType]` dict threaded through every
helper. The TypeChecker passes `program.node_type` (the F5.D
Program-owned side-table) at call time.
"""

import hashlib
from typing import Dict, Optional, cast

import zast
from zast import NodeType
from ztypes import ZType


NodeTypes = Dict[int, Optional[ZType]]


def hash_function(func: zast.Function, node_types: NodeTypes) -> str:
    """Hash a typechecked Function node, returning a hex digest.

    Hashes parameter types, return type, and body structure. Function
    name is excluded. `node_types` maps parsed `nodeid` to resolved
    `ZType` (was `Node.type` before Step 6.9.b).
    """
    h = hashlib.sha256()

    # hash parameter types (order matters)
    for pname, ppath in func.parameters.items():
        h.update(pname.encode())
        h.update(b":")
        ppath_t = node_types.get(ppath.nodeid)
        if ppath_t:
            h.update(_hash_type(ppath_t).encode())

    # hash return type
    h.update(b"|RET|")
    if func.returntype:
        rt = node_types.get(func.returntype.nodeid)
        if rt:
            h.update(_hash_type(rt).encode())

    # hash body
    h.update(b"|BODY|")
    if func.is_native:
        h.update(b"|NATIVE|")
    elif func.body:
        h.update(_hash_node(func.body, node_types).encode())

    return h.hexdigest()


def _hash_type(ztype: ZType) -> str:
    """Hash a ZType by name and typetype."""
    return f"{ztype.typetype.name}:{ztype.name}"


def _hash_node(node: zast.Node, node_types: NodeTypes) -> str:
    """Recursively hash an AST node by structure and types."""
    handler = _node_handlers.get(node.nodetype)
    if handler:
        return handler(node, node_types)  # type: ignore[arg-type]
    # fallback: hash nodetype + type
    h = hashlib.sha256()
    h.update(node.nodetype.name.encode())
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    return h.hexdigest()


def _hash_statement(node: zast.Statement, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"STATEMENT")
    for sline in node.statements:
        h.update(_hash_statementline(sline, node_types).encode())
    return h.hexdigest()


def _hash_statementline(node: zast.StatementLine, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"SLINE")
    inner = node.statementline
    if inner.nodetype == NodeType.ASSIGNMENT:
        h.update(_hash_assignment(cast(zast.Assignment, inner), node_types).encode())
    elif inner.nodetype == NodeType.REASSIGNMENT:
        h.update(
            _hash_reassignment(cast(zast.Reassignment, inner), node_types).encode()
        )
    elif inner.nodetype == NodeType.SWAP:
        h.update(_hash_swap(cast(zast.Swap, inner), node_types).encode())
    elif inner.nodetype == NodeType.EXPRESSION:
        h.update(_hash_expression(cast(zast.Expression, inner), node_types).encode())
    return h.hexdigest()


def _hash_assignment(node: zast.Assignment, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"ASSIGN")
    h.update(node.name.encode())
    h.update(_hash_expression(node.value, node_types).encode())
    return h.hexdigest()


def _hash_reassignment(node: zast.Reassignment, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"REASSIGN")
    h.update(_hash_path(node.topath, node_types).encode())
    h.update(_hash_expression(node.value, node_types).encode())
    return h.hexdigest()


def _hash_swap(node: zast.Swap, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"SWAP")
    h.update(_hash_path(node.lhs, node_types).encode())
    h.update(_hash_path(node.rhs, node_types).encode())
    return h.hexdigest()


def _hash_expression(node: zast.Expression, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"EXPR")
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    inner = node.expression
    if inner.nodetype == NodeType.CALL:
        h.update(_hash_call(cast(zast.Call, inner), node_types).encode())
    elif inner.nodetype == NodeType.IF:
        h.update(_hash_if(cast(zast.If, inner), node_types).encode())
    elif inner.nodetype == NodeType.FOR:
        h.update(_hash_for(cast(zast.For, inner), node_types).encode())
    elif inner.nodetype == NodeType.DO:
        h.update(_hash_do(cast(zast.Do, inner), node_types).encode())
    elif inner.nodetype == NodeType.CASE:
        h.update(_hash_case(cast(zast.Case, inner), node_types).encode())
    elif inner.nodetype == NodeType.DATA:
        h.update(_hash_data(cast(zast.Data, inner), node_types).encode())
    elif inner.nodetype == NodeType.WITH:
        h.update(_hash_with(cast(zast.With, inner), node_types).encode())
    else:
        h.update(_hash_operation(cast(zast.Operation, inner), node_types).encode())
    return h.hexdigest()


def _hash_path(path: zast.Path, node_types: NodeTypes) -> str:
    if path.nodetype == NodeType.DOTTEDPATH:
        return _hash_dottedpath(cast(zast.DottedPath, path), node_types)
    if path.nodetype == NodeType.ATOMID:
        return _hash_atomid(cast(zast.AtomId, path), node_types)
    if path.nodetype == NodeType.LABELVALUE:
        return _hash_atomid(cast(zast.AtomId, path), node_types)
    if path.nodetype == NodeType.ATOMSTRING:
        return _hash_atomstring(cast(zast.AtomString, path), node_types)
    if path.nodetype == NodeType.EXPRESSION:
        return _hash_expression(cast(zast.Expression, path), node_types)
    if path.nodetype == NodeType.BINOP:
        return _hash_binop(cast(zast.BinOp, path), node_types)
    # fallback
    return _hash_node(path, node_types)


def _hash_operation(node: zast.Operation, node_types: NodeTypes) -> str:
    if node.nodetype == NodeType.BINOP:
        return _hash_binop(cast(zast.BinOp, node), node_types)
    # All Path subclasses (DottedPath, AtomId, AtomString, Expression, LabelValue)
    return _hash_path(cast(zast.Path, node), node_types)


def _hash_call(node: zast.Call, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"CALL")
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    h.update(_hash_path(node.callable, node_types).encode())
    for arg in node.arguments:
        h.update(_hash_namedop(arg, node_types).encode())
    return h.hexdigest()


def _hash_namedop(node: zast.NamedOperation, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"NAMEDOP")
    if node.name:
        h.update(node.name.encode())
    h.update(_hash_operation(node.valtype, node_types).encode())
    return h.hexdigest()


def _hash_if(node: zast.If, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"IF")
    for clause in node.clauses:
        for cname, cop in clause.conditions.items():
            h.update(cname.encode())
            h.update(_hash_operation(cop, node_types).encode())
        h.update(_hash_statement(clause.statement, node_types).encode())
    if node.elseclause:
        h.update(b"ELSE")
        h.update(_hash_statement(node.elseclause, node_types).encode())
    return h.hexdigest()


def _hash_for(node: zast.For, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"FOR")
    for cname, cop in node.conditions.items():
        h.update(cname.encode())
        h.update(_hash_operation(cop, node_types).encode())
    if node.loop:
        h.update(_hash_statement(node.loop, node_types).encode())
    for pc in node.postconditions:
        h.update(_hash_operation(pc, node_types).encode())
    return h.hexdigest()


def _hash_do(node: zast.Do, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"DO")
    h.update(_hash_statement(node.statement, node_types).encode())
    return h.hexdigest()


def _hash_case(node: zast.Case, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"CASE")
    h.update(_hash_operation(node.subject, node_types).encode())
    for clause in node.clauses:
        h.update(clause.name.encode())
        h.update(_hash_atomid(clause.match, node_types).encode())
        h.update(_hash_statement(clause.statement, node_types).encode())
    if node.elseclause:
        h.update(b"ELSE")
        h.update(_hash_statement(node.elseclause, node_types).encode())
    return h.hexdigest()


def _hash_data(node: zast.Data, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"DATA")
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    for item in node.data:
        h.update(_hash_namedop(item, node_types).encode())
    return h.hexdigest()


def _hash_with(node: zast.With, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"WITH")
    h.update(node.name.encode())
    h.update(_hash_expression(node.value, node_types).encode())
    h.update(_hash_expression(node.doexpr, node_types).encode())
    return h.hexdigest()


def _hash_binop(node: zast.BinOp, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"BINOP")
    h.update(node.operator.name.encode())
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    h.update(_hash_operation(node.lhs, node_types).encode())
    h.update(_hash_path(node.rhs, node_types).encode())
    return h.hexdigest()


def _hash_dottedpath(node: zast.DottedPath, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"DOT")
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    h.update(_hash_path(node.parent, node_types).encode())
    h.update(node.child.name.encode())
    return h.hexdigest()


def _hash_atomid(node: zast.AtomId, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"ATOMID")
    h.update(node.name.encode())
    nt = node_types.get(node.nodeid)
    if nt:
        h.update(_hash_type(nt).encode())
    return h.hexdigest()


def _hash_atomstring(node: zast.AtomString, node_types: NodeTypes) -> str:
    h = hashlib.sha256()
    h.update(b"ATOMSTR")
    for part in node.stringparts:
        if part.nodetype == NodeType.STRINGCHUNK:
            h.update(cast(zast.StringChunk, part).text.encode())
        else:
            h.update(_hash_expression(cast(zast.Expression, part), node_types).encode())
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
