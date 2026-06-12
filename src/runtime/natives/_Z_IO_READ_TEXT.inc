static z_Result_String_IoError_t z_io_readText(z_StringView_t path) {
    /* path is a borrowed view; caller retains ownership */
    z_Result_String_IoError_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, O_RDONLY);
    free(path_cstr);
    if (fd < 0) {
        int e = errno;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_String_t content = z_String_create((uint64_t)4096);
    char buf[4096];
    for (;;) {
        long n = read(fd, buf, sizeof(buf));
        if (n == 0) break;
        if (n < 0) {
            if (errno == EINTR) continue;
            int e = errno;
            close(fd);
            z_String_free(&content);
            z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
            *boxed = z_io_errno_to_IoError(e);
            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
            result.data = boxed;
            return result;
        }
        z_String_append(&content, buf, (uint64_t)n);
    }
    close(fd);
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = content;
    result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

