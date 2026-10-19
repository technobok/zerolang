SQLite 3.54.0, public domain (https://sqlite.org/copyright.html).

Vendored from https://sqlite.org/2026/sqlite-amalgamation-3540000.zip, SHA3-256
7b670a62fdfbd672b75fef004cb703c8a3e87d3a5cc7d675b4a08337004a2d93 (the hash
sqlite.org publishes), on 2026-10-16. Copied: sqlite3.c and sqlite3.h only --
the shell (shell.c) and the loadable-extension header (sqlite3ext.h) are not
built here.

## How it is built

Nothing in `make` builds it. `zc` compiles sqlite3.c ON DEMAND, the first time a
program that reaches the `sqlite` unit is linked, with the compiler that links
that program, and keeps the object in its cache keyed by the source's hash, the
compiler, the target and the flags. The object is linked statically. The flags
are fixed -- `-O2 -DNDEBUG -w` plus the options below -- whatever the program is
built with: a debug build of a program does not rebuild a 9.5 MB library, and
the defines must match between every object that includes sqlite3.h.

## Compile-time options

The same for every target. Each one, and why:

| Option | Why |
|---|---|
| SQLITE_THREADSAFE=0 | A program that starts no thread uses SQLite from one thread: no mutexes. A program that starts one builds with SQLITE_THREADSAFE=2 instead (natives.tbl, `when=threaded`): SQLite locks its own shared state, and a connection is used by one thread at a time, which `thread.Unshareable` on the sqlite types enforces. |
| SQLITE_OMIT_LOAD_EXTENSION | no dlopen of extension libraries; the extensions we want are compiled in |
| SQLITE_DQS=0 | a double-quoted string is an identifier, never a string literal (the documented misfeature, off) |
| SQLITE_DEFAULT_MEMSTATUS=0 | no global memory-statistics bookkeeping on every allocation |
| SQLITE_DEFAULT_WAL_SYNCHRONOUS=1 | NORMAL in WAL mode: durable across application crashes, the recommended WAL setting |
| SQLITE_OMIT_DEPRECATED | the deprecated interfaces are not bound |
| SQLITE_OMIT_SHARED_CACHE | shared-cache mode is discouraged and unused |
| SQLITE_OMIT_PROGRESS_CALLBACK | no callbacks into zerolang yet; one less check per VM step |
| SQLITE_LIKE_DOESNT_MATCH_BLOBS | LIKE and GLOB do not match BLOBs, as the documentation recommends |
| SQLITE_MAX_EXPR_DEPTH=0 | no expression-depth limit or tracking |
| SQLITE_STRICT_SUBTYPE=1 | a function must declare that it returns subtypes |
| SQLITE_ENABLE_FTS5 | full-text search |
| SQLITE_ENABLE_RTREE | R*Tree spatial indexes |
| SQLITE_ENABLE_MATH_FUNCTIONS | sqrt, ln, pow, ... in SQL |
| SQLITE_ENABLE_PERCENTILE | median, percentile aggregates |
| SQLITE_ENABLE_DBSTAT_VTAB | the dbstat virtual table (space use per table) |
| SQLITE_ENABLE_COLUMN_METADATA | a statement's column's table and origin column (Statement.columnTableName ...) |
| SQLITE_ENABLE_CARRAY | carray: binding a list as a table-valued parameter (`bindIntegerList ...`) |

JSON and sqlite3_deserialize are on by default. Declared types are kept. Every
other behaviour is SQLite's default -- foreign keys, for one, are off until a
program turns them on.

## Upgrading

1. Read the release's change log on sqlite.org for anything the `sqlite` unit's
   declarations or the options above depend on.
2. Take the version, its release year and the zip's SHA3-256 from
   https://sqlite.org/download.html, and from the repository root run

       tools/vendor-sqlite/vendor-sqlite.sh 3.54.0 2026 <sha3-256>

   It downloads the amalgamation, refuses it unless the hash matches, and copies
   sqlite3.c and sqlite3.h here.
3. Update this file: version, URL, hash, date.
4. `make ci`. It compiles the `sqlite` unit's declaration fragment against the
   new sqlite3.h (the drift guard) and runs every sqlite program under each
   compiler. Commit the upgrade as one commit.
