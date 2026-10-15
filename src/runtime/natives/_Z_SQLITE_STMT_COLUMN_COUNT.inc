/* Statement.columnCount */
static uint64_t z_@Statement@_columnCount(const z_@Statement@_t* t) {
    sqlite3_stmt* st = z_sqlite_stmt(t);
    return st ? (uint64_t)sqlite3_column_count(st) : 0;
}

