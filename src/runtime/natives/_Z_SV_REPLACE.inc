static z_String_t z_StringView_replace(
    const z_StringView_t* self, const z_StringView_t* needle,
    const z_StringView_t* replacement);
static z_String_t z_StringView_replace(
    const z_StringView_t* self, const z_StringView_t* needle,
    const z_StringView_t* replacement) {
    return z_StringView_replace_impl(self, needle, replacement, false);
}

