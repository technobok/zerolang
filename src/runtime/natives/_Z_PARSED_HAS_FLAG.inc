static bool z_@Parsed@_hasFlag(const z_@Parsed@_t* self, const z_@StringView@_t* name);
static bool z_@Parsed@_hasFlag(const z_@Parsed@_t* self, const z_@StringView@_t* name) {
    for (uint64_t i = 0; i < self->flagSet.length; i++) {
        z_@String@_t* s = &self->flagSet.data[i];
        if (s->size == name->size
            && memcmp(s->data, name->data, name->size) == 0) {
            return true;
        }
    }
    return false;
}


