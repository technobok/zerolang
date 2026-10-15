/* Transaction.changes -- rows the last INSERT, UPDATE or DELETE changed */
static uint64_t z_@Transaction@_changes(const z_@Transaction@_t* t) {
    return (uint64_t)sqlite3_changes64(z_sqlite_tx_db(t));
}

