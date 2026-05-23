"""
End-to-end smoke test for the zerolang-side lexer skeleton.

Reads src/zlexer.z, runs it through the full parse + typecheck + emit
pipeline, compiles the resulting C, runs the binary, and asserts on a
fixed 1-line golden output.

This is PR 2 of the Phase B port: the only thing under test here is that
the toolchain end-to-end produces a working binary from a .z file in
src/. PRs 3-7 progressively grow the scanner; PR 8 wires the binary
into the per-example differential harness against the Python reference
(src/ztokendump.py).
"""

import os

import pytest

from conftest import make_parser
from ztypecheck import typecheck
import zast
import zemitterc

from test_emitter import compile_and_run


pytestmark = [pytest.mark.emitter, pytest.mark.runtime]

REPO_SRC = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src"))
LIB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))


def _emit_zlexer_c() -> str:
    """Read src/zlexer.z and emit C source for it."""
    with open(os.path.join(REPO_SRC, "zlexer.z"), "r", encoding="utf-8") as f:
        source = f.read()
    p = make_parser(source, unitname="zlexer", src_dir=LIB_DIR)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    typing = typecheck(program)
    assert typing.errors == [], (
        f"Type errors in src/zlexer.z: {[e.msg for e in typing.errors]}"
    )
    return zemitterc.emit(typing)


def test_zlexer_stub_emits_eof_line():
    """src/zlexer.z compiles end-to-end and prints the canonical EOF line.

    Matches what `ztokendump.dump_tokens("")` produces for an empty
    source: `1:0 EOF ""` followed by the trailing newline from `print`.
    """
    csource = _emit_zlexer_c()
    output = compile_and_run(csource)
    assert output == '1:0 EOF ""\n', (
        f"Unexpected stub output: {output!r}\n"
        "If this is an intentional format change, update the assertion "
        "and document the divergence in the canonical-format design notes."
    )
