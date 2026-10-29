/* sqlite -- the part of SQLite's C interface this unit's fragments call,
   declared rather than included: the emitted C stays one file, and the
   library's object comes from zc's cache. sqlite-decls-guard compiles these
   after vendor/sqlite/sqlite3.h, so a declaration or a constant that drifts
   from the vendored library fails the build. */
/*@@row @sqlite.sqlite3 demand=@sqlite3@@*/
typedef struct sqlite3 sqlite3;
/*@@end@@*/
/*@@row @sqlite.sqlite3_stmt demand=@sqlite3_stmt@@*/
typedef struct sqlite3_stmt sqlite3_stmt;
/*@@end@@*/
/*@@row @sqlite.sqlite3_int64 demand=@sqlite3_int64@@*/
typedef long long int sqlite3_int64;
/*@@end@@*/
/*@@row @sqlite.sqlite3_uint64 demand=@sqlite3_uint64@@*/
typedef unsigned long long int sqlite3_uint64;
/*@@end@@*/
/*@@row @sqlite.sqlite3_open_v2 demand=@sqlite3_open_v2 needs=sqlite.@sqlite3@@*/
int sqlite3_open_v2(const char *filename, sqlite3 **ppDb, int flags, const char *zVfs);
/*@@end@@*/
/*@@row @sqlite.sqlite3_close_v2 demand=@sqlite3_close_v2 needs=sqlite.@sqlite3@@*/
int sqlite3_close_v2(sqlite3*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_exec demand=@sqlite3_exec needs=sqlite.@sqlite3@@*/
int sqlite3_exec(sqlite3*, const char *sql, int (*callback)(void*,int,char**,char**), void *, char **errmsg);
/*@@end@@*/
/*@@row @sqlite.sqlite3_errmsg demand=@sqlite3_errmsg needs=sqlite.@sqlite3@@*/
const char *sqlite3_errmsg(sqlite3*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_extended_errcode demand=@sqlite3_extended_errcode needs=sqlite.@sqlite3@@*/
int sqlite3_extended_errcode(sqlite3 *db);
/*@@end@@*/
/*@@row @sqlite.sqlite3_error_offset demand=@sqlite3_error_offset needs=sqlite.@sqlite3@@*/
int sqlite3_error_offset(sqlite3 *db);
/*@@end@@*/
/*@@row @sqlite.sqlite3_errstr demand=@sqlite3_errstr@@*/
const char *sqlite3_errstr(int);
/*@@end@@*/
/*@@row @sqlite.sqlite3_libversion demand=@sqlite3_libversion@@*/
const char *sqlite3_libversion(void);
/*@@end@@*/
/*@@row @sqlite.sqlite3_busy_timeout demand=@sqlite3_busy_timeout needs=sqlite.@sqlite3@@*/
int sqlite3_busy_timeout(sqlite3*, int ms);
/*@@end@@*/
/*@@row @sqlite.sqlite3_prepare_v3 demand=@sqlite3_prepare_v3 needs=sqlite.@sqlite3,sqlite.@sqlite3_stmt@@*/
int sqlite3_prepare_v3(sqlite3 *db, const char *zSql, int nByte, unsigned int prepFlags, sqlite3_stmt **ppStmt, const char **pzTail);
/*@@end@@*/
/*@@row @sqlite.sqlite3_finalize demand=@sqlite3_finalize needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_finalize(sqlite3_stmt *pStmt);
/*@@end@@*/
/*@@row @sqlite.sqlite3_step demand=@sqlite3_step needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_step(sqlite3_stmt*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_reset demand=@sqlite3_reset needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_reset(sqlite3_stmt *pStmt);
/*@@end@@*/
/*@@row @sqlite.sqlite3_clear_bindings demand=@sqlite3_clear_bindings needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_clear_bindings(sqlite3_stmt*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_sql demand=@sqlite3_sql needs=sqlite.@sqlite3_stmt@@*/
const char *sqlite3_sql(sqlite3_stmt *pStmt);
/*@@end@@*/
/*@@row @sqlite.sqlite3_changes64 demand=@sqlite3_changes64 needs=sqlite.@sqlite3,sqlite.@sqlite3_int64@@*/
sqlite3_int64 sqlite3_changes64(sqlite3*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_total_changes64 demand=@sqlite3_total_changes64 needs=sqlite.@sqlite3,sqlite.@sqlite3_int64@@*/
sqlite3_int64 sqlite3_total_changes64(sqlite3*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_last_insert_rowid demand=@sqlite3_last_insert_rowid needs=sqlite.@sqlite3,sqlite.@sqlite3_int64@@*/
sqlite3_int64 sqlite3_last_insert_rowid(sqlite3*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_get_autocommit demand=@sqlite3_get_autocommit needs=sqlite.@sqlite3@@*/
int sqlite3_get_autocommit(sqlite3*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_carray_bind demand=@sqlite3_carray_bind needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_carray_bind(sqlite3_stmt *pStmt, int i, void *aData, int nData, int mFlags, void (*xDel)(void*));
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_int64 demand=@sqlite3_bind_int64 needs=sqlite.@sqlite3_int64,sqlite.@sqlite3_stmt@@*/
int sqlite3_bind_int64(sqlite3_stmt*, int, sqlite3_int64);
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_double demand=@sqlite3_bind_double needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_bind_double(sqlite3_stmt*, int, double);
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_null demand=@sqlite3_bind_null needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_bind_null(sqlite3_stmt*, int);
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_text64 demand=@sqlite3_bind_text64 needs=sqlite.@sqlite3_stmt,sqlite.@sqlite3_uint64@@*/
int sqlite3_bind_text64(sqlite3_stmt*, int, const char*, sqlite3_uint64, void(*)(void*), unsigned char encoding);
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_blob64 demand=@sqlite3_bind_blob64 needs=sqlite.@sqlite3_stmt,sqlite.@sqlite3_uint64@@*/
int sqlite3_bind_blob64(sqlite3_stmt*, int, const void*, sqlite3_uint64, void(*)(void*));
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_parameter_count demand=@sqlite3_bind_parameter_count needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_bind_parameter_count(sqlite3_stmt*);
/*@@end@@*/
/*@@row @sqlite.sqlite3_bind_parameter_index demand=@sqlite3_bind_parameter_index needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_bind_parameter_index(sqlite3_stmt*, const char *zName);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_count demand=@sqlite3_column_count needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_column_count(sqlite3_stmt *pStmt);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_type demand=@sqlite3_column_type needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_column_type(sqlite3_stmt*, int iCol);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_int64 demand=@sqlite3_column_int64 needs=sqlite.@sqlite3_int64,sqlite.@sqlite3_stmt@@*/
sqlite3_int64 sqlite3_column_int64(sqlite3_stmt*, int iCol);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_double demand=@sqlite3_column_double needs=sqlite.@sqlite3_stmt@@*/
double sqlite3_column_double(sqlite3_stmt*, int iCol);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_text demand=@sqlite3_column_text needs=sqlite.@sqlite3_stmt@@*/
const unsigned char *sqlite3_column_text(sqlite3_stmt*, int iCol);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_blob demand=@sqlite3_column_blob needs=sqlite.@sqlite3_stmt@@*/
const void *sqlite3_column_blob(sqlite3_stmt*, int iCol);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_bytes demand=@sqlite3_column_bytes needs=sqlite.@sqlite3_stmt@@*/
int sqlite3_column_bytes(sqlite3_stmt*, int iCol);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_name demand=@sqlite3_column_name needs=sqlite.@sqlite3_stmt@@*/
const char *sqlite3_column_name(sqlite3_stmt*, int N);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_decltype demand=@sqlite3_column_decltype needs=sqlite.@sqlite3_stmt@@*/
const char *sqlite3_column_decltype(sqlite3_stmt*,int);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_database_name demand=@sqlite3_column_database_name needs=sqlite.@sqlite3_stmt@@*/
const char *sqlite3_column_database_name(sqlite3_stmt*,int);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_table_name demand=@sqlite3_column_table_name needs=sqlite.@sqlite3_stmt@@*/
const char *sqlite3_column_table_name(sqlite3_stmt*,int);
/*@@end@@*/
/*@@row @sqlite.sqlite3_column_origin_name demand=@sqlite3_column_origin_name needs=sqlite.@sqlite3_stmt@@*/
const char *sqlite3_column_origin_name(sqlite3_stmt*,int);
/*@@end@@*/
/*@@row @sqlite.SQLITE_OK demand=@SQLITE_OK@@*/
#define SQLITE_OK           0
/*@@end@@*/
/*@@row @sqlite.SQLITE_ERROR demand=@SQLITE_ERROR@@*/
#define SQLITE_ERROR        1
/*@@end@@*/
/*@@row @sqlite.SQLITE_MISUSE demand=@SQLITE_MISUSE@@*/
#define SQLITE_MISUSE      21
/*@@end@@*/
/*@@row @sqlite.SQLITE_TOOBIG demand=@SQLITE_TOOBIG@@*/
#define SQLITE_TOOBIG      18
/*@@end@@*/
/*@@row @sqlite.SQLITE_RANGE demand=@SQLITE_RANGE@@*/
#define SQLITE_RANGE       25
/*@@end@@*/
/*@@row @sqlite.SQLITE_ROW demand=@SQLITE_ROW@@*/
#define SQLITE_ROW         100
/*@@end@@*/
/*@@row @sqlite.SQLITE_DONE demand=@SQLITE_DONE@@*/
#define SQLITE_DONE        101
/*@@end@@*/
/*@@row @sqlite.SQLITE_INTEGER demand=@SQLITE_INTEGER@@*/
#define SQLITE_INTEGER  1
/*@@end@@*/
/*@@row @sqlite.SQLITE_FLOAT demand=@SQLITE_FLOAT@@*/
#define SQLITE_FLOAT    2
/*@@end@@*/
/*@@row @sqlite.SQLITE_BLOB demand=@SQLITE_BLOB@@*/
#define SQLITE_BLOB     4
/*@@end@@*/
/*@@row @sqlite.SQLITE_NULL demand=@SQLITE_NULL@@*/
#define SQLITE_NULL     5
/*@@end@@*/
/*@@row @sqlite.SQLITE3_TEXT demand=@SQLITE3_TEXT@@*/
#define SQLITE3_TEXT     3
/*@@end@@*/
/*@@row @sqlite.SQLITE_UTF8 demand=@SQLITE_UTF8@@*/
#define SQLITE_UTF8           1
/*@@end@@*/
/*@@row @sqlite.SQLITE_CARRAY_INT64 demand=@SQLITE_CARRAY_INT64@@*/
#define SQLITE_CARRAY_INT64     1
/*@@end@@*/
/*@@row @sqlite.SQLITE_CARRAY_DOUBLE demand=@SQLITE_CARRAY_DOUBLE@@*/
#define SQLITE_CARRAY_DOUBLE    2
/*@@end@@*/
/*@@row @sqlite.SQLITE_CARRAY_TEXT demand=@SQLITE_CARRAY_TEXT@@*/
#define SQLITE_CARRAY_TEXT      3
/*@@end@@*/
/*@@row @sqlite.SQLITE_CARRAY_BLOB demand=@SQLITE_CARRAY_BLOB@@*/
#define SQLITE_CARRAY_BLOB      4
/*@@end@@*/
/*@@row @sqlite.SQLITE_OPEN_EXRESCODE demand=@SQLITE_OPEN_EXRESCODE@@*/
#define SQLITE_OPEN_EXRESCODE        0x02000000
/*@@end@@*/

