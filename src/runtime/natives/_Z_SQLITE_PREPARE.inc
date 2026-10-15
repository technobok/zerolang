/* the Statement `sql` compiles to on `db`: exactly one statement, so SQL with
   a second one after the first is refused (a trailing comment or `;` is
   none). SQL holding no statement at all prepares to an empty Statement,
   whose step is done at once. */
static z_@Result_Statement_SqliteError@_t z_sqlite_prepare(sqlite3* db, z_@StringView@_t sql) {
    z_@Result_Statement_SqliteError@_t result = {0};
    sqlite3_stmt* st = NULL;
    const char* tail = NULL;
    const char* end = sql.data + sql.size;
    int rc = sqlite3_prepare_v3(db, sql.data, (int)sql.size, 0, &st, &tail);
    if (rc != SQLITE_OK) {
        result.tag = @ARM_Result_Statement_SqliteError.err@;
        result.data = z_sqlite_error(db, rc, NULL);
        return result;
    }
    if (st && tail && tail < end) {
        sqlite3_stmt* st2 = NULL;
        int rc2 = sqlite3_prepare_v3(db, tail, (int)(end - tail), 0, &st2, NULL);
        if (rc2 != SQLITE_OK || st2) {
            if (st2) sqlite3_finalize(st2);
            sqlite3_finalize(st);
            result.tag = @ARM_Result_Statement_SqliteError.err@;
            result.data = z_sqlite_error(NULL, SQLITE_ERROR,
                "the SQL holds more than one statement; run it with executeBatch");
            return result;
        }
    }
    z_@Statement@_t* s = (z_@Statement@_t*)z_xmalloc(sizeof(z_@Statement@_t));
    s->@MEMBER_Statement.handle@ = (uint64_t)(uintptr_t)st;
    s->@MEMBER_Statement.db@ = (uint64_t)(uintptr_t)db;
    s->@MEMBER_Statement.bindRc@ = 0;
    s->@MEMBER_Statement.bindParam@ = 0;
    result.tag = @ARM_Result_Statement_SqliteError.ok@;
    result.data = s;
    return result;
}

