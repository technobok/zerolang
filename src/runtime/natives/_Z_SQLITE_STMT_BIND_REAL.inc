/* Statement.bindReal */
static void z_@Statement@_bindReal(z_@Statement@_t* t, uint64_t parameter, double value) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    int rc = sqlite3_bind_double(st, (int)parameter, value);
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

