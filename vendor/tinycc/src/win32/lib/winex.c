/* ------------------------------------------------------------------------- */
/* winex.c : extra stuff */

#if __aarch64__
#include <stdlib.h>
/* replaces environ from arm64-msvcrt.dll which does not exist */
char **_environ;
wchar_t **_wenviron;
/* those do exist but have problems */
int __argc;
char **__argv;
wchar_t **__wargv;
#endif

#if __aarch64__ || __x86_64__
/* MSVC x64 intrinsic */
void __faststorefence(void)
{
#if __aarch64__
    /* ARM64: Data Memory Barrier (Inner Shareable) */
    __asm__("dmb ish");
#elif __x86_64__
    /* x86-64: lock prefix to flush store buffer */
    __asm__("lock; orl $0,(%%rsp)" ::: "memory");
#endif
}
#endif

#if __x86_64__
/* zerolang local patch: the x64 128-bit multiply and shift intrinsics
   winapi/winnt.h declares (__umulh, __mulh, _umul128, _mul128,
   __shiftleft128, __shiftright128), which nothing defined: a program using
   one -- SQLite on _WIN64 -- failed to link. Plain C, in 32-bit halves. */
typedef unsigned long long z_u64;
z_u64 __umulh(z_u64 a, z_u64 b)
{
    z_u64 a_lo = (unsigned)a, a_hi = a >> 32, b_lo = (unsigned)b, b_hi = b >> 32;
    z_u64 p0 = a_lo * b_lo, p1 = a_lo * b_hi, p2 = a_hi * b_lo, p3 = a_hi * b_hi;
    z_u64 mid = (p0 >> 32) + (unsigned)p1 + (unsigned)p2;
    return p3 + (p1 >> 32) + (p2 >> 32) + (mid >> 32);
}
long long __mulh(long long a, long long b)
{
    z_u64 h = __umulh((z_u64)a, (z_u64)b);
    if (a < 0) h -= (z_u64)b;
    if (b < 0) h -= (z_u64)a;
    return (long long)h;
}
z_u64 _umul128(z_u64 a, z_u64 b, z_u64 *hi)
{
    *hi = __umulh(a, b);
    return a * b;
}
long long _mul128(long long a, long long b, long long *hi)
{
    *hi = __mulh(a, b);
    return (long long)((z_u64)a * (z_u64)b);
}
z_u64 __shiftleft128(z_u64 lo, z_u64 hi, unsigned char s)
{
    s &= 63;
    return s ? (hi << s) | (lo >> (64 - s)) : hi;
}
z_u64 __shiftright128(z_u64 lo, z_u64 hi, unsigned char s)
{
    s &= 63;
    return s ? (lo >> s) | (hi << (64 - s)) : lo;
}
#endif
