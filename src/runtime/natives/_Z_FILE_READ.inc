/* file.read — read up to `max` bytes, appending to `buf`.
   Grows the list capacity as needed. Returns actual bytes read
   (0 indicates EOF); retries on EINTR. The loop breaks out so the body
   ends in its return -- c2mir miscompiles a struct return otherwise. */
static z_@Result_u64_IoError@_t z_@File@_read(
    z_@File@_t* f, z_@ListVal_u8@_t* buf, uint64_t max
);
static z_@Result_u64_IoError@_t z_@File@_read(
    z_@File@_t* f, z_@ListVal_u8@_t* buf, uint64_t max
) {
    if (buf->capacity < buf->length + max) {
        uint64_t newcap = buf->length + max;
        buf->data = (uint8_t*)z_xrealloc(buf->data, newcap);
        buf->capacity = newcap;
    }
    long n;
    for (;;) {
        n = read(f->fd, buf->data + buf->length, max);
        if (n >= 0) break;
        if (errno != EINTR) return z_io_u64_err(errno);
    }
    buf->length += (uint64_t)n;
    return z_io_u64_ok((uint64_t)n);
}


