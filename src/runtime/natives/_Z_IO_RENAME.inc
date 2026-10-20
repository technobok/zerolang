/* rename -- atomically replaces an existing `to` (MoveFileExW's
   REPLACE_EXISTING on Windows) */
static z_@Result_null_IoError@_t z_io_rename(
    z_@StringView@_t from, z_@StringView@_t to
) {
#if defined(_WIN32)
    wchar_t* wf = z_win_path(from.data, from.size);
    wchar_t* wt = wf != NULL ? z_win_path(to.data, to.size) : NULL;
    int rc = (wt != NULL && MoveFileExW(wf, wt, MOVEFILE_REPLACE_EXISTING)) ? 0 : -1;
    int e = (int)GetLastError();
    free(wf);
    free(wt);
#else
    char* from_cstr = z_sv_to_cstr(from);
    char* to_cstr = z_sv_to_cstr(to);
    int rc = rename(from_cstr, to_cstr);
    int e = errno;
    free(from_cstr);
    free(to_cstr);
#endif
    return z_io_wrap_null_Result(rc, e);
}
