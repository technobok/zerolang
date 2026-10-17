/* thread.yieldNow -- give the CPU to another runnable thread */
static void z_thread_yieldNow(void);
static void z_thread_yieldNow(void) {
    z_thread_yield();
}
