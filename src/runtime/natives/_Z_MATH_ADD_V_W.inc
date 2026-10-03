static uint64_t z_math_addVW(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, uint64_t y) {
    if (x.length != z.length) z_panic("addVW len");
    return z_mathk_addVW(z.data, x.data, y, z.length);
}
