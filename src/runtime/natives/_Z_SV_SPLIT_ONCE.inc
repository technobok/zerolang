static z_@optionval_u64@_t z_@StringView@_splitOnce(
    const z_@StringView@_t* self, const z_@StringView@_t* sep);
static z_@optionval_u64@_t z_@StringView@_splitOnce(
    const z_@StringView@_t* self, const z_@StringView@_t* sep) {
    z_@optionval_u64@_t out = {0};
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

