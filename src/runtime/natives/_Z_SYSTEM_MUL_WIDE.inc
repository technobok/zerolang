/* u64.mulWide: the whole product of two words, as a system.wide. */
static inline z_@wide@_t z_word_mul_wide(uint64_t a, uint64_t b) {
    z_@wide@_t r = {0};
    z_fh_mul128(a, b, &r.@MEMBER_wide.lo@, &r.@MEMBER_wide.hi@);
    return r;
}
