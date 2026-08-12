static z_@optionval_u64@_t z_@StringView@_indexOf(const z_@StringView@_t* self, const z_@StringView@_t* needle);
static z_@optionval_u64@_t z_@StringView@_indexOf(const z_@StringView@_t* self, const z_@StringView@_t* needle) {
    z_@optionval_u64@_t out = {0};
    uint64_t r = z_@StringView@_indexOf_raw(self, needle);
    if (r == UINT64_MAX) {
        out.tag = Z_OPTIONVAL_U64_TAG_NONE;
        return out;
    }
    out.tag = Z_OPTIONVAL_U64_TAG_SOME;
    out.data.some = r;
    return out;
}


