/* textreader.create -- wrap a bufreader. Owns a small line-accum
   buffer (`buf`) used across read_line calls to carry bytes that
   didn't yet form a complete LF-terminated line. */
static z_TextReader_t z_TextReader_create(z_BufReader_t* source);
static z_TextReader_t z_TextReader_create(z_BufReader_t* source) {
    z_TextReader_t self = {0};
    self.source = source;
    self.buf = z_List_u8_create(0);
    return self;
}

