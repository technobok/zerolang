"""Differential harness for the VFS port (PR 7).

Runs each fixture op-script in `tests/fixtures/zvfs_ops/*.script`
against the pure-Python dispatcher in `tests/zvfs_script.py`
(which calls into the Python reference at `compiler0/zvfs.py`) and
asserts that the captured output matches the same `.expected`
golden the zerolang binary matches via `tests/test_zvfs_z.py`.

Two ports asserting against one shared golden = port equivalence
proof.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Tuple

import pytest

from zvfs_script import run_python_dispatcher


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zvfs_ops")


def _list_script_names() -> List[str]:
    return sorted(n for n in os.listdir(FIXTURE_DIR) if n.endswith(".script"))


def _prepare_script(script_name: str, tmp_path) -> Tuple[str, str]:
    """Apply the same `.tree/` + `{ROOT}` substitution test_zvfs_z does.

    Returns (script_text, expected_text).
    """
    base = script_name[:-7]
    script_path = os.path.join(FIXTURE_DIR, script_name)
    expected_path = os.path.join(FIXTURE_DIR, base + ".expected")
    tree_dir = os.path.join(FIXTURE_DIR, base + ".tree")
    with open(script_path, "r", encoding="utf-8") as f:
        script_text = f.read()
    if os.path.isdir(tree_dir):
        root = tmp_path / "root"
        shutil.copytree(tree_dir, root)
        script_text = script_text.replace("{ROOT}", str(root))
    with open(expected_path, "r", encoding="utf-8") as f:
        expected_text = f.read()
    return script_text, expected_text


@pytest.mark.parametrize("script_name", _list_script_names())
def test_python_dispatcher_matches_golden(script_name, tmp_path):
    """The Python ZVfs dispatcher's stdout must match the same
    byte-for-byte golden the zerolang binary matches. Proves
    end-to-end port equivalence.
    """
    script_text, expected = _prepare_script(script_name, tmp_path)
    actual = run_python_dispatcher(script_text)
    if actual != expected:
        pytest.fail(
            f"Python dispatcher output diverged from golden for {script_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{actual}"
        )
