/* os.unset_env in a program that starts threads -- as
   _Z_OS_UNSET_ENV.inc, but refused while another thread runs, as
   os.setEnv is. */
static z_@Result_null_IoError@_t z_os_unsetEnv(z_@StringView@_t key);
static z_@Result_null_IoError@_t z_os_unsetEnv(z_@StringView@_t key) {
    z_@Result_null_IoError@_t out = {0};
    if (z_thread_live() > 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        z_@IoError@_t v = {0};
        v.tag = @ARM_IoError.other@;
        z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
        *sp = z_@String@_new("os.unsetEnv: another thread is running, and it may be reading the environment");
        v.data = sp;
        *boxed = v;
        out.tag = @ARM_Result_null_IoError.err@;
        out.data = boxed;
        return out;
    }
    char* key_cstr = z_sv_to_cstr(key);
    int rc = unsetenv(key_cstr);
    int e = errno;
    free(key_cstr);
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


