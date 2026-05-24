"""End-to-end tests for the in-progress self-hosting lexer (src/zlexer.z).

PRs 3-7 progressively grow the scanner; PR 8 will wire the binary into
the existing differential harness against the Python reference
(tests/test_lexer_differential.py against 96 example goldens). Until
then, this file gates each PR with a small fixture under
tests/fixtures/zlexer_z/ covering the tokens that PR introduced.
"""

import os
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
OUT_DIR = os.path.join(REPO_ROOT, "out")
ZC = [sys.executable, os.path.join(SRC_DIR, "zc.py")]
CC = os.environ.get("Z_TEST_CC", "gcc")
CFLAGS = [
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Wno-unused-function",
    "-Wno-unused-parameter",
    "-Werror=implicit-function-declaration",
    "-Werror=implicit-int",
    "-Werror=int-conversion",
    "-Werror=incompatible-pointer-types",
]
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zlexer_z")


pytestmark = pytest.mark.emitter


@pytest.fixture(scope="module")
def zlexer_binary():
    """Build out/zlexer once per test session and return its path. Skip
    the whole module if gcc is unavailable."""
    if shutil.which(CC) is None:
        pytest.skip(f"{CC} not on PATH; cannot build zlexer binary")
    os.makedirs(OUT_DIR, exist_ok=True)
    c_path = os.path.join(OUT_DIR, "zlexer.c")
    bin_path = os.path.join(OUT_DIR, "zlexer")
    zc_proc = subprocess.run(
        ZC + ["zlexer", "--src", SRC_DIR, "-o", c_path],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert zc_proc.returncode == 0, (
        f"zc.py failed:\nstdout:\n{zc_proc.stdout}\nstderr:\n{zc_proc.stderr}"
    )
    cc_proc = subprocess.run(
        [CC, *CFLAGS, "-o", bin_path, c_path],
        capture_output=True,
        text=True,
    )
    assert cc_proc.returncode == 0, (
        f"{CC} failed:\nstdout:\n{cc_proc.stdout}\nstderr:\n{cc_proc.stderr}"
    )
    return bin_path


def _run_zlexer(binary: str, source_path: str) -> str:
    proc = subprocess.run([binary, source_path], capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"zlexer exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _assert_matches_golden(zlexer_binary: str, name: str) -> None:
    fixture = os.path.join(FIXTURE_DIR, f"{name}.z")
    golden = os.path.join(FIXTURE_DIR, f"{name}.tokens")
    actual = _run_zlexer(zlexer_binary, fixture)
    with open(golden, "r", encoding="utf-8") as f:
        expected = f.read()
    assert actual == expected, (
        f"zlexer output diverges from {name} golden\n--- expected ---\n{expected}"
        f"--- actual ---\n{actual}"
    )


def test_structural_fixture_matches_golden(zlexer_binary):
    """PR 3: bedrock scanner output must match the Python reference for
    a fixture exercising whitespace, comments, EOL, dot variants, and
    structural single-char delimiters."""
    _assert_matches_golden(zlexer_binary, "structural")


def test_words_fixture_matches_golden(zlexer_binary):
    """PR 4: identifiers (REFID), labels (LABEL, LABELPRE), all 28
    keywords, reserved words rejected as ERR, operators-as-REFIDs,
    and numeric-shaped REFIDs with dot continuation (e.g. `1.5` as
    one REFID rather than three tokens)."""
    _assert_matches_golden(zlexer_binary, "words")
