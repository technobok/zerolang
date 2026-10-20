#include <stdatomic.h>
#if defined(_WIN32)
#include <windows.h>
#endif
/* a program that runs threads ends once: the first thread to end it -- a
   panic, or os.exit -- flushes and leaves with _exit, so exit's handlers never
   run under the other threads' feet, and any thread ending it meanwhile waits
   here for that end */
static atomic_flag z_ending = ATOMIC_FLAG_INIT;
static void z_end_claim(void) {
    if (atomic_flag_test_and_set(&z_ending)) {
#if defined(_WIN32)
        for (;;) Sleep(INFINITE);
#else
        for (;;) pause();
#endif
    }
}
static _Noreturn void z_panic(const char* msg) {
    z_end_claim();
#if defined(_WIN32)
    if (!z_win_console_write(2, "zpanic: ", 8, 0) || !z_win_console_write(2, msg, strlen(msg), 1))
#endif
    fprintf(stderr, "zpanic: %s\n", msg);
    fflush(NULL);
    _exit(1);
}
