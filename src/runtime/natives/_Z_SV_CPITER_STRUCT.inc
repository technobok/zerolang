/* Codepoint iterator state. Opaque to zerolang. */
typedef struct {
    const char* data;
    uint64_t    length;
    uint64_t    cursor;
} z_CpIter_t;

