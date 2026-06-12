/* os.pid -- getpid. pid_t fits in int32_t on every supported
   platform (Linux caps at 4_194_304 by default). */
static int32_t z_os_pid(void);
static int32_t z_os_pid(void) {
    return (int32_t)getpid();
}

