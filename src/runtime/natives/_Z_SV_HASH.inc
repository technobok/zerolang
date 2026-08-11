static uint64_t z_@StringView@_hash(const z_@StringView@_t* self);
static uint64_t z_@StringView@_hash(const z_@StringView@_t* self) {
    return z_siphash_stringview(*self);
}
