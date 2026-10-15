/* Statement.step -- the next row, the end, or why it failed. A bind failure
   recorded since the last step is reported first. A failed step rewinds the
   statement, so it can run again. */
static z_@Step@_t z_@Statement@_step(z_@Statement@_t* t) {
    z_@Step@_t s = {0};
    if (t->@MEMBER_Statement.bindRc@ != 0) {
        s.tag = @ARM_Step.err@;
        s.data = z_sqlite_bind_error(t);
        return s;
    }
    sqlite3_stmt* st = z_sqlite_stmt(t);
    if (!st) {
        s.tag = @ARM_Step.done@;
        s.data = NULL;
        return s;
    }
    int rc = sqlite3_step(st);
    if (rc == SQLITE_ROW) {
        z_@Row@_t* r = (z_@Row@_t*)z_xmalloc(sizeof(z_@Row@_t));
        r->@MEMBER_Row.handle@ = t->@MEMBER_Statement.handle@;
        s.tag = @ARM_Step.row@;
        s.data = r;
    } else if (rc == SQLITE_DONE) {
        s.tag = @ARM_Step.done@;
        s.data = NULL;
    } else {
        s.tag = @ARM_Step.err@;
        s.data = z_sqlite_error(z_sqlite_stmt_db(t), rc, NULL);
        sqlite3_reset(st);
    }
    return s;
}

