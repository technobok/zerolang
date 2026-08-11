/* textwriter.write -- forward the stringview's bytes to the
   underlying bufwriter. Stringviews are UTF-8 by construction
   (the compiler's string/stringview types enforce it at their
   ingress points), so no validation is performed here. */
static z_@Result_u64_IoError@_t z_@TextWriter@_write(
    z_@TextWriter@_t* self, const z_@StringView@_t* from
);
static z_@Result_u64_IoError@_t z_@TextWriter@_write(
    z_@TextWriter@_t* self, const z_@StringView@_t* from
) {
    z_@ListViewVal_u8@_t view = { from->size, (uint8_t*)from->data };
    return z_@BufWriter@_write(self->sink, &view);
}

