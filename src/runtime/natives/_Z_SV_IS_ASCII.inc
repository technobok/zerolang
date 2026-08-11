static bool z_@StringView@_isAscii(const z_@StringView@_t* self);
static bool z_@StringView@_isAscii(const z_@StringView@_t* self) {
    for (uint64_t i = 0; i < self->size; i++) {
        if ((unsigned char)self->data[i] >= 0x80) return false;
    }
    return true;
}

