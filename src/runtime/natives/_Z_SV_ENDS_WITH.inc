static bool z_StringView_endsWith(const z_StringView_t* self, const z_StringView_t* suffix);
static bool z_StringView_endsWith(const z_StringView_t* self, const z_StringView_t* suffix) {
    if (suffix->size > self->size) return false;
    if (suffix->size == 0) return true;
    return memcmp(self->data + (self->size - suffix->size),
                  suffix->data, suffix->size) == 0;
}

