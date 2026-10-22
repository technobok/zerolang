/* u64.divWide: a two-word number hi:lo divided by a word, as a system.divmod. */
static inline z_@divmod@_t z_word_div_wide(uint64_t hi, uint64_t lo, uint64_t d) {
    z_@divmod@_t r = {0};
    r.@MEMBER_divmod.quo@ = z_word_divwide(hi, lo, d, &r.@MEMBER_divmod.rem@);
    return r;
}
