static uint64_t z_math_lshVU(z_@SpanVal_u64@_t z, z_@ListViewVal_u64@_t x, uint64_t s) {
    if (x.length != z.length) z_panic("lshVU len");
    return z_mathk_lshVU(z.data, x.data, s, z.length);
}
