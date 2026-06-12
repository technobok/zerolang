/* os.set_cwd -- chdir(path). `path` is a borrowed view; caller
   retains. */
static z_Result_null_IoError_t z_os_setCwd(z_StringView_t path);
static z_Result_null_IoError_t z_os_setCwd(z_StringView_t path) {
    z_Result_null_IoError_t out = {0};
    char* path_cstr = z_sv_to_cstr(path);
    int rc = chdir(path_cstr);
    int e = errno;
    free(path_cstr);
    if (rc != 0) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    out.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    out.data = NULL;
    return out;
}

