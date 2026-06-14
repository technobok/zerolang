"""
Differential test harness for the lexer port (Phase B step 1).

Two parametrizations over examples/*.z:
* test_python_lexer_matches_golden  -- the Python reference printer
  (src/ztokendump.dump_tokens) must produce the checked-in golden
  byte-for-byte. Guards the reference against accidental drift.
* test_zlexer_binary_matches_golden -- the self-hosted lexer
  (src/zlexer.z compiled to out/zlexer) must produce the same
  output. Closes the lexer-parity loop. Marked `emitter` so it
  skips cleanly without a C compiler.

To regenerate a golden (after verifying the change is intentional):

    python tools/lexdump.py examples/<name>.z \
        > tests/fixtures/lexer_golden/<name>.tokens
"""

import os
import subprocess

import pytest

from ztokendump import dump_tokens


pytestmark = pytest.mark.lexer

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "lexer_golden")


def _list_example_names():
    names = []
    for name in sorted(os.listdir(EXAMPLES_DIR)):
        if name.endswith(".z"):
            names.append(name)
    return names


@pytest.mark.parametrize("example_name", _list_example_names())
def test_python_lexer_matches_golden(example_name):
    """Reference printer output must match the checked-in golden byte-for-byte."""
    example_path = os.path.join(EXAMPLES_DIR, example_name)
    with open(example_path, "r", encoding="utf-8") as f:
        source = f.read()
    actual = dump_tokens(source)

    golden_path = os.path.join(GOLDEN_DIR, example_name[:-2] + ".tokens")
    if not os.path.exists(golden_path):
        pytest.fail(
            f"Missing golden file: {golden_path}\n"
            f"Regenerate: python tools/lexdump.py {example_path} > {golden_path}"
        )
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if actual != expected:
        pytest.fail(
            f"Token dump diverged from golden for {example_name}.\n"
            f"If intentional, regenerate: "
            f"python tools/lexdump.py {example_path} > {golden_path}"
        )


@pytest.mark.emitter
@pytest.mark.parametrize("example_name", _list_example_names())
def test_zlexer_binary_matches_golden(example_name, zlexer_binary):
    """Self-hosted lexer output (out/zlexer) must match the golden too.

    Closes the lexer-parity loop: byte-clean equality with the Python
    reference printer on every example. The `zlexer_binary` fixture
    (tests/conftest.py) builds src/zlexer.z -> C -> binary once per
    session and skips when no C compiler is on PATH.
    """
    example_path = os.path.join(EXAMPLES_DIR, example_name)
    proc = subprocess.run([zlexer_binary, example_path], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"out/zlexer exited {proc.returncode} on {example_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    actual = proc.stdout

    golden_path = os.path.join(GOLDEN_DIR, example_name[:-2] + ".tokens")
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if actual != expected:
        pytest.fail(
            f"out/zlexer output diverged from golden for {example_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{actual}"
        )


@pytest.mark.infra
@pytest.mark.emitter
@pytest.mark.timeout(240)
@pytest.mark.parametrize("example_name", _list_example_names())
def test_zlexer_selfhost_matches_golden(example_name, zlexer_selfhost_binary):
    """zlexer compiled by the PORTED zc must match the reference goldens.

    The self-host gate for the lexer: stage1 (the reference-built ported
    compiler) emits zlexer's C, which builds and tokenizes every example
    byte-identically to the Python reference. Distinct from
    test_zlexer_binary_matches_golden, which builds zlexer with zc.py -- this
    proves the *ported* emitter produces a correct lexer end to end.
    """
    example_path = os.path.join(EXAMPLES_DIR, example_name)
    proc = subprocess.run(
        [zlexer_selfhost_binary, example_path], capture_output=True, text=True
    )
    if proc.returncode != 0:
        pytest.fail(
            f"self-host zlexer exited {proc.returncode} on {example_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    actual = proc.stdout

    golden_path = os.path.join(GOLDEN_DIR, example_name[:-2] + ".tokens")
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if actual != expected:
        pytest.fail(
            f"self-host zlexer output diverged from golden for {example_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{actual}"
        )
