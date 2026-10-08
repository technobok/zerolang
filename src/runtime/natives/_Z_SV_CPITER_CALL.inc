static z_@optionval_u32@_t z_@CpIter@_call(z_@CpIter@_t* it);
static z_@optionval_u32@_t z_@CpIter@_call(z_@CpIter@_t* it) {
    z_@optionval_u32@_t out = {0};
    if (it->cursor >= it->length) {
        out.tag = @ARM_optionval_u32.none@;
        return out;
    }
    uint32_t cp = z_utf8_decode_one(it->data, it->length, &it->cursor);
    out.tag = @ARM_optionval_u32.some@;
    out.data.some = cp;
    return out;
}


