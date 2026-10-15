/* Row.columnName */
static z_@ResultRV_StringView_columnerror@_t z_@Row@_columnName(const z_@Row@_t* t, uint64_t column) {
    return z_sqlite_name_result(z_sqlite_row_stmt(t), column, sqlite3_column_name);
}

