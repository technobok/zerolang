/* textwriter.flush -- delegate to the bufwriter's flush. The
   textwriter has no buffer of its own to drain. */
static z_@Result_null_IoError@_t z_@TextWriter@_flush(z_@TextWriter@_t* self);
static z_@Result_null_IoError@_t z_@TextWriter@_flush(z_@TextWriter@_t* self) {
    return z_@BufWriter@_flush(self->sink);
}

