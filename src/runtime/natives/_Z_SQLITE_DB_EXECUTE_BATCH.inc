/* Database.executeBatch -- every statement of `sql` (z_sqlite_exec_batch); a
   closed connection answers misuse */
static z_@Result_null_SqliteError@_t z_@Database@_executeBatch(const z_@Database@_t* t, z_@StringView@_t sql) {
    if (t->@MEMBER_Database.closed@) return z_sqlite_null_misuse("the database is closed");
    return z_sqlite_exec_batch(z_sqlite_db(t), sql);
}

