/* Row.columnType -- the storage class the column holds */
static z_@resultval_columntype_columnerror@_t z_@Row@_columnType(const z_@Row@_t* t, uint64_t column) {
    z_@resultval_columntype_columnerror@_t r = {0};
    int ty = z_sqlite_row_type(t, column);
    if (ty == 0) {
        r.tag = @ARM_resultval_columntype_columnerror.err@;
        r.data.@MEMBER_resultval_columntype_columnerror.err@@TAG_columnerror@ = @ARM_columnerror.range@;
        return r;
    }
    r.tag = @ARM_resultval_columntype_columnerror.ok@;
    if (ty == SQLITE_INTEGER) r.data.@MEMBER_resultval_columntype_columnerror.ok@@TAG_columntype@ = @ARM_columntype.integer@;
    else if (ty == SQLITE_FLOAT) r.data.@MEMBER_resultval_columntype_columnerror.ok@@TAG_columntype@ = @ARM_columntype.real@;
    else if (ty == SQLITE3_TEXT) r.data.@MEMBER_resultval_columntype_columnerror.ok@@TAG_columntype@ = @ARM_columntype.text@;
    else if (ty == SQLITE_BLOB) r.data.@MEMBER_resultval_columntype_columnerror.ok@@TAG_columntype@ = @ARM_columntype.blob@;
    else r.data.@MEMBER_resultval_columntype_columnerror.ok@@TAG_columntype@ = @ARM_columntype.null@;
    return r;
}

