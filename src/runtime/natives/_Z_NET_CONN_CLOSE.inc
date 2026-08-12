/* conn.close / listener.close — close once; double closes no-op. */
static z_@Result_null_IoError@_t z_@Conn@_close(z_@Conn@_t* c) {
    z_@Result_null_IoError@_t result = {0};
    if (!c->closed) {
        c->closed = true;
        if (close(c->fd) < 0) {
            z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
            *boxed = z_io_errno_to_IoError(errno);
            result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
            result.data = boxed;
            return result;
        }
    }
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}

static z_@Result_null_IoError@_t z_@Listener@_close(z_@Listener@_t* l) {
    z_@Result_null_IoError@_t result = {0};
    if (!l->closed) {
        l->closed = true;
        if (close(l->fd) < 0) {
            z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
            *boxed = z_io_errno_to_IoError(errno);
            result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
            result.data = boxed;
            return result;
        }
    }
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}

/* conn.flush — sockets write through; nothing buffered here. */
static z_@Result_null_IoError@_t z_@Conn@_flush(z_@Conn@_t* c) {
    (void)c;
    z_@Result_null_IoError@_t result = {0};
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}

