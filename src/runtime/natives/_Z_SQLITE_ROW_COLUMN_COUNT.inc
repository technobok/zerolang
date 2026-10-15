/* Row.columnCount */
static uint64_t z_@Row@_columnCount(const z_@Row@_t* t) {
    return (uint64_t)sqlite3_column_count(z_sqlite_row_stmt(t));
}

