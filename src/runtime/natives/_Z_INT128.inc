/* i128 and u128: every operation the natives.tbl rows and the emitter need, as
   a function over z_i128 / z_u128 (z_prelude.inc). Where the C compiler has a
   128-bit integer type each one is the operator it names; where it has none
   (tcc), or under -DZ_INT128_PORTABLE, the values are two 64-bit words and each
   function computes on them. The emitted C is the same for every compiler: it
   is z_prelude.inc's preprocessor test that decides, not zc. Loaded only for
   a program that operates on either type. */
#if defined(Z_INT128_NATIVE)

static inline uint64_t z_u128_lo(z_u128 x) { return (uint64_t)x; }
static inline uint64_t z_u128_hi(z_u128 x) { return (uint64_t)(x >> 64); }
static inline z_u128 z_i128_bits(z_i128 x) { return (z_u128)x; }
static inline z_i128 z_u128_signed(z_u128 x) { return (z_i128)x; }
static inline z_i128 z_i128_from_i64(int64_t v) { return (z_i128)v; }
static inline z_i128 z_i128_from_u64(uint64_t v) { return (z_i128)v; }
static inline z_u128 z_u128_from_u64(uint64_t v) { return (z_u128)v; }
static inline int64_t z_i128_to_i64(z_i128 x) { return (int64_t)x; }
static inline uint64_t z_u128_to_u64(z_u128 x) { return (uint64_t)x; }

static inline z_u128 z_u128_add(z_u128 a, z_u128 b) { return a + b; }
static inline z_u128 z_u128_sub(z_u128 a, z_u128 b) { return a - b; }
static inline z_u128 z_u128_mul(z_u128 a, z_u128 b) { return a * b; }
static inline z_u128 z_u128_div(z_u128 a, z_u128 b) { return a / b; }
static inline z_u128 z_u128_rem(z_u128 a, z_u128 b) { return a % b; }
static inline z_u128 z_u128_and(z_u128 a, z_u128 b) { return a & b; }
static inline z_u128 z_u128_or(z_u128 a, z_u128 b) { return a | b; }
static inline z_u128 z_u128_xor(z_u128 a, z_u128 b) { return a ^ b; }
static inline z_u128 z_u128_not(z_u128 a) { return ~a; }
static inline z_u128 z_u128_shl(z_u128 a, unsigned n) { return a << n; }
static inline z_u128 z_u128_shr(z_u128 a, unsigned n) { return a >> n; }
static inline bool z_u128_eq(z_u128 a, z_u128 b) { return a == b; }
static inline bool z_u128_lt(z_u128 a, z_u128 b) { return a < b; }

static inline z_i128 z_i128_add(z_i128 a, z_i128 b) { return (z_i128)((z_u128)a + (z_u128)b); }
static inline z_i128 z_i128_sub(z_i128 a, z_i128 b) { return (z_i128)((z_u128)a - (z_u128)b); }
static inline z_i128 z_i128_mul(z_i128 a, z_i128 b) { return (z_i128)((z_u128)a * (z_u128)b); }
static inline z_i128 z_i128_div(z_i128 a, z_i128 b) { return a / b; }
static inline z_i128 z_i128_rem(z_i128 a, z_i128 b) { return a % b; }
static inline z_i128 z_i128_and(z_i128 a, z_i128 b) { return a & b; }
static inline z_i128 z_i128_or(z_i128 a, z_i128 b) { return a | b; }
static inline z_i128 z_i128_xor(z_i128 a, z_i128 b) { return a ^ b; }
static inline z_i128 z_i128_not(z_i128 a) { return ~a; }
static inline z_i128 z_i128_shl(z_i128 a, unsigned n) { return (z_i128)((z_u128)a << n); }
static inline z_i128 z_i128_shr(z_i128 a, unsigned n) { return a >> n; }
static inline bool z_i128_eq(z_i128 a, z_i128 b) { return a == b; }
static inline bool z_i128_lt(z_i128 a, z_i128 b) { return a < b; }

static inline double z_u128_to_f64(z_u128 x) { return (double)x; }
static inline double z_i128_to_f64(z_i128 x) { return (double)x; }
static inline z_u128 z_u128_from_f64(double v) { return (z_u128)v; }
static inline z_i128 z_i128_from_f64(double v) { return (z_i128)v; }
#if defined(__SIZEOF_FLOAT128__)
static inline __float128 z_u128_to_f128(z_u128 x) { return (__float128)x; }
static inline __float128 z_i128_to_f128(z_i128 x) { return (__float128)x; }
static inline z_u128 z_u128_from_f128(__float128 v) { return (z_u128)v; }
static inline z_i128 z_i128_from_f128(__float128 v) { return (z_i128)v; }
#endif

#else

static inline uint64_t z_u128_lo(z_u128 x) { return x.lo; }
static inline uint64_t z_u128_hi(z_u128 x) { return x.hi; }
static inline z_u128 z_i128_bits(z_i128 x) { z_u128 r = { x.lo, x.hi }; return r; }
static inline z_i128 z_u128_signed(z_u128 x) { z_i128 r = { x.lo, x.hi }; return r; }
static inline z_i128 z_i128_from_i64(int64_t v) {
    z_i128 r = { (uint64_t)v, v < 0 ? ~(uint64_t)0 : 0 };
    return r;
}
static inline z_i128 z_i128_from_u64(uint64_t v) { z_i128 r = { v, 0 }; return r; }
static inline z_u128 z_u128_from_u64(uint64_t v) { z_u128 r = { v, 0 }; return r; }
static inline int64_t z_i128_to_i64(z_i128 x) { return (int64_t)x.lo; }
static inline uint64_t z_u128_to_u64(z_u128 x) { return x.lo; }

static inline z_u128 z_u128_add(z_u128 a, z_u128 b) {
    z_u128 r;
    r.lo = a.lo + b.lo;
    r.hi = a.hi + b.hi + (r.lo < a.lo);
    return r;
}
static inline z_u128 z_u128_sub(z_u128 a, z_u128 b) {
    z_u128 r;
    r.lo = a.lo - b.lo;
    r.hi = a.hi - b.hi - (a.lo < b.lo);
    return r;
}
/* the full 128-bit product of two words, in four 32-bit partial products */
static inline z_u128 z_u128_mul64(uint64_t a, uint64_t b) {
    uint64_t al = a & 0xFFFFFFFFu, ah = a >> 32;
    uint64_t bl = b & 0xFFFFFFFFu, bh = b >> 32;
    uint64_t ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    uint64_t mid = (ll >> 32) + (lh & 0xFFFFFFFFu) + (hl & 0xFFFFFFFFu);
    z_u128 r;
    r.lo = (mid << 32) | (ll & 0xFFFFFFFFu);
    r.hi = hh + (lh >> 32) + (hl >> 32) + (mid >> 32);
    return r;
}
static inline z_u128 z_u128_mul(z_u128 a, z_u128 b) {
    z_u128 r = z_u128_mul64(a.lo, b.lo);
    r.hi += a.lo * b.hi + a.hi * b.lo;
    return r;
}
static inline z_u128 z_u128_and(z_u128 a, z_u128 b) { z_u128 r = { a.lo & b.lo, a.hi & b.hi }; return r; }
static inline z_u128 z_u128_or(z_u128 a, z_u128 b) { z_u128 r = { a.lo | b.lo, a.hi | b.hi }; return r; }
static inline z_u128 z_u128_xor(z_u128 a, z_u128 b) { z_u128 r = { a.lo ^ b.lo, a.hi ^ b.hi }; return r; }
static inline z_u128 z_u128_not(z_u128 a) { z_u128 r = { ~a.lo, ~a.hi }; return r; }
/* n < 128, as C's shift requires of the native type */
static inline z_u128 z_u128_shl(z_u128 a, unsigned n) {
    z_u128 r;
    if (n == 0) return a;
    if (n >= 64) { r.hi = a.lo << (n - 64); r.lo = 0; return r; }
    r.hi = (a.hi << n) | (a.lo >> (64 - n));
    r.lo = a.lo << n;
    return r;
}
static inline z_u128 z_u128_shr(z_u128 a, unsigned n) {
    z_u128 r;
    if (n == 0) return a;
    if (n >= 64) { r.lo = a.hi >> (n - 64); r.hi = 0; return r; }
    r.lo = (a.lo >> n) | (a.hi << (64 - n));
    r.hi = a.hi >> n;
    return r;
}
static inline bool z_u128_eq(z_u128 a, z_u128 b) { return a.lo == b.lo && a.hi == b.hi; }
static inline bool z_u128_lt(z_u128 a, z_u128 b) { return a.hi < b.hi || (a.hi == b.hi && a.lo < b.lo); }
static inline int z_u128_clz(z_u128 a) {
    if (a.hi) return __builtin_clzll(a.hi);
    if (a.lo) return 64 + __builtin_clzll(a.lo);
    return 128;
}
/* a / b and a % b by binary long division: b is aligned under a's top bit
   and subtracted back down, one quotient bit per step. A zero divisor
   panics, where the native type traps. */
static z_u128 z_u128_divmod(z_u128 a, z_u128 b, z_u128* rem) {
    z_u128 q = { 0, 0 };
    if (b.lo == 0 && b.hi == 0) z_panic("integer division by zero");
    if (z_u128_lt(a, b)) { *rem = a; return q; }
    int shift = z_u128_clz(b) - z_u128_clz(a);
    b = z_u128_shl(b, (unsigned)shift);
    for (int i = 0; i <= shift; i++) {
        q = z_u128_shl(q, 1);
        if (!z_u128_lt(a, b)) { a = z_u128_sub(a, b); q.lo |= 1; }
        b = z_u128_shr(b, 1);
    }
    *rem = a;
    return q;
}
static inline z_u128 z_u128_div(z_u128 a, z_u128 b) { z_u128 r; return z_u128_divmod(a, b, &r); }
static inline z_u128 z_u128_rem(z_u128 a, z_u128 b) { z_u128 r; z_u128_divmod(a, b, &r); return r; }

static inline bool z_i128_neg(z_i128 a) { return (a.hi >> 63) != 0; }
static inline z_u128 z_u128_negate(z_u128 a) { return z_u128_add(z_u128_not(a), z_u128_from_u64(1)); }
static inline z_i128 z_i128_add(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_add(z_i128_bits(a), z_i128_bits(b))); }
static inline z_i128 z_i128_sub(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_sub(z_i128_bits(a), z_i128_bits(b))); }
static inline z_i128 z_i128_mul(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_mul(z_i128_bits(a), z_i128_bits(b))); }
/* truncating division, as C's: the quotient's sign is the operands' signs
   combined, the remainder's is the dividend's. The minimum over -1 wraps. */
static inline z_i128 z_i128_div(z_i128 a, z_i128 b) {
    z_u128 ma = z_i128_neg(a) ? z_u128_negate(z_i128_bits(a)) : z_i128_bits(a);
    z_u128 mb = z_i128_neg(b) ? z_u128_negate(z_i128_bits(b)) : z_i128_bits(b);
    z_u128 q = z_u128_div(ma, mb);
    return z_u128_signed(z_i128_neg(a) != z_i128_neg(b) ? z_u128_negate(q) : q);
}
static inline z_i128 z_i128_rem(z_i128 a, z_i128 b) {
    z_u128 ma = z_i128_neg(a) ? z_u128_negate(z_i128_bits(a)) : z_i128_bits(a);
    z_u128 mb = z_i128_neg(b) ? z_u128_negate(z_i128_bits(b)) : z_i128_bits(b);
    z_u128 r = z_u128_rem(ma, mb);
    return z_u128_signed(z_i128_neg(a) ? z_u128_negate(r) : r);
}
static inline z_i128 z_i128_and(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_and(z_i128_bits(a), z_i128_bits(b))); }
static inline z_i128 z_i128_or(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_or(z_i128_bits(a), z_i128_bits(b))); }
static inline z_i128 z_i128_xor(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_xor(z_i128_bits(a), z_i128_bits(b))); }
static inline z_i128 z_i128_not(z_i128 a) { return z_u128_signed(z_u128_not(z_i128_bits(a))); }
static inline z_i128 z_i128_shl(z_i128 a, unsigned n) { return z_u128_signed(z_u128_shl(z_i128_bits(a), n)); }
/* arithmetic: the sign fills the vacated high bits */
static inline z_i128 z_i128_shr(z_i128 a, unsigned n) {
    z_u128 r = z_u128_shr(z_i128_bits(a), n);
    if (z_i128_neg(a) && n > 0) r = z_u128_or(r, z_u128_not(z_u128_shr(z_u128_not(z_u128_from_u64(0)), n)));
    return z_u128_signed(r);
}
static inline bool z_i128_eq(z_i128 a, z_i128 b) { return a.lo == b.lo && a.hi == b.hi; }
static inline bool z_i128_lt(z_i128 a, z_i128 b) {
    if (a.hi != b.hi) return (int64_t)a.hi < (int64_t)b.hi;
    return a.lo < b.lo;
}

/* to a float: each word is exact in the destination whenever the whole value
   is, which is the only case a checked conversion answers ok */
static inline double z_u128_to_f64(z_u128 x) { return (double)x.hi * 0x1p64 + (double)x.lo; }
static inline double z_i128_to_f64(z_i128 x) {
    if (z_i128_neg(x)) return -z_u128_to_f64(z_u128_negate(z_i128_bits(x)));
    return z_u128_to_f64(z_i128_bits(x));
}
/* from a float already known to be in range: truncated toward zero */
static inline z_u128 z_u128_from_f64(double v) {
    z_u128 r;
    if (v < 0x1p64) { r.lo = (uint64_t)v; r.hi = 0; return r; }
    r.hi = (uint64_t)(v / 0x1p64);
    r.lo = (uint64_t)(v - (double)r.hi * 0x1p64);
    return r;
}
static inline z_i128 z_i128_from_f64(double v) {
    if (v < 0) return z_u128_signed(z_u128_negate(z_u128_from_f64(-v)));
    return z_u128_signed(z_u128_from_f64(v));
}
#if defined(__SIZEOF_FLOAT128__)
static inline __float128 z_u128_to_f128(z_u128 x) {
    return (__float128)x.hi * (__float128)0x1p64 + (__float128)x.lo;
}
static inline __float128 z_i128_to_f128(z_i128 x) {
    if (z_i128_neg(x)) return -z_u128_to_f128(z_u128_negate(z_i128_bits(x)));
    return z_u128_to_f128(z_i128_bits(x));
}
static inline z_u128 z_u128_from_f128(__float128 v) {
    z_u128 r;
    if (v < (__float128)0x1p64) { r.lo = (uint64_t)v; r.hi = 0; return r; }
    r.hi = (uint64_t)(v / (__float128)0x1p64);
    r.lo = (uint64_t)(v - (__float128)r.hi * (__float128)0x1p64);
    return r;
}
static inline z_i128 z_i128_from_f128(__float128 v) {
    if (v < 0) return z_u128_signed(z_u128_negate(z_u128_from_f128(-v)));
    return z_u128_signed(z_u128_from_f128(v));
}
#endif

#endif

/* the comparisons every row derives from the two above */
static inline int32_t z_u128_cmp(z_u128 a, z_u128 b) { return z_u128_lt(b, a) - z_u128_lt(a, b); }
static inline int32_t z_i128_cmp(z_i128 a, z_i128 b) { return z_i128_lt(b, a) - z_i128_lt(a, b); }
static inline bool z_i128_is_neg(z_i128 a) { return z_i128_lt(a, z_i128_from_i64(0)); }

/* decimal text, written into out with no terminator; the length is the
   result. 39 digits and a sign is the widest. */
static int z_fmt_u128(char* out, z_u128 v) {
    char tmp[40];
    int n = 0;
    const z_u128 ten = z_u128_from_u64(10);
    if (z_u128_eq(v, z_u128_from_u64(0))) { out[0] = '0'; return 1; }
    while (!z_u128_eq(v, z_u128_from_u64(0))) {
        tmp[n++] = (char)('0' + (int)z_u128_to_u64(z_u128_rem(v, ten)));
        v = z_u128_div(v, ten);
    }
    for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
    return n;
}

static int z_fmt_i128(char* out, z_i128 v) {
    if (z_i128_is_neg(v)) {
        out[0] = '-';
        return 1 + z_fmt_u128(out + 1, z_u128_sub(z_u128_from_u64(0), z_i128_bits(v)));
    }
    return z_fmt_u128(out, z_i128_bits(v));
}
