static z_@optionval_u8@_t z_@StringView@_byteAt(const z_@StringView@_t* self, uint64_t i);
static z_@optionval_u8@_t z_@StringView@_byteAt(const z_@StringView@_t* self, uint64_t i) {
    z_@optionval_u8@_t out = {0};
    if (i >= self->size) {
        out.tag = @ARM_optionval_u8.none@;
        return out;
    }
    out.tag = @ARM_optionval_u8.some@;
    out.data.some = (uint8_t)self->data[i];
    return out;
}


