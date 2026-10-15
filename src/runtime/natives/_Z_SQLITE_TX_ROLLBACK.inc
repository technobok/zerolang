/* Transaction.rollback */
static z_@Result_null_SqliteError@_t z_@Transaction@_rollback(z_@Transaction@_t* x) {
    return z_sqlite_tx_finish(x, "ROLLBACK");
}

