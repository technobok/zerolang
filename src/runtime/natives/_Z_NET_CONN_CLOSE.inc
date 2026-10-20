/* conn.close — close once; double closes no-op. */
static z_@Result_null_IoError@_t z_@Conn@_close(z_@Conn@_t* c) {
    z_@Result_null_IoError@_t result = {0};
    if (c->@MEMBER_Conn.open@) {
        c->@MEMBER_Conn.open@ = false;
        if (close(c->@MEMBER_Conn.fd@) < 0) {
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

