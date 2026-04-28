typedef struct {
    const char* data;    /* pointer into string buffer or .rodata */
    uint64_t length;     /* byte count of the viewed region */
} z_StringView_t;

static void z_StringView_print(z_StringView_t sv) {
    printf("%.*s\n", (int)sv.length, sv.data);
}

static bool z_StringView_eq(z_StringView_t a, z_StringView_t b) {
    return a.length == b.length && memcmp(a.data, b.data, a.length) == 0;
}

/* byte-wise lexicographic compare: returns -1 / 0 / 1. Shorter
   view is less than longer when they share a prefix. */
static int32_t z_StringView_cmp(z_StringView_t a, z_StringView_t b) {
    uint64_t n = a.length < b.length ? a.length : b.length;
    int c = n > 0 ? memcmp(a.data, b.data, n) : 0;
    if (c != 0) return c < 0 ? -1 : 1;
    if (a.length < b.length) return -1;
    if (a.length > b.length) return 1;
    return 0;
}

static z_String_t z_String_from_view(z_StringView_t sv) {
    z_String_t z = {0};
    z.size = sv.length;
    z.capacity = sv.length + 1;
    z.data = (char*)z_xmalloc(z.capacity);
    memcpy(z.data, sv.data, sv.length);
    z.data[sv.length] = '\0';
    return z;
}

