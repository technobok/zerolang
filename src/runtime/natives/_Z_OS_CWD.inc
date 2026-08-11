/* os.cwd -- getcwd(NULL, 0) allocates a buffer sized to the
   current path. We copy into a z_@String@_t and free the libc
   buffer so ownership stays uniform. */
static z_@Result_String_IoError@_t z_os_cwd(void);
static z_@Result_String_IoError@_t z_os_cwd(void) {
    z_@Result_String_IoError@_t out = {0};
    char* buf = getcwd(NULL, 0);
    if (buf == NULL) {
        int e = errno;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(buf);
    free(buf);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}

