static void z_io_eprintln(z_@StringView@_t sv) {
    fprintf(stderr, "%.*s\n", (int)sv.size, sv.data);
}

