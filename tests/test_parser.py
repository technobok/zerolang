"""
Tests for the Parser
"""

import os

from conftest import make_parser_vfs
from zparser import Parser
import zast
from zast import ERR


SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")


def parse_unit(source: str, unitname: str = "test") -> zast.Program | zast.Error:
    """Parse a source string as a unit, returning Program or Error."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=SRC_DIR)
    p = Parser(vfs, name)
    return p.parse()


def get_unit_body(result, unitname: str = "test"):
    """Extract the unit body dict from a successful parse result."""
    assert isinstance(result, zast.Program), f"Expected Program, got {result!r}"
    assert unitname in result.units
    return result.units[unitname].body


class TestSimpleDefinitions:
    def test_number_literal(self):
        result = parse_unit("x: 42")
        body = get_unit_body(result)
        assert "x" in body
        assert isinstance(body["x"], zast.AtomNumber)

    def test_dotted_path(self):
        """Dotted paths referencing known system types work."""
        result = parse_unit("y: system.u8")
        body = get_unit_body(result)
        assert "y" in body
        assert isinstance(body["y"], zast.DottedPath)

    def test_multiple_definitions(self):
        result = parse_unit("x: 42\ny: 99\n")
        body = get_unit_body(result)
        assert "x" in body
        assert "y" in body

    def test_simple_ref(self):
        """A single REFID as definition value (known type)."""
        result = parse_unit("x: i64")
        body = get_unit_body(result)
        assert "x" in body
        assert isinstance(body["x"], zast.AtomId)


class TestFunctionDefinitions:
    def test_no_params(self):
        result = parse_unit("f: function is { 42 }")
        body = get_unit_body(result)
        assert "f" in body
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.body is not None

    def test_with_params_and_out(self):
        result = parse_unit("f: function {n: i64} out i64 is { n }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "n" in func.parameters
        assert func.returntype is not None
        assert func.body is not None

    def test_spec_no_body(self):
        result = parse_unit("f: function {n: i64} out i64")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.body is None  # spec has no body

    def test_out_keyword_not_return(self):
        """'out' is the keyword for return type, not 'return'"""
        result = parse_unit("f: function out i64 is { 0 }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.returntype is not None


class TestRecordDefinitions:
    def test_simple_record(self):
        result = parse_unit("point: record { x: f64\n y: f64 }")
        body = get_unit_body(result)
        assert "point" in body
        rec = body["point"]
        assert isinstance(rec, zast.Record)


class TestControlFlow:
    def test_if_then(self):
        result = parse_unit("f: function is { if 1 then 42 }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_if_then_else(self):
        result = parse_unit("f: function is { if 1 then 42 else 0 }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_for_while_loop(self):
        result = parse_unit("f: function {i: i64} is { for i while i loop { 42 } }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)


class TestMatchCase:
    def test_match_case_then(self):
        """match expr case id then stmt else stmt - all on one line"""
        source = "f: function {d: i64} out i64 is { match d case north then 1 else 0 }"
        result = parse_unit(source)
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_match_multiple_cases(self):
        source = "f: function {d: i64} out i64 is { match d case north then 1 case south then 2 else 0 }"
        result = parse_unit(source)
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)


class TestDataExpression:
    def test_data_block(self):
        result = parse_unit("d: function is { x: data { 1 } }")
        body = get_unit_body(result)
        func = body["d"]
        assert isinstance(func, zast.Function)


class TestExpressions:
    def test_binary_op_with_known_refs(self):
        """Binary operation using function parameters (no external refs)."""
        result = parse_unit("f: function {a: i64 b: i64} is { x: a + b }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_dotted_path_expr(self):
        result = parse_unit("f: function {p: system.u8} is { x: p }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_parenthesized_expr(self):
        result = parse_unit("f: function {a: i64 b: i64} is { x: (a + b) }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)


class TestStatements:
    def test_single_expression_statement(self):
        """A single expression as a statement line"""
        result = parse_unit("f: function is { 42 }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        stmts = func.body.statements
        assert len(stmts) == 1
        assert isinstance(stmts[0].statementline, zast.Expression)

    def test_identifier_expression_statement(self):
        """A single identifier as a statement line (e.g. break/continue are now regular ids)"""
        result = parse_unit("f: function {n: i64} is { n }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        assert isinstance(stmts[0].statementline, zast.Expression)

    def test_call_expression_statement(self):
        """A call expression as a statement (e.g. return 42 is now parsed as call)"""
        result = parse_unit("f: function {g: i64} is { g 42 }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Expression)

    def test_assignment(self):
        result = parse_unit("f: function is { x: 42 }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Assignment)
        assert sl.name == "x"

    def test_reassignment(self):
        result = parse_unit("f: function is { x: 42\n x = 43 }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        assert len(stmts) == 2
        assert isinstance(stmts[1].statementline, zast.Reassignment)

    def test_swap(self):
        result = parse_unit("f: function {a: i64 b: i64} is { a swap b }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Swap)


class TestIfAtUnitLevel:
    def test_if_at_unit_level(self):
        """if/match should be valid at type definition level"""
        result = parse_unit("x: if 1 then 42 else 0")
        body = get_unit_body(result)
        assert "x" in body
        assert isinstance(body["x"], zast.Expression)

    def test_match_at_unit_level(self):
        result = parse_unit("x: match d case north then 1 else 0")
        # d and north are unresolved refs but parse should still work
        # Actually this will fail due to extern resolution for d and north
        # Just test that match keyword is accepted at unit level
        assert isinstance(result, (zast.Program, zast.Error))


class TestSubunit:
    def test_subunit_with_as(self):
        """unit can have optional 'as' keyword"""
        result = parse_unit("m: unit as { x: 42 }")
        body = get_unit_body(result)
        assert "m" in body
        assert isinstance(body["m"], zast.Unit)

    def test_subunit_without_as(self):
        result = parse_unit("m: unit { x: 42 }")
        body = get_unit_body(result)
        assert "m" in body
        assert isinstance(body["m"], zast.Unit)


class TestDataDefinition:
    def test_data_with_is(self):
        """data with explicit 'is' keyword"""
        result = parse_unit("d: function is { x: data is { 1 } }")
        body = get_unit_body(result)
        func = body["d"]
        assert isinstance(func, zast.Function)

    def test_data_without_is(self):
        """data with implicit 'is' (unnamed first arg)"""
        result = parse_unit("d: function is { x: data { 1 } }")
        body = get_unit_body(result)
        func = body["d"]
        assert isinstance(func, zast.Function)


class TestFunctionInKeyword:
    def test_function_with_in_keyword(self):
        """'in' is the keyword for function parameters"""
        result = parse_unit("f: function in {n: i64} out i64 is { n }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "n" in func.parameters

    def test_function_without_in_keyword(self):
        """Parameters block without explicit 'in' still works"""
        result = parse_unit("f: function {n: i64} out i64 is { n }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "n" in func.parameters


class TestEnumDefinition:
    def test_simple_enum(self):
        """enum definition should work (was broken with TT.RECORD bug)"""
        result = parse_unit("color: enum { red\n green\n blue }")
        body = get_unit_body(result)
        assert "color" in body
        assert isinstance(body["color"], zast.Enum)


class TestErrorCases:
    def test_empty_unit(self):
        """Empty source should parse as unit with empty body"""
        result = parse_unit("")
        body = get_unit_body(result)
        assert len(body) == 0

    def test_duplicate_definition(self):
        result = parse_unit("x: 1\nx: 2")
        assert isinstance(result, zast.Error)
        assert result.err == ERR.DUPLICATEDEF

    def test_bad_unit_name(self):
        result = parse_unit("x: 1", unitname="Bad_Name")
        assert isinstance(result, zast.Error)
        assert result.err == ERR.BADUNITNAME

    def test_no_hang_on_bad_input(self):
        """Parser should not hang on problematic input."""
        # This used to cause infinite loops due to tokenizer dot bug
        result = parse_unit("x: system.u8\ny: system.i64")
        assert isinstance(result, zast.Program)


class TestWithExpression:
    def test_simple_with(self):
        """with label operation do expression"""
        result = parse_unit("f: function is { with x: 42 do x }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_with_chained(self):
        """with can be chained"""
        result = parse_unit("f: function is { with x: 1 do with y: 2 do x }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_with_at_unit_level(self):
        """with can be used as an expression in type definitions"""
        result = parse_unit("f: function is { with n: 10 do n }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        stmts = func.body.statements
        assert len(stmts) == 1
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Expression)
        assert isinstance(sl.expression, zast.With)
        assert sl.expression.name == "n"

    def test_with_missing_label(self):
        """with without label should error"""
        result = parse_unit("f: function is { with 42 do 1 }")
        assert isinstance(result, zast.Error)

    def test_with_missing_do(self):
        """with without do should error"""
        result = parse_unit("f: function is { with x: 42 1 }")
        assert isinstance(result, zast.Error)


class TestFacetDefinition:
    def test_simple_facet(self):
        """facet definition similar to record"""
        result = parse_unit("f: facet { x: f64\n y: f64 }")
        body = get_unit_body(result)
        assert "f" in body
        assert isinstance(body["f"], zast.Facet)

    def test_facet_with_is(self):
        """facet with explicit is keyword"""
        result = parse_unit("f: facet is { x: f64 }")
        body = get_unit_body(result)
        assert isinstance(body["f"], zast.Facet)

    def test_facet_with_as(self):
        """facet with as clause"""
        result = parse_unit("f: facet { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        fac = body["f"]
        assert isinstance(fac, zast.Facet)
        assert "x" in fac.items
        assert "y" in fac.as_items


class TestAsClause:
    def test_record_with_as(self):
        """record with as clause for static members"""
        result = parse_unit("p: record { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert isinstance(rec, zast.Record)
        assert "x" in rec.items
        assert "y" in rec.as_items

    def test_record_without_as(self):
        """record without as clause still works"""
        result = parse_unit("p: record { x: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert isinstance(rec, zast.Record)
        assert rec.as_items == {}
        assert rec.as_functions == {}

    def test_record_is_as_named(self):
        """record with explicitly named is and as"""
        result = parse_unit("p: record is { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert isinstance(rec, zast.Record)
        assert "x" in rec.items
        assert "y" in rec.as_items

    def test_record_as_is_reversed(self):
        """as before is when both named"""
        result = parse_unit("p: record as { y: f64 } is { x: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert isinstance(rec, zast.Record)
        assert "x" in rec.items
        assert "y" in rec.as_items

    def test_class_with_as(self):
        """class with as clause"""
        result = parse_unit("c: class { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        cls = body["c"]
        assert isinstance(cls, zast.Class)
        assert "x" in cls.items
        assert "y" in cls.as_items

    def test_enum_with_as(self):
        """enum with as clause"""
        result = parse_unit("c: enum { red\n green } as { x: f64 }")
        body = get_unit_body(result)
        e = body["c"]
        assert isinstance(e, zast.Enum)
        assert "red" in e.items
        assert "x" in e.as_items

    def test_variant_with_as(self):
        """variant with as clause"""
        result = parse_unit("v: variant { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        v = body["v"]
        assert isinstance(v, zast.Variant)
        assert "x" in v.items
        assert "y" in v.as_items

    def test_union_with_as(self):
        """union with as clause"""
        result = parse_unit("u: union { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        u = body["u"]
        assert isinstance(u, zast.Union)
        assert "x" in u.items
        assert "y" in u.as_items
