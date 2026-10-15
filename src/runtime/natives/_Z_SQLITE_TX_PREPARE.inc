/* Transaction.prepare -- a Statement inside the transaction, pinning it */
static z_@Result_Statement_SqliteError@_t z_@Transaction@_prepare(const z_@Transaction@_t* x, z_@StringView@_t sql) {
    if (x->@MEMBER_Transaction.finished@) {
        z_@Result_Statement_SqliteError@_t result = {0};
        result.tag = @ARM_Result_Statement_SqliteError.err@;
        result.data = z_sqlite_error(NULL, SQLITE_MISUSE, "the transaction is already finished");
        return result;
    }
    return z_sqlite_prepare(z_sqlite_tx_db(x), sql);
}

