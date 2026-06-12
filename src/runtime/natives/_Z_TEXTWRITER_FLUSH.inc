/* textwriter.flush -- delegate to the bufwriter's flush. The
   textwriter has no buffer of its own to drain. */
static z_Result_null_IoError_t z_TextWriter_flush(z_TextWriter_t* self);
static z_Result_null_IoError_t z_TextWriter_flush(z_TextWriter_t* self) {
    return z_BufWriter_flush(self->sink);
}

