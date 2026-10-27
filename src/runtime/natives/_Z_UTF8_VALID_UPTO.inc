/* the bytes of [data, data+len) that are valid UTF-8 before the first invalid
   sequence: len when all of them are. Rejects overlong sequences and
   surrogate halves (U+D800..U+DFFF) per RFC 3629, and accepts the full
   4-byte range through U+10FFFF. */
static uint64_t z_utf8_valid_upto(const uint8_t* data, uint64_t len);
static uint64_t z_utf8_valid_upto(const uint8_t* data, uint64_t len) {
    uint64_t i = 0;
    while (i < len) {
        uint8_t c = data[i];
        if (c < 0x80) { i++; continue; }
        if ((c & 0xE0) == 0xC0) {
            if (c < 0xC2) return i;
            if (i + 1 >= len) return i;
            if ((data[i+1] & 0xC0) != 0x80) return i;
            i += 2; continue;
        }
        if ((c & 0xF0) == 0xE0) {
            if (i + 2 >= len) return i;
            if ((data[i+1] & 0xC0) != 0x80) return i;
            if ((data[i+2] & 0xC0) != 0x80) return i;
            if (c == 0xE0 && data[i+1] < 0xA0) return i;
            if (c == 0xED && data[i+1] >= 0xA0) return i;
            i += 3; continue;
        }
        if ((c & 0xF8) == 0xF0) {
            if (c > 0xF4) return i;
            if (i + 3 >= len) return i;
            if ((data[i+1] & 0xC0) != 0x80) return i;
            if ((data[i+2] & 0xC0) != 0x80) return i;
            if ((data[i+3] & 0xC0) != 0x80) return i;
            if (c == 0xF0 && data[i+1] < 0x90) return i;
            if (c == 0xF4 && data[i+1] >= 0x90) return i;
            i += 4; continue;
        }
        return i;
    }
    return len;
}
