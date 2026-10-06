#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>
#if defined(__SIZEOF_FLOAT128__) && !defined(__clang__)
#include <quadmath.h>
#elif defined(__SIZEOF_FLOAT128__)
/* clang: __float128 without GCC's quadmath.h; declare each libquadmath
   symbol the runtime and the f128 rows call. */
extern __float128 fmodq(__float128 x, __float128 y);
extern __float128 strtoflt128(const char *s, char **sp);
extern int quadmath_snprintf(char *s, size_t size, const char *format, ...);
extern __float128 sqrtq(__float128 x);
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
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <pwd.h>
#include <sys/wait.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include <sys/random.h>
#include <sys/syscall.h>

static _Noreturn void z_panic(const char* msg) {
    fprintf(stderr, "zpanic: %s\n", msg);
    exit(1);
}
__attribute__((noreturn)) static void z_unreachable(void) { z_panic("unreachable"); }
static void* z_xmalloc(size_t n) {
    void* p = malloc(n);
    if (!p) z_panic("out of memory");
    return p;
}
static void* z_xcalloc(size_t count, size_t size) {
    void* p = calloc(count, size);
    if (!p) z_panic("out of memory");
    return p;
}
static void* z_xrealloc(void* p, size_t n) {
    void* q = realloc(p, n);
    if (!q) z_panic("out of memory");
    return q;
}


/* i128 / u128: the C compiler's 128-bit integer where it has one, two 64-bit
   words where it has none (tcc) or under -DZ_INT128_PORTABLE. No emitted
   expression applies a C operator to either: every operation is a function in
   natives/_Z_INT128.inc, and a literal is one of the macros below, so zc emits
   the same C for every compiler and this preprocessor test is the only place
   the two representations part. */
#if defined(__SIZEOF_INT128__) && !defined(Z_INT128_PORTABLE)
#define Z_INT128_NATIVE 1
typedef __int128 z_i128;
typedef unsigned __int128 z_u128;
#define Z_U128_C(hi, lo) ((z_u128)(((z_u128)(uint64_t)(hi) << 64) | (z_u128)(uint64_t)(lo)))
#define Z_I128_C(hi, lo) ((z_i128)Z_U128_C(hi, lo))
#define Z_I128_I64(v) ((z_i128)(int64_t)(v))
#define Z_I128_U64(v) ((z_i128)(uint64_t)(v))
#define Z_U128_U64(v) ((z_u128)(uint64_t)(v))
#else
typedef struct { uint64_t lo; uint64_t hi; } z_u128;
typedef struct { uint64_t lo; uint64_t hi; } z_i128;
#define Z_U128_C(hi, lo) ((z_u128){ (uint64_t)(lo), (uint64_t)(hi) })
#define Z_I128_C(hi, lo) ((z_i128){ (uint64_t)(lo), (uint64_t)(hi) })
#define Z_I128_I64(v) ((z_i128){ (uint64_t)(int64_t)(v), (int64_t)(v) < 0 ? ~(uint64_t)0 : 0 })
#define Z_I128_U64(v) ((z_i128){ (uint64_t)(v), 0 })
#define Z_U128_U64(v) ((z_u128){ (uint64_t)(v), 0 })
#endif

/* f16: the C compiler's _Float16 where it has one, its 16 bits where it has
   none (tcc) or under -DZ_F16_PORTABLE. As with i128, every operation is a
   function -- in natives/_Z_F16.inc, computing in float and rounding once --
   and a constant is Z_F16_C of its value and its bits, so zc emits the same C
   for every compiler. */
#if defined(__FLT16_MANT_DIG__) && !defined(Z_F16_PORTABLE)
#define Z_F16_NATIVE 1
typedef _Float16 z_f16;
#define Z_F16_C(lit, bits) ((z_f16)(lit))
#else
typedef struct { uint16_t bits; } z_f16;
#define Z_F16_C(lit, bits) ((z_f16){ (uint16_t)(bits) })
#endif
