/* Row.blob -- a BLOB, as a view into the row */
static z_@ResultRV_ByteView_columnerror@_t z_@Row@_blob(const z_@Row@_t* t, uint64_t column) {
    z_@ResultRV_ByteView_columnerror@_t r = {0};
    int ty = z_sqlite_row_type(t, column);
    if (ty == SQLITE_BLOB) {
        sqlite3_stmt* st = z_sqlite_row_stmt(t);
        const void* p = sqlite3_column_blob(st, (int)column);
        uint64_t n = (uint64_t)sqlite3_column_bytes(st, (int)column);
        z_@ListViewVal_u8@_t* v = (z_@ListViewVal_u8@_t*)z_xmalloc(sizeof(z_@ListViewVal_u8@_t));
        v->length = n;
        v->data = (uint8_t*)p;
        r.tag = @ARM_ResultRV_ByteView_columnerror.ok@;
        r.data = v;
        return r;
    }
    r.tag = @ARM_ResultRV_ByteView_columnerror.err@;
    if (ty == 0) r.@MEMBER_ResultRV_ByteView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.range@;
    else if (ty == SQLITE_NULL) r.@MEMBER_ResultRV_ByteView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.null@;
    else r.@MEMBER_ResultRV_ByteView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.mismatch@;
    return r;
}

