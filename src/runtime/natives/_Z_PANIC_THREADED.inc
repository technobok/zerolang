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
static Z_NORETURN void z_panic(const char* msg) {
    z_end_claim();
    z_write_line(2, "zpanic: ", 8, 0);
    z_write_line(2, msg, strlen(msg), 1);
    fflush(NULL);
    _exit(1);
}
/* the program's normal end, once main has returned: it ends the process once,
   as z_panic does, and flushes its output before exit runs. Exit's own flush
   is not enough here: a C runtime may flush only as it unloads, and a thread
   still finishing at that moment can cut that short (wine's does). */
static void z_end(void) {
    z_end_claim();
    fflush(NULL);
}
