/* sqlite's Row helpers: the statement a row reads, and the check every typed
   getter makes first -- the column exists, and which storage class it holds */
/*@@row @sqlite.sqlite_row_stmt demand=@sqlite_row_stmt needs=@sqlite3_stmt@@*/
static sqlite3_stmt* z_sqlite_row_stmt(const z_@Row@_t* r) {
    return (sqlite3_stmt*)(uintptr_t)r->@MEMBER_Row.handle@;
}
/*@@end@@*/
/*@@row @sqlite.sqlite_row_type demand=@sqlite_row_type needs=@sqlite3_column_count,@sqlite3_column_type,@sqlite3_stmt,@sqlite_row_stmt@@*/
/* the column's storage class (SQLITE_INTEGER ...), 0 past the last column */
static int z_sqlite_row_type(const z_@Row@_t* r, uint64_t column) {
    sqlite3_stmt* st = z_sqlite_row_stmt(r);
    if (column >= (uint64_t)sqlite3_column_count(st)) return 0;
    return sqlite3_column_type(st, (int)column);
}
/*@@end@@*/
/*@@row @sqlite.sqlite_utf8 demand=@sqlite_utf8@@*/
/* whether `n` bytes at `p` are well-formed UTF-8 (no overlongs, surrogates
   or code points past U+10FFFF) */
static bool z_sqlite_utf8(const unsigned char* p, uint64_t n) {
    uint64_t i = 0;
    while (i < n) {
        unsigned char c = p[i];
        if (c < 0x80) { i++; continue; }
        uint64_t len; uint32_t cp;
        if ((c & 0xE0) == 0xC0) { len = 2; cp = c & 0x1F; }
        else if ((c & 0xF0) == 0xE0) { len = 3; cp = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { len = 4; cp = c & 0x07; }
        else return false;
        if (i + len > n) return false;
        for (uint64_t k = 1; k < len; k++) {
            if ((p[i + k] & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (p[i + k] & 0x3F);
        }
        if ((len == 2 && cp < 0x80) || (len == 3 && cp < 0x800) || (len == 4 && cp < 0x10000)) return false;
        if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        i += len;
    }
    return true;
}
/*@@end@@*/
