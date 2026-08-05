static z_optionval_u64_t z_StringView_splitOnce(
    const z_StringView_t* self, const z_StringView_t* sep);
static z_optionval_u64_t z_StringView_splitOnce(
    const z_StringView_t* self, const z_StringView_t* sep) {
    z_optionval_u64_t out = {0};
    if (sep->size == 0) {
        out.tag = Z_OPTIONVAL_U64_TAG_SOME;
        out.data.some = 0;
        return out;
    }
    if (sep->size > self->size) {
        out.tag = Z_OPTIONVAL_U64_TAG_NONE;
        return out;
    }
    uint64_t last = self->size - sep->size;
    for (uint64_t i = 0; i <= last; i++) {
        if (memcmp(self->data + i, sep->data, sep->size) == 0) {
            out.tag = Z_OPTIONVAL_U64_TAG_SOME;
            out.data.some = i;
            return out;
        }
    }
    out.tag = Z_OPTIONVAL_U64_TAG_NONE;
    return out;
}

