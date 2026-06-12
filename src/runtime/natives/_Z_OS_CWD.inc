/* os.cwd -- getcwd(NULL, 0) allocates a buffer sized to the
   current path. We copy into a z_String_t and free the libc
   buffer so ownership stays uniform. */
static z_Result_String_IoError_t z_os_cwd(void);
static z_Result_String_IoError_t z_os_cwd(void) {
    z_Result_String_IoError_t out = {0};
    char* buf = getcwd(NULL, 0);
    if (buf == NULL) {
        int e = errno;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = z_String_new(buf);
    free(buf);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}

