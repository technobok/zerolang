/* conn.write — write all bytes from `src`; loops on short writes,
   retries EINTR. Mirrors file.write. */
static z_@Result_u64_IoError@_t z_@Conn@_write(
    z_@Conn@_t* c, z_@ListViewVal_u8@_t* src
) {
    uint64_t total = 0;
    while (total < src->length) {
        long n = write(c->fd, src->data + total, src->length - total);
        if (n < 0) {
            if (errno == EINTR) continue;
            return z_io_u64_err(errno);
        }
        total += (uint64_t)n;
    }
    return z_io_u64_ok(total);
}
