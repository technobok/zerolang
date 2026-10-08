/* bufwriter.flush — drain the backing buffer to the sink via its
   writer vtable. On ok, the buffer length is reset to 0. On err,
   the underlying ioerror is transferred into the returned
   result(null, ioerror) so callers see exactly one error. */
static z_@Result_null_IoError@_t z_@BufWriter@_flush(z_@BufWriter@_t* self);
static z_@Result_null_IoError@_t z_@BufWriter@_flush(z_@BufWriter@_t* self) {
    z_@Result_null_IoError@_t result = {0};
    if (self->buf.length == 0) {
        result.tag = @ARM_Result_null_IoError.ok@;
        result.data = NULL;
        return result;
    }
    z_@ListViewVal_u8@_t view = { self->buf.length, self->buf.data };
    z_@ResultVR_u64_IoError@_t wr =
        self->sink.vtable->write(self->sink.data, &view);
    if (wr.tag == @ARM_ResultVR_u64_IoError.ok@) {
        self->buf.length = 0;
        result.tag = @ARM_Result_null_IoError.ok@;
        result.data = NULL;
        return result;
    }
    /* err: transfer ioerror box into result(null, ioerror) */
    result.tag = @ARM_Result_null_IoError.err@;
    result.data = wr.data;
    return result;
}


