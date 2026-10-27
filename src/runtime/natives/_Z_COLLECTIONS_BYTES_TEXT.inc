/* `bytes` as a StringView over the same memory */
static z_@StringView@_t z_collections_bytesText(z_@ListViewVal_u8@_t bytes);
static z_@StringView@_t z_collections_bytesText(z_@ListViewVal_u8@_t bytes) {
    z_@StringView@_t sv = { bytes.length, (const char*)bytes.data };
    return sv;
}
