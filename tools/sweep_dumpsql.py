"""Sweep every example: `zc --full` vs typecheck(full=False), one summary line
each (CLEAN or the diverging projections). The typechecker-port admission scout.

Usage: uv run python tools/sweep_dumpsql.py   (repo root, /tmp/zc built)
"""

import glob
import os
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tools")
from probe_dumpsql import (
    python_sql,
    zc_sql,
    load,
    _CHECK_PROJECTIONS,
    _typed_projections,
    _CONFORMANCE_QUERY,
    _TYPED_NODES_QUERY,
)

EXTRA = {
    "strview": ("collections",),
    "str": ("collections",),
    "with_alias": ("collections",),
}

names = sorted(
    os.path.splitext(os.path.basename(p))[0] for p in glob.glob("examples/*.z")
)
for unit in names:
    try:
        py = load(python_sql(unit, "examples"))
    except Exception as e:
        print(f"{unit:24s} PY-ERROR {type(e).__name__}")
        continue
    try:
        zp = load(zc_sql("/tmp/zc", unit, "examples"))
    except SystemExit as e:
        print(f"{unit:24s} {'ZC-TIMEOUT' if e.code == 2 else 'ZC-ERROR'}")
        continue
    units = (unit, "system", *EXTRA.get(unit, ()))
    bad = []
    queries = dict(_CHECK_PROJECTIONS)
    for t, q in queries.items():
        if py.execute(q).fetchall() != zp.execute(q).fetchall():
            bad.append(t)
    for t, q in _typed_projections(len(units)).items():
        if py.execute(q, units).fetchall() != zp.execute(q, units).fetchall():
            bad.append(t)
    if (
        py.execute(_CONFORMANCE_QUERY).fetchall()
        != zp.execute(_CONFORMANCE_QUERY).fetchall()
    ):
        bad.append("conformance")
    if (
        py.execute(_TYPED_NODES_QUERY, (unit,)).fetchall()
        != zp.execute(_TYPED_NODES_QUERY, (unit,)).fetchall()
    ):
        bad.append("typed_nodes")
    print(f"{unit:24s} {'CLEAN' if not bad else 'DIVERGES: ' + ','.join(bad)}")
