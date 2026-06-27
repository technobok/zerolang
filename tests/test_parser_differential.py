"""
Differential test harness for the parser port.

Two parametrizations over examples/*.z:
* test_zparser_binary_matches_golden -- the self-hosted parser
  (src/zparser.z compiled to out/zparser, file-dump mode) must produce
  the checked-in golden byte-for-byte. Closes the parser-parity loop.
  Marked `emitter` so it skips cleanly without a C compiler.
* test_zparser_selfhost_matches_golden -- the parser built by the ported
  zc (stage1) must match the same goldens -- the self-host gate for the
  parser.

The per-file dump is the unit body (parser._accept_unitbody), id-stripped
and canonicalised. Two further parametrizations over fixtures/parser_program/
exercise whole-program loading (parser.parse): extern resolution of sibling
units and same-named-subdirectory subunits. The example corpus has no
filesystem subunits, so those fixtures are synthetic.

To regenerate the goldens (after verifying the change is intentional),
use the self-hosted dump binary (no Python):

    make regen-goldens            # all parser + program (+ lexer) goldens
    # or for a single file / program:
    out/zparser examples/<name>.z > tests/fixtures/parser_golden/<name>.ast
    out/zparser --program tests/fixtures/parser_program/<name>.tree main \
        > tests/fixtures/parser_program/<name>.expected

# SKIP set -- examples that exercise a deliberately-deferred parser feature,
# so the ported parser legitimately diverges from the committed golden (no
# golden committed). Currently empty: every example dumps identically to its
# golden, including strings.z (multi-line string blank-line + common-prefix
# dedent, ported to zparser.z's stripStringWhitespace).
"""

import os
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.parser

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "parser_golden")
PROGRAM_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "parser_program")

# Examples skipped because a deferred parser feature makes the ported parser
# diverge from its committed golden (see module docstring). Currently none.
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


@pytest.mark.emitter
@pytest.mark.parametrize("example_name", _list_example_names())
def test_zparser_binary_matches_golden(example_name, zparser_binary):
    """Self-hosted parser output (out/zparser <file>) must match the committed golden.

    Closes the parser-parity loop: byte-clean equality with the committed
    AST golden on every example. The `zparser_binary` fixture
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


@pytest.mark.infra
@pytest.mark.emitter
@pytest.mark.timeout(600)
@pytest.mark.parametrize("example_name", _list_example_names())
def test_zparser_selfhost_matches_golden(example_name, zparser_selfhost_binary):
    """Self-host lock-in: the parser built by the PORTED zc (the self-host loop)
    must match the committed golden on every example -- the ported compiler emits
    a behaviorally correct copy of its own parser."""
    if example_name in SKIP:
        pytest.skip(SKIP[example_name])
    example_path = os.path.join(EXAMPLES_DIR, example_name)
    proc = subprocess.run(
        [zparser_selfhost_binary, example_path], capture_output=True, text=True
    )
    if proc.returncode != 0:
        pytest.fail(
            f"selfhost zparser exited {proc.returncode} on {example_name}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    golden_path = os.path.join(GOLDEN_DIR, example_name[:-2] + ".ast")
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()
    if proc.stdout != expected:
        pytest.fail(
            f"selfhost zparser diverged from golden for {example_name}.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.infra
@pytest.mark.emitter
@pytest.mark.timeout(600)
@pytest.mark.parametrize("fixture", _list_program_fixtures())
def test_zparser_program_selfhost_matches_golden(
    fixture, zparser_selfhost_binary, tmp_path
):
    """Self-host lock-in for whole-program load (--program): the ported-zc-built
    parser must match the committed golden on multi-unit load + extern
    resolution + subunit recursion."""
    root = tmp_path / "root"
    shutil.copytree(os.path.join(PROGRAM_DIR, fixture + ".tree"), root)
    proc = subprocess.run(
        [zparser_selfhost_binary, "--program", str(root), "main"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    expected_path = os.path.join(PROGRAM_DIR, fixture + ".expected")
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = f.read()
    assert proc.stdout == expected


@pytest.mark.emitter
@pytest.mark.parametrize("fixture", _list_program_fixtures())
def test_zparser_program_matches_golden(fixture, zparser_binary, tmp_path):
    """Self-hosted whole-program load (out/zparser --program <dir> main) must
    match the committed golden -- exercises parser.parse, extern resolution, and
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
