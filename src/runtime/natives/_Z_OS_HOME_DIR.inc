/* os.home_dir -- read $HOME from the environment. No passwd
   fallback: callers who care about stripped environments must
   set HOME explicitly. */
static z_Result_String_IoError_t z_os_homeDir(void);
static z_Result_String_IoError_t z_os_homeDir(void) {
    z_Result_String_IoError_t out = {0};
    const char* h = getenv("HOME");
    if (h == NULL) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        z_IoError_t v = {0};
        v.tag = Z_IOERROR_TAG_OTHER;
        z_String_t* sp = (z_String_t*)z_xmalloc(sizeof(z_String_t));
        *sp = z_String_new("$HOME is not set");
        v.data = sp;
        *boxed = v;
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = z_String_new(h);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}

