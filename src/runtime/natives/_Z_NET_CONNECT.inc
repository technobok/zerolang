/* net.connect — open a client connection to 127.0.0.1:port. */
static z_Result_Conn_IoError_t z_net_connect(uint16_t port) {
    z_Result_Conn_IoError_t result = {0};
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd >= 0) {
        struct sockaddr_in addr = {0};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(port);
        int rc;
        for (;;) {
            rc = connect(fd, (struct sockaddr*)&addr, sizeof(addr));
            if (rc >= 0 || errno != EINTR) break;
        }
        if (rc >= 0) {
            z_Conn_t* boxed = (z_Conn_t*)z_xmalloc(sizeof(z_Conn_t));
            boxed->fd = (int32_t)fd;
            boxed->closed = false;
            result.tag = Z_RESULT_CONN_IOERROR_TAG_OK;
            result.data = boxed;
            return result;
        }
    }
    int e = errno;
    if (fd >= 0) close(fd);
    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
    *boxed = z_io_errno_to_IoError(e);
    result.tag = Z_RESULT_CONN_IOERROR_TAG_ERR;
    result.data = boxed;
    return result;
}
