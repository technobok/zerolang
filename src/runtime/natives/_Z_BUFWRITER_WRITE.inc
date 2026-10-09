/* bufwriter.write — append `from` to the backing buffer. If the
   combined length would exceed `cap`, flush first (propagating
   any write error). Chunks larger than `cap` bypass the buffer
   and go straight to the sink. */
static z_@ResultVR_u64_IoError@_t z_@BufWriter@_write(
    z_@BufWriter@_t* self, z_@ListViewVal_u8@_t* from
);
static z_@ResultVR_u64_IoError@_t z_@BufWriter@_write(
    z_@BufWriter@_t* self, z_@ListViewVal_u8@_t* from
) {
    if (self->@MEMBER_BufWriter.buf@.length + from->length > self->@MEMBER_BufWriter.cap@) {
        z_@Result_null_IoError@_t fr = z_@BufWriter@_flush(self);
        if (fr.tag != @ARM_Result_null_IoError.ok@) {
            z_@ResultVR_u64_IoError@_t result = {0};
            result.tag = @ARM_ResultVR_u64_IoError.err@;
            result.data = fr.data;
            return result;
        }
    }
    if (from->length > self->@MEMBER_BufWriter.cap@) {
        /* oversize write: bypass the buffer */
        return self->@MEMBER_BufWriter.sink@.vtable->@MEMBER_Writer.write@(self->@MEMBER_BufWriter.sink@.data, from);
    }
    z_@ListVal_u8@_grow(&self->@MEMBER_BufWriter.buf@, self->@MEMBER_BufWriter.buf@.length + from->length);
    memcpy(self->@MEMBER_BufWriter.buf@.data + self->@MEMBER_BufWriter.buf@.length, from->data, from->length);
    self->@MEMBER_BufWriter.buf@.length += from->length;
    return z_io_u64_ok(from->length);
}


