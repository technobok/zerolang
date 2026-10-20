/* os.unset_env -- unsetenv(key). Idempotent: removing a variable
   that was never set returns ok null. */
static z_@Result_null_IoError@_t z_os_unsetEnv(z_@StringView@_t key);
static z_@Result_null_IoError@_t z_os_unsetEnv(z_@StringView@_t key) {
    z_@Result_null_IoError@_t out = {0};
#if defined(_WIN32)
    int rc = z_win_unsetenv(key.data, key.size);
    int e = (int)GetLastError();
#else
    char* key_cstr = z_sv_to_cstr(key);
    int rc = unsetenv(key_cstr);
    int e = errno;
    free(key_cstr);
#endif
    if (rc != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        out.tag = @ARM_Result_null_IoError.err@;
        out.data = boxed;
        return out;
    }
    out.tag = @ARM_Result_null_IoError.ok@;
    out.data = NULL;
    return out;
}


