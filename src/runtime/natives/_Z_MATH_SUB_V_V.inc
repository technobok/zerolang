static uint64_t z_math_subVV(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, z_@ListViewVal_u64@_t y) {
    if (x.length != z.length || y.length != z.length) z_panic("subVV len");
    return z_mathk_subVV(z.data, x.data, y.data, z.length);
}
