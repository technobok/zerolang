static uint64_t z_math_mulAddVWW(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, uint64_t y, uint64_t r) {
    if (x.length != z.length) z_panic("mulAddVWW len");
    return z_mathk_mulAddVWW(z.data, x.data, y, r, z.length);
}
