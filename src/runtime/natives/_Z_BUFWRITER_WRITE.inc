/* bufwriter.write — append `from` to the backing buffer. If the
   combined length would exceed `cap`, flush first (propagating
   any write error). Chunks larger than `cap` bypass the buffer
   and go straight to the sink. */
static z_@Result_u64_IoError@_t z_@BufWriter@_write(
    z_@BufWriter@_t* self, z_@ListViewVal_u8@_t* from
);
static z_@Result_u64_IoError@_t z_@BufWriter@_write(
    z_@BufWriter@_t* self, z_@ListViewVal_u8@_t* from
) {
    if (self->buf.length + from->length > self->cap) {
        z_@Result_null_IoError@_t fr = z_@BufWriter@_flush(self);
        if (fr.tag != Z_RESULT_NULL_IOERROR_TAG_OK) {
            z_@Result_u64_IoError@_t result = {0};
            result.tag = Z_RESULT_BOX_U64_IOERROR_TAG_ERR;
            result.data = fr.data;
            return result;
        }
    }
    if (from->length > self->cap) {
        /* oversize write: bypass the buffer */
        return self->sink.vtable->write(self->sink.data, from);
    }
    z_@ListVal_u8@_grow(&self->buf, self->buf.length + from->length);
    memcpy(self->buf.data + self->buf.length, from->data, from->length);
    self->buf.length += from->length;
    return z_io_u64_ok(from->length);
}

