/* Database.changes -- rows the last INSERT, UPDATE or DELETE changed */
static uint64_t z_@Database@_changes(const z_@Database@_t* t) {
    return (uint64_t)sqlite3_changes64(z_sqlite_db(t));
}

