"""
Tests for the AST content hasher (zasthash)
"""

from conftest import make_parser_vfs
from zparser import Parser
from ztypecheck import typecheck
import zast
import zasthash

import os

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def parse_and_check(source: str, unitname: str = "test"):
    """Parse and typecheck a source string, returning the program."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=LIB_DIR)
    p = Parser(vfs, name)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    errors = typecheck(program)
    assert errors == [], f"Type errors: {[e.msg for e in errors]}"
    return program


class TestAstHash:
    def test_identical_bodies_same_hash(self):
        """Two functions with identical bodies and types produce the same hash."""
        program = parse_and_check(
            "f1: function {x: i64} out i64 is { return x + 1 }\n"
            "f2: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is { f1 1\n f2 1 }"
        )
        unit = program.units["test"]
        f1 = unit.body["f1"]
        f2 = unit.body["f2"]
        assert isinstance(f1, zast.Function)
        assert isinstance(f2, zast.Function)
        h1 = zasthash.hash_function(f1)
        h2 = zasthash.hash_function(f2)
        assert h1 == h2

    def test_different_bodies_different_hash(self):
        """Functions with different bodies produce different hashes."""
        program = parse_and_check(
            "f1: function {x: i64} out i64 is { return x + 1 }\n"
            "f2: function {x: i64} out i64 is { return x + 2 }\n"
            "main: function is { f1 1\n f2 1 }"
        )
        unit = program.units["test"]
        f1 = unit.body["f1"]
        f2 = unit.body["f2"]
        h1 = zasthash.hash_function(f1)
        h2 = zasthash.hash_function(f2)
        assert h1 != h2

    def test_function_name_excluded(self):
        """Function name does not affect hash — same body = same hash."""
        program = parse_and_check(
            "alpha: function {x: i64} out i64 is { return x + 1 }\n"
            "beta: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is { alpha 1\n beta 1 }"
        )
        unit = program.units["test"]
        h1 = zasthash.hash_function(unit.body["alpha"])
        h2 = zasthash.hash_function(unit.body["beta"])
        assert h1 == h2

    def test_different_param_types_different_hash(self):
        """Different parameter types produce different hashes."""
        program = parse_and_check(
            "f1: function {x: i64} out i64 is { return x }\n"
            "f2: function {x: i32} out i32 is { return x }\n"
            "main: function is { f1 1\n f2 1i32 }"
        )
        unit = program.units["test"]
        h1 = zasthash.hash_function(unit.body["f1"])
        h2 = zasthash.hash_function(unit.body["f2"])
        assert h1 != h2

    def test_deterministic(self):
        """Same function hashed twice gives same result."""
        program = parse_and_check(
            "f1: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is { f1 1 }"
        )
        unit = program.units["test"]
        f = unit.body["f1"]
        h1 = zasthash.hash_function(f)
        h2 = zasthash.hash_function(f)
        assert h1 == h2

    def test_different_local_var_names_different_hash(self):
        """Different local variable names in body produce different hashes."""
        program = parse_and_check(
            "f1: function {x: i64} out i64 is { a: x + 1\n return a }\n"
            "f2: function {x: i64} out i64 is { b: x + 1\n return b }\n"
            "main: function is { f1 1\n f2 1 }"
        )
        unit = program.units["test"]
        h1 = zasthash.hash_function(unit.body["f1"])
        h2 = zasthash.hash_function(unit.body["f2"])
        assert h1 != h2

    def test_different_return_type_different_hash(self):
        """Different return types produce different hashes."""
        program = parse_and_check(
            "f1: function {x: i64} out i64 is { return x }\n"
            "f2: function {x: i64} out i32 is { return x.i32 }\n"
            "main: function is { f1 1\n f2 1 }"
        )
        unit = program.units["test"]
        h1 = zasthash.hash_function(unit.body["f1"])
        h2 = zasthash.hash_function(unit.body["f2"])
        assert h1 != h2
