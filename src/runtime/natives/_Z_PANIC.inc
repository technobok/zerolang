static Z_NORETURN void z_panic(const char* msg) {
    z_write_line(2, "zpanic: ", 8, 0);
    z_write_line(2, msg, strlen(msg), 1);
    exit(1);
}
/* the program's normal end, once main has returned: nothing to do with one
   thread, so exit flushes and leaves */
static void z_end(void) {}
