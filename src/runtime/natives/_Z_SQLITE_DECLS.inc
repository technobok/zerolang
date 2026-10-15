/* sqlite -- the part of SQLite's C interface this unit's fragments call,
   declared rather than included: the emitted C stays one file, and the
   library's object comes from zc's cache. sqlite-decls-guard compiles these
   after vendor/sqlite/sqlite3.h, so a declaration or a constant that drifts
   from the vendored library fails the build. */
typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef long long int sqlite3_int64;
typedef unsigned long long int sqlite3_uint64;
int sqlite3_open_v2(const char *filename, sqlite3 **ppDb, int flags, const char *zVfs);
int sqlite3_close_v2(sqlite3*);
int sqlite3_exec(sqlite3*, const char *sql, int (*callback)(void*,int,char**,char**), void *, char **errmsg);
const char *sqlite3_errmsg(sqlite3*);
int sqlite3_extended_errcode(sqlite3 *db);
int sqlite3_error_offset(sqlite3 *db);
const char *sqlite3_errstr(int);
const char *sqlite3_libversion(void);
int sqlite3_busy_timeout(sqlite3*, int ms);
int sqlite3_prepare_v3(sqlite3 *db, const char *zSql, int nByte, unsigned int prepFlags, sqlite3_stmt **ppStmt, const char **pzTail);
int sqlite3_finalize(sqlite3_stmt *pStmt);
int sqlite3_step(sqlite3_stmt*);
int sqlite3_reset(sqlite3_stmt *pStmt);
int sqlite3_clear_bindings(sqlite3_stmt*);
const char *sqlite3_sql(sqlite3_stmt *pStmt);
sqlite3_int64 sqlite3_changes64(sqlite3*);
sqlite3_int64 sqlite3_total_changes64(sqlite3*);
sqlite3_int64 sqlite3_last_insert_rowid(sqlite3*);
int sqlite3_get_autocommit(sqlite3*);
int sqlite3_carray_bind(sqlite3_stmt *pStmt, int i, void *aData, int nData, int mFlags, void (*xDel)(void*));
int sqlite3_bind_int64(sqlite3_stmt*, int, sqlite3_int64);
int sqlite3_bind_double(sqlite3_stmt*, int, double);
int sqlite3_bind_null(sqlite3_stmt*, int);
int sqlite3_bind_text64(sqlite3_stmt*, int, const char*, sqlite3_uint64, void(*)(void*), unsigned char encoding);
int sqlite3_bind_blob64(sqlite3_stmt*, int, const void*, sqlite3_uint64, void(*)(void*));
int sqlite3_bind_parameter_count(sqlite3_stmt*);
int sqlite3_bind_parameter_index(sqlite3_stmt*, const char *zName);
int sqlite3_column_count(sqlite3_stmt *pStmt);
int sqlite3_column_type(sqlite3_stmt*, int iCol);
sqlite3_int64 sqlite3_column_int64(sqlite3_stmt*, int iCol);
double sqlite3_column_double(sqlite3_stmt*, int iCol);
const unsigned char *sqlite3_column_text(sqlite3_stmt*, int iCol);
const void *sqlite3_column_blob(sqlite3_stmt*, int iCol);
int sqlite3_column_bytes(sqlite3_stmt*, int iCol);
const char *sqlite3_column_name(sqlite3_stmt*, int N);
const char *sqlite3_column_decltype(sqlite3_stmt*,int);
const char *sqlite3_column_database_name(sqlite3_stmt*,int);
const char *sqlite3_column_table_name(sqlite3_stmt*,int);
const char *sqlite3_column_origin_name(sqlite3_stmt*,int);
#define SQLITE_OK           0
#define SQLITE_ERROR        1
#define SQLITE_MISUSE      21
#define SQLITE_TOOBIG      18
#define SQLITE_RANGE       25
#define SQLITE_ROW         100
#define SQLITE_DONE        101
#define SQLITE_INTEGER  1
#define SQLITE_FLOAT    2
#define SQLITE_BLOB     4
#define SQLITE_NULL     5
#define SQLITE3_TEXT     3
#define SQLITE_UTF8           1
#define SQLITE_CARRAY_INT64     1
#define SQLITE_CARRAY_DOUBLE    2
#define SQLITE_CARRAY_TEXT      3
#define SQLITE_CARRAY_BLOB      4
#define SQLITE_OPEN_EXRESCODE        0x02000000

