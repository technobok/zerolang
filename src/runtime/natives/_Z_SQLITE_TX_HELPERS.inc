/* sqlite's Transaction helpers: its connection, and the COMMIT or ROLLBACK
   that finishes it -- once; after that every call answers misuse */
static sqlite3* z_sqlite_tx_db(const z_@Transaction@_t* x) {
    return (sqlite3*)(uintptr_t)x->@MEMBER_Transaction.db@;
}

static z_@Result_null_SqliteError@_t z_sqlite_tx_finish(z_@Transaction@_t* x, const char* sql) {
    if (x->@MEMBER_Transaction.finished@) return z_sqlite_null_misuse("the transaction is already finished");
    sqlite3* db = z_sqlite_tx_db(x);
    int rc = sqlite3_exec(db, sql, NULL, NULL, NULL);
    z_@Result_null_SqliteError@_t result = {0};
    if (rc != SQLITE_OK) {
        result.tag = @ARM_Result_null_SqliteError.err@;
        result.data = z_sqlite_error(db, rc, NULL);
        /* a failed COMMIT leaves the transaction open: the destructor rolls it
           back, unless SQLite already ended it */
        if (sqlite3_get_autocommit(db)) x->@MEMBER_Transaction.finished@ = true;
        return result;
    }
    x->@MEMBER_Transaction.finished@ = true;
    result.tag = @ARM_Result_null_SqliteError.ok@;
    result.data = NULL;
    return result;
}

