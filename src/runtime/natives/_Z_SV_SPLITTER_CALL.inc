static z_@Option_StringView@_t z_@Splitter@_call(z_@Splitter@_t* s);
static z_@Option_StringView@_t z_@Splitter@_call(z_@Splitter@_t* s) {
    z_@Option_StringView@_t out = {0};
    if (s->done) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    const char* start = s->src + s->cursor;
    uint64_t remaining = s->src_len - s->cursor;
    if (s->sep_len > remaining) {
        /* no more separators possible — return the tail */
        z_@StringView@_t* boxed = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
        boxed->data = start;
        boxed->size = remaining;
        s->cursor = s->src_len;
        s->done = true;
        out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
        out.data = boxed;
        return out;
    }
    uint64_t scan_end = remaining - s->sep_len;
    for (uint64_t i = 0; i <= scan_end; i++) {
        if (memcmp(start + i, s->sep, s->sep_len) == 0) {
            z_@StringView@_t* boxed = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
            boxed->data = start;
            boxed->size = i;
            s->cursor += i + s->sep_len;
            out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
            out.data = boxed;
            return out;
        }
    }
    /* no separator in remaining — final fragment */
    z_@StringView@_t* boxed = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
    boxed->data = start;
    boxed->size = remaining;
    s->cursor = s->src_len;
    s->done = true;
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}

