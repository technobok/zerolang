static z_optionval_u32_t z_CpIter_call(z_CpIter_t* it);
static z_optionval_u32_t z_CpIter_call(z_CpIter_t* it) {
    z_optionval_u32_t out = {0};
    if (it->cursor >= it->length) {
        out.tag = Z_OPTIONVAL_U32_TAG_NONE;
        return out;
    }
    uint32_t cp = z_utf8_decode_one(it->data, it->length, &it->cursor);
    out.tag = Z_OPTIONVAL_U32_TAG_SOME;
    out.data.some = cp;
    return out;
}

