/* os.exit in a program that runs threads -- it ends the process once, as
   z_panic does (z_end_claim): output is flushed and the process leaves with
   _exit. In-scope zerolang destructors are skipped. */
static void z_os_exit(int32_t code);
static void z_os_exit(int32_t code) {
    z_end_claim();
    fflush(NULL);
    _exit((int)code);
}
