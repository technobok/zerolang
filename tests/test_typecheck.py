"""
Tests for the type checker (ztypecheck)
"""

import os


from conftest import make_parser_vfs
from zparser import Parser
from ztypecheck import typecheck, TypeChecker
from ztypechecker import (
    ZTypeType,
    ZParamOwnership,
    ZOwnership,
    ZLockState,
    ZVariable,
    ZNaming,
    LockEntry,
)
import zast


LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def parse_and_check(source: str, unitname: str = "test"):
    """Parse source, run type checker, return (program, errors)."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=LIB_DIR)
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
        check_ok("f: function {s: string} is {}")

    def test_bool_type_resolves(self):
        check_ok("f: function {b: bool} is {}")


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
        systemdir = os.path.join(LIB_DIR, "system")
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

    def test_classes(self):
        assert self._check_example("classes") == []

    def test_unions(self):
        assert self._check_example("unions") == []


class TestReturnTypeChecking:
    def test_correct_return_type(self):
        check_ok("f: function out i64 is { return 42 }")

    def test_wrong_return_type(self):
        errors = check_errors(
            'f: function out i64 is { return "hello" }\nmain: function is {}'
        )
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
        assert v.locks == []
        assert v.held_locks == []

    def test_zvariable_with_lock(self):
        from ztypechecker import ZType

        t = ZType(name="point", typetype=ZTypeType.RECORD, parent=None)
        v = ZVariable(
            ztype=t,
            ownership=ZOwnership.BORROWED,
            named=ZNaming.NAMED,
            locks=[LockEntry(lock_type=ZLockState.EXCLUSIVE, holder="y")],
            held_locks=["x"],
        )
        assert v.ownership == ZOwnership.BORROWED
        assert len(v.locks) == 1
        assert v.locks[0].lock_type == ZLockState.EXCLUSIVE
        assert v.locks[0].holder == "y"
        assert v.held_locks == ["x"]


class TestOwnershipParsing:
    """Test that ownership annotations parse correctly on function parameters."""

    def test_param_borrow(self):
        """Parameter with .borrow annotation should parse and type-check."""
        program = check_ok("f: function {a: i64.borrow} is {}\nmain: function is {}")
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert "a" in func.param_ownership
        assert func.param_ownership["a"] == ZParamOwnership.BORROW

    def test_param_take(self):
        """Parameter with .take annotation."""
        program = check_ok("f: function {a: i64.take} is {}\nmain: function is {}")
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.param_ownership["a"] == ZParamOwnership.TAKE

    def test_param_lock(self):
        """Parameter with .lock annotation (requires return value)."""
        program = check_ok(
            "f: function {a: i64.lock} out i64 is { return a }\nmain: function is {}"
        )
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.param_ownership["a"] == ZParamOwnership.LOCK

    def test_param_no_ownership(self):
        """Parameter without annotation should have empty param_ownership."""
        program = check_ok("f: function {a: i64} is {}\nmain: function is {}")
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert "a" not in func.param_ownership

    def test_mixed_params(self):
        """Mix of annotated and unannotated parameters."""
        program = check_ok(
            "f: function {a: i64.take b: i64 c: i64.borrow} is {}\nmain: function is {}"
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
        program = check_ok("f: function out i64 is { return 42 }\nmain: function is {}")
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
            "f: function {a: i64} out i64 is { return a }\nmain: function is {}"
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
            "point: record { x: f64\n y: f64 }\nmain: function is { p: point }"
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

    def test_enum_is_reserved(self):
        """Enum keyword is reserved and should produce a parse error."""
        vfs, name = make_parser_vfs(
            "color: enum { red\n green\n blue }\nmain: function is { c: color.red }",
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = Parser(vfs, name)
        result = p.parse()
        assert isinstance(result, zast.Error)

    def test_function_type_valtype_is_none(self):
        """Function types don't have a valtype classification."""
        program = check_ok("f: function is {}\nmain: function is {}")
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.is_valtype is None


class TestOwnershipSignatureValidation:
    """Test ownership rules on function signatures."""

    def test_borrow_return_without_lock_param_error(self):
        """Returning borrow without any lock parameter is an error."""
        errors = check_errors(
            "f: function {t: i64} out i64.borrow is { return t }\nmain: function is {}"
        )
        assert any(
            "lock" in e.msg.lower() and "borrow" in e.msg.lower() for e in errors
        )

    def test_borrow_return_with_lock_param_ok(self):
        """Returning borrow with a lock parameter is OK."""
        check_ok(
            "f: function {t: i64.lock} out i64.borrow is { return t }\n"
            "main: function is {}"
        )

    def test_lock_param_without_return_error(self):
        """Lock parameter on a void function is an error."""
        errors = check_errors("f: function {t: i64.lock} is {}\nmain: function is {}")
        assert any(
            "lock" in e.msg.lower() and "no return" in e.msg.lower() for e in errors
        )

    def test_lock_param_with_return_ok(self):
        """Lock parameter with a return value is OK."""
        check_ok(
            "f: function {t: i64.lock} out i64 is { return t }\nmain: function is {}"
        )

    def test_borrow_return_no_params_error(self):
        """Returning borrow with zero parameters is an error."""
        errors = check_errors(
            "f: function out i64.borrow is { return 42 }\nmain: function is {}"
        )
        assert any("borrow" in e.msg.lower() for e in errors)


class TestOwnershipReturnChecking:
    """Test that returning local variables as borrowed is caught."""

    def test_return_local_as_borrow_error(self):
        """Cannot return a local variable as borrowed."""
        errors = check_errors(
            "f: function {t: i64.lock} out i64.borrow is {\n"
            "  x: 42\n"
            "  return x\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            "local variable" in e.msg.lower() or "local" in e.msg for e in errors
        )

    def test_return_lock_param_as_borrow_ok(self):
        """Returning a lock parameter as borrowed is OK."""
        check_ok(
            "f: function {t: i64.lock} out i64.borrow is { return t }\n"
            "main: function is {}"
        )


class TestTakeBorrowCompilerMethods:
    """.take and .borrow compiler methods."""

    def test_take_resolves(self):
        """x.take should resolve to x's type."""
        check_ok("main: function is {\n  x: 42\n  y: x.take\n}")

    def test_borrow_resolves(self):
        """x.borrow should resolve to x's type."""
        check_ok("main: function is {\n  x: 42\n  y: x.borrow\n}")

    def test_take_invalidates_name(self):
        """After x.take, x should be invalid."""
        errors = check_errors("main: function is {\n  x: 42\n  y: x.take\n  z: x\n}")
        assert any("Undefined" in e.msg or "undefined" in e.msg for e in errors)


class TestSwapOwnership:
    """Test swap ownership rules."""

    def test_swap_valtype_ok(self):
        """Swap of owned valtype variables is OK."""
        check_ok("main: function is {\n  a: 10\n  b: 20\n  a swap b\n}")


class TestExampleProgramsOwnership:
    """Verify all v1 example programs still pass with ownership checking."""

    EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

    def _check_example(self, name: str):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
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

    def test_classes(self):
        assert self._check_example("classes") == []

    def test_unions(self):
        assert self._check_example("unions") == []


# ---- Phase 4d: Lock Checking Tests ----


class TestLockEntry:
    """Test the LockEntry dataclass."""

    def test_lock_entry_exclusive(self):
        e = LockEntry(lock_type=ZLockState.EXCLUSIVE, holder="y")
        assert e.lock_type == ZLockState.EXCLUSIVE
        assert e.holder == "y"

    def test_lock_entry_shared(self):
        e = LockEntry(lock_type=ZLockState.SHARED, holder="parent")
        assert e.lock_type == ZLockState.SHARED
        assert e.holder == "parent"


class TestSymbolTableLocking:
    """Test lock operations on the symbol table."""

    def _make_symtab_with_vars(self, *names):
        from zenv import SymbolTable
        from ztypechecker import ZType

        st = SymbolTable()
        st.push("test")
        t = ZType(name="myclass", typetype=ZTypeType.UNION, parent=None)
        t.is_valtype = False
        for name in names:
            var = ZVariable(ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
            st.define_var(name, var)
        return st

    def test_try_lock_exclusive_on_unlocked(self):
        st = self._make_symtab_with_vars("x", "y")
        err = st.try_lock("x", ZLockState.EXCLUSIVE, "y")
        assert err is None
        var = st.lookup_var("x")
        assert len(var.locks) == 1
        assert var.locks[0].lock_type == ZLockState.EXCLUSIVE

    def test_try_lock_exclusive_on_exclusive_fails(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock("x", ZLockState.EXCLUSIVE, "y")
        assert err is None
        err = st.try_lock("x", ZLockState.EXCLUSIVE, "z")
        assert err is not None
        assert "exclusive" in err.lower()

    def test_try_lock_shared_on_shared_ok(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock("x", ZLockState.SHARED, "y")
        assert err is None
        err = st.try_lock("x", ZLockState.SHARED, "z")
        assert err is None
        var = st.lookup_var("x")
        assert len(var.locks) == 2

    def test_try_lock_shared_on_exclusive_fails(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock("x", ZLockState.EXCLUSIVE, "y")
        assert err is None
        err = st.try_lock("x", ZLockState.SHARED, "z")
        assert err is not None
        assert "exclusive" in err.lower()

    def test_try_lock_exclusive_on_shared_fails(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock("x", ZLockState.SHARED, "y")
        assert err is None
        err = st.try_lock("x", ZLockState.EXCLUSIVE, "z")
        assert err is not None

    def test_release_lock(self):
        st = self._make_symtab_with_vars("x", "y")
        st.try_lock("x", ZLockState.EXCLUSIVE, "y")
        var = st.lookup_var("x")
        assert len(var.locks) == 1
        st.release_lock("x", "y")
        assert len(var.locks) == 0

    def test_release_held_locks(self):
        st = self._make_symtab_with_vars("x", "y")
        st.try_lock("x", ZLockState.EXCLUSIVE, "y")
        target = st.lookup_var("x")
        holder = st.lookup_var("y")
        assert len(target.locks) == 1
        assert holder.held_locks == ["x"]
        st.release_held_locks("y")
        assert len(target.locks) == 0
        assert holder.held_locks == []

    def test_release_only_specific_holder(self):
        """Releasing locks for one holder should not affect another holder's locks."""
        st = self._make_symtab_with_vars("x", "y", "z")
        st.try_lock("x", ZLockState.SHARED, "y")
        st.try_lock("x", ZLockState.SHARED, "z")
        var = st.lookup_var("x")
        assert len(var.locks) == 2
        st.release_lock("x", "y")
        assert len(var.locks) == 1
        assert var.locks[0].holder == "z"


class TestLockCheckingBorrow:
    """Test lock checking for .borrow compiler method."""

    def test_borrow_ok(self):
        """y: x.borrow should work without errors."""
        check_ok("main: function is {\n  x: 42\n  y: x.borrow\n}")

    def test_chained_borrow_ok(self):
        """y: x.borrow, z: y.borrow should work (z locks y which locks x)."""
        check_ok("main: function is {\n  x: 42\n  y: x.borrow\n  z: y.borrow\n}")

    def test_double_borrow_same_var_error(self):
        """Cannot borrow x twice — second borrow conflicts with existing exclusive lock."""
        errors = check_errors(
            "main: function is {\n  x: 42\n  y: x.borrow\n  z: x.borrow\n}"
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )


class TestLockCheckingDataExempt:
    """Test that data items are exempt from locking."""

    def test_data_item_no_lock(self):
        """Data items are immutable and should not require locks."""
        check_ok("primes: data { 2 3 5 7 }\nmain: function is { x: primes.0 }")


class TestLockCheckingScopeExit:
    """Test that locks are released on scope exit."""

    def test_borrow_released_after_take(self):
        """After .take invalidates a var, its held locks are released."""
        check_ok("main: function is {\n  x: 42\n  y: x.take\n}")


class TestLockCheckingExamplePrograms:
    """Verify all v1 example programs still pass with lock checking."""

    EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

    def _check_example(self, name: str):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
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

    def test_classes(self):
        assert self._check_example("classes") == []

    def test_unions(self):
        assert self._check_example("unions") == []


# ---- Phase 4f: Class Type Checking Tests ----


class TestClassTypeResolution:
    """Test that class types resolve correctly."""

    def test_class_resolves_as_class_type(self):
        """A class definition resolves to ZTypeType.CLASS."""
        program = check_ok(
            "myclass: class { x: i64 }\nmain: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert ct is not None
        assert ct.typetype == ZTypeType.CLASS

    def test_class_is_reftype(self):
        """Classes should be tagged as reftype (is_valtype=False)."""
        program = check_ok(
            "myclass: class { x: i64 }\nmain: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert ct is not None
        assert ct.is_valtype is False

    def test_class_fields_resolved(self):
        """Class fields should be resolved as children of the class type."""
        program = check_ok(
            "myclass: class { x: i64\n y: f64 }\nmain: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert "x" in ct.children
        assert ct.children["x"].name == "i64"
        assert "y" in ct.children
        assert ct.children["y"].name == "f64"

    def test_class_methods_resolved(self):
        """Class methods should be resolved as children."""
        program = check_ok(
            "myclass: class { x: i64 } as {\n"
            "  get: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert "get" in ct.children
        assert ct.children["get"].typetype == ZTypeType.FUNCTION

    def test_class_this_resolves_to_class(self):
        """The `this` keyword in class methods resolves to the class type."""
        program = check_ok(
            "myclass: class { x: i64 } as {\n"
            "  get: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        get_fn = ct.children["get"]
        param_c = get_fn.children.get("c")
        assert param_c is ct

    def test_class_type_keyword(self):
        """The `type` keyword in a class resolves to the class type."""
        program = check_ok(
            "myclass: class { x: i64 } as {\n"
            "  clone: function {c: this} out type is { return c }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        clone_fn = ct.children["clone"]
        ret = clone_fn.children.get(":return")
        assert ret is ct


class TestClassConstruction:
    """Test class construction type checking."""

    def test_class_construction_returns_class_type(self):
        """Calling a class type creates an instance of that type."""
        check_ok("myclass: class { x: i64 }\nmain: function is { c: myclass x: 5 }")

    def test_class_bare_name_construction(self):
        """A bare class name creates a zero-initialized instance."""
        check_ok("myclass: class { x: i64 }\nmain: function is { c: myclass }")


class TestClassOwnership:
    """Test ownership rules for class instances."""

    def test_class_take_invalidates(self):
        """After .take on a class variable, the source is invalidated."""
        errors = check_errors(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: myclass\n"
            "  d: c.take\n"
            "  e: c\n"
            "}"
        )
        assert any("Undefined" in e.msg or "undefined" in e.msg for e in errors)

    def test_class_borrow_locks(self):
        """Borrowing a class variable locks the source."""
        errors = check_errors(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: myclass\n"
            "  d: c.borrow\n"
            "  e: c.borrow\n"
            "}"
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_class_swap_ok(self):
        """Swapping two class variables should work."""
        check_ok(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass\n"
            "  b: myclass\n"
            "  a swap b\n"
            "}"
        )

    def test_class_aliasing_error(self):
        """Passing the same class instance twice to a call is an aliasing error."""
        errors = check_errors(
            "myclass: class { x: i64 }\n"
            "f: function {a: myclass b: myclass} is {}\n"
            "main: function is {\n"
            "  c: myclass\n"
            "  f a: c b: c\n"
            "}"
        )
        assert any("aliasing" in e.msg.lower() for e in errors)


class TestStringMigration:
    """Phase 4g: string resolves via class path, not record special-case."""

    def test_string_resolves_as_class_type(self):
        """string should now resolve as ZTypeType.CLASS."""
        program = check_ok('main: function is { s: "hello" }')
        tc = TypeChecker(program)
        tc.check()
        st = tc._resolved.get("system.string")
        assert st is not None
        assert st.typetype == ZTypeType.CLASS

    def test_string_is_reftype(self):
        """string should be tagged as reftype (is_valtype=False) via class path."""
        program = check_ok('main: function is { s: "hello" }')
        tc = TypeChecker(program)
        tc.check()
        st = tc._resolved.get("system.string")
        assert st is not None
        assert st.is_valtype is False

    def test_string_take_invalidates(self):
        """After .take on a string variable, the source is invalidated."""
        errors = check_errors(
            'main: function is {\n  s: "hello"\n  d: s.take\n  e: s\n}'
        )
        assert any("Undefined" in e.msg or "undefined" in e.msg for e in errors)

    def test_string_borrow_locks(self):
        """Borrowing a string variable should lock the source."""
        errors = check_errors(
            'main: function is {\n  s: "hello"\n  d: s.borrow\n  e: s.borrow\n}'
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_string_swap_ok(self):
        """Swapping two string variables should work."""
        check_ok('main: function is {\n  a: "hello"\n  b: "world"\n  a swap b\n}')

    def test_string_aliasing_error(self):
        """Passing the same string twice to a call is an aliasing error."""
        errors = check_errors(
            "f: function {a: string b: string} is {}\n"
            "main: function is {\n"
            '  s: "hello"\n'
            "  f a: s b: s\n"
            "}"
        )
        assert any("aliasing" in e.msg.lower() for e in errors)


# ---- Phase 4h: Union Type Checking Tests ----


class TestUnionTypeResolution:
    """Test that union types resolve correctly."""

    def test_union_resolves_as_union_type(self):
        """A union definition resolves to ZTypeType.UNION."""
        program = check_ok(
            "myunion: union { a: i64\n b: string\n c: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None
        assert ut.typetype == ZTypeType.UNION

    def test_union_is_reftype(self):
        """Unions should be tagged as reftype (is_valtype=False)."""
        program = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None
        assert ut.is_valtype is False

    def test_union_subtypes_stored_as_children(self):
        """Union subtypes should be stored as children."""
        program = check_ok(
            "myunion: union { a: i64\n b: string\n c: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert "a" in ut.children
        assert "b" in ut.children
        assert "c" in ut.children
        assert ut.children["a"].name == "i64"
        assert ut.children["b"].name == "string"
        assert ut.children["c"].name == "null"

    def test_union_tag_type_generated(self):
        """Union should have a :tag child with enum-like discriminators."""
        program = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        tag = ut.children.get(":tag")
        assert tag is not None
        assert tag.typetype == ZTypeType.ENUM
        assert "a" in tag.children
        assert "b" in tag.children

    def test_union_null_subtype(self):
        """Null subtypes get a sentinel NULL type."""
        program = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.b }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut.children["b"].name == "null"
        assert ut.children["b"].is_valtype is True


class TestUnionConstruction:
    """Test union construction type checking."""

    def test_union_subtype_construction_returns_union_type(self):
        """Calling union.subtype expr returns the union type."""
        program = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None

    def test_union_null_construction(self):
        """Calling union.nullsubtype (no args) creates a union instance."""
        check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.b }"
        )

    def test_union_string_construction(self):
        """Calling union.stringsubtype with string arg creates a union."""
        check_ok(
            "myunion: union { a: string\n b: null }\n"
            'main: function is { x: myunion.a "hello" }'
        )


class TestUnionOwnership:
    """Test ownership rules for union instances."""

    def test_union_take_invalidates(self):
        """After .take on a union variable, the source is invalidated."""
        errors = check_errors(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  y: x.take\n"
            "  z: x\n"
            "}"
        )
        assert any("Undefined" in e.msg or "undefined" in e.msg for e in errors)

    def test_union_borrow_locks(self):
        """Borrowing a union variable locks the source."""
        errors = check_errors(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  y: x.borrow\n"
            "  z: x.borrow\n"
            "}"
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_union_swap_ok(self):
        """Swapping two union variables should work."""
        check_ok(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  y: myunion.b\n"
            "  x swap y\n"
            "}"
        )

    def test_union_aliasing_error(self):
        """Passing the same union twice to a call is an aliasing error."""
        errors = check_errors(
            "myunion: union { a: i64\n b: null }\n"
            "f: function {x: myunion y: myunion} is {}\n"
            "main: function is {\n"
            "  u: myunion.a 1\n"
            "  f x: u y: u\n"
            "}"
        )
        assert any("aliasing" in e.msg.lower() for e in errors)


class TestUnionMatchExhaustiveness:
    """Test match exhaustiveness checking for unions."""

    def test_exhaustive_match_ok(self):
        """All subtypes covered is ok."""
        check_ok(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  } case b then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )

    def test_missing_case_error(self):
        """Missing case without else is an error."""
        errors = check_errors(
            "myunion: union { a: i64\n b: string\n c: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  }\n"
            "}"
        )
        assert any(
            "exhaustive" in e.msg.lower() or "missing" in e.msg.lower() for e in errors
        )

    def test_else_covers_remaining(self):
        """Else clause covers remaining subtypes."""
        check_ok(
            "myunion: union { a: i64\n b: string\n c: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            '  } else print "other"\n'
            "}"
        )

    def test_union_example_passes(self):
        """The unions.z example program passes type checking."""
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
        pmainid = vfs.register(FSProvider(rootpath=examples_dir, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "unions")
        program = p.parse()
        assert isinstance(program, zast.Program), "Parse failed"
        errors = typecheck(program)
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"


class TestDataTypeResolution:
    """Test data type resolution in the type checker."""

    def test_data_resolves_as_data_type(self):
        """Data definitions should resolve to DATA ZType."""
        program = check_ok(
            "mydata: data { 10 20 30 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert dt is not None
        assert dt.typetype == ZTypeType.DATA

    def test_data_ordinal_identifiers(self):
        """Unnamed data elements get ordinal identifiers 0, 1, 2..."""
        program = check_ok(
            "mydata: data { 10 20 30 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert "0" in dt.children
        assert "1" in dt.children
        assert "2" in dt.children

    def test_data_named_elements(self):
        """Named data elements use their labels."""
        program = check_ok(
            "mydata: data { LOW: 0 HIGH: 10 }\nmain: function is { x: mydata.LOW }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert "LOW" in dt.children
        assert "HIGH" in dt.children

    def test_data_has_tag_subtype(self):
        """All data types should have a .tag subtype of TAG type."""
        program = check_ok("mydata: data { 1 2 3 }\nmain: function is { x: mydata.0 }")
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert "tag" in dt.children
        assert dt.children["tag"].typetype == ZTypeType.TAG

    def test_data_tag_parent_is_data(self):
        """The .tag type's parent should point back to its data type."""
        program = check_ok(
            "mydata: data { LOW: 0 HIGH: 1 }\nmain: function is { x: mydata.LOW }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        tag = dt.children["tag"]
        assert tag.parent is dt

    def test_data_mixed_named_unnamed(self):
        """Data with mixed named and unnamed elements."""
        program = check_ok(
            "mydata: data { 10 MIDDLE: 20 30 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert "0" in dt.children
        assert "MIDDLE" in dt.children
        assert "2" in dt.children


class TestUnionCustomTag:
    """Test union custom tag via as block (Phase 18)."""

    def test_custom_data_tag(self):
        """Union with custom data tag should resolve without errors."""
        check_ok(
            "pv: data { LOW: 0 HIGH: 1 }\n"
            "priority: union {\n"
            "    LOW: null\n"
            "    HIGH: null\n"
            "} as {\n"
            "    tag: pv.tag\n"
            "}\n"
            "main: function is { x: priority.LOW }"
        )

    def test_custom_tag_values_in_tag_enum(self):
        """Custom data values should appear in the :tag enum."""
        program = check_ok(
            "pv: data { A: 10 B: 20 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        tag = ut.children.get(":tag")
        assert tag is not None
        # check that the discriminator values match the data
        assert tag.children["A"].name == "10"
        assert tag.children["B"].name == "20"

    def test_custom_tag_mismatched_labels_error(self):
        """Data labels not matching union subtypes should error."""
        errors = check_errors(
            "pv: data { X: 0 Y: 1 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        assert any("do not match" in e.msg for e in errors)

    def test_custom_tag_duplicate_values_error(self):
        """Data with duplicate values used as tag should error."""
        errors = check_errors(
            "pv: data { A: 5 B: 5 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        assert any("duplicate" in e.msg.lower() for e in errors)

    def test_custom_tag_sparse_values(self):
        """Custom tag with non-sequential values."""
        program = check_ok(
            "pv: data { LOW: 0 MEDIUM: 1 HIGH: 2 CRITICAL: 10 }\n"
            "priority: union {\n"
            "    LOW: null\n"
            "    MEDIUM: null\n"
            "    HIGH: null\n"
            "    CRITICAL: null\n"
            "} as {\n"
            "    tag: pv.tag\n"
            "}\n"
            "main: function is { x: priority.LOW }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.priority")
        tag = ut.children.get(":tag")
        assert tag.children["CRITICAL"].name == "10"

    def test_multiple_tag_items_error(self):
        """Multiple .tag items in as block should error."""
        errors = check_errors(
            "pv1: data { A: 0 B: 1 }\n"
            "pv2: data { A: 0 B: 1 }\n"
            "myunion: union { A: null\n B: null } as { t1: pv1.tag\n t2: pv2.tag }\n"
            "main: function is { x: myunion.A }"
        )
        assert any("multiple" in e.msg.lower() for e in errors)

    def test_default_auto_tag_u8(self):
        """Union without custom tag gets auto-generated u8 tag."""
        program = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        tag = ut.children.get(":tag")
        assert tag is not None
        assert tag.children["a"].name == "0"
        assert tag.children["b"].name == "1"

    def test_union_has_tag_data_child(self):
        """Union should have a 'tag' child (data type) for MyUnion.tag access."""
        program = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert "tag" in ut.children
        assert ut.children["tag"].typetype == ZTypeType.DATA

    def test_custom_tag_union_has_data_child(self):
        """Union with custom tag should have the data instance as 'tag' child."""
        program = check_ok(
            "pv: data { A: 0 B: 1 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert "tag" in ut.children
        tag_data = ut.children["tag"]
        assert tag_data.typetype == ZTypeType.DATA
        assert tag_data.name == "pv"

    def test_custom_tag_name_convention_only(self):
        """The as block label name can be anything, not just 'tag'."""
        check_ok(
            "pv: data { A: 0 B: 1 }\n"
            "myunion: union { A: null\n B: null } as { discriminator: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )

    def test_union_match_with_custom_tag(self):
        """Match on union with custom tag should work."""
        check_ok(
            "pv: data { A: 0 B: 1 }\n"
            "myunion: union { A: i64\n B: null } as { tag: pv.tag }\n"
            "main: function is {\n"
            "  x: myunion.A 42\n"
            "  match (\n"
            "    x\n"
            "  ) case A then {\n"
            '    print "a"\n'
            "  } case B then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )

    def test_numeric_type_tag(self):
        """Using u16.tag should auto-generate sequential values with u16 storage."""
        check_ok(
            "myunion: union { A: null\n B: null } as { tag: u16.tag }\n"
            "main: function is { x: myunion.A }"
        )


class TestLabelValueShorthand:
    """Test :x (label_value) type checking — 'don't bind to self' semantics."""

    def test_label_value_unit_level_resolves_core_type(self):
        """:u8 at unit level resolves to core u8, not circular error."""
        program = check_ok(":u8\nmain: function is { x: u8 42 }")
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.u8")
        assert ut is not None
        assert ut.name == "u8"

    def test_label_value_core_type_resolves(self):
        """:x where x exists in core resolves correctly."""
        program = check_ok(":i64\nmain: function is { x: i64 1 }")
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.i64")
        assert ut is not None
        assert ut.name == "i64"

    def test_label_value_unknown_name_error(self):
        """:x where x doesn't exist anywhere produces an error, not circular."""
        errors = check_errors(":nonexistent\nmain: function is { x: nonexistent }")
        msgs = [e.msg for e in errors]
        # Should be an unresolved name, not a circular alias
        assert not any("ircular" in m for m in msgs)

    def test_union_with_label_value_subtypes(self):
        """union { :u8 :u16 :u32 } type checks correctly."""
        program = check_ok(
            "myunion: union { :u8\n :u16\n :u32 }\n"
            "main: function is { x: myunion.u8 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None
        assert ut.typetype == ZTypeType.UNION
        assert "u8" in ut.children
        assert "u16" in ut.children
        assert "u32" in ut.children

    def test_union_label_value_subtype_names(self):
        """Label value union subtypes resolve to their payload types."""
        program = check_ok(
            "myunion: union { :u8\n :string }\nmain: function is { x: myunion.u8 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut.children["u8"].name == "u8"
        assert ut.children["string"].name == "string"

    def test_function_with_label_value_param(self):
        """Function with :i64 parameter type checks."""
        check_ok("f: function {:i64} out i64 is { i64 }\nmain: function is { f 42 }")

    def test_call_with_label_value_arg(self):
        """Call with :x argument type checks."""
        check_ok(
            "f: function {x: i64} out i64 is { x }\nmain: function is { x: 42\n f :x }"
        )

    def test_statement_label_value_in_body(self):
        """Statement with label value in function body type checks."""
        check_ok(
            "f: function {x: i64} out i64 is { y: x\n y }\nmain: function is { f 42 }"
        )


class TestInlineUnits:
    def test_inline_unit_with_constant(self):
        """Inline unit with a constant resolves as UNIT type."""
        prog = check_ok("m: unit { X: 42 }\nmain: function is {}")
        tc = TypeChecker(prog)
        tc.check()
        assert "m" in tc.unit_types
        assert tc.unit_types["m"].typetype == ZTypeType.UNIT

    def test_dotted_access_constant(self):
        """Dotted access to inline unit constant resolves correctly."""
        check_ok("m: unit { X: 42 }\nY: m.X\nmain: function is {}")

    def test_inline_unit_with_function(self):
        """Inline unit containing a function resolves the function type."""
        prog = check_ok(
            'm: unit { greet: function is { print "hi" } }\n'
            "main: function is { m.greet }"
        )
        tc = TypeChecker(prog)
        tc.check()
        ft = tc.unit_types["m"].children.get("greet")
        assert ft is not None
        assert ft.typetype == ZTypeType.FUNCTION

    def test_nested_units(self):
        """Nested inline units: a.b.X resolves correctly."""
        check_ok("a: unit { b: unit { X: 1 } }\nY: a.b.X\nmain: function is {}")

    def test_name_resolution_upward(self):
        """Inline unit body can reference definitions from parent unit."""
        check_ok("X: 10\nm: unit { Y: X }\nmain: function is {}")

    def test_forward_reference_between_inline_units(self):
        """Inline unit A references inline unit B defined later in same parent."""
        check_ok("a: unit { Y: b.X }\nb: unit { X: 42 }\nmain: function is {}")

    def test_inline_unit_with_record(self):
        """Inline unit containing a record definition."""
        prog = check_ok(
            "m: unit { pt: record { x: i64  y: i64 } }\nmain: function is {}"
        )
        tc = TypeChecker(prog)
        tc.check()
        pt = tc.unit_types["m"].children.get("pt")
        assert pt is not None
        assert pt.typetype == ZTypeType.RECORD

    def test_inline_unit_function_body_checking(self):
        """Function bodies inside inline units are type-checked."""
        prog = check_ok(
            'm: unit { f: function {x: i64} is { print "\\{x}" } }\n'
            "main: function is { m.f 42 }"
        )
        # if body checking failed, we would have errors
        assert isinstance(prog, zast.Program)

    def test_3level_nesting(self):
        """3-level nesting resolves correctly via dotted access."""
        prog = check_ok(
            "a: unit { b: unit { c: unit { X: 99 } } }\n"
            "Y: a.b.c.X\n"
            "main: function is {}"
        )
        tc = TypeChecker(prog)
        tc.check()
        # nested units are stored with qualified names
        at = tc.unit_types.get("a")
        assert at is not None
        assert "b" in at.children

    def test_inline_unit_upward_reference(self):
        """Inline unit body can reference definitions from parent unit."""
        check_ok("X: 42\nm: unit { Y: X }\nmain: function is {}")

    def test_nesting_shadow(self):
        """Nested unit shadows parent definition via unit context stack."""
        prog = check_ok(
            "a: unit { X: 10\n b: unit { X: 20\n Y: X } }\nmain: function is {}"
        )
        tc = TypeChecker(prog)
        tc.check()
        # b is stored under qualified name "a.b"
        bt = tc.unit_types.get("a.b")
        assert bt is not None
        assert "Y" in bt.children


class TestVariantTypeResolution:
    """Test that variant types resolve correctly."""

    def test_variant_resolves(self):
        """A variant definition resolves to ZTypeType.VARIANT."""
        program = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert vt is not None
        assert vt.typetype == ZTypeType.VARIANT

    def test_variant_is_valtype(self):
        """Variants should be tagged as valtype (is_valtype=True)."""
        program = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert vt is not None
        assert vt.is_valtype is True

    def test_variant_subtypes(self):
        """Variant subtypes should be stored as children."""
        program = check_ok(
            "myvar: variant { a: i64\n b: u8\n c: null }\n"
            "main: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert "a" in vt.children
        assert "b" in vt.children
        assert "c" in vt.children
        assert vt.children["a"].name == "i64"
        assert vt.children["b"].name == "u8"
        assert vt.children["c"].name == "null"

    def test_variant_tag_generated(self):
        """Variant should have a :tag child with enum-like discriminators."""
        program = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        tag = vt.children.get(":tag")
        assert tag is not None
        assert tag.typetype == ZTypeType.ENUM
        assert "a" in tag.children
        assert "b" in tag.children

    def test_variant_null_subtype(self):
        """Null subtypes are fine in variants."""
        program = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.b }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert vt.children["b"].name == "null"
        assert vt.children["b"].is_valtype is True

    def test_variant_rejects_string(self):
        """Variant subtypes that are reftypes (string) should be rejected."""
        errors = check_errors(
            "myvar: variant { a: string\n b: null }\nmain: function is { x: myvar.b }"
        )
        assert any("value type" in e.msg.lower() for e in errors)

    def test_variant_rejects_union(self):
        """Variant subtypes that are unions (reftype) should be rejected."""
        errors = check_errors(
            "myunion: union { x: i64\n y: null }\n"
            "myvar: variant { a: myunion\n b: null }\n"
            "main: function is { x: myvar.b }"
        )
        assert any("value type" in e.msg.lower() for e in errors)

    def test_variant_allows_record(self):
        """Variant subtypes that are records (valtype) should be allowed."""
        check_ok(
            "point: record { x: i64\n y: i64 }\n"
            "myvar: variant { a: point\n b: null }\n"
            "main: function is { x: myvar.a (point x: 1 y: 2) }"
        )

    def test_variant_custom_data_tag(self):
        """Variant with custom data tag should resolve without errors."""
        check_ok(
            "pv: data { A: 10 B: 20 }\n"
            "myvar: variant { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myvar.A }"
        )

    def test_variant_construction_type(self):
        """Constructing a variant returns the variant type."""
        program = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert vt is not None

    def test_variant_match_exhaustiveness(self):
        """Missing case without else is an error for variants."""
        errors = check_errors(
            "myvar: variant { a: i64\n b: u8\n c: null }\n"
            "main: function is {\n"
            "  x: myvar.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  }\n"
            "}"
        )
        assert any(
            "exhaustive" in e.msg.lower() or "missing" in e.msg.lower() for e in errors
        )

    def test_variant_match_with_else(self):
        """Else clause covers remaining subtypes for variants."""
        check_ok(
            "myvar: variant { a: i64\n b: u8\n c: null }\n"
            "main: function is {\n"
            "  x: myvar.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            '  } else print "other"\n'
            "}"
        )

    def test_variant_in_variant(self):
        """Nested variant (both valtypes) should be allowed."""
        check_ok(
            "inner: variant { x: i64\n y: null }\n"
            "outer: variant { a: inner\n b: null }\n"
            "main: function is { x: outer.b }"
        )

    def test_variant_enum_pattern(self):
        """All-null subtypes (enum pattern) should work."""
        check_ok(
            "mode: variant { READ: null\n WRITE: null\n EXEC: null }\n"
            "main: function is { x: mode.READ }"
        )


class TestSpecs:
    """Tests for specs (function pointer types) — Phase 20."""

    def test_spec_resolves_to_function_type(self):
        """A spec (function without body) resolves to a FUNCTION type."""
        program = check_ok(
            "binop: function {a: i64 b: i64} out i64\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "binop")
        assert t is not None
        assert t.typetype == ZTypeType.FUNCTION

    def test_take_on_function_succeeds(self):
        """Taking a function name produces a function reference."""
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "main: function is { cb: add.take }"
        )

    def test_take_on_spec_is_error(self):
        """Taking a spec name is an error (specs are types, not values)."""
        errors = check_errors(
            "binop: function {a: i64 b: i64} out i64\n"
            "main: function is { cb: binop.take }"
        )
        assert any("spec" in e.msg.lower() for e in errors)

    def test_function_ref_is_valtype(self):
        """Function references are value types — .take doesn't invalidate."""
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "main: function is {\n"
            "  cb1: add.take\n"
            "  cb2: add.take\n"
            "}"
        )

    def test_structural_equivalence(self):
        """Different-named functions with same signature are compatible with a spec."""
        check_ok(
            "binop: function {a: i64 b: i64} out i64\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            "main: function is { apply f: add.take a: 3 b: 4 }"
        )

    def test_incompatible_signatures_error(self):
        """Mismatched function signatures produce type errors."""
        errors = check_errors(
            "binop: function {a: i64 b: i64} out i64\n"
            "negate: function {x: i64} out i64 is { return 0 - x }\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            "main: function is { apply f: negate.take a: 3 b: 4 }"
        )
        assert any("mismatch" in e.msg.lower() for e in errors)

    def test_function_ref_as_parameter(self):
        """Pass a function reference as a callback parameter."""
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "apply: function {f: add a: i64 b: i64} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            "main: function is { apply f: add.take a: 3 b: 4 }"
        )

    def test_function_ref_local_variable(self):
        """Assign a function reference to a local variable."""
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "main: function is {\n"
            "  cb: add.take\n"
            "  cb a: 1 b: 2\n"
            "}"
        )

    def test_function_ref_reassignment(self):
        """Reassign a local function reference variable."""
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "sub: function {a: i64 b: i64} out i64 is { return a - b }\n"
            "main: function is {\n"
            "  cb: add.take\n"
            "  cb = sub.take\n"
            "}"
        )

    def test_record_with_spec_field_in_is(self):
        """Record with spec (function without body) in 'is' section becomes a field."""
        check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "calculator: record {\n"
            "    x: i64\n"
            "    callback: function {a: i64 b: i64} out i64\n"
            "}\n"
            "main: function is {\n"
            "  c: calculator x: 5 callback: add.take\n"
            "}"
        )

    def test_record_with_function_in_as(self):
        """Record with function in 'as' section does NOT create a field."""
        program = check_ok(
            "myrec: record {\n"
            "    x: i64\n"
            "} as {\n"
            "    helper: function {a: i64} out i64 is { return a + 1 }\n"
            "}\n"
            "main: function is { r: myrec x: 5 }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "myrec")
        assert t is not None
        create_t = t.children.get(":meta.create")
        assert create_t is not None
        assert "x" in create_t.children
        assert "helper" not in create_t.children


class TestDefaults:
    def test_numeric_default_resolves_i64(self):
        """Numeric default '0' resolves to i64 type."""
        program = check_ok(
            "greet: function {a: 0} out i64 is { return a }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert "a" in t.children
        assert t.children["a"].name == "i64"

    def test_numeric_default_42(self):
        """Numeric default '42' resolves to i64 type."""
        program = check_ok(
            "greet: function {a: 42} out i64 is { return a }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert t.children["a"].name == "i64"

    def test_param_defaults_populated_numeric(self):
        """param_defaults populated for numeric defaults."""
        program = check_ok(
            "greet: function {a: 0 b: 42} out i64 is { return a + b }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert t.param_defaults == {"a": "0", "b": "42"}

    def test_function_ref_default_detected(self):
        """Function reference default detected (function with body)."""
        program = check_ok(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "apply: function {f: add} out i64 is {\n"
            "  result: f a: 1 b: 2\n"
            "  return result\n"
            "}\n"
            "main: function is { apply }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "apply")
        assert t is not None
        assert "f" in t.param_defaults
        assert t.param_defaults["f"] == "add"

    def test_spec_no_default(self):
        """Spec (function without body) does NOT produce a default."""
        program = check_ok(
            "binop: function {a: i64 b: i64} out i64\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            "main: function is { apply f: add.take a: 1 b: 2 }\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "apply")
        assert t is not None
        assert "f" not in t.param_defaults

    def test_call_with_missing_defaulted_arg_no_error(self):
        """Call omitting an arg that has a default produces no type error."""
        check_ok(
            "greet: function {a: 0} out i64 is { return a }\n"
            "main: function is { greet }"
        )

    def test_record_with_numeric_default_field(self):
        """Record field with numeric default stores it on the type."""
        program = check_ok(
            "myrec: record {\n"
            "    x: i64\n"
            "    y: 0\n"
            "}\n"
            "main: function is { r: myrec x: 5 }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "myrec")
        assert t is not None
        assert t.param_defaults == {"y": "0"}
        # defaults propagate to constructor
        create_t = t.children.get(":meta.create")
        assert create_t is not None
        assert create_t.param_defaults == {"y": "0"}

    def test_type_name_no_default(self):
        """A type name like 'i64' does NOT produce a default."""
        program = check_ok(
            "greet: function {a: i64} out i64 is { return a }\n"
            "main: function is { greet a: 5 }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert "a" not in t.param_defaults
