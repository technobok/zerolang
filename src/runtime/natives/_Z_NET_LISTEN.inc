/* net.listen — bind + listen a TCP socket on 127.0.0.1:port with
   SO_REUSEADDR; backlog 16. */
static z_@Result_Listener_IoError@_t z_net_listen(uint16_t port) {
    z_@Result_Listener_IoError@_t result = {0};
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
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
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_LISTENER_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_@Listener@_t* boxed = (z_@Listener@_t*)z_xmalloc(sizeof(z_@Listener@_t));
    boxed->fd = (int32_t)fd;
    boxed->closed = false;
    result.tag = Z_RESULT_LISTENER_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

