static uint64_t z_math_subVW(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, uint64_t y) {
    if (x.length != z.length) z_panic("subVW len");
    return z_mathk_subVW(z.data, x.data, y, z.length);
}
