/* os.waitAny -- reap one exited child among `children` without blocking,
   marking it reaped; pid 0 = none of them has exited. Each child is asked
   about by its own pid (POSIX) or handle (Windows), so a child another part
   of the program started is never reaped here, and one already reaped is
   skipped. status decodes like os.spawn: the exit code; a signalled child
   reports 128 + signo; one Child.kill ended reports 137 everywhere. */
static z_@procexit@_t z_os_waitAny(z_@List_Child@_t* children);
static z_@procexit@_t z_os_waitAny(z_@List_Child@_t* children) {
    z_@procexit@_t r = {0};
    for (uint64_t i = 0; i < children->length; i++) {
        z_@Child@_t* c = &children->data[i];
        if (c->@MEMBER_Child.reaped@ || c->@MEMBER_Child.pid@ <= 0) continue;
#if defined(_WIN32)
        HANDLE h = (HANDLE)(intptr_t)c->@MEMBER_Child.handle@;
        if (WaitForSingleObject(h, 0) != WAIT_OBJECT_0) continue;
        DWORD code = 127;
        if (!GetExitCodeProcess(h, &code)) code = 127;
        r.@MEMBER_procexit.status@ = (int32_t)code;
#else
        int wst = 0;
        pid_t p = waitpid((pid_t)c->@MEMBER_Child.pid@, &wst, WNOHANG);
        if (p <= 0) continue;
        if (WIFEXITED(wst)) {
            r.@MEMBER_procexit.status@ = (int32_t)WEXITSTATUS(wst);
        } else if (WIFSIGNALED(wst)) {
            r.@MEMBER_procexit.status@ = 128 + (int32_t)WTERMSIG(wst);
        } else {
            r.@MEMBER_procexit.status@ = 127;
        }
#endif
        c->@MEMBER_Child.reaped@ = true;
        r.@MEMBER_procexit.pid@ = c->@MEMBER_Child.pid@;
        return r;
    }
    return r;
}
