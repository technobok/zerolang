static bool z_@StringView@_contains(const z_@StringView@_t* self, const z_@StringView@_t* needle);
static bool z_@StringView@_contains(const z_@StringView@_t* self, const z_@StringView@_t* needle) {
    return z_@StringView@_indexOf_raw(self, needle) != UINT64_MAX;
}

