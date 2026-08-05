static z_StringView_t z_StringView_trim(const z_StringView_t* self);
static z_StringView_t z_StringView_trim(const z_StringView_t* self) {
    const char* p = self->data;
    const char* end = self->data + self->size;
    while (p < end && z_ascii_is_ws((unsigned char)*p)) p++;
    while (end > p && z_ascii_is_ws((unsigned char)end[-1])) end--;
    z_StringView_t out = { .size = (uint64_t)(end - p), .data = p };
    return out;
}

