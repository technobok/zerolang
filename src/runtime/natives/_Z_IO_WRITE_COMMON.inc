/* shared helper: write all bytes; returns 0 ok, errno on failure. */
static int z_io_write_all(int fd, const char* data, uint64_t size) {
    uint64_t off = 0;
    while (off < size) {
        long n = write(fd, data + off, size - off);
        if (n < 0) {
            if (errno == EINTR) continue;
            return errno;
        }
        off += (uint64_t)n;
    }
    return 0;
}

/* shared helper: path + open flags -> write content -> close.
   path and content are borrowed views; caller retains. */
static z_Result_null_IoError_t z_io_write_common(
    z_StringView_t path, z_StringView_t content, int flags
) {
    z_Result_null_IoError_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, flags, 0644);
    free(path_cstr);
    if (fd < 0) {
        int e = errno;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    int werr = z_io_write_all(fd, content.data, content.length);
    close(fd);
    if (werr != 0) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(werr);
        result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}

