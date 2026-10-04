/* math_kernels.c -- math's word-vector kernels timed alone, one row per kernel
   and vector length, Go's BenchmarkAddVV/words=N shape. math_bench.z reaches
   the kernels only through BigInt, whose result allocation hides them at small
   sizes; this harness calls the fragments' z_mathk_* functions directly on
   vectors allocated once.

   make bench-math builds it once per kernel tier from the fragments
   themselves: _Z_SYSTEM_WORD.inc, the wide multiply cut out of z_hash.inc into
   mul128.h, the generated asm kernels and _Z_MATH_ARITH.inc. A row repeats its kernel until 20 ms have
   passed, R times (-r R, default 5), and prints the median time per call;
   tiers are compared on those medians. */
#define _POSIX_C_SOURCE 199309L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static void z_panic(const char* msg) {
    fprintf(stderr, "zpanic: %s\n", msg);
    exit(1);
}

#include "mul128.h"
#include "_Z_SYSTEM_WORD.inc"
#include "_Z_MATH_ARITH_AMD64.inc"
#include "_Z_MATH_ARITH_ARM64.inc"
#include "_Z_MATH_ARITH.inc"

/* the lengths a row is timed at, Go's series */
static const uint64_t sizes[] = {1, 10, 16, 100, 1000, 10000, 100000};
#define NSIZES (sizeof sizes / sizeof sizes[0])
#define MAXN 100000

static uint64_t* vx;
static uint64_t* vy;
static uint64_t* vz;
/* every kernel's result is folded in, so no call is dead */
static volatile uint64_t sink;

/* the kernels a row can time */
enum kernel { kAddVV, kSubVV, kAddVW, kSubVW, kLshVU, kRshVU, kMulAddVWW, kAddMulVVWW, kCount };
static const char* const names[kCount] = {
    "addVV", "subVV", "addVW", "subVW", "lshVU", "rshVU", "mulAddVWW", "addMulVVWW"};

/* once -- iters calls of kernel k at length n */
static void once(enum kernel k, uint64_t n, uint64_t iters) {
    uint64_t acc = 0;
    for (uint64_t i = 0; i < iters; i++) {
        switch (k) {
        case kAddVV: acc += z_mathk_addVV(vz, vx, vy, n); break;
        case kSubVV: acc += z_mathk_subVV(vz, vx, vy, n); break;
        case kAddVW: acc += z_mathk_addVW(vz, vx, 3, n); break;
        case kSubVW: acc += z_mathk_subVW(vz, vx, 3, n); break;
        case kLshVU: acc += z_mathk_lshVU(vz, vx, 3, n); break;
        case kRshVU: acc += z_mathk_rshVU(vz, vx, 3, n); break;
        case kMulAddVWW: acc += z_mathk_mulAddVWW(vz, vx, vy[0], vy[1], n); break;
        case kAddMulVVWW: acc += z_mathk_addMulVVWW(vz, vx, vy, vy[0], vy[1], n); break;
        default: break;
        }
    }
    sink += acc;
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

/* run -- nanoseconds per call of kernel k at length n, over at least 20 ms */
static double run(enum kernel k, uint64_t n) {
    uint64_t iters = 1;
    for (;;) {
        double start = now();
        once(k, n, iters);
        double elapsed = now() - start;
        if (elapsed >= 0.02) return elapsed * 1e9 / (double)iters;
        iters = elapsed < 0.002 ? iters * 10 : iters * 2;
    }
}

static int cmpd(const void* a, const void* b) {
    double x = *(const double*)a;
    double y = *(const double*)b;
    return (x > y) - (x < y);
}

int main(int argc, char** argv) {
    int reps = 5;
    if (argc == 3 && strcmp(argv[1], "-r") == 0) reps = atoi(argv[2]);
    if (reps < 1 || reps > 101) {
        fprintf(stderr, "usage: math_kernels [-r REPS]\n");
        return 2;
    }
    vx = malloc(MAXN * sizeof(uint64_t));
    vy = malloc(MAXN * sizeof(uint64_t));
    vz = malloc(MAXN * sizeof(uint64_t));
    if (!vx || !vy || !vz) return 1;
    /* the 64-bit LCG Knuth's MMIX uses, as math_bench.z does */
    uint64_t s = 1;
    for (uint64_t i = 0; i < MAXN; i++) {
        s = s * 6364136223846793005ULL + 1442695040888963407ULL;
        vx[i] = s;
        s = s * 6364136223846793005ULL + 1442695040888963407ULL;
        vy[i] = s;
    }
    double* t = malloc((size_t)reps * sizeof(double));
    if (!t) return 1;
    for (int k = 0; k < kCount; k++) {
        for (size_t j = 0; j < NSIZES; j++) {
            for (int r = 0; r < reps; r++) t[r] = run((enum kernel)k, sizes[j]);
            qsort(t, (size_t)reps, sizeof(double), cmpd);
            printf("%s words=%llu %.2f ns/op\n", names[k], (unsigned long long)sizes[j], t[reps / 2]);
        }
    }
    printf("sink %llu\n", (unsigned long long)sink);
    free(t);
    free(vx);
    free(vy);
    free(vz);
    return 0;
}
