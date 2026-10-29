/* u64.addCarry and u64.subBorrow: one step of a multi-word sum or difference,
   as a system.carried. A carry in that is not 0 counts as 1. */
/*@@row @system.word_add_carry demand=@word_add_carry needs=word,@word_addc@@*/
static inline z_@carried@_t z_word_add_carry(uint64_t a, uint64_t b, uint64_t carry) {
    z_@carried@_t r = {0};
    r.@MEMBER_carried.value@ = z_word_addc(a, b, carry != 0, &r.@MEMBER_carried.carry@);
    return r;
}
/*@@end@@*/
/*@@row @system.word_sub_borrow demand=@word_sub_borrow needs=word,@word_subb@@*/
static inline z_@carried@_t z_word_sub_borrow(uint64_t a, uint64_t b, uint64_t carry) {
    z_@carried@_t r = {0};
    r.@MEMBER_carried.value@ = z_word_subb(a, b, carry != 0, &r.@MEMBER_carried.carry@);
    return r;
}
/*@@end@@*/
