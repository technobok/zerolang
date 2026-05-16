"""
Phase 7c: ZEntry/ZVariable/ZScope id surfaces + SQL dump for the symbol table.
"""

import os
import sqlite3

import pytest

import zast
from conftest import make_parser
from ztypecheck import typecheck
from zsqldump import dump_sql
from ztypes import ZEntry, ZType, ZTypeType, ZOwnership, ZVariable
from ztyping import ZTyping
from zenv import ZSymbolTable


def _empty_typing() -> ZTyping:
    """Construct a minimal ZTyping for tests that just need the
    type_child / type_generic_arg tables."""
    from zvfs import ZVfs

    return ZTyping(parsed=zast.Program(vfs=ZVfs(), units={}, mainunitname=""))


pytestmark = pytest.mark.infra

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def _make_ztype(name: str, tt: ZTypeType = ZTypeType.RECORD) -> ZType:
    return ZType(name=name, typetype=tt, parent=None)


def _parse_check(src: str, unitname: str = "test"):
    program = make_parser(src, unitname=unitname, src_dir=LIB_DIR).parse()
    typing = typecheck(program)
    assert typing.errors == [], f"unexpected errors: {[e.msg for e in typing.errors]}"
    return program, typing


# F5.E.5: alias retained for callers using this name.
_parse_check_typing = _parse_check


class TestEntryIdInfrastructure:
    def test_entry_id_is_monotonic(self):
        t = _make_ztype("rec")
        a = ZEntry(name="a", ztype=t, is_definition=True)
        b = ZEntry(name="b", ztype=t, is_definition=True)
        assert a.entry_id < b.entry_id

    def test_entry_id_is_distinct(self):
        t = _make_ztype("rec")
        ids = {
            ZEntry(name=f"n{i}", ztype=t, is_definition=True).entry_id for i in range(8)
        }
        assert len(ids) == 8

    def test_new_id_fields_default_none(self):
        t = _make_ztype("rec")
        e = ZEntry(name="x", ztype=t, is_definition=True)
        assert e.narrowed_subtype_id is None
        assert e.excluded_subtype_ids is None


class TestNarrowStampsIds:
    def test_narrow_stamps_narrowed_subtype_id(self):
        outer = _make_ztype("Result", ZTypeType.UNION)
        ok = _make_ztype("ok_payload", ZTypeType.RECORD)
        typing = _empty_typing()
        typing.set_child(outer, "ok", ok)

        st = ZSymbolTable(typing=typing)
        st.push("main")
        v = ZVariable(ztype=outer, ownership=ZOwnership.OWNED)
        st.define_var("r", v)

        expected_id = outer.child_id_for("ok")
        st.narrow("r", to_type=outer, subtype_name="ok", shadow=True)
        entry = st.lookup_entry("r")
        assert entry is not None
        assert entry.narrowed_subtype == "ok"
        assert entry.narrowed_subtype_id == expected_id
        # round-trip via ZTyping.child_by_id
        assert typing.child_by_id(outer, entry.narrowed_subtype_id) is ok

    def test_exclude_stamps_excluded_subtype_ids(self):
        outer = _make_ztype("Result", ZTypeType.UNION)
        typing = _empty_typing()
        typing.set_child(outer, "ok", _make_ztype("ok_payload", ZTypeType.RECORD))
        typing.set_child(outer, "err", _make_ztype("err_payload", ZTypeType.RECORD))
        typing.set_child(outer, "none", _make_ztype("none", ZTypeType.NULL))

        st = ZSymbolTable(typing=typing)
        st.push("main")
        v = ZVariable(ztype=outer, ownership=ZOwnership.OWNED)
        st.define_var("r", v)

        st.exclude("r", "ok", outer)
        entry = st.lookup_entry("r")
        assert entry is not None
        assert entry.excluded_subtypes == frozenset({"ok"})
        assert entry.excluded_subtype_ids is not None
        # same cardinality as the string set
        assert len(entry.excluded_subtype_ids) == len(entry.excluded_subtypes)
        # ok's id on `outer` is in the set
        assert outer.child_id_for("ok") in entry.excluded_subtype_ids


class TestScopeHistory:
    def test_popped_scopes_archived(self):
        st = ZSymbolTable()
        marker = st.push_block("outer")
        inner = st.push("inner")
        assert inner.scope_id in {s.scope_id for s in st._scopes}
        st.pop_to(marker)
        # inner scope is no longer live but is archived
        live = {s.scope_id for s in st._scopes}
        archived = {s.scope_id for s in st._history}
        assert inner.scope_id not in live
        assert inner.scope_id in archived


class TestScopeLog:
    def test_log_captures_push_pop_with_parent_and_seq(self):
        # F6: scope_log records each push and stamps closed_at_seq on pop,
        # so the SQL dump can reconstruct the full history from one table.
        st = ZSymbolTable()
        outer_marker = st.push_block("outer")
        st.push("inner")
        st.pop()  # close inner
        st.pop_to(outer_marker)  # close outer

        log = st.scope_log
        # one row per push
        assert len(log) == 2
        outer_row, inner_row = log
        # parent_id is wired correctly: outer has no parent, inner's parent is outer
        assert outer_row.parent_id is None
        assert inner_row.parent_id == outer_row.scope_id
        # both rows got closed_at_seq stamped on pop
        assert outer_row.closed_at_seq is not None
        assert inner_row.closed_at_seq is not None
        # seq is monotonically increasing across both push and pop events
        assert outer_row.opened_at_seq == 0
        assert inner_row.opened_at_seq == 1
        # inner closes before outer; outer closes last
        assert inner_row.closed_at_seq < outer_row.closed_at_seq

    def test_log_row_matches_archived_scope(self):
        # While _history is still maintained, every scope_log row with a
        # closed_at_seq has a matching archived scope. (When _history is
        # retired, this test goes away.)
        st = ZSymbolTable()
        marker = st.push_block("outer")
        inner = st.push("inner")
        st.pop_to(marker)
        archived_ids = {s.scope_id for s in st._history}
        closed_log_ids = {
            row.scope_id for row in st.scope_log if row.closed_at_seq is not None
        }
        assert inner.scope_id in archived_ids
        assert inner.scope_id in closed_log_ids
        assert archived_ids == closed_log_ids


class TestSqlDumpSymtab:
    def _compile_and_dump(self, src: str) -> str:
        _program, typing = _parse_check_typing(src)
        return dump_sql(typing)

    def test_scopes_and_entries_and_variables_populated(self):
        src = """
Result: union { ok: i64 err: String none: null }
main: function is {
    r: Result.ok 42
    match (
        r
    ) case ok then { a: 1 } case err then { b: 2 } case none then { c: 3 }
}
"""
        sql = self._compile_and_dump(src)
        con = sqlite3.connect(":memory:")
        con.executescript(sql)

        n_scopes = con.execute("SELECT COUNT(*) FROM scope").fetchone()[0]
        n_entries = con.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
        n_vars = con.execute("SELECT COUNT(*) FROM variable").fetchone()[0]
        assert n_scopes > 0, "expected at least one scope row"
        assert n_entries > 0, "expected at least one entry row"
        assert n_vars > 0, "expected at least one variable row"

        # F6: narrowing state lives in the narrowed_subtype child
        # table, one row per narrowed-to or excluded subtype.
        n_narrow = con.execute(
            "SELECT COUNT(*) FROM narrowed_subtype WHERE excluded = 0"
        ).fetchone()[0]
        n_narrow_id = con.execute(
            "SELECT COUNT(*) FROM narrowed_subtype "
            "WHERE excluded = 0 AND type_id IS NOT NULL"
        ).fetchone()[0]
        assert n_narrow > 0, "match arms should produce narrowed_subtype rows"
        assert n_narrow == n_narrow_id, "every narrowed-to row must carry a type_id"

    def test_scopes_kind_values_expected(self):
        src = """
main: function is {
    x: 1
}
"""
        sql = self._compile_and_dump(src)
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        kinds = {k for (k,) in con.execute("SELECT DISTINCT kind FROM scope")}
        assert kinds.issubset({"BLOCK", "CALL", "OVERLAY"})
        assert "BLOCK" in kinds

    def test_dump_tolerates_no_symbol_table(self):
        # dump_sql must still work when typing.symbol_table is unset.
        src = "main: function is { x: 1 }"
        _program, typing = _parse_check_typing(src)
        typing.symbol_table = None
        sql = dump_sql(typing)
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        assert con.execute("SELECT COUNT(*) FROM scope").fetchone()[0] == 0
