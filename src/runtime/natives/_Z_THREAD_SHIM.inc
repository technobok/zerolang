/* z_thread -- the C layer under the thread and sync units: threads, mutexes,
   reader-writer locks, condition variables, once, and atomics, over pthreads
   or Win32. Loaded only by a program that reaches a row naming
   `needs=prelude.zthread`; nothing else pays for it.

   One API for both backends. Every call answers 0 on success and an errno-
   style nonzero on failure, except where noted; the units above decide what a
   failure means (most are panics: a lock that cannot be taken is a broken
   program, not a recoverable state).

     z_thread_start(t, fn, arg, stack, name)  start fn(arg) on a new thread
     z_thread_join(t) / z_thread_detach(t)    wait for it / let it go
     z_thread_yield() / z_thread_self_id()    give up the CPU / this thread's id
     z_mutex_*, z_rwlock_*, z_cond_*, z_once  the primitives
     z_thread_live()                          threads started and not finished

   `stack` 0 takes Z_THREAD_STACK_DEFAULT (8 MiB, the main thread's usual size,
   so a program that recurses deeply on main recurses as deeply on a thread).
   `name` (may be NULL) is set from inside the new thread, truncated to the 15
   bytes Linux keeps. A condition variable times out on the MONOTONIC clock,
   so a wall-clock change neither shortens nor stretches a wait. */
#ifndef Z_THREAD_SHIM_INCLUDED
#define Z_THREAD_SHIM_INCLUDED

#define Z_THREAD_STACK_DEFAULT ((uint64_t)8 * 1024 * 1024)

/* -- atomics -------------------------------------------------------------- */
/* C11 <stdatomic.h> behind macros, so a backend whose compiler spells them
   differently changes one place. MSVC needs /experimental:c11atomics. */
#include <stdatomic.h>
#define Z_ATOMIC(T) _Atomic(T)
#define Z_ATOMIC_LOAD(p, o) atomic_load_explicit((p), (o))
#define Z_ATOMIC_STORE(p, v, o) atomic_store_explicit((p), (v), (o))
#define Z_ATOMIC_ADD(p, v, o) atomic_fetch_add_explicit((p), (v), (o))
#define Z_ATOMIC_SUB(p, v, o) atomic_fetch_sub_explicit((p), (v), (o))
#define Z_ATOMIC_XCHG(p, v, o) atomic_exchange_explicit((p), (v), (o))
#define Z_ATOMIC_CAS(p, e, v, os, of) atomic_compare_exchange_strong_explicit((p), (e), (v), (os), (of))
#define Z_RELAXED memory_order_relaxed
#define Z_ACQUIRE memory_order_acquire
#define Z_RELEASE memory_order_release
#define Z_ACQREL memory_order_acq_rel
#define Z_SEQCST memory_order_seq_cst
typedef memory_order z_order_t;

/* threads started and not yet finished, the program's own count */
static Z_ATOMIC(int64_t) z_thread_live_count;

static int64_t z_thread_live(void) {
    return Z_ATOMIC_LOAD(&z_thread_live_count, Z_ACQUIRE);
}

/* what a new thread runs first: its name, then the body, then the count */
typedef void (*z_thread_fn)(void* arg);
typedef struct z_thread_start_block {
    z_thread_fn fn;
    void* arg;
    char name[16];
} z_thread_start_block;

#if defined(_WIN32)
/* ---------------------------------------------------------------- Win32 -- */
#include <windows.h>
#include <process.h>

typedef struct { HANDLE h; } z_thread_t;
typedef struct { SRWLOCK l; } z_mutex_t;
typedef struct { SRWLOCK l; } z_rwlock_t;
typedef struct { CONDITION_VARIABLE c; } z_cond_t;
typedef struct { INIT_ONCE o; } z_once_t;
#define Z_ONCE_INIT { INIT_ONCE_STATIC_INIT }

typedef HRESULT (WINAPI *z_set_desc_fn)(HANDLE, PCWSTR);

/* SetThreadDescription exists from Windows 10 1607; asked for by name so an
   older system runs the thread unnamed rather than failing to load */
static void z_thread_set_name(const char* name) {
    if (name[0] == 0) return;
    HMODULE k = GetModuleHandleW(L"kernel32.dll");
    if (k == NULL) return;
    z_set_desc_fn f = (z_set_desc_fn)(void*)GetProcAddress(k, "SetThreadDescription");
    if (f == NULL) return;
    wchar_t w[16];
    int i = 0;
    for (; i < 15 && name[i] != 0; i++) w[i] = (wchar_t)(unsigned char)name[i];
    w[i] = 0;
    f(GetCurrentThread(), w);
}

static unsigned __stdcall z_thread_trampoline(void* p) {
    z_thread_start_block b = *(z_thread_start_block*)p;
    free(p);
    z_thread_set_name(b.name);
    b.fn(b.arg);
    Z_ATOMIC_SUB(&z_thread_live_count, 1, Z_RELEASE);
    return 0;
}

static int z_thread_start(z_thread_t* t, z_thread_fn fn, void* arg, uint64_t stack, const char* name) {
    z_thread_start_block* b = (z_thread_start_block*)z_xmalloc(sizeof(z_thread_start_block));
    b->fn = fn;
    b->arg = arg;
    b->name[0] = 0;
    if (name != NULL) {
        size_t n = strlen(name);
        if (n > 15) n = 15;
        memcpy(b->name, name, n);
        b->name[n] = 0;
    }
    if (stack == 0) stack = Z_THREAD_STACK_DEFAULT;
    Z_ATOMIC_ADD(&z_thread_live_count, 1, Z_ACQREL);
    uintptr_t h = _beginthreadex(NULL, (unsigned)stack, z_thread_trampoline, b,
                                 STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
    if (h == 0) {
        Z_ATOMIC_SUB(&z_thread_live_count, 1, Z_RELEASE);
        free(b);
        return errno != 0 ? errno : EAGAIN;
    }
    t->h = (HANDLE)h;
    return 0;
}

static int z_thread_join(z_thread_t* t) {
    if (WaitForSingleObject(t->h, INFINITE) != WAIT_OBJECT_0) return EINVAL;
    CloseHandle(t->h);
    return 0;
}

static int z_thread_detach(z_thread_t* t) {
    return CloseHandle(t->h) ? 0 : EINVAL;
}

static void z_thread_yield(void) { SwitchToThread(); }
static uint64_t z_thread_self_id(void) { return (uint64_t)GetCurrentThreadId(); }

static int z_mutex_init(z_mutex_t* m) { InitializeSRWLock(&m->l); return 0; }
static int z_mutex_destroy(z_mutex_t* m) { (void)m; return 0; }
static int z_mutex_lock(z_mutex_t* m) { AcquireSRWLockExclusive(&m->l); return 0; }
static int z_mutex_trylock(z_mutex_t* m) { return TryAcquireSRWLockExclusive(&m->l) ? 0 : EBUSY; }
static int z_mutex_unlock(z_mutex_t* m) { ReleaseSRWLockExclusive(&m->l); return 0; }

static int z_rwlock_init(z_rwlock_t* l) { InitializeSRWLock(&l->l); return 0; }
static int z_rwlock_destroy(z_rwlock_t* l) { (void)l; return 0; }
static int z_rwlock_rdlock(z_rwlock_t* l) { AcquireSRWLockShared(&l->l); return 0; }
static int z_rwlock_wrlock(z_rwlock_t* l) { AcquireSRWLockExclusive(&l->l); return 0; }
static int z_rwlock_rdunlock(z_rwlock_t* l) { ReleaseSRWLockShared(&l->l); return 0; }
static int z_rwlock_wrunlock(z_rwlock_t* l) { ReleaseSRWLockExclusive(&l->l); return 0; }

static int z_cond_init(z_cond_t* c) { InitializeConditionVariable(&c->c); return 0; }
static int z_cond_destroy(z_cond_t* c) { (void)c; return 0; }
static int z_cond_wait(z_cond_t* c, z_mutex_t* m) {
    return SleepConditionVariableSRW(&c->c, &m->l, INFINITE, 0) ? 0 : EINVAL;
}
/* 0 when signalled, ETIMEDOUT when `ms` passed first (the caller re-checks its
   condition either way: a wake may be spurious) */
static int z_cond_timedwait(z_cond_t* c, z_mutex_t* m, uint64_t ms) {
    DWORD d = ms >= (uint64_t)INFINITE ? INFINITE - 1 : (DWORD)ms;
    if (SleepConditionVariableSRW(&c->c, &m->l, d, 0)) return 0;
    return GetLastError() == ERROR_TIMEOUT ? ETIMEDOUT : EINVAL;
}
static int z_cond_signal(z_cond_t* c) { WakeConditionVariable(&c->c); return 0; }
static int z_cond_broadcast(z_cond_t* c) { WakeAllConditionVariable(&c->c); return 0; }

static BOOL CALLBACK z_once_call(PINIT_ONCE o, PVOID p, PVOID* ctx) {
    (void)o;
    (void)ctx;
    void (*f)(void) = *(void (**)(void))p;
    f();
    return TRUE;
}
static int z_once(z_once_t* o, void (*fn)(void)) {
    return InitOnceExecuteOnce(&o->o, z_once_call, (PVOID)&fn, NULL) ? 0 : EINVAL;
}

#else
/* ------------------------------------------------------------- pthreads -- */
#include <pthread.h>
#include <sched.h>
#include <time.h>
#if defined(__linux__)
#include <sys/prctl.h>
#endif

typedef struct { pthread_t h; } z_thread_t;
typedef struct { pthread_mutex_t m; } z_mutex_t;
typedef struct { pthread_rwlock_t l; } z_rwlock_t;
typedef struct { pthread_cond_t c; } z_cond_t;
typedef struct { pthread_once_t o; } z_once_t;
#define Z_ONCE_INIT { PTHREAD_ONCE_INIT }

static void z_thread_set_name(const char* name) {
    if (name[0] == 0) return;
#if defined(__linux__)
    prctl(PR_SET_NAME, (unsigned long)name, 0, 0, 0);
#endif
}

static void* z_thread_trampoline(void* p) {
    z_thread_start_block b = *(z_thread_start_block*)p;
    free(p);
    z_thread_set_name(b.name);
    b.fn(b.arg);
    Z_ATOMIC_SUB(&z_thread_live_count, 1, Z_RELEASE);
    return NULL;
}

static int z_thread_start(z_thread_t* t, z_thread_fn fn, void* arg, uint64_t stack, const char* name) {
    z_thread_start_block* b = (z_thread_start_block*)z_xmalloc(sizeof(z_thread_start_block));
    b->fn = fn;
    b->arg = arg;
    b->name[0] = 0;
    if (name != NULL) {
        size_t n = strlen(name);
        if (n > 15) n = 15;
        memcpy(b->name, name, n);
        b->name[n] = 0;
    }
    if (stack == 0) stack = Z_THREAD_STACK_DEFAULT;
    pthread_attr_t a;
    int e = pthread_attr_init(&a);
    if (e != 0) {
        free(b);
        return e;
    }
    e = pthread_attr_setstacksize(&a, (size_t)stack);
    if (e == 0) {
        Z_ATOMIC_ADD(&z_thread_live_count, 1, Z_ACQREL);
        e = pthread_create(&t->h, &a, z_thread_trampoline, b);
        if (e != 0) Z_ATOMIC_SUB(&z_thread_live_count, 1, Z_RELEASE);
    }
    pthread_attr_destroy(&a);
    if (e != 0) free(b);
    return e;
}

static int z_thread_join(z_thread_t* t) { return pthread_join(t->h, NULL); }
static int z_thread_detach(z_thread_t* t) { return pthread_detach(t->h); }
static void z_thread_yield(void) { sched_yield(); }
static uint64_t z_thread_self_id(void) {
#if defined(__linux__) && defined(SYS_gettid)
    return (uint64_t)syscall(SYS_gettid);
#else
    return (uint64_t)(uintptr_t)pthread_self();
#endif
}

static int z_mutex_init(z_mutex_t* m) { return pthread_mutex_init(&m->m, NULL); }
static int z_mutex_destroy(z_mutex_t* m) { return pthread_mutex_destroy(&m->m); }
static int z_mutex_lock(z_mutex_t* m) { return pthread_mutex_lock(&m->m); }
static int z_mutex_trylock(z_mutex_t* m) { return pthread_mutex_trylock(&m->m); }
static int z_mutex_unlock(z_mutex_t* m) { return pthread_mutex_unlock(&m->m); }

static int z_rwlock_init(z_rwlock_t* l) { return pthread_rwlock_init(&l->l, NULL); }
static int z_rwlock_destroy(z_rwlock_t* l) { return pthread_rwlock_destroy(&l->l); }
static int z_rwlock_rdlock(z_rwlock_t* l) { return pthread_rwlock_rdlock(&l->l); }
static int z_rwlock_wrlock(z_rwlock_t* l) { return pthread_rwlock_wrlock(&l->l); }
static int z_rwlock_rdunlock(z_rwlock_t* l) { return pthread_rwlock_unlock(&l->l); }
static int z_rwlock_wrunlock(z_rwlock_t* l) { return pthread_rwlock_unlock(&l->l); }

static int z_cond_init(z_cond_t* c) {
    pthread_condattr_t a;
    int e = pthread_condattr_init(&a);
    if (e != 0) return e;
    e = pthread_condattr_setclock(&a, CLOCK_MONOTONIC);
    if (e == 0) e = pthread_cond_init(&c->c, &a);
    pthread_condattr_destroy(&a);
    return e;
}
static int z_cond_destroy(z_cond_t* c) { return pthread_cond_destroy(&c->c); }
static int z_cond_wait(z_cond_t* c, z_mutex_t* m) { return pthread_cond_wait(&c->c, &m->m); }
/* 0 when signalled, ETIMEDOUT when `ms` passed first (the caller re-checks its
   condition either way: a wake may be spurious) */
static int z_cond_timedwait(z_cond_t* c, z_mutex_t* m, uint64_t ms) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    uint64_t ns = (uint64_t)ts.tv_nsec + (ms % 1000) * 1000000;
    ts.tv_sec += (time_t)(ms / 1000 + ns / 1000000000);
    ts.tv_nsec = (long)(ns % 1000000000);
    return pthread_cond_timedwait(&c->c, &m->m, &ts);
}
static int z_cond_signal(z_cond_t* c) { return pthread_cond_signal(&c->c); }
static int z_cond_broadcast(z_cond_t* c) { return pthread_cond_broadcast(&c->c); }

static int z_once(z_once_t* o, void (*fn)(void)) { return pthread_once(&o->o, fn); }

#endif
#endif
