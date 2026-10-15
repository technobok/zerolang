/* Database.executeBatch -- every statement of `sql` through sqlite3_exec, the
   first failure stopping the rest; result rows are discarded. A closed
   connection answers misuse. */
static z_@Result_null_SqliteError@_t z_@Database@_executeBatch(const z_@Database@_t* t, z_@StringView@_t sql) {
    z_@Result_null_SqliteError@_t result = {0};
    if (t->@MEMBER_Database.closed@) {
        result.tag = @ARM_Result_null_SqliteError.err@;
        result.data = z_sqlite_error(NULL, SQLITE_MISUSE, "the database is closed");
        return result;
    }
    sqlite3* db = z_sqlite_db(t);
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

