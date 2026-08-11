static z_@String@_t z_@StringView@_repeated(const z_@StringView@_t* self, uint64_t n);
static z_@String@_t z_@StringView@_repeated(const z_@StringView@_t* self, uint64_t n) {
    z_@String@_t z = {0};
    uint64_t total = self->size * n;
    z.size = total;
    z.capacity = total + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    for (uint64_t i = 0; i < n; i++) {
        memcpy(z.data + i * self->size, self->data, self->size);
    }
    z.data[total] = '\0';
    return z;
}

