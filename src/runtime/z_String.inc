typedef struct {
    uint64_t size;       /* byte count of current content */
    char* data;          /* heap-allocated data buffer */
    uint64_t capacity;   /* allocated buffer size */
} z_@String@_t;

static z_@String@_t z_@String@_new(const char* s) {
    z_@String@_t z = {0};
    z.size = (uint64_t)strlen(s);
    z.capacity = z.size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    memcpy(z.data, s, z.size);
    z.data[z.size] = '\0';
    return z;
}

static z_@String@_t z_@String@_cat(z_@String@_t* a, z_@String@_t* b) {
    z_@String@_t z = {0};
    z.size = a->size + b->size;
    z.capacity = z.size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    memcpy(z.data, a->data, a->size);
    memcpy(z.data + a->size, b->data, b->size);
    z.data[z.size] = '\0';
    return z;
}

static z_@String@_t z_@String@_from_i64(int64_t n) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", (long)n);
    return z_@String@_new(buf);
}

static z_@String@_t z_@String@_from_f64(double n) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%g", n);
    return z_@String@_new(buf);
}

static void z_@String@_print(z_@String@_t* s) {
    printf("%.*s\n", (int)s->size, s->data);
}

static void z_@String@_free(z_@String@_t* s) {
    if (s && s->data) free(s->data);
}

static bool z_@String@_eq(z_@String@_t* a, z_@String@_t* b) {
    if (a == b) return true;
    return a->size == b->size && memcmp(a->data, b->data, a->size) == 0;
}

/* byte-wise lexicographic compare: returns -1 / 0 / 1. Shorter
   string is less than longer when they share a prefix. */
static int32_t z_@String@_cmp(z_@String@_t* a, z_@String@_t* b) {
    if (a == b) return 0;
    uint64_t n = a->size < b->size ? a->size : b->size;
    int c = n > 0 ? memcmp(a->data, b->data, n) : 0;
    if (c != 0) return c < 0 ? -1 : 1;
    if (a->size < b->size) return -1;
    if (a->size > b->size) return 1;
    return 0;
}

static void z_@String@_reserve(z_@String@_t* s, uint64_t additional) {
    uint64_t needed = s->size + additional;
    if (needed <= s->capacity) return;
    uint64_t new_cap = s->capacity + (s->capacity >> 1) + 16;
    if (new_cap < needed) new_cap = needed;
    s->data = (char*)z_xrealloc(s->data, new_cap + 1);
    s->capacity = new_cap;
}

static void z_@String@_append(z_@String@_t* s, const char* data, uint64_t len) {
    z_@String@_reserve(s, len);
    memcpy(s->data + s->size, data, len);
    s->size += len;
    s->data[s->size] = '\0';
}

static void z_@String@_append_byte(z_@String@_t* s, uint8_t b) {
    z_@String@_reserve(s, 1);
    s->data[s->size] = (char)b;
    s->size += 1;
    s->data[s->size] = '\0';
}

static void z_@String@_shrink(z_@String@_t* s) {
    if (s->size == s->capacity) return;
    s->data = (char*)z_xrealloc(s->data, s->size + 1);
    s->capacity = s->size;
}

static z_@String@_t z_@String@_create(uint64_t cap) {
    z_@String@_t z = {0};
    z.capacity = cap;
    if (cap > 0) {
        z.data = (char*)z_xmalloc(cap + 1);
        z.data[0] = '\0';
    }
    return z;
}

static z_@String@_t z_@String@_copy(const z_@String@_t* s) {
    z_@String@_t z = {0};
    z.size = s->size;
    z.capacity = s->size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    if (s->size > 0) memcpy(z.data, s->data, s->size);
    z.data[z.size] = '\0';
    return z;
}

