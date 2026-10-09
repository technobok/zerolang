static z_@resultval_i64_parseerror@_t z_@StringView@_parseI64(const z_@StringView@_t* self);
static z_@resultval_i64_parseerror@_t z_@StringView@_parseI64(const z_@StringView@_t* self) {
    z_@resultval_i64_parseerror@_t out = {0};
    uint64_t i = 0;
    int neg = 0;
    if (self->size == 0) goto empty;
    if (self->data[i] == '+' || self->data[i] == '-') {
        if (self->data[i] == '-') neg = 1;
        i++;
    }
    if (i >= self->size) goto empty;
    uint64_t acc = 0;
    for (; i < self->size; i++) {
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
    out.tag = @ARM_resultval_i64_parseerror.ok@;
    out.data.@MEMBER_resultval_i64_parseerror.ok@ = v;
    return out;
empty:
    out.tag = @ARM_resultval_i64_parseerror.err@;
    out.data.@MEMBER_resultval_i64_parseerror.err@@TAG_parseerror@ = @ARM_parseerror.empty@;
    return out;
invalid:
    out.tag = @ARM_resultval_i64_parseerror.err@;
    out.data.@MEMBER_resultval_i64_parseerror.err@@TAG_parseerror@ = @ARM_parseerror.invalidDigit@;
    return out;
overflow:
    out.tag = @ARM_resultval_i64_parseerror.err@;
    out.data.@MEMBER_resultval_i64_parseerror.err@@TAG_parseerror@ = @ARM_parseerror.overflow@;
    return out;
}


