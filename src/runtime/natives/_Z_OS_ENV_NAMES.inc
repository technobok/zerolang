/* os.env_names -- snapshot of the environment's names. Each entry is
   `key=value`; we copy the portion before the first `=` into a
   freshly-allocated z_@String@_t and push onto an owned list. The list's
   destructor frees each string. Windows' block (GetEnvironmentStringsW)
   also holds a hidden `=C:=C:\dir` entry per drive -- the shell's current
   directories -- which names nothing a program set and is left out. */
#if !defined(_WIN32)
extern char** environ;
#endif
static z_@List_String@_t z_os_envNames(void);
static z_@List_String@_t z_os_envNames(void) {
    z_@List_String@_t out = z_@List_String@_create((uint64_t)0);
#if defined(_WIN32)
    wchar_t* block = GetEnvironmentStringsW();
    if (block == NULL) return out;
    for (const wchar_t* ep = block; *ep != 0; ep += wcslen(ep) + 1) {
        if (ep[0] == L'=') continue;
        const wchar_t* eq = wcschr(ep, L'=');
        int n = eq != NULL ? (int)(eq - ep) : (int)wcslen(ep);
        size_t un = 0;
        char* u = z_win_utf8(ep, n, &un);
        if (u == NULL) continue;
        z_@String@_t s = z_@String@_create((uint64_t)un);
        z_@String@_append(&s, u, (uint64_t)un);
        free(u);
        z_@List_String@_append(&out, s);
    }
    FreeEnvironmentStringsW(block);
#else
    if (environ == NULL) return out;
    for (char** ep = environ; *ep != NULL; ep++) {
        const char* entry = *ep;
        const char* eq = strchr(entry, '=');
        size_t n = (eq != NULL) ? (size_t)(eq - entry) : strlen(entry);
        z_@String@_t s = z_@String@_create((uint64_t)n);
        z_@String@_append(&s, entry, (uint64_t)n);
        z_@List_String@_append(&out, s);
    }
#endif
    return out;
}
