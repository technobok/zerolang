static z_String_t z_StringView_toUpperAscii(const z_StringView_t* self);
static z_String_t z_StringView_toUpperAscii(const z_StringView_t* self) {
    z_String_t z = {0};
    z.size = self->size;
    z.capacity = self->size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    for (uint64_t i = 0; i < self->size; i++) {
        unsigned char c = (unsigned char)self->data[i];
        if (c >= 'a' && c <= 'z') c = (unsigned char)(c - ('a' - 'A'));
        z.data[i] = (char)c;
    }
    z.data[self->size] = '\0';
    return z;
}

