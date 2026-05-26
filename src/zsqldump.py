"""
SQL dump of compiler state for analysis.

Walks the compiled program and emitter output, producing SQL INSERT
statements that match the schema from the code review document.
"""

from typing import List, Optional, Tuple, cast

import zast
import ztyping
from zast import NodeType
from zlexer import Token
from ztypes import ZType
import zemitterc


# Operation-shaped node kinds whose typed mirrors carry `ztype` /
# `const_value`. Mirrors `CEmitter._node_const_value` filtering.
_OP_NODETYPES = (
    NodeType.ATOMID,
    NodeType.LABELVALUE,
    NodeType.ATOMSTRING,
    NodeType.DOTTEDPATH,
    NodeType.BINOP,
    NodeType.CALL,
    NodeType.IF,
    NodeType.CASE,
    NodeType.FOR,
    NodeType.DO,
    NodeType.WITH,
)


def _node_ztype(typing: "ztyping.ZTyping", node: zast.Node) -> Optional[ZType]:
    """Read the resolved `ZType` for `node`, descending through the
    `Expression` wrapper. Tries the inner-subtype nodeid first and
    falls back to the outer Expression nodeid (typecheck stamps both
    paths)."""
    target = node
    while target.nodetype == NodeType.EXPRESSION:
        target = cast(zast.Expression, target).expression
    zt = typing.node_type.get(target.nodeid)
    if zt is not None:
        return zt
    return typing.node_type.get(node.nodeid)


def _node_const_value(typing: "ztyping.ZTyping", node: zast.Node):
    """Read `const_value` for `node`. Same descent rule as
    `_node_ztype`."""
    target = node
    while target.nodetype == NodeType.EXPRESSION:
        target = cast(zast.Expression, target).expression
    v = typing.node_const_value.get(target.nodeid)
    if v is not None:
        return v
    return typing.node_const_value.get(node.nodeid)


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
    start_col       INTEGER,
    synth_origin    TEXT
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
    child_id      INTEGER,
    is_private    INTEGER NOT NULL DEFAULT 0,
    is_lock_field INTEGER NOT NULL DEFAULT 0,
    is_lock_arm   INTEGER NOT NULL DEFAULT 0,
    default_expr  TEXT,
    param_ownership INTEGER,
    PRIMARY KEY (type_id, child_name)
);

-- typed_nodes carries the typechecker-derived per-node data
-- (resolved ZType, constant-fold result, mangled C name). One row
-- per parsed AST node that the typechecker typed; FK to ast_nodes
-- via node_id. Step 5b of the typed-tree migration: parser-set and
-- typecheck-set fields are now in disjoint tables.
CREATE TABLE IF NOT EXISTS typed_nodes (
    node_id      INTEGER PRIMARY KEY REFERENCES ast_nodes(node_id),
    type_id      INTEGER REFERENCES types(type_id),
    cname        TEXT,
    is_const     BOOLEAN,
    const_value  TEXT
);

CREATE TABLE IF NOT EXISTS emitted_lines (
    line_num    INTEGER PRIMARY KEY,
    node_id     INTEGER REFERENCES ast_nodes(node_id),
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit (
    unit_id      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    is_main      BOOLEAN NOT NULL,
    unit_type_id INTEGER REFERENCES types(type_id)
);

CREATE TABLE IF NOT EXISTS scope (
    scope_id       INTEGER PRIMARY KEY,
    parent_id      INTEGER REFERENCES scope(scope_id),
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    depth          INTEGER NOT NULL,
    opened_at_seq  INTEGER NOT NULL,
    closed_at_seq  INTEGER,
    unreachable    BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS variable (
    variable_id       INTEGER PRIMARY KEY,
    ztype_id          INTEGER REFERENCES types(type_id),
    ownership         TEXT NOT NULL,
    is_private_access BOOLEAN NOT NULL,
    borrow_origin     TEXT,
    synth_origin      TEXT
);

CREATE TABLE IF NOT EXISTS entry (
    entry_id              INTEGER PRIMARY KEY,
    scope_id              INTEGER NOT NULL REFERENCES scope(scope_id),
    position              INTEGER NOT NULL,
    name                  TEXT NOT NULL,
    ztype_id              INTEGER NOT NULL REFERENCES types(type_id),
    is_definition         BOOLEAN NOT NULL,
    variable_id           INTEGER REFERENCES variable(variable_id),
    original_ztype_id     INTEGER REFERENCES types(type_id),
    is_taken              BOOLEAN NOT NULL,
    taken_at_line         INTEGER,
    taken_at_col          INTEGER,
    taken_at_file         INTEGER
);

-- Per-entry narrowing state. One row per narrowed-to or excluded
-- subtype (singular table name per CLAUDE.md).
CREATE TABLE IF NOT EXISTS narrowed_subtype (
    entry_id  INTEGER NOT NULL REFERENCES entry(entry_id),
    name      TEXT NOT NULL,
    type_id   INTEGER,
    excluded  INTEGER NOT NULL  -- 0 = narrowed-to, 1 = excluded
);
"""


# ---- Data collection ----


def _collect_tokens(program: zast.Program) -> List[Token]:
    """Collect all unique tokens from AST nodes via a typed walk."""
    tokens: dict[int, Token] = {}
    visited: set[int] = set()
    pending: list[zast.Node] = list(program.units.values())
    while pending:
        node = pending.pop()
        nid = id(node)
        if nid in visited:
            continue
        visited.add(nid)
        if node.start is not None:
            tokens[node.start.tokenid] = node.start
        for tok in zast.node_tokens(node):
            tokens[tok.tokenid] = tok
        pending.extend(zast.node_children(node))
    return list(tokens.values())


def _collect_ast_nodes(program: zast.Program) -> List[Tuple[zast.Node, str]]:
    """Collect all AST nodes with a context name.

    Top-level definitions in each unit's body get the dict key as their
    context (e.g. `"main"`, `"foo.bar"`); nodes deeper in the tree
    inherit the nearest top-level definition's name. Walk drives off the
    typed `node_children` enumeration — no `__dataclass_fields__`
    reflection.
    """
    nodes: list[Tuple[zast.Node, str]] = []
    visited: set[int] = set()

    def _walk_subtree(root: zast.Node, label: str) -> None:
        pending: list[zast.Node] = [root]
        while pending:
            node = pending.pop()
            nid = id(node)
            if nid in visited:
                continue
            visited.add(nid)
            nodes.append((node, label))
            pending.extend(zast.node_children(node))

    for uname, unit in program.units.items():
        # The Unit node itself is labeled with the unit name.
        if id(unit) not in visited:
            visited.add(id(unit))
            nodes.append((unit, uname))
        for dname, defn in unit.body.items():
            sublabel = f"{uname}.{dname}" if uname else dname
            _walk_subtree(defn, sublabel)
    return nodes


# ---- SQL generation ----


def dump_sql(
    typing: ztyping.ZTyping,
    emitter: Optional[zemitterc.CEmitter] = None,
    csource: Optional[str] = None,
) -> str:
    """Generate SQL statements for the full compiler state.

    Returns a string of SQL (schema DDL + INSERT statements).
    """
    program = typing.parsed
    lines: List[str] = []
    lines.append(SCHEMA_SQL)

    # Stage 1: files
    file_table = program.vfs.file_table()
    for file_id, path in file_table:
        lines.append(f"INSERT INTO files VALUES ({file_id}, {_sql_str(path)});")

    # Stage 2: tokens
    tokens = _collect_tokens(program)

    def _by_tokenid(t: Token) -> int:
        return t.tokenid

    for tok in sorted(tokens, key=_by_tokenid):
        lines.append(
            f"INSERT INTO tokens VALUES ("
            f"{tok.tokenid}, {_sql_int(tok.fsno)}, {tok.lineno}, {tok.colno}, "
            f"{_sql_str(tok.toktype.name)}, {_sql_str(tok.tokstr)});"
        )

    # Stage 3: AST nodes (parser-set fields only — Step 5b)
    ast_nodes = _collect_ast_nodes(program)
    for node, name in ast_nodes:
        kind = type(node).__name__
        token_id = _sql_int(node.start.tokenid) if node.start else "NULL"
        file_id = _sql_int(node.start.fsno) if node.start else "NULL"
        start_line = _sql_int(node.start.lineno) if node.start else "NULL"
        start_col = _sql_int(node.start.colno) if node.start else "NULL"
        synth_origin = _sql_str(node.synth_origin)
        lines.append(
            f"INSERT INTO ast_nodes VALUES ("
            f"{node.nodeid}, {_sql_str(kind)}, {token_id}, "
            f"{_sql_str(name)}, {file_id}, {start_line}, {start_col}, "
            f"{synth_origin});"
        )

    # Stage 4: types — collect all reachable types
    all_types: dict[int, ZType] = {}

    def _register_type(zt: ZType) -> None:
        if zt.type_id in all_types:
            return
        all_types[zt.type_id] = zt
        for ctype in typing.child_types_of(zt):
            _register_type(ctype)
        if zt.return_type:
            _register_type(zt.return_type)
        zt_bound = zt.bound_type()
        if zt_bound is not None:
            _register_type(zt_bound)
        zt_owner = zt.data_owner_type()
        if zt_owner is not None:
            _register_type(zt_owner)
        if zt.typedef_base:
            _register_type(zt.typedef_base)
        if zt.generic_origin is not None:
            _register_type(zt.generic_origin)

    # from resolved dict
    for ztype in typing.resolved.values():
        _register_type(ztype)
    # from AST node type annotations (route via typed mirror so the
    # parsed `init=False` `.type` field can be retired in Step 6).
    for node, _ in ast_nodes:
        n_ztype = _node_ztype(typing, node)
        if n_ztype is not None:
            _register_type(n_ztype)

    for ztype in all_types.values():
        # bound_id (GENERIC_PARAM markers) and data_owner_id (tag RECORDs)
        # are mutually exclusive by typetype; emit whichever is set under
        # the existing parent_type_id column.
        if ztype.bound_id is not None:
            parent_id = _sql_int(ztype.bound_id)
        elif ztype.data_owner_id is not None:
            parent_id = _sql_int(ztype.data_owner_id)
        else:
            parent_id = "NULL"
        typedef_id = (
            _sql_int(ztype.typedef_base.type_id) if ztype.typedef_base else "NULL"
        )
        origin_id = "NULL"
        if ztype.generic_origin is not None:
            origin_id = _sql_int(ztype.generic_origin.type_id)
        lines.append(
            f"INSERT OR IGNORE INTO types VALUES ("
            f"{ztype.type_id}, {_sql_str(ztype.name)}, "
            f"{_sql_str(ztype.typetype.name)}, {parent_id}, "
            f"{_sql_bool(ztype.is_valtype)}, {_sql_bool(ztype.isgeneric)}, "
            f"{typedef_id}, {origin_id}, "
            f"{_sql_bool((ztype.destructor_name is not None))}, "
            f"{_sql_str(ztype.destructor_name)}, "
            f"{_sql_bool(ztype.is_heap_allocated)}, "
            f"{_sql_str(ztype.cname if ztype.cname else None)});"
        )

    # type_children: dumped directly from the flat typing.type_child
    # table. Rows belonging to types unreachable from `all_types` (e.g.
    # discarded mono shells, transient builders) are filtered out so
    # the dump stays in sync with the `types` table.
    for row in typing.type_child:
        if row.parent_type_id not in all_types:
            continue
        lines.append(
            f"INSERT OR IGNORE INTO type_children VALUES ("
            f"{row.parent_type_id}, {_sql_str(row.child_name)}, "
            f"{row.child_type_id}, {row.position}, {row.child_name_id}, "
            f"{_sql_bool(row.is_private)}, {_sql_bool(row.is_lock_field)}, "
            f"{_sql_bool(row.is_lock_arm)}, {_sql_str(row.default)}, "
            f"{_sql_int(int(row.param_ownership)) if row.param_ownership is not None else 'NULL'});"
        )

    # Stage 5: typed nodes — typecheck-set per-node data. Step 5b
    # consolidated the cname / is_const / const_value columns out of
    # ast_nodes so the parser-side and typecheck-side rows are
    # disjoint.
    for node, name in ast_nodes:
        n_ztype = _node_ztype(typing, node)
        n_const = _node_const_value(typing, node)
        # Skip rows where the node carries no typecheck-set data at
        # all — reflects a parser-only node (e.g. a structural child
        # selector, error sentinel) untouched by typecheck.
        if n_ztype is None and n_const is None:
            continue
        type_id = _sql_int(n_ztype.type_id) if n_ztype is not None else "NULL"
        cname = _sql_str(n_ztype.cname) if n_ztype and n_ztype.cname else "NULL"
        is_const = _sql_bool(n_const is not None)
        const_val = _sql_str(str(n_const)) if n_const is not None else "NULL"
        lines.append(
            f"INSERT OR IGNORE INTO typed_nodes VALUES ("
            f"{node.nodeid}, {type_id}, {cname}, {is_const}, {const_val});"
        )

    # Stage 5b: units. unit_id is the Unit AST's Node.nodeid. unit_type_id
    # comes from the id-keyed unit_types snapshot; NULL when a unit was
    # never materialised by typecheck.
    unit_types_map = typing.unit_types_by_id
    for unitname, unit_ast in program.units.items():
        utype = unit_types_map.get(unit_ast.nodeid)
        unit_type_id = _sql_int(utype.type_id) if utype is not None else "NULL"
        lines.append(
            f"INSERT OR IGNORE INTO unit VALUES ("
            f"{unit_ast.nodeid}, {_sql_str(unitname)}, "
            f"{_sql_bool(unitname == program.mainunitname)}, "
            f"{unit_type_id});"
        )

    # Stage 6a: symbol table — scopes, variables, entries. Iterates
    # `symtab.scope_log` (append-only single source of truth for scope
    # order). Each row carries the live ZScope reference so
    # entries/unreachable read off the row without a lookup. The
    # dumper tolerates a missing symbol_table (e.g. when called
    # without running typecheck): simply emits no rows for the
    # symtab tables.
    symtab = typing.symbol_table
    if symtab is not None:
        seen_vars: dict[int, object] = {}
        for depth, row in enumerate(symtab.scope_log):
            scope = row.scope
            lines.append(
                f"INSERT OR IGNORE INTO scope VALUES ("
                f"{row.scope_id}, {_sql_int(row.parent_id)}, "
                f"{_sql_str(row.kind.name)}, {_sql_str(row.name)}, "
                f"{depth}, {row.opened_at_seq}, "
                f"{_sql_int(row.closed_at_seq)}, "
                f"{_sql_bool(scope.unreachable)});"
            )
            for pos, entry in enumerate(scope.entries):
                var_id_sql = "NULL"
                if entry.var is not None:
                    vid = entry.var.variable_id
                    if vid not in seen_vars:
                        seen_vars[vid] = entry.var
                    var_id_sql = str(vid)
                orig_id_sql = (
                    _sql_int(entry.original_ztype.type_id)
                    if entry.original_ztype is not None
                    else "NULL"
                )
                taken_line = _sql_int(entry.taken_at[0]) if entry.taken_at else "NULL"
                taken_col = _sql_int(entry.taken_at[1]) if entry.taken_at else "NULL"
                taken_file = _sql_int(entry.taken_at[2]) if entry.taken_at else "NULL"
                lines.append(
                    f"INSERT OR IGNORE INTO entry VALUES ("
                    f"{entry.entry_id}, {scope.scope_id}, {pos}, "
                    f"{_sql_str(entry.name)}, {entry.ztype.type_id}, "
                    f"{_sql_bool(entry.is_definition)}, {var_id_sql}, "
                    f"{orig_id_sql}, "
                    f"{_sql_bool(entry.is_taken)}, "
                    f"{taken_line}, {taken_col}, {taken_file});"
                )
                # Per-entry narrowing as child rows: one row for the
                # narrowed-to subtype (excluded=0) when present, one
                # row per excluded subtype (excluded=1).
                if entry.narrowed_subtype is not None:
                    lines.append(
                        f"INSERT INTO narrowed_subtype VALUES ("
                        f"{entry.entry_id}, "
                        f"{_sql_str(entry.narrowed_subtype)}, "
                        f"{_sql_int(entry.narrowed_subtype_id)}, 0);"
                    )
                exs = entry.excluded_subtypes
                exs_ids = entry.excluded_subtype_ids
                if exs:
                    ids_by_name: dict[str, Optional[int]] = {}
                    if exs_ids is not None and len(exs) == len(exs_ids):
                        # ids_by_name keyed by name iff cardinalities
                        # match — same shape the old CSV pair assumed.
                        for nm, tid in zip(sorted(exs), sorted(exs_ids)):
                            ids_by_name[nm] = tid
                    for nm in sorted(exs):
                        tid_sql = _sql_int(ids_by_name.get(nm))
                        lines.append(
                            f"INSERT INTO narrowed_subtype VALUES ("
                            f"{entry.entry_id}, {_sql_str(nm)}, "
                            f"{tid_sql}, 1);"
                        )
        for vid, var in seen_vars.items():
            lines.append(
                f"INSERT OR IGNORE INTO variable VALUES ("
                f"{vid}, {_sql_int(var.ztype.type_id)}, "  # type: ignore[attr-defined]
                f"{_sql_str(var.ownership.name)}, "  # type: ignore[attr-defined]
                f"{_sql_bool(var.is_private_access)}, "  # type: ignore[attr-defined]
                f"{_sql_str(var.borrow_origin)}, "  # type: ignore[attr-defined]
                f"{_sql_str(var.synth_origin)});"  # type: ignore[attr-defined]
            )

    # Stage 6: emitted lines (if emitter provided)
    if emitter and csource:
        c_lines = csource.split("\n")
        # Source-map and emitted-line counts must agree — a mismatch
        # silently dropped rows under plain `zip` and made source
        # mappings ambiguous. Pre-assert the lengths (the assertion's
        # message points at the bug); `strict=True` is the belt to the
        # assertion's braces.
        assert len(c_lines) == len(emitter.source_map), (
            f"emitter source_map length ({len(emitter.source_map)}) "
            f"does not match emitted-line count ({len(c_lines)})"
        )
        for i, (text, nid) in enumerate(zip(c_lines, emitter.source_map, strict=True)):
            lines.append(
                f"INSERT INTO emitted_lines VALUES ("
                f"{i + 1}, {_sql_int(nid)}, {_sql_str(text)});"
            )

    return "\n".join(lines) + "\n"
