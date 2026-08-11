/* os.buildCommit -- the build stamp this binary carries, empty when the
   build supplied none. Two routes set it: define ZC_BUILD_COMMIT for the C
   compiler, or link a strong z_build_commit that overrides the weak default
   below. The link route costs a relink rather than a recompile of this
   translation unit, which is why the Makefile uses it. */
#ifndef ZC_BUILD_COMMIT
#define ZC_BUILD_COMMIT ""
#endif
__attribute__((weak)) const char z_build_commit[] = ZC_BUILD_COMMIT;

static z_@String@_t z_os_buildCommit(void);
static z_@String@_t z_os_buildCommit(void) {
    return z_@String@_new(z_build_commit);
}
