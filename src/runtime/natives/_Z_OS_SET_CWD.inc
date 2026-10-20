/* os.set_cwd -- chdir(path). `path` is a borrowed view; caller
   retains. */
static z_@Result_null_IoError@_t z_os_setCwd(z_@StringView@_t path);
static z_@Result_null_IoError@_t z_os_setCwd(z_@StringView@_t path) {
    z_@Result_null_IoError@_t out = {0};
#if defined(_WIN32)
    wchar_t* w = z_win_path(path.data, path.size);
    int rc = (w != NULL && SetCurrentDirectoryW(w)) ? 0 : -1;
    int e = (int)GetLastError();
    free(w);
#else
    char* path_cstr = z_sv_to_cstr(path);
    int rc = chdir(path_cstr);
    int e = errno;
    free(path_cstr);
#endif
    if (rc != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        out.tag = @ARM_Result_null_IoError.err@;
        out.data = boxed;
        return out;
    }
    out.tag = @ARM_Result_null_IoError.ok@;
    out.data = NULL;
    return out;
}


