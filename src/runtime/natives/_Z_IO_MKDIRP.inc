/* mkdir -p path. Walks components, creating each missing
   directory with mode 0755. Succeeds if the final path already
   exists as a directory; fails with notdir if a parent exists as
   a non-directory, and with exists if the path itself does. Allocates a mutable copy of path
   so it can null-terminate prefixes in place. */
static z_@Result_null_IoError@_t z_io_mkdirp(z_@StringView@_t path);
static z_@Result_null_IoError@_t z_io_mkdirp(z_@StringView@_t path) {
    uint64_t n = path.size;
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
    /* the walk took "already there" as done; the path itself must be a directory */
    struct stat sb;
    int there = n > 0 && stat(buf, &sb) == 0;
    free(buf);
    if (there && !S_ISDIR(sb.st_mode)) return z_io_wrap_null_Result(-1, EEXIST);
    return z_io_wrap_null_Result(0, 0);
}


