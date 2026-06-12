static z_Splitter_t z_StringView_split(
    const z_StringView_t* self, const z_StringView_t* sep);
static z_Splitter_t z_StringView_split(
    const z_StringView_t* self, const z_StringView_t* sep) {
    z_Splitter_t s;
    s.src = self->data;
    s.src_len = self->length;
    s.sep = sep->data;
    s.sep_len = sep->length;
    s.cursor = 0;
    s.done = (sep->length == 0);
    return s;
}

