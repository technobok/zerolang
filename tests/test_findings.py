"""
Tests for code review findings infrastructure (Findings 1, 3, 7).

These test the new fields and metadata added during the code review:
- Finding 1: type annotations on all Path nodes
- Finding 3: destructor metadata on ZType
- Finding 7: Token IDs, VFS file_table, CallKind, source map
"""

import os

from conftest import make_parser_vfs, collect_tokens
from zparser import Parser
from ztypecheck import typecheck
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
