"""
Differential test harness for the parser port.

Two parametrizations over examples/*.z:
* test_python_parser_matches_golden -- the Python reference dumper
  (src/zastdump.dump_ast) must produce the checked-in golden
  byte-for-byte. Guards the oracle against accidental drift.
* test_zparser_binary_matches_golden -- the self-hosted parser
  (src/zparser.z compiled to out/zparser, file-dump mode) must
  produce the same output. Closes the parser-parity loop. Marked
  `emitter` so it skips cleanly without a C compiler.

The dump is the per-file unit body (parser._accept_unitbody), id-stripped
and canonicalised; multi-unit loading is a later slice.

To regenerate a golden (after verifying the change is intentional):

    python tools/astdump.py examples/<name>.z \
        > tests/fixtures/parser_golden/<name>.ast

# SKIP set -- examples that exercise a deliberately-deferred parser feature,
# so the two parsers legitimately disagree (no golden committed):
#   strings.z -- multi-line string `_strip_string_whitespace` (blank-line +
#     common-prefix dedent) is implemented in the Python parser but is still
#     the identity stub in zparser.z (deferred PR-G/G2b). Closing that gap
#     makes the binary match and lets strings.z rejoin the differential.
"""

import os
import subprocess

import pytest

from zastdump import dump_ast


pytestmark = pytest.mark.parser

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "parser_golden")

# Examples skipped because a deferred parser feature makes the two parsers
# legitimately disagree (see module docstring).
SKIP = {
    "strings.z": "multi-line string stripStringWhitespace deferred in zparser.z",
}


def _list_example_names():
    names = []
    for name in sorted(os.listdir(EXAMPLES_DIR)):
        if name.endswith(".z"):
            names.append(name)
    return names


@pytest.mark.parametrize("example_name", _list_example_names())
def test_python_parser_matches_golden(example_name):
    """Reference dumper output must match the checked-in golden byte-for-byte."""
    if example_name in SKIP:
        pytest.skip(SKIP[example_name])
    example_path = os.path.join(EXAMPLES_DIR, example_name)
    with open(example_path, "r", encoding="utf-8") as f:
        source = f.read()
    actual = dump_ast(source)

    golden_path = os.path.join(GOLDEN_DIR, example_name[:-2] + ".ast")
    if not os.path.exists(golden_path):
        pytest.fail(
            f"Missing golden file: {golden_path}\n"
            f"Regenerate: python tools/astdump.py {example_path} > {golden_path}"
        )
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if actual != expected:
        pytest.fail(
            f"AST dump diverged from golden for {example_name}.\n"
            f"If intentional, regenerate: "
            f"python tools/astdump.py {example_path} > {golden_path}"
        )


@pytest.mark.emitter
@pytest.mark.parametrize("example_name", _list_example_names())
def test_zparser_binary_matches_golden(example_name, zparser_binary):
    """Self-hosted parser output (out/zparser <file>) must match the golden too.

    Closes the parser-parity loop: byte-clean equality with the Python
    reference dumper on every example. The `zparser_binary` fixture
    (tests/conftest.py) builds src/zparser.z -> C -> binary once per
    session and skips when no C compiler is on PATH.
    """
    if example_name in SKIP:
        pytest.skip(SKIP[example_name])
    example_path = os.path.join(EXAMPLES_DIR, example_name)
    proc = subprocess.run(
        [zparser_binary, example_path], capture_output=True, text=True
    )
    if proc.returncode != 0:
        pytest.fail(
            f"out/zparser exited {proc.returncode} on {example_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    actual = proc.stdout

    golden_path = os.path.join(GOLDEN_DIR, example_name[:-2] + ".ast")
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if actual != expected:
        pytest.fail(
            f"out/zparser output diverged from golden for {example_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{actual}"
        )
