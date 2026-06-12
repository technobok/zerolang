static z_LinesIter_t z_StringView_lines(const z_StringView_t* self);
static z_LinesIter_t z_StringView_lines(const z_StringView_t* self) {
    z_LinesIter_t s;
    s.src = self->data;
    s.src_len = self->length;
    s.sep = NULL;
    s.sep_len = 0;
    s.cursor = 0;
    s.done = (self->length == 0);
    return s;
}

