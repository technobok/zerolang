/* i64 and u64 to decimal text: an interpolated integer's digits, written
   straight into the caller's buffer. snprintf's format parse and locale-aware
   machinery cost more than the digits do, and every integer a program prints
   or interpolates goes through here. Returns the length; writes no NUL, as
   z_fmt_i128 does not -- the caller appends exactly that many bytes. Loaded
   only for a program that interpolates an integer. */
static int z_fmt_u64(char* out, uint64_t v) {
    char tmp[20];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + (int)(v % 10));
        v /= 10;
    } while (v != 0);
    for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
    return n;
}

static int z_fmt_i64(char* out, int64_t v) {
    if (v < 0) {
        out[0] = '-';
        return 1 + z_fmt_u64(out + 1, (uint64_t)0 - (uint64_t)v);
    }
    return z_fmt_u64(out, (uint64_t)v);
}
