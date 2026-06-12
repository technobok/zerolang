static bool z_io_exists(z_StringView_t path) {
    char* path_cstr = z_sv_to_cstr(path);
    int r = access(path_cstr, F_OK);
    free(path_cstr);
    return r == 0;
}

