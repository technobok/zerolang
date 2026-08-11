static z_@optionval_u8@_t z_@StringView@_byteAt(const z_@StringView@_t* self, uint64_t i);
static z_@optionval_u8@_t z_@StringView@_byteAt(const z_@StringView@_t* self, uint64_t i) {
    z_@optionval_u8@_t out = {0};
    if (i >= self->size) {
        out.tag = Z_OPTIONVAL_U8_TAG_NONE;
        return out;
    }
    out.tag = Z_OPTIONVAL_U8_TAG_SOME;
    out.data.some = (uint8_t)self->data[i];
    return out;
}

