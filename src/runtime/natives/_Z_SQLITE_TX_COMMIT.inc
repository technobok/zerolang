/* Transaction.commit */
static z_@Result_null_SqliteError@_t z_@Transaction@_commit(z_@Transaction@_t* x) {
    return z_sqlite_tx_finish(x, "COMMIT");
}

