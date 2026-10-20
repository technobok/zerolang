/* written with the struct, ahead of net's socket layer (_Z_NET_SYS) */
static int z_net_close(int64_t s);
static void z_@Conn@_destroy(z_@Conn@_t* p) {
    if (!p) return;
    if (!p->@MEMBER_Conn.open@) return;
    z_net_close(p->@MEMBER_Conn.sock@);
    p->@MEMBER_Conn.open@ = false;
}
