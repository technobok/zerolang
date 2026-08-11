/* os.buildDate -- companion to os.buildCommit: the date the build stamped
   in, empty when it stamped none. Set by defining ZC_BUILD_DATE for the C
   compiler, or by linking a strong z_build_date. */
#ifndef ZC_BUILD_DATE
#define ZC_BUILD_DATE ""
#endif
__attribute__((weak)) const char z_build_date[] = ZC_BUILD_DATE;

static z_@String@_t z_os_buildDate(void);
static z_@String@_t z_os_buildDate(void) {
    return z_@String@_new(z_build_date);
}
