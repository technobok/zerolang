/* conn.read — read up to `max` bytes, appending to `buf`; 0 = peer
   closed. An interrupted read retries. Mirrors file.read. */
static z_@ResultVR_u64_IoError@_t z_@Conn@_read(
    z_@Conn@_t* c, z_@ListVal_u8@_t* buf, uint64_t max
) {
    if (buf->capacity < buf->length + max) {
        uint64_t newcap = buf->length + max;
        buf->data = (uint8_t*)z_xrealloc(buf->data, newcap);
        buf->capacity = newcap;
    }
    int64_t n;
    for (;;) {
        n = z_net_recv(c->@MEMBER_Conn.sock@, buf->data + buf->length, max);
        if (n >= 0) break;
        int e = z_net_error();
        if (!z_net_interrupted(e)) return z_io_u64_err(e);
    }
    buf->length += (uint64_t)n;
    return z_io_u64_ok((uint64_t)n);
}
