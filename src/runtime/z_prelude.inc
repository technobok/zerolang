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
/* clang: __float128 without GCC's quadmath.h; declare the two
   libquadmath symbols the runtime uses. */
extern __float128 fmodq(__float128 x, __float128 y);
extern __float128 strtoflt128(const char *s, char **sp);
extern int quadmath_snprintf(char *s, size_t size, const char *format, ...);
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

