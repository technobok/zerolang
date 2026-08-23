/* os.cpu_count -- online processors, for sizing a worker pool. sysconf is
   POSIX and returns -1 with errno set when the name is unsupported, so the
   result is clamped to at least 1: a caller dividing work by this must never
   be handed 0, and "I could not tell" is answerable with "assume serial". */
static int32_t z_os_cpuCount(void);
static int32_t z_os_cpuCount(void) {
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    if (n < 1) return 1;
    if (n > 1024) return 1024;
    return (int32_t)n;
}
