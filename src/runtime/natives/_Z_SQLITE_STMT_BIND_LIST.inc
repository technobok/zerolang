/* the carray binds' shared tail: SQLite copies the array, text and blob
   contents included (SQLITE_TRANSIENT), before the bind returns. An empty list
   yields no rows. z_sqlite_iovec is struct iovec's layout, which carray reads
   for a BLOB array, declared here as SQLite's declarations are. */
typedef struct { void* iov_base; size_t iov_len; } z_sqlite_iovec;

static void z_sqlite_bind_array(z_@Statement@_t* t, uint64_t parameter, void* data, uint64_t n, int kind) {
    sqlite3_stmt* st = z_sqlite_bind_target(t, parameter);
    if (!st) return;
    if (n > 0x7fffffffULL) {
        z_sqlite_bind_failed(t, SQLITE_TOOBIG, parameter);
        return;
    }
    /* SQLite cannot copy nothing (a 0-byte allocation fails), so an empty
       list is bound as a static empty array */
    static int64_t none = 0;
    int rc = n ? sqlite3_carray_bind(st, (int)parameter, data, (int)n, kind, Z_SQLITE_TRANSIENT)
               : sqlite3_carray_bind(st, (int)parameter, (void*)&none, 0, kind, (z_sqlite_destructor)0);
    if (rc != SQLITE_OK) z_sqlite_bind_failed(t, rc, parameter);
}

