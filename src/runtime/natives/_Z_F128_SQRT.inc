/* z_f128_sqrt -- the square root of an f128, correctly rounded (to nearest,
   ties to even) as IEEE-754 requires of sqrt. It is computed here, digit by
   digit on the 113-bit significand as soft-fp does, rather than taken from
   libquadmath: one build of sqrtq (mingw-w64's) starts from long double's
   sqrtl and takes one Newton step, which is a unit in the last place off for
   2 and for 0.5. NaN, +inf and both zeros are their own roots; a negative
   number's is NaN. */
/* the 128 bits of an f128, which only a compiler with __int128 has */
__extension__ typedef unsigned __int128 z_f128_bits;
static __float128 z_f128_sqrt(__float128 x) {
    z_f128_bits b;
    memcpy(&b, &x, sizeof b);
    const z_f128_bits one = 1;
    const z_f128_bits fracmask = (one << 112) - 1;
    int neg = (int)(b >> 127);
    int ex = (int)((b >> 112) & 0x7fff);
    z_f128_bits m = b & fracmask;
    if (ex == 0x7fff) {
        if (m != 0 || !neg) return x + x;
        return (x - x) / (x - x);
    }
    if (ex == 0 && m == 0) return x;
    if (neg) return (x - x) / (x - x);
    /* the unbiased exponent and the significand with its leading 1 at bit 112;
       a subnormal is normalized first */
    int e;
    if (ex == 0) {
        e = -16382;
        while ((m >> 112) == 0) {
            m <<= 1;
            e--;
        }
    } else {
        e = ex - 16383;
        m |= one << 112;
    }
    /* three working bits below the significand (guard, round, sticky), and an
       odd exponent folded into the significand so the root's is e / 2 */
    z_f128_bits xf = m << 3;
    if (e & 1) xf <<= 1;
    int re = (e - (e & 1)) / 2;
    z_f128_bits r = 0;
    z_f128_bits s = 0;
    for (z_f128_bits q = one << 115; q != 4; q >>= 1) {
        z_f128_bits t = s + q;
        if (t <= xf) {
            s = t + q;
            xf -= t;
            r += q;
        }
        xf <<= 1;
    }
    if (xf != 0) {
        if (s < xf) r |= 4;
        r |= 1;
    }
    /* round to nearest even on the working bits */
    if ((r & 15) != 4) r += 4;
    r >>= 3;
    if ((r >> 113) != 0) {
        r >>= 1;
        re++;
    }
    z_f128_bits out = ((z_f128_bits)(re + 16383) << 112) | (r & fracmask);
    __float128 y;
    memcpy(&y, &out, sizeof y);
    return y;
}
