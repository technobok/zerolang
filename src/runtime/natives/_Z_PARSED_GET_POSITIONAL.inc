static z_Option_String_t z_Parsed_positional(z_Parsed_t* self, const z_StringView_t* name);
static z_Option_String_t z_Parsed_positional(z_Parsed_t* self, const z_StringView_t* name) {
    z_Option_String_t out = {0};
    z_String_t key = z_String_create(name->length);
    z_String_append(&key, name->data, name->length);
    z_Option_String_t v = z_Map_String_String_get(
        self->positionalValues, key);
    z_String_free(&key);
    if (v.tag == Z_OPTION_STRING_TAG_NONE) {
        if (v.data) free(v.data);
        out.tag = Z_OPTION_STRING_TAG_NONE;
        return out;
    }
    return v;
}

