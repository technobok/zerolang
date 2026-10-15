/* Database.lastInsertRowid */
static int64_t z_@Database@_lastInsertRowid(const z_@Database@_t* t) {
    return (int64_t)sqlite3_last_insert_rowid(z_sqlite_db(t));
}

