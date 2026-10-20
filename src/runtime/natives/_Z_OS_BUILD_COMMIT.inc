/* os.buildCommit -- the build stamp this binary carries, empty when the
   build supplied none. Two routes set it: define ZC_BUILD_COMMIT for the C
   compiler, or define ZC_BUILD_STAMP_LINKED and link an object that defines
   z_build_commit and z_build_date. The link route costs a relink rather than
   a recompile of this translation unit when the stamp changes, which is why
   the Makefile uses it. There is no weak default to override: on PE/COFF
   (mingw) a strong definition does not reliably replace a weak one, and
   MSVC has no weak symbols at all. */
#if defined(ZC_BUILD_STAMP_LINKED)
extern const char z_build_commit[];
#else
#ifndef ZC_BUILD_COMMIT
#define ZC_BUILD_COMMIT ""
#endif
const char z_build_commit[] = ZC_BUILD_COMMIT;
#endif

static z_@String@_t z_os_buildCommit(void);
static z_@String@_t z_os_buildCommit(void) {
    return z_@String@_new(z_build_commit);
}

