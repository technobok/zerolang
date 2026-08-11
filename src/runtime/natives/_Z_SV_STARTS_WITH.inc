static bool z_@StringView@_startsWith(const z_@StringView@_t* self, const z_@StringView@_t* prefix);
static bool z_@StringView@_startsWith(const z_@StringView@_t* self, const z_@StringView@_t* prefix) {
    if (prefix->size > self->size) return false;
    if (prefix->size == 0) return true;
    return memcmp(self->data, prefix->data, prefix->size) == 0;
}

