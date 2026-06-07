"""Differential test for the self-hosted SQL dump (src/zsqldump.z + the
ztypecheck.z skeleton + the zc.z driver).

The ported pipeline carries the parse-derived tables a skeleton typecheck
can populate: ``files``, ``ast_nodes`` and ``unit``. We compare the
``zc --dump-sql`` output against the Python reference at the SAME capability
level -- ``TypeChecker(program)`` (its ``__init__`` registers the unit
types) WITHOUT ``.check()`` -- by loading both dumps into SQLite and
projecting each table id-independently. Absolute ids (node/type ids) are not
expected to match between the two compilers (the parser differential strips
them, the symtab differential remaps them), so the projections JOIN away or
omit them:

* files     -> path
* ast_nodes -> kind, name, start_line, start_col   (token_id / file_id are
              NULL in the ported AST -- it has no token/fsno linkage -- so
              they are excluded; node identity is covered by the parser
              differential)
* unit      -> name, is_main                        (unit_type_id depends on
              type-id minting order; deferred until the types table lands)

The corpus is a curated smoke set; it grows toward the full example set as
later slices port the typecheck tables (types / typed_nodes / symbol table /
conformance) into both the dumper and the skeleton.

The ``zc_binary`` fixture (tests/conftest.py) builds src/zc.z once per session
and skips cleanly without a C compiler.
"""

import os
import sqlite3
import subprocess

import pytest

from zvfs import ZVfs, FSProvider, BindType
from zparser import Parser
from ztypecheck import resolve_only_main
from zsqldump import dump_sql

# Building zc.z compiles the entire ported pipeline as one unit -- the
# reference compiler takes ~30s on it, over the default per-test timeout.
pytestmark = [pytest.mark.infra, pytest.mark.timeout(240)]

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
SYSTEM_DIR = os.path.join(REPO_ROOT, "lib", "system")

# Curated smoke set: each fully matches the reference on the implemented
# tables. Covers single + multi unit, recursion, data blocks and swap.
SMOKE = ["hello", "factorial", "mathutil", "swap", "multimod", "data", "fibonacci"]

PROJECTIONS = {
    "files": "SELECT path FROM files ORDER BY path",
    "ast_nodes": (
        "SELECT kind, name, start_line, start_col FROM ast_nodes "
        "ORDER BY kind, name, start_line, start_col"
    ),
    "unit": "SELECT name, is_main FROM unit ORDER BY name",
}

# Examples whose types / type_children are compared. The .z typechecker resolves
# the MAIN unit's FUNCTION, DATA, and RECORD definition signatures.
TYPES_SMOKE = [
    "hello",
    "factorial",
    "fibonacci",
    "mathutil",
    "swap",
    "multimod",
    "data",
    "vector",
    "records",
    "strview",
    "str",
    "with_alias",
]

# SCAFFOLD: `defined_in_unit` filters the comparison to types DEFINED in the
# example's own unit, NOT the (monolithic ~500-type) system closure any
# numeric reference pulls in. This is a TEMPORARY widening scaffold -- as later
# slices deepen the record/variant/class/generic resolvers, the filter is
# relaxed toward the COMPLETE `types` closure. Do not let it ossify. See
# project_zerolang_ztypes_port. cname / destructor_name are excluded (their
# values embed the type id, so they diverge between the two compilers).
TYPED_PROJECTIONS = {
    "types": (
        "SELECT name, typetype, is_valtype, is_generic, needs_destructor, "
        "is_heap_allocated FROM types WHERE defined_in_unit = ? "
        "ORDER BY name, typetype"
    ),
    "type_children": (
        "SELECT pt.name, tc.child_name, ct.name, tc.position, tc.param_ownership "
        "FROM type_children tc "
        "JOIN types pt ON tc.type_id = pt.type_id "
        "JOIN types ct ON tc.child_type_id = ct.type_id "
        "WHERE pt.defined_in_unit = ? ORDER BY pt.name, tc.position"
    ),
}


def _python_skeleton_sql(unit: str) -> str:
    """Reference dump at the ported pipeline's capability: parse over the same
    stdlib + source VFS as zc, run the resolve-only-main pass (resolve every
    main-unit definition SIGNATURE, no function-body walk), and dump."""
    vfs = ZVfs()
    sysid = vfs.register(FSProvider(rootpath=SYSTEM_DIR, parentpath=""))
    srcid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
    root = vfs.walk()
    root = vfs.bind(parentid=root, name=None, newid=sysid)
    root = vfs.bind(parentid=root, name=None, newid=srcid, bindtype=BindType.BEFORE)
    program = Parser(vfs, unit).parse()
    assert not program.is_error, f"python parse failed for {unit}"
    return dump_sql(resolve_only_main(program))


def _zc_sql(zc_binary: str, unit: str) -> str:
    proc = subprocess.run(
        [
            zc_binary,
            unit,
            "--src",
            EXAMPLES_DIR,
            "--system",
            SYSTEM_DIR,
            "--dump-sql",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(f"zc exited {proc.returncode} on {unit}.\nstderr:\n{proc.stderr}")
    return proc.stdout


def _load(sql: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(sql)
    return con


@pytest.mark.emitter
@pytest.mark.parametrize("unit", SMOKE)
def test_dumpsql_matches_python(unit, zc_binary):
    """The .z dump must match the Python skeleton dump on every implemented
    table's id-independent projection."""
    py = _load(_python_skeleton_sql(unit))
    zp = _load(_zc_sql(zc_binary, unit))
    for table, query in PROJECTIONS.items():
        pr = py.execute(query).fetchall()
        zr = zp.execute(query).fetchall()
        if pr != zr:
            only_py = sorted(set(pr) - set(zr))[:10]
            only_z = sorted(set(zr) - set(pr))[:10]
            pytest.fail(
                f"{unit}: table '{table}' diverged "
                f"(python={len(pr)} rows, z={len(zr)} rows).\n"
                f"  only in python: {only_py}\n"
                f"  only in z:      {only_z}"
            )


@pytest.mark.emitter
@pytest.mark.parametrize("unit", TYPES_SMOKE)
def test_dumpsql_types_match_python(unit, zc_binary):
    """The .z dump must match the Python resolve-only dump on the types /
    type_children tables, filtered to the example's own unit (the source-types
    scaffold)."""
    py = _load(_python_skeleton_sql(unit))
    zp = _load(_zc_sql(zc_binary, unit))
    for table, query in TYPED_PROJECTIONS.items():
        pr = py.execute(query, (unit,)).fetchall()
        zr = zp.execute(query, (unit,)).fetchall()
        if pr != zr:
            only_py = sorted(set(pr) - set(zr))[:10]
            only_z = sorted(set(zr) - set(pr))[:10]
            pytest.fail(
                f"{unit}: table '{table}' diverged "
                f"(python={len(pr)} rows, z={len(zr)} rows).\n"
                f"  only in python: {only_py}\n"
                f"  only in z:      {only_z}"
            )


@pytest.mark.emitter
def test_dumpsql_structural_smoke(zc_binary):
    """Standalone invariants on the .z dump for hello, independent of the
    Python oracle."""
    con = _load(_zc_sql(zc_binary, "hello"))
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM unit").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM unit WHERE is_main=1").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM ast_nodes").fetchone()[0] > 0
    # Every unit's node id is a real ast_nodes row (the unit table keys off
    # the unit definition's nodeid).
    orphans = con.execute(
        "SELECT u.name FROM unit u "
        "LEFT JOIN ast_nodes a ON a.node_id = u.unit_id WHERE a.node_id IS NULL"
    ).fetchall()
    assert orphans == [], f"unit ids missing from ast_nodes: {orphans}"
