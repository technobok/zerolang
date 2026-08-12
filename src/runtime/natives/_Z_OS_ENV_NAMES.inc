/* os.env_names -- snapshot of environ keys. Each entry in
   environ is `key=value`; we copy the portion before the first
   `=` into a freshly-allocated z_@String@_t and push onto an
   owned list. The list's destructor frees each string. */
extern char** environ;
static z_@List_String@_t z_os_envNames(void);
static z_@List_String@_t z_os_envNames(void) {
    z_@List_String@_t out = z_@List_String@_create((uint64_t)0);
    if (environ == NULL) return out;
    for (char** ep = environ; *ep != NULL; ep++) {
        const char* entry = *ep;
        const char* eq = strchr(entry, '=');
        size_t n = (eq != NULL) ? (size_t)(eq - entry) : strlen(entry);
        z_@String@_t s = z_@String@_create((uint64_t)n);
        z_@String@_append(&s, entry, (uint64_t)n);
        z_@List_String@_append(&out, s);
    }
    return out;
}


