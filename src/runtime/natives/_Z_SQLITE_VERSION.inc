/* sqlite.version -- the library's version, "3.54.0". */
static z_@String@_t z_sqlite_version(void) {
    const char* v = sqlite3_libversion();
    uint64_t n = (uint64_t)strlen(v);
    z_@String@_t s = z_@String@_create(n);
    z_@String@_append(&s, v, n);
    return s;
}

