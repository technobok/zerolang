#include <stdatomic.h>
/* a program that runs threads ends once: the first thread to end it -- a
   panic, or os.exit -- flushes and leaves with _exit, so exit's handlers never
   run under the other threads' feet, and any thread ending it meanwhile waits
   here for that end */
static atomic_flag z_ending = ATOMIC_FLAG_INIT;
static void z_end_claim(void) {
    if (atomic_flag_test_and_set(&z_ending)) {
        for (;;) pause();
    }
}
static _Noreturn void z_panic(const char* msg) {
    z_end_claim();
    fprintf(stderr, "zpanic: %s\n", msg);
    fflush(NULL);
    _exit(1);
}
