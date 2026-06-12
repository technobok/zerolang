static uint64_t z_StringView_indexOf_raw(const z_StringView_t* self, const z_StringView_t* needle);
static uint64_t z_StringView_indexOf_raw(const z_StringView_t* self, const z_StringView_t* needle) {
    if (needle->length == 0) return 0;
    if (needle->length > self->length) return UINT64_MAX;
    uint64_t last = self->length - needle->length;
    for (uint64_t i = 0; i <= last; i++) {
        if (memcmp(self->data + i, needle->data, needle->length) == 0) {
            return i;
        }
    }
    return UINT64_MAX;
}

