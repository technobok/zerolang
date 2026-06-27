"""End-to-end tests for the self-hosted VFS (src/zvfs.z).

The binary is a script-driven dispatcher: it reads a script file (one
operation per line, '#' comments, whitespace-separated tokens) and emits
one line per verb to stdout. Both tests parametrise over every `.script`
fixture under tests/fixtures/zvfs_ops/ and assert byte-for-byte equality
with the matching `.expected` golden:

- test_zvfs_script_matches_golden runs the seed-built binary.
- test_zvfs_selfhost_matches_golden runs the binary built by the PORTED zc.

The fixtures live in tests/conftest.py.
"""

import os
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.emitter


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zvfs_ops")


def _list_script_names():
    names = []
    for name in sorted(os.listdir(FIXTURE_DIR)):
        if name.endswith(".script"):
            names.append(name)
    return names


def _run_zvfs_script(binary, script_name, tmp_path):
    """Run `binary` over a zvfs_ops `.script` fixture; return (proc, expected).

    FSProvider-style fixtures opt in by checking in a sibling `<base>.tree/`
    directory. When present, the tree is copied into `tmp_path/root/`, `{ROOT}`
    in the script text is substituted with that path, and the rewritten script
    is run. The `.expected` golden uses paths relative to the provider's
    parentpath (stable across runs), so the substituted root never leaks into
    stdout.
    """
    base = script_name[:-7]
    script_path = os.path.join(FIXTURE_DIR, script_name)
    expected_path = os.path.join(FIXTURE_DIR, base + ".expected")
    tree_dir = os.path.join(FIXTURE_DIR, base + ".tree")
    if os.path.isdir(tree_dir):
        root = tmp_path / "root"
        shutil.copytree(tree_dir, root)
        with open(script_path, "r", encoding="utf-8") as f:
            script_text = f.read()
        script_text = script_text.replace("{ROOT}", str(root))
        script_path = str(tmp_path / "ops.script")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_text)
    proc = subprocess.run([binary, script_path], capture_output=True, text=True)
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()
    return proc, expected


@pytest.mark.parametrize("script_name", _list_script_names())
def test_zvfs_script_matches_golden(script_name, zvfs_binary, tmp_path):
    """The seed-built dispatcher's stdout matches the golden byte-for-byte."""
    proc, expected = _run_zvfs_script(zvfs_binary, script_name, tmp_path)
    if proc.returncode != 0:
        pytest.fail(
            f"zvfs exited {proc.returncode} on {script_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    if proc.stdout != expected:
        pytest.fail(
            f"zvfs output diverged from golden for {script_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.parametrize("script_name", _list_script_names())
def test_zvfs_selfhost_matches_golden(script_name, zvfs_selfhost_binary, tmp_path):
    """zvfs compiled by the PORTED zc matches the reference goldens -- the
    headline self-host gate: the ported compiler emits buildable C and the
    emitted VFS dispatcher is behaviorally correct on every script."""
    proc, expected = _run_zvfs_script(zvfs_selfhost_binary, script_name, tmp_path)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host zvfs exited {proc.returncode} on {script_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    if proc.stdout != expected:
        pytest.fail(
            f"self-host zvfs output diverged from golden for {script_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
