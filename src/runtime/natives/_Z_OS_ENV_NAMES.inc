/* os.env_names -- snapshot of environ keys. Each entry in
   environ is `key=value`; we copy the portion before the first
   `=` into a freshly-allocated z_String_t and push onto an
   owned list. The list's destructor frees each string. */
extern char** environ;
static z_List_String_t z_os_envNames(void);
static z_List_String_t z_os_envNames(void) {
    z_List_String_t out = z_List_String_create((uint64_t)0);
    if (environ == NULL) return out;
    for (char** ep = environ; *ep != NULL; ep++) {
        const char* entry = *ep;
        const char* eq = strchr(entry, '=');
        size_t n = (eq != NULL) ? (size_t)(eq - entry) : strlen(entry);
        z_String_t s = z_String_create((uint64_t)n);
        z_String_append(&s, entry, (uint64_t)n);
        z_List_String_append(&out, s);
    }
    return out;
}

