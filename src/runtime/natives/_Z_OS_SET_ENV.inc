/* os.set_env -- setenv(key, value, 1): always overwrites. libc
   copies key/value into its own storage. Both args are borrowed
   views; caller retains. */
static z_Result_null_IoError_t z_os_setEnv(
    z_StringView_t key, z_StringView_t value
);
static z_Result_null_IoError_t z_os_setEnv(
    z_StringView_t key, z_StringView_t value
) {
    z_Result_null_IoError_t out = {0};
    char* key_cstr = z_sv_to_cstr(key);
    char* value_cstr = z_sv_to_cstr(value);
    int rc = setenv(key_cstr, value_cstr, 1);
    int e = errno;
    free(key_cstr);
    free(value_cstr);
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

