/* sqlite's Statement helpers: the statement's pointers, the deferred bind
   error a failed bind records and the next step or execute reports, and the
   rewind a bind on a stepped statement makes first. */
/* SQLITE_TRANSIENT: SQLite copies what is bound before the bind returns */
typedef void (*z_sqlite_destructor)(void*);
#define Z_SQLITE_TRANSIENT ((z_sqlite_destructor)-1)

static sqlite3_stmt* z_sqlite_stmt(const z_@Statement@_t* t) {
    return (sqlite3_stmt*)(uintptr_t)t->@MEMBER_Statement.handle@;
}

static sqlite3* z_sqlite_stmt_db(const z_@Statement@_t* t) {
    return (sqlite3*)(uintptr_t)t->@MEMBER_Statement.db@;
}

/* the first failed bind since the last step is the one reported */
static void z_sqlite_bind_failed(z_@Statement@_t* t, int rc, uint64_t parameter) {
    if (t->@MEMBER_Statement.bindRc@ != 0) return;
    t->@MEMBER_Statement.bindRc@ = (int32_t)rc;
    t->@MEMBER_Statement.bindParam@ = parameter;
}

/* the statement a bind may write to: rewound when it has been stepped, so a
   bind never meets SQLITE_MISUSE; NULL, with the failure recorded, when it
   holds no statement or the parameter cannot be an index */
static sqlite3_stmt* z_sqlite_bind_target(z_@Statement@_t* t, uint64_t parameter) {
    sqlite3_stmt* st = z_sqlite_stmt(t);
    if (!st || parameter == 0 || parameter > 0x7fffffffULL) {
        z_sqlite_bind_failed(t, SQLITE_RANGE, parameter);
        return NULL;
    }
    if (sqlite3_stmt_busy(st)) sqlite3_reset(st);
    return st;
}

/* the SqliteError a recorded bind failure answers, naming the parameter; the
   record is cleared */
static z_@SqliteError@_t* z_sqlite_bind_error(z_@Statement@_t* t) {
    char about[64];
    snprintf(about, sizeof(about), "binding parameter %llu",
        (unsigned long long)t->@MEMBER_Statement.bindParam@);
    int rc = (int)t->@MEMBER_Statement.bindRc@;
    t->@MEMBER_Statement.bindRc@ = 0;
    t->@MEMBER_Statement.bindParam@ = 0;
    return z_sqlite_error(NULL, rc, about);
}

