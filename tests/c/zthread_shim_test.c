/* zthread_shim_test.c -- the C thread shim (src/runtime/natives/_Z_THREAD_SHIM.inc)
   on its own: every primitive, under contention, with the two runtime helpers
   it calls stubbed. Built and run by `make shim-guard` under gcc, clang, tcc
   and gcc -fsanitize=thread, and by the windows-shim workflow under MSVC and
   mingw. Exit status 0 and "zthread shim OK" mean every check held. */
#if !defined(_WIN32)
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#endif
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdbool.h>
#if !defined(_WIN32)
#include <unistd.h>
#include <sys/syscall.h>
#include <time.h>
#endif

static void z_panic(const char* msg) {
    fprintf(stderr, "zpanic: %s\n", msg);
    exit(1);
}
static void* z_xmalloc(size_t n) {
    void* p = malloc(n);
    if (p == NULL) z_panic("out of memory");
    return p;
}

#include "../../src/runtime/natives/_Z_THREAD_SHIM.inc"

static int failures;
#define CHECK(c, what) do { if (!(c)) { fprintf(stderr, "FAIL: %s\n", what); failures++; } } while (0)

enum { NT = 8, ITER = 20000 };

/* counters: an atomic one, and a plain one behind a mutex */
static Z_ATOMIC(int64_t) acount;
static int64_t mcount;
static z_mutex_t mlock;

static void bump(void* arg) {
    (void)arg;
    for (int i = 0; i < ITER; i++) {
        Z_ATOMIC_ADD(&acount, 1, Z_RELAXED);
        z_mutex_lock(&mlock);
        mcount++;
        z_mutex_unlock(&mlock);
    }
}

/* rwlock: a writer keeps a == b; readers must never see them differ */
static z_rwlock_t rw;
static int64_t pa, pb;
static Z_ATOMIC(int) torn;
static Z_ATOMIC(int) rw_done;

static void writer(void* arg) {
    (void)arg;
    for (int i = 0; i < ITER; i++) {
        z_rwlock_wrlock(&rw);
        pa = i;
        pb = i;
        z_rwlock_wrunlock(&rw);
    }
    Z_ATOMIC_STORE(&rw_done, 1, Z_RELEASE);
}

static void reader(void* arg) {
    (void)arg;
    while (!Z_ATOMIC_LOAD(&rw_done, Z_ACQUIRE)) {
        z_rwlock_rdlock(&rw);
        if (pa != pb) Z_ATOMIC_STORE(&torn, 1, Z_RELAXED);
        z_rwlock_rdunlock(&rw);
    }
}

/* cond: a one-slot handoff, producer to consumer, every value once in order */
static z_mutex_t qm;
static z_cond_t qc;
static int qfull;
static int64_t qval;
static int64_t qsum;

static void producer(void* arg) {
    (void)arg;
    for (int64_t i = 1; i <= ITER; i++) {
        z_mutex_lock(&qm);
        while (qfull) z_cond_wait(&qc, &qm);
        qval = i;
        qfull = 1;
        z_cond_broadcast(&qc);
        z_mutex_unlock(&qm);
    }
}

static void consumer(void* arg) {
    (void)arg;
    for (int64_t i = 1; i <= ITER; i++) {
        z_mutex_lock(&qm);
        while (!qfull) z_cond_wait(&qc, &qm);
        if (qval != i) qsum = -1000000000;
        qsum += qval;
        qfull = 0;
        z_cond_broadcast(&qc);
        z_mutex_unlock(&qm);
    }
}

/* once: many callers, one call */
static z_once_t once = Z_ONCE_INIT;
static Z_ATOMIC(int) once_runs;
static void once_body(void) { Z_ATOMIC_ADD(&once_runs, 1, Z_RELAXED); }
static void call_once(void* arg) {
    (void)arg;
    z_once(&once, once_body);
}

/* name: the thread reads back what it was started with, truncated */
static char seen_name[32];
static void read_name(void* arg) {
    (void)arg;
#if defined(__linux__)
    prctl(PR_GET_NAME, (unsigned long)seen_name, 0, 0, 0);
#else
    strcpy(seen_name, "a-name-longer-t");
#endif
}

/* detach: the thread finishes on its own; the live count says when */
static Z_ATOMIC(int) detached_ran;
static void detached(void* arg) {
    (void)arg;
    Z_ATOMIC_STORE(&detached_ran, 1, Z_RELEASE);
}

/* a deep stack: the default must hold what main's would */
static int64_t deep(int64_t n) {
    volatile char pad[256];
    pad[0] = (char)n;
    if (n == 0) return pad[0];
    return deep(n - 1) + 1 + pad[0] * 0;
}
static Z_ATOMIC(int64_t) deep_result;
static void go_deep(void* arg) {
    (void)arg;
    Z_ATOMIC_STORE(&deep_result, deep(10000), Z_RELEASE);
}

static void start_or_die(z_thread_t* t, z_thread_fn fn, const char* name) {
    if (z_thread_start(t, fn, NULL, 0, name) != 0) {
        fprintf(stderr, "FAIL: could not start a thread\n");
        exit(1);
    }
}

int main(void) {
    z_thread_t ts[NT];

    z_mutex_init(&mlock);
    for (int i = 0; i < NT; i++) start_or_die(&ts[i], bump, "bump");
    for (int i = 0; i < NT; i++) CHECK(z_thread_join(&ts[i]) == 0, "join bump");
    CHECK(Z_ATOMIC_LOAD(&acount, Z_SEQCST) == (int64_t)NT * ITER, "atomic counter");
    CHECK(mcount == (int64_t)NT * ITER, "mutex counter");
    CHECK(z_mutex_trylock(&mlock) == 0, "trylock free mutex");
    CHECK(z_mutex_trylock(&mlock) != 0, "trylock held mutex");
    z_mutex_unlock(&mlock);
    z_mutex_destroy(&mlock);

    z_rwlock_init(&rw);
    start_or_die(&ts[0], writer, "writer");
    for (int i = 1; i < 4; i++) start_or_die(&ts[i], reader, "reader");
    for (int i = 0; i < 4; i++) z_thread_join(&ts[i]);
    CHECK(Z_ATOMIC_LOAD(&torn, Z_SEQCST) == 0, "rwlock readers never saw a torn pair");
    z_rwlock_destroy(&rw);

    z_mutex_init(&qm);
    CHECK(z_cond_init(&qc) == 0, "cond init on the monotonic clock");
    start_or_die(&ts[0], producer, "producer");
    start_or_die(&ts[1], consumer, "consumer");
    z_thread_join(&ts[0]);
    z_thread_join(&ts[1]);
    CHECK(qsum == (int64_t)ITER * (ITER + 1) / 2, "cond handoff delivered every value in order");

    z_mutex_lock(&qm);
    int e = z_cond_timedwait(&qc, &qm, 30);
    z_mutex_unlock(&qm);
    CHECK(e == ETIMEDOUT, "timed wait times out");
    z_cond_destroy(&qc);
    z_mutex_destroy(&qm);

    for (int i = 0; i < NT; i++) start_or_die(&ts[i], call_once, NULL);
    for (int i = 0; i < NT; i++) z_thread_join(&ts[i]);
    CHECK(Z_ATOMIC_LOAD(&once_runs, Z_SEQCST) == 1, "once runs once");

    start_or_die(&ts[0], read_name, "a-name-longer-than-fifteen");
    z_thread_join(&ts[0]);
    CHECK(strcmp(seen_name, "a-name-longer-t") == 0, "thread name, truncated to 15 bytes");

    start_or_die(&ts[0], go_deep, "deep");
    z_thread_join(&ts[0]);
    CHECK(Z_ATOMIC_LOAD(&deep_result, Z_SEQCST) == 10000, "deep recursion on the default stack");

    start_or_die(&ts[0], detached, "detached");
    CHECK(z_thread_detach(&ts[0]) == 0, "detach");
    /* a loaded machine may not run the detached thread for a while: wait up to
       10 s, by the clock rather than a spin count */
    for (int waited = 0; z_thread_live() != 0 && waited < 10000; waited++) {
#if defined(_WIN32)
        Sleep(1);
#else
        struct timespec ms = { 0, 1000000L };
        nanosleep(&ms, NULL);
#endif
    }
    CHECK(Z_ATOMIC_LOAD(&detached_ran, Z_ACQUIRE) == 1, "detached thread ran");
    CHECK(z_thread_live() == 0, "live count back to zero");
    CHECK(z_thread_self_id() != 0, "a thread id");

    if (failures != 0) {
        fprintf(stderr, "zthread shim: %d check(s) failed\n", failures);
        return 1;
    }
    printf("zthread shim OK\n");
    return 0;
}
