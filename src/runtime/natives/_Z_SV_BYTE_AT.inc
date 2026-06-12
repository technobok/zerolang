static z_optionval_u8_t z_StringView_byteAt(const z_StringView_t* self, uint64_t i);
static z_optionval_u8_t z_StringView_byteAt(const z_StringView_t* self, uint64_t i) {
    z_optionval_u8_t out = {0};
    if (i >= self->length) {
        out.tag = Z_OPTIONVAL_U8_TAG_NONE;
        return out;
    }
    out.tag = Z_OPTIONVAL_U8_TAG_SOME;
    out.data.some = (uint8_t)self->data[i];
    return out;
}

