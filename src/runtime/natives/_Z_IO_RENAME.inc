static z_@Result_null_IoError@_t z_io_rename(
    z_@StringView@_t from, z_@StringView@_t to
) {
    char* from_cstr = z_sv_to_cstr(from);
    char* to_cstr = z_sv_to_cstr(to);
    int rc = rename(from_cstr, to_cstr);
    int e = errno;
    free(from_cstr);
    free(to_cstr);
    return z_io_wrap_null_Result(rc, e);
}


