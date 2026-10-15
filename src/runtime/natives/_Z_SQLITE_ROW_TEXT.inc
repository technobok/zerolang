/* Row.text -- TEXT, checked to be UTF-8, as a view into the row */
static z_@ResultRV_StringView_columnerror@_t z_@Row@_text(const z_@Row@_t* t, uint64_t column) {
    z_@ResultRV_StringView_columnerror@_t r = {0};
    int ty = z_sqlite_row_type(t, column);
    if (ty == SQLITE3_TEXT) {
        sqlite3_stmt* st = z_sqlite_row_stmt(t);
        const unsigned char* p = sqlite3_column_text(st, (int)column);
        uint64_t n = (uint64_t)sqlite3_column_bytes(st, (int)column);
        if (!z_sqlite_utf8(p, n)) {
            r.tag = @ARM_ResultRV_StringView_columnerror.err@;
            r.@MEMBER_ResultRV_StringView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.encoding@;
            return r;
        }
        z_@StringView@_t* v = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
        v->data = (const char*)p;
        v->size = n;
        r.tag = @ARM_ResultRV_StringView_columnerror.ok@;
        r.data = v;
        return r;
    }
    r.tag = @ARM_ResultRV_StringView_columnerror.err@;
    if (ty == 0) r.@MEMBER_ResultRV_StringView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.range@;
    else if (ty == SQLITE_NULL) r.@MEMBER_ResultRV_StringView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.null@;
    else r.@MEMBER_ResultRV_StringView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.mismatch@;
    return r;
}

