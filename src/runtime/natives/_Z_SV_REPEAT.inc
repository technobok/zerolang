static z_String_t z_StringView_repeated(const z_StringView_t* self, uint64_t n);
static z_String_t z_StringView_repeated(const z_StringView_t* self, uint64_t n) {
    z_String_t z = {0};
    uint64_t total = self->length * n;
    z.size = total;
    z.capacity = total + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    for (uint64_t i = 0; i < n; i++) {
        memcpy(z.data + i * self->length, self->data, self->length);
    }
    z.data[total] = '\0';
    return z;
}

