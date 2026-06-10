"""Probe one example: the python typecheck(full=False) oracle vs `zc --full`,
per-projection diffs (the typechecker-port inner loop).

Usage: uv run python tools/probe_dumpsql.py <unit> [--src DIR] [--zc /tmp/zc]
       [--extra unit1,unit2] [--pyonly]
Run from the repo root with a freshly built /tmp/zc.
"""

import argparse
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, "src")

from zvfs import ZVfs, FSProvider, BindType
from zparser import Parser
from ztypecheck import typecheck
from zsqldump import dump_sql

REPO_ROOT = os.getcwd()
SYSTEM_DIR = os.path.join(REPO_ROOT, "lib", "system")


def _typed_projections(nunits):
    ph = ", ".join("?" * nunits)
    return {
        "types": (
            "SELECT name, typetype, is_valtype, is_generic, needs_destructor, "
            f"is_heap_allocated FROM types WHERE defined_in_unit IN ({ph}) "
            "ORDER BY name, typetype"
        ),
        "type_children": (
            "SELECT pt.name, tc.child_name, ct.name, tc.position, tc.param_ownership "
            "FROM type_children tc "
            "JOIN types pt ON tc.type_id = pt.type_id "
            "JOIN types ct ON tc.child_type_id = ct.type_id "
            f"WHERE pt.defined_in_unit IN ({ph}) ORDER BY pt.name, tc.position"
        ),
    }


_CHECK_PROJECTIONS = {
    "scope": (
        "SELECT p.name, s.kind, s.name, s.depth, s.unreachable FROM scope s "
        "LEFT JOIN scope p ON s.parent_id = p.scope_id "
        "ORDER BY s.depth, s.name"
    ),
    "entry": (
        "SELECT sc.name, e.position, e.name, t.name, e.is_definition, "
        "(e.variable_id IS NOT NULL), e.is_taken FROM entry e "
        "JOIN scope sc ON e.scope_id = sc.scope_id "
        "JOIN types t ON e.ztype_id = t.type_id "
        "ORDER BY sc.name, e.position, e.name"
    ),
    "variable": (
        "SELECT t.name, v.ownership, v.is_private_access, v.borrow_origin, "
        "v.synth_origin FROM variable v JOIN types t ON v.ztype_id = t.type_id "
        "ORDER BY t.name, v.ownership"
    ),
    "narrowed_subtype": (
        "SELECT name, excluded FROM narrowed_subtype ORDER BY name, excluded"
    ),
    "mono": (
        "SELECT t.name, t.typetype, o.name, t.is_generic FROM types t "
        "JOIN types o ON t.generic_origin_id = o.type_id ORDER BY t.name"
    ),
    "mono_children": (
        "SELECT pt.name, tc.child_name, ct.name, tc.position "
        "FROM type_children tc "
        "JOIN types pt ON tc.type_id = pt.type_id "
        "JOIN types ct ON tc.child_type_id = ct.type_id "
        "WHERE pt.generic_origin_id IS NOT NULL "
        "ORDER BY pt.name, tc.position"
    ),
    "mono_typed_nodes": (
        "SELECT an.kind, an.name, an.start_line, an.start_col, t.name "
        "FROM typed_nodes tn "
        "JOIN ast_nodes an ON tn.node_id = an.node_id "
        "JOIN types t ON tn.type_id = t.type_id "
        "WHERE t.generic_origin_id IS NOT NULL "
        "ORDER BY an.name, an.start_line, an.start_col, t.name"
    ),
}

_TYPED_NODES_QUERY = (
    "SELECT an.kind, an.name, an.start_line, an.start_col, t.name "
    "FROM typed_nodes tn "
    "JOIN ast_nodes an ON tn.node_id = an.node_id "
    "JOIN types t ON tn.type_id = t.type_id "
    "WHERE t.defined_in_unit IN (?, 'system', 'collections') "
    "ORDER BY an.kind, an.name, an.start_line, an.start_col"
)

_CONFORMANCE_QUERY = (
    "SELECT it.name, st.name, cf.label, cf.is_facet "
    "FROM conformance cf "
    "JOIN types it ON cf.impl_type_id = it.type_id "
    "JOIN types st ON cf.spec_type_id = st.type_id "
    "ORDER BY it.name, st.name, cf.label"
)


def python_sql(unit, srcdir):
    vfs = ZVfs()
    sysid = vfs.register(FSProvider(rootpath=SYSTEM_DIR, parentpath=""))
    srcid = vfs.register(FSProvider(rootpath=srcdir, parentpath=""))
    root = vfs.walk()
    root = vfs.bind(parentid=root, name=None, newid=sysid)
    root = vfs.bind(parentid=root, name=None, newid=srcid, bindtype=BindType.BEFORE)
    program = Parser(vfs, unit).parse()
    assert not program.is_error, f"python parse failed for {unit}"
    return dump_sql(typecheck(program, full=False))


def zc_sql(zc, unit, srcdir):
    args = [zc, unit, "--src", srcdir, "--system", SYSTEM_DIR, "--full", "--dump-sql", "-"]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"zc exited {proc.returncode}\nstderr:\n{proc.stderr}")
        sys.exit(1)
    return proc.stdout


def load(sql):
    con = sqlite3.connect(":memory:")
    con.executescript(sql)
    return con


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("unit")
    ap.add_argument("--src", default=os.path.join(REPO_ROOT, "examples"))
    ap.add_argument("--zc", default="/tmp/zc")
    ap.add_argument("--extra", default="")
    ap.add_argument("--pyonly", action="store_true", help="dump python rows only")
    args = ap.parse_args()

    py = load(python_sql(args.unit, args.src))
    units = [args.unit, "system"] + ([u for u in args.extra.split(",") if u])

    queries = dict(_CHECK_PROJECTIONS)
    zp = None if args.pyonly else load(zc_sql(args.zc, args.unit, args.src))

    def show(table, query, params=()):
        pr = py.execute(query, params).fetchall()
        if args.pyonly:
            print(f"== {table} ({len(pr)} rows)")
            for r in pr:
                print("  ", r)
            return
        zr = zp.execute(query, params).fetchall()
        if pr == zr:
            print(f"== {table}: MATCH ({len(pr)} rows)")
        else:
            key = lambda r: tuple(str(c) for c in r)
            only_py = sorted(set(pr) - set(zr), key=key)
            only_z = sorted(set(zr) - set(pr), key=key)
            print(f"== {table}: DIVERGED (python={len(pr)}, z={len(zr)})")
            for r in only_py[:25]:
                print("   only-py:", r)
            for r in only_z[:25]:
                print("   only-z: ", r)

    for table, query in queries.items():
        show(table, query)
    for table, query in _typed_projections(len(units)).items():
        show(table, query, units)
    show("conformance", _CONFORMANCE_QUERY)
    show("typed_nodes", _TYPED_NODES_QUERY, (args.unit,))


if __name__ == "__main__":
    main()
