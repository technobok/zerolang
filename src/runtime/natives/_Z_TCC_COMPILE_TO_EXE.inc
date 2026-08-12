/* tcc.compileToExe -- compile `csrc` and link it to the executable `exe`,
   in this process, through libtcc loaded from `tccDir`.

   The result is a file on disk (TCC_OUTPUT_EXE), never a run in memory:
   in-process buys the fast COMPILE, and the program still executes as its
   own child, so exit codes, signals and the leak corpus are unaffected and
   a crashing program cannot take zc down with it.

   `tccDir` is also handed to tcc_set_lib_path -- tcc's runtime payload is
   fixed when tcc is configured, so a relocated one cannot resolve even
   stddef.h until it is told. `cflags` is parsed by tcc itself (the same
   spelling as the command line); `libs` are the `-l` names the emitted C
   declared, with -lm appended the way the spawned path appends it.

   Returns 0 on success, 1 when tcc rejected the program or the link
   failed, and 127 when libtcc could not be loaded -- the code os.spawn
   already uses for "could not run the compiler", so the driver reports
   both through one path rather than growing a second. */
static int32_t z_tcc_compileToExe(
    z_@StringView@_t csrc, z_@StringView@_t exe, z_@StringView@_t tccDir,
    bool release, z_@StringView@_t cflags, z_@List_String@_t* libs
);
static int32_t z_tcc_compileToExe(
    z_@StringView@_t csrc, z_@StringView@_t exe, z_@StringView@_t tccDir,
    bool release, z_@StringView@_t cflags, z_@List_String@_t* libs
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
    /* -g matches the spawned path's debug default; tcc has one optimisation
       level, so --release changes nothing here beyond dropping -g. */
    if (!release && a.set_options(s, "-g") == -1) rc = 1;

    if (rc == 0 && cflags.size > 0) {
        char* cf = z_sv_to_cstr(cflags);
        if (a.set_options(s, cf) == -1) rc = 1;
        free(cf);
    }
    if (rc == 0 && a.set_output_type(s, Z_TCC_OUTPUT_EXE) == -1) rc = 1;

    if (rc == 0) {
        char* src_c = z_sv_to_cstr(csrc);
        if (a.add_file(s, src_c) == -1) rc = 1;
        free(src_c);
    }
    if (rc == 0) {
        for (uint64_t i = 0; i < libs->length; i++) {
            if (a.add_library(s, libs->data[i].data) == -1) {
                rc = 1;
                break;
            }
        }
    }
    if (rc == 0 && a.add_library(s, "m") == -1) rc = 1;

    if (rc == 0) {
        char* exe_c = z_sv_to_cstr(exe);
        if (a.output_file(s, exe_c) == -1) rc = 1;
        free(exe_c);
    }

    a.del(s);
    z_tcc_close(&a);
    return rc;
}

