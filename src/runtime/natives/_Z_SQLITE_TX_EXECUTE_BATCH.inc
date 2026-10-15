/* Transaction.executeBatch -- every statement of `sql` inside the transaction */
static z_@Result_null_SqliteError@_t z_@Transaction@_executeBatch(const z_@Transaction@_t* x, z_@StringView@_t sql) {
    if (x->@MEMBER_Transaction.finished@) return z_sqlite_null_misuse("the transaction is already finished");
    return z_sqlite_exec_batch(z_sqlite_tx_db(x), sql);
}

