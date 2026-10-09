/* net.pollReadable — POLLIN|POLLHUP readiness within timeoutMillis;
   an interrupted wait reports not-readable. */
static z_@ResultVR_bool_IoError@_t z_net_pollReadable(int32_t fd, int32_t timeoutMillis) {
    z_@ResultVR_bool_IoError@_t result = {0};
    struct pollfd p = { .fd = fd, .events = POLLIN };
    int rc = poll(&p, 1, timeoutMillis);
    if (rc < 0 && errno != EINTR) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(errno);
        result.tag = @ARM_ResultVR_bool_IoError.err@;
        result.data = boxed;
        return result;
    }
    result.tag = @ARM_ResultVR_bool_IoError.ok@;
    result.@MEMBER_ResultVR_bool_IoError.ok@ = (rc > 0) && (p.revents & (POLLIN | POLLHUP));
    return result;
}

