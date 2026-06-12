static z_optionval_u64_t z_StringView_lastIndexOf(const z_StringView_t* self, const z_StringView_t* needle);
static z_optionval_u64_t z_StringView_lastIndexOf(const z_StringView_t* self, const z_StringView_t* needle) {
    z_optionval_u64_t out = {0};
    if (needle->length == 0) {
        out.tag = Z_OPTIONVAL_U64_TAG_SOME;
        out.data.some = self->length;
        return out;
    }
    if (needle->length > self->length) {
        out.tag = Z_OPTIONVAL_U64_TAG_NONE;
        return out;
    }
    for (uint64_t i = self->length - needle->length + 1; i > 0; i--) {
        uint64_t idx = i - 1;
        if (memcmp(self->data + idx, needle->data, needle->length) == 0) {
            out.tag = Z_OPTIONVAL_U64_TAG_SOME;
            out.data.some = idx;
            return out;
        }
    }
    out.tag = Z_OPTIONVAL_U64_TAG_NONE;
    return out;
}

