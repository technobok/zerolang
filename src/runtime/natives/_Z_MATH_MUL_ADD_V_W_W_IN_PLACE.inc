static uint64_t z_math_mulAddVWWInPlace(z_@SpanVal_u64@_t z, uint64_t y, uint64_t r) {
    return z_mathk_mulAddVWW(z.data, z.data, y, r, z.length);
}
