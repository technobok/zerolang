/* os.sleepMillis -- suspend the calling process for ms milliseconds.
   Restarts after EINTR so the full duration always elapses. */
#include <time.h>
static void z_os_sleepMillis(int64_t ms);
static void z_os_sleepMillis(int64_t ms) {
    if (ms <= 0) return;
    struct timespec req;
    req.tv_sec = (time_t)(ms / 1000);
    req.tv_nsec = (long)(ms % 1000) * 1000000L;
    while (nanosleep(&req, &req) != 0 && errno == EINTR) {
    }
}
