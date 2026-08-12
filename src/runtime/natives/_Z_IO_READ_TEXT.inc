static z_@Result_String_IoError@_t z_io_readText(z_@StringView@_t path) {
    /* path is a borrowed view; caller retains ownership */
    z_@Result_String_IoError@_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, O_RDONLY);
    free(path_cstr);
    if (fd < 0) {
        int e = errno;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_@String@_t content = z_@String@_create((uint64_t)4096);
    char buf[4096];
    for (;;) {
        long n = read(fd, buf, sizeof(buf));
        if (n == 0) break;
        if (n < 0) {
            if (errno == EINTR) continue;
            int e = errno;
            close(fd);
            z_@String@_free(&content);
            z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
            *boxed = z_io_errno_to_IoError(e);
            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
            result.data = boxed;
            return result;
        }
        z_@String@_append(&content, buf, (uint64_t)n);
    }
    close(fd);
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = content;
    result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}


