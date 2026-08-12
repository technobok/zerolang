/* os.buildCompiler -- name and version of the C compiler that compiled this
   translation unit, read from its own predefined macros, so it needs nothing
   from the build. clang defines __GNUC__ as well, so it is tested first. The
   numeric macros are used rather than __VERSION__, which gcc and clang spell
   differently ("15.2.0" vs "Ubuntu Clang 21.1.8 (6ubuntu1)"). */
#define Z_BUILDCC_STR_(x) #x
#define Z_BUILDCC_STR(x) Z_BUILDCC_STR_(x)

static z_@String@_t z_os_buildCompiler(void);
static z_@String@_t z_os_buildCompiler(void) {
#if defined(__clang__)
    return z_@String@_new("clang " Z_BUILDCC_STR(__clang_major__) "."
                        Z_BUILDCC_STR(__clang_minor__) "."
                        Z_BUILDCC_STR(__clang_patchlevel__));
#elif defined(__GNUC__)
    return z_@String@_new("gcc " Z_BUILDCC_STR(__GNUC__) "."
                        Z_BUILDCC_STR(__GNUC_MINOR__) "."
                        Z_BUILDCC_STR(__GNUC_PATCHLEVEL__));
#else
    return z_@String@_new("cc");
#endif
}

