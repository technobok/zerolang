static z_@Option_String@_t z_@Parsed@_option(const z_@Parsed@_t* self, const z_@StringView@_t* name);
static z_@Option_String@_t z_@Parsed@_option(const z_@Parsed@_t* self, const z_@StringView@_t* name) {
    z_@Option_String@_t out = {0};
    z_@String@_t key = z_@String@_create(name->size);
    z_@String@_append(&key, name->data, name->size);
    /* map.get returns a borrowed OptionView into the map's storage; deep-copy
       the borrowed String into an owned box so the caller owns the result. */
    z_@OptionView_String@_t v = z_@Map_String_String@_get(
        self->optionValues, &key);
    z_@String@_free(&key);
    if (v.tag == Z_OPTIONVIEW_STRING_TAG_NONE) {
        out.tag = Z_OPTION_STRING_TAG_NONE;
        return out;
    }
    z_@String@_t* src = (z_@String@_t*)v.data;
    z_@String@_t* box = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *box = z_@String@_create(src->size);
    z_@String@_append(box, src->data, src->size);
    out.tag = Z_OPTION_STRING_TAG_SOME;
    out.data = box;
    return out;
}

