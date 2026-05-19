"""
Tests for the type checker (ztypecheck)
"""

import os
from typing import Optional

import pytest

from conftest import make_parser_vfs, make_parser, make_parser_with_vfs
from ztypecheck import typecheck, TypeChecker
from ztypes import (
    ZTypeType,
    ZSubType,
    ZParamOwnership,
    ZOwnership,
    ZLockState,
    ZVariable,
)
import zast
from zast import NodeType

pytestmark = pytest.mark.typecheck

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")
SRC_TYPECHECK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "ztypecheck.py"
)


def parse_and_check(source: str, unitname: str = "test"):
    """Parse source, run type checker, return `(program, typing, errors)`."""
    p = make_parser(source, unitname=unitname, src_dir=LIB_DIR)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    typing = typecheck(program)
    return program, typing, typing.errors


def check_ok(source: str, unitname: str = "test"):
    """Parse and type-check, assert no errors, return `(program, typing)`."""
    program, typing, errors = parse_and_check(source, unitname)
    assert errors == [], f"Expected no errors, got: {[e.msg for e in errors]}"
    return program, typing


def _node_ztype(typing, node):
    """Look up a parsed node's resolved ZType in `ZTyping.node_type`."""
    return typing.node_type.get(node.nodeid)


def check_errors(source: str, unitname: str = "test"):
    """Parse and type-check, assert errors, return error List."""
    program, typing, errors = parse_and_check(source, unitname)
    assert errors != [], "Expected type errors but got none"
    return errors


def _resolve_synth_field_type(
    source: str, *, synth_name: str, field_name: str, unitname: str = "test"
):
    """Helper for the TypeOfExpr-resolution tests: parse, type-check,
    and walk down `tc.unit_types[unitname] -> <synth class>` to fetch
    the *resolved* ZType of the named field. The desugarer emits a
    `TypeOfExpr` placeholder for promoted-local fields; the
    typechecker fills in the real type when it walks the synth
    `.call` body. Returns the resolved ZType or None.
    """
    p = make_parser(source, unitname=unitname, src_dir=LIB_DIR)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    tc = TypeChecker(program)
    tc.check()
    assert tc.typing.errors == [], (
        f"unexpected typecheck errors: {[e.msg for e in tc.typing.errors]}"
    )
    unit_t = tc.unit_types.get(unitname)
    assert unit_t is not None, f"unit '{unitname}' not registered"
    synth_t = tc.typing.child_of(unit_t, synth_name)
    assert synth_t is not None, (
        f"synth class '{synth_name}' not registered on unit '{unitname}'"
    )
    return tc.typing.child_of(synth_t, field_name)


def find_user_monos(typing, *, origin_name: Optional[str] = None):
    """Filter `typing.mono_types` to entries triggered by user code.

    System library load eagerly monomorphises types whose natives reference
    a generic (e.g. `optionval<T>` from each integer record's `iterate`
    native). Tests that previously assumed `mono_types[0]` is the user's
    mono must filter past those system-load monos. Pass `origin_name`
    (e.g. `"Option"`, `"Box"`) to filter by generic-origin name; omit to
    return all monos with nodes whose definition is in a user unit.
    """
    result = []
    for mono, defn in typing.mono_types:
        if origin_name is not None:
            origin = mono.generic_origin
            if origin is None or getattr(origin, "name", None) != origin_name:
                continue
        result.append((mono, defn))
    return result


class TestBasicPrograms:
    def test_empty_function(self):
        check_ok("main: function is {}")

    def test_hello_world(self):
        check_ok('main: function is { print "Hello" }')

    def test_print_resolves_from_core(self):
        """print should be resolved via core -> io -> system.io_print."""
        program, typing = check_ok('main: function is { print "test" }')
        tc = TypeChecker(program)
        tc.check()
        core_type = tc.unit_types.get("core")
        assert core_type is not None
        assert tc.typing.has_child(core_type, "print")
        assert tc.typing.child_of(core_type, "print").typetype == ZTypeType.FUNCTION


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

    def test_reftype_field_reassign_with_take(self):
        """Reftype field '=' reassignment moves RHS into LHS (drop-and-
        transfer); the RHS name is invalidated after the move."""
        check_ok(
            "h: class { msg: String } as {\n"
            "  set: function {x: this v: String.take} is { x.msg = v }\n"
            "}\n"
            'main: function is { a: h msg: "init".string\n'
            '  b: "next".string\n'
            "  h.set a v: b\n"
            "}\n"
        )

    def test_reftype_field_reassign_invalidates_source(self):
        """After `this.field = v`, the source name `v` is invalidated —
        re-using it is an ownership error."""
        errors = check_errors(
            "h: class { msg: String } as {\n"
            "  set: function {x: this v: String.take} out u64 is {\n"
            "    x.msg = v\n"
            "    n: v.length\n"
            "    return n\n"
            "  }\n"
            "}\n"
            'main: function is { a: h msg: "init".string\n'
            '  b: "s".string\n'
            "  n: h.set a v: b\n"
            "}\n"
        )
        assert any(
            "'v'" in e.msg
            and ("ownership" in e.msg.lower() or "after" in e.msg.lower())
            for e in errors
        ), [e.msg for e in errors]

    def test_reftype_field_reassign_rejects_borrowed_source(self):
        """A borrowed RHS can't be moved into a reftype field (borrow
        binding must stay live for its full scope)."""
        errors = check_errors(
            "h: class { msg: String } as {\n"
            "  set: function {x: this v: String.borrow} is { x.msg = v }\n"
            "}\n"
            'main: function is { a: h msg: "init".string\n'
            '  b: "s".string\n'
            "  h.set a v: b.borrow\n"
            "}\n"
        )
        assert any("borrow" in e.msg.lower() for e in errors), [e.msg for e in errors]

    def test_protocol_autoproject_record(self):
        """A concrete record that conforms to a protocol may be passed
        directly to a parameter of that protocol type — the compiler
        auto-projects the wrapper."""
        check_ok(
            "p: protocol { m: function {:this n: i64} out i64 }\n"
            "c: record { k: i64 } as {\n"
            "  :p\n"
            "  m: function {x: this n: i64} out i64 is { return x.k + n }\n"
            "}\n"
            "use_p: function {q: p n: i64} out i64 is {\n"
            "  r: q.m n: n\n"
            "  return r\n"
            "}\n"
            "main: function is {\n"
            "  a: c k: 1\n"
            "  n: use_p q: a n: 2\n"
            "}\n"
        )

    def test_method_calls_self_protocol_lock_field(self):
        """A method can call a protocol method through a `.lock` field
        on `this`. The outer `:this` shared lock must not block the
        inner call's access to the field."""
        check_ok(
            "p: protocol {\n"
            "  read: function {:this into: Bytes max: u64}"
            " out (Result t: u64 e: IoError)\n"
            "}\n"
            "w: class {\n"
            "  source: p.lock\n"
            "  buf: Bytes\n"
            "  cap: u64\n"
            "} as {\n"
            "  create: function {from: p.lock} out this is {\n"
            "    return meta.create source: from buf: Bytes cap: 16.u64\n"
            "  }\n"
            "  fill: function {:this}"
            " out (Result t: u64 e: IoError) is {\n"
            "    this.buf = Bytes\n"
            "    r: this.source.read into: this.buf max: this.cap\n"
            "    return r\n"
            "  }\n"
            "}\n"
            "main: function is {}\n"
        )

    def test_protocol_autoproject_nonconforming_errors(self):
        """Passing a type that does NOT conform still errs as a type
        mismatch."""
        errors = check_errors(
            "p: protocol { m: function {:this n: i64} out i64 }\n"
            "c: record { k: i64 }\n"
            "use_p: function {q: p n: i64} out i64 is { return 0 }\n"
            "main: function is {\n"
            "  a: c k: 1\n"
            "  n: use_p q: a n: 2\n"
            "}\n"
        )
        assert any("type mismatch" in e.msg for e in errors), [e.msg for e in errors]


class TestNonRuntimeTypes:
    def test_null_assignment_error(self):
        """Cannot assign null directly to a variable."""
        errors = check_errors("main: function is { x: null }")
        assert any("null" in e.msg for e in errors)

    def test_never_assignment_error(self):
        """Cannot assign never (return Result) to a variable."""
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
        """Null as a union subtype (eg. Option.none) is fine."""
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

    def test_for_loop_rejects_non_iterable_binding(self):
        """`for x: <int> loop { ... }` is not a valid form — the bound
        expression must be iterable, or paired with a while-clause for
        a C-style counter loop. Silent acceptance previously emitted a
        `while(1) { x = 3; body }` infinite loop."""
        errors = check_errors("main: function is { for x: 3 loop { break } }")
        assert any("iterable" in e.msg or "while" in e.msg for e in errors), (
            f"got: {[e.msg for e in errors]}"
        )

    def test_for_loop_accepts_iterate_binding(self):
        check_ok("main: function is { for x: 3.iterate loop { break } }")

    def test_for_loop_accepts_cstyle_init_with_while(self):
        check_ok("main: function is {\n  for i: 0 while i < 3 loop { i = i + 1 }\n}")


class TestTypeResolution:
    def test_numeric_types_resolve(self):
        """All standard numeric types should resolve as parameter types."""
        for t in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "f32", "f64"):
            check_ok(f"f: function {{n: {t}}} is {{}}")

    def test_string_type_resolves(self):
        check_ok("f: function {s: String} is {}")

    def test_bool_type_resolves(self):
        check_ok("f: function {b: bool} is {}")


class TestUnitResolution:
    def test_core_numeric_types_in_scope(self):
        check_ok("f: function {n: i64} out i64 is { return n }")

    def test_core_types_populated(self):
        """Core unit type should have numeric types, print, etc.
        Demand-driven: types are resolved when referenced."""
        program, typing = check_ok('main: function is { x: 42\n print "test" }')
        tc = TypeChecker(program)
        tc.check()
        # print was referenced, so it should be resolved in core
        core = tc.unit_types["core"]
        assert tc.typing.has_child(core, "print")
        # i64 was referenced (via literal 42), so it should be resolved
        assert tc.typing.has_child(core, "i64")

    def test_cross_unit_alias_resolution(self):
        """io.print -> system.io_print should resolve across units."""
        program, typing = check_ok('main: function is { print "test" }')
        tc = TypeChecker(program)
        tc.check()
        io_type = tc.unit_types.get("io")
        assert io_type is not None
        assert tc.typing.has_child(io_type, "print")
        assert tc.typing.child_of(io_type, "print").typetype == ZTypeType.FUNCTION

    def test_system_unit_has_numeric_records(self):
        """System unit should have numeric types as records with methods.
        Demand-driven: reference i64 to trigger resolution."""
        program, typing = check_ok("f: function {n: i64} out i64 is { return n + 1 }")
        tc = TypeChecker(program)
        tc.check()
        system = tc.unit_types["system"]
        i64 = tc.typing.child_of(system, "i64")
        assert i64 is not None
        assert i64.typetype == ZTypeType.RECORD
        assert tc.typing.has_child(i64, "+")
        assert tc.typing.has_child(i64, "-")
        assert tc.typing.has_child(i64, "*")
        assert tc.typing.has_child(i64, "/")


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
        program, typing = check_ok(
            "f: function {a: i64 b: i64} is { if a < b then return 0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        system = tc.unit_types["system"]
        i64 = tc.typing.child_of(system, "i64")
        lt = tc.typing.child_of(i64, "<")
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
            '  with y: abs -5 do print "Result = \\{y}"\n'
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
        p = make_parser_with_vfs(vfs, name)
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
        program, typing = check_ok(
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
        program, typing = check_ok(
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
        scale = tc.typing.child_of(vec_type, "scale")
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

        program, typing = check_ok("main: function is {}")
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

    def test_zlockstate_two_state(self):
        assert ZLockState.EXCLUSIVE == 1
        assert ZLockState.SHARED == 2
        assert len(ZLockState) == 2

    def test_zparam_ownership(self):
        assert ZParamOwnership.TAKE == 0
        assert ZParamOwnership.BORROW == 1
        assert ZParamOwnership.LOCK == 2
        assert len(ZParamOwnership) == 3

    def test_zvariable_defaults(self):
        from ztypes import ZType

        t = ZType(name="i64", typetype=ZTypeType.RECORD, parent=None)
        v = ZVariable(ztype=t, ownership=ZOwnership.OWNED)
        assert v.ownership == ZOwnership.OWNED

    def test_zvariable_borrowed(self):
        from ztypes import ZType

        t = ZType(name="point", typetype=ZTypeType.RECORD, parent=None)
        v = ZVariable(
            ztype=t,
            ownership=ZOwnership.BORROWED,
        )
        assert v.ownership == ZOwnership.BORROWED


class TestOwnershipParsing:
    """Test that ownership annotations parse correctly on function parameters."""

    def test_param_borrow(self):
        """Parameter with .borrow annotation should parse and type-check."""
        program, typing = check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.borrow} is {}\nmain: function is {}"
        )
        ftype = typing.resolved["test.f"]
        assert typing.child_ownership(ftype, "a") == ZParamOwnership.BORROW

    def test_param_take(self):
        """Parameter with .take annotation."""
        program, typing = check_ok(
            "f: function {a: i64.take} is {}\nmain: function is {}"
        )
        ftype = typing.resolved["test.f"]
        assert typing.child_ownership(ftype, "a") == ZParamOwnership.TAKE

    def test_param_lock(self):
        """Parameter with .lock annotation (requires return value)."""
        program, typing = check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.lock} out myclass is { return a }\n"
            "main: function is {}"
        )
        ftype = typing.resolved["test.f"]
        assert typing.child_ownership(ftype, "a") == ZParamOwnership.LOCK

    def test_param_no_ownership(self):
        """Parameter without annotation should have empty param_ownership."""
        program, typing = check_ok("f: function {a: i64} is {}\nmain: function is {}")
        ftype = typing.resolved["test.f"]
        assert not typing.has_child_ownership(ftype, "a")

    def test_mixed_params(self):
        """Mix of annotated and unannotated parameters."""
        program, typing = check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.take b: myclass c: myclass.borrow} is {}\n"
            "main: function is {}"
        )
        ftype = typing.resolved["test.f"]
        assert typing.child_ownership(ftype, "a") == ZParamOwnership.TAKE
        assert not typing.has_child_ownership(ftype, "b")
        assert typing.child_ownership(ftype, "c") == ZParamOwnership.BORROW

    def test_return_type_borrow(self):
        """Return type with .borrow annotation."""
        program, typing = check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.lock} out myclass.borrow is { return a }\n"
            "main: function is {}"
        )
        ftype = typing.resolved["test.f"]
        assert ftype.return_ownership == ZParamOwnership.BORROW
        assert typing.child_ownership(ftype, "a") == ZParamOwnership.LOCK

    def test_return_type_no_ownership(self):
        """Return type without annotation should not have return_ownership set."""
        program, typing = check_ok(
            "f: function out i64 is { return 42 }\nmain: function is {}"
        )
        ftype = typing.resolved["test.f"]
        assert ftype.return_ownership is None


class TestOwnershipInZType:
    """Test that ownership annotations propagate to ZType."""

    def test_param_ownership_on_ztype(self):
        """Ownership annotations should be on the ZType after type checking."""
        program, typing = check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.take b: myclass.borrow} out myclass is { return a }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert tc.typing.child_ownership(ftype, "a") == ZParamOwnership.TAKE
        assert tc.typing.child_ownership(ftype, "b") == ZParamOwnership.BORROW

    def test_return_ownership_on_ztype(self):
        """Return ownership should propagate to ZType."""
        program, typing = check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.lock} out myclass.borrow is { return a }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.return_ownership == ZParamOwnership.BORROW
        assert tc.typing.child_ownership(ftype, "a") == ZParamOwnership.LOCK

    def test_no_ownership_empty_dict(self):
        """Functions without ownership annotations should have empty param_ownership."""
        program, typing = check_ok(
            "f: function {a: i64} out i64 is { return a }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert tc.typing.child_ownerships_of(ftype) == {}


class TestValTypeTagging:
    """Test that types are correctly tagged as valtype or reftype."""

    def test_numeric_records_are_valtype(self):
        """System numeric types (records) should be tagged as valtype."""
        program, typing = check_ok("f: function {n: i64} out i64 is { return n }")
        tc = TypeChecker(program)
        tc.check()
        system = tc.unit_types["system"]
        i64 = tc.typing.child_of(system, "i64")
        assert i64 is not None
        assert i64.is_valtype is True

    def test_user_record_is_valtype(self):
        """User-defined records should be tagged as valtype."""
        program, typing = check_ok(
            "point: record { x: 0.0\n y: 0.0 }\nmain: function is { p: point }"
        )
        tc = TypeChecker(program)
        tc.check()
        point = tc._resolved.get("test.point")
        assert point is not None
        assert point.is_valtype is True

    def test_union_is_reftype(self):
        """Unions should be tagged as reftype (is_valtype=False)."""
        program, typing = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        system = tc.unit_types["system"]
        any_type = tc.typing.child_of(system, "Any")
        assert any_type is not None
        assert any_type.is_valtype is False

    def test_enum_is_reserved(self):
        """Enum keyword is reserved and should produce a parse error."""
        vfs, name = make_parser_vfs(
            "color: enum { red\n green\n blue }\nmain: function is { c: color.red }",
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = make_parser_with_vfs(vfs, name)
        result = p.parse()
        assert isinstance(result, zast.Error)

    def test_function_type_valtype_is_none(self):
        """Function types don't have a valtype classification."""
        program, typing = check_ok("f: function is {}\nmain: function is {}")
        tc = TypeChecker(program)
        tc.check()
        ftype = tc._resolved.get("test.f")
        assert ftype is not None
        assert ftype.is_valtype is None


class TestOwnershipSignatureValidation:
    """Test ownership rules on function signatures."""

    def test_borrow_return_without_lock_param_error(self):
        """Returning borrow without Any lock parameter is an error."""
        errors = check_errors(
            "f: function {t: i64} out i64.borrow is { return t }\nmain: function is {}"
        )
        assert any(
            "lock" in e.msg.lower() and "borrow" in e.msg.lower() for e in errors
        )

    def test_borrow_return_with_lock_param_ok(self):
        """Returning borrow with a lock parameter is OK."""
        check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {t: myclass.lock} out myclass.borrow is { return t }\n"
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
            "myclass: class { value: 0 }\n"
            "f: function {t: myclass.lock} out myclass is { return t }\n"
            "main: function is {}"
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
            "myclass: class { value: 0 }\n"
            "f: function {t: myclass.lock} out myclass.borrow is { return t }\n"
            "main: function is {}"
        )

    def test_return_borrowed_string_param_rejected(self):
        """Returning an unannotated String param as owned aliases caller data."""
        errors = check_errors(
            "f: function {s: String} out String is { return s }\nmain: function is {}"
        )
        assert any(
            "borrowed parameter" in e.msg.lower() and "'s'" in e.msg for e in errors
        ), [e.msg for e in errors]

    def test_return_taken_string_param_allowed(self):
        """`.take` on the param transfers ownership in — return is then sound."""
        check_ok(
            "f: function {s: String.take} out String is { return s }\n"
            "main: function is {}"
        )

    def test_return_copy_of_borrowed_string_param_allowed(self):
        """`.copy` on the value returns a fresh independent owner."""
        check_ok(
            "f: function {s: String} out String is { return s.copy }\n"
            "main: function is {}"
        )

    def test_return_borrow_of_lock_param_still_works(self):
        """Existing `out T.borrow` + `.lock` parameter pattern unaffected."""
        check_ok(
            "f: function {s: String.lock} out String.borrow is { return s }\n"
            "main: function is {}"
        )

    def test_return_construction_field_args_typechecked(self):
        """Regression: return-construction shorthand `return Type field: val`
        must type-check the field args, otherwise nested paths never get
        their `.type` stamped and the emitter silently falls through to
        field access. The smoking gun is a Path like `s.copy` in a field
        slot — verify type resolution propagates to the Path's parent."""
        program, typing = check_ok(
            "mybox: class { label: String }\n"
            "mk: function {s: String.take} out mybox is {\n"
            "  return mybox label: s.copy\n"
            "}\n"
            "main: function is {}"
        )
        # locate the `s.copy` DOTTEDPATH inside mk's body and confirm the
        # parent (`s`) has its `.type` stamped.
        mk = program.units["test"].body["mk"]
        statement_line = mk.body.statements[0].statementline
        # statementline is an Expression wrapping the return Call
        inner = statement_line.expression
        if hasattr(inner, "expression"):
            inner = inner.expression
        return_call = inner
        # return is a Call whose arguments[1].valtype is `s.copy`
        s_copy = return_call.arguments[1].valtype
        assert s_copy.nodetype == zast.NodeType.DOTTEDPATH, s_copy.nodetype
        s_copy_parent_t = _node_ztype(typing, s_copy.parent)
        assert s_copy_parent_t is not None, (
            "s.copy parent.type unstamped — _check_return_call's "
            "field-arg visit is missing"
        )
        assert s_copy_parent_t.subtype == ZSubType.STRING

    def test_store_borrowed_param_in_aggregate_field_rejected(self):
        """Storing a default-borrowed param into an owned aggregate
        field aliases the caller's storage. Reject; user must `.copy`,
        `.take` the param, or annotate the field as `.lock`."""
        errors = check_errors(
            "mybox: class { label: String }\n"
            "mk: function {s: String} out mybox is {\n"
            "  return mybox label: s\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            "borrowed value 's'" in e.msg and "aggregate field" in e.msg for e in errors
        ), [e.msg for e in errors]

    def test_store_copy_of_borrowed_param_allowed(self):
        """`.copy` produces a fresh owned value — break the borrow
        chain, allow the store."""
        check_ok(
            "mybox: class { label: String }\n"
            "mk: function {s: String} out mybox is {\n"
            "  return mybox label: s.copy\n"
            "}\n"
            "main: function is {}"
        )

    def test_store_taken_param_in_aggregate_field_allowed(self):
        """`.take` on the param transfers ownership in — the body owns
        `s` and may move it into the Box."""
        check_ok(
            "mybox: class { label: String }\n"
            "mk: function {s: String.take} out mybox is {\n"
            "  return mybox label: s\n"
            "}\n"
            "main: function is {}"
        )

    def test_store_lock_param_in_lock_field_allowed(self):
        """`.lock`-annotated params are user-explicit borrow holders;
        they may legitimately be projected into matching `.lock` fields
        (the borrowed_record / ListIter examples). The store-borrow
        check exempts `.lock`-annotated params."""
        check_ok(
            "container: class { x: i64 } as { public: unit { :slice }\n"
            "  slice: function {c: this.lock} out cview is {\n"
            "    return cview source: c.private\n"
            "  }\n"
            "}\n"
            "cview: class {\n"
            "  source: container.private.lock\n"
            "} as {\n"
            "  create: function {source: container.private.lock} out this is {\n"
            "    return meta.create source: source\n"
            "  }\n"
            "}\n"
            "main: function is {}"
        )


class TestStoreOfBorrowedRejection:
    """Pin the rejection of storing a default-borrowed reftype param into
    an aggregate slot via collection mutation methods or field
    reassignment. Coverage exists via two separate mechanisms — TAKE
    annotations on synthesized List/Map params (rejected by
    `_apply_take_to_arg`) and the borrowed-RHS check in
    `_check_reassignment`. These tests lock that behaviour in so a
    future refactor can't silently regress it."""

    def test_list_append_borrowed_rejected(self):
        """`lst.append from: s` with a borrowed-String param `s` aliases
        the caller's storage into the List. Rejected via the `from`
        param's TAKE annotation."""
        errors = check_errors(
            "f: function {lst: (List of: String) s: String} is {\n"
            "  lst.append from: s\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            "'s'" in e.msg and "'take' parameter" in e.msg and "'from'" in e.msg
            for e in errors
        ), [e.msg for e in errors]

    def test_list_append_copy_allowed(self):
        """`.copy` produces a fresh owned String — append is sound."""
        check_ok(
            "f: function {lst: (List of: String) s: String} is {\n"
            "  lst.append from: s.copy\n"
            "}\n"
            "main: function is {}"
        )

    def test_list_insert_borrowed_rejected(self):
        """`lst.insert from: s at: 0u64` — same TAKE rejection on `from`."""
        errors = check_errors(
            "f: function {lst: (List of: String) s: String} is {\n"
            "  lst.insert from: s at: 0u64\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            "'s'" in e.msg and "'take' parameter" in e.msg and "'from'" in e.msg
            for e in errors
        ), [e.msg for e in errors]

    def test_list_set_borrowed_rejected(self):
        """`lst.set i: 0u64 val: s` — TAKE annotation is on `val`."""
        errors = check_errors(
            "f: function {lst: (List of: String) s: String} is {\n"
            "  lst.set i: 0u64 val: s\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            "'s'" in e.msg and "'take' parameter" in e.msg and "'val'" in e.msg
            for e in errors
        ), [e.msg for e in errors]

    def test_list_extend_borrowed_rejected(self):
        """`a.extend from: b` with both lists borrowed — `extend` consumes
        its `from` argument so passing a borrowed List aliases."""
        errors = check_errors(
            "f: function {a: (List of: String) b: (List of: String)} is {\n"
            "  a.extend from: b\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            "'b'" in e.msg and "'take' parameter" in e.msg and "'from'" in e.msg
            for e in errors
        ), [e.msg for e in errors]

    def test_map_set_borrowed_value_rejected(self):
        """`m.set key: k value: v` — both `key` and `value` are TAKE.
        Pin the value-side rejection (the first TAKE failure stops
        further checks, so we test value via a fresh-owned key)."""
        errors = check_errors(
            "f: function {m: (Map key: String value: String) v: String} is {\n"
            '  m.set key: "k".copy value: v\n'
            "}\n"
            "main: function is {}"
        )
        assert any(
            "'v'" in e.msg and "'take' parameter" in e.msg and "'value'" in e.msg
            for e in errors
        ), [e.msg for e in errors]

    def test_map_set_copy_value_allowed(self):
        """`.copy` on the borrowed value param breaks the alias."""
        check_ok(
            "f: function {m: (Map key: String value: String) v: String} is {\n"
            '  m.set key: "k".copy value: v.copy\n'
            "}\n"
            "main: function is {}"
        )

    def test_field_reassign_borrowed_rejected(self):
        """`b.label = s` where `s` is a default-borrowed String param
        and `b` is a locked class instance. Rejected by
        `_check_reassignment`'s borrowed-RHS rule — storing the borrow
        into the Box's owned-String slot would alias caller storage."""
        errors = check_errors(
            "mybox: class { label: String }\n"
            "f: function {b: mybox.lock s: String} is {\n"
            "  b.label = s\n"
            "}\n"
            "main: function is {}"
        )
        assert any("'s'" in e.msg for e in errors), [e.msg for e in errors]


class TestReturnLockPropagation:
    """When a user-defined method declares a `.lock` parameter and
    returns a borrow, the call's binding must lock the corresponding
    source Path in the outer scope. Mutating that source while the
    binding is live errors.

    The receiver case (`t: this.lock`) and the explicit-arg case
    (`s: T.lock`) both route through `lock_param_targets` transfer.
    """

    def test_user_method_this_locked_return_locks_receiver(self):
        """User-defined method with `t: this.lock` receiver:
        propagation comes from the standard `.lock`-param transfer at
        call resolution; the call's source-slot is exclusively locked
        for the borrow's lifetime.
        """
        errors = check_errors(
            "holder: class {\n"
            "  val: String\n"
            "} as {\n"
            "  pick: function {t: this.lock prefix: StringView}"
            " out String.borrow is { return t.val }\n"
            "}\n"
            "main: function is {\n"
            '  src: holder val: "hi".string\n'
            '  v: src.pick prefix: ""\n'
            '  src.val = "bye".string\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() for e in errors), [
            e.msg for e in errors
        ]

    def test_user_method_param_locked_return_locks_arg_source(self):
        """User-defined function with a `.lock`-annotated arg param:
        the call's leaf-locked source survives as `_pending_borrow_lock`
        for the binding to install.
        """
        errors = check_errors(
            "get_view: function {s: String.lock}"
            " out String.borrow is { return s }\n"
            "main: function is {\n"
            '  src: "hi".string\n'
            "  v: get_view s: src.lock\n"
            '  src = "bye".string\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() for e in errors), [
            e.msg for e in errors
        ]

    def test_named_receiver_param_locked_return(self):
        """Receiver-bound param spelled `h: this.lock` (not `t:`) —
        the receiver-source lock propagates identically regardless of
        the parameter name.
        """
        errors = check_errors(
            "holder: class {\n"
            "  val: String\n"
            "} as {\n"
            "  pick: function {h: this.lock prefix: StringView}"
            " out String.borrow is { return h.val }\n"
            "}\n"
            "main: function is {\n"
            '  src: holder val: "hi".string\n'
            '  v: src.pick prefix: ""\n'
            '  src.val = "bye".string\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() for e in errors), [
            e.msg for e in errors
        ]

    def test_borrow_return_without_lock_param_rejected(self):
        """A function declaring `out T.borrow` whose return root
        traces back to a non-`.lock` parameter must be rejected at
        the function definition site — the borrow has no lockable
        source on the caller side.
        """
        errors = check_errors(
            "get_view: function {s: String}"
            " out String.borrow is { return s }\n"
            "main: function is {}"
        )
        assert any("lock" in e.msg.lower() and "'s'" in e.msg for e in errors), [
            e.msg for e in errors
        ]

    def test_no_propagation_when_no_lock_target(self):
        """A function with `out T` (no `from:`) returns an owned value
        — no propagation, no spurious lock, source remains mutable.
        """
        check_ok(
            "make_copy: function {s: String} out String is { return s.copy }\n"
            "main: function is {\n"
            '  src: "hi".string\n'
            "  v: make_copy s: src\n"
            '  src = "bye".string\n'
            "}"
        )

    def test_user_method_lock_receiver_rvalue_coercion(self):
        """A user-defined no-arg method with a `t: this.lock` receiver
        coerces in value position the same way a native does — no
        `is_native` asymmetry. Pinned after the auto-call coercion
        moved out of `_resolve_dotted_path` and into `_check_dotted_path`,
        so that Path resolution is context-free and the call/value
        disambiguation lives where it has visibility into the call.

        Tests two forms must produce the same binding type:
          (a) explicit-arg method call: `container.slice c: c`
          (b) implicit-receiver value access: `c.slice`
        Both must bind to `cview`.

        Each binding retains the lock on `c` for its scope, so the
        two forms are exercised in sibling inner blocks rather than
        side-by-side in the outer scope -- two concurrent
        `cview source: container.private.lock` borrows of the same
        source `c` would themselves conflict (exclusive lock on
        `(c,)`).
        """
        program, typing = check_ok(
            "container: class { x: i64 } as { public: unit { :slice }\n"
            "  slice: function {c: this.lock} out cview is {\n"
            "    return cview source: c.private\n"
            "  }\n"
            "}\n"
            "cview: class {\n"
            "  source: container.private.lock\n"
            "} as {\n"
            "  create: function {source: container.private.lock} out this is {\n"
            "    return meta.create source: source\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  c: container x: 7\n"
            "  v1: container.slice c: c\n"
            "}\n"
            "other: function is {\n"
            "  c: container x: 7\n"
            "  v2: c.slice\n"
            "}"
        )
        # Walk each function's body separately -- each binding holds an
        # exclusive lock on its `c` for the rest of the function scope,
        # so the two forms are exercised in sibling functions to avoid
        # the (legitimate) lock conflict that two side-by-side
        # `cview source: ...` borrows of the same source would produce.
        bindings = {}
        for fn_name in ("main", "other"):
            fn = program.units[program.mainunitname].body[fn_name]
            assert fn.body is not None
            for stmt in fn.body.statements:
                sline = stmt.statementline
                if (
                    sline.nodetype == NodeType.ASSIGNMENT
                    and getattr(sline, "value", None) is not None
                    and _node_ztype(typing, sline.value) is not None
                ):
                    bindings[sline.name] = _node_ztype(typing, sline.value)
        assert "v1" in bindings, list(bindings.keys())
        assert "v2" in bindings, list(bindings.keys())
        assert bindings["v1"].name == "cview", bindings["v1"].name
        assert bindings["v2"].name == "cview", bindings["v2"].name


class TestClassConstructionLockEscape:
    """Pins for the lock-escape gap that allowed mutation of a source
    while a class instance holding a `Type.lock` field on it was alive.

    Before the fix in `_dispatch_call_construction`, plain class
    construction (`BagIter target: b.lock`) and any
    `Type.method <name>: <var>.lock` style invocation bypassed the
    `_finalize_call` lock-transfer logic, so the receiving binding did
    not retain the source lock. The receiver-as-`.lock`-param block in
    `_finalize_call` also overwrote the per-arg target with the dotted
    callable's parent path when that parent was a *type name* (a
    namespace marker, not a value), wiping out the actual source-arg
    lock. `_check_path` further dropped `borrow_target` when an
    iterator-binding expression was parenthesised. Each of the four
    cases below would have type-checked clean pre-fix.
    """

    def test_class_factory_with_lock_field_arg_retains_source_lock(self):
        """`BagIter target: b.lock` -- plain class construction with a
        `.lock`-annotated field. The binding `it` must hold an
        exclusive lock on `b` for its scope; a subsequent
        `b.x = ...` mutation is rejected."""
        errors = check_errors(
            "Bag: class { x: i64 }\n"
            "BagIter: class { target: Bag.lock } as {\n"
            "  create: function {target: Bag.lock} out this is "
            "{ return meta.create :target }\n"
            "}\n"
            "main: function is {\n"
            "  b: Bag x: 10\n"
            "  it: BagIter target: b.lock\n"
            "  b.x = 99\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'b'" in e.msg for e in errors)

    def test_static_method_call_with_lock_arg_retains_source_lock(self):
        """`Bag.iterate b: b.lock` -- static-style method-name call
        whose dotted receiver is a *type* (Bag), not a variable.
        Before the fix, the receiver-as-`.lock`-param block in
        `_finalize_call` clobbered the per-arg lock target with the
        type-name path; after the fix it only fires when the
        dotted-receiver root is actually a variable."""
        errors = check_errors(
            "Bag: class { x: i64 } as {\n"
            "  iterate: function {b: this.lock} out BagIter is "
            "{ return BagIter target: b }\n"
            "}\n"
            "BagIter: class { target: Bag.lock } as {\n"
            "  create: function {target: Bag.lock} out this is "
            "{ return meta.create :target }\n"
            "}\n"
            "main: function is {\n"
            "  b: Bag x: 10\n"
            "  it: Bag.iterate b: b.lock\n"
            "  b.x = 99\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'b'" in e.msg for e in errors)

    def test_with_block_class_factory_retains_source_lock(self):
        """`with it: (BagIter target: b.lock) do { ... }` -- the
        parenthesised iterator-binding expression must propagate
        `borrow_target` out through `_check_path` so the with-bound
        variable installs the source lock. Mutation inside the do
        body is rejected."""
        errors = check_errors(
            "Bag: class { x: i64 }\n"
            "BagIter: class { target: Bag.lock } as {\n"
            "  create: function {target: Bag.lock} out this is "
            "{ return meta.create :target }\n"
            "}\n"
            "main: function is {\n"
            "  b: Bag x: 10\n"
            "  with it: (BagIter target: b.lock) do {\n"
            "    b.x = 99\n"
            "  }\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'b'" in e.msg for e in errors)

    def test_lock_released_after_class_factory_binding_drops(self):
        """The lock the class-factory binding retains is scoped, not
        permanent. Once the binding's scope exits, the source is
        mutable again."""
        check_ok(
            "Bag: class { x: i64 }\n"
            "BagIter: class { target: Bag.lock } as {\n"
            "  create: function {target: Bag.lock} out this is "
            "{ return meta.create :target }\n"
            "}\n"
            "main: function is {\n"
            "  b: Bag x: 10\n"
            "  { it: BagIter target: b.lock }\n"
            "  b.x = 99\n"
            "}"
        )


class TestPhaseC3Pins:
    """Phase C-3 pin tests: per-call sub-scope semantics.

    These pin behaviour established by commits 7c63562 (call-identity
    stack), 7757ccc (per-arg hoisting), and bafd598 (receiver-param
    detection). Without those changes the tests here would silently
    regress (call locks leaking across statements, args evaluated in
    arbitrary order, etc.).
    """

    def _reader_and_myfile(self) -> str:
        return (
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
        )

    def test_call_subscope_releases_borrow_lock(self):
        """Two consecutive call statements that each borrow `f` via
        a hoisted `f.myreader` projection both succeed because each
        call's sub-scope releases the borrow on close. If the lock
        leaked past the first statement, the second would fail with
        'already has exclusive lock on f'.
        """
        check_ok(
            self._reader_and_myfile() + "use_reader: function {r: Reader} is {}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    use_reader r: f.myreader\n"
            "    use_reader r: f.myreader\n"
            "}"
        )

    def test_chained_method_call_hoists_recursively(self):
        """An arg that is itself a call hoists into a synth temp; the
        outer call then hoists its own arg-temp on top. Both temps
        carry synth_origin == 'anf'.
        """
        program, typing = check_ok(
            "make_val: function {x: i64} out i64 is { return x + 1 }\n"
            "consume: function {v: i64} is {}\n"
            "main: function is {\n"
            "    consume v: (make_val x: 5)\n"
            "}"
        )
        # The synth Assignment lives in main's body as a preamble line
        # injected before the consume() call. Walk the body and assert
        # at least one synth_origin=='anf' Assignment is present.
        main_body = program.units[program.mainunitname].body["main"].body
        synth_assignments = [
            s.statementline
            for s in main_body.statements
            if getattr(s, "synth_origin", None) == "anf"
            and s.statementline.nodetype == NodeType.ASSIGNMENT
        ]
        assert len(synth_assignments) >= 1, (
            f"expected at least one synth ANF assignment in body; "
            f"got {[type(s.statementline).__name__ for s in main_body.statements]}"
        )

    def test_borrow_returning_method_passed_as_take_rejected(self):
        """A user method with `t: this.lock` receiver returns a
        borrowed value. Passing that value to a `String.take`
        parameter must be rejected — the borrow carries an outstanding
        lock on its source which cannot transfer ownership.
        """
        errors = check_errors(
            "holder: class {\n"
            "    val: String\n"
            "} as {\n"
            "    pick: function {t: this.lock} out String.borrow is {\n"
            "        return t.val\n"
            "    }\n"
            "}\n"
            "consume: function {s: String.take} is {}\n"
            "main: function is {\n"
            '    h: holder val: "hi".string\n'
            "    consume s: h.pick\n"
            "}"
        )
        assert any(
            "borrow" in e.msg.lower() or "lock" in e.msg.lower() for e in errors
        ), [e.msg for e in errors]

    def test_left_to_right_arg_hoist_order(self):
        """Args hoist left-to-right: the synth Assignment for the first
        non-trivial arg appears in the preamble before the second.
        Pinned by walking the post-typecheck AST.
        """
        program, typing = check_ok(
            "make_val: function {x: i64} out i64 is { return x + 1 }\n"
            "consume2: function {a: i64 b: i64} is {}\n"
            "main: function is {\n"
            "    consume2 a: (make_val x: 1) b: (make_val x: 2)\n"
            "}"
        )
        main_body = program.units[program.mainunitname].body["main"].body
        synth_assigns = [
            s.statementline
            for s in main_body.statements
            if getattr(s, "synth_origin", None) == "anf"
            and s.statementline.nodetype == NodeType.ASSIGNMENT
        ]
        # Two non-trivial args -> at least two synth temps in source
        # order. Inspect their RHS const_value (1+1 then 2+1) to confirm
        # the first temp captures arg `a` and the second captures `b`.
        assert len(synth_assigns) >= 2, [
            type(s.statementline).__name__ for s in main_body.statements
        ]
        # First synth assigns the lhs of the consume2 call (a:),
        # second assigns rhs (b:). Names follow the FreshNamer
        # left-to-right counter, so the first temp's number is lower.
        names = [a.name for a in synth_assigns[:2]]
        # both should start with the synth prefix and the first should
        # sort before the second (numeric counter behind the prefix).
        assert all(n.startswith("_t") for n in names), names
        assert int(names[0][2:]) < int(names[1][2:]), names

    def test_unknown_unit_member_errors(self):
        """Phase D: a dotted Path through a known unit with an unknown
        child must error (the leak that let `io.read_only` slip through
        as a call argument before the fix in `_resolve_dotted_path`).
        """
        errors = check_errors("main: function is { x: io.read_only }")
        assert any("io" in e.msg and "read_only" in e.msg for e in errors), [
            e.msg for e in errors
        ]

    def test_unknown_unit_member_in_call_arg_errors(self):
        """Same leak surfaced through a call's named arg — what the
        regression test that motivated Phase D was relying on.
        """
        errors = check_errors(
            "main: function is {\n"
            '    with f: (io.open path: "/tmp/x" mode: io.read_only) do {}\n'
            "}"
        )
        assert any("io" in e.msg and "read_only" in e.msg for e in errors), [
            e.msg for e in errors
        ]

    def test_destructor_metadata_preserved_through_hoist(self):
        """An un-taken reftype temp must end up with a needs_destructor
        type, so the scope-exit machinery cleans it up. Pin via the
        type metadata on the synth Assignment's RHS.
        """
        program, typing = check_ok(
            "make_str: function {tag: i64} out String is {\n"
            '    return "hi".string\n'
            "}\n"
            "use_borrow: function {s: String.borrow} is {}\n"
            "main: function is {\n"
            "    use_borrow s: (make_str tag: 1)\n"
            "}"
        )
        main_body = program.units[program.mainunitname].body["main"].body
        synth_assigns = [
            s.statementline
            for s in main_body.statements
            if getattr(s, "synth_origin", None) == "anf"
            and s.statementline.nodetype == NodeType.ASSIGNMENT
        ]
        assert len(synth_assigns) >= 1, "expected synth temp for hoisted call arg"
        temp_assn = synth_assigns[0]
        # The temp's bound type is `string`, a reftype with
        # needs_destructor == True. Pin it.
        temp_t = _node_ztype(typing, temp_assn)
        assert temp_t is not None
        assert (temp_t.destructor_name is not None) is True, (
            f"temp type {temp_t.name} should need a destructor"
        )


class TestSyntacticHooksDeletion:
    """Pin: the typecheck-side syntactic hooks that previously injected
    method semantics (`_INLINE_LOCK_PROJECTIONS`, the
    `child_name == \"ByteView\"|\"ListView\"|\"StringView\"` branches in
    `_resolve_dotted_path`, the integer `.iterate`/`.each` synth) are
    gone. Aggregate-escape rejection now flows through standard
    return-ownership / `.lock`-param metadata. Reintroducing Any of
    these hooks should fail this test class first.
    """

    def test_no_inline_lock_projections_constant(self):
        """The constant must not be reintroduced. Aggregate-escape
        Case 1 reads return-ownership metadata exclusively."""
        with open(SRC_TYPECHECK_PATH) as f:
            src = f.read()
        assert "_INLINE_LOCK_PROJECTIONS" not in src

    def test_no_view_method_syntactic_branches(self):
        """No `child_name == \"ByteView\"` / `\"ListView\"` /
        `\"StringView\"` syntactic branches survive in the typechecker.
        These projections all resolve through their native declarations
        in `lib/system/*.z` and propagate locks via the standard
        `.lock`-param transfer."""
        with open(SRC_TYPECHECK_PATH) as f:
            src = f.read()
        for name in ("ByteView", "ListView", "StringView"):
            assert f'child_name == "{name}"' not in src, (
                f'syntactic branch on child_name == "{name}" found in '
                f"src/ztypecheck.py — this projection must route through "
                f"the native declaration's metadata path"
            )

    def test_no_integer_iterate_each_synth(self):
        """Integer `.iterate` / `.each` are declared natively per
        integer record in lib/system/system.z (commit 090f45a). The
        synthetic dispatch branch must not be reintroduced."""
        with open(SRC_TYPECHECK_PATH) as f:
            src = f.read()
        assert 'child_name in ("each", "iterate")' not in src
        assert 'child_name in ("iterate", "each")' not in src


class TestTakeBorrowCompilerMethods:
    """.take and .borrow compiler methods."""

    def test_take_resolves(self):
        """x.take should resolve to x's type."""
        check_ok("main: function is {\n  x: 42\n  y: x.take\n}")

    def test_borrow_resolves(self):
        """x.borrow should resolve to x's type (reftype)."""
        check_ok(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n}"
        )

    def test_take_invalidates_name(self):
        """After x.take, x should be invalid (ownership transferred)."""
        errors = check_errors("main: function is {\n  x: 42\n  y: x.take\n  z: x\n}")
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )

    def test_user_method_shadows_take_intrinsic(self):
        """A user-defined .take method on a class shadows the intrinsic.

        Without resolution-order inversion the intrinsic would fire and
        invalidate x; the subsequent x.value access would error. With
        the user method shadowing, x stays a live variable.
        """
        check_ok(
            "mybox: class {\n"
            "  value: 0u64\n"
            "} as {\n"
            "  take: function {:this} out u64 is { return 42u64 }\n"
            "}\n"
            "main: function is {\n"
            "  x: mybox\n"
            "  y: x.take\n"
            "  z: x.value\n"
            "}"
        )


class TestReleaseCompilerMethod:
    """.release compiler method."""

    def test_release_owned_valtype(self):
        """x.release on an owned valtype should succeed."""
        check_ok("main: function is {\n  x: 42\n  x.release\n}")

    def test_release_invalidates_name(self):
        """After x.release, x should be invalid."""
        errors = check_errors("main: function is {\n  x: 42\n  x.release\n  y: x\n}")
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )

    def test_release_borrowed_reftype(self):
        """Releasing a borrowed reftype ends the borrow."""
        check_ok(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  y.release\n}"
        )

    def test_source_unlocked_after_borrow_release(self):
        """After releasing a borrow, the source is unlocked and usable."""
        check_ok(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  y.release\n  z: x.take\n}"
        )

    def test_release_parameter_ok(self):
        """Releasing a parameter is allowed (same as .take on a parameter)."""
        check_ok("main: function {a: i64} is {\n  a.release\n}")

    def test_cannot_release_locked_variable(self):
        """Cannot release a variable that has a lock held by someone else."""
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  x.release\n}"
        )
        assert any(
            "release" in e.msg.lower() and "lock" in e.msg.lower() for e in errors
        )

    def test_cannot_release_top_level_definition(self):
        """Cannot release a top-level definition."""
        errors = check_errors("f: function is {}\nmain: function is {\n  f.release\n}")
        assert any(
            "release" in e.msg.lower() and "top-level" in e.msg.lower() for e in errors
        )

    def test_release_as_value_is_error(self):
        """y: x.release should be an error."""
        errors = check_errors("main: function is {\n  x: 42\n  y: x.release\n}")
        assert any(
            "release" in e.msg.lower() and "value" in e.msg.lower() for e in errors
        )

    def test_release_non_variable_path_is_error(self):
        """field.release on a non-simple variable should be an error."""
        errors = check_errors(
            "point: record { x: i64 y: i64 }\n"
            "main: function is {\n"
            "  p: point x: 1 y: 2\n"
            "  p.x.release\n"
            "}"
        )
        assert any("release" in e.msg.lower() for e in errors)

    def test_double_release(self):
        """Releasing a variable twice is an error (second sees undefined name)."""
        errors = check_errors(
            "main: function is {\n  x: 42\n  x.release\n  x.release\n}"
        )
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )


class TestSwapOwnership:
    """Test swap ownership rules."""

    def test_swap_valtype_ok(self):
        """Swap of owned valtype variables is OK."""
        check_ok("main: function is {\n  a: 10\n  b: 20\n  a swap b\n}")


class TestArgHoistInfrastructure:
    """Phase C step 2 commit 1: dormant preamble + hoist helper.

    Helpers are not yet wired into _check_call; these tests exercise
    `_arg_is_trivial` and `_hoist_arg` directly to pin their shape
    before commit 2 turns the wiring on.
    """

    def _fresh_checker(self):
        """Build a TypeChecker over a minimal program and prime its
        symtab with a function scope so define_var has somewhere to land.
        """
        program, typing, errors = parse_and_check("main: function is {}")
        assert errors == []
        tc = TypeChecker(program)
        tc.check()
        # push a scope so _hoist_arg's define_var has a target
        tc.symtab.push("test")
        return tc

    def test_arg_is_trivial_atom_id(self):
        tc = self._fresh_checker()
        atom = zast.AtomId(name="x", start=None)
        arg = zast.NamedOperation(name=None, valtype=atom, start=None)
        assert tc._arg_is_trivial(arg) is True

    def test_arg_is_trivial_call_is_not(self):
        tc = self._fresh_checker()
        # Expression wrapping a Call counts as non-trivial.
        call = zast.Call(
            callable=zast.AtomId(name="f", start=None), arguments=[], start=None
        )
        expr = zast.Expression(expression=call, start=None)
        arg = zast.NamedOperation(name=None, valtype=expr, start=None)
        assert tc._arg_is_trivial(arg) is False

    def test_arg_is_trivial_dotted_path_is_not(self):
        tc = self._fresh_checker()
        dp = zast.DottedPath(
            parent=zast.AtomId(name="obj", start=None),
            child=zast.AtomId(name="field", start=None),
            start=None,
        )
        expr = zast.Expression(expression=dp, start=None)
        arg = zast.NamedOperation(name=None, valtype=expr, start=None)
        assert tc._arg_is_trivial(arg) is False

    def test_hoist_appends_to_preamble_and_rewrites_arg(self):
        """_hoist_arg pushes a synth Assignment into the topmost
        preamble entry, registers a ZVariable, and rewrites
        arg.valtype to an AtomId."""
        tc = self._fresh_checker()
        tc._call_preamble.append([])
        # build a non-trivial arg: Expression(DottedPath(obj.field))
        dp = zast.DottedPath(
            parent=zast.AtomId(name="obj", start=None),
            child=zast.AtomId(name="field", start=None),
            start=None,
        )
        original_expr = zast.Expression(expression=dp, start=None)
        arg = zast.NamedOperation(name="a", valtype=original_expr, start=None)
        # any ZType works for the test — just assert plumbing
        u64 = tc._resolve_name("u64")
        assert u64 is not None
        name = tc._hoist_arg(arg, u64, arg_borrow_path=None)
        # 1) preamble grew
        assert len(tc._call_preamble[-1]) == 1
        synth_line = tc._call_preamble[-1][0]
        assert synth_line.synth_origin == "anf"
        # 2) arg.valtype was rewritten to an AtomId pointing at the temp
        assert arg.valtype.nodetype == NodeType.ATOMID
        assert arg.valtype.name == name
        assert arg.valtype.synth_origin == "anf"
        # 3) the temp is registered in the symtab as an OWNED variable
        var = tc.symtab.lookup_var(name)
        assert var is not None
        assert var.ownership == ZOwnership.OWNED
        assert var.synth_origin == "anf"
        # 4) name follows the prefix convention
        assert name.startswith("_t")

    def test_hoist_borrow_source_marks_temp_borrowed(self):
        tc = self._fresh_checker()
        tc._call_preamble.append([])
        dp = zast.DottedPath(
            parent=zast.AtomId(name="src", start=None),
            child=zast.AtomId(name="borrow", start=None),
            start=None,
        )
        original_expr = zast.Expression(expression=dp, start=None)
        arg = zast.NamedOperation(name=None, valtype=original_expr, start=None)
        u64 = tc._resolve_name("u64")
        assert u64 is not None
        name = tc._hoist_arg(arg, u64, arg_borrow_path=("src",))
        var = tc.symtab.lookup_var(name)
        assert var is not None
        assert var.ownership == ZOwnership.BORROWED
        assert var.borrow_origin == "src"


class TestBorrowValtypeAllowed:
    """.borrow on valtypes is allowed (produces a copy, no lock).
    .lock on valtype PARAMETERS is an error (locking requires identity).
    """

    def test_borrow_valtype_inline_ok(self):
        """x.borrow on a valtype produces a borrowed copy (no lock)."""
        check_ok("main: function is {\n  x: 42\n  y: x.borrow\n}")

    def test_lock_valtype_inline_ok(self):
        """x.lock on a valtype produces a borrowed copy (no lock)."""
        check_ok("main: function is {\n  x: 42\n  y: x.lock\n}")

    def test_borrow_valtype_param_ok(self):
        """.borrow on a valtype parameter is allowed (redundant but valid)."""
        check_ok("f: function {a: i64.borrow} is {}\nmain: function is {}")

    def test_lock_valtype_param_error(self):
        """.lock on a valtype parameter is an error (locking requires identity)."""
        errors = check_errors(
            "f: function {a: i64.lock} out i64 is { return a }\nmain: function is {}"
        )
        assert any("'.lock'" in e.msg and "valtype" in e.msg for e in errors)

    def test_valtype_default_is_borrow(self):
        """Valtype params default to borrow — source not invalidated."""
        check_ok(
            "f: function {x: i64} is {}\n"
            'main: function is {\n  a: 42\n  f x: a\n  print "\\{a}"\n}'
        )

    def test_valtype_explicit_take_invalidates(self):
        """Explicit .take on valtype param invalidates source at call site."""
        errors = check_errors(
            "f: function {x: i64.take} is {}\n"
            'main: function is {\n  a: 42\n  f x: a\n  print "\\{a}"\n}'
        )
        assert any("ownership transfer" in e.msg.lower() for e in errors)

    def test_borrow_generic_param_allowed(self):
        """Generic parameters may monomorphize to reftype — .borrow is allowed."""
        check_ok(
            "identity: function {a: t.borrow} out t as { t: Any.generic } is {\n"
            "  return a\n"
            "}\n"
            "main: function is {}"
        )

    def test_lock_generic_param_allowed(self):
        """Generic parameters may monomorphize to reftype — .lock is allowed."""
        check_ok(
            "identity: function {a: t.lock} out t.borrow as { t: Any.generic } is {\n"
            "  return a\n"
            "}\n"
            "main: function is {}"
        )

    def test_field_read_from_borrowed_class_ok(self):
        # Reading a valtype field through a borrowed class produces a fresh
        # value and is freely passable.
        check_ok(
            "Box: class { v: i64 }\n"
            "f: function {b: Box} out i64 is { return b.v }\n"
            "main: function is {\n"
            "  c: Box v: 5\n"
            "  r: f c\n"
            "}"
        )


class TestBorrowedReftypeRestrictions:
    """Borrow restrictions for reftypes (classes).

    Once a reftype is BORROWED (via .borrow inline method or a .borrow/.lock
    function parameter), it cannot be copied to a new name, taken, swapped,
    reassigned, or passed where a copy/take is expected.
    """

    def test_borrowed_reftype_can_be_borrowed_again(self):
        check_ok(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  z: y.borrow\n}"
        )

    def test_borrowed_reftype_cannot_be_taken(self):
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  y.take\n}"
        )
        assert any(
            "borrowed" in e.msg.lower() and "take" in e.msg.lower() for e in errors
        )

    def test_borrowed_reftype_cannot_be_swapped(self):
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "main: function is {\n"
            "  x: myclass\n  y: myclass\n  z: x.borrow\n  z swap y\n"
            "}"
        )
        assert any(
            "swap" in e.msg.lower() and "borrowed" in e.msg.lower() for e in errors
        )

    def test_borrowed_reftype_cannot_be_reassigned(self):
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  y = myclass\n}"
        )
        assert any(
            "reassign" in e.msg.lower() and "borrowed" in e.msg.lower() for e in errors
        )

    def test_borrowed_reftype_cannot_be_passed_to_take_param(self):
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.take} is {}\n"
            "main: function is {\n"
            "  x: myclass\n"
            "  y: x.borrow\n"
            "  f y\n"
            "}"
        )
        assert any("borrowed" in e.msg.lower() for e in errors)

    def test_borrowed_reftype_can_be_passed_to_borrow_param(self):
        check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.borrow} is {}\n"
            "main: function is {\n"
            "  x: myclass\n"
            "  y: x.borrow\n"
            "  f y\n"
            "}"
        )

    def test_owned_reftype_passed_to_borrow_is_downgrade(self):
        check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.borrow} is {}\n"
            "main: function is {\n  x: myclass\n  f x\n}"
        )

    def test_lock_param_body_is_borrowed(self):
        # .lock parameters are also borrowed in the body. Returning the
        # original is fine; copying is not.
        check_ok(
            "myclass: class { value: 0 }\n"
            "f: function {a: myclass.lock} out myclass.borrow is { return a }\n"
            "main: function is {}"
        )


class TestLockFieldsAndBornBorrowedRemoved:
    """Born-borrowed records have been removed; the equivalent functionality
    is provided by classes with .lock fields. These tests verify that the
    obsolete forms now produce errors directing users to classes."""

    def test_lock_field_on_record_error(self):
        # A .lock field on a record is no longer accepted.
        errors = check_errors(
            "bag: class { a: i64 }\n"
            "badrec: record { target: bag.private.lock } as {\n"
            "  create: function {target: bag.private.lock} out this is {\n"
            "    return meta.create target: target\n"
            "  }\n"
            "}\n"
            "main: function is { b: bag a: 1; r: badrec target: b.private }"
        )
        assert any("'.lock'" in e.msg and "class" in e.msg.lower() for e in errors)

    def test_this_borrow_constructor_on_record_error(self):
        # A constructor returning this.borrow on a record is no longer
        # accepted. Use a class with .lock fields instead.
        errors = check_errors(
            "v: record { x: i64 } as {\n"
            "  create: function out this.borrow is { return meta.create x: 0 }\n"
            "}\n"
            "main: function is { r: v.create }"
        )
        assert any("this.borrow" in e.msg and "class" in e.msg.lower() for e in errors)

    def test_lock_field_on_class_allowed(self):
        # Phase 7: classes may have .lock fields (stack-allocated, single-owner)
        check_ok(
            "bag: class { a: i64 }\n"
            "bagview: class { target: bag.lock } as {\n"
            "  create: function {target: bag.lock} out this is {\n"
            "    return meta.create target: target\n"
            "  }\n"
            "}\n"
            "main: function is { b: bag a: 1\nv: bagview target: b }"
        )

    def test_take_field_modifier_on_record_error(self):
        # Only .lock is permitted on field types; .take/.borrow are not.
        errors = check_errors(
            "v: record { f: i64.take }\nmain: function is { x: v f: 0 }"
        )
        assert any("only '.lock'" in e.msg.lower() for e in errors)


class TestImplicitConstruction:
    """Unified call-dispatch for types in callable position.

    Rule: a type in callable position dispatches through children["create"].
    If the type is a record/class with a user-defined create, that function
    is invoked. If the type is a union/variant (create_disabled), bare-name
    construction is rejected with a subtype hint. If the type's create has
    been explicitly disabled with 'create: null', bare-name and explicit
    .create both error. Inside a type's own create body, calling the type's
    own create (directly or via bare-name shorthand) is compile-time
    recursion — users must use 'meta.create' instead.
    """

    def test_bare_name_record_default_fields(self):
        """Regression: bare-name construction with default meta-create."""
        check_ok(
            "point: record { x: i64 y: i64 }\nmain: function is { p: point x: 1 y: 2 }"
        )

    def test_bare_name_record_custom_create_signature(self):
        """Bare-name validates against the custom create signature."""
        check_ok(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  create: function {seed: i64} out this is {\n"
            "    return meta.create x: seed y: seed\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec seed: 5 }"
        )

    def test_bare_name_record_custom_create_wrong_arg_error(self):
        """Calling a custom create with a wrong arg name errors."""
        errors = check_errors(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  create: function {seed: i64} out this is {\n"
            "    return meta.create x: seed y: seed\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec.create wrong: 5 }"
        )
        assert errors != []

    def test_bare_name_class_custom_create_signature(self):
        """Bare-name on a class also validates against custom create."""
        check_ok(
            "thing: class { val: i64 } as {\n"
            "  create: function {seed: i64} out this is {\n"
            "    return meta.create val: seed\n"
            "  }\n"
            "}\n"
            "main: function is { t: thing seed: 5 }"
        )

    def test_union_bare_name_rejected(self):
        """Bare-name on a union type is rejected with a subtype hint."""
        errors = check_errors(
            "myunion: union { A: i64 B: i64 }\nmain: function is { u: myunion 42 }"
        )
        assert any(
            "union" in e.msg.lower() and "subtype" in e.msg.lower() for e in errors
        )

    def test_variant_bare_name_rejected(self):
        """Bare-name on a variant type is rejected with a subtype hint."""
        errors = check_errors(
            "myvariant: variant { A: i64 B: i64 }\n"
            "main: function is { v: myvariant 42 }"
        )
        assert any(
            "variant" in e.msg.lower() and "subtype" in e.msg.lower() for e in errors
        )

    def test_create_null_disables_bare_name(self):
        """`create: null` rejects bare-name construction."""
        errors = check_errors(
            "myrec: record { x: i64 } as { create: null }\n"
            "main: function is { r: myrec x: 1 }"
        )
        assert any(
            "disabled" in e.msg.lower() or "create" in e.msg.lower() for e in errors
        )

    def test_create_null_disables_explicit_create(self):
        """`create: null` also rejects the explicit .create form."""
        errors = check_errors(
            "myrec: record { x: i64 } as {\n"
            "  create: null\n"
            "  build: function {v: i64} out this is {\n"
            "    return meta.create x: v\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec.create x: 1 }"
        )
        assert errors != []

    def test_create_null_alternate_constructor_ok(self):
        """With create: null, a named alternate constructor still works."""
        check_ok(
            "myrec: record { x: i64 } as {\n"
            "  create: null\n"
            "  build: function {v: i64} out this is {\n"
            "    return meta.create x: v\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec.build v: 5 }"
        )

    def test_recursion_detection_explicit(self):
        """Calling Type.create inside Type.create is compile-time recursion."""
        errors = check_errors(
            "myrec: record { x: i64 } as {\n"
            "  create: function {v: i64} out this is {\n"
            "    return myrec.create v: v\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec.create v: 1 }"
        )
        assert any(
            "recursively" in e.msg.lower() or "meta.create" in e.msg.lower()
            for e in errors
        )

    def test_recursion_detection_bare_name(self):
        """Bare-name Type inside Type.create is also recursion."""
        errors = check_errors(
            "myrec: record { x: i64 } as {\n"
            "  create: function {v: i64} out this is {\n"
            "    return myrec x: v\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec.create v: 1 }"
        )
        assert any(
            "recursively" in e.msg.lower() or "meta.create" in e.msg.lower()
            for e in errors
        )

    def test_meta_create_inside_body_ok(self):
        """meta.create inside a custom create body resolves to the raw allocator."""
        check_ok(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  create: function {seed: i64} out this is {\n"
            "    return meta.create x: seed y: seed + 1\n"
            "  }\n"
            "}\n"
            "main: function is { r: myrec seed: 5 }"
        )

    def test_meta_create_top_level_error(self):
        """meta.create outside Any type body is an error."""
        errors = check_errors("main: function is { r: meta.create x: 1 }")
        assert any("meta.create" in e.msg for e in errors)


# Note: TestExamplePrograms and TestExampleProgramsOwnership were removed —
# they parsed+type-checked the same examples that TestEmitterExamples
# (test_emitter.py) compiles and runs end-to-end. The emitter pass subsumes
# them (it has to type-check first to emit). See plan
# /home/pawe/.claude/plans/for-zerolang-do-a-expressive-narwhal.md (B1).


# ---- Phase 4d: Lock Checking Tests ----


class TestZLockInfo:
    """Test the ZLockInfo dataclass."""

    def test_lock_info_exclusive(self):
        from ztypes import ZLockInfo, ZLockHolder, ZLockHolderKind

        h = ZLockHolder(ZLockHolderKind.VAR, 7)
        e = ZLockInfo(lock_type=ZLockState.EXCLUSIVE, holder=h)
        assert e.lock_type == ZLockState.EXCLUSIVE
        assert e.holder == h

    def test_lock_info_shared(self):
        from ztypes import ZLockInfo, ZLockHolder, ZLockHolderKind

        h = ZLockHolder(ZLockHolderKind.CALL, 42)
        e = ZLockInfo(lock_type=ZLockState.SHARED, holder=h)
        assert e.lock_type == ZLockState.SHARED
        assert e.holder == h


class TestZSymbolTableLocking:
    """Test lock operations on the symbol table."""

    @staticmethod
    def _h(n: int):
        """Make a synthetic ZLockHolder for tests. Distinct n → distinct
        holder identity."""
        from ztypes import ZLockHolder, ZLockHolderKind

        return ZLockHolder(ZLockHolderKind.VAR, n)

    def _make_symtab_with_vars(self, *names):
        from zenv import ZSymbolTable
        from ztypes import ZType

        st = ZSymbolTable()
        st.push("test")
        t = ZType(name="myclass", typetype=ZTypeType.UNION, parent=None)
        t.is_valtype = False
        for name in names:
            var = ZVariable(ztype=t, ownership=ZOwnership.OWNED)
            st.define_var(name, var)
        return st

    def test_try_lock_exclusive_on_unlocked(self):
        st = self._make_symtab_with_vars("x", "y")
        err = st.try_lock(("x",), ZLockState.EXCLUSIVE, self._h(1))
        assert err is None
        lock = st.find_lock("x")
        assert lock is not None
        assert lock.lock_type == ZLockState.EXCLUSIVE

    def test_try_lock_exclusive_on_exclusive_fails(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock(("x",), ZLockState.EXCLUSIVE, self._h(1))
        assert err is None
        err = st.try_lock(("x",), ZLockState.EXCLUSIVE, self._h(2))
        assert err is not None
        assert "exclusive" in err.lower()

    def test_try_lock_shared_on_shared_ok(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock(("x",), ZLockState.SHARED, self._h(1))
        assert err is None
        err = st.try_lock(("x",), ZLockState.SHARED, self._h(2))
        assert err is None
        # shared + shared is OK (deduplicated to single entry)
        lock = st.find_lock("x")
        assert lock is not None
        assert lock.lock_type == ZLockState.SHARED

    def test_try_lock_shared_on_exclusive_fails(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock(("x",), ZLockState.EXCLUSIVE, self._h(1))
        assert err is None
        err = st.try_lock(("x",), ZLockState.SHARED, self._h(2))
        assert err is not None
        assert "exclusive" in err.lower()

    def test_try_lock_exclusive_on_shared_fails(self):
        st = self._make_symtab_with_vars("x", "y", "z")
        err = st.try_lock(("x",), ZLockState.SHARED, self._h(1))
        assert err is None
        err = st.try_lock(("x",), ZLockState.EXCLUSIVE, self._h(2))
        assert err is not None

    def test_lock_released_by_scope_pop(self):
        """Locks are released when the scope containing them is popped."""
        st = self._make_symtab_with_vars("x", "y")
        st.try_lock(("x",), ZLockState.EXCLUSIVE, self._h(1))
        assert st.find_lock("x") is not None
        st.pop()
        assert st.find_lock("x") is None

    def test_release_held_locks(self):
        st = self._make_symtab_with_vars("x", "y")
        h = self._h(1)
        st.try_lock(("x",), ZLockState.EXCLUSIVE, h)
        assert st.find_lock("x") is not None
        st.release_held_locks(h)
        assert st.find_lock("x") is None

    # --- path-scoped lock semantics (Commit D) ---

    def test_sibling_paths_do_not_conflict(self):
        """EXCLUSIVE on (obj, a) and EXCLUSIVE on (obj, b) both succeed —
        sibling paths have no prefix relation and cannot conflict."""
        st = self._make_symtab_with_vars("obj", "h1", "h2")
        assert st.try_lock(("obj", "a"), ZLockState.EXCLUSIVE, self._h(1)) is None
        assert st.try_lock(("obj", "b"), ZLockState.EXCLUSIVE, self._h(2)) is None

    def test_ancestor_exclusive_blocks_descendant_lock(self):
        """EXCLUSIVE on (obj,) owns the whole subtree — Any new lock below
        is rejected with a Path-aware message."""
        st = self._make_symtab_with_vars("obj", "h1", "h2")
        assert st.try_lock(("obj",), ZLockState.EXCLUSIVE, self._h(1)) is None
        err = st.try_lock(("obj", "a"), ZLockState.EXCLUSIVE, self._h(2))
        assert err is not None
        assert "'obj'" in err
        assert "exclusive" in err.lower()

    def test_descendant_lock_blocks_ancestor_exclusive(self):
        """A new EXCLUSIVE on an ancestor would absorb Any outstanding
        sub-lock; rejected."""
        st = self._make_symtab_with_vars("obj", "h1", "h2")
        assert st.try_lock(("obj", "a"), ZLockState.EXCLUSIVE, self._h(1)) is None
        err = st.try_lock(("obj",), ZLockState.EXCLUSIVE, self._h(2))
        assert err is not None
        assert "'obj'" in err

    def test_shared_ancestor_permits_exclusive_descendant(self):
        """Multi-granularity: SHARED on an ancestor is INTENT-shared and
        allows Any lock (S or X) on descendants. This is what lets a
        single operation install SHARED on each intermediate prefix and
        EXCLUSIVE on the leaf without self-conflict."""
        st = self._make_symtab_with_vars("obj", "h1")
        h = self._h(1)
        assert st.try_lock(("obj",), ZLockState.SHARED, h) is None
        assert st.try_lock(("obj", "a"), ZLockState.EXCLUSIVE, h) is None

    def test_shared_stacking_and_idempotence(self):
        """Two SHARED on the same full Path dedupe (no extra entry); SHARED
        on ancestor and SHARED on descendant both live (distinct entries,
        independent release)."""
        st = self._make_symtab_with_vars("obj", "h1", "h2")
        assert st.try_lock(("obj",), ZLockState.SHARED, self._h(1)) is None
        # same path: idempotent, still no conflict, no second entry
        assert st.try_lock(("obj",), ZLockState.SHARED, self._h(2)) is None
        # descendant: both live
        assert st.try_lock(("obj", "a"), ZLockState.SHARED, self._h(2)) is None


class TestLockCheckingBorrow:
    """Test lock checking for .borrow compiler method."""

    def test_borrow_ok(self):
        """y: x.borrow should work without errors (reftype)."""
        check_ok(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n}"
        )

    def test_chained_borrow_ok(self):
        """y: x.borrow, z: y.borrow should work (z locks y which locks x)."""
        check_ok(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  z: y.borrow\n}"
        )

    def test_double_borrow_same_var_error(self):
        """Cannot borrow x twice — second borrow conflicts with existing exclusive lock."""
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  z: x.borrow\n}"
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


class TestLockEnforcement:
    """Mutation-site enforcement of outstanding exclusive Borrow-scoped Locks.

    While a variable holds an exclusive Borrow-scoped Lock (e.g. from
    `.borrow` or `String.stringview`), the compiler must reject reassignment,
    field reassignment, swap, and method calls whose Path is rooted at the
    locked variable. See doc/ownership.pdoc, Mutation-Site Enforcement.
    """

    def test_reassign_locked_var_rejected(self):
        """Reassigning a var holding an outstanding view lock must error."""
        errors = check_errors(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  v: s.stringview\n"
            '  s = "world".string\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_reassign_locked_reftype_var_rejected(self):
        """Same rule applies to borrowed reftype via .borrow."""
        errors = check_errors(
            "myclass: class { value: 0 }\n"
            "main: function is {\n  x: myclass\n  y: x.borrow\n  x = myclass\n}"
        )
        assert any("exclusive lock" in e.msg.lower() for e in errors)

    def test_field_reassign_of_locked_leaf_rejected(self):
        """Reassigning the locked leaf field errors — the view installs an
        EXCLUSIVE Borrow-scoped Lock on the leaf Path `(p, name)`."""
        errors = check_errors(
            "namepair: record { name: String other: String }\n"
            "main: function is {\n"
            '  p: namepair name: "a".string other: "b".string\n'
            "  v: p.name.stringview\n"
            '  p.name = "c".string\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'p'" in e.msg for e in errors)

    def test_sibling_field_reassign_permitted(self):
        """Path-scoped locks: reassigning a sibling field is permitted while
        another field is borrowed."""
        check_ok(
            "namepair: class { name: String other: i64 }\n"
            "main: function is {\n"
            '  p: namepair name: "a".string other: 0\n'
            "  v: p.name.stringview\n"
            "  p.other = 3\n"
            "}"
        )

    def test_swap_rejects_locked_var(self):
        """Swap with a locked var on either side errors."""
        errors = check_errors(
            "main: function is {\n"
            '  x: "hello".string\n'
            '  a: "world".string\n'
            "  v: x.stringview\n"
            "  x swap a\n"
            "}"
        )
        assert any(
            "exclusive lock" in e.msg.lower() and "swap" in e.msg.lower()
            for e in errors
        )

    def test_swap_rejects_locked_leaf(self):
        """Swap on the locked leaf Path errors — sibling-field swaps are OK."""
        errors = check_errors(
            "namepair: record { name: String other: String }\n"
            "main: function is {\n"
            '  p: namepair name: "a".string other: "b".string\n'
            '  a: "c".string\n'
            "  v: p.name.stringview\n"
            "  p.name swap a\n"
            "}"
        )
        assert any(
            "exclusive lock" in e.msg.lower() and "swap" in e.msg.lower()
            for e in errors
        )

    def test_swap_sibling_field_permitted(self):
        """Sibling-field swap is permitted while another field is borrowed."""
        check_ok(
            "namepair: class { name: String other: String }\n"
            "main: function is {\n"
            '  p: namepair name: "a".string other: "b".string\n'
            '  a: "c".string\n'
            "  v: p.name.stringview\n"
            "  p.other swap a\n"
            "}"
        )

    def test_method_call_on_locked_receiver_rejected(self):
        """Calling a method on a locked receiver errors (existing _lock_receiver
        behavior; guards against B3 regression)."""
        errors = check_errors(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  v: s.stringview\n"
            '  s.append " world"\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() for e in errors)

    def test_mutation_ok_after_release(self):
        """After explicit .release of the view, the source becomes mutable."""
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  v: s.stringview\n"
            "  v.release\n"
            '  s = "world".string\n'
            "}"
        )

    def test_method_call_with_arg_derived_from_receiver_allowed(self):
        """Call-identity stack (Phase C step 1): a method call whose
        argument borrows from the same receiver Path should not
        self-block. Receiver lock and the arg's prefix-overlapping lock
        carry the same call identity now, so try_lock's same-holder
        predicate skips the conflict.
        """
        check_ok(
            "inner: class { val: 0u64 }\n"
            "pair: class {\n"
            "  a: inner\n"
            "  b: inner\n"
            "} as {\n"
            "  poke: function {:this side: inner} is {}\n"
            "}\n"
            "main: function is {\n"
            "  p: pair a: inner b: inner\n"
            "  p.poke side: p.a.borrow\n"
            "}"
        )

    def test_call_shared_lock_allows_sibling_field_args(self):
        """Call-scoped SHARED locks on the parent permit passing sibling fields
        as separate arguments (regression guard for B4)."""
        check_ok(
            "point: record { x: i64 y: i64 }\n"
            "f: function {a: i64 b: i64} is {}\n"
            "main: function is {\n"
            "  p: point x: 1 y: 2\n"
            "  f a: p.x b: p.y\n"
            "}"
        )

    # --- Phase 2: acquisition-path coverage ---

    def test_borrow_on_dotted_path_locks_leaf(self):
        """.borrow on a field Path locks the leaf Path EXCLUSIVE; reassigning
        the locked field errors but sibling fields remain mutable."""
        errors = check_errors(
            "inner: class { value: 0 }\n"
            "point: record { a: inner b: inner }\n"
            "main: function is {\n"
            "  p: point a: inner b: inner\n"
            "  y: p.a.borrow\n"
            "  p.a = inner\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'p'" in e.msg for e in errors)

    def test_borrow_on_dotted_path_permits_sibling(self):
        """.borrow on `p.a` does not block reassignment of sibling `p.b`."""
        check_ok(
            "inner: class { value: 0 }\n"
            "point: class { a: inner b: inner }\n"
            "main: function is {\n"
            "  p: point a: inner b: inner\n"
            "  y: p.a.borrow\n"
            "  p.b = inner\n"
            "}"
        )

    def test_lock_inline_on_dotted_path_locks_leaf(self):
        """.lock on a field Path locks the leaf (alias for .borrow)."""
        errors = check_errors(
            "inner: class { value: 0 }\n"
            "point: record { a: inner b: inner }\n"
            "main: function is {\n"
            "  p: point a: inner b: inner\n"
            "  y: p.a.lock\n"
            "  p.a = inner\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'p'" in e.msg for e in errors)

    def test_borrow_on_field_released_on_scope_exit(self):
        """Borrow-scoped locks on dotted paths are released when the holder
        goes out of scope. (Needs Fix A + Fix B.)"""
        check_ok(
            "inner: class { value: 0 }\n"
            "point: class { a: inner b: i64 }\n"
            "main: function is {\n"
            "  p: point a: inner b: 0\n"
            "  { y: p.a.borrow }\n"
            "  p.b = 3\n"
            "}"
        )

    # --- Phase 2: scope-exit lock release (Fix B) ---

    def test_mutation_ok_after_view_scope_exit(self):
        """After a view in an inner bare block exits, the outer source is
        mutable. (Fix B: ZSymbolTable.pop calls release_held_locks.)"""
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  {\n"
            "    v: s.stringview\n"
            "    print v\n"
            "  }\n"
            '  s = "world".string\n'
            "}"
        )

    # --- Phase 2: method-body cases (Gap C was a false alarm) ---

    def test_custom_receiver_name_locks_correctly(self):
        """A user-chosen receiver name ('me: this') triggers the root lock
        on the method parameter name."""
        errors = check_errors(
            "thing: class { s: String } as {\n"
            "  peek: function {me: this} is {\n"
            "    v: me.s.stringview\n"
            '    me.s = "new".string\n'
            "  }\n"
            "}\n"
            "main: function is {\n"
            '  t: thing s: "x".string\n'
            "  t.peek\n"
            "}"
        )
        assert any(
            "exclusive lock" in e.msg.lower() and "'me'" in e.msg for e in errors
        )

    # --- Phase 3 tests ---

    def test_callable_dispatch_rejects_locked_callable(self):
        """Explicit method call c.call on a locked variable errors —
        callable dispatch must check the receiver lock."""
        errors = check_errors(
            "counter: class { i: i64 max: i64 } as {\n"
            "  call: function {:this} out (optionval t: i64) is {\n"
            "    if this.i < this.max then {\n"
            "      result: optionval.some this.i\n"
            "      this.i = this.i + 1\n"
            "      return result\n"
            "    }\n"
            "    return (optionval.none i64)\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  c: counter i: 0 max: 5\n"
            "  v: c.borrow\n"
            "  x: c.call\n"
            "}"
        )
        assert any("lock" in e.msg.lower() for e in errors)

    # --- Commit D: path-scoped lock integration ---

    def test_self_field_borrow_and_sibling_method_call(self):
        """BufWriter-style flush: a method that borrows one self field and
        calls a method through a sibling self field must type-check under
        the Path-scoped lock model. Under the old name-scoped model this
        was rejected because both operations took a root lock on `this`.

        The method body is only checked if `pipe` is instantiated and
        `flush` reached, so `main` does the instantiation."""
        check_ok(
            "src: class { x: i64 }\n"
            "dst: class { count: i64 } as {\n"
            "  write: function {:this n: i64} is {\n"
            "    this.count = this.count + n\n"
            "  }\n"
            "}\n"
            "pipe: class {\n"
            "  src: src\n"
            "  dst: dst\n"
            "} as {\n"
            "  flush: function {:this} is {\n"
            "    r: this.src.borrow\n"
            "    this.dst.write n: r.x\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  p: pipe src: (src x: 7) dst: (dst count: 0)\n"
            "  p.flush\n"
            "}\n"
        )

    def test_self_field_mutation_of_locked_leaf_rejected(self):
        """Within a method, mutating the locked leaf field errors."""
        errors = check_errors(
            "inner: class { val: i64 } as {\n"
            "  peek: function {i: this} out i64 is { return i.val }\n"
            "}\n"
            "wrap: class {\n"
            "  a: inner\n"
            "  b: inner\n"
            "} as {\n"
            "  go: function {w: this} is {\n"
            "    r: w.a.borrow\n"
            "    w.a = inner val: 42\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  w: wrap a: (inner val: 1) b: (inner val: 2)\n"
            "  w.go\n"
            "}\n"
        )
        assert any("exclusive lock" in e.msg.lower() and "'w'" in e.msg for e in errors)

    def test_bufwriter_style_flush_pattern_typechecks(self):
        """Regression: the buffered-wrapper `flush` pattern must
        type-check cleanly. It exercises two things together:

        * A protocol-typed .lock field on `this` (sink). Reading
          `this.sink` as the callable lifts a pending borrow on
          `(this,)`; the call must drop that pending lock before
          processing the first argument, otherwise `bv` (which already
          holds EXCLUSIVE on `(this, buf)`) would conflict with a
          re-lock of `this` during arg handling.
        * Arithmetic on `List.length` (u64). The synthesized u64
          record previously had no `+` method, so
          `this.buf.length + from.length` failed to resolve. Wiring
          length/capacity to the global u64 type restores arithmetic
          operators on List-length results."""
        check_ok(
            "myproto: protocol {\n"
            "  write: function {:this from: ByteView}"
            " out (Result t: u64 e: IoError)\n"
            "}\n"
            "holder: class {\n"
            "  sink: myproto.lock\n"
            "  buf:  Bytes\n"
            "  cap:  u64\n"
            "} as {\n"
            "  flush: function {:this}"
            " out (Result t: null e: IoError) is {\n"
            "    if this.buf.length + this.cap > this.cap then {\n"
            "      bv: ByteView.borrow from: this.buf.listview\n"
            "      fr: this.sink.write from: bv\n"
            "      bv.release\n"
            "      match (fr) case ok then {\n"
            "        return (Result.ok null e: IoError)\n"
            "      } case err then {\n"
            "        return (Result.err fr t: null)\n"
            "      }\n"
            "    }\n"
            "    return (Result.ok null e: IoError)\n"
            "  }\n"
            "}\n"
            "main: function is {}\n"
        )

    def test_return_lock_method_with_borrowed_param_typechecks(self):
        """Companion to the flush-pattern regression above: a method
        that `return`s the Result of a `.lock`-field protocol call
        whose argument is a sibling `:this` parameter borrow. Mirrors
        the oversize-bypass branch a pure-Zerolang `BufWriter.write`
        would contain:

            return this.sink.write from: from

        Exercises lifetime flow through the `.lock` callable without
        the Result being decomposed into explicit `Result.ok`/`err`
        arms — the flush pattern covers decomposition; this covers
        the straight-through Path."""
        check_ok(
            "mysink: protocol {\n"
            "  write: function {:this from: ByteView}"
            " out (Result t: u64 e: IoError)\n"
            "}\n"
            "holder: class {\n"
            "  sink: mysink.lock\n"
            "} as {\n"
            "  write: function {:this from: ByteView}"
            " out (Result t: u64 e: IoError) is {\n"
            "    return this.sink.write from: from\n"
            "  }\n"
            "}\n"
            "main: function is {}\n"
        )

    def test_self_field_mutation_of_sibling_permitted(self):
        """Within a method, mutating a sibling field of the locked leaf is
        permitted under the Path-scoped lock model."""
        check_ok(
            "inner: class { val: i64 } as {\n"
            "  peek: function {i: this} out i64 is { return i.val }\n"
            "}\n"
            "wrap: class {\n"
            "  a: inner\n"
            "  b: inner\n"
            "} as {\n"
            "  go: function {w: this} is {\n"
            "    r: w.a.borrow\n"
            "    w.b = inner val: 42\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  w: wrap a: (inner val: 1) b: (inner val: 2)\n"
            "  w.go\n"
            "}\n"
        )

    # --- Commit D: path-scoped lock transfer on return ---

    def test_lock_field_capture_transfers_lock_to_binding(self):
        """A protocol `.borrow from: X.lock` constructor retains the source
        lock through its `.lock` field. The transferred lock moves into the
        Result binding's scope — mutating the source while the wrapper is
        alive errors."""
        errors = check_errors(
            "provider: protocol {\n"
            "  get: function {:this seed: i64} out i64\n"
            "}\n"
            "multiplier: class { factor: i64 } as {\n"
            "  prov: provider\n"
            "  get: function {m: this seed: i64} out i64 is {\n"
            "    return m.factor * seed\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  m: multiplier factor: 5\n"
            "  borrowed: provider.borrow from: m.lock\n"
            "  m.factor = 10\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'m'" in e.msg for e in errors)

    def test_lock_released_when_wrapper_scope_exits(self):
        """The transferred lock lives for the wrapper binding's scope;
        once the wrapper falls out of an inner block, the source is
        mutable again."""
        check_ok(
            "provider: protocol {\n"
            "  get: function {:this seed: i64} out i64\n"
            "}\n"
            "multiplier: class { factor: i64 } as {\n"
            "  prov: provider\n"
            "  get: function {m: this seed: i64} out i64 is {\n"
            "    return m.factor * seed\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  m: multiplier factor: 5\n"
            "  {\n"
            "    borrowed: provider.borrow from: m.lock\n"
            "    r: borrowed.get seed: 3\n"
            "  }\n"
            "  m.factor = 10\n"
            "}"
        )

    def test_call_scoped_arg_lock_released_after_non_retaining_call(self):
        """A plain reftype borrow passed as a call argument does not retain
        the lock after the call returns. A subsequent mutation of the source
        is permitted — the call-scoped lock released when the call scope
        popped."""
        check_ok(
            "counter: class { n: i64 } as {\n"
            "  inc_by: function {:this by: i64} out i64 is {\n"
            "    return by + 1\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  c: counter n: 0\n"
            "  x: c.inc_by by: 5\n"
            "  c.n = 42\n"
            "}"
        )

    def test_stale_borrow_lock_cleared_after_expression(self):
        """_pending_borrow_lock is cleared between statements, so a standalone
        expression that sets it doesn't affect the next assignment."""
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  print s.stringview\n"
            '  t: "world".string\n'
            "}"
        )

    def test_stringview_on_temporary_rejected(self):
        """Creating a view from a temporary expression is rejected — no named
        root to lock, so the view would dangle."""
        errors = check_errors('main: function is { v: "hello".string.stringview }')
        assert any("temporary" in e.msg.lower() for e in errors)

    def test_borrow_on_temporary_rejected(self):
        """Borrowing a temporary reftype expression is rejected."""
        errors = check_errors('main: function is { v: ("hello".string).borrow }')
        assert any("temporary" in e.msg.lower() for e in errors)

    def test_read_locked_var_rejected(self):
        """Reading a locked variable (not just writing) is an error.
        Locked means completely unavailable."""
        errors = check_errors(
            'main: function is {\n  s: "hello".string\n  v: s.stringview\n  t: s\n}'
        )
        assert any("cannot access" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_read_sibling_field_permitted(self):
        """Reading a sibling field of a locked-leaf class is permitted under
        the Path-scoped lock model."""
        check_ok(
            "namepair: class { name: String other: i64 }\n"
            "main: function is {\n"
            '  p: namepair name: "a".string other: 0\n'
            "  v: p.name.stringview\n"
            "  x: p.other\n"
            "}"
        )

    def test_read_locked_leaf_field_rejected(self):
        """Reading the locked leaf Path itself remains an error."""
        errors = check_errors(
            "namepair: record { name: String other: i64 }\n"
            "main: function is {\n"
            '  p: namepair name: "a".string other: 0\n'
            "  v: p.name.stringview\n"
            "  m: p.name.length\n"
            "}"
        )
        assert any("cannot access" in e.msg.lower() and "'p'" in e.msg for e in errors)

    def test_read_locked_var_in_interpolation_rejected(self):
        """String interpolation of a locked variable is a read access — error."""
        errors = check_errors(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  v: s.stringview\n"
            '  print "\\{s}"\n'
            "}"
        )
        assert any("cannot access" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_holder_read_ok(self):
        """The lock holder itself is not locked — reads through it are fine."""
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  v: s.stringview\n"
            '  print "\\{v.length}"\n'
            "}"
        )

    # --- G1: value-level lock-escape at aggregate storage sites ---

    def test_store_borrowed_class_in_class_field_rejected(self):
        """A borrowed reftype cannot be passed to a class constructor field:
        the borrow carries an EXCLUSIVE lock on its source, and the
        constructed aggregate may outlive the lock's source."""
        errors = check_errors(
            "inner: class { value: 0 }\n"
            "outer: class { a: inner }\n"
            "main: function is {\n"
            "  i: inner\n"
            "  b: i.borrow\n"
            "  o: outer a: b\n"
            "}"
        )
        assert any(
            "lock-carrying" in e.msg.lower() and "aggregate" in e.msg.lower()
            for e in errors
        )

    def test_store_unlocked_value_in_class_field_allowed(self):
        """Positive control: an unlocked owned value flows into a class
        field without error."""
        check_ok(
            "inner: class { value: 0 }\n"
            "outer: class { a: inner }\n"
            "main: function is {\n"
            "  i: inner\n"
            "  o: outer a: i\n"
            "}"
        )

    def test_field_reassign_with_locked_rhs_rejected(self):
        """Reassigning an aggregate field with a locked RHS Path is a storage
        transfer and must reject: the lock would escape into the field slot."""
        errors = check_errors(
            "inner: class { value: 0 }\n"
            "outer: class { a: inner }\n"
            "main: function is {\n"
            "  i1: inner\n"
            "  i2: inner\n"
            "  o: outer a: i1\n"
            "  b: i2.borrow\n"
            "  o.a = b\n"
            "}"
        )
        assert any(
            "lock-carrying" in e.msg.lower() and "field" in e.msg.lower()
            for e in errors
        )

    def test_field_reassign_with_unlocked_rhs_allowed(self):
        """Positive control: field reassignment with an unlocked owned RHS
        is permitted."""
        check_ok(
            "inner: class { value: 0 }\n"
            "outer: class { a: inner }\n"
            "main: function is {\n"
            "  i1: inner\n"
            "  i2: inner\n"
            "  o: outer a: i1\n"
            "  o.a = i2\n"
            "}"
        )

    # --- G2: value-level lock-escape at return sites ---

    def test_return_view_of_local_rejected(self):
        """Returning a StringView over a function-local source is rejected:
        the local dies at function exit, leaving the view dangling."""
        errors = check_errors(
            "g: function out StringView is {\n"
            '  local: "hi".string\n'
            "  return local.stringview\n"
            "}\n"
            "main: function is { v: g }"
        )
        assert any(
            "lock-carrying" in e.msg.lower() and "local" in e.msg.lower()
            for e in errors
        )

    def test_return_view_of_borrow_param_allowed(self):
        """Returning a view over a borrow-typed parameter is legal: the
        caller owns the source, so the view outlives the method call."""
        check_ok(
            "f: function {s: String} out StringView is {\n"
            "  return s.stringview\n"
            "}\n"
            "main: function is {\n"
            '  x: "hi".string\n'
            "  v: f s: x\n"
            "}"
        )

    def test_return_view_of_this_receiver_allowed(self):
        """A method returning a view of its receiver's field is legal —
        `this` is borrowed from the caller and outlives the call."""
        check_ok(
            "mylabel: class { s: String } as {\n"
            "    :Text\n"
            "    stringview: function {m: this} out StringView is {\n"
            "        return m.s.stringview\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            '    m: mylabel s: "hi".string\n'
            "    print m\n"
            "}"
        )

    def test_return_unlocked_value_allowed(self):
        """Positive control: returning an owned unlocked value is fine."""
        check_ok(
            "f: function out String is {\n"
            '  return "hi".string\n'
            "}\n"
            "main: function is { v: f }"
        )


class TestWithOwnership:
    """`with name: expr do body` ownership.

    Mirrors function-argument rules:
    - bare name / dotted Path → BORROW (EXCLUSIVE-lock the source root)
    - `.take` inline          → OWNED (source invalidated)
    - `.borrow` inline        → BORROW (same as default for names)
    - call / constructor      → OWNED (fresh value)
    """

    def test_bare_name_borrows_source(self):
        """with a: c do ... borrows c; c is still usable after the do."""
        check_ok(
            "main: function is {\n"
            '  c: "hi".string\n'
            "  with a: c do print a\n"
            "  print c\n"
            "}"
        )

    def test_bare_name_locks_source_in_body(self):
        """Inside the do, the source is EXCLUSIVE-locked and cannot mutate."""
        errors = check_errors(
            'main: function is {\n  c: "hi".string\n  with a: c do c.append " world"\n}'
        )
        assert any("lock" in e.msg.lower() and "'c'" in e.msg for e in errors)

    def test_bare_name_releases_on_scope_exit(self):
        """The lock ends with the do scope, allowing later mutation."""
        check_ok(
            "main: function is {\n"
            '  c: "hi".string\n'
            "  with a: c do print a\n"
            '  c.append " world"\n'
            "}"
        )

    def test_take_moves_ownership(self):
        """with a: c.take do ... invalidates c."""
        errors = check_errors(
            "main: function is {\n"
            '  c: "hi".string\n'
            "  with a: c.take do print a\n"
            "  print c\n"
            "}"
        )
        assert any("ownership" in e.msg.lower() for e in errors)

    def test_borrow_explicit_locks_like_default(self):
        """with a: c.borrow do ... behaves like default-borrow — source locked."""
        errors = check_errors(
            "main: function is {\n"
            '  c: "hi".string\n'
            '  with a: c.borrow do c.append " !"\n'
            "}"
        )
        assert any("lock" in e.msg.lower() and "'c'" in e.msg for e in errors)

    def test_call_rhs_owns_result(self):
        """Call RHS produces a fresh value that the with-binding owns."""
        check_ok(
            "bag: class { x: i64 } as {\n"
            "  public: unit { :x }\n"
            "  create: function { x: i64 } out this is {\n"
            "    return meta.create x: x\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            '  with b: (bag x: 1) do print "\\{b.x}"\n'
            "}"
        )

    def test_dotted_path_locks_leaf(self):
        """`with a: r.f do ...` locks the leaf Path; reassigning that leaf
        errors but sibling fields stay mutable."""
        errors = check_errors(
            "pair: record { first: String other: String }\n"
            "main: function is {\n"
            '  p: pair first: "a".string other: "b".string\n'
            "  with v: p.first do {\n"
            '    p.first = "c".string\n'
            "  }\n"
            "}"
        )
        assert any("lock" in e.msg.lower() and "'p'" in e.msg for e in errors)

    def test_dotted_path_permits_sibling_in_with(self):
        """`with a: r.f do ...` permits mutation of sibling fields in the body."""
        check_ok(
            "pair: class { first: String other: String }\n"
            "main: function is {\n"
            '  p: pair first: "a".string other: "b".string\n'
            "  with v: p.first do {\n"
            '    p.other = "c".string\n'
            "  }\n"
            "}"
        )

    def test_release_rejected_in_with(self):
        """`.release` cannot be a `with` value."""
        errors = check_errors(
            'main: function is {\n  c: "hi".string\n  with a: c.release do print a\n}'
        )
        assert any("release" in e.msg.lower() for e in errors)

    def test_valtype_source_does_not_require_lock(self):
        """Valtype sources don't need locks — borrowing copies them."""
        check_ok(
            "main: function is {\n"
            "  n: 42\n"
            '  with x: n do print "\\{x}"\n'
            '  print "\\{n}"\n'
            "}"
        )


class TestIoWrappers:
    """Phase 1b buffered wrappers: typecheck-level behavior.

    The wrapper classes are declared in lib/system/io.z with native
    methods. These tests exercise the contracts the typechecker has to
    enforce: construction signatures, return-type coercion on zero-arg
    `.flush`, and the Path-scoped lock transfer that blocks access to
    the source Writer/Reader while the wrapper is alive."""

    def test_bufwriter_create_and_flush_typecheck(self):
        """BufWriter.create accepts (to: Writer.lock, capacity: u64) and
        `.flush` on the resulting class coerces to its return type so
        `fr: bw.flush` binds fr to `Result(null, IoError)`."""
        check_ok(
            "main: function is {\n"
            "  w: io.stdout\n"
            "  bw: io.BufWriter.create to: w.lock capacity: 16.u64\n"
            "  fr: bw.flush\n"
            "  match (fr) case ok then {} case err then {}\n"
            "}\n"
        )

    def test_bufwriter_write_byteview(self):
        """BufWriter.write takes a ByteView and returns Result(u64, IoError)."""
        check_ok(
            "main: function is {\n"
            "  w: io.stdout\n"
            "  bw: io.BufWriter.create to: w.lock capacity: 16.u64\n"
            "  msg: Bytes\n"
            "  msg.append from: 72.u8\n"
            "  bv: ByteView.borrow from: msg.listview\n"
            "  wr: bw.write from: bv\n"
            "  bv.release\n"
            "  match (wr) case ok then {} case err then {}\n"
            "}\n"
        )

    def test_bufwriter_locks_source_writer(self):
        """While bw holds a borrow on w via Writer.lock, calling a method
        on w is rejected (Path-scoped lock conflict)."""
        errors = check_errors(
            "main: function is {\n"
            '  w: io.open path: "/tmp/x" mode: openmode.write\n'
            "  match (w) case ok then {\n"
            "    bw: io.BufWriter.create to: w.lock capacity: 16.u64\n"
            "    cr: w.close\n"
            "  } case err then {}\n"
            "}\n"
        )
        assert any("lock" in e.msg.lower() and "'w'" in e.msg for e in errors), (
            f"expected a lock-conflict error referencing 'w'; got: {[e.msg for e in errors]}"
        )

    def test_bufreader_create_and_read(self):
        """BufReader.create takes (from: Reader.lock, capacity: u64) and
        .read returns Result(u64, IoError) with Bytes appended to `into`."""
        check_ok(
            "main: function is {\n"
            '  r: io.open path: "/tmp/x" mode: openmode.read\n'
            "  match (r) case ok then {\n"
            "    br: io.BufReader.create from: r.lock capacity: 16.u64\n"
            "    buf: Bytes\n"
            "    rr: br.read into: buf max: 32.u64\n"
            '    print "read"\n'
            "  } case err then {\n"
            '    print "open failed"\n'
            "  }\n"
            "}\n"
        )

    def test_textwriter_create_and_write_line_typecheck(self):
        """TextWriter.create takes (to: BufWriter.lock), and
        writeLine takes a StringView and returns Result(u64, IoError).
        Exercises the two-layer wrapper construction (File -> BufWriter
        -> TextWriter) that's the canonical Phase 1c caller shape."""
        check_ok(
            "main: function is {\n"
            "  w: io.stdout\n"
            "  bw: io.BufWriter.create to: w.lock capacity: 64.u64\n"
            "  tw: io.TextWriter.create to: bw.lock\n"
            '  wr: tw.writeLine from: "hi"\n'
            "  match (wr) case ok then {} case err then {}\n"
            "}\n"
        )

    def test_textwriter_locks_bufwriter_source(self):
        """While tw holds a borrow on bw via BufWriter.lock, calling
        bw.flush is rejected -- the source BufWriter cannot be drained
        directly while the TextWriter is alive."""
        errors = check_errors(
            "main: function is {\n"
            "  w: io.stdout\n"
            "  bw: io.BufWriter.create to: w.lock capacity: 64.u64\n"
            "  tw: io.TextWriter.create to: bw.lock\n"
            "  fr: bw.flush\n"
            "}\n"
        )
        assert any("lock" in e.msg.lower() and "'bw'" in e.msg for e in errors), (
            f"expected lock-conflict error referencing 'bw'; got: "
            f"{[e.msg for e in errors]}"
        )

    def test_textreader_create_and_read_line_typecheck(self):
        """TextReader.create takes (from: BufReader.lock); readLine
        returns Result(String, IoError). Mirrors the TextWriter shape
        on the read side of the stack."""
        check_ok(
            "main: function is {\n"
            "  r: io.stdin\n"
            "  br: io.BufReader.create from: r.lock capacity: 64.u64\n"
            "  tr: io.TextReader.create from: br.lock\n"
            "  l: tr.readLine\n"
            "  match (l) case ok then {} case err then {}\n"
            "}\n"
        )

    def test_textreader_is_iterable(self):
        """TextReader exposes `call: function {:this} out (Option t: String)`,
        so it plugs into the for-loop iterator protocol directly:
        `for line: tr loop { ... }` binds `line` to a String."""
        check_ok(
            "main: function is {\n"
            "  r: io.stdin\n"
            "  br: io.BufReader.create from: r.lock capacity: 64.u64\n"
            "  tr: io.TextReader.create from: br.lock\n"
            "  for line: tr loop { print line }\n"
            "}\n"
        )

    def test_textreader_locks_bufreader_source(self):
        """While tr holds a borrow on br via BufReader.lock, calling
        br.read is rejected -- the source BufReader cannot be consumed
        directly while the TextReader is alive."""
        errors = check_errors(
            "main: function is {\n"
            "  r: io.stdin\n"
            "  br: io.BufReader.create from: r.lock capacity: 64.u64\n"
            "  tr: io.TextReader.create from: br.lock\n"
            "  buf: (Bytes)\n"
            "  rr: br.read into: buf.borrow max: 16.u64\n"
            "}\n"
        )
        assert any("lock" in e.msg.lower() and "'br'" in e.msg for e in errors), (
            f"expected lock-conflict error referencing 'br'; got: "
            f"{[e.msg for e in errors]}"
        )

    def test_textwriter_flush_zero_arg_coerces_to_return_type(self):
        """`fr: tw.flush` must bind fr to Result(null, IoError) via
        the generalised zero-arg class-method coercion (previously
        required a per-class special case in the typechecker)."""
        check_ok(
            "main: function is {\n"
            "  w: io.stdout\n"
            "  bw: io.BufWriter.create to: w.lock capacity: 64.u64\n"
            "  tw: io.TextWriter.create to: bw.lock\n"
            "  fr: tw.flush\n"
            "  match (fr) case ok then {} case err then {}\n"
            "}\n"
        )

    def test_bufwriter_flush_zero_arg_call_form(self):
        """The `bw.flush` dotted-Path form must produce a call whose
        Result typechecks as Result(null, IoError) — not a function
        reference. Covers the Path-value coercion added alongside
        the File.close precedent in ztypecheck._resolve_dotted_child."""
        check_ok(
            "main: function is {\n"
            "  w: io.stdout\n"
            "  bw: io.BufWriter.create to: w.lock capacity: 16.u64\n"
            "  fr: bw.flush\n"
            "  match (fr) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )


class TestOsUnit:
    """`os` unit: process-level primitives (argv / env / exit).
    Thin compared to io -- only three natives -- but exercises the
    same machinery: unit-qualified dispatch, zero-arg native
    coercion, and ownership of String-returning results."""

    def test_args_returns_list_of_string(self):
        """os.args is a zero-arg native whose return type coerces to
        `List of: String`, so `argv: os.args` binds argv to the List
        directly (not to a function pointer)."""
        check_ok('main: function is {\n  argv: os.args\n  print "\\{argv.length}"\n}\n')

    def test_get_env_returns_option_string(self):
        """os.env returns Option(String); the typechecker accepts
        matching on some/none arms in the usual way."""
        check_ok(
            "main: function is {\n"
            '  ev: os.env key: "PATH"\n'
            "  match (ev) case some then {\n"
            "    print ev\n"
            "  } case none then {\n"
            '    print "missing"\n'
            "  }\n"
            "}\n"
        )

    def test_exit_is_no_return(self):
        """os.exit has no return; calling it with a numeric i32 is
        legal and the scope does not need to dispatch a Result."""
        check_ok("main: function is {\n  os.exit code: 0.i32\n}\n")

    def test_set_env_returns_result_null_ioerror(self):
        """os.setEnv takes two strings and returns Result(null, IoError),
        matching io's fallible write shape."""
        check_ok(
            "main: function is {\n"
            '  r: os.setEnv key: "X" value: "y"\n'
            "  match (r) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_unset_env_returns_result_null_ioerror(self):
        """os.unsetEnv consumes just `key` and returns Result(null, IoError)."""
        check_ok(
            "main: function is {\n"
            '  r: os.unsetEnv key: "X"\n'
            "  match (r) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_env_names_returns_list_of_string(self):
        """os.envNames is a zero-arg native returning an owned List of
        strings — same coercion rule as os.args."""
        check_ok(
            'main: function is {\n  names: os.envNames\n  print "\\{names.length}"\n}\n'
        )

    def test_cwd_returns_result_string_ioerror(self):
        """os.cwd is zero-arg and returns Result(String, IoError)."""
        check_ok(
            "main: function is {\n"
            "  r: os.cwd\n"
            "  match (r) case ok then {\n"
            "    print r\n"
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_set_cwd_takes_path_returns_result_null_ioerror(self):
        check_ok(
            "main: function is {\n"
            '  r: os.setCwd path: "/tmp"\n'
            "  match (r) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_pid_and_ppid_return_i32(self):
        """pid/ppid are zero-arg natives coercing to i32 literals."""
        check_ok(
            "main: function is {\n"
            "  p: os.pid\n"
            "  pp: os.ppid\n"
            '  print "\\{p} \\{pp}"\n'
            "}\n"
        )

    def test_user_name_and_home_dir_return_result_string_ioerror(self):
        check_ok(
            "main: function is {\n"
            "  un: os.userName\n"
            "  hd: os.homeDir\n"
            "  match (un) case ok then {\n"
            "    print un\n"
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "  match (hd) case ok then {\n"
            "    print hd\n"
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_platform_returns_platformkind_variant(self):
        """os.platform returns a platformkind variant; all four arms
        must typecheck exhaustively."""
        check_ok(
            "main: function is {\n"
            "  p: os.platform\n"
            "  match (p) case linux then {\n"
            '    print "linux"\n'
            "  } case darwin then {\n"
            '    print "darwin"\n'
            "  } case windows then {\n"
            '    print "windows"\n'
            "  } case other then {\n"
            '    print "other"\n'
            "  }\n"
            "}\n"
        )

    def test_arch_returns_archkind_variant(self):
        check_ok(
            "main: function is {\n"
            "  a: os.arch\n"
            "  match (a) case x86_64 then {\n"
            '    print "x86_64"\n'
            "  } case aarch64 then {\n"
            '    print "aarch64"\n'
            "  } case other then {\n"
            '    print "other"\n'
            "  }\n"
            "}\n"
        )

    def test_hostname_returns_result_string_ioerror(self):
        check_ok(
            "main: function is {\n"
            "  hn: os.hostname\n"
            "  match (hn) case ok then {\n"
            "    print hn\n"
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )


class TestStringviewQueries:
    """Phase S1 non-allocating query methods on StringView."""

    def test_is_empty_and_is_ascii_are_zero_arg_methods(self):
        check_ok(
            "main: function is {\n"
            '  s: "hi".string\n'
            "  sv: s.stringview\n"
            "  e: sv.isEmpty\n"
            "  a: sv.isAscii\n"
            '  print "\\{e} \\{a}"\n'
            "}\n"
        )

    def test_starts_with_ends_with_take_stringview_param(self):
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  sv: s.stringview\n"
            '  sw: sv.startsWith prefix: "he"\n'
            '  ew: sv.endsWith suffix: "lo"\n'
            '  print "\\{sw} \\{ew}"\n'
            "}\n"
        )

    def test_contains_returns_bool(self):
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  sv: s.stringview\n"
            '  c: sv.contains needle: "ll"\n'
            '  print "\\{c}"\n'
            "}\n"
        )

    def test_index_of_returns_optionval_u64(self):
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  sv: s.stringview\n"
            '  r: sv.indexOf needle: "l"\n'
            "  match (r) case some then {\n"
            '    print "\\{r}"\n'
            "  } case none then {\n"
            '    print "miss"\n'
            "  }\n"
            "}\n"
        )

    def test_last_index_of_returns_optionval_u64(self):
        check_ok(
            "main: function is {\n"
            '  s: "hello".string\n'
            "  sv: s.stringview\n"
            '  r: sv.lastIndexOf needle: "l"\n'
            "  match (r) case some then {\n"
            '    print "\\{r}"\n'
            "  } case none then {\n"
            '    print "miss"\n'
            "  }\n"
            "}\n"
        )

    def test_byte_at_returns_optionval_u8(self):
        check_ok(
            "main: function is {\n"
            '  s: "hi".string\n'
            "  sv: s.stringview\n"
            "  b: sv.byteAt i: 0.u64\n"
            "  match (b) case some then {\n"
            '    print "\\{b}"\n'
            "  } case none then {\n"
            '    print "oob"\n'
            "  }\n"
            "}\n"
        )


class TestStringviewSlicing:
    """Phase S2 view-returning slicing helpers on StringView."""

    def test_trim_returns_stringview(self):
        check_ok(
            "main: function is {\n"
            '  s: "  hi  ".string\n'
            "  sv: s.stringview\n"
            "  t: sv.trim\n"
            "  l: t.length\n"
            '  print "\\{l}"\n'
            "}\n"
        )

    def test_trim_start_and_trim_end(self):
        check_ok(
            "main: function is {\n"
            '  s: "  hi  ".string\n'
            "  sv: s.stringview\n"
            "  a: sv.trimStart\n"
            "  b: sv.trimEnd\n"
            "  la: a.length\n"
            "  lb: b.length\n"
            '  print "\\{la} \\{lb}"\n'
            "}\n"
        )

    def test_strip_prefix_returns_option_stringview(self):
        check_ok(
            "main: function is {\n"
            '  s: "--x".string\n'
            "  sv: s.stringview\n"
            '  r: sv.stripPrefix p: "--"\n'
            "  match (r) case some then {\n"
            "    l: r.length\n"
            '    print "\\{l}"\n'
            "  } case none then {\n"
            '    print "no"\n'
            "  }\n"
            "}\n"
        )

    def test_strip_suffix_returns_option_stringview(self):
        check_ok(
            "main: function is {\n"
            '  s: "a.txt".string\n'
            "  sv: s.stringview\n"
            '  r: sv.stripSuffix s: ".txt"\n'
            "  match (r) case some then {\n"
            "    l: r.length\n"
            '    print "\\{l}"\n'
            "  } case none then {\n"
            '    print "no"\n'
            "  }\n"
            "}\n"
        )


class TestStringviewSplitting:
    """Phase S3 iterator-based splitting."""

    def test_split_iteration(self):
        check_ok(
            "main: function is {\n"
            '  s: "a,b,c".string\n'
            "  sv: s.stringview\n"
            '  with it: (sv.split sep: ",") do for piece: it loop {\n'
            "    print piece\n"
            "  }\n"
            "}\n"
        )

    def test_split_once_returns_optionval_u64(self):
        check_ok(
            "main: function is {\n"
            '  s: "k=v".string\n'
            "  sv: s.stringview\n"
            '  r: sv.splitOnce sep: "="\n'
            "  match (r) case some then {\n"
            '    print "\\{r}"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}\n"
        )

    def test_lines_iteration(self):
        check_ok(
            "main: function is {\n"
            '  s: "a\nb".string\n'
            "  sv: s.stringview\n"
            "  with li: sv.lines do for line: li loop {\n"
            "    print line\n"
            "  }\n"
            "}\n"
        )


class TestStringviewTransforms:
    """Phase S4 allocating String transforms on StringView."""

    def test_to_lower_and_to_upper(self):
        check_ok(
            "main: function is {\n"
            '  s: "Hi".string\n'
            "  sv: s.stringview\n"
            "  lo: sv.toLowerAscii\n"
            "  up: sv.toUpperAscii\n"
            "  print lo\n"
            "  print up\n"
            "}\n"
        )

    def test_replace_and_replace_first(self):
        check_ok(
            "main: function is {\n"
            '  s: "aaa".string\n'
            "  sv: s.stringview\n"
            '  r: sv.replace needle: "a" replacement: "b"\n'
            '  rf: sv.replaceFirst needle: "a" replacement: "b"\n'
            "  print r\n"
            "  print rf\n"
            "}\n"
        )

    def test_repeated_and_concat(self):
        check_ok(
            "main: function is {\n"
            '  s: "ab".string\n'
            "  sv: s.stringview\n"
            "  rep: sv.repeated n: 2.u64\n"
            '  o: "cd".string\n'
            "  ov: o.stringview\n"
            "  cat: sv.concat other: ov\n"
            "  print rep\n"
            "  print cat\n"
            "}\n"
        )


class TestStringviewCodepoints:
    """Phase S5 codepoint iteration + count."""

    def test_count_returns_u64(self):
        check_ok(
            "main: function is {\n"
            '  s: "abc".string\n'
            "  sv: s.stringview\n"
            "  n: sv.count\n"
            '  print "\\{n}"\n'
            "}\n"
        )

    def test_codepoints_iteration(self):
        check_ok(
            "main: function is {\n"
            '  s: "abc".string\n'
            "  sv: s.stringview\n"
            "  with it: sv.codepoints do for cp: it loop {\n"
            '    print "\\{cp}"\n'
            "  }\n"
            "}\n"
        )


class TestStringviewParsing:
    """Phase S6 numeric parsing on StringView."""

    def test_parse_i64_returns_result_i64_parseerror(self):
        check_ok(
            "main: function is {\n"
            '  s: "-42".string\n'
            "  sv: s.stringview\n"
            "  r: sv.parseI64\n"
            "  match (r) case ok then {\n"
            '    print "\\{r}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_parse_u64(self):
        check_ok(
            "main: function is {\n"
            '  s: "42".string\n'
            "  sv: s.stringview\n"
            "  r: sv.parseU64\n"
            "  match (r) case ok then {\n"
            '    print "\\{r}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_parse_f64(self):
        check_ok(
            "main: function is {\n"
            '  s: "3.14".string\n'
            "  sv: s.stringview\n"
            "  r: sv.parseF64\n"
            "  match (r) case ok then {\n"
            '    print "\\{r}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_parseerror_arm_matching(self):
        """parseerror variant can be matched into its specific arms."""
        check_ok(
            "main: function is {\n"
            '  s: "x".string\n'
            "  sv: s.stringview\n"
            "  r: sv.parseI64\n"
            "  match (r) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            "    match (r) case empty then {\n"
            '      print "empty"\n'
            "    } case invalidDigit then {\n"
            '      print "inv"\n'
            "    } case overflow then {\n"
            '      print "ovf"\n'
            "    }\n"
            "  }\n"
            "}\n"
        )


class TestStringJoin:
    """Phase S7 stringJoin free function."""

    def test_join_parts_and_sep(self):
        check_ok(
            "main: function is {\n"
            "  parts: (List of: String)\n"
            '  parts.append from: "a".string\n'
            '  parts.append from: "b".string\n'
            '  j: stringJoin parts: parts sep: ","\n'
            "  print j\n"
            "}\n"
        )

    def test_join_empty_list(self):
        check_ok(
            "main: function is {\n"
            "  parts: (List of: String)\n"
            '  j: stringJoin parts: parts sep: ","\n'
            "  l: j.length\n"
            '  print "\\{l}"\n'
            "}\n"
        )


class TestCliUnit:
    """cli unit: Spec / Parsed / registration + parse + helpText."""

    def test_spec_create_and_register(self):
        check_ok(
            "main: function is {\n"
            '  sp: cli.Spec.create programName: "p".string summary: "s".string\n'
            '  cli.addFlag spec: sp name: "--v".string shortName: "-v".string help: "".string\n'
            '  cli.addOption spec: sp name: "--o".string shortName: "-o".string help: "".string required: true\n'
            '  cli.addPositional spec: sp name: "x".string help: "".string required: true\n'
            "}\n"
        )

    def test_parse_returns_result(self):
        check_ok(
            "main: function is {\n"
            '  sp: cli.Spec.create programName: "p".string summary: "".string\n'
            "  r: cli.parse spec: sp args: os.args\n"
            "  match (r) case ok then {\n"
            '    v: r.hasFlag name: "--v"\n'
            '    print "\\{v}"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}\n"
        )

    def test_help_text_returns_string(self):
        check_ok(
            "main: function is {\n"
            '  sp: cli.Spec.create programName: "p".string summary: "".string\n'
            "  h: cli.helpText spec: sp\n"
            "  print h\n"
            "}\n"
        )


class TestValtypeReftypeEnforcement:
    """Valtypes (record / variant / facet) cannot hold reftype fields
    directly or transitively. This is the language invariant described
    in CLAUDE.md; reftypes are String / class / union / protocol and
    generic collection templates (List / Map / Box / Option / Result).
    """

    def test_record_cannot_hold_string(self):
        errors = check_errors(
            'r: record { s: String }\nmain: function is { p: r s: "hello".string }'
        )
        assert any("reftype" in e.msg and "String" in e.msg for e in errors)

    def test_record_cannot_hold_list(self):
        errors = check_errors(
            "r: record { xs: (List of: i32) }\n"
            "main: function is { p: r xs: (List of: i32) }"
        )
        assert any("reftype" in e.msg for e in errors)

    def test_record_cannot_hold_map(self):
        errors = check_errors(
            "r: record { m: (Map key: String value: i32) }\n"
            "main: function is { p: r m: (Map key: String value: i32) }"
        )
        assert any("reftype" in e.msg for e in errors)

    def test_record_cannot_hold_class(self):
        errors = check_errors(
            "c: class { v: i64 }\nr: record { x: c }\nmain: function is { p: r x: c }"
        )
        assert any("reftype" in e.msg and "class" in e.msg for e in errors)

    def test_record_cannot_hold_union(self):
        errors = check_errors(
            "u: union { a: i64 b: null }\n"
            "r: record { x: u }\n"
            "main: function is { p: r x: u.a 1 }"
        )
        assert any("reftype" in e.msg and "union" in e.msg for e in errors)

    def test_variant_cannot_hold_list(self):
        errors = check_errors(
            "v: variant { a: (List of: i64) b: null }\n"
            "main: function is { x: v.a (List of: i64) }"
        )
        assert any("reftype" in e.msg for e in errors)

    def test_record_allows_primitive_fields(self):
        check_ok(
            "r: record { x: i64 y: f64 b: bool }\n"
            "main: function is { p: r x: 1 y: 2.0 b: 0 < 1 }"
        )

    def test_record_allows_nested_valtype_record(self):
        check_ok(
            "inner: record { x: i64 y: i64 }\n"
            "outer: record { a: inner b: i64 }\n"
            "main: function is { "
            "  i: inner x: 1 y: 2\n"
            "  o: outer a: i b: 3\n"
            "}"
        )

    def test_record_allows_str_to_N(self):
        check_ok(
            "r: record { s: (str to: 16) }\nmain: function is { p: r s: (str to: 16) }"
        )

    def test_class_allows_reftype_fields(self):
        """Reftype fields in classes are fine — classes are themselves
        reftypes, so the self-containment rule doesn't apply."""
        check_ok("c: class { s: String xs: (List of: i64) }\nmain: function is { }")

    # --- Views: rejected in all aggregates (record / variant / class)
    # per doc/strings.pdoc. Views lock their source and v2 does not
    # propagate lock state through aggregate fields.

    def test_record_cannot_hold_stringview(self):
        errors = check_errors(
            "r: record { sv: StringView }\n"
            "main: function is {\n"
            '  s: "hi".string\n'
            "  v: s.stringview\n"
            "  p: r sv: v\n"
            "}"
        )
        assert any("StringView" in e.msg and "view" in e.msg for e in errors)

    def test_variant_cannot_hold_stringview(self):
        errors = check_errors(
            "v: variant { a: StringView b: null }\n"
            "main: function is {\n"
            '  s: "hi".string\n'
            "  x: v.a s.stringview\n"
            "}"
        )
        assert any("StringView" in e.msg and "view" in e.msg for e in errors)

    def test_class_cannot_hold_stringview(self):
        """After G3 the class declaration itself is permitted; construction
        with a locked RHS is rejected by the value-level G1 check."""
        errors = check_errors(
            "c: class { sv: StringView }\n"
            "main: function is {\n"
            '  s: "hi".string\n'
            "  v: s.stringview\n"
            "  p: c sv: v\n"
            "}"
        )
        assert any("lock-carrying" in e.msg.lower() for e in errors)

    def test_class_cannot_hold_byteview(self):
        errors = check_errors(
            "c: class { bv: ByteView }\n"
            "main: function is {\n"
            "  b: Bytes\n"
            "  p: c bv: b.byteview\n"
            "}"
        )
        assert any("view" in e.msg for e in errors)

    def test_class_cannot_hold_byteview_via_metadata(self):
        """Pin the metadata-driven rejection.

        Bytes.byteview is declared natively in lib/system/system.z
        with a `.lock`-annotated receiver and a borrow return. The
        legacy syntactic constant `_INLINE_LOCK_PROJECTIONS` is gone;
        this test pins the ByteView rejection via
        _check_aggregate_lock_escape Case 1, which keys off the
        method's return ownership being BORROW.
        """
        errors = check_errors(
            "c: class { bv: ByteView }\n"
            "main: function is {\n"
            "  b: Bytes\n"
            "  p: c bv: b.byteview\n"
            "}"
        )
        assert any("view" in e.msg for e in errors)

    def test_class_cannot_hold_listview(self):
        errors = check_errors(
            "c: class { lv: (ListView of: i64) }\n"
            "main: function is {\n"
            "  xs: (List of: i64)\n"
            "  p: c lv: xs.listview\n"
            "}"
        )
        assert any("view" in e.msg for e in errors)

    def test_user_method_with_lock_receiver_rejected_in_aggregate(self):
        """The aggregate-escape check fires for Any borrow-returning
        user method (which by validation must have a `.lock` parameter).
        A method storing its borrowed return into another class field
        must be rejected — the value carries an outstanding lock on
        its source.
        """
        errors = check_errors(
            "src: class { val: 0u64 } as {\n"
            "  peek: function {t: this.lock} out u64.borrow is { return t.val }\n"
            "}\n"
            "holder: class { x: u64 }\n"
            "main: function is {\n"
            "  s: src val: 5u64\n"
            "  h: holder x: s.peek\n"
            "}"
        )
        assert any("lock-carrying" in e.msg.lower() for e in errors)

    # --- Positive controls: views remain legal outside aggregate storage.

    def test_stringview_as_local_allowed(self):
        check_ok(
            'main: function is {\n  s: "hi".string\n  v: s.stringview\n  print v\n}'
        )

    def test_stringview_as_function_parameter_allowed(self):
        check_ok(
            "f: function {v: StringView} is { print v }\n"
            "main: function is {\n"
            '  t: "hi".string\n'
            "  f v: t.stringview\n"
            "}"
        )


class TestPureZerolangBufferedShapes:
    """Ownership-Path coverage for the shapes a pure-Zerolang
    BufWriter/BufReader body would have exercised.

    Phase 1b shipped the wrappers with native C method bodies, so
    these ownership paths never got user-code exercise in the test
    suite. Each test below mirrors one shape from a hypothetical
    pure-Zerolang implementation, written against user classes so
    the ownership machinery is genuinely exercised."""

    def test_self_dispatch_in_method_body_typechecks(self):
        """Inside `write`, calling `this.flush` — a zero-arg method
        on the same concrete class — must typecheck and bind `fr` to
        `Result<null, IoError>` (the flush return type), not to a
        function-pointer. Exercises the typechecker's generalised
        zero-arg class-method coercion (previously only File.close /
        BufWriter.flush got this treatment via per-class branches)
        and the emitter's matching dispatch. A pure-Zerolang
        `BufWriter.write` would call `this.flush` when the buffer
        would overflow."""
        check_ok(
            "mysink: protocol {\n"
            "  write: function {:this from: ByteView}"
            " out (Result t: u64 e: IoError)\n"
            "}\n"
            "holder: class {\n"
            "  sink: mysink.lock\n"
            "  buf:  Bytes\n"
            "  cap:  u64\n"
            "} as {\n"
            "  flush: function {:this}"
            " out (Result t: null e: IoError) is {\n"
            "    bv: ByteView.borrow from: this.buf.listview\n"
            "    fr: this.sink.write from: bv\n"
            "    bv.release\n"
            "    match (fr) case ok then {\n"
            "      return (Result.ok null e: IoError)\n"
            "    } case err then {\n"
            "      return (Result.err fr t: null)\n"
            "    }\n"
            "  }\n"
            "  write: function {:this from: ByteView}"
            " out (Result t: u64 e: IoError) is {\n"
            "    fr: this.flush\n"
            "    match (fr) case ok then {\n"
            "      return (Result.ok 0.u64 e: IoError)\n"
            "    } case err then {\n"
            "      return (Result.err fr t: u64)\n"
            "    }\n"
            "  }\n"
            "}\n"
            "main: function is {}\n"
        )

    def test_extend_view_from_param_into_self_field_typechecks(self):
        """A method holds a parameter borrow live across a mutation
        of a sibling self field: `this.buf.extendView other: from`
        where `from: ByteView` is the parameter and `buf: Bytes` is a
        self field. Path-scoped locking must treat `from` and
        `(this, buf)` as disjoint paths — both can hold their locks
        simultaneously. A pure-Zerolang `BufWriter.write` fast-Path
        does exactly this to append incoming Bytes into the buffer."""
        check_ok(
            "holder: class {\n"
            "  buf: Bytes\n"
            "  cap: u64\n"
            "} as {\n"
            "  stash: function {:this from: ByteView} is {\n"
            "    this.buf.extendView other: from\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  h: holder buf: Bytes cap: 16.u64\n"
            "  src: Bytes\n"
            "  src.append from: 65.u8\n"
            "  bv: ByteView.borrow from: src.listview\n"
            "  h.stash from: bv\n"
            "  bv.release\n"
            "}\n"
        )

    def test_passthrough_mut_param_via_lock_field_typechecks(self):
        """A method forwards its mutable parameter through a protocol
        method on a `.lock`-typed self field:

            return this.source.read into: into max: max

        Exercises the same pending-borrow-lift-on-`(this,)` mechanism
        the existing flush regression (test_bufwriter_style_flush_...)
        covered for a borrowed param, but with a mutable `into: Bytes`
        parameter — the call must drop the pending lock on `this`
        before processing `into`, otherwise `into` would conflict
        with a re-lock of `this`. This is the shape of a
        pure-Zerolang `BufReader.read` pass-through."""
        check_ok(
            "mysource: protocol {\n"
            "  read: function {:this into: Bytes max: u64}"
            " out (Result t: u64 e: IoError)\n"
            "}\n"
            "holder: class {\n"
            "  source: mysource.lock\n"
            "  cap:    u64\n"
            "} as {\n"
            "  read: function {:this into: Bytes max: u64}"
            " out (Result t: u64 e: IoError) is {\n"
            "    return this.source.read into: into max: max\n"
            "  }\n"
            "}\n"
            "main: function is {}\n"
        )


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
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        assert isinstance(program, zast.Program), f"Parse failed for {name}"
        typing = typecheck(program)
        errors = typing.errors
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
        program, typing = check_ok(
            "myclass: class { x: 0 }\nmain: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert ct is not None
        assert ct.typetype == ZTypeType.CLASS

    def test_class_is_reftype(self):
        """Classes should be tagged as reftype (is_valtype=False)."""
        program, typing = check_ok(
            "myclass: class { x: 0 }\nmain: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert ct is not None
        assert ct.is_valtype is False

    def test_class_fields_resolved(self):
        """Class fields should be resolved as children of the class type."""
        program, typing = check_ok(
            "myclass: class { x: 0\n y: 0.0 }\nmain: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert tc.typing.has_child(ct, "x")
        assert tc.typing.child_of(ct, "x").name == "i64"
        assert tc.typing.has_child(ct, "y")
        assert tc.typing.child_of(ct, "y").name == "f64"

    def test_class_methods_resolved(self):
        """Class methods should be resolved as children."""
        program, typing = check_ok(
            "myclass: class { x: 0 } as {\n"
            "  get: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        assert tc.typing.has_child(ct, "get")
        assert tc.typing.child_of(ct, "get").typetype == ZTypeType.FUNCTION

    def test_class_this_resolves_to_class(self):
        """The `this` keyword in class methods resolves to the class type."""
        program, typing = check_ok(
            "myclass: class { x: 0 } as {\n"
            "  get: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        get_fn = tc.typing.child_of(ct, "get")
        param_c = tc.typing.child_of(get_fn, "c")
        assert param_c is ct

    def test_class_type_keyword(self):
        """The `type` keyword in a class resolves to the class type."""
        program, typing = check_ok(
            "myclass: class { x: 0 } as {\n"
            "  clone: function {c: this.take} out type is { return c }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        tc = TypeChecker(program)
        tc.check()
        ct = tc._resolved.get("test.myclass")
        clone_fn = tc.typing.child_of(ct, "clone")
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
    """Phase 4g: String resolves via class Path, not record special-case."""

    def test_string_resolves_as_class_type(self):
        """String should resolve as ZTypeType.CLASS."""
        program, typing = check_ok('main: function is { s: "hello" }')
        tc = TypeChecker(program)
        tc.check()
        st = tc._resolved.get("system.String")
        assert st is not None
        assert st.typetype == ZTypeType.CLASS

    def test_string_is_reftype(self):
        """String should be tagged as reftype (is_valtype=False) via class Path."""
        program, typing = check_ok('main: function is { s: "hello" }')
        tc = TypeChecker(program)
        tc.check()
        st = tc._resolved.get("system.String")
        assert st is not None
        assert st.is_valtype is False

    def test_string_take_invalidates(self):
        """After .take on a String variable, the source is invalidated."""
        errors = check_errors(
            'main: function is {\n  s: "hello".string\n  d: s.take\n  e: s\n}'
        )
        assert any(
            "ownership transfer" in e.msg or "undefined" in e.msg for e in errors
        )

    def test_string_borrow_locks(self):
        """Borrowing a String variable should lock the source."""
        errors = check_errors(
            'main: function is {\n  s: "hello"\n  d: s.borrow\n  e: s.borrow\n}'
        )
        assert any(
            "lock" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_string_swap_ok(self):
        """Swapping two String variables should work."""
        check_ok(
            'main: function is {\n  a: "hello".string\n  b: "world".string\n  a swap b\n}'
        )

    def test_string_aliasing_error(self):
        """Passing the same String twice to a call is an aliasing error."""
        errors = check_errors(
            "f: function {a: String b: String} is {}\n"
            "main: function is {\n"
            '  s: "hello".string\n'
            "  f a: s b: s\n"
            "}"
        )
        assert any("aliasing" in e.msg.lower() for e in errors)


# ---- Phase 4h: Union Type Checking Tests ----


class TestUnionTypeResolution:
    """Test that union types resolve correctly."""

    def test_union_resolves_as_union_type(self):
        """A union definition resolves to ZTypeType.UNION."""
        program, typing = check_ok(
            "myunion: union { a: i64\n b: String\n c: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None
        assert ut.typetype == ZTypeType.UNION

    def test_union_is_reftype(self):
        """Unions should be tagged as reftype (is_valtype=False)."""
        program, typing = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None
        assert ut.is_valtype is False

    def test_union_subtypes_stored_as_children(self):
        """Union subtypes should be stored as children."""
        program, typing = check_ok(
            "myunion: union { a: i64\n b: String\n c: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert tc.typing.has_child(ut, "a")
        assert tc.typing.has_child(ut, "b")
        assert tc.typing.has_child(ut, "c")
        assert tc.typing.child_of(ut, "a").name == "i64"
        assert tc.typing.child_of(ut, "b").name == "String"
        assert tc.typing.child_of(ut, "c").name == "null"

    def test_union_null_subtype(self):
        """Null subtypes get a sentinel NULL type."""
        program, typing = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.b }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert tc.typing.child_of(ut, "b").name == "null"
        assert tc.typing.child_of(ut, "b").is_valtype is True


class TestUnionConstruction:
    """Test union construction type checking."""

    def test_union_subtype_construction_returns_union_type(self):
        """Calling union.subtype expr returns the union type."""
        program, typing = check_ok(
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
        """Calling union.stringsubtype with String arg creates a union."""
        check_ok(
            "myunion: union { a: String\n b: null }\n"
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
            "myunion: union { a: i64\n b: String\n c: null }\n"
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
            "myunion: union { a: i64\n b: String\n c: null }\n"
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
        p = make_parser_with_vfs(vfs, "unions")
        program = p.parse()
        assert isinstance(program, zast.Program), "Parse failed"
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"


class TestDataTypeResolution:
    """Test data type resolution in the type checker."""

    def test_data_resolves_as_data_type(self):
        """Data definitions should resolve to DATA ZType."""
        program, typing = check_ok(
            "mydata: data { 10 20 30 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert dt is not None
        assert dt.typetype == ZTypeType.DATA

    def test_data_ordinal_identifiers(self):
        """Unnamed data elements get ordinal identifiers 0, 1, 2..."""
        program, typing = check_ok(
            "mydata: data { 10 20 30 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert tc.typing.has_child(dt, "0")
        assert tc.typing.has_child(dt, "1")
        assert tc.typing.has_child(dt, "2")

    def test_data_named_elements(self):
        """Named data elements use their labels."""
        program, typing = check_ok(
            "mydata: data { LOW: 0 HIGH: 10 }\nmain: function is { x: mydata.LOW }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert tc.typing.has_child(dt, "LOW")
        assert tc.typing.has_child(dt, "HIGH")

    def test_data_has_tag_subtype(self):
        """All data types should have a .tag subtype (monomorphized tag record)."""
        program, typing = check_ok(
            "mydata: data { 1 2 3 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert tc.typing.has_child(dt, "tag")
        tag = tc.typing.child_of(dt, "tag")
        assert tag.typetype == ZTypeType.RECORD
        assert tag.is_tag_generic_origin
        assert tag.name == "tag__i64"

    def test_data_tag_parent_is_data(self):
        """The .tag type's parent should point back to its data type."""
        program, typing = check_ok(
            "mydata: data { LOW: 0 HIGH: 1 }\nmain: function is { x: mydata.LOW }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        tag = tc.typing.child_of(dt, "tag")
        assert tag.parent is dt

    def test_data_mixed_named_unnamed(self):
        """Data with mixed named and unnamed elements."""
        program, typing = check_ok(
            "mydata: data { 10 MIDDLE: 20 30 }\nmain: function is { x: mydata.0 }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        assert tc.typing.has_child(dt, "0")
        assert tc.typing.has_child(dt, "MIDDLE")
        assert tc.typing.has_child(dt, "2")


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
        program, typing = check_ok(
            "pv: data { A: 10 B: 20 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        tag = tc.typing.child_of(ut, "tag")
        assert tag is not None
        # check that the discriminator values match the data
        assert tc.typing.child_of(tag, "A").name == "10"
        assert tc.typing.child_of(tag, "B").name == "20"

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
        program, typing = check_ok(
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
        tag = tc.typing.child_of(ut, "tag")
        assert tc.typing.child_of(tag, "CRITICAL").name == "10"

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
        program, typing = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        tag = tc.typing.child_of(ut, "tag")
        assert tag is not None
        assert tc.typing.child_of(tag, "a").name == "0"
        assert tc.typing.child_of(tag, "b").name == "1"

    def test_union_has_tag_data_child(self):
        """Union should have a 'tag' child (data type) for MyUnion.tag access."""
        program, typing = check_ok(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert tc.typing.has_child(ut, "tag")
        assert tc.typing.child_of(ut, "tag").typetype == ZTypeType.DATA

    def test_custom_tag_union_has_data_child(self):
        """Union with custom tag should have the data instance as 'tag' child."""
        program, typing = check_ok(
            "pv: data { A: 0 B: 1 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert tc.typing.has_child(ut, "tag")
        tag_data = tc.typing.child_of(ut, "tag")
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
        program, typing = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check()
        tag = tc._resolve_unit_name("system", "tag")
        assert tag is not None
        assert tag.typetype == ZTypeType.RECORD
        assert tag.isgeneric is True
        assert "t" in tag.generic_params

    def test_data_tag_returns_monomorphized_tag(self):
        """data.tag returns tag__element_type with generic_origin='tag'."""
        program, typing = check_ok(
            "mydata: data { A: 0 B: 1 }\nmain: function is { x: mydata.A }"
        )
        tc = TypeChecker(program)
        tc.check()
        dt = tc._resolved.get("test.mydata")
        tag = tc.typing.child_of(dt, "tag")
        assert tag.is_tag_generic_origin
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
        program, typing = check_ok(":u8\nmain: function is { x: u8 42 }")
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.u8")
        assert ut is not None
        assert ut.name == "u8"

    def test_label_value_core_type_resolves(self):
        """:x where x exists in core resolves correctly."""
        program, typing = check_ok(":i64\nmain: function is { x: i64 1 }")
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
        program, typing = check_ok(
            "myunion: union { :u8\n :u16\n :u32 }\n"
            "main: function is { x: myunion.u8 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert ut is not None
        assert ut.typetype == ZTypeType.UNION
        assert tc.typing.has_child(ut, "u8")
        assert tc.typing.has_child(ut, "u16")
        assert tc.typing.has_child(ut, "u32")

    def test_union_label_value_subtype_names(self):
        """Label value union subtypes resolve to their payload types."""
        program, typing = check_ok(
            "myunion: union { :u8\n :String }\nmain: function is { x: myunion.u8 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myunion")
        assert tc.typing.child_of(ut, "u8").name == "u8"
        assert tc.typing.child_of(ut, "String").name == "String"

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
        prog, typing = check_ok("m: unit { X: 42 }\nmain: function is {}")
        tc = TypeChecker(prog)
        tc.check()
        assert "m" in tc.unit_types
        assert tc.unit_types["m"].typetype == ZTypeType.UNIT

    def test_dotted_access_constant(self):
        """Dotted access to inline unit constant resolves correctly."""
        check_ok("m: unit { X: 42 }\nY: m.X\nmain: function is {}")

    def test_inline_unit_with_function(self):
        """Inline unit containing a function resolves the function type."""
        prog, typing = check_ok(
            'm: unit { greet: function is { print "hi" } }\n'
            "main: function is { m.greet }"
        )
        tc = TypeChecker(prog)
        tc.check()
        ft = tc.typing.child_of(tc.unit_types["m"], "greet")
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
        prog, typing = check_ok(
            "m: unit { pt: record { x: i64  y: i64 } }\nmain: function is {}"
        )
        tc = TypeChecker(prog)
        tc.check()
        pt = tc.typing.child_of(tc.unit_types["m"], "pt")
        assert pt is not None
        assert pt.typetype == ZTypeType.RECORD

    def test_inline_unit_function_body_checking(self):
        """Function bodies inside inline units are type-checked."""
        prog, typing = check_ok(
            'm: unit { f: function {x: i64} is { print "\\{x}" } }\n'
            "main: function is { m.f 42 }"
        )
        # if body checking failed, we would have errors
        assert isinstance(prog, zast.Program)

    def test_3level_nesting(self):
        """3-level nesting resolves correctly via dotted access."""
        prog, typing = check_ok(
            "a: unit { b: unit { c: unit { X: 99 } } }\n"
            "Y: a.b.c.X\n"
            "main: function is {}"
        )
        tc = TypeChecker(prog)
        tc.check()
        # nested units are stored with qualified names
        at = tc.unit_types.get("a")
        assert at is not None
        assert tc.typing.has_child(at, "b")

    def test_inline_unit_upward_reference(self):
        """Inline unit body can reference definitions from parent unit."""
        check_ok("X: 42\nm: unit { Y: X }\nmain: function is {}")

    def test_nesting_shadow(self):
        """Nested unit shadows parent definition via unit context stack."""
        prog, typing = check_ok(
            "a: unit { X: 10\n b: unit { X: 20\n Y: X } }\nmain: function is {}"
        )
        tc = TypeChecker(prog)
        tc.check()
        # b is stored under qualified name "a.b"
        bt = tc.unit_types.get("a.b")
        assert bt is not None
        assert tc.typing.has_child(bt, "Y")


class TestVariantTypeResolution:
    """Test that variant types resolve correctly."""

    def test_variant_resolves(self):
        """A variant definition resolves to ZTypeType.VARIANT."""
        program, typing = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert vt is not None
        assert vt.typetype == ZTypeType.VARIANT

    def test_variant_is_valtype(self):
        """Variants should be tagged as valtype (is_valtype=True)."""
        program, typing = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert vt is not None
        assert vt.is_valtype is True

    def test_variant_subtypes(self):
        """Variant subtypes should be stored as children."""
        program, typing = check_ok(
            "myvar: variant { a: i64\n b: u8\n c: null }\n"
            "main: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert tc.typing.has_child(vt, "a")
        assert tc.typing.has_child(vt, "b")
        assert tc.typing.has_child(vt, "c")
        assert tc.typing.child_of(vt, "a").name == "i64"
        assert tc.typing.child_of(vt, "b").name == "u8"
        assert tc.typing.child_of(vt, "c").name == "null"

    def test_variant_tag_generated(self):
        """Variant should have a 'tag' child holding the discriminator."""
        program, typing = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        tag = tc.typing.child_of(vt, "tag")
        assert tag is not None
        assert tag.typetype == ZTypeType.DATA
        assert tc.typing.has_child(tag, "a")
        assert tc.typing.has_child(tag, "b")

    def test_variant_null_subtype(self):
        """Null subtypes are fine in variants."""
        program, typing = check_ok(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.b }"
        )
        tc = TypeChecker(program)
        tc.check()
        vt = tc._resolved.get("test.myvar")
        assert tc.typing.child_of(vt, "b").name == "null"
        assert tc.typing.child_of(vt, "b").is_valtype is True

    def test_variant_rejects_string(self):
        """Variant subtypes that are reftypes (String) should be rejected."""
        errors = check_errors(
            "myvar: variant { a: String\n b: null }\nmain: function is { x: myvar.b }"
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
        program, typing = check_ok(
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
        """A Spec (function without body) resolves to a FUNCTION type."""
        program, typing = check_ok(
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
        """Taking a Spec name is an error (specs are types, not values)."""
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
        """Different-named functions with same signature are compatible with a Spec."""
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
        """Record with Spec (function without body) in 'is' section becomes a field."""
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
        program, typing = check_ok(
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
        create_t = t.meta_create
        assert create_t is not None
        assert tc.typing.has_child(create_t, "x")
        assert not tc.typing.has_child(create_t, "helper")


class TestDefaults:
    def test_numeric_default_resolves_i64(self):
        """Numeric default '0' resolves to i64 type."""
        program, typing = check_ok(
            "greet: function {a: 0} out i64 is { return a }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert tc.typing.has_child(t, "a")
        assert tc.typing.child_of(t, "a").name == "i64"

    def test_numeric_default_42(self):
        """Numeric default '42' resolves to i64 type."""
        program, typing = check_ok(
            "greet: function {a: 42} out i64 is { return a }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert tc.typing.child_of(t, "a").name == "i64"

    def test_param_defaults_populated_numeric(self):
        """param_defaults populated for numeric defaults."""
        program, typing = check_ok(
            "greet: function {a: 0 b: 42} out i64 is { return a + b }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert tc.typing.child_defaults_of(t) == {"a": "0", "b": "42"}

    def test_function_ref_default_detected(self):
        """Function reference default detected (function with body)."""
        program, typing = check_ok(
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
        assert tc.typing.has_child_default(t, "f")
        assert tc.typing.child_default(t, "f") == "add"

    def test_spec_no_default(self):
        """Spec (function without body) does NOT produce a default."""
        program, typing = check_ok(
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
        assert not tc.typing.has_child_default(t, "f")

    def test_call_with_missing_defaulted_arg_no_error(self):
        """Call omitting an arg that has a default produces no type error."""
        check_ok(
            "greet: function {a: 0} out i64 is { return a }\n"
            "main: function is { greet }"
        )

    def test_record_with_numeric_default_field(self):
        """Record field with numeric default stores it on the type."""
        program, typing = check_ok(
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
        assert tc.typing.child_defaults_of(t) == {"y": "0"}
        # defaults propagate to constructor
        create_t = t.meta_create
        assert create_t is not None
        assert tc.typing.child_defaults_of(create_t) == {"y": "0"}

    def test_type_name_no_default(self):
        """A type name like 'i64' does NOT produce a default."""
        program, typing = check_ok(
            "greet: function {a: i64} out i64 is { return a }\n"
            "main: function is { greet a: 5 }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert not tc.typing.has_child_default(t, "a")

    def test_variant_subtype_default_on_field(self):
        """`field: VariantType.arm` (qualified, null-payload arm) sets
        the field's stored type to the variant and stores the arm as a
        tagged-default string."""
        program, typing = check_ok(
            "direction: variant {\n"
            "    north: null\n"
            "    south: null\n"
            "} as { tag: u8.tag }\n"
            "mystate: record {\n"
            "    dir: direction.north\n"
            "}\n"
            "main: function is { s: mystate }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "mystate")
        assert t is not None
        # field stored type is the variant, not the null arm
        dir_t = tc.typing.child_of(t, "dir")
        assert dir_t is not None
        assert dir_t.name == "direction"
        # default carries the structured #variant tag
        assert tc.typing.child_default(t, "dir") == "#variant:north"

    def test_variant_subtype_default_on_param(self):
        """Function param with a variant subtype default resolves the
        param type to the variant and stores the tagged default."""
        program, typing = check_ok(
            "color: variant {\n"
            "    red: null\n"
            "    green: null\n"
            "    blue: null\n"
            "} as { tag: u8.tag }\n"
            "paint: function {c: color.red} out i64 is { return 0 }\n"
            "main: function is { x: paint }"
        )
        tc = TypeChecker(program)
        tc.check()
        f = tc._resolve_unit_name("test", "paint")
        assert f is not None
        c_t = tc.typing.child_of(f, "c")
        assert c_t is not None and c_t.name == "color"
        assert tc.typing.child_default(f, "c") == "#variant:red"

    def test_union_subtype_default_on_class_field(self):
        """Union null-payload subtype works the same as variant. Classes
        can hold union fields; records cannot (separate reftype rule)."""
        program, typing = check_ok(
            "event: union {\n"
            "    idle: null\n"
            "    busy: null\n"
            "}\n"
            "machine: class {\n"
            "    e: event.idle\n"
            "}\n"
            "main: function is { m: machine }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "machine")
        assert t is not None
        e_t = tc.typing.child_of(t, "e")
        assert e_t is not None and e_t.name == "event"
        assert tc.typing.child_default(t, "e") == "#variant:idle"

    def test_variant_payload_arm_rejected_as_default(self):
        """Defaulting to a payload-carrying arm is a typecheck error --
        a default expression can't supply constructor arguments."""
        errors = check_errors(
            "result: variant {\n"
            "    ok: i64\n"
            "    fail: null\n"
            "} as { tag: u8.tag }\n"
            "myrec: record {\n"
            "    r: result.ok\n"
            "}\n"
            "main: function is { v: myrec }"
        )
        assert any("not defaultable" in e.msg for e in errors), [e.msg for e in errors]


class TestNumericCasting:
    def test_dotted_numeric_u32(self):
        """x: 0.u32 resolves, type is u32."""
        program, typing = check_ok("main: function is { x: 0.u32 }")
        tc = TypeChecker(program)
        tc.check()

    def test_dotted_numeric_i8(self):
        """x: 42.i8 resolves, type is i8."""
        program, typing = check_ok("main: function is { x: 42.i8 }")
        tc = TypeChecker(program)
        tc.check()

    def test_dotted_numeric_hex(self):
        """x: 0xff.u16 resolves, type is u16."""
        program, typing = check_ok("main: function is { x: 0xff.u16 }")
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
        program, typing = check_ok(
            "greet: function {a: 0.u32} out u32 is { return a }\n"
            "main: function is { greet }"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "greet")
        assert t is not None
        assert tc.typing.child_defaults_of(t) == {"a": "0"}

    def test_dotted_default_record_field(self):
        """Record with x: 0.u32 field default."""
        program, typing = check_ok(
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
        assert tc.typing.child_defaults_of(t) == {"y": "0"}


class TestProtocols:
    def test_protocol_resolves(self):
        """Protocol definition creates PROTOCOL ZType with Spec children."""
        program, typing = check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "Reader")
        assert t is not None
        assert t.typetype == ZTypeType.PROTOCOL
        assert t.is_valtype is False
        assert tc.typing.has_child(t, "read")
        assert tc.typing.child_of(t, "read").typetype == ZTypeType.FUNCTION

    def test_protocol_conformance_ok(self):
        """Record with correct methods passes conformance check."""
        check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )

    def test_protocol_conformance_missing(self):
        """Record missing a Spec method errors."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any("missing method 'read'" in e.msg for e in errors)

    def test_protocol_as_param_type(self):
        """Function accepting protocol type resolves correctly."""
        check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n"
            "    return result\n"
            "}\n"
            "main: function is {}"
        )

    def test_protocol_instance_via_dotted_path(self):
        """f.myreader resolves to PROTOCOL type."""
        program, typing = check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
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
        assert tc.typing.has_child(t, "myreader")
        assert tc.typing.child_of(t, "myreader").typetype == ZTypeType.PROTOCOL

    def test_protocol_is_field(self):
        """Protocol in 'is' block becomes instance field."""
        check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "wrapper: record {\n"
            "    r: Reader\n"
            "}\n"
            "main: function is {}"
        )

    def test_protocol_signature_matching_ok(self):
        """Matching signatures pass conformance check."""
        check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )

    def test_protocol_signature_param_count_mismatch(self):
        """Error when impl has different param count than Spec."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this} out i64 is {\n"
            "        return f.fd\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any("0 param" in e.msg and "expects 1" in e.msg for e in errors)

    def test_protocol_signature_param_type_mismatch(self):
        """Error when impl param type differs from Spec."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
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
        """Error when impl return type differs from Spec."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out bool is {\n"
            "        return 0.bool\n"
            "    }\n"
            "}\n"
            "main: function is { f: myfile fd: 1 }"
        )
        assert any("returns 'bool'" in e.msg and "'i64'" in e.msg for e in errors)

    def test_protocol_signature_no_return_both_ok(self):
        """Both Spec and impl with no return type is fine."""
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
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
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
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
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
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "use_reader: function {r: Reader} out i64 is {\n"
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
        program, typing = check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "Reader")
        assert t is not None
        assert tc.typing.has_child(t, "create")
        assert tc.typing.child_of(t, "create").typetype == ZTypeType.FUNCTION

    def test_protocol_create_typechecks(self):
        """Reader.create from: f.take type-checks, Result is PROTOCOL."""
        check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.create from: f.take\n"
            "}\n"
        )

    def test_protocol_create_nonconforming_error(self):
        """from: with non-conforming type errors."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "other: record { x: i64 }\n"
            "main: function is {\n"
            "    o: other x: 1\n"
            "    r: Reader.create from: o.take\n"
            "}\n"
        )
        assert any("does not conform" in e.msg for e in errors)

    def test_protocol_create_missing_from_error(self):
        """Reader.create with wrong arg name errors."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.create x: f.take\n"
            "}\n"
        )
        assert any("requires 'from:'" in e.msg for e in errors)

    def test_generic_protocol_no_create(self):
        """Generic (unmonomorphized) protocol has no `create`."""
        program, typing = check_ok(
            "myproto: protocol {\n"
            "    t: Any.generic\n"
            "    get: function {:this} out t\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        t = tc._resolved.get("test.myproto")
        assert t is not None
        assert t.isgeneric is True
        assert not tc.typing.has_child(t, "create")

    def test_protocol_has_no_take(self):
        """Protocol type has no `.take` child; `.take` is not a constructor."""
        program, typing = check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "Reader")
        assert t is not None
        assert not tc.typing.has_child(t, "take")
        assert tc.typing.has_child(t, "create")
        assert tc.typing.has_child(t, "borrow")

    def test_protocol_take_rejected_with_hint(self):
        """`Reader.take from: ...` errors with a migration hint."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.take from: f.take\n"
            "}\n"
        )
        assert any(
            "no longer a constructor" in e.msg
            and ".create" in e.msg
            and ".borrow" in e.msg
            for e in errors
        )

    def test_protocol_has_borrow(self):
        """Protocol type has `borrow` child (FUNCTION)."""
        program, typing = check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "Reader")
        assert t is not None
        assert tc.typing.has_child(t, "borrow")
        assert tc.typing.child_of(t, "borrow").typetype == ZTypeType.FUNCTION

    def test_protocol_borrow_typechecks(self):
        """Reader.borrow from: f.lock type-checks, Result is PROTOCOL."""
        check_ok(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.borrow from: f.lock\n"
            "}\n"
        )

    def test_protocol_borrow_nonconforming_error(self):
        """from: with non-conforming type errors for borrow."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "other: record { x: i64 }\n"
            "main: function is {\n"
            "    o: other x: 1\n"
            "    r: Reader.borrow from: o.lock\n"
            "}\n"
        )
        assert any("does not conform" in e.msg for e in errors)

    def test_protocol_borrow_missing_from_error(self):
        """Reader.borrow with wrong arg name errors."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.borrow x: f.lock\n"
            "}\n"
        )
        assert any("requires 'from:'" in e.msg for e in errors)

    def test_protocol_borrow_locks_source(self):
        """Source is locked after borrow — second borrow errors (like obj.label)."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.borrow from: f.lock\n"
            "    b: f.borrow\n"
            "}\n"
        )
        assert any(
            "locked" in e.msg.lower() or "exclusive" in e.msg.lower() for e in errors
        )

    def test_generic_protocol_no_take(self):
        """Generic (unmonomorphized) protocol has no `take`."""
        program, typing = check_ok(
            "myproto: protocol {\n"
            "    t: Any.generic\n"
            "    get: function {:this} out t\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        t = tc._resolved.get("test.myproto")
        assert t is not None
        assert t.isgeneric is True
        assert not tc.typing.has_child(t, "take")

    def test_generic_protocol_no_borrow(self):
        """Generic (unmonomorphized) protocol has no `borrow`."""
        program, typing = check_ok(
            "myproto: protocol {\n"
            "    t: Any.generic\n"
            "    get: function {:this} out t\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        t = tc._resolved.get("test.myproto")
        assert t is not None
        assert t.isgeneric is True
        assert not tc.typing.has_child(t, "borrow")


class TestProtocolCreateInvalidatesSource:
    """Phase A: `.create` must invalidate its `from:` argument source.

    `.create` has parameter ownership TAKE; passing a source to it consumes
    ownership. Reads of the source after the call must be rejected with the
    standard 'cannot use X after ownership transfer' error. This is enforced
    at the type-checker level without requiring `.take` at the call site.
    """

    def _reader_and_myfile(self) -> str:
        return (
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
        )

    def test_create_from_record_invalidates_source(self):
        """proto.create from: rec — `rec` unreadable afterward (no .take)."""
        errors = check_errors(
            self._reader_and_myfile() + "main: function is {\n"
            "    o: myfile fd: 20\n"
            "    r: Reader.create from: o\n"
            '    print "\\{o.fd}"\n'
            "}"
        )
        assert any(
            "after ownership transfer" in e.msg and "'o'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'o', got: {[e.msg for e in errors]}"

    def test_create_bare_form_invalidates_source(self):
        """Bare-name form `proto source` behaves the same as proto.create."""
        errors = check_errors(
            self._reader_and_myfile() + "main: function is {\n"
            "    o: myfile fd: 20\n"
            "    r: Reader o\n"
            '    print "\\{o.fd}"\n'
            "}"
        )
        assert any(
            "after ownership transfer" in e.msg and "'o'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'o', got: {[e.msg for e in errors]}"

    def test_create_explicit_take_still_works(self):
        """`.take` at the call site is idempotent with the implicit rule."""
        check_ok(
            self._reader_and_myfile() + "main: function is {\n"
            "    o: myfile fd: 20\n"
            "    r: Reader.create from: o.take\n"
            "}"
        )

    def test_box_from_owned_returning_method_succeeds(self):
        """Box from: s.copy — `.copy` on a borrowed source produces a fresh
        owned value, satisfying TAKE on the constructor's `from:` param.
        The receiver `s` remains usable afterward. Regression for
        constructor-site hoisting (protocol.create / typedef.create /
        Box from:): the synth temp `_tN: s.copy` is OWNED, so
        `_apply_take_to_arg` releases the temp's locks and invalidates
        the temp — never the receiver. This shape was previously handled
        by an `_apply_take_to_arg` short-circuit on `DOTTEDPATH`+owned-
        returning method; that short-circuit is deleted once hoisting
        reaches all three constructor sites.
        """
        check_ok(
            "f: function {s: String} is {\n"
            "    b: Box from: s.copy\n"
            '    print "{s}"\n'
            "}\n"
            "main: function is {}"
        )

    def test_create_from_class_invalidates_source(self):
        """Classes: source invalidated by .create, no .take required at call site."""
        errors = check_errors(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    o: myfile fd: 20\n"
            "    r: Reader.create from: o\n"
            '    print "\\{o.fd}"\n'
            "}"
        )
        assert any(
            "after ownership transfer" in e.msg and "'o'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'o', got: {[e.msg for e in errors]}"

    def test_create_rejects_borrowed_source(self):
        """Passing a borrowed local to .create yields the standard error."""
        errors = check_errors(
            self._reader_and_myfile() + "use_borrow: function {b: myfile.borrow} is {\n"
            "    r: Reader.create from: b\n"
            "}\n"
            "main: function is {\n"
            "    o: myfile fd: 20\n"
            "    use_borrow b: o\n"
            "}"
        )
        assert any(
            "borrowed" in e.msg.lower() and "take" in e.msg.lower() for e in errors
        ), f"expected borrowed-to-take error, got: {[e.msg for e in errors]}"


class TestProtocolBorrowLocksSource:
    """Phase A: `.borrow` and the label-form borrow must lock the source.

    Borrowing already locks the source in today's code via
    `_pending_borrow_lock`; these tests pin that behavior down as a
    regression suite alongside the .create changes.
    """

    def _reader_and_myfile(self) -> str:
        return (
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
        )

    def test_label_borrow_does_not_invalidate_source(self):
        """`r: f.myreader` leaves `f` readable (borrow, not take)."""
        check_ok(
            self._reader_and_myfile() + "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            "    n: r.read b: 5\n"
            '    print "\\{n}"\n'
            "}"
        )

    def test_explicit_borrow_does_not_invalidate_source(self):
        """`Reader.borrow from: f` mirrors the label form."""
        check_ok(
            self._reader_and_myfile() + "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.borrow from: f\n"
            "    n: r.read b: 5\n"
            '    print "\\{n}"\n'
            "}"
        )


class TestTypedefCreateInvalidatesSource:
    """Phase A: typedef `.create` must invalidate its `from:` argument.

    Typedefs wrap a base type via `field: base.typedef` pattern; their
    `.create` shares the TAKE semantics of protocol/facet `.create`.
    """

    def test_typedef_create_wrapping_i64_literal(self):
        """Literal source: no named source to invalidate — continues to work."""
        check_ok(
            "meters: record { val: i64.typedef }\n"
            "main: function is {\n"
            "    m: meters.create from: 42\n"
            "}"
        )

    def test_typedef_create_chained_invalidates_inner(self):
        """Chained typedef: outer.create from: inner_local — inner invalidated."""
        errors = check_errors(
            "meters: record { val: i64.typedef }\n"
            "height: record { h: meters.typedef }\n"
            "main: function is {\n"
            "    m: meters.create from: 100\n"
            "    h: height.create from: m\n"
            '    print "\\{m}"\n'
            "}"
        )
        assert any(
            "after ownership transfer" in e.msg and "'m'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'm', got: {[e.msg for e in errors]}"

    def test_typedef_create_invalidates_without_call_site_take(self):
        """Same as chained test — confirms no `.take` at call site needed."""
        errors = check_errors(
            "meters: record { val: i64.typedef }\n"
            "height: record { h: meters.typedef }\n"
            "takes: function {m: meters} out i64 is { return m }\n"
            "main: function is {\n"
            "    m: meters.create from: 100\n"
            "    h: height.create from: m\n"
            "    n: takes m: m\n"
            "}"
        )
        # invalidation should surface on the subsequent use of `m`
        assert any(
            "after ownership transfer" in e.msg and "'m'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'm', got: {[e.msg for e in errors]}"


class TestBoxInvalidatesSource:
    """Phase B: `Box from:` must invalidate its source name.

    Boxing is an ownership transfer from the source into the Box — the
    source becomes inaccessible afterward. Literals remain legal (nothing
    to invalidate).

    Applies to both BOX_CREATE (stack valtype → heap copy) and
    BOX_PASSTHROUGH (already-heap source, ownership handed off).
    """

    def test_box_from_literal_ok(self):
        """Literal sources have no root name — unaffected."""
        check_ok("main: function is { b: Box from: 42 }")
        check_ok('main: function is { b: Box from: "hi".string }')

    def test_box_passthrough_invalidates_source(self):
        """Nested box: `Box from: b` invalidates `b`; later read is rejected."""
        errors = check_errors(
            "main: function is {\n"
            "    b: Box from: 42\n"
            "    b2: Box from: b\n"
            '    print "\\{b}"\n'
            "}"
        )
        assert any(
            "after ownership transfer" in e.msg and "'b'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'b', got: {[e.msg for e in errors]}"

    def test_box_passthrough_chain_final_user_ok(self):
        """Long passthrough chain — only the final name is usable afterward."""
        check_ok(
            "main: function is {\n"
            "    b: Box from: 42\n"
            "    b2: Box from: b\n"
            "    b3: Box from: b2\n"
            '    print "\\{b3}"\n'
            "}"
        )

    def test_box_from_valtype_record_invalidates_source(self):
        """Boxing a stack record consumes it."""
        errors = check_errors(
            "myrec: record { n: i64 }\n"
            "main: function is {\n"
            "    r: myrec n: 5\n"
            "    b: Box from: r\n"
            '    print "\\{r.n}"\n'
            "}"
        )
        assert any(
            "after ownership transfer" in e.msg and "'r'" in e.msg for e in errors
        ), f"expected ownership-transfer error on 'r', got: {[e.msg for e in errors]}"

    def test_box_in_second_position_uses_fresh_sources(self):
        """After boxing source1, a second Box requires a separate source."""
        check_ok(
            "main: function is {\n"
            "    b1: Box from: 42\n"
            "    b2: Box from: 99\n"
            '    print "\\{b1}"\n'
            '    print "\\{b2}"\n'
            "}"
        )


class TestBorrowedProtocolEscape:
    """Phase C: a borrowed protocol value cannot escape its source's scope.

    A borrowed protocol is a 3-word struct with `data` pointing into the
    source's storage and `destroy == NULL`. If the wrapper escapes (return
    from function, stored in a data structure, passed to a TAKE parameter
    that stores it), the source can die before the protocol is used, and
    dispatch reads freed memory.

    The compiler already locks the source for the protocol's local scope;
    this phase extends that to reject escape of the wrapper itself.
    """

    def _reader_and_myfile(self) -> str:
        return (
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
        )

    def test_return_label_borrowed_protocol_rejected(self):
        """`r: f.myreader; return r` — borrow cannot escape."""
        errors = check_errors(
            self._reader_and_myfile() + "make_reader: function out Reader is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            "    return r\n"
            "}\n"
            "main: function is { }"
        )
        assert any(
            "borrow" in e.msg.lower() and "return" in e.msg.lower() for e in errors
        ) or any("cannot return" in e.msg.lower() and "'r'" in e.msg for e in errors), (
            f"expected borrow-escape error, got: {[e.msg for e in errors]}"
        )

    def test_return_explicit_borrow_protocol_rejected(self):
        """Same via `Reader.borrow from: f`."""
        errors = check_errors(
            self._reader_and_myfile() + "make_reader: function out Reader is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.borrow from: f\n"
            "    return r\n"
            "}\n"
            "main: function is { }"
        )
        assert any(
            "borrow" in e.msg.lower() and "return" in e.msg.lower() for e in errors
        ) or any("cannot return" in e.msg.lower() and "'r'" in e.msg for e in errors), (
            f"expected borrow-escape error, got: {[e.msg for e in errors]}"
        )

    def test_return_owned_protocol_accepted(self):
        """Owned protocol is escape-capable — legal to return."""
        check_ok(
            self._reader_and_myfile() + "make_reader: function out Reader is {\n"
            "    f: myfile fd: 10\n"
            "    r: Reader.create from: f\n"
            "    return r\n"
            "}\n"
            "main: function is {\n"
            "    r: make_reader\n"
            "}"
        )

    def test_store_borrowed_protocol_in_record_field_rejected(self):
        """Borrowed protocol cannot be stored in a wrapper record."""
        errors = check_errors(
            self._reader_and_myfile() + "wrapper: record { r: Reader }\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            "    w: wrapper r: r\n"
            "}"
        )
        assert any("borrow" in e.msg.lower() and "'r'" in e.msg for e in errors), (
            f"expected borrow-escape error on field store, got: {[e.msg for e in errors]}"
        )

    def test_pass_borrowed_protocol_to_take_param_rejected(self):
        """Borrowed protocol cannot flow into a TAKE parameter."""
        errors = check_errors(
            self._reader_and_myfile() + "store: function {r: Reader.take} out i64 is "
            "{ return r.read b: 0 }\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            "    n: store r: r\n"
            "}"
        )
        assert any("borrowed variable" in e.msg and "take" in e.msg for e in errors), (
            f"expected borrowed-to-take error, got: {[e.msg for e in errors]}"
        )


class TestBoxProtocolComposition:
    """Phase D: `Box → protocol.create` composition.

    Investigation confirmed: `Box(T)` does not propagate protocol
    conformance, so `proto.create from: b` where `b: Box from: T` fails at
    the conformance check. This is the intended design — `proto.create`
    already heap-allocates internally, making the Box-first composition
    redundant. The error message hints at the direct form.
    """

    def _proto_and_myfile(self) -> str:
        return (
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myfile: record {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {f: this b: i64} out i64 is {\n"
            "        return f.fd + b\n"
            "    }\n"
            "}\n"
        )

    def test_box_protocol_create_rejected_with_hint(self):
        """Boxing first and then protocol.create is rejected; hint steers user."""
        errors = check_errors(
            self._proto_and_myfile() + "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    b: Box from: f\n"
            "    p: Reader.create from: b\n"
            "}"
        )
        assert any("does not conform" in e.msg for e in errors), (
            f"expected conformance error, got: {[e.msg for e in errors]}"
        )
        assert any(
            e.hint is not None and "heap-allocates internally" in e.hint for e in errors
        ), f"expected hint on reader.create, got: {[e.hint for e in errors]}"

    def test_direct_create_from_record_is_the_supported_form(self):
        """`.create` already produces an escape-capable owned protocol."""
        check_ok(
            self._proto_and_myfile() + "make: function out Reader is {\n"
            "    f: myfile fd: 10\n"
            "    p: Reader.create from: f\n"
            "    return p\n"
            "}\n"
            "main: function is {\n"
            "    p: make\n"
            '    print "\\{p.read b: 5}"\n'
            "}"
        )


class TestResultGenericType:
    """I/O Phase 1: built-in generic `Result t: type, e: type`.

    `Result` is a two-arm union (ok / err) parameterized over both arms.
    Used as the foundation for I/O fallible operations and Any future
    API that wants pattern-matchable success/failure.
    """

    def test_result_ok_construction_with_explicit_err_type(self):
        """`Result.ok 42 e: i64` constructs the ok arm; e supplied explicitly."""
        check_ok("main: function is {\n    r: Result.ok 42 e: i64\n}")

    def test_result_err_construction_with_explicit_t_type(self):
        """`Result.err msg t: u64` constructs the err arm; t supplied explicitly."""
        check_ok("main: function is {\n    r: Result.err 99 t: u64\n}")

    def test_result_pattern_match_dispatches_arms(self):
        """Match on Result reaches both arms."""
        check_ok(
            "main: function is {\n"
            "    r: Result.ok 42 e: i64\n"
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "got ok"\n'
            "    } case err then {\n"
            '        print "got err"\n'
            "    }\n"
            "}"
        )

    def test_result_with_reftype_ok_arm(self):
        """ok arm may be a reftype (String), err a valtype (i64)."""
        check_ok('main: function is {\n    r: Result.ok "hi".string e: i64\n}')

    def test_result_with_reftype_err_arm(self):
        """err arm may be a reftype (String), ok a valtype (i64)."""
        check_ok('main: function is {\n    r: Result.err "boom".string t: i64\n}')

    def test_result_err_with_null_t_type_arg(self):
        """`t: null` is valid for constructing `Result<null, E>.err` — the
        null-arm case is exactly what `Writer.flush` returns, and
        downstream code (e.g. BufWriter.flush) needs to forward
        incoming errs with `Result.err x t: null`."""
        check_ok("main: function is {\n    r: Result.err 42 t: null\n}")

    def test_result_err_forward_across_result_shapes(self):
        """Propagation pattern: a function that converts a
        `Result<u64, IoError>` into a `Result<null, IoError>` by
        returning ok-null on success and reconstructing err on failure
        using the narrowed err payload."""
        check_ok(
            "forward: function {r: (Result t: u64 e: i64)}"
            " out (Result t: null e: i64) is {\n"
            "    match (r) case ok then {\n"
            "        return (Result.ok null e: i64)\n"
            "    } case err then {\n"
            "        return (Result.err r t: null)\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    s: (Result.err 42 t: u64)\n"
            "    o: (forward r: s)\n"
            "}\n"
        )


class TestIoErrorVariant:
    """I/O Phase 2: `IoError` variant + Result-with-IoError integration.

    `IoError` is the failure side of every fallible io operation; it's
    constructed in the C runtime from the underlying errno. User code
    pattern-matches the specific variants it cares about and treats the
    rest as `other`.
    """

    def test_ioerror_can_be_constructed(self):
        """Each IoError variant arm is constructable."""
        check_ok(
            "main: function is {\n"
            "    a: IoError.notfound\n"
            "    b: IoError.permissiondenied\n"
            "    c: IoError.eof\n"
            '    d: IoError.invalidpath "x"\n'
            "}"
        )

    def test_ioerror_construction_from_mainunit(self):
        """Phase 4 regression: bare `IoError.<arm>` resolves and emits
        proper construction when used in a main unit that does not
        redeclare IoError. Before the emitter's cross-unit resolver
        fallback, the bare name failed to resolve and the emitter fell
        through to literal-Text output that wouldn't compile.
        """
        check_ok(
            "main: function is {\n"
            "    e: IoError.notfound\n"
            '    e2: IoError.invalidpath "p".string\n'
            "    s: seekorigin.start\n"
            "}"
        )

    def test_ioerror_pattern_match(self):
        """Match on IoError reaches every variant."""
        check_ok(
            "main: function is {\n"
            "    e: IoError.notfound\n"
            "    match (\n"
            "        e\n"
            "    ) case notfound then {\n"
            '        print "nf"\n'
            "    } case permissiondenied then {\n"
            '        print "perm"\n'
            "    } case interrupted then {\n"
            '        print "intr"\n'
            "    } case invalidpath then {\n"
            '        print "inval"\n'
            "    } case eof then {\n"
            '        print "eof"\n'
            "    } case badencoding then {\n"
            '        print "enc"\n'
            "    } case exists then {\n"
            '        print "exists"\n'
            "    } case isdir then {\n"
            '        print "isdir"\n'
            "    } case notdir then {\n"
            '        print "notdir"\n'
            "    } case nospace then {\n"
            '        print "nospc"\n'
            "    } case other then {\n"
            '        print "other"\n'
            "    }\n"
            "}"
        )

    def test_result_with_ioerror_in_err_arm(self):
        """`Result` parameterized with IoError in the err arm — the canonical io return shape."""
        check_ok(
            "main: function is {\n"
            "    r: Result.ok 42 e: IoError\n"
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "got n"\n'
            "    } case err then {\n"
            '        print "got err"\n'
            "    }\n"
            "}"
        )


class TestStreamProtocolsAndFile:
    """I/O Phase 3: Reader/Writer/Closer/Seeker protocols + File class.

    Type-system scaffolding only. Native operations and method bodies
    arrive in Phase 4 along with the dispatch infrastructure.
    """

    def test_seekorigin_constructs(self):
        """seekorigin variant arms construct cleanly."""
        check_ok(
            "main: function is {\n"
            "    a: seekorigin.start\n"
            "    b: seekorigin.current\n"
            "    c: seekorigin.end\n"
            "}"
        )

    def test_file_class_can_be_declared(self):
        """File class instances can be constructed."""
        check_ok("main: function is {\n    f: File fd: 0 closed: 0 == 1\n}")

    def test_file_class_resolves_as_class(self):
        """File is a CLASS type with fd and closed fields."""
        program, typing = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        f = tc._resolved.get("system.io.File") or tc._resolved.get("io.File")
        assert f is not None
        assert f.typetype == ZTypeType.CLASS
        assert tc.typing.has_child(f, "fd")
        assert tc.typing.has_child(f, "closed")

    def test_protocols_resolve(self):
        """Reader/Writer/Closer/Seeker resolve as PROTOCOL types."""
        program, typing = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        for p in ("Reader", "Writer", "Closer", "Seeker"):
            t = tc._resolved.get(f"system.io.{p}") or tc._resolved.get(f"io.{p}")
            assert t is not None, f"protocol {p} not resolved"
            assert t.typetype == ZTypeType.PROTOCOL, (
                f"{p} expected PROTOCOL, got {t.typetype}"
            )

    def test_protocols_have_their_methods(self):
        """Each protocol declares the expected method set."""
        program, typing = check_ok("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        expected = {
            "Reader": {"read"},
            "Writer": {"write", "flush"},
            "Closer": {"close"},
            "Seeker": {"seek"},
        }
        for proto, methods in expected.items():
            t = tc._resolved.get(f"system.io.{proto}") or tc._resolved.get(
                f"io.{proto}"
            )
            assert t is not None
            actual = {
                k for k in tc.typing.child_names_of(t) if k not in ("create", "borrow")
            }
            assert methods.issubset(actual), (
                f"protocol {proto}: expected {methods}, got {actual}"
            )


class TestIoNativeDispatch:
    """I/O Phase 5a: native function dispatch + io.eprintln.

    First io native beyond the hardcoded `print`. Proves the generic
    dispatch path: calls to `io.<name>` that are declared `is native`
    emit as `z_io_<name>(args)` and the runtime emitter includes the
    C implementation when `needs_io` is set.
    """

    def test_eprintln_typechecks(self):
        """io.eprintln msg: StringView is callable."""
        check_ok('main: function is {\n    io.eprintln "diag"\n}')

    def test_eprintln_with_stringview_literal(self):
        """io.eprintln accepts a bare String literal (auto-projects to StringView)."""
        check_ok('main: function is { io.eprintln "error" }')

    def test_read_text_typechecks(self):
        """io.readText path: String returns Result(String, IoError).
        The native dispatch plus Result union monomorphization must both
        resolve."""
        check_ok('main: function is {\n    r: io.readText "/tmp/x"\n}')

    def test_write_text_typechecks(self):
        """io.writeText returns Result(null, IoError)."""
        check_ok(
            'main: function is {\n    r: io.writeText path: "/tmp/x" content: "hi"\n}'
        )

    def test_append_text_typechecks(self):
        """io.appendText has the same shape as writeText."""
        check_ok(
            'main: function is {\n    r: io.appendText path: "/tmp/x" content: "hi"\n}'
        )

    def test_exists_returns_bool(self):
        """io.exists returns plain bool — no Result wrapper."""
        check_ok(
            "main: function is {\n"
            '    b: io.exists "/tmp/x"\n'
            '    if b then print "y" else print "n"\n'
            "}"
        )

    def test_mkdir_remove_rename_typecheck(self):
        """mkdir / remove / rename all return Result(null, IoError)."""
        check_ok(
            "main: function is {\n"
            '    r1: io.mkdir "/tmp/d"\n'
            '    r2: io.remove "/tmp/d"\n'
            '    r3: io.rename from: "/tmp/a" to: "/tmp/b"\n'
            "}"
        )

    def test_read_text_pattern_match(self):
        """Result of io.readText can be matched on ok/err arms."""
        check_ok(
            "main: function is {\n"
            '    r: io.readText "/tmp/x"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "got ok"\n'
            "    } case err then {\n"
            '        print "got err"\n'
            "    }\n"
            "}"
        )

    def test_open_returns_result_file(self):
        """io.open Path:_ mode:_ returns Result(File, IoError).

        Exercises cross-unit reference to `openmode` (re-exported from
        core) and the Result-monomorphization Path over the File class.
        """
        check_ok(
            'main: function is {\n    r: io.open path: "/tmp/x" mode: openmode.read\n}'
        )

    def test_open_all_modes_typecheck(self):
        """All three openmode arms are accepted by io.open."""
        for mode in ("read", "write", "append"):
            check_ok(
                "main: function is {\n"
                f'    r: io.open path: "/tmp/x" mode: openmode.{mode}\n'
                "}"
            )

    def test_open_pattern_match(self):
        """Result of io.open can be pattern-matched on ok/err."""
        check_ok(
            "main: function is {\n"
            '    r: io.open path: "/tmp/x" mode: openmode.write\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "opened"\n'
            "    } case err then {\n"
            '        print "open failed"\n'
            "    }\n"
            "}"
        )

    def test_file_has_destructor(self):
        """The io.File class must carry a destructor so Result(File, _)
        destructors invoke z_File_destroy on the ok payload (RAII close)."""
        program, typing, _ = parse_and_check("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        file_type = tc._resolved.get("io.File")
        assert file_type is not None
        assert (file_type.destructor_name is not None) is True
        assert file_type.destructor_name == "z_File_destroy"

    def test_file_read_typechecks(self):
        """File.read takes (into: Bytes, max: u64) and returns
        Result(u64, IoError). Exercised through the match-ok narrowing
        pattern that unwraps the File handle out of io.open's Result."""
        check_ok(
            "main: function is {\n"
            '    r: io.open path: "/tmp/x" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        buf: Bytes\n"
            "        n: r.read into: buf max: 1024.u64\n"
            '        print "ok"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_file_write_typechecks(self):
        """File.write takes (from: ByteView) and returns
        Result(u64, IoError)."""
        check_ok(
            "main: function is {\n"
            '    r: io.open path: "/tmp/x" mode: openmode.write\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        buf: Bytes\n"
            "        buf.append from: 65.u8\n"
            "        bv: ByteView.borrow from: buf.listview\n"
            "        n: r.write from: bv\n"
            '        print "ok"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_file_seek_typechecks(self):
        """File.seek takes (to: i64, from: seekorigin) and returns
        Result(u64, IoError)."""
        check_ok(
            "main: function is {\n"
            '    r: io.open path: "/tmp/x" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        n: r.seek to: 0.i64 from: seekorigin.end\n"
            '        print "ok"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_file_declares_all_stream_protocol_conformance(self):
        """io.File declares `:Reader :Writer :Closer :Seeker`. Protocol
        conformance validation compares method signatures against each
        protocol; all four must match for this to typecheck."""
        program, typing, _ = parse_and_check("main: function is {}")
        tc = TypeChecker(program)
        tc.check(full=True)
        file_type = tc._resolved.get("io.File")
        assert file_type is not None
        labels = [lbl for (lbl, _) in tc._protocol_labels.get("File", [])]
        for expected in ("Reader", "Writer", "Closer", "Seeker"):
            assert expected in labels, (
                f"file should declare :{expected} conformance; labels: {labels}"
            )
            assert tc.typing.child_of(file_type, expected) is not None

    def test_file_projects_to_closer(self):
        """A File value can be passed as a `Closer` parameter via the
        `.Closer` projection. Verifies that the typechecker accepts
        projection through `fr.Closer` inside the ok arm of a
        Result match."""
        check_ok(
            "use_closer: function {c: Closer} is {\n"
            '    print "got Closer"\n'
            "}\n"
            "main: function is {\n"
            '    fr: io.open path: "/tmp/x" mode: openmode.write\n'
            "    match (\n"
            "        fr\n"
            "    ) case ok then {\n"
            "        use_closer c: fr.Closer\n"
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_stdio_handles_coerce_to_protocol(self):
        """`io.stdin` / `io.stdout` / `io.stderr` are declared as
        zero-arg native functions; accessing them via bare Path
        coerces to their return type (`Reader` / `Writer`) so users
        can write `w: io.stdout` and immediately treat `w` as a
        Writer."""
        check_ok(
            "main: function is {\n"
            "    w: io.stdout\n"
            "    e: io.stderr\n"
            "    r: io.stdin\n"
            '    print "ok"\n'
            "}"
        )

    def test_stdio_handles_reexported_in_core(self):
        """core re-exports `stdin` / `stdout` / `stderr` so users
        can drop the `io.` prefix."""
        check_ok('main: function is {\n    w: stdout\n    print "ok"\n}')

    def test_stat_typechecks(self):
        """io.stat returns Result(filestat, IoError); filestat has
        `kind: filekind` and `size: u64`."""
        check_ok(
            "main: function is {\n"
            '    s: io.stat "/tmp/x"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.size}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_mkdirp_typechecks(self):
        """io.mkdirp returns Result(null, IoError), same shape as mkdir."""
        check_ok(
            "main: function is {\n"
            '    r: io.mkdirp "/tmp/x/y/z"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "ok"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_filekind_variant_subtypes(self):
        """filekind has File / dir / symlink / other arms; each is
        pattern-matchable through a stat Result."""
        check_ok(
            "main: function is {\n"
            '    s: io.stat "/tmp/x"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        match (\n"
            "            s.kind\n"
            "        ) case file then {\n"
            '            print "file"\n'
            "        } case dir then {\n"
            '            print "dir"\n'
            "        } case symlink then {\n"
            '            print "link"\n'
            "        } case other then {\n"
            '            print "other"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_lstat_typechecks(self):
        """io.lstat shares stat's signature: Result(filestat, IoError)."""
        check_ok(
            "main: function is {\n"
            '    s: io.lstat "/tmp/x"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.size}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_filestat_mtime_and_mode_typecheck(self):
        """filestat exposes mtimeSeconds: u64 and mode: u32 for
        callers that want freshness or permission bits."""
        check_ok(
            "main: function is {\n"
            '    s: io.stat "/tmp/x"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.mtimeSeconds} \\{s.mode}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_list_dir_typechecks(self):
        """io.listDir signature parses and resolves; ok-arm is a List
        of strings, err-arm is IoError."""
        check_ok(
            "main: function is {\n"
            '    r: io.listDir "/tmp"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "\\{r.length}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_narrowed_bare_field_typechecks(self):
        """Inside `case ok then`, `s.size` reads through the narrowed
        filestat payload — no explicit `s.ok` required."""
        check_ok(
            "main: function is {\n"
            '    s: io.stat "/tmp/x"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.size} \\{s.mtimeSeconds}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )

    def test_narrowed_missing_field_errors(self):
        """Accessing a field that isn't on the narrowed payload type
        is a clear error — not the old silent None."""
        errors = check_errors(
            "main: function is {\n"
            '    s: io.stat "/tmp/x"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.bogus}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert any("bogus" in e.msg for e in errors)
        assert any("narrowed" in e.msg for e in errors)

    def test_protocol_zero_arg_method_coerces_to_return_type(self):
        """Accessing a zero-arg protocol Spec as an rvalue types as
        the Spec's return type, not as the function pointer. Enables
        `r: c.close` to typecheck cleanly without explicit call
        parens when the method has no non-this params."""
        check_ok(
            "myproto: protocol {\n"
            "    tick: function {:this} out i64\n"
            "}\n"
            "mything: record { x: i64 } as {\n"
            "    mp: myproto\n"
            "    tick: function {:this} out i64 is { return this.x }\n"
            "}\n"
            "use_proto: function {p: myproto} is {\n"
            "    n: p.tick\n"
            '    print "\\{n}"\n'
            "}\n"
            "main: function is {\n"
            "    t: mything x: 42\n"
            "    use_proto p: t.mp\n"
            "}"
        )

    def test_union_payload_method_call(self):
        """Method calls on a narrowed union subject dispatch through the
        payload type: inside `case ok then` the narrowed `r` IS the
        File, so `r.close` dispatches directly. The inner match on the
        fresh `cr` value uses the standard (non-narrowed) ok/err Path."""
        check_ok(
            "main: function is {\n"
            '    r: io.open path: "/tmp/x" mode: openmode.write\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        cr: r.close\n"
            "        match (\n"
            "            cr\n"
            "        ) case ok then {\n"
            '            print "ok"\n'
            "        } case err then {\n"
            '            print "err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )


class TestBytesAndPathTypedefs:
    """I/O Phase 1: Bytes / ByteView / Path / PathView typedefs.

    Typedef wrappers over `List of: u8` / `ListView of: u8` / `String` /
    `StringView` so that signatures read for what they mean. Backward-
    compatible: List / String operations on Bytes / Path work via the
    typedef rule.
    """

    def test_bytes_can_be_declared(self):
        check_ok("main: function is {\n    b: Bytes\n}")

    def test_bytes_inherits_list_methods(self):
        """append, length etc. work because Bytes typedefs over List of: u8.

        The monomorphized List of: u8 synthesizes its `append` with
        `from:` as the parameter name (see _monomorphize in
        ztypecheck), so callers use `from:` on Bytes too.
        """
        check_ok(
            "main: function is {\n"
            "    b: Bytes\n"
            "    b.append from: 65.u8\n"
            "    n: b.length\n"
            "}"
        )

    def test_path_can_be_declared(self):
        check_ok('main: function is {\n    p: Path.create from: "hello.txt".string\n}')

    def test_path_inherits_string_methods(self):
        """length etc. work because Path typedefs over String."""
        check_ok(
            "main: function is {\n"
            '    p: Path.create from: "hello.txt".string\n'
            "    n: p.length\n"
            "}"
        )

    def test_byteview_can_be_obtained_from_bytes(self):
        """ByteView borrowed from a Bytes value via the inherited ListView method."""
        check_ok(
            "main: function is {\n"
            "    b: Bytes\n"
            "    b.append from: 65.u8\n"
            "    v: b.listview\n"
            "}"
        )


class TestGenerics:
    """Tests for generic type resolution and monomorphization."""

    def test_generic_record_resolution(self):
        """Record with t: Any.generic puts t in generic_params, not children."""
        program, typing = check_ok(
            "myrec: record { x: i64 } as { t: Any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric is True
        assert "t" in myrec.generic_params
        assert not tc.typing.has_child(myrec, "t")
        assert tc.typing.has_child(myrec, "x")

    def test_generic_record_with_generic_field_ref(self):
        """Record field referencing generic param: x: t resolves to GENERIC_PARAM."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric
        assert tc.typing.has_child(myrec, "x")
        assert tc.typing.child_of(myrec, "x").typetype == ZTypeType.GENERIC_PARAM

    def test_generic_union_resolution(self):
        """Union with t: Any.generic detects generic params correctly."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myopt = tc._resolved.get("test.myopt")
        assert myopt is not None
        assert myopt.isgeneric is True
        assert "t" in myopt.generic_params
        assert tc.typing.has_child(myopt, "some")
        assert tc.typing.has_child(myopt, "none")
        assert not tc.typing.has_child(myopt, "t")

    def test_generic_union_subtype_is_generic_param_ref(self):
        """Union subtype referencing generic param: some: t is GENERIC_PARAM."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myopt = tc._resolved.get("test.myopt")
        assert myopt is not None
        assert tc.typing.child_of(myopt, "some").typetype == ZTypeType.GENERIC_PARAM

    def test_multiple_generic_params(self):
        """Record with multiple generic params."""
        program, typing = check_ok(
            "mypair: record { x: a\n y: b } as { a: Any.generic\n b: Any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        pair = tc._resolved.get("test.mypair")
        assert pair is not None
        assert pair.isgeneric
        assert "a" in pair.generic_params
        assert "b" in pair.generic_params
        assert tc.typing.has_child(pair, "x")
        assert tc.typing.has_child(pair, "y")

    def test_generic_function_resolution(self):
        """Function with generic param in 'as' clause: t: Any.generic."""
        program, typing = check_ok(
            "myfn: function as { t: Any.generic } in { x: t } out t\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myfn = tc._resolved.get("test.myfn")
        assert myfn is not None
        assert myfn.isgeneric is True
        assert "t" in myfn.generic_params
        assert tc.typing.has_child(myfn, "x")
        assert tc.typing.child_of(myfn, "x").typetype == ZTypeType.GENERIC_PARAM

    def test_generic_function_multiple_params(self):
        """Function with multiple generic params in 'as'."""
        program, typing = check_ok(
            "myfn: function as { t: Any.generic\n u: Any.generic } "
            "in { x: t\n y: u } out t\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myfn = tc._resolved.get("test.myfn")
        assert myfn is not None
        assert myfn.isgeneric is True
        assert "t" in myfn.generic_params
        assert "u" in myfn.generic_params
        assert tc.typing.has_child(myfn, "x")
        assert tc.typing.has_child(myfn, "y")

    def test_generic_function_any_clause_order(self):
        """Function with 'as' after 'out' resolves correctly."""
        program, typing = check_ok(
            "myfn: function in { x: t } out t as { t: Any.generic }\nmain: function is {}"
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
            "myfn: function { t: Any.generic\n x: t } out t\nmain: function is {}"
        )
        assert any(
            "generic parameters must be declared in the 'as' section" in e.msg.lower()
            for e in errors
        )

    def test_method_with_as_error(self):
        """Method (function with 'this' type) cannot have 'as' clause."""
        errors = check_errors(
            "myrec: record { x: i64 } as {\n"
            "  meth: function as { t: Any.generic } in { self: this\n val: t } out t is { val }\n"
            "}\nmain: function is { r: myrec x: 1 }"
        )
        assert any(
            "methods cannot declare generic parameters" in e.msg.lower() for e in errors
        )

    def test_static_function_in_type_as_with_own_as(self):
        """Static function (no 'this') in type's 'as' block can have own 'as'."""
        program, typing = check_ok(
            "myrec: record { x: i64 } as {\n"
            "  helper: function as { t: Any.generic } in { val: t } out i64 is { 0 }\n"
            "}\nmain: function is { r: myrec x: 1 }"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        helper = tc._resolved.get("test.myrec.helper")
        assert helper is not None
        assert helper.isgeneric is True
        assert "t" in helper.generic_params

    # ---- Generic function call tests ----

    def test_generic_function_infer_single_arg(self):
        """Generic function call infers type from single value arg."""
        program, typing = check_ok(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name
        assert mono.generic_origin is not None

    def test_generic_function_infer_multiple_same_param(self):
        """Multiple args of the same generic param must agree."""
        program, typing = check_ok(
            "pair: function as { t: Any.generic } in { a: t\n b: t } out t is { return a }\n"
            "main: function is { x: pair 1 b: 2 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name

    def test_generic_function_conflict_error(self):
        """Same generic param with conflicting value types → error."""
        errors = check_errors(
            "pair: function as { t: Any.generic } in { a: t\n b: t } out t is { return a }\n"
            "main: function is { x: pair 1 b: 3.14 }"
        )
        assert any("conflicting types" in e.msg.lower() for e in errors)

    def test_generic_function_explicit_type_arg(self):
        """Explicit generic arg in function call."""
        program, typing = check_ok(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id t: i64 val: 42 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name

    def test_generic_function_explicit_conflicts_with_inferred(self):
        """Explicit generic arg conflicts with inferred type → error."""
        errors = check_errors(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id t: i32 val: 42 }"
        )
        assert any("conflicting types" in e.msg.lower() for e in errors)

    def test_generic_function_multiple_params_inferred(self):
        """Multiple generic params, both inferred from args."""
        program, typing = check_ok(
            "pick: function as { a: Any.generic\n b: Any.generic }\n"
            "  in { x: a\n y: b } out b is { return y }\n"
            'main: function is { r: pick 42 y: "hello" }'
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name
        assert "String" in mono.name

    def test_generic_function_constraint_violation(self):
        """Generic function with valtype constraint rejects reftype arg."""
        errors = check_errors(
            "id: function as { t: Any.valtype } in { val: t } out t is { return val }\n"
            'main: function is { x: id "hello".string }'
        )
        assert any("not a value type" in e.msg.lower() for e in errors)

    def test_generic_function_stringlike_accepts_members(self):
        """Union-as-constraint: StringLike admits both String and StringView."""
        check_ok(
            "id: function as { t: StringLike.generic } in { v: t } out t is "
            "{ return v }\n"
            'main: function is { a: id "hi"\n b: id "hi".string }'
        )

    def test_generic_function_stringlike_rejects_non_member(self):
        """Union-as-constraint: StringLike rejects a non-member type."""
        errors = check_errors(
            "id: function as { t: StringLike.generic } in { v: t } out t is "
            "{ return v }\n"
            "main: function is { x: id 42 }"
        )
        assert any("does not satisfy constraint 'StringLike'" in e.msg for e in errors)

    def test_generic_function_stringlike_accepts_str_via_text(self):
        """StringLike union includes `Text` protocol; str conforms via :Text."""
        check_ok(
            "show: function as { t: StringLike.generic } in { v: t } is "
            "{ print v }\n"
            'main: function is { s: "hi".str to: 16\n show s }'
        )

    def test_generic_function_stringlike_accepts_user_text_conformer(self):
        """Any user type declaring :Text and the Spec method satisfies StringLike.
        `mytext` is a class holding an owned String because views cannot be
        aggregate fields (doc/strings.pdoc); the StringView is produced on
        demand by the protocol method."""
        check_ok(
            "mytext: class { data_: String } as {\n"
            "    :Text\n"
            "    stringview: function {t: this} out StringView is "
            "{ return t.data_.stringview }\n"
            "}\n"
            "show: function as { t: StringLike.generic } in { v: t } is "
            "{ print v }\n"
            'main: function is { m: mytext data_: "hi".string\n show m }'
        )

    def test_generic_function_stringlike_rejects_missing_text_conformance(self):
        """A type that does not declare :Text but has a .stringview method
        is still rejected — conformance is explicit, not structural."""
        errors = check_errors(
            "nottext: record { data_: StringView } as {\n"
            "    stringview: function {t: this} out StringView is { return t.data_ }\n"
            "}\n"
            "show: function as { t: StringLike.generic } in { v: t } is "
            "{ print v }\n"
            'main: function is { n: nottext data_: "hi"\n show n }'
        )
        assert any("does not satisfy constraint 'StringLike'" in e.msg for e in errors)

    def test_generic_function_stringlike_declaration_order_stringview_wins(self):
        """StringLike declares :StringView before :Text; a StringView T
        matches :StringView even if StringView conformed to Text."""
        program, typing = check_ok(
            "show: function as { t: StringLike.generic } in { v: t } is "
            "{ print v }\n"
            'main: function is { show "hi" }'
        )
        # monomorphization for stringview, not text
        sv_monos = [
            m
            for m, _ in typing.mono_functions
            if "StringView" in m.name and m.name.startswith("show_")
        ]
        assert len(sv_monos) == 1

    def test_generic_function_monomorphization_cached(self):
        """Same instantiation produces one cached mono function."""
        program, typing = check_ok(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42\n y: id 99 }"
        )
        i64_monos = [m for m, _ in typing.mono_functions if "i64" in m.name]
        assert len(i64_monos) == 1

    def test_generic_function_different_instantiations(self):
        """Different type args produce different mono functions."""
        program, typing = check_ok(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42\n y: id 3.14 }"
        )
        names = {m.name for m, _ in typing.mono_functions}
        assert any("i64" in n for n in names)
        assert any("f64" in n for n in names)

    def test_generic_function_return_type_resolved(self):
        """Monomorphized function has resolved return type."""
        program, typing = check_ok(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42 }"
        )
        mono, _ = typing.mono_functions[0]
        assert mono.return_type is not None
        assert mono.return_type.name == "i64"

    def test_generic_function_no_inferrable_args_error(self):
        """Generic function call with non-generic args only → cannot infer."""
        errors = check_errors(
            "id: function as { t: Any.generic } in { val: t\n x: i64 } out t\n"
            "  is { return val }\n"
            "main: function is { r: id x: 42 }"
        )
        assert any("cannot infer" in e.msg.lower() for e in errors)

    # ---- Error: compile-time-only instantiation in code ----

    def test_unit_instantiation_in_code_error(self):
        """Unit instantiation inside function body → error."""
        errors = check_errors(
            "mathops: unit as {\n"
            "  t: Any.generic\n"
            "  add: function {a: t b: t} out t is { return a + b }\n"
            "}\n"
            "main: function is { u: (mathops t: i64) }"
        )
        assert any(
            "unit" in e.msg.lower() and "unit level" in e.msg.lower() for e in errors
        )

    def test_generic_function_type_args_only_error(self):
        """Generic function call with only type args (no value args) → missing args."""
        errors = check_errors(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id t: i64 }"
        )
        assert any("missing required" in e.msg.lower() for e in errors)

    # ---- Generic default type tests ----

    def test_generic_function_default_used(self):
        """Default type is used when generic param cannot be inferred."""
        program, typing = check_ok(
            "id: function as { t: (Any.generic default: i64) } in { val: t } out t\n"
            "  is { return val }\n"
            "main: function is { x: id 42 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name

    def test_generic_record_default_used(self):
        """Default type fills in when not inferred for records."""
        program, typing = check_ok(
            "myrec: record { x: t\n y: i64 } as { t: (Any.generic default: i64) }\n"
            "main: function is { r: myrec x: 42 y: 1 }"
        )
        monos = find_user_monos(typing, origin_name="myrec")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name

    def test_generic_default_overridden_by_explicit(self):
        """Explicit generic arg overrides default."""
        program, typing = check_ok(
            "id: function as { t: (Any.generic default: i64) } in { val: t } out t\n"
            "  is { return val }\n"
            "main: function is { x: id t: f64 val: 3.14 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "f64" in mono.name

    def test_generic_default_overridden_by_inference(self):
        """Inferred type takes priority over default."""
        program, typing = check_ok(
            "id: function as { t: (Any.generic default: i32) } in { val: t } out t\n"
            "  is { return val }\n"
            "main: function is { x: id 42 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        # i64 inferred from 42, not default i32
        assert "i64" in mono.name

    # ---- Inline generic args in function calls ----

    def test_inline_generic_and_value_args(self):
        """Generic arg inline with value args in function call."""
        program, typing = check_ok(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id t: i64 val: 42 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name

    def test_inline_multiple_generic_args(self):
        """Multiple generic args inline with value args."""
        program, typing = check_ok(
            "pick: function as { a: Any.generic\n b: Any.generic }\n"
            "  in { x: a\n y: b } out b is { return y }\n"
            "main: function is { r: pick a: i64 b: f64 x: 42 y: 3.14 }"
        )
        assert len(typing.mono_functions) >= 1
        mono, _ = typing.mono_functions[0]
        assert "i64" in mono.name
        assert "f64" in mono.name

    def test_generic_default_stored_on_type(self):
        """Default type is stored in generic_defaults dict."""
        program, typing = check_ok(
            "id: function as { t: (Any.generic default: i64) } in { val: t } out t\n"
            "  is { return val }\n"
            "main: function is { x: id 42 }"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        ftype = tc._resolved.get("test.id")
        assert ftype is not None
        assert "t" in ftype.generic_defaults
        assert ftype.generic_defaults["t"].name == "i64"

    def test_option_some_infers_i64(self):
        """Option.some 42 infers t=i64."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.some 42 }"
        )
        monos = find_user_monos(typing, origin_name="myopt")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name
        assert mono.generic_origin is not None

    def test_option_none_explicit_type_arg(self):
        """Option.none i32 with explicit type argument."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.none i32 }"
        )
        monos = find_user_monos(typing, origin_name="myopt")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i32" in mono.name

    def test_same_generic_different_types(self):
        """Same generic instantiated with different types creates different monomorphizations."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: myopt.some 42\n"
            "    y: myopt.some 3.14\n"
            "}"
        )
        assert len(typing.mono_types) >= 2
        names = {m.name for m, _ in typing.mono_types}
        assert any("i64" in n for n in names)
        assert any("f64" in n for n in names)

    def test_duplicate_instantiation_cached(self):
        """Duplicate instantiation with same type returns cached type."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: myopt.some 1\n"
            "    y: myopt.some 2\n"
            "}"
        )
        # should produce only one monomorphization of `myopt` with t=i64
        myopt_monos = find_user_monos(typing, origin_name="myopt")
        i64_monos = [m for m, _ in myopt_monos if "i64" in m.name]
        assert len(i64_monos) == 1

    def test_system_option_available(self):
        """System optionval type is available via core for valtypes."""
        program, typing = check_ok("main: function is { x: optionval.some 42 }")
        assert len(typing.mono_types) >= 1
        mono, _ = typing.mono_types[0]
        assert mono.generic_origin is not None

    def test_monomorphized_union_has_tag(self):
        """Monomorphized union has a 'tag' child holding the discriminator."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.some 42 }"
        )
        mono, _ = typing.mono_types[0]
        assert typing.has_child(mono, "tag")
        tag_data = typing.child_of(mono, "tag")
        assert tag_data.typetype == ZTypeType.DATA
        assert typing.has_child(tag_data, "some")
        assert typing.has_child(tag_data, "none")

    def test_monomorphized_union_concrete_subtypes(self):
        """Monomorphized union replaces generic param with concrete type."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.some 42 }"
        )
        monos = find_user_monos(typing, origin_name="myopt")
        assert len(monos) >= 1
        mono, _ = monos[0]
        some_type = typing.child_of(mono, "some")
        assert some_type is not None
        assert some_type.name == "i64"
        assert some_type.typetype != ZTypeType.GENERIC_PARAM

    def test_error_generic_union_no_args(self):
        """Using generic union subtype with no inferrable args emits error."""
        errors = check_errors(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.some }"
        )
        assert any("cannot infer type arguments" in e.msg for e in errors)

    def test_error_generic_union_none_no_args(self):
        """Using generic union null subtype with no type arg emits error."""
        errors = check_errors(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.none }"
        )
        assert any("cannot infer type arguments" in e.msg for e in errors)

    def test_generic_union_from_infers_type(self):
        """Option.some from: 42 infers t=i64 via from: syntax."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.some from: 42 }"
        )
        monos = find_user_monos(typing, origin_name="myopt")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name
        assert mono.generic_origin is not None

    def test_generic_union_explicit_type_and_from(self):
        """Option.some t: i64 from: 42 with explicit generic param and from: value."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: myopt.some t: i64 from: 42 }"
        )
        monos = find_user_monos(typing, origin_name="myopt")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name

    def test_generic_union_from_with_different_types(self):
        """from: syntax with different types creates different monomorphizations."""
        program, typing = check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: myopt.some from: 42\n"
            "    y: myopt.some from: 3.14\n"
            "}"
        )
        assert len(typing.mono_types) >= 2
        names = {m.name for m, _ in typing.mono_types}
        assert any("i64" in n for n in names)
        assert any("f64" in n for n in names)

    def test_system_option_from_syntax(self):
        """System optionval type works with from: syntax."""
        program, typing = check_ok("main: function is { x: optionval.some from: 42 }")
        assert len(typing.mono_types) >= 1
        mono, _ = typing.mono_types[0]
        assert mono.generic_origin is not None

    def test_option_requires_reftype(self):
        """Option.some with valtype should error (requires Any.reftype)."""
        errors = check_errors("main: function is { x: Option.some 42 }")
        assert any("not a reference type" in e.msg for e in errors)

    def test_optionval_requires_valtype(self):
        """optionval with reftype should error (requires Any.valtype)."""
        errors = check_errors('main: function is { x: optionval.some "hello".string }')
        assert any("not a value type" in e.msg for e in errors)

    def test_optionval_some_infers_i64(self):
        """optionval.some 42 infers t=i64."""
        program, typing = check_ok("main: function is { x: optionval.some 42 }")
        i64_monos = [
            (m, d)
            for m, d in find_user_monos(typing, origin_name="optionval")
            if "i64" in m.name
        ]
        assert len(i64_monos) >= 1
        mono, _ = i64_monos[0]
        assert mono.typetype == ZTypeType.VARIANT

    def test_optionval_none_explicit_type(self):
        """optionval.none i32 with explicit type argument."""
        program, typing = check_ok("main: function is { x: optionval.none i32 }")
        i32_monos = [
            (m, d)
            for m, d in find_user_monos(typing, origin_name="optionval")
            if "i32" in m.name
        ]
        assert len(i32_monos) >= 1

    def test_optionval_is_valtype(self):
        """Monomorphized optionval is a value type."""
        program, typing = check_ok("main: function is { x: optionval.some 42 }")
        mono, _ = typing.mono_types[0]
        assert mono.is_valtype is True

    def test_option_nullable_ptr_flag(self):
        """Monomorphized Option(stack-struct) does NOT use nullable-ptr."""
        program, typing = check_ok(
            'main: function is { x: Option.some "hello".string }'
        )
        mono, _ = typing.mono_types[0]
        assert mono.is_nullable_ptr is False

    def test_box_valtype_creates_reftype(self):
        """Box from: valtype creates a Box reftype."""
        program, typing = check_ok("main: function is { b: Box from: 42 }")
        box_monos = [(m, d) for m, d in typing.mono_types if m.is_box]
        assert len(box_monos) >= 1
        mono, _ = box_monos[0]
        assert mono.is_box is True
        assert mono.is_valtype is False

    def test_box_string_creates_box_mono(self):
        """Box from: String creates a Box monomorphized type (strings are stack now)."""
        program, typing = check_ok('main: function is { b: Box from: "hello".string }')
        # string is stack-allocated now; box creates a real box mono
        box_monos = [m for m, _ in typing.mono_types if m.is_box]
        assert len(box_monos) >= 1

    def test_box_valtype_has_inner_children(self):
        """Box(valtype) has children copied from inner type for transparent access."""
        program, typing = check_ok("main: function is { b: Box from: 42 }")
        box_monos = [(m, d) for m, d in typing.mono_types if m.is_box]
        assert len(box_monos) >= 1
        mono, _ = box_monos[0]
        assert mono.is_box is True
        # should have i64's operator children
        assert typing.has_child(mono, "+") or typing.child_count(mono) > 0

    def test_error_generic_record_no_args(self):
        """Using generic record with no args emits error."""
        errors = check_errors(
            "myrec: record { x: t } as { t: Any.generic }\nmain: function is { r: myrec }"
        )
        assert any("cannot infer type arguments" in e.msg for e in errors)

    def test_error_generic_record_no_inferrable_args(self):
        """Using generic record with args that don't cover generic params emits error."""
        errors = check_errors(
            "myrec: record { x: t\n y: i64 } as { t: Any.generic }\n"
            "main: function is { r: myrec y: 42 }"
        )
        assert any("cannot infer" in e.msg for e in errors)

    def test_generic_record_infer_from_value(self):
        """myrec x: 42 infers t=i64 from field type."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.generic }\n"
            "main: function is { r: myrec x: 42 }"
        )
        monos = find_user_monos(typing, origin_name="myrec")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name
        assert typing.child_of(mono, "x").name == "i64"

    def test_generic_record_explicit_and_value(self):
        """myrec t: i64 x: 42 — both explicit type arg and value, compatible."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.generic }\n"
            "main: function is { r: myrec t: i64 x: 42 }"
        )
        monos = find_user_monos(typing, origin_name="myrec")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name

    def test_generic_record_conflict_error(self):
        """myrec t: i32 x: "hello" — conflicting types for t emits error."""
        errors = check_errors(
            "myrec: record { x: t } as { t: Any.generic }\n"
            'main: function is { r: myrec t: i32 x: "hello" }'
        )
        assert any("Conflicting types" in e.msg for e in errors)

    def test_generic_record_multi_param_infer(self):
        """pair x: 42 y: "hi".string infers a=i64, b=String."""
        program, typing = check_ok(
            "mypair: record { x: a\n y: b } as { a: Any.generic\n b: Any.generic }\n"
            'main: function is { p: mypair x: 42 y: "hi".string }'
        )
        # Find the user mypair mono; stdlib method signatures may
        # contribute additional monomorphizations (e.g. optionval(u64)
        # from stringview.index_of) that land ahead of it.
        mono = next(m for m, _ in typing.mono_types if m.name.startswith("mypair_"))
        assert typing.child_of(mono, "x").name == "i64"
        assert typing.child_of(mono, "y").name == "String"

    def test_generic_type_in_type_position_concrete(self):
        """(myrec t: i64) in field type position produces concrete monomorphization."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.generic }\n"
            "wrapper: record { inner: (myrec t: i64) }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        wrapper = tc._resolved.get("test.wrapper")
        assert wrapper is not None
        inner = tc.typing.child_of(wrapper, "inner")
        assert inner is not None
        assert inner.name == "myrec_i64"
        assert inner.isgeneric is False
        assert tc.typing.child_of(inner, "x").name == "i64"

    def test_generic_type_in_type_position_partial(self):
        """(myrec t: u) in field type position produces partial instantiation."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.generic }\n"
            "wrapper: record { inner: (myrec t: u) } as { u: Any.generic }\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        wrapper = tc._resolved.get("test.wrapper")
        assert wrapper is not None
        inner = tc.typing.child_of(wrapper, "inner")
        assert inner is not None
        assert inner.name == "myrec_u"
        assert inner.isgeneric is True
        assert "u" in inner.generic_params

    def test_partial_instantiation_full_monomorphize(self):
        """Wrapper with (myrec t: u) fully resolves inner when monomorphized."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.generic }\n"
            "wrapper: record { inner: (myrec t: u) } as { u: Any.generic }\n"
            "main: function is { w: wrapper u: i64 inner: (myrec x: 42) }"
        )
        monos = {m.name: m for m, _ in typing.mono_types}
        assert "wrapper_i64" in monos
        wrapper_mono = monos["wrapper_i64"]
        inner = typing.child_of(wrapper_mono, "inner")
        assert inner is not None
        assert inner.name == "myrec_i64"
        assert inner.isgeneric is False

    def test_error_bare_generic_in_type_position(self):
        """Using bare generic type in field position emits error."""
        errors = check_errors(
            "myrec: record { x: t } as { t: Any.generic }\n"
            "wrapper: record { inner: myrec }\n"
            "main: function is { w: wrapper inner: (myrec x: 42) }"
        )
        assert any("requires type arguments" in e.msg for e in errors)

    def test_error_missing_type_arg_in_type_position(self):
        """Missing type arg in (myrec) call emits error."""
        errors = check_errors(
            "mypair: record { x: a\n y: b } as { a: Any.generic\n b: Any.generic }\n"
            "wrapper: record { inner: (mypair a: i64) }\n"
            'main: function is { w: wrapper inner: (mypair x: 1 y: "a") }'
        )
        assert any("Missing type argument" in e.msg for e in errors)

    def test_generic_param_in_is_error(self):
        """Generic params in is-section should error for record/union/class."""
        errors = check_errors(
            "myrec: record { t: Any.generic\n x: t }\n"
            "main: function is { r: myrec x: 42 }"
        )
        assert any(
            "generic parameters must be declared in the 'as' section" in e.msg.lower()
            for e in errors
        )

    # ---- Generic Classes ----

    def test_generic_class_resolution(self):
        """Class with t: Any.generic puts t in generic_params, not children."""
        program, typing = check_ok(
            "mycls: class { x: i64 } as { t: Any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        mycls = tc._resolved.get("test.mycls")
        assert mycls is not None
        assert mycls.isgeneric is True
        assert "t" in mycls.generic_params
        assert not tc.typing.has_child(mycls, "t")
        assert tc.typing.has_child(mycls, "x")

    def test_generic_class_field_uses_param(self):
        """Class field referencing generic param: val: t resolves to GENERIC_PARAM."""
        program, typing = check_ok(
            "mycls: class { val: t } as { t: Any.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        mycls = tc._resolved.get("test.mycls")
        assert mycls is not None
        assert mycls.isgeneric
        assert tc.typing.has_child(mycls, "val")
        assert tc.typing.child_of(mycls, "val").typetype == ZTypeType.GENERIC_PARAM

    def test_generic_class_construction_infers(self):
        """mycls val: 42 infers t=i64 and produces monomorphized type."""
        program, typing = check_ok(
            "mycls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: mycls val: 42 }"
        )
        monos = find_user_monos(typing, origin_name="mycls")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name
        assert mono.typetype == ZTypeType.CLASS
        assert mono.isgeneric is False
        assert typing.has_child(mono, "val")
        assert typing.child_of(mono, "val").name == "i64"

    def test_generic_class_explicit_type_arg(self):
        """mycls t: i64 val: 42 with explicit type arg."""
        program, typing = check_ok(
            "mycls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: mycls t: i64 val: 42 }"
        )
        monos = find_user_monos(typing, origin_name="mycls")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert "i64" in mono.name
        assert mono.typetype == ZTypeType.CLASS

    def test_generic_class_is_reftype(self):
        """Monomorphized generic class is still a reference type."""
        program, typing = check_ok(
            "mycls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: mycls val: 42 }"
        )
        monos = find_user_monos(typing, origin_name="mycls")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert mono.is_valtype is False

    def test_generic_class_has_create(self):
        """Monomorphized class has :meta.create constructor."""
        program, typing = check_ok(
            "mycls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: mycls val: 42 }"
        )
        monos = find_user_monos(typing, origin_name="mycls")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert mono.meta_create is not None
        assert mono.meta_create.typetype == ZTypeType.FUNCTION

    def test_error_generic_class_no_args(self):
        """Bare generic class name in expression is an error."""
        errors = check_errors(
            "mycls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: mycls }"
        )
        assert any("generic" in e.msg.lower() for e in errors)

    # ---- Generic Protocols ----

    def test_generic_protocol_resolution(self):
        """Protocol with t: Any.generic param is generic."""
        program, typing = check_ok(
            "myproto: protocol {\n"
            "  t: Any.generic\n"
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
        program, typing = check_ok(
            "myproto: protocol {\n"
            "  t: Any.generic\n"
            "  get: function {:this} out t\n"
            "}\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myproto = tc._resolved.get("test.myproto")
        assert myproto is not None
        get_fn = tc.typing.child_of(myproto, "get")
        assert get_fn is not None
        ret = get_fn.return_type
        assert ret is not None
        assert ret.typetype == ZTypeType.GENERIC_PARAM

    def test_error_generic_protocol_no_args(self):
        """Bare generic protocol name in expression is an error."""
        errors = check_errors(
            "myproto: protocol {\n"
            "  t: Any.generic\n"
            "  get: function {:this} out t\n"
            "}\n"
            "myrec: record { x: i64 } as { p: myproto }\n"
            "main: function is { r: myrec x: 1\n v: r.p }"
        )
        assert any("generic" in e.msg.lower() for e in errors)

    # ---- any.valtype / any.reftype constraint subtypes ----

    def test_valtype_constraint_record_ok(self):
        """Record with t: Any.valtype accepts record types."""
        check_ok(
            "myrec: record { x: t } as { t: Any.valtype }\n"
            "inner: record { v: i64 }\n"
            "main: function is { r: myrec x: (inner v: 1) }"
        )

    def test_valtype_constraint_i64_ok(self):
        """Record with t: Any.valtype accepts numeric types."""
        check_ok(
            "myrec: record { x: t } as { t: Any.valtype }\n"
            "main: function is { r: myrec x: 42 }"
        )

    def test_valtype_constraint_class_error(self):
        """Record with t: Any.valtype rejects class types."""
        errors = check_errors(
            "mycls: class { v: i64 }\n"
            "myrec: record { x: t } as { t: Any.valtype }\n"
            "main: function is {\n"
            "    c: mycls v: 1\n"
            "    r: myrec x: c\n"
            "}"
        )
        assert any("not a value type" in e.msg for e in errors)

    def test_valtype_constraint_union_error(self):
        """Record with t: Any.valtype rejects union types."""
        errors = check_errors(
            "myunion: union { a: i64\n b: null }\n"
            "myrec: record { x: t } as { t: Any.valtype }\n"
            "main: function is {\n"
            "    u: myunion.a 1\n"
            "    r: myrec x: u\n"
            "}"
        )
        assert any("not a value type" in e.msg for e in errors)

    def test_reftype_constraint_class_ok(self):
        """Record with t: Any.reftype accepts class types."""
        check_ok(
            "mycls: class { v: i64 }\n"
            "myrec: record { x: t } as { t: Any.reftype }\n"
            "main: function is {\n"
            "    c: mycls v: 1\n"
            "    r: myrec x: c\n"
            "}"
        )

    def test_reftype_constraint_union_ok(self):
        """Record with t: Any.reftype accepts union types."""
        check_ok(
            "myunion: union { a: i64\n b: null }\n"
            "myrec: record { x: t } as { t: Any.reftype }\n"
            "main: function is {\n"
            "    u: myunion.a 1\n"
            "    r: myrec x: u\n"
            "}"
        )

    def test_reftype_constraint_record_error(self):
        """Record with t: Any.reftype rejects record types."""
        errors = check_errors(
            "inner: record { v: i64 }\n"
            "myrec: record { x: t } as { t: Any.reftype }\n"
            "main: function is { r: myrec x: (inner v: 1) }"
        )
        assert any("not a reference type" in e.msg for e in errors)

    def test_reftype_constraint_i64_error(self):
        """Record with t: Any.reftype rejects numeric types."""
        errors = check_errors(
            "myrec: record { x: t } as { t: Any.reftype }\n"
            "main: function is { r: myrec x: 42 }"
        )
        assert any("not a reference type" in e.msg for e in errors)

    def test_valtype_constraint_in_generic_params(self):
        """Any.valtype constraint stored correctly in generic_params."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.valtype }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric
        assert "t" in myrec.generic_params
        assert myrec.generic_params["t"].name == "Any.valtype"

    def test_reftype_constraint_in_generic_params(self):
        """Any.reftype constraint stored correctly in generic_params."""
        program, typing = check_ok(
            "myrec: record { x: t } as { t: Any.reftype }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.isgeneric
        assert "t" in myrec.generic_params
        assert myrec.generic_params["t"].name == "Any.reftype"

    def test_valtype_union_subtype_ok(self):
        """Union with t: Any.valtype accepts valtypes for subtype construction."""
        check_ok(
            "myopt: union { some: t\n none: null } as { t: Any.valtype }\n"
            "main: function is { x: myopt.some 42 }"
        )

    def test_valtype_union_subtype_class_error(self):
        """Union with t: Any.valtype rejects class type in subtype construction."""
        errors = check_errors(
            "mycls: class { v: i64 }\n"
            "myopt: union { some: t\n none: null } as { t: Any.valtype }\n"
            "main: function is {\n"
            "    c: mycls v: 1\n"
            "    x: myopt.some c\n"
            "}"
        )
        assert any("not a value type" in e.msg for e in errors)


class TestTypedefs:
    def test_record_typedef_resolves(self):
        """Record typedef with .typedef resolves and sets typedef_base."""
        program, typing = check_ok(
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
        program, typing = check_ok(
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
        assert tc.typing.has_child(mt, "double")

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
        """create/borrow are synthesized for typedefs. `.take` is not."""
        program, typing = check_ok(
            "meters: record { val: i64.typedef } as {}\n"
            "main: function is { m: meters.create from: 42 }"
        )
        tc = TypeChecker(program)
        tc.check()
        mt = tc._resolved.get("test.meters")
        assert mt is not None
        assert tc.typing.has_child(mt, "create")
        assert tc.typing.has_child(mt, "borrow")
        assert not tc.typing.has_child(mt, "take")

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
        """Facet definition creates FACET ZType with Spec children."""
        program, typing = check_ok(
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
        assert tc.typing.has_child(t, "show")
        assert tc.typing.child_of(t, "show").typetype == ZTypeType.FUNCTION

    def test_facet_has_constructors(self):
        """Non-generic facets get create/borrow constructors. `.take` is not
        registered — it was an alias for create and has been removed."""
        program, typing = check_ok(
            "showable: facet {\n"
            "    show: function {:this} out i64\n"
            "}\n"
            "main: function is {}"
        )
        tc = TypeChecker(program)
        tc.check()
        t = tc._resolve_unit_name("test", "showable")
        assert tc.typing.has_child(t, "create")
        assert tc.typing.has_child(t, "borrow")
        assert not tc.typing.has_child(t, "take")

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
        """Record missing a Spec method errors."""
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
        program, typing = check_ok(
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
        program, typing = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\nmain: function is {}"
        )
        tc = TypeChecker(program)
        tc.check(full=True)
        myrec = tc._resolved.get("test.myrec")
        assert myrec is not None
        assert myrec.generic_params["size"].name == "u64"

    def test_numeric_generic_monomorphization(self):
        """(myrec size: 10) creates myrec_10 with u64 field."""
        program, typing = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is { a: (myrec size: 10) x: 5 }"
        )
        monos = find_user_monos(typing, origin_name="myrec")
        assert len(monos) >= 1
        mono, _ = monos[0]
        assert mono.name == "myrec_10"
        assert mono.generic_origin is not None
        # auto-synthesized field
        assert typing.has_child(mono, "size")
        assert typing.child_of(mono, "size").name == "u64"
        assert typing.child_default(mono, "size") == "10"

    def test_numeric_generic_range_check(self):
        """Value 300 for u8 constraint produces error."""
        errors = check_errors(
            "myrec: record { x: i64 } as { size: u8.generic }\n"
            "main: function is { a: (myrec size: 300) x: 5 }"
        )
        assert any("out of range" in e.msg for e in errors)

    def test_mixed_type_and_numeric_generics(self):
        """(myarray t: i64 size: 10) creates myarray_i64_10."""
        program, typing = check_ok(
            "myarray: record { payload: t } as { t: Any.generic\n size: u64.generic }\n"
            "main: function is { a: (myarray t: i64 size: 10) payload: 42 }"
        )
        monos = [m for m, _ in typing.mono_types if m.name == "myarray_i64_10"]
        assert len(monos) == 1
        mono = monos[0]
        assert typing.has_child(mono, "payload")
        assert typing.child_of(mono, "payload").name == "i64"
        assert typing.has_child(mono, "size")
        assert typing.child_of(mono, "size").name == "u64"
        assert typing.child_default(mono, "size") == "10"

    def test_numeric_generic_different_values(self):
        """size 10 vs 20 produce different types."""
        program, typing = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is {\n"
            "    a: (myrec size: 10) x: 1\n"
            "    b: (myrec size: 20) x: 2\n"
            "}"
        )
        names = [m.name for m, _ in typing.mono_types]
        assert "myrec_10" in names
        assert "myrec_20" in names

    def test_numeric_generic_same_value_cached(self):
        """Same value produces same type (cache hit)."""
        program, typing = check_ok(
            "myrec: record { x: i64 } as { size: u64.generic }\n"
            "main: function is {\n"
            "    a: (myrec size: 10) x: 1\n"
            "    b: (myrec size: 10) x: 2\n"
            "}"
        )
        mono_names = [
            m.name for m, _ in typing.mono_types if m.name.startswith("myrec_")
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
        program, typing = check_ok(
            "myrec: record { x: i64 } as { off: i32.generic }\n"
            "main: function is { a: (myrec off: -5) x: 1 }"
        )
        monos = [m for m, _ in typing.mono_types if m.name == "myrec_neg5"]
        assert len(monos) == 1
        mono = monos[0]
        assert typing.child_default(mono, "off") == "-5"

    def test_numeric_generic_auto_field(self):
        """Numeric param auto-creates field when not referenced by Any child."""
        program, typing = check_ok(
            "myrec: record { x: i64 } as { n: u32.generic }\n"
            "main: function is { a: (myrec n: 42) x: 1 }"
        )
        monos = [m for m, _ in typing.mono_types if m.name == "myrec_42"]
        assert len(monos) == 1
        mono = monos[0]
        assert typing.has_child(mono, "n")
        assert typing.child_of(mono, "n").name == "u32"
        assert typing.child_default(mono, "n") == "42"


class TestArrays:
    """Tests for array type resolution and monomorphization."""

    def test_array_creation(self):
        """array of: i64 to: 4 creates a monomorphized array type."""
        program, typing = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in typing.mono_types if "array" in m.name]
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
        program, typing = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in typing.mono_types if m.name == "array_i64_4"]
        assert len(monos) == 1
        mono = monos[0]
        assert typing.has_child(mono, "get")
        get = typing.child_of(mono, "get")
        assert get.typetype == ZTypeType.FUNCTION
        ret = get.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_array_set_method(self):
        """.set method is synthesized and returns element type."""
        program, typing = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in typing.mono_types if m.name == "array_i64_4"]
        assert len(monos) == 1
        mono = monos[0]
        assert typing.has_child(mono, "set")
        set_ = typing.child_of(mono, "set")
        assert set_.typetype == ZTypeType.FUNCTION
        ret = set_.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_array_length_field(self):
        """.length is synthesized with correct default value."""
        program, typing = check_ok("main: function is { a: (array of: i64 to: 4) }")
        monos = [m for m, _ in typing.mono_types if m.name == "array_i64_4"]
        assert len(monos) == 1
        mono = monos[0]
        assert typing.has_child(mono, "length")
        assert typing.child_default(mono, "length") == "4"

    def test_array_different_lengths_different_types(self):
        """array of: i64 to: 4 and array of: i64 to: 8 are different types."""
        program, typing = check_ok(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    b: (array of: i64 to: 8)\n"
            "}"
        )
        names = [m.name for m, _ in typing.mono_types if "array" in m.name]
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
        program, typing = check_ok(
            "primes: data { 2 3 5 7 11 }\nmain: function is { a: primes.array }"
        )
        monos = [m for m, _ in typing.mono_types if "array" in m.name]
        assert len(monos) >= 1
        mono = monos[0]
        assert "i64" in mono.name
        assert "5" in mono.name


class TestStr:
    """Tests for str type resolution and monomorphization."""

    def test_str_creation(self):
        """str to: 32 creates a monomorphized str type."""
        program, typing = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in typing.mono_types if m.name.startswith("str_")]
        assert len(monos) >= 1
        mono = monos[0]
        assert mono.name == "str_32"

    def test_str_is_valtype(self):
        """str is a value type."""
        program, typing = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in typing.mono_types if m.name == "str_32"]
        assert len(monos) == 1
        assert monos[0].is_valtype is True

    def test_str_length_field(self):
        """.length is synthesized as u64 field."""
        program, typing = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in typing.mono_types if m.name == "str_32"]
        mono = monos[0]
        assert typing.has_child(mono, "length")
        assert typing.child_of(mono, "length").name == "u64"

    def test_str_size_field(self):
        """.size is synthesized with correct default value."""
        program, typing = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in typing.mono_types if m.name == "str_32"]
        mono = monos[0]
        assert typing.has_child(mono, "size")
        assert typing.child_default(mono, "size") == "32"

    def test_str_string_method(self):
        """.string method is synthesized returning String type."""
        program, typing = check_ok("main: function is { s: (str to: 32) }")
        monos = [m for m, _ in typing.mono_types if m.name == "str_32"]
        mono = monos[0]
        assert typing.has_child(mono, "string")
        string_method = typing.child_of(mono, "string")
        assert string_method.typetype == ZTypeType.FUNCTION
        ret = string_method.return_type
        assert ret is not None
        assert ret.name == "String"

    def test_str_different_capacities_different_types(self):
        """str to: 16 and str to: 32 are different types."""
        program, typing = check_ok(
            "main: function is {\n    a: (str to: 16)\n    b: (str to: 32)\n}"
        )
        names = [m.name for m, _ in typing.mono_types if "str" in m.name]
        assert "str_16" in names
        assert "str_32" in names

    def test_str_from_string_literal(self):
        """str via .str method on String literal."""
        check_ok('main: function is { s: "hello".str to: 32 }')

    def test_str_from_string_variable(self):
        """str via .str method on String variable."""
        check_ok('main: function is {\n    msg: "hello"\n    s: msg.str to: 32\n}')

    def test_str_in_record(self):
        """str can be used as a record field (valtype)."""
        check_ok(
            "entry: record { name: (str to: 16)\n age: 0 }\n"
            'main: function is { e: entry name: ("".str to: 16) }'
        )

    def test_string_str_method_resolves(self):
        """String.str to: N resolves to str_N type."""
        program, typing = check_ok('main: function is { s: "hello".str to: 32 }')
        monos = [m for m, _ in typing.mono_types if m.name == "str_32"]
        assert len(monos) >= 1

    def test_str_str_method_resolves(self):
        """str.str to: N resolves to different str type."""
        program, typing = check_ok(
            'main: function is {\n    a: "hi".str to: 16\n    b: a.str to: 32\n}'
        )
        names = [m.name for m, _ in typing.mono_types]
        assert "str_16" in names
        assert "str_32" in names

    def test_str_str_narrowing_resolves(self):
        """str.str to: smaller capacity type-checks ok."""
        check_ok(
            'main: function is {\n    a: "hello".str to: 32\n    b: a.str to: 4\n}'
        )

    def test_str_constructor_no_args(self):
        """str to: N with no arguments creates empty str."""
        check_ok("main: function is { s: str to: 32 }")


class TestStrStringview:
    """StringView created from a str valtype.

    str.stringview installs a borrow-scoped Path lock on the source: SHARED
    on each intermediate prefix and EXCLUSIVE on the leaf Path. Sibling
    paths remain accessible; reads / writes that overlap with the locked
    leaf (the leaf itself, Any descendant, or Any prefix) are rejected.
    """

    def test_returns_stringview(self):
        program, typing = check_ok(
            'main: function is {\n  s: "hi".str to: 32\n  v: s.stringview\n}'
        )
        # the view local should have stringview type
        assert program is not None

    def test_blocks_take(self):
        errors = check_errors(
            "main: function is {\n"
            '  s: "hi".str to: 32\n'
            "  v: s.stringview\n"
            "  t: s.take\n"
            "}"
        )
        assert any("lock" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_blocks_reassignment(self):
        errors = check_errors(
            "main: function is {\n"
            '  s: "hi".str to: 32\n'
            "  v: s.stringview\n"
            '  s = "x".str to: 32\n'
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_blocks_direct_source_read(self):
        """While viewed, the str source is off-limits — only the view reads it."""
        errors = check_errors(
            'main: function is {\n  s: "hi".str to: 32\n  v: s.stringview\n  print s\n}'
        )
        assert any("cannot access" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_blocks_direct_length_read(self):
        """Method call on the locked source is rejected."""
        errors = check_errors(
            "main: function is {\n"
            '  s: "hi".str to: 32\n'
            "  v: s.stringview\n"
            "  n: s.length\n"
            "}"
        )
        assert any("cannot access" in e.msg.lower() and "'s'" in e.msg for e in errors)

    def test_permits_sibling_field_read(self):
        """View of `e.name` locks the leaf `(e, name)` — reading `e.age`
        (a sibling) is permitted under the Path-scoped lock model."""
        check_ok(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '  e: entry name: ("a".str to: 16) age: 1\n'
            "  v: e.name.stringview\n"
            "  x: e.age\n"
            "}"
        )

    def test_blocks_read_of_parent_record(self):
        """Reading the parent record `e` while `e.name` is locked errors —
        a whole-record read would expose the locked leaf."""
        errors = check_errors(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '  e: entry name: ("a".str to: 16) age: 1\n'
            "  v: e.name.stringview\n"
            "  print e\n"
            "}"
        )
        assert any("cannot access" in e.msg.lower() and "'e'" in e.msg for e in errors)

    def test_blocks_leaf_read_outside_view(self):
        """View of e.name locks root — direct read of e.name is also blocked."""
        errors = check_errors(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '  e: entry name: ("a".str to: 16) age: 1\n'
            "  v: e.name.stringview\n"
            "  m: e.name.length\n"
            "}"
        )
        assert any("cannot access" in e.msg.lower() and "'e'" in e.msg for e in errors)

    def test_permits_sibling_field_reassign(self):
        """Sibling-field reassignment is permitted while another field is
        viewed (Path-scoped lock model)."""
        check_ok(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '  e: entry name: ("a".str to: 16) age: 1\n'
            "  v: e.name.stringview\n"
            "  e.age = 2\n"
            "}"
        )

    def test_reads_via_view_permitted(self):
        check_ok(
            "main: function is {\n"
            '  s: "hi".str to: 32\n'
            "  v: s.stringview\n"
            "  print v\n"
            '  print "\\{v.length}"\n'
            "}"
        )

    def test_substring_bounds_form(self):
        check_ok(
            "main: function is {\n"
            '  s: "hello".str to: 32\n'
            "  v: s.stringview from: 0 to: 3\n"
            "  print v\n"
            "}"
        )

    def test_release_after_scope_exit(self):
        """Inner block releases the view, source becomes accessible again."""
        check_ok(
            "main: function is {\n"
            '  s: "hi".str to: 32\n'
            "  { v: s.stringview\n    print v\n  }\n"
            "  print s.stringview\n"
            "}"
        )

    def test_rejects_temporary(self):
        errors = check_errors('main: function is { v: ("hi".str to: 8).stringview }')
        assert any("temporary" in e.msg.lower() for e in errors)

    def test_rejects_return_of_local_view(self):
        """Cannot return a view of a local str — escape analysis blocks it.

        There is no way to make a str.stringview escape because str is a
        valtype (stack-local) and .lock cannot be applied to valtype
        parameters — so a borrowed StringView return can never be rooted at
        a surviving source. The function definition itself is rejected for
        declaring borrow-return with no .lock parameter, and returning the
        local view also errors.
        """
        errors = check_errors(
            "f: function out StringView.borrow is {\n"
            '  s: "hi".str to: 32\n'
            "  return s.stringview\n"
            "}\n"
            "main: function is { v: f }"
        )
        assert any("cannot return" in e.msg.lower() for e in errors)

    def test_lock_on_str_param_is_rejected(self):
        """str is a valtype — .lock cannot be applied to valtype parameters.

        This captures the reason str.stringview cannot escape a function:
        there is no .lock-able source to anchor the borrow to.
        """
        errors = check_errors(
            "f: function {s: (str to: 32).lock} out StringView.borrow is {\n"
            "  return s.stringview\n"
            "}\n"
            "main: function is {}"
        )
        assert any(
            ".lock" in e.msg.lower() and "valtype" in e.msg.lower() for e in errors
        )


class TestList:
    """Tests for List type resolution and monomorphization."""

    def test_list_creation(self):
        """List of: i64 creates a monomorphized List type."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        # look specifically for list_i64 (not listview_i64, which is also mono'd)
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        assert len(monos) == 1

    def test_list_creation_with_capacity(self):
        """List of: i64 with capacity argument type-checks."""
        check_ok("main: function is { l: (List of: i64) capacity: 10.u64 }")

    def test_list_is_reftype(self):
        """List is a reference type (not valtype)."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        assert len(monos) == 1
        assert monos[0].is_valtype is False

    def test_list_length_field(self):
        """.length is synthesized as u64 field."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "length")
        assert typing.child_of(mono, "length").name == "u64"

    def test_list_capacity_field(self):
        """.capacity is synthesized as u64 field."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "capacity")
        assert typing.child_of(mono, "capacity").name == "u64"

    def test_list_append_method(self):
        """.append is synthesized with from: parameter."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "append")
        append = typing.child_of(mono, "append")
        assert append.typetype == ZTypeType.FUNCTION
        assert typing.has_child(append, "from")

    def test_list_get_method(self):
        """.get is synthesized returning element type."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "get")
        get = typing.child_of(mono, "get")
        assert get.typetype == ZTypeType.FUNCTION
        assert typing.has_child(get, "i")
        ret = get.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_list_set_method(self):
        """.set is synthesized returning element type."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "set")
        set_m = typing.child_of(mono, "set")
        assert set_m.typetype == ZTypeType.FUNCTION
        assert typing.has_child(set_m, "i")
        assert typing.has_child(set_m, "val")

    def test_list_pop_method(self):
        """.pop is synthesized returning element type."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "pop")
        pop = typing.child_of(mono, "pop")
        assert pop.typetype == ZTypeType.FUNCTION
        ret = pop.return_type
        assert ret is not None
        assert ret.name == "i64"

    def test_list_contains_method(self):
        """.contains is synthesized with item: param and bool return."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "contains")
        contains = typing.child_of(mono, "contains")
        assert contains.typetype == ZTypeType.FUNCTION
        assert typing.has_child(contains, "item")
        assert contains.return_type is not None
        assert contains.return_type.name == "bool"

    def test_list_contains_call(self):
        """`l.contains item: x` typechecks for numeric and String element
        types."""
        check_ok(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 1\n"
            "    b: l.contains item: 1\n"
            "}"
        )
        check_ok(
            "main: function is {\n"
            "    l: (List of: String)\n"
            '    l.append from: "x".string\n'
            '    b: l.contains item: "x".string\n'
            "}"
        )

    def test_list_sort_method(self):
        """.sort is synthesized as a zero-arg function on numeric and
        String element lists."""
        for ofty in ("i64", "u64", "f64", "String"):
            program, typing = check_ok(f"main: function is {{ l: (List of: {ofty}) }}")
            mono = [m for m, _ in typing.mono_types if m.name == f"List_{ofty}"][0]
            assert typing.has_child(mono, "sort"), f"List_{ofty} missing .sort"
            sort = typing.child_of(mono, "sort")
            assert sort.typetype == ZTypeType.FUNCTION

    def test_list_sort_call(self):
        """`l.sort` typechecks for numeric and String element types."""
        check_ok(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 3\n"
            "    l.append from: 1\n"
            "    l.sort\n"
            "}"
        )
        check_ok(
            "main: function is {\n"
            "    l: (List of: String)\n"
            '    l.append from: "b".string\n'
            '    l.append from: "a".string\n'
            "    l.sort\n"
            "}"
        )

    def test_list_insert_method(self):
        """.insert is synthesized with from: and at: parameters."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "insert")
        insert = typing.child_of(mono, "insert")
        assert insert.typetype == ZTypeType.FUNCTION
        assert typing.has_child(insert, "from")
        assert typing.has_child(insert, "at")

    def test_list_extend_method(self):
        """.extend is synthesized with from: list_T parameter."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        assert typing.has_child(mono, "extend")
        extend = typing.child_of(mono, "extend")
        assert extend.typetype == ZTypeType.FUNCTION
        assert typing.has_child(extend, "from")

    def test_list_different_element_types(self):
        """List of: i64 and List of: u64 are different types."""
        program, typing = check_ok(
            "main: function is {\n    a: (List of: i64)\n    b: (List of: u64)\n}"
        )
        names = [m.name for m, _ in typing.mono_types if "List" in m.name]
        assert "List_i64" in names
        assert "List_u64" in names

    def test_list_listview_returns_borrow(self):
        """The synthesised .listview on a mono List returns a borrowed
        ListView. The receiver lock transfers to the binding via the
        standard `.lock`-param mechanism declared on collections.z's
        native List.listview (`{t: this.lock} out (ListView of: of).borrow`).
        """
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        listview_method = typing.child_of(mono, "listview")
        assert listview_method.return_ownership == ZParamOwnership.BORROW

    def test_list_iterate_returns_borrow(self):
        """Same propagation for the .iterate iterator method."""
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "List_i64"]
        mono = monos[0]
        iterate_method = typing.child_of(mono, "iterate")
        assert iterate_method.return_ownership == ZParamOwnership.BORROW


class TestSynthesisedNativeMethodFlag:
    """Pin: every method synthesised for a monomorphised native
    collection type carries `is_native=True`. The property holds
    uniformly via the end-of-`_monomorphize` propagation loop, not
    per-site assignments. If a future synth site adds a method ZType
    on a native parent that escapes this property, this test fails
    first.

    Motivation: natives like `List_i64.iterate` are built via
    `_make_type` at monomorphisation time, but pre-fix the synth
    method itself had `is_native=False` even though its underlying
    `lib/system/collections.z` declaration is `is native`. Existing
    code only checked the parent's flag (correctly set), so nothing
    broke — but a future method-level `is_native` check would silently
    mishandle synth methods as user code. The propagation loop closes
    that gap."""

    def test_list_i64_methods_all_native(self):
        program, typing = check_ok("main: function is { l: (List of: i64) }")
        list_i64 = next(m for m, _ in typing.mono_types if m.name == "List_i64")
        for name, child in typing.children_of(list_i64):
            if child.typetype == ZTypeType.FUNCTION:
                assert child.is_native, f"list_i64.{name} should be is_native=True"

    def test_map_methods_all_native(self):
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        map_mono = next(m for m, _ in typing.mono_types if m.name == "Map_i64_i64")
        for name, child in typing.children_of(map_mono):
            if child.typetype == ZTypeType.FUNCTION:
                assert child.is_native, (
                    f"{map_mono.name}.{name} should be is_native=True"
                )

    def test_listview_methods_all_native(self):
        program, typing = check_ok(
            "main: function is {\n  l: (List of: i64)\n  v: l.listview\n}"
        )
        lv_mono = next(
            (m for m, _ in typing.mono_types if m.name.startswith("ListView_")),
            None,
        )
        assert lv_mono is not None, [m.name for m, _ in typing.mono_types]
        for name, child in typing.children_of(lv_mono):
            if child.typetype == ZTypeType.FUNCTION:
                assert child.is_native, (
                    f"{lv_mono.name}.{name} should be is_native=True"
                )

    def test_listiter_call_native(self):
        program, typing = check_ok(
            "main: function is {\n  l: (List of: i64)\n  with it: l.iterate do { }\n}"
        )
        li_mono = next(
            (m for m, _ in typing.mono_types if m.name.startswith("ListIter_")),
            None,
        )
        assert li_mono is not None, [m.name for m, _ in typing.mono_types]
        call_method = typing.child_of(li_mono, "call")
        assert call_method is not None
        assert call_method.is_native


class TestMap:
    """Tests for Map type resolution and monomorphization."""

    def test_map_creation(self):
        """Map key: i64 value: i64 creates a monomorphized Map type."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        monos = [m for m, _ in typing.mono_types if "Map" in m.name]
        assert len(monos) >= 1
        assert any(m.name == "Map_i64_i64" for m in monos)

    def test_map_is_reftype(self):
        """Map is a reference type (not valtype)."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        monos = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"]
        assert len(monos) == 1
        assert monos[0].is_valtype is False

    def test_map_length_field(self):
        """.length is synthesized as u64 field."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"][0]
        assert typing.has_child(mono, "length")
        assert typing.child_of(mono, "length").name == "u64"

    def test_map_capacity_field(self):
        """.capacity is synthesized as u64 field."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"][0]
        assert typing.has_child(mono, "capacity")
        assert typing.child_of(mono, "capacity").name == "u64"

    def test_map_set_method(self):
        """.set is synthesized with key: and value: parameters."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"][0]
        assert typing.has_child(mono, "set")
        set_m = typing.child_of(mono, "set")
        assert set_m.typetype == ZTypeType.FUNCTION
        assert typing.has_child(set_m, "key")
        assert typing.has_child(set_m, "value")

    def test_map_get_method_returns_option(self):
        """.get is synthesized returning Option of value type."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"][0]
        assert typing.has_child(mono, "get")
        get_m = typing.child_of(mono, "get")
        assert get_m.typetype == ZTypeType.FUNCTION
        ret = get_m.return_type
        assert ret is not None
        assert "option" in ret.name

    def test_map_delete_method(self):
        """.delete is synthesized returning bool."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"][0]
        assert typing.has_child(mono, "delete")
        del_m = typing.child_of(mono, "delete")
        assert del_m.typetype == ZTypeType.FUNCTION
        assert typing.has_child(del_m, "key")
        ret = del_m.return_type
        assert ret is not None
        assert ret.name == "bool"

    def test_map_has_method(self):
        """.has is synthesized returning bool."""
        program, typing = check_ok("main: function is { m: (Map key: i64 value: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Map_i64_i64"][0]
        assert typing.has_child(mono, "has")
        has_m = typing.child_of(mono, "has")
        assert has_m.typetype == ZTypeType.FUNCTION
        ret = has_m.return_type
        assert ret is not None
        assert ret.name == "bool"

    def test_map_different_types(self):
        """Different key/value types produce different monomorphized types."""
        program, typing = check_ok(
            "main: function is {\n"
            "    a: (Map key: i64 value: i64)\n"
            "    b: (Map key: String value: u64)\n"
            "}"
        )
        names = [m.name for m, _ in typing.mono_types if "Map" in m.name]
        assert "Map_i64_i64" in names
        assert "Map_String_u64" in names

    def test_map_string_key(self):
        """Map with String keys type-checks (caller projects literal to String)."""
        check_ok(
            "main: function is {\n"
            "    m: (Map key: String value: i64)\n"
            '    m.set key: "hello".string value: 42\n'
            "}"
        )


class TestSet:
    """Tests for Set type resolution and monomorphization."""

    def test_set_creation(self):
        """`(Set of: i64)` creates a monomorphized set."""
        program, typing = check_ok("main: function is { s: (Set of: i64) }")
        monos = [m for m, _ in typing.mono_types if "Set" in m.name]
        assert any(m.name == "Set_i64" for m in monos)

    def test_set_is_reftype(self):
        program, typing = check_ok("main: function is { s: (Set of: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Set_i64"][0]
        assert mono.is_valtype is False
        assert mono.is_heap_allocated is True

    def test_set_length_and_capacity_fields(self):
        program, typing = check_ok("main: function is { s: (Set of: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Set_i64"][0]
        assert typing.has_child(mono, "length")
        assert typing.child_of(mono, "length").name == "u64"
        assert typing.has_child(mono, "capacity")
        assert typing.child_of(mono, "capacity").name == "u64"

    def test_set_methods_synthesized(self):
        """.add / .has / .delete / .iterate synthesized with `item:` arg
        and bool return where applicable."""
        program, typing = check_ok("main: function is { s: (Set of: i64) }")
        mono = [m for m, _ in typing.mono_types if m.name == "Set_i64"][0]
        for mname in ("add", "has", "delete"):
            assert typing.has_child(mono, mname), f"missing {mname}"
            m = typing.child_of(mono, mname)
            assert m.typetype == ZTypeType.FUNCTION
            assert typing.has_child(m, "item")
            assert m.return_type is not None
            assert m.return_type.name == "bool"
        assert typing.has_child(mono, "iterate")

    def test_set_iter_mono_created(self):
        """SetIter<of> is monomorphized alongside the source Set."""
        program, typing = check_ok("main: function is { s: (Set of: i64) }")
        names = [m.name for m, _ in typing.mono_types]
        assert "SetIter_i64" in names

    def test_set_different_element_types(self):
        program, typing = check_ok(
            "main: function is {\n    a: (Set of: i64)\n    b: (Set of: String)\n}"
        )
        names = [m.name for m, _ in typing.mono_types if "Set" in m.name]
        assert "Set_i64" in names
        assert "Set_String" in names

    def test_set_add_has_delete_typecheck(self):
        """Round-trip .add / .has / .delete with i64 element."""
        check_ok(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 1\n"
            "    s.add item: 2\n"
            "    h: s.has item: 1\n"
            "    d: s.delete item: 1\n"
            "}"
        )

    def test_set_iterate_via_with_do(self):
        """`with it: s.iterate do for x: it loop` drives the iterator."""
        check_ok(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 1\n"
            "    with it: s.iterate do for x: it loop { }\n"
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

    @staticmethod
    def _const_value(typing, parsed_node):
        """Look up `const_value` for a parsed expression. Descends the
        `Expression` wrapper into its inner subtype, then reads from
        `ZTyping.node_const_value`."""
        target = parsed_node
        while isinstance(target, zast.Expression):
            target = target.expression
        return typing.node_const_value.get(target.nodeid)

    def test_const_value_numeric_literal(self):
        """Numeric literal should have const_value set."""
        program, typing = check_ok("main: function is { x: 42 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert self._const_value(typing, inner) == 42

    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("1 + 2", 3),
            ("10 - 3", 7),
            ("4 * 5", 20),
            ("10 / 3", 3),  # truncation toward zero
            ("-7 / 2", -3),  # C semantics
            ("3 < 5", True),
            ("5 < 3", False),
            ("1 + 2 + 3", 6),  # chained, left-to-right
        ],
    )
    def test_const_value_binop_folds(self, expr, expected):
        program, typing = check_ok(f"main: function is {{ x: {expr} }}")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert self._const_value(typing, inner) == expected

    def test_const_value_none_for_variables(self):
        """Variable + literal should NOT fold."""
        program, typing = check_ok("main: function is {\n  x: 5\n  y: x + 1\n}")
        inner = self._get_rhs_inner(program, stmt_index=1)
        assert isinstance(inner, zast.BinOp)
        assert self._const_value(typing, inner) is None

    def test_const_value_division_by_zero(self):
        """1 / 0 should be a compile-time error."""
        errors = check_errors("main: function is { x: 1 / 0 }")
        assert any("division by zero" in e.msg.lower() for e in errors)

    def test_const_value_named_constant(self):
        """Reference to a named constant should propagate const_value."""
        program, typing = check_ok(
            'north: 0\nmain: function is {\n  x: north\n  print "\\{x}"\n}'
        )
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert self._const_value(typing, inner) == 0

    def test_const_value_chained_named(self):
        """Chained named constants: a: 1, b: a + 2 -> b is 3."""
        program, typing = check_ok(
            'a: 1\nb: a + 2\nmain: function is {\n  x: b\n  print "\\{x}"\n}'
        )
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert self._const_value(typing, inner) == 3

    def test_const_value_overflow_error(self):
        """255u8 + 1u8 should produce a compile-time overflow error."""
        errors = check_errors("main: function is { x: 255u8 + 1u8 }")
        assert any("overflow" in e.msg.lower() for e in errors)

    def test_const_value_f64_folded(self):
        """f64 operations should be folded."""
        program, typing = check_ok("main: function is { x: 1.5f64 + 2.5f64 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert self._const_value(typing, inner) == 4.0

    def test_const_value_f32_not_folded(self):
        """f32 operations should not be folded (precision mismatch with host)."""
        program, typing = check_ok("main: function is { x: 1.5f32 + 2.5f32 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert self._const_value(typing, inner) is None

    def test_const_value_bool_via_comparison(self):
        """Comparison results should have bool const_value (True/False)."""
        program, typing = check_ok("main: function is {\n  x: 1 == 1\n  y: 1 == 2\n}")
        inner0 = self._get_rhs_inner(program, stmt_index=0)
        inner1 = self._get_rhs_inner(program, stmt_index=1)
        assert isinstance(inner0, zast.BinOp)
        assert self._const_value(typing, inner0) is True
        assert isinstance(inner1, zast.BinOp)
        assert self._const_value(typing, inner1) is False

    def test_const_value_propagates_through_expression(self):
        """const_value should propagate from inner Operation to Expression wrapper."""
        program, typing = check_ok("main: function is { x: 1 + 2 }")
        rhs = self._get_rhs(program)
        assert isinstance(rhs, zast.Expression)
        assert self._const_value(typing, rhs) == 3

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

    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("1.5 + 2.5", 4.0),
            ("5.0 - 1.5", 3.5),
            ("2.0 * 3.5", 7.0),
            ("7.0 / 2.0", 3.5),
            ("1.0 < 2.0", True),
            ("3.0 < 2.0", False),
            ("1.0 + 2.0 + 3.0", 6.0),
        ],
    )
    def test_f64_binop_folds(self, expr, expected):
        program, typing = check_ok(f"main: function is {{ x: {expr} }}")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.BinOp)
        assert self._const_value(typing, inner) == expected

    def test_f64_literal_const_value(self):
        """f64 literal should have const_value set."""
        program, typing = check_ok("main: function is { x: 3.14 }")
        inner = self._get_rhs_inner(program)
        assert isinstance(inner, zast.AtomId)
        assert self._const_value(typing, inner) == 3.14


class TestIfExpression:
    """Tests for if-as-expression (Phase 42)."""

    def test_if_expression_basic(self):
        """Basic if-expression with compatible integer branches."""
        program, typing = check_ok("main: function is { x: if 1 < 2 then 1 else 2 }")
        main = program.units[program.mainunitname].body["main"]
        assign = main.body.statements[0].statementline
        assert isinstance(assign, zast.Assignment)
        assign_t = _node_ztype(typing, assign)
        assert assign_t is not None
        assert assign_t.name == "i64"

    def test_if_expression_sets_ifnode_type(self):
        """If with else should set ifnode.type to common branch type."""
        program, typing = check_ok("main: function is { x: if 1 < 2 then 10 else 20 }")
        main = program.units[program.mainunitname].body["main"]
        assign = main.body.statements[0].statementline
        expr = assign.value
        inner = expr.expression
        assert isinstance(inner, zast.If)
        inner_t = _node_ztype(typing, inner)
        assert inner_t is not None
        assert inner_t.name == "i64"

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
        program, typing = check_ok(
            'x: if 1 < 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )
        resolved = typing.resolved
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
        program, typing = check_ok(
            'x: if 1 < 2 then { 42 } else { 99u8 }\nmain: function is { print "\\{x}" }'
        )
        # x should have type i64 (the true branch type)
        resolved = typing.resolved
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
            '  with c: (myclass x: 1 y: 2) do print "\\{c.get_y}"\n'
            "}"
        )

    def test_class_public_restricts_field(self):
        """Class with public restriction prevents external field access."""
        errors = check_errors(
            "myclass: class { x: i64 secret: i64 } as {\n"
            "  public: unit { :x }\n"
            "}\n"
            "main: function is {\n"
            '  with c: (myclass x: 1 secret: 42) do print "\\{c.secret}"\n'
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
        program, typing = check_ok('main: function is { print "hello" }')
        # return/break/continue are resolved from system.z as native functions
        assert program is not None

    def test_system_native_types_resolve(self):
        """System native types (bool, null, String) resolve and are usable."""
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
        """return (native) should not trigger 'Cannot take Spec' error."""
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
        """error with interpolated String uses generic fallback message."""
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
            "  t: Any.generic\n"
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
            "  t: Any.generic\n"
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
            "  t: Any.generic\n"
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
            "  t: Any.generic\n"
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
            "  t: Any.generic\n"
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
        program, typing = check_ok(
            "main: function is {\n  x: {\n    if 1 > 2 then { break }\n    42\n  }\n}"
        )
        tc = TypeChecker(program)
        tc.check()
        # x should have optionval type
        t = tc._resolve_unit_name("test", "main")
        assert t is not None

    def test_do_without_break_unchanged(self):
        """Do block without break keeps plain type (no optional wrapping)."""
        program, typing = check_ok("main: function is { x: { 42 } }")
        tc = TypeChecker(program)
        tc.check()

    def test_do_break_nested_for_binds_to_for(self):
        """break inside for inside do binds to the for, not the do."""
        program, typing = check_ok(
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
            "Reader: class { src: bag.private } as {\n"
            "  read: function {r: this} out i64 is { return r.src.secret }\n"
            "}\n"
            "main: function is { b: bag secret: 42\n"
            "  r: Reader src: b.take\n"
            '  print "\\{Reader.read r: r}" }'
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
            "Reader: class { src: bag } as {\n"
            "  read: function {r: this} out i64 is { return r.src.secret }\n"
            "}\n"
            "main: function is { b: bag secret: 42\n"
            "  r: Reader src: b.take\n"
            '  print "\\{Reader.read r: r}" }'
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
        """Within a match arm, the variable is narrowed to that arm's
        subtype — `x` inside `case ok then` has type i64 (the payload),
        so bare `x` prints the value. Reaching back via `x.ok` is an
        error (the parent union is shadowed)."""
        check_ok(
            "r: variant { ok: i64  err: i64  none: null }\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "\\{x}"\n'
            "  } case err then {\n"
            '    print "\\{x}"\n'
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
        """Narrowing is intra-function: callee sees full type. Inside
        the match arms, `x` is narrowed to the payload (i64), so bare
        `x` prints it directly."""
        check_ok(
            "r: variant { ok: i64  err: i64 }\n"
            "f: function {x: r} is {\n"
            "  match (\n    x\n  ) case ok then {\n"
            '    print "\\{x}"\n'
            "  } case err then {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  x: r.ok 42\n"
            "  f x: x\n"
            "}"
        )

    # -- Nested match --

    def test_narrow_nested_match(self):
        """Inner match narrows independently of outer. `x` inside each
        arm is the narrowed payload (i64); bare `x` reads it."""
        check_ok(
            "r: variant { a: i64  b: i64  c: i64 }\n"
            "main: function is {\n"
            "  x: r.a 1\n"
            "  match (\n    x\n  ) case a then {\n"
            '    print "\\{x}"\n'
            "  } case b then {\n"
            '    print "\\{x}"\n'
            "  } case c then {\n"
            '    print "\\{x}"\n'
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
            "Box: record { val: t } as { t: Any.generic }\n"
            "main: function is {\n"
            "  b: Box val: 42\n"
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
        """Reassigning a String constant from 'as' is a compile error."""
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

    def test_match_subject_take_rejected(self):
        """`.take` directly on a match subject is rejected at type-check
        time. Narrowing requires the subject to remain addressable across
        arms; taking ownership at match time would invalidate later arms."""
        errors = check_errors(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u.take) case ok then {\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}"
        )
        assert any("'.take' the subject of 'match'" in e.msg for e in errors), (
            f"expected match-take rejection; got: {[e.msg for e in errors]}"
        )

    def test_union_no_take_subject_still_valid(self):
        """Match without take — subject still valid after match. Under
        shadow narrowing, `u` inside each arm IS the narrowed payload
        (i64); bare `u` prints the value."""
        check_ok(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            '    print "\\{u}"\n'
            "  } case err then {\n"
            '    print "\\{u}"\n'
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
        program, typing = check_ok(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = typing.resolved.get("test.point")
        assert point_type is not None
        eq = typing.child_of(point_type, "==")
        assert eq is not None
        assert eq.is_autogen_eq
        assert eq.return_type.name == "bool"
        neq = typing.child_of(point_type, "!=")
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
            "Result: variant { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  a: Result.ok 1\n"
            "  b: Result.ok 2\n"
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
        program, typing = check_ok(
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
        point_type = typing.resolved.get("test.point")
        assert point_type is not None
        eq = typing.child_of(point_type, "==")
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
        program, typing = check_ok(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = typing.resolved.get("test.point")
        eq = typing.child_of(point_type, "==")
        assert eq.is_simple_eq

    def test_memcmp_eq_float_disqualifies(self):
        """Record with float field does NOT get memcmp-safe equality."""
        program, typing = check_ok(
            "point: record { x: 0.0  y: 0.0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        point_type = typing.resolved.get("test.point")
        eq = typing.child_of(point_type, "==")
        assert eq.is_autogen_eq
        assert not eq.is_simple_eq

    def test_memcmp_eq_mixed_int_float(self):
        """Record with mixed int and float fields is NOT memcmp-safe."""
        program, typing = check_ok(
            "rec: record { a: 0  b: 0.0f32 }\n"
            "main: function is {\n"
            "  x: rec\n"
            "  y: rec\n"
            "  if x == y then return 0\n"
            "}"
        )
        rec_type = typing.resolved.get("test.rec")
        assert not typing.child_of(rec_type, "==").is_simple_eq

    def test_memcmp_eq_enum_variant(self):
        """Pure enum variant is memcmp-safe."""
        program, typing = check_ok(
            "color: variant { red: null  green: null }\n"
            "main: function is {\n"
            "  a: color.red\n"
            "  b: color.red\n"
            "  if a == b then return 0\n"
            "}"
        )
        color_type = typing.resolved.get("test.color")
        assert typing.child_of(color_type, "==").is_simple_eq

    def test_memcmp_eq_variant_with_int_payloads(self):
        """Variant with integer payloads is memcmp-safe."""
        program, typing = check_ok(
            "Result: variant { ok: i64  err: u8 }\n"
            "main: function is {\n"
            "  a: Result.ok 1\n"
            "  b: Result.ok 1\n"
            "  if a == b then return 0\n"
            "}"
        )
        result_type = typing.resolved.get("test.Result")
        assert typing.child_of(result_type, "==").is_simple_eq

    def test_memcmp_eq_variant_with_float_payload(self):
        """Variant with float payload is NOT memcmp-safe."""
        program, typing = check_ok(
            "Result: variant { ok: f64  none: null }\n"
            "main: function is {\n"
            "  a: Result.ok 1.0\n"
            "  b: Result.ok 1.0\n"
            "  if a == b then return 0\n"
            "}"
        )
        result_type = typing.resolved.get("test.Result")
        assert not typing.child_of(result_type, "==").is_simple_eq


class TestStringEquality:
    """Test String == / != operators."""

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

    def test_string_ordering_resolves(self):
        """<, <=, >, >= on strings type-check; `compare` returns i32."""
        check_ok(
            "main: function is {\n"
            '  a: "hello".string\n'
            '  b: "world".string\n'
            "  if a < b then return 0\n"
            "  if a <= b then return 0\n"
            "  if a > b then return 0\n"
            "  if a >= b then return 0\n"
            "  c: a.compare rhs: b\n"
            '  print "\\{c}"\n'
            "}"
        )

    def test_stringview_ordering_resolves(self):
        """<, <=, >, >= on StringView type-check; `compare` returns i32."""
        check_ok(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "world"\n'
            "  if a < b then return 0\n"
            "  if a <= b then return 0\n"
            "  c: a.compare rhs: b\n"
            '  print "\\{c}"\n'
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


class TestStringHash:
    """`String.hash` / `StringView.hash` natives type-check and produce
    a `u64` value usable in arithmetic / equality."""

    def test_string_hash_returns_u64(self):
        program, typing = check_ok(
            'main: function is {\n  s: "hello".string\n  h: s.hash\n}'
        )
        main = program.units[program.mainunitname].body["main"]
        # the binding's RHS resolves to u64
        h_decl = main.body.statements[1].statementline
        rhs_type = typing.node_type.get(h_decl.value.nodeid)
        assert rhs_type is not None
        assert rhs_type.name == "u64"

    def test_stringview_hash_returns_u64(self):
        check_ok(
            "main: function is {\n"
            '  sv: "abc"\n'
            "  h: sv.hash\n"
            "  if h == 0u64 then return 0\n"
            "}"
        )


class TestTakeInArm:
    """Test that .take inside if/match arms invalidates the variable after."""

    def test_take_in_one_if_arm_invalidates(self):
        """Take in then-arm, variable invalid after if."""
        errors = check_errors(
            "Box: class { value: i64 }\n"
            "main: function is {\n"
            "  a: Box value: 1\n"
            "  if 1 > 0 then { b: a.take }\n"
            '  print "\\{a.value}"\n'
            "}"
        )
        assert any("ownership transfer" in e.msg.lower() for e in errors)

    def test_take_in_if_else_both_arms_invalidates(self):
        """Take in both arms, variable invalid after if."""
        errors = check_errors(
            "Box: class { value: i64 }\n"
            "consume: function {b: Box.take} is {}\n"
            "main: function is {\n"
            "  a: Box value: 1\n"
            "  if 1 > 0 then { consume a } else { consume a }\n"
            '  print "\\{a.value}"\n'
            "}"
        )
        assert any("ownership transfer" in e.msg.lower() for e in errors)

    def test_no_take_in_any_arm_still_valid(self):
        """No take in Any arm, variable still valid after if."""
        check_ok(
            "Box: class { value: i64 }\n"
            "main: function is {\n"
            "  a: Box value: 1\n"
            '  if 1 > 0 then { print "hello" } else { print "world" }\n'
            '  print "\\{a.value}"\n'
            "}"
        )

    def test_take_in_one_if_arm_no_else_invalidates(self):
        """Take in then-arm with no else, variable invalid after if."""
        errors = check_errors(
            "Box: class { value: i64 }\n"
            "main: function is {\n"
            "  a: Box value: 1\n"
            "  if 1 > 0 then { b: a.take }\n"
            '  print "\\{a.value}"\n'
            "}"
        )
        assert any("ownership transfer" in e.msg.lower() for e in errors)


class TestUnionLockedArm:
    """Locked union arms (W-C) — arms declared `name: t.lock` hold a
    borrowed reference into a parent rather than owning their payload."""

    def test_union_with_locked_arm_resolves(self):
        """A union may declare an arm as `name: type.lock`."""
        program, typing = check_ok(
            "src: class { v: i64 }\n"
            "myview: union { none: null\n some: src.lock }\n"
            "main: function is {\n"
            "  s: src v: 7\n"
            "  x: myview.none src\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myview")
        assert ut is not None
        assert tc.typing.is_child_lock_arm(ut, "some")
        assert not tc.typing.is_child_lock_arm(ut, "none")

    def test_union_all_null_or_locked_no_destructor(self):
        """A union whose every arm is `null` or `.lock` needs no destructor."""
        program, typing = check_ok(
            "src: class { v: i64 }\n"
            "myview: union { none: null\n some: src.lock }\n"
            "main: function is {\n"
            "  s: src v: 7\n"
            "  x: myview.none src\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.myview")
        assert (ut.destructor_name is not None) is False
        assert ut.destructor_name is None

    def test_union_mixed_arms_keeps_destructor(self):
        """An owned arm anywhere in the union keeps the destructor live."""
        program, typing = check_ok(
            "src: class { v: i64 }\n"
            "errkind: variant { e1: null\n e2: null }\n"
            "mixed: union { err: errkind\n cached: src.lock\n fresh: src }\n"
            "main: function is {\n"
            "  s: src v: 7\n"
            "  x: mixed.err (errkind.e1)\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.mixed")
        assert (ut.destructor_name is not None) is True
        assert ut.destructor_name == "z_mixed_destroy"
        assert set(tc.typing.lock_arm_names_of(ut)) == {"cached"}

    def test_variant_locked_arm_rejected(self):
        """A variant arm cannot use `.lock` — variants are valtype-only."""
        errors = check_errors(
            "myvar: variant { a: i64\n b: i64.lock }\n"
            "main: function is { x: myvar.a 1 }"
        )
        assert any(
            "variant" in e.msg.lower() and ".lock" in e.msg.lower() for e in errors
        )

    def test_union_arm_borrow_rejected(self):
        """`.borrow` modifier on union arms is rejected; only `.lock`
        is permitted."""
        errors = check_errors(
            "src: class { v: i64 }\n"
            "u: union { only: src.borrow }\n"
            "main: function is {\n"
            "  s: src v: 7\n"
            "  x: u.only src\n"
            "}"
        )
        assert any("only '.lock' is permitted" in e.msg.lower() for e in errors)

    def test_union_with_only_locked_arm(self):
        """Single-locked-arm union resolves and has no destructor."""
        program, typing = check_ok(
            "src: class { v: i64 }\n"
            "wrap: union { val: src.lock }\n"
            "main: function is {\n"
            "  s: src v: 7\n"
            "  x: wrap.val src\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        ut = tc._resolved.get("test.wrap")
        assert set(tc.typing.lock_arm_names_of(ut)) == {"val"}
        assert (ut.destructor_name is not None) is False


class TestOptionview:
    """The stdlib `OptionView` type — built on locked union arms.
    Container iterators use it to yield non-owning views into their
    source. Layout: standard {tag, void*}; the .some arm holds a
    pointer to the source's storage."""

    def test_optionview_template_resolves(self):
        """The OptionView template exists in stdlib and is a generic union."""
        program, typing = check_ok("main: function is { x: OptionView.none i64 }")
        tc = TypeChecker(program)
        tc.check()
        ovt = tc._resolve_name("OptionView")
        assert ovt is not None
        assert ovt.typetype == ZTypeType.UNION
        assert ovt.isgeneric
        assert tc.typing.is_child_lock_arm(ovt, "some")
        assert not tc.typing.is_child_lock_arm(ovt, "none")
        # destructor elision is deferred to monomorphization for generic
        # unions; see test_optionview_mono_no_destructor for the mono case.

    def test_optionview_none_compiles(self):
        """OptionView.none with explicit type arg compiles cleanly."""
        check_ok("main: function is { x: OptionView.none i64 }")

    def test_optionview_some_from_class(self):
        """OptionView.some from: lvalue compiles for a reftype source."""
        check_ok(
            "src: class { v: i64 }\n"
            "main: function is {\n"
            "  s: src v: 42\n"
            "  ov: OptionView.some from: s\n"
            "}"
        )

    def test_optionview_mono_no_destructor(self):
        """A monomorphized OptionView<T> needs no runtime cleanup."""
        program, typing = check_ok(
            "src: class { v: i64 }\n"
            "main: function is {\n"
            "  s: src v: 7\n"
            "  ov: OptionView.some from: s\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        mono = None
        for k, v in tc._resolved.items():
            if "OptionView" in k and v.typetype == ZTypeType.UNION:
                if v.generic_origin is not None:
                    mono = v
                    break
        assert mono is not None, "OptionView mono not resolved"
        assert (mono.destructor_name is not None) is False
        assert mono.destructor_name is None
        assert tc.typing.is_child_lock_arm(mono, "some")

    def test_is_iterator_wrapper_recognises_all_three(self):
        """The for-loop dispatch helper accepts Option, optionval, AND
        OptionView."""
        program, typing = check_ok(
            "src: class { v: i64 }\n"
            "main: function is {\n"
            '  a: Option.some "x".string\n'
            "  b: optionval.some 1\n"
            "  s: src v: 7\n"
            "  c: OptionView.some from: s\n"
            "}"
        )
        tc = TypeChecker(program)
        tc.check()
        a_t = b_t = c_t = None
        for k, v in tc._resolved.items():
            if "Option_String" in k and v.typetype == ZTypeType.UNION:
                a_t = v
            elif "optionval_i64" in k and v.typetype == ZTypeType.VARIANT:
                b_t = v
            elif "OptionView" in k and v.typetype == ZTypeType.UNION:
                if v.generic_origin is not None:
                    c_t = v
        assert a_t and b_t and c_t
        assert tc._is_iterator_wrapper(a_t)
        assert tc._is_iterator_wrapper(b_t)
        assert tc._is_iterator_wrapper(c_t)


class TestOptionviewBorrowEscape:
    """Loop variables bound from OptionView iteration carry borrow_origin
    and trigger the existing lock-escape checks. Option / optionval
    bindings stay owned (regression guard)."""

    def test_optionview_loop_var_rejects_move_into_aggregate(self):
        """Cannot move a borrowed loop var into another collection."""
        errors = check_errors(
            "main: function is {\n"
            "  xs: (List of: String)\n"
            '  xs.append from: "hello".string\n'
            "  sink: (List of: String)\n"
            "  with it: xs.iterate do for s: it loop {\n"
            "    sink.append from: s\n"
            "  }\n"
            "}"
        )
        assert any("borrowed" in e.msg.lower() for e in errors)

    def test_optionview_loop_var_rejects_return(self):
        """Cannot return a borrowed loop var from the enclosing function."""
        errors = check_errors(
            "get_first: function {xs: (List of: String)} out String is {\n"
            "  with it: xs.iterate do for s: it loop {\n"
            "    return s\n"
            "  }\n"
            '  return "empty".string\n'
            "}\n"
            "main: function is {\n"
            "  xs: (List of: String)\n"
            '  xs.append from: "hi".string\n'
            "  print get_first xs: xs\n"
            "}"
        )
        assert any("borrowed" in e.msg.lower() for e in errors)

    def test_optionview_inloop_read_still_works(self):
        """In-loop read access of a borrowed String view is allowed."""
        check_ok(
            "main: function is {\n"
            "  xs: (List of: String)\n"
            '  xs.append from: "hello".string\n'
            "  with it: xs.iterate do for s: it loop {\n"
            '    print "s=\\{s}"\n'
            "  }\n"
            "}"
        )

    def test_option_loop_var_still_owned_regression(self):
        """Option / optionval iterators yield owned values that may be
        moved (regression guard for the borrow-only scoping in W-D)."""
        # textreader yields option(string); the loop body may consume
        # each line by moving it into a collection.
        check_ok(
            "main: function is {\n"
            "  saved: (List of: String)\n"
            '  with f: (io.open path: "/tmp/__nope" mode: openmode.read) do {\n'
            "  }\n"
            '  print "ok"\n'
            "}"
        )

    def test_map_iterate_items_borrow_escape_rejected(self):
        """MapEntry from Map.iterateItems is borrowed; transferring
        a key into another collection fails the escape check."""
        errors = check_errors(
            "main: function is {\n"
            "  m: (Map key: String value: i64)\n"
            '  m.set key: "k".string value: 1\n'
            "  sink: (List of: String)\n"
            "  with it: m.iterateItems do for e: it loop {\n"
            "    sink.append from: e.key\n"
            "  }\n"
            "}"
        )
        # accept any error that blocks the transfer; the exact
        # diagnostic path may evolve as borrow tracking matures.
        assert errors


class TestIteratorReturnType:
    """Phase G2: `out (iterator gives: T (takes: U))` return type.

    The body is left empty here — full generator-body handling (yield
    suspension points, terminal return, etc.) lands in G3. What G2
    verifies is that the structural return-type pattern parses,
    resolves, and that the `gives:` argument's ownership modifier is
    validated against the legal yield forms."""

    def test_iterator_return_type_parses_and_resolves(self):
        """`out (iterator gives: i32)` resolves to a monomorphized
        iterator protocol with `gives=i32` and the defaulted
        `takes=null`."""
        program, typing = check_ok("g: function out (iterator gives: i32) is { }")
        g = program.units["test"].body["g"]
        rt_zt = typing.node_type.get(g.returntype.nodeid)
        assert rt_zt is not None
        assert rt_zt.generic_origin is not None
        assert rt_zt.generic_origin.name == "iterator"

    def test_iterator_return_type_takes_parses(self):
        """`out (iterator gives: i32 takes: bool)` — both generic
        params explicit — type-checks cleanly."""
        program, typing = check_ok(
            "g: function out (iterator gives: i32 takes: bool) is { }"
        )
        g = program.units["test"].body["g"]
        rt_zt = typing.node_type.get(g.returntype.nodeid)
        assert rt_zt is not None
        assert rt_zt.generic_origin is not None
        assert rt_zt.generic_origin.name == "iterator"

    def test_iterator_return_type_borrow_form(self):
        """`gives: T.borrow` is accepted (yields a borrowed view)."""
        check_ok("g: function out (iterator gives: String.borrow) is { }")

    def test_iterator_return_type_take_form(self):
        """`gives: T.take` is accepted (yields an owned reftype)."""
        check_ok("g: function out (iterator gives: String.take) is { }")

    def test_iterator_return_type_bad_gives_form_rejected(self):
        """`gives: T.lock` is rejected — `.lock` is parameter-only
        ownership and has no corresponding yield-wrapper form."""
        errors = check_errors("g: function out (iterator gives: String.lock) is { }")
        assert any(".lock" in e.msg for e in errors), (
            f"Expected an error mentioning .lock, got: {[e.msg for e in errors]}"
        )


class TestGeneratorDesugaring:
    """Phase G3: desugar generator functions into a synthesized
    iterator class + factory pair.

    A *generator* is any function whose declared return type is
    `iterator gives: T (takes: U)` AND whose body contains at least
    one `yield`. The pass walks the parsed program before type
    resolution; the resulting AST is ordinary class+function code
    that the rest of the typechecker validates with no further
    generator-specific machinery.
    """

    def test_simple_generator_desugars_to_class(self):
        """A no-parameter generator produces a class named
        `<funcname>_iter` with a `state` field, a `create` static,
        and an instance `call` method. The original function is
        rewritten as a factory whose return type is the synth
        class and whose body builds an instance via
        `<synth>.create`."""
        program, _typing = check_ok(
            "gen: function out (iterator gives: i32) is { yield 1 }"
        )
        body = program.units["test"].body
        assert "gen_iter" in body, f"Expected synth class 'gen_iter', got: {list(body)}"
        synth = body["gen_iter"]
        assert synth.nodetype == zast.NodeType.CLASS
        assert "state" in synth.is_items
        assert "create" in synth.as_items
        assert "call" in synth.as_items
        gen = body["gen"]
        assert isinstance(gen, zast.Function)
        stmts = gen.body.statements
        assert len(stmts) == 1
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Expression)
        assert sl.expression.nodetype == zast.NodeType.CALL

    def test_generator_with_parameter_take_captured(self):
        """A `.take` parameter becomes an owned field on the synth
        class (the `.take` is the ownership-transfer at the
        factory's call site; the class itself stores the value
        directly)."""
        program, _typing = check_ok(
            "gen: function {s: String.take} out (iterator gives: i32) is { yield 1 }"
        )
        synth = program.units["test"].body["gen_iter"]
        assert "s" in synth.is_items
        field = synth.is_items["s"]
        assert field.nodetype == zast.NodeType.ATOMID and field.name == "String"

    def test_generator_with_parameter_lock_captured(self):
        """A `.lock` parameter becomes a lock field so the iterator
        holds the lock for its lifetime."""
        program, _typing = check_ok(
            "holdgen: function {b: Bytes.lock} out (iterator gives: i32) "
            "is { yield 1 }\n"
            "main: function is { b: (Bytes) "
            "with g: (holdgen b: b.lock) do { } }"
        )
        synth = program.units["test"].body["holdgen_iter"]
        assert "b" in synth.is_items
        assert synth.is_items["b"].nodetype == zast.NodeType.DOTTEDPATH

    def test_generator_borrow_param_rejected(self):
        """Bare `.borrow` on a generator parameter is rejected with
        a message pointing at the legal alternatives."""
        errors = check_errors(
            "gen: function {s: String.borrow} out (iterator gives: i32) is { yield 1 }"
        )
        assert any(
            "borrow" in e.msg and (".lock" in e.msg or ".take" in e.msg) for e in errors
        ), f"Expected a borrow-rejection message, got: {[e.msg for e in errors]}"

    def test_generator_method_receiver_lock_captured(self):
        """A method on a class typed `b: this.lock` becomes a
        receiver-lock field on the synth class."""
        program, _typing = check_ok(
            "Bag: class { x: i64 } as {\n"
            "    each: function {b: this.lock} out (iterator gives: i32) "
            "is { yield b.x }\n"
            "}"
        )
        synth = program.units["test"].body["Bag_each_iter"]
        assert "b" in synth.is_items
        assert synth.is_items["b"].nodetype == zast.NodeType.DOTTEDPATH

    def test_generator_method_this_bare_rejected(self):
        """Bare `:this` on a generator method is rejected; the
        iterator outlives the factory call so the receiver needs
        an explicit lock."""
        errors = check_errors(
            "Bag: class { x: i64 } as {\n"
            "    each: function {:this} out (iterator gives: i32) "
            "is { yield 1 }\n"
            "}"
        )
        assert any("':this.lock'" in e.msg or "this.lock" in e.msg for e in errors), (
            f"Expected a :this rejection message, got: {[e.msg for e in errors]}"
        )

    def test_generator_method_private_lock_captured(self):
        """A method receiver typed `b: this.private.lock` captures
        the friend-access lock — the synth class's field is a
        `Type.private.lock` dotted path."""
        program, _typing = check_ok(
            "Bag: class { x: i64 } as {\n"
            "    each: function {b: this.private.lock} out "
            "(iterator gives: i32) is { yield b.x }\n"
            "}"
        )
        synth = program.units["test"].body["Bag_each_iter"]
        assert "b" in synth.is_items

    def test_generator_return_with_value_rejected(self):
        """`return <value>` inside a generator body is a compile
        error — yielded values exit via `yield`."""
        errors = check_errors(
            "gen: function out (iterator gives: i32) is {\n    yield 1\n    return 5\n}"
        )
        assert any(
            "'return <value>'" in e.msg
            or ("return" in e.msg.lower() and "generator" in e.msg.lower())
            for e in errors
        ), f"Expected a return-rejection message, got: {[e.msg for e in errors]}"

    def test_generator_no_yield_in_body_accepted_as_factory(self):
        """Open question A settled toward (i): an iterator-return
        function with no `yield` in its body is an ordinary
        factory; no synth class is generated."""
        program, _typing = check_ok("gen: function out (iterator gives: i32) is { }")
        body = program.units["test"].body
        assert "gen_iter" not in body
        assert "gen" in body

    def test_yield_in_nested_function_rejected_at_parse(self):
        """Already enforced by the parser (G1 rule 10); re-asserted
        here to lock the boundary down at the desugaring layer
        too (the parser surfaces this as a parse error, before
        desugaring ever runs)."""
        result = make_parser(
            "gen: function out (iterator gives: i32) is { yield 1 } "
            "as { helper: function is { yield 9 } }",
            unitname="test",
            src_dir=LIB_DIR,
        ).parse()
        assert isinstance(result, zast.Error)

    def test_generator_method_locks_receiver_for_iter_lifetime(self):
        """A generator method declaring `b: this.lock` causes the
        synth iterator class to hold an exclusive lock on the
        receiver for the iterator's lifetime. Mutation of the
        receiver inside the for-loop body is rejected at type-check
        time. (Originally G4's deferred soundness exercise; landed
        once the broader class-construction lock-transfer gap was
        closed in commit 4e0123d.)"""
        errors = check_errors(
            "Bag: class { x: i64 } as {\n"
            "    each: function {b: this.lock} out (iterator gives: i64) is "
            "{ yield b.x }\n"
            "}\n"
            "main: function is {\n"
            "    b: Bag x: 10\n"
            "    with it: (Bag.each b: b.lock) do for v: it loop {\n"
            "        b.x = 99\n"
            "    }\n"
            "}"
        )
        assert any("exclusive lock" in e.msg.lower() and "'b'" in e.msg for e in errors)

    def test_generator_receiver_lock_released_after_iter_scope(self):
        """The receiver lock the synth iterator holds is scoped to
        the `with` binding -- once the iterator falls out of scope
        the receiver is mutable again. Pins the symmetric positive
        case alongside the rejection above."""
        check_ok(
            "Bag: class { x: i64 } as {\n"
            "    each: function {b: this.lock} out (iterator gives: i64) is "
            "{ yield b.x }\n"
            "}\n"
            "main: function is {\n"
            "    b: Bag x: 10\n"
            "    with it: (Bag.each b: b.lock) do for v: it loop { }\n"
            "    b.x = 99\n"
            "}"
        )

    def test_for_loop_over_generator_synthesizes_class_call(self):
        """A for-loop drives the synth class's `.call`; the
        end-to-end pipeline (parse → desugar → type-check) finishes
        without errors."""
        check_ok(
            "gen: function out (iterator gives: i32) is "
            "{ yield 1 yield 2 yield 3 }\n"
            "main: function is { with it: gen do for x: it loop { } }"
        )

    def test_generator_field_promotion_only_for_yield_crossing_locals(self):
        """G7 liveness: only locals that cross a yield are promoted
        to class fields. Non-crossing locals (defined and used
        between two yields, with no suspension in the middle) stay
        on the C stack inside the synth `.call` body.

        Sample shape:
            crossing:     defined before yield, used after  → field
            non_crossing: defined and used between yields   → stack
            counter:      loop counter with a yield in body → field
                                                                (yielding-loop rule)
        """
        program, _typing = check_ok(
            "gen: function {n: i64} out (iterator gives: i64) is {\n"
            "    crossing: 100\n"
            "    yield 1\n"
            "    yield crossing\n"
            "    non_crossing: 200\n"
            "    yield non_crossing\n"
            "    counter: 0\n"
            "    for while counter < n loop {\n"
            "        yield counter\n"
            "        counter = counter + 1\n"
            "    }\n"
            "}"
        )
        synth = program.units["test"].body["gen_iter"]
        fields = set(synth.is_items.keys())
        # Always-present fields: state cursor + captured params.
        assert "state" in fields
        assert "n" in fields  # parameter — always promoted
        # Cross-yield locals are promoted.
        assert "crossing" in fields, (
            f"`crossing` (def before yield, use after) should be a "
            f"field; got fields={fields}"
        )
        assert "counter" in fields, (
            f"`counter` (loop counter with yield in body) should be a "
            f"field; got fields={fields}"
        )
        # Non-crossing local stays off the synth class.
        assert "non_crossing" not in fields, (
            f"`non_crossing` (def + use between two yields, no cross) "
            f"should NOT be a field; got fields={fields}"
        )

    def test_generator_local_field_infers_record_construction(self):
        """`r: Bag x: 10 y: 20` as a yield-crossing local promotes to
        a synth-class field of type `Bag`. The desugarer emits a
        `TypeOfExpr` field-type marker; the typechecker resolves
        the actual ZType from the RHS at the first `this.r = ...`
        reassignment in the synth `.call` body."""
        field_t = _resolve_synth_field_type(
            "Bag: record { x: i64 y: i64 }\n"
            "gen: function out (iterator gives: i64) is {\n"
            "    r: Bag x: 10 y: 20\n"
            "    yield 1\n"
            "    yield r.x\n"
            "}",
            synth_name="gen_iter",
            field_name="r",
        )
        assert field_t is not None and field_t.name == "Bag", (
            f"expected `r` resolved to Bag, got {field_t!r}"
        )

    def test_generator_local_field_infers_string_literal(self):
        """Bare `sv: "hello"` -> StringView. The TypeOfExpr-resolution
        path types the string literal RHS as StringView."""
        field_t = _resolve_synth_field_type(
            "gen: function out (iterator gives: i64) is {\n"
            '    sv: "hello"\n'
            "    yield 1\n"
            "    yield sv.length\n"
            "}",
            synth_name="gen_iter",
            field_name="sv",
        )
        assert field_t is not None and field_t.name == "StringView", f"got {field_t!r}"

    def test_generator_local_field_infers_string_projection(self):
        """`s: "hello".string` -> field type String."""
        field_t = _resolve_synth_field_type(
            "gen: function out (iterator gives: i64) is {\n"
            '    s: "hello".string\n'
            "    yield 1\n"
            "    yield s.length\n"
            "}",
            synth_name="gen_iter",
            field_name="s",
        )
        assert field_t is not None and field_t.name == "String", f"got {field_t!r}"

    def test_generator_local_field_infers_method_on_local(self):
        """`bag: Bag x: 10; d: bag.doubled` -> d's field type is the
        method's return type (i32 here), not the default i64."""
        field_t = _resolve_synth_field_type(
            "Bag: record { x: i64 } as {\n"
            "    doubled: function {b: this} out i32 is { return 2.i32 }\n"
            "}\n"
            "gen: function out (iterator gives: i32) is {\n"
            "    bag: Bag x: 10\n"
            "    d: bag.doubled\n"
            "    yield 1.i32\n"
            "    yield d\n"
            "}",
            synth_name="gen_iter",
            field_name="d",
        )
        assert field_t is not None and field_t.name == "i32", f"got {field_t!r}"

    def test_generator_local_field_infers_field_method_via_receiver_param(self):
        """`<recv>.<field>.<method>` in a generator method: the
        typechecker resolves `o.inner.sized` (where `o: this.lock`
        and `Outer.inner: Inner`) to Inner.sized's return type u32."""
        field_t = _resolve_synth_field_type(
            "Inner: record { v: i64 } as {\n"
            "    sized: function {i: this} out u32 is { return 4.u32 }\n"
            "}\n"
            "Outer: class { inner: Inner } as {\n"
            "    each: function {o: this.lock} out (iterator gives: u32) is {\n"
            "        sz: o.inner.sized\n"
            "        yield 0.u32\n"
            "        yield sz\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    o: Outer inner: (Inner v: 1)\n"
            "    with it: (Outer.each o: o.lock) do for v: it loop { }\n"
            "}",
            synth_name="Outer_each_iter",
            field_name="sz",
        )
        assert field_t is not None and field_t.name == "u32", f"got {field_t!r}"

    def test_generator_local_field_infers_binop(self):
        """A promoted local whose first RHS is a BinOp (`c: a + b`)
        resolves through the typechecker's binop type rule. Previously
        fell through to the i64 fallback; now resolves to the
        operands' resolved type (u32 here)."""
        field_t = _resolve_synth_field_type(
            "gen: function {a: u32 b: u32} out (iterator gives: u32) is {\n"
            "    c: a + b\n"
            "    yield 0.u32\n"
            "    yield c\n"
            "}",
            synth_name="gen_iter",
            field_name="c",
        )
        assert field_t is not None and field_t.name == "u32", f"got {field_t!r}"
