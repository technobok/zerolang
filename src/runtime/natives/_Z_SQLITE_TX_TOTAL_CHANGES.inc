/* Transaction.totalChanges -- rows changed since the connection opened, triggers'
   included */
static uint64_t z_@Transaction@_totalChanges(const z_@Transaction@_t* t) {
    return (uint64_t)sqlite3_total_changes64(z_sqlite_tx_db(t));
}

