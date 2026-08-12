static z_@Option_StringView@_t z_@LinesIter@_call(z_@LinesIter@_t* s);
static z_@Option_StringView@_t z_@LinesIter@_call(z_@LinesIter@_t* s) {
    z_@Option_StringView@_t out = {0};
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
    z_@StringView@_t* boxed = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
    boxed->data = start;
    boxed->size = line_len;
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


