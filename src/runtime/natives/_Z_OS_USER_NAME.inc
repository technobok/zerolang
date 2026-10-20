/* os.user_name -- resolve the effective uid via getpwuid_r.
   Starts with a 1 KiB buffer and retries once with 16 KiB if
   ERANGE; beyond that, surface as ioerror.other. */
static z_@Result_String_IoError@_t z_os_userName(void);
static z_@Result_String_IoError@_t z_os_userName(void) {
    z_@Result_String_IoError@_t out = {0};
#if defined(_WIN32)
    /* the account the process runs as (GetUserNameW) */
    wchar_t w[257];
    DWORD wn = 257;
    char* name = GetUserNameW(w, &wn) ? z_win_utf8(w, -1, NULL) : NULL;
    if (name == NULL) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error((int)GetLastError());
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
    z_@String@_t* sb = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *sb = z_@String@_new(name);
    free(name);
    out.tag = @ARM_Result_String_IoError.ok@;
    out.data = sb;
    return out;
#else
    uid_t uid = geteuid();
    size_t bufsize = 1024;
    for (int attempt = 0; attempt < 2; attempt++) {
        char* buf = (char*)z_xmalloc(bufsize);
        struct passwd pwd;
        struct passwd* result = NULL;
        int rc = getpwuid_r(uid, &pwd, buf, bufsize, &result);
        if (rc == 0 && result != NULL) {
            z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
            *boxed = z_@String@_new(pwd.pw_name);
            free(buf);
            out.tag = @ARM_Result_String_IoError.ok@;
            out.data = boxed;
            return out;
        }
        free(buf);
        if (rc == ERANGE && attempt == 0) {
            bufsize = 16 * 1024;
            continue;
        }
        int e = (rc == 0) ? ENOENT : rc;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
    /* unreachable */
    out.tag = @ARM_Result_String_IoError.err@;
    out.data = NULL;
    return out;
#endif
}


