/* a Statement's destructor: finalize it. */
static void z_@Statement@_destroy(z_@Statement@_t* p) {
    if (!p) return;
    sqlite3_finalize(z_sqlite_stmt(p));
    p->@MEMBER_Statement.handle@ = 0;
}

