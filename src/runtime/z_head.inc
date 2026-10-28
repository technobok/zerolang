#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
/* rand_s (the hash seed on Windows) is declared only under this */
#define _CRT_RAND_S
/* windows.h, wherever a fragment includes it, without winsock 1 (net includes
   winsock2.h) or the min/max macros */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>
/*@@row @prelude.quadmath demand=@quadmath@@*/
/* libquadmath, which f128 calls: written only where written C calls it */
#if defined(__SIZEOF_FLOAT128__) && !defined(__clang__)
#include <quadmath.h>
#elif defined(__SIZEOF_FLOAT128__)
/* clang: __float128 without GCC's quadmath.h; declare each libquadmath
   symbol the runtime and the f128 rows call. */
extern __float128 fmodq(__float128 x, __float128 y);
extern __float128 strtoflt128(const char *s, char **sp);
extern int quadmath_snprintf(char *s, size_t size, const char *format, ...);
extern __float128 cbrtq(__float128 x);
extern __float128 hypotq(__float128 x, __float128 y);
extern __float128 powq(__float128 x, __float128 y);
extern __float128 expq(__float128 x);
extern __float128 exp2q(__float128 x);
extern __float128 expm1q(__float128 x);
extern __float128 logq(__float128 x);
extern __float128 log2q(__float128 x);
extern __float128 log10q(__float128 x);
extern __float128 log1pq(__float128 x);
extern __float128 sinq(__float128 x);
extern __float128 cosq(__float128 x);
extern __float128 tanq(__float128 x);
extern __float128 asinq(__float128 x);
extern __float128 acosq(__float128 x);
extern __float128 atanq(__float128 x);
extern __float128 atan2q(__float128 x, __float128 y);
extern __float128 sinhq(__float128 x);
extern __float128 coshq(__float128 x);
extern __float128 tanhq(__float128 x);
extern __float128 asinhq(__float128 x);
extern __float128 acoshq(__float128 x);
extern __float128 atanhq(__float128 x);
extern __float128 erfq(__float128 x);
extern __float128 erfcq(__float128 x);
extern __float128 tgammaq(__float128 x);
extern __float128 lgammaq(__float128 x);
extern __float128 floorq(__float128 x);
extern __float128 ceilq(__float128 x);
extern __float128 truncq(__float128 x);
extern __float128 roundq(__float128 x);
extern __float128 nearbyintq(__float128 x);
extern __float128 fabsq(__float128 x);
extern __float128 copysignq(__float128 x, __float128 y);
extern __float128 fmaq(__float128 x, __float128 y, __float128 z);
extern __float128 fminq(__float128 x, __float128 y);
extern __float128 fmaxq(__float128 x, __float128 y);
extern int isnanq(__float128 x);
extern int isinfq(__float128 x);
extern int finiteq(__float128 x);
extern int signbitq(__float128 x);
extern __float128 scalbnq(__float128 x, int n);
extern int ilogbq(__float128 x);
#endif
/*@@end@@*/
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#if !defined(_WIN32)
#include <unistd.h>
#include <dirent.h>
#include <pwd.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include <sys/random.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#else
#include <io.h>
#include <windows.h>
#endif
#include <signal.h>

/*@@row @prelude.noreturn demand=@noreturn@@*/
/* Z_NORETURN -- a function that never returns. ISO C spells it _Noreturn,
   which gcc, clang and MSVC act on; tcc acts only on its attribute form, and
   without it reports a value-returning function that ends in a call of one. */
#if defined(__TINYC__)
#define Z_NORETURN __attribute__((noreturn))
#else
#define Z_NORETURN _Noreturn
#endif
/*@@end@@*/

/*@@row @prelude.typeof demand=@typeof@@*/
/* Z_TYPEOF -- the type of an expression, which the emitter names only for a
   temporary whose type it has no C text for. ISO C17 has no spelling for it:
   gcc, clang, tcc and MSVC take __typeof__ in C17 mode, and C23 standardises
   it as typeof. */
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 202311L
#define Z_TYPEOF(x) typeof(x)
#else
#define Z_TYPEOF(x) __typeof__(x)
#endif
/*@@end@@*/

/*@@row @prelude.value_typeof demand=@value_typeof@@*/
/* Z_VALUE_TYPEOF -- the unqualified type of an expression's value, for a
   temporary the emitter declares first and assigns after: a `const` source
   must still give an assignable temp. gcc, clang and tcc drop an rvalue's
   qualifiers, which a comma makes of any operand; MSVC keeps them, and takes
   __typeof_unqual__ instead. C23 names it typeof_unqual. */
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 202311L
#define Z_VALUE_TYPEOF(x) typeof_unqual(x)
#elif defined(_MSC_VER) && !defined(__clang__)
#define Z_VALUE_TYPEOF(x) __typeof_unqual__(x)
#else
#define Z_VALUE_TYPEOF(x) __typeof__(((void)0, x))
#endif
/*@@end@@*/

/*@@row @prelude.bits demand=@bits@@*/
/* z_clz64, z_ctz64, z_popcount64 -- the leading zeros, trailing zeros and set
   bits of a 64-bit word. v must not be 0 for the first two, as for the
   builtins they stand for. gcc, clang and tcc take their builtins; MSVC has
   none, and takes the bit-scan intrinsics and a portable count of set bits
   (__popcnt64 needs a processor with POPCNT, which MSVC does not test for). */
#if defined(_MSC_VER) && !defined(__clang__)
#include <intrin.h>
static inline int z_clz64(uint64_t v) {
    unsigned long i;
    _BitScanReverse64(&i, v);
    return 63 - (int)i;
}
static inline int z_ctz64(uint64_t v) {
    unsigned long i;
    _BitScanForward64(&i, v);
    return (int)i;
}
static inline int z_popcount64(uint64_t v) {
    v = v - ((v >> 1) & 0x5555555555555555u);
    v = (v & 0x3333333333333333u) + ((v >> 2) & 0x3333333333333333u);
    v = (v + (v >> 4)) & 0x0F0F0F0F0F0F0F0Fu;
    return (int)((v * 0x0101010101010101u) >> 56);
}
#else
#define z_clz64(v) __builtin_clzll(v)
#define z_ctz64(v) __builtin_ctzll(v)
#define z_popcount64(v) __builtin_popcountll(v)
#endif
/*@@end@@*/

/*@@row @prelude.span64 demand=@span64 needs=@bits@@*/
/* z_span64 -- how many bits lie from the highest set bit of v down to its
   lowest, both counted: the significand an integer needs to convert to a
   float exactly. 0 for 0. */
static inline int z_span64(uint64_t v) { return v == 0 ? 0 : 64 - z_clz64(v) - z_ctz64(v); }
/*@@end@@*/

/*@@row @prelude.write_line demand=@write_line@@*/
#if defined(_WIN32)
/* z_win_console -- a write to a standard stream that is a console goes as
   UTF-16 (WriteConsoleW): the console shows non-ASCII text that way whatever
   its code page, where UTF-8 bytes would be read in that code page. A stream
   that is a pipe or a file gets the bytes as written, by the caller. A UTF-8
   sequence a write splits is held until the stream's next write completes
   it; a byte no sequence explains shows as U+FFFD. */
static int z_win_console_kind[3] = { -1, -1, -1 };
static unsigned char z_win_console_held[3][4];
static int z_win_console_nheld[3];

/* 1 when fd 1 or 2 is a console */
static int z_win_is_console(int fd) {
    if (fd < 1 || fd > 2) return 0;
    if (z_win_console_kind[fd] < 0) {
        DWORD mode;
        HANDLE h = GetStdHandle(fd == 1 ? STD_OUTPUT_HANDLE : STD_ERROR_HANDLE);
        z_win_console_kind[fd] = (h != NULL && h != INVALID_HANDLE_VALUE && GetConsoleMode(h, &mode)) ? 1 : 0;
    }
    return z_win_console_kind[fd];
}

/* the bytes at the end of buf that begin a UTF-8 sequence not yet complete */
static size_t z_win_utf8_tail(const char* buf, size_t n) {
    for (size_t i = 1; i <= 3 && i <= n; i++) {
        unsigned char c = (unsigned char)buf[n - i];
        if ((c & 0xC0) == 0x80) continue;
        if (c < 0xC0) return 0;
        size_t need = c >= 0xF0 ? 4 : (c >= 0xE0 ? 3 : 2);
        return need > i ? i : 0;
    }
    return 0;
}

/* write n bytes, and a line break when `nl`, to console fd as UTF-16; 0 when
   fd is not a console, so the caller writes the bytes itself */
static int z_win_console_write(int fd, const char* s, size_t n, int nl) {
    if (!z_win_is_console(fd)) return 0;
    HANDLE h = GetStdHandle(fd == 1 ? STD_OUTPUT_HANDLE : STD_ERROR_HANDLE);
    char buf[4096 + 8];
    wchar_t wbuf[4096 + 8];
    size_t at = 0;
    do {
        size_t bn = 0;
        for (int k = 0; k < z_win_console_nheld[fd]; k++) buf[bn++] = (char)z_win_console_held[fd][k];
        z_win_console_nheld[fd] = 0;
        size_t take = n - at > 4096 ? 4096 : n - at;
        if (take > 0) memcpy(buf + bn, s + at, take);
        bn += take;
        at += take;
        int last = at == n;
        if (last && nl) buf[bn++] = '\n';
        size_t keep = (last && nl) ? 0 : z_win_utf8_tail(buf, bn);
        if (keep > 0) {
            memcpy(z_win_console_held[fd], buf + bn - keep, keep);
            z_win_console_nheld[fd] = (int)keep;
            bn -= keep;
        }
        if (bn > 0) {
            int wn = MultiByteToWideChar(CP_UTF8, 0, buf, (int)bn, wbuf, 4096 + 8);
            DWORD put = 0;
            if (wn > 0) WriteConsoleW(h, wbuf, (DWORD)wn, &put, NULL);
        }
    } while (at < n);
    return 1;
}
#endif

/* z_write_line -- n bytes of s, and a line break when nl, written to fd in
   one call to the OS: no C stdio buffer holds them, so what print, a stream's
   write and a spawned child put out reaches the OS in the order the program
   ran it. A Windows console takes the bytes as UTF-16 (z_win_console_write). */
static void z_write_line(int fd, const char* s, size_t n, int nl) {
#if defined(_WIN32)
    if (z_win_console_write(fd, s, n, nl)) return;
    HANDLE h = GetStdHandle(fd == 1 ? STD_OUTPUT_HANDLE : STD_ERROR_HANDLE);
    char small[512];
    size_t total = n + (nl ? 1 : 0);
    char* buf = total <= sizeof(small) ? small : (char*)malloc(total);
    if (buf == NULL) return;
    memcpy(buf, s, n);
    if (nl) buf[n] = '\n';
    size_t at = 0;
    while (at < total) {
        DWORD put = 0;
        DWORD ask = total - at > 0x40000000u ? 0x40000000u : (DWORD)(total - at);
        if (!WriteFile(h, buf + at, ask, &put, NULL) || put == 0) break;
        at += put;
    }
    if (buf != small) free(buf);
#else
    struct iovec v[2];
    v[0].iov_base = (void*)s;
    v[0].iov_len = n;
    v[1].iov_base = (void*)"\n";
    v[1].iov_len = nl ? 1 : 0;
    int k = 0;
    while (k < 2) {
        ssize_t w = writev(fd, v + k, 2 - k);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return;
        size_t left = (size_t)w;
        while (k < 2 && left >= v[k].iov_len) {
            left -= v[k].iov_len;
            k++;
        }
        if (k < 2) {
            v[k].iov_base = (char*)v[k].iov_base + left;
            v[k].iov_len -= left;
        }
    }
#endif
}
/*@@end@@*/

