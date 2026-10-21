/*
 * ucrt.c -- the C library functions the Universal CRT supplies only as header
 * inlines, as real functions for tcc's headers to call.
 *
 * zerolang local patch (see VERSION.md): this tcc links ucrtbase.dll, which
 * exports no printf or scanf family -- its own headers define each one inline
 * over __stdio_common_* -- no time() family (only _time64 and friends), and no
 * atexit(). tcc's headers declare them all as plain functions, so libtcc1.a
 * provides them here, as mingw-w64's libucrt does. The printf family asks for
 * the IEEE rounding of exactly-halfway values and C99's snprintf, as mingw-w64
 * and the Windows SDK headers do.
 *
 * This file is part of the zerolang vendored copy of TinyCC and is licensed
 * under the GNU Lesser General Public License, version 2.1, as TinyCC is.
 */
#include <stdarg.h>
#include <stddef.h>

typedef struct _iobuf FILE;
typedef unsigned short wchar;
typedef long long time64;
struct tm;

FILE *__cdecl __acrt_iob_func(unsigned);
int __cdecl __stdio_common_vfprintf(unsigned long long, FILE *, const char *, void *, va_list);
int __cdecl __stdio_common_vsprintf(unsigned long long, char *, size_t, const char *, void *, va_list);
int __cdecl __stdio_common_vfwprintf(unsigned long long, FILE *, const wchar *, void *, va_list);
int __cdecl __stdio_common_vswprintf(unsigned long long, wchar *, size_t, const wchar *, void *, va_list);
int __cdecl __stdio_common_vfscanf(unsigned long long, FILE *, const char *, void *, va_list);
int __cdecl __stdio_common_vsscanf(unsigned long long, const char *, size_t, const char *, void *, va_list);
int __cdecl __stdio_common_vfwscanf(unsigned long long, FILE *, const wchar *, void *, va_list);
int __cdecl __stdio_common_vswscanf(unsigned long long, const wchar *, size_t, const wchar *, void *, va_list);

/* _CRT_INTERNAL_PRINTF_* option bits, as corecrt_stdio_config.h spells them */
#define LEGACY_VSPRINTF_NULL_TERMINATION 0x0001ULL
#define STANDARD_SNPRINTF_BEHAVIOR       0x0002ULL
#define STANDARD_ROUNDING                0x0020ULL
#define PRINTF STANDARD_ROUNDING
#define SNPRINTF (STANDARD_ROUNDING | STANDARD_SNPRINTF_BEHAVIOR)
#define LEGACY_SPRINTF (STANDARD_ROUNDING | LEGACY_VSPRINTF_NULL_TERMINATION)
#define SCANF 0ULL
#define UNBOUNDED ((size_t)-1)

/* ---- printf ---- */
int __cdecl vfprintf(FILE *s, const char *f, va_list a) { return __stdio_common_vfprintf(PRINTF, s, f, NULL, a); }
int __cdecl vprintf(const char *f, va_list a) { return vfprintf(__acrt_iob_func(1), f, a); }
int __cdecl fprintf(FILE *s, const char *f, ...) { va_list a; va_start(a, f); int r = vfprintf(s, f, a); va_end(a); return r; }
int __cdecl printf(const char *f, ...) { va_list a; va_start(a, f); int r = vprintf(f, a); va_end(a); return r; }

/* C99: always terminated (when n > 0), returns the length the whole output needs */
int __cdecl vsnprintf(char *b, size_t n, const char *f, va_list a)
{
    int r = __stdio_common_vsprintf(SNPRINTF, b, n, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl snprintf(char *b, size_t n, const char *f, ...) { va_list a; va_start(a, f); int r = vsnprintf(b, n, f, a); va_end(a); return r; }
int __cdecl vsprintf(char *b, const char *f, va_list a)
{
    int r = __stdio_common_vsprintf(LEGACY_SPRINTF, b, UNBOUNDED, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl sprintf(char *b, const char *f, ...) { va_list a; va_start(a, f); int r = vsprintf(b, f, a); va_end(a); return r; }

/* Microsoft's: not terminated when the output fills the buffer, -1 when it does not fit */
int __cdecl _vsnprintf(char *b, size_t n, const char *f, va_list a)
{
    int r = __stdio_common_vsprintf(LEGACY_SPRINTF, b, n, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl _snprintf(char *b, size_t n, const char *f, ...) { va_list a; va_start(a, f); int r = _vsnprintf(b, n, f, a); va_end(a); return r; }
int __cdecl _vscprintf(const char *f, va_list a)
{
    int r = __stdio_common_vsprintf(SNPRINTF, NULL, 0, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl _scprintf(const char *f, ...) { va_list a; va_start(a, f); int r = _vscprintf(f, a); va_end(a); return r; }

/* ---- wide printf ---- */
int __cdecl vfwprintf(FILE *s, const wchar *f, va_list a) { return __stdio_common_vfwprintf(PRINTF, s, f, NULL, a); }
int __cdecl vwprintf(const wchar *f, va_list a) { return vfwprintf(__acrt_iob_func(1), f, a); }
int __cdecl fwprintf(FILE *s, const wchar *f, ...) { va_list a; va_start(a, f); int r = vfwprintf(s, f, a); va_end(a); return r; }
int __cdecl wprintf(const wchar *f, ...) { va_list a; va_start(a, f); int r = vwprintf(f, a); va_end(a); return r; }
int __cdecl vsnwprintf(wchar *b, size_t n, const wchar *f, va_list a)
{
    int r = __stdio_common_vswprintf(SNPRINTF, b, n, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl snwprintf(wchar *b, size_t n, const wchar *f, ...) { va_list a; va_start(a, f); int r = vsnwprintf(b, n, f, a); va_end(a); return r; }
int __cdecl _vsnwprintf(wchar *b, size_t n, const wchar *f, va_list a)
{
    int r = __stdio_common_vswprintf(LEGACY_SPRINTF, b, n, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl _snwprintf(wchar *b, size_t n, const wchar *f, ...) { va_list a; va_start(a, f); int r = _vsnwprintf(b, n, f, a); va_end(a); return r; }
/* the two-argument forms tcc's headers declare (Microsoft's non-conforming swprintf) */
int __cdecl _vswprintf(wchar *b, const wchar *f, va_list a) { return _vsnwprintf(b, UNBOUNDED, f, a); }
int __cdecl _swprintf(wchar *b, const wchar *f, ...) { va_list a; va_start(a, f); int r = _vswprintf(b, f, a); va_end(a); return r; }
int __cdecl vswprintf(wchar *b, const wchar *f, va_list a) { return _vswprintf(b, f, a); }
int __cdecl swprintf(wchar *b, const wchar *f, ...) { va_list a; va_start(a, f); int r = _vswprintf(b, f, a); va_end(a); return r; }
int __cdecl _vscwprintf(const wchar *f, va_list a)
{
    int r = __stdio_common_vswprintf(SNPRINTF, NULL, 0, f, NULL, a);
    return r < 0 ? -1 : r;
}
int __cdecl _scwprintf(const wchar *f, ...) { va_list a; va_start(a, f); int r = _vscwprintf(f, a); va_end(a); return r; }

/* ---- scanf ---- */
int __cdecl vfscanf(FILE *s, const char *f, va_list a) { return __stdio_common_vfscanf(SCANF, s, f, NULL, a); }
int __cdecl vscanf(const char *f, va_list a) { return vfscanf(__acrt_iob_func(0), f, a); }
int __cdecl vsscanf(const char *b, const char *f, va_list a) { return __stdio_common_vsscanf(SCANF, b, UNBOUNDED, f, NULL, a); }
int __cdecl fscanf(FILE *s, const char *f, ...) { va_list a; va_start(a, f); int r = vfscanf(s, f, a); va_end(a); return r; }
int __cdecl scanf(const char *f, ...) { va_list a; va_start(a, f); int r = vscanf(f, a); va_end(a); return r; }
int __cdecl sscanf(const char *b, const char *f, ...) { va_list a; va_start(a, f); int r = vsscanf(b, f, a); va_end(a); return r; }
int __cdecl vfwscanf(FILE *s, const wchar *f, va_list a) { return __stdio_common_vfwscanf(SCANF, s, f, NULL, a); }
int __cdecl vwscanf(const wchar *f, va_list a) { return vfwscanf(__acrt_iob_func(0), f, a); }
int __cdecl vswscanf(const wchar *b, const wchar *f, va_list a) { return __stdio_common_vswscanf(SCANF, b, UNBOUNDED, f, NULL, a); }
int __cdecl fwscanf(FILE *s, const wchar *f, ...) { va_list a; va_start(a, f); int r = vfwscanf(s, f, a); va_end(a); return r; }
int __cdecl wscanf(const wchar *f, ...) { va_list a; va_start(a, f); int r = vwscanf(f, a); va_end(a); return r; }
int __cdecl swscanf(const wchar *b, const wchar *f, ...) { va_list a; va_start(a, f); int r = vswscanf(b, f, a); va_end(a); return r; }

/* ---- time: time_t is 64-bit, the UCRT exports only the _*64 forms ---- */
time64 __cdecl _time64(time64 *);
struct tm *__cdecl _localtime64(const time64 *);
struct tm *__cdecl _gmtime64(const time64 *);
time64 __cdecl _mktime64(struct tm *);
time64 __cdecl _mkgmtime64(struct tm *);
char *__cdecl _ctime64(const time64 *);
wchar *__cdecl _wctime64(const time64 *);
double __cdecl _difftime64(time64, time64);
time64 __cdecl time(time64 *t) { return _time64(t); }
struct tm *__cdecl localtime(const time64 *t) { return _localtime64(t); }
struct tm *__cdecl gmtime(const time64 *t) { return _gmtime64(t); }
time64 __cdecl mktime(struct tm *t) { return _mktime64(t); }
time64 __cdecl _mkgmtime(struct tm *t) { return _mkgmtime64(t); }
char *__cdecl ctime(const time64 *t) { return _ctime64(t); }
wchar *__cdecl _wctime(const time64 *t) { return _wctime64(t); }
double __cdecl difftime(time64 a, time64 b) { return _difftime64(a, b); }

/* ---- atexit: the UCRT keeps the process's exit table ---- */
int __cdecl _crt_atexit(void (__cdecl *)(void));
int __cdecl atexit(void (__cdecl *f)(void)) { return _crt_atexit(f); }
