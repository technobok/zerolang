"""
Phase 7b: tests for ZType child_id infrastructure + emitter stamp invariants.
"""

import os

import pytest

from conftest import make_parser
from ztypecheck import typecheck
from ztypes import ZType, ZTypeType
from zast import NodeType
import zast

pytestmark = pytest.mark.infra

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def _make_ztype(name: str, tt: ZTypeType = ZTypeType.RECORD) -> ZType:
    return ZType(name=name, typetype=tt, parent=None)


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

    def test_resolve_child_by_id_returns_none_when_no_child(self):
        t = _make_ztype("parent")
        cid = t.child_id_for("foo")
        assert t.resolve_child_by_id(cid) is None

    def test_resolve_child_by_id_round_trips_when_child_present(self):
        parent = _make_ztype("parent")
        child = _make_ztype("child")
        parent.children["foo"] = child
        cid = parent.child_id_for("foo")
        assert parent.resolve_child_by_id(cid) is child

    def test_resolve_child_by_id_returns_none_for_unknown_id(self):
        t = _make_ztype("parent")
        # minted on a different type — must not resolve on t
        other = _make_ztype("other")
        alien = other.child_id_for("anything")
        assert t.resolve_child_by_id(alien) is None


def _parse_check(source: str, unitname: str = "test"):
    program = make_parser(source, unitname=unitname, src_dir=LIB_DIR).parse()
    errors = typecheck(program)
    assert errors == [], f"unexpected errors: {[e.msg for e in errors]}"
    return program


def _walk(node):
    if getattr(node, "is_node", False):
        yield node
    if hasattr(node, "__dataclass_fields__"):
        for fname in node.__dataclass_fields__:
            val = getattr(node, fname, None)
            if val is None:
                continue
            if getattr(val, "is_node", False):
                yield from _walk(val)
            elif type(val) is list:
                for v in val:
                    if getattr(v, "is_node", False):
                        yield from _walk(v)
            elif type(val) is dict:
                for v in val.values():
                    if getattr(v, "is_node", False):
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
        program = _parse_check(src)
        stamped = 0
        for node in _walk(program):
            if node.nodetype == NodeType.DOTTEDPATH:
                dp = zast.cast(zast.DottedPath, node)
                if dp.child_id != -1:
                    stamped += 1
        assert stamped >= 1, (
            "expected at least one DottedPath to carry a stamped child_id"
        )

    def test_match_clause_match_child_id_stamped(self):
        src = """
result: union {
    ok: i64
    err: string
    none: null
}

main: function is {
    r: result.ok 42
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
        program = _parse_check(src)
        stamped = 0
        for node in _walk(program):
            if node.nodetype == NodeType.CASECLAUSE:
                clause = zast.cast(zast.CaseClause, node)
                if clause.match.child_id != -1:
                    stamped += 1
        assert stamped >= 2, (
            f"expected both match clauses to carry stamped child_ids, got {stamped}"
        )
