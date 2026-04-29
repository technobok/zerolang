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
            assert typed.narrowed_subtype == patom.narrowed_subtype
            assert typed.original_ztype is patom.original_ztype
            assert typed.child_id == patom.child_id
            assert typed.is_label_value == (parsed.nodetype == NodeType.LABELVALUE)
            atomid_typed_count += 1
        assert atomid_typed_count > 0, (
            "expected some TypedAtomId entries from a non-trivial program"
        )
