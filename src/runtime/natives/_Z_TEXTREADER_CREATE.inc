/* textreader.create -- wrap a bufreader. Owns a small line-accum
   buffer (`buf`) used across read_line calls to carry bytes that
   didn't yet form a complete LF-terminated line. */
static z_@TextReader@_t z_@TextReader@_create(z_@BufReader@_t* source);
static z_@TextReader@_t z_@TextReader@_create(z_@BufReader@_t* source) {
    z_@TextReader@_t self = {0};
    self.source = source;
    self.buf = z_@ListVal_u8@_create(0);
    return self;
}


