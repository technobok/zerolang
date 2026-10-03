static uint64_t z_math_rshVUInPlace(z_@SpanVal_u64@_t z, uint64_t s) {
    return z_mathk_rshVU(z.data, z.data, s, z.length);
}
