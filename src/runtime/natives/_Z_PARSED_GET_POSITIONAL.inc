static z_Option_String_t z_Parsed_positional(z_Parsed_t* self, const z_StringView_t* name);
static z_Option_String_t z_Parsed_positional(z_Parsed_t* self, const z_StringView_t* name) {
    z_Option_String_t out = {0};
    z_String_t key = z_String_create(name->length);
    z_String_append(&key, name->data, name->length);
    /* map.get returns a borrowed OptionView into the map's storage; deep-copy
       the borrowed String into an owned box so the caller owns the result. */
    z_OptionView_String_t v = z_Map_String_String_get(
        self->positionalValues, key);
    z_String_free(&key);
    if (v.tag == Z_OPTIONVIEW_STRING_TAG_NONE) {
        out.tag = Z_OPTION_STRING_TAG_NONE;
        return out;
    }
    z_String_t* src = (z_String_t*)v.data;
    z_String_t* box = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *box = z_String_create(src->size);
    z_String_append(box, src->data, src->size);
    out.tag = Z_OPTION_STRING_TAG_SOME;
    out.data = box;
    return out;
}
