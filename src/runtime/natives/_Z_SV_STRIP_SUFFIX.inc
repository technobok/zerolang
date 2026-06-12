static z_Option_StringView_t z_StringView_stripSuffix(
    const z_StringView_t* self, const z_StringView_t* s);
static z_Option_StringView_t z_StringView_stripSuffix(
    const z_StringView_t* self, const z_StringView_t* s) {
    z_Option_StringView_t out = {0};
    if (s->length > self->length
        || (s->length > 0
            && memcmp(self->data + (self->length - s->length),
                      s->data, s->length) != 0)) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));
    boxed->data = self->data;
    boxed->length = self->length - s->length;
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}

