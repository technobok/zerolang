/* conn.close / listener.close — close once; double closes no-op. */
static z_Result_null_IoError_t z_Conn_close(z_Conn_t* c) {
    z_Result_null_IoError_t result = {0};
    if (!c->closed) {
        c->closed = true;
        if (close(c->fd) < 0) {
            z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
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

static z_Result_null_IoError_t z_Listener_close(z_Listener_t* l) {
    z_Result_null_IoError_t result = {0};
    if (!l->closed) {
        l->closed = true;
        if (close(l->fd) < 0) {
            z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
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
static z_Result_null_IoError_t z_Conn_flush(z_Conn_t* c) {
    (void)c;
    z_Result_null_IoError_t result = {0};
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}
