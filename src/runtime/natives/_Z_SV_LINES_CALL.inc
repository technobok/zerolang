static z_Option_StringView_t z_LinesIter_call(z_LinesIter_t* s);
static z_Option_StringView_t z_LinesIter_call(z_LinesIter_t* s) {
    z_Option_StringView_t out = {0};
    if (s->done) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    const char* start = s->src + s->cursor;
    uint64_t remaining = s->src_len - s->cursor;
    uint64_t i = 0;
    while (i < remaining && start[i] != '\n') i++;
    uint64_t line_len = i;
    if (line_len > 0 && start[line_len - 1] == '\r') line_len--;
    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));
    boxed->data = start;
    boxed->length = line_len;
    if (i == remaining) {
        s->cursor = s->src_len;
        s->done = true;
    } else {
        s->cursor += i + 1;
        if (s->cursor >= s->src_len) s->done = true;
    }
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}

