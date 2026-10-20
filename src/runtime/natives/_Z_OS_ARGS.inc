/* os.args -- copy argv into a freshly-allocated list of strings.
   The caller owns the outer list and every string inside; scope
   exit runs the list destructor which frees each element.

   On Windows the arguments come from the command line itself
   (GetCommandLineW), as UTF-16, split by the rules CommandLineToArgvW
   and the C runtime use: the program name ends at the first space
   outside quotes and takes no escapes; after it, 2n backslashes and a
   quote are n backslashes and a quote that opens or closes quoting,
   2n+1 backslashes and a quote are n backslashes and a literal quote,
   "" inside quotes is a literal quote, and other backslashes are
   literal. */
#if defined(_WIN32)
static void z_os_win_arg(z_@List_String@_t* out, const wchar_t* w, size_t n) {
    size_t un = 0;
    char* u = z_win_utf8(w, (int)n, &un);
    z_@String@_t s = z_@String@_create((uint64_t)un);
    if (u != NULL) z_@String@_append(&s, u, (uint64_t)un);
    free(u);
    z_@List_String@_append(out, s);
}
#endif
static z_@List_String@_t z_os_args(void);
static z_@List_String@_t z_os_args(void) {
#if defined(_WIN32)
    z_@List_String@_t out = z_@List_String@_create((uint64_t)0);
    const wchar_t* c = GetCommandLineW();
    size_t total = wcslen(c);
    wchar_t* cur = (wchar_t*)z_xmalloc((total + 1) * sizeof(wchar_t));
    size_t cn = 0;
    if (*c == 0) {
        wchar_t me[MAX_PATH];
        DWORD got = GetModuleFileNameW(NULL, me, MAX_PATH);
        z_os_win_arg(&out, me, got < MAX_PATH ? got : 0);
    } else {
        int inq = 0;
        for (; *c != 0; c++) {
            if (*c == L'"') {
                inq = !inq;
                continue;
            }
            if (!inq && (*c == L' ' || *c == L'\t')) break;
            cur[cn++] = *c;
        }
        z_os_win_arg(&out, cur, cn);
    }
    while (*c == L' ' || *c == L'\t') c++;
    while (*c != 0) {
        int inq = 0;
        size_t bs = 0;
        cn = 0;
        for (; *c != 0; c++) {
            if (*c == L'\\') {
                bs++;
                continue;
            }
            if (*c == L'"') {
                for (size_t k = 0; k < bs / 2; k++) cur[cn++] = L'\\';
                int literal = (bs % 2) != 0;
                bs = 0;
                if (literal) {
                    cur[cn++] = L'"';
                } else if (inq && c[1] == L'"') {
                    cur[cn++] = L'"';
                    c++;
                } else {
                    inq = !inq;
                }
                continue;
            }
            for (; bs > 0; bs--) cur[cn++] = L'\\';
            if (!inq && (*c == L' ' || *c == L'\t')) break;
            cur[cn++] = *c;
        }
        for (; bs > 0; bs--) cur[cn++] = L'\\';
        z_os_win_arg(&out, cur, cn);
        while (*c == L' ' || *c == L'\t') c++;
    }
    free(cur);
    return out;
#else
    uint64_t n = (uint64_t)(z_os_argc_g > 0 ? z_os_argc_g : 0);
    z_@List_String@_t out = {0};
    out.length = n;
    out.capacity = n;
    if (n > 0) {
        out.data = (z_@String@_t*)z_xmalloc(n * sizeof(z_@String@_t));
        for (uint64_t i = 0; i < n; i++) {
            out.data[i] = z_@String@_new(z_os_argv_g[i]);
        }
    }
    return out;
#endif
}
