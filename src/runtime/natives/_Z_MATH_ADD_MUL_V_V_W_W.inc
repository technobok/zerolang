static uint64_t z_math_addMulVVWW(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, z_@ListViewVal_u64@_t y, uint64_t m, uint64_t a) {
    if (x.length != z.length || y.length != z.length) z_panic("addMulVVWW len");
    return z_mathk_addMulVVWW(z.data, x.data, y.data, m, a, z.length);
}
