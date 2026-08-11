/* textwriter.write_line -- emit the stringview followed by an
   LF ('\n'). Two sink writes: content then newline. Returns the
   total byte count on ok; on a partial-write err from the
   newline, the content is still considered written (its count
   would have been reported by a plain `write` call) so the
   caller can distinguish 'nothing landed' from 'content landed,
   newline did not' by inspecting the ok count. Errors from the
   content write short-circuit. */
static z_@Result_u64_IoError@_t z_@TextWriter@_writeLine(
    z_@TextWriter@_t* self, const z_@StringView@_t* from
);
static z_@Result_u64_IoError@_t z_@TextWriter@_writeLine(
    z_@TextWriter@_t* self, const z_@StringView@_t* from
) {
    z_@ListViewVal_u8@_t body = { from->size, (uint8_t*)from->data };
    z_@Result_u64_IoError@_t br = z_@BufWriter@_write(self->sink, &body);
    if (br.tag != Z_RESULT_BOX_U64_IOERROR_TAG_OK) return br;
    uint8_t nl = (uint8_t)'\n';
    z_@ListViewVal_u8@_t tail = { (uint64_t)1, &nl };
    z_@Result_u64_IoError@_t tr = z_@BufWriter@_write(self->sink, &tail);
    if (tr.tag != Z_RESULT_BOX_U64_IOERROR_TAG_OK) {
        free(br.data);
        return tr;
    }
    uint64_t body_n = *(uint64_t*)br.data;
    uint64_t tail_n = *(uint64_t*)tr.data;
    free(br.data);
    free(tr.data);
    return z_io_u64_ok(body_n + tail_n);
}

