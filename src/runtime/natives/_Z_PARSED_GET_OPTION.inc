static z_Option_String_t z_Parsed_option(z_Parsed_t* self, const z_StringView_t* name);
static z_Option_String_t z_Parsed_option(z_Parsed_t* self, const z_StringView_t* name) {
    z_Option_String_t out = {0};
    z_String_t key = z_String_create(name->length);
    z_String_append(&key, name->data, name->length);
    /* map.get returns an OWNED deep copy: a box whose inner
       z_String_t owns its own heap buffer. Hand that box back
       directly on a hit; free both halves on a miss. */
    z_Option_String_t v = z_Map_String_String_get(
        self->optionValues, key);
    z_String_free(&key);
    if (v.tag == Z_OPTION_STRING_TAG_NONE) {
        if (v.data) free(v.data);
        out.tag = Z_OPTION_STRING_TAG_NONE;
        return out;
    }
    return v;
}

