/* a Child's destructor: its handle is closed -- the pidfd, or on Windows the
   process handle -- and the process itself is left running */
static void z_@Child@_destroy(z_@Child@_t* p) {
    if (!p || !p->@MEMBER_Child.open@) return;
    p->@MEMBER_Child.open@ = false;
    if (p->@MEMBER_Child.handle@ < 0) return;
#if defined(_WIN32)
    CloseHandle((HANDLE)(intptr_t)p->@MEMBER_Child.handle@);
#else
    close((int)p->@MEMBER_Child.handle@);
#endif
    p->@MEMBER_Child.handle@ = -1;
}
