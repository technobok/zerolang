/* Process argc/argv captured by main() before z_main runs, so
   os.args can expose them without threading them through every
   call site. Only emitted when the program references os.args. */
static int z_os_argc_g;
static char** z_os_argv_g;

