/* Database.totalChanges -- rows changed since the connection opened, triggers'
   included */
static uint64_t z_@Database@_totalChanges(const z_@Database@_t* t) {
    return (uint64_t)sqlite3_total_changes64(z_sqlite_db(t));
}

