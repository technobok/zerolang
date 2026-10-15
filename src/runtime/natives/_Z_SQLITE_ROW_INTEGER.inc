/* Row.integer -- an INTEGER, and nothing else */
static z_@resultval_i64_columnerror@_t z_@Row@_integer(const z_@Row@_t* t, uint64_t column) {
    z_@resultval_i64_columnerror@_t r = {0};
    int ty = z_sqlite_row_type(t, column);
    if (ty == SQLITE_INTEGER) {
        r.tag = @ARM_resultval_i64_columnerror.ok@;
        r.data.@MEMBER_resultval_i64_columnerror.ok@ = (int64_t)sqlite3_column_int64(z_sqlite_row_stmt(t), (int)column);
        return r;
    }
    r.tag = @ARM_resultval_i64_columnerror.err@;
    if (ty == 0) r.data.@MEMBER_resultval_i64_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.range@;
    else if (ty == SQLITE_NULL) r.data.@MEMBER_resultval_i64_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.null@;
    else r.data.@MEMBER_resultval_i64_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.mismatch@;
    return r;
}

