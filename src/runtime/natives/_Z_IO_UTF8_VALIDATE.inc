/* Returns 1 if [data, data+len) is valid UTF-8, else 0. Rejects
   overlong sequences and surrogate halves (U+D800..U+DFFF) per
   RFC 3629; accepts the full 4-byte range through U+10FFFF. */
static int z_io_utf8_is_valid(const uint8_t* data, uint64_t len);
static int z_io_utf8_is_valid(const uint8_t* data, uint64_t len) {
    uint64_t i = 0;
    while (i < len) {
        uint8_t c = data[i];
        if (c < 0x80) { i++; continue; }
        if ((c & 0xE0) == 0xC0) {
            if (c < 0xC2) return 0;
            if (i + 1 >= len) return 0;
            if ((data[i+1] & 0xC0) != 0x80) return 0;
            i += 2; continue;
        }
        if ((c & 0xF0) == 0xE0) {
            if (i + 2 >= len) return 0;
            if ((data[i+1] & 0xC0) != 0x80) return 0;
            if ((data[i+2] & 0xC0) != 0x80) return 0;
            if (c == 0xE0 && data[i+1] < 0xA0) return 0;
            if (c == 0xED && data[i+1] >= 0xA0) return 0;
            i += 3; continue;
        }
        if ((c & 0xF8) == 0xF0) {
            if (c > 0xF4) return 0;
            if (i + 3 >= len) return 0;
            if ((data[i+1] & 0xC0) != 0x80) return 0;
            if ((data[i+2] & 0xC0) != 0x80) return 0;
            if ((data[i+3] & 0xC0) != 0x80) return 0;
            if (c == 0xF0 && data[i+1] < 0x90) return 0;
            if (c == 0xF4 && data[i+1] >= 0x90) return 0;
            i += 4; continue;
        }
        return 0;
    }
    return 1;
}

/* Box a null-payload ioerror arm into result(string, ioerror). */
static z_Result_String_IoError_t z_io_String_err_arm(z_IoError_tag_t tag);
static z_Result_String_IoError_t z_io_String_err_arm(z_IoError_tag_t tag) {
    z_Result_String_IoError_t result = {0};
    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
    z_IoError_t e = {0};
    e.tag = tag;
    *boxed = e;
    result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
    result.data = boxed;
    return result;
}

/* Box a string value into result(string, ioerror) ok arm. Takes
   ownership of `s` (its heap buffer moves into the boxed copy). */
static z_Result_String_IoError_t z_io_String_ok(z_String_t s);
static z_Result_String_IoError_t z_io_String_ok(z_String_t s) {
    z_Result_String_IoError_t result = {0};
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = s;
    result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

