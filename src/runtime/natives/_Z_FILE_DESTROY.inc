static void z_@File@_destroy(z_@File@_t* p) {
    if (!p) return;
    if (!p->@MEMBER_File.open@) return;
#if defined(_WIN32)
    _close(p->@MEMBER_File.fd@);
#else
    close(p->@MEMBER_File.fd@);
#endif
    p->@MEMBER_File.open@ = false;
}
