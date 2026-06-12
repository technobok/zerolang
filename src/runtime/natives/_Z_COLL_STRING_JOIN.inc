/* string_join -- borrow `parts` (read-only) and return a new
   owned string. `parts` is pointer-passed (Phase A default for
   stack-reftype params); caller retains ownership. */
static z_String_t z_stringJoin(
    z_List_String_t* parts, z_StringView_t sep);
static z_String_t z_stringJoin(
    z_List_String_t* parts, z_StringView_t sep) {
    z_String_t out = {0};
    uint64_t n = parts->length;
    if (n == 0) {
        out.capacity = 1;
        out.data = (char*)z_xmalloc(1);
        out.data[0] = '\0';
        return out;
    }
    uint64_t total = sep.length * (n - 1);
    for (uint64_t i = 0; i < n; i++) {
        total += parts->data[i].size;
    }
    out.size = total;
    out.capacity = total + 1;
    out.data = (char*)z_xmalloc(out.capacity + 1);
    uint64_t pos = 0;
    for (uint64_t i = 0; i < n; i++) {
        if (i > 0 && sep.length > 0) {
            memcpy(out.data + pos, sep.data, sep.length);
            pos += sep.length;
        }
        memcpy(out.data + pos, parts->data[i].data, parts->data[i].size);
        pos += parts->data[i].size;
    }
    out.data[total] = '\0';
    return out;
}

