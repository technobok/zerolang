/* net.pollReadable — POLLIN|POLLHUP readiness within timeoutMillis;
   an interrupted wait reports not-readable. */
static z_Result_bool_IoError_t z_net_pollReadable(int32_t fd, int32_t timeoutMillis) {
    z_Result_bool_IoError_t result = {0};
    struct pollfd p = { .fd = fd, .events = POLLIN };
    int rc = poll(&p, 1, timeoutMillis);
    if (rc < 0 && errno != EINTR) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(errno);
        result.tag = Z_RESULT_BOOL_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    bool* boxed = (bool*)z_xmalloc(sizeof(bool));
    *boxed = (rc > 0) && (p.revents & (POLLIN | POLLHUP));
    result.tag = Z_RESULT_BOOL_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}
