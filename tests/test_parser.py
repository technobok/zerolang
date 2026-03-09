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
        result = parse_unit("f: function is { break }")
        body = get_unit_body(result)
        assert "f" in body
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.body is not None

    def test_with_params_and_out(self):
        result = parse_unit("f: function {n: i64} out i64 is { return n }")
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
        result = parse_unit("f: function out i64 is { return 0 }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.returntype is not None

    def test_yield(self):
        result = parse_unit("f: function yield i64 is { break }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.yieldtype is not None


class TestRecordDefinitions:
    def test_simple_record(self):
        result = parse_unit("point: record { x: f64\n y: f64 }")
        body = get_unit_body(result)
        assert "point" in body
        rec = body["point"]
        assert isinstance(rec, zast.Record)


class TestControlFlow:
    def test_if_then(self):
        result = parse_unit("f: function is { if 1 then break }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_if_then_else(self):
        result = parse_unit("f: function is { if 1 then break else continue }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_for_while_loop(self):
        result = parse_unit("f: function {i: i64} is { for i while i loop { break } }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)


class TestMatchCase:
    def test_match_case_then(self):
        """match expr case id then stmt else stmt - all on one line"""
        source = "f: function {d: i64} out i64 is { match d case north then return 1 else return 0 }"
        result = parse_unit(source)
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)

    def test_match_multiple_cases(self):
        source = "f: function {d: i64} out i64 is { match d case north then return 1 case south then return 2 else return 0 }"
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
    def test_break(self):
        result = parse_unit("f: function is { break }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        stmts = func.body.statements
        assert len(stmts) == 1
        assert isinstance(stmts[0].statementline, zast.Break)

    def test_continue(self):
        result = parse_unit("f: function is { continue }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        assert isinstance(stmts[0].statementline, zast.Continue)

    def test_return_expr(self):
        result = parse_unit("f: function is { return 42 }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Return)
        assert sl.expression is not None

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
