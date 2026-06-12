static bool z_StringView_endsWith(const z_StringView_t* self, const z_StringView_t* suffix);
static bool z_StringView_endsWith(const z_StringView_t* self, const z_StringView_t* suffix) {
    if (suffix->length > self->length) return false;
    if (suffix->length == 0) return true;
    return memcmp(self->data + (self->length - suffix->length),
                  suffix->data, suffix->length) == 0;
}

