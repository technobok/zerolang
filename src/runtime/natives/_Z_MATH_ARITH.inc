/* Copyright 2009 The Go Authors. All rights reserved.
   Use of this source code is governed by a BSD-style
   license that can be found in lib/system/math/LICENSE-GO.

   Ported from Go's src/math/big/arith.go at 577b91f341: the loops over word
   vectors behind math's kernels, each kernel's own fragment checking lengths
   and handing them the windows' storage. A vector is a pointer and the one
   length all of a call's vectors share. z may be x itself -- the in-place
   twins pass it so -- and every loop reads a word of x before it writes that
   word of z, or reads only words it has not yet written (lshVU runs from the
   top down, rshVU from the bottom up).

   A carry step and a borrow step are the system word primitives' and the
   wide multiply is z_fh_mul128, so a C compiler's builtins are taken where it
   has them -- __int128 and the carry builtins, which gcc and clang lower to
   mul, adc and sbb -- and tcc runs their portable forms. */

/* z = x + y, the final carry 0 or 1 */
static uint64_t z_mathk_addVV(uint64_t* z, const uint64_t* x, const uint64_t* y, uint64_t n) {
    uint64_t c = 0;
    for (uint64_t i = 0; i < n; i++) z[i] = z_word_addc(x[i], y[i], c, &c);
    return c;
}

/* z = x - y, the final borrow 0 or 1 */
static uint64_t z_mathk_subVV(uint64_t* z, const uint64_t* x, const uint64_t* y, uint64_t n) {
    uint64_t c = 0;
    for (uint64_t i = 0; i < n; i++) z[i] = z_word_subb(x[i], y[i], c, &c);
    return c;
}

/* z = x + y for a word y: y itself when n is 0, else the carry 0 or 1. Once
   the carry dies the rest of x is copied, which z == x skips. */
static uint64_t z_mathk_addVW(uint64_t* z, const uint64_t* x, uint64_t y, uint64_t n) {
    if (n == 0) return y;
    uint64_t s = x[0] + y;
    z[0] = s;
    if (s >= y) {
        if (z != x) memcpy(z + 1, x + 1, (size_t)(n - 1) * sizeof(uint64_t));
        return 0;
    }
    for (uint64_t i = 1; i < n; i++) {
        uint64_t xi = x[i];
        if (xi != UINT64_MAX) {
            z[i] = xi + 1;
            if (z != x) memcpy(z + i + 1, x + i + 1, (size_t)(n - i - 1) * sizeof(uint64_t));
            return 0;
        }
        z[i] = 0;
    }
    return 1;
}

/* z = x - y for a word y: y itself when n is 0, else the borrow 0 or 1. Once
   the borrow dies the rest of x is copied, which z == x skips. */
static uint64_t z_mathk_subVW(uint64_t* z, const uint64_t* x, uint64_t y, uint64_t n) {
    if (n == 0) return y;
    uint64_t x0 = x[0];
    z[0] = x0 - y;
    if (x0 >= y) {
        if (z != x) memcpy(z + 1, x + 1, (size_t)(n - 1) * sizeof(uint64_t));
        return 0;
    }
    for (uint64_t i = 1; i < n; i++) {
        uint64_t xi = x[i];
        if (xi != 0) {
            z[i] = xi - 1;
            if (z != x) memcpy(z + i + 1, x + i + 1, (size_t)(n - i - 1) * sizeof(uint64_t));
            return 0;
        }
        z[i] = UINT64_MAX;
    }
    return 1;
}

/* z = x << s for 0 <= s < 64, the bits shifted out of the top */
static uint64_t z_mathk_lshVU(uint64_t* z, const uint64_t* x, uint64_t s, uint64_t n) {
    if (n == 0) return 0;
    if (s == 0) {
        if (z != x) memcpy(z, x, (size_t)n * sizeof(uint64_t));
        return 0;
    }
    uint64_t sh = s & 63;
    uint64_t back = (64 - sh) & 63;
    uint64_t c = x[n - 1] >> back;
    for (uint64_t i = n - 1; i > 0; i--) z[i] = (x[i] << sh) | (x[i - 1] >> back);
    z[0] = x[0] << sh;
    return c;
}

/* z = x >> s for 0 <= s < 64, the bits shifted out of the bottom, left-aligned
   in a word */
static uint64_t z_mathk_rshVU(uint64_t* z, const uint64_t* x, uint64_t s, uint64_t n) {
    if (n == 0) return 0;
    if (s == 0) {
        if (z != x) memcpy(z, x, (size_t)n * sizeof(uint64_t));
        return 0;
    }
    uint64_t sh = s & 63;
    uint64_t back = (64 - sh) & 63;
    uint64_t c = x[0] << back;
    for (uint64_t i = 1; i < n; i++) z[i - 1] = (x[i - 1] >> sh) | (x[i] << back);
    z[n - 1] = x[n - 1] >> sh;
    return c;
}

/* z = x*y + r for words y and r, the high word that spills past z */
static uint64_t z_mathk_mulAddVWW(uint64_t* z, const uint64_t* x, uint64_t y, uint64_t r, uint64_t n) {
    uint64_t c = r;
    for (uint64_t i = 0; i < n; i++) {
        uint64_t lo;
        uint64_t hi;
        z_fh_mul128(x[i], y, &lo, &hi);
        lo += c;
        z[i] = lo;
        c = hi + (lo < c);
    }
    return c;
}

/* z = x + y*m + a for words m and a, the high word that spills past z */
static uint64_t z_mathk_addMulVVWW(uint64_t* z, const uint64_t* x, const uint64_t* y, uint64_t m, uint64_t a, uint64_t n) {
    uint64_t c = a;
    for (uint64_t i = 0; i < n; i++) {
        uint64_t lo;
        uint64_t hi;
        z_fh_mul128(y[i], m, &lo, &hi);
        lo += x[i];
        hi += lo < x[i];
        lo += c;
        z[i] = lo;
        c = hi + (lo < c);
    }
    return c;
}
