/* bufwriter.flush — drain the backing buffer to the sink via its
   writer vtable. On ok, the buffer length is reset to 0. On err,
   the underlying ioerror is transferred into the returned
   result(null, ioerror) so callers see exactly one error. */
static z_Result_null_IoError_t z_BufWriter_flush(z_BufWriter_t* self);
static z_Result_null_IoError_t z_BufWriter_flush(z_BufWriter_t* self) {
    z_Result_null_IoError_t result = {0};
    if (self->buf.length == 0) {
        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
        result.data = NULL;
        return result;
    }
    z_ListView_u8_t view = { self->buf.length, self->buf.data };
    z_Result_u64_IoError_t wr =
        self->sink.vtable->write(self->sink.data, &view);
    if (wr.tag == Z_RESULT_U64_IOERROR_TAG_OK) {
        free(wr.data);
        self->buf.length = 0;
        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
        result.data = NULL;
        return result;
    }
    /* err: transfer ioerror box into result(null, ioerror) */
    result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
    result.data = wr.data;
    return result;
}

