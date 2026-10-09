/* listener.close — close once; double closes no-op. */
static z_@Result_null_IoError@_t z_@Listener@_close(z_@Listener@_t* l) {
    z_@Result_null_IoError@_t result = {0};
    if (!l->@MEMBER_Listener.closed@) {
        l->@MEMBER_Listener.closed@ = true;
        if (close(l->@MEMBER_Listener.fd@) < 0) {
            z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
            *boxed = z_io_errno_to_IoError(errno);
            result.tag = @ARM_Result_null_IoError.err@;
            result.data = boxed;
            return result;
        }
    }
    result.tag = @ARM_Result_null_IoError.ok@;
    result.data = NULL;
    return result;
}
