/* a Database's destructor: the close an explicit `close` did not do. */
static void z_@Database@_destroy(z_@Database@_t* p) {
    if (!p) return;
    if (p->@MEMBER_Database.closed@) return;
    sqlite3_close_v2(z_sqlite_db(p));
    p->@MEMBER_Database.closed@ = true;
}

