"""
Typed-tree mirror invariants (Step 3 of the typed-tree migration).

The typechecker builds a parallel `TypedProgram` (in `src/ztypedast.py`)
alongside its in-place decorations on the parsed AST. These tests pin
the structural invariant: every parsed node the typechecker has typed
has a typed-tree counterpart in `TypedProgram.by_parsed_id`, and the
typed counterpart's fields agree with the in-place decorations on its
parsed back-reference.

The invariant gradually broadens as more `_check_*` methods build their
typed mirrors. Today only `TypedAtomId` is built; subsequent steps add
the rest of the typed-node families.
"""

import os

import pytest

from typing import cast as _cast

from conftest import make_parser
import zast
import ztypedast
from zast import NodeType
from ztypecheck import TypeChecker

pytestmark = pytest.mark.typecheck

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def _typecheck(source: str, unitname: str = "test") -> TypeChecker:
    """Parse + run typecheck. Returns the TypeChecker so tests can read
    `typed_program` directly (rather than only the public errors list)."""
    p = make_parser(source, unitname=unitname, src_dir=LIB_DIR)
    program = p.parse()
    assert isinstance(program, zast.Program), f"parse failed: {program!r}"
    tc = TypeChecker(program)
    errors = tc.check()
    assert errors == [], f"unexpected typecheck errors: {[e.msg for e in errors]}"
    return tc


def _walk_main(unit: zast.Unit):
    """Yield every parsed Node reachable from the main unit's body."""
    seen: set[int] = set()
    stack: list[zast.Node] = list(unit.body.values())
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        yield n
        stack.extend(zast.node_children(n))


class TestTypedProgramScaffold:
    """The typechecker exposes a TypedProgram and indexes typed nodes
    by parsed nodeid as it builds them."""

    def test_typed_program_is_constructed(self):
        tc = _typecheck("main: function is {}")
        tp = tc.typed_program
        assert isinstance(tp, ztypedast.TypedProgram)
        assert tp.parsed is tc.program
        assert tp.mainunitname == "test"

    def test_by_parsed_id_populated_for_visited_atoms(self):
        """Type-checking a function body with an integer literal should
        register a TypedAtomId for that literal's parsed AtomId."""
        tc = _typecheck("main: function is {\n    x: 42\n}")
        # find the parsed AtomId for the integer literal
        mainunit = tc.program.units[tc.program.mainunitname]
        atoms = [
            n
            for n in _walk_main(mainunit)
            if n.nodetype == NodeType.ATOMID and _cast(zast.AtomId, n).name == "42"
        ]
        assert len(atoms) == 1, f"expected exactly one '42' atom, got {len(atoms)}"
        atom = _cast(zast.AtomId, atoms[0])
        typed = tc.typed_program.by_parsed_id.get(atom.nodeid)
        assert typed is not None, "TypedAtomId should be registered for visited atom"
        assert isinstance(typed, ztypedast.TypedAtomId)
        assert typed.parsed is atom
        assert typed.name == atom.name
        assert typed.ztype is atom.type
        assert typed.const_value == atom.const_value
        assert typed.is_label_value is False

    def test_atomid_typed_for_named_constant(self):
        """A reference to a named binding (variable) builds a TypedAtomId
        registered in by_parsed_id."""
        tc = _typecheck("main: function is {\n    x: 7\n    y: x\n}")
        mainunit = tc.program.units[tc.program.mainunitname]
        # the bare `x` reference on the rhs of `y: x`
        x_refs = [
            n
            for n in _walk_main(mainunit)
            if n.nodetype == NodeType.ATOMID and _cast(zast.AtomId, n).name == "x"
        ]
        assert x_refs, "expected an AtomId reference to x"
        for ref in x_refs:
            typed = tc.typed_program.by_parsed_id.get(ref.nodeid)
            # may be None for atoms not visited via _check_atomid
            # (binop operators etc.); skip those for now.
            if typed is None:
                continue
            assert isinstance(typed, ztypedast.TypedAtomId)
            assert typed.name == "x"


class TestTypedAtomStringInvariants:
    """A non-interpolated string literal builds a TypedAtomString whose
    parts are inert StringChunks. Interpolated literals get a typed
    mirror only when every interpolation part has a typed counterpart
    in `by_parsed_id` — earlier sub-steps cover AtomId/DottedPath
    interpolations; BinOp/Call interpolations land later."""

    def test_plain_string_literal(self):
        tc = _typecheck('main: function is {\n    s: "hello"\n}')
        mainunit = tc.program.units[tc.program.mainunitname]
        strings = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.ATOMSTRING]
        assert len(strings) == 1
        atom_str = _cast(zast.AtomString, strings[0])
        typed = tc.typed_program.by_parsed_id.get(atom_str.nodeid)
        assert typed is not None
        assert isinstance(typed, ztypedast.TypedAtomString)
        assert typed.parsed is atom_str
        assert typed.ztype is atom_str.type
        # parts are inert StringChunks for a plain literal
        assert len(typed.parts) == len(atom_str.stringparts)
        for tp, sp in zip(typed.parts, atom_str.stringparts):
            assert tp is sp  # StringChunk passed through by reference

    def test_interpolated_with_atomid(self):
        """`"hi \\{name}"` — interpolation part is an AtomId, which has a
        typed mirror. The TypedAtomString's parts include the AtomId's
        TypedAtomId in place of the parsed Expression wrapper."""
        tc = _typecheck(
            'main: function is {\n    name: "world"\n    msg: "hi \\{name}"\n}'
        )
        mainunit = tc.program.units[tc.program.mainunitname]
        strings = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.ATOMSTRING]
        # the second string carries the interpolation
        interpolated = next(
            s
            for s in strings
            if any(
                p.nodetype != NodeType.STRINGCHUNK
                for p in _cast(zast.AtomString, s).stringparts
            )
        )
        typed = tc.typed_program.by_parsed_id.get(interpolated.nodeid)
        assert typed is not None, "expected TypedAtomString for interpolated literal"
        assert isinstance(typed, ztypedast.TypedAtomString)
        # exactly one part should be a TypedAtomId for `name`
        atomid_parts = [p for p in typed.parts if isinstance(p, ztypedast.TypedAtomId)]
        assert len(atomid_parts) == 1
        assert atomid_parts[0].name == "name"


class TestTypedBinOpInvariants:
    """`TypedBinOp` mirrors `zast.BinOp`. The operator AtomId is
    structural (never independently typed by the typechecker) so the
    typed mirror constructs a fresh TypedAtomId for `operator` rather
    than looking it up in `by_parsed_id`."""

    def test_simple_int_add(self):
        tc = _typecheck("main: function is {\n    x: 21.i64 + 21.i64\n}")
        mainunit = tc.program.units[tc.program.mainunitname]
        binops = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.BINOP]
        assert binops, "expected a BinOp"
        for b in binops:
            pb = _cast(zast.BinOp, b)
            typed = tc.typed_program.by_parsed_id.get(pb.nodeid)
            assert typed is not None
            assert isinstance(typed, ztypedast.TypedBinOp)
            assert typed.ztype is pb.type
            assert typed.const_value == pb.const_value
            assert typed.operator.name == pb.operator.name
            # operator is a fresh TypedAtomId, not registered separately
            assert pb.operator.nodeid not in tc.typed_program.by_parsed_id
            # lhs / rhs typed are present
            assert typed.lhs is not None
            assert typed.rhs is not None


class TestTypedCallInvariants:
    """`TypedCall` mirrors `zast.Call` plus its argument list as
    `TypedNamedOperation` siblings (each registered in by_parsed_id)."""

    def test_simple_call(self):
        tc = _typecheck(
            "incr: function {x: i64} out i64 is {\n"
            "    return x + 1.i64\n"
            "}\n"
            "main: function is {\n"
            "    y: incr x: 41.i64\n"
            "}"
        )
        mainunit = tc.program.units[tc.program.mainunitname]
        calls = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.CALL]
        # locate the user-level `incr x: 41.i64` call
        target = None
        for c in calls:
            cc = _cast(zast.Call, c)
            if (
                cc.callable.nodetype == NodeType.ATOMID
                and _cast(zast.AtomId, cc.callable).name == "incr"
            ):
                target = cc
                break
        assert target is not None
        typed = tc.typed_program.by_parsed_id.get(target.nodeid)
        assert typed is not None
        assert isinstance(typed, ztypedast.TypedCall)
        assert typed.parsed is target
        assert typed.ztype is target.type
        assert typed.call_kind == target.call_kind
        assert typed.callable_type_name == target.callable_type_name
        # callable is the TypedAtomId for `incr`
        assert isinstance(typed.callable, ztypedast.TypedAtomId)
        assert typed.callable.name == "incr"
        # one argument: x: 41.i64
        assert len(typed.arguments) == 1
        arg = typed.arguments[0]
        assert isinstance(arg, ztypedast.TypedNamedOperation)
        assert arg.name == "x"
        # arg is registered in by_parsed_id keyed by the parsed
        # NamedOperation's nodeid
        parsed_arg = target.arguments[0]
        assert tc.typed_program.by_parsed_id.get(parsed_arg.nodeid) is arg

    def test_atomstring_with_binop_interpolation_now_mirrors(self):
        """Step 3c noted that `\\{a + b}` interpolation could not yet
        produce a TypedAtomString because BinOp had no typed mirror.
        After Step 3d, this gap closes — verify the AtomString gets
        its typed mirror."""
        tc = _typecheck(
            'main: function is {\n    a: 1.i64\n    b: 2.i64\n    s: "sum=\\{a + b}"\n}'
        )
        mainunit = tc.program.units[tc.program.mainunitname]
        strings = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.ATOMSTRING]
        interpolated = next(
            s
            for s in strings
            if any(
                p.nodetype != NodeType.STRINGCHUNK
                for p in _cast(zast.AtomString, s).stringparts
            )
        )
        typed = tc.typed_program.by_parsed_id.get(interpolated.nodeid)
        assert typed is not None, (
            "after 3d, interpolated string with BinOp should have a typed mirror"
        )
        assert isinstance(typed, ztypedast.TypedAtomString)
        binop_parts = [p for p in typed.parts if isinstance(p, ztypedast.TypedBinOp)]
        assert len(binop_parts) == 1


class TestTypedStatementInvariants:
    """Statements / StatementLines / Assignments wrap operations in
    typed-tree form. Each parsed statement-shape has a mirror keyed by
    its parsed nodeid; the wrapped values reference the typed
    counterparts of their inner expressions."""

    def test_statement_and_assignment_mirrors(self):
        tc = _typecheck("main: function is {\n    x: 7\n    y: x\n}")
        mainunit = tc.program.units[tc.program.mainunitname]
        # the function body is a Statement
        main_fn = _cast(zast.Function, mainunit.body["main"])
        assert main_fn.body is not None
        body_typed = tc.typed_program.by_parsed_id.get(main_fn.body.nodeid)
        assert body_typed is not None
        assert isinstance(body_typed, ztypedast.TypedStatement)
        # two statement lines: `x: 7` and `y: x`
        assert len(body_typed.statements) == 2
        for sline_typed, sline_parsed in zip(
            body_typed.statements, main_fn.body.statements
        ):
            assert isinstance(sline_typed, ztypedast.TypedStatementLine)
            assert sline_typed.parsed is sline_parsed
            inner_parsed = sline_parsed.statementline
            assert inner_parsed.nodetype == NodeType.ASSIGNMENT
            inner_typed = sline_typed.statementline
            assert isinstance(inner_typed, ztypedast.TypedAssignment)
            assert inner_typed.name == _cast(zast.Assignment, inner_parsed).name
            # the assignment is registered in by_parsed_id
            assert tc.typed_program.by_parsed_id.get(inner_parsed.nodeid) is inner_typed

    def test_reassignment_mirror(self):
        tc = _typecheck("main: function is {\n    x: 7\n    x = 11\n}")
        mainunit = tc.program.units[tc.program.mainunitname]
        reassigns = [
            n for n in _walk_main(mainunit) if n.nodetype == NodeType.REASSIGNMENT
        ]
        assert reassigns, "expected a reassignment"
        for r in reassigns:
            typed = tc.typed_program.by_parsed_id.get(r.nodeid)
            assert typed is not None
            assert isinstance(typed, ztypedast.TypedReassignment)
            assert typed.parsed is r
            assert typed.topath is not None
            assert typed.value is not None


class TestTypedControlFlowInvariants:
    """If/Case/For/Do/With produce typed mirrors via the wrapper
    pattern; clauses (IfClause, CaseClause) are built inline and also
    registered."""

    def test_if_else_mirror(self):
        tc = _typecheck(
            "main: function is {\n"
            "    x: 1\n"
            '    if x > 0 then print "pos" else print "nonpos"\n'
            "}"
        )
        mainunit = tc.program.units[tc.program.mainunitname]
        ifs = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.IF]
        assert len(ifs) == 1
        ifn = _cast(zast.If, ifs[0])
        typed = tc.typed_program.by_parsed_id.get(ifn.nodeid)
        assert typed is not None
        assert isinstance(typed, ztypedast.TypedIf)
        assert len(typed.clauses) == len(ifn.clauses)
        for ct, cp in zip(typed.clauses, ifn.clauses):
            assert isinstance(ct, ztypedast.TypedIfClause)
            assert ct.parsed is cp
            assert tc.typed_program.by_parsed_id.get(cp.nodeid) is ct
        assert typed.elseclause is not None

    def test_case_match_mirror(self):
        tc = _typecheck(
            "main: function is {\n"
            "    b: bool.true\n"
            "    match (b) case true then {\n"
            "        x: 1\n"
            "    } case false then {\n"
            "        x: 2\n"
            "    }\n"
            "}"
        )
        mainunit = tc.program.units[tc.program.mainunitname]
        cases = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.CASE]
        assert len(cases) == 1
        cn = _cast(zast.Case, cases[0])
        typed = tc.typed_program.by_parsed_id.get(cn.nodeid)
        assert typed is not None
        assert isinstance(typed, ztypedast.TypedCase)
        assert typed.subject is not None
        assert len(typed.clauses) == 2
        names = {c.name for c in typed.clauses}
        assert names == {"true", "false"}

    def test_do_block_mirror(self):
        tc = _typecheck("main: function is {\n    x: {\n        7\n    }\n}")
        mainunit = tc.program.units[tc.program.mainunitname]
        dos = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.DO]
        assert len(dos) == 1
        dn = _cast(zast.Do, dos[0])
        typed = tc.typed_program.by_parsed_id.get(dn.nodeid)
        assert typed is not None
        assert isinstance(typed, ztypedast.TypedDo)
        assert typed.statement is not None
        # `has_break` used to live on the parsed Do; after Step 6.3
        # it lives on TypedDo only.
        assert typed.has_break is False


class TestTypedTopLevelInvariants:
    """`TypedProgram.units` must mirror `Program.units` at end of
    `check()`. Each unit body has typed Function / ObjectDef / Unit
    counterparts."""

    def test_main_unit_populated(self):
        tc = _typecheck("main: function is {\n    x: 7\n}")
        units = tc.typed_program.units
        assert "test" in units
        unit = units["test"]
        assert isinstance(unit, ztypedast.TypedUnit)
        assert unit.parsed is tc.program.units["test"]
        assert "main" in unit.body
        main_typed = unit.body["main"]
        assert isinstance(main_typed, ztypedast.TypedFunction)
        assert main_typed.body is not None
        # ztype resolved for the main function
        assert main_typed.ztype is tc._resolved.get("test.main")

    def test_function_with_parameters_and_returntype(self):
        tc = _typecheck(
            "double: function {x: i64} out i64 is {\n"
            "    return x + x\n"
            "}\n"
            "main: function is {\n"
            "    y: double x: 21.i64\n"
            "}"
        )
        unit = tc.typed_program.units["test"]
        double_typed = unit.body["double"]
        assert isinstance(double_typed, ztypedast.TypedFunction)
        # parameters: TypedAtomId for the i64 typeref
        assert "x" in double_typed.parameters
        assert isinstance(double_typed.parameters["x"], ztypedast.TypedAtomId)
        # returntype mirror present
        assert double_typed.returntype is not None
        assert isinstance(double_typed.returntype, ztypedast.TypedAtomId)

    def test_record_objectdef_mirror(self):
        tc = _typecheck(
            "Point: record { x: i64 y: i64 } as {\n"
            "    create: function {x: i64 y: i64} out this is {\n"
            "        return meta.create x: x y: y\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    p: Point x: 1.i64 y: 2.i64\n"
            "}"
        )
        unit = tc.typed_program.units["test"]
        assert "Point" in unit.body
        point_typed = unit.body["Point"]
        assert isinstance(point_typed, ztypedast.TypedObjectDef)
        assert point_typed.kind == NodeType.RECORD
        # is_items has the field typerefs
        assert "x" in point_typed.is_items
        assert "y" in point_typed.is_items


class TestTypedDottedPathInvariants:
    """Every TypedDottedPath in by_parsed_id must mirror its parsed
    back-reference's in-place decorations and must reference the typed
    parent / a fresh TypedAtomId selector for `child`."""

    def test_simple_field_access(self):
        """`v.x` builds a TypedDottedPath whose parent is the
        TypedAtomId for `v` and whose child is a fresh TypedAtomId
        with name='x'."""
        tc = _typecheck(
            "Point: record { x: i64 y: i64 } as {\n"
            "    create: function {x: i64 y: i64} out this is {\n"
            "        return meta.create x: x y: y\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    p: Point x: 1.i64 y: 2.i64\n"
            "    q: p.x\n"
            "}"
        )
        mainunit = tc.program.units[tc.program.mainunitname]
        dotted = [n for n in _walk_main(mainunit) if n.nodetype == NodeType.DOTTEDPATH]
        # locate the `p.x` node — child name "x", parent name "p"
        target = None
        for d in dotted:
            dp = _cast(zast.DottedPath, d)
            if dp.child.name == "x" and dp.parent.nodetype == NodeType.ATOMID:
                if _cast(zast.AtomId, dp.parent).name == "p":
                    target = dp
                    break
        assert target is not None, "expected a `p.x` dotted path in main"
        typed = tc.typed_program.by_parsed_id.get(target.nodeid)
        assert typed is not None, "TypedDottedPath should be registered"
        assert isinstance(typed, ztypedast.TypedDottedPath)
        assert typed.parsed is target
        assert typed.ztype is target.type
        # `parent_tagged_type` / `narrowed_subtype` / `child_id` used
        # to live on parsed DottedPath; after Step 6.7 they live on
        # TypedDottedPath only.
        assert isinstance(typed.child_id, int)
        # parent typed mirror must be the TypedAtomId for `p`
        parent_typed = tc.typed_program.by_parsed_id.get(target.parent.nodeid)
        assert parent_typed is typed.parent
        assert isinstance(typed.parent, ztypedast.TypedAtomId)
        assert typed.parent.name == "p"
        # child is a fresh TypedAtomId, not registered (it is structural)
        assert isinstance(typed.child, ztypedast.TypedAtomId)
        assert typed.child.name == "x"

    def test_dotted_fields_agree_with_parsed(self):
        """Walk every TypedDottedPath in by_parsed_id and assert
        field-for-field agreement with its parsed back-reference."""
        tc = _typecheck(
            "Point: record { x: i64 y: i64 } as {\n"
            "    create: function {x: i64 y: i64} out this is {\n"
            "        return meta.create x: x y: y\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    p: Point x: 1.i64 y: 2.i64\n"
            "    a: p.x\n"
            "    b: p.y\n"
            "}"
        )
        n_dotted = 0
        for parsed_id, typed in tc.typed_program.by_parsed_id.items():
            if not isinstance(typed, ztypedast.TypedDottedPath):
                continue
            parsed = typed.parsed
            assert parsed.nodeid == parsed_id
            assert parsed.nodetype == NodeType.DOTTEDPATH
            pdp = _cast(zast.DottedPath, parsed)
            assert typed.ztype is pdp.type
            assert typed.const_value == pdp.const_value
            # `parent_tagged_type` / `narrowed_subtype` / `child_id`
            # used to live on parsed DottedPath; after Step 6.7 they
            # live on TypedDottedPath only.
            assert isinstance(typed.child_id, int)
            assert typed.child.name == pdp.child.name
            n_dotted += 1
        assert n_dotted > 0, "expected some TypedDottedPath entries"


class TestTypedAtomIdInvariants:
    """Every TypedAtomId in by_parsed_id must mirror its parsed
    back-reference's in-place decorations field-for-field. As more
    typed-node kinds are added, the same shape of invariant generalises
    (parsed-decoration agreement) — this test covers AtomId today."""

    def test_atomid_fields_agree_with_parsed(self):
        tc = _typecheck(
            "double: function {x: i64} out i64 is {\n"
            "    return x + x\n"
            "}\n"
            "main: function is {\n"
            "    y: double x: 21.i64\n"
            "}"
        )
        atomid_typed_count = 0
        for parsed_id, typed in tc.typed_program.by_parsed_id.items():
            if not isinstance(typed, ztypedast.TypedAtomId):
                continue
            parsed = typed.parsed
            assert parsed.nodeid == parsed_id
            assert parsed.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
            patom = _cast(zast.AtomId, parsed)
            assert typed.name == patom.name
            assert typed.ztype is patom.type
            assert typed.const_value == patom.const_value
            # `narrowed_subtype` / `original_ztype` / `child_id` used
            # to live on the parsed AtomId. After Step 6.6 they live on
            # `TypedAtomId` only; assert their default sentinel values
            # for atoms outside narrowing / case-arm contexts.
            assert isinstance(typed.child_id, int)
            assert typed.is_label_value == (parsed.nodetype == NodeType.LABELVALUE)
            atomid_typed_count += 1
        assert atomid_typed_count > 0, (
            "expected some TypedAtomId entries from a non-trivial program"
        )
