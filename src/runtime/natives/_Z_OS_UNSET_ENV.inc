/* os.unset_env -- unsetenv(key). Idempotent: removing a variable
   that was never set returns ok null. */
static z_Result_null_IoError_t z_os_unsetEnv(z_StringView_t key);
static z_Result_null_IoError_t z_os_unsetEnv(z_StringView_t key) {
    z_Result_null_IoError_t out = {0};
    char* key_cstr = z_sv_to_cstr(key);
    int rc = unsetenv(key_cstr);
    int e = errno;
    free(key_cstr);
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

