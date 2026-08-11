static z_@StringView@_t z_@StringView@_trimEnd(const z_@StringView@_t* self);
static z_@StringView@_t z_@StringView@_trimEnd(const z_@StringView@_t* self) {
    const char* p = self->data;
    const char* end = self->data + self->size;
    while (end > p && z_ascii_is_ws((unsigned char)end[-1])) end--;
    z_@StringView@_t out = { .size = (uint64_t)(end - p), .data = p };
    return out;
}

