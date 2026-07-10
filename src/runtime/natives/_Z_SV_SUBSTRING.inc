static z_StringView_t z_StringView_substring(const z_StringView_t* self, uint64_t from, uint64_t to);
static z_StringView_t z_StringView_substring(const z_StringView_t* self, uint64_t from, uint64_t to) {
    if (from > self->length || to > self->length || from > to) z_panic("substring: bounds error");
    return (z_StringView_t){ self->data + from, to - from };
}
