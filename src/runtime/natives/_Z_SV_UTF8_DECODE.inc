static uint32_t z_utf8_decode_one(
    const char* data, uint64_t length, uint64_t* i);
static uint32_t z_utf8_decode_one(
    const char* data, uint64_t length, uint64_t* i) {
    uint64_t idx = *i;
    unsigned char b0 = (unsigned char)data[idx];
    if (b0 < 0x80) { *i = idx + 1; return (uint32_t)b0; }
    uint32_t cp;
    uint64_t need;
    if ((b0 & 0xE0) == 0xC0) { cp = b0 & 0x1Fu; need = 1; }
    else if ((b0 & 0xF0) == 0xE0) { cp = b0 & 0x0Fu; need = 2; }
    else if ((b0 & 0xF8) == 0xF0) { cp = b0 & 0x07u; need = 3; }
    else { *i = idx + 1; return 0xFFFDu; }
    if (idx + 1 + need > length) { *i = idx + 1; return 0xFFFDu; }
    for (uint64_t k = 1; k <= need; k++) {
        unsigned char b = (unsigned char)data[idx + k];
        if ((b & 0xC0) != 0x80) { *i = idx + 1; return 0xFFFDu; }
        cp = (cp << 6) | (b & 0x3Fu);
    }
    *i = idx + 1 + need;
    return cp;
}

