/* Database.close -- close the connection now, answering what closing reported;
   a closed Database answers ok. sqlite3_close_v2 defers the close while a
   statement it prepared is alive, which the ownership rules already forbid. */
static z_@Result_null_SqliteError@_t z_@Database@_close(z_@Database@_t* t) {
    z_@Result_null_SqliteError@_t result = {0};
    if (!t->@MEMBER_Database.closed@) {
        int rc = sqlite3_close_v2(z_sqlite_db(t));
        t->@MEMBER_Database.closed@ = true;
        if (rc != SQLITE_OK) {
            result.tag = @ARM_Result_null_SqliteError.err@;
            result.data = z_sqlite_error(NULL, rc, NULL);
            return result;
        }
    }
    result.tag = @ARM_Result_null_SqliteError.ok@;
    result.data = NULL;
    return result;
}

