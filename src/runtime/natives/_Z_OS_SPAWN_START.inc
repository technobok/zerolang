/* z_os_spawn_start -- start argv as a child, the start os.spawn and
   os.spawnAsync share. stdout->outPath, stderr->errPath (each created or
   truncated); an empty out/err path inherits the parent's stream, and empty
   out+err inherits stdin too (else stdin is the null device). A non-empty cwd
   is the child's working directory. Answers 0 with the child's pid and the
   OS's handle on it, or the OS error code: the redirections could not be
   opened, the directory could not be entered, or the program could not be
   started.

   The redirection files are opened in the parent before the child starts, so
   the child's cwd cannot change their resolution, and no other child started
   meanwhile inherits them: a child sees only its three standard streams.

   POSIX: posix_spawnp (argv[0] resolved via PATH), which unlike fork is safe in
   a program that runs threads -- the child runs no code of ours between its
   start and the exec; the redirections are opened close-on-exec and handed
   over as 0/1/2 by file actions (dup2 clears close-on-exec on the copy). The
   handle is a pidfd, -1 on a kernel without them.

   Windows: CreateProcessW with a command line quoted so CommandLineToArgvW
   (and the C runtime) split it back into argv exactly; argv[0] is resolved
   through PATH, a name without an extension tried with .exe and .com; a .bat
   or .cmd is refused (ERROR_BAD_EXE_FORMAT) -- cmd.exe runs those, by its own
   quoting rules. The three standard handles are duplicated inheritable and
   are the only handles the child inherits (PROC_THREAD_ATTRIBUTE_HANDLE_LIST).
   The handle is the process handle. argv element buffers are NUL-terminated
   and borrowed. */
#if defined(_WIN32)
/* append `s` to the command line `w`, quoted when it must be */
static size_t z_os_win_quote(wchar_t* w, size_t at, const wchar_t* s, int force) {
    int plain = !force && s[0] != 0;
    for (const wchar_t* c = s; *c != 0 && plain; c++) {
        if (*c == L' ' || *c == L'\t' || *c == L'\n' || *c == L'\v' || *c == L'"') plain = 0;
    }
    if (plain) {
        for (const wchar_t* c = s; *c != 0; c++) w[at++] = *c;
        return at;
    }
    w[at++] = L'"';
    size_t bs = 0;
    for (const wchar_t* c = s;; c++) {
        if (*c == L'\\') {
            bs++;
            continue;
        }
        if (*c == 0) {
            /* backslashes before the closing quote are doubled */
            for (size_t k = 0; k < bs * 2; k++) w[at++] = L'\\';
            break;
        }
        if (*c == L'"') {
            for (size_t k = 0; k < bs * 2 + 1; k++) w[at++] = L'\\';
        } else {
            for (size_t k = 0; k < bs; k++) w[at++] = L'\\';
        }
        bs = 0;
        w[at++] = *c;
    }
    w[at++] = L'"';
    return at;
}

static int z_os_win_is_file(const wchar_t* p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

/* the program argv[0] names: as written when it holds a separator, else the
   first PATH directory that has it; either way tried as written when it has
   an extension, else with .exe, then .com. NULL with the last error set when
   there is none. */
static wchar_t* z_os_win_find_program(const wchar_t* name) {
    size_t n = wcslen(name);
    int has_sep = wcschr(name, L'\\') != NULL || wcschr(name, L'/') != NULL || (n >= 2 && name[1] == L':');
    const wchar_t* base = name;
    for (const wchar_t* c = name; *c != 0; c++) if (*c == L'\\' || *c == L'/' || *c == L':') base = c + 1;
    int has_ext = wcschr(base, L'.') != NULL;
    const wchar_t* exts[] = { L"", L".exe", L".com" };
    int first = has_ext ? 0 : 1;
    int last = has_ext ? 0 : 2;
    wchar_t* path = NULL;
    DWORD pn = 0;
    if (!has_sep) {
        pn = GetEnvironmentVariableW(L"PATH", NULL, 0);
        if (pn > 0) {
            path = (wchar_t*)z_xmalloc((size_t)pn * sizeof(wchar_t));
            if (GetEnvironmentVariableW(L"PATH", path, pn) == 0) path[0] = 0;
        }
    }
    const wchar_t* dir = has_sep ? L"" : (path != NULL ? path : L"");
    wchar_t* found = NULL;
    for (;;) {
        const wchar_t* end = has_sep ? dir : wcschr(dir, L';');
        size_t dn = has_sep ? 0 : (end != NULL ? (size_t)(end - dir) : wcslen(dir));
        if (has_sep || dn > 0) {
            for (int k = first; k <= last && found == NULL; k++) {
                size_t cap = dn + 1 + n + 5;
                wchar_t* cand = (wchar_t*)z_xmalloc(cap * sizeof(wchar_t));
                size_t at = 0;
                for (size_t i = 0; i < dn; i++) cand[at++] = dir[i];
                if (dn > 0 && cand[at - 1] != L'\\' && cand[at - 1] != L'/') cand[at++] = L'\\';
                for (size_t i = 0; i < n; i++) cand[at++] = name[i];
                for (const wchar_t* e = exts[k]; *e != 0; e++) cand[at++] = *e;
                cand[at] = 0;
                if (z_os_win_is_file(cand)) found = cand; else free(cand);
            }
        }
        if (found != NULL || has_sep || end == NULL) break;
        dir = end + 1;
    }
    free(path);
    if (found == NULL) SetLastError(ERROR_FILE_NOT_FOUND);
    return found;
}

/* an inheritable copy of a standard handle, or of the null device when this
   process has none; NULL on failure */
static HANDLE z_os_win_inheritable(HANDLE h) {
    HANDLE dup = NULL;
    if (h == NULL || h == INVALID_HANDLE_VALUE) {
        SECURITY_ATTRIBUTES sa = { sizeof(SECURITY_ATTRIBUTES), NULL, TRUE };
        dup = CreateFileW(L"NUL", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, NULL);
        return dup == INVALID_HANDLE_VALUE ? NULL : dup;
    }
    if (!DuplicateHandle(GetCurrentProcess(), h, GetCurrentProcess(), &dup, 0, TRUE, DUPLICATE_SAME_ACCESS)) return NULL;
    return dup;
}

/* a redirection file, opened for writing and inheritable */
static HANDLE z_os_win_redirect(z_@StringView@_t path) {
    wchar_t* w = z_win_path(path.data, path.size);
    if (w == NULL) return NULL;
    SECURITY_ATTRIBUTES sa = { sizeof(SECURITY_ATTRIBUTES), NULL, TRUE };
    HANDLE h = CreateFileW(w, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD e = GetLastError();
    free(w);
    SetLastError(e);
    return h == INVALID_HANDLE_VALUE ? NULL : h;
}
#else
#include <spawn.h>
extern char** environ;
#endif

static int z_os_spawn_start(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath, int32_t* pid_out, int64_t* handle_out
);
static int z_os_spawn_start(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath, int32_t* pid_out, int64_t* handle_out
) {
    *pid_out = 0;
    *handle_out = -1;
    int inherit_out = (outPath.size == 0);
    int inherit_err = (errPath.size == 0);
    int inherit_in = (inherit_out && inherit_err);
#if defined(_WIN32)
    if (argv->length == 0) return ERROR_INVALID_PARAMETER;
    /* the arguments as UTF-16, and the program they name */
    wchar_t** wargs = (wchar_t**)z_xmalloc(argv->length * sizeof(wchar_t*));
    size_t line_cap = 1;
    int bad = 0;
    for (uint64_t i = 0; i < argv->length; i++) {
        wargs[i] = z_win_wide(argv->data[i].data, argv->data[i].size);
        if (wargs[i] == NULL) {
            bad = 1;
            wargs[i] = z_win_wide("", 0);
        }
        line_cap += wcslen(wargs[i]) * 2 + 3;
    }
    int rc = 0;
    wchar_t* program = NULL;
    if (bad) {
        rc = ERROR_NO_UNICODE_TRANSLATION;
    } else if (wcschr(wargs[0], L'"') != NULL) {
        rc = ERROR_INVALID_NAME;
    } else {
        program = z_os_win_find_program(wargs[0]);
        if (program == NULL) {
            rc = (int)GetLastError();
        } else {
            size_t pl = wcslen(program);
            const wchar_t* ext = pl >= 4 ? program + pl - 4 : L"";
            if (_wcsicmp(ext, L".bat") == 0 || _wcsicmp(ext, L".cmd") == 0) rc = ERROR_BAD_EXE_FORMAT;
        }
    }
    wchar_t* line = NULL;
    if (rc == 0) {
        line = (wchar_t*)z_xmalloc(line_cap * sizeof(wchar_t));
        size_t at = z_os_win_quote(line, 0, wargs[0], 1);
        for (uint64_t i = 1; i < argv->length; i++) {
            line[at++] = L' ';
            at = z_os_win_quote(line, at, wargs[i], 0);
        }
        line[at] = 0;
    }
    for (uint64_t i = 0; i < argv->length; i++) free(wargs[i]);
    free(wargs);
    HANDLE hin = NULL;
    HANDLE hout = NULL;
    HANDLE herr = NULL;
    wchar_t* wcwd = NULL;
    if (rc == 0) {
        hout = inherit_out ? z_os_win_inheritable(GetStdHandle(STD_OUTPUT_HANDLE)) : z_os_win_redirect(outPath);
        if (hout == NULL) rc = (int)GetLastError();
    }
    if (rc == 0) {
        herr = inherit_err ? z_os_win_inheritable(GetStdHandle(STD_ERROR_HANDLE)) : z_os_win_redirect(errPath);
        if (herr == NULL) rc = (int)GetLastError();
    }
    if (rc == 0) {
        hin = z_os_win_inheritable(inherit_in ? GetStdHandle(STD_INPUT_HANDLE) : NULL);
        if (hin == NULL) rc = (int)GetLastError();
    }
    if (rc == 0 && cwd.size > 0) {
        wcwd = z_win_path(cwd.data, cwd.size);
        if (wcwd == NULL) {
            rc = (int)GetLastError();
        } else {
            DWORD a = GetFileAttributesW(wcwd);
            if (a == INVALID_FILE_ATTRIBUTES) rc = (int)GetLastError();
            else if (!(a & FILE_ATTRIBUTE_DIRECTORY)) rc = ERROR_DIRECTORY;
        }
    }
    LPPROC_THREAD_ATTRIBUTE_LIST attrs = NULL;
    HANDLE inherit[3] = { hin, hout, herr };
    if (rc == 0) {
        SIZE_T asize = 0;
        InitializeProcThreadAttributeList(NULL, 1, 0, &asize);
        attrs = (LPPROC_THREAD_ATTRIBUTE_LIST)z_xmalloc(asize);
        if (!InitializeProcThreadAttributeList(attrs, 1, 0, &asize)
            || !UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherit, sizeof inherit, NULL, NULL)) {
            rc = (int)GetLastError();
        }
    }
    if (rc == 0) {
        STARTUPINFOEXW si;
        memset(&si, 0, sizeof si);
        si.StartupInfo.cb = sizeof si;
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        si.StartupInfo.hStdInput = hin;
        si.StartupInfo.hStdOutput = hout;
        si.StartupInfo.hStdError = herr;
        si.lpAttributeList = attrs;
        PROCESS_INFORMATION pi;
        if (CreateProcessW(program, line, NULL, NULL, TRUE, CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                           NULL, wcwd, &si.StartupInfo, &pi)) {
            CloseHandle(pi.hThread);
            *pid_out = (int32_t)pi.dwProcessId;
            *handle_out = (int64_t)(intptr_t)pi.hProcess;
        } else {
            rc = (int)GetLastError();
        }
    }
    if (attrs != NULL) {
        DeleteProcThreadAttributeList(attrs);
        free(attrs);
    }
    if (hin != NULL) CloseHandle(hin);
    if (hout != NULL) CloseHandle(hout);
    if (herr != NULL) CloseHandle(herr);
    free(wcwd);
    free(line);
    free(program);
    return rc;
#else
    if (argv->length == 0) return EINVAL;
    char** c_argv = (char**)z_xmalloc((argv->length + 1) * sizeof(char*));
    for (uint64_t i = 0; i < argv->length; i++) {
        c_argv[i] = argv->data[i].data;
    }
    c_argv[argv->length] = NULL;
    char* cwd_c = z_sv_to_cstr(cwd);
    int rc = 0;
    int out_fd = -1;
    int err_fd = -1;
    int null_fd = -1;
    if (!inherit_out) {
        char* out_c = z_sv_to_cstr(outPath);
        out_fd = open(out_c, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (out_fd < 0) rc = errno;
        free(out_c);
    }
    if (rc == 0 && !inherit_err) {
        char* err_c = z_sv_to_cstr(errPath);
        err_fd = open(err_c, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (err_fd < 0) rc = errno;
        free(err_c);
    }
    if (rc == 0 && !inherit_in) {
        null_fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
        if (null_fd < 0) rc = errno;
    }
    if (rc == 0) {
        posix_spawn_file_actions_t fa;
        posix_spawn_file_actions_init(&fa);
        if (cwd.size > 0) rc = posix_spawn_file_actions_addchdir_np(&fa, cwd_c);
        if (rc == 0 && !inherit_in) rc = posix_spawn_file_actions_adddup2(&fa, null_fd, 0);
        if (rc == 0 && !inherit_out) rc = posix_spawn_file_actions_adddup2(&fa, out_fd, 1);
        if (rc == 0 && !inherit_err) rc = posix_spawn_file_actions_adddup2(&fa, err_fd, 2);
        pid_t child = -1;
        if (rc == 0) rc = posix_spawnp(&child, c_argv[0], &fa, NULL, c_argv, environ);
        posix_spawn_file_actions_destroy(&fa);
        if (rc == 0) {
            *pid_out = (int32_t)child;
#ifdef SYS_pidfd_open
            int pfd = (int)syscall(SYS_pidfd_open, child, 0);
            if (pfd >= 0) *handle_out = (int64_t)pfd;
#endif
        }
    }
    if (out_fd >= 0) close(out_fd);
    if (err_fd >= 0) close(err_fd);
    if (null_fd >= 0) close(null_fd);
    free(cwd_c);
    free(c_argv);
    return rc;
#endif
}
