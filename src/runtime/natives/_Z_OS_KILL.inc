/* os.kill -- SIGKILL a child by pid (reap it with waitAny afterwards).
   No-op on pid <= 0 so a cleared handle can never signal a process
   group by accident. */
static void z_os_kill(int32_t pid);
static void z_os_kill(int32_t pid) {
    if (pid <= 0) return;
    kill((pid_t)pid, SIGKILL);
}
