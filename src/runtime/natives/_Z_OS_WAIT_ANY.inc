/* os.waitAny -- reap one exited child without blocking. pid 0 = no
   child has exited (or there are no children); status decodes like
   os.spawn (exit code; 127 covers exec/setup failure via the child
   convention; a signalled child reports 128 + signo). */
static z_@procexit@_t z_os_waitAny(void);
static z_@procexit@_t z_os_waitAny(void) {
    z_@procexit@_t r = {0};
    int wst = 0;
    pid_t p = waitpid(-1, &wst, WNOHANG);
    if (p <= 0) return r;
    r.@MEMBER_procexit.pid@ = (int32_t)p;
    if (WIFEXITED(wst)) {
        r.@MEMBER_procexit.status@ = (int32_t)WEXITSTATUS(wst);
    } else if (WIFSIGNALED(wst)) {
        r.@MEMBER_procexit.status@ = 128 + (int32_t)WTERMSIG(wst);
    } else {
        r.@MEMBER_procexit.status@ = 127;
    }
    return r;
}

