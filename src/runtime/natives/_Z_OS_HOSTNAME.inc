/* os.hostname -- gethostname(3). HOST_NAME_MAX is typically 64
   on Linux; we use 256 to be defensive on unusual POSIX hosts
   and truncate on overflow. */
static z_@Result_String_IoError@_t z_os_hostname(void);
static z_@Result_String_IoError@_t z_os_hostname(void) {
    z_@Result_String_IoError@_t out = {0};
    char buf[256];
    if (gethostname(buf, sizeof(buf)) != 0) {
        int e = errno;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    buf[sizeof(buf) - 1] = '\0';
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(buf);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}


