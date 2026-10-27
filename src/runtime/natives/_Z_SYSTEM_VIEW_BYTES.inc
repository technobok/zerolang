/* the bytes `s` views, as a ByteView over the same memory */
static z_@ListViewVal_u8@_t z_system_viewBytes(z_@StringView@_t s);
static z_@ListViewVal_u8@_t z_system_viewBytes(z_@StringView@_t s) {
    z_@ListViewVal_u8@_t v = { (uint64_t)s.size, (uint8_t*)s.data };
    return v;
}
