static void z_io_eprintln(z_StringView_t sv) {
    fprintf(stderr, "%.*s\n", (int)sv.length, sv.data);
}

