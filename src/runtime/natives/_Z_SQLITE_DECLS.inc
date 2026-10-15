/* sqlite -- the part of SQLite's C interface this unit's fragments call,
   declared rather than included: the emitted C stays one file, and the
   library's object comes from zc's cache. sqlite-decls-guard compiles these
   after vendor/sqlite/sqlite3.h, so a declaration or a constant that drifts
   from the vendored library fails the build. */
typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef long long int sqlite3_int64;
int sqlite3_open_v2(const char *filename, sqlite3 **ppDb, int flags, const char *zVfs);
int sqlite3_close_v2(sqlite3*);
int sqlite3_exec(sqlite3*, const char *sql, int (*callback)(void*,int,char**,char**), void *, char **errmsg);
const char *sqlite3_errmsg(sqlite3*);
int sqlite3_extended_errcode(sqlite3 *db);
int sqlite3_error_offset(sqlite3 *db);
const char *sqlite3_errstr(int);
const char *sqlite3_libversion(void);
int sqlite3_busy_timeout(sqlite3*, int ms);
#define SQLITE_OK           0
#define SQLITE_MISUSE      21
#define SQLITE_OPEN_EXRESCODE        0x02000000

