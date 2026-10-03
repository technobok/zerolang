static int64_t z_mystery_brew(int64_t n);
static int64_t z_mystery_brew(int64_t n) {
    uint64_t c;
    return (int64_t)z_word_addc((uint64_t)n, (uint64_t)n, 0, &c);
}
