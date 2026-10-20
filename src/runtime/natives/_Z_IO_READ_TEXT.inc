static z_@Result_String_IoError@_t z_io_readText(z_@StringView@_t path) {
    /* path is a borrowed view; caller retains ownership */
    z_@Result_String_IoError@_t result = {0};
#if defined(_WIN32)
    int fd = z_win_open_fd(path.data, path.size, 0);
    int e = (int)GetLastError();
#else
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, O_RDONLY | O_CLOEXEC);
    int e = errno;
    free(path_cstr);
#endif
    if (fd < 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_Result_String_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@String@_t content = z_@String@_create((uint64_t)4096);
    char buf[4096];
    for (;;) {
#if defined(_WIN32)
        int64_t n = z_win_read(fd, buf, sizeof(buf));
        if (n < 0) e = (int)GetLastError();
#else
        int64_t n = read(fd, buf, sizeof(buf));
        if (n < 0) {
            if (errno == EINTR) continue;
            e = errno;
        }
#endif
        if (n == 0) break;
        if (n < 0) {
#if defined(_WIN32)
            _close(fd);
#else
            close(fd);
#endif
            z_@String@_free(&content);
            z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
            *boxed = z_io_os_error(e);
            result.tag = @ARM_Result_String_IoError.err@;
            result.data = boxed;
            return result;
        }
        z_@String@_append(&content, buf, (uint64_t)n);
    }
#if defined(_WIN32)
    _close(fd);
#else
    close(fd);
#endif
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = content;
    result.tag = @ARM_Result_String_IoError.ok@;
    result.data = boxed;
    return result;
}


