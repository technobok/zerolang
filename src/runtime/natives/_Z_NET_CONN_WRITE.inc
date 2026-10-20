/* conn.write — write all bytes from `src`; loops on short writes,
   retries an interrupted one. Mirrors file.write. */
static z_@ResultVR_u64_IoError@_t z_@Conn@_write(
    z_@Conn@_t* c, z_@ListViewVal_u8@_t src
) {
    uint64_t total = 0;
    while (total < src.length) {
        int64_t n = z_net_send(c->@MEMBER_Conn.sock@, src.data + total, src.length - total);
        if (n < 0) {
            int e = z_net_error();
            if (z_net_interrupted(e)) continue;
            return z_io_u64_err(e);
        }
        total += (uint64_t)n;
    }
    return z_io_u64_ok(total);
}
