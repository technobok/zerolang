/* a column-named string SQLite answers (a name, a declared type, a table), as
   a view: `range` past the last column, `null` where SQLite has none */
static z_@ResultRV_StringView_columnerror@_t z_sqlite_name_result(sqlite3_stmt* st, uint64_t column,
        const char* (*get)(sqlite3_stmt*, int)) {
    z_@ResultRV_StringView_columnerror@_t r = {0};
    if (!st || column >= (uint64_t)sqlite3_column_count(st)) {
        r.tag = @ARM_ResultRV_StringView_columnerror.err@;
        r.@MEMBER_ResultRV_StringView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.range@;
        return r;
    }
    const char* s = get(st, (int)column);
    if (!s) {
        r.tag = @ARM_ResultRV_StringView_columnerror.err@;
        r.@MEMBER_ResultRV_StringView_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.null@;
        return r;
    }
    z_@StringView@_t* v = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
    v->data = s;
    v->size = (uint64_t)strlen(s);
    r.tag = @ARM_ResultRV_StringView_columnerror.ok@;
    r.data = v;
    return r;
}

