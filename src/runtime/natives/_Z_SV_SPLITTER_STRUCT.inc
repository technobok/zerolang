/* Splitter / linesiter state. Classes are declared `is native`
   with no user fields; the runtime supplies the real layout,
   stack-allocated value type (48 bytes). Both iterators share
   the same struct — linesiter leaves sep NULL. */
typedef struct {
    const char* src;
    uint64_t    src_len;
    const char* sep;
    uint64_t    sep_len;
    uint64_t    cursor;
    bool        done;
} z_Splitter_t;

typedef z_Splitter_t z_LinesIter_t;

