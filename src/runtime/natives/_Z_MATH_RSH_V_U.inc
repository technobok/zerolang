static uint64_t z_math_rshVU(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, uint64_t s) {
    if (x.length != z.length) z_panic("rshVU len");
    return z_mathk_rshVU(z.data, x.data, s, z.length);
}
