/* os.spawnAsync -- start argv like os.spawn (z_os_spawn_start: same PATH
   resolution, redirection and cwd rules) but return immediately with the
   Child: its pid and the OS's handle on it. The error says why the start
   failed. No timeout: the caller reaps with waitAny and enforces deadlines
   with monotonicMillis and Child.kill. */
static z_@Result_Child_IoError@_t z_os_spawnAsync(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath
);
static z_@Result_Child_IoError@_t z_os_spawnAsync(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath
) {
    z_@Result_Child_IoError@_t result = {0};
    int32_t pid = 0;
    int64_t handle = -1;
    int e = z_os_spawn_start(argv, cwd, outPath, errPath, &pid, &handle);
    if (e != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_Result_Child_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@Child@_t* boxed = (z_@Child@_t*)z_xmalloc(sizeof(z_@Child@_t));
    boxed->@MEMBER_Child.pid@ = pid;
    boxed->@MEMBER_Child.handle@ = handle;
    boxed->@MEMBER_Child.open@ = true;
    boxed->@MEMBER_Child.reaped@ = false;
    result.tag = @ARM_Result_Child_IoError.ok@;
    result.data = boxed;
    return result;
}
