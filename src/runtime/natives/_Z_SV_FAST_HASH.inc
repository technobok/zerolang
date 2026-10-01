static uint64_t z_@StringView@_fastHash(const z_@StringView@_t* self);
static uint64_t z_@StringView@_fastHash(const z_@StringView@_t* self) {
    return z_fasthash_stringview(*self);
}
