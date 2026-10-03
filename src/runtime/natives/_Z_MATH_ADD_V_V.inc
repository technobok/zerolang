static uint64_t z_math_addVV(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, z_@ListViewVal_u64@_t y) {
    if (x.length != z.length || y.length != z.length) z_panic("addVV len");
    return z_mathk_addVV(z.data, x.data, y.data, z.length);
}
