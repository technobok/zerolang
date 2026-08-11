typedef struct {
    uint64_t size;       /* byte count of the viewed region */
    const char* data;    /* pointer into string buffer or .rodata */
} z_@StringView@_t;

static void z_@StringView@_print(z_@StringView@_t sv) {
    printf("%.*s\n", (int)sv.size, sv.data);
}

static bool z_@StringView@_eq(z_@StringView@_t a, z_@StringView@_t b) {
    return a.size == b.size && memcmp(a.data, b.data, a.size) == 0;
}

/* byte-wise lexicographic compare: returns -1 / 0 / 1. Shorter
   view is less than longer when they share a prefix. */
static int32_t z_@StringView@_cmp(z_@StringView@_t a, z_@StringView@_t b) {
    uint64_t n = a.size < b.size ? a.size : b.size;
    int c = n > 0 ? memcmp(a.data, b.data, n) : 0;
    if (c != 0) return c < 0 ? -1 : 1;
    if (a.size < b.size) return -1;
    if (a.size > b.size) return 1;
    return 0;
}

static z_@String@_t z_@String@_from_view(z_@StringView@_t sv) {
    z_@String@_t z = {0};
    z.size = sv.size;
    z.capacity = sv.size;
    z.data = (char*)z_xmalloc(sv.size + 1);
    memcpy(z.data, sv.data, sv.size);
    z.data[sv.size] = '\0';
    return z;
}

