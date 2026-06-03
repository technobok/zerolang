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

The per-file dump is the unit body (parser._accept_unitbody), id-stripped
and canonicalised. Two further parametrizations over fixtures/parser_program/
exercise whole-program loading (parser.parse): extern resolution of sibling
units and same-named-subdirectory subunits. The example corpus has no
filesystem subunits, so those fixtures are synthetic.

To regenerate a golden (after verifying the change is intentional):

    python tools/astdump.py examples/<name>.z \
        > tests/fixtures/parser_golden/<name>.ast
    python tools/astdump.py --program <abs-tree-dir> main \
        > tests/fixtures/parser_program/<name>.expected

# SKIP set -- examples that exercise a deliberately-deferred parser feature,
# so the two parsers legitimately disagree (no golden committed). Currently
# empty: every example parses identically in the Python and ported parsers,
# including strings.z (multi-line string blank-line + common-prefix dedent,
# ported to zparser.z's stripStringWhitespace).
"""

import os
import shutil
import subprocess

import pytest

from zastdump import dump_ast, dump_program


pytestmark = pytest.mark.parser

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "parser_golden")
PROGRAM_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "parser_program")

# Examples skipped because a deferred parser feature makes the two parsers
# legitimately disagree (see module docstring). Currently none.
SKIP = {}


def _list_example_names():
    names = []
    for name in sorted(os.listdir(EXAMPLES_DIR)):
        if name.endswith(".z"):
            names.append(name)
    return names


def _list_program_fixtures():
    names = []
    for name in sorted(os.listdir(PROGRAM_DIR)):
        if name.endswith(".tree"):
            names.append(name[:-5])
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


@pytest.mark.parametrize("fixture", _list_program_fixtures())
def test_python_program_matches_golden(fixture, tmp_path):
    """Reference whole-program dump must match the checked-in golden.

    The tree is copied into tmp_path so the FSProvider sees a stable root;
    the canonical dump carries no absolute paths, so the golden is
    path-independent.
    """
    root = tmp_path / "root"
    shutil.copytree(os.path.join(PROGRAM_DIR, fixture + ".tree"), root)
    actual = dump_program(str(root), "main")

    expected_path = os.path.join(PROGRAM_DIR, fixture + ".expected")
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if actual != expected:
        pytest.fail(
            f"program dump diverged from golden for {fixture}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{actual}"
        )


@pytest.mark.emitter
@pytest.mark.parametrize("fixture", _list_program_fixtures())
def test_zparser_program_matches_golden(fixture, zparser_binary, tmp_path):
    """Self-hosted whole-program load (out/zparser --program <dir> main) must
    match the golden too -- exercises parser.parse, extern resolution, and
    subunit recursion that the per-file dump cannot reach."""
    root = tmp_path / "root"
    shutil.copytree(os.path.join(PROGRAM_DIR, fixture + ".tree"), root)
    proc = subprocess.run(
        [zparser_binary, "--program", str(root), "main"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"out/zparser --program exited {proc.returncode} on {fixture}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    expected_path = os.path.join(PROGRAM_DIR, fixture + ".expected")
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()

    if proc.stdout != expected:
        pytest.fail(
            f"out/zparser --program output diverged from golden for {fixture}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )
