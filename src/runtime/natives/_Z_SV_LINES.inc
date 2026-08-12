static z_@LinesIter@_t z_@StringView@_lines(const z_@StringView@_t* self);
static z_@LinesIter@_t z_@StringView@_lines(const z_@StringView@_t* self) {
    z_@LinesIter@_t s;
    s.src = self->data;
    s.src_len = self->size;
    s.sep = NULL;
    s.sep_len = 0;
    s.cursor = 0;
    s.done = (self->size == 0);
    return s;
}


