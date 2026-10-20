/* os.sleepMillis -- suspend the calling thread for ms milliseconds.
   Restarts after EINTR so the full duration always elapses; Windows'
   Sleep is not interrupted. */
#include <time.h>
static void z_os_sleepMillis(int64_t ms);
static void z_os_sleepMillis(int64_t ms) {
    if (ms <= 0) return;
#if defined(_WIN32)
    while (ms > 0) {
        DWORD part = ms > 0x7fffffff ? 0x7fffffff : (DWORD)ms;
        Sleep(part);
        ms -= (int64_t)part;
    }
#else
    struct timespec req;
    req.tv_sec = (time_t)(ms / 1000);
    req.tv_nsec = (long)(ms % 1000) * 1000000L;
    while (nanosleep(&req, &req) != 0 && errno == EINTR) {
    }
#endif
}
