/* written with the struct, ahead of net's socket layer (_Z_NET_SYS) */
static int z_net_close(int64_t s);
static void z_@Listener@_destroy(z_@Listener@_t* p) {
    if (!p) return;
    if (!p->@MEMBER_Listener.open@) return;
    z_net_close(p->@MEMBER_Listener.sock@);
    p->@MEMBER_Listener.open@ = false;
}
