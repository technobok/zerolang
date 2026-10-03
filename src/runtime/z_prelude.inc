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

