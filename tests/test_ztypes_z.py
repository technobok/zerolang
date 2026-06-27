"""Smoke + differential tests for the self-hosted type model (src/ztypes.z).

This is the head of the typechecker port slice. The ztypes.z binary is a
smoke harness: it dumps every enum arm, a representative of each carrier,
and a battery of the pure numeric-literal / name-mangling helpers.

Two checks:

- ``test_ztypes_smoke_matches_golden`` pins the whole binary output against
  the checked-in golden (byte-for-byte), like tests/test_zast_z.py.
- ``test_ztypes_selfhost_matches_golden`` runs the same check on the binary
  built by the ported zc (stage1) -- the self-host gate for the type-model
  unit. The full ``zc --dump-sql`` typing differential arrives once the type
  checker itself is ported.

The ``ztypes_binary`` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "ztypes_z")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "smoke.expected")


def _read_golden():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.emitter
def test_ztypes_smoke_matches_golden(ztypes_binary):
    """The smoke binary's stdout must match the checked-in golden."""
    proc = subprocess.run([ztypes_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"ztypes exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "ztypes smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.emitter
@pytest.mark.timeout(240)
def test_ztypes_selfhost_matches_golden(ztypes_selfhost_binary):
    """ztypes built by the PORTED zc (stage1) must produce the same golden
    smoke dump as the reference -- the self-host gate for the type-model unit."""
    proc = subprocess.run([ztypes_selfhost_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host ztypes exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "self-host ztypes smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
