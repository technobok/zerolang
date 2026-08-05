static z_String_t z_StringView_concat(
    const z_StringView_t* self, const z_StringView_t* other);
static z_String_t z_StringView_concat(
    const z_StringView_t* self, const z_StringView_t* other) {
    z_String_t z = {0};
    uint64_t total = self->size + other->size;
    z.size = total;
    z.capacity = total + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    memcpy(z.data, self->data, self->size);
    memcpy(z.data + self->size, other->data, other->size);
    z.data[total] = '\0';
    return z;
}

