/* z_f64_exp2 -- 2 to the power x, within a unit in the last place. On
   Windows it is pow(2, x): the Universal CRT's own exp2 is 23 units in the
   last place off for 1023.5 and 12 for 100.25 (Windows 11), while its pow is
   correctly rounded on the same inputs. Elsewhere it is the C library's exp2. */
static inline double z_f64_exp2(double x) {
#if defined(_WIN32)
    return pow(2.0, x);
#else
    return exp2(x);
#endif
}
