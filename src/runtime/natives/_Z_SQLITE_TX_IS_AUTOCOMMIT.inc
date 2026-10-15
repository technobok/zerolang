/* Transaction.isAutocommit -- no transaction is open on the connection */
static bool z_@Transaction@_isAutocommit(const z_@Transaction@_t* t) {
    return sqlite3_get_autocommit(z_sqlite_tx_db(t)) != 0;
}

