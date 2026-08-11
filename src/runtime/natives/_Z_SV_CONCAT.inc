static z_@String@_t z_@StringView@_concat(
    const z_@StringView@_t* self, const z_@StringView@_t* other);
static z_@String@_t z_@StringView@_concat(
    const z_@StringView@_t* self, const z_@StringView@_t* other) {
    z_@String@_t z = {0};
    uint64_t total = self->size + other->size;
    z.size = total;
    z.capacity = total + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    memcpy(z.data, self->data, self->size);
    memcpy(z.data + self->size, other->data, other->size);
    z.data[total] = '\0';
    return z;
}

