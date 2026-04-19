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
    "   retries on EINTR. Returns total bytes written on success. */\n"
    "static z_result_u64_ioerror_t z_file_write(\n"
    "    z_file_t* f, z_list_u8_t* src\n"
    ");\n"
    "static z_result_u64_ioerror_t z_file_write(\n"
    "    z_file_t* f, z_list_u8_t* src\n"
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
        "remove",
        "rename",
        "open",
        "file_close",
        "file_read",
        "file_write",
    }
    # result(null, ioerror) wrapper used by mkdir / remove / rename /
    # file_close
    null_wrap = natives & {"mkdir", "remove", "rename", "file_close"}
    # result(u64, ioerror) wrapper used by file_read / file_write
    u64_wrap = natives & {"file_read", "file_write"}
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
