/* Database.busyTimeout -- how long a statement meeting another connection's
   lock waits before failing with busy; 0 fails at once */
static z_@Result_null_SqliteError@_t z_@Database@_busyTimeout(z_@Database@_t* t, int32_t milliseconds) {
    if (t->@MEMBER_Database.closed@) return z_sqlite_null_misuse("the database is closed");
    int rc = sqlite3_busy_timeout(z_sqlite_db(t), (int)milliseconds);
    if (rc != SQLITE_OK) {
        z_@Result_null_SqliteError@_t result = {0};
        result.tag = @ARM_Result_null_SqliteError.err@;
        result.data = z_sqlite_error(z_sqlite_db(t), rc, NULL);
        return result;
    }
    z_@Result_null_SqliteError@_t ok = {0};
    ok.tag = @ARM_Result_null_SqliteError.ok@;
    ok.data = NULL;
    return ok;
}

