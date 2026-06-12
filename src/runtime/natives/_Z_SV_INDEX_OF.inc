static z_optionval_u64_t z_StringView_indexOf(const z_StringView_t* self, const z_StringView_t* needle);
static z_optionval_u64_t z_StringView_indexOf(const z_StringView_t* self, const z_StringView_t* needle) {
    z_optionval_u64_t out = {0};
    uint64_t r = z_StringView_indexOf_raw(self, needle);
    if (r == UINT64_MAX) {
        out.tag = Z_OPTIONVAL_U64_TAG_NONE;
        return out;
    }
    out.tag = Z_OPTIONVAL_U64_TAG_SOME;
    out.data.some = r;
    return out;
}

