static bool z_io_exists(z_@StringView@_t path) {
#if defined(_WIN32)
    wchar_t* w = z_win_path(path.data, path.size);
    if (w == NULL) return false;
    DWORD a = GetFileAttributesW(w);
    free(w);
    return a != INVALID_FILE_ATTRIBUTES;
#else
    char* path_cstr = z_sv_to_cstr(path);
    int r = access(path_cstr, F_OK);
    free(path_cstr);
    return r == 0;
#endif
}
