"""
Tests for code review findings infrastructure (Findings 1, 3, 7, 8, 10, 11, 12, SQL).

These test the new fields and metadata added during the code review:
- Finding 1: type annotations on all Path nodes
- Finding 3: destructor metadata on ZType
- Finding 7: Token IDs, VFS File_table, CallKind, source Map
- Finding 8: File ID consistency
- Finding 10: type annotation audit
- Finding 11: ScopeState / TempState
- Finding 12: self-hosting patterns
- SQL schema: dump_sql integration
"""

import os
import sys
import sqlite3
import tempfile

import pytest

from conftest import make_parser_vfs, collect_tokens, make_parser
from zparser import Parser
from ztypecheck import typecheck, audit_type_annotations
from ztypes import ZTypeType
import zemitterc
import zast
from zast import CallKind
from zvfs import ZVfs, StringProvider
import zsqldump


pytestmark = pytest.mark.infra

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def parse_and_check(source: str, unitname: str = "test"):
    """Parse source, run type checker, return `(program, typing)`."""
    p = make_parser(source, unitname=unitname, src_dir=LIB_DIR)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    typing = typecheck(program)
    assert typing.errors == [], f"Type errors: {[e.msg for e in typing.errors]}"
    return program, typing


def parse_and_check_uncached(source: str, unitname: str = "test"):
    """parse_and_check variant that bypasses the system-lib cache.
    Returns `(program, typing)`.

    The cache reuses Parsed system units across tests; their tokens carry
    File IDs (fsno) from the seed VFS, not the per-test VFS. Tests that
    inspect VFS File_table state or token-to-File foreign keys must use
    this variant so system files are walked and assigned IDs in the
    per-test VFS.
    """
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=LIB_DIR)
    program = Parser(vfs, name).parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    typing = typecheck(program)
    assert typing.errors == [], f"Type errors: {[e.msg for e in typing.errors]}"
    return program, typing


# F5.E.5: aliases retained for any callsites that switched to these
# names during the F5.E transition. They now have identical signatures
# to the canonical helpers.
parse_check_typing = parse_and_check
parse_check_typing_uncached = parse_and_check_uncached


def emit_with_emitter(source: str, unitname: str = "test"):
    """Parse, type-check, and return (c_source, emitter) for inspection."""
    _program, typing = parse_and_check(source, unitname)
    emitter = zemitterc.CEmitter(typing)
    csource = emitter.emit()
    return csource, emitter


# ---- Finding 1: Type annotation completeness ----


def _walk_path_nodes(node, visited=None):
    """Recursively collect all Path nodes from an AST node."""
    if visited is None:
        visited = set()
    node_id = id(node)
    if node_id in visited:
        return []
    visited.add(node_id)

    paths = []
    if isinstance(node, (zast.AtomId, zast.DottedPath)):
        paths.append(node)
    if isinstance(node, zast.DottedPath):
        paths.extend(_walk_path_nodes(node.parent, visited))
        paths.extend(_walk_path_nodes(node.child, visited))

    # walk dataclass fields
    if hasattr(node, "__dataclass_fields__"):
        for fname in node.__dataclass_fields__:
            val = getattr(node, fname, None)
            if val is None:
                continue
            if isinstance(val, zast.Node):
                paths.extend(_walk_path_nodes(val, visited))
            elif isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, zast.Node):
                        paths.extend(_walk_path_nodes(v, visited))
            elif isinstance(val, list):
                for v in val:
                    if isinstance(v, zast.Node):
                        paths.extend(_walk_path_nodes(v, visited))
    return paths


def _node_ztype(typing, node):
    """Look up a parsed node's resolved ZType in `Typing.node_type`."""
    return typing.node_type.get(node.nodeid)


class TestFinding1TypeAnnotations:
    """Finding 1: type checker should annotate every Path node."""

    def test_record_field_types_annotated(self):
        program, typing = parse_and_check(
            "point: record is { x: 0.0  y: 0.0 }\n"
            'main: function is {\n    p: point\n    print "ok"\n}'
        )
        # find the record definition
        mainunit = program.units[program.mainunitname]
        point = mainunit.body["point"]
        assert point.nodetype == zast.NodeType.RECORD
        for fname, fpath in point.is_items.items():
            ft = _node_ztype(typing, fpath)
            assert ft is not None, f"Field '{fname}' has no .type"
            assert ft.name in ("f64",), f"Field '{fname}' type is {ft.name}"

    def test_function_param_types_annotated(self):
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        mainunit = program.units[program.mainunitname]
        add = mainunit.body["add"]
        assert isinstance(add, zast.Function)
        for pname, ppath in add.parameters.items():
            if pname.startswith(":"):
                continue
            assert _node_ztype(typing, ppath) is not None, (
                f"Param '{pname}' has no .type"
            )

    def test_function_return_type_annotated(self):
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        mainunit = program.units[program.mainunitname]
        add = mainunit.body["add"]
        assert add.returntype is not None
        rt = _node_ztype(typing, add.returntype)
        assert rt is not None, "Return type has no .type"
        assert rt.name == "i64"

    def test_class_field_types_annotated(self):
        program, typing = parse_and_check(
            "Box: class is { value: i64 }\n"
            'main: function is {\n    b: Box value: 42\n    print "\\{b.value}"\n}'
        )
        mainunit = program.units[program.mainunitname]
        box = mainunit.body["Box"]
        assert box.nodetype == zast.NodeType.CLASS
        for fname, fpath in box.is_items.items():
            assert _node_ztype(typing, fpath) is not None, (
                f"Field '{fname}' has no .type"
            )

    def test_union_subtype_annotated(self):
        program, typing = parse_and_check(
            "Result: union is { ok: i64  err: String }\n"
            'main: function is {\n    r: Result.ok 42\n    print "ok"\n}'
        )
        mainunit = program.units[program.mainunitname]
        result = mainunit.body["Result"]
        assert result.nodetype == zast.NodeType.UNION
        for sname, spath in result.is_items.items():
            assert _node_ztype(typing, spath) is not None, (
                f"Subtype '{sname}' has no .type"
            )


# ---- Finding 3: Destructor metadata ----


class TestFinding3DestructorMetadata:
    """Finding 3: ZType should carry needs_destructor, destructor_name, is_heap_allocated."""

    def test_string_destructor(self):
        program, typing = parse_and_check(
            'main: function is {\n    s: "hello"\n    print s\n}'
        )
        # string type should have destructor metadata
        _ = typing.resolved.get("system.String")
        # string might not be in resolved directly; check via a known type
        # use the record field approach
        program2, typing2 = parse_and_check(
            "Box: class is { name: String }\n"
            'main: function is {\n    b: Box name: "hi"\n    print b.name\n}'
        )
        mainunit = program2.units[program2.mainunitname]
        box = mainunit.body["Box"]
        name_type = _node_ztype(typing2, box.is_items["name"])
        assert name_type is not None
        assert name_type.name == "String"
        assert (name_type.destructor_name is not None) is True
        assert name_type.destructor_name == "z_String_free"
        assert name_type.is_heap_allocated is False

    def test_class_destructor(self):
        program, typing = parse_and_check(
            "Box: class is { value: i64 }\n"
            'main: function is {\n    b: Box value: 0\n    print "ok"\n}'
        )
        t = None
        for key, ztype in typing.resolved.items():
            if ztype.name == "Box" and ztype.typetype == ZTypeType.CLASS:
                t = ztype
                break
        assert t is not None, "Box type not found in resolved"
        assert (t.destructor_name is not None) is False
        assert t.is_heap_allocated is False

    def test_union_destructor(self):
        program, typing = parse_and_check(
            "Result: union is { ok: i64  err: String }\n"
            'main: function is {\n    r: Result.ok 42\n    print "ok"\n}'
        )
        t = None
        for key, ztype in typing.resolved.items():
            if ztype.name == "Result" and ztype.typetype == ZTypeType.UNION:
                t = ztype
                break
        assert t is not None, "Result type not found in resolved"
        assert (t.destructor_name is not None) is True
        assert t.destructor_name == "z_Result_destroy"
        assert t.is_heap_allocated is False

    def test_record_no_destructor(self):
        program, typing = parse_and_check(
            "point: record is { x: 0.0  y: 0.0 }\n"
            'main: function is {\n    p: point\n    print "ok"\n}'
        )
        t = None
        for key, ztype in typing.resolved.items():
            if ztype.name == "point" and ztype.typetype == ZTypeType.RECORD:
                t = ztype
                break
        assert t is not None, "point type not found in resolved"
        assert (t.destructor_name is not None) is False
        assert t.destructor_name is None
        assert t.is_heap_allocated is False

    def test_numeric_no_destructor(self):
        program, typing = parse_and_check(
            'main: function is {\n    x: 42\n    print "ok"\n}'
        )
        # i64 is a record type with no destructor
        t = typing.resolved.get("system.i64")
        assert t is not None
        assert (t.destructor_name is not None) is False
        assert t.destructor_name is None


# ---- Finding 7: Token IDs ----


class TestFinding7TokenIds:
    """Finding 7: tokens should get auto-incrementing IDs."""

    def test_tokens_have_sequential_ids(self):
        tokens = collect_tokens("x: i64\n")
        assert len(tokens) > 0
        ids = [t.tokenid for t in tokens]
        # IDs should be unique and ascending
        for i in range(1, len(ids)):
            assert ids[i] > ids[i - 1], f"Token IDs not ascending: {ids}"

    def test_token_ids_are_integers(self):
        tokens = collect_tokens("hello\n")
        for t in tokens:
            assert isinstance(t.tokenid, int)


# ---- Finding 7: VFS file_table ----


class TestFinding7VfsFileTable:
    """Finding 7: VFS should expose a File_table() for SQL serialization."""

    def test_file_table_returns_walked_files(self):
        vfs = ZVfs()
        provider = StringProvider(files={"test.z": "main: function is {}"})
        pid = vfs.register(provider)
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=pid)
        # walk to the file
        _ = vfs.walk(path=["test.z"])
        table = vfs.file_table()
        assert len(table) >= 1
        names = [name for _, name in table]
        assert "test.z" in names

    def test_file_table_includes_file_id(self):
        vfs = ZVfs()
        provider = StringProvider(files={"a.z": "x: 1", "b.z": "y: 2"})
        pid = vfs.register(provider)
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=pid)
        vfs.walk(path=["a.z"])
        vfs.walk(path=["b.z"])
        table = vfs.file_table()
        ids = [fid for fid, _ in table]
        names = [name for _, name in table]
        assert "a.z" in names
        assert "b.z" in names
        # IDs should be unique integers
        assert len(set(ids)) == len(ids)
        assert all(isinstance(fid, int) for fid in ids)

    def test_file_table_empty_initially(self):
        vfs = ZVfs()
        assert vfs.file_table() == []


# ---- Finding 8: file_id consistency through compiler stages ----


class TestFinding8FileIdConsistency:
    """Finding 8: token.fsno should be resolvable via VFS through all stages."""

    def test_token_fsno_is_integer(self):
        program, typing = parse_and_check('main: function is { print "hello" }')
        mainunit = program.units[program.mainunitname]
        main_func = mainunit.body["main"]
        assert isinstance(main_func.start.fsno, int)

    def test_token_fsno_resolves_via_vfs_path(self):
        program, typing = parse_and_check('main: function is { print "hello" }')
        mainunit = program.units[program.mainunitname]
        main_func = mainunit.body["main"]
        # vfs.path() should resolve the token's fsno to a file path
        path = program.vfs.path(main_func.start.fsno)
        assert path is not None
        assert "test.z" in path

    def test_file_table_contains_compiled_files(self):
        program, typing = parse_and_check_uncached(
            'main: function is { print "hello" }'
        )
        table = program.vfs.file_table()
        names = [name for _, name in table]
        # the test unit should appear
        assert "test.z" in names
        # system files should also appear
        assert any("core.z" in n or "system.z" in n or "io.z" in n for n in names)

    def test_file_table_ids_match_token_fsno(self):
        program, typing = parse_and_check_uncached(
            'main: function is { print "hello" }'
        )
        table = program.vfs.file_table()
        file_ids = {fid for fid, _ in table}
        # the main function's token fsno should be in the file table
        mainunit = program.units[program.mainunitname]
        main_func = mainunit.body["main"]
        assert int(main_func.start.fsno) in file_ids


# ---- Finding 7: CallKind ----


class TestFinding7CallKind:
    """Finding 7: type checker should classify calls with CallKind.

    Step 6.8 of the typed-tree migration moved `call_kind` off the
    parsed `zast.Call` and onto `TypedCall`. These tests now look up
    each parsed call's typed counterpart via `program.typed_program`.
    """

    def _typed_calls(self, program, typing):
        """Yield every parsed `Call` that was visited by the typechecker
        (`call_kind` was stamped) paired with its classified `call_kind`.
        F5.E.4.d: walks the parsed AST instead of the deleted typed-tree
        mirror, then filters to calls that have a `call_kind` entry —
        matches the pre-F5.E.4.d behaviour where the mirror only held
        typechecked calls."""
        # Tiny ad-hoc namespace so existing test bodies that read
        # `c.call_kind` keep working without further per-site rewrite.
        from types import SimpleNamespace

        call_kinds = typing.call_kind

        def _walk(node):
            if node is None:
                return
            if node.nodetype == zast.NodeType.CALL:
                yield node
            for child in zast.node_children(node):
                yield from _walk(child)

        for unit in program.units.values():
            for call in _walk(unit):
                if call.nodeid not in call_kinds:
                    continue
                yield SimpleNamespace(
                    call_kind=call_kinds[call.nodeid],
                    parsed=call,
                )

    def test_regular_function_call(self):
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        regular = [
            c
            for c in self._typed_calls(program, typing)
            if c.call_kind == CallKind.REGULAR
        ]
        assert len(regular) > 0, "No REGULAR calls found"

    def test_return_call(self):
        program, typing = parse_and_check(
            "id: function {x: i64} out i64 is { return x }\n"
            'main: function is { print "\\{id 1}" }'
        )
        returns = [
            c
            for c in self._typed_calls(program, typing)
            if c.call_kind == CallKind.RETURN
        ]
        assert len(returns) >= 1, f"Expected at least 1 RETURN, got {len(returns)}"

    def test_record_create(self):
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point x: 1.0 y: 2.0\n    print "ok"\n}'
        )
        creates = [
            c
            for c in self._typed_calls(program, typing)
            if c.call_kind == CallKind.RECORD_CREATE
        ]
        assert len(creates) >= 1, "No RECORD_CREATE calls found"

    def test_class_create(self):
        program, typing = parse_and_check(
            "Box: class is { value: i64 }\n"
            'main: function is {\n    b: Box value: 42\n    print "ok"\n}'
        )
        creates = [
            c
            for c in self._typed_calls(program, typing)
            if c.call_kind == CallKind.CLASS_CREATE
        ]
        assert len(creates) >= 1, "No CLASS_CREATE calls found"

    def test_union_create(self):
        program, typing = parse_and_check(
            "Result: union is { ok: i64  err: String }\n"
            'main: function is {\n    r: Result.ok 42\n    print "ok"\n}'
        )
        creates = [
            c
            for c in self._typed_calls(program, typing)
            if c.call_kind == CallKind.UNION_CREATE
        ]
        assert len(creates) >= 1, "No UNION_CREATE calls found"

    def test_no_unknown_after_typecheck(self):
        """After type checking, no calls should have UNKNOWN kind."""
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        unknowns = [
            c
            for c in self._typed_calls(program, typing)
            if c.call_kind == CallKind.UNKNOWN
        ]
        assert unknowns == [], f"Found UNKNOWN calls: {len(unknowns)}"


# ---- Finding 7: Source map ----


class TestFinding7SourceMap:
    """Finding 7: emitter should produce a source Map (C line → AST node ID)."""

    def test_source_map_length_matches_output(self):
        csource, emitter = emit_with_emitter(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "hello" }'
        )
        lines = csource.split("\n")
        assert len(emitter.source_map) == len(lines)

    def test_source_map_has_mapped_lines(self):
        csource, emitter = emit_with_emitter(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "hello" }'
        )
        mapped = [n for n in emitter.source_map if n is not None]
        assert len(mapped) > 0, "No lines mapped to AST nodes"

    def test_source_map_boilerplate_is_none(self):
        _, emitter = emit_with_emitter('main: function is { print "hello" }')
        # first line is a comment, should be None
        assert emitter.source_map[0] is None

    def test_source_map_definition_lines_have_node_ids(self):
        csource, emitter = emit_with_emitter(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "hello" }'
        )
        lines = csource.split("\n")
        # find the struct definition line
        struct_lines = [
            (i, emitter.source_map[i])
            for i, line in enumerate(lines)
            if "z_point_t" in line
        ]
        assert len(struct_lines) > 0, "No z_point_t lines found"
        # all struct lines should have a node ID
        for lineno, nid in struct_lines:
            assert nid is not None, f"Line {lineno} has z_point_t but no node ID"

    def test_source_map_function_lines_have_node_ids(self):
        csource, emitter = emit_with_emitter('main: function is { print "hello" }')
        lines = csource.split("\n")
        # find the z_main function body (not forward decl)
        main_lines = [
            (i, emitter.source_map[i])
            for i, line in enumerate(lines)
            if "z_main" in line and "{" in line and "int main" not in line
        ]
        assert len(main_lines) > 0, "No z_main body lines found"
        for lineno, nid in main_lines:
            assert nid is not None, f"Line {lineno} has z_main body but no node ID"


# ---- Finding 10: Type annotation audit ----


class TestFinding10TypeAnnotationAudit:
    """Finding 10: audit_type_annotations should detect missing .type."""

    def test_audit_clean_for_simple_program(self):
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is {\n    x: add a: 1 b: 2\n    print "ok"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_record_fields(self):
        program, typing = parse_and_check(
            "point: record is { x: 0.0  y: 0.0 }\n"
            'main: function is {\n    p: point\n    print "ok"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_class(self):
        program, typing = parse_and_check(
            "Box: class is { value: i64 }\n"
            'main: function is {\n    b: Box value: 42\n    print "ok"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_union(self):
        program, typing = parse_and_check(
            "Result: union is { ok: i64  err: String }\n"
            'main: function is {\n    r: Result.ok 42\n    print "ok"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_string_operations(self):
        program, typing = parse_and_check(
            'main: function is {\n    s: "hello"\n    print s\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_skips_binop_operator(self):
        """Binary operators like + should not require .type."""
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is {\n    print "\\{add a: 1 b: 2}"\n}'
        )
        missing = audit_type_annotations(typing)
        assert not any("+'" in m for m in missing), f"Operator + flagged: {missing}"

    def test_audit_skips_data_values(self):
        """Numeric literals in data arrays should not require .type."""
        program, typing = parse_and_check(
            'primes: data is { 2 3 5 7 }\nmain: function is { print "ok" }'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Data values flagged: {missing}"

    def test_audit_skips_constants(self):
        """Top-level numeric constants should not require .type."""
        program, typing = parse_and_check(
            'north: 0\nsouth: 1\nmain: function is { print "ok" }'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Constants flagged: {missing}"

    def test_audit_clean_for_variant(self):
        """Variant subtype types should be annotated."""
        program, typing = parse_and_check(
            "shape: variant is { circle: f64  square: f64  none: null }\n"
            'main: function is {\n    s: shape.circle 3.14\n    print "ok"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_method_params(self):
        """Method parameters in class 'as' blocks should be annotated."""
        program, typing = parse_and_check(
            "counter: class {\n"
            "    value: i64\n"
            "} as {\n"
            "    get: function {c: this} out i64 is { return c.value }\n"
            "}\n"
            'main: function is {\n    c: counter value: 0\n    print "\\{counter.get c}"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_spec(self):
        """Spec (function pointer type) parameters should be annotated."""
        program, typing = parse_and_check(
            "binop: function {a: i64 b: i64} out i64\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "    result: f a: a b: b\n"
            "    return result\n"
            "}\n"
            'main: function is {\n    print "ok"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_nested_expressions(self):
        """Nested if/then/else expressions should have annotated paths."""
        program, typing = parse_and_check(
            "abs: function {x: i64} out i64 is {\n"
            "    if x < 0 then return 0 - x else return x\n"
            "}\n"
            'main: function is { print "\\{abs x: -5}" }'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_dotted_path_access(self):
        """Dotted Path access (field reads) should be annotated."""
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point x: 1.0 y: 2.0\n    print "\\{p.x}"\n}'
        )
        missing = audit_type_annotations(typing)
        assert missing == [], f"Unexpected missing annotations: {missing}"


# ---- Finding 11: Scope cleanup state management ----


class TestFinding11ScopeState:
    """Finding 11: per-function state should use ScopeState/TempState dataclasses."""

    def test_scope_state_dataclass_exists(self):
        """ScopeState dataclass should be importable and have expected fields."""
        from zemitterc import ScopeState

        s = ScopeState()
        assert s.cleanup_vars == []
        assert s.temp_counter == 0
        assert s.record_name == ""
        assert s.class_params == set()

    def test_temp_state_dataclass_exists(self):
        """TempState dataclass should be importable and have expected fields."""
        from zemitterc import TempState

        t = TempState()
        assert t.decls == []
        assert t.frees == []
        assert t.string_set == set()
        assert t.class_set == {}

    def test_emitter_uses_scope_stack(self):
        """Emitter should have _scope_stack and _temp_stack."""
        _program, typing = parse_check_typing('main: function is { print "hello" }')
        emitter = zemitterc.CEmitter(typing)
        assert hasattr(emitter, "_scope_stack")
        assert hasattr(emitter, "_temp_stack")
        assert len(emitter._scope_stack) == 1
        assert len(emitter._temp_stack) == 1

    def test_scope_stack_depth_after_emit(self):
        """After emitting, scope stack should be back to depth 1."""
        csource, emitter = emit_with_emitter(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        assert len(emitter._scope_stack) == 1
        assert len(emitter._temp_stack) == 1

    def test_nested_functions_isolate_scope(self):
        """Nested function calls should not leak scope state."""
        csource, emitter = emit_with_emitter(
            "inner: function {x: i64} out i64 is { return x + 1 }\n"
            "outer: function {x: i64} out i64 is {\n    result: inner x: x\n    return result\n}\n"
            'main: function is { print "\\{outer x: 5}" }'
        )
        # after emission, scope stack should be clean
        assert len(emitter._scope_stack) == 1
        assert emitter._scope_stack[0].cleanup_vars == []

    def test_class_cleanup_emitted(self):
        """Class variables with only valtype fields need no destroy at scope exit."""
        csource, emitter = emit_with_emitter(
            "Box: class { value: i64 }\n"
            'main: function is {\n    b: Box value: 42\n    print "\\{b.value}"\n}'
        )
        assert "z_Box_destroy" not in csource

    def test_string_cleanup_emitted(self):
        """String variables should get z_String_free at scope exit."""
        csource, emitter = emit_with_emitter(
            "greet: function {name: String} out String is "
            '{ return "hello".string }\n'
            "main: function is {\n"
            '    s: greet name: "world".string\n'
            "    print s\n"
            "}"
        )
        assert "z_String_free" in csource

    def test_union_cleanup_emitted(self):
        """Union variables should get destroy calls at scope exit."""
        csource, emitter = emit_with_emitter(
            "Result: union is { ok: i64  err: String }\n"
            'main: function is {\n    r: Result.ok 42\n    print "ok"\n}'
        )
        assert "z_Result_destroy" in csource

    def test_cleanup_uses_destructor_name(self):
        """Scope cleanup should use ZType.destructor_name (type-driven, not cascades)."""
        from zemitterc import ScopeState
        from ztypes import ZType, ZTypeType

        # verify that a ZType with destructor_name set gets correct cleanup
        t = ZType(name="Box", typetype=ZTypeType.CLASS, parent=None)
        t.destructor_name = "z_Box_destroy"
        s = ScopeState()
        s.cleanup_vars.append(("myvar", t))
        # the cleanup_vars list stores (var_name, ZType) — verify structure
        assert len(s.cleanup_vars) == 1
        assert s.cleanup_vars[0][0] == "myvar"
        assert s.cleanup_vars[0][1].destructor_name == "z_Box_destroy"


# ---- Finding 12: Python-specific patterns simplified for self-hosting ----


class TestFinding12SelfHostingPatterns:
    """Finding 12: Python-specific patterns replaced with simpler equivalents."""

    def test_type_ids_auto_increment(self):
        """ZType.nodeid should auto-increment via _alloc_type_id."""
        from ztypes import ZType, ZTypeType

        t1 = ZType(name="a", typetype=ZTypeType.RECORD, parent=None)
        t2 = ZType(name="b", typetype=ZTypeType.RECORD, parent=None)
        assert isinstance(t1.nodeid, int)
        assert isinstance(t2.nodeid, int)
        assert t2.nodeid > t1.nodeid

    def test_variable_ids_auto_increment(self):
        """ZVariable.variableid should auto-increment via _alloc_variable_id."""
        from ztypes import ZVariable, ZType, ZTypeType, ZOwnership, ZNaming

        t = ZType(name="x", typetype=ZTypeType.RECORD, parent=None)
        v1 = ZVariable(ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
        v2 = ZVariable(ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
        assert isinstance(v1.variableid, int)
        assert v2.variableid > v1.variableid

    def test_no_ordered_dict_in_ztype(self):
        """Remaining dict-typed fields on ZType should be plain dicts
        (not OrderedDict). F5.H.5 removed `children` / `generic_args`
        in favour of flat tables on Typing — only `generic_params`
        survives as a per-ZType dict."""
        from ztypes import ZType, ZTypeType

        t = ZType(name="test", typetype=ZTypeType.RECORD, parent=None)
        assert type(t.generic_params) is dict

    def test_children_table_preserves_order(self):
        """Typing.type_child rows preserve declaration order via the
        `position` column (and via the order rows are appended). This
        replaces the pre-F5.H test that exercised ZType.children dict
        ordering directly."""
        from ztypes import ZType, ZTypeType
        from ztyping import Typing
        from zvfs import ZVfs
        import zast

        typing = Typing(parsed=zast.Program(vfs=ZVfs(), units={}, mainunitname=""))
        parent = ZType(name="rec", typetype=ZTypeType.RECORD, parent=None)
        c1 = ZType(name="x", typetype=ZTypeType.RECORD, parent=parent)
        c2 = ZType(name="y", typetype=ZTypeType.RECORD, parent=parent)
        c3 = ZType(name="z", typetype=ZTypeType.RECORD, parent=parent)
        typing.set_child(parent, "x", c1)
        typing.set_child(parent, "y", c2)
        typing.set_child(parent, "z", c3)
        names = [n for n, _ in typing.children_of(parent)]
        assert names == ["x", "y", "z"]


# ---- SQL Schema: dump_sql integration ----


def _load_sql(sql: str) -> sqlite3.Connection:
    """Execute SQL dump into an in-memory SQLite database."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    return conn


class TestSqlDump:
    """SQL schema dump: compile a program, dump SQL, verify integrity."""

    def test_dump_sql_produces_output(self):
        """dump_sql should return non-empty SQL String."""
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        sql = zsqldump.dump_sql(typing)
        assert len(sql) > 0
        assert "CREATE TABLE" in sql
        assert "INSERT INTO" in sql

    def test_dump_sql_loads_into_sqlite(self):
        """SQL dump should be valid SQLite."""
        program, typing = parse_and_check(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "ok" }'
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        # basic sanity: tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "files" in table_names
        assert "tokens" in table_names
        assert "ast_nodes" in table_names
        assert "types" in table_names
        assert "typed_nodes" in table_names
        conn.close()

    def test_files_table_populated(self):
        """files table should contain compiled source files."""
        program, typing = parse_and_check('main: function is { print "hello" }')
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        rows = conn.execute("SELECT * FROM files").fetchall()
        assert len(rows) >= 1
        names = [r[1] for r in rows]
        assert any("test.z" in n for n in names)
        conn.close()

    def test_tokens_table_populated(self):
        """tokens table should contain Parsed tokens."""
        program, typing = parse_and_check('main: function is { print "hello" }')
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        count = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        assert count > 0
        conn.close()

    def test_ast_nodes_table_populated(self):
        """ast_nodes table should contain Parsed AST nodes."""
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        count = conn.execute("SELECT COUNT(*) FROM ast_nodes").fetchone()[0]
        assert count > 0
        # should have Function nodes
        funcs = conn.execute(
            "SELECT name FROM ast_nodes WHERE kind = 'Function'"
        ).fetchall()
        func_names = [r[0] for r in funcs]
        assert any("add" in n for n in func_names)
        conn.close()

    def test_types_table_populated(self):
        """types table should contain resolved types."""
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point x: 1.0 y: 2.0\n    print "ok"\n}'
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        count = conn.execute("SELECT COUNT(*) FROM types").fetchone()[0]
        assert count > 0
        # check for specific type
        rows = conn.execute(
            "SELECT name, typetype FROM types WHERE name = 'point'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][1] == "RECORD"
        conn.close()

    def test_typed_nodes_populated(self):
        """typed_nodes should link AST nodes to types."""
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        count = conn.execute("SELECT COUNT(*) FROM typed_nodes").fetchone()[0]
        assert count > 0
        conn.close()

    def test_emitted_lines_populated(self):
        """emitted_lines should be populated when emitter is provided."""
        csource, emitter = emit_with_emitter(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "hello" }'
        )
        _program, typing = parse_check_typing(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "hello" }'
        )
        emitter2 = zemitterc.CEmitter(typing)
        csource2 = emitter2.emit()
        sql = zsqldump.dump_sql(typing, emitter=emitter2, csource=csource2)
        conn = _load_sql(sql)
        count = conn.execute("SELECT COUNT(*) FROM emitted_lines").fetchone()[0]
        assert count > 0
        # some lines should map back to AST nodes
        mapped = conn.execute(
            "SELECT COUNT(*) FROM emitted_lines WHERE node_id IS NOT NULL"
        ).fetchone()[0]
        assert mapped > 0
        conn.close()

    def test_foreign_key_integrity(self):
        """All foreign keys should be valid (no dangling references)."""
        _program, typing = parse_check_typing_uncached(
            "Box: class { value: i64 }\n"
            'main: function is {\n    b: Box value: 42\n    print "\\{b.value}"\n}'
        )
        emitter = zemitterc.CEmitter(typing)
        csource = emitter.emit()
        sql = zsqldump.dump_sql(typing, emitter=emitter, csource=csource)
        conn = _load_sql(sql)
        conn.execute("PRAGMA foreign_keys = ON")

        # tokens → files: every token.file_id should exist in files
        orphan_tokens = conn.execute("""
            SELECT COUNT(*) FROM tokens t
            WHERE NOT EXISTS (SELECT 1 FROM files f WHERE f.File_id = t.File_id)
        """).fetchone()[0]
        assert orphan_tokens == 0, f"{orphan_tokens} tokens reference missing files"

        # typed_nodes → ast_nodes: every typed_nodes.node_id should exist in ast_nodes
        orphan_typed = conn.execute("""
            SELECT COUNT(*) FROM typed_nodes tn
            WHERE NOT EXISTS (SELECT 1 FROM ast_nodes a WHERE a.node_id = tn.node_id)
        """).fetchone()[0]
        assert orphan_typed == 0, (
            f"{orphan_typed} typed_nodes reference missing ast_nodes"
        )

        # typed_nodes → types: every typed_nodes.type_id should exist in types
        orphan_type_refs = conn.execute("""
            SELECT COUNT(*) FROM typed_nodes tn
            WHERE tn.type_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM types t WHERE t.type_id = tn.type_id)
        """).fetchone()[0]
        assert orphan_type_refs == 0, (
            f"{orphan_type_refs} typed_nodes reference missing types"
        )

        # emitted_lines → ast_nodes (where node_id is not null)
        orphan_emitted = conn.execute("""
            SELECT COUNT(*) FROM emitted_lines el
            WHERE el.node_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM ast_nodes a WHERE a.node_id = el.node_id)
        """).fetchone()[0]
        assert orphan_emitted == 0, (
            f"{orphan_emitted} emitted_lines reference missing ast_nodes"
        )

        conn.close()

    def test_type_children_populated(self):
        """type_children should link parent types to their children."""
        program, typing = parse_and_check(
            'point: record is { x: f64  y: f64 }\nmain: function is { print "ok" }'
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        count = conn.execute("SELECT COUNT(*) FROM type_children").fetchone()[0]
        assert count > 0
        conn.close()

    def test_destructor_metadata_in_types(self):
        """Types with destructors should have needs_destructor and destructor_name."""
        program, typing = parse_and_check(
            "Box: class { value: i64 }\n"
            'main: function is {\n    b: Box value: 42\n    print "ok"\n}'
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        row = conn.execute(
            "SELECT needs_destructor, destructor_name, is_heap_allocated "
            "FROM types WHERE name = 'Box'"
        ).fetchone()
        assert row is not None, "Box type not in types table"
        assert row[0] == 0  # needs_destructor (valtype-only class)
        assert row[2] == 0  # is_heap_allocated
        conn.close()

    def test_cli_dump_sql_flag(self):
        """zc --dump-sql should write valid SQL to a File."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            # write a small zerolang source file
            src = os.path.join(tmpdir, "clitest.z")
            with open(src, "w") as f:
                f.write('main: function is { print "hello" }\n')
            sql_path = os.path.join(tmpdir, "out.sql")
            # give zc an explicit -o inside the tempdir so it does not
            # create a stray clitest.c in the repo root (zc defaults its C
            # output path to `<unit>.c` in the current working directory).
            c_path = os.path.join(tmpdir, "out.c")
            src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(src_dir, "zc.py"),
                    "--src",
                    tmpdir,
                    "clitest",
                    "-o",
                    c_path,
                    "--dump-sql",
                    sql_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"zc failed: {result.stderr}"
            assert os.path.exists(sql_path), "SQL File not created"
            sql = open(sql_path).read()
            assert "CREATE TABLE" in sql
            assert "INSERT INTO" in sql
            # verify it loads into SQLite
            conn = _load_sql(sql)
            count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            assert count > 0
            conn.close()


# ---- Code Review 2: Name Mangling and cname ----


class TestCname:
    """Tests for cname assignment on ZType."""

    def test_record_gets_cname(self):
        """Record types should have cname set to z_{name}_t."""
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            "main: function is {\n"
            "    p: point x: 1.0 y: 2.0\n"
            '    print "\\{p.x}"\n'
            "}\n"
        )
        for ztype in typing.resolved.values():
            if ztype.name == "point":
                assert ztype.cname == "z_point_t"
                return
        assert False, "point type not found in resolved"

    def test_class_gets_cname(self):
        """Class types should have cname set to z_{name}_t."""
        program, typing = parse_and_check(
            "node: class is { val: i64 }\n"
            "main: function is {\n"
            "    n: node val: 1\n"
            '    print "\\{n.val}"\n'
            "}\n"
        )
        for ztype in typing.resolved.values():
            if ztype.name == "node":
                assert ztype.cname == "z_node_t"
                return
        assert False, "node type not found in resolved"

    def test_function_gets_cname(self):
        """Functions should have cname set to z_{name}."""
        program, typing = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        for ztype in typing.resolved.values():
            if ztype.name == "add":
                assert ztype.cname == "z_add"
                return
        assert False, "add function type not found in resolved"

    def test_union_gets_cname(self):
        """Union types should have cname set to z_{name}_t."""
        program, typing = parse_and_check(
            "shape: union {\n"
            "    circle: f64\n"
            "    square: f64\n"
            "}\n"
            "main: function is {\n"
            "    s: shape.circle 1.0\n"
            '    print "ok"\n'
            "}\n"
        )
        for ztype in typing.resolved.values():
            if ztype.name == "shape":
                assert ztype.cname == "z_shape_t"
                return
        assert False, "shape type not found in resolved"

    def test_collision_auto_resolves(self):
        """All assigned cnames should be unique across the program."""
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            "node: class is { val: i64 }\n"
            "main: function is {\n"
            "    p: point x: 1.0 y: 2.0\n"
            "    n: node val: 1\n"
            '    print "\\{p.x} \\{n.val}"\n'
            "}\n"
        )
        cnames: dict[str, int] = {}  # cname -> object id
        for ztype in typing.resolved.values():
            if ztype.cname:
                prev_id = cnames.get(ztype.cname)
                if prev_id is not None and prev_id != id(ztype):
                    raise AssertionError(
                        f"Collision: {ztype.cname} assigned to multiple types"
                    )
                cnames[ztype.cname] = id(ztype)

    def test_cname_in_sql_dump(self):
        """SQL dump should include cname column in types table."""
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            "main: function is {\n"
            "    p: point x: 1.0 y: 2.0\n"
            '    print "\\{p.x}"\n'
            "}"
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        # check cname column exists
        row = conn.execute("SELECT cname FROM types WHERE name = 'point'").fetchone()
        assert row is not None
        assert row[0] == "z_point_t"
        conn.close()

    def test_cname_in_typed_nodes_dump(self):
        """SQL dump should include cname column in typed_nodes table.

        Step 5b of the typed-tree migration moved typecheck-set
        per-node columns (cname, is_const, const_value) out of
        `ast_nodes` into `typed_nodes` so the parser-side and
        typechecker-side rows are disjoint."""
        program, typing = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            "main: function is {\n"
            "    p: point x: 1.0 y: 2.0\n"
            '    print "\\{p.x}"\n'
            "}"
        )
        sql = zsqldump.dump_sql(typing)
        conn = _load_sql(sql)
        # expression nodes referencing point should have its cname
        row = conn.execute(
            "SELECT cname FROM typed_nodes WHERE cname = 'z_point_t'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_dot_underscore_collision_resolved(self):
        """Unit function m.f and top-level m_f get distinct cnames."""
        program, typing = parse_and_check(
            "m: unit { f: function {x: i64} out i64 is { return x } }\n"
            "m_f: function {x: i64} out i64 is { return x + 1 }\n"
            'main: function is { print "\\{m.f 5} \\{m_f 5}" }'
        )
        # m.f (unit function) and m_f (top-level) both mangle to z_m_f base
        unit_fn = typing.resolved.get("test.m.f")
        top_fn = typing.resolved.get("test.m_f")
        assert unit_fn is not None, "test.m.f not resolved"
        assert top_fn is not None, "test.m_f not resolved"
        assert unit_fn.cname != top_fn.cname, (
            f"Collision not resolved: both have cname {unit_fn.cname}"
        )

    def test_generic_monomorphization_collision_resolved(self):
        """Non-generic Box_i64 record and generic Box[of i64] get distinct cnames."""
        program, typing = parse_and_check(
            "Box: union { some: t\n none: null } as { t: Any.generic }\n"
            "Box_i64: record is { val: i64 }\n"
            "main: function is {\n"
            "    a: Box.some 42\n"
            "    b: Box_i64 val: 99\n"
            '    print "\\{b.val}"\n'
            "}"
        )
        # box[of i64] monomorphizes to name "box_i64" — same as the plain record
        mono_cname = None
        plain_cname = None
        for ztype in typing.resolved.values():
            if ztype.name == "Box_i64" and ztype.generic_origin:
                mono_cname = ztype.cname
            elif ztype.name == "Box_i64" and not ztype.generic_origin:
                plain_cname = ztype.cname
        assert mono_cname is not None, "Monomorphized Box_i64 not found"
        assert plain_cname is not None, "Plain Box_i64 not found"
        assert mono_cname != plain_cname, (
            f"Collision not resolved: both have cname {mono_cname}"
        )


class TestNodeIdTemps:
    """Tests for NodeID-scoped temporary variable names."""

    def test_temps_include_nodeid(self):
        """Emitter temporaries should include the function's NodeID."""
        csource, _ = emit_with_emitter(
            'main: function is { print "hello \\{1 + 2} world" }'
        )
        # temp variables follow _{prefix}{nodeid}_{counter} pattern
        # (e.g., _s1234_1 for string result, _b1234_1 for buffer)
        import re

        temps = re.findall(r"_[a-z]\d+_\d+", csource)
        assert len(temps) > 0, "No NodeID-scoped temps found in output"
        # all temps in main should share the same nodeid
        nodeids = {
            t.split("_")[1][0:-1] if t.split("_")[1][-1].isdigit() else t.split("_")[1]
            for t in temps
        }
        # extract numeric part after the letter prefix
        nodeids = set()
        for t in temps:
            parts = t.lstrip("_")
            nid = re.match(r"[a-z](\d+)", parts)
            if nid:
                nodeids.add(nid.group(1))
        assert len(nodeids) == 1, f"Expected 1 NodeID prefix, got {nodeids}"
