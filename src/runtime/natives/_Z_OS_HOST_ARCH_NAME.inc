/* os.hostArchName -- the CPU family this translation unit was compiled for.
   Companion to _Z_OS_HOST_OS_NAME; same reasoning for returning a name. */
static z_@String@_t z_os_hostArchName(void);
static z_@String@_t z_os_hostArchName(void) {
#if defined(__x86_64__) || defined(_M_X64)
    return z_@String@_new("x86_64");
#elif defined(__aarch64__) || defined(_M_ARM64)
    return z_@String@_new("aarch64");
#else
    return z_@String@_new("other");
#endif
}

