/* listener.accept — block for one connection; EINTR retries. */
static z_Result_Conn_IoError_t z_Listener_accept(z_Listener_t* l) {
    z_Result_Conn_IoError_t result = {0};
    int fd;
    for (;;) {
        fd = accept(l->fd, NULL, NULL);
        if (fd >= 0) break;
        if (errno == EINTR) continue;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(errno);
        result.tag = Z_RESULT_CONN_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_Conn_t* boxed = (z_Conn_t*)z_xmalloc(sizeof(z_Conn_t));
    boxed->fd = (int32_t)fd;
    boxed->closed = false;
    result.tag = Z_RESULT_CONN_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}
