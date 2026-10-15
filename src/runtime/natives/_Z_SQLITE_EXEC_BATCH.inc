/* every statement of `sql` on `db` through sqlite3_exec, the first failure
   stopping the rest; result rows are discarded */
static z_@Result_null_SqliteError@_t z_sqlite_exec_batch(sqlite3* db, z_@StringView@_t sql) {
    z_@Result_null_SqliteError@_t result = {0};
    char* s = z_sqlite_cstr(sql);
    int rc = sqlite3_exec(db, s, NULL, NULL, NULL);
    free(s);
    if (rc != SQLITE_OK) {
        result.tag = @ARM_Result_null_SqliteError.err@;
        result.data = z_sqlite_error(db, rc, NULL);
        return result;
    }
    result.tag = @ARM_Result_null_SqliteError.ok@;
    result.data = NULL;
    return result;
}

/* the misuse a closed connection or a finished transaction answers */
static z_@Result_null_SqliteError@_t z_sqlite_null_misuse(const char* why) {
    z_@Result_null_SqliteError@_t result = {0};
    result.tag = @ARM_Result_null_SqliteError.err@;
    result.data = z_sqlite_error(NULL, SQLITE_MISUSE, why);
    return result;
}

