/* Statement.clearBindings -- every parameter back to NULL */
static void z_@Statement@_clearBindings(z_@Statement@_t* t) {
    sqlite3_stmt* st = z_sqlite_stmt(t);
    if (st) sqlite3_clear_bindings(st);
}

