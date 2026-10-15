/* sqlite's helpers: a view as a C string, a connection's pointer, and the
   SqliteError a failed call answers. */
static char* z_sqlite_cstr(z_@StringView@_t v) {
    char* buf = (char*)z_xmalloc(v.size + 1);
    if (v.size > 0) memcpy(buf, v.data, v.size);
    buf[v.size] = '\0';
    return buf;
}

static sqlite3* z_sqlite_db(const z_@Database@_t* d) {
    return (sqlite3*)(uintptr_t)d->@MEMBER_Database.handle@;
}

/* the SqliteError for the failure `rc`: on connection `db` its message and
   offset are the connection's, with no connection (NULL) the code's own text.
   `about` (a path, or NULL) is appended to the message. */
static z_@SqliteError@_t* z_sqlite_error(sqlite3* db, int rc, const char* about) {
    z_@SqliteError@_t* e = (z_@SqliteError@_t*)z_xmalloc(sizeof(z_@SqliteError@_t));
    int ext = rc;
    if (db) {
        int x = sqlite3_extended_errcode(db);
        if ((x & 0xff) == (rc & 0xff)) ext = x;
    }
    switch (rc & 0xff) {
        case 2:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.internal@; break;
        case 3:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.permission@; break;
        case 4:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.abort@; break;
        case 5:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.busy@; break;
        case 6:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.locked@; break;
        case 7:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.noMemory@; break;
        case 8:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.readOnly@; break;
        case 9:  e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.interrupt@; break;
        case 10: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.ioError@; break;
        case 11: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.corrupt@; break;
        case 12: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.notFound@; break;
        case 13: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.full@; break;
        case 14: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.cannotOpen@; break;
        case 15: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.lockProtocol@; break;
        case 17: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.schema@; break;
        case 18: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.tooBig@; break;
        case 19: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.constraint@; break;
        case 20: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.mismatch@; break;
        case 21: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.misuse@; break;
        case 22: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.noLargeFileSupport@; break;
        case 23: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.authorization@; break;
        case 24: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.format@; break;
        case 25: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.range@; break;
        case 26: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.notADatabase@; break;
        case 27: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.notice@; break;
        case 28: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.warning@; break;
        default: e->@MEMBER_SqliteError.code@@TAG_errorcode@ = @ARM_errorcode.error@; break;
    }
    e->@MEMBER_SqliteError.extendedCode@ = (int32_t)ext;
    const char* msg = db ? sqlite3_errmsg(db) : sqlite3_errstr(rc);
    uint64_t n = (uint64_t)strlen(msg);
    e->@MEMBER_SqliteError.message@ = z_@String@_create(n + 64);
    z_@String@_append(&e->@MEMBER_SqliteError.message@, msg, n);
    if (about) {
        z_@String@_append(&e->@MEMBER_SqliteError.message@, ": ", 2);
        z_@String@_append(&e->@MEMBER_SqliteError.message@, about, (uint64_t)strlen(about));
    }
    int off = db ? sqlite3_error_offset(db) : -1;
    if (off >= 0) {
        e->@MEMBER_SqliteError.offset@.tag = @ARM_optionval_u64.some@;
        e->@MEMBER_SqliteError.offset@.data.@MEMBER_optionval_u64.some@ = (uint64_t)off;
    } else {
        e->@MEMBER_SqliteError.offset@.tag = @ARM_optionval_u64.none@;
    }
    return e;
}

