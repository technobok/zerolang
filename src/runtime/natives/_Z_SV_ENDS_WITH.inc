static bool z_@StringView@_endsWith(const z_@StringView@_t* self, const z_@StringView@_t* suffix);
static bool z_@StringView@_endsWith(const z_@StringView@_t* self, const z_@StringView@_t* suffix) {
    if (suffix->size > self->size) return false;
    if (suffix->size == 0) return true;
    return memcmp(self->data + (self->size - suffix->size),
                  suffix->data, suffix->size) == 0;
}

