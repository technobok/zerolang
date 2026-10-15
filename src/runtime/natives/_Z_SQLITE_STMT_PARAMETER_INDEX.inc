/* Statement.parameterIndex -- the index of the parameter named `name` */
static z_@optionval_u64@_t z_@Statement@_parameterIndex(const z_@Statement@_t* t, z_@StringView@_t name) {
    z_@optionval_u64@_t out = {0};
    sqlite3_stmt* st = z_sqlite_stmt(t);
    int i = 0;
    if (st) {
        char* n = z_sqlite_cstr(name);
        i = sqlite3_bind_parameter_index(st, n);
        free(n);
    }
    if (i > 0) {
        out.tag = @ARM_optionval_u64.some@;
        out.data.@MEMBER_optionval_u64.some@ = (uint64_t)i;
    } else {
        out.tag = @ARM_optionval_u64.none@;
    }
    return out;
}

