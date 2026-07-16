/* os.spawnAsync -- fork/exec argv like os.spawn (same PATH resolution,
   redirection and cwd rules) but return immediately with the child's
   pid; -1 on setup/fork failure. No timeout: the caller reaps with
   waitAny and enforces deadlines with monotonicMillis + kill. */
static int32_t z_os_spawnAsync(
    z_List_String_t* argv, z_StringView_t cwd, z_StringView_t outPath,
    z_StringView_t errPath
);
static int32_t z_os_spawnAsync(
    z_List_String_t* argv, z_StringView_t cwd, z_StringView_t outPath,
    z_StringView_t errPath
) {
    if (argv->length == 0) return -1;
    char** c_argv = (char**)z_xmalloc((argv->length + 1) * sizeof(char*));
    for (uint64_t i = 0; i < argv->length; i++) {
        c_argv[i] = argv->data[i].data;
    }
    c_argv[argv->length] = NULL;
    char* cwd_c = z_sv_to_cstr(cwd);
    int inherit_out = (outPath.length == 0);
    int inherit_err = (errPath.length == 0);
    int inherit_in = (inherit_out && inherit_err);
    char* out_c = inherit_out ? NULL : z_sv_to_cstr(outPath);
    char* err_c = inherit_err ? NULL : z_sv_to_cstr(errPath);
    int out_fd = inherit_out ? -1 : open(out_c, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int err_fd = inherit_err ? -1 : open(err_c, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int null_fd = inherit_in ? -1 : open("/dev/null", O_RDONLY);
    int32_t result = -1;
    int setup_ok = (inherit_out || out_fd >= 0) && (inherit_err || err_fd >= 0)
                   && (inherit_in || null_fd >= 0);
    if (setup_ok) {
        pid_t pid = fork();
        if (pid == 0) {
            if (cwd.length > 0 && chdir(cwd_c) != 0) _exit(127);
            if (!inherit_in) dup2(null_fd, 0);
            if (!inherit_out) dup2(out_fd, 1);
            if (!inherit_err) dup2(err_fd, 2);
            if (null_fd >= 0) close(null_fd);
            if (out_fd >= 0) close(out_fd);
            if (err_fd >= 0) close(err_fd);
            execvp(c_argv[0], c_argv);
            _exit(127);
        } else if (pid > 0) {
            result = (int32_t)pid;
        }
    }
    if (out_fd >= 0) close(out_fd);
    if (err_fd >= 0) close(err_fd);
    if (null_fd >= 0) close(null_fd);
    free(cwd_c);
    free(out_c);
    free(err_c);
    free(c_argv);
    return result;
}
