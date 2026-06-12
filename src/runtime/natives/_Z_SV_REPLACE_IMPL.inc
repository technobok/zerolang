static z_String_t z_StringView_replace_impl(
    const z_StringView_t* self, const z_StringView_t* needle,
    const z_StringView_t* repl, bool once);
static z_String_t z_StringView_replace_impl(
    const z_StringView_t* self, const z_StringView_t* needle,
    const z_StringView_t* repl, bool once) {
    /* empty needle: just copy self */
    if (needle->length == 0) {
        z_String_t z = {0};
        z.size = self->length;
        z.capacity = self->length + 1;
        z.data = (char*)z_xmalloc(z.capacity + 1);
        memcpy(z.data, self->data, self->length);
        z.data[self->length] = '\0';
        return z;
    }
    /* pass 1: count matches so we can size the output once */
    uint64_t matches = 0;
    if (needle->length <= self->length) {
        uint64_t last = self->length - needle->length;
        uint64_t i = 0;
        while (i <= last) {
            if (memcmp(self->data + i, needle->data, needle->length) == 0) {
                matches++;
                i += needle->length;
                if (once) break;
            } else {
                i++;
            }
        }
    }
    uint64_t delta_per =
        (repl->length > needle->length)
            ? (repl->length - needle->length)
            : 0;
    uint64_t shrink_per =
        (needle->length > repl->length)
            ? (needle->length - repl->length)
            : 0;
    uint64_t out_len = self->length + matches * delta_per - matches * shrink_per;
    z_String_t z = {0};
    z.size = out_len;
    z.capacity = out_len + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    /* pass 2: copy with replacement */
    uint64_t src_i = 0, dst_i = 0, done_matches = 0;
    while (src_i < self->length) {
        bool can_match = (done_matches < matches)
                      && (src_i + needle->length <= self->length)
                      && (memcmp(self->data + src_i, needle->data, needle->length) == 0);
        if (can_match) {
            memcpy(z.data + dst_i, repl->data, repl->length);
            dst_i += repl->length;
            src_i += needle->length;
            done_matches++;
        } else {
            z.data[dst_i++] = self->data[src_i++];
        }
    }
    z.data[dst_i] = '\0';
    return z;
}

