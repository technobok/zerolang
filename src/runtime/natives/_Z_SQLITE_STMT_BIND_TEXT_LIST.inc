/* Statement.bindTextList -- the list as carray(?N)'s rows: each String is
   made a C string for the bind, and SQLite copies them all */
static void z_@Statement@_bindTextList(z_@Statement@_t* t, uint64_t parameter, const z_@List_String@_t* values) {
    uint64_t n = values->length;
    char** a = (char**)z_xmalloc((n ? n : 1) * sizeof(char*));
    for (uint64_t i = 0; i < n; i++) {
        z_@StringView@_t v = { .data = values->data[i].data, .size = values->data[i].size };
        a[i] = z_sqlite_cstr(v);
    }
    z_sqlite_bind_array(t, parameter, (void*)a, n, SQLITE_CARRAY_TEXT);
    for (uint64_t i = 0; i < n; i++) free(a[i]);
    free(a);
}

