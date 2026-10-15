/* tcc.compileToObject -- compile `csrc` to the object file `obj`, in this
   process, through libtcc loaded from `tccDir` (TCC_OUTPUT_OBJ). `cflags` is
   parsed by tcc itself, the -D defines among them. Codes as compileToExe's:
   0 on success, 1 when tcc rejected the source, 127 when libtcc could not be
   loaded. */
static int32_t z_tcc_compileToObject(
    z_@StringView@_t csrc, z_@StringView@_t obj, z_@StringView@_t tccDir,
    z_@StringView@_t cflags
);
static int32_t z_tcc_compileToObject(
    z_@StringView@_t csrc, z_@StringView@_t obj, z_@StringView@_t tccDir,
    z_@StringView@_t cflags
) {
    z_tcc_api_t a;
    if (!z_tcc_open(&a, tccDir)) return 127;

    z_tcc_State* s = a.nw();
    if (!s) {
        z_tcc_close(&a);
        return 127;
    }
    a.set_error_func(s, NULL, z_tcc_on_error);

    char* dir_c = z_sv_to_cstr(tccDir);
    a.set_lib_path(s, dir_c);
    free(dir_c);

    int32_t rc = 0;
    if (cflags.size > 0) {
        char* cf = z_sv_to_cstr(cflags);
        if (a.set_options(s, cf) == -1) rc = 1;
        free(cf);
    }
    if (rc == 0 && a.set_output_type(s, Z_TCC_OUTPUT_OBJ) == -1) rc = 1;
    if (rc == 0) {
        char* src_c = z_sv_to_cstr(csrc);
        if (a.add_file(s, src_c) == -1) rc = 1;
        free(src_c);
    }
    if (rc == 0) {
        char* obj_c = z_sv_to_cstr(obj);
        if (a.output_file(s, obj_c) == -1) rc = 1;
        free(obj_c);
    }

    a.del(s);
    z_tcc_close(&a);
    return rc;
}

