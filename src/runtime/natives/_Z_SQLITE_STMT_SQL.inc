/* Statement.sql -- the SQL compiled, a view the statement keeps alive */
static z_@StringView@_t z_@Statement@_sql(const z_@Statement@_t* t) {
    sqlite3_stmt* st = z_sqlite_stmt(t);
    const char* s = st ? sqlite3_sql(st) : "";
    z_@StringView@_t v = { .data = s, .size = (uint64_t)strlen(s) };
    return v;
}

