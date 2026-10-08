/* os.set_env -- setenv(key, value, 1): always overwrites. libc
   copies key/value into its own storage. Both args are borrowed
   views; caller retains. */
static z_@Result_null_IoError@_t z_os_setEnv(
    z_@StringView@_t key, z_@StringView@_t value
);
static z_@Result_null_IoError@_t z_os_setEnv(
    z_@StringView@_t key, z_@StringView@_t value
) {
    z_@Result_null_IoError@_t out = {0};
    char* key_cstr = z_sv_to_cstr(key);
    char* value_cstr = z_sv_to_cstr(value);
    int rc = setenv(key_cstr, value_cstr, 1);
    int e = errno;
    free(key_cstr);
    free(value_cstr);
    if (rc != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = @ARM_Result_null_IoError.err@;
        out.data = boxed;
        return out;
    }
    out.tag = @ARM_Result_null_IoError.ok@;
    out.data = NULL;
    return out;
}


