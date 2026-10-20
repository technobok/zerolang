/* os.hostname -- gethostname(3). HOST_NAME_MAX is typically 64
   on Linux; we use 256 to be defensive on unusual POSIX hosts
   and truncate on overflow. On Windows, the DNS host name
   (GetComputerNameExW). */
static z_@Result_String_IoError@_t z_os_hostname(void);
static z_@Result_String_IoError@_t z_os_hostname(void) {
    z_@Result_String_IoError@_t out = {0};
#if defined(_WIN32)
    wchar_t w[256];
    DWORD wn = 256;
    char* name = NULL;
    int e = 0;
    if (GetComputerNameExW(ComputerNameDnsHostname, w, &wn)) {
        name = z_win_utf8(w, (int)wn, NULL);
    }
    if (name == NULL) {
        e = (int)GetLastError();
#else
    char buf[256];
    if (gethostname(buf, sizeof(buf)) != 0) {
        int e = errno;
#endif
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
#if defined(_WIN32)
    *boxed = z_@String@_new(name);
    free(name);
#else
    buf[sizeof(buf) - 1] = '\0';
    *boxed = z_@String@_new(buf);
#endif
    out.tag = @ARM_Result_String_IoError.ok@;
    out.data = boxed;
    return out;
}


