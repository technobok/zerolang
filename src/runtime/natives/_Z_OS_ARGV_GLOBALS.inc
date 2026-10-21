/* z_os_args_init -- what main() hands os.args before z_main runs, so os.args
   needs no argument threaded through every call. On POSIX it keeps argc and
   argv. On Windows os.args reads the command line itself (GetCommandLineW),
   so nothing is kept. Only emitted when the program references os.args. */
#if defined(_WIN32)
static void z_os_args_init(int argc, char** argv) {
    (void)argc;
    (void)argv;
}
#else
static int z_os_argc_g;
static char** z_os_argv_g;
static void z_os_args_init(int argc, char** argv) {
    z_os_argc_g = argc;
    z_os_argv_g = argv;
}
#endif
