/* os.user_name -- resolve the effective uid via getpwuid_r.
   Starts with a 1 KiB buffer and retries once with 16 KiB if
   ERANGE; beyond that, surface as ioerror.other. */
static z_Result_String_IoError_t z_os_userName(void);
static z_Result_String_IoError_t z_os_userName(void) {
    z_Result_String_IoError_t out = {0};
    uid_t uid = geteuid();
    size_t bufsize = 1024;
    for (int attempt = 0; attempt < 2; attempt++) {
        char* buf = (char*)z_xmalloc(bufsize);
        struct passwd pwd;
        struct passwd* result = NULL;
        int rc = getpwuid_r(uid, &pwd, buf, bufsize, &result);
        if (rc == 0 && result != NULL) {
            z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
            *boxed = z_String_new(pwd.pw_name);
            free(buf);
            out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
            out.data = boxed;
            return out;
        }
        free(buf);
        if (rc == ERANGE && attempt == 0) {
            bufsize = 16 * 1024;
            continue;
        }
        int e = (rc == 0) ? ENOENT : rc;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    /* unreachable */
    out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
    out.data = NULL;
    return out;
}

