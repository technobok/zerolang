/* os.pid -- getpid (GetCurrentProcessId on Windows). A pid fits in
   int32_t on every supported platform (Linux caps at 4_194_304 by
   default; Windows' are multiples of 4 below 2^26 in practice). */
static int32_t z_os_pid(void);
static int32_t z_os_pid(void) {
#if defined(_WIN32)
    return (int32_t)GetCurrentProcessId();
#else
    return (int32_t)getpid();
#endif
}
