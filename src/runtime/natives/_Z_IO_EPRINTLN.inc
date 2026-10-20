static void z_io_eprintln(z_@StringView@_t sv) {
#if defined(_WIN32)
    if (z_win_console_write(2, sv.data, sv.size, 1)) return;
#endif
    fprintf(stderr, "%.*s\n", (int)sv.size, sv.data);
}


