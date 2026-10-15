/* Row.value -- whatever the column holds, copied out */
static z_@ResultRV_Value_columnerror@_t z_@Row@_value(const z_@Row@_t* t, uint64_t column) {
    z_@ResultRV_Value_columnerror@_t r = {0};
    int ty = z_sqlite_row_type(t, column);
    if (ty == 0) {
        r.tag = @ARM_ResultRV_Value_columnerror.err@;
        r.@MEMBER_ResultRV_Value_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.range@;
        return r;
    }
    sqlite3_stmt* st = z_sqlite_row_stmt(t);
    z_@Value@_t* v = (z_@Value@_t*)z_xmalloc(sizeof(z_@Value@_t));
    v->data = NULL;
    if (ty == SQLITE_INTEGER) {
        v->tag = @ARM_Value.integer@;
        v->@MEMBER_Value.integer@ = (int64_t)sqlite3_column_int64(st, (int)column);
    } else if (ty == SQLITE_FLOAT) {
        v->tag = @ARM_Value.real@;
        v->@MEMBER_Value.real@ = sqlite3_column_double(st, (int)column);
    } else if (ty == SQLITE3_TEXT) {
        const unsigned char* p = sqlite3_column_text(st, (int)column);
        uint64_t n = (uint64_t)sqlite3_column_bytes(st, (int)column);
        if (!z_sqlite_utf8(p, n)) {
            free(v);
            r.tag = @ARM_ResultRV_Value_columnerror.err@;
            r.@MEMBER_ResultRV_Value_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.encoding@;
            return r;
        }
        z_@String@_t* s = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
        *s = z_@String@_create(n);
        z_@String@_append(s, (const char*)p, n);
        v->tag = @ARM_Value.text@;
        v->data = s;
    } else if (ty == SQLITE_BLOB) {
        const void* p = sqlite3_column_blob(st, (int)column);
        uint64_t n = (uint64_t)sqlite3_column_bytes(st, (int)column);
        z_@ListVal_u8@_t* b = (z_@ListVal_u8@_t*)z_xmalloc(sizeof(z_@ListVal_u8@_t));
        memset(b, 0, sizeof(*b));
        if (n > 0) {
            z_@ListVal_u8@_grow(b, n);
            memcpy(b->data, p, n);
            b->length = n;
        }
        v->tag = @ARM_Value.blob@;
        v->data = b;
    } else {
        v->tag = @ARM_Value.null@;
    }
    r.tag = @ARM_ResultRV_Value_columnerror.ok@;
    r.data = v;
    return r;
}

