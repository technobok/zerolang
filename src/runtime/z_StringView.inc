typedef struct {
    /* transitional: `length` aliases `size` until the seed emits `size` */
    union { uint64_t size; uint64_t length; };  /* byte count of the viewed region */
    const char* data;    /* pointer into string buffer or .rodata */
} z_StringView_t;

static void z_StringView_print(z_StringView_t sv) {
    printf("%.*s\n", (int)sv.size, sv.data);
}

static bool z_StringView_eq(z_StringView_t a, z_StringView_t b) {
    return a.size == b.size && memcmp(a.data, b.data, a.size) == 0;
}

/* byte-wise lexicographic compare: returns -1 / 0 / 1. Shorter
   view is less than longer when they share a prefix. */
static int32_t z_StringView_cmp(z_StringView_t a, z_StringView_t b) {
    uint64_t n = a.size < b.size ? a.size : b.size;
    int c = n > 0 ? memcmp(a.data, b.data, n) : 0;
    if (c != 0) return c < 0 ? -1 : 1;
    if (a.size < b.size) return -1;
    if (a.size > b.size) return 1;
    return 0;
}

static z_String_t z_String_from_view(z_StringView_t sv) {
    z_String_t z = {0};
    z.size = sv.size;
    z.capacity = sv.size;
    z.data = (char*)z_xmalloc(sv.size + 1);
    memcpy(z.data, sv.data, sv.size);
    z.data[sv.size] = '\0';
    return z;
}

