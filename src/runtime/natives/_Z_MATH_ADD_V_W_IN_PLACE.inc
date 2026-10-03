static uint64_t z_math_addVWInPlace(z_@SpanVal_u64@_t z, uint64_t y) {
    return z_mathk_addVW(z.data, z.data, y, z.length);
}
