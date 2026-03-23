"""
SQL dump of compiler state for analysis.

Walks the compiled program and emitter output, producing SQL INSERT
statements that match the schema from the code review document.
"""

from typing import List, Optional, Tuple

import zast
from zlexer import Token
from ztypes import ZType, ZTypeType
import zemitterc


def _sql_str(s: Optional[str]) -> str:
    """Escape a string for SQL, or return NULL."""
    if s is None:
        return "NULL"
    escaped = s.replace("'", "''")
    return f"'{escaped}'"


def _sql_bool(b: Optional[bool]) -> str:
    if b is None:
        return "NULL"
    return "1" if b else "0"


def _sql_int(i: Optional[int]) -> str:
    if i is None:
        return "NULL"
    return str(int(i))


# ---- Schema DDL ----

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS files (
    file_id     INTEGER PRIMARY KEY,
    path        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    token_id    INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(file_id),
    line        INTEGER NOT NULL,
    col         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ast_nodes (
    node_id         INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,
    token_id        INTEGER REFERENCES tokens(token_id),
    name            TEXT,
    file_id         INTEGER REFERENCES files(file_id),
    start_line      INTEGER,
    start_col       INTEGER
);

CREATE TABLE IF NOT EXISTS types (
    type_id           INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    typetype          TEXT NOT NULL,
    parent_type_id    INTEGER REFERENCES types(type_id),
    is_valtype        BOOLEAN,
    is_generic        BOOLEAN DEFAULT 0,
    typedef_base_id   INTEGER REFERENCES types(type_id),
    generic_origin_id INTEGER REFERENCES types(type_id),
    needs_destructor  BOOLEAN,
    destructor_name   TEXT,
    is_heap_allocated BOOLEAN,
    cname             TEXT
);

CREATE TABLE IF NOT EXISTS type_children (
    type_id       INTEGER NOT NULL REFERENCES types(type_id),
    child_name    TEXT NOT NULL,
    child_type_id INTEGER NOT NULL REFERENCES types(type_id),
    position      INTEGER NOT NULL,
    PRIMARY KEY (type_id, child_name)
);

CREATE TABLE IF NOT EXISTS typed_nodes (
    node_id     INTEGER PRIMARY KEY REFERENCES ast_nodes(node_id),
    type_id     INTEGER REFERENCES types(type_id)
);

CREATE TABLE IF NOT EXISTS emitted_lines (
    line_num    INTEGER PRIMARY KEY,
    node_id     INTEGER REFERENCES ast_nodes(node_id),
    text        TEXT NOT NULL
);
"""


# ---- Data collection ----

def _collect_tokens(program: zast.Program) -> List[Token]:
    """Collect all unique tokens from AST nodes."""
    tokens: dict[int, Token] = {}
    visited: set[int] = set()

    def _walk(node):
        nid = id(node)
        if nid in visited:
            return
        visited.add(nid)
        if isinstance(node, zast.Node) and hasattr(node, "start") and node.start:
            tok = node.start
            tokens[tok.tokenid] = tok
        if hasattr(node, "__dataclass_fields__"):
            for fname in node.__dataclass_fields__:
                val = getattr(node, fname, None)
                if val is None:
                    continue
                if isinstance(val, zast.Node):
                    _walk(val)
                elif isinstance(val, Token):
                    tokens[val.tokenid] = val
                elif isinstance(val, dict):
                    for v in val.values():
                        if isinstance(v, zast.Node):
                            _walk(v)
                elif isinstance(val, list):
                    for v in val:
                        if isinstance(v, zast.Node):
                            _walk(v)

    for unit in program.units.values():
        _walk(unit)
    return list(tokens.values())


def _collect_ast_nodes(program: zast.Program) -> List[Tuple[zast.Node, str]]:
    """Collect all AST nodes with their definition name context."""
    nodes: list[Tuple[zast.Node, str]] = []
    visited: set[int] = set()

    def _walk(node, name: str):
        nid = id(node)
        if nid in visited:
            return
        visited.add(nid)
        if isinstance(node, zast.Node):
            nodes.append((node, name))
        if hasattr(node, "__dataclass_fields__"):
            for fname in node.__dataclass_fields__:
                val = getattr(node, fname, None)
                if val is None:
                    continue
                if isinstance(val, zast.Node):
                    _walk(val, name)
                elif isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, zast.Node):
                            child_name = f"{name}.{k}" if name else k
                            _walk(v, child_name)
                elif isinstance(val, list):
                    for v in val:
                        if isinstance(v, zast.Node):
                            _walk(v, name)

    for uname, unit in program.units.items():
        _walk(unit, uname)
    return nodes


# ---- SQL generation ----

def dump_sql(
    program: zast.Program,
    emitter: Optional[zemitterc.CEmitter] = None,
    csource: Optional[str] = None,
) -> str:
    """Generate SQL statements for the full compiler state.

    Returns a string of SQL (schema DDL + INSERT statements).
    """
    lines: List[str] = []
    lines.append(SCHEMA_SQL)

    # Stage 1: files
    file_table = program.vfs.file_table()
    for file_id, path in file_table:
        lines.append(
            f"INSERT INTO files VALUES ({file_id}, {_sql_str(path)});"
        )

    # Stage 2: tokens
    tokens = _collect_tokens(program)
    for tok in sorted(tokens, key=lambda t: t.tokenid):
        lines.append(
            f"INSERT INTO tokens VALUES ("
            f"{tok.tokenid}, {_sql_int(tok.fsno)}, {tok.lineno}, {tok.colno}, "
            f"{_sql_str(tok.toktype.name)}, {_sql_str(tok.tokstr)});"
        )

    # Stage 3: AST nodes
    ast_nodes = _collect_ast_nodes(program)
    for node, name in ast_nodes:
        kind = type(node).__name__
        token_id = _sql_int(node.start.tokenid) if node.start else "NULL"
        file_id = _sql_int(node.start.fsno) if node.start else "NULL"
        start_line = _sql_int(node.start.lineno) if node.start else "NULL"
        start_col = _sql_int(node.start.colno) if node.start else "NULL"
        lines.append(
            f"INSERT INTO ast_nodes VALUES ("
            f"{node.nodeid}, {_sql_str(kind)}, {token_id}, "
            f"{_sql_str(name)}, {file_id}, {start_line}, {start_col});"
        )

    # Stage 4: types — collect all reachable types
    all_types: dict[int, ZType] = {}

    def _register_type(zt: ZType) -> None:
        if zt.nodeid in all_types:
            return
        all_types[zt.nodeid] = zt
        for ctype in zt.children.values():
            _register_type(ctype)
        if zt.parent and isinstance(zt.parent, ZType):
            _register_type(zt.parent)
        if zt.typedef_base:
            _register_type(zt.typedef_base)
        if isinstance(zt.generic_origin, ZType):
            _register_type(zt.generic_origin)

    # from resolved dict
    for ztype in program.resolved.values():
        _register_type(ztype)
    # from AST node type annotations
    for node, _ in ast_nodes:
        if node.type is not None:
            _register_type(node.type)

    for ztype in all_types.values():
        parent_id = _sql_int(ztype.parent.nodeid) if ztype.parent else "NULL"
        typedef_id = _sql_int(ztype.typedef_base.nodeid) if ztype.typedef_base else "NULL"
        origin_id = "NULL"
        if isinstance(ztype.generic_origin, ZType):
            origin_id = _sql_int(ztype.generic_origin.nodeid)
        lines.append(
            f"INSERT OR IGNORE INTO types VALUES ("
            f"{ztype.nodeid}, {_sql_str(ztype.name)}, "
            f"{_sql_str(ztype.typetype.name)}, {parent_id}, "
            f"{_sql_bool(ztype.is_valtype)}, {_sql_bool(ztype.isgeneric)}, "
            f"{typedef_id}, {origin_id}, "
            f"{_sql_bool(ztype.needs_destructor)}, "
            f"{_sql_str(ztype.destructor_name)}, "
            f"{_sql_bool(ztype.is_heap_allocated)}, "
            f"{_sql_str(ztype.cname if ztype.cname else None)});"
        )

    # type_children
    for ztype in all_types.values():
        for i, (cname, ctype) in enumerate(ztype.children.items()):
            lines.append(
                f"INSERT OR IGNORE INTO type_children VALUES ("
                f"{ztype.nodeid}, {_sql_str(cname)}, {ctype.nodeid}, {i});"
            )

    # Stage 5: typed nodes (AST nodes with type annotations)
    for node, name in ast_nodes:
        if node.type is not None:
            lines.append(
                f"INSERT OR IGNORE INTO typed_nodes VALUES ("
                f"{node.nodeid}, {node.type.nodeid});"
            )

    # Stage 6: emitted lines (if emitter provided)
    if emitter and csource:
        c_lines = csource.split("\n")
        for i, (text, nid) in enumerate(zip(c_lines, emitter.source_map)):
            lines.append(
                f"INSERT INTO emitted_lines VALUES ("
                f"{i + 1}, {_sql_int(nid)}, {_sql_str(text)});"
            )

    return "\n".join(lines) + "\n"
