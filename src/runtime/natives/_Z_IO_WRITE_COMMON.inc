/* shared helper: write all bytes; returns 0 ok, the OS error code on
   failure. */
static int z_io_write_all(int fd, const char* data, uint64_t size) {
    uint64_t off = 0;
    while (off < size) {
#if defined(_WIN32)
        int64_t n = z_win_write(fd, data + off, size - off);
        if (n < 0) return (int)GetLastError();
#else
        int64_t n = write(fd, data + off, size - off);
        if (n < 0) {
            if (errno == EINTR) continue;
            return errno;
        }
#endif
        off += (uint64_t)n;
    }
    return 0;
}

/* shared helper: path + how it opens (1 truncates, 2 appends; the file is
   created either way) -> write content -> close. path and content are
   borrowed views; caller retains. */
static z_@Result_null_IoError@_t z_io_write_common(
    z_@StringView@_t path, z_@StringView@_t content, int how
) {
    z_@Result_null_IoError@_t result = {0};
#if defined(_WIN32)
    int fd = z_win_open_fd(path.data, path.size, how);
    int e = (int)GetLastError();
#else
    int flags = O_WRONLY | O_CREAT | (how == 2 ? O_APPEND : O_TRUNC);
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, flags | O_CLOEXEC, 0644);
    int e = errno;
    free(path_cstr);
#endif
    if (fd < 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_Result_null_IoError.err@;
        result.data = boxed;
        return result;
    }
    int werr = z_io_write_all(fd, content.data, content.size);
#if defined(_WIN32)
    _close(fd);
#else
    close(fd);
#endif
    if (werr != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(werr);
        result.tag = @ARM_Result_null_IoError.err@;
        result.data = boxed;
        return result;
    }
    result.tag = @ARM_Result_null_IoError.ok@;
    result.data = NULL;
    return result;
}
