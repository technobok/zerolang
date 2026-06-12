static z_Result_null_IoError_t z_io_mkdir(z_StringView_t path) {
    char* path_cstr = z_sv_to_cstr(path);
    int rc = mkdir(path_cstr, 0755);
    int e = errno;
    free(path_cstr);
    return z_io_wrap_null_Result(rc, e);
}

