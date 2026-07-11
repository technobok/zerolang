/* net.listen — bind + listen a TCP socket on 127.0.0.1:port with
   SO_REUSEADDR; backlog 16. */
static z_Result_Listener_IoError_t z_net_listen(uint16_t port) {
    z_Result_Listener_IoError_t result = {0};
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(errno);
        result.tag = Z_RESULT_LISTENER_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(port);
    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0 || listen(fd, 16) < 0) {
        int e = errno;
        close(fd);
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_LISTENER_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_Listener_t* boxed = (z_Listener_t*)z_xmalloc(sizeof(z_Listener_t));
    boxed->fd = (int32_t)fd;
    boxed->closed = false;
    result.tag = Z_RESULT_LISTENER_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}
