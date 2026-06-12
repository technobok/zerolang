static z_Option_StringView_t z_Splitter_call(z_Splitter_t* s);
static z_Option_StringView_t z_Splitter_call(z_Splitter_t* s) {
    z_Option_StringView_t out = {0};
    if (s->done) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    const char* start = s->src + s->cursor;
    uint64_t remaining = s->src_len - s->cursor;
    if (s->sep_len > remaining) {
        /* no more separators possible — return the tail */
        z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));
        boxed->data = start;
        boxed->length = remaining;
        s->cursor = s->src_len;
        s->done = true;
        out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
        out.data = boxed;
        return out;
    }
    uint64_t scan_end = remaining - s->sep_len;
    for (uint64_t i = 0; i <= scan_end; i++) {
        if (memcmp(start + i, s->sep, s->sep_len) == 0) {
            z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));
            boxed->data = start;
            boxed->length = i;
            s->cursor += i + s->sep_len;
            out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
            out.data = boxed;
            return out;
        }
    }
    /* no separator in remaining — final fragment */
    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));
    boxed->data = start;
    boxed->length = remaining;
    s->cursor = s->src_len;
    s->done = true;
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}

