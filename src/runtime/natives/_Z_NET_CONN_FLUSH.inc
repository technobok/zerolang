/* conn.flush — sockets write through; nothing buffered here. */
static z_@Result_null_IoError@_t z_@Conn@_flush(z_@Conn@_t* c) {
    (void)c;
    z_@Result_null_IoError@_t result = {0};
    result.tag = @ARM_Result_null_IoError.ok@;
    result.data = NULL;
    return result;
}

