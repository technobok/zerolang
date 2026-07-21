static z_resultval_f16_parseerror_t z_StringView_parseF16(const z_StringView_t* self);
static z_resultval_f16_parseerror_t z_StringView_parseF16(const z_StringView_t* self) {
    z_resultval_f16_parseerror_t out = {0};
    if (self->length == 0) {
        out.tag = Z_RESULTVAL_F16_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;
        return out;
    }
    char buf[64];
    char* p = (self->length < sizeof(buf)) ? buf
            : (char*)z_xmalloc(self->length + 1);
    memcpy(p, self->data, self->length);
    p[self->length] = '\0';
    char* end = NULL;
    errno = 0;
    float v = strtof(p, &end);
    int err = errno;
    uint64_t consumed = (uint64_t)(end - p);
    if (p != buf) free(p);
    if (consumed == 0 || consumed != self->length) {
        out.tag = Z_RESULTVAL_F16_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;
        return out;
    }
    _Float16 h = (_Float16)v;
    if (err == ERANGE || (!__builtin_isinf(v) && __builtin_isinf((float)h))) {
        out.tag = Z_RESULTVAL_F16_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;
        return out;
    }
    out.tag = Z_RESULTVAL_F16_PARSEERROR_TAG_OK;
    out.data.ok = h;
    return out;
}
