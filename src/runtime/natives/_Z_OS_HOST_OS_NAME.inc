/* os.hostOsName -- the OS family this translation unit was compiled for,
   resolved by the C preprocessor. A NAME rather than a variant so the runtime
   needs to know nothing about `platform`'s kinds: `zc` maps it to a
   `platform.oskind` arm when it fills that unit in for the build it is
   performing. Falls through to `other` on an unrecognised host. */
static z_String_t z_os_hostOsName(void);
static z_String_t z_os_hostOsName(void) {
#if defined(__linux__)
    return z_String_new("linux");
#elif defined(__APPLE__)
    return z_String_new("darwin");
#elif defined(_WIN32)
    return z_String_new("windows");
#else
    return z_String_new("other");
#endif
}
