/* Database.prepare -- compile `sql` into a Statement on this connection
   (z_sqlite_prepare). A closed connection answers misuse. */
static z_@Result_Statement_SqliteError@_t z_@Database@_prepare(const z_@Database@_t* t, z_@StringView@_t sql) {
    if (t->@MEMBER_Database.closed@) {
        z_@Result_Statement_SqliteError@_t result = {0};
        result.tag = @ARM_Result_Statement_SqliteError.err@;
        result.data = z_sqlite_error(NULL, SQLITE_MISUSE, "the database is closed");
        return result;
    }
    return z_sqlite_prepare(z_sqlite_db(t), sql);
}

