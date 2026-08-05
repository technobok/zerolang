static z_String_t z_StringView_replace_impl(
    const z_StringView_t* self, const z_StringView_t* needle,
    const z_StringView_t* repl, bool once);
static z_String_t z_StringView_replace_impl(
    const z_StringView_t* self, const z_StringView_t* needle,
    const z_StringView_t* repl, bool once) {
    /* empty needle: just copy self */
    if (needle->size == 0) {
        z_String_t z = {0};
        z.size = self->size;
        z.capacity = self->size + 1;
        z.data = (char*)z_xmalloc(z.capacity + 1);
        memcpy(z.data, self->data, self->size);
        z.data[self->size] = '\0';
        return z;
    }
    /* pass 1: count matches so we can size the output once */
    uint64_t matches = 0;
    if (needle->size <= self->size) {
        uint64_t last = self->size - needle->size;
        uint64_t i = 0;
        while (i <= last) {
            if (memcmp(self->data + i, needle->data, needle->size) == 0) {
                matches++;
                i += needle->size;
                if (once) break;
            } else {
                i++;
            }
        }
    }
    uint64_t delta_per =
        (repl->size > needle->size)
            ? (repl->size - needle->size)
            : 0;
    uint64_t shrink_per =
        (needle->size > repl->size)
            ? (needle->size - repl->size)
            : 0;
    uint64_t out_len = self->size + matches * delta_per - matches * shrink_per;
    z_String_t z = {0};
    z.size = out_len;
    z.capacity = out_len + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    /* pass 2: copy with replacement */
    uint64_t src_i = 0, dst_i = 0, done_matches = 0;
    while (src_i < self->size) {
        bool can_match = (done_matches < matches)
                      && (src_i + needle->size <= self->size)
                      && (memcmp(self->data + src_i, needle->data, needle->size) == 0);
        if (can_match) {
            memcpy(z.data + dst_i, repl->data, repl->size);
            dst_i += repl->size;
            src_i += needle->size;
            done_matches++;
        } else {
            z.data[dst_i++] = self->data[src_i++];
        }
    }
    z.data[dst_i] = '\0';
    return z;
}

