/* i128 and u128: every operation the natives.tbl rows and the emitter need, as
   a function over z_i128 / z_u128 (z_head.inc). Where the C compiler has a
   128-bit integer type each one is the operator it names; where it has none
   (tcc), or under -DZ_INT128_PORTABLE, the values are two 64-bit words and each
   function computes on them. The emitted C is the same for every compiler: it
   is z_head.inc's preprocessor test that decides, not zc. Loaded only for
   a program that operates on either type. */
/*@@row @prelude.u128_lo demand=@u128_lo needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline uint64_t z_u128_lo(z_u128 x) { return (uint64_t)x; }
#else
static inline uint64_t z_u128_lo(z_u128 x) { return x.lo; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_hi demand=@u128_hi needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline uint64_t z_u128_hi(z_u128 x) { return (uint64_t)(x >> 64); }
#else
static inline uint64_t z_u128_hi(z_u128 x) { return x.hi; }
#endif
/*@@end@@*/
/*@@row @prelude.i128_bits demand=@i128_bits needs=@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_i128_bits(z_i128 x) { return (z_u128)x; }
#else
static inline z_u128 z_i128_bits(z_i128 x) { z_u128 r = { x.lo, x.hi }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_signed demand=@u128_signed needs=@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_u128_signed(z_u128 x) { return (z_i128)x; }
#else
static inline z_i128 z_u128_signed(z_u128 x) { z_i128 r = { x.lo, x.hi }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.i128_from_i64 demand=@i128_from_i64 needs=@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_from_i64(int64_t v) { return (z_i128)v; }
#else
static inline z_i128 z_i128_from_i64(int64_t v) {
    z_i128 r = { (uint64_t)v, v < 0 ? ~(uint64_t)0 : 0 };
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.i128_from_u64 demand=@i128_from_u64 needs=@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_from_u64(uint64_t v) { return (z_i128)v; }
#else
static inline z_i128 z_i128_from_u64(uint64_t v) { z_i128 r = { v, 0 }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_from_u64 demand=@u128_from_u64 needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_from_u64(uint64_t v) { return (z_u128)v; }
#else
static inline z_u128 z_u128_from_u64(uint64_t v) { z_u128 r = { v, 0 }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.i128_to_i64 demand=@i128_to_i64 needs=@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline int64_t z_i128_to_i64(z_i128 x) { return (int64_t)x; }
#else
static inline int64_t z_i128_to_i64(z_i128 x) { return (int64_t)x.lo; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_to_u64 demand=@u128_to_u64 needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline uint64_t z_u128_to_u64(z_u128 x) { return (uint64_t)x; }
#else
static inline uint64_t z_u128_to_u64(z_u128 x) { return x.lo; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_add demand=@u128_add needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_add(z_u128 a, z_u128 b) { return a + b; }
#else
static inline z_u128 z_u128_add(z_u128 a, z_u128 b) {
    z_u128 r;
    r.lo = a.lo + b.lo;
    r.hi = a.hi + b.hi + (r.lo < a.lo);
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_sub demand=@u128_sub needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_sub(z_u128 a, z_u128 b) { return a - b; }
#else
static inline z_u128 z_u128_sub(z_u128 a, z_u128 b) {
    z_u128 r;
    r.lo = a.lo - b.lo;
    r.hi = a.hi - b.hi - (a.lo < b.lo);
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_mul64 demand=@u128_mul64 needs=@int128_native,@u128_t@@*/
#if !defined(Z_INT128_NATIVE)
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
#endif
/*@@end@@*/
/*@@row @prelude.u128_mul demand=@u128_mul needs=@u128_mul64?portable128,@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_mul(z_u128 a, z_u128 b) { return a * b; }
#else
static inline z_u128 z_u128_mul(z_u128 a, z_u128 b) {
    z_u128 r = z_u128_mul64(a.lo, b.lo);
    r.hi += a.lo * b.hi + a.hi * b.lo;
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_and demand=@u128_and needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_and(z_u128 a, z_u128 b) { return a & b; }
#else
static inline z_u128 z_u128_and(z_u128 a, z_u128 b) { z_u128 r = { a.lo & b.lo, a.hi & b.hi }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_or demand=@u128_or needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_or(z_u128 a, z_u128 b) { return a | b; }
#else
static inline z_u128 z_u128_or(z_u128 a, z_u128 b) { z_u128 r = { a.lo | b.lo, a.hi | b.hi }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_xor demand=@u128_xor needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_xor(z_u128 a, z_u128 b) { return a ^ b; }
#else
static inline z_u128 z_u128_xor(z_u128 a, z_u128 b) { z_u128 r = { a.lo ^ b.lo, a.hi ^ b.hi }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_not demand=@u128_not needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_not(z_u128 a) { return ~a; }
#else
static inline z_u128 z_u128_not(z_u128 a) { z_u128 r = { ~a.lo, ~a.hi }; return r; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_shl demand=@u128_shl needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_shl(z_u128 a, unsigned n) { return a << n; }
#else
/* n < 128, as C's shift requires of the native type */
static inline z_u128 z_u128_shl(z_u128 a, unsigned n) {
    z_u128 r;
    if (n == 0) return a;
    if (n >= 64) { r.hi = a.lo << (n - 64); r.lo = 0; return r; }
    r.hi = (a.hi << n) | (a.lo >> (64 - n));
    r.lo = a.lo << n;
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_shr demand=@u128_shr needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_shr(z_u128 a, unsigned n) { return a >> n; }
#else
static inline z_u128 z_u128_shr(z_u128 a, unsigned n) {
    z_u128 r;
    if (n == 0) return a;
    if (n >= 64) { r.lo = a.hi >> (n - 64); r.hi = 0; return r; }
    r.lo = (a.lo >> n) | (a.hi << (64 - n));
    r.hi = a.hi >> n;
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_eq demand=@u128_eq needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline bool z_u128_eq(z_u128 a, z_u128 b) { return a == b; }
#else
static inline bool z_u128_eq(z_u128 a, z_u128 b) { return a.lo == b.lo && a.hi == b.hi; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_lt demand=@u128_lt needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline bool z_u128_lt(z_u128 a, z_u128 b) { return a < b; }
#else
static inline bool z_u128_lt(z_u128 a, z_u128 b) { return a.hi < b.hi || (a.hi == b.hi && a.lo < b.lo); }
#endif
/*@@end@@*/
/*@@row @prelude.u128_clz demand=@u128_clz needs=@int128_native,@u128_t,@clz64?portable128@@*/
#if !defined(Z_INT128_NATIVE)
static inline int z_u128_clz(z_u128 a) {
    if (a.hi) return z_clz64(a.hi);
    if (a.lo) return 64 + z_clz64(a.lo);
    return 128;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_divmod demand=@u128_divmod needs=@u128_clz?portable128,@u128_lt?portable128,@u128_shl?portable128,@u128_shr?portable128,@u128_sub?portable128,@int128_native,@u128_t,@panic?portable128@@*/
#if !defined(Z_INT128_NATIVE)
/* a / b and a % b by binary long division: b is aligned under a's top bit
   and subtracted back down, one quotient bit per step. A zero divisor
   panics, where the native type traps. */
static z_u128 z_u128_divmod(z_u128 a, z_u128 b, z_u128* rem) {
    z_u128 q = { 0, 0 };
    if (b.lo == 0 && b.hi == 0) z_panic("division by zero");
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
#endif
/*@@end@@*/
/*@@row @prelude.u128_div demand=@u128_div needs=@u128_divmod?portable128,@int128_native,@u128_t,@panic?native128@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_div(z_u128 a, z_u128 b) {
    if (b == 0) z_panic("division by zero");
    return a / b;
}
#else
static inline z_u128 z_u128_div(z_u128 a, z_u128 b) { z_u128 r; return z_u128_divmod(a, b, &r); }
#endif
/*@@end@@*/
/*@@row @prelude.u128_rem demand=@u128_rem needs=@u128_divmod?portable128,@int128_native,@u128_t,@panic?native128@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_rem(z_u128 a, z_u128 b) {
    if (b == 0) z_panic("division by zero");
    return a % b;
}
#else
static inline z_u128 z_u128_rem(z_u128 a, z_u128 b) { z_u128 r; z_u128_divmod(a, b, &r); return r; }
#endif
/*@@end@@*/
/*@@row @prelude.i128_neg demand=@i128_neg needs=@int128_native,@i128_t@@*/
#if !defined(Z_INT128_NATIVE)
static inline bool z_i128_neg(z_i128 a) { return (a.hi >> 63) != 0; }
#endif
/*@@end@@*/
/*@@row @prelude.u128_negate demand=@u128_negate needs=@u128_add?portable128,@u128_from_u64?portable128,@u128_not?portable128,@int128_native,@u128_t@@*/
#if !defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_negate(z_u128 a) { return z_u128_add(z_u128_not(a), z_u128_from_u64(1)); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_add demand=@i128_add needs=@i128_bits?portable128,@u128_add?portable128,@u128_signed?portable128,@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_add(z_i128 a, z_i128 b) { return (z_i128)((z_u128)a + (z_u128)b); }
#else
static inline z_i128 z_i128_add(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_add(z_i128_bits(a), z_i128_bits(b))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_sub demand=@i128_sub needs=@i128_bits?portable128,@u128_signed?portable128,@u128_sub?portable128,@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_sub(z_i128 a, z_i128 b) { return (z_i128)((z_u128)a - (z_u128)b); }
#else
static inline z_i128 z_i128_sub(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_sub(z_i128_bits(a), z_i128_bits(b))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_mul demand=@i128_mul needs=@i128_bits?portable128,@u128_mul?portable128,@u128_signed?portable128,@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_mul(z_i128 a, z_i128 b) { return (z_i128)((z_u128)a * (z_u128)b); }
#else
static inline z_i128 z_i128_mul(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_mul(z_i128_bits(a), z_i128_bits(b))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_div demand=@i128_div needs=@i128_bits?portable128,@i128_neg?portable128,@u128_div?portable128,@u128_negate?portable128,@u128_signed?portable128,@int128_native,@i128_t,@u128_t,@panic?native128@@*/
#if defined(Z_INT128_NATIVE)
/* a zero divisor panics; the minimum divided by -1 wraps back to itself */
static inline z_i128 z_i128_div(z_i128 a, z_i128 b) {
    if (b == 0) z_panic("division by zero");
    if (b == -1) return (z_i128)((z_u128)0 - (z_u128)a);
    return a / b;
}
#else
/* truncating division, as C's: the quotient's sign is the operands' signs
   combined, the remainder's is the dividend's. The minimum over -1 wraps. */
static inline z_i128 z_i128_div(z_i128 a, z_i128 b) {
    z_u128 ma = z_i128_neg(a) ? z_u128_negate(z_i128_bits(a)) : z_i128_bits(a);
    z_u128 mb = z_i128_neg(b) ? z_u128_negate(z_i128_bits(b)) : z_i128_bits(b);
    z_u128 q = z_u128_div(ma, mb);
    return z_u128_signed(z_i128_neg(a) != z_i128_neg(b) ? z_u128_negate(q) : q);
}
#endif
/*@@end@@*/
/*@@row @prelude.i128_rem demand=@i128_rem needs=@i128_bits?portable128,@i128_neg?portable128,@u128_negate?portable128,@u128_rem?portable128,@u128_signed?portable128,@int128_native,@i128_t,@u128_t,@panic?native128@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_rem(z_i128 a, z_i128 b) {
    if (b == 0) z_panic("division by zero");
    if (b == -1) return 0;
    return a % b;
}
#else
static inline z_i128 z_i128_rem(z_i128 a, z_i128 b) {
    z_u128 ma = z_i128_neg(a) ? z_u128_negate(z_i128_bits(a)) : z_i128_bits(a);
    z_u128 mb = z_i128_neg(b) ? z_u128_negate(z_i128_bits(b)) : z_i128_bits(b);
    z_u128 r = z_u128_rem(ma, mb);
    return z_u128_signed(z_i128_neg(a) ? z_u128_negate(r) : r);
}
#endif
/*@@end@@*/
/*@@row @prelude.i128_and demand=@i128_and needs=@i128_bits?portable128,@u128_and?portable128,@u128_signed?portable128,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_and(z_i128 a, z_i128 b) { return a & b; }
#else
static inline z_i128 z_i128_and(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_and(z_i128_bits(a), z_i128_bits(b))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_or demand=@i128_or needs=@i128_bits?portable128,@u128_or?portable128,@u128_signed?portable128,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_or(z_i128 a, z_i128 b) { return a | b; }
#else
static inline z_i128 z_i128_or(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_or(z_i128_bits(a), z_i128_bits(b))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_xor demand=@i128_xor needs=@i128_bits?portable128,@u128_signed?portable128,@u128_xor?portable128,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_xor(z_i128 a, z_i128 b) { return a ^ b; }
#else
static inline z_i128 z_i128_xor(z_i128 a, z_i128 b) { return z_u128_signed(z_u128_xor(z_i128_bits(a), z_i128_bits(b))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_not demand=@i128_not needs=@i128_bits?portable128,@u128_not?portable128,@u128_signed?portable128,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_not(z_i128 a) { return ~a; }
#else
static inline z_i128 z_i128_not(z_i128 a) { return z_u128_signed(z_u128_not(z_i128_bits(a))); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_shl demand=@i128_shl needs=@i128_bits?portable128,@u128_shl?portable128,@u128_signed?portable128,@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_shl(z_i128 a, unsigned n) { return (z_i128)((z_u128)a << n); }
#else
static inline z_i128 z_i128_shl(z_i128 a, unsigned n) { return z_u128_signed(z_u128_shl(z_i128_bits(a), n)); }
#endif
/*@@end@@*/
/*@@row @prelude.i128_shr demand=@i128_shr needs=@i128_bits?portable128,@i128_neg?portable128,@u128_from_u64?portable128,@u128_not?portable128,@u128_or?portable128,@u128_shr?portable128,@u128_signed?portable128,@int128_native,@i128_t,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_shr(z_i128 a, unsigned n) { return a >> n; }
#else
/* arithmetic: the sign fills the vacated high bits */
static inline z_i128 z_i128_shr(z_i128 a, unsigned n) {
    z_u128 r = z_u128_shr(z_i128_bits(a), n);
    if (z_i128_neg(a) && n > 0) r = z_u128_or(r, z_u128_not(z_u128_shr(z_u128_not(z_u128_from_u64(0)), n)));
    return z_u128_signed(r);
}
#endif
/*@@end@@*/
/*@@row @prelude.i128_eq demand=@i128_eq needs=@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline bool z_i128_eq(z_i128 a, z_i128 b) { return a == b; }
#else
static inline bool z_i128_eq(z_i128 a, z_i128 b) { return a.lo == b.lo && a.hi == b.hi; }
#endif
/*@@end@@*/
/*@@row @prelude.i128_lt demand=@i128_lt needs=@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline bool z_i128_lt(z_i128 a, z_i128 b) { return a < b; }
#else
static inline bool z_i128_lt(z_i128 a, z_i128 b) {
    if (a.hi != b.hi) return (int64_t)a.hi < (int64_t)b.hi;
    return a.lo < b.lo;
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_to_f64 demand=@u128_to_f64 needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline double z_u128_to_f64(z_u128 x) { return (double)x; }
#else
/* to a float: each word is exact in the destination whenever the whole value
   is, which is the only case a checked conversion answers ok */
static inline double z_u128_to_f64(z_u128 x) { return (double)x.hi * 0x1p64 + (double)x.lo; }
#endif
/*@@end@@*/
/*@@row @prelude.i128_to_f64 demand=@i128_to_f64 needs=@i128_bits?portable128,@i128_neg?portable128,@u128_negate?portable128,@u128_to_f64?portable128,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline double z_i128_to_f64(z_i128 x) { return (double)x; }
#else
static inline double z_i128_to_f64(z_i128 x) {
    if (z_i128_neg(x)) return -z_u128_to_f64(z_u128_negate(z_i128_bits(x)));
    return z_u128_to_f64(z_i128_bits(x));
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_from_f64 demand=@u128_from_f64 needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_u128 z_u128_from_f64(double v) { return (z_u128)v; }
#else
/* from a float already known to be in range: truncated toward zero */
static inline z_u128 z_u128_from_f64(double v) {
    z_u128 r;
    if (v < 0x1p64) { r.lo = (uint64_t)v; r.hi = 0; return r; }
    r.hi = (uint64_t)(v / 0x1p64);
    r.lo = (uint64_t)(v - (double)r.hi * 0x1p64);
    return r;
}
#endif
/*@@end@@*/
/*@@row @prelude.i128_from_f64 demand=@i128_from_f64 needs=@u128_from_f64?portable128,@u128_negate?portable128,@u128_signed?portable128,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
static inline z_i128 z_i128_from_f64(double v) { return (z_i128)v; }
#else
static inline z_i128 z_i128_from_f64(double v) {
    if (v < 0) return z_u128_signed(z_u128_negate(z_u128_from_f64(-v)));
    return z_u128_signed(z_u128_from_f64(v));
}
#endif
/*@@end@@*/
/*@@row @prelude.u128_to_f128 demand=@u128_to_f128 needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
#if defined(__SIZEOF_FLOAT128__)
static inline __float128 z_u128_to_f128(z_u128 x) { return (__float128)x; }
#endif
#else
#if defined(__SIZEOF_FLOAT128__)
static inline __float128 z_u128_to_f128(z_u128 x) {
    return (__float128)x.hi * (__float128)0x1p64 + (__float128)x.lo;
}
#endif
#endif
/*@@end@@*/
/*@@row @prelude.i128_to_f128 demand=@i128_to_f128 needs=@i128_bits?portable128q,@i128_neg?portable128q,@u128_negate?portable128q,@u128_to_f128?portable128q,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
#if defined(__SIZEOF_FLOAT128__)
static inline __float128 z_i128_to_f128(z_i128 x) { return (__float128)x; }
#endif
#else
#if defined(__SIZEOF_FLOAT128__)
static inline __float128 z_i128_to_f128(z_i128 x) {
    if (z_i128_neg(x)) return -z_u128_to_f128(z_u128_negate(z_i128_bits(x)));
    return z_u128_to_f128(z_i128_bits(x));
}
#endif
#endif
/*@@end@@*/
/*@@row @prelude.u128_from_f128 demand=@u128_from_f128 needs=@int128_native,@u128_t@@*/
#if defined(Z_INT128_NATIVE)
#if defined(__SIZEOF_FLOAT128__)
static inline z_u128 z_u128_from_f128(__float128 v) { return (z_u128)v; }
#endif
#else
#if defined(__SIZEOF_FLOAT128__)
static inline z_u128 z_u128_from_f128(__float128 v) {
    z_u128 r;
    if (v < (__float128)0x1p64) { r.lo = (uint64_t)v; r.hi = 0; return r; }
    r.hi = (uint64_t)(v / (__float128)0x1p64);
    r.lo = (uint64_t)(v - (__float128)r.hi * (__float128)0x1p64);
    return r;
}
#endif
#endif
/*@@end@@*/
/*@@row @prelude.i128_from_f128 demand=@i128_from_f128 needs=@u128_from_f128?portable128q,@u128_negate?portable128q,@u128_signed?portable128q,@int128_native,@i128_t@@*/
#if defined(Z_INT128_NATIVE)
#if defined(__SIZEOF_FLOAT128__)
static inline z_i128 z_i128_from_f128(__float128 v) { return (z_i128)v; }
#endif
#else
#if defined(__SIZEOF_FLOAT128__)
static inline z_i128 z_i128_from_f128(__float128 v) {
    if (v < 0) return z_u128_signed(z_u128_negate(z_u128_from_f128(-v)));
    return z_u128_signed(z_u128_from_f128(v));
}
#endif
#endif
/*@@end@@*/

/*@@row @prelude.u128_cmp demand=@u128_cmp needs=@u128_lt,@u128_t@@*/
/* the comparisons every row derives from the two above */
static inline int32_t z_u128_cmp(z_u128 a, z_u128 b) { return z_u128_lt(b, a) - z_u128_lt(a, b); }
/*@@end@@*/
/*@@row @prelude.i128_cmp demand=@i128_cmp needs=@i128_lt,@i128_t@@*/
static inline int32_t z_i128_cmp(z_i128 a, z_i128 b) { return z_i128_lt(b, a) - z_i128_lt(a, b); }
/*@@end@@*/
/*@@row @prelude.i128_is_neg demand=@i128_is_neg needs=@i128_from_i64,@i128_lt,@i128_t@@*/
static inline bool z_i128_is_neg(z_i128 a) { return z_i128_lt(a, z_i128_from_i64(0)); }
/*@@end@@*/

/*@@row @prelude.u128_span demand=@u128_span needs=@u128_hi,@u128_lo,@span64,@u128_t@@*/
/* z_span64 for a 128-bit magnitude: the bits from its highest set bit down to
   its lowest, both counted; 0 for 0 */
static inline int z_u128_span(z_u128 m) {
    uint64_t hi = z_u128_hi(m), lo = z_u128_lo(m);
    if (hi == 0) return z_span64(lo);
    return (128 - z_clz64(hi)) - (lo != 0 ? z_ctz64(lo) : 64 + z_ctz64(hi));
}
/*@@end@@*/

/*@@row @prelude.fmt_u128 demand=@fmt_u128 needs=@u128_div,@u128_eq,@u128_from_u64,@u128_rem,@u128_to_u64,@u128_t@@*/
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
/*@@end@@*/

/*@@row @prelude.fmt_i128 demand=@fmt_i128 needs=@fmt_u128,@i128_bits,@i128_is_neg,@u128_from_u64,@u128_sub,@i128_t@@*/
static int z_fmt_i128(char* out, z_i128 v) {
    if (z_i128_is_neg(v)) {
        out[0] = '-';
        return 1 + z_fmt_u128(out + 1, z_u128_sub(z_u128_from_u64(0), z_i128_bits(v)));
    }
    return z_fmt_u128(out, z_i128_bits(v));
}
/*@@end@@*/
