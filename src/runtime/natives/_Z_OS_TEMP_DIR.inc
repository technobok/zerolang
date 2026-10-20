/* os.tempDir -- $TMPDIR, else /tmp; on Windows GetTempPathW (which reads
   %TMP%, %TEMP%, %USERPROFILE%, then the Windows directory), a native path.
   A trailing separator is dropped unless the path is a root. */
static z_@String@_t z_os_tempDir(void);
static z_@String@_t z_os_tempDir(void) {
#if defined(_WIN32)
    wchar_t w[MAX_PATH + 2];
    DWORD n = GetTempPathW(MAX_PATH + 1, w);
    char* u = NULL;
    size_t un = 0;
    if (n > 0 && n <= MAX_PATH + 1) u = z_win_utf8(w, (int)n, &un);
    if (u == NULL) return z_@String@_new("C:\\Windows\\Temp");
    while (un > 1 && (u[un - 1] == '\\' || u[un - 1] == '/') && !(un == 3 && u[1] == ':')) u[--un] = '\0';
    z_@String@_t out = z_@String@_new(u);
    free(u);
    return out;
#else
    const char* t = getenv("TMPDIR");
    if (t == NULL || t[0] == '\0') t = "/tmp";
    size_t n = strlen(t);
    while (n > 1 && t[n - 1] == '/') n--;
    z_@String@_t out = z_@String@_create((uint64_t)n);
    z_@String@_append(&out, t, (uint64_t)n);
    return out;
#endif
}
