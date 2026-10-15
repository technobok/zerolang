/* Statement.bindBlob */
static void z_@Statement@_bindBlob(z_@Statement@_t* t, uint64_t parameter, z_@ListViewVal_u8@_t value) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    int rc = sqlite3_bind_blob64(st, (int)parameter, value.length ? (const void*)value.data : (const void*)"", (sqlite3_uint64)value.length, Z_SQLITE_TRANSIENT);
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

