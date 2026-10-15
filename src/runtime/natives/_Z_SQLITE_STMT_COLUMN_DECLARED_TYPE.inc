/* Statement.columnDeclaredType */
static z_@ResultRV_StringView_columnerror@_t z_@Statement@_columnDeclaredType(const z_@Statement@_t* t, uint64_t column) {
    return z_sqlite_name_result(z_sqlite_stmt(t), column, sqlite3_column_decltype);
}

