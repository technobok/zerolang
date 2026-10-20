/* os.buildDate -- companion to os.buildCommit: the date the build stamped
   in, empty when it stamped none. Set by defining ZC_BUILD_DATE for the C
   compiler, or under ZC_BUILD_STAMP_LINKED by the linked object, as
   os.buildCommit is. */
#if defined(ZC_BUILD_STAMP_LINKED)
extern const char z_build_date[];
#else
#ifndef ZC_BUILD_DATE
#define ZC_BUILD_DATE ""
#endif
const char z_build_date[] = ZC_BUILD_DATE;
#endif

static z_@String@_t z_os_buildDate(void);
static z_@String@_t z_os_buildDate(void) {
    return z_@String@_new(z_build_date);
}

