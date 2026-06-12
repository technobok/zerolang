static z_StringView_t z_StringView_trimEnd(const z_StringView_t* self);
static z_StringView_t z_StringView_trimEnd(const z_StringView_t* self) {
    const char* p = self->data;
    const char* end = self->data + self->length;
    while (end > p && z_ascii_is_ws((unsigned char)end[-1])) end--;
    z_StringView_t out = { p, (uint64_t)(end - p) };
    return out;
}

