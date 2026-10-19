/* os.set_env in a program that starts threads -- as _Z_OS_SET_ENV.inc, but
   refused while another thread runs: setenv may move the environment under a
   getenv on that thread (POSIX makes no promise for either). */
static z_@Result_null_IoError@_t z_os_setEnv(
    z_@StringView@_t key, z_@StringView@_t value
);
static z_@Result_null_IoError@_t z_os_setEnv(
    z_@StringView@_t key, z_@StringView@_t value
) {
    z_@Result_null_IoError@_t out = {0};
    if (z_thread_live() > 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        z_@IoError@_t v = {0};
        v.tag = @ARM_IoError.other@;
        z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
        *sp = z_@String@_new("os.setEnv: another thread is running, and it may be reading the environment");
        v.data = sp;
        *boxed = v;
        out.tag = @ARM_Result_null_IoError.err@;
        out.data = boxed;
        return out;
    }
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


