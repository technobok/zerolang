/* listener.close — close once; double closes no-op. */
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
