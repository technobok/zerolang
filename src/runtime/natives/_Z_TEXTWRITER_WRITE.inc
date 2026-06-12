/* textwriter.write -- forward the stringview's bytes to the
   underlying bufwriter. Stringviews are UTF-8 by construction
   (the compiler's string/stringview types enforce it at their
   ingress points), so no validation is performed here. */
static z_Result_u64_IoError_t z_TextWriter_write(
    z_TextWriter_t* self, const z_StringView_t* from
);
static z_Result_u64_IoError_t z_TextWriter_write(
    z_TextWriter_t* self, const z_StringView_t* from
) {
    z_ListView_u8_t view = { from->length, (uint8_t*)from->data };
    return z_BufWriter_write(self->sink, &view);
}

