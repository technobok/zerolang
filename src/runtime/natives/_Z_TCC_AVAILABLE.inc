/* tcc.available -- can libtcc be loaded from `tccDir` and driven from here?
   A probe for diagnostics and for a program deciding what to offer; the
   build path does NOT consult it, because `--cc-mode inproc` never falls
   back and so has nothing to decide. Loads and immediately unloads. */
static bool z_tcc_available(z_@StringView@_t tccDir);
static bool z_tcc_available(z_@StringView@_t tccDir) {
    z_tcc_api_t a;
    if (!z_tcc_open(&a, tccDir)) return false;
    z_tcc_close(&a);
    return true;
}

