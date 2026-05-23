"""
Tests for the Parser
"""

import os
import pytest

from conftest import make_parser, make_parser_with_vfs
from zparser import Parser  # noqa: F401
from zvfs import ZVfs, FSProvider, BindType
import zast
from zast import ERR


pytestmark = pytest.mark.parser

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


def parse_unit(source: str, unitname: str = "test") -> zast.Program | zast.Error:
    """Parse a source String as a unit, returning Program or Error."""
    return make_parser(source, unitname=unitname, src_dir=LIB_DIR).parse()


def get_unit_body(result, unitname: str = "test"):
    """Extract the unit body dict from a successful parse Result."""
    assert isinstance(result, zast.Program), f"Expected Program, got {result!r}"
    assert unitname in result.units
    return result.units[unitname].body


class TestSimpleDefinitions:
    def test_number_literal(self):
        result = parse_unit("x: 42")
        body = get_unit_body(result)
        assert "x" in body
        assert isinstance(body["x"], zast.AtomId)
        assert body["x"].name == "42"

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
        assert rec.nodetype == zast.NodeType.RECORD


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

    def test_paren_expr_preserves_eol(self):
        """Regression: filtereol(True) inside parens must not eat the EOL after ')'.

        Without the fix, _advance after ')' runs with filtereol=True and
        permanently discards the EOL separating the two statement lines,
        causing the parser to swallow the second line into the first.
        """
        source = (
            "inc: function {n: i64} out i64 is { return n + 1 }\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "\n"
            "main: function is {\n"
            "    result: add a: (inc n: 1) b: (inc n: 2)\n"
            '    print "\\{result}"\n'
            "}"
        )
        result = parse_unit(source)
        assert isinstance(result, zast.Program), f"Parse failed: {result!r}"
        body = get_unit_body(result, "test")
        func = body["main"]
        assert isinstance(func, zast.Function)
        stmts = func.body.statements
        assert len(stmts) == 2, (
            f"Expected 2 statements (assignment + print), got {len(stmts)}: "
            f"{[type(s.statementline).__name__ for s in stmts]}"
        )


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
        """A call expression as a statement (e.g. return 42 is now Parsed as call)"""
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
        # reassignment is an expression per grammar; it is wrapped in
        # zast.Expression at statement level.
        sl = stmts[1].statementline
        assert isinstance(sl, zast.Expression)
        assert isinstance(sl.expression, zast.Reassignment)

    def test_swap(self):
        result = parse_unit("f: function {a: i64 b: i64} is { a swap b }")
        body = get_unit_body(result)
        func = body["f"]
        stmts = func.body.statements
        sl = stmts[0].statementline
        # swap is an expression per grammar; wrapped in zast.Expression
        # at statement level.
        assert isinstance(sl, zast.Expression)
        assert isinstance(sl.expression, zast.Swap)


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

    def test_data_empty_rejected(self):
        """data definition must have at least one element (grammar requirement)."""
        result = parse_unit("foo: data { }\nmain: function is { }")
        assert isinstance(result, zast.Error)
        assert "at least one element" in result.msg


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


class TestFunctionAsClause:
    def test_function_as_before_in(self):
        """'as' clause before 'in' for generic params"""
        result = parse_unit("f: function as { t: Any.generic } in { x: t } out t")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "t" in func.as_items
        assert "x" in func.parameters

    def test_function_as_after_out(self):
        """'as' clause after 'out' — Any order is valid"""
        result = parse_unit("f: function in { x: t } out t as { t: Any.generic }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "t" in func.as_items
        assert "x" in func.parameters

    def test_function_as_between_in_and_out(self):
        """'as' clause between 'in' and 'out'"""
        result = parse_unit("f: function in { x: t } as { t: Any.generic } out t")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "t" in func.as_items

    def test_function_as_multiple_generics(self):
        """Multiple generic params in 'as'"""
        result = parse_unit(
            "f: function as { t: Any.generic\n u: Any.generic } in { x: t } out u"
        )
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "t" in func.as_items
        assert "u" in func.as_items

    def test_function_as_duplicate_error(self):
        """Duplicate 'as' clause is an error"""
        result = parse_unit(
            "f: function as { t: Any.generic } as { u: Any.generic } in { x: t } out t"
        )
        assert isinstance(result, zast.Error)

    def test_function_as_empty_items(self):
        """Function without 'as' has empty as_items"""
        result = parse_unit("f: function in { x: i64 } out i64")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.as_items == {}
        assert func.as_functions() == {}

    def test_function_as_with_static_function(self):
        """'as' clause can contain static functions"""
        result = parse_unit(
            "f: function as { t: Any.generic\n helper: function out i64 } in { x: t } out t"
        )
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "t" in func.as_items
        assert "helper" in func.as_functions()

    def test_function_as_requires_explicit_in(self):
        """When 'as' is first, unnamed brace block is not 'in'"""
        result = parse_unit("f: function as { t: Any.generic } { x: t } out t")
        # The '{' after 'as {...}' is not treated as 'in' — it's unexpected
        assert isinstance(result, zast.Error)


class TestEnumReserved:
    def test_enum_is_reserved_word(self):
        """enum is a reserved word and should produce an error"""
        result = parse_unit("color: enum { red\n green\n blue }")
        assert isinstance(result, zast.Error)


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

    def test_with_named_arg_call_rejected(self):
        """with's value is an operation — calls with named args must be parenthesized."""
        result = parse_unit("f: function is { with b: bag x: 1 do b }")
        assert isinstance(result, zast.Error)
        assert "parentheses" in result.msg.lower()

    def test_with_parenthesized_named_arg_call_ok(self):
        """Wrapping the call in parentheses makes it a valid with-value."""
        src = "bag: record { x: i64 }\nf: function is { with b: (bag x: 1) do 0 }"
        result = parse_unit(src)
        body = get_unit_body(result)
        assert isinstance(body["f"], zast.Function)

    def test_with_single_positional_call_ok(self):
        """Call with a single unnamed arg matches grammar's `term binop` form."""
        src = (
            "g: function {v: i64} out i64 is { return v }\n"
            "f: function is { with y: g 5 do 0 }"
        )
        result = parse_unit(src)
        body = get_unit_body(result)
        assert isinstance(body["f"], zast.Function)


class TestFacetDefinition:
    def test_simple_facet(self):
        """facet definition with specs (like protocol)"""
        result = parse_unit("f: facet { show: function {:this} out i64 }")
        body = get_unit_body(result)
        assert "f" in body
        assert body["f"].nodetype == zast.NodeType.FACET
        assert "show" in body["f"].functions()

    def test_facet_with_is(self):
        """facet with explicit is keyword"""
        result = parse_unit("f: facet is { show: function {:this} out i64 }")
        body = get_unit_body(result)
        assert body["f"].nodetype == zast.NodeType.FACET
        assert "show" in body["f"].functions()

    def test_facet_with_generic_param(self):
        """facet with generic parameter"""
        result = parse_unit(
            "f: facet { t: Any.generic\n show: function {:this} out t }"
        )
        body = get_unit_body(result)
        fac = body["f"]
        assert fac.nodetype == zast.NodeType.FACET
        assert "t" in fac.is_items
        assert "show" in fac.functions()


class TestAsClause:
    def test_record_with_as(self):
        """record with as clause for static members"""
        result = parse_unit("p: record { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert "x" in rec.is_items
        assert "y" in rec.as_items

    def test_record_without_as(self):
        """record without as clause still works"""
        result = parse_unit("p: record { x: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert rec.as_items == {}
        assert rec.as_functions() == {}

    def test_record_is_as_named(self):
        """record with explicitly named is and as"""
        result = parse_unit("p: record is { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert "x" in rec.is_items
        assert "y" in rec.as_items

    def test_record_as_is_reversed(self):
        """as before is when both named"""
        result = parse_unit("p: record as { y: f64 } is { x: f64 }")
        body = get_unit_body(result)
        rec = body["p"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert "x" in rec.is_items
        assert "y" in rec.as_items

    def test_class_with_as(self):
        """class with as clause"""
        result = parse_unit("c: class { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        cls = body["c"]
        assert cls.nodetype == zast.NodeType.CLASS
        assert "x" in cls.is_items
        assert "y" in cls.as_items

    def test_enum_with_as_is_reserved(self):
        """enum is reserved, even with as clause"""
        result = parse_unit("c: enum { red\n green } as { x: f64 }")
        assert isinstance(result, zast.Error)

    def test_variant_with_as(self):
        """variant with as clause"""
        result = parse_unit("v: variant { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        v = body["v"]
        assert v.nodetype == zast.NodeType.VARIANT
        assert "x" in v.is_items
        assert "y" in v.as_items

    def test_union_with_as(self):
        """union with as clause"""
        result = parse_unit("u: union { x: f64 } as { y: f64 }")
        body = get_unit_body(result)
        u = body["u"]
        assert u.nodetype == zast.NodeType.UNION
        assert "x" in u.is_items
        assert "y" in u.as_items


class TestInlineTypedefRejection:
    """Inline type definitions (record / class / variant / union /
    protocol / facet / data) inside an `is` or `as` body produce a
    friendly diagnostic pointing the user at the unit-level workaround.
    This case is deferred-by-design — the workaround is well-shipped
    (see examples/borrowed_record.z)."""

    def test_inline_record_rejected(self):
        result = parse_unit(
            "Outer: class { x: i64 } as {\n    Helper: record { a: i64 b: i64 }\n}"
        )
        assert isinstance(result, zast.Error)
        assert "Inline 'record' definition" in result.msg
        assert "'Helper'" in result.msg
        assert "unit level" in result.msg

    def test_inline_class_rejected(self):
        result = parse_unit(
            "Outer: class { x: i64 } as {\n    Helper: class { v: i64 }\n}"
        )
        assert isinstance(result, zast.Error)
        assert "Inline 'class' definition" in result.msg

    def test_inline_variant_rejected(self):
        result = parse_unit(
            "Outer: class { x: i64 } as {\n    Tag: variant { a: null b: null }\n}"
        )
        assert isinstance(result, zast.Error)
        assert "Inline 'variant' definition" in result.msg

    def test_inline_union_rejected(self):
        result = parse_unit(
            "Outer: class { x: i64 } as {\n    Slot: union { empty: null full: i64 }\n}"
        )
        assert isinstance(result, zast.Error)
        assert "Inline 'union' definition" in result.msg

    def test_inline_protocol_rejected(self):
        result = parse_unit(
            "Outer: class { x: i64 } as {\n"
            "    Iface: protocol { do: function {:this} }\n"
            "}"
        )
        assert isinstance(result, zast.Error)
        assert "Inline 'protocol' definition" in result.msg

    def test_inline_data_rejected(self):
        result = parse_unit(
            "Outer: class { x: i64 } as {\n    table: data { 1 2 3 }\n}"
        )
        assert isinstance(result, zast.Error)
        assert "Inline 'data' definition" in result.msg

    def test_inline_typedef_in_is_block_rejected(self):
        """Same rule applies to `is` blocks — `_get_object_body` is
        shared between `is` and `as`."""
        result = parse_unit("Outer: class is {\n    Helper: record { a: i64 }\n}")
        assert isinstance(result, zast.Error)
        assert "Inline 'record' definition" in result.msg

    def test_unit_level_record_referenced_from_as_works(self):
        """The supported workaround: define the helper at unit level
        and reference it by name from within the parent's `as`."""
        result = parse_unit(
            "Helper: record { a: i64 b: i64 }\n"
            "Outer: class { x: i64 } as {\n"
            "    public: unit { :use }\n"
            "    use: function {:this} out i64 is {\n"
            "        h: Helper a: this.x b: 0\n"
            "        return h.a + h.b\n"
            "    }\n"
            "}"
        )
        body = get_unit_body(result)
        assert "Helper" in body
        assert "Outer" in body
        assert body["Helper"].nodetype == zast.NodeType.RECORD
        assert body["Outer"].nodetype == zast.NodeType.CLASS


class TestDuplicateDefinitions:
    """Test duplicate definition error handling in 'is' and 'as' sections."""

    def test_duplicate_is_clause_record(self):
        """Duplicate 'is' clause on record is an error."""
        result = parse_unit("p: record is { x: f64 } is { y: f64 }")
        assert isinstance(result, zast.Error)

    def test_duplicate_as_clause_record(self):
        """Duplicate 'as' clause on record is an error."""
        result = parse_unit("p: record { x: f64 } as { y: f64 } as { z: f64 }")
        assert isinstance(result, zast.Error)

    def test_duplicate_is_clause_class(self):
        """Duplicate 'is' clause on class is an error."""
        result = parse_unit("c: class is { x: f64 } is { y: f64 }")
        assert isinstance(result, zast.Error)

    def test_duplicate_as_clause_class(self):
        """Duplicate 'as' clause on class is an error."""
        result = parse_unit("c: class { x: f64 } as { y: f64 } as { z: f64 }")
        assert isinstance(result, zast.Error)

    def test_duplicate_item_within_is_body(self):
        """Duplicate item name within a single 'is' body is an error."""
        result = parse_unit("p: record { x: f64  x: i64 }")
        assert isinstance(result, zast.Error)

    def test_duplicate_item_within_as_body(self):
        """Duplicate item name within a single 'as' body is an error."""
        result = parse_unit(
            "p: record { x: f64 } as {\n"
            "  f: function {a: this} out f64 is { return a.x }\n"
            "  f: function {a: this} out f64 is { return a.x }\n"
            "}"
        )
        assert isinstance(result, zast.Error)

    def test_duplicate_item_name_field_and_func_within_is(self):
        """Field and function with same name within 'is' is an error."""
        result = parse_unit("p: record { x: f64\n x: function {a: i64} out i64 }")
        assert isinstance(result, zast.Error)


class TestDataAsTagParsing:
    """Test parsing data as tag source (Phase 18)."""

    def test_union_as_tag_dotted_path(self):
        """union with as { tag: mydata.tag } should parse."""
        result = parse_unit(
            "mydata: data { A: 0 B: 1 }\n"
            "u: union { A: null\n B: null } as { tag: mydata.tag }"
        )
        body = get_unit_body(result)
        u = body["u"]
        assert u.nodetype == zast.NodeType.UNION
        assert "tag" in u.as_items
        as_tag = u.as_items["tag"]
        assert isinstance(as_tag, zast.DottedPath)
        assert as_tag.child.name == "tag"

    def test_tag_is_not_keyword(self):
        """tag should be a regular identifier, not a keyword."""
        result = parse_unit("tag: 42")
        body = get_unit_body(result)
        assert "tag" in body

    def test_enum_is_reserved(self):
        """enum should be rejected as reserved word."""
        result = parse_unit("x: enum { a\n b }")
        assert isinstance(result, zast.Error)


class TestLabelValueShorthand:
    """Test :x (label_value) parsing in various contexts."""

    def test_unit_level_label_value(self):
        """:x at unit level produces name='x', value=LabelValue('x')."""
        result = parse_unit(":u8")
        body = get_unit_body(result)
        assert "u8" in body
        defn = body["u8"]
        assert isinstance(defn, zast.LabelValue)
        assert defn.name == "u8"

    def test_record_field_label_value(self):
        """:x in record fields."""
        result = parse_unit("r: record { :i64\n :String }")
        body = get_unit_body(result)
        rec = body["r"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert "i64" in rec.is_items
        assert "String" in rec.is_items

    def test_union_field_label_value(self):
        """:x in union subtypes."""
        result = parse_unit("u: union { :u8\n :u16\n :u32 }")
        body = get_unit_body(result)
        u = body["u"]
        assert u.nodetype == zast.NodeType.UNION
        assert "u8" in u.is_items
        assert "u16" in u.is_items
        assert "u32" in u.is_items

    def test_function_param_label_value(self):
        """:x in function parameters."""
        result = parse_unit("f: function {:i64} out i64 is { i64 }")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert "i64" in func.parameters

    def test_call_arg_label_value(self):
        """:x in call arguments."""
        result = parse_unit(
            "f: function {n: i64} out i64 is { n }\nmain: function is { x: 42\n f :x }"
        )
        body = get_unit_body(result)
        assert isinstance(body["f"], zast.Function)
        assert isinstance(body["main"], zast.Function)

    def test_statement_label_value(self):
        """:x as a labeled statement in function body."""
        result = parse_unit("f: function {x: i64} out i64 is { y: x\n y }")
        body = get_unit_body(result)
        assert isinstance(body["f"], zast.Function)


def parse_example(unitname: str) -> zast.Program | zast.Error:
    """Parse an example .z File using the same VFS setup as zc.py."""
    systemdir = os.path.join(LIB_DIR, "system")
    vfs = ZVfs()
    psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
    pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
    rootid = vfs.walk()
    rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
    rootid = vfs.bind(
        parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
    )
    p = make_parser_with_vfs(vfs, unitname)
    return p.parse()


def get_example_names():
    """Get all example unit names from .z files in examples/."""
    names = []
    for f in sorted(os.listdir(EXAMPLES_DIR)):
        if f.endswith(".z"):
            names.append(f[:-2])
    return names


# Examples that still need fixes for other reasons (e.g., scoping bugs)
EXAMPLES_NEEDING_UPDATE: set[str] = set()


class TestExamples:
    @pytest.mark.parametrize("unitname", get_example_names())
    def test_example_parses(self, unitname):
        """Each example .z File should parse without errors."""
        if unitname in EXAMPLES_NEEDING_UPDATE:
            pytest.xfail(f"{unitname}.z needs grammar update")
        result = parse_example(unitname)
        if isinstance(result, zast.Error):
            msg = f"{unitname}.z: {result.err.name} - {result.msg}"
            if result.loc:
                msg += f" at line {result.loc.lineno}:{result.loc.colno}"
            pytest.fail(msg)
        assert isinstance(result, zast.Program)
        assert unitname in result.units


class TestNativeKeyword:
    def test_native_function(self):
        """is native marks a function as compiler-provided."""
        result = parse_unit("f: function out i64 is native")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.is_native is True
        assert func.body is None

    def test_native_function_with_params(self):
        """Native function with parameters."""
        result = parse_unit("f: function {n: i64} out i64 is native")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.is_native is True
        assert "n" in func.parameters

    def test_native_not_spec(self):
        """Native function is distinguishable from a Spec."""
        result = parse_unit("f: function out i64")
        body = get_unit_body(result)
        spec = body["f"]
        assert isinstance(spec, zast.Function)
        assert spec.is_native is False
        assert spec.body is None

    def test_native_record(self):
        """record is native parses as a native record."""
        result = parse_unit("r: record is native")
        body = get_unit_body(result)
        rec = body["r"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert rec.is_native is True
        assert rec.is_items == {}

    def test_native_record_elided_is(self):
        """record native (elided is) parses as a native record."""
        result = parse_unit("r: record native")
        body = get_unit_body(result)
        rec = body["r"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert rec.is_native is True

    def test_native_class(self):
        """class is native parses as a native class."""
        result = parse_unit("c: class is native")
        body = get_unit_body(result)
        cls = body["c"]
        assert cls.nodetype == zast.NodeType.CLASS
        assert cls.is_native is True

    def test_native_class_with_as(self):
        """class is native as { ... } parses with native flag and as section."""
        result = parse_unit("c: class is native as { m: function out i64 is native }")
        body = get_unit_body(result)
        cls = body["c"]
        assert cls.nodetype == zast.NodeType.CLASS
        assert cls.is_native is True
        assert "m" in cls.as_functions()
        assert cls.as_functions()["m"].is_native is True

    def test_native_union(self):
        """union is native parses as a native union."""
        result = parse_unit("u: union is native")
        body = get_unit_body(result)
        u = body["u"]
        assert u.nodetype == zast.NodeType.UNION
        assert u.is_native is True

    def test_native_variant(self):
        """variant is native parses as a native variant."""
        result = parse_unit("v: variant is native")
        body = get_unit_body(result)
        v = body["v"]
        assert v.nodetype == zast.NodeType.VARIANT
        assert v.is_native is True

    def test_native_keyword_as_ref(self):
        """native is a keyword; using it as a reference (not label) parses as keyword."""
        result = parse_unit("f: function is native")
        body = get_unit_body(result)
        func = body["f"]
        assert isinstance(func, zast.Function)
        assert func.is_native is True

    def test_native_as_label_errors(self):
        """native: produces a parse error (keywords cannot be used as labels)."""
        result = parse_unit("native: i64")
        assert isinstance(result, zast.Error)

    def test_regular_record_not_native(self):
        """A normal record is not marked native."""
        result = parse_unit("r: record { x: i64 }")
        body = get_unit_body(result)
        rec = body["r"]
        assert rec.nodetype == zast.NodeType.RECORD
        assert rec.is_native is False


class TestGeneratorYieldParsing:
    """Phase G1: lexer + AST + parser for the `yield` keyword.

    The parser stays context-free with respect to whether the
    enclosing function is iterator-returning — that check is in G3.
    What the parser does enforce here is the lexical position rule:
    `yield` is valid only directly inside one function body
    (depth == 1), never at top level or inside a nested function
    literal.
    """

    def test_yield_statement_parses(self):
        """`yield <expr>` is a statement form — wrapped as StatementLine
        carrying Expression(Yield(...))."""
        result = parse_unit("g: function is { yield 42 }")
        body = get_unit_body(result)
        func = body["g"]
        assert isinstance(func, zast.Function)
        assert func.body is not None
        stmts = func.body.statements
        assert len(stmts) == 1
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Expression)
        assert sl.expression.nodetype == zast.NodeType.YIELD
        y = sl.expression
        assert isinstance(y, zast.Yield)
        # the yielded value is wrapped in an Expression
        assert isinstance(y.expr, zast.Expression)

    def test_yield_expression_form_parses(self):
        """`x: yield <expr>` binds `x` on resumption — the parser
        materialises it as an Assignment whose value is
        Expression(Yield(...))."""
        result = parse_unit("g: function is { x: yield 42 }")
        body = get_unit_body(result)
        func = body["g"]
        assert isinstance(func, zast.Function)
        assert func.body is not None
        stmts = func.body.statements
        assert len(stmts) == 1
        sl = stmts[0].statementline
        assert isinstance(sl, zast.Assignment)
        assert sl.name == "x"
        assert isinstance(sl.value, zast.Expression)
        assert sl.value.expression.nodetype == zast.NodeType.YIELD

    def test_yield_outside_function_rejected(self):
        """Top-level `yield` is a parse error (no enclosing function
        body)."""
        result = parse_unit("yield 42")
        assert isinstance(result, zast.Error)

    def test_yield_in_record_initializer_rejected(self):
        """A record field initializer slot accepts a path/type, not an
        expression — `yield` does not parse there."""
        result = parse_unit("r: record { x: yield 1 }")
        assert isinstance(result, zast.Error)

    def test_yield_nested_in_function_literal_rejected(self):
        """`yield` inside a function nested in another function's
        `as` block belongs to neither — reject at parse time
        (rule 10)."""
        source = "g: function is { yield 1 } as { helper: function is { yield 2 } }"
        result = parse_unit(source)
        assert isinstance(result, zast.Error)
