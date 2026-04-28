"""
Phase 7d: id-keyed cross-File unit resolution + SQL `unit` table.
"""

import os
import sqlite3

import pytest

from conftest import make_parser
from ztypecheck import typecheck, TypeChecker
from zsqldump import dump_sql

pytestmark = pytest.mark.infra

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")


def _parse(src: str, unitname: str = "test"):
    return make_parser(src, unitname=unitname, src_dir=LIB_DIR).parse()


def _parse_check(src: str, unitname: str = "test"):
    program = _parse(src, unitname)
    errors = typecheck(program)
    assert errors == [], f"unexpected errors: {[e.msg for e in errors]}"
    return program


class TestUnitTypesById:
    def test_parity_at_init(self):
        program = _parse('main: function is { print "hi" }')
        tc = TypeChecker(program)
        assert set(tc.unit_types) == set(program.units)
        for uname, unit_ast in program.units.items():
            assert unit_ast.nodeid in tc.unit_types_by_id
            assert tc.unit_types_by_id[unit_ast.nodeid] is tc.unit_types[uname]

    def test_id_lookup_hits_after_resolution(self):
        program = _parse_check('main: function is { print "hi" }')
        # after typecheck, system unit's ZType is reachable by its AST nodeid
        system_ast = program.units["system"]
        assert system_ast.nodeid in program.unit_types_by_id
        system_type = program.unit_types_by_id[system_ast.nodeid]
        assert system_type.nodeid >= 0
        # sanity: Unit AST nodeid is distinct from unit ZType nodeid
        assert system_ast.nodeid != system_type.nodeid


class TestUnitSqlDump:
    def _dump(self, src: str) -> str:
        return dump_sql(_parse_check(src))

    def test_unit_table_populated(self):
        sql = self._dump('main: function is { print "hi" }')
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        n = con.execute("SELECT COUNT(*) FROM unit").fetchone()[0]
        assert n >= 2, f"expected at least 2 unit rows (main + system), got {n}"

    def test_exactly_one_main_unit(self):
        sql = self._dump('main: function is { print "hi" }')
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        n_main = con.execute("SELECT COUNT(*) FROM unit WHERE is_main = 1").fetchone()[
            0
        ]
        assert n_main == 1

    def test_unit_type_id_populated_for_system_units(self):
        sql = self._dump('main: function is { print "hi" }')
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        null_rows = con.execute(
            "SELECT name FROM unit WHERE unit_type_id IS NULL"
        ).fetchall()
        assert null_rows == [], (
            f"unit_type_id should be populated for all parsed units, got NULL for {null_rows}"
        )

    def test_unit_id_references_ast_nodes(self):
        # unit.unit_id is the Unit AST's Node.nodeid, so it must appear in ast_nodes.
        sql = self._dump('main: function is { print "hi" }')
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        n = con.execute(
            "SELECT COUNT(*) FROM unit u "
            "WHERE NOT EXISTS (SELECT 1 FROM ast_nodes a WHERE a.node_id = u.unit_id)"
        ).fetchone()[0]
        assert n == 0, "every unit_id should have a matching ast_nodes row"

    def test_dump_tolerates_missing_unit_types_snapshot(self):
        program = _parse_check('main: function is { print "hi" }')
        program.unit_types_by_id = {}
        sql = dump_sql(program)
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        # rows still emitted; unit_type_id is NULL
        rows = con.execute("SELECT COUNT(*) FROM unit").fetchone()[0]
        assert rows >= 2
        nulls = con.execute(
            "SELECT COUNT(*) FROM unit WHERE unit_type_id IS NULL"
        ).fetchone()[0]
        assert nulls == rows
