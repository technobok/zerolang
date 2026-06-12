/* os.get_env -- look up the POSIX environment by name. Returns
   option.some(string) on hit (copying the value so the caller
   owns the heap buffer and libc's environ isn't aliased); returns
   option.none on miss. `key` is a borrowed view; caller retains. */
static z_Option_String_t z_os_env(z_StringView_t key);
static z_Option_String_t z_os_env(z_StringView_t key) {
    z_Option_String_t out = {0};
    char* key_cstr = z_sv_to_cstr(key);
    const char* v = getenv(key_cstr);
    free(key_cstr);
    if (v == NULL) {
        out.tag = Z_OPTION_STRING_TAG_NONE;
        return out;
    }
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = z_String_new(v);
    out.tag = Z_OPTION_STRING_TAG_SOME;
    out.data = boxed;
    return out;
}

