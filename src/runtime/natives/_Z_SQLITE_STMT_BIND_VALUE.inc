/* Statement.bindValue -- whatever the Value holds, copied */
static void z_@Statement@_bindValue(z_@Statement@_t* t, uint64_t parameter, const z_@Value@_t* value) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    int rc;
    if (value->tag == @ARM_Value.integer@) {
        rc = sqlite3_bind_int64(st, (int)parameter, (sqlite3_int64)value->@MEMBER_Value.integer@);
    } else if (value->tag == @ARM_Value.real@) {
        rc = sqlite3_bind_double(st, (int)parameter, value->@MEMBER_Value.real@);
    } else if (value->tag == @ARM_Value.text@) {
        const z_@String@_t* s = (const z_@String@_t*)value->data;
        rc = sqlite3_bind_text64(st, (int)parameter, s->size ? (const char*)s->data : "",
            (sqlite3_uint64)s->size, Z_SQLITE_TRANSIENT, SQLITE_UTF8);
    } else if (value->tag == @ARM_Value.blob@) {
        const z_@ListVal_u8@_t* b = (const z_@ListVal_u8@_t*)value->data;
        rc = sqlite3_bind_blob64(st, (int)parameter, b->length ? (const void*)b->data : (const void*)"",
            (sqlite3_uint64)b->length, Z_SQLITE_TRANSIENT);
    } else {
        rc = sqlite3_bind_null(st, (int)parameter);
    }
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

