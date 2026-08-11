/* os.home_dir -- read $HOME from the environment. No passwd
   fallback: callers who care about stripped environments must
   set HOME explicitly. */
static z_@Result_String_IoError@_t z_os_homeDir(void);
static z_@Result_String_IoError@_t z_os_homeDir(void) {
    z_@Result_String_IoError@_t out = {0};
    const char* h = getenv("HOME");
    if (h == NULL) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        z_@IoError@_t v = {0};
        v.tag = Z_IOERROR_TAG_OTHER;
        z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
        *sp = z_@String@_new("$HOME is not set");
        v.data = sp;
        *boxed = v;
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(h);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}

