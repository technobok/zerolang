static z_Splitter_t z_StringView_split(
    const z_StringView_t* self, const z_StringView_t* sep);
static z_Splitter_t z_StringView_split(
    const z_StringView_t* self, const z_StringView_t* sep) {
    z_Splitter_t s;
    s.src = self->data;
    s.src_len = self->size;
    s.sep = sep->data;
    s.sep_len = sep->size;
    s.cursor = 0;
    s.done = (sep->size == 0);
    return s;
}

