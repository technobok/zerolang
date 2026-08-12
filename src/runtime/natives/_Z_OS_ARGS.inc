/* os.args -- copy argv into a freshly-allocated list of strings.
   The caller owns the outer list and every string inside; scope
   exit runs the list destructor which frees each element. */
static z_@List_String@_t z_os_args(void);
static z_@List_String@_t z_os_args(void) {
    uint64_t n = (uint64_t)(z_os_argc_g > 0 ? z_os_argc_g : 0);
    z_@List_String@_t out = {0};
    out.length = n;
    out.capacity = n;
    if (n > 0) {
        out.data = (z_@String@_t*)z_xmalloc(n * sizeof(z_@String@_t));
        for (uint64_t i = 0; i < n; i++) {
            out.data[i] = z_@String@_new(z_os_argv_g[i]);
        }
    }
    return out;
}


