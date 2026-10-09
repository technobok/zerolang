static z_@optionval_u64@_t z_@StringView@_indexOf(const z_@StringView@_t* self, const z_@StringView@_t* needle);
static z_@optionval_u64@_t z_@StringView@_indexOf(const z_@StringView@_t* self, const z_@StringView@_t* needle) {
    z_@optionval_u64@_t out = {0};
    uint64_t r = z_@StringView@_indexOf_raw(self, needle);
    if (r == UINT64_MAX) {
        out.tag = @ARM_optionval_u64.none@;
        return out;
    }
    out.tag = @ARM_optionval_u64.some@;
    out.data.@MEMBER_optionval_u64.some@ = r;
    return out;
}


