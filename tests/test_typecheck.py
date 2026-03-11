"""
Tests for the type checker (ztypecheck)
"""

import os


from conftest import make_parser_vfs
from zparser import Parser
from ztypecheck import typecheck, TypeChecker
from ztypechecker import ZTypeType, ZParamOwnership, ZOwnership, ZLockState, ZVariable, ZNaming
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
        """Core unit type should have numeric types, print, etc.
        Demand-driven: types are resolved when referenced."""
        program = check_ok('main: function is { x: 42\n print "test" }')
        tc = TypeChecker(program)
        tc.check()
        # print was referenced, so it should be resolved in core
        core = tc.unit_types["core"]
        assert "print" in core.children
        # i64 was referenced (via literal 42), so it should be resolved
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
        """System unit should have numeric types as records with methods.
        Demand-driven: reference i64 to trigger resolution."""
        program = check_ok("f: function {n: i64} out i64 is { return n + 1 }")
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


class TestComparisonOperators:
    """Test comparison operators added to all numeric types."""

    def test_less_than(self):
        check_ok("f: function {a: i64 b: i64} is { if a < b then return 0 }")

    def test_greater_than(self):
        check_ok("f: function {a: i64 b: i64} is { if a > b then return 0 }")

    def test_greater_equal(self):
        check_ok("f: function {a: i64 b: i64} is { if a >= b then return 0 }")

    def test_equal(self):
        check_ok("f: function {a: i64 b: i64} is { if a == b then return 0 }")

    def test_not_equal(self):
        check_ok("f: function {a: i64 b: i64} is { if a != b then return 0 }")

    def test_comparison_returns_bool(self):
        """Comparison operators should return bool, not the numeric type."""
        program = check_ok("f: function {a: i64 b: i64} is { if a < b then return 0 }")
        tc = TypeChecker(program)
        tc.check()
        system = tc.unit_types["system"]
        i64 = system.children["i64"]
        lt = i64.children["<"]
        assert lt.typetype == ZTypeType.FUNCTION
        ret = lt.children[":return"]
        assert ret.name == "bool"

    def test_comparison_on_f64(self):
        check_ok("f: function {a: f64 b: f64} is { if a < b then return 0 }")

    def test_comparison_on_u32(self):
        check_ok("f: function {a: u32 b: u32} is { if a >= b then return 0 }")


class TestSwapTypeCheck:
    def test_swap_same_type(self):
        check_ok("main: function is {\n  a: 10\n  b: 20\n  a swap b\n}")

    def test_swap_different_types_error(self):
        errors = check_errors(
            'main: function is {\n  a: 10\n  b: "hello"\n  a swap b\n}'
        )
        assert any("swap" in e.msg.lower() or "Cannot swap" in e.msg for e in errors)


class TestWithDo:
    def test_with_do_basic(self):
        check_ok(
            "abs: function {n: i64} out i64 is { return n }\n"
            "main: function is {\n"
            '  with y: abs -5 do print "result = \\{y}"\n'
            "}"
        )


class TestStringInterpolation:
    def test_interpolation_checks_expressions(self):
        check_ok('main: function is {\n  x: 42\n  print "value = \\{x}"\n}')


class TestDemandDriven:
    """Test demand-driven resolution behavior."""

    def test_unreferenced_not_resolved(self):
        """Definitions not reachable from main are not type-checked."""
        program = check_ok(
            "unused: function {n: i64} out i64 is { return n }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        # unused should still get resolved because check() iterates all functions
        # in the main unit
        key = "test.unused"
        assert key in tc._resolved


class TestCircularReferences:
    """Test cycle detection in type alias resolution."""

    def test_two_way_circular_alias(self):
        """a -> b -> a should be detected as circular."""
        errors = check_errors("a: b\nb: a\nmain: function is { x: a }")
        assert any("Circular" in e.msg or "circular" in e.msg for e in errors)

    def test_three_way_circular_alias(self):
        """a -> b -> c -> a should be detected as circular."""
        errors = check_errors("a: b\nb: c\nc: a\nmain: function is { x: a }")
        assert any("Circular" in e.msg or "circular" in e.msg for e in errors)

    def test_self_alias(self):
        """a: a is a trivially circular alias."""
        errors = check_errors("a: a\nmain: function is { x: a }")
        assert any("Circular" in e.msg or "circular" in e.msg for e in errors)

    def test_record_self_reference_via_type_is_valid(self):
        """Records using `type` keyword for self-reference should not error."""
        check_ok(
            "point: record {\n"
            "    x: f64\n"
            "    y: f64\n"
            "    +: function {:this rhs: type} out type\n"
            "}\n"
            "main: function is {}"
        )

    def test_record_method_returns_own_type(self):
        """A record method returning its own type via `type` resolves correctly."""
        program = check_ok(
            "vec: record {\n"
            "    x: f64\n"
            "} as {\n"
            "    scale: function {v: this} out type is { return v }\n"
            "}\n"
            "main: function is { v: vec }"
        )
        tc = TypeChecker(program)
        tc.check()
        vec_type = tc._resolved.get("test.vec")
        assert vec_type is not None
        scale = vec_type.children.get("scale")
        assert scale is not None
        ret = scale.children.get(":return")
        assert ret is vec_type

    def test_circular_chain_reports_full_chain(self):
        """Error message should include the full chain of names."""
        errors = check_errors("a: b\nb: c\nc: a\nmain: function is { x: a }")
        circular_errors = [e for e in errors if "Circular" in e.msg]
        assert len(circular_errors) >= 1
        msg = circular_errors[0].msg
        assert "test.a" in msg
        assert "test.b" in msg
        assert "test.c" in msg

    def test_non_circular_alias_chain(self):
        """a -> b -> i64 is a valid alias chain, not circular."""
        check_ok("b: i64\na: b\nmain: function is { x: a }")


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


class TestReturnTypeChecking:
    def test_correct_return_type(self):
        check_ok("f: function out i64 is { return 42 }")

    def test_wrong_return_type(self):
        errors = check_errors('f: function out i64 is { return "hello" }\nmain: function is {}')
        assert any("Return type mismatch" in e.msg for e in errors)

    def test_void_function_no_return(self):
        check_ok("f: function is {}\nmain: function is {}")

    def test_return_in_if(self):
        check_ok(
            "f: function {n: i64} out i64 is {\n"
            "  if n <= 1 then return 1\n"
            "  return n\n"
            "}"
        )


class TestNamedArguments:
    def test_named_arg_correct_type(self):
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "main: function is { add a: 1 b: 2 }"
        )

    def test_named_arg_wrong_type(self):
        errors = check_errors(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { add a: "hello" b: 2 }'
        )
        assert any("mismatch" in e.msg.lower() for e in errors)


class TestFullTypecheck:
    def test_full_flag_checks_all_units(self):
        from ztypecheck import TypeChecker

        program = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        # system unit types should all be resolved with full check
        system = tc.unit_types.get("system")
        assert system is not None


class TestOwnershipEnums:
    """Test the ownership-related enums and dataclasses."""

    def test_zownership_two_state(self):
        assert ZOwnership.OWNED == 0
        assert ZOwnership.BORROWED == 1
        assert len(ZOwnership) == 2

    def test_zlockstate_three_state(self):
        assert ZLockState.UNLOCKED == 0
        assert ZLockState.EXCLUSIVE == 1
        assert ZLockState.SHARED == 2
        assert len(ZLockState) == 3

    def test_zparam_ownership(self):
        assert ZParamOwnership.TAKE == 0
        assert ZParamOwnership.BORROW == 1
        assert ZParamOwnership.LOCK == 2
        assert len(ZParamOwnership) == 3

    def test_zvariable_defaults(self):
        from ztypechecker import ZType
        t = ZType(name="i64", typetype=ZTypeType.RECORD, parent=None)
        v = ZVariable(ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
        assert v.lock_state == ZLockState.UNLOCKED
        assert v.lock_targets == []

    def test_zvariable_with_lock(self):
        from ztypechecker import ZType
        t = ZType(name="point", typetype=ZTypeType.RECORD, parent=None)
        v = ZVariable(
            ztype=t,
            ownership=ZOwnership.BORROWED,
            named=ZNaming.NAMED,
            lock_state=ZLockState.EXCLUSIVE,
            lock_targets=["x"],
        )
        assert v.ownership == ZOwnership.BORROWED
        assert v.lock_state == ZLockState.EXCLUSIVE
        assert v.lock_targets == ["x"]


class TestOwnershipParsing:
    """Test that ownership annotations parse correctly on function parameters."""

    def test_param_borrow(self):
        """Parameter with .borrow annotation should parse and type-check."""
        program = check_ok(
            "f: function {a: i64.borrow} is {}\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert "a" in func.param_ownership
        assert func.param_ownership["a"] == ZParamOwnership.BORROW

    def test_param_take(self):
        """Parameter with .take annotation."""
        program = check_ok(
            "f: function {a: i64.take} is {}\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.param_ownership["a"] == ZParamOwnership.TAKE

    def test_param_lock(self):
        """Parameter with .lock annotation."""
        program = check_ok(
            "f: function {a: i64.lock} is {}\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.param_ownership["a"] == ZParamOwnership.LOCK

    def test_param_no_ownership(self):
        """Parameter without annotation should have empty param_ownership."""
        program = check_ok(
            "f: function {a: i64} is {}\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert "a" not in func.param_ownership

    def test_mixed_params(self):
        """Mix of annotated and unannotated parameters."""
        program = check_ok(
            "f: function {a: i64.take b: i64 c: i64.borrow} is {}\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.param_ownership["a"] == ZParamOwnership.TAKE
        assert "b" not in func.param_ownership
        assert func.param_ownership["c"] == ZParamOwnership.BORROW

    def test_return_type_borrow(self):
        """Return type with .borrow annotation."""
        program = check_ok(
            "f: function {a: i64.lock} out i64.borrow is { return a }\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.param_ownership[":return"] == ZParamOwnership.BORROW
        assert func.param_ownership["a"] == ZParamOwnership.LOCK

    def test_return_type_no_ownership(self):
        """Return type without annotation should not have :return in param_ownership."""
        program = check_ok(
            "f: function out i64 is { return 42 }\n"
            "main: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert ":return" not in func.param_ownership


class TestOwnershipInZType:
    """Test that ownership annotations propagate to ZType."""

    def test_param_ownership_on_ztype(self):
        """Ownership annotations should be on the ZType after type checking."""
        program = check_ok(
            "f: function {a: i64.take b: i64.borrow} out i64 is { return a }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.param_ownership["a"] == ZParamOwnership.TAKE
        assert ftype.param_ownership["b"] == ZParamOwnership.BORROW

    def test_return_ownership_on_ztype(self):
        """Return ownership should propagate to ZType."""
        program = check_ok(
            "f: function {a: i64.lock} out i64.borrow is { return a }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.param_ownership[":return"] == ZParamOwnership.BORROW
        assert ftype.param_ownership["a"] == ZParamOwnership.LOCK

    def test_no_ownership_empty_dict(self):
        """Functions without ownership annotations should have empty param_ownership."""
        program = check_ok(
            "f: function {a: i64} out i64 is { return a }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.param_ownership == {}


class TestValTypeTagging:
    """Test that types are correctly tagged as valtype or reftype."""

    def test_numeric_records_are_valtype(self):
        """System numeric types (records) should be tagged as valtype."""
        program = check_ok("f: function {n: i64} out i64 is { return n }")
        tc = TypeChecker(program)
        tc.check()
        system = tc.unit_types["system"]
        i64 = system.children.get("i64")
        assert i64 is not None
        assert i64.is_valtype is True

    def test_user_record_is_valtype(self):
        """User-defined records should be tagged as valtype."""
        program = check_ok(
            "point: record { x: f64\n y: f64 }\n"
            "main: function is { p: point }"
        )
        tc = TypeChecker(program)
        tc.check()
        point = tc._resolved.get("test.point")
        assert point is not None
        assert point.is_valtype is True

    def test_union_is_reftype(self):
        """Unions should be tagged as reftype (is_valtype=False)."""
        program = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        system = tc.unit_types["system"]
        any_type = system.children.get("any")
        assert any_type is not None
        assert any_type.is_valtype is False

    def test_enum_is_valtype(self):
        """Enums should be tagged as valtype."""
        program = check_ok(
            "color: enum { red\n green\n blue }\n"
            "main: function is { c: color.red }"
        )
        tc = TypeChecker(program)
        tc.check()
        color = tc._resolved.get("test.color")
        assert color is not None
        assert color.is_valtype is True

    def test_function_type_valtype_is_none(self):
        """Function types don't have a valtype classification."""
        program = check_ok("f: function is {}\nmain: function is {}")
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.is_valtype is None
