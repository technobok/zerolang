/* The word primitives with no one-expression C form: a carry chain's step, in
   both directions, and the division of a two-word number by a word. Each takes
   the builtin a C compiler offers -- gcc and clang lower these to adc, sbb and
   div -- and otherwise a portable form, which is what tcc runs. Defining
   Z_WORD_PORTABLE (-DZ_WORD_PORTABLE) takes the portable forms under any
   compiler, which is how make bench-math sets the two tiers side by side. */
#if defined(__has_builtin) && !defined(Z_WORD_PORTABLE)
#if __has_builtin(__builtin_addcll) && __has_builtin(__builtin_subcll)
#define Z_WORD_CARRY_BUILTINS 1
#endif
#endif

/* a + b + cin, and the carry out of it in *cout. cin is 0 or 1. */
static inline uint64_t z_word_addc(uint64_t a, uint64_t b, uint64_t cin, uint64_t* cout) {
#if defined(Z_WORD_CARRY_BUILTINS)
    unsigned long long c;
    uint64_t s = __builtin_addcll(a, b, cin, &c);
    *cout = c;
    return s;
#else
    uint64_t s = a + b;
    uint64_t r = s + cin;
    *cout = (uint64_t)(s < a) | (uint64_t)(r < s);
    return r;
#endif
}

/* a - b - bin, and the borrow out of it in *bout. bin is 0 or 1. */
static inline uint64_t z_word_subb(uint64_t a, uint64_t b, uint64_t bin, uint64_t* bout) {
#if defined(Z_WORD_CARRY_BUILTINS)
    unsigned long long c;
    uint64_t d = __builtin_subcll(a, b, bin, &c);
    *bout = c;
    return d;
#else
    uint64_t d = a - b;
    uint64_t r = d - bin;
    *bout = (uint64_t)(a < b) | (uint64_t)(d < bin);
    return r;
#endif
}

/* (hi, lo) / d, the remainder in *rem. The quotient fits a word only when
   hi < d, and a zero d has none: both panic. The portable form is long
   division in 32-bit digits: d is shifted until its top bit is set, so each
   estimate of a quotient digit from the top digits is at most two too large,
   and each correction step lowers it by one. */
static uint64_t z_word_divwide(uint64_t hi, uint64_t lo, uint64_t d, uint64_t* rem) {
    if (d == 0) z_panic("divWide: division by zero");
    if (hi >= d) z_panic("divWide: the quotient does not fit a word (hi >= by)");
#if defined(__SIZEOF_INT128__) && !defined(Z_WORD_PORTABLE)
    unsigned __int128 n = ((unsigned __int128)hi << 64) | lo;
    *rem = (uint64_t)(n % d);
    return (uint64_t)(n / d);
#else
    const uint64_t b = 4294967296u;
    int s = __builtin_clzll(d);
    uint64_t v = d << s;
    uint64_t vn1 = v >> 32;
    uint64_t vn0 = v & 0xFFFFFFFFu;
    uint64_t un32 = s == 0 ? hi : (hi << s) | (lo >> (64 - s));
    uint64_t un10 = lo << s;
    uint64_t un1 = un10 >> 32;
    uint64_t un0 = un10 & 0xFFFFFFFFu;
    uint64_t q1 = un32 / vn1;
    uint64_t rhat = un32 - q1 * vn1;
    while (q1 >= b || q1 * vn0 > b * rhat + un1) {
        q1 -= 1;
        rhat += vn1;
        if (rhat >= b) break;
    }
    uint64_t un21 = un32 * b + un1 - q1 * v;
    uint64_t q0 = un21 / vn1;
    rhat = un21 - q0 * vn1;
    while (q0 >= b || q0 * vn0 > b * rhat + un0) {
        q0 -= 1;
        rhat += vn1;
        if (rhat >= b) break;
    }
    *rem = (un21 * b + un0 - q0 * v) >> s;
    return q1 * b + q0;
#endif
}
