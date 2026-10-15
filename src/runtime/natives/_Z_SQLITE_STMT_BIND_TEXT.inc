/* Statement.bindText */
static void z_@Statement@_bindText(z_@Statement@_t* t, uint64_t parameter, z_@StringView@_t value) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    int rc = sqlite3_bind_text64(st, (int)parameter, value.size ? value.data : "", (sqlite3_uint64)value.size, Z_SQLITE_TRANSIENT, SQLITE_UTF8);
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

