/* os.waitAny -- reap one exited child among `pids` without blocking. pid 0 =
   none of them has exited; a 0 entry is skipped (waitpid would read it as the
   process group). Each pid is asked by name, so a child another part of the
   program started is never reaped here. status decodes like os.spawn (exit
   code; 127 covers exec/setup failure via the child convention; a signalled
   child reports 128 + signo). */
static z_@procexit@_t z_os_waitAny(z_@ListViewVal_i32@_t pids);
static z_@procexit@_t z_os_waitAny(z_@ListViewVal_i32@_t pids) {
    z_@procexit@_t r = {0};
    for (uint64_t i = 0; i < pids.length; i++) {
        pid_t want = (pid_t)pids.data[i];
        if (want <= 0) continue;
        int wst = 0;
        pid_t p = waitpid(want, &wst, WNOHANG);
        if (p <= 0) continue;
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
    return r;
}
