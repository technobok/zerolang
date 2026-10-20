/* listener.close — close once; double closes no-op. */
static z_@Result_null_IoError@_t z_@Listener@_close(z_@Listener@_t* l) {
    z_@Result_null_IoError@_t result = {0};
    if (l->@MEMBER_Listener.open@) {
        l->@MEMBER_Listener.open@ = false;
        if (z_net_close(l->@MEMBER_Listener.sock@) != 0) {
            result.tag = @ARM_Result_null_IoError.err@;
            result.data = z_net_boxed_error(z_net_error());
            return result;
        }
    }
    result.tag = @ARM_Result_null_IoError.ok@;
    result.data = NULL;
    return result;
}
