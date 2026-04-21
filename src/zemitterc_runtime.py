"""Runtime C code generation for the zerolang compiler.

Generates the runtime support code (string handling, error helpers) that
is inlined into every compiled .c file.

Future: extract output to runtime/ directory as zrt_string.c,
zrt_error.c, etc. and compile into libzrt.a.
"""

from typing import List


def emit_runtime_includes(
    *,
    needs_stdio: bool,
    needs_stdint: bool,
    needs_stdlib: bool,
    needs_stdbool: bool,
    needs_string: bool,
    needs_io: bool = False,
) -> str:
    """Emit #include directives for required C standard headers."""
    parts: list[str] = []
    if needs_stdio:
        parts.append("#include <stdio.h>\n")
    if needs_stdint:
        parts.append("#include <stdint.h>\n")
    if needs_stdlib:
        parts.append("#include <stdlib.h>\n")
    if needs_stdbool:
        parts.append("#include <stdbool.h>\n")
    if needs_string:
        parts.append("#include <string.h>\n")
    if needs_io:
        parts.append("#include <fcntl.h>\n")
        parts.append("#include <unistd.h>\n")
        parts.append("#include <errno.h>\n")
        parts.append("#include <sys/stat.h>\n")
        parts.append("#include <sys/types.h>\n")
        parts.append("#include <dirent.h>\n")
    if parts:
        parts.append("\n")
    return "".join(parts)


_Z_STRING_RUNTIME = (
    "typedef struct {\n"
    "    uint64_t size;       /* byte count of current content */\n"
    "    char* data;          /* heap-allocated data buffer */\n"
    "    uint64_t capacity;   /* allocated buffer size */\n"
    "} z_string_t;\n\n"
    "static z_string_t z_string_new(const char* s) {\n"
    "    z_string_t z = {0};\n"
    "    z.size = (uint64_t)strlen(s);\n"
    "    z.capacity = z.size + 1;\n"
    "    z.data = (char*)malloc(z.capacity);\n"
    "    memcpy(z.data, s, z.size);\n"
    "    z.data[z.size] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
    "static z_string_t z_string_cat(z_string_t* a, z_string_t* b) {\n"
    "    z_string_t z = {0};\n"
    "    z.size = a->size + b->size;\n"
    "    z.capacity = z.size + 1;\n"
    "    z.data = (char*)malloc(z.capacity);\n"
    "    memcpy(z.data, a->data, a->size);\n"
    "    memcpy(z.data + a->size, b->data, b->size);\n"
    "    z.data[z.size] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
    "static z_string_t z_string_from_i64(int64_t n) {\n"
    "    char buf[32];\n"
    '    snprintf(buf, sizeof(buf), "%ld", (long)n);\n'
    "    return z_string_new(buf);\n"
    "}\n\n"
    "static z_string_t z_string_from_f64(double n) {\n"
    "    char buf[64];\n"
    '    snprintf(buf, sizeof(buf), "%g", n);\n'
    "    return z_string_new(buf);\n"
    "}\n\n"
    "static void z_string_print(z_string_t* s) {\n"
    '    printf("%.*s\\n", (int)s->size, s->data);\n'
    "}\n\n"
    "static void z_string_free(z_string_t* s) {\n"
    "    if (s && s->data) free(s->data);\n"
    "}\n\n"
    "static bool z_string_eq(z_string_t* a, z_string_t* b) {\n"
    "    if (a == b) return true;\n"
    "    return a->size == b->size && memcmp(a->data, b->data, a->size) == 0;\n"
    "}\n\n"
    "static void z_string_reserve(z_string_t* s, uint64_t additional) {\n"
    "    uint64_t needed = s->size + additional;\n"
    "    if (needed <= s->capacity) return;\n"
    "    uint64_t new_cap = s->capacity + (s->capacity >> 1) + 16;\n"
    "    if (new_cap < needed) new_cap = needed;\n"
    "    s->data = (char*)realloc(s->data, new_cap + 1);\n"
    "    s->capacity = new_cap;\n"
    "}\n\n"
    "static void z_string_append(z_string_t* s, const char* data, uint64_t len) {\n"
    "    z_string_reserve(s, len);\n"
    "    memcpy(s->data + s->size, data, len);\n"
    "    s->size += len;\n"
    "    s->data[s->size] = '\\0';\n"
    "}\n\n"
    "static void z_string_shrink(z_string_t* s) {\n"
    "    if (s->size == s->capacity) return;\n"
    "    s->data = (char*)realloc(s->data, s->size + 1);\n"
    "    s->capacity = s->size;\n"
    "}\n\n"
    "static z_string_t z_string_create(uint64_t cap) {\n"
    "    z_string_t z = {0};\n"
    "    z.capacity = cap;\n"
    "    if (cap > 0) {\n"
    "        z.data = (char*)malloc(cap + 1);\n"
    "        z.data[0] = '\\0';\n"
    "    }\n"
    "    return z;\n"
    "}\n\n"
)


_Z_STRINGVIEW_RUNTIME = (
    "typedef struct {\n"
    "    const char* data;    /* pointer into string buffer or .rodata */\n"
    "    uint64_t length;     /* byte count of the viewed region */\n"
    "} z_stringview_t;\n\n"
    "static void z_stringview_print(z_stringview_t sv) {\n"
    '    printf("%.*s\\n", (int)sv.length, sv.data);\n'
    "}\n\n"
    "static bool z_stringview_eq(z_stringview_t a, z_stringview_t b) {\n"
    "    return a.length == b.length && memcmp(a.data, b.data, a.length) == 0;\n"
    "}\n\n"
    "static z_string_t z_string_from_view(z_stringview_t sv) {\n"
    "    z_string_t z = {0};\n"
    "    z.size = sv.length;\n"
    "    z.capacity = sv.length + 1;\n"
    "    z.data = (char*)malloc(z.capacity);\n"
    "    memcpy(z.data, sv.data, sv.length);\n"
    "    z.data[sv.length] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
)


def emit_runtime_z_string(*, needs_string: bool, needs_stdio: bool) -> str:
    """Emit z_string_t struct, macros, and helper functions.

    Future: extract to runtime/zrt_string.c / zrt_string.h.
    """
    if needs_string or needs_stdio:
        return _Z_STRING_RUNTIME
    return ""


def emit_runtime_z_stringview(*, needs_stringview: bool) -> str:
    """Emit z_stringview_t struct and helper functions."""
    if needs_stringview:
        return _Z_STRINGVIEW_RUNTIME
    return ""


_Z_IO_EPRINTLN = (
    "static void z_io_eprintln(z_stringview_t sv) {\n"
    '    fprintf(stderr, "%.*s\\n", (int)sv.length, sv.data);\n'
    "}\n\n"
)

_Z_IO_ERRNO_MAP = (
    "static z_ioerror_t z_io_errno_to_ioerror(int e) {\n"
    "    z_ioerror_t r = {0};\n"
    "    r.data = NULL;\n"
    "    switch (e) {\n"
    "        case ENOENT:  r.tag = Z_IOERROR_TAG_NOTFOUND; break;\n"
    "        case EACCES:\n"
    "        case EPERM:   r.tag = Z_IOERROR_TAG_PERMISSIONDENIED; break;\n"
    "        case EINTR:   r.tag = Z_IOERROR_TAG_INTERRUPTED; break;\n"
    "        case EEXIST:  r.tag = Z_IOERROR_TAG_EXISTS; break;\n"
    "        case EISDIR:  r.tag = Z_IOERROR_TAG_ISDIR; break;\n"
    "        case ENOTDIR: r.tag = Z_IOERROR_TAG_NOTDIR; break;\n"
    "        case ENOSPC:  r.tag = Z_IOERROR_TAG_NOSPACE; break;\n"
    "        default:      r.tag = Z_IOERROR_TAG_OTHER; break;\n"
    "    }\n"
    "    return r;\n"
    "}\n\n"
)

_Z_IO_READ_TEXT = (
    "static z_result_string_ioerror_t z_io_read_text(z_string_t path) {\n"
    "    /* path arg owned by this callee per zerolang string-arg convention */\n"
    "    z_result_string_ioerror_t result = {0};\n"
    "    int fd = open(path.data, O_RDONLY);\n"
    "    if (fd < 0) {\n"
    "        int e = errno;\n"
    "        z_string_free(&path);\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(e);\n"
    "        result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_string_free(&path);\n"
    "    z_string_t content = z_string_create((uint64_t)4096);\n"
    "    char buf[4096];\n"
    "    for (;;) {\n"
    "        long n = read(fd, buf, sizeof(buf));\n"
    "        if (n == 0) break;\n"
    "        if (n < 0) {\n"
    "            if (errno == EINTR) continue;\n"
    "            int e = errno;\n"
    "            close(fd);\n"
    "            z_string_free(&content);\n"
    "            z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "            *boxed = z_io_errno_to_ioerror(e);\n"
    "            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "            result.data = boxed;\n"
    "            return result;\n"
    "        }\n"
    "        z_string_append(&content, buf, (uint64_t)n);\n"
    "    }\n"
    "    close(fd);\n"
    "    z_string_t* boxed = (z_string_t*)malloc(sizeof(z_string_t));\n"
    "    *boxed = content;\n"
    "    result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_WRITE_COMMON = (
    "/* shared helper: write all bytes; returns 0 ok, errno on failure. */\n"
    "static int z_io_write_all(int fd, const char* data, uint64_t size) {\n"
    "    uint64_t off = 0;\n"
    "    while (off < size) {\n"
    "        long n = write(fd, data + off, size - off);\n"
    "        if (n < 0) {\n"
    "            if (errno == EINTR) continue;\n"
    "            return errno;\n"
    "        }\n"
    "        off += (uint64_t)n;\n"
    "    }\n"
    "    return 0;\n"
    "}\n\n"
    "/* shared helper: path + open flags -> write content -> close.\n"
    "   Frees path and content (callee owns per string-arg convention). */\n"
    "static z_result_null_ioerror_t z_io_write_common(\n"
    "    z_string_t path, z_string_t content, int flags\n"
    ") {\n"
    "    z_result_null_ioerror_t result = {0};\n"
    "    int fd = open(path.data, flags, 0644);\n"
    "    if (fd < 0) {\n"
    "        int e = errno;\n"
    "        z_string_free(&path);\n"
    "        z_string_free(&content);\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(e);\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_string_free(&path);\n"
    "    int werr = z_io_write_all(fd, content.data, content.size);\n"
    "    z_string_free(&content);\n"
    "    close(fd);\n"
    "    if (werr != 0) {\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(werr);\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "    result.data = NULL;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_WRITE_TEXT = (
    "static z_result_null_ioerror_t z_io_write_text(\n"
    "    z_string_t path, z_string_t content\n"
    ") {\n"
    "    return z_io_write_common(path, content, O_WRONLY | O_CREAT | O_TRUNC);\n"
    "}\n\n"
)

_Z_IO_APPEND_TEXT = (
    "static z_result_null_ioerror_t z_io_append_text(\n"
    "    z_string_t path, z_string_t content\n"
    ") {\n"
    "    return z_io_write_common(path, content, O_WRONLY | O_CREAT | O_APPEND);\n"
    "}\n\n"
)

_Z_IO_EXISTS = (
    "static bool z_io_exists(z_string_t path) {\n"
    "    int r = access(path.data, F_OK);\n"
    "    z_string_free(&path);\n"
    "    return r == 0;\n"
    "}\n\n"
)

# shared helper: wrap an int return (0 ok, -1 err-with-errno) + path
# free into a z_result_null_ioerror_t. Used by mkdir / remove / rename.
_Z_IO_WRAP_NULL_RESULT = (
    "static z_result_null_ioerror_t z_io_wrap_null_result(int rc, int saved_errno) {\n"
    "    z_result_null_ioerror_t result = {0};\n"
    "    if (rc == 0) {\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "    *boxed = z_io_errno_to_ioerror(saved_errno);\n"
    "    result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_MKDIR = (
    "static z_result_null_ioerror_t z_io_mkdir(z_string_t path) {\n"
    "    int rc = mkdir(path.data, 0755);\n"
    "    int e = errno;\n"
    "    z_string_free(&path);\n"
    "    return z_io_wrap_null_result(rc, e);\n"
    "}\n\n"
)

_Z_IO_REMOVE = (
    "static z_result_null_ioerror_t z_io_remove(z_string_t path) {\n"
    "    /* try unlink first; if EISDIR, fall back to rmdir */\n"
    "    int rc = unlink(path.data);\n"
    "    int e = errno;\n"
    "    if (rc != 0 && e == EISDIR) {\n"
    "        rc = rmdir(path.data);\n"
    "        e = errno;\n"
    "    }\n"
    "    z_string_free(&path);\n"
    "    return z_io_wrap_null_result(rc, e);\n"
    "}\n\n"
)

_Z_IO_RENAME = (
    "static z_result_null_ioerror_t z_io_rename(z_string_t from, z_string_t to) {\n"
    "    int rc = rename(from.data, to.data);\n"
    "    int e = errno;\n"
    "    z_string_free(&from);\n"
    "    z_string_free(&to);\n"
    "    return z_io_wrap_null_result(rc, e);\n"
    "}\n\n"
)

_Z_IO_STAT_FILL = (
    "/* Populate fs from a struct stat. Shared by z_io_stat / z_io_lstat —\n"
    "   the only behavioral split between them lives in the syscall call\n"
    "   site (stat(2) follows symlinks, lstat(2) does not). */\n"
    "static void z_io_fill_filestat(z_filestat_t* fs, const struct stat* sb);\n"
    "static void z_io_fill_filestat(z_filestat_t* fs, const struct stat* sb) {\n"
    "    if (S_ISREG(sb->st_mode))       fs->kind.tag = Z_FILEKIND_TAG_FILE;\n"
    "    else if (S_ISDIR(sb->st_mode))  fs->kind.tag = Z_FILEKIND_TAG_DIR;\n"
    "    else if (S_ISLNK(sb->st_mode))  fs->kind.tag = Z_FILEKIND_TAG_SYMLINK;\n"
    "    else                            fs->kind.tag = Z_FILEKIND_TAG_OTHER;\n"
    "    fs->size = (uint64_t)sb->st_size;\n"
    "    fs->mtime_seconds = (uint64_t)sb->st_mtime;\n"
    "    fs->atime_seconds = (uint64_t)sb->st_atime;\n"
    "    fs->ctime_seconds = (uint64_t)sb->st_ctime;\n"
    "    fs->mode = (uint32_t)sb->st_mode;\n"
    "    fs->device = (uint64_t)sb->st_dev;\n"
    "    fs->inode = (uint64_t)sb->st_ino;\n"
    "    fs->nlink = (uint64_t)sb->st_nlink;\n"
    "}\n\n"
)

_Z_IO_STAT = (
    "/* stat(2) follows symlinks; the SYMLINK arm never fires here —\n"
    "   that arm is reached through z_io_lstat. Returns the filestat\n"
    "   value by value (not boxed); the compiler-generated result\n"
    "   destructor frees the ok payload's heap copy. */\n"
    "static z_result_filestat_ioerror_t z_io_stat(z_string_t path);\n"
    "static z_result_filestat_ioerror_t z_io_stat(z_string_t path) {\n"
    "    z_result_filestat_ioerror_t result = {0};\n"
    "    struct stat sb;\n"
    "    int rc = stat(path.data, &sb);\n"
    "    int e = errno;\n"
    "    z_string_free(&path);\n"
    "    if (rc != 0) {\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(e);\n"
    "        result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_filestat_t fs = {0};\n"
    "    z_io_fill_filestat(&fs, &sb);\n"
    "    z_filestat_t* boxed = (z_filestat_t*)malloc(sizeof(z_filestat_t));\n"
    "    *boxed = fs;\n"
    "    result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_LSTAT = (
    "/* lstat(2) — like stat but does not follow symlinks. The SYMLINK\n"
    "   arm of filekind fires here when the target path is a symlink. */\n"
    "static z_result_filestat_ioerror_t z_io_lstat(z_string_t path);\n"
    "static z_result_filestat_ioerror_t z_io_lstat(z_string_t path) {\n"
    "    z_result_filestat_ioerror_t result = {0};\n"
    "    struct stat sb;\n"
    "    int rc = lstat(path.data, &sb);\n"
    "    int e = errno;\n"
    "    z_string_free(&path);\n"
    "    if (rc != 0) {\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(e);\n"
    "        result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_filestat_t fs = {0};\n"
    "    z_io_fill_filestat(&fs, &sb);\n"
    "    z_filestat_t* boxed = (z_filestat_t*)malloc(sizeof(z_filestat_t));\n"
    "    *boxed = fs;\n"
    "    result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_MKDIRP = (
    "/* mkdir -p path. Walks components, creating each missing\n"
    "   directory with mode 0755. Succeeds if the final path already\n"
    "   exists as a directory; fails with ioerror if any component\n"
    "   exists as a non-directory. Allocates a mutable copy of path\n"
    "   so it can null-terminate prefixes in place. */\n"
    "static z_result_null_ioerror_t z_io_mkdirp(z_string_t path);\n"
    "static z_result_null_ioerror_t z_io_mkdirp(z_string_t path) {\n"
    "    uint64_t n = path.size;\n"
    "    char* buf = (char*)malloc(n + 1);\n"
    "    if (!buf) { int e = errno; z_string_free(&path); return z_io_wrap_null_result(-1, e); }\n"
    "    memcpy(buf, path.data, n);\n"
    "    buf[n] = '\\0';\n"
    "    z_string_free(&path);\n"
    "    /* walk, splitting at '/' boundaries */\n"
    "    for (uint64_t i = 1; i <= n; i++) {\n"
    "        if (i == n || buf[i] == '/') {\n"
    "            char saved = buf[i];\n"
    "            buf[i] = '\\0';\n"
    "            if (buf[0] != '\\0') {\n"
    "                int rc = mkdir(buf, 0755);\n"
    "                if (rc != 0 && errno != EEXIST) {\n"
    "                    int e = errno;\n"
    "                    free(buf);\n"
    "                    return z_io_wrap_null_result(-1, e);\n"
    "                }\n"
    "            }\n"
    "            buf[i] = saved;\n"
    "        }\n"
    "    }\n"
    "    free(buf);\n"
    "    return z_io_wrap_null_result(0, 0);\n"
    "}\n\n"
)

_Z_IO_LIST_DIR = (
    "/* Enumerate directory entries via opendir/readdir/closedir.\n"
    "   Returns bare entry names (not full paths), skipping `.` and\n"
    "   `..`, in filesystem order. The list is constructed on stack and\n"
    "   then boxed heap-side so the result union's void* data can\n"
    "   carry it; the compiler-generated result destructor calls\n"
    "   z_list_string_destroy on the ok payload, which in turn frees\n"
    "   each entry's z_string_t. */\n"
    "static z_result_list_string_ioerror_t z_io_list_dir(z_string_t path);\n"
    "static z_result_list_string_ioerror_t z_io_list_dir(z_string_t path) {\n"
    "    z_result_list_string_ioerror_t result = {0};\n"
    "    DIR* d = opendir(path.data);\n"
    "    int e = errno;\n"
    "    z_string_free(&path);\n"
    "    if (!d) {\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(e);\n"
    "        result.tag = Z_RESULT_LIST_STRING_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_list_string_t list = z_list_string_create((uint64_t)0);\n"
    "    struct dirent* entry;\n"
    "    errno = 0;\n"
    "    while ((entry = readdir(d)) != NULL) {\n"
    "        const char* name = entry->d_name;\n"
    "        if (name[0] == '.' && (name[1] == '\\0' ||\n"
    "            (name[1] == '.' && name[2] == '\\0'))) continue;\n"
    "        z_list_string_append(&list, z_string_new(name));\n"
    "    }\n"
    "    closedir(d);\n"
    "    z_list_string_t* boxed = (z_list_string_t*)malloc(sizeof(z_list_string_t));\n"
    "    *boxed = list;\n"
    "    result.tag = Z_RESULT_LIST_STRING_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_OPEN = (
    "/* translate openmode variant tag to open(2) flags + default mode */\n"
    "static z_result_file_ioerror_t z_io_open(z_string_t path, z_openmode_t mode) {\n"
    "    z_result_file_ioerror_t result = {0};\n"
    "    int flags;\n"
    "    mode_t perm = 0644;\n"
    "    switch (mode.tag) {\n"
    "        case Z_OPENMODE_TAG_READ:   flags = O_RDONLY; break;\n"
    "        case Z_OPENMODE_TAG_WRITE:  flags = O_WRONLY | O_CREAT | O_TRUNC; break;\n"
    "        case Z_OPENMODE_TAG_APPEND: flags = O_WRONLY | O_CREAT | O_APPEND; break;\n"
    "        default:                    flags = O_RDONLY; break;\n"
    "    }\n"
    "    int fd = open(path.data, flags, perm);\n"
    "    int e = errno;\n"
    "    z_string_free(&path);\n"
    "    if (fd < 0) {\n"
    "        z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "        *boxed = z_io_errno_to_ioerror(e);\n"
    "        result.tag = Z_RESULT_FILE_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_file_t* boxed = (z_file_t*)malloc(sizeof(z_file_t));\n"
    "    boxed->fd = (int32_t)fd;\n"
    "    boxed->closed = false;\n"
    "    result.tag = Z_RESULT_FILE_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_FILE_CLOSE = (
    "/* file.close — explicit close that surfaces delayed write errors.\n"
    "   Marks the file as closed so z_file_destroy skips a second close. */\n"
    "static z_result_null_ioerror_t z_file_close(z_file_t* p);\n"
    "static z_result_null_ioerror_t z_file_close(z_file_t* p) {\n"
    "    z_result_null_ioerror_t result = {0};\n"
    "    if (!p || p->closed) {\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    int rc = close(p->fd);\n"
    "    int e = errno;\n"
    "    p->closed = true;\n"
    "    return z_io_wrap_null_result(rc, e);\n"
    "}\n\n"
)

# shared helper: box a u64 into result(u64, ioerror) ok arm
_Z_IO_WRAP_U64_RESULT = (
    "static z_result_u64_ioerror_t z_io_u64_ok(uint64_t v);\n"
    "static z_result_u64_ioerror_t z_io_u64_ok(uint64_t v) {\n"
    "    z_result_u64_ioerror_t result = {0};\n"
    "    uint64_t* boxed = (uint64_t*)malloc(sizeof(uint64_t));\n"
    "    *boxed = v;\n"
    "    result.tag = Z_RESULT_U64_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
    "static z_result_u64_ioerror_t z_io_u64_err(int saved_errno);\n"
    "static z_result_u64_ioerror_t z_io_u64_err(int saved_errno) {\n"
    "    z_result_u64_ioerror_t result = {0};\n"
    "    z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "    *boxed = z_io_errno_to_ioerror(saved_errno);\n"
    "    result.tag = Z_RESULT_U64_IOERROR_TAG_ERR;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_FILE_READ = (
    "/* file.read — read up to `max` bytes, appending to `buf`.\n"
    "   Grows the list capacity as needed. Returns actual bytes read\n"
    "   (0 indicates EOF); retries on EINTR. */\n"
    "static z_result_u64_ioerror_t z_file_read(\n"
    "    z_file_t* f, z_list_u8_t* buf, uint64_t max\n"
    ");\n"
    "static z_result_u64_ioerror_t z_file_read(\n"
    "    z_file_t* f, z_list_u8_t* buf, uint64_t max\n"
    ") {\n"
    "    if (buf->capacity < buf->length + max) {\n"
    "        uint64_t newcap = buf->length + max;\n"
    "        buf->data = (uint8_t*)realloc(buf->data, newcap);\n"
    "        buf->capacity = newcap;\n"
    "    }\n"
    "    for (;;) {\n"
    "        long n = read(f->fd, buf->data + buf->length, max);\n"
    "        if (n >= 0) {\n"
    "            buf->length += (uint64_t)n;\n"
    "            return z_io_u64_ok((uint64_t)n);\n"
    "        }\n"
    "        if (errno == EINTR) continue;\n"
    "        return z_io_u64_err(errno);\n"
    "    }\n"
    "}\n\n"
)

_Z_FILE_WRITE = (
    "/* file.write — write all bytes from `src`. Loops on short writes;\n"
    "   retries on EINTR. Returns total bytes written on success.\n"
    "   `src` is a listview (layout: length, data*), matching byteview. */\n"
    "static z_result_u64_ioerror_t z_file_write(\n"
    "    z_file_t* f, z_listview_u8_t* src\n"
    ");\n"
    "static z_result_u64_ioerror_t z_file_write(\n"
    "    z_file_t* f, z_listview_u8_t* src\n"
    ") {\n"
    "    uint64_t total = 0;\n"
    "    while (total < src->length) {\n"
    "        long n = write(f->fd, src->data + total, src->length - total);\n"
    "        if (n < 0) {\n"
    "            if (errno == EINTR) continue;\n"
    "            return z_io_u64_err(errno);\n"
    "        }\n"
    "        total += (uint64_t)n;\n"
    "    }\n"
    "    return z_io_u64_ok(total);\n"
    "}\n\n"
)

_Z_FILE_FLUSH = (
    "/* file.flush — no-op for raw file descriptors (POSIX write goes\n"
    "   directly to the kernel, no userspace buffer to drain). Exists\n"
    "   so file satisfies the `writer` protocol signature. */\n"
    "static z_result_null_ioerror_t z_file_flush(z_file_t* f);\n"
    "static z_result_null_ioerror_t z_file_flush(z_file_t* f) {\n"
    "    (void)f;\n"
    "    z_result_null_ioerror_t result = {0};\n"
    "    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "    result.data = NULL;\n"
    "    return result;\n"
    "}\n\n"
)


def emit_io_std_streams(natives: "set[str]") -> str:
    """Emit the static file handles + accessor functions for
    io.stdin / io.stdout / io.stderr — only those actually used,
    since unused ones would reference undefined protocol types if
    the corresponding conformance wasn't emitted."""
    want = natives & {"stdin", "stdout", "stderr"}
    if not want:
        return ""
    parts: list[str] = []
    header = (
        "/* Standard stream file handles. `closed = true` sentinels prevent\n"
        "   z_file_destroy from calling close() on fds 0/1/2 via an\n"
        "   accidental scope exit (the returned protocol handles are\n"
        "   borrowed, but belt-and-braces). write/read/seek on these\n"
        "   fds still work — `closed` only gates the close() syscall. */\n"
    )
    parts.append(header)
    if "stdin" in want:
        parts.append("static z_file_t z_io_stdin_file  = { 0, true };\n")
    if "stdout" in want:
        parts.append("static z_file_t z_io_stdout_file = { 1, true };\n")
    if "stderr" in want:
        parts.append("static z_file_t z_io_stderr_file = { 2, true };\n")
    parts.append("\n")
    if "stdin" in want:
        parts.append(
            "static z_reader_t z_io_stdin(void);\n"
            "static z_reader_t z_io_stdin(void) {\n"
            "    return z_file_reader_create(&z_io_stdin_file);\n"
            "}\n\n"
        )
    if "stdout" in want:
        parts.append(
            "static z_writer_t z_io_stdout(void);\n"
            "static z_writer_t z_io_stdout(void) {\n"
            "    return z_file_writer_create(&z_io_stdout_file);\n"
            "}\n\n"
        )
    if "stderr" in want:
        parts.append(
            "static z_writer_t z_io_stderr(void);\n"
            "static z_writer_t z_io_stderr(void) {\n"
            "    return z_file_writer_create(&z_io_stderr_file);\n"
            "}\n\n"
        )
    return "".join(parts)


_Z_BUFWRITER_CREATE = (
    "/* bufwriter.create — allocate a buffered writer with an empty\n"
    "   backing buffer of the requested capacity. The sink is held by\n"
    "   borrow (the source writer is locked by the typechecker's\n"
    "   path-scoped lock for the wrapper's lifetime). */\n"
    "static z_bufwriter_t z_bufwriter_create(z_writer_t sink, uint64_t cap);\n"
    "static z_bufwriter_t z_bufwriter_create(z_writer_t sink, uint64_t cap) {\n"
    "    z_bufwriter_t self = {0};\n"
    "    self.sink = sink;\n"
    "    self.buf = z_list_u8_create(cap);\n"
    "    self.cap = cap;\n"
    "    return self;\n"
    "}\n\n"
)

_Z_BUFWRITER_FLUSH = (
    "/* bufwriter.flush — drain the backing buffer to the sink via its\n"
    "   writer vtable. On ok, the buffer length is reset to 0. On err,\n"
    "   the underlying ioerror is transferred into the returned\n"
    "   result(null, ioerror) so callers see exactly one error. */\n"
    "static z_result_null_ioerror_t z_bufwriter_flush(z_bufwriter_t* self);\n"
    "static z_result_null_ioerror_t z_bufwriter_flush(z_bufwriter_t* self) {\n"
    "    z_result_null_ioerror_t result = {0};\n"
    "    if (self->buf.length == 0) {\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    z_listview_u8_t view = { self->buf.length, self->buf.data };\n"
    "    z_result_u64_ioerror_t wr =\n"
    "        self->sink.vtable->write(self->sink.data, &view);\n"
    "    if (wr.tag == Z_RESULT_U64_IOERROR_TAG_OK) {\n"
    "        free(wr.data);\n"
    "        self->buf.length = 0;\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    /* err: transfer ioerror box into result(null, ioerror) */\n"
    "    result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "    result.data = wr.data;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_BUFWRITER_WRITE = (
    "/* bufwriter.write — append `from` to the backing buffer. If the\n"
    "   combined length would exceed `cap`, flush first (propagating\n"
    "   any write error). Chunks larger than `cap` bypass the buffer\n"
    "   and go straight to the sink. */\n"
    "static z_result_u64_ioerror_t z_bufwriter_write(\n"
    "    z_bufwriter_t* self, z_listview_u8_t* from\n"
    ");\n"
    "static z_result_u64_ioerror_t z_bufwriter_write(\n"
    "    z_bufwriter_t* self, z_listview_u8_t* from\n"
    ") {\n"
    "    if (self->buf.length + from->length > self->cap) {\n"
    "        z_result_null_ioerror_t fr = z_bufwriter_flush(self);\n"
    "        if (fr.tag != Z_RESULT_NULL_IOERROR_TAG_OK) {\n"
    "            z_result_u64_ioerror_t result = {0};\n"
    "            result.tag = Z_RESULT_U64_IOERROR_TAG_ERR;\n"
    "            result.data = fr.data;\n"
    "            return result;\n"
    "        }\n"
    "    }\n"
    "    if (from->length > self->cap) {\n"
    "        /* oversize write: bypass the buffer */\n"
    "        return self->sink.vtable->write(self->sink.data, from);\n"
    "    }\n"
    "    z_list_u8_grow(&self->buf, self->buf.length + from->length);\n"
    "    memcpy(self->buf.data + self->buf.length, from->data, from->length);\n"
    "    self->buf.length += from->length;\n"
    "    return z_io_u64_ok(from->length);\n"
    "}\n\n"
)

_Z_BUFREADER_CREATE = (
    "/* bufreader.create — wrap a reader. v1 is a pass-through (no\n"
    "   internal buffer); `cap` is recorded for Phase 1c textreader\n"
    "   chunk sizing. The source is held by borrow via the typechecker's\n"
    "   path-scoped lock. */\n"
    "static z_bufreader_t z_bufreader_create(z_reader_t source, uint64_t cap);\n"
    "static z_bufreader_t z_bufreader_create(z_reader_t source, uint64_t cap) {\n"
    "    z_bufreader_t self = {0};\n"
    "    self.source = source;\n"
    "    self.cap = cap;\n"
    "    return self;\n"
    "}\n\n"
)

_Z_TEXTWRITER_CREATE = (
    "/* textwriter.create -- wrap a bufwriter. The sink is held by\n"
    "   borrow (path-scoped lock on the bufwriter keeps it stable\n"
    "   for the wrapper's lifetime). No backing buffer of our own --\n"
    "   we delegate to the bufwriter's buffer. */\n"
    "static z_textwriter_t z_textwriter_create(z_bufwriter_t* sink);\n"
    "static z_textwriter_t z_textwriter_create(z_bufwriter_t* sink) {\n"
    "    z_textwriter_t self = {0};\n"
    "    self.sink = sink;\n"
    "    return self;\n"
    "}\n\n"
)

_Z_TEXTWRITER_WRITE = (
    "/* textwriter.write -- forward the stringview's bytes to the\n"
    "   underlying bufwriter. Stringviews are UTF-8 by construction\n"
    "   (the compiler's string/stringview types enforce it at their\n"
    "   ingress points), so no validation is performed here. */\n"
    "static z_result_u64_ioerror_t z_textwriter_write(\n"
    "    z_textwriter_t* self, z_stringview_t* from\n"
    ");\n"
    "static z_result_u64_ioerror_t z_textwriter_write(\n"
    "    z_textwriter_t* self, z_stringview_t* from\n"
    ") {\n"
    "    z_listview_u8_t view = { from->length, (uint8_t*)from->data };\n"
    "    return z_bufwriter_write(self->sink, &view);\n"
    "}\n\n"
)

_Z_TEXTWRITER_WRITE_LINE = (
    "/* textwriter.write_line -- emit the stringview followed by an\n"
    "   LF ('\\n'). Two sink writes: content then newline. Returns the\n"
    "   total byte count on ok; on a partial-write err from the\n"
    "   newline, the content is still considered written (its count\n"
    "   would have been reported by a plain `write` call) so the\n"
    "   caller can distinguish 'nothing landed' from 'content landed,\n"
    "   newline did not' by inspecting the ok count. Errors from the\n"
    "   content write short-circuit. */\n"
    "static z_result_u64_ioerror_t z_textwriter_write_line(\n"
    "    z_textwriter_t* self, z_stringview_t* from\n"
    ");\n"
    "static z_result_u64_ioerror_t z_textwriter_write_line(\n"
    "    z_textwriter_t* self, z_stringview_t* from\n"
    ") {\n"
    "    z_listview_u8_t body = { from->length, (uint8_t*)from->data };\n"
    "    z_result_u64_ioerror_t br = z_bufwriter_write(self->sink, &body);\n"
    "    if (br.tag != Z_RESULT_U64_IOERROR_TAG_OK) return br;\n"
    "    uint8_t nl = (uint8_t)'\\n';\n"
    "    z_listview_u8_t tail = { (uint64_t)1, &nl };\n"
    "    z_result_u64_ioerror_t tr = z_bufwriter_write(self->sink, &tail);\n"
    "    if (tr.tag != Z_RESULT_U64_IOERROR_TAG_OK) {\n"
    "        free(br.data);\n"
    "        return tr;\n"
    "    }\n"
    "    uint64_t body_n = *(uint64_t*)br.data;\n"
    "    uint64_t tail_n = *(uint64_t*)tr.data;\n"
    "    free(br.data);\n"
    "    free(tr.data);\n"
    "    return z_io_u64_ok(body_n + tail_n);\n"
    "}\n\n"
)

_Z_TEXTWRITER_FLUSH = (
    "/* textwriter.flush -- delegate to the bufwriter's flush. The\n"
    "   textwriter has no buffer of its own to drain. */\n"
    "static z_result_null_ioerror_t z_textwriter_flush(z_textwriter_t* self);\n"
    "static z_result_null_ioerror_t z_textwriter_flush(z_textwriter_t* self) {\n"
    "    return z_bufwriter_flush(self->sink);\n"
    "}\n\n"
)

_Z_BUFREADER_READ = (
    "/* bufreader.read — pass-through to the underlying reader. Phase\n"
    "   1c adds chunk buffering on top of this for UTF-8 boundary\n"
    "   handling in textreader. */\n"
    "static z_result_u64_ioerror_t z_bufreader_read(\n"
    "    z_bufreader_t* self, z_list_u8_t* into, uint64_t max\n"
    ");\n"
    "static z_result_u64_ioerror_t z_bufreader_read(\n"
    "    z_bufreader_t* self, z_list_u8_t* into, uint64_t max\n"
    ") {\n"
    "    return self->source.vtable->read(self->source.data, into, max);\n"
    "}\n\n"
)

_Z_IO_UTF8_VALIDATE = (
    "/* Returns 1 if [data, data+len) is valid UTF-8, else 0. Rejects\n"
    "   overlong sequences and surrogate halves (U+D800..U+DFFF) per\n"
    "   RFC 3629; accepts the full 4-byte range through U+10FFFF. */\n"
    "static int z_io_utf8_is_valid(const uint8_t* data, uint64_t len);\n"
    "static int z_io_utf8_is_valid(const uint8_t* data, uint64_t len) {\n"
    "    uint64_t i = 0;\n"
    "    while (i < len) {\n"
    "        uint8_t c = data[i];\n"
    "        if (c < 0x80) { i++; continue; }\n"
    "        if ((c & 0xE0) == 0xC0) {\n"
    "            if (c < 0xC2) return 0;\n"
    "            if (i + 1 >= len) return 0;\n"
    "            if ((data[i+1] & 0xC0) != 0x80) return 0;\n"
    "            i += 2; continue;\n"
    "        }\n"
    "        if ((c & 0xF0) == 0xE0) {\n"
    "            if (i + 2 >= len) return 0;\n"
    "            if ((data[i+1] & 0xC0) != 0x80) return 0;\n"
    "            if ((data[i+2] & 0xC0) != 0x80) return 0;\n"
    "            if (c == 0xE0 && data[i+1] < 0xA0) return 0;\n"
    "            if (c == 0xED && data[i+1] >= 0xA0) return 0;\n"
    "            i += 3; continue;\n"
    "        }\n"
    "        if ((c & 0xF8) == 0xF0) {\n"
    "            if (c > 0xF4) return 0;\n"
    "            if (i + 3 >= len) return 0;\n"
    "            if ((data[i+1] & 0xC0) != 0x80) return 0;\n"
    "            if ((data[i+2] & 0xC0) != 0x80) return 0;\n"
    "            if ((data[i+3] & 0xC0) != 0x80) return 0;\n"
    "            if (c == 0xF0 && data[i+1] < 0x90) return 0;\n"
    "            if (c == 0xF4 && data[i+1] >= 0x90) return 0;\n"
    "            i += 4; continue;\n"
    "        }\n"
    "        return 0;\n"
    "    }\n"
    "    return 1;\n"
    "}\n\n"
    "/* Box a null-payload ioerror arm into result(string, ioerror). */\n"
    "static z_result_string_ioerror_t z_io_string_err_arm(z_ioerror_tag_t tag);\n"
    "static z_result_string_ioerror_t z_io_string_err_arm("
    "z_ioerror_tag_t tag) {\n"
    "    z_result_string_ioerror_t result = {0};\n"
    "    z_ioerror_t* boxed = (z_ioerror_t*)malloc(sizeof(z_ioerror_t));\n"
    "    z_ioerror_t e = {0};\n"
    "    e.tag = tag;\n"
    "    *boxed = e;\n"
    "    result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
    "/* Box a string value into result(string, ioerror) ok arm. Takes\n"
    "   ownership of `s` (its heap buffer moves into the boxed copy). */\n"
    "static z_result_string_ioerror_t z_io_string_ok(z_string_t s);\n"
    "static z_result_string_ioerror_t z_io_string_ok(z_string_t s) {\n"
    "    z_result_string_ioerror_t result = {0};\n"
    "    z_string_t* boxed = (z_string_t*)malloc(sizeof(z_string_t));\n"
    "    *boxed = s;\n"
    "    result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_TEXTREADER_CREATE = (
    "/* textreader.create -- wrap a bufreader. Owns a small line-accum\n"
    "   buffer (`buf`) used across read_line calls to carry bytes that\n"
    "   didn't yet form a complete LF-terminated line. */\n"
    "static z_textreader_t z_textreader_create(z_bufreader_t* source);\n"
    "static z_textreader_t z_textreader_create(z_bufreader_t* source) {\n"
    "    z_textreader_t self = {0};\n"
    "    self.source = source;\n"
    "    self.buf = z_list_u8_create(0);\n"
    "    return self;\n"
    "}\n\n"
)

_Z_TEXTREADER_READ_LINE = (
    "/* textreader.read_line -- scan `buf` for LF; if none present pull\n"
    "   another chunk from the underlying bufreader. On finding LF split\n"
    "   at that offset, validate the line (without its LF) as UTF-8,\n"
    "   emit `ioerror.badencoding` on failure or `result.ok(line)` on\n"
    "   success. On a zero-byte read with buf non-empty, surface the\n"
    "   unterminated tail once; with buf empty, surface `ioerror.eof`. */\n"
    "static z_result_string_ioerror_t z_textreader_read_line(\n"
    "    z_textreader_t* self\n"
    ");\n"
    "static z_result_string_ioerror_t z_textreader_read_line(\n"
    "    z_textreader_t* self\n"
    ") {\n"
    "    uint64_t scan_start = 0;\n"
    "    for (;;) {\n"
    "        for (uint64_t i = scan_start; i < self->buf.length; i++) {\n"
    "            if (self->buf.data[i] == (uint8_t)'\\n') {\n"
    "                if (!z_io_utf8_is_valid(self->buf.data, i)) {\n"
    "                    return z_io_string_err_arm("
    "Z_IOERROR_TAG_BADENCODING);\n"
    "                }\n"
    "                z_string_t line = {0};\n"
    "                line.size = i;\n"
    "                line.capacity = i + 1;\n"
    "                line.data = (char*)malloc(line.capacity);\n"
    "                if (i > 0) memcpy(line.data, self->buf.data, i);\n"
    "                line.data[i] = '\\0';\n"
    "                uint64_t tail = self->buf.length - (i + 1);\n"
    "                if (tail > 0) {\n"
    "                    memmove(self->buf.data, self->buf.data + i + 1, tail);\n"
    "                }\n"
    "                self->buf.length = tail;\n"
    "                return z_io_string_ok(line);\n"
    "            }\n"
    "        }\n"
    "        scan_start = self->buf.length;\n"
    "        uint64_t cap = self->source->cap;\n"
    "        if (cap == 0) cap = 4096;\n"
    "        z_result_u64_ioerror_t rr = z_bufreader_read(\n"
    "            self->source, &self->buf, cap\n"
    "        );\n"
    "        if (rr.tag != Z_RESULT_U64_IOERROR_TAG_OK) {\n"
    "            z_result_string_ioerror_t result = {0};\n"
    "            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "            result.data = rr.data;\n"
    "            return result;\n"
    "        }\n"
    "        uint64_t n = *(uint64_t*)rr.data;\n"
    "        free(rr.data);\n"
    "        if (n == 0) {\n"
    "            if (self->buf.length == 0) {\n"
    "                return z_io_string_err_arm(Z_IOERROR_TAG_EOF);\n"
    "            }\n"
    "            uint64_t size = self->buf.length;\n"
    "            if (!z_io_utf8_is_valid(self->buf.data, size)) {\n"
    "                return z_io_string_err_arm("
    "Z_IOERROR_TAG_BADENCODING);\n"
    "            }\n"
    "            z_string_t line = {0};\n"
    "            line.size = size;\n"
    "            line.capacity = size + 1;\n"
    "            line.data = (char*)malloc(line.capacity);\n"
    "            if (size > 0) memcpy(line.data, self->buf.data, size);\n"
    "            line.data[size] = '\\0';\n"
    "            self->buf.length = 0;\n"
    "            return z_io_string_ok(line);\n"
    "        }\n"
    "    }\n"
    "}\n\n"
)

_Z_FILE_SEEK = (
    "/* file.seek — reposition the fd head. Maps seekorigin to the\n"
    "   matching POSIX whence constant. Returns the new absolute\n"
    "   position measured from the start of the file. */\n"
    "static z_result_u64_ioerror_t z_file_seek(\n"
    "    z_file_t* f, int64_t off, z_seekorigin_t origin\n"
    ");\n"
    "static z_result_u64_ioerror_t z_file_seek(\n"
    "    z_file_t* f, int64_t off, z_seekorigin_t origin\n"
    ") {\n"
    "    int whence;\n"
    "    switch (origin.tag) {\n"
    "        case Z_SEEKORIGIN_TAG_START:   whence = SEEK_SET; break;\n"
    "        case Z_SEEKORIGIN_TAG_CURRENT: whence = SEEK_CUR; break;\n"
    "        case Z_SEEKORIGIN_TAG_END:     whence = SEEK_END; break;\n"
    "        default:                       whence = SEEK_SET; break;\n"
    "    }\n"
    "    off_t pos = lseek(f->fd, (off_t)off, whence);\n"
    "    if (pos < 0) return z_io_u64_err(errno);\n"
    "    return z_io_u64_ok((uint64_t)pos);\n"
    "}\n\n"
)


def emit_runtime_io(*, needs_io: bool, natives: "set[str] | None" = None) -> str:
    """Emit io-unit native function implementations per requested name.

    `natives` is the set of io-native function names the program
    actually calls (e.g. `{"eprintln", "read_text"}`). Per-name
    granularity so unused natives never pull in the
    compiler-generated types they would reference.
    """
    if not needs_io or not natives:
        return ""
    parts: list[str] = []
    # errno mapping is shared; include if any fallible native is used
    fallible = natives & {
        "read_text",
        "write_text",
        "append_text",
        "mkdir",
        "mkdirp",
        "remove",
        "rename",
        "stat",
        "lstat",
        "list_dir",
        "open",
        "file_close",
        "file_read",
        "file_write",
        "file_seek",
    }
    # result(null, ioerror) wrapper used by mkdir / mkdirp / remove /
    # rename / file_close
    null_wrap = natives & {"mkdir", "mkdirp", "remove", "rename", "file_close"}
    # result(u64, ioerror) wrapper used by file_read / file_write /
    # file_seek
    u64_wrap = natives & {
        "file_read",
        "file_write",
        "file_seek",
        "bufwriter_write",
        "textwriter_write_line",
    }
    if "eprintln" in natives:
        parts.append(_Z_IO_EPRINTLN)
    if fallible:
        parts.append(_Z_IO_ERRNO_MAP)
    if null_wrap:
        parts.append(_Z_IO_WRAP_NULL_RESULT)
    if "read_text" in natives:
        parts.append(_Z_IO_READ_TEXT)
    if natives & {"write_text", "append_text"}:
        parts.append(_Z_IO_WRITE_COMMON)
    if "write_text" in natives:
        parts.append(_Z_IO_WRITE_TEXT)
    if "append_text" in natives:
        parts.append(_Z_IO_APPEND_TEXT)
    if "exists" in natives:
        parts.append(_Z_IO_EXISTS)
    if "mkdir" in natives:
        parts.append(_Z_IO_MKDIR)
    if "remove" in natives:
        parts.append(_Z_IO_REMOVE)
    if "rename" in natives:
        parts.append(_Z_IO_RENAME)
    if "mkdirp" in natives:
        parts.append(_Z_IO_MKDIRP)
    if natives & {"stat", "lstat"}:
        parts.append(_Z_IO_STAT_FILL)
    if "stat" in natives:
        parts.append(_Z_IO_STAT)
    if "lstat" in natives:
        parts.append(_Z_IO_LSTAT)
    if "list_dir" in natives:
        parts.append(_Z_IO_LIST_DIR)
    if "open" in natives:
        parts.append(_Z_IO_OPEN)
    if "file_close" in natives:
        parts.append(_Z_FILE_CLOSE)
    if u64_wrap:
        parts.append(_Z_IO_WRAP_U64_RESULT)
    if "file_read" in natives:
        parts.append(_Z_FILE_READ)
    if "file_write" in natives:
        parts.append(_Z_FILE_WRITE)
    if "file_flush" in natives:
        parts.append(_Z_FILE_FLUSH)
    if "file_seek" in natives:
        parts.append(_Z_FILE_SEEK)
    # Buffered wrappers — emit flush before write so the write-side
    # overflow branch can call the forward-declared flush. create is
    # self-contained and can come in any order.
    if "bufwriter_create" in natives:
        parts.append(_Z_BUFWRITER_CREATE)
    if "bufwriter_flush" in natives or "bufwriter_write" in natives:
        parts.append(_Z_BUFWRITER_FLUSH)
    if "bufwriter_write" in natives:
        parts.append(_Z_BUFWRITER_WRITE)
    if "bufreader_create" in natives:
        parts.append(_Z_BUFREADER_CREATE)
    if "bufreader_read" in natives:
        parts.append(_Z_BUFREADER_READ)
    # textwriter forwards to bufwriter; its bodies must land after the
    # bufwriter bodies above.
    if "textwriter_create" in natives:
        parts.append(_Z_TEXTWRITER_CREATE)
    if "textwriter_write" in natives:
        parts.append(_Z_TEXTWRITER_WRITE)
    if "textwriter_write_line" in natives:
        parts.append(_Z_TEXTWRITER_WRITE_LINE)
    if "textwriter_flush" in natives:
        parts.append(_Z_TEXTWRITER_FLUSH)
    # textreader forwards to bufreader; UTF-8 validator + string-result
    # boxing helpers are shared with read_line and must precede it.
    if "textreader_read_line" in natives:
        parts.append(_Z_IO_UTF8_VALIDATE)
    if "textreader_create" in natives:
        parts.append(_Z_TEXTREADER_CREATE)
    if "textreader_read_line" in natives:
        parts.append(_Z_TEXTREADER_READ_LINE)
    return "".join(parts)


def emit_runtime(
    *,
    needs_stdio: bool,
    needs_stdint: bool,
    needs_stdlib: bool,
    needs_stdbool: bool,
    needs_string: bool,
    needs_stringview: bool = False,
    needs_io: bool = False,
) -> str:
    """Return all runtime support code (includes + types + helper functions)."""
    # z_string_t runtime uses malloc/free (stdlib.h), strlen/memcpy (string.h),
    # and bool (stdbool.h) for z_string_eq
    has_z_string = needs_string or needs_stdio
    return (
        emit_runtime_includes(
            needs_stdio=needs_stdio or needs_io,
            needs_stdint=needs_stdint,
            needs_stdlib=needs_stdlib or has_z_string or needs_stringview,
            needs_stdbool=needs_stdbool or has_z_string or needs_stringview,
            needs_string=needs_string or has_z_string or needs_stringview,
            needs_io=needs_io,
        )
        + emit_runtime_z_string(needs_string=needs_string, needs_stdio=needs_stdio)
        + emit_runtime_z_stringview(needs_stringview=needs_stringview)
        # io helpers are NOT emitted here — they reference compiler-generated
        # struct names (z_ioerror_t, z_result_string_ioerror_t, ...) that
        # only exist after struct_defs. The emitter calls emit_runtime_io
        # separately, after the struct definitions block.
    )


def emit_static_stringviews(string_literals: dict[str, str]) -> str:
    """Emit per-program static stringview constants.

    Each literal gets a static const char array and a z_stringview_t constant
    pointing into .rodata with zero runtime cost.
    """
    if not string_literals:
        return ""
    parts: list[str] = []
    for escaped, sname in string_literals.items():
        dname = f"{sname}_d"
        byte_len = len(escaped.encode("utf-8").decode("unicode_escape").encode("utf-8"))
        parts.append(f'static const char {dname}[] = "{escaped}";\n')
        parts.append(
            f"static const z_stringview_t {sname} = {{ {dname}, {byte_len} }};\n"
        )
    parts.append("\n")
    return "".join(parts)


# -- Error helpers (future: extract to runtime/zrt_error.c) ----------------


def emit_bounds_check(
    lines: List[str],
    idx_expr: str,
    len_expr: str,
    label: str,
    idx_fmt: str = "%lu",
    idx_cast: str = "(unsigned long)",
) -> None:
    """Emit a bounds-check with error exit for container get/set."""
    lines.append(f"    if ({idx_expr} >= {len_expr}) {{\n")
    lines.append(
        f'        fprintf(stderr, "{label}: index {idx_fmt} out of bounds'
        f' (length {idx_fmt})\\n", {idx_cast}{idx_expr}, {idx_cast}{len_expr});\n'
    )
    lines.append("        exit(1);\n")
    lines.append("    }\n")


def emit_array_bounds_check(
    lines: List[str],
    idx_expr: str,
    arr_len: int,
    label: str,
) -> None:
    """Emit a signed-index bounds-check for fixed-length arrays."""
    lines.append(f"    if ({idx_expr} < 0 || {idx_expr} >= {arr_len}) {{\n")
    lines.append(
        f'        fprintf(stderr, "{label}: index %ld out of bounds'
        f' (length {arr_len})\\n", (long){idx_expr});\n'
    )
    lines.append("        exit(1);\n")
    lines.append("    }\n")


def emit_empty_check(
    lines: List[str],
    label: str,
) -> None:
    """Emit an empty-container check with error exit (e.g. list pop)."""
    lines.append("    if (_this->length == 0) {\n")
    lines.append(f'        fprintf(stderr, "{label}\\n");\n')
    lines.append("        exit(1);\n")
    lines.append("    }\n")
