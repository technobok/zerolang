/* Transaction.lastInsertRowid */
static int64_t z_@Transaction@_lastInsertRowid(const z_@Transaction@_t* t) {
    return (int64_t)sqlite3_last_insert_rowid(z_sqlite_tx_db(t));
}

