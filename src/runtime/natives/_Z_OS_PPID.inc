/* os.ppid -- getppid. */
static int32_t z_os_ppid(void);
static int32_t z_os_ppid(void) {
    return (int32_t)getppid();
}

