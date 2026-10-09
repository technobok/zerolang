static z_@resultval_u64_parseerror@_t z_@StringView@_parseU64(const z_@StringView@_t* self);
static z_@resultval_u64_parseerror@_t z_@StringView@_parseU64(const z_@StringView@_t* self) {
    z_@resultval_u64_parseerror@_t out = {0};
    if (self->size == 0) {
        out.tag = @ARM_resultval_u64_parseerror.err@;
        out.data.@MEMBER_resultval_u64_parseerror.err@@TAG_parseerror@ = @ARM_parseerror.empty@;
        return out;
    }
    uint64_t acc = 0;
    for (uint64_t i = 0; i < self->size; i++) {
        unsigned char c = (unsigned char)self->data[i];
        if (c < '0' || c > '9') {
            out.tag = @ARM_resultval_u64_parseerror.err@;
            out.data.@MEMBER_resultval_u64_parseerror.err@@TAG_parseerror@ = @ARM_parseerror.invalidDigit@;
            return out;
        }
        uint64_t d = (uint64_t)(c - '0');
        if (acc > (UINT64_MAX - d) / 10) {
            out.tag = @ARM_resultval_u64_parseerror.err@;
            out.data.@MEMBER_resultval_u64_parseerror.err@@TAG_parseerror@ = @ARM_parseerror.overflow@;
            return out;
        }
        acc = acc * 10 + d;
    }
    out.tag = @ARM_resultval_u64_parseerror.ok@;
    out.data.@MEMBER_resultval_u64_parseerror.ok@ = acc;
    return out;
}


