/* textwriter.write_line -- emit the stringview followed by an
   LF ('\n'). Two sink writes: content then newline. Returns the
   total byte count on ok; on a partial-write err from the
   newline, the content is still considered written (its count
   would have been reported by a plain `write` call) so the
   caller can distinguish 'nothing landed' from 'content landed,
   newline did not' by inspecting the ok count. Errors from the
   content write short-circuit. */
static z_@ResultVR_u64_IoError@_t z_@TextWriter@_writeLine(
    z_@TextWriter@_t* self, const z_@StringView@_t* from
);
static z_@ResultVR_u64_IoError@_t z_@TextWriter@_writeLine(
    z_@TextWriter@_t* self, const z_@StringView@_t* from
) {
    z_@ListViewVal_u8@_t body = { from->size, (uint8_t*)from->data };
    z_@ResultVR_u64_IoError@_t br = z_@BufWriter@_write(self->sink, &body);
    if (br.tag != @ARM_ResultVR_u64_IoError.ok@) return br;
    uint8_t nl = (uint8_t)'\n';
    z_@ListViewVal_u8@_t tail = { (uint64_t)1, &nl };
    z_@ResultVR_u64_IoError@_t tr = z_@BufWriter@_write(self->sink, &tail);
    if (tr.tag != @ARM_ResultVR_u64_IoError.ok@) return tr;
    return z_io_u64_ok(br.in_ok + tr.in_ok);
}


