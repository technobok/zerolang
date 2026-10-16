/* z_os_spawn_start -- start argv as a child with posix_spawnp, the start
   os.spawn and os.spawnAsync share. argv[0] is resolved via PATH.
   stdout->outPath, stderr->errPath (each O_WRONLY|O_CREAT|O_TRUNC); an empty
   out/err path inherits the parent's fd, and empty out+err inherits stdin too
   (else stdin is /dev/null). A non-empty cwd is the child's working directory.

   The redirection files are opened in the parent, close-on-exec, before the
   child starts, so the child's cwd cannot change their resolution and no
   other child started meanwhile inherits them; the child receives them on
   0/1/2 through file actions (dup2 clears close-on-exec on the copy).
   posix_spawnp, unlike fork, is safe in a program that runs threads: the
   child runs no code of ours between its start and the exec. Answers the
   child's pid; -1 when the redirections could not be opened, the directory
   could not be entered, or the program could not be executed (the spawn
   reports each). argv element buffers are NUL-terminated and borrowed. */
#include <spawn.h>
extern char** environ;
static pid_t z_os_spawn_start(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath
);
static pid_t z_os_spawn_start(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath
) {
    if (argv->length == 0) return -1;
    char** c_argv = (char**)z_xmalloc((argv->length + 1) * sizeof(char*));
    for (uint64_t i = 0; i < argv->length; i++) {
        c_argv[i] = argv->data[i].data;
    }
    c_argv[argv->length] = NULL;
    int inherit_out = (outPath.size == 0);
    int inherit_err = (errPath.size == 0);
    int inherit_in = (inherit_out && inherit_err);
    char* cwd_c = z_sv_to_cstr(cwd);
    char* out_c = inherit_out ? NULL : z_sv_to_cstr(outPath);
    char* err_c = inherit_err ? NULL : z_sv_to_cstr(errPath);
    int out_fd = inherit_out ? -1 : open(out_c, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    int err_fd = inherit_err ? -1 : open(err_c, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    int null_fd = inherit_in ? -1 : open("/dev/null", O_RDONLY | O_CLOEXEC);
    pid_t pid = -1;
    int setup_ok = (inherit_out || out_fd >= 0) && (inherit_err || err_fd >= 0)
                   && (inherit_in || null_fd >= 0);
    if (setup_ok) {
        posix_spawn_file_actions_t fa;
        posix_spawn_file_actions_init(&fa);
        int fa_ok = 1;
        if (cwd.size > 0 && posix_spawn_file_actions_addchdir_np(&fa, cwd_c) != 0) fa_ok = 0;
        if (!inherit_in && posix_spawn_file_actions_adddup2(&fa, null_fd, 0) != 0) fa_ok = 0;
        if (!inherit_out && posix_spawn_file_actions_adddup2(&fa, out_fd, 1) != 0) fa_ok = 0;
        if (!inherit_err && posix_spawn_file_actions_adddup2(&fa, err_fd, 2) != 0) fa_ok = 0;
        pid_t child = -1;
        if (fa_ok && posix_spawnp(&child, c_argv[0], &fa, NULL, c_argv, environ) == 0) pid = child;
        posix_spawn_file_actions_destroy(&fa);
    }
    if (out_fd >= 0) close(out_fd);
    if (err_fd >= 0) close(err_fd);
    if (null_fd >= 0) close(null_fd);
    free(cwd_c);
    free(out_c);
    free(err_c);
    free(c_argv);
    return pid;
}
