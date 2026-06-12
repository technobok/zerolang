static z_resultval_f64_parseerror_t z_StringView_parseF64(const z_StringView_t* self);
static z_resultval_f64_parseerror_t z_StringView_parseF64(const z_StringView_t* self) {
    z_resultval_f64_parseerror_t out = {0};
    if (self->length == 0) {
        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;
        return out;
    }
    /* copy into a NUL-terminated local buffer for strtod */
    char buf[64];
    char* p = (self->length < sizeof(buf)) ? buf
            : (char*)z_xmalloc(self->length + 1);
    memcpy(p, self->data, self->length);
    p[self->length] = '\0';
    char* end = NULL;
    errno = 0;
    double v = strtod(p, &end);
    int err = errno;
    uint64_t consumed = (uint64_t)(end - p);
    if (p != buf) free(p);
    if (consumed == 0) {
        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;
        return out;
    }
    if (consumed != self->length) {
        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;
        return out;
    }
    if (err == ERANGE) {
        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;
        out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;
        return out;
    }
    out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_OK;
    out.data.ok = v;
    return out;
}

