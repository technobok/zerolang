static z_@CpIter@_t z_@StringView@_codepoints(const z_@StringView@_t* self);
static z_@CpIter@_t z_@StringView@_codepoints(const z_@StringView@_t* self) {
    z_@CpIter@_t it;
    it.data = self->data;
    it.length = self->size;
    it.cursor = 0;
    return it;
}


