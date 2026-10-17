/* thread.running -- threads the program started that have not finished. A
   thread leaves the count after its task is destroyed and its block freed */
static int64_t z_thread_running(void);
static int64_t z_thread_running(void) {
    return z_thread_live();
}
