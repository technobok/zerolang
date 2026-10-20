/* remove -- a file or an empty directory. POSIX tries unlink and falls back to
   rmdir on EISDIR; Windows asks the kind first, a directory symlink or junction
   being removed as the directory entry it is. */
static z_@Result_null_IoError@_t z_io_remove(z_@StringView@_t path) {
#if defined(_WIN32)
    wchar_t* w = z_win_path(path.data, path.size);
    int rc = -1;
    if (w != NULL) {
        DWORD a = GetFileAttributesW(w);
        if (a != INVALID_FILE_ATTRIBUTES) {
            BOOL ok = (a & FILE_ATTRIBUTE_DIRECTORY) ? RemoveDirectoryW(w) : DeleteFileW(w);
            rc = ok ? 0 : -1;
        }
    }
    int e = (int)GetLastError();
    free(w);
#else
    char* path_cstr = z_sv_to_cstr(path);
    int rc = unlink(path_cstr);
    int e = errno;
    if (rc != 0 && e == EISDIR) {
        rc = rmdir(path_cstr);
        e = errno;
    }
    free(path_cstr);
#endif
    return z_io_wrap_null_Result(rc, e);
}
