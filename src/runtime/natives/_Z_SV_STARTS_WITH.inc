static bool z_StringView_startsWith(const z_StringView_t* self, const z_StringView_t* prefix);
static bool z_StringView_startsWith(const z_StringView_t* self, const z_StringView_t* prefix) {
    if (prefix->length > self->length) return false;
    if (prefix->length == 0) return true;
    return memcmp(self->data, prefix->data, prefix->length) == 0;
}

