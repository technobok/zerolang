static z_resultval_u64_parseerror_t z_StringView_parseU64(const z_StringView_t* self);
static z_resultval_u64_parseerror_t z_StringView_parseU64(const z_StringView_t* self) {
    z_resultval_u64_parseerror_t out = {0};
    if (self->length == 0) {
        out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;
        return out;
    }
    uint64_t acc = 0;
    for (uint64_t i = 0; i < self->length; i++) {
        unsigned char c = (unsigned char)self->data[i];
        if (c < '0' || c > '9') {
            out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_ERR;
            out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;
            return out;
        }
        uint64_t d = (uint64_t)(c - '0');
        if (acc > (UINT64_MAX - d) / 10) {
            out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_ERR;
            out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;
            return out;
        }
        acc = acc * 10 + d;
    }
    out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_OK;
    out.data.ok = acc;
    return out;
}

