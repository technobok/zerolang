static z_Option_StringView_t z_StringView_stripPrefix(
    const z_StringView_t* self, const z_StringView_t* p);
static z_Option_StringView_t z_StringView_stripPrefix(
    const z_StringView_t* self, const z_StringView_t* p) {
    z_Option_StringView_t out = {0};
    if (p->length > self->length
        || (p->length > 0 && memcmp(self->data, p->data, p->length) != 0)) {
        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;
        return out;
    }
    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));
    boxed->data = self->data + p->length;
    boxed->length = self->length - p->length;
    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;
    out.data = boxed;
    return out;
}

