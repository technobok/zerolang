/* mkdir -p path. Walks components, creating each missing
   directory with mode 0755. Succeeds if the final path already
   exists as a directory; fails with ioerror if any component
   exists as a non-directory. Allocates a mutable copy of path
   so it can null-terminate prefixes in place. */
static z_Result_null_IoError_t z_io_mkdirp(z_StringView_t path);
static z_Result_null_IoError_t z_io_mkdirp(z_StringView_t path) {
    uint64_t n = path.length;
    char* buf = (char*)z_xmalloc(n + 1);
    if (n > 0) memcpy(buf, path.data, n);
    buf[n] = '\0';
    /* walk, splitting at '/' boundaries */
    for (uint64_t i = 1; i <= n; i++) {
        if (i == n || buf[i] == '/') {
            char saved = buf[i];
            buf[i] = '\0';
            if (buf[0] != '\0') {
                int rc = mkdir(buf, 0755);
                if (rc != 0 && errno != EEXIST) {
                    int e = errno;
                    free(buf);
                    return z_io_wrap_null_Result(-1, e);
                }
            }
            buf[i] = saved;
        }
    }
    free(buf);
    return z_io_wrap_null_Result(0, 0);
}

