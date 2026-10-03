static uint64_t z_math_addVVInPlace(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t y) {
    if (y.length != z.length) z_panic("addVV len");
    return z_mathk_addVV(z.data, z.data, y.data, z.length);
}
