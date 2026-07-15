static void z_io_removeTree_impl(const char* path) {
    struct stat st;
    if (lstat(path, &st) != 0) return;
    if (S_ISDIR(st.st_mode)) {
        DIR* d = opendir(path);
        if (d) {
            struct dirent* ent;
            while ((ent = readdir(d)) != NULL) {
                if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
                size_t n = strlen(path) + 1 + strlen(ent->d_name) + 1;
                char* child = (char*)z_xmalloc(n);
                snprintf(child, n, "%s/%s", path, ent->d_name);
                z_io_removeTree_impl(child);
                free(child);
            }
            closedir(d);
        }
        rmdir(path);
    } else {
        unlink(path);
    }
}

static void z_io_removeTree(z_StringView_t path) {
    char* path_cstr = z_sv_to_cstr(path);
    z_io_removeTree_impl(path_cstr);
    free(path_cstr);
}
