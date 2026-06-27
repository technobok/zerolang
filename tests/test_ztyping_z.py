"""Smoke + differential tests for the self-hosted ZTyping container
(src/ztyping.z).

Mirrors tests/test_ztypes_z.py. The ztyping.z binary is a smoke harness that
exercises the children / sidecar / generic-arg tables, a representative set of
per-node stamps, the flattened node tables, and the aggregate state, dumping
deterministically.

Two checks:

- ``test_ztyping_smoke_matches_golden`` pins the whole binary output against the
  checked-in golden (byte-for-byte).
- ``test_ztyping_selfhost_matches_golden`` runs the same check on the binary
  built by the ported zc (stage1) -- the self-host gate for the
  typecheck-output unit. The full ``zc --dump-sql`` typing differential arrives
  once the type checker itself is ported.

The ``ztyping_binary`` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "ztyping_z")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "smoke.expected")


def _read_golden():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.emitter
def test_ztyping_smoke_matches_golden(ztyping_binary):
    """The smoke binary's stdout must match the checked-in golden."""
    proc = subprocess.run([ztyping_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"ztyping exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "ztyping smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.emitter
@pytest.mark.timeout(240)
def test_ztyping_selfhost_matches_golden(ztyping_selfhost_binary):
    """ztyping built by the PORTED zc (stage1) must produce the same golden
    smoke dump as the reference -- the self-host gate for the typecheck-output
    unit (cross-unit-optionval keystone)."""
    proc = subprocess.run([ztyping_selfhost_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host ztyping exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "self-host ztyping smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
