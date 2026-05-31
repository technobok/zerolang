"""Smoke test for the self-hosted parser skeleton (src/zparser.z).

PR-E ships the atom layer: identifiers, dotted paths, and string
literals (interpolation and parenthesised expressions are stubbed
pending the expression parser). The binary takes no arguments; it
parses a handful of inline snippets and dumps each resulting node
tree via zast.printNode. This test asserts byte-for-byte equality
with the checked-in golden.

The differential against the Python parser over the example corpus
lands once parse()/unit-loading exist (a later slice).

The `zparser_binary` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest


pytestmark = pytest.mark.emitter


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zparser_z")


def test_zparser_smoke_matches_golden(zparser_binary):
    """The smoke binary's stdout must match the checked-in golden
    byte-for-byte."""
    proc = subprocess.run([zparser_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"zparser exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected_path = os.path.join(FIXTURE_DIR, "smoke.expected")
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()
    if proc.stdout != expected:
        pytest.fail(
            f"zparser smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
