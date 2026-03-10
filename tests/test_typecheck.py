"""
Tests for the type checker (ztypecheck)
"""

import os


from conftest import make_parser_vfs
from zparser import Parser
from ztypecheck import typecheck, TypeChecker
from ztypechecker import ZTypeType
import zast


SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")


def parse_and_check(source: str, unitname: str = "test"):
    """Parse source, run type checker, return (program, errors)."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=SRC_DIR)
    p = Parser(vfs, name)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    errors = typecheck(program)
    return program, errors


def check_ok(source: str, unitname: str = "test"):
    """Parse and type-check, assert no errors, return program."""
    program, errors = parse_and_check(source, unitname)
    assert errors == [], f"Expected no errors, got: {[e.msg for e in errors]}"
    return program


def check_errors(source: str, unitname: str = "test"):
    """Parse and type-check, assert errors, return error list."""
    program, errors = parse_and_check(source, unitname)
    assert errors != [], "Expected type errors but got none"
    return errors


class TestBasicPrograms:
    def test_empty_function(self):
        check_ok("main: function is {}")

    def test_hello_world(self):
        check_ok('main: function is { print "Hello" }')

    def test_print_resolves_from_core(self):
        """print should be resolved via core -> io -> system.io_print."""
        program = check_ok('main: function is { print "test" }')
        tc = TypeChecker(program)
        tc.check()
        core_type = tc.unit_types.get("core")
        assert core_type is not None
        assert "print" in core_type.children
        assert core_type.children["print"].typetype == ZTypeType.FUNCTION


class TestNumericLiterals:
    def test_integer_literal(self):
        check_ok("main: function is { x: 42 }")

    def test_float_literal(self):
        check_ok("main: function is { x: 3.14 }")

    def test_typed_integer(self):
        check_ok("main: function is { x: 42i32 }")

    def test_negative_integer(self):
        check_ok("main: function is { x: -5 }")

    def test_invalid_float_with_int_suffix(self):
        errors = check_errors("main: function is { x: 3.14i32 }")
        assert any(
            "float" in e.msg.lower() or "decimal" in e.msg.lower() for e in errors
        )


class TestFunctionParameters:
    def test_function_with_params(self):
        check_ok("add: function {a: i64 b: i64} out i64 is { return a + b }")

    def test_function_calls_function(self):
        check_ok(
            "double: function {n: i64} out i64 is { return n + n }\n"
            "main: function is { double 5 }"
        )

    def test_recursive_function(self):
        check_ok(
            "fact: function {n: i64} out i64 is {\n"
            "  if n <= 1 then return 1\n"
            "  return n * (fact n - 1)\n"
            "}"
        )


class TestAssignment:
    def test_simple_assignment(self):
        check_ok("main: function is { x: 42 }")

    def test_string_assignment(self):
        check_ok('main: function is { x: "hello" }')

    def test_reassignment(self):
        check_ok(
            "main: function is {\n"
            "  for i: 0 while i <= 10 loop {\n"
            "    i = i + 1\n"
            "  }\n"
            "}"
        )


class TestBinaryOperations:
    def test_integer_addition(self):
        check_ok("f: function {a: i64 b: i64} out i64 is { return a + b }")

    def test_integer_subtraction(self):
        check_ok("f: function {a: i64 b: i64} out i64 is { return a - b }")

    def test_integer_multiplication(self):
        check_ok("f: function {a: i64 b: i64} out i64 is { return a * b }")

    def test_integer_comparison(self):
        check_ok("f: function {n: i64} is { if n <= 0 then return 0 }")


class TestControlFlow:
    def test_if_then(self):
        check_ok('f: function {n: i64} is { if n <= 0 then print "neg" }')

    def test_if_else(self):
        check_ok(
            "f: function {n: i64} is {\n"
            '  if n <= 0 then print "neg" else print "pos"\n'
            "}"
        )

    def test_for_loop(self):
        check_ok(
            "main: function is {\n"
            "  for i: 0 while i <= 10 loop {\n"
            "    i = i + 1\n"
            "  }\n"
            "}"
        )

    def test_for_loop_uses_variable(self):
        check_ok(
            "main: function is {\n"
            "  for i: 0 while i <= 10 loop {\n"
            "    i = i + 1\n"
            "  }\n"
            "}"
        )


class TestTypeResolution:
    def test_numeric_types_resolve(self):
        """All standard numeric types should resolve as parameter types."""
        for t in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "f32", "f64"):
            check_ok(f"f: function {{n: {t}}} is {{}}")

    def test_string_type_resolves(self):
        check_ok("f: function {s: system.string} is {}")

    def test_bool_type_resolves(self):
        check_ok("f: function {b: system.bool} is {}")


class TestUnitResolution:
    def test_core_numeric_types_in_scope(self):
        check_ok("f: function {n: i64} out i64 is { return n }")

    def test_core_types_populated(self):
        """Core unit type should have numeric types, print, etc."""
        program = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check()
        core = tc.unit_types["core"]
        assert "print" in core.children
        assert "i64" in core.children

    def test_cross_unit_alias_resolution(self):
        """io.print -> system.io_print should resolve across units."""
        program = check_ok('main: function is { print "test" }')
        tc = TypeChecker(program)
        tc.check()
        io_type = tc.unit_types.get("io")
        assert io_type is not None
        assert "print" in io_type.children
        assert io_type.children["print"].typetype == ZTypeType.FUNCTION

    def test_system_unit_has_numeric_records(self):
        """System unit should have numeric types as records with methods."""
        program = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check()
        system = tc.unit_types["system"]
        i64 = system.children.get("i64")
        assert i64 is not None
        assert i64.typetype == ZTypeType.RECORD
        assert "+" in i64.children
        assert "-" in i64.children
        assert "*" in i64.children
        assert "/" in i64.children


class TestExamplePrograms:
    """Test that all v1 example programs type-check successfully."""

    EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

    def _check_example(self, name: str):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=self.EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, name)
        program = p.parse()
        assert isinstance(program, zast.Program), f"Parse failed for {name}"
        errors = typecheck(program)
        return errors

    def test_hello(self):
        assert self._check_example("hello") == []

    def test_factorial(self):
        assert self._check_example("factorial") == []

    def test_fibonacci(self):
        assert self._check_example("fibonacci") == []

    def test_records(self):
        assert self._check_example("records") == []

    def test_strings(self):
        assert self._check_example("strings") == []

    def test_data(self):
        assert self._check_example("data") == []

    def test_mathutil(self):
        assert self._check_example("mathutil") == []

    def test_multimod(self):
        assert self._check_example("multimod") == []

    def test_swap(self):
        assert self._check_example("swap") == []

    def test_case(self):
        assert self._check_example("case") == []

    def test_control(self):
        assert self._check_example("control") == []
