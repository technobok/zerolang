/* mkdir -- one directory, mode 0755 on POSIX; Windows has no mode bits, and the
   directory inherits its parent's access control. */
static z_@Result_null_IoError@_t z_io_mkdir(z_@StringView@_t path) {
#if defined(_WIN32)
    wchar_t* w = z_win_path(path.data, path.size);
    int rc = (w != NULL && CreateDirectoryW(w, NULL)) ? 0 : -1;
    int e = (int)GetLastError();
    free(w);
#else
    char* path_cstr = z_sv_to_cstr(path);
    int rc = mkdir(path_cstr, 0755);
    int e = errno;
    free(path_cstr);
#endif
    return z_io_wrap_null_Result(rc, e);
}
