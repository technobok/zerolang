/* listener.accept — block for one connection; an interrupted wait retries.
   The connection is never inherited by a child (z_net_accept). */
static z_@Result_Conn_IoError@_t z_@Listener@_accept(z_@Listener@_t* l) {
    z_@Result_Conn_IoError@_t result = {0};
    int64_t s;
    for (;;) {
        s = z_net_accept(l->@MEMBER_Listener.sock@);
        if (s >= 0) break;
        int e = z_net_error();
        if (z_net_interrupted(e)) continue;
        result.tag = @ARM_Result_Conn_IoError.err@;
        result.data = z_net_boxed_error(e);
        return result;
    }
    z_@Conn@_t* boxed = (z_@Conn@_t*)z_xmalloc(sizeof(z_@Conn@_t));
    boxed->@MEMBER_Conn.sock@ = s;
    boxed->@MEMBER_Conn.open@ = true;
    result.tag = @ARM_Result_Conn_IoError.ok@;
    result.data = boxed;
    return result;
}
