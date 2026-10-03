static uint64_t z_math_addMulVVW(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, uint64_t y) {
    if (x.length != z.length) z_panic("addMulVVW len");
    return z_mathk_addMulVVWW(z.data, z.data, x.data, y, 0, z.length);
}
