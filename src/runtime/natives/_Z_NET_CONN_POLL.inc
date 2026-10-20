/* conn.pollReadable — readable within timeoutMillis (z_net_poll); an
   interrupted wait reports not-readable. */
static z_@ResultVR_bool_IoError@_t z_@Conn@_pollReadable(const z_@Conn@_t* c, int32_t timeoutMillis) {
    z_@ResultVR_bool_IoError@_t result = {0};
    int r = z_net_poll(c->@MEMBER_Conn.sock@, timeoutMillis);
    if (r < 0) {
        result.tag = @ARM_ResultVR_bool_IoError.err@;
        result.data = z_net_boxed_error(z_net_error());
        return result;
    }
    result.tag = @ARM_ResultVR_bool_IoError.ok@;
    result.@MEMBER_ResultVR_bool_IoError.ok@ = r > 0;
    return result;
}
