static void z_@File@_destroy(z_@File@_t* p) {
    if (!p) return;
    if (!p->@MEMBER_File.open@) return;
    close(p->@MEMBER_File.fd@);
    p->@MEMBER_File.open@ = false;
}

