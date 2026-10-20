/* os.cwd -- getcwd(NULL, 0) allocates a buffer sized to the
   current path. We copy into a z_@String@_t and free the libc
   buffer so ownership stays uniform. On Windows it is
   GetCurrentDirectoryW's native path, as UTF-8. */
static z_@Result_String_IoError@_t z_os_cwd(void);
static z_@Result_String_IoError@_t z_os_cwd(void) {
    z_@Result_String_IoError@_t out = {0};
#if defined(_WIN32)
    char* buf = NULL;
    DWORD need = GetCurrentDirectoryW(0, NULL);
    int e = (int)GetLastError();
    if (need > 0) {
        wchar_t* w = (wchar_t*)z_xmalloc((size_t)need * sizeof(wchar_t));
        DWORD got = GetCurrentDirectoryW(need, w);
        e = (int)GetLastError();
        if (got > 0 && got < need) buf = z_win_utf8(w, (int)got, NULL);
        free(w);
    }
#else
    char* buf = getcwd(NULL, 0);
    int e = errno;
#endif
    if (buf == NULL) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(buf);
    free(buf);
    out.tag = @ARM_Result_String_IoError.ok@;
    out.data = boxed;
    return out;
}


