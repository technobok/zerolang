static z_@StringView@_t z_@StringView@_substring(const z_@StringView@_t* self, uint64_t from, uint64_t to);
static z_@StringView@_t z_@StringView@_substring(const z_@StringView@_t* self, uint64_t from, uint64_t to) {
    if (from > self->size || to > self->size || from > to) z_panic("substring: bounds error");
    return (z_@StringView@_t){ .size = to - from, .data = self->data + from };
}
