static z_@resultval_f128_parseerror@_t z_@StringView@_parseF128(const z_@StringView@_t* self);
static z_@resultval_f128_parseerror@_t z_@StringView@_parseF128(const z_@StringView@_t* self) {
    z_@resultval_f128_parseerror@_t out = {0};
    if (self->size == 0) {
        out.tag = Z_RESULTVAL_F128_PARSEERROR_TAG_ERR;
        out.data.err@TAG_parseerror@ = Z_PARSEERROR_TAG_EMPTY;
        return out;
    }
    char buf[64];
    char* p = (self->size < sizeof(buf)) ? buf
            : (char*)z_xmalloc(self->size + 1);
    memcpy(p, self->data, self->size);
    p[self->size] = '\0';
    char* end = NULL;
    errno = 0;
    __float128 v = strtoflt128(p, &end);
    int err = errno;
    uint64_t consumed = (uint64_t)(end - p);
    if (p != buf) free(p);
    if (consumed == 0 || consumed != self->size) {
        out.tag = Z_RESULTVAL_F128_PARSEERROR_TAG_ERR;
        out.data.err@TAG_parseerror@ = Z_PARSEERROR_TAG_INVALIDDIGIT;
        return out;
    }
    if (err == ERANGE) {
        out.tag = Z_RESULTVAL_F128_PARSEERROR_TAG_ERR;
        out.data.err@TAG_parseerror@ = Z_PARSEERROR_TAG_OVERFLOW;
        return out;
    }
    out.tag = Z_RESULTVAL_F128_PARSEERROR_TAG_OK;
    out.data.ok = v;
    return out;
}

