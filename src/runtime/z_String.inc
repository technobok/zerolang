typedef struct {
    uint64_t size;       /* byte count of current content */
    char* data;          /* heap-allocated data buffer */
    uint64_t capacity;   /* allocated buffer size */
} z_@String@_t;

/*@@row @prelude.String_new demand=@String_new needs=@xmalloc@@*/
static z_@String@_t z_@String@_new(const char* s) {
    z_@String@_t z = {0};
    z.size = (uint64_t)strlen(s);
    z.capacity = z.size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    memcpy(z.data, s, z.size);
    z.data[z.size] = '\0';
    return z;
}

/*@@end@@*/
/*@@row @prelude.String_cat demand=@String_cat needs=@xmalloc@@*/
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

/*@@end@@*/
/*@@row @prelude.String_from_f64 demand=@String_from_f64 needs=@String_new@@*/
static z_@String@_t z_@String@_from_f64(double n) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%g", n);
    return z_@String@_new(buf);
}

/*@@end@@*/
/*@@row @prelude.String_print demand=@String_print needs=@write_line@@*/
static void z_@String@_print(const z_@String@_t* s) {
    z_write_line(1, s->data, s->size, 1);
}

/*@@end@@*/
/*@@row @prelude.String_free demand=@String_free@@*/
static void z_@String@_free(z_@String@_t* s) {
    if (s && s->data) free(s->data);
}

/*@@end@@*/
/*@@row @prelude.String_eq demand=@String_eq@@*/
static bool z_@String@_eq(z_@String@_t* a, z_@String@_t* b) {
    if (a == b) return true;
    if (a->size != b->size) return false;
    return a->size == 0 || memcmp(a->data, b->data, a->size) == 0;
}

/*@@end@@*/
/*@@row @prelude.String_cmp demand=@String_cmp@@*/
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

/*@@end@@*/
/*@@row @prelude.String_reserve demand=@String_reserve needs=@xrealloc@@*/
static void z_@String@_reserve(z_@String@_t* s, uint64_t additional) {
    uint64_t needed = s->size + additional;
    if (needed <= s->capacity) return;
    uint64_t new_cap = s->capacity + (s->capacity >> 1) + 16;
    if (new_cap < needed) new_cap = needed;
    s->data = (char*)z_xrealloc(s->data, new_cap + 1);
    s->capacity = new_cap;
}

/*@@end@@*/
/*@@row @prelude.String_append demand=@String_append needs=@String_reserve@@*/
static void z_@String@_append(z_@String@_t* s, const char* data, uint64_t len) {
    if (len == 0) return;
    z_@String@_reserve(s, len);
    memcpy(s->data + s->size, data, len);
    s->size += len;
    s->data[s->size] = '\0';
}

/*@@end@@*/
/*@@row @prelude.String_append_byte demand=@String_append_byte needs=@String_reserve@@*/
static void z_@String@_append_byte(z_@String@_t* s, uint8_t b) {
    z_@String@_reserve(s, 1);
    s->data[s->size] = (char)b;
    s->size += 1;
    s->data[s->size] = '\0';
}

/*@@end@@*/
/*@@row @prelude.String_shrink demand=@String_shrink needs=@xrealloc@@*/
static void z_@String@_shrink(z_@String@_t* s) {
    if (s->size == s->capacity) return;
    s->data = (char*)z_xrealloc(s->data, s->size + 1);
    s->capacity = s->size;
}

/*@@end@@*/
/*@@row @prelude.String_create demand=@String_create needs=@xmalloc@@*/
static z_@String@_t z_@String@_create(uint64_t cap) {
    z_@String@_t z = {0};
    z.capacity = cap;
    if (cap > 0) {
        z.data = (char*)z_xmalloc(cap + 1);
        z.data[0] = '\0';
    }
    return z;
}

/*@@end@@*/
/*@@row @prelude.String_copy demand=@String_copy needs=@xmalloc@@*/
static z_@String@_t z_@String@_copy(const z_@String@_t* s) {
    z_@String@_t z = {0};
    z.size = s->size;
    /* an empty source needs no buffer, exactly as an empty view needs none:
       the zero struct IS an empty String. Its capacity then reads 0, which is
       what the other two producers -- create(0) and from_view -- have always
       answered for one. */
    if (s->size == 0) return z;
    z.capacity = s->size + 1;
    z.data = (char*)z_xmalloc(z.capacity + 1);
    memcpy(z.data, s->data, s->size);
    z.data[z.size] = '\0';
    return z;
}

/*@@end@@*/
