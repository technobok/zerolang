static _Noreturn void z_panic(const char* msg) {
    fprintf(stderr, "zpanic: %s\n", msg);
    exit(1);
}
