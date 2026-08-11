static z_@optionval_u64@_t z_@StringView@_lastIndexOf(const z_@StringView@_t* self, const z_@StringView@_t* needle);
static z_@optionval_u64@_t z_@StringView@_lastIndexOf(const z_@StringView@_t* self, const z_@StringView@_t* needle) {
    z_@optionval_u64@_t out = {0};
    if (needle->size == 0) {
        out.tag = Z_OPTIONVAL_U64_TAG_SOME;
        out.data.some = self->size;
        return out;
    }
    if (needle->size > self->size) {
        out.tag = Z_OPTIONVAL_U64_TAG_NONE;
        return out;
    }
    for (uint64_t i = self->size - needle->size + 1; i > 0; i--) {
        uint64_t idx = i - 1;
        if (memcmp(self->data + idx, needle->data, needle->size) == 0) {
            out.tag = Z_OPTIONVAL_U64_TAG_SOME;
            out.data.some = idx;
            return out;
        }
    }
    out.tag = Z_OPTIONVAL_U64_TAG_NONE;
    return out;
}

