/* Statement.execute -- run to completion, answering the rows changed; a
   statement that yields a row is refused. Rewound after, to run again. */
static z_@ResultVR_u64_SqliteError@_t z_@Statement@_execute(z_@Statement@_t* t) {
    z_@ResultVR_u64_SqliteError@_t result = {0};
    if (t->@MEMBER_Statement.bindRc@ != 0) {
        result.tag = @ARM_ResultVR_u64_SqliteError.err@;
        result.data = z_sqlite_bind_error(t);
        return result;
    }
    sqlite3_stmt* st = z_sqlite_stmt(t);
    if (!st) {
        result.tag = @ARM_ResultVR_u64_SqliteError.ok@;
        result.@MEMBER_ResultVR_u64_SqliteError.ok@ = 0;
        return result;
    }
    sqlite3* db = z_sqlite_stmt_db(t);
    int rc = sqlite3_step(st);
    if (rc == SQLITE_DONE) {
        result.tag = @ARM_ResultVR_u64_SqliteError.ok@;
        result.@MEMBER_ResultVR_u64_SqliteError.ok@ = (uint64_t)sqlite3_changes64(db);
    } else if (rc == SQLITE_ROW) {
        result.tag = @ARM_ResultVR_u64_SqliteError.err@;
        result.data = z_sqlite_error(NULL, SQLITE_MISUSE,
            "execute: the statement returns rows; step through them");
    } else {
        result.tag = @ARM_ResultVR_u64_SqliteError.err@;
        result.data = z_sqlite_error(db, rc, NULL);
    }
    sqlite3_reset(st);
    return result;
}

