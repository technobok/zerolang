/* cdead_probe.c -- C with dead items of every kind, on purpose. `make
   cdead-guard` requires its reader to report exactly the items named
   cdead_dead_*, and `make cc-unused-guard` requires gcc and clang to report
   cdead_dead_fn, cdead_dead_str and cdead_dead_word_fn (and not the unused
   local), before either trusts a count over the emitted C: a reader or a
   compiler that stops seeing dead C cannot pass.
   Everything named cdead_live_* is reached from main, some of it only through
   a path a rough reader misses. */
/* configures a system header: live although the C never names it */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>

/* tested by an #if: live */
#define CDEAD_LIVE_SWITCH 1
#define cdead_dead_macro(x) ((x) + 1)

typedef struct cdead_live_t {
    int n;
} cdead_live_t;

typedef struct cdead_dead_t {
    int n;
} cdead_dead_t;

/* a function-pointer FIELD names no top-level item */
typedef struct {
    void* data;
    void (*destroy)(void*);
} cdead_dead_proto_t;

typedef struct {
    void (*call)(void*);
} cdead_dead_vtable_t;

/* live through its enumerators alone: nothing spells the typedef */
typedef enum {
    CDEAD_LIVE_ARM_0,
    CDEAD_LIVE_ARM_1
} cdead_live_tag_t;

/* an anonymous enum, live through its first enumerator alone: the enum is
   one item, whichever of its constants C spells */
enum {
    CDEAD_LIVE_ANON_0,
    CDEAD_LIVE_ANON_1
};

typedef void (*cdead_dead_fn_ft)(int);

static const char cdead_dead_str[] = "unused";

static int cdead_live_count;

/* called only by a dead function: no compiler reports it */
static void cdead_dead_leaf(void) {
}

static void cdead_dead_fn(void) {
    cdead_dead_leaf();
}

#if defined(Z_WORD_PORTABLE)
/* defined in one configuration only, and called in none */
static void cdead_dead_word_fn(void) {
}
#endif

/* static inline: gcc never reports it, clang does */
static inline int cdead_dead_inline(int x) {
    return x;
}

/* `__extension__` ahead of a typedef: still a type, named by its last word */
__extension__ typedef struct {
    int n;
} cdead_dead_ext_t;

static int cdead_live_add(cdead_live_t v);

#if defined(Z_INT128_PORTABLE)
/* reached only where the portable integers meet a compiler with __float128,
   a configuration of its own */
static int cdead_live_portable(void) {
    return 3;
}
#endif

#if defined(_WIN32)
/* reached from the Windows branch only */
static int cdead_live_win(void) {
    return 2;
}
#endif

static int cdead_live_add(cdead_live_t v) {
#if defined(_WIN32)
    return v.n + cdead_live_win();
#elif defined(Z_INT128_PORTABLE) && defined(__SIZEOF_FLOAT128__)
    return v.n + cdead_live_portable();
#else
    return v.n + CDEAD_LIVE_ARM_1;
#endif
}

int main(void) {
    /* a local nothing reads: no file-scope item, so not counted */
    int cdead_unused_local = 0;
#if CDEAD_LIVE_SWITCH
    cdead_live_t v = {1};
    cdead_live_count = cdead_live_add(v);
#endif
    printf("%d\n", cdead_live_count + CDEAD_LIVE_ANON_0);
    return 0;
}
