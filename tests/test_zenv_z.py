"""Smoke + differential tests for the self-hosted symbol table (src/zenv.z).

Mirrors tests/test_ztyping_z.py. The zenv.z binary is a smoke harness that
drives the symbol table through every cluster -- scopes, name/var resolution,
take/invalidation, locks, narrowing/exclusion, live-owned-var detection,
all-names, and the scope log -- dumping deterministically.

Two checks:

- ``test_zenv_smoke_matches_golden`` pins the whole binary output against the
  checked-in golden (byte-for-byte).
- ``test_zenv_selfhost_matches_golden`` runs the same check on the binary built
  by the ported zc (stage1) -- the self-host gate for the symbol-table unit.
  The full ``zc --dump-sql`` differential arrives once the type checker itself
  is ported.

The ``zenv_binary`` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zenv_z")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "smoke.expected")


def _read_golden():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.emitter
def test_zenv_smoke_matches_golden(zenv_binary):
    """The smoke binary's stdout must match the checked-in golden."""
    proc = subprocess.run([zenv_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"zenv exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "zenv smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.emitter
@pytest.mark.timeout(240)
def test_zenv_selfhost_matches_golden(zenv_selfhost_binary):
    """zenv built by the PORTED zc (stage1) must produce the same golden smoke
    dump as the reference -- the self-host gate for the symbol-table unit."""
    proc = subprocess.run([zenv_selfhost_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host zenv exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "self-host zenv smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
