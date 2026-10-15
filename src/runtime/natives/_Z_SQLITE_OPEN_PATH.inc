/* sqlite.openPath -- sqlite3_open_v2 with extended result codes on, then the
   connection's 5 second busy timeout. A failed open closes the half-made
   connection, and its error names the file. */
static z_@Result_Database_SqliteError@_t z_sqlite_openPath(z_@StringView@_t path, int32_t flags) {
    z_@Result_Database_SqliteError@_t result = {0};
    char* p = z_sqlite_cstr(path);
    sqlite3* db = NULL;
    int rc = sqlite3_open_v2(p, &db, (int)flags | SQLITE_OPEN_EXRESCODE, NULL);
    if (rc != SQLITE_OK) {
        result.tag = @ARM_Result_Database_SqliteError.err@;
        result.data = z_sqlite_error(db, rc, p);
        if (db) sqlite3_close_v2(db);
        free(p);
        return result;
    }
    free(p);
    sqlite3_busy_timeout(db, 5000);
    z_@Database@_t* d = (z_@Database@_t*)z_xmalloc(sizeof(z_@Database@_t));
    d->@MEMBER_Database.handle@ = (uint64_t)(uintptr_t)db;
    d->@MEMBER_Database.closed@ = false;
    result.tag = @ARM_Result_Database_SqliteError.ok@;
    result.data = d;
    return result;
}

