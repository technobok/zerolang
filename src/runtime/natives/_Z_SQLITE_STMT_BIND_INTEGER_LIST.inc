/* Statement.bindIntegerList -- the list as carray(?N)'s rows */
static void z_@Statement@_bindIntegerList(z_@Statement@_t* t, uint64_t parameter, const z_@ListVal_i64@_t* values) {
    z_sqlite_bind_array(t, parameter, (void*)values->data, values->length, SQLITE_CARRAY_INT64);
}

