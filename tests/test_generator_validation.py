"""
Generator validation tests (Phase G5).

End-to-end check that a hand-written iterator class can be replaced
by an equivalent generator function with byte-identical observable
output. The two source files live in `examples/`:

    listiter.z              — hand-written BagIter class
    generator_listiter.z    — same Bag, but iterator is a generator
                              method (Bag.iterate yields each field)

Both compile through the full pipeline (parse → desugar → typecheck
→ emit → gcc → run). The test asserts that their stdouts match.
"""

import os
import subprocess
import tempfile

import pytest

from conftest import make_parser
from ztypecheck import typecheck
import zemitterc
import zast


pytestmark = [pytest.mark.emitter, pytest.mark.runtime]

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

# Allow override for cross-compiler validation.
_CC = os.environ.get("Z_TEST_CC", "gcc")


def _compile_and_run_example(unit_name: str) -> str:
    """Parse, type-check, emit, gcc-compile, and run the named
    example program. Returns its stdout."""
    src_path = os.path.join(EXAMPLES_DIR, f"{unit_name}.z")
    with open(src_path) as f:
        source = f.read()
    parser = make_parser(source, unitname=unit_name, src_dir=LIB_DIR)
    program = parser.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    typing = typecheck(program)
    assert typing.errors == [], (
        f"Type errors in {unit_name}: {[e.msg for e in typing.errors]}"
    )
    csource = zemitterc.emit(typing)

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        comp = subprocess.run(
            [
                _CC,
                "-std=c17",
                "-Wall",
                "-Wextra",
                "-Wno-unused-function",
                "-Wno-unused-parameter",
                "-Werror=implicit-function-declaration",
                "-o",
                outpath,
                cpath,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if comp.returncode != 0:
            raise RuntimeError(f"gcc failed:\n{comp.stderr}")
        run = subprocess.run([outpath], capture_output=True, text=True, timeout=10)
        return run.stdout
    finally:
        for p in (cpath, outpath):
            if os.path.exists(p):
                os.unlink(p)


class TestGeneratorValidation:
    def test_generator_bagiter_equals_handwritten(self):
        """`examples/generator_listiter.z` (generator method) and
        `examples/listiter.z` (hand-written iterator class) produce
        byte-identical stdout. The generator-based version is
        what's expected to be the idiomatic Zerolang form going
        forward; this test pins the equivalence."""
        hand = _compile_and_run_example("listiter")
        gen = _compile_and_run_example("generator_listiter")
        assert hand == gen, (
            f"Generator and hand-written outputs differ:\n"
            f"--- listiter.z (hand) ---\n{hand}"
            f"--- generator_listiter.z (gen) ---\n{gen}"
        )
        # Pin the actual output too so future refactors that change
        # both files in the same wrong way still surface the
        # regression.
        assert hand == ("Bag count: 3\niter: 10\niter: 20\niter: 30\ndone\n")
