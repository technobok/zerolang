"""
Phase 7c: Entry/Variable/Scope id surfaces + SQL dump for the symbol table.
"""

import os
import sqlite3

import pytest

from conftest import make_parser
from ztypecheck import typecheck
from zsqldump import dump_sql
from ztypes import Entry, ZType, ZTypeType, ZOwnership, ZNaming, ZVariable
from zenv import SymbolTable

pytestmark = pytest.mark.infra

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def _make_ztype(name: str, tt: ZTypeType = ZTypeType.RECORD) -> ZType:
    return ZType(name=name, typetype=tt, parent=None)


def _parse_check(src: str, unitname: str = "test"):
    program = make_parser(src, unitname=unitname, src_dir=LIB_DIR).parse()
    errors = typecheck(program)
    assert errors == [], f"unexpected errors: {[e.msg for e in errors]}"
    return program


class TestEntryIdInfrastructure:
    def test_entry_id_is_monotonic(self):
        t = _make_ztype("rec")
        a = Entry(name="a", ztype=t, is_definition=True)
        b = Entry(name="b", ztype=t, is_definition=True)
        assert a.entry_id < b.entry_id

    def test_entry_id_is_distinct(self):
        t = _make_ztype("rec")
        ids = {
            Entry(name=f"n{i}", ztype=t, is_definition=True).entry_id for i in range(8)
        }
        assert len(ids) == 8

    def test_new_id_fields_default_none(self):
        t = _make_ztype("rec")
        e = Entry(name="x", ztype=t, is_definition=True)
        assert e.narrowed_subtype_id is None
        assert e.excluded_subtype_ids is None


class TestNarrowStampsIds:
    def test_narrow_stamps_narrowed_subtype_id(self):
        outer = _make_ztype("result", ZTypeType.UNION)
        ok = _make_ztype("ok_payload", ZTypeType.RECORD)
        outer.children["ok"] = ok

        st = SymbolTable()
        st.push("main")
        v = ZVariable(ztype=outer, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
        st.define_var("r", v)

        expected_id = outer.child_id_for("ok")
        st.narrow("r", to_type=outer, subtype_name="ok", shadow=True)
        entry = st.lookup_entry("r")
        assert entry is not None
        assert entry.narrowed_subtype == "ok"
        assert entry.narrowed_subtype_id == expected_id
        # round-trip via resolve_child_by_id
        assert outer.resolve_child_by_id(entry.narrowed_subtype_id) is ok

    def test_exclude_stamps_excluded_subtype_ids(self):
        outer = _make_ztype("result", ZTypeType.UNION)
        outer.children["ok"] = _make_ztype("ok_payload", ZTypeType.RECORD)
        outer.children["err"] = _make_ztype("err_payload", ZTypeType.RECORD)
        outer.children["none"] = _make_ztype("none", ZTypeType.NULL)

        st = SymbolTable()
        st.push("main")
        v = ZVariable(ztype=outer, ownership=ZOwnership.OWNED, named=ZNaming.NAMED)
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
        st = SymbolTable()
        marker = st.push_block("outer")
        inner = st.push("inner")
        assert inner.scope_id in {s.scope_id for s in st._scopes}
        st.pop_to(marker)
        # inner scope is no longer live but is archived
        live = {s.scope_id for s in st._scopes}
        archived = {s.scope_id for s in st._history}
        assert inner.scope_id not in live
        assert inner.scope_id in archived


class TestSqlDumpSymtab:
    def _compile_and_dump(self, src: str) -> str:
        program = _parse_check(src)
        return dump_sql(program)

    def test_scopes_and_entries_and_variables_populated(self):
        src = """
result: union { ok: i64 err: string none: null }
main: function is {
    r: result.ok 42
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

        n_narrow = con.execute(
            "SELECT COUNT(*) FROM entry WHERE narrowed_subtype IS NOT NULL"
        ).fetchone()[0]
        n_narrow_id = con.execute(
            "SELECT COUNT(*) FROM entry WHERE narrowed_subtype_id IS NOT NULL"
        ).fetchone()[0]
        assert n_narrow > 0, "match arms should have narrowed entries"
        assert n_narrow == n_narrow_id, (
            "every narrowed entry must carry a narrowed_subtype_id"
        )

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
        # dump_sql must still work when program.symbol_table is unset.
        src = "main: function is { x: 1 }"
        program = _parse_check(src)
        program.symbol_table = None
        sql = dump_sql(program)
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        assert con.execute("SELECT COUNT(*) FROM scope").fetchone()[0] == 0
