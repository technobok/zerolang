"""End-to-end tests for the self-hosted VFS (src/zvfs.z).

PR 2 turned the binary into a script-driven dispatcher: it reads a
script file (one operation per line, '#' comments, whitespace-
separated tokens) and emits one line per verb to stdout. The test
parametrises over every `.script` fixture under
tests/fixtures/zvfs_ops/, asserting byte-for-byte equality with the
matching `.expected` golden.

PR 3+ will add new verbs (`provider`, `walk`, `bind`, `open`,
`getline`) and new fixtures. PR 7 will wire the same fixtures
against the Python ref running through an equivalent dispatcher.

The `zvfs_binary` fixture lives in tests/conftest.py.
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


@pytest.mark.parametrize("script_name", _list_script_names())
def test_zvfs_script_matches_golden(script_name, zvfs_binary, tmp_path):
    """The dispatcher's stdout must match the checked-in golden for
    each fixture script byte-for-byte.

    FSProvider-style fixtures opt in by checking in a sibling
    `<base>.tree/` directory. When present, the runner copies the
    tree into `tmp_path/root/`, substitutes `{ROOT}` in the script
    text with that path, and writes the substituted script back to
    tmp_path before invoking the binary. The .expected golden uses
    paths relative to the provider's parentpath (stable across runs)
    so the {ROOT} substitution does not leak into stdout.
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
    proc = subprocess.run([zvfs_binary, script_path], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"zvfs exited {proc.returncode} on {script_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()
    if proc.stdout != expected:
        pytest.fail(
            f"zvfs output diverged from golden for {script_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
