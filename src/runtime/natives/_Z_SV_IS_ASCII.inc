static bool z_StringView_isAscii(const z_StringView_t* self);
static bool z_StringView_isAscii(const z_StringView_t* self) {
    for (uint64_t i = 0; i < self->length; i++) {
        if ((unsigned char)self->data[i] >= 0x80) return false;
    }
    return true;
}

