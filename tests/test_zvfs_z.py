"""End-to-end smoke test for the self-hosted VFS (src/zvfs.z).

PR 1 lays down the foundation types -- DEntry union with five arms,
DEntryTable registry, no providers or engine yet. This test confirms
the union + table + case-dispatch all round-trip through a built
binary by running `out/zvfs` no-args and asserting it emits exactly
the expected header lines.

Subsequent PRs grow the binary's smoke output and eventually replace
this with the differential harness that diffs against the Python ref.

The `zvfs_binary` fixture lives in tests/conftest.py.
"""

import subprocess

import pytest


pytestmark = pytest.mark.emitter


EXPECTED_OUTPUT = """0: root
1: notfound
2: file
3: directory
4: mount
"""


def test_zvfs_smoke_matches_expected(zvfs_binary):
    """Running the PR-1 binary prints one line per DEntry arm in the
    order they were appended to the table -- pins the variant +
    table + case dispatch all the way through a compiled binary."""
    proc = subprocess.run([zvfs_binary], capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"zvfs exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.stdout == EXPECTED_OUTPUT
