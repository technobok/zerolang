"""
Tests for the type checker (ztypecheck)
"""

import os


from conftest import make_parser_vfs
from zparser import Parser
from ztypecheck import typecheck, TypeChecker
from ztypes import (
    ZTypeType,
    ZParamOwnership,
    ZOwnership,
    ZLockState,
    ZVariable,
    ZNaming,
    LockEntry,
    TAG_ORIGIN,
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


class TestNonRuntimeTypes:
    def test_null_assignment_error(self):
        """Cannot assign null directly to a variable."""
        errors = check_errors("main: function is { x: null }")
        assert any("null" in e.msg for e in errors)

    def test_never_assignment_error(self):
        """Cannot assign never (return result) to a variable."""
        errors = check_errors(
            "f: function out i64 is { return 42 }\nmain: function is { x: return 0 }"
        )
        assert any("never" in e.msg for e in errors)

    def test_null_as_param_type_error(self):
        """Cannot use null as a parameter type."""
        errors = check_errors("f: function {x: null} is {}\nmain: function is {}")
        assert any("null" in e.msg for e in errors)

    def test_never_as_param_type_error(self):
        """Cannot use never as a parameter type."""
        errors = check_errors("f: function {x: never} is {}\nmain: function is {}")
        assert any("never" in e.msg for e in errors)

    def test_null_as_return_type_error(self):
        """Cannot use null as a return type."""
        errors = check_errors("f: function out null is {}\nmain: function is {}")
        assert any("null" in e.msg for e in errors)

    def test_never_as_return_type_error(self):
        """Cannot use never as a return type."""
        errors = check_errors("f: function out never is {}\nmain: function is {}")
        assert any("never" in e.msg for e in errors)

    def test_null_in_union_is_ok(self):
        """Null as a union subtype (eg. option.none) is fine."""
        check_ok(
            "myopt: union { some: i64\n none: null }\n"
            "main: function is { x: myopt.none }"
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
        ret = lt.return_type
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


class TestBareBlockScope:
    def test_bare_block_scopes_variable(self):
        """Variable defined inside a bare block is not visible outside."""
        vfs, name = make_parser_vfs(
            'main: function is {\n  { x: 42 }\n  print "\\{x}"\n}',
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = Parser(vfs, name)
        result = p.parse()
        assert isinstance(result, zast.Error)
        assert "x" in result.msg

    def test_bare_block_does_not_shadow_outer(self):
        """Outer variable remains accessible after a bare block."""
        check_ok('main: function is {\n  x: 10\n  { y: 20 }\n  print "\\{x}"\n}')

    def test_bare_block_reads_outer_scope(self):
        """Code inside a bare block can read outer variables."""
        check_ok('main: function is {\n  x: 42\n  { print "\\{x}" }\n}')


class TestImplicitReturn:
    def test_implicit_return_integer(self):
        """Function implicitly returns an integer."""
        check_ok("f: function out i64 is { 42 }\nmain: function is {}")

    def test_implicit_return_expression(self):
        """Arithmetic expression as implicit return."""
        check_ok(
            "f: function {a: i64 b: i64} out i64 is { a + b }\nmain: function is {}"
        )

    def test_implicit_return_type_mismatch(self):
        """Implicit return type doesn't match declared return type."""
        errors = check_errors(
            'f: function out i64 is { "hello" }\nmain: function is {}'
        )
        assert any("implicit return type" in e.msg for e in errors)

    def test_implicit_return_if_expression(self):
        """if-expression in tail position as implicit return."""
        check_ok(
            "f: function {n: i64} out i64 is {\n"
            "  if n > 0 then n else 0\n"
            "}\nmain: function is {}"
        )

    def test_implicit_return_void_ignores(self):
        """Void function ignores last expression value."""
        check_ok("f: function is { 42 }\nmain: function is {}")

    def test_explicit_return_still_works(self):
        """Explicit return continues to work."""
        check_ok("f: function out i64 is { return 42 }\nmain: function is {}")

    def test_implicit_return_bare_block(self):
        """Bare block in tail position provides implicit return."""
        check_ok("f: function out i64 is { { 42 } }\nmain: function is {}")

    def test_implicit_return_mixed(self):
        """Early explicit return in branch, implicit return at end."""
        check_ok(
            "f: function {n: i64} out i64 is {\n"
            "  if n <= 0 then return 0\n"
            "  n + 1\n"
            "}\nmain: function is {}"
        )

    def test_implicit_return_assignment_tail_error(self):
        """Assignment in tail position with out type is not an implicit return."""
        # Assignment type is null, which doesn't match i64
        # This should not error because the function may have explicit returns elsewhere
        # (no control flow analysis yet — see Phase 48b)
        check_ok(
            "f: function {n: i64} out i64 is {\n"
            "  if n <= 0 then return 0\n"
            "  x: n + 1\n"
            "}\nmain: function is {}"
        )

    def test_all_branches_return_explicitly(self):
        """All branches have explicit return — no implicit return error."""
        check_ok(
            "f: function {n: i64} out i64 is {\n"
            "  if n < 0 then return 0 - n else return n\n"
            "}\nmain: function is {}"
        )


class TestMatchExpression:
    def test_match_as_expression_simple(self):
        """Simple enum match assigned to a variable."""
        check_ok(
            "north: 0\nsouth: 1\n"
            "f: function {d: i64} out i64 is {\n"
            "  match d case north then 10 case south then 20 else 30\n"
            "}\nmain: function is {}"
        )

    def test_match_as_expression_union(self):
        """Union match assigned to a variable."""
        check_ok(
            "shape: union { circle: i64\n square: i64 }\n"
            "area: function {s: shape} out i64 is {\n"
            "  match s case circle then 314 case square then 100\n"
            "}\nmain: function is {}"
        )

    def test_match_branch_type_mismatch(self):
        """Incompatible branch types in match-expression."""
        errors = check_errors(
            "north: 0\nsouth: 1\n"
            "f: function {d: i64} out i64 is {\n"
            '  match d case north then 10 case south then "bad" else 30\n'
            "}\nmain: function is {}"
        )
        assert any("incompatible branch types" in e.msg for e in errors)

    def test_match_non_exhaustive_no_value(self):
        """Non-exhaustive match without else does not produce a value type."""
        check_ok(
            "north: 0\nsouth: 1\n"
            "main: function is {\n"
            "  x: 0\n"
            '  match x case north then print "N"\n'
            "}"
        )

    def test_match_all_branches_return(self):
        """All branches return explicitly — match type is never."""
        check_ok(
            "north: 0\nsouth: 1\n"
            "f: function {d: i64} out i64 is {\n"
            "  match d case north then return 10 case south then return 20 else return 30\n"
            "}\nmain: function is {}"
        )

    def test_match_as_implicit_return(self):
        """Match expression as implicit return of function."""
        check_ok(
            "north: 0\nsouth: 1\n"
            "f: function {d: i64} out i64 is {\n"
            "  match d case north then 10 case south then 20 else 30\n"
            "}\nmain: function is {}"
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
            "    x: 0.0\n"
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
        ret = scale.return_type
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
        assert any("return type mismatch" in e.msg for e in errors)

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
        from ztypes import ZType

        t = ZType(name="i64", typetype=ZTypeType.RECORD, parent=None)
        v = ZVariable(ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
        assert v.locks == []
        assert v.held_locks == []

    def test_zvariable_with_lock(self):
        from ztypes import ZType

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
        assert func.return_ownership == ZParamOwnership.BORROW
        assert func.param_ownership["a"] == ZParamOwnership.LOCK

    def test_return_type_no_ownership(self):
        """Return type without annotation should not have return_ownership set."""
        program = check_ok("f: function out i64 is { return 42 }\nmain: function is {}")
        func = program.units["test"].body["f"]
        assert isinstance(func, zast.Function)
        assert func.return_ownership is None


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
        assert ftype.return_ownership == ZParamOwnership.BORROW
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
            "point: record { x: 0.0\n y: 0.0 }\nmain: function is { p: point }"
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
        """After x.take, x should be invalid (ownership transferred)."""
        errors = check_errors("main: function is {\n  x: 42\n  y: x.take\n  z: x\n}")
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )


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
        from ztypes import ZType

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
        program = check_ok("myclass: class { x: 0 }\nmain: function is { c: myclass }")
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert ct is not None
        assert ct.typetype == ZTypeType.CLASS

    def test_class_is_reftype(self):
        """Classes should be tagged as reftype (is_valtype=False)."""
        program = check_ok("myclass: class { x: 0 }\nmain: function is { c: myclass }")
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert ct is not None
        assert ct.is_valtype is False

    def test_class_fields_resolved(self):
        """Class fields should be resolved as children of the class type."""
        program = check_ok(
            "myclass: class { x: 0\n y: 0.0 }\nmain: function is { c: myclass }"
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
            "myclass: class { x: 0 } as {\n"
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
            "myclass: class { x: 0 } as {\n"
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
            "myclass: class { x: 0 } as {\n"
            "  clone: function {c: this} out type is { return c }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        clone_fn = ct.children["clone"]
        ret = clone_fn.return_type
        assert ret is ct


class TestClassConstruction:
    """Test class construction type checking."""

    def test_class_construction_returns_class_type(self):
        """Calling a class type creates an instance of that type."""
        check_ok("myclass: class { x: i64 }\nmain: function is { c: myclass x: 5 }")

    def test_class_bare_name_construction(self):
        """A bare class name creates a zero-initialized instance."""
        check_ok("myclass: class { x: 0 }\nmain: function is { c: myclass }")


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
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )

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
            "myclass: class { x: 0 }\n"
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
        """string should resolve as ZTypeType.CLASS."""
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
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )

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
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )

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
        """All data types should have a .tag subtype (monomorphized tag record)."""
        program = check_ok("mydata: data { 1 2 3 }\nmain: function is { x: mydata.0 }")
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert "tag" in dt.children
        tag = dt.children["tag"]
        assert tag.typetype == ZTypeType.RECORD
        assert tag.generic_origin is TAG_ORIGIN
        assert tag.name == "tag__i64"

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

    def test_tag_is_generic_record(self):
        """The system 'tag' type is a generic record."""
        program = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check()
        tag = tc._resolve_unit_name("system", "tag")
        assert tag is not None
        assert tag.typetype == ZTypeType.RECORD
        assert tag.isgeneric is True
        assert "t" in tag.generic_params

    def test_data_tag_returns_monomorphized_tag(self):
        """data.tag returns tag__element_type with generic_origin='tag'."""
        program = check_ok(
            "mydata: data { A: 0 B: 1 }\nmain: function is { x: mydata.A }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        tag = dt.children["tag"]
        assert tag.generic_origin is TAG_ORIGIN
        assert tag.name == "tag__i64"
        assert tag.parent is dt

    def test_u16_tag_resolves_for_union(self):
        """u16.tag resolves and works in union as block."""
        check_ok(
            "myunion: union { X: i64\n Y: null } as { tag: u16.tag }\n"
            "main: function is {\n"
            "  v: myunion.X 10\n"
            "  match (v) case X then {\n"
            '    print "x"\n'
            "  } case Y then {\n"
            '    print "y"\n'
            "  }\n"
            "}"
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


class TestNumericCasting:
    def test_dotted_numeric_u32(self):
        """x: 0.u32 resolves, type is u32."""
        program = check_ok("main: function is { x: 0.u32 }")
        tc = TypeChecker(program)
        tc.check()

    def test_dotted_numeric_i8(self):
        """x: 42.i8 resolves, type is i8."""
        program = check_ok("main: function is { x: 42.i8 }")
        tc = TypeChecker(program)
        tc.check()

    def test_dotted_numeric_hex(self):
        """x: 0xff.u16 resolves, type is u16."""
        program = check_ok("main: function is { x: 0xff.u16 }")
        tc = TypeChecker(program)
        tc.check()

    def test_range_error_i8(self):
        """2000.i8 produces error."""
        errors = check_errors("main: function is { x: 2000.i8 }")
        assert any("out of range" in e.msg for e in errors)

    def test_concat_range_error(self):
        """2000i8 (concatenated) also errors."""
        errors = check_errors("main: function is { x: 2000i8 }")
        assert any("out of range" in e.msg for e in errors)

    def test_overflow_decimal_i64(self):
        """18446744073709551615 (no cast) errors: out of range for i64."""
        errors = check_errors("main: function is { x: 18446744073709551615 }")
        assert any("out of range" in e.msg for e in errors)

    def test_dotted_default_param(self):
        """a: 0.u32 as function default: type=u32, default='0'."""
        program = check_ok(
            "greet: function {a: 0.u32} out u32 is { return a }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert t.param_defaults == {"a": "0"}

    def test_dotted_default_record_field(self):
        """Record with x: 0.u32 field default."""
        program = check_ok(
            "myrec: record {\n"
            "    x: i64\n"
            "    y: 0.u32\n"
            "}\n"
            "main: function is { r: myrec x: 5 }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "myrec")
        assert t is not None
        assert t.param_defaults == {"y": "0"}


class TestProtocols:
    def test_protocol_resolves(self):
        """Protocol definition creates PROTOCOL ZType with spec children."""
        program = check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "reader")
        assert t is not None
        assert t.typetype == ZTypeType.PROTOCOL
        assert t.is_valtype is False
        assert "read" in t.children
        assert t.children["read"].typetype == ZTypeType.FUNCTION

    def test_protocol_conformance_ok(self):
        """Record with correct methods passes conformance check."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )

    def test_protocol_conformance_missing(self):
        """Record missing a spec method errors."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any("missing method 'read'" in e.msg for e in errors)

    def test_protocol_as_param_type(self):
        """Function accepting protocol type resolves correctly."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n"
            "    return result\n"
            "}\n"
            "main: function is {}"
        )

    def test_protocol_instance_via_dotted_path(self):
        """f.myreader resolves to PROTOCOL type."""
        program = check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            "}\n"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "myfile")
        assert t is not None
        assert "myreader" in t.children
        assert t.children["myreader"].typetype == ZTypeType.PROTOCOL

    def test_protocol_is_field(self):
        """Protocol in 'is' block becomes instance field."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "wrapper: record {\n"
            "    r: reader\n"
            "}\n"
            "main: function is {}"
        )

    def test_protocol_signature_matching_ok(self):
        """Matching signatures pass conformance check."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )

    def test_protocol_signature_param_count_mismatch(self):
        """Error when impl has different param count than spec."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this} out i64 is {\n"
            "        return f.fd\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any("0 param" in e.msg and "expects 1" in e.msg for e in errors)

    def test_protocol_signature_param_type_mismatch(self):
        """Error when impl param type differs from spec."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: bool} out i64 is {\n"
            "        return f.fd\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any(
            "'b'" in e.msg and "'bool'" in e.msg and "'i64'" in e.msg for e in errors
        )

    def test_protocol_signature_return_type_mismatch(self):
        """Error when impl return type differs from spec."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out bool is {\n"
            "        return 0.bool\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any("returns 'bool'" in e.msg and "'i64'" in e.msg for e in errors)

    def test_protocol_signature_no_return_both_ok(self):
        """Both spec and impl with no return type is fine."""
        check_ok(
            "worker: protocol {\n"
            "    work: function {:this}\n"
            "}\n"
            "myworker: record {\n"
            "    x: i64\n"
            "} as {\n"
            "    w: worker\n"
            "    work: function {w: this} is {}\n"
            "}\n"
            "main: function is { w: myworker x: 1 }"
        )

    def test_protocol_borrow_lock_source(self):
        """Source is locked after protocol borrow — second borrow errors."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            "    b: f.borrow\n"
            "}\n"
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_protocol_double_borrow_error(self):
        """Double protocol borrow of same source errors."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r1: f.myreader\n"
            "    r2: f.myreader\n"
            "}\n"
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_protocol_temp_no_lock(self):
        """Temp protocol usage (f.myreader passed directly) doesn't lock source."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n"
            "    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    x: use_reader r: f.myreader\n"
            "    y: use_reader r: f.myreader\n"
            "}\n"
        )

    def test_protocol_has_create(self):
        """Protocol type has `create` child (FUNCTION)."""
        program = check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "reader")
        assert t is not None
        assert "create" in t.children
        assert t.children["create"].typetype == ZTypeType.FUNCTION

    def test_protocol_create_typechecks(self):
        """reader.create from: f.take type-checks, result is PROTOCOL."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.create from: f.take\n"
            "}\n"
        )

    def test_protocol_create_nonconforming_error(self):
        """from: with non-conforming type errors."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "other: record { x: i64 }\n"
            "main: function is {\n"
            "    o: other x: 1\n"
            "    r: reader.create from: o.take\n"
            "}\n"
        )
        assert any("does not conform" in e.msg for e in errors)

    def test_protocol_create_missing_from_error(self):
        """reader.create with wrong arg name errors."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.create x: f.take\n"
            "}\n"
        )
        assert any("requires 'from:'" in e.msg for e in errors)

    def test_generic_protocol_no_create(self):
        """Generic (unmonomorphized) protocol has no `create`."""
        program = check_ok(
            "myproto: protocol {\n"
            "    t: any.generic\n"
            "    get: function {:this} out t\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        t = tc._resolved.get("test.myproto")
        assert t is not None
        assert t.isgeneric is True
        assert "create" not in t.children

    def test_protocol_has_take(self):
        """Protocol type has `take` child (FUNCTION)."""
        program = check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "reader")
        assert t is not None
        assert "take" in t.children
        assert t.children["take"].typetype == ZTypeType.FUNCTION

    def test_protocol_take_typechecks(self):
        """reader.take from: f.take type-checks, result is PROTOCOL."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.take from: f.take\n"
            "}\n"
        )

    def test_protocol_take_nonconforming_error(self):
        """from: with non-conforming type errors for take."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "other: record { x: i64 }\n"
            "main: function is {\n"
            "    o: other x: 1\n"
            "    r: reader.take from: o.take\n"
            "}\n"
        )
        assert any("does not conform" in e.msg for e in errors)

    def test_protocol_has_borrow(self):
        """Protocol type has `borrow` child (FUNCTION)."""
        program = check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "reader")
        assert t is not None
        assert "borrow" in t.children
        assert t.children["borrow"].typetype == ZTypeType.FUNCTION

    def test_protocol_borrow_typechecks(self):
        """reader.borrow from: f.lock type-checks, result is PROTOCOL."""
        check_ok(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.borrow from: f.lock\n"
            "}\n"
        )

    def test_protocol_borrow_nonconforming_error(self):
        """from: with non-conforming type errors for borrow."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "other: record { x: i64 }\n"
            "main: function is {\n"
            "    o: other x: 1\n"
            "    r: reader.borrow from: o.lock\n"
            "}\n"
        )
        assert any("does not conform" in e.msg for e in errors)

    def test_protocol_borrow_missing_from_error(self):
        """reader.borrow with wrong arg name errors."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.borrow x: f.lock\n"
            "}\n"
        )
        assert any("requires 'from:'" in e.msg for e in errors)

    def test_protocol_borrow_locks_source(self):
        """Source is locked after borrow — second borrow errors (like obj.label)."""
        errors = check_errors(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.borrow from: f.lock\n"
            "    b: f.borrow\n"
            "}\n"
        )
        assert any(
            "locked" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_generic_protocol_no_take(self):
        """Generic (unmonomorphized) protocol has no `take`."""
        program = check_ok(
            "myproto: protocol {\n"
            "    t: any.generic\n"
            "    get: function {:this} out t\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        t = tc._resolved.get("test.myproto")
        assert t is not None
        assert t.isgeneric is True
        assert "take" not in t.children

    def test_generic_protocol_no_borrow(self):
        """Generic (unmonomorphized) protocol has no `borrow`."""
        program = check_ok(
            "myproto: protocol {\n"
            "    t: any.generic\n"
            "    get: function {:this} out t\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        t = tc._resolved.get("test.myproto")
        assert t is not None
        assert t.isgeneric is True
        assert "borrow" not in t.children


class TestGenerics:
    """Tests for generic type resolution and monomorphization."""

    def test_generic_record_resolution(self):
        """Record with t: any.generic puts t in generic_params, not children."""
        program = check_ok(
            "myrec: record { x: i64 } as { t: any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric is True
        assert "t" in myrec.generic_params
        assert "t" not in myrec.children
        assert "x" in myrec.children

    def test_generic_record_with_generic_field_ref(self):
        """Record field referencing generic param: x: t resolves to GENERIC_PARAM."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric
        assert "x" in myrec.children
        assert myrec.children["x"].typetype == ZTypeType.GENERIC_PARAM

    def test_generic_union_resolution(self):
        """Union with t: any.generic detects generic params correctly."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myopt = tc._resolved.get("test.myopt")
        assert myopt is not None
        assert myopt.isgeneric is True
        assert "t" in myopt.generic_params
        assert "some" in myopt.children
        assert "none" in myopt.children
        assert "t" not in myopt.children

    def test_generic_union_subtype_is_generic_param_ref(self):
        """Union subtype referencing generic param: some: t is GENERIC_PARAM."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myopt = tc._resolved.get("test.myopt")
        assert myopt is not None
        assert myopt.children["some"].typetype == ZTypeType.GENERIC_PARAM

    def test_multiple_generic_params(self):
        """Record with multiple generic params."""
        program = check_ok(
            "mypair: record { x: a\n y: b } as { a: any.generic\n b: any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        pair = tc._resolved.get("test.mypair")
        assert pair is not None
        assert pair.isgeneric
        assert "a" in pair.generic_params
        assert "b" in pair.generic_params
        assert "x" in pair.children
        assert "y" in pair.children

    def test_generic_function_resolution(self):
        """Function with generic param in 'as' clause: t: any.generic."""
        program = check_ok(
            "myfn: function as { t: any.generic } in { x: t } out t\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myfn = tc._resolved.get("test.myfn")
        assert myfn is not None
        assert myfn.isgeneric is True
        assert "t" in myfn.generic_params
        assert "x" in myfn.children
        assert myfn.children["x"].typetype == ZTypeType.GENERIC_PARAM

    def test_generic_function_multiple_params(self):
        """Function with multiple generic params in 'as'."""
        program = check_ok(
            "myfn: function as { t: any.generic\n u: any.generic } "
            "in { x: t\n y: u } out t\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myfn = tc._resolved.get("test.myfn")
        assert myfn is not None
        assert myfn.isgeneric is True
        assert "t" in myfn.generic_params
        assert "u" in myfn.generic_params
        assert "x" in myfn.children
        assert "y" in myfn.children

    def test_generic_function_any_clause_order(self):
        """Function with 'as' after 'out' resolves correctly."""
        program = check_ok(
            "myfn: function in { x: t } out t as { t: any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myfn = tc._resolved.get("test.myfn")
        assert myfn is not None
        assert myfn.isgeneric is True
        assert "t" in myfn.generic_params

    def test_generic_param_in_function_in_error(self):
        """Generic params in function 'in' section should error."""
        errors = check_errors(
            "myfn: function { t: any.generic\n x: t } out t\nmain: function is {}"
        )
        assert any(
            "generic parameters must be declared in the 'as' section" in e.msg.lower()
            for e in errors
        )

    def test_method_with_as_error(self):
        """Method (function with 'this' type) cannot have 'as' clause."""
        errors = check_errors(
            "myrec: record { x: i64 } as {\n"
            "  meth: function as { t: any.generic } in { self: this\n val: t } out t is { val }\n"
            "}\nmain: function is { r: myrec x: 1 }"
        )
        assert any(
            "methods cannot declare generic parameters" in e.msg.lower() for e in errors
        )

    def test_static_function_in_type_as_with_own_as(self):
        """Static function (no 'this') in type's 'as' block can have own 'as'."""
        program = check_ok(
            "myrec: record { x: i64 } as {\n"
            "  helper: function as { t: any.generic } in { val: t } out i64 is { 0 }\n"
            "}\nmain: function is { r: myrec x: 1 }"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        helper = tc._resolved.get("test.myrec.helper")
        assert helper is not None
        assert helper.isgeneric is True
        assert "t" in helper.generic_params

    def test_option_some_infers_i64(self):
        """option.some 42 infers t=i64."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.some 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name
        assert mono.generic_origin is not None

    def test_option_none_explicit_type_arg(self):
        """option.none i32 with explicit type argument."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.none i32 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i32" in mono.name

    def test_same_generic_different_types(self):
        """Same generic instantiated with different types creates different monomorphizations."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is {\n"
            "    x: myopt.some 42\n"
            "    y: myopt.some 3.14\n"
            "}"
        )
        assert len(program.mono_types) >= 2
        names = {m.name for m, _ in program.mono_types}
        assert any("i64" in n for n in names)
        assert any("f64" in n for n in names)

    def test_duplicate_instantiation_cached(self):
        """Duplicate instantiation with same type returns cached type."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is {\n"
            "    x: myopt.some 1\n"
            "    y: myopt.some 2\n"
            "}"
        )
        # should produce only one monomorphization for i64
        i64_monos = [m for m, _ in program.mono_types if "i64" in m.name]
        assert len(i64_monos) == 1

    def test_system_option_available(self):
        """System optionval type is available via core for valtypes."""
        program = check_ok("main: function is { x: optionval.some 42 }")
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert mono.generic_origin is not None

    def test_monomorphized_union_has_tag(self):
        """Monomorphized union has proper :tag enum."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.some 42 }"
        )
        mono, _ = program.mono_types[0]
        assert ":tag" in mono.children
        tag_type = mono.children[":tag"]
        assert tag_type.typetype == ZTypeType.ENUM
        assert "some" in tag_type.children
        assert "none" in tag_type.children

    def test_monomorphized_union_concrete_subtypes(self):
        """Monomorphized union replaces generic param with concrete type."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.some 42 }"
        )
        mono, _ = program.mono_types[0]
        some_type = mono.children.get("some")
        assert some_type is not None
        assert some_type.name == "i64"
        assert some_type.typetype != ZTypeType.GENERIC_PARAM

    def test_error_generic_union_no_args(self):
        """Using generic union subtype with no inferrable args emits error."""
        errors = check_errors(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.some }"
        )
        assert any("cannot infer type arguments" in e.msg for e in errors)

    def test_error_generic_union_none_no_args(self):
        """Using generic union null subtype with no type arg emits error."""
        errors = check_errors(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.none }"
        )
        assert any("cannot infer type arguments" in e.msg for e in errors)

    def test_generic_union_from_infers_type(self):
        """option.some from: 42 infers t=i64 via from: syntax."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.some from: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name
        assert mono.generic_origin is not None

    def test_generic_union_explicit_type_and_from(self):
        """option.some t: i64 from: 42 with explicit generic param and from: value."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is { x: myopt.some t: i64 from: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name

    def test_generic_union_from_with_different_types(self):
        """from: syntax with different types creates different monomorphizations."""
        program = check_ok(
            "myopt: union { some: t\n none: null } as { t: any.generic }\n"
            "main: function is {\n"
            "    x: myopt.some from: 42\n"
            "    y: myopt.some from: 3.14\n"
            "}"
        )
        assert len(program.mono_types) >= 2
        names = {m.name for m, _ in program.mono_types}
        assert any("i64" in n for n in names)
        assert any("f64" in n for n in names)

    def test_system_option_from_syntax(self):
        """System optionval type works with from: syntax."""
        program = check_ok("main: function is { x: optionval.some from: 42 }")
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert mono.generic_origin is not None

    def test_option_requires_reftype(self):
        """option.some with valtype should error (requires any.reftype)."""
        errors = check_errors("main: function is { x: option.some 42 }")
        assert any("not a reference type" in e.msg for e in errors)

    def test_optionval_requires_valtype(self):
        """optionval with reftype should error (requires any.valtype)."""
        errors = check_errors('main: function is { x: optionval.some "hello" }')
        assert any("not a value type" in e.msg for e in errors)

    def test_optionval_some_infers_i64(self):
        """optionval.some 42 infers t=i64."""
        program = check_ok("main: function is { x: optionval.some 42 }")
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name
        assert mono.typetype == ZTypeType.VARIANT

    def test_optionval_none_explicit_type(self):
        """optionval.none i32 with explicit type argument."""
        program = check_ok("main: function is { x: optionval.none i32 }")
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i32" in mono.name

    def test_optionval_is_valtype(self):
        """Monomorphized optionval is a value type."""
        program = check_ok("main: function is { x: optionval.some 42 }")
        mono, _ = program.mono_types[0]
        assert mono.is_valtype is True

    def test_option_nullable_ptr_flag(self):
        """Monomorphized option(reftype) has is_nullable_ptr set."""
        program = check_ok('main: function is { x: option.some "hello" }')
        mono, _ = program.mono_types[0]
        assert mono.is_nullable_ptr is True

    def test_box_valtype_creates_reftype(self):
        """box from: valtype creates a box reftype."""
        program = check_ok("main: function is { b: box from: 42 }")
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert mono.is_box is True
        assert mono.is_valtype is False

    def test_box_reftype_passthrough(self):
        """box from: reftype is a passthrough (type is the reftype)."""
        program = check_ok('main: function is { b: box from: "hello" }')
        # for reftype passthrough, the type is string, not box
        # no box mono type should be created
        for mono, _ in program.mono_types:
            assert not mono.is_box

    def test_box_valtype_has_inner_children(self):
        """box(valtype) has children copied from inner type for transparent access."""
        program = check_ok("main: function is { b: box from: 42 }")
        mono, _ = program.mono_types[0]
        assert mono.is_box is True
        # should have i64's operator children
        assert "+" in mono.children or len(mono.children) > 0

    def test_error_generic_record_no_args(self):
        """Using generic record with no args emits error."""
        errors = check_errors(
            "myrec: record { x: t } as { t: any.generic }\nmain: function is { r: myrec }"
        )
        assert any("cannot infer type arguments" in e.msg for e in errors)

    def test_error_generic_record_no_inferrable_args(self):
        """Using generic record with args that don't cover generic params emits error."""
        errors = check_errors(
            "myrec: record { x: t\n y: i64 } as { t: any.generic }\n"
            "main: function is { r: myrec y: 42 }"
        )
        assert any("cannot infer" in e.msg for e in errors)

    def test_generic_record_infer_from_value(self):
        """myrec x: 42 infers t=i64 from field type."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.generic }\n"
            "main: function is { r: myrec x: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name
        assert mono.children["x"].name == "i64"

    def test_generic_record_explicit_and_value(self):
        """myrec t: i64 x: 42 — both explicit type arg and value, compatible."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.generic }\n"
            "main: function is { r: myrec t: i64 x: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name

    def test_generic_record_conflict_error(self):
        """myrec t: i32 x: "hello" — conflicting types for t emits error."""
        errors = check_errors(
            "myrec: record { x: t } as { t: any.generic }\n"
            'main: function is { r: myrec t: i32 x: "hello" }'
        )
        assert any("Conflicting types" in e.msg for e in errors)

    def test_generic_record_multi_param_infer(self):
        """pair x: 42 y: "hi" infers a=i64, b=string."""
        program = check_ok(
            "mypair: record { x: a\n y: b } as { a: any.generic\n b: any.generic }\n"
            'main: function is { p: mypair x: 42 y: "hi" }'
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert mono.children["x"].name == "i64"
        assert mono.children["y"].name == "string"

    def test_generic_type_in_type_position_concrete(self):
        """(myrec t: i64) in field type position produces concrete monomorphization."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.generic }\n"
            "wrapper: record { inner: (myrec t: i64) }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        wrapper = tc._resolved.get("test.wrapper")
        assert wrapper is not None
        inner = wrapper.children.get("inner")
        assert inner is not None
        assert inner.name == "myrec_i64"
        assert inner.isgeneric is False
        assert inner.children["x"].name == "i64"

    def test_generic_type_in_type_position_partial(self):
        """(myrec t: u) in field type position produces partial instantiation."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.generic }\n"
            "wrapper: record { inner: (myrec t: u) } as { u: any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        wrapper = tc._resolved.get("test.wrapper")
        assert wrapper is not None
        inner = wrapper.children.get("inner")
        assert inner is not None
        assert inner.name == "myrec_u"
        assert inner.isgeneric is True
        assert "u" in inner.generic_params

    def test_partial_instantiation_full_monomorphize(self):
        """Wrapper with (myrec t: u) fully resolves inner when monomorphized."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.generic }\n"
            "wrapper: record { inner: (myrec t: u) } as { u: any.generic }\n"
            "main: function is { w: wrapper u: i64 inner: (myrec x: 42) }"
        )
        monos = {m.name: m for m, _ in program.mono_types}
        assert "wrapper_i64" in monos
        wrapper_mono = monos["wrapper_i64"]
        inner = wrapper_mono.children.get("inner")
        assert inner is not None
        assert inner.name == "myrec_i64"
        assert inner.isgeneric is False

    def test_error_bare_generic_in_type_position(self):
        """Using bare generic type in field position emits error."""
        errors = check_errors(
            "myrec: record { x: t } as { t: any.generic }\n"
            "wrapper: record { inner: myrec }\n"
            "main: function is { w: wrapper inner: (myrec x: 42) }"
        )
        assert any("requires type arguments" in e.msg for e in errors)

    def test_error_missing_type_arg_in_type_position(self):
        """Missing type arg in (myrec) call emits error."""
        errors = check_errors(
            "mypair: record { x: a\n y: b } as { a: any.generic\n b: any.generic }\n"
            "wrapper: record { inner: (mypair a: i64) }\n"
            'main: function is { w: wrapper inner: (mypair x: 1 y: "a") }'
        )
        assert any("Missing type argument" in e.msg for e in errors)

    def test_generic_param_in_is_error(self):
        """Generic params in is-section should error for record/union/class."""
        errors = check_errors(
            "myrec: record { t: any.generic\n x: t }\n"
            "main: function is { r: myrec x: 42 }"
        )
        assert any(
            "generic parameters must be declared in the 'as' section" in e.msg.lower()
            for e in errors
        )

    # ---- Generic Classes ----

    def test_generic_class_resolution(self):
        """Class with t: any.generic puts t in generic_params, not children."""
        program = check_ok(
            "mycls: class { x: i64 } as { t: any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        mycls = tc._resolved.get("test.mycls")
        assert mycls is not None
        assert mycls.isgeneric is True
        assert "t" in mycls.generic_params
        assert "t" not in mycls.children
        assert "x" in mycls.children

    def test_generic_class_field_uses_param(self):
        """Class field referencing generic param: val: t resolves to GENERIC_PARAM."""
        program = check_ok(
            "mycls: class { val: t } as { t: any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        mycls = tc._resolved.get("test.mycls")
        assert mycls is not None
        assert mycls.isgeneric
        assert "val" in mycls.children
        assert mycls.children["val"].typetype == ZTypeType.GENERIC_PARAM

    def test_generic_class_construction_infers(self):
        """mycls val: 42 infers t=i64 and produces monomorphized type."""
        program = check_ok(
            "mycls: class { val: t } as { t: any.generic }\n"
            "main: function is { x: mycls val: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name
        assert mono.typetype == ZTypeType.CLASS
        assert mono.isgeneric is False
        assert "val" in mono.children
        assert mono.children["val"].name == "i64"

    def test_generic_class_explicit_type_arg(self):
        """mycls t: i64 val: 42 with explicit type arg."""
        program = check_ok(
            "mycls: class { val: t } as { t: any.generic }\n"
            "main: function is { x: mycls t: i64 val: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert "i64" in mono.name
        assert mono.typetype == ZTypeType.CLASS

    def test_generic_class_is_reftype(self):
        """Monomorphized generic class is still a reference type."""
        program = check_ok(
            "mycls: class { val: t } as { t: any.generic }\n"
            "main: function is { x: mycls val: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert mono.is_valtype is False

    def test_generic_class_has_create(self):
        """Monomorphized class has :meta.create constructor."""
        program = check_ok(
            "mycls: class { val: t } as { t: any.generic }\n"
            "main: function is { x: mycls val: 42 }"
        )
        assert len(program.mono_types) >= 1
        mono, _ = program.mono_types[0]
        assert ":meta.create" in mono.children
        assert mono.children[":meta.create"].typetype == ZTypeType.FUNCTION

    def test_error_generic_class_no_args(self):
        """Bare generic class name in expression is an error."""
        errors = check_errors(
            "mycls: class { val: t } as { t: any.generic }\n"
            "main: function is { x: mycls }"
        )
        assert any("generic" in e.msg.lower() for e in errors)

    # ---- Generic Protocols ----

    def test_generic_protocol_resolution(self):
        """Protocol with t: any.generic param is generic."""
        program = check_ok(
            "myproto: protocol {\n"
            "  t: any.generic\n"
            "  get: function {:this} out t\n"
            "}\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myproto = tc._resolved.get("test.myproto")
        assert myproto is not None
        assert myproto.isgeneric is True
        assert "t" in myproto.generic_params

    def test_generic_protocol_spec_uses_param(self):
        """Spec function uses generic param type."""
        program = check_ok(
            "myproto: protocol {\n"
            "  t: any.generic\n"
            "  get: function {:this} out t\n"
            "}\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myproto = tc._resolved.get("test.myproto")
        assert myproto is not None
        get_fn = myproto.children.get("get")
        assert get_fn is not None
        ret = get_fn.return_type
        assert ret is not None
        assert ret.typetype == ZTypeType.GENERIC_PARAM

    def test_error_generic_protocol_no_args(self):
        """Bare generic protocol name in expression is an error."""
        errors = check_errors(
            "myproto: protocol {\n"
            "  t: any.generic\n"
            "  get: function {:this} out t\n"
            "}\n"
            "myrec: record { x: i64 } as { p: myproto }\n"
            "main: function is { r: myrec x: 1\n v: r.p }"
        )
        assert any("generic" in e.msg.lower() for e in errors)

    # ---- any.valtype / any.reftype constraint subtypes ----

    def test_valtype_constraint_record_ok(self):
        """Record with t: any.valtype accepts record types."""
        check_ok(
            "myrec: record { x: t } as { t: any.valtype }\n"
            "inner: record { v: i64 }\n"
            "main: function is { r: myrec x: (inner v: 1) }"
        )

    def test_valtype_constraint_i64_ok(self):
        """Record with t: any.valtype accepts numeric types."""
        check_ok(
            "myrec: record { x: t } as { t: any.valtype }\n"
            "main: function is { r: myrec x: 42 }"
        )

    def test_valtype_constraint_class_error(self):
        """Record with t: any.valtype rejects class types."""
        errors = check_errors(
            "mycls: class { v: i64 }\n"
            "myrec: record { x: t } as { t: any.valtype }\n"
            "main: function is {\n"
            "    c: mycls v: 1\n"
            "    r: myrec x: c\n"
            "}"
        )
        assert any("not a value type" in e.msg for e in errors)

    def test_valtype_constraint_union_error(self):
        """Record with t: any.valtype rejects union types."""
        errors = check_errors(
            "myunion: union { a: i64\n b: null }\n"
            "myrec: record { x: t } as { t: any.valtype }\n"
            "main: function is {\n"
            "    u: myunion.a 1\n"
            "    r: myrec x: u\n"
            "}"
        )
        assert any("not a value type" in e.msg for e in errors)

    def test_reftype_constraint_class_ok(self):
        """Record with t: any.reftype accepts class types."""
        check_ok(
            "mycls: class { v: i64 }\n"
            "myrec: record { x: t } as { t: any.reftype }\n"
            "main: function is {\n"
            "    c: mycls v: 1\n"
            "    r: myrec x: c\n"
            "}"
        )

    def test_reftype_constraint_union_ok(self):
        """Record with t: any.reftype accepts union types."""
        check_ok(
            "myunion: union { a: i64\n b: null }\n"
            "myrec: record { x: t } as { t: any.reftype }\n"
            "main: function is {\n"
            "    u: myunion.a 1\n"
            "    r: myrec x: u\n"
            "}"
        )

    def test_reftype_constraint_record_error(self):
        """Record with t: any.reftype rejects record types."""
        errors = check_errors(
            "inner: record { v: i64 }\n"
            "myrec: record { x: t } as { t: any.reftype }\n"
            "main: function is { r: myrec x: (inner v: 1) }"
        )
        assert any("not a reference type" in e.msg for e in errors)

    def test_reftype_constraint_i64_error(self):
        """Record with t: any.reftype rejects numeric types."""
        errors = check_errors(
            "myrec: record { x: t } as { t: any.reftype }\n"
            "main: function is { r: myrec x: 42 }"
        )
        assert any("not a reference type" in e.msg for e in errors)

    def test_valtype_constraint_in_generic_params(self):
        """any.valtype constraint stored correctly in generic_params."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.valtype }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric
        assert "t" in myrec.generic_params
        assert myrec.generic_params["t"].name == "any.valtype"

    def test_reftype_constraint_in_generic_params(self):
        """any.reftype constraint stored correctly in generic_params."""
        program = check_ok(
            "myrec: record { x: t } as { t: any.reftype }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric
        assert "t" in myrec.generic_params
        assert myrec.generic_params["t"].name == "any.reftype"

    def test_valtype_union_subtype_ok(self):
        """Union with t: any.valtype accepts valtypes for subtype construction."""
        check_ok(
            "myopt: union { some: t\n none: null } as { t: any.valtype }\n"
            "main: function is { x: myopt.some 42 }"
        )

    def test_valtype_union_subtype_class_error(self):
        """Union with t: any.valtype rejects class type in subtype construction."""
        errors = check_errors(
            "mycls: class { v: i64 }\n"
            "myopt: union { some: t\n none: null } as { t: any.valtype }\n"
            "main: function is {\n"
            "    c: mycls v: 1\n"
            "    x: myopt.some c\n"
            "}"
        )
        assert any("not a value type" in e.msg for e in errors)


class TestTypedefs:
    def test_record_typedef_resolves(self):
        """Record typedef with .typedef resolves and sets typedef_base."""
        program = check_ok(
            "meters: record { val: i64.typedef } as {}\n"
            "main: function is { m: meters.create from: 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        mt = tc._resolved.get("test.meters")
        assert mt is not None
        assert mt.typedef_base is not None
        assert mt.typedef_base.name == "i64"
        assert mt.is_valtype is True

    def test_typedef_has_base_methods(self):
        """Unshadowed base methods are accessible on typedef types."""
        check_ok(
            "meters: record { val: i64.typedef } as {}\n"
            "main: function is {\n"
            "    m: meters.create from: 42\n"
            "    x: m.val + 1\n"
            "}"
        )

    def test_typedef_method_shadow(self):
        """New method in 'as' shadows base method."""
        program = check_ok(
            "meters: record { val: i64.typedef } as {\n"
            "    double: function {a: this} out meters is {\n"
            "        return (meters.create from: (a.val + a.val))\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    m: meters.create from: 5\n"
            "    d: m.double\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        mt = tc._resolved.get("test.meters")
        assert mt is not None
        assert "double" in mt.children

    def test_typedef_null_hides_method(self):
        """Setting a method to null in 'as' hides it from the typedef."""
        errors = check_errors(
            "mypoint: record { x: i64 y: i64 } as {\n"
            "    sum: function {p: this} out i64 is { return p.x + p.y }\n"
            "}\n"
            "restricted: record { base: mypoint.typedef } as {\n"
            "    sum: null\n"
            "}\n"
            "main: function is {\n"
            "    r: restricted.create from: (mypoint x: 1 y: 2)\n"
            "    s: r.sum\n"
            "}"
        )
        assert any("not available" in e.msg for e in errors)

    def test_typedef_backward_compatible(self):
        """Typedef type is accepted where base type is expected."""
        check_ok(
            "meters: record { val: i64.typedef } as {}\n"
            "show: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is {\n"
            "    m: meters.create from: 42\n"
            "    y: show m\n"
            "}"
        )

    def test_typedef_not_forward_compatible(self):
        """Base type is NOT accepted where typedef is expected."""
        errors = check_errors(
            "meters: record { val: i64.typedef } as {}\n"
            "measure: function {m: meters} out i64 is { return m.val }\n"
            "main: function is {\n"
            "    x: 42\n"
            "    measure x\n"
            "}"
        )
        assert any("mismatch" in e.msg.lower() for e in errors)

    def test_typedef_multiple_fields_error(self):
        """>1 field in 'is' section is an error for typedefs."""
        errors = check_errors(
            "bad: record { a: i64.typedef b: i64 } as {}\n"
            "main: function is { x: bad.create from: 1 }"
        )
        assert any("Additional fields" in e.msg or "forbidden" in e.msg for e in errors)

    def test_typedef_kind_mismatch_error(self):
        """Record wrapping a class type gives an error."""
        errors = check_errors(
            "mycls: class { v: i64 }\n"
            "bad: record { base: mycls.typedef } as {}\n"
            "main: function is { x: bad.create from: (mycls v: 1) }"
        )
        assert any("record type" in e.msg.lower() for e in errors)

    def test_class_typedef_wrapping_protocol(self):
        """Class typedef wrapping a protocol type should pass."""
        check_ok(
            "showable: protocol {\n"
            "    show: function {:this b: i64} out i64\n"
            "}\n"
            "myshow: class { base: showable.typedef } as {}\n"
            "main: function is { }"
        )

    def test_class_typedef_wrapping_union_error(self):
        """Class typedef wrapping a union type should error (strict kind)."""
        errors = check_errors(
            "myunion: union { a: i64\n b: f64 }\n"
            "bad: class { base: myunion.typedef } as {}\n"
            "main: function is { x: bad }"
        )
        assert any("class or protocol" in e.msg.lower() for e in errors)

    def test_record_typedef_wrapping_variant_error(self):
        """Record typedef wrapping a variant type should error (strict kind)."""
        errors = check_errors(
            "myvar: variant { a: i64\n b: f64 }\n"
            "bad: record { base: myvar.typedef } as {}\n"
            "main: function is { x: bad }"
        )
        assert any("record type" in e.msg.lower() for e in errors)

    def test_typedef_has_constructors(self):
        """take/create/borrow are synthesized for typedefs."""
        program = check_ok(
            "meters: record { val: i64.typedef } as {}\n"
            "main: function is { m: meters.create from: 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        mt = tc._resolved.get("test.meters")
        assert mt is not None
        assert "take" in mt.children
        assert "create" in mt.children
        assert "borrow" in mt.children

    def test_typedef_of_typedef(self):
        """Chained typedefs, compatibility walks stack."""
        check_ok(
            "meters: record { val: i64.typedef } as {}\n"
            "height: record { h: meters.typedef } as {}\n"
            "show: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is {\n"
            "    h: height.create from: (meters.create from: 10)\n"
            "    y: show h\n"
            "}"
        )


# ---- Phase 30: Facet Tests ----


class TestFacets:
    def test_facet_resolves(self):
        """Facet definition creates FACET ZType with spec children."""
        program = check_ok(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "showable")
        assert t is not None
        assert t.typetype == ZTypeType.FACET
        assert t.is_valtype is True
        assert "show" in t.children
        assert t.children["show"].typetype == ZTypeType.FUNCTION

    def test_facet_has_constructors(self):
        """Non-generic facets get create/take/borrow constructors."""
        program = check_ok(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "showable")
        assert "create" in t.children
        assert "take" in t.children
        assert "borrow" in t.children

    def test_facet_conformance_ok(self):
        """Record with correct methods passes facet conformance check."""
        check_ok(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "point: record {\n"
            "    x: i64\n"
            "} as {\n"
            "    s: showable\n"
            "    show: function {p: this} out i64 is { return p.x }\n"
            "}\n"
            "main: function is { p: point x: 5 }"
        )

    def test_facet_conformance_missing_method(self):
        """Record missing a spec method errors."""
        errors = check_errors(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "point: record {\n"
            "    x: i64\n"
            "} as {\n"
            "    s: showable\n"
            "}\n"
            "main: function is { p: point x: 5 }"
        )
        assert any("missing method 'show'" in e.msg for e in errors)

    def test_facet_valtype_only(self):
        """Class implementing facet should error — facets are valtype only."""
        errors = check_errors(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "myclass: class {\n"
            "    x: i64\n"
            "} as {\n"
            "    s: showable\n"
            "    show: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: myclass x: 5 }"
        )
        assert any("value type" in e.msg.lower() for e in errors)

    def test_facet_create(self):
        """facet.create from: expr should type check."""
        check_ok(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "point: record {\n"
            "    x: i64\n"
            "} as {\n"
            "    s: showable\n"
            "    show: function {p: this} out i64 is { return p.x }\n"
            "}\n"
            "main: function is {\n"
            "    p: point x: 10\n"
            "    f: showable.create from: p\n"
            "}"
        )

    def test_facet_create_nonconforming_error(self):
        """facet.create with nonconforming type should error."""
        errors = check_errors(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "point: record { x: i64 } as {}\n"
            "main: function is {\n"
            "    p: point x: 10\n"
            "    f: showable.create from: p\n"
            "}"
        )
        assert any("does not conform" in e.msg for e in errors)

    def test_facet_borrow_locks_source(self):
        """facet.borrow should lock the source variable."""
        check_ok(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "point: record {\n"
            "    x: i64\n"
            "} as {\n"
            "    s: showable\n"
            "    show: function {p: this} out i64 is { return p.x }\n"
            "}\n"
            "main: function is {\n"
            "    p: point x: 10\n"
            "    f: showable.borrow from: p\n"
            "}"
        )

    def test_facet_as_param_type(self):
        """Function accepting facet type resolves correctly."""
        check_ok(
            "showable: facet {\n"
            "    show: function {:this b: i64} out i64\n"
            "}\n"
            "use_facet: function {f: showable} out i64 is {\n"
            "    return (f.show b: 5)\n"
            "}\n"
            "main: function is {}"
        )


class TestNumericGenerics:
    """Tests for numeric generic type parameters."""

    def test_numeric_generic_record_detection(self):
        """size: u64.generic detected as numeric generic param."""
        program = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric is True
        assert "size" in myrec.generic_params
        assert "size" in myrec.numeric_generic_params

    def test_numeric_generic_in_generic_params(self):
        """Constraint for numeric generic is the numeric type itself."""
        program = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.generic_params["size"].name == "u64"

    def test_numeric_generic_monomorphization(self):
        """(myrec size: 10) creates myrec_10 with u64 field."""
        program = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is { a: (myrec size: 10) x: 5 }"
        )
        mono, _ = program.mono_types[0]
        assert mono.name == "myrec_10"
        assert mono.generic_origin is not None
        # auto-synthesized field
        assert "size" in mono.children
        assert mono.children["size"].name == "u64"
        assert mono.param_defaults["size"] == "10"

    def test_numeric_generic_range_check(self):
        """Value 300 for u8 constraint produces error."""
        errors = check_errors(
            "myrec: record { x: i64 } as { size: u8.generic }\n"
            "main: function is { a: (myrec size: 300) x: 5 }"
        )
        assert any("out of range" in e.msg for e in errors)

    def test_mixed_type_and_numeric_generics(self):
        """(myarray t: i64 size: 10) creates myarray_i64_10."""
        program = check_ok(
            "myarray: record { payload: t } as { t: any.generic\n size: u64.generic }\n"
            "main: function is { a: (myarray t: i64 size: 10) payload: 42 }"
        )
        monos = [m for m, _ in program.mono_types if m.name == "myarray_i64_10"]
        assert len(monos) == 1
        mono = monos[0]
        assert "payload" in mono.children
        assert mono.children["payload"].name == "i64"
        assert "size" in mono.children
        assert mono.children["size"].name == "u64"
        assert mono.param_defaults["size"] == "10"

    def test_numeric_generic_different_values(self):
        """size 10 vs 20 produce different types."""
        program = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is {\n"
            "    a: (myrec size: 10) x: 1\n"
            "    b: (myrec size: 20) x: 2\n"
            "}"
        )
        names = [m.name for m, _ in program.mono_types]
        assert "myrec_10" in names
        assert "myrec_20" in names

    def test_numeric_generic_same_value_cached(self):
        """Same value produces same type (cache hit)."""
        program = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is {\n"
            "    a: (myrec size: 10) x: 1\n"
            "    b: (myrec size: 10) x: 2\n"
            "}"
        )
        mono_names = [
            m.name for m, _ in program.mono_types if m.name.startswith("myrec_")
        ]
        assert mono_names.count("myrec_10") == 1

    def test_numeric_generic_must_be_explicit(self):
        """Numeric params cannot be inferred from field values."""
        errors = check_errors(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is { a: myrec x: 5 }"
        )
        assert len(errors) > 0

    def test_numeric_generic_negative_value(self):
        """Negative value produces neg prefix in mangled name."""
        program = check_ok(
            "myrec: record { x: i64 } as { off: i32.generic }\n"
            "main: function is { a: (myrec off: -5) x: 1 }"
        )
        monos = [m for m, _ in program.mono_types if m.name == "myrec_neg5"]
        assert len(monos) == 1
        mono = monos[0]
        assert mono.param_defaults["off"] == "-5"

    def test_numeric_generic_auto_field(self):
        """Numeric param auto-creates field when not referenced by any child."""
        program = check_ok(
            "myrec: record { x: i64 } as { n: u32.generic }\n"
            "main: function is { a: (myrec n: 42) x: 1 }"
        )
        monos = [m for m, _ in program.mono_types if m.name == "myrec_42"]
        assert len(monos) == 1
        mono = monos[0]
        assert "n" in mono.children
        assert mono.children["n"].name == "u32"
        assert mono.param_defaults["n"] == "42"


class TestArrays:
    """Tests for array type resolution and monomorphization."""

    def test_array_creation(self):
        """array of: i64 to: 4 creates a monomorphized array type."""
        program = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in program.mono_types if "array" in m.name]
        assert len(monos) >= 1
        mono = monos[0]
        assert mono.name == "array_i64_4"
        assert mono.is_valtype is True

    def test_array_element_access(self):
        """a.0 resolves to the element type."""
        check_ok("main: function is {\n    a: (array of: i64 to: 4)\n    x: a.0\n}")

    def test_array_element_set(self):
        """a.0 = 5 is valid reassignment."""
        check_ok("main: function is {\n    a: (array of: i64 to: 4)\n    a.0 = 5\n}")

    def test_array_bounds_error(self):
        """a.4 on to: 4 array produces error."""
        errors = check_errors(
            "main: function is {\n    a: (array of: i64 to: 4)\n    x: a.4\n}"
        )
        assert any("out of bounds" in e.msg for e in errors)

    def test_array_bounds_error_on_set(self):
        """a.4 = 5 on to: 4 array produces error."""
        errors = check_errors(
            "main: function is {\n    a: (array of: i64 to: 4)\n    a.4 = 5\n}"
        )
        assert any("out of bounds" in e.msg for e in errors)

    def test_array_get_method(self):
        """.get method is synthesized and returns element type."""
        program = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in program.mono_types if m.name == "array_i64_4"]
        assert len(monos) == 1
        mono = monos[0]
        assert "get" in mono.children
        get = mono.children["get"]
        assert get.typetype == ZTypeType.FUNCTION
        ret = get.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_array_set_method(self):
        """.set method is synthesized and returns element type."""
        program = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in program.mono_types if m.name == "array_i64_4"]
        assert len(monos) == 1
        mono = monos[0]
        assert "set" in mono.children
        set_ = mono.children["set"]
        assert set_.typetype == ZTypeType.FUNCTION
        ret = set_.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_array_length_field(self):
        """.length is synthesized with correct default value."""
        program = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in program.mono_types if m.name == "array_i64_4"]
        assert len(monos) == 1
        mono = monos[0]
        assert "length" in mono.children
        assert mono.param_defaults.get("length") == "4"

    def test_array_different_lengths_different_types(self):
        """array of: i64 to: 4 and array of: i64 to: 8 are different types."""
        program = check_ok(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    b: (array of: i64 to: 8)\n"
            "}"
        )
        names = [m.name for m, _ in program.mono_types if "array" in m.name]
        assert "array_i64_4" in names
        assert "array_i64_8" in names

    def test_array_of_records(self):
        """Array of records with default constructor."""
        check_ok(
            "point: record { x: i64\n y: i64 }\n"
            "main: function is { a: (array of: point to: 3) }"
        )

    def test_data_array_method(self):
        """data.array returns matching array type."""
        program = check_ok(
            "primes: data { 2 3 5 7 11 }\nmain: function is { a: primes.array }"
        )
        monos = [m for m, _ in program.mono_types if "array" in m.name]
        assert len(monos) >= 1
        mono = monos[0]
        assert "i64" in mono.name
        assert "5" in mono.name


class TestStr:
    """Tests for str type resolution and monomorphization."""

    def test_str_creation(self):
        """str to: 32 creates a monomorphized str type."""
        program = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in program.mono_types if "str" in m.name]
        assert len(monos) >= 1
        mono = monos[0]
        assert mono.name == "str_32"

    def test_str_is_valtype(self):
        """str is a value type."""
        program = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in program.mono_types if m.name == "str_32"]
        assert len(monos) == 1
        assert monos[0].is_valtype is True

    def test_str_length_field(self):
        """.length is synthesized as u64 field."""
        program = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in program.mono_types if m.name == "str_32"]
        mono = monos[0]
        assert "length" in mono.children
        assert mono.children["length"].name == "u64"

    def test_str_capacity_field(self):
        """.capacity is synthesized with correct default value."""
        program = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in program.mono_types if m.name == "str_32"]
        mono = monos[0]
        assert "capacity" in mono.children
        assert mono.param_defaults.get("capacity") == "32"

    def test_str_string_method(self):
        """.string method is synthesized returning string type."""
        program = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in program.mono_types if m.name == "str_32"]
        mono = monos[0]
        assert "string" in mono.children
        string_method = mono.children["string"]
        assert string_method.typetype == ZTypeType.FUNCTION
        ret = string_method.return_type
        assert ret is not None
        assert ret.name == "string"

    def test_str_different_capacities_different_types(self):
        """str to: 16 and str to: 32 are different types."""
        program = check_ok(
            "main: function is {\n    a: (str to: 16)\n    b: (str to: 32)\n}"
        )
        names = [m.name for m, _ in program.mono_types if "str" in m.name]
        assert "str_16" in names
        assert "str_32" in names

    def test_str_from_string_literal(self):
        """str with from: string literal."""
        check_ok('main: function is { s: (str to: 32) from: "hello" }')

    def test_str_from_string_variable(self):
        """str with from: string variable."""
        check_ok(
            'main: function is {\n    msg: "hello"\n    s: (str to: 32) from: msg\n}'
        )

    def test_str_in_record(self):
        """str can be used as a record field (valtype)."""
        check_ok(
            "entry: record { name: (str to: 16)\n age: 0 }\n"
            'main: function is { e: entry name: ((str to: 16) from: "") }'
        )


class TestList:
    """Tests for list type resolution and monomorphization."""

    def test_list_creation(self):
        """list of: i64 creates a monomorphized list type."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if "list" in m.name]
        assert len(monos) >= 1
        mono = monos[0]
        assert mono.name == "list_i64"

    def test_list_creation_with_capacity(self):
        """list of: i64 with capacity argument type-checks."""
        check_ok("main: function is { l: (list of: i64) capacity: 10.u64 }")

    def test_list_is_reftype(self):
        """list is a reference type (not valtype)."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        assert len(monos) == 1
        assert monos[0].is_valtype is False

    def test_list_length_field(self):
        """.length is synthesized as u64 field."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "length" in mono.children
        assert mono.children["length"].name == "u64"

    def test_list_capacity_field(self):
        """.capacity is synthesized as u64 field."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "capacity" in mono.children
        assert mono.children["capacity"].name == "u64"

    def test_list_append_method(self):
        """.append is synthesized with from: parameter."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "append" in mono.children
        append = mono.children["append"]
        assert append.typetype == ZTypeType.FUNCTION
        assert "from" in append.children

    def test_list_get_method(self):
        """.get is synthesized returning element type."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "get" in mono.children
        get = mono.children["get"]
        assert get.typetype == ZTypeType.FUNCTION
        assert "i" in get.children
        ret = get.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_list_set_method(self):
        """.set is synthesized returning element type."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "set" in mono.children
        set_m = mono.children["set"]
        assert set_m.typetype == ZTypeType.FUNCTION
        assert "i" in set_m.children
        assert "val" in set_m.children

    def test_list_pop_method(self):
        """.pop is synthesized returning element type."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "pop" in mono.children
        pop = mono.children["pop"]
        assert pop.typetype == ZTypeType.FUNCTION
        ret = pop.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_list_insert_method(self):
        """.insert is synthesized with from: and at: parameters."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "insert" in mono.children
        insert = mono.children["insert"]
        assert insert.typetype == ZTypeType.FUNCTION
        assert "from" in insert.children
        assert "at" in insert.children

    def test_list_extend_method(self):
        """.extend is synthesized with from: list_T parameter."""
        program = check_ok("main: function is { l: (list of: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "list_i64"]
        mono = monos[0]
        assert "extend" in mono.children
        extend = mono.children["extend"]
        assert extend.typetype == ZTypeType.FUNCTION
        assert "from" in extend.children

    def test_list_different_element_types(self):
        """list of: i64 and list of: u64 are different types."""
        program = check_ok(
            "main: function is {\n    a: (list of: i64)\n    b: (list of: u64)\n}"
        )
        names = [m.name for m, _ in program.mono_types if "list" in m.name]
        assert "list_i64" in names
        assert "list_u64" in names


class TestMap:
    """Tests for map type resolution and monomorphization."""

    def test_map_creation(self):
        """map key: i64 value: i64 creates a monomorphized map type."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        monos = [m for m, _ in program.mono_types if "map" in m.name]
        assert len(monos) >= 1
        assert any(m.name == "map_i64_i64" for m in monos)

    def test_map_is_reftype(self):
        """map is a reference type (not valtype)."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        monos = [m for m, _ in program.mono_types if m.name == "map_i64_i64"]
        assert len(monos) == 1
        assert monos[0].is_valtype is False

    def test_map_length_field(self):
        """.length is synthesized as u64 field."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        mono = [m for m, _ in program.mono_types if m.name == "map_i64_i64"][0]
        assert "length" in mono.children
        assert mono.children["length"].name == "u64"

    def test_map_capacity_field(self):
        """.capacity is synthesized as u64 field."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        mono = [m for m, _ in program.mono_types if m.name == "map_i64_i64"][0]
        assert "capacity" in mono.children
        assert mono.children["capacity"].name == "u64"

    def test_map_set_method(self):
        """.set is synthesized with key: and value: parameters."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        mono = [m for m, _ in program.mono_types if m.name == "map_i64_i64"][0]
        assert "set" in mono.children
        set_m = mono.children["set"]
        assert set_m.typetype == ZTypeType.FUNCTION
        assert "key" in set_m.children
        assert "value" in set_m.children

    def test_map_get_method_returns_option(self):
        """.get is synthesized returning option of value type."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        mono = [m for m, _ in program.mono_types if m.name == "map_i64_i64"][0]
        assert "get" in mono.children
        get_m = mono.children["get"]
        assert get_m.typetype == ZTypeType.FUNCTION
        ret = get_m.return_type
        assert ret is not None
        assert "option" in ret.name

    def test_map_delete_method(self):
        """.delete is synthesized returning bool."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        mono = [m for m, _ in program.mono_types if m.name == "map_i64_i64"][0]
        assert "delete" in mono.children
        del_m = mono.children["delete"]
        assert del_m.typetype == ZTypeType.FUNCTION
        assert "key" in del_m.children
        ret = del_m.return_type
        assert ret is not None
        assert ret.name == "bool"

    def test_map_has_method(self):
        """.has is synthesized returning bool."""
        program = check_ok("main: function is { m: (map key: i64 value: i64) }")
        mono = [m for m, _ in program.mono_types if m.name == "map_i64_i64"][0]
        assert "has" in mono.children
        has_m = mono.children["has"]
        assert has_m.typetype == ZTypeType.FUNCTION
        ret = has_m.return_type
        assert ret is not None
        assert ret.name == "bool"

    def test_map_different_types(self):
        """Different key/value types produce different monomorphized types."""
        program = check_ok(
            "main: function is {\n"
            "    a: (map key: i64 value: i64)\n"
            "    b: (map key: string value: u64)\n"
            "}"
        )
        names = [m.name for m, _ in program.mono_types if "map" in m.name]
        assert "map_i64_i64" in names
        assert "map_string_u64" in names

    def test_map_string_key(self):
        """map with string keys type-checks."""
        check_ok(
            "main: function is {\n"
            "    m: (map key: string value: i64)\n"
            '    m.set key: "hello" value: 42\n'
            "}"
        )


class TestConstantFolding:
    """Tests for constant folding (Phase 41)."""

    @staticmethod
    def _get_rhs(program, stmt_index=0):
        """Get the RHS expression from an assignment in main's body."""
        main = program.units[program.mainunitname].body["main"]
        line = main.body.statements[stmt_index]
        sl = line.statementline
        if isinstance(sl, zast.Assignment):
            return sl.value
        return sl

    @staticmethod
    def _get_rhs_inner(program, stmt_index=0):
        """Get the inner expression from an assignment RHS."""
        main = program.units[program.mainunitname].body["main"]
        line = main.body.statements[stmt_index]
        sl = line.statementline
        if isinstance(sl, zast.Assignment):
            expr = sl.value
            if isinstance(expr, zast.Expression):
                return expr.expression
            return expr
        return sl

    def test_const_value_numeric_literal(self):
        """Numeric literal should have const_value set."""
        program = check_ok("main: function is { x: 42 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert inner.const_value == 42

    def test_const_value_binop_addition(self):
        """1 + 2 should fold to const_value == 3."""
        program = check_ok("main: function is { x: 1 + 2 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 3

    def test_const_value_binop_subtraction(self):
        """10 - 3 should fold to const_value == 7."""
        program = check_ok("main: function is { x: 10 - 3 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 7

    def test_const_value_binop_multiplication(self):
        """4 * 5 should fold to const_value == 20."""
        program = check_ok("main: function is { x: 4 * 5 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 20

    def test_const_value_binop_division(self):
        """10 / 3 should fold to const_value == 3 (truncation toward zero)."""
        program = check_ok("main: function is { x: 10 / 3 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 3

    def test_const_value_negative_division(self):
        """-7 / 2 should fold to -3 (truncation toward zero, C semantics)."""
        program = check_ok("main: function is { x: -7 / 2 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == -3

    def test_const_value_binop_comparison(self):
        """3 < 5 should fold to const_value == True."""
        program = check_ok("main: function is { x: 3 < 5 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value is True

    def test_const_value_comparison_false(self):
        """5 < 3 should fold to const_value == False."""
        program = check_ok("main: function is { x: 5 < 3 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value is False

    def test_const_value_chained(self):
        """1 + 2 + 3 should fold to const_value == 6 (left-to-right)."""
        program = check_ok("main: function is { x: 1 + 2 + 3 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 6

    def test_const_value_none_for_variables(self):
        """Variable + literal should NOT fold."""
        program = check_ok("main: function is {\n  x: 5\n  y: x + 1\n}")
        inner = self._get_rhs_inner(program, stmt_index=1)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value is None

    def test_const_value_division_by_zero(self):
        """1 / 0 should be a compile-time error."""
        errors = check_errors("main: function is { x: 1 / 0 }")
        assert any("division by zero" in e.msg.lower() for e in errors)

    def test_const_value_named_constant(self):
        """Reference to a named constant should propagate const_value."""
        program = check_ok(
            'north: 0\nmain: function is {\n  x: north\n  print "\\{x}"\n}'
        )
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert inner.const_value == 0

    def test_const_value_chained_named(self):
        """Chained named constants: a: 1, b: a + 2 -> b is 3."""
        program = check_ok(
            'a: 1\nb: a + 2\nmain: function is {\n  x: b\n  print "\\{x}"\n}'
        )
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert inner.const_value == 3

    def test_const_value_overflow_error(self):
        """255u8 + 1u8 should produce a compile-time overflow error."""
        errors = check_errors("main: function is { x: 255u8 + 1u8 }")
        assert any("overflow" in e.msg.lower() for e in errors)

    def test_const_value_f64_folded(self):
        """f64 operations should be folded."""
        program = check_ok("main: function is { x: 1.5f64 + 2.5f64 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 4.0

    def test_const_value_f32_not_folded(self):
        """f32 operations should not be folded (precision mismatch with host)."""
        program = check_ok("main: function is { x: 1.5f32 + 2.5f32 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value is None

    def test_const_value_bool_via_comparison(self):
        """Comparison results should have bool const_value (True/False)."""
        program = check_ok("main: function is {\n  x: 1 == 1\n  y: 1 == 2\n}")
        inner0 = self._get_rhs_inner(program, stmt_index=0)
        inner1 = self._get_rhs_inner(program, stmt_index=1)
        assert isinstance(inner0, zast.BinOp)
        assert inner0.const_value is True
        assert isinstance(inner1, zast.BinOp)
        assert inner1.const_value is False

    def test_const_value_propagates_through_expression(self):
        """const_value should propagate from inner Operation to Expression wrapper."""
        program = check_ok("main: function is { x: 1 + 2 }")
        rhs = self._get_rhs(program)
        assert isinstance(rhs, zast.Expression)
        assert rhs.const_value == 3

    # -- Division by zero ---

    def test_const_division_by_zero_error(self):
        """Division by constant zero should be a compile-time error."""
        errors = check_errors("main: function is { x: 10 / 0 }")
        assert any("division by zero" in e.msg.lower() for e in errors)

    def test_const_division_by_zero_f64_error(self):
        """Float division by constant zero should be a compile-time error."""
        errors = check_errors("main: function is { x: 10.0f64 / 0.0f64 }")
        assert any("division by zero" in e.msg.lower() for e in errors)

    # -- Float (f64) folding ---

    def test_f64_addition(self):
        """f64 addition should fold."""
        program = check_ok("main: function is { x: 1.5 + 2.5 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 4.0

    def test_f64_subtraction(self):
        """f64 subtraction should fold."""
        program = check_ok("main: function is { x: 5.0 - 1.5 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 3.5

    def test_f64_multiplication(self):
        """f64 multiplication should fold."""
        program = check_ok("main: function is { x: 2.0 * 3.5 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 7.0

    def test_f64_division(self):
        """f64 division should fold (no truncation)."""
        program = check_ok("main: function is { x: 7.0 / 2.0 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 3.5

    def test_f64_comparison(self):
        """f64 comparison should fold to bool."""
        program = check_ok("main: function is { x: 1.0 < 2.0 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value is True

    def test_f64_comparison_false(self):
        """f64 comparison should fold to False."""
        program = check_ok("main: function is { x: 3.0 < 2.0 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value is False

    def test_f64_chained(self):
        """Chained f64 operations should fold."""
        program = check_ok("main: function is { x: 1.0 + 2.0 + 3.0 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert inner.const_value == 6.0

    def test_f64_literal_const_value(self):
        """f64 literal should have const_value set."""
        program = check_ok("main: function is { x: 3.14 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert inner.const_value == 3.14


class TestIfExpression:
    """Tests for if-as-expression (Phase 42)."""

    def test_if_expression_basic(self):
        """Basic if-expression with compatible integer branches."""
        program = check_ok("main: function is { x: if 1 < 2 then 1 else 2 }")
        main = program.units[program.mainunitname].body["main"]
        assign = main.body.statements[0].statementline
        assert isinstance(assign, zast.Assignment)
        assert assign.type is not None
        assert assign.type.name == "i64"

    def test_if_expression_sets_ifnode_type(self):
        """If with else should set ifnode.type to common branch type."""
        program = check_ok("main: function is { x: if 1 < 2 then 10 else 20 }")
        main = program.units[program.mainunitname].body["main"]
        assign = main.body.statements[0].statementline
        expr = assign.value
        inner = expr.expression
        assert isinstance(inner, zast.If)
        assert inner.type is not None
        assert inner.type.name == "i64"

    def test_if_expression_type_mismatch(self):
        """Incompatible branch types should produce an error."""
        errors = check_errors('main: function is { x: if 1 < 2 then 1 else "hello" }')
        assert any("incompatible" in e.msg.lower() for e in errors)

    def test_if_expression_missing_else(self):
        """If-expression without else should produce an exhaustiveness error."""
        errors = check_errors("main: function is { x: if 1 < 2 then 1 }")
        assert any("exhaustive" in e.msg.lower() for e in errors)

    def test_if_expression_missing_else_reassignment(self):
        """Exhaustiveness check also applies to reassignment."""
        errors = check_errors("main: function is {\n  x: 0\n  x = if 1 < 2 then 1\n}")
        assert any("exhaustive" in e.msg.lower() for e in errors)

    def test_if_expression_return_exemption(self):
        """Branch ending in return is exempt from type matching."""
        check_ok(
            "f: function {n: i64} out i64 is {\n"
            "  x: if n < 0 then { return 0 } else n\n"
            '  print "\\{x}"\n'
            "  return x\n"
            "}"
        )

    def test_if_expression_statement_if_still_works(self):
        """Statement-if (no expression context) should still work as before."""
        check_ok(
            "main: function is {\n"
            "  x: 5\n"
            '  if x > 3 then print "big" else print "small"\n'
            "}"
        )


class TestUnitLevelIf:
    """Tests for unit-level if definitions (Phase 42.2)."""

    def test_unit_level_if_basic(self):
        """Unit-level if with constant condition type-checks."""
        check_ok(
            'x: if 1 < 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )

    def test_unit_level_if_type(self):
        """Unit-level if should resolve to the taken branch's type."""
        program = check_ok(
            'x: if 1 < 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )
        resolved = program.resolved
        x_type = None
        for k, v in resolved.items():
            if k.endswith(".x"):
                x_type = v
                break
        assert x_type is not None
        assert x_type.name == "i64"

    def test_unit_level_if_false_branch(self):
        """Unit-level if where condition is false selects else branch."""
        check_ok(
            'x: if 1 > 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )

    def test_unit_level_if_with_named_constants(self):
        """Unit-level if can reference other constants in condition."""
        check_ok(
            "THRESHOLD: 5\n"
            "x: if THRESHOLD > 3 then { 100 } else { 0 }\n"
            'main: function is { print "\\{x}" }'
        )

    def test_unit_level_if_nonconstant_error(self):
        """Non-constant condition at unit level should produce an error."""
        errors = check_errors(
            'x: if main then { 1 } else { 0 }\nmain: function is { print "\\{x}" }'
        )
        assert any("compile-time constant" in e.msg for e in errors)

    def test_unit_level_if_different_types(self):
        """Unit-level if arms can produce different types."""
        program = check_ok(
            'x: if 1 < 2 then { 42 } else { 99u8 }\nmain: function is { print "\\{x}" }'
        )
        # x should have type i64 (the true branch type)
        resolved = program.resolved
        x_type = None
        for k, v in resolved.items():
            if k.endswith(".x"):
                x_type = v
                break
        assert x_type is not None
        assert x_type.name == "i64"


class TestVisibility:
    """Tests for public/private access control."""

    def test_default_all_public(self):
        """Without public declaration, all members are accessible."""
        check_ok(
            "myrec: record { x: i64 y: i64 }\n"
            'main: function is { r: myrec x: 1 y: 2\n print "\\{r.x} \\{r.y}" }'
        )

    def test_public_restricts_access(self):
        """public: unit restricts external access to listed members."""
        errors = check_errors(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  public: unit { :x }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2\n print "\\{r.y}" }'
        )
        assert any("not public" in e.msg for e in errors)

    def test_public_allows_listed_members(self):
        """Members listed in public are accessible."""
        check_ok(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  public: unit { :x }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2\n print "\\{r.x}" }'
        )

    def test_this_accesses_all_members(self):
        """this.field always accesses all members (private)."""
        check_ok(
            "myclass: class { x: i64 y: i64 } as {\n"
            "  public: unit { :get_y }\n"
            "  get_y: function {:this} out i64 is { return this.y }\n"
            "}\n"
            "main: function is {\n"
            '  with c: myclass x: 1 y: 2 do print "\\{c.get_y}"\n'
            "}"
        )

    def test_class_public_restricts_field(self):
        """Class with public restriction prevents external field access."""
        errors = check_errors(
            "myclass: class { x: i64 secret: i64 } as {\n"
            "  public: unit { :x }\n"
            "}\n"
            "main: function is {\n"
            '  with c: myclass x: 1 secret: 42 do print "\\{c.secret}"\n'
            "}"
        )
        assert any("not public" in e.msg for e in errors)

    def test_public_with_multiple_members(self):
        """Multiple members in public unit."""
        check_ok(
            "myrec: record { x: i64 y: i64 z: i64 } as {\n"
            "  public: unit { :x :z }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2 z: 3\n print "\\{r.x} \\{r.z}" }'
        )

    def test_error_private_redefinition(self):
        """private: unit { ... } should be an error."""
        errors = check_errors(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  private: unit { :x }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2\n print "\\{r.x}" }'
        )
        assert any(
            "private" in e.msg and "cannot be redefined" in e.msg for e in errors
        )

    def test_public_renaming(self):
        """public: unit { api_x: x } allows access via renamed name."""
        check_ok(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  public: unit { api_x: x }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2\n print "\\{r.api_x}" }'
        )

    def test_public_renaming_blocks_internal_name(self):
        """When renamed, the internal name is not directly accessible."""
        errors = check_errors(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  public: unit { api_x: x }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2\n print "\\{r.x}" }'
        )
        assert any("not public" in e.msg for e in errors)


class TestNativeTypeCheck:
    """Tests for native keyword handling in the type checker."""

    def test_system_native_function_resolves(self):
        """System native functions (error, return, break, continue) resolve normally."""
        program = check_ok('main: function is { print "hello" }')
        # return/break/continue are resolved from system.z as native functions
        assert program is not None

    def test_system_native_types_resolve(self):
        """System native types (bool, null, string) resolve and are usable."""
        check_ok('main: function is { x: "hello"\n print x }')

    def test_native_function_in_user_code_errors(self):
        """Using 'is native' in user code should produce an error."""
        errors = check_errors(
            "f: function out i64 is native\nmain: function is { print f }"
        )
        assert any(
            "native" in e.msg.lower() and "reserved" in e.msg.lower() for e in errors
        )

    def test_native_function_error_has_hint(self):
        """Native function error should include a helpful hint."""
        errors = check_errors(
            "f: function {n: i64} out i64 is native\nmain: function is { print f }"
        )
        native_errors = [e for e in errors if "native" in e.msg.lower()]
        assert len(native_errors) > 0
        assert native_errors[0].hint is not None
        assert "body" in native_errors[0].hint.lower()

    def test_native_record_in_user_code_errors(self):
        """Using 'record is native' in user code should produce an error."""
        errors = check_errors("r: record is native\nmain: function is {}")
        assert any("native" in e.msg.lower() for e in errors)

    def test_native_class_in_user_code_errors(self):
        """Using 'class is native' in user code should produce an error."""
        errors = check_errors("c: class is native\nmain: function is {}")
        assert any("native" in e.msg.lower() for e in errors)

    def test_native_method_in_user_record_errors(self):
        """Native method inside a user-defined record should produce an error."""
        errors = check_errors(
            "r: record { x: i64 } as { m: function {:this} out i64 is native }\n"
            "main: function is {}"
        )
        assert any("native" in e.msg.lower() for e in errors)

    def test_return_is_native_not_spec_error(self):
        """return (native) should not trigger 'Cannot take spec' error."""
        # This test verifies the fix: native functions have body=None but
        # should not be treated as specs for error purposes
        check_ok("main: function out i64 is { return 42 }")

    def test_break_in_loop(self):
        """break (native) works correctly in a loop."""
        check_ok("main: function is { for loop { break } }")

    def test_continue_in_loop(self):
        """continue (native) works correctly in a loop."""
        check_ok(
            "main: function is { x: 0\n for while x < 10 loop { x = x + 1\n continue } }"
        )

    def test_break_outside_loop_error(self):
        """break outside a loop is undefined (only exists inside for scopes)."""
        errors = check_errors("main: function is { break }")
        assert any("undefined" in e.msg.lower() and "break" in e.msg for e in errors)

    def test_continue_outside_loop_error(self):
        """continue outside a loop is undefined (only exists inside for scopes)."""
        errors = check_errors("main: function is { continue }")
        assert any("undefined" in e.msg.lower() and "continue" in e.msg for e in errors)

    def test_break_in_nested_loop(self):
        """break in nested loop should type-check ok."""
        check_ok(
            "main: function is {\n"
            "  for loop {\n"
            "    for loop {\n"
            "      if 1 < 2 then { break }\n"
            "    }\n"
            "    break\n"
            "  }\n"
            "}"
        )

    def test_continue_in_nested_loop(self):
        """continue in nested loop should type-check ok."""
        check_ok(
            "main: function is {\n"
            "  x: 0\n"
            "  for while x < 5 loop {\n"
            "    x = x + 1\n"
            "    for loop {\n"
            "      if 1 < 2 then { continue }\n"
            "      break\n"
            "    }\n"
            "  }\n"
            "}"
        )

    def test_break_in_if_inside_loop(self):
        """break inside if inside loop is valid."""
        check_ok(
            "main: function is {\n  for loop {\n    if 1 < 2 then { break }\n  }\n}"
        )

    def test_error_function_native(self):
        """error function (native) produces a compile-time error."""
        errors = check_errors('main: function is { error "test" }')
        assert any(e.msg == "test" for e in errors)


class TestCompileTimeError:
    """Tests for compile-time error via the error builtin."""

    def test_error_unconditional(self):
        """Unconditional error produces compile-time error with message."""
        errors = check_errors('main: function is { error "boom" }')
        assert any(e.msg == "boom" for e in errors)

    def test_error_in_const_true_branch(self):
        """error in a constant-true if branch triggers compile-time error."""
        errors = check_errors(
            'SIZE: 0\nmain: function is { if SIZE == 0 then { error "bad" } }'
        )
        assert any(e.msg == "bad" for e in errors)

    def test_error_in_const_false_branch(self):
        """error in a constant-false if branch is suppressed."""
        check_ok('SIZE: 1\nmain: function is { if SIZE == 0 then { error "bad" } }')

    def test_error_in_const_false_else_taken(self):
        """error in else clause when all if clauses are const-false triggers."""
        errors = check_errors(
            "SIZE: 1\nmain: function is {\n"
            '  if SIZE == 0 then { x: 1 } else { error "fallthrough" }\n}'
        )
        assert any(e.msg == "fallthrough" for e in errors)

    def test_error_in_const_true_else_suppressed(self):
        """error in else clause when a const-true clause was taken is suppressed."""
        check_ok(
            "SIZE: 1\nmain: function is {\n"
            '  if SIZE == 1 then { x: 1 } else { error "never" }\n}'
        )

    def test_error_runtime_branch(self):
        """error in a runtime-conditional branch triggers compile-time error."""
        errors = check_errors(
            'main: function {x: i32} is { if x == 0 then { error "bad" } }'
        )
        assert any(e.msg == "bad" for e in errors)

    def test_error_preserves_unreachable_detection(self):
        """Code after error is flagged as unreachable."""
        errors = check_errors('main: function is { error "stop"\n  x: 1 }')
        assert any("stop" in e.msg for e in errors)
        assert any("nreachable" in e.msg for e in errors)

    def test_error_generic_fallback_message(self):
        """error with interpolated string uses generic fallback message."""
        errors = check_errors('N: 0\nmain: function is { error "value is \\{N}" }')
        assert any(e.msg == "compile-time error" for e in errors)

    def test_error_numeric_generic_triggered(self):
        """error triggered via numeric generic param in monomorphized code."""
        errors = check_errors(
            "buf: record { val: u8 } as { cap: u32.generic }\n"
            "main: function is {\n"
            "  b: (buf cap: 0)\n"
            '  if b.cap == 0 then { error "cap must be > 0" }\n'
            "}"
        )
        assert any("cap must be > 0" in e.msg for e in errors)

    def test_error_numeric_generic_ok(self):
        """error suppressed when numeric generic param makes condition false."""
        check_ok(
            "buf: record { val: u8 } as { cap: u32.generic }\n"
            "main: function is {\n"
            "  b: (buf cap: 16)\n"
            '  if b.cap == 0 then { error "cap must be > 0" }\n'
            "}"
        )


class TestCompileTimeErrorMatch:
    """Tests for compile-time error suppression in match statements."""

    def test_error_in_const_match_dead_arm(self):
        """error in non-matching scalar match arm is suppressed."""
        check_ok(
            "MODE: 1\nmain: function is {\n"
            '  match MODE case 0 then { error "bad" } case 1 then { x: 1 }\n}'
        )

    def test_error_in_const_match_live_arm(self):
        """error in matching scalar match arm triggers."""
        errors = check_errors(
            "MODE: 0\nmain: function is {\n"
            '  match MODE case 0 then { error "bad" } case 1 then { x: 1 }\n}'
        )
        assert any(e.msg == "bad" for e in errors)

    def test_error_in_const_match_else_suppressed(self):
        """error in else suppressed when a const arm matches."""
        check_ok(
            "MODE: 1\nmain: function is {\n"
            '  match MODE case 1 then { x: 1 } else { error "unknown" }\n}'
        )

    def test_error_in_const_match_else_triggers(self):
        """error in else triggers when no const arm matches."""
        errors = check_errors(
            "MODE: 5\nmain: function is {\n"
            "  match MODE case 0 then { x: 1 } case 1 then { x: 2 }"
            ' else { error "unknown" }\n}'
        )
        assert any(e.msg == "unknown" for e in errors)

    def test_error_in_const_match_named_constants(self):
        """match patterns are named constants with const_value."""
        check_ok(
            "MODE: 1\nOK: 1\nBAD: 0\nmain: function is {\n"
            '  match MODE case BAD then { error "bad" } case OK then { x: 1 }\n}'
        )

    def test_error_in_const_match_numeric_generic(self):
        """numeric generic field as match subject."""
        check_ok(
            "buf: record { val: u8 } as { mode: u32.generic }\n"
            "main: function is {\n"
            "  b: (buf mode: 1)\n"
            '  match b.mode case 0 then { error "bad" } case 1 then { x: 1 }\n'
            "}"
        )

    def test_error_in_const_match_numeric_generic_triggers(self):
        """numeric generic field match triggers error in matching arm."""
        errors = check_errors(
            "buf: record { val: u8 } as { mode: u32.generic }\n"
            "main: function is {\n"
            "  b: (buf mode: 0)\n"
            '  match b.mode case 0 then { error "bad" } case 1 then { x: 1 }\n'
            "}"
        )
        assert any(e.msg == "bad" for e in errors)


class TestGenericTypeMatch:
    """Tests for compile-time match on generic type parameters."""

    def test_generic_type_match_basic(self):
        """match t in monomorphized method resolves to the correct arm."""
        check_ok(
            "mybox: class { val: t } as {\n"
            "  t: any.generic\n"
            "  bits: function {b: this} out i64 is {\n"
            "    match t case i32 then { return 32 }"
            " case i64 then { return 64 } else { return 0 }\n"
            "  }\n"
            "}\n"
            "main: function is { b: mybox val: 42\n  x: b.bits }"
        )

    def test_generic_type_match_error_suppressed(self):
        """error in non-matching generic arm is suppressed."""
        check_ok(
            "mybox: class { val: t } as {\n"
            "  t: any.generic\n"
            "  check: function {b: this} is {\n"
            '    match t case i32 then { error "i32 not supported" }'
            ' case i64 then { x: 1 } else { error "unsupported" }\n'
            "  }\n"
            "}\n"
            "main: function is { b: mybox val: 42\n  b.check }"
        )

    def test_generic_type_match_error_triggers(self):
        """error in matching generic arm triggers."""
        errors = check_errors(
            "mybox: class { val: t } as {\n"
            "  t: any.generic\n"
            "  check: function {b: this} is {\n"
            '    match t case i32 then { error "i32 not supported" }'
            " case i64 then { x: 1 }\n"
            "  }\n"
            "}\n"
            "main: function is { b: mybox val: 42.i32\n  b.check }"
        )
        assert any("i32 not supported" in e.msg for e in errors)

    def test_generic_type_match_else_suppressed(self):
        """else suppressed when a generic type arm matches."""
        check_ok(
            "mybox: class { val: t } as {\n"
            "  t: any.generic\n"
            "  check: function {b: this} is {\n"
            '    match t case i64 then { x: 1 } else { error "unsupported" }\n'
            "  }\n"
            "}\n"
            "main: function is { b: mybox val: 42\n  b.check }"
        )

    def test_generic_type_match_else_triggers(self):
        """else triggers when no generic type arm matches."""
        errors = check_errors(
            "mybox: class { val: t } as {\n"
            "  t: any.generic\n"
            "  check: function {b: this} is {\n"
            "    match t case i32 then { x: 1 }"
            ' case i64 then { x: 2 } else { error "unsupported" }\n'
            "  }\n"
            "}\n"
            "main: function is { b: mybox val: 3.14\n  b.check }"
        )
        assert any("unsupported" in e.msg for e in errors)


class TestDoBreak:
    """Tests for break in do/bare-brace blocks."""

    def test_do_break_makes_type_optionval(self):
        """Do block with break wraps return type in optionval."""
        program = check_ok(
            "main: function is {\n  x: {\n    if 1 > 2 then { break }\n    42\n  }\n}"
        )
        tc = TypeChecker(program)
        tc.check()
        # x should have optionval type
        t = tc._resolve_unit_name("test", "main")
        assert t is not None

    def test_do_without_break_unchanged(self):
        """Do block without break keeps plain type (no optional wrapping)."""
        program = check_ok("main: function is { x: { 42 } }")
        tc = TypeChecker(program)
        tc.check()

    def test_do_break_nested_for_binds_to_for(self):
        """break inside for inside do binds to the for, not the do."""
        program = check_ok(
            "main: function is {\n"
            "  x: {\n"
            "    for loop {\n"
            "      if 1 < 2 then { break }\n"
            "    }\n"
            "    42\n"
            "  }\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()

    def test_do_break_nested_do_binds_to_inner(self):
        """break inside inner do binds to inner do only."""
        check_ok(
            "main: function is {\n"
            "  x: {\n"
            "    y: {\n"
            "      if 1 > 2 then { break }\n"
            "      10\n"
            "    }\n"
            "    42\n"
            "  }\n"
            "}"
        )

    def test_continue_in_do_block_undefined(self):
        """continue in bare do block (outside for) is undefined."""
        errors = check_errors("main: function is { { continue } }")
        assert any("undefined" in e.msg.lower() and "continue" in e.msg for e in errors)

    def test_do_break_in_if(self):
        """break inside if inside do sets has_break on do."""
        check_ok(
            "main: function is {\n  x: {\n    if 1 > 2 then { break }\n    42\n  }\n}"
        )

    def test_break_in_do_inside_for_binds_to_do(self):
        """break in do block inside for loop binds to the do, not the for."""
        check_ok(
            "main: function is {\n"
            "  for loop {\n"
            "    x: {\n"
            "      if 1 > 2 then { break }\n"
            "      42\n"
            "    }\n"
            "    break\n"
            "  }\n"
            "}"
        )


class TestPrivateFriendAccess:
    """Tests for .private friend access mechanism."""

    def test_private_field_type_grants_access(self):
        """A field declared as mytype.private grants private access."""
        check_ok(
            "bag: class { secret: i64 } as { public: unit {} }\n"
            "reader: class { src: bag.private } as {\n"
            "  read: function {r: this} out i64 is { return r.src.secret }\n"
            "}\n"
            "main: function is { b: bag secret: 42\n"
            "  r: reader src: b.take\n"
            '  print "\\{reader.read r: r}" }'
        )

    def test_external_private_access_blocked(self):
        """External code cannot use .private to bypass access control."""
        errors = check_errors(
            "bag: class { secret: i64 } as { public: unit {} }\n"
            "main: function is { b: bag secret: 42\n"
            "  x: b.private }"
        )
        assert any("private" in e.msg.lower() for e in errors)

    def test_external_private_access_has_hint(self):
        """Error for external .private includes a helpful hint."""
        errors = check_errors(
            "bag: class { secret: i64 } as { public: unit {} }\n"
            "main: function is { b: bag secret: 42\n"
            "  x: b.private }"
        )
        priv_errors = [e for e in errors if "private" in e.msg.lower()]
        assert len(priv_errors) > 0
        assert priv_errors[0].hint is not None

    def test_direct_field_access_still_blocked(self):
        """Without .private, external code still can't access private fields."""
        errors = check_errors(
            "bag: class { secret: i64 } as { public: unit {} }\n"
            "reader: class { src: bag } as {\n"
            "  read: function {r: this} out i64 is { return r.src.secret }\n"
            "}\n"
            "main: function is { b: bag secret: 42\n"
            "  r: reader src: b.take\n"
            '  print "\\{reader.read r: r}" }'
        )
        assert any("not public" in e.msg.lower() for e in errors)

    def test_private_via_this_in_method(self):
        """this.private can be passed from inside the type's own method."""
        check_ok(
            "bag: class { val: i64 } as {\n"
            "  public: unit { :get }\n"
            "  get: function {self: this} out holder is {\n"
            "    return holder src: self.private\n"
            "  }\n"
            "}\n"
            "holder: class { src: bag.private } as {\n"
            "  read: function {h: this} out i64 is { return h.src.val }\n"
            "}\n"
            "main: function is { b: bag val: 42\n"
            "  h: (bag.get self: b)\n"
            '  print "\\{holder.read h: h}" }'
        )


class TestTypeNarrowing:
    """Tests for type narrowing (flow typing) — Phase 49a."""

    # -- Assignment-based narrowing --

    def test_narrow_on_variant_subtype_construction(self):
        """Variable assigned a specific variant subtype is narrowed."""
        check_ok(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            '  print "\\{x.ok}"\n'
            "}"
        )

    def test_narrow_wrong_subtype_access_after_assignment(self):
        """Accessing wrong subtype after narrowing assignment is an error."""
        errors = check_errors(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            '  print "\\{x.err}"\n'
            "}"
        )
        assert any("narrowed" in e.msg for e in errors)

    def test_narrow_on_union_subtype_construction(self):
        """Variable assigned a specific union subtype is narrowed."""
        check_ok(
            "r: union { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            '  print "\\{x.ok}"\n'
            "}"
        )

    def test_narrow_wrong_subtype_on_union_after_assignment(self):
        """Accessing wrong subtype on union after narrowing is an error."""
        errors = check_errors(
            "r: union { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            '  print "\\{x.err}"\n'
            "}"
        )
        assert any("narrowed" in e.msg for e in errors)

    def test_narrow_reassignment_resets(self):
        """Reassignment to different subtype resets narrowing."""
        check_ok(
            "r: variant { a: i64  b: i64 }\n"
            "main: function is {\n"
            "  x: r.a 1\n"
            "  x = r.b 2\n"
            '  print "\\{x.b}"\n'
            "}"
        )

    def test_narrow_reassignment_changes_narrowing(self):
        """Reassignment narrows to new subtype; old subtype is invalid."""
        errors = check_errors(
            "r: variant { a: i64  b: i64 }\n"
            "main: function is {\n"
            "  x: r.a 1\n"
            "  x = r.b 2\n"
            '  print "\\{x.a}"\n'
            "}"
        )
        assert any("narrowed" in e.msg for e in errors)

    # -- Match arm narrowing --

    def test_narrow_within_match_arm(self):
        """Within a match arm, the variable is narrowed to that arm's subtype."""
        check_ok(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "\\{x.ok}"\n'
            "  } case err then {\n"
            '    print "\\{x.err}"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )

    def test_narrow_wrong_subtype_in_match_arm(self):
        """Accessing wrong subtype inside a match arm is an error."""
        errors = check_errors(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "\\{x.err}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )
        assert any("narrowed" in e.msg for e in errors)

    def test_narrow_else_clause(self):
        """Else clause sees union minus all explicit case subtypes."""
        check_ok(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "ok"\n'
            '  } else print "other"\n'
            "}"
        )

    def test_narrow_excluded_in_else(self):
        """Accessing excluded subtype in else clause is an error."""
        errors = check_errors(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "ok"\n'
            "  } else {\n"
            '    print "\\{x.ok}"\n'
            "  }\n"
            "}"
        )
        assert any("excluded" in e.msg or "narrowed" in e.msg for e in errors)

    # -- Post-match narrowing (guard clause) --

    def test_narrow_post_match_diverging_arm(self):
        """After a match arm that returns, the subtype is excluded."""
        check_ok(
            "r: variant { ok: i64  err: i64 }\n"
            "f: function {x: r} is {\n"
            "  match (\n    x\n  ) case err then {\n"
            "    return\n"
            "  } else {}\n"
            '  print "\\{x.ok}"\n'
            "}"
        )

    def test_narrow_post_match_excluded_access_error(self):
        """After a diverging arm, accessing the excluded subtype is an error."""
        errors = check_errors(
            "r: variant { ok: i64  err: i64 }\n"
            "f: function {x: r} is {\n"
            "  match (\n    x\n  ) case err then {\n"
            "    return\n"
            "  } else {}\n"
            '  print "\\{x.err}"\n'
            "}"
        )
        assert any("excluded" in e.msg or "narrowed" in e.msg for e in errors)

    def test_narrow_multiple_diverging_arms(self):
        """Multiple diverging arms accumulate exclusions."""
        check_ok(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "f: function {x: r} is {\n"
            "  match (\n    x\n  ) case err then {\n"
            "    return\n"
            "  } case none then {\n"
            "    return\n"
            "  } else {}\n"
            '  print "\\{x.ok}"\n'
            "}"
        )

    # -- Dead code detection --

    def test_dead_code_after_return(self):
        """Statements after return are flagged as dead code."""
        errors = check_errors('main: function is {\n  return\n  print "unreachable"\n}')
        assert any("Unreachable" in e.msg for e in errors)

    def test_dead_code_after_all_branches_return(self):
        """Statements after if where all branches return are dead code."""
        errors = check_errors(
            "f: function {x: i64} out i64 is {\n"
            "  if x > 0 then { return 1 } else { return 0 }\n"
            '  print "dead"\n'
            "}"
        )
        assert any("Unreachable" in e.msg for e in errors)

    def test_no_dead_code_when_branch_completes(self):
        """No dead code error when some branches don't return."""
        check_ok(
            "main: function is {\n"
            "  x: 5\n"
            '  if x > 3 then { return } else print "small"\n'
            '  print "reachable"\n'
            "}"
        )

    # -- Function boundary --

    def test_narrow_does_not_cross_function_boundary(self):
        """Narrowing is intra-function: callee sees full type."""
        check_ok(
            "r: variant { ok: i64  err: i64 }\n"
            "f: function {x: r} is {\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "\\{x.ok}"\n'
            "  } case err then {\n"
            '    print "\\{x.err}"\n'
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  f x: x\n"
            "}"
        )

    # -- Nested match --

    def test_narrow_nested_match(self):
        """Inner match narrows independently of outer."""
        check_ok(
            "r: variant { a: i64  b: i64  c: i64 }\n"
            "main: function is {\n"
            "  x: r.a 1\n"
            "  match (\n    x\n  ) case a then {\n"
            '    print "\\{x.a}"\n'
            "  } case b then {\n"
            '    print "\\{x.b}"\n'
            "  } case c then {\n"
            '    print "\\{x.c}"\n'
            "  }\n"
            "}"
        )

    # -- Non-addressable target --

    def test_narrow_non_addressable_no_narrowing(self):
        """Match on expression (not variable) doesn't crash or error on narrowing."""
        check_ok(
            "r: variant { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  x: r.ok 1\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}"
        )


class TestIsAsNamespaceCollision:
    """Names cannot appear in both 'is' and 'as' sections of an object."""

    def test_record_function_in_both_is_and_as(self):
        """Same function name in 'is' and 'as' of record is an error."""
        errors = check_errors(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "r: record {\n"
            "  f: function {a: i64 b: i64} out i64\n"
            "} as {\n"
            "  f: function {p: this} out i64 is { return 0 }\n"
            "}\n"
            "main: function is { x: r f: add.take }"
        )
        assert any("'f'" in e.msg and "'is' and 'as'" in e.msg for e in errors)

    def test_class_function_in_both_is_and_as(self):
        """Same function name in 'is' and 'as' of class is an error."""
        errors = check_errors(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "c: class {\n"
            "  f: function {a: i64 b: i64} out i64\n"
            "} as {\n"
            "  f: function {p: this} out i64 is { return 0 }\n"
            "}\n"
            "main: function is { x: c f: add.take }"
        )
        assert any("'f'" in e.msg and "'is' and 'as'" in e.msg for e in errors)

    def test_union_no_collision_when_names_differ(self):
        """Union with different names in 'is' subtypes and 'as' methods is fine."""
        check_ok(
            "u: union {\n"
            "  a: i64\n"
            "  b: null\n"
            "} as {\n"
            "  f: function {p: this} out i64 is { return 0 }\n"
            "}\n"
            "main: function is { x: u.a 1 }"
        )

    def test_union_function_name_collision(self):
        """Function in 'is' and 'as' of union with same name is an error."""
        errors = check_errors(
            "u: union {\n"
            "  a: i64\n"
            "  b: null\n"
            "  f: function {x: i64} out i64\n"
            "} as {\n"
            "  f: function {p: this} out i64 is { return 0 }\n"
            "}\n"
            "main: function is { x: u.a 1 }"
        )
        assert any("'f'" in e.msg and "'is' and 'as'" in e.msg for e in errors)

    def test_different_names_in_is_and_as_ok(self):
        """Different names in 'is' and 'as' is fine."""
        check_ok(
            "r: record {\n"
            "  x: i64\n"
            "} as {\n"
            "  get_x: function {p: this} out i64 is { return p.x }\n"
            "}\n"
            "main: function is {\n"
            "  p: r x: 1\n"
            '  print "\\{r.get_x p}"\n'
            "}"
        )

    def test_generic_param_in_as_no_collision_with_field(self):
        """Generic param in 'as' doesn't collide with field in 'is'."""
        # 'val' is in 'is', 't' is in 'as' — different names, no collision
        check_ok(
            "box: record { val: t } as { t: any.generic }\n"
            "main: function is {\n"
            "  b: box val: 42\n"
            '  print "\\{b.val}"\n'
            "}"
        )

    def test_field_in_is_clashes_with_function_in_as(self):
        """Field in 'is' with same name as function in 'as' is an error."""
        errors = check_errors(
            "r: record {\n"
            "  x: i64\n"
            "} as {\n"
            "  x: function {p: this} out i64 is { return 0 }\n"
            "}\n"
            "main: function is { p: r x: 1 }"
        )
        assert any("'x'" in e.msg and "'is' and 'as'" in e.msg for e in errors)


class TestAsConstants:
    """Constants in the 'as' section of records and classes."""

    def test_record_as_constant_typechecks(self):
        """Integer constant in 'as' section type-checks successfully."""
        check_ok(
            "r: record { x: i64 } as { max_val: 100 }\n"
            "main: function is {\n"
            '  print "\\{r.max_val}"\n'
            "}"
        )

    def test_class_as_constant_typechecks(self):
        """Integer constant in 'as' section of class type-checks."""
        check_ok(
            "c: class { x: i64 } as { default_x: 42 }\n"
            "main: function is {\n"
            '  print "\\{c.default_x}"\n'
            "}"
        )

    def test_as_constant_collision_with_is_field(self):
        """Constant in 'as' with same name as 'is' field is a collision error."""
        errors = check_errors(
            "r: record { x: i64 } as { x: 100 }\nmain: function is { p: r x: 1 }"
        )
        assert any("'x'" in e.msg and "'is' and 'as'" in e.msg for e in errors)

    def test_as_constant_cannot_be_reassigned(self):
        """Reassigning a constant from 'as' section is a compile error."""
        errors = check_errors(
            "r: record { x: i64 } as { max_val: 100 }\n"
            "main: function is {\n"
            "  r.max_val = 200\n"
            "}"
        )
        assert any("static constant" in e.msg for e in errors)

    def test_float_constant_in_as(self):
        """Float constant in 'as' section type-checks successfully."""
        check_ok(
            "r: record { x: i64 } as { pi: 3.14 }\n"
            "main: function is {\n"
            '  print "\\{r.pi}"\n'
            "}"
        )

    def test_float_constant_in_expression(self):
        """Float constant from 'as' can be used in expressions."""
        check_ok(
            "r: record { x: i64 } as { scale: 2.5 }\n"
            "main: function is {\n"
            "  val: r.scale * 4.0\n"
            '  print "\\{val}"\n'
            "}"
        )

    def test_reference_to_unit_constant(self):
        """Reference to unit-level constant in 'as' section."""
        check_ok(
            "max_size: 100\n"
            "config: record { x: i64 } as { limit: max_size }\n"
            "main: function is {\n"
            '  print "\\{config.limit}"\n'
            "}"
        )

    def test_computed_constant_expression(self):
        """Computed constant expression in 'as' section."""
        check_ok(
            "r: record { x: i64 } as { max: 2 * 1024 }\n"
            "main: function is {\n"
            '  print "\\{r.max}"\n'
            "}"
        )

    def test_string_constant_in_as(self):
        """String constant in 'as' section type-checks successfully."""
        check_ok(
            'r: record { x: i64 } as { name: "hello" }\n'
            "main: function is {\n"
            "  print r.name\n"
            "}"
        )

    def test_string_constant_cannot_be_reassigned(self):
        """Reassigning a string constant from 'as' is a compile error."""
        errors = check_errors(
            'r: record { x: i64 } as { name: "hello" }\n'
            "main: function is {\n"
            '  r.name = "world"\n'
            "}"
        )
        assert any("static constant" in e.msg for e in errors)


class TestMatchTake:
    """Take ownership of match subject inside arms."""

    def test_union_take_in_one_arm(self):
        """Take subject in one arm of union match — no error."""
        check_ok(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            "    x: u.take\n"
            '    print "\\{x.ok}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}"
        )

    def test_union_take_in_all_arms(self):
        """Take subject in all arms — no error."""
        check_ok(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            "    x: u.take\n"
            '    print "\\{x.ok}"\n'
            "  } case err then {\n"
            "    y: u.take\n"
            '    print "\\{y.err}"\n'
            "  }\n"
            "}"
        )

    def test_union_use_after_match_take_is_error(self):
        """Using subject after match where take occurred is an error."""
        errors = check_errors(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            "    x: u.take\n"
            '    print "\\{x.ok}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            '  print "\\{u.ok}"\n'
            "}"
        )
        assert errors != []

    def test_union_no_take_subject_still_valid(self):
        """Match without take — subject still valid after match."""
        check_ok(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            '    print "\\{u.ok}"\n'
            "  } case err then {\n"
            '    print "\\{u.err}"\n'
            "  }\n"
            '  print "done"\n'
            "}"
        )

    def test_take_in_arm_with_else(self):
        """Take in one arm with else clause — subject invalid after match."""
        errors = check_errors(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            "    x: u.take\n"
            '    print "\\{x.ok}"\n'
            "  } else {\n"
            '    print "other"\n'
            "  }\n"
            '  print "\\{u.ok}"\n'
            "}"
        )
        assert errors != []


class TestAutoGeneratedEquality:
    """Test auto-generated == and != for value types."""

    def test_record_auto_eq(self):
        """Record with numeric fields gets == and != synthesized."""
        program = check_ok(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = program.resolved.get("test.point")
        assert point_type is not None
        eq = point_type.children.get("==")
        assert eq is not None
        assert eq.is_autogen_eq
        assert eq.return_type.name == "bool"
        neq = point_type.children.get("!=")
        assert neq is not None
        assert neq.is_autogen_eq

    def test_record_auto_eq_nested(self):
        """Record containing another record gets ==."""
        check_ok(
            "inner: record { v: 0 }\n"
            "outer: record { a: inner  b: 0 }\n"
            "main: function is {\n"
            "  x: outer a: inner\n"
            "  y: outer a: inner\n"
            "  if x == y then return 0\n"
            "}"
        )

    def test_variant_auto_eq(self):
        """Variant with value subtypes gets ==."""
        check_ok(
            "result: variant { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  a: result.ok 1\n"
            "  b: result.ok 2\n"
            "  if a == b then return 0\n"
            "}"
        )

    def test_enum_auto_eq(self):
        """Pure enum variant (all null subtypes) gets ==."""
        check_ok(
            "color: variant { red: null  green: null  blue: null }\n"
            "main: function is {\n"
            "  a: color.red\n"
            "  b: color.blue\n"
            "  if a == b then return 0\n"
            "}"
        )

    def test_record_eq_null_hide(self):
        """==: null in as section prevents synthesis."""
        errors = check_errors(
            "point: record { x: i64 } as { ==: null }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        assert any("No operator '=='" in e.msg for e in errors)

    def test_record_eq_user_override(self):
        """User-defined == in as_functions is preserved."""
        program = check_ok(
            "point: record { x: 0 } as {\n"
            "  ==: function {a: this b: point} out bool is {\n"
            "    return a.x == b.x\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = program.resolved.get("test.point")
        assert point_type is not None
        eq = point_type.children.get("==")
        assert eq is not None
        assert not eq.is_autogen_eq  # user-defined, not auto-generated

    def test_neq_auto_derived(self):
        """!= is synthesized alongside ==."""
        check_ok(
            "point: record { x: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a != b then return 0\n"
            "}"
        )

    def test_record_eq_includes_function_fields(self):
        """Function pointer fields do not block == synthesis."""
        check_ok(
            "rec: record { op: function {a: i64} out i64 }\n"
            "main: function is {\n"
            "  a: rec\n"
            "  b: rec\n"
            "  if a == b then return 0\n"
            "}"
        )

    def test_record_eq_with_variant_field(self):
        """Record with variant field gets == (variant also auto-generated)."""
        check_ok(
            "status: variant { ok: null  err: null }\n"
            "item: record { id: 0  s: status }\n"
            "main: function is {\n"
            "  a: item s: status.ok\n"
            "  b: item s: status.ok\n"
            "  if a == b then return 0\n"
            "}"
        )

    def test_memcmp_eq_integer_record(self):
        """Record with only integer fields gets memcmp-safe equality."""
        program = check_ok(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = program.resolved.get("test.point")
        eq = point_type.children["=="]
        assert eq.is_simple_eq

    def test_memcmp_eq_float_disqualifies(self):
        """Record with float field does NOT get memcmp-safe equality."""
        program = check_ok(
            "point: record { x: 0.0  y: 0.0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = program.resolved.get("test.point")
        eq = point_type.children["=="]
        assert eq.is_autogen_eq
        assert not eq.is_simple_eq

    def test_memcmp_eq_mixed_int_float(self):
        """Record with mixed int and float fields is NOT memcmp-safe."""
        program = check_ok(
            "rec: record { a: 0  b: 0.0f32 }\n"
            "main: function is {\n"
            "  x: rec\n"
            "  y: rec\n"
            "  if x == y then return 0\n"
            "}"
        )
        rec_type = program.resolved.get("test.rec")
        assert not rec_type.children["=="].is_simple_eq

    def test_memcmp_eq_enum_variant(self):
        """Pure enum variant is memcmp-safe."""
        program = check_ok(
            "color: variant { red: null  green: null }\n"
            "main: function is {\n"
            "  a: color.red\n"
            "  b: color.red\n"
            "  if a == b then return 0\n"
            "}"
        )
        color_type = program.resolved.get("test.color")
        assert color_type.children["=="].is_simple_eq

    def test_memcmp_eq_variant_with_int_payloads(self):
        """Variant with integer payloads is memcmp-safe."""
        program = check_ok(
            "result: variant { ok: i64  err: u8 }\n"
            "main: function is {\n"
            "  a: result.ok 1\n"
            "  b: result.ok 1\n"
            "  if a == b then return 0\n"
            "}"
        )
        result_type = program.resolved.get("test.result")
        assert result_type.children["=="].is_simple_eq

    def test_memcmp_eq_variant_with_float_payload(self):
        """Variant with float payload is NOT memcmp-safe."""
        program = check_ok(
            "result: variant { ok: f64  none: null }\n"
            "main: function is {\n"
            "  a: result.ok 1.0\n"
            "  b: result.ok 1.0\n"
            "  if a == b then return 0\n"
            "}"
        )
        result_type = program.resolved.get("test.result")
        assert not result_type.children["=="].is_simple_eq


class TestIdenticalAndStringEquality:
    """Test identical function and string == / != operators."""

    def test_identical_resolves(self):
        """identical type-checks with two string args."""
        check_ok(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "world"\n'
            "  x: identical lhs: a rhs: b\n"
            "}"
        )

    def test_identical_rejects_valtypes(self):
        """identical rejects value type arguments."""
        errors = check_errors(
            "main: function is {\n  a: 1\n  b: 2\n  x: identical lhs: a rhs: b\n}"
        )
        assert any("value type" in e.msg for e in errors)

    def test_string_eq_resolves(self):
        """== on strings type-checks."""
        check_ok(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "world"\n'
            "  if a == b then return 0\n"
            "}"
        )

    def test_string_neq_resolves(self):
        """!= on strings type-checks."""
        check_ok(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "world"\n'
            "  if a != b then return 0\n"
            "}"
        )

    def test_class_no_default_eq(self):
        """== on a user class without defined == is a compile error."""
        errors = check_errors(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass\n"
            "  b: myclass\n"
            "  if a == b then return 0\n"
            "}"
        )
        assert any("No operator '=='" in e.msg for e in errors)
