"""Smoke test for the self-hosted AST skeleton (src/zast.z).

PR 1 ships foundation types only (ERR / NodeType / CallKind enum
variants + a Node union with four leaf-only arms). The binary
takes no arguments; it dumps one line per arm to stdout. This
test asserts byte-for-byte equality with the checked-in golden.

The full Node union (29 arms, recursive) is now dumped; the binary
still takes no arguments.

The `zast_binary` (reference) and `zast_selfhost_binary` (built by the
ported zc, stage1) fixtures live in tests/conftest.py; both must match
the same golden.
"""

import os
import subprocess

import pytest


pytestmark = pytest.mark.emitter


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zast_z")


def test_zast_smoke_matches_golden(zast_binary):
    """The smoke binary's stdout must match the checked-in golden
    byte-for-byte."""
    proc = subprocess.run([zast_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"zast exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected_path = os.path.join(FIXTURE_DIR, "smoke.expected")
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()
    if proc.stdout != expected:
        pytest.fail(
            f"zast smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.timeout(240)
def test_zast_selfhost_matches_golden(zast_selfhost_binary):
    """zast built by the PORTED zc (stage1) must produce the same golden
    smoke dump as the reference -- the self-host gate for the AST unit."""
    proc = subprocess.run([zast_selfhost_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host zast exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected_path = os.path.join(FIXTURE_DIR, "smoke.expected")
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()
    if proc.stdout != expected:
        pytest.fail(
            f"self-host zast smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
