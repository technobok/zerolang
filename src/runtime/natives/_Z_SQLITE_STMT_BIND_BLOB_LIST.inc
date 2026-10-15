/* Statement.bindBlobList -- the list as carray(?N)'s rows, one BLOB each */
static void z_@Statement@_bindBlobList(z_@Statement@_t* t, uint64_t parameter, const z_@List_Bytes@_t* values) {
    uint64_t n = values->length;
    z_sqlite_iovec* a = (z_sqlite_iovec*)z_xmalloc((n ? n : 1) * sizeof(z_sqlite_iovec));
    for (uint64_t i = 0; i < n; i++) {
        a[i].iov_base = (void*)values->data[i].data;
        a[i].iov_len = (size_t)values->data[i].length;
    }
    z_sqlite_bind_array(t, parameter, (void*)a, n, SQLITE_CARRAY_BLOB);
    free(a);
}

