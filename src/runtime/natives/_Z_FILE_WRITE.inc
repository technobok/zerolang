/* file.write — write all bytes from `src`. Loops on short writes;
   retries on EINTR. Returns total bytes written on success.
   `src` is a listview (layout: length, data*), matching byteview. */
static z_@ResultVR_u64_IoError@_t z_@File@_write(
    z_@File@_t* f, z_@ListViewVal_u8@_t src
);
static z_@ResultVR_u64_IoError@_t z_@File@_write(
    z_@File@_t* f, z_@ListViewVal_u8@_t src
) {
    uint64_t total = 0;
    while (total < src.length) {
#if defined(_WIN32)
        if (z_win_console_write(f->@MEMBER_File.fd@, (const char*)src.data + total, src.length - total, 0)) {
            total = src.length;
            break;
        }
        int64_t n = z_win_write(f->@MEMBER_File.fd@, src.data + total, src.length - total);
        if (n < 0) return z_io_u64_err((int)GetLastError());
#else
        int64_t n = write(f->@MEMBER_File.fd@, src.data + total, src.length - total);
        if (n < 0) {
            if (errno == EINTR) continue;
            return z_io_u64_err(errno);
        }
#endif
        total += (uint64_t)n;
    }
    return z_io_u64_ok(total);
}


