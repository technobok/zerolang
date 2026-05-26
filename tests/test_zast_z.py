"""Smoke test for the self-hosted AST skeleton (src/zast.z).

PR 1 ships foundation types only (ERR / NodeType / CallKind enum
variants + a Node union with four leaf-only arms). The binary
takes no arguments; it dumps one line per arm to stdout. This
test asserts byte-for-byte equality with the checked-in golden.

PR 2+ will replace the no-arg smoke with a script-driven
dispatcher (mirroring tests/test_zvfs_z.py) once recursive Node
arms (Unit, Function, If, ...) exist.

The `zast_binary` fixture lives in tests/conftest.py.
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
