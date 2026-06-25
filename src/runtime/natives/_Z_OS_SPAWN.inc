/* os.spawn -- fork/exec argv as a child. argv[0] is resolved via
   PATH (execvp). stdout->outPath, stderr->errPath (each
   O_WRONLY|O_CREAT|O_TRUNC); an empty out/err path inherits the
   parent's fd, and empty out+err inherits stdin too (else stdin is
   /dev/null). A non-empty cwd chdirs
   the child. The redirection files are opened in the parent before
   fork so the child's chdir cannot change their resolution. argv
   element buffers are NUL-terminated (z_String), borrowed directly;
   only the three view paths need z_sv_to_cstr. Returns the child's
   exit status; 127 on exec/setup failure; 124 if timeoutSecs > 0 and
   the child outlived it (SIGKILLed). timeoutSecs <= 0 waits forever. */
static void z_os_spawn_on_alarm(int sig) { (void)sig; }
static int32_t z_os_spawn(
    z_List_String_t* argv, z_StringView_t cwd, z_StringView_t outPath,
    z_StringView_t errPath, int32_t timeoutSecs
);
static int32_t z_os_spawn(
    z_List_String_t* argv, z_StringView_t cwd, z_StringView_t outPath,
    z_StringView_t errPath, int32_t timeoutSecs
) {
    if (argv->length == 0) return 127;
    char** c_argv = (char**)z_xmalloc((argv->length + 1) * sizeof(char*));
    for (uint64_t i = 0; i < argv->length; i++) {
        c_argv[i] = argv->data[i].data;
    }
    c_argv[argv->length] = NULL;
    char* cwd_c = z_sv_to_cstr(cwd);
    /* An empty outPath/errPath inherits the parent's fd 1/2 (skip the
       open+dup2). When both are empty (the `zc run` case) inherit stdin
       too; otherwise stdin is /dev/null. Non-empty callers unaffected. */
    int inherit_out = (outPath.length == 0);
    int inherit_err = (errPath.length == 0);
    int inherit_in = (inherit_out && inherit_err);
    char* out_c = inherit_out ? NULL : z_sv_to_cstr(outPath);
    char* err_c = inherit_err ? NULL : z_sv_to_cstr(errPath);
    int out_fd = inherit_out ? -1 : open(out_c, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int err_fd = inherit_err ? -1 : open(err_c, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int null_fd = inherit_in ? -1 : open("/dev/null", O_RDONLY);
    int32_t status = 127;
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
            int wst = 0;
            if (timeoutSecs > 0) {
                struct sigaction sa, old_sa;
                memset(&sa, 0, sizeof(sa));
                sa.sa_handler = z_os_spawn_on_alarm;
                sigemptyset(&sa.sa_mask);
                sigaction(SIGALRM, &sa, &old_sa);
                alarm((unsigned int)timeoutSecs);
                pid_t r = waitpid(pid, &wst, 0);
                int e = errno;
                alarm(0);
                sigaction(SIGALRM, &old_sa, NULL);
                if (r == pid) {
                    if (WIFEXITED(wst)) status = (int32_t)WEXITSTATUS(wst);
                } else if (r < 0 && e == EINTR) {
                    kill(pid, SIGKILL);
                    waitpid(pid, &wst, 0);
                    status = 124;
                }
            } else {
                if (waitpid(pid, &wst, 0) == pid && WIFEXITED(wst)) {
                    status = (int32_t)WEXITSTATUS(wst);
                }
            }
        }
    }
    if (out_fd >= 0) close(out_fd);
    if (err_fd >= 0) close(err_fd);
    if (null_fd >= 0) close(null_fd);
    free(cwd_c);
    free(out_c);
    free(err_c);
    free(c_argv);
    return status;
}

