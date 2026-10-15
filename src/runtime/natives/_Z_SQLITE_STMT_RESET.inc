/* Statement.reset -- rewind, keeping the bindings */
static void z_@Statement@_reset(z_@Statement@_t* t) {
    sqlite3_stmt* st = z_sqlite_stmt(t);
    if (st) sqlite3_reset(st);
}

