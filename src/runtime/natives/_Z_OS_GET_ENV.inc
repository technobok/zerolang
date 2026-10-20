/* os.get_env -- look up the environment by name (Win32's on Windows,
   where names match regardless of case). Returns
   option.some(string) on hit (copying the value so the caller
   owns the heap buffer and libc's environ isn't aliased); returns
   option.none on miss. `key` is a borrowed view; caller retains. */
static z_@Option_String@_t z_os_env(z_@StringView@_t key);
static z_@Option_String@_t z_os_env(z_@StringView@_t key) {
    z_@Option_String@_t out = {0};
#if defined(_WIN32)
    char* v = z_win_getenv(key.data, key.size);
#else
    char* key_cstr = z_sv_to_cstr(key);
    const char* v = getenv(key_cstr);
    free(key_cstr);
#endif
    if (v == NULL) {
        out.tag = @ARM_Option_String.none@;
        return out;
    }
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(v);
#if defined(_WIN32)
    free(v);
#endif
    out.tag = @ARM_Option_String.some@;
    out.data = boxed;
    return out;
}


