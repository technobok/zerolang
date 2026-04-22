typedef struct {
    uint64_t size;       /* byte count of current content */
    char* data;          /* heap-allocated data buffer */
    uint64_t capacity;   /* allocated buffer size */
} z_string_t;

static z_string_t z_string_new(const char* s) {
    z_string_t z = {0};
    z.size = (uint64_t)strlen(s);
    z.capacity = z.size + 1;
    z.data = (char*)malloc(z.capacity);
    memcpy(z.data, s, z.size);
    z.data[z.size] = '\0';
    return z;
}

static z_string_t z_string_cat(z_string_t* a, z_string_t* b) {
    z_string_t z = {0};
    z.size = a->size + b->size;
    z.capacity = z.size + 1;
    z.data = (char*)malloc(z.capacity);
    memcpy(z.data, a->data, a->size);
    memcpy(z.data + a->size, b->data, b->size);
    z.data[z.size] = '\0';
    return z;
}

static z_string_t z_string_from_i64(int64_t n) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", (long)n);
    return z_string_new(buf);
}

static z_string_t z_string_from_f64(double n) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%g", n);
    return z_string_new(buf);
}

static void z_string_print(z_string_t* s) {
    printf("%.*s\n", (int)s->size, s->data);
}

static void z_string_free(z_string_t* s) {
    if (s && s->data) free(s->data);
}

static bool z_string_eq(z_string_t* a, z_string_t* b) {
    if (a == b) return true;
    return a->size == b->size && memcmp(a->data, b->data, a->size) == 0;
}

/* byte-wise lexicographic compare: returns -1 / 0 / 1. Shorter
   string is less than longer when they share a prefix. */
static int32_t z_string_cmp(z_string_t* a, z_string_t* b) {
    if (a == b) return 0;
    uint64_t n = a->size < b->size ? a->size : b->size;
    int c = n > 0 ? memcmp(a->data, b->data, n) : 0;
    if (c != 0) return c < 0 ? -1 : 1;
    if (a->size < b->size) return -1;
    if (a->size > b->size) return 1;
    return 0;
}

static void z_string_reserve(z_string_t* s, uint64_t additional) {
    uint64_t needed = s->size + additional;
    if (needed <= s->capacity) return;
    uint64_t new_cap = s->capacity + (s->capacity >> 1) + 16;
    if (new_cap < needed) new_cap = needed;
    s->data = (char*)realloc(s->data, new_cap + 1);
    s->capacity = new_cap;
}

static void z_string_append(z_string_t* s, const char* data, uint64_t len) {
    z_string_reserve(s, len);
    memcpy(s->data + s->size, data, len);
    s->size += len;
    s->data[s->size] = '\0';
}

static void z_string_shrink(z_string_t* s) {
    if (s->size == s->capacity) return;
    s->data = (char*)realloc(s->data, s->size + 1);
    s->capacity = s->size;
}

static z_string_t z_string_create(uint64_t cap) {
    z_string_t z = {0};
    z.capacity = cap;
    if (cap > 0) {
        z.data = (char*)malloc(cap + 1);
        z.data[0] = '\0';
    }
    return z;
}

