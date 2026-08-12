/* textreader.read_line -- scan `buf` for LF; if none present pull
   another chunk from the underlying bufreader. On finding LF split
   at that offset, validate the line (without its LF) as UTF-8,
   emit `ioerror.badencoding` on failure or `result.ok(line)` on
   success. On a zero-byte read with buf non-empty, surface the
   unterminated tail once; with buf empty, surface `ioerror.eof`. Both
   success paths break out with `line` set so the body ends in its
   return -- c2mir miscompiles a struct return otherwise. */
static z_@Result_String_IoError@_t z_@TextReader@_readLine(
    z_@TextReader@_t* self
);
static z_@Result_String_IoError@_t z_@TextReader@_readLine(
    z_@TextReader@_t* self
) {
    uint64_t scan_start = 0;
    z_@String@_t line = {0};
    bool found = false;
    for (;;) {
        for (uint64_t i = scan_start; i < self->buf.length; i++) {
            if (self->buf.data[i] == (uint8_t)'\n') {
                if (!z_io_utf8_is_valid(self->buf.data, i)) {
                    return z_io_String_err_arm(Z_IOERROR_TAG_BADENCODING);
                }
                line.size = i;
                line.capacity = i + 1;
                line.data = (char*)z_xmalloc(line.capacity + 1);
                if (i > 0) memcpy(line.data, self->buf.data, i);
                line.data[i] = '\0';
                uint64_t tail = self->buf.length - (i + 1);
                if (tail > 0) {
                    memmove(self->buf.data, self->buf.data + i + 1, tail);
                }
                self->buf.length = tail;
                found = true;
                break;
            }
        }
        if (found) break;
        scan_start = self->buf.length;
        uint64_t cap = self->source->cap;
        if (cap == 0) cap = 4096;
        z_@Result_u64_IoError@_t rr = z_@BufReader@_read(
            self->source, &self->buf, cap
        );
        if (rr.tag != Z_RESULT_BOX_U64_IOERROR_TAG_OK) {
            z_@Result_String_IoError@_t result = {0};
            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
            result.data = rr.data;
            return result;
        }
        uint64_t n = *(uint64_t*)rr.data;
        free(rr.data);
        if (n == 0) {
            if (self->buf.length == 0) {
                return z_io_String_err_arm(Z_IOERROR_TAG_EOF);
            }
            uint64_t size = self->buf.length;
            if (!z_io_utf8_is_valid(self->buf.data, size)) {
                return z_io_String_err_arm(Z_IOERROR_TAG_BADENCODING);
            }
            line.size = size;
            line.capacity = size + 1;
            line.data = (char*)z_xmalloc(line.capacity + 1);
            if (size > 0) memcpy(line.data, self->buf.data, size);
            line.data[size] = '\0';
            self->buf.length = 0;
            break;
        }
    }
    return z_io_String_ok(line);
}


