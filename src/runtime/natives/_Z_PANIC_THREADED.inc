#include <stdatomic.h>
static _Noreturn void z_panic(const char* msg) {
    /* a program that runs threads: one panic is reported, and the process ends
       without running exit's handlers under the other threads' feet; a second
       panicking thread waits here for the end */
    static atomic_flag z_panicking = ATOMIC_FLAG_INIT;
    if (atomic_flag_test_and_set(&z_panicking)) {
        for (;;) pause();
    }
    fprintf(stderr, "zpanic: %s\n", msg);
    fflush(NULL);
    _exit(1);
}
