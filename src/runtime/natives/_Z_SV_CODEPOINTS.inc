static z_CpIter_t z_StringView_codepoints(const z_StringView_t* self);
static z_CpIter_t z_StringView_codepoints(const z_StringView_t* self) {
    z_CpIter_t it;
    it.data = self->data;
    it.length = self->length;
    it.cursor = 0;
    return it;
}

