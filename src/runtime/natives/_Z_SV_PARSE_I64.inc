static z_resultval_i64_parseerror_t z_StringView_parseI64(const z_StringView_t* self);
static z_resultval_i64_parseerror_t z_StringView_parseI64(const z_StringView_t* self) {
    z_resultval_i64_parseerror_t out = {0};
    uint64_t i = 0;
    int neg = 0;
    if (self->length == 0) goto empty;
    if (self->data[i] == '+' || self->data[i] == '-') {
        if (self->data[i] == '-') neg = 1;
        i++;
    }
    if (i >= self->length) goto empty;
    uint64_t acc = 0;
    for (; i < self->length; i++) {
        unsigned char c = (unsigned char)self->data[i];
        if (c < '0' || c > '9') goto invalid;
        uint64_t d = (uint64_t)(c - '0');
        if (acc > (UINT64_MAX - d) / 10) goto overflow;
        acc = acc * 10 + d;
    }
    /* range check: fits in i64 */
    uint64_t bound = neg ? (uint64_t)1 << 63 : ((uint64_t)1 << 63) - 1;
    if (acc > bound) goto overflow;
    int64_t v = neg ? -(int64_t)acc : (int64_t)acc;
    if (neg && acc == ((uint64_t)1 << 63)) v = INT64_MIN;
    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_OK;
    out.data.ok = v;
    return out;
empty:
    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_ERR;
    out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;
    return out;
invalid:
    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_ERR;
    out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;
    return out;
overflow:
    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_ERR;
    out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;
    return out;
}

