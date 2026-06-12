static bool z_StringView_contains(const z_StringView_t* self, const z_StringView_t* needle);
static bool z_StringView_contains(const z_StringView_t* self, const z_StringView_t* needle) {
    return z_StringView_indexOf_raw(self, needle) != UINT64_MAX;
}

