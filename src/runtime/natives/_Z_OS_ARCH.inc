/* os.arch -- compile-time CPU family. */
static z_archkind_t z_os_arch(void);
static z_archkind_t z_os_arch(void) {
    z_archkind_t out = {0};
#if defined(__x86_64__) || defined(_M_X64)
    out.tag = Z_ARCHKIND_TAG_X86_64;
#elif defined(__aarch64__) || defined(_M_ARM64)
    out.tag = Z_ARCHKIND_TAG_AARCH64;
#else
    out.tag = Z_ARCHKIND_TAG_OTHER;
#endif
    return out;
}

