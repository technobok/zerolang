static z_@Splitter@_t z_@StringView@_split(
    const z_@StringView@_t* self, const z_@StringView@_t* sep);
static z_@Splitter@_t z_@StringView@_split(
    const z_@StringView@_t* self, const z_@StringView@_t* sep) {
    z_@Splitter@_t s;
    s.src = self->data;
    s.src_len = self->size;
    s.sep = sep->data;
    s.sep_len = sep->size;
    s.cursor = 0;
    s.done = (sep->size == 0);
    return s;
}

