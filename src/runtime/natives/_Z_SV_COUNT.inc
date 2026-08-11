static uint64_t z_@StringView@_count(const z_@StringView@_t* self);
static uint64_t z_@StringView@_count(const z_@StringView@_t* self) {
    uint64_t i = 0, n = 0;
    while (i < self->size) {
        (void)z_utf8_decode_one(self->data, self->size, &i);
        n++;
    }
    return n;
}

