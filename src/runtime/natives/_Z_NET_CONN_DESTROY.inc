static void z_@Conn@_destroy(z_@Conn@_t* p) {
    if (!p) return;
    if (!p->@MEMBER_Conn.open@) return;
    close(p->@MEMBER_Conn.fd@);
    p->@MEMBER_Conn.open@ = false;
}

