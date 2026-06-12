static z_Result_null_IoError_t z_io_rename(
    z_StringView_t from, z_StringView_t to
) {
    char* from_cstr = z_sv_to_cstr(from);
    char* to_cstr = z_sv_to_cstr(to);
    int rc = rename(from_cstr, to_cstr);
    int e = errno;
    free(from_cstr);
    free(to_cstr);
    return z_io_wrap_null_Result(rc, e);
}

