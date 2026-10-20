/* conn.close — close once; double closes no-op. */
static z_@Result_null_IoError@_t z_@Conn@_close(z_@Conn@_t* c) {
    z_@Result_null_IoError@_t result = {0};
    if (c->@MEMBER_Conn.open@) {
        c->@MEMBER_Conn.open@ = false;
        if (z_net_close(c->@MEMBER_Conn.sock@) != 0) {
            result.tag = @ARM_Result_null_IoError.err@;
            result.data = z_net_boxed_error(z_net_error());
            return result;
        }
    }
    result.tag = @ARM_Result_null_IoError.ok@;
    result.data = NULL;
    return result;
}
