static z_@String@_t z_@StringView@_toLowerAscii(const z_@StringView@_t* self);
static z_@String@_t z_@StringView@_toLowerAscii(const z_@StringView@_t* self) {
    z_@String@_t z = {0};
    z.size = self->size;
    z.capacity = self->size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    for (uint64_t i = 0; i < self->size; i++) {
        unsigned char c = (unsigned char)self->data[i];
        if (c >= 'A' && c <= 'Z') c = (unsigned char)(c + ('a' - 'A'));
        z.data[i] = (char)c;
    }
    z.data[self->size] = '\0';
    return z;
}

