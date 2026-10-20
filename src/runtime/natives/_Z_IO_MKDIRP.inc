/* mkdir -p path. Walks components, creating each missing directory (mode 0755
   on POSIX). Succeeds if the final path already exists as a directory; fails
   with notdir if a parent exists as a non-directory, and with exists if the
   path itself does. Components split at '/', and on Windows at '\' too; a
   Windows path's root (C:\, \\srv\share\, \\?\C:\) is never created.
   Allocates a mutable copy of path so it can null-terminate prefixes in
   place. */
static z_@Result_null_IoError@_t z_io_mkdirp(z_@StringView@_t path);
static z_@Result_null_IoError@_t z_io_mkdirp(z_@StringView@_t path) {
    uint64_t n = path.size;
    char* buf = (char*)z_xmalloc(n + 1);
    if (n > 0) memcpy(buf, path.data, n);
    buf[n] = '\0';
#if defined(_WIN32)
    uint64_t first = (uint64_t)z_win_root_len(buf, (size_t)n);
    if (first == 0) first = 1;
#else
    uint64_t first = 1;
#endif
    for (uint64_t i = first; i <= n; i++) {
#if defined(_WIN32)
        int at_sep = i < n && (buf[i] == '/' || buf[i] == '\\');
#else
        int at_sep = i < n && buf[i] == '/';
#endif
        if (i == n || at_sep) {
            char saved = buf[i];
            buf[i] = '\0';
            if (buf[0] != '\0') {
#if defined(_WIN32)
                wchar_t* w = z_win_path(buf, (size_t)i);
                int made = w != NULL && CreateDirectoryW(w, NULL);
                DWORD e = made ? 0 : GetLastError();
                /* a parent that is a file is notdir, as POSIX's mkdir says;
                   Windows would answer "path not found" for the next one */
                if (e == ERROR_ALREADY_EXISTS && i < n && !(GetFileAttributesW(w) & FILE_ATTRIBUTE_DIRECTORY)) {
                    e = ERROR_DIRECTORY;
                }
                free(w);
                if (!made && e != ERROR_ALREADY_EXISTS) {
                    free(buf);
                    return z_io_wrap_null_Result(-1, (int)e);
                }
#else
                int rc = mkdir(buf, 0755);
                if (rc != 0 && errno != EEXIST) {
                    int e = errno;
                    free(buf);
                    return z_io_wrap_null_Result(-1, e);
                }
#endif
            }
            buf[i] = saved;
        }
    }
    /* the walk took "already there" as done; the path itself must be a directory */
#if defined(_WIN32)
    wchar_t* w = z_win_path(buf, (size_t)n);
    DWORD a = w != NULL ? GetFileAttributesW(w) : INVALID_FILE_ATTRIBUTES;
    free(w);
    free(buf);
    if (a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY)) return z_io_wrap_null_Result(-1, ERROR_ALREADY_EXISTS);
#else
    struct stat sb;
    int there = n > 0 && stat(buf, &sb) == 0;
    free(buf);
    if (there && !S_ISDIR(sb.st_mode)) return z_io_wrap_null_Result(-1, EEXIST);
#endif
    return z_io_wrap_null_Result(0, 0);
}
