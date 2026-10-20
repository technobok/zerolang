/* child.kill -- end the child now, through its handle where there is one so
   no process that reused its pid is touched: SIGKILL (pidfd_send_signal, else
   kill), or TerminateProcess with 137, the status a SIGKILLed child reports.
   Nothing once the child is reaped. */
static void z_@Child@_kill(z_@Child@_t* c);
static void z_@Child@_kill(z_@Child@_t* c) {
    if (c->@MEMBER_Child.reaped@ || c->@MEMBER_Child.pid@ <= 0) return;
#if defined(_WIN32)
    TerminateProcess((HANDLE)(intptr_t)c->@MEMBER_Child.handle@, 137);
#else
#ifdef SYS_pidfd_send_signal
    if (c->@MEMBER_Child.handle@ >= 0
        && syscall(SYS_pidfd_send_signal, (int)c->@MEMBER_Child.handle@, SIGKILL, NULL, 0) == 0) return;
#endif
    kill((pid_t)c->@MEMBER_Child.pid@, SIGKILL);
#endif
}
