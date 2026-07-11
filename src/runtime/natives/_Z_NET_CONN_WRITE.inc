/* conn.write — write all bytes from `src`; loops on short writes,
   retries EINTR. Mirrors file.write. */
static z_Result_u64_IoError_t z_Conn_write(
    z_Conn_t* c, z_ListView_u8_t* src
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
