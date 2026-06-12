static bool z_StringView_isEmpty(const z_StringView_t* self);
static bool z_StringView_isEmpty(const z_StringView_t* self) {
    return self->length == 0;
}

