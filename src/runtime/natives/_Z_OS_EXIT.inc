/* os.exit -- libc exit with the given status. Does not return;
   in-scope zerolang destructors are skipped. */
static void z_os_exit(int32_t code);
static void z_os_exit(int32_t code) {
    exit((int)code);
}

