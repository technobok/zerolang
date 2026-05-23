"""
Differential test harness for the lexer port (Phase B step 1).

Asserts the Python reference printer (src/ztokendump.dump_tokens) produces
output byte-identical to a checked-in golden file for every examples/*.z.

When the zerolang-side lexer (src/zlexer.z) ships, a second parametrization
will diff its output against the same goldens. Until then this guards the
Python printer against accidental drift, and the goldens act as the
contract the ported lexer must reproduce.

To regenerate a golden (after verifying the change is intentional):

    python tools/lexdump.py examples/<name>.z \
        > tests/fixtures/lexer_golden/<name>.tokens
"""

import os

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
