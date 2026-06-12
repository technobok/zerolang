/* file.read — read up to `max` bytes, appending to `buf`.
   Grows the list capacity as needed. Returns actual bytes read
   (0 indicates EOF); retries on EINTR. */
static z_Result_u64_IoError_t z_File_read(
    z_File_t* f, z_List_u8_t* buf, uint64_t max
);
static z_Result_u64_IoError_t z_File_read(
    z_File_t* f, z_List_u8_t* buf, uint64_t max
) {
    if (buf->capacity < buf->length + max) {
        uint64_t newcap = buf->length + max;
        buf->data = (uint8_t*)z_xrealloc(buf->data, newcap);
        buf->capacity = newcap;
    }
    for (;;) {
        long n = read(f->fd, buf->data + buf->length, max);
        if (n >= 0) {
            buf->length += (uint64_t)n;
            return z_io_u64_ok((uint64_t)n);
        }
        if (errno == EINTR) continue;
        return z_io_u64_err(errno);
    }
}

