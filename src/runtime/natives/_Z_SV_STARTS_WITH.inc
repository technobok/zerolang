static bool z_StringView_startsWith(const z_StringView_t* self, const z_StringView_t* prefix);
static bool z_StringView_startsWith(const z_StringView_t* self, const z_StringView_t* prefix) {
    if (prefix->size > self->size) return false;
    if (prefix->size == 0) return true;
    return memcmp(self->data, prefix->data, prefix->size) == 0;
}

