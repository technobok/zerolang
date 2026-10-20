/* os.monotonicMillis -- milliseconds on the monotonic clock. Only
   differences are meaningful (the epoch is arbitrary, typically boot).
   Infallible: CLOCK_MONOTONIC cannot fail on a supported platform, nor
   QueryPerformanceCounter on Windows. */
#include <time.h>
static int64_t z_os_monotonicMillis(void);
static int64_t z_os_monotonicMillis(void) {
#if defined(_WIN32)
    LARGE_INTEGER f;
    LARGE_INTEGER c;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (int64_t)(c.QuadPart / f.QuadPart) * 1000 + (int64_t)((c.QuadPart % f.QuadPart) * 1000 / f.QuadPart);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + (int64_t)(ts.tv_nsec / 1000000);
#endif
}
