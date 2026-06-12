/* os.platform -- compile-time selection via C preprocessor.
   Falls through to `other` on unrecognised hosts. */
static z_platformkind_t z_os_platform(void);
static z_platformkind_t z_os_platform(void) {
    z_platformkind_t out = {0};
#if defined(__linux__)
    out.tag = Z_PLATFORMKIND_TAG_LINUX;
#elif defined(__APPLE__)
    out.tag = Z_PLATFORMKIND_TAG_DARWIN;
#elif defined(_WIN32)
    out.tag = Z_PLATFORMKIND_TAG_WINDOWS;
#else
    out.tag = Z_PLATFORMKIND_TAG_OTHER;
#endif
    return out;
}

