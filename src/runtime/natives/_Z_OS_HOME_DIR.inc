/* os.home_dir -- read $HOME from the environment, or on Windows
   %USERPROFILE% ($HOME is a POSIX notion there, as Rust holds). No
   passwd fallback: callers who care about stripped environments must
   set the variable explicitly. */
static z_@Result_String_IoError@_t z_os_homeDir(void);
static z_@Result_String_IoError@_t z_os_homeDir(void) {
    z_@Result_String_IoError@_t out = {0};
#if defined(_WIN32)
    char* h = z_win_getenv("USERPROFILE", 11);
    const char* why = "%USERPROFILE% is not set";
#else
    const char* h = getenv("HOME");
    const char* why = "$HOME is not set";
#endif
    if (h == NULL) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        z_@IoError@_t v = {0};
        v.tag = @ARM_IoError.other@;
        z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
        *sp = z_@String@_new(why);
        v.data = sp;
        *boxed = v;
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(h);
#if defined(_WIN32)
    free(h);
#endif
    out.tag = @ARM_Result_String_IoError.ok@;
    out.data = boxed;
    return out;
}
