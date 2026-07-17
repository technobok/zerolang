static uint64_t z_StringView_hash(const z_StringView_t* self);
static uint64_t z_StringView_hash(const z_StringView_t* self) {
    return z_siphash_stringview(*self);
}
