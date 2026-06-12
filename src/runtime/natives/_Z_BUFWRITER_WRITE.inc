/* bufwriter.write — append `from` to the backing buffer. If the
   combined length would exceed `cap`, flush first (propagating
   any write error). Chunks larger than `cap` bypass the buffer
   and go straight to the sink. */
static z_Result_u64_IoError_t z_BufWriter_write(
    z_BufWriter_t* self, z_ListView_u8_t* from
);
static z_Result_u64_IoError_t z_BufWriter_write(
    z_BufWriter_t* self, z_ListView_u8_t* from
) {
    if (self->buf.length + from->length > self->cap) {
        z_Result_null_IoError_t fr = z_BufWriter_flush(self);
        if (fr.tag != Z_RESULT_NULL_IOERROR_TAG_OK) {
            z_Result_u64_IoError_t result = {0};
            result.tag = Z_RESULT_U64_IOERROR_TAG_ERR;
            result.data = fr.data;
            return result;
        }
    }
    if (from->length > self->cap) {
        /* oversize write: bypass the buffer */
        return self->sink.vtable->write(self->sink.data, from);
    }
    z_List_u8_grow(&self->buf, self->buf.length + from->length);
    memcpy(self->buf.data + self->buf.length, from->data, from->length);
    self->buf.length += from->length;
    return z_io_u64_ok(from->length);
}

