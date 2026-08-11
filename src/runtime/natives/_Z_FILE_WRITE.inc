/* file.write — write all bytes from `src`. Loops on short writes;
   retries on EINTR. Returns total bytes written on success.
   `src` is a listview (layout: length, data*), matching byteview. */
static z_@Result_u64_IoError@_t z_@File@_write(
    z_@File@_t* f, z_@ListViewVal_u8@_t* src
);
static z_@Result_u64_IoError@_t z_@File@_write(
    z_@File@_t* f, z_@ListViewVal_u8@_t* src
) {
    uint64_t total = 0;
    while (total < src->length) {
        long n = write(f->fd, src->data + total, src->length - total);
        if (n < 0) {
            if (errno == EINTR) continue;
            return z_io_u64_err(errno);
        }
        total += (uint64_t)n;
    }
    return z_io_u64_ok(total);
}

