/* f16: every operation the natives.tbl rows and the emitter need, as a function
   over z_f16 (z_prelude.inc). Each computes in float and rounds once to f16,
   which float's 24 bits make exact for + - * / and sqrt, so the two
   representations give the same results: where the C compiler has _Float16 a
   value is one and the conversions are casts; where it has none (tcc), or
   under -DZ_F16_PORTABLE, a value is its 16 bits and the conversions are done
   here, rounding to nearest, ties to even. Loaded only for a program that
   operates on f16. */
#if defined(Z_F16_NATIVE)

static inline float z_f16_to_f32(z_f16 x) { return (float)x; }
static inline z_f16 z_f16_from_f32(float v) { return (z_f16)v; }
static inline z_f16 z_f16_from_f64(double v) { return (z_f16)v; }
#if defined(__SIZEOF_FLOAT128__)
static inline z_f16 z_f16_from_f128(__float128 v) { return (z_f16)v; }
#endif
static inline uint16_t z_f16_bits(z_f16 x) { uint16_t b; memcpy(&b, &x, sizeof(b)); return b; }

#else

static inline float z_f16_to_f32(z_f16 x) {
    uint32_t s = (uint32_t)(x.bits >> 15) << 31;
    uint32_t e = (x.bits >> 10) & 0x1Fu;
    uint32_t m = x.bits & 0x3FFu;
    uint32_t f;
    if (e == 0x1Fu) {
        f = s | 0x7F800000u | (m << 13);
    } else if (e != 0) {
        f = s | ((e + 112u) << 23) | (m << 13);
    } else if (m == 0) {
        f = s;
    } else {
        /* a subnormal half is a normal float: shift its fraction up */
        int sh = 0;
        while ((m & 0x400u) == 0) { m <<= 1; sh++; }
        f = s | ((uint32_t)(113 - sh) << 23) | ((m & 0x3FFu) << 13);
    }
    float r;
    memcpy(&r, &f, sizeof(r));
    return r;
}
/* a double rounded to the nearest f16, ties to even: its 52-bit fraction
   shifted down to 10 bits (more for a subnormal), the shifted-out part
   deciding the rounding. A carry out of the fraction raises the exponent,
   up to infinity. */
static inline z_f16 z_f16_from_f64(double v) {
    uint64_t b;
    memcpy(&b, &v, sizeof(b));
    uint16_t s = (uint16_t)((b >> 48) & 0x8000u);
    int e = (int)((b >> 52) & 0x7FFu);
    uint64_t m = b & 0xFFFFFFFFFFFFFull;
    z_f16 r;
    if (e == 0x7FF) {
        r.bits = (uint16_t)(s | 0x7C00u | (m ? 0x200u | (uint16_t)(m >> 42) : 0));
        return r;
    }
    int he = e - 1023 + 15;
    if (he >= 0x1F) { r.bits = (uint16_t)(s | 0x7C00u); return r; }
    uint64_t full = m | (e ? (1ull << 52) : 0);
    int shift = 42;
    if (he <= 0) {
        /* subnormal (or zero) in f16: one more bit shifted out per step */
        shift = 42 + 1 - he;
        he = 0;
        if (shift > 63) { r.bits = s; return r; }
    }
    uint64_t q = full >> shift;
    uint64_t rem = full & ((1ull << shift) - 1);
    uint64_t half = 1ull << (shift - 1);
    if (rem > half || (rem == half && (q & 1))) q++;
    uint32_t out;
    if (he == 0) {
        out = (uint32_t)q;  /* a carry into bit 10 is the smallest normal */
    } else {
        out = ((uint32_t)he << 10) + (uint32_t)(q - (1ull << 10));
        if (q >= (1ull << 11)) out = ((uint32_t)(he + 1) << 10);
        if (out >= 0x7C00u) out = 0x7C00u;
    }
    r.bits = (uint16_t)(s | out);
    return r;
}
static inline z_f16 z_f16_from_f32(float v) { return z_f16_from_f64((double)v); }
#if defined(__SIZEOF_FLOAT128__)
/* an f128 rounded to a double with round-to-odd, then to f16: a double keeps
   more than twice f16's precision plus two bits, so the second rounding is
   the one a direct conversion would make */
static inline z_f16 z_f16_from_f128(__float128 v) {
    double d = (double)v;
    if ((__float128)d != v && d == d) {
        if ((d > 0) == ((__float128)d > v)) d = nextafter(d, 0.0);
        uint64_t b;
        memcpy(&b, &d, sizeof(b));
        b |= 1;
        memcpy(&d, &b, sizeof(d));
    }
    return z_f16_from_f64(d);
}
#endif
static inline uint16_t z_f16_bits(z_f16 x) { return x.bits; }

#endif

static inline z_f16 z_f16_add(z_f16 a, z_f16 b) { return z_f16_from_f32(z_f16_to_f32(a) + z_f16_to_f32(b)); }
static inline z_f16 z_f16_sub(z_f16 a, z_f16 b) { return z_f16_from_f32(z_f16_to_f32(a) - z_f16_to_f32(b)); }
static inline z_f16 z_f16_mul(z_f16 a, z_f16 b) { return z_f16_from_f32(z_f16_to_f32(a) * z_f16_to_f32(b)); }
static inline z_f16 z_f16_div(z_f16 a, z_f16 b) { return z_f16_from_f32(z_f16_to_f32(a) / z_f16_to_f32(b)); }
static inline bool z_f16_eq(z_f16 a, z_f16 b) { return z_f16_to_f32(a) == z_f16_to_f32(b); }
static inline bool z_f16_lt(z_f16 a, z_f16 b) { return z_f16_to_f32(a) < z_f16_to_f32(b); }
static inline bool z_f16_le(z_f16 a, z_f16 b) { return z_f16_to_f32(a) <= z_f16_to_f32(b); }
