/* net.connect — open a client connection to 127.0.0.1:port. */
static z_@Result_Conn_IoError@_t z_net_connect(uint16_t port) {
    z_@Result_Conn_IoError@_t result = {0};
    int64_t s = z_net_socket();
    if (s >= 0) {
        struct sockaddr_in addr;
        memset(&addr, 0, sizeof addr);
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(port);
        int rc;
        for (;;) {
            rc = connect(Z_NET_RAW(s), (struct sockaddr*)&addr, sizeof(addr));
            if (rc == 0 || !z_net_interrupted(z_net_error())) break;
        }
        if (rc == 0) {
            z_@Conn@_t* boxed = (z_@Conn@_t*)z_xmalloc(sizeof(z_@Conn@_t));
            boxed->@MEMBER_Conn.sock@ = s;
            boxed->@MEMBER_Conn.open@ = true;
            result.tag = @ARM_Result_Conn_IoError.ok@;
            result.data = boxed;
            return result;
        }
    }
    int e = z_net_error();
    if (s >= 0) z_net_close(s);
    result.tag = @ARM_Result_Conn_IoError.err@;
    result.data = z_net_boxed_error(e);
    return result;
}
