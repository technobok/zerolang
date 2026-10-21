/* Integer division and remainder by a divisor known only at run time. A zero
   divisor panics: C leaves it undefined, and x86 traps on it. A signed
   minimum divided by -1 wraps back to the minimum, as every signed operation
   wraps, and its remainder is 0: C leaves that undefined as well, and x86
   traps on it too. i8 and i16 go through the 32-bit pair, whose result the row
   narrows; u8, c8, u16 and c32 through the unsigned 32-bit pair. A constant
   divisor of an unsigned, i8 or i16 operation takes the row's plain `/`
   instead: zc refuses a constant zero, and no other constant can trap there.
   i32 and i64 always come here, where a constant -1 still needs its wrap; at
   -O2 the C compiler folds the tests away for a constant. */
static inline int32_t z_div_i32(int32_t a, int32_t b) {
    if (b == 0) z_panic("division by zero");
    if (b == -1) return (int32_t)(0u - (uint32_t)a);
    return a / b;
}

static inline int32_t z_rem_i32(int32_t a, int32_t b) {
    if (b == 0) z_panic("division by zero");
    if (b == -1) return 0;
    return a % b;
}

static inline int64_t z_div_i64(int64_t a, int64_t b) {
    if (b == 0) z_panic("division by zero");
    if (b == -1) return (int64_t)(0u - (uint64_t)a);
    return a / b;
}

static inline int64_t z_rem_i64(int64_t a, int64_t b) {
    if (b == 0) z_panic("division by zero");
    if (b == -1) return 0;
    return a % b;
}

static inline uint32_t z_div_u32(uint32_t a, uint32_t b) {
    if (b == 0) z_panic("division by zero");
    return a / b;
}

static inline uint32_t z_rem_u32(uint32_t a, uint32_t b) {
    if (b == 0) z_panic("division by zero");
    return a % b;
}

static inline uint64_t z_div_u64(uint64_t a, uint64_t b) {
    if (b == 0) z_panic("division by zero");
    return a / b;
}

static inline uint64_t z_rem_u64(uint64_t a, uint64_t b) {
    if (b == 0) z_panic("division by zero");
    return a % b;
}
