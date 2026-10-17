__attribute__((noreturn)) static void z_unreachable(void) { z_panic("unreachable"); }
static void* z_xmalloc(size_t n) {
    void* p = malloc(n);
    if (!p) z_panic("out of memory");
    return p;
}
static void* z_xcalloc(size_t count, size_t size) {
    void* p = calloc(count, size);
    if (!p) z_panic("out of memory");
    return p;
}
static void* z_xrealloc(void* p, size_t n) {
    void* q = realloc(p, n);
    if (!q) z_panic("out of memory");
    return q;
}


/* i128 / u128: the C compiler's 128-bit integer where it has one, two 64-bit
   words where it has none (tcc) or under -DZ_INT128_PORTABLE. No emitted
   expression applies a C operator to either: every operation is a function in
   natives/_Z_INT128.inc, and a literal is one of the macros below, so zc emits
   the same C for every compiler and this preprocessor test is the only place
   the two representations part. */
#if defined(__SIZEOF_INT128__) && !defined(Z_INT128_PORTABLE)
#define Z_INT128_NATIVE 1
typedef __int128 z_i128;
typedef unsigned __int128 z_u128;
#define Z_U128_C(hi, lo) ((z_u128)(((z_u128)(uint64_t)(hi) << 64) | (z_u128)(uint64_t)(lo)))
#define Z_I128_C(hi, lo) ((z_i128)Z_U128_C(hi, lo))
#define Z_I128_I64(v) ((z_i128)(int64_t)(v))
#define Z_I128_U64(v) ((z_i128)(uint64_t)(v))
#define Z_U128_U64(v) ((z_u128)(uint64_t)(v))
#else
typedef struct { uint64_t lo; uint64_t hi; } z_u128;
typedef struct { uint64_t lo; uint64_t hi; } z_i128;
#define Z_U128_C(hi, lo) ((z_u128){ (uint64_t)(lo), (uint64_t)(hi) })
#define Z_I128_C(hi, lo) ((z_i128){ (uint64_t)(lo), (uint64_t)(hi) })
#define Z_I128_I64(v) ((z_i128){ (uint64_t)(int64_t)(v), (int64_t)(v) < 0 ? ~(uint64_t)0 : 0 })
#define Z_I128_U64(v) ((z_i128){ (uint64_t)(v), 0 })
#define Z_U128_U64(v) ((z_u128){ (uint64_t)(v), 0 })
#endif

/* f16: the C compiler's _Float16 where it has one, its 16 bits where it has
   none (tcc) or under -DZ_F16_PORTABLE. As with i128, every operation is a
   function -- in natives/_Z_F16.inc, computing in float and rounding once --
   and a constant is Z_F16_C of its value and its bits, so zc emits the same C
   for every compiler. */
#if defined(__FLT16_MANT_DIG__) && !defined(Z_F16_PORTABLE)
#define Z_F16_NATIVE 1
typedef _Float16 z_f16;
#define Z_F16_C(lit, bits) ((z_f16)(lit))
#else
typedef struct { uint16_t bits; } z_f16;
#define Z_F16_C(lit, bits) ((z_f16){ (uint16_t)(bits) })
#endif
