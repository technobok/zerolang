/* 128-bit integer decimal formatting (snprintf has no portable %128d).
   Loaded only for a program that interpolates an i128 or u128: these are
   the only two definitions in the runtime spelling __int128, and a backend
   without that type must not see them unless the program asks. */
static int z_fmt_u128(char* out, unsigned __int128 v) {
    char tmp[40];
    int n = 0;
    if (v == 0) { out[0] = '0'; return 1; }
    while (v > 0) { tmp[n++] = (char)('0' + (int)(v % 10)); v /= 10; }
    for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
    return n;
}

static int z_fmt_i128(char* out, __int128 v) {
    if (v < 0) {
        out[0] = '-';
        return 1 + z_fmt_u128(out + 1, (unsigned __int128)(-(v + 1)) + 1);
    }
    return z_fmt_u128(out, (unsigned __int128)v);
}
