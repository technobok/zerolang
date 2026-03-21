"""
Tests for code review findings infrastructure (Findings 1, 3, 7, 8, 10).

These test the new fields and metadata added during the code review:
- Finding 1: type annotations on all Path nodes
- Finding 3: destructor metadata on ZType
- Finding 7: Token IDs, VFS file_table, CallKind, source map
- Finding 8: file ID consistency
- Finding 10: type annotation audit
"""

import os

from conftest import make_parser_vfs, collect_tokens
from zparser import Parser
from ztypecheck import typecheck, audit_type_annotations
from ztypechecker import ZTypeType
import zemitterc
import zast
from zast import CallKind
from zvfs import ZVfs, FSProvider, StringProvider, BindType


LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def parse_and_check(source: str, unitname: str = "test"):
    """Parse source, run type checker, return program (assert no errors)."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=LIB_DIR)
    p = Parser(vfs, name)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    errors = typecheck(program)
    assert errors == [], f"Type errors: {[e.msg for e in errors]}"
    return program


def emit_with_emitter(source: str, unitname: str = "test"):
    """Parse, type-check, and return (c_source, emitter) for inspection."""
    program = parse_and_check(source, unitname)
    emitter = zemitterc.CEmitter(program)
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


class TestFinding1TypeAnnotations:
    """Finding 1: type checker should annotate every Path node."""

    def test_record_field_types_annotated(self):
        program = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point\n    print "ok"\n}'
        )
        # find the record definition
        mainunit = program.units[program.mainunitname]
        point = mainunit.body["point"]
        assert isinstance(point, zast.Record)
        for fname, fpath in point.items.items():
            assert fpath.type is not None, f"Field '{fname}' has no .type"
            assert fpath.type.name in ("f64",), f"Field '{fname}' type is {fpath.type.name}"

    def test_function_param_types_annotated(self):
        program = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        mainunit = program.units[program.mainunitname]
        add = mainunit.body["add"]
        assert isinstance(add, zast.Function)
        for pname, ppath in add.parameters.items():
            if pname.startswith(":"):
                continue
            assert ppath.type is not None, f"Param '{pname}' has no .type"

    def test_function_return_type_annotated(self):
        program = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        mainunit = program.units[program.mainunitname]
        add = mainunit.body["add"]
        assert add.returntype is not None
        assert add.returntype.type is not None, "Return type has no .type"
        assert add.returntype.type.name == "i64"

    def test_class_field_types_annotated(self):
        program = parse_and_check(
            "box: class is { value: i64 }\n"
            'main: function is {\n    b: box value: 42\n    print "\\{b.value}"\n}'
        )
        mainunit = program.units[program.mainunitname]
        box = mainunit.body["box"]
        assert isinstance(box, zast.Class)
        for fname, fpath in box.items.items():
            assert fpath.type is not None, f"Field '{fname}' has no .type"

    def test_union_subtype_annotated(self):
        program = parse_and_check(
            "result: union is { ok: i64  err: string }\n"
            'main: function is {\n    r: result.ok 42\n    print "ok"\n}'
        )
        mainunit = program.units[program.mainunitname]
        result = mainunit.body["result"]
        assert isinstance(result, zast.Union)
        for sname, spath in result.items.items():
            assert spath.type is not None, f"Subtype '{sname}' has no .type"


# ---- Finding 3: Destructor metadata ----


class TestFinding3DestructorMetadata:
    """Finding 3: ZType should carry needs_destructor, destructor_name, is_heap_allocated."""

    def test_string_destructor(self):
        program = parse_and_check('main: function is {\n    s: "hello"\n    print s\n}')
        # string type should have destructor metadata
        t = program.resolved.get("system.string")
        # string might not be in resolved directly; check via a known type
        # use the record field approach
        program2 = parse_and_check(
            "box: class is { name: string }\n"
            'main: function is {\n    b: box name: "hi"\n    print b.name\n}'
        )
        mainunit = program2.units[program2.mainunitname]
        box = mainunit.body["box"]
        name_type = box.items["name"].type
        assert name_type is not None
        assert name_type.name == "string"
        assert name_type.needs_destructor is True
        assert name_type.destructor_name == "zstr_free"
        assert name_type.is_heap_allocated is True

    def test_class_destructor(self):
        program = parse_and_check(
            "box: class is { value: i64 }\n"
            'main: function is {\n    b: box value: 0\n    print "ok"\n}'
        )
        t = None
        for key, ztype in program.resolved.items():
            if ztype.name == "box" and ztype.typetype == ZTypeType.CLASS:
                t = ztype
                break
        assert t is not None, "box type not found in resolved"
        assert t.needs_destructor is True
        assert t.destructor_name == "z_box_destroy"
        assert t.is_heap_allocated is True

    def test_union_destructor(self):
        program = parse_and_check(
            "result: union is { ok: i64  err: string }\n"
            'main: function is {\n    r: result.ok 42\n    print "ok"\n}'
        )
        t = None
        for key, ztype in program.resolved.items():
            if ztype.name == "result" and ztype.typetype == ZTypeType.UNION:
                t = ztype
                break
        assert t is not None, "result type not found in resolved"
        assert t.needs_destructor is True
        assert t.destructor_name == "z_result_destroy"
        assert t.is_heap_allocated is True

    def test_record_no_destructor(self):
        program = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point\n    print "ok"\n}'
        )
        t = None
        for key, ztype in program.resolved.items():
            if ztype.name == "point" and ztype.typetype == ZTypeType.RECORD:
                t = ztype
                break
        assert t is not None, "point type not found in resolved"
        assert t.needs_destructor is False
        assert t.destructor_name is None
        assert t.is_heap_allocated is False

    def test_numeric_no_destructor(self):
        program = parse_and_check('main: function is {\n    x: 42\n    print "ok"\n}')
        # i64 is a record type with no destructor
        t = program.resolved.get("system.i64")
        assert t is not None
        assert t.needs_destructor is False
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
    """Finding 7: VFS should expose a file_table() for SQL serialization."""

    def test_file_table_returns_walked_files(self):
        vfs = ZVfs()
        provider = StringProvider(files={"test.z": "main: function is {}"})
        pid = vfs.register(provider)
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=pid)
        # walk to the file
        fileid = vfs.walk(path=["test.z"])
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
        program = parse_and_check('main: function is { print "hello" }')
        mainunit = program.units[program.mainunitname]
        main_func = mainunit.body["main"]
        assert isinstance(main_func.start.fsno, int)

    def test_token_fsno_resolves_via_vfs_path(self):
        program = parse_and_check('main: function is { print "hello" }')
        mainunit = program.units[program.mainunitname]
        main_func = mainunit.body["main"]
        # vfs.path() should resolve the token's fsno to a file path
        path = program.vfs.path(main_func.start.fsno)
        assert path is not None
        assert "test.z" in path

    def test_file_table_contains_compiled_files(self):
        program = parse_and_check('main: function is { print "hello" }')
        table = program.vfs.file_table()
        names = [name for _, name in table]
        # the test unit should appear
        assert "test.z" in names
        # system files should also appear
        assert any("core.z" in n or "system.z" in n or "io.z" in n for n in names)

    def test_file_table_ids_match_token_fsno(self):
        program = parse_and_check('main: function is { print "hello" }')
        table = program.vfs.file_table()
        file_ids = {fid for fid, _ in table}
        # the main function's token fsno should be in the file table
        mainunit = program.units[program.mainunitname]
        main_func = mainunit.body["main"]
        assert int(main_func.start.fsno) in file_ids


# ---- Finding 7: CallKind ----


class TestFinding7CallKind:
    """Finding 7: type checker should classify calls with CallKind."""

    def _find_calls(self, node, visited=None):
        """Recursively find all Call nodes in an AST."""
        if visited is None:
            visited = set()
        nid = id(node)
        if nid in visited:
            return []
        visited.add(nid)
        calls = []
        if isinstance(node, zast.Call):
            calls.append(node)
        if hasattr(node, "__dataclass_fields__"):
            for fname in node.__dataclass_fields__:
                val = getattr(node, fname, None)
                if val is None:
                    continue
                if isinstance(val, zast.Node):
                    calls.extend(self._find_calls(val, visited))
                elif isinstance(val, dict):
                    for v in val.values():
                        if isinstance(v, zast.Node):
                            calls.extend(self._find_calls(v, visited))
                elif isinstance(val, list):
                    for v in val:
                        if isinstance(v, zast.Node):
                            calls.extend(self._find_calls(v, visited))
        return calls

    def test_regular_function_call(self):
        program = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        mainunit = program.units[program.mainunitname]
        calls = self._find_calls(mainunit.body["main"])
        regular = [c for c in calls if c.call_kind == CallKind.REGULAR]
        assert len(regular) > 0, "No REGULAR calls found"

    def test_return_call(self):
        program = parse_and_check(
            "id: function {x: i64} out i64 is { return x }\n"
            'main: function is { print "\\{id 1}" }'
        )
        mainunit = program.units[program.mainunitname]
        calls = self._find_calls(mainunit.body["id"])
        returns = [c for c in calls if c.call_kind == CallKind.RETURN]
        assert len(returns) == 1, f"Expected 1 RETURN, got {len(returns)}"

    def test_record_create(self):
        program = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point x: 1.0 y: 2.0\n    print "ok"\n}'
        )
        mainunit = program.units[program.mainunitname]
        calls = self._find_calls(mainunit.body["main"])
        creates = [c for c in calls if c.call_kind == CallKind.RECORD_CREATE]
        assert len(creates) >= 1, "No RECORD_CREATE calls found"

    def test_class_create(self):
        program = parse_and_check(
            "box: class is { value: i64 }\n"
            'main: function is {\n    b: box value: 42\n    print "ok"\n}'
        )
        mainunit = program.units[program.mainunitname]
        calls = self._find_calls(mainunit.body["main"])
        creates = [c for c in calls if c.call_kind == CallKind.CLASS_CREATE]
        assert len(creates) >= 1, "No CLASS_CREATE calls found"

    def test_union_create(self):
        program = parse_and_check(
            "result: union is { ok: i64  err: string }\n"
            'main: function is {\n    r: result.ok 42\n    print "ok"\n}'
        )
        mainunit = program.units[program.mainunitname]
        calls = self._find_calls(mainunit.body["main"])
        creates = [c for c in calls if c.call_kind == CallKind.UNION_CREATE]
        assert len(creates) >= 1, "No UNION_CREATE calls found"

    def test_no_unknown_after_typecheck(self):
        """After type checking, no calls should have UNKNOWN kind."""
        program = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is { print "\\{add a: 1 b: 2}" }'
        )
        mainunit = program.units[program.mainunitname]
        for name, defn in mainunit.body.items():
            calls = self._find_calls(defn)
            unknowns = [c for c in calls if c.call_kind == CallKind.UNKNOWN]
            assert unknowns == [], (
                f"Found UNKNOWN calls in '{name}': "
                f"{[(c.callable, c.call_kind) for c in unknowns]}"
            )


# ---- Finding 7: Source map ----


class TestFinding7SourceMap:
    """Finding 7: emitter should produce a source map (C line → AST node ID)."""

    def test_source_map_length_matches_output(self):
        csource, emitter = emit_with_emitter(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is { print "hello" }'
        )
        lines = csource.split("\n")
        assert len(emitter.source_map) == len(lines)

    def test_source_map_has_mapped_lines(self):
        csource, emitter = emit_with_emitter(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is { print "hello" }'
        )
        mapped = [n for n in emitter.source_map if n is not None]
        assert len(mapped) > 0, "No lines mapped to AST nodes"

    def test_source_map_boilerplate_is_none(self):
        csource, emitter = emit_with_emitter(
            'main: function is { print "hello" }'
        )
        lines = csource.split("\n")
        # first line is a comment, should be None
        assert emitter.source_map[0] is None

    def test_source_map_definition_lines_have_node_ids(self):
        csource, emitter = emit_with_emitter(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is { print "hello" }'
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
        csource, emitter = emit_with_emitter(
            'main: function is { print "hello" }'
        )
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
        program = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is {\n    x: add a: 1 b: 2\n    print "ok"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_record_fields(self):
        program = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point\n    print "ok"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_class(self):
        program = parse_and_check(
            "box: class is { value: i64 }\n"
            'main: function is {\n    b: box value: 42\n    print "ok"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_union(self):
        program = parse_and_check(
            "result: union is { ok: i64  err: string }\n"
            'main: function is {\n    r: result.ok 42\n    print "ok"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_string_operations(self):
        program = parse_and_check(
            'main: function is {\n    s: "hello"\n    print s\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_skips_binop_operator(self):
        """Binary operators like + should not require .type."""
        program = parse_and_check(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            'main: function is {\n    print "\\{add a: 1 b: 2}"\n}'
        )
        missing = audit_type_annotations(program)
        assert not any("+'" in m for m in missing), f"Operator + flagged: {missing}"

    def test_audit_skips_data_values(self):
        """Numeric literals in data arrays should not require .type."""
        program = parse_and_check(
            'primes: data is { 2 3 5 7 }\nmain: function is { print "ok" }'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Data values flagged: {missing}"

    def test_audit_skips_constants(self):
        """Top-level numeric constants should not require .type."""
        program = parse_and_check(
            'north: 0\nsouth: 1\nmain: function is { print "ok" }'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Constants flagged: {missing}"

    def test_audit_clean_for_variant(self):
        """Variant subtype types should be annotated."""
        program = parse_and_check(
            "shape: variant is { circle: f64  square: f64  none: null }\n"
            'main: function is {\n    s: shape.circle 3.14\n    print "ok"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_method_params(self):
        """Method parameters in class 'as' blocks should be annotated."""
        program = parse_and_check(
            "counter: class {\n"
            "    value: i64\n"
            "} as {\n"
            "    get: function {c: this} out i64 is { return c.value }\n"
            "}\n"
            'main: function is {\n    c: counter value: 0\n    print "\\{counter.get c}"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_spec(self):
        """Spec (function pointer type) parameters should be annotated."""
        program = parse_and_check(
            "binop: function {a: i64 b: i64} out i64\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "    result: f a: a b: b\n"
            "    return result\n"
            "}\n"
            'main: function is {\n    print "ok"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_nested_expressions(self):
        """Nested if/then/else expressions should have annotated paths."""
        program = parse_and_check(
            "abs: function {x: i64} out i64 is {\n"
            "    if x < 0 then return 0 - x else return x\n"
            "}\n"
            'main: function is { print "\\{abs x: -5}" }'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"

    def test_audit_clean_for_dotted_path_access(self):
        """Dotted path access (field reads) should be annotated."""
        program = parse_and_check(
            "point: record is { x: f64  y: f64 }\n"
            'main: function is {\n    p: point x: 1.0 y: 2.0\n    print "\\{p.x}"\n}'
        )
        missing = audit_type_annotations(program)
        assert missing == [], f"Unexpected missing annotations: {missing}"


# ---- Finding 11: Scope cleanup state management ----


class TestFinding11ScopeState:
    """Finding 11: per-function state should use ScopeState/TempState dataclasses."""

    def test_scope_state_dataclass_exists(self):
        """ScopeState dataclass should be importable and have expected fields."""
        from zemitterc import ScopeState
        s = ScopeState()
        assert s.string_vars == []
        assert s.class_vars == []
        assert s.union_vars == []
        assert s.protocol_vars == []
        assert s.union_var_types == {}
        assert s.class_var_types == {}
        assert s.protocol_var_types == {}
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
        program = parse_and_check('main: function is { print "hello" }')
        emitter = zemitterc.CEmitter(program)
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
        assert emitter._scope_stack[0].string_vars == []

    def test_class_cleanup_emitted(self):
        """Class variables should get destroy calls at scope exit."""
        csource, emitter = emit_with_emitter(
            "box: class { value: i64 }\n"
            'main: function is {\n    b: box value: 42\n    print "\\{b.value}"\n}'
        )
        assert "z_box_destroy" in csource

    def test_string_cleanup_emitted(self):
        """String variables should get zstr_free at scope exit."""
        csource, emitter = emit_with_emitter(
            'greet: function {name: string} out string is { return "hello" }\n'
            'main: function is {\n    s: greet name: "world"\n    print s\n}'
        )
        assert "zstr_free" in csource
