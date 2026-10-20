/* os.spawn -- start argv as a child (z_os_spawn_start: PATH resolution,
   redirections, cwd) and wait for it. Returns the child's exit status; 128 +
   signo for a child a signal ended; 127 on exec/setup failure; 124 if
   timeoutSecs > 0 and the child outlived it (killed). timeoutSecs <= 0 waits
   forever.

   The timeout waits on the child's own handle -- its pidfd, polled; on
   Windows its process handle -- so it touches no process-wide state (no
   signal handler, no alarm) and another thread's spawn is not disturbed. A
   kernel without pidfd_open (ENOSYS) is polled with WNOHANG every 10ms
   instead. */
#include <time.h>
#if !defined(_WIN32)
static int32_t z_os_spawn_status(int wst) {
    if (WIFEXITED(wst)) return (int32_t)WEXITSTATUS(wst);
    if (WIFSIGNALED(wst)) return 128 + (int32_t)WTERMSIG(wst);
    return 127;
}
#endif
static int32_t z_os_spawn(
    z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath, int32_t timeoutSecs
);
static int32_t z_os_spawn(
    z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath, int32_t timeoutSecs
) {
    int32_t pid = 0;
    int64_t handle = -1;
    if (z_os_spawn_start(argv, cwd, outPath, errPath, &pid, &handle) != 0) return 127;
#if defined(_WIN32)
    HANDLE h = (HANDLE)(intptr_t)handle;
    DWORD budget = INFINITE;
    if (timeoutSecs > 0) budget = timeoutSecs < 4000000 ? (DWORD)timeoutSecs * 1000 : 4000000000u;
    DWORD w = WaitForSingleObject(h, budget);
    int32_t status = 127;
    if (w == WAIT_TIMEOUT) {
        TerminateProcess(h, 137);
        WaitForSingleObject(h, INFINITE);
        status = 124;
    } else {
        DWORD code = 0;
        if (w == WAIT_OBJECT_0 && GetExitCodeProcess(h, &code)) status = (int32_t)code;
    }
    CloseHandle(h);
    return status;
#else
    int pfd = (int)handle;
    int wst = 0;
    int32_t status = 127;
    if (timeoutSecs <= 0) {
        for (;;) {
            if (waitpid(pid, &wst, 0) >= 0) {
                status = z_os_spawn_status(wst);
                break;
            }
            if (errno != EINTR) break;
        }
        if (pfd >= 0) close(pfd);
        return status;
    }
    int64_t budget = (int64_t)timeoutSecs * 1000;
    if (pfd >= 0) {
        struct pollfd p = { pfd, POLLIN, 0 };
        struct timespec t0;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        int ready = 0;
        for (;;) {
            struct timespec t1;
            clock_gettime(CLOCK_MONOTONIC, &t1);
            int64_t spent = (int64_t)(t1.tv_sec - t0.tv_sec) * 1000
                          + (int64_t)(t1.tv_nsec - t0.tv_nsec) / 1000000;
            if (spent >= budget) break;
            int r = poll(&p, 1, (int)(budget - spent));
            if (r > 0) { ready = 1; break; }
            if (r < 0 && errno != EINTR) break;
        }
        close(pfd);
        if (!ready) {
            kill(pid, SIGKILL);
            while (waitpid(pid, &wst, 0) < 0 && errno == EINTR) {}
            return 124;
        }
        while (waitpid(pid, &wst, 0) < 0) {
            if (errno != EINTR) return 127;
        }
        return z_os_spawn_status(wst);
    }
    for (int64_t waited = 0; ; waited += 10) {
        pid_t r = waitpid(pid, &wst, WNOHANG);
        if (r == pid) return z_os_spawn_status(wst);
        if (r < 0 && errno != EINTR) return 127;
        if (waited >= budget) break;
        struct timespec ts = { 0, 10 * 1000000L };
        nanosleep(&ts, NULL);
    }
    kill(pid, SIGKILL);
    while (waitpid(pid, &wst, 0) < 0 && errno == EINTR) {}
    return 124;
#endif
}
