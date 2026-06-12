static uint64_t z_StringView_count(const z_StringView_t* self);
static uint64_t z_StringView_count(const z_StringView_t* self) {
    uint64_t i = 0, n = 0;
    while (i < self->length) {
        (void)z_utf8_decode_one(self->data, self->length, &i);
        n++;
    }
    return n;
}

