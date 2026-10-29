/* z_win -- the Win32 layer under the io, os and net natives: UTF-8 <-> UTF-16
   at the boundary, native paths (both separators accepted; an absolute path
   too long for MAX_PATH gets the \\?\ verbatim prefix), the open every file
   and directory handle goes through, and the text of an OS error. Empty on
   every other system. Loaded by a row naming `needs=prelude.zwin32`.

     z_win_wide(s, n)        UTF-8 -> a NUL-terminated UTF-16 copy
     z_win_utf8(w, wn, &len) UTF-16 (wn -1: NUL-terminated) -> a UTF-8 copy
     z_win_path(s, n)        a path as UTF-16, verbatim-prefixed when long
     z_win_root_len(s, n)    the bytes of a path's root (C:\, \\srv\share\, \)
     z_win_open(...)         CreateFileW on a path, never inheritable
     z_win_open_fd(s, n, how) a C runtime fd over such a file
     z_win_read/write/seek/close(fd, ...) one call on an fd's handle
     z_win_error_text(code)  FormatMessageW's text, as UTF-8
     z_win_from_errno(e)     the Win32 code for a C runtime errno
     z_win_getenv/setenv/unsetenv  the environment, Win32's and the C runtime's

   Each answers NULL / INVALID_HANDLE_VALUE on failure with the reason left
   for GetLastError, so a native reads every failure the same way. */
#ifndef Z_WIN32_SHIM_INCLUDED
#define Z_WIN32_SHIM_INCLUDED
#if defined(_WIN32)
#include <limits.h>
#include <wchar.h>
#include <windows.h>
#include <winioctl.h>
#include <stddef.h>

/*@@row @prelude.win_from_errno demand=@win_from_errno@@*/
/* the Win32 code for an errno the C runtime set, for the few calls that go
   through it (_close, the environment) */
static DWORD z_win_from_errno(int e) {
    switch (e) {
        case ENOENT: return ERROR_FILE_NOT_FOUND;
        case EACCES: return ERROR_ACCESS_DENIED;
        case EEXIST: return ERROR_FILE_EXISTS;
        case EBADF:  return ERROR_INVALID_HANDLE;
        case EMFILE: return ERROR_TOO_MANY_OPEN_FILES;
        case ENOMEM: return ERROR_NOT_ENOUGH_MEMORY;
        case ENOSPC: return ERROR_DISK_FULL;
        default:     return ERROR_INVALID_PARAMETER;
    }
}
/*@@end@@*/

/*@@row @prelude.win_wide demand=@win_wide needs=@xmalloc@@*/
static wchar_t* z_win_wide(const char* s, size_t n) {
    if (n > (size_t)INT_MAX) {
        SetLastError(ERROR_FILENAME_EXCED_RANGE);
        return NULL;
    }
    int wn = 0;
    if (n > 0) {
        wn = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, (int)n, NULL, 0);
        if (wn == 0) return NULL;
    }
    wchar_t* w = (wchar_t*)z_xmalloc(((size_t)wn + 1) * sizeof(wchar_t));
    if (n > 0) MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, (int)n, w, wn);
    w[wn] = 0;
    return w;
}
/*@@end@@*/

/*@@row @prelude.win_utf8 demand=@win_utf8 needs=@xmalloc@@*/
static char* z_win_utf8(const wchar_t* w, int wn, size_t* len) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w, wn, NULL, 0, NULL, NULL);
    if (n == 0 && wn != 0) return NULL;
    char* s = (char*)z_xmalloc((size_t)n + 1);
    if (n > 0) WideCharToMultiByte(CP_UTF8, 0, w, wn, s, n, NULL, NULL);
    /* a NUL-terminated source counts its terminator */
    if (wn < 0 && n > 0) n--;
    s[n] = '\0';
    if (len) *len = (size_t)n;
    return s;
}
/*@@end@@*/

/*@@row @prelude.win_sep demand=@win_sep@@*/
static int z_win_sep(char c) {
    return c == '\\' || c == '/';
}
/*@@end@@*/

/*@@row @prelude.win_root_len demand=@win_root_len needs=@win_sep@@*/
/* the root of a path, in bytes: "\\?\C:\" and "\\?\UNC\srv\share\" (verbatim),
   "\\srv\share\" (UNC), "C:\", "C:" (drive-relative), "\" (current drive);
   0 for a relative path */
static size_t z_win_root_len(const char* s, size_t n) {
    if (n >= 4 && z_win_sep(s[0]) && z_win_sep(s[1]) && (s[2] == '?' || s[2] == '.') && z_win_sep(s[3])) {
        size_t i = 4;
        if (n >= 8 && (s[4] == 'U' || s[4] == 'u') && (s[5] == 'N' || s[5] == 'n') && (s[6] == 'C' || s[6] == 'c')
            && z_win_sep(s[7])) {
            i = 8;
            for (int parts = 0; parts < 2 && i < n; parts++) {
                while (i < n && !z_win_sep(s[i])) i++;
                if (i < n) i++;
            }
            return i;
        }
        while (i < n && !z_win_sep(s[i])) i++;
        return i < n ? i + 1 : i;
    }
    if (n >= 2 && z_win_sep(s[0]) && z_win_sep(s[1])) {
        size_t i = 2;
        for (int parts = 0; parts < 2 && i < n; parts++) {
            while (i < n && !z_win_sep(s[i])) i++;
            if (i < n) i++;
        }
        return i;
    }
    if (n >= 2 && s[1] == ':' && ((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z'))) {
        return (n >= 3 && z_win_sep(s[2])) ? 3 : 2;
    }
    if (n >= 1 && z_win_sep(s[0])) return 1;
    return 0;
}
/*@@end@@*/

/*@@row @prelude.win_path demand=@win_path needs=@win_wide,@xmalloc@@*/
/* the path as UTF-16. A path the plain Win32 API cannot take whole -- one
   reaching MAX_PATH less the 12 a directory needs for its 8.3 entry -- is made
   absolute and given the \\?\ prefix (\\?\UNC\ for a share), which lifts the
   limit; GetFullPathNameW turns '/' into '\' on the way, as a verbatim path
   requires. */
static wchar_t* z_win_path(const char* s, size_t n) {
    wchar_t* w = z_win_wide(s, n);
    if (w == NULL) return NULL;
    size_t wn = wcslen(w);
    if (wn < MAX_PATH - 12) return w;
    if (wn >= 4 && w[0] == L'\\' && w[1] == L'\\' && w[2] == L'?' && w[3] == L'\\') return w;
    DWORD need = GetFullPathNameW(w, 0, NULL, NULL);
    if (need == 0) {
        free(w);
        return NULL;
    }
    wchar_t* full = (wchar_t*)z_xmalloc(((size_t)need + 8) * sizeof(wchar_t));
    DWORD got = GetFullPathNameW(w, need, full + 8, NULL);
    free(w);
    if (got == 0 || got >= need) {
        free(full);
        return NULL;
    }
    wchar_t* p = full + 8;
    wchar_t* out;
    if (p[0] == L'\\' && p[1] == L'\\') {
        /* \\srv\share\x -> \\?\UNC\srv\share\x, over the leading two */
        out = p - 6;
        memcpy(out, L"\\\\?\\UNC", 7 * sizeof(wchar_t));
        out[7] = L'\\';
        memmove(out + 8, p + 2, ((size_t)got - 1) * sizeof(wchar_t));
    } else {
        out = p - 4;
        memcpy(out, L"\\\\?\\", 4 * sizeof(wchar_t));
    }
    if (out != full) memmove(full, out, (wcslen(out) + 1) * sizeof(wchar_t));
    return full;
}
/*@@end@@*/

/*@@row @prelude.win_open demand=@win_open needs=@win_path@@*/
/* CreateFileW on a UTF-8 path: shared for reading, writing and deletion, as
   POSIX files are, and never inherited by a child. A directory opened for
   access a directory cannot give answers ERROR_DIRECTORY_NOT_SUPPORTED
   rather than access denied, so the caller sees isdir, as on POSIX. */
static HANDLE z_win_open(const char* s, size_t n, DWORD access, DWORD disposition, DWORD flags) {
    wchar_t* w = z_win_path(s, n);
    if (w == NULL) return INVALID_HANDLE_VALUE;
    SECURITY_ATTRIBUTES sa = { sizeof(SECURITY_ATTRIBUTES), NULL, FALSE };
    HANDLE h = CreateFileW(
        w, access, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, &sa, disposition, flags, NULL
    );
    if (h == INVALID_HANDLE_VALUE) {
        DWORD e = GetLastError();
        if (e == ERROR_ACCESS_DENIED) {
            DWORD a = GetFileAttributesW(w);
            if (a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY)) e = ERROR_DIRECTORY_NOT_SUPPORTED;
        }
        free(w);
        SetLastError(e);
        return INVALID_HANDLE_VALUE;
    }
    free(w);
    return h;
}
/*@@end@@*/

/*@@row @prelude.win_open_fd demand=@win_open_fd needs=@win_open@@*/
/* a C runtime fd over a file z_win_open opens: `how` 0 reads an existing file,
   1 writes one created or truncated, 2 appends to one created if missing --
   through FILE_APPEND_DATA, so every write lands at the end whoever else
   writes. Binary, and not inherited. -1 on failure. */
static int z_win_open_fd(const char* s, size_t n, int how) {
    DWORD access = GENERIC_READ;
    DWORD disposition = OPEN_EXISTING;
    int crt = _O_BINARY | _O_NOINHERIT | _O_RDONLY;
    if (how == 1) {
        access = GENERIC_WRITE;
        disposition = CREATE_ALWAYS;
        crt = _O_BINARY | _O_NOINHERIT | _O_WRONLY;
    } else if (how == 2) {
        access = FILE_GENERIC_WRITE & ~FILE_WRITE_DATA;
        disposition = OPEN_ALWAYS;
        crt = _O_BINARY | _O_NOINHERIT | _O_WRONLY | _O_APPEND;
    }
    HANDLE h = z_win_open(s, n, access, disposition, FILE_ATTRIBUTE_NORMAL);
    if (h == INVALID_HANDLE_VALUE) return -1;
    int fd = _open_osfhandle((intptr_t)h, crt);
    if (fd < 0) {
        CloseHandle(h);
        SetLastError(ERROR_TOO_MANY_OPEN_FILES);
    }
    return fd;
}
/*@@end@@*/

/*@@row @prelude.win_read demand=@win_read@@*/
/* read / write / seek on an fd's handle, one call each: the byte count or the
   new position, -1 on failure. A pipe whose writer has gone reads as the end
   of the stream, as on POSIX. */
static int64_t z_win_read(int fd, void* buf, uint64_t n) {
    DWORD got = 0;
    DWORD ask = n > 0x40000000u ? 0x40000000u : (DWORD)n;
    if (!ReadFile((HANDLE)_get_osfhandle(fd), buf, ask, &got, NULL)) {
        DWORD e = GetLastError();
        if (e == ERROR_BROKEN_PIPE || e == ERROR_HANDLE_EOF) return 0;
        return -1;
    }
    return (int64_t)got;
}
/*@@end@@*/

/*@@row @prelude.win_write demand=@win_write@@*/
static int64_t z_win_write(int fd, const void* buf, uint64_t n) {
    DWORD put = 0;
    DWORD ask = n > 0x40000000u ? 0x40000000u : (DWORD)n;
    if (!WriteFile((HANDLE)_get_osfhandle(fd), buf, ask, &put, NULL)) return -1;
    return (int64_t)put;
}
/*@@end@@*/

/*@@row @prelude.win_seek demand=@win_seek@@*/
/* `whence` is SEEK_SET, SEEK_CUR or SEEK_END, which are FILE_BEGIN,
   FILE_CURRENT and FILE_END's values */
static int64_t z_win_seek(int fd, int64_t off, int whence) {
    LARGE_INTEGER d;
    LARGE_INTEGER at;
    d.QuadPart = off;
    if (!SetFilePointerEx((HANDLE)_get_osfhandle(fd), d, &at, (DWORD)whence)) return -1;
    return (int64_t)at.QuadPart;
}
/*@@end@@*/

/*@@row @prelude.win_close demand=@win_close needs=@win_from_errno@@*/
/* _close, its failure as a Win32 code: the C runtime keeps the OS's in
   _doserrno when the OS refused, and only an errno when it refused itself */
static int z_win_close(int fd) {
    if (_close(fd) == 0) return 0;
    DWORD e = (DWORD)_doserrno;
    SetLastError(e != 0 ? e : z_win_from_errno(errno));
    return -1;
}
/*@@end@@*/

/*@@row @prelude.win_getenv demand=@win_getenv needs=@win_utf8,@win_wide,@xmalloc@@*/
/* the environment: Win32's, which a spawned child receives and whose names
   match regardless of case, kept in step with the C runtime's copy (_wgetenv's)
   for C code that reads that one. z_win_getenv answers a UTF-8 copy or NULL;
   the setters 0, or -1 with GetLastError set. */
static char* z_win_getenv(const char* k, size_t kn) {
    wchar_t* wk = z_win_wide(k, kn);
    if (wk == NULL) return NULL;
    SetLastError(0);
    DWORD need = GetEnvironmentVariableW(wk, NULL, 0);
    char* out = NULL;
    if (need == 0) {
        /* a variable set to "" answers 0 with no error */
        if (GetLastError() == 0) out = z_win_utf8(L"", 0, NULL);
    } else {
        wchar_t* wv = (wchar_t*)z_xmalloc((size_t)need * sizeof(wchar_t));
        DWORD got = GetEnvironmentVariableW(wk, wv, need);
        if (got < need) out = z_win_utf8(wv, (int)got, NULL);
        free(wv);
    }
    free(wk);
    return out;
}
/*@@end@@*/

/*@@row @prelude.win_setenv demand=@win_setenv needs=@win_wide@@*/
static int z_win_setenv(const char* k, size_t kn, const char* v, size_t vn) {
    wchar_t* wk = z_win_wide(k, kn);
    wchar_t* wv = wk != NULL ? z_win_wide(v, vn) : NULL;
    if (wv == NULL) {
        free(wk);
        return -1;
    }
    BOOL ok = kn > 0 && SetEnvironmentVariableW(wk, wv);
    DWORD e = kn > 0 ? GetLastError() : ERROR_INVALID_PARAMETER;
    /* the C runtime takes "" as removal; Win32 keeps an empty value */
    if (ok && vn > 0) _wputenv_s(wk, wv);
    free(wk);
    free(wv);
    SetLastError(e);
    return ok ? 0 : -1;
}
/*@@end@@*/

/*@@row @prelude.win_unsetenv demand=@win_unsetenv needs=@win_wide@@*/
static int z_win_unsetenv(const char* k, size_t kn) {
    wchar_t* wk = z_win_wide(k, kn);
    if (wk == NULL) return -1;
    BOOL ok = kn > 0 && SetEnvironmentVariableW(wk, NULL);
    DWORD e = kn > 0 ? GetLastError() : ERROR_INVALID_PARAMETER;
    /* removing a variable that is not there is not a failure, as on POSIX */
    if (!ok && e == ERROR_ENVVAR_NOT_FOUND) ok = TRUE;
    if (ok) _wputenv_s(wk, L"");
    free(wk);
    SetLastError(e);
    return ok ? 0 : -1;
}
/*@@end@@*/

/*@@row @prelude.win_error_text demand=@win_error_text needs=@win_utf8@@*/
/* FormatMessageW's text for a Win32 error, as UTF-8 without the trailing line
   break; NULL when the system has none */
static char* z_win_error_text(DWORD code) {
    wchar_t* w = NULL;
    DWORD n = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        NULL, code, 0, (LPWSTR)&w, 0, NULL
    );
    if (n == 0 || w == NULL) return NULL;
    while (n > 0 && (w[n - 1] == L'\r' || w[n - 1] == L'\n' || w[n - 1] == L' ')) n--;
    char* s = z_win_utf8(w, (int)n, NULL);
    LocalFree(w);
    return s;
}
/*@@end@@*/


#endif
#endif
