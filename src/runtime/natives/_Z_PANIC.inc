static Z_NORETURN void z_panic(const char* msg) {
#if defined(_WIN32)
    if (!z_win_console_write(2, "zpanic: ", 8, 0) || !z_win_console_write(2, msg, strlen(msg), 1))
#endif
    fprintf(stderr, "zpanic: %s\n", msg);
    exit(1);
}
/* the program's normal end, once main has returned: nothing to do with one
   thread, so exit flushes and leaves */
static void z_end(void) {}
