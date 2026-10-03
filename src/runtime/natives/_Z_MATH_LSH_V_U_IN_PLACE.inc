static uint64_t z_math_lshVUInPlace(z_@SpanVal_u64@_t z, uint64_t s) {
    return z_mathk_lshVU(z.data, z.data, s, z.length);
}
