/* os.spawnAsync -- start argv like os.spawn (z_os_spawn_start: same PATH
   resolution, redirection and cwd rules) but return immediately with the
   child's pid; -1 on setup/start failure. No timeout: the caller reaps with
   waitAny and enforces deadlines with monotonicMillis + kill. */
static int32_t z_os_spawnAsync(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath
);
static int32_t z_os_spawnAsync(
    const z_@List_String@_t* argv, z_@StringView@_t cwd, z_@StringView@_t outPath,
    z_@StringView@_t errPath
) {
    return (int32_t)z_os_spawn_start(argv, cwd, outPath, errPath);
}
