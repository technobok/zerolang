/* Database.transaction -- BEGIN in `mode`, answering the Transaction that
   pins the Database until it ends; a transaction already open on the
   connection answers SQLite's error */
static z_@Result_Transaction_SqliteError@_t z_@Database@_transaction(z_@Database@_t* t, z_@transactionmode@_t mode) {
    z_@Result_Transaction_SqliteError@_t result = {0};
    if (t->@MEMBER_Database.closed@) {
        result.tag = @ARM_Result_Transaction_SqliteError.err@;
        result.data = z_sqlite_error(NULL, SQLITE_MISUSE, "the database is closed");
        return result;
    }
    const char* begin = "BEGIN DEFERRED";
    switch (mode@TAG_transactionmode@) {
        case @ARM_transactionmode.immediate@: begin = "BEGIN IMMEDIATE"; break;
        case @ARM_transactionmode.exclusive@: begin = "BEGIN EXCLUSIVE"; break;
        default: break;
    }
    sqlite3* db = z_sqlite_db(t);
    int rc = sqlite3_exec(db, begin, NULL, NULL, NULL);
    if (rc != SQLITE_OK) {
        result.tag = @ARM_Result_Transaction_SqliteError.err@;
        result.data = z_sqlite_error(db, rc, NULL);
        return result;
    }
    z_@Transaction@_t* x = (z_@Transaction@_t*)z_xmalloc(sizeof(z_@Transaction@_t));
    x->@MEMBER_Transaction.db@ = t->@MEMBER_Database.handle@;
    x->@MEMBER_Transaction.finished@ = false;
    result.tag = @ARM_Result_Transaction_SqliteError.ok@;
    result.data = x;
    return result;
}

