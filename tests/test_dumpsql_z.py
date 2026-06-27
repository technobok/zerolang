"""Structural smoke test for the self-hosted SQL dump (src/zsqldump.z + the
ztypecheck.z skeleton + the zc.z driver).

``test_dumpsql_structural_smoke`` runs ``zc --dump-sql`` on ``hello`` and
asserts standalone invariants on the ported pipeline's output -- the expected
``files`` and ``unit`` row counts, a single main unit, a non-empty
``ast_nodes`` table, and that every unit id resolves to a real ``ast_nodes``
row. No Python oracle is involved.

The ``zc_binary`` fixture (tests/conftest.py) builds src/zc.z once per session
and skips cleanly without a C compiler.
"""

import os
import resource
import sqlite3
import subprocess

import pytest

# Building zc.z compiles the entire ported pipeline as one unit -- the
# reference compiler takes ~30s on it, over the default per-test timeout.
pytestmark = [pytest.mark.infra, pytest.mark.timeout(240)]

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
SYSTEM_DIR = os.path.join(REPO_ROOT, "lib", "system")

# Runaway guards for the zc child: a non-terminating allocation loop must die
# at the cap instead of swapping the machine to death. RLIMIT_AS is safe here
# (plain-C binary, no ASan); normal full dumps stay well under the limit.
ZC_TIMEOUT_S = 60
ZC_MEM_LIMIT = 1536 * 1024 * 1024


def _cap_zc():
    resource.setrlimit(resource.RLIMIT_AS, (ZC_MEM_LIMIT, ZC_MEM_LIMIT))


def _zc_sql(zc_binary: str, unit: str, full: bool = False) -> str:
    args = [zc_binary, unit, "--src", EXAMPLES_DIR, "--system", SYSTEM_DIR]
    if full:
        args.append("--full")
    args += ["--dump-sql", "-"]
    flag = " --full" if full else ""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=ZC_TIMEOUT_S,
            preexec_fn=_cap_zc,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"zc{flag} timed out (>{ZC_TIMEOUT_S}s) on {unit} -- runaway zc")
    if proc.returncode != 0:
        pytest.fail(
            f"zc{flag} exited {proc.returncode} on {unit}.\nstderr:\n{proc.stderr}"
        )
    return proc.stdout


def _load(sql: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(sql)
    return con


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
