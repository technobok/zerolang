/* Database.isAutocommit -- no transaction is open on the connection */
static bool z_@Database@_isAutocommit(const z_@Database@_t* t) {
    return sqlite3_get_autocommit(z_sqlite_db(t)) != 0;
}

