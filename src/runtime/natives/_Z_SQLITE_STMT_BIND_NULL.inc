/* Statement.bindNull */
static void z_@Statement@_bindNull(z_@Statement@_t* t, uint64_t parameter) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    int rc = sqlite3_bind_null(st, (int)parameter);
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

