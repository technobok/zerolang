/* os.hostname -- gethostname(3). HOST_NAME_MAX is typically 64
   on Linux; we use 256 to be defensive on unusual POSIX hosts
   and truncate on overflow. */
static z_Result_String_IoError_t z_os_hostname(void);
static z_Result_String_IoError_t z_os_hostname(void) {
    z_Result_String_IoError_t out = {0};
    char buf[256];
    if (gethostname(buf, sizeof(buf)) != 0) {
        int e = errno;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    buf[sizeof(buf) - 1] = '\0';
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = z_String_new(buf);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}

