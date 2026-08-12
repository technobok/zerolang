/* listener.accept — block for one connection; EINTR retries. */
static z_@Result_Conn_IoError@_t z_@Listener@_accept(z_@Listener@_t* l) {
    z_@Result_Conn_IoError@_t result = {0};
    int fd;
    for (;;) {
        fd = accept(l->fd, NULL, NULL);
        if (fd >= 0) break;
        if (errno == EINTR) continue;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(errno);
        result.tag = Z_RESULT_CONN_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_@Conn@_t* boxed = (z_@Conn@_t*)z_xmalloc(sizeof(z_@Conn@_t));
    boxed->fd = (int32_t)fd;
    boxed->closed = false;
    result.tag = Z_RESULT_CONN_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

