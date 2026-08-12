/* os.set_cwd -- chdir(path). `path` is a borrowed view; caller
   retains. */
static z_@Result_null_IoError@_t z_os_setCwd(z_@StringView@_t path);
static z_@Result_null_IoError@_t z_os_setCwd(z_@StringView@_t path) {
    z_@Result_null_IoError@_t out = {0};
    char* path_cstr = z_sv_to_cstr(path);
    int rc = chdir(path_cstr);
    int e = errno;
    free(path_cstr);
    if (rc != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    out.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    out.data = NULL;
    return out;
}


