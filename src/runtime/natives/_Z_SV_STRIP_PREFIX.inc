static z_@Option_StringView@_t z_@StringView@_stripPrefix(
    const z_@StringView@_t* self, const z_@StringView@_t* p);
static z_@Option_StringView@_t z_@StringView@_stripPrefix(
    const z_@StringView@_t* self, const z_@StringView@_t* p) {
    z_@Option_StringView@_t out = {0};
    if (p->size > self->size
        || (p->size > 0 && memcmp(self->data, p->data, p->size) != 0)) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    z_@StringView@_t* boxed = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
    boxed->data = self->data + p->size;
    boxed->size = self->size - p->size;
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}


