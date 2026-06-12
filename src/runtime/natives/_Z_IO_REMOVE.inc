static z_Result_null_IoError_t z_io_remove(z_StringView_t path) {
    char* path_cstr = z_sv_to_cstr(path);
    /* try unlink first; if EISDIR, fall back to rmdir */
    int rc = unlink(path_cstr);
    int e = errno;
    if (rc != 0 && e == EISDIR) {
        rc = rmdir(path_cstr);
        e = errno;
    }
    free(path_cstr);
    return z_io_wrap_null_Result(rc, e);
}

