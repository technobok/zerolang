/* os.waitAny -- reap one exited child without blocking. pid 0 = no
   child has exited (or there are no children); status decodes like
   os.spawn (exit code; 127 covers exec/setup failure via the child
   convention; a signalled child reports 128 + signo). */
static z_procexit_t z_os_waitAny(void);
static z_procexit_t z_os_waitAny(void) {
    z_procexit_t r = {0};
    int wst = 0;
    pid_t p = waitpid(-1, &wst, WNOHANG);
    if (p <= 0) return r;
    r.pid = (int32_t)p;
    if (WIFEXITED(wst)) {
        r.status = (int32_t)WEXITSTATUS(wst);
    } else if (WIFSIGNALED(wst)) {
        r.status = 128 + (int32_t)WTERMSIG(wst);
    } else {
        r.status = 127;
    }
    return r;
}
