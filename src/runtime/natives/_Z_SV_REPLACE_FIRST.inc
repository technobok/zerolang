static z_@String@_t z_@StringView@_replaceFirst(
    const z_@StringView@_t* self, const z_@StringView@_t* needle,
    const z_@StringView@_t* replacement);
static z_@String@_t z_@StringView@_replaceFirst(
    const z_@StringView@_t* self, const z_@StringView@_t* needle,
    const z_@StringView@_t* replacement) {
    return z_@StringView@_replace_impl(self, needle, replacement, true);
}

