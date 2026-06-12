static bool z_ascii_is_ws(unsigned char c);
static bool z_ascii_is_ws(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n'
        || c == '\v' || c == '\f' || c == '\r';
}

