"""
Phase 7b: tests for ZType child_id infrastructure + emitter stamp invariants.
"""

import os

import pytest

from conftest import make_parser
from ztypecheck import typecheck
from ztypes import ZType, ZTypeType
from ztyping import ZTyping
import zast


def _empty_typing() -> ZTyping:
    from zvfs import ZVfs

    return ZTyping(parsed=zast.Program(vfs=ZVfs(), units={}, mainunitname=""))


pytestmark = pytest.mark.infra

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def _make_ztype(name: str, tt: ZTypeType = ZTypeType.RECORD) -> ZType:
    return ZType(name=name, typetype=tt)


class TestChildIdInfrastructure:
    def test_child_id_for_is_stable(self):
        t = _make_ztype("parent")
        a = t.child_id_for("foo")
        b = t.child_id_for("foo")
        assert a == b

    def test_child_id_for_mints_distinct_ids(self):
        t = _make_ztype("parent")
        a = t.child_id_for("foo")
        b = t.child_id_for("bar")
        assert a != b

    def test_child_id_for_globally_monotonic_across_types(self):
        t1 = _make_ztype("t1")
        t2 = _make_ztype("t2")
        a = t1.child_id_for("x")
        b = t2.child_id_for("x")
        # globally unique — ids minted on different parents do not collide
        assert a != b

    def test_child_by_id_returns_none_when_no_child(self):
        t = _make_ztype("parent")
        cid = t.child_id_for("foo")
        typing = _empty_typing()
        assert typing.child_by_id(t, cid) is None

    def test_child_by_id_round_trips_when_child_present(self):
        parent = _make_ztype("parent")
        child = _make_ztype("child")
        typing = _empty_typing()
        typing.set_child(parent, "foo", child)
        cid = parent.child_id_for("foo")
        assert typing.child_by_id(parent, cid) is child

    def test_child_by_id_returns_none_for_unknown_id(self):
        t = _make_ztype("parent")
        # minted on a different type — must not resolve on t
        other = _make_ztype("other")
        alien = other.child_id_for("anything")
        typing = _empty_typing()
        assert typing.child_by_id(t, alien) is None


def _parse_check(source: str, unitname: str = "test"):
    program = make_parser(source, unitname=unitname, src_dir=LIB_DIR).parse()
    typing = typecheck(program)
    assert typing.errors == [], f"unexpected errors: {[e.msg for e in typing.errors]}"
    return program, typing


def _walk(node):
    if hasattr(node, "nodetype"):
        yield node
    if hasattr(node, "__dataclass_fields__"):
        for fname in node.__dataclass_fields__:
            val = getattr(node, fname, None)
            if val is None:
                continue
            if hasattr(val, "nodetype"):
                yield from _walk(val)
            elif type(val) is list:
                for v in val:
                    if hasattr(v, "nodetype"):
                        yield from _walk(v)
            elif type(val) is dict:
                for v in val.values():
                    if hasattr(v, "nodetype"):
                        yield from _walk(v)


class TestEmitterStampInvariant:
    def test_dotted_path_child_id_stamped(self):
        src = """
myrec: record { x: i64 y: i64 }
main: function is {
    p: myrec x: 1 y: 2
    v: p.x
}
"""
        program, typing = _parse_check(src)
        # F5.E.4.d: child_id is on `ZTyping.dp_child_id` keyed by parsed
        # DottedPath nodeid. Walk parsed nodes and check the stamps.
        dp_child_id = typing.dp_child_id
        stamped = 0

        def _walk(node):
            nonlocal stamped
            if node is None:
                return
            if node.nodetype == zast.NodeType.DOTTEDPATH:
                if dp_child_id.get(node.nodeid, -1) != -1:
                    stamped += 1
            for child in zast.node_children(node):
                _walk(child)

        for unit in program.units.values():
            _walk(unit)
        assert stamped >= 1, (
            "expected at least one DottedPath to carry a stamped child_id"
        )

    def test_match_clause_match_child_id_stamped(self):
        src = """
Result: union {
    ok: i64
    err: String
    none: null
}

main: function is {
    r: Result.ok 42
    match (
        r
    ) case ok then {
        a: 1
    } case err then {
        b: 2
    } case none then {
        c: 3
    }
}
"""
        program, typing = _parse_check(src)
        # F5.E.4.d: child_id for case-clause match selectors lives on
        # `ZTyping.atom_child_id` keyed by the parsed `clause.match`
        # AtomId's nodeid. Walk parsed clauses and check the stamps.
        atom_child_id = typing.atom_child_id
        stamped = 0

        def _walk(node):
            nonlocal stamped
            if node is None:
                return
            if node.nodetype == zast.NodeType.CASECLAUSE:
                if atom_child_id.get(node.match.nodeid, -1) != -1:
                    stamped += 1
            for child in zast.node_children(node):
                _walk(child)

        for unit in program.units.values():
            _walk(unit)
        assert stamped >= 2, (
            f"expected both match clauses to carry stamped child_ids, got {stamped}"
        )
