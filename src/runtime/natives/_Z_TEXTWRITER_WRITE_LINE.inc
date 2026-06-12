/* textwriter.write_line -- emit the stringview followed by an
   LF ('\n'). Two sink writes: content then newline. Returns the
   total byte count on ok; on a partial-write err from the
   newline, the content is still considered written (its count
   would have been reported by a plain `write` call) so the
   caller can distinguish 'nothing landed' from 'content landed,
   newline did not' by inspecting the ok count. Errors from the
   content write short-circuit. */
static z_Result_u64_IoError_t z_TextWriter_writeLine(
    z_TextWriter_t* self, const z_StringView_t* from
);
static z_Result_u64_IoError_t z_TextWriter_writeLine(
    z_TextWriter_t* self, const z_StringView_t* from
) {
    z_ListView_u8_t body = { from->length, (uint8_t*)from->data };
    z_Result_u64_IoError_t br = z_BufWriter_write(self->sink, &body);
    if (br.tag != Z_RESULT_U64_IOERROR_TAG_OK) return br;
    uint8_t nl = (uint8_t)'\n';
    z_ListView_u8_t tail = { (uint64_t)1, &nl };
    z_Result_u64_IoError_t tr = z_BufWriter_write(self->sink, &tail);
    if (tr.tag != Z_RESULT_U64_IOERROR_TAG_OK) {
        free(br.data);
        return tr;
    }
    uint64_t body_n = *(uint64_t*)br.data;
    uint64_t tail_n = *(uint64_t*)tr.data;
    free(br.data);
    free(tr.data);
    return z_io_u64_ok(body_n + tail_n);
}

