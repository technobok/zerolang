/* the bounds of a window over [from, to) of `len` elements: a list's or an
   array's storage lent as a SpanVal or a view, or a window split in two. */
static void z_coll_window_check(uint64_t _from, uint64_t _to, uint64_t _len, const char* _who);
static void z_coll_window_check(uint64_t _from, uint64_t _to, uint64_t _len, const char* _who) {
    if (_from > _to || _to > _len) {
        char _zp_buf[160];
        snprintf(_zp_buf, sizeof(_zp_buf), "%s: [%lu, %lu) out of bounds (length %lu)", _who, (unsigned long)_from, (unsigned long)_to, (unsigned long)_len);
        z_panic(_zp_buf);
    }
}
