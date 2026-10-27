/* how many of `bytes` are valid UTF-8 before the first invalid sequence; the
   count itself is the shared validator's (_Z_UTF8_VALID_UPTO) */
static uint64_t z_utf8_valid_upto(const uint8_t* data, uint64_t len);
static uint64_t z_collections_utf8ValidUpTo(z_@ListViewVal_u8@_t bytes);
static uint64_t z_collections_utf8ValidUpTo(z_@ListViewVal_u8@_t bytes) {
    return z_utf8_valid_upto(bytes.data, bytes.length);
}
