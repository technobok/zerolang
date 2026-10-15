static void z_@Listener@_destroy(z_@Listener@_t* p) {
    if (!p) return;
    if (p->@MEMBER_Listener.closed@) return;
    close(p->@MEMBER_Listener.fd@);
    p->@MEMBER_Listener.closed@ = true;
}

