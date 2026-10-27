/* net.listen — bind + listen a TCP socket on 127.0.0.1:port; backlog 16. Port
   0 takes a free port, read back with getsockname. A port another socket holds
   is refused (z_net_exclusive). */
static z_@Result_Listener_IoError@_t z_net_listen(uint16_t port) {
    z_@Result_Listener_IoError@_t result = {0};
    int64_t s = z_net_socket();
    if (s < 0) {
        result.tag = @ARM_Result_Listener_IoError.err@;
        result.data = z_net_boxed_error(z_net_error());
        return result;
    }
    z_net_exclusive(s);
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(port);
    int32_t bound = -1;
    if (bind(Z_NET_RAW(s), (struct sockaddr*)&addr, sizeof(addr)) != 0 || listen(Z_NET_RAW(s), 16) != 0
        || (bound = z_net_port(s)) < 0) {
        int e = z_net_error();
        z_net_close(s);
        result.tag = @ARM_Result_Listener_IoError.err@;
        result.data = z_net_boxed_error(e);
        return result;
    }
    z_@Listener@_t* boxed = (z_@Listener@_t*)z_xmalloc(sizeof(z_@Listener@_t));
    boxed->@MEMBER_Listener.sock@ = s;
    boxed->@MEMBER_Listener.port@ = (uint16_t)bound;
    boxed->@MEMBER_Listener.open@ = true;
    result.tag = @ARM_Result_Listener_IoError.ok@;
    result.data = boxed;
    return result;
}
