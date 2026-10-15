/* Statement.bindInteger */
static void z_@Statement@_bindInteger(z_@Statement@_t* t, uint64_t parameter, int64_t value) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    int rc = sqlite3_bind_int64(st, (int)parameter, (sqlite3_int64)value);
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

