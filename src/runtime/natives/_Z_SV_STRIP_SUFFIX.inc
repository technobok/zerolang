static z_@Option_StringView@_t z_@StringView@_stripSuffix(
    const z_@StringView@_t* self, const z_@StringView@_t* s);
static z_@Option_StringView@_t z_@StringView@_stripSuffix(
    const z_@StringView@_t* self, const z_@StringView@_t* s) {
    z_@Option_StringView@_t out = {0};
    if (s->size > self->size
        || (s->size > 0
            && memcmp(self->data + (self->size - s->size),
                      s->data, s->size) != 0)) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    z_@StringView@_t* boxed = (z_@StringView@_t*)z_xmalloc(sizeof(z_@StringView@_t));
    boxed->data = self->data;
    boxed->size = self->size - s->size;
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}

