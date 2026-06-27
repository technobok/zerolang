"""
Differential test harness for the lexer port (Phase B step 1).

Two parametrizations over examples/*.z:
* test_zlexer_binary_matches_golden -- the self-hosted lexer
  (src/zlexer.z compiled to out/zlexer) must produce the checked-in
  golden byte-for-byte. Closes the lexer-parity loop. Marked `emitter`
  so it skips cleanly without a C compiler.
* test_zlexer_selfhost_matches_golden -- the lexer built by the ported
  zc (stage1) must match the same goldens -- the self-host gate for the
  lexer.

To regenerate the goldens (after verifying the change is intentional),
use the self-hosted dump binary (no Python):

    make regen-goldens            # all lexer + parser goldens
    # or for a single file:
    out/zlexer examples/<name>.z > tests/fixtures/lexer_golden/<name>.tokens
"""

import os
import subprocess

import pytest


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


@pytest.mark.emitter
@pytest.mark.parametrize("example_name", _list_example_names())
def test_zlexer_binary_matches_golden(example_name, zlexer_binary):
    """Self-hosted lexer output (out/zlexer) must match the committed golden.

    Closes the lexer-parity loop: byte-clean equality with the committed
    token golden on every example. The `zlexer_binary` fixture
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
    """zlexer compiled by the PORTED zc must match the committed goldens.

    The self-host gate for the lexer: stage1 (the seed-built ported
    compiler) emits zlexer's C, which builds and tokenizes every example
    byte-identically to the committed token goldens. Distinct from
    test_zlexer_binary_matches_golden, which builds zlexer with the seed -- this
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
