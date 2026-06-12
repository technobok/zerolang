static bool z_Parsed_hasFlag(z_Parsed_t* self, const z_StringView_t* name);
static bool z_Parsed_hasFlag(z_Parsed_t* self, const z_StringView_t* name) {
    for (uint64_t i = 0; i < self->flagSet.length; i++) {
        z_String_t* s = &self->flagSet.data[i];
        if (s->size == name->length
            && memcmp(s->data, name->data, name->length) == 0) {
            return true;
        }
    }
    return false;
}

