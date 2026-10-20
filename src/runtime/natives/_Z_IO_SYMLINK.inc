/* symlink — create a symbolic link at `link` with `target` as the
   link content. Both args are borrowed views; caller retains.
   EEXIST maps to ioerror.exists so callers can distinguish
   'already there' from other failures.

   On Windows a link is a file link or a directory link, so it is a
   directory link when the target -- relative to the link's own folder
   when relative -- is a directory now. The target is stored with
   native separators. Creating one needs Developer Mode or the
   privilege to (permissiondenied without): it is asked for
   unprivileged first, and plainly on a Windows too old to know the
   flag. */
#if defined(_WIN32)
static int z_io_win_symlink(z_@StringView@_t target, z_@StringView@_t link) {
    char* t = (char*)z_xmalloc(target.size + 1);
    for (uint64_t i = 0; i < target.size; i++) t[i] = target.data[i] == '/' ? '\\' : target.data[i];
    t[target.size] = '\0';
    /* where the target resolves from: itself when absolute, else the link's folder */
    char* at = t;
    if (z_win_root_len(t, target.size) == 0) {
        size_t d = link.size;
        while (d > 0 && link.data[d - 1] != '/' && link.data[d - 1] != '\\') d--;
        at = (char*)z_xmalloc(d + target.size + 1);
        if (d > 0) memcpy(at, link.data, d);
        memcpy(at + d, t, target.size + 1);
    }
    wchar_t* wa = z_win_path(at, strlen(at));
    DWORD a = wa != NULL ? GetFileAttributesW(wa) : INVALID_FILE_ATTRIBUTES;
    free(wa);
    if (at != t) free(at);
    DWORD flags = (a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY)) ? SYMBOLIC_LINK_FLAG_DIRECTORY : 0;
    wchar_t* wt = z_win_wide(t, target.size);
    free(t);
    wchar_t* wl = wt != NULL ? z_win_path(link.data, link.size) : NULL;
    int rc = -1;
    if (wl != NULL) {
        BOOLEAN ok = CreateSymbolicLinkW(wl, wt, flags | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE);
        if (!ok && GetLastError() == ERROR_INVALID_PARAMETER) ok = CreateSymbolicLinkW(wl, wt, flags);
        rc = ok ? 0 : -1;
    }
    DWORD e = GetLastError();
    free(wt);
    free(wl);
    SetLastError(e);
    return rc;
}
#endif

static z_@Result_null_IoError@_t z_io_symlink(
    z_@StringView@_t target, z_@StringView@_t link
);
static z_@Result_null_IoError@_t z_io_symlink(
    z_@StringView@_t target, z_@StringView@_t link
) {
#if defined(_WIN32)
    int rc = z_io_win_symlink(target, link);
    int e = (int)GetLastError();
#else
    char* target_cstr = z_sv_to_cstr(target);
    char* link_cstr = z_sv_to_cstr(link);
    int rc = symlink(target_cstr, link_cstr);
    int e = errno;
    free(target_cstr);
    free(link_cstr);
#endif
    return z_io_wrap_null_Result(rc, e);
}
