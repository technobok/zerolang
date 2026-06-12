/* file.write — write all bytes from `src`. Loops on short writes;
   retries on EINTR. Returns total bytes written on success.
   `src` is a listview (layout: length, data*), matching byteview. */
static z_Result_u64_IoError_t z_File_write(
    z_File_t* f, z_ListView_u8_t* src
);
static z_Result_u64_IoError_t z_File_write(
    z_File_t* f, z_ListView_u8_t* src
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

