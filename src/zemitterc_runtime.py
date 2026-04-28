"""Runtime C code generation for the zerolang compiler.

Generates the runtime support code (string handling, error helpers) that
is inlined into every compiled .c file.

Future: extract output to runtime/ directory as zrt_string.c,
zrt_error.c, etc. and compile into libzrt.a.
"""

import os
from typing import List


_XALLOC_HELPERS = """\
static _Noreturn void z_panic(const char* msg) {
    fprintf(stderr, "zpanic: %s\\n", msg);
    exit(1);
}
static void* z_xmalloc(size_t n) {
    void* p = malloc(n);
    if (!p) z_panic("out of memory");
    return p;
}
static void* z_xcalloc(size_t count, size_t size) {
    void* p = calloc(count, size);
    if (!p) z_panic("out of memory");
    return p;
}
static void* z_xrealloc(void* p, size_t n) {
    void* q = realloc(p, n);
    if (!q) z_panic("out of memory");
    return q;
}

"""


def emit_runtime_includes(
    *,
    needs_stdio: bool,
    needs_stdint: bool,
    needs_stdlib: bool,
    needs_string: bool,
    needs_io: bool = False,
    needs_pwd: bool = False,
) -> str:
    """Emit #include directives for required C standard headers.

    `<stdbool.h>` is always included: zerolang's `bool` type lowers
    to C99 `_Bool` via TYPEMAP, so every emitted program uses it.

    When stdlib is included, also emit the unified panic helper
    (`z_panic`) and OOM-safe allocation helpers (`z_xmalloc` /
    `z_xcalloc` / `z_xrealloc`) that route through `z_panic` instead
    of returning NULL. All emitter-generated unrecoverable exit sites
    (OOM, bounds, cast overflow, user `panic()`) funnel through
    `z_panic`, which prints `zpanic: <msg>\\n` to stderr and exits(1).
    """
    # The panic/x-alloc helpers below need fprintf/stderr, so stdlib implies stdio.
    if needs_stdlib:
        needs_stdio = True
    parts: list[str] = []
    if needs_stdio:
        parts.append("#include <stdio.h>\n")
    if needs_stdint:
        parts.append("#include <stdint.h>\n")
    if needs_stdlib:
        parts.append("#include <stdlib.h>\n")
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
    if needs_pwd:
        parts.append("#include <pwd.h>\n")
    if parts:
        parts.append("\n")
    if needs_stdlib:
        parts.append(_XALLOC_HELPERS)
    return "".join(parts)


_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "runtime")
_FRAGMENT_CACHE: "dict[str, str]" = {}


def _load_runtime_fragment(name: str) -> str:
    """Read a verbatim C fragment from src/runtime/ and cache it.

    Fragments are `.inc` files — valid C once the preceding runtime
    header block is in scope, but not standalone (no #include lines,
    since those live in emit_runtime_includes).
    """
    cached = _FRAGMENT_CACHE.get(name)
    if cached is not None:
        return cached
    path = os.path.join(_RUNTIME_DIR, name)
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    # Runtime fragments are plain C; normalise trailing whitespace and
    # ensure exactly one blank line between file-level definitions so
    # the concatenated output matches the pre-extraction formatting.
    if not content.endswith("\n"):
        content += "\n"
    _FRAGMENT_CACHE[name] = content
    return content


def _z_string_runtime() -> str:
    return _load_runtime_fragment("z_String.inc")


def _z_stringview_runtime() -> str:
    return _load_runtime_fragment("z_StringView.inc")


def emit_runtime_z_string(*, needs_string: bool, needs_stdio: bool) -> str:
    """Emit z_String_t struct and helper functions.

    Source lives in src/runtime/z_String.inc and is loaded verbatim.
    """
    if needs_string or needs_stdio:
        return _z_string_runtime()
    return ""


def emit_runtime_z_stringview(*, needs_stringview: bool) -> str:
    """Emit z_StringView_t struct and helper functions.

    Source lives in src/runtime/z_StringView.inc and is loaded verbatim.
    """
    if needs_stringview:
        return _z_stringview_runtime()
    return ""


_Z_IO_EPRINTLN = (
    "static void z_io_eprintln(z_StringView_t sv) {\n"
    '    fprintf(stderr, "%.*s\\n", (int)sv.length, sv.data);\n'
    "}\n\n"
)

_Z_IO_ERRNO_MAP = (
    "static z_IoError_t z_io_errno_to_IoError(int e) {\n"
    "    z_IoError_t r = {0};\n"
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

# Helper used by every native taking a `stringview` it must hand
# off to a C API expecting a NUL-terminated string. Allocates a
# heap copy because views may be substrings (no NUL guarantee at
# v.data[v.length]). Caller is responsible for free().
_Z_SV_TO_CSTR = (
    "/* Heap-allocate a NUL-terminated copy of the view. Free with free(). */\n"
    "static char* z_sv_to_cstr(z_StringView_t v) {\n"
    "    char* buf = (char*)z_xmalloc(v.length + 1);\n"
    "    if (v.length > 0) memcpy(buf, v.data, v.length);\n"
    "    buf[v.length] = '\\0';\n"
    "    return buf;\n"
    "}\n\n"
)

_Z_IO_READ_TEXT = (
    "static z_Result_String_IoError_t z_io_readText(z_StringView_t path) {\n"
    "    /* path is a borrowed view; caller retains ownership */\n"
    "    z_Result_String_IoError_t result = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    int fd = open(path_cstr, O_RDONLY);\n"
    "    free(path_cstr);\n"
    "    if (fd < 0) {\n"
    "        int e = errno;\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_String_t content = z_String_create((uint64_t)4096);\n"
    "    char buf[4096];\n"
    "    for (;;) {\n"
    "        long n = read(fd, buf, sizeof(buf));\n"
    "        if (n == 0) break;\n"
    "        if (n < 0) {\n"
    "            if (errno == EINTR) continue;\n"
    "            int e = errno;\n"
    "            close(fd);\n"
    "            z_String_free(&content);\n"
    "            z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "            *boxed = z_io_errno_to_IoError(e);\n"
    "            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "            result.data = boxed;\n"
    "            return result;\n"
    "        }\n"
    "        z_String_append(&content, buf, (uint64_t)n);\n"
    "    }\n"
    "    close(fd);\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
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
    "   path and content are borrowed views; caller retains. */\n"
    "static z_Result_null_IoError_t z_io_write_common(\n"
    "    z_StringView_t path, z_StringView_t content, int flags\n"
    ") {\n"
    "    z_Result_null_IoError_t result = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    int fd = open(path_cstr, flags, 0644);\n"
    "    free(path_cstr);\n"
    "    if (fd < 0) {\n"
    "        int e = errno;\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    int werr = z_io_write_all(fd, content.data, content.length);\n"
    "    close(fd);\n"
    "    if (werr != 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(werr);\n"
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
    "static z_Result_null_IoError_t z_io_writeText(\n"
    "    z_StringView_t path, z_StringView_t content\n"
    ") {\n"
    "    return z_io_write_common(path, content, O_WRONLY | O_CREAT | O_TRUNC);\n"
    "}\n\n"
)

_Z_IO_APPEND_TEXT = (
    "static z_Result_null_IoError_t z_io_appendText(\n"
    "    z_StringView_t path, z_StringView_t content\n"
    ") {\n"
    "    return z_io_write_common(path, content, O_WRONLY | O_CREAT | O_APPEND);\n"
    "}\n\n"
)

_Z_IO_READLINK = (
    "/* readlink — follow-free read of a symlink's target. readlink(2)\n"
    "   does not NUL-terminate, and we don't know the exact target size\n"
    "   up front; loop with a growing buffer until the kernel says the\n"
    "   full target fit. EINVAL from readlink means the path exists but\n"
    "   isn't a symbolic link -- surfaced as invalidpath with a\n"
    "   caller-readable message. */\n"
    "static z_Result_String_IoError_t z_io_readlink(z_StringView_t path);\n"
    "static z_Result_String_IoError_t z_io_readlink(z_StringView_t path) {\n"
    "    z_Result_String_IoError_t result = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    uint64_t cap = (uint64_t)256;\n"
    "    char* buf = (char*)z_xmalloc(cap);\n"
    "    for (;;) {\n"
    "        ssize_t n = readlink(path_cstr, buf, cap);\n"
    "        if (n < 0) {\n"
    "            int e = errno;\n"
    "            free(buf);\n"
    "            free(path_cstr);\n"
    "            z_IoError_t* boxed = "
    "(z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "            if (e == EINVAL) {\n"
    "                z_IoError_t v = {0};\n"
    "                v.tag = Z_IOERROR_TAG_INVALIDPATH;\n"
    "                z_String_t* sp = "
    "(z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    '                *sp = z_String_new("not a symbolic link");\n'
    "                v.data = sp;\n"
    "                *boxed = v;\n"
    "            } else {\n"
    "                *boxed = z_io_errno_to_IoError(e);\n"
    "            }\n"
    "            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "            result.data = boxed;\n"
    "            return result;\n"
    "        }\n"
    "        if ((uint64_t)n < cap) {\n"
    "            z_String_t target = {0};\n"
    "            target.size = (uint64_t)n;\n"
    "            target.capacity = target.size + 1;\n"
    "            target.data = (char*)z_xmalloc(target.capacity);\n"
    "            memcpy(target.data, buf, target.size);\n"
    "            target.data[target.size] = '\\0';\n"
    "            free(buf);\n"
    "            free(path_cstr);\n"
    "            z_String_t* boxed = "
    "(z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "            *boxed = target;\n"
    "            result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "            result.data = boxed;\n"
    "            return result;\n"
    "        }\n"
    "        cap *= 2;\n"
    "        buf = (char*)z_xrealloc(buf, cap);\n"
    "    }\n"
    "}\n\n"
)

_Z_IO_SYMLINK = (
    "/* symlink — create a symbolic link at `link` with `target` as the\n"
    "   link content. Both args are borrowed views; caller retains.\n"
    "   EEXIST maps to ioerror.exists so callers can distinguish\n"
    "   'already there' from other failures. */\n"
    "static z_Result_null_IoError_t z_io_symlink(\n"
    "    z_StringView_t target, z_StringView_t link\n"
    ");\n"
    "static z_Result_null_IoError_t z_io_symlink(\n"
    "    z_StringView_t target, z_StringView_t link\n"
    ") {\n"
    "    char* target_cstr = z_sv_to_cstr(target);\n"
    "    char* link_cstr = z_sv_to_cstr(link);\n"
    "    int rc = symlink(target_cstr, link_cstr);\n"
    "    int e = errno;\n"
    "    free(target_cstr);\n"
    "    free(link_cstr);\n"
    "    return z_io_wrap_null_Result(rc, e);\n"
    "}\n\n"
)

_Z_IO_EXISTS = (
    "static bool z_io_exists(z_StringView_t path) {\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    int r = access(path_cstr, F_OK);\n"
    "    free(path_cstr);\n"
    "    return r == 0;\n"
    "}\n\n"
)

# shared helper: wrap an int return (0 ok, -1 err-with-errno) + path
# free into a z_Result_null_IoError_t. Used by mkdir / remove / rename.
_Z_IO_WRAP_NULL_RESULT = (
    "static z_Result_null_IoError_t z_io_wrap_null_Result(int rc, int saved_errno) {\n"
    "    z_Result_null_IoError_t result = {0};\n"
    "    if (rc == 0) {\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "    *boxed = z_io_errno_to_IoError(saved_errno);\n"
    "    result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_MKDIR = (
    "static z_Result_null_IoError_t z_io_mkdir(z_StringView_t path) {\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    int rc = mkdir(path_cstr, 0755);\n"
    "    int e = errno;\n"
    "    free(path_cstr);\n"
    "    return z_io_wrap_null_Result(rc, e);\n"
    "}\n\n"
)

_Z_IO_REMOVE = (
    "static z_Result_null_IoError_t z_io_remove(z_StringView_t path) {\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    /* try unlink first; if EISDIR, fall back to rmdir */\n"
    "    int rc = unlink(path_cstr);\n"
    "    int e = errno;\n"
    "    if (rc != 0 && e == EISDIR) {\n"
    "        rc = rmdir(path_cstr);\n"
    "        e = errno;\n"
    "    }\n"
    "    free(path_cstr);\n"
    "    return z_io_wrap_null_Result(rc, e);\n"
    "}\n\n"
)

_Z_IO_RENAME = (
    "static z_Result_null_IoError_t z_io_rename(\n"
    "    z_StringView_t from, z_StringView_t to\n"
    ") {\n"
    "    char* from_cstr = z_sv_to_cstr(from);\n"
    "    char* to_cstr = z_sv_to_cstr(to);\n"
    "    int rc = rename(from_cstr, to_cstr);\n"
    "    int e = errno;\n"
    "    free(from_cstr);\n"
    "    free(to_cstr);\n"
    "    return z_io_wrap_null_Result(rc, e);\n"
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
    "    fs->mtimeSeconds = (uint64_t)sb->st_mtime;\n"
    "    fs->atimeSeconds = (uint64_t)sb->st_atime;\n"
    "    fs->ctimeSeconds = (uint64_t)sb->st_ctime;\n"
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
    "static z_Result_filestat_IoError_t z_io_stat(z_StringView_t path);\n"
    "static z_Result_filestat_IoError_t z_io_stat(z_StringView_t path) {\n"
    "    z_Result_filestat_IoError_t result = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    struct stat sb;\n"
    "    int rc = stat(path_cstr, &sb);\n"
    "    int e = errno;\n"
    "    free(path_cstr);\n"
    "    if (rc != 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_filestat_t fs = {0};\n"
    "    z_io_fill_filestat(&fs, &sb);\n"
    "    z_filestat_t* boxed = (z_filestat_t*)z_xmalloc(sizeof(z_filestat_t));\n"
    "    *boxed = fs;\n"
    "    result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_LSTAT = (
    "/* lstat(2) — like stat but does not follow symlinks. The SYMLINK\n"
    "   arm of filekind fires here when the target path is a symlink. */\n"
    "static z_Result_filestat_IoError_t z_io_lstat(z_StringView_t path);\n"
    "static z_Result_filestat_IoError_t z_io_lstat(z_StringView_t path) {\n"
    "    z_Result_filestat_IoError_t result = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    struct stat sb;\n"
    "    int rc = lstat(path_cstr, &sb);\n"
    "    int e = errno;\n"
    "    free(path_cstr);\n"
    "    if (rc != 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_filestat_t fs = {0};\n"
    "    z_io_fill_filestat(&fs, &sb);\n"
    "    z_filestat_t* boxed = (z_filestat_t*)z_xmalloc(sizeof(z_filestat_t));\n"
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
    "static z_Result_null_IoError_t z_io_mkdirp(z_StringView_t path);\n"
    "static z_Result_null_IoError_t z_io_mkdirp(z_StringView_t path) {\n"
    "    uint64_t n = path.length;\n"
    "    char* buf = (char*)z_xmalloc(n + 1);\n"
    "    if (n > 0) memcpy(buf, path.data, n);\n"
    "    buf[n] = '\\0';\n"
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
    "                    return z_io_wrap_null_Result(-1, e);\n"
    "                }\n"
    "            }\n"
    "            buf[i] = saved;\n"
    "        }\n"
    "    }\n"
    "    free(buf);\n"
    "    return z_io_wrap_null_Result(0, 0);\n"
    "}\n\n"
)

_Z_IO_LIST_DIR = (
    "/* Enumerate directory entries via opendir/readdir/closedir.\n"
    "   Returns bare entry names (not full paths), skipping `.` and\n"
    "   `..`, in filesystem order. The list is constructed on stack and\n"
    "   then boxed heap-side so the result union's void* data can\n"
    "   carry it; the compiler-generated result destructor calls\n"
    "   z_List_String_destroy on the ok payload, which in turn frees\n"
    "   each entry's z_String_t. */\n"
    "static z_Result_List_String_IoError_t z_io_listDir(z_StringView_t path);\n"
    "static z_Result_List_String_IoError_t z_io_listDir(z_StringView_t path) {\n"
    "    z_Result_List_String_IoError_t result = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    DIR* d = opendir(path_cstr);\n"
    "    int e = errno;\n"
    "    free(path_cstr);\n"
    "    if (!d) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        result.tag = Z_RESULT_LIST_STRING_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_List_String_t list = z_List_String_create((uint64_t)0);\n"
    "    struct dirent* entry;\n"
    "    errno = 0;\n"
    "    while ((entry = readdir(d)) != NULL) {\n"
    "        const char* name = entry->d_name;\n"
    "        if (name[0] == '.' && (name[1] == '\\0' ||\n"
    "            (name[1] == '.' && name[2] == '\\0'))) continue;\n"
    "        z_List_String_append(&list, z_String_new(name));\n"
    "    }\n"
    "    closedir(d);\n"
    "    z_List_String_t* boxed = (z_List_String_t*)z_xmalloc(sizeof(z_List_String_t));\n"
    "    *boxed = list;\n"
    "    result.tag = Z_RESULT_LIST_STRING_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_IO_OPEN = (
    "/* translate openmode variant tag to open(2) flags + default mode */\n"
    "static z_Result_File_IoError_t z_io_open(\n"
    "    z_StringView_t path, z_openmode_t mode\n"
    ") {\n"
    "    z_Result_File_IoError_t result = {0};\n"
    "    int flags;\n"
    "    mode_t perm = 0644;\n"
    "    switch (mode.tag) {\n"
    "        case Z_OPENMODE_TAG_READ:   flags = O_RDONLY; break;\n"
    "        case Z_OPENMODE_TAG_WRITE:  flags = O_WRONLY | O_CREAT | O_TRUNC; break;\n"
    "        case Z_OPENMODE_TAG_APPEND: flags = O_WRONLY | O_CREAT | O_APPEND; break;\n"
    "        default:                    flags = O_RDONLY; break;\n"
    "    }\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    int fd = open(path_cstr, flags, perm);\n"
    "    int e = errno;\n"
    "    free(path_cstr);\n"
    "    if (fd < 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        result.tag = Z_RESULT_FILE_IOERROR_TAG_ERR;\n"
    "        result.data = boxed;\n"
    "        return result;\n"
    "    }\n"
    "    z_File_t* boxed = (z_File_t*)z_xmalloc(sizeof(z_File_t));\n"
    "    boxed->fd = (int32_t)fd;\n"
    "    boxed->closed = false;\n"
    "    result.tag = Z_RESULT_FILE_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_FILE_CLOSE = (
    "/* file.close — explicit close that surfaces delayed write errors.\n"
    "   Marks the file as closed so z_File_destroy skips a second close. */\n"
    "static z_Result_null_IoError_t z_File_close(z_File_t* p);\n"
    "static z_Result_null_IoError_t z_File_close(z_File_t* p) {\n"
    "    z_Result_null_IoError_t result = {0};\n"
    "    if (!p || p->closed) {\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    int rc = close(p->fd);\n"
    "    int e = errno;\n"
    "    p->closed = true;\n"
    "    return z_io_wrap_null_Result(rc, e);\n"
    "}\n\n"
)

# shared helper: box a u64 into result(u64, ioerror) ok arm
_Z_IO_WRAP_U64_RESULT = (
    "static z_Result_u64_IoError_t z_io_u64_ok(uint64_t v);\n"
    "static z_Result_u64_IoError_t z_io_u64_ok(uint64_t v) {\n"
    "    z_Result_u64_IoError_t result = {0};\n"
    "    uint64_t* boxed = (uint64_t*)z_xmalloc(sizeof(uint64_t));\n"
    "    *boxed = v;\n"
    "    result.tag = Z_RESULT_U64_IOERROR_TAG_OK;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
    "static z_Result_u64_IoError_t z_io_u64_err(int saved_errno);\n"
    "static z_Result_u64_IoError_t z_io_u64_err(int saved_errno) {\n"
    "    z_Result_u64_IoError_t result = {0};\n"
    "    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "    *boxed = z_io_errno_to_IoError(saved_errno);\n"
    "    result.tag = Z_RESULT_U64_IOERROR_TAG_ERR;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
)

_Z_FILE_READ = (
    "/* file.read — read up to `max` bytes, appending to `buf`.\n"
    "   Grows the list capacity as needed. Returns actual bytes read\n"
    "   (0 indicates EOF); retries on EINTR. */\n"
    "static z_Result_u64_IoError_t z_File_read(\n"
    "    z_File_t* f, z_List_u8_t* buf, uint64_t max\n"
    ");\n"
    "static z_Result_u64_IoError_t z_File_read(\n"
    "    z_File_t* f, z_List_u8_t* buf, uint64_t max\n"
    ") {\n"
    "    if (buf->capacity < buf->length + max) {\n"
    "        uint64_t newcap = buf->length + max;\n"
    "        buf->data = (uint8_t*)z_xrealloc(buf->data, newcap);\n"
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
    "static z_Result_u64_IoError_t z_File_write(\n"
    "    z_File_t* f, z_ListView_u8_t* src\n"
    ");\n"
    "static z_Result_u64_IoError_t z_File_write(\n"
    "    z_File_t* f, z_ListView_u8_t* src\n"
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
    "static z_Result_null_IoError_t z_File_flush(z_File_t* f);\n"
    "static z_Result_null_IoError_t z_File_flush(z_File_t* f) {\n"
    "    (void)f;\n"
    "    z_Result_null_IoError_t result = {0};\n"
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
        "   z_File_destroy from calling close() on fds 0/1/2 via an\n"
        "   accidental scope exit (the returned protocol handles are\n"
        "   borrowed, but belt-and-braces). write/read/seek on these\n"
        "   fds still work — `closed` only gates the close() syscall. */\n"
    )
    parts.append(header)
    if "stdin" in want:
        parts.append("static z_File_t z_io_stdin_File  = { 0, true };\n")
    if "stdout" in want:
        parts.append("static z_File_t z_io_stdout_File = { 1, true };\n")
    if "stderr" in want:
        parts.append("static z_File_t z_io_stderr_File = { 2, true };\n")
    parts.append("\n")
    if "stdin" in want:
        parts.append(
            "static z_Reader_t z_io_stdin(void);\n"
            "static z_Reader_t z_io_stdin(void) {\n"
            "    return z_File_Reader_create(&z_io_stdin_File);\n"
            "}\n\n"
        )
    if "stdout" in want:
        parts.append(
            "static z_Writer_t z_io_stdout(void);\n"
            "static z_Writer_t z_io_stdout(void) {\n"
            "    return z_File_Writer_create(&z_io_stdout_File);\n"
            "}\n\n"
        )
    if "stderr" in want:
        parts.append(
            "static z_Writer_t z_io_stderr(void);\n"
            "static z_Writer_t z_io_stderr(void) {\n"
            "    return z_File_Writer_create(&z_io_stderr_File);\n"
            "}\n\n"
        )
    return "".join(parts)


_Z_BUFWRITER_CREATE = (
    "/* bufwriter.create — allocate a buffered writer with an empty\n"
    "   backing buffer of the requested capacity. The sink is held by\n"
    "   borrow (the source writer is locked by the typechecker's\n"
    "   path-scoped lock for the wrapper's lifetime). */\n"
    "static z_BufWriter_t z_BufWriter_create(z_Writer_t sink, uint64_t cap);\n"
    "static z_BufWriter_t z_BufWriter_create(z_Writer_t sink, uint64_t cap) {\n"
    "    z_BufWriter_t self = {0};\n"
    "    self.sink = sink;\n"
    "    self.buf = z_List_u8_create(cap);\n"
    "    self.cap = cap;\n"
    "    return self;\n"
    "}\n\n"
)

_Z_BUFWRITER_FLUSH = (
    "/* bufwriter.flush — drain the backing buffer to the sink via its\n"
    "   writer vtable. On ok, the buffer length is reset to 0. On err,\n"
    "   the underlying ioerror is transferred into the returned\n"
    "   result(null, ioerror) so callers see exactly one error. */\n"
    "static z_Result_null_IoError_t z_BufWriter_flush(z_BufWriter_t* self);\n"
    "static z_Result_null_IoError_t z_BufWriter_flush(z_BufWriter_t* self) {\n"
    "    z_Result_null_IoError_t result = {0};\n"
    "    if (self->buf.length == 0) {\n"
    "        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "        result.data = NULL;\n"
    "        return result;\n"
    "    }\n"
    "    z_ListView_u8_t view = { self->buf.length, self->buf.data };\n"
    "    z_Result_u64_IoError_t wr =\n"
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
    "static z_Result_u64_IoError_t z_BufWriter_write(\n"
    "    z_BufWriter_t* self, z_ListView_u8_t* from\n"
    ");\n"
    "static z_Result_u64_IoError_t z_BufWriter_write(\n"
    "    z_BufWriter_t* self, z_ListView_u8_t* from\n"
    ") {\n"
    "    if (self->buf.length + from->length > self->cap) {\n"
    "        z_Result_null_IoError_t fr = z_BufWriter_flush(self);\n"
    "        if (fr.tag != Z_RESULT_NULL_IOERROR_TAG_OK) {\n"
    "            z_Result_u64_IoError_t result = {0};\n"
    "            result.tag = Z_RESULT_U64_IOERROR_TAG_ERR;\n"
    "            result.data = fr.data;\n"
    "            return result;\n"
    "        }\n"
    "    }\n"
    "    if (from->length > self->cap) {\n"
    "        /* oversize write: bypass the buffer */\n"
    "        return self->sink.vtable->write(self->sink.data, from);\n"
    "    }\n"
    "    z_List_u8_grow(&self->buf, self->buf.length + from->length);\n"
    "    memcpy(self->buf.data + self->buf.length, from->data, from->length);\n"
    "    self->buf.length += from->length;\n"
    "    return z_io_u64_ok(from->length);\n"
    "}\n\n"
)

_Z_BUFREADER_CREATE = (
    "/* bufreader.create — wrap a reader. Pre-allocates the internal\n"
    "   `buf` to `cap` so the first refill never has to grow it. `head`\n"
    "   starts at 0; the buffer is empty (buf.length == 0) so the first\n"
    "   read triggers a refill. Source is held by borrow via the\n"
    "   typechecker's path-scoped lock. */\n"
    "static z_BufReader_t z_BufReader_create(z_Reader_t source, uint64_t cap);\n"
    "static z_BufReader_t z_BufReader_create(z_Reader_t source, uint64_t cap) {\n"
    "    z_BufReader_t self = {0};\n"
    "    self.source = source;\n"
    "    self.buf = z_List_u8_create(cap);\n"
    "    self.head = 0;\n"
    "    self.cap = cap;\n"
    "    return self;\n"
    "}\n\n"
)

_Z_TEXTWRITER_CREATE = (
    "/* textwriter.create -- wrap a bufwriter. The sink is held by\n"
    "   borrow (path-scoped lock on the bufwriter keeps it stable\n"
    "   for the wrapper's lifetime). No backing buffer of our own --\n"
    "   we delegate to the bufwriter's buffer. */\n"
    "static z_TextWriter_t z_TextWriter_create(z_BufWriter_t* sink);\n"
    "static z_TextWriter_t z_TextWriter_create(z_BufWriter_t* sink) {\n"
    "    z_TextWriter_t self = {0};\n"
    "    self.sink = sink;\n"
    "    return self;\n"
    "}\n\n"
)

_Z_TEXTWRITER_WRITE = (
    "/* textwriter.write -- forward the stringview's bytes to the\n"
    "   underlying bufwriter. Stringviews are UTF-8 by construction\n"
    "   (the compiler's string/stringview types enforce it at their\n"
    "   ingress points), so no validation is performed here. */\n"
    "static z_Result_u64_IoError_t z_TextWriter_write(\n"
    "    z_TextWriter_t* self, z_StringView_t* from\n"
    ");\n"
    "static z_Result_u64_IoError_t z_TextWriter_write(\n"
    "    z_TextWriter_t* self, z_StringView_t* from\n"
    ") {\n"
    "    z_ListView_u8_t view = { from->length, (uint8_t*)from->data };\n"
    "    return z_BufWriter_write(self->sink, &view);\n"
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
    "static z_Result_u64_IoError_t z_TextWriter_writeLine(\n"
    "    z_TextWriter_t* self, z_StringView_t* from\n"
    ");\n"
    "static z_Result_u64_IoError_t z_TextWriter_writeLine(\n"
    "    z_TextWriter_t* self, z_StringView_t* from\n"
    ") {\n"
    "    z_ListView_u8_t body = { from->length, (uint8_t*)from->data };\n"
    "    z_Result_u64_IoError_t br = z_BufWriter_write(self->sink, &body);\n"
    "    if (br.tag != Z_RESULT_U64_IOERROR_TAG_OK) return br;\n"
    "    uint8_t nl = (uint8_t)'\\n';\n"
    "    z_ListView_u8_t tail = { (uint64_t)1, &nl };\n"
    "    z_Result_u64_IoError_t tr = z_BufWriter_write(self->sink, &tail);\n"
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
    "static z_Result_null_IoError_t z_TextWriter_flush(z_TextWriter_t* self);\n"
    "static z_Result_null_IoError_t z_TextWriter_flush(z_TextWriter_t* self) {\n"
    "    return z_BufWriter_flush(self->sink);\n"
    "}\n\n"
)

_Z_BUFREADER_READ = (
    "/* bufreader.read — drain the internal buffer into `into`; when\n"
    "   empty, refill from the source with a single `cap`-sized read\n"
    "   (or forward straight to the source when the caller wants more\n"
    "   than `cap` bytes). Returns what it has in one call; callers\n"
    "   that need exactly N bytes loop externally, matching the\n"
    "   reader-protocol contract ('short reads are fine'). */\n"
    "static z_Result_u64_IoError_t z_BufReader_read(\n"
    "    z_BufReader_t* self, z_List_u8_t* into, uint64_t max\n"
    ");\n"
    "static z_Result_u64_IoError_t z_BufReader_read(\n"
    "    z_BufReader_t* self, z_List_u8_t* into, uint64_t max\n"
    ") {\n"
    "    uint64_t available = self->buf.length - self->head;\n"
    "    if (available == 0) {\n"
    "        if (max >= self->cap) {\n"
    "            /* bypass: caller wants more than our buffer holds --\n"
    "               forward directly and skip the intermediate copy. */\n"
    "            return self->source.vtable->read(\n"
    "                self->source.data, into, max\n"
    "            );\n"
    "        }\n"
    "        /* refill: pull up to cap bytes into our buffer in one\n"
    "           source read, then fall through to the drain path. */\n"
    "        self->buf.length = 0;\n"
    "        self->head = 0;\n"
    "        z_Result_u64_IoError_t rr = self->source.vtable->read(\n"
    "            self->source.data, &self->buf, self->cap\n"
    "        );\n"
    "        if (rr.tag != Z_RESULT_U64_IOERROR_TAG_OK) return rr;\n"
    "        free(rr.data);\n"
    "        available = self->buf.length;\n"
    "        if (available == 0) return z_io_u64_ok(0);\n"
    "    }\n"
    "    uint64_t take = max < available ? max : available;\n"
    "    if (into->capacity < into->length + take) {\n"
    "        uint64_t newcap = into->length + take;\n"
    "        into->data = (uint8_t*)z_xrealloc(into->data, newcap);\n"
    "        into->capacity = newcap;\n"
    "    }\n"
    "    memcpy(\n"
    "        into->data + into->length,\n"
    "        self->buf.data + self->head,\n"
    "        take\n"
    "    );\n"
    "    into->length += take;\n"
    "    self->head += take;\n"
    "    return z_io_u64_ok(take);\n"
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
    "static z_Result_String_IoError_t z_io_String_err_arm(z_IoError_tag_t tag);\n"
    "static z_Result_String_IoError_t z_io_String_err_arm("
    "z_IoError_tag_t tag) {\n"
    "    z_Result_String_IoError_t result = {0};\n"
    "    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "    z_IoError_t e = {0};\n"
    "    e.tag = tag;\n"
    "    *boxed = e;\n"
    "    result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "    result.data = boxed;\n"
    "    return result;\n"
    "}\n\n"
    "/* Box a string value into result(string, ioerror) ok arm. Takes\n"
    "   ownership of `s` (its heap buffer moves into the boxed copy). */\n"
    "static z_Result_String_IoError_t z_io_String_ok(z_String_t s);\n"
    "static z_Result_String_IoError_t z_io_String_ok(z_String_t s) {\n"
    "    z_Result_String_IoError_t result = {0};\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
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
    "static z_TextReader_t z_TextReader_create(z_BufReader_t* source);\n"
    "static z_TextReader_t z_TextReader_create(z_BufReader_t* source) {\n"
    "    z_TextReader_t self = {0};\n"
    "    self.source = source;\n"
    "    self.buf = z_List_u8_create(0);\n"
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
    "static z_Result_String_IoError_t z_TextReader_readLine(\n"
    "    z_TextReader_t* self\n"
    ");\n"
    "static z_Result_String_IoError_t z_TextReader_readLine(\n"
    "    z_TextReader_t* self\n"
    ") {\n"
    "    uint64_t scan_start = 0;\n"
    "    for (;;) {\n"
    "        for (uint64_t i = scan_start; i < self->buf.length; i++) {\n"
    "            if (self->buf.data[i] == (uint8_t)'\\n') {\n"
    "                if (!z_io_utf8_is_valid(self->buf.data, i)) {\n"
    "                    return z_io_String_err_arm("
    "Z_IOERROR_TAG_BADENCODING);\n"
    "                }\n"
    "                z_String_t line = {0};\n"
    "                line.size = i;\n"
    "                line.capacity = i + 1;\n"
    "                line.data = (char*)z_xmalloc(line.capacity);\n"
    "                if (i > 0) memcpy(line.data, self->buf.data, i);\n"
    "                line.data[i] = '\\0';\n"
    "                uint64_t tail = self->buf.length - (i + 1);\n"
    "                if (tail > 0) {\n"
    "                    memmove(self->buf.data, self->buf.data + i + 1, tail);\n"
    "                }\n"
    "                self->buf.length = tail;\n"
    "                return z_io_String_ok(line);\n"
    "            }\n"
    "        }\n"
    "        scan_start = self->buf.length;\n"
    "        uint64_t cap = self->source->cap;\n"
    "        if (cap == 0) cap = 4096;\n"
    "        z_Result_u64_IoError_t rr = z_BufReader_read(\n"
    "            self->source, &self->buf, cap\n"
    "        );\n"
    "        if (rr.tag != Z_RESULT_U64_IOERROR_TAG_OK) {\n"
    "            z_Result_String_IoError_t result = {0};\n"
    "            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "            result.data = rr.data;\n"
    "            return result;\n"
    "        }\n"
    "        uint64_t n = *(uint64_t*)rr.data;\n"
    "        free(rr.data);\n"
    "        if (n == 0) {\n"
    "            if (self->buf.length == 0) {\n"
    "                return z_io_String_err_arm(Z_IOERROR_TAG_EOF);\n"
    "            }\n"
    "            uint64_t size = self->buf.length;\n"
    "            if (!z_io_utf8_is_valid(self->buf.data, size)) {\n"
    "                return z_io_String_err_arm("
    "Z_IOERROR_TAG_BADENCODING);\n"
    "            }\n"
    "            z_String_t line = {0};\n"
    "            line.size = size;\n"
    "            line.capacity = size + 1;\n"
    "            line.data = (char*)z_xmalloc(line.capacity);\n"
    "            if (size > 0) memcpy(line.data, self->buf.data, size);\n"
    "            line.data[size] = '\\0';\n"
    "            self->buf.length = 0;\n"
    "            return z_io_String_ok(line);\n"
    "        }\n"
    "    }\n"
    "}\n\n"
)

_Z_TEXTREADER_CALL = (
    "/* textreader.call -- iterator hook. Delegates to read_line and\n"
    "   collapses the result(string, ioerror) into option(string):\n"
    "   ok(s) -> some(s), any err -> none. The eof / badencoding / I/O\n"
    "   arms all terminate iteration silently; callers who need to\n"
    "   distinguish them must use read_line directly. */\n"
    "static z_Option_String_t z_TextReader_call(z_TextReader_t* self);\n"
    "static z_Option_String_t z_TextReader_call(z_TextReader_t* self) {\n"
    "    z_Option_String_t out = {0};\n"
    "    z_Result_String_IoError_t rr = z_TextReader_readLine(self);\n"
    "    if (rr.tag == Z_RESULT_STRING_IOERROR_TAG_OK) {\n"
    "        /* Move the heap-owned string out of rr's ok box into a\n"
    "           fresh some box; free the source box without calling\n"
    "           the string destructor (ownership moved). */\n"
    "        z_String_t* src = (z_String_t*)rr.data;\n"
    "        z_String_t* dst = "
    "(z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "        *dst = *src;\n"
    "        free(src);\n"
    "        out.tag = Z_OPTION_STRING_TAG_SOME;\n"
    "        out.data = dst;\n"
    "        return out;\n"
    "    }\n"
    "    /* err arm: run the ioerror's destructor + free the box so\n"
    "       string payloads (invalidpath / other) don't leak. */\n"
    "    if (rr.data) {\n"
    "        z_IoError_destroy((z_IoError_t*)rr.data);\n"
    "        free(rr.data);\n"
    "    }\n"
    "    out.tag = Z_OPTION_STRING_TAG_NONE;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_FILE_SEEK = (
    "/* file.seek — reposition the fd head. Maps seekorigin to the\n"
    "   matching POSIX whence constant. Returns the new absolute\n"
    "   position measured from the start of the file. */\n"
    "static z_Result_u64_IoError_t z_File_seek(\n"
    "    z_File_t* f, int64_t off, z_seekorigin_t origin\n"
    ");\n"
    "static z_Result_u64_IoError_t z_File_seek(\n"
    "    z_File_t* f, int64_t off, z_seekorigin_t origin\n"
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


_Z_OS_ARGV_GLOBALS = (
    "/* Process argc/argv captured by main() before z_main runs, so\n"
    "   os.args can expose them without threading them through every\n"
    "   call site. Only emitted when the program references os.args. */\n"
    "static int z_os_argc_g;\n"
    "static char** z_os_argv_g;\n\n"
)

_Z_OS_ARGS = (
    "/* os.args -- copy argv into a freshly-allocated list of strings.\n"
    "   The caller owns the outer list and every string inside; scope\n"
    "   exit runs the list destructor which frees each element. */\n"
    "static z_List_String_t z_os_args(void);\n"
    "static z_List_String_t z_os_args(void) {\n"
    "    uint64_t n = (uint64_t)(z_os_argc_g > 0 ? z_os_argc_g : 0);\n"
    "    z_List_String_t out = {0};\n"
    "    out.length = n;\n"
    "    out.capacity = n;\n"
    "    if (n > 0) {\n"
    "        out.data = (z_String_t*)z_xmalloc(n * sizeof(z_String_t));\n"
    "        for (uint64_t i = 0; i < n; i++) {\n"
    "            out.data[i] = z_String_new(z_os_argv_g[i]);\n"
    "        }\n"
    "    }\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_GET_ENV = (
    "/* os.get_env -- look up the POSIX environment by name. Returns\n"
    "   option.some(string) on hit (copying the value so the caller\n"
    "   owns the heap buffer and libc's environ isn't aliased); returns\n"
    "   option.none on miss. `key` is a borrowed view; caller retains. */\n"
    "static z_Option_String_t z_os_env(z_StringView_t key);\n"
    "static z_Option_String_t z_os_env(z_StringView_t key) {\n"
    "    z_Option_String_t out = {0};\n"
    "    char* key_cstr = z_sv_to_cstr(key);\n"
    "    const char* v = getenv(key_cstr);\n"
    "    free(key_cstr);\n"
    "    if (v == NULL) {\n"
    "        out.tag = Z_OPTION_STRING_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *boxed = z_String_new(v);\n"
    "    out.tag = Z_OPTION_STRING_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_SET_ENV = (
    "/* os.set_env -- setenv(key, value, 1): always overwrites. libc\n"
    "   copies key/value into its own storage. Both args are borrowed\n"
    "   views; caller retains. */\n"
    "static z_Result_null_IoError_t z_os_setEnv(\n"
    "    z_StringView_t key, z_StringView_t value\n"
    ");\n"
    "static z_Result_null_IoError_t z_os_setEnv(\n"
    "    z_StringView_t key, z_StringView_t value\n"
    ") {\n"
    "    z_Result_null_IoError_t out = {0};\n"
    "    char* key_cstr = z_sv_to_cstr(key);\n"
    "    char* value_cstr = z_sv_to_cstr(value);\n"
    "    int rc = setenv(key_cstr, value_cstr, 1);\n"
    "    int e = errno;\n"
    "    free(key_cstr);\n"
    "    free(value_cstr);\n"
    "    if (rc != 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        out.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    out.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "    out.data = NULL;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_UNSET_ENV = (
    "/* os.unset_env -- unsetenv(key). Idempotent: removing a variable\n"
    "   that was never set returns ok null. */\n"
    "static z_Result_null_IoError_t z_os_unsetEnv(z_StringView_t key);\n"
    "static z_Result_null_IoError_t z_os_unsetEnv(z_StringView_t key) {\n"
    "    z_Result_null_IoError_t out = {0};\n"
    "    char* key_cstr = z_sv_to_cstr(key);\n"
    "    int rc = unsetenv(key_cstr);\n"
    "    int e = errno;\n"
    "    free(key_cstr);\n"
    "    if (rc != 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        out.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    out.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "    out.data = NULL;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_ENV_NAMES = (
    "/* os.env_names -- snapshot of environ keys. Each entry in\n"
    "   environ is `key=value`; we copy the portion before the first\n"
    "   `=` into a freshly-allocated z_String_t and push onto an\n"
    "   owned list. The list's destructor frees each string. */\n"
    "extern char** environ;\n"
    "static z_List_String_t z_os_envNames(void);\n"
    "static z_List_String_t z_os_envNames(void) {\n"
    "    z_List_String_t out = z_List_String_create((uint64_t)0);\n"
    "    if (environ == NULL) return out;\n"
    "    for (char** ep = environ; *ep != NULL; ep++) {\n"
    "        const char* entry = *ep;\n"
    "        const char* eq = strchr(entry, '=');\n"
    "        size_t n = (eq != NULL) ? (size_t)(eq - entry) : strlen(entry);\n"
    "        z_String_t s = z_String_create((uint64_t)n);\n"
    "        z_String_append(&s, entry, (uint64_t)n);\n"
    "        z_List_String_append(&out, s);\n"
    "    }\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_CWD = (
    "/* os.cwd -- getcwd(NULL, 0) allocates a buffer sized to the\n"
    "   current path. We copy into a z_String_t and free the libc\n"
    "   buffer so ownership stays uniform. */\n"
    "static z_Result_String_IoError_t z_os_cwd(void);\n"
    "static z_Result_String_IoError_t z_os_cwd(void) {\n"
    "    z_Result_String_IoError_t out = {0};\n"
    "    char* buf = getcwd(NULL, 0);\n"
    "    if (buf == NULL) {\n"
    "        int e = errno;\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *boxed = z_String_new(buf);\n"
    "    free(buf);\n"
    "    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_SET_CWD = (
    "/* os.set_cwd -- chdir(path). `path` is a borrowed view; caller\n"
    "   retains. */\n"
    "static z_Result_null_IoError_t z_os_setCwd(z_StringView_t path);\n"
    "static z_Result_null_IoError_t z_os_setCwd(z_StringView_t path) {\n"
    "    z_Result_null_IoError_t out = {0};\n"
    "    char* path_cstr = z_sv_to_cstr(path);\n"
    "    int rc = chdir(path_cstr);\n"
    "    int e = errno;\n"
    "    free(path_cstr);\n"
    "    if (rc != 0) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        out.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    out.tag = Z_RESULT_NULL_IOERROR_TAG_OK;\n"
    "    out.data = NULL;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_PID = (
    "/* os.pid -- getpid. pid_t fits in int32_t on every supported\n"
    "   platform (Linux caps at 4_194_304 by default). */\n"
    "static int32_t z_os_pid(void);\n"
    "static int32_t z_os_pid(void) {\n"
    "    return (int32_t)getpid();\n"
    "}\n\n"
)

_Z_OS_PPID = (
    "/* os.ppid -- getppid. */\n"
    "static int32_t z_os_ppid(void);\n"
    "static int32_t z_os_ppid(void) {\n"
    "    return (int32_t)getppid();\n"
    "}\n\n"
)

_Z_OS_USER_NAME = (
    "/* os.user_name -- resolve the effective uid via getpwuid_r.\n"
    "   Starts with a 1 KiB buffer and retries once with 16 KiB if\n"
    "   ERANGE; beyond that, surface as ioerror.other. */\n"
    "static z_Result_String_IoError_t z_os_userName(void);\n"
    "static z_Result_String_IoError_t z_os_userName(void) {\n"
    "    z_Result_String_IoError_t out = {0};\n"
    "    uid_t uid = geteuid();\n"
    "    size_t bufsize = 1024;\n"
    "    for (int attempt = 0; attempt < 2; attempt++) {\n"
    "        char* buf = (char*)z_xmalloc(bufsize);\n"
    "        struct passwd pwd;\n"
    "        struct passwd* result = NULL;\n"
    "        int rc = getpwuid_r(uid, &pwd, buf, bufsize, &result);\n"
    "        if (rc == 0 && result != NULL) {\n"
    "            z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "            *boxed = z_String_new(pwd.pw_name);\n"
    "            free(buf);\n"
    "            out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "            out.data = boxed;\n"
    "            return out;\n"
    "        }\n"
    "        free(buf);\n"
    "        if (rc == ERANGE && attempt == 0) {\n"
    "            bufsize = 16 * 1024;\n"
    "            continue;\n"
    "        }\n"
    "        int e = (rc == 0) ? ENOENT : rc;\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    /* unreachable */\n"
    "    out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "    out.data = NULL;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_HOME_DIR = (
    "/* os.home_dir -- read $HOME from the environment. No passwd\n"
    "   fallback: callers who care about stripped environments must\n"
    "   set HOME explicitly. */\n"
    "static z_Result_String_IoError_t z_os_homeDir(void);\n"
    "static z_Result_String_IoError_t z_os_homeDir(void) {\n"
    "    z_Result_String_IoError_t out = {0};\n"
    '    const char* h = getenv("HOME");\n'
    "    if (h == NULL) {\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        z_IoError_t v = {0};\n"
    "        v.tag = Z_IOERROR_TAG_OTHER;\n"
    "        z_String_t* sp = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    '        *sp = z_String_new("$HOME is not set");\n'
    "        v.data = sp;\n"
    "        *boxed = v;\n"
    "        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *boxed = z_String_new(h);\n"
    "    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_PLATFORM = (
    "/* os.platform -- compile-time selection via C preprocessor.\n"
    "   Falls through to `other` on unrecognised hosts. */\n"
    "static z_platformkind_t z_os_platform(void);\n"
    "static z_platformkind_t z_os_platform(void) {\n"
    "    z_platformkind_t out = {0};\n"
    "#if defined(__linux__)\n"
    "    out.tag = Z_PLATFORMKIND_TAG_LINUX;\n"
    "#elif defined(__APPLE__)\n"
    "    out.tag = Z_PLATFORMKIND_TAG_DARWIN;\n"
    "#elif defined(_WIN32)\n"
    "    out.tag = Z_PLATFORMKIND_TAG_WINDOWS;\n"
    "#else\n"
    "    out.tag = Z_PLATFORMKIND_TAG_OTHER;\n"
    "#endif\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_ARCH = (
    "/* os.arch -- compile-time CPU family. */\n"
    "static z_archkind_t z_os_arch(void);\n"
    "static z_archkind_t z_os_arch(void) {\n"
    "    z_archkind_t out = {0};\n"
    "#if defined(__x86_64__) || defined(_M_X64)\n"
    "    out.tag = Z_ARCHKIND_TAG_X86_64;\n"
    "#elif defined(__aarch64__) || defined(_M_ARM64)\n"
    "    out.tag = Z_ARCHKIND_TAG_AARCH64;\n"
    "#else\n"
    "    out.tag = Z_ARCHKIND_TAG_OTHER;\n"
    "#endif\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_HOSTNAME = (
    "/* os.hostname -- gethostname(3). HOST_NAME_MAX is typically 64\n"
    "   on Linux; we use 256 to be defensive on unusual POSIX hosts\n"
    "   and truncate on overflow. */\n"
    "static z_Result_String_IoError_t z_os_hostname(void);\n"
    "static z_Result_String_IoError_t z_os_hostname(void) {\n"
    "    z_Result_String_IoError_t out = {0};\n"
    "    char buf[256];\n"
    "    if (gethostname(buf, sizeof(buf)) != 0) {\n"
    "        int e = errno;\n"
    "        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));\n"
    "        *boxed = z_io_errno_to_IoError(e);\n"
    "        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    buf[sizeof(buf) - 1] = '\\0';\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *boxed = z_String_new(buf);\n"
    "    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_OS_EXIT = (
    "/* os.exit -- libc exit with the given status. Does not return;\n"
    "   in-scope zerolang destructors are skipped. */\n"
    "static void z_os_exit(int32_t code);\n"
    "static void z_os_exit(int32_t code) {\n"
    "    exit((int)code);\n"
    "}\n\n"
)


# -- Phase S1: non-allocating query natives on stringview ---------
# Emitted AFTER mono types so the option/optionval wrappers they
# return (e.g. z_optionval_u64_t) are already declared. Per-name
# gated like io/os.

_Z_SV_IS_EMPTY = (
    "static bool z_StringView_isEmpty(const z_StringView_t* self);\n"
    "static bool z_StringView_isEmpty(const z_StringView_t* self) {\n"
    "    return self->length == 0;\n"
    "}\n\n"
)

_Z_SV_IS_ASCII = (
    "static bool z_StringView_isAscii(const z_StringView_t* self);\n"
    "static bool z_StringView_isAscii(const z_StringView_t* self) {\n"
    "    for (uint64_t i = 0; i < self->length; i++) {\n"
    "        if ((unsigned char)self->data[i] >= 0x80) return false;\n"
    "    }\n"
    "    return true;\n"
    "}\n\n"
)

_Z_SV_STARTS_WITH = (
    "static bool z_StringView_startsWith(const z_StringView_t* self, const z_StringView_t* prefix);\n"
    "static bool z_StringView_startsWith(const z_StringView_t* self, const z_StringView_t* prefix) {\n"
    "    if (prefix->length > self->length) return false;\n"
    "    if (prefix->length == 0) return true;\n"
    "    return memcmp(self->data, prefix->data, prefix->length) == 0;\n"
    "}\n\n"
)

_Z_SV_ENDS_WITH = (
    "static bool z_StringView_endsWith(const z_StringView_t* self, const z_StringView_t* suffix);\n"
    "static bool z_StringView_endsWith(const z_StringView_t* self, const z_StringView_t* suffix) {\n"
    "    if (suffix->length > self->length) return false;\n"
    "    if (suffix->length == 0) return true;\n"
    "    return memcmp(self->data + (self->length - suffix->length),\n"
    "                  suffix->data, suffix->length) == 0;\n"
    "}\n\n"
)

# Raw byte-search helper used by contains / index_of. Returns\n
# UINT64_MAX on miss. Empty needle returns 0.
_Z_SV_INDEX_OF_RAW = (
    "static uint64_t z_StringView_indexOf_raw(const z_StringView_t* self, const z_StringView_t* needle);\n"
    "static uint64_t z_StringView_indexOf_raw(const z_StringView_t* self, const z_StringView_t* needle) {\n"
    "    if (needle->length == 0) return 0;\n"
    "    if (needle->length > self->length) return UINT64_MAX;\n"
    "    uint64_t last = self->length - needle->length;\n"
    "    for (uint64_t i = 0; i <= last; i++) {\n"
    "        if (memcmp(self->data + i, needle->data, needle->length) == 0) {\n"
    "            return i;\n"
    "        }\n"
    "    }\n"
    "    return UINT64_MAX;\n"
    "}\n\n"
)

_Z_SV_CONTAINS = (
    "static bool z_StringView_contains(const z_StringView_t* self, const z_StringView_t* needle);\n"
    "static bool z_StringView_contains(const z_StringView_t* self, const z_StringView_t* needle) {\n"
    "    return z_StringView_indexOf_raw(self, needle) != UINT64_MAX;\n"
    "}\n\n"
)

_Z_SV_INDEX_OF = (
    "static z_optionval_u64_t z_StringView_indexOf(const z_StringView_t* self, const z_StringView_t* needle);\n"
    "static z_optionval_u64_t z_StringView_indexOf(const z_StringView_t* self, const z_StringView_t* needle) {\n"
    "    z_optionval_u64_t out = {0};\n"
    "    uint64_t r = z_StringView_indexOf_raw(self, needle);\n"
    "    if (r == UINT64_MAX) {\n"
    "        out.tag = Z_OPTIONVAL_U64_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    out.tag = Z_OPTIONVAL_U64_TAG_SOME;\n"
    "    out.data.some = r;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_LAST_INDEX_OF = (
    "static z_optionval_u64_t z_StringView_lastIndexOf(const z_StringView_t* self, const z_StringView_t* needle);\n"
    "static z_optionval_u64_t z_StringView_lastIndexOf(const z_StringView_t* self, const z_StringView_t* needle) {\n"
    "    z_optionval_u64_t out = {0};\n"
    "    if (needle->length == 0) {\n"
    "        out.tag = Z_OPTIONVAL_U64_TAG_SOME;\n"
    "        out.data.some = self->length;\n"
    "        return out;\n"
    "    }\n"
    "    if (needle->length > self->length) {\n"
    "        out.tag = Z_OPTIONVAL_U64_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    for (uint64_t i = self->length - needle->length + 1; i > 0; i--) {\n"
    "        uint64_t idx = i - 1;\n"
    "        if (memcmp(self->data + idx, needle->data, needle->length) == 0) {\n"
    "            out.tag = Z_OPTIONVAL_U64_TAG_SOME;\n"
    "            out.data.some = idx;\n"
    "            return out;\n"
    "        }\n"
    "    }\n"
    "    out.tag = Z_OPTIONVAL_U64_TAG_NONE;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_BYTE_AT = (
    "static z_optionval_u8_t z_StringView_byteAt(const z_StringView_t* self, uint64_t i);\n"
    "static z_optionval_u8_t z_StringView_byteAt(const z_StringView_t* self, uint64_t i) {\n"
    "    z_optionval_u8_t out = {0};\n"
    "    if (i >= self->length) {\n"
    "        out.tag = Z_OPTIONVAL_U8_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    out.tag = Z_OPTIONVAL_U8_TAG_SOME;\n"
    "    out.data.some = (uint8_t)self->data[i];\n"
    "    return out;\n"
    "}\n\n"
)

# -- Phase S2: view-returning slicing helpers ---------------------

# ASCII whitespace: space, tab, LF, VT, FF, CR. Matches Rust's
# `is_ascii_whitespace`. Small inline helper shared by trim.
_Z_SV_IS_ASCII_WS = (
    "static bool z_ascii_is_ws(unsigned char c);\n"
    "static bool z_ascii_is_ws(unsigned char c) {\n"
    "    return c == ' ' || c == '\\t' || c == '\\n'\n"
    "        || c == '\\v' || c == '\\f' || c == '\\r';\n"
    "}\n\n"
)

_Z_SV_TRIM = (
    "static z_StringView_t z_StringView_trim(const z_StringView_t* self);\n"
    "static z_StringView_t z_StringView_trim(const z_StringView_t* self) {\n"
    "    const char* p = self->data;\n"
    "    const char* end = self->data + self->length;\n"
    "    while (p < end && z_ascii_is_ws((unsigned char)*p)) p++;\n"
    "    while (end > p && z_ascii_is_ws((unsigned char)end[-1])) end--;\n"
    "    z_StringView_t out = { p, (uint64_t)(end - p) };\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_TRIM_START = (
    "static z_StringView_t z_StringView_trimStart(const z_StringView_t* self);\n"
    "static z_StringView_t z_StringView_trimStart(const z_StringView_t* self) {\n"
    "    const char* p = self->data;\n"
    "    const char* end = self->data + self->length;\n"
    "    while (p < end && z_ascii_is_ws((unsigned char)*p)) p++;\n"
    "    z_StringView_t out = { p, (uint64_t)(end - p) };\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_TRIM_END = (
    "static z_StringView_t z_StringView_trimEnd(const z_StringView_t* self);\n"
    "static z_StringView_t z_StringView_trimEnd(const z_StringView_t* self) {\n"
    "    const char* p = self->data;\n"
    "    const char* end = self->data + self->length;\n"
    "    while (end > p && z_ascii_is_ws((unsigned char)end[-1])) end--;\n"
    "    z_StringView_t out = { p, (uint64_t)(end - p) };\n"
    "    return out;\n"
    "}\n\n"
)

# strip_prefix / strip_suffix return option(stringview). The some
# arm is a heap-boxed stringview (union payloads are void*); the
# compiler-generated destructor frees it on scope exit. One tiny
# (16 byte) allocation per call.
_Z_SV_STRIP_PREFIX = (
    "static z_Option_StringView_t z_StringView_stripPrefix(\n"
    "    const z_StringView_t* self, const z_StringView_t* p);\n"
    "static z_Option_StringView_t z_StringView_stripPrefix(\n"
    "    const z_StringView_t* self, const z_StringView_t* p) {\n"
    "    z_Option_StringView_t out = {0};\n"
    "    if (p->length > self->length\n"
    "        || (p->length > 0 && memcmp(self->data, p->data, p->length) != 0)) {\n"
    "        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));\n"
    "    boxed->data = self->data + p->length;\n"
    "    boxed->length = self->length - p->length;\n"
    "    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

# -- Phase S3: splitter / linesiter ------------------------------

# Shared struct layout for both splitter and linesiter. Fields are
# not exposed to zerolang; the classes are declared `is native`.
# The emitter emits a typedef for each class based on the declared
# fields — since there are none, the runtime must provide the full
# struct here. We shadow the compiler-emitted empty typedef by
# defining the struct first and using a different name with a
# forward `typedef`.
_Z_SV_SPLITTER_STRUCT = (
    "/* Splitter / linesiter state. Classes are declared `is native`\n"
    "   with no user fields; the runtime supplies the real layout,\n"
    "   stack-allocated value type (48 bytes). Both iterators share\n"
    "   the same struct — linesiter leaves sep NULL. */\n"
    "typedef struct {\n"
    "    const char* src;\n"
    "    uint64_t    src_len;\n"
    "    const char* sep;\n"
    "    uint64_t    sep_len;\n"
    "    uint64_t    cursor;\n"
    "    bool        done;\n"
    "} z_Splitter_t;\n\n"
    "typedef z_Splitter_t z_LinesIter_t;\n\n"
)

_Z_SV_SPLIT = (
    "static z_Splitter_t z_StringView_split(\n"
    "    const z_StringView_t* self, const z_StringView_t* sep);\n"
    "static z_Splitter_t z_StringView_split(\n"
    "    const z_StringView_t* self, const z_StringView_t* sep) {\n"
    "    z_Splitter_t s;\n"
    "    s.src = self->data;\n"
    "    s.src_len = self->length;\n"
    "    s.sep = sep->data;\n"
    "    s.sep_len = sep->length;\n"
    "    s.cursor = 0;\n"
    "    s.done = (sep->length == 0);\n"
    "    return s;\n"
    "}\n\n"
)

_Z_SV_SPLITTER_CALL = (
    "static z_Option_StringView_t z_Splitter_call(z_Splitter_t* s);\n"
    "static z_Option_StringView_t z_Splitter_call(z_Splitter_t* s) {\n"
    "    z_Option_StringView_t out = {0};\n"
    "    if (s->done) {\n"
    "        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    const char* start = s->src + s->cursor;\n"
    "    uint64_t remaining = s->src_len - s->cursor;\n"
    "    if (s->sep_len > remaining) {\n"
    "        /* no more separators possible — return the tail */\n"
    "        z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));\n"
    "        boxed->data = start;\n"
    "        boxed->length = remaining;\n"
    "        s->cursor = s->src_len;\n"
    "        s->done = true;\n"
    "        out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;\n"
    "        out.data = boxed;\n"
    "        return out;\n"
    "    }\n"
    "    uint64_t scan_end = remaining - s->sep_len;\n"
    "    for (uint64_t i = 0; i <= scan_end; i++) {\n"
    "        if (memcmp(start + i, s->sep, s->sep_len) == 0) {\n"
    "            z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));\n"
    "            boxed->data = start;\n"
    "            boxed->length = i;\n"
    "            s->cursor += i + s->sep_len;\n"
    "            out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;\n"
    "            out.data = boxed;\n"
    "            return out;\n"
    "        }\n"
    "    }\n"
    "    /* no separator in remaining — final fragment */\n"
    "    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));\n"
    "    boxed->data = start;\n"
    "    boxed->length = remaining;\n"
    "    s->cursor = s->src_len;\n"
    "    s->done = true;\n"
    "    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_SPLIT_ONCE = (
    "static z_optionval_u64_t z_StringView_splitOnce(\n"
    "    const z_StringView_t* self, const z_StringView_t* sep);\n"
    "static z_optionval_u64_t z_StringView_splitOnce(\n"
    "    const z_StringView_t* self, const z_StringView_t* sep) {\n"
    "    z_optionval_u64_t out = {0};\n"
    "    if (sep->length == 0) {\n"
    "        out.tag = Z_OPTIONVAL_U64_TAG_SOME;\n"
    "        out.data.some = 0;\n"
    "        return out;\n"
    "    }\n"
    "    if (sep->length > self->length) {\n"
    "        out.tag = Z_OPTIONVAL_U64_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    uint64_t last = self->length - sep->length;\n"
    "    for (uint64_t i = 0; i <= last; i++) {\n"
    "        if (memcmp(self->data + i, sep->data, sep->length) == 0) {\n"
    "            out.tag = Z_OPTIONVAL_U64_TAG_SOME;\n"
    "            out.data.some = i;\n"
    "            return out;\n"
    "        }\n"
    "    }\n"
    "    out.tag = Z_OPTIONVAL_U64_TAG_NONE;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_LINES = (
    "static z_LinesIter_t z_StringView_lines(const z_StringView_t* self);\n"
    "static z_LinesIter_t z_StringView_lines(const z_StringView_t* self) {\n"
    "    z_LinesIter_t s;\n"
    "    s.src = self->data;\n"
    "    s.src_len = self->length;\n"
    "    s.sep = NULL;\n"
    "    s.sep_len = 0;\n"
    "    s.cursor = 0;\n"
    "    s.done = (self->length == 0);\n"
    "    return s;\n"
    "}\n\n"
)

# -- Phase S4: allocating transforms -------------------------------
# All return a freshly-allocated z_String_t; caller's scope cleanup
# frees the heap buffer.

_Z_SV_TO_LOWER_ASCII = (
    "static z_String_t z_StringView_toLowerAscii(const z_StringView_t* self);\n"
    "static z_String_t z_StringView_toLowerAscii(const z_StringView_t* self) {\n"
    "    z_String_t z = {0};\n"
    "    z.size = self->length;\n"
    "    z.capacity = self->length + 1;\n"
    "    z.data = (char*)z_xmalloc(z.capacity);\n"
    "    for (uint64_t i = 0; i < self->length; i++) {\n"
    "        unsigned char c = (unsigned char)self->data[i];\n"
    "        if (c >= 'A' && c <= 'Z') c = (unsigned char)(c + ('a' - 'A'));\n"
    "        z.data[i] = (char)c;\n"
    "    }\n"
    "    z.data[self->length] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
)

_Z_SV_TO_UPPER_ASCII = (
    "static z_String_t z_StringView_toUpperAscii(const z_StringView_t* self);\n"
    "static z_String_t z_StringView_toUpperAscii(const z_StringView_t* self) {\n"
    "    z_String_t z = {0};\n"
    "    z.size = self->length;\n"
    "    z.capacity = self->length + 1;\n"
    "    z.data = (char*)z_xmalloc(z.capacity);\n"
    "    for (uint64_t i = 0; i < self->length; i++) {\n"
    "        unsigned char c = (unsigned char)self->data[i];\n"
    "        if (c >= 'a' && c <= 'z') c = (unsigned char)(c - ('a' - 'A'));\n"
    "        z.data[i] = (char)c;\n"
    "    }\n"
    "    z.data[self->length] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
)

# Shared replace implementation. `once=true` replaces only the
# first occurrence.
_Z_SV_REPLACE_IMPL = (
    "static z_String_t z_StringView_replace_impl(\n"
    "    const z_StringView_t* self, const z_StringView_t* needle,\n"
    "    const z_StringView_t* repl, bool once);\n"
    "static z_String_t z_StringView_replace_impl(\n"
    "    const z_StringView_t* self, const z_StringView_t* needle,\n"
    "    const z_StringView_t* repl, bool once) {\n"
    "    /* empty needle: just copy self */\n"
    "    if (needle->length == 0) {\n"
    "        z_String_t z = {0};\n"
    "        z.size = self->length;\n"
    "        z.capacity = self->length + 1;\n"
    "        z.data = (char*)z_xmalloc(z.capacity);\n"
    "        memcpy(z.data, self->data, self->length);\n"
    "        z.data[self->length] = '\\0';\n"
    "        return z;\n"
    "    }\n"
    "    /* pass 1: count matches so we can size the output once */\n"
    "    uint64_t matches = 0;\n"
    "    if (needle->length <= self->length) {\n"
    "        uint64_t last = self->length - needle->length;\n"
    "        uint64_t i = 0;\n"
    "        while (i <= last) {\n"
    "            if (memcmp(self->data + i, needle->data, needle->length) == 0) {\n"
    "                matches++;\n"
    "                i += needle->length;\n"
    "                if (once) break;\n"
    "            } else {\n"
    "                i++;\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    uint64_t delta_per =\n"
    "        (repl->length > needle->length)\n"
    "            ? (repl->length - needle->length)\n"
    "            : 0;\n"
    "    uint64_t shrink_per =\n"
    "        (needle->length > repl->length)\n"
    "            ? (needle->length - repl->length)\n"
    "            : 0;\n"
    "    uint64_t out_len = self->length + matches * delta_per - matches * shrink_per;\n"
    "    z_String_t z = {0};\n"
    "    z.size = out_len;\n"
    "    z.capacity = out_len + 1;\n"
    "    z.data = (char*)z_xmalloc(z.capacity);\n"
    "    /* pass 2: copy with replacement */\n"
    "    uint64_t src_i = 0, dst_i = 0, done_matches = 0;\n"
    "    while (src_i < self->length) {\n"
    "        bool can_match = (done_matches < matches)\n"
    "                      && (src_i + needle->length <= self->length)\n"
    "                      && (memcmp(self->data + src_i, needle->data, needle->length) == 0);\n"
    "        if (can_match) {\n"
    "            memcpy(z.data + dst_i, repl->data, repl->length);\n"
    "            dst_i += repl->length;\n"
    "            src_i += needle->length;\n"
    "            done_matches++;\n"
    "        } else {\n"
    "            z.data[dst_i++] = self->data[src_i++];\n"
    "        }\n"
    "    }\n"
    "    z.data[dst_i] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
)

_Z_SV_REPLACE = (
    "static z_String_t z_StringView_replace(\n"
    "    const z_StringView_t* self, const z_StringView_t* needle,\n"
    "    const z_StringView_t* replacement);\n"
    "static z_String_t z_StringView_replace(\n"
    "    const z_StringView_t* self, const z_StringView_t* needle,\n"
    "    const z_StringView_t* replacement) {\n"
    "    return z_StringView_replace_impl(self, needle, replacement, false);\n"
    "}\n\n"
)

_Z_SV_REPLACE_FIRST = (
    "static z_String_t z_StringView_replaceFirst(\n"
    "    const z_StringView_t* self, const z_StringView_t* needle,\n"
    "    const z_StringView_t* replacement);\n"
    "static z_String_t z_StringView_replaceFirst(\n"
    "    const z_StringView_t* self, const z_StringView_t* needle,\n"
    "    const z_StringView_t* replacement) {\n"
    "    return z_StringView_replace_impl(self, needle, replacement, true);\n"
    "}\n\n"
)

_Z_SV_REPEAT = (
    "static z_String_t z_StringView_repeated(const z_StringView_t* self, uint64_t n);\n"
    "static z_String_t z_StringView_repeated(const z_StringView_t* self, uint64_t n) {\n"
    "    z_String_t z = {0};\n"
    "    uint64_t total = self->length * n;\n"
    "    z.size = total;\n"
    "    z.capacity = total + 1;\n"
    "    z.data = (char*)z_xmalloc(z.capacity);\n"
    "    for (uint64_t i = 0; i < n; i++) {\n"
    "        memcpy(z.data + i * self->length, self->data, self->length);\n"
    "    }\n"
    "    z.data[total] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
)

# -- Phase S5: codepoint iteration + count -------------------------

_Z_SV_CPITER_STRUCT = (
    "/* Codepoint iterator state. Opaque to zerolang. */\n"
    "typedef struct {\n"
    "    const char* data;\n"
    "    uint64_t    length;\n"
    "    uint64_t    cursor;\n"
    "} z_CpIter_t;\n\n"
)

# Decode one UTF-8 codepoint starting at data[i]. Advances i past
# the consumed bytes. Ill-formed sequences return U+FFFD and
# advance one byte. Assumes i < length on entry.
_Z_SV_UTF8_DECODE = (
    "static uint32_t z_utf8_decode_one(\n"
    "    const char* data, uint64_t length, uint64_t* i);\n"
    "static uint32_t z_utf8_decode_one(\n"
    "    const char* data, uint64_t length, uint64_t* i) {\n"
    "    uint64_t idx = *i;\n"
    "    unsigned char b0 = (unsigned char)data[idx];\n"
    "    if (b0 < 0x80) { *i = idx + 1; return (uint32_t)b0; }\n"
    "    uint32_t cp;\n"
    "    uint64_t need;\n"
    "    if ((b0 & 0xE0) == 0xC0) { cp = b0 & 0x1Fu; need = 1; }\n"
    "    else if ((b0 & 0xF0) == 0xE0) { cp = b0 & 0x0Fu; need = 2; }\n"
    "    else if ((b0 & 0xF8) == 0xF0) { cp = b0 & 0x07u; need = 3; }\n"
    "    else { *i = idx + 1; return 0xFFFDu; }\n"
    "    if (idx + 1 + need > length) { *i = idx + 1; return 0xFFFDu; }\n"
    "    for (uint64_t k = 1; k <= need; k++) {\n"
    "        unsigned char b = (unsigned char)data[idx + k];\n"
    "        if ((b & 0xC0) != 0x80) { *i = idx + 1; return 0xFFFDu; }\n"
    "        cp = (cp << 6) | (b & 0x3Fu);\n"
    "    }\n"
    "    *i = idx + 1 + need;\n"
    "    return cp;\n"
    "}\n\n"
)

_Z_SV_COUNT = (
    "static uint64_t z_StringView_count(const z_StringView_t* self);\n"
    "static uint64_t z_StringView_count(const z_StringView_t* self) {\n"
    "    uint64_t i = 0, n = 0;\n"
    "    while (i < self->length) {\n"
    "        (void)z_utf8_decode_one(self->data, self->length, &i);\n"
    "        n++;\n"
    "    }\n"
    "    return n;\n"
    "}\n\n"
)

_Z_SV_CODEPOINTS = (
    "static z_CpIter_t z_StringView_codepoints(const z_StringView_t* self);\n"
    "static z_CpIter_t z_StringView_codepoints(const z_StringView_t* self) {\n"
    "    z_CpIter_t it;\n"
    "    it.data = self->data;\n"
    "    it.length = self->length;\n"
    "    it.cursor = 0;\n"
    "    return it;\n"
    "}\n\n"
)

_Z_SV_CPITER_CALL = (
    "static z_optionval_u32_t z_CpIter_call(z_CpIter_t* it);\n"
    "static z_optionval_u32_t z_CpIter_call(z_CpIter_t* it) {\n"
    "    z_optionval_u32_t out = {0};\n"
    "    if (it->cursor >= it->length) {\n"
    "        out.tag = Z_OPTIONVAL_U32_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    uint32_t cp = z_utf8_decode_one(it->data, it->length, &it->cursor);\n"
    "    out.tag = Z_OPTIONVAL_U32_TAG_SOME;\n"
    "    out.data.some = cp;\n"
    "    return out;\n"
    "}\n\n"
)

# -- Phase S6: numeric parsing -----------------------------------

_Z_SV_PARSE_I64 = (
    "static z_resultval_i64_parseerror_t z_StringView_parseI64(const z_StringView_t* self);\n"
    "static z_resultval_i64_parseerror_t z_StringView_parseI64(const z_StringView_t* self) {\n"
    "    z_resultval_i64_parseerror_t out = {0};\n"
    "    uint64_t i = 0;\n"
    "    int neg = 0;\n"
    "    if (self->length == 0) goto empty;\n"
    "    if (self->data[i] == '+' || self->data[i] == '-') {\n"
    "        if (self->data[i] == '-') neg = 1;\n"
    "        i++;\n"
    "    }\n"
    "    if (i >= self->length) goto empty;\n"
    "    uint64_t acc = 0;\n"
    "    for (; i < self->length; i++) {\n"
    "        unsigned char c = (unsigned char)self->data[i];\n"
    "        if (c < '0' || c > '9') goto invalid;\n"
    "        uint64_t d = (uint64_t)(c - '0');\n"
    "        if (acc > (UINT64_MAX - d) / 10) goto overflow;\n"
    "        acc = acc * 10 + d;\n"
    "    }\n"
    "    /* range check: fits in i64 */\n"
    "    uint64_t bound = neg ? (uint64_t)1 << 63 : ((uint64_t)1 << 63) - 1;\n"
    "    if (acc > bound) goto overflow;\n"
    "    int64_t v = neg ? -(int64_t)acc : (int64_t)acc;\n"
    "    if (neg && acc == ((uint64_t)1 << 63)) v = INT64_MIN;\n"
    "    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_OK;\n"
    "    out.data.ok = v;\n"
    "    return out;\n"
    "empty:\n"
    "    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_ERR;\n"
    "    out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;\n"
    "    return out;\n"
    "invalid:\n"
    "    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_ERR;\n"
    "    out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;\n"
    "    return out;\n"
    "overflow:\n"
    "    out.tag = Z_RESULTVAL_I64_PARSEERROR_TAG_ERR;\n"
    "    out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_PARSE_U64 = (
    "static z_resultval_u64_parseerror_t z_StringView_parseU64(const z_StringView_t* self);\n"
    "static z_resultval_u64_parseerror_t z_StringView_parseU64(const z_StringView_t* self) {\n"
    "    z_resultval_u64_parseerror_t out = {0};\n"
    "    if (self->length == 0) {\n"
    "        out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_ERR;\n"
    "        out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;\n"
    "        return out;\n"
    "    }\n"
    "    uint64_t acc = 0;\n"
    "    for (uint64_t i = 0; i < self->length; i++) {\n"
    "        unsigned char c = (unsigned char)self->data[i];\n"
    "        if (c < '0' || c > '9') {\n"
    "            out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_ERR;\n"
    "            out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;\n"
    "            return out;\n"
    "        }\n"
    "        uint64_t d = (uint64_t)(c - '0');\n"
    "        if (acc > (UINT64_MAX - d) / 10) {\n"
    "            out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_ERR;\n"
    "            out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;\n"
    "            return out;\n"
    "        }\n"
    "        acc = acc * 10 + d;\n"
    "    }\n"
    "    out.tag = Z_RESULTVAL_U64_PARSEERROR_TAG_OK;\n"
    "    out.data.ok = acc;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_PARSE_F64 = (
    "static z_resultval_f64_parseerror_t z_StringView_parseF64(const z_StringView_t* self);\n"
    "static z_resultval_f64_parseerror_t z_StringView_parseF64(const z_StringView_t* self) {\n"
    "    z_resultval_f64_parseerror_t out = {0};\n"
    "    if (self->length == 0) {\n"
    "        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;\n"
    "        out.data.err.tag = Z_PARSEERROR_TAG_EMPTY;\n"
    "        return out;\n"
    "    }\n"
    "    /* copy into a NUL-terminated local buffer for strtod */\n"
    "    char buf[64];\n"
    "    char* p = (self->length < sizeof(buf)) ? buf\n"
    "            : (char*)z_xmalloc(self->length + 1);\n"
    "    memcpy(p, self->data, self->length);\n"
    "    p[self->length] = '\\0';\n"
    "    char* end = NULL;\n"
    "    errno = 0;\n"
    "    double v = strtod(p, &end);\n"
    "    int err = errno;\n"
    "    uint64_t consumed = (uint64_t)(end - p);\n"
    "    if (p != buf) free(p);\n"
    "    if (consumed == 0) {\n"
    "        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;\n"
    "        out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;\n"
    "        return out;\n"
    "    }\n"
    "    if (consumed != self->length) {\n"
    "        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;\n"
    "        out.data.err.tag = Z_PARSEERROR_TAG_INVALIDDIGIT;\n"
    "        return out;\n"
    "    }\n"
    "    if (err == ERANGE) {\n"
    "        out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_ERR;\n"
    "        out.data.err.tag = Z_PARSEERROR_TAG_OVERFLOW;\n"
    "        return out;\n"
    "    }\n"
    "    out.tag = Z_RESULTVAL_F64_PARSEERROR_TAG_OK;\n"
    "    out.data.ok = v;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_CONCAT = (
    "static z_String_t z_StringView_concat(\n"
    "    const z_StringView_t* self, const z_StringView_t* other);\n"
    "static z_String_t z_StringView_concat(\n"
    "    const z_StringView_t* self, const z_StringView_t* other) {\n"
    "    z_String_t z = {0};\n"
    "    uint64_t total = self->length + other->length;\n"
    "    z.size = total;\n"
    "    z.capacity = total + 1;\n"
    "    z.data = (char*)z_xmalloc(z.capacity);\n"
    "    memcpy(z.data, self->data, self->length);\n"
    "    memcpy(z.data + self->length, other->data, other->length);\n"
    "    z.data[total] = '\\0';\n"
    "    return z;\n"
    "}\n\n"
)

_Z_SV_LINES_CALL = (
    "static z_Option_StringView_t z_LinesIter_call(z_LinesIter_t* s);\n"
    "static z_Option_StringView_t z_LinesIter_call(z_LinesIter_t* s) {\n"
    "    z_Option_StringView_t out = {0};\n"
    "    if (s->done) {\n"
    "        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    const char* start = s->src + s->cursor;\n"
    "    uint64_t remaining = s->src_len - s->cursor;\n"
    "    uint64_t i = 0;\n"
    "    while (i < remaining && start[i] != '\\n') i++;\n"
    "    uint64_t line_len = i;\n"
    "    if (line_len > 0 && start[line_len - 1] == '\\r') line_len--;\n"
    "    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));\n"
    "    boxed->data = start;\n"
    "    boxed->length = line_len;\n"
    "    if (i == remaining) {\n"
    "        s->cursor = s->src_len;\n"
    "        s->done = true;\n"
    "    } else {\n"
    "        s->cursor += i + 1;\n"
    "        if (s->cursor >= s->src_len) s->done = true;\n"
    "    }\n"
    "    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_SV_STRIP_SUFFIX = (
    "static z_Option_StringView_t z_StringView_stripSuffix(\n"
    "    const z_StringView_t* self, const z_StringView_t* s);\n"
    "static z_Option_StringView_t z_StringView_stripSuffix(\n"
    "    const z_StringView_t* self, const z_StringView_t* s) {\n"
    "    z_Option_StringView_t out = {0};\n"
    "    if (s->length > self->length\n"
    "        || (s->length > 0\n"
    "            && memcmp(self->data + (self->length - s->length),\n"
    "                      s->data, s->length) != 0)) {\n"
    "        out.tag = Z_OPTION_STRINGVIEW_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    z_StringView_t* boxed = (z_StringView_t*)z_xmalloc(sizeof(z_StringView_t));\n"
    "    boxed->data = self->data;\n"
    "    boxed->length = self->length - s->length;\n"
    "    out.tag = Z_OPTION_STRINGVIEW_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)


# Phase S7: string.join free function. Lives alongside the
# stringview natives because it shares the same late emission slot
# (it references z_List_String_t which is a mono type).
_Z_COLL_STRING_JOIN = (
    "/* string_join -- borrow `parts` (read-only) and return a new\n"
    "   owned string. `parts` is pointer-passed (Phase A default for\n"
    "   stack-reftype params); caller retains ownership. */\n"
    "static z_String_t z_stringJoin(\n"
    "    z_List_String_t* parts, z_StringView_t sep);\n"
    "static z_String_t z_stringJoin(\n"
    "    z_List_String_t* parts, z_StringView_t sep) {\n"
    "    z_String_t out = {0};\n"
    "    uint64_t n = parts->length;\n"
    "    if (n == 0) {\n"
    "        out.capacity = 1;\n"
    "        out.data = (char*)z_xmalloc(1);\n"
    "        out.data[0] = '\\0';\n"
    "        return out;\n"
    "    }\n"
    "    uint64_t total = sep.length * (n - 1);\n"
    "    for (uint64_t i = 0; i < n; i++) {\n"
    "        total += parts->data[i].size;\n"
    "    }\n"
    "    out.size = total;\n"
    "    out.capacity = total + 1;\n"
    "    out.data = (char*)z_xmalloc(out.capacity);\n"
    "    uint64_t pos = 0;\n"
    "    for (uint64_t i = 0; i < n; i++) {\n"
    "        if (i > 0 && sep.length > 0) {\n"
    "            memcpy(out.data + pos, sep.data, sep.length);\n"
    "            pos += sep.length;\n"
    "        }\n"
    "        memcpy(out.data + pos, parts->data[i].data, parts->data[i].size);\n"
    "        pos += parts->data[i].size;\n"
    "    }\n"
    "    out.data[total] = '\\0';\n"
    "    return out;\n"
    "}\n\n"
)


def emit_runtime_stringview_natives(
    *,
    needs_stringview: bool,
    natives: "set[str] | None" = None,
) -> str:
    """Emit stringview native function bodies, per-name gated.

    Runs AFTER struct_defs and monomorphization emission so the
    return-wrapper types (`z_optionval_u64_t`, `z_optionval_u8_t`)
    are already declared. Mirrors `emit_runtime_io` /
    `emit_runtime_os` gating.
    """
    if not needs_stringview or not natives:
        return ""
    parts: list[str] = []
    if "isEmpty" in natives:
        parts.append(_Z_SV_IS_EMPTY)
    if "isAscii" in natives:
        parts.append(_Z_SV_IS_ASCII)
    if "startsWith" in natives:
        parts.append(_Z_SV_STARTS_WITH)
    if "endsWith" in natives:
        parts.append(_Z_SV_ENDS_WITH)
    # contains and index_of share the raw helper; emit once.
    if natives & {"contains", "indexOf"}:
        parts.append(_Z_SV_INDEX_OF_RAW)
    if "contains" in natives:
        parts.append(_Z_SV_CONTAINS)
    if "indexOf" in natives:
        parts.append(_Z_SV_INDEX_OF)
    if "lastIndexOf" in natives:
        parts.append(_Z_SV_LAST_INDEX_OF)
    if "byteAt" in natives:
        parts.append(_Z_SV_BYTE_AT)
    # Phase S2: shared ascii-ws helper when any trim variant is used.
    if natives & {"trim", "trimStart", "trimEnd"}:
        parts.append(_Z_SV_IS_ASCII_WS)
    if "trim" in natives:
        parts.append(_Z_SV_TRIM)
    if "trimStart" in natives:
        parts.append(_Z_SV_TRIM_START)
    if "trimEnd" in natives:
        parts.append(_Z_SV_TRIM_END)
    if "stripPrefix" in natives:
        parts.append(_Z_SV_STRIP_PREFIX)
    if "stripSuffix" in natives:
        parts.append(_Z_SV_STRIP_SUFFIX)
    # Phase S3: iterators. splitter / linesiter share one impl struct.
    if natives & {"split", "lines"}:
        parts.append(_Z_SV_SPLITTER_STRUCT)
    if "split" in natives:
        parts.append(_Z_SV_SPLIT)
        parts.append(_Z_SV_SPLITTER_CALL)
    if "splitOnce" in natives:
        parts.append(_Z_SV_SPLIT_ONCE)
    if "lines" in natives:
        parts.append(_Z_SV_LINES)
        parts.append(_Z_SV_LINES_CALL)
    # Phase S4: allocating transforms.
    if "toLowerAscii" in natives:
        parts.append(_Z_SV_TO_LOWER_ASCII)
    if "toUpperAscii" in natives:
        parts.append(_Z_SV_TO_UPPER_ASCII)
    if natives & {"replace", "replaceFirst"}:
        parts.append(_Z_SV_REPLACE_IMPL)
    if "replace" in natives:
        parts.append(_Z_SV_REPLACE)
    if "replaceFirst" in natives:
        parts.append(_Z_SV_REPLACE_FIRST)
    if "repeated" in natives:
        parts.append(_Z_SV_REPEAT)
    if "concat" in natives:
        parts.append(_Z_SV_CONCAT)
    # Phase S5: codepoint iteration + count.
    if natives & {"count", "codepoints"}:
        parts.append(_Z_SV_UTF8_DECODE)
    if "codepoints" in natives:
        parts.append(_Z_SV_CPITER_STRUCT)
    if "count" in natives:
        parts.append(_Z_SV_COUNT)
    if "codepoints" in natives:
        parts.append(_Z_SV_CODEPOINTS)
        parts.append(_Z_SV_CPITER_CALL)
    # Phase S6: numeric parsing.
    if "parseI64" in natives:
        parts.append(_Z_SV_PARSE_I64)
    if "parseU64" in natives:
        parts.append(_Z_SV_PARSE_U64)
    if "parseF64" in natives:
        parts.append(_Z_SV_PARSE_F64)
    # Phase S7: string.join free function.
    if "join" in natives:
        parts.append(_Z_COLL_STRING_JOIN)
    return "".join(parts)


# -- cli unit natives ---------------------------------------------
# Emitted in the same late slot as stringview natives so the types
# they reference (z_Spec_t, z_Parsed_t, z_List_<def>_t,
# z_Result_Parsed_CliError_t) are already declared.

_Z_CLI_SPEC_CREATE = (
    "/* `programName` and `summary` are .take params: caller transfers\n"
    "   ownership of the string buffers; the spec stores them directly\n"
    "   (no copy). Caller-side implicit-take zeros the source vars. */\n"
    "static z_Spec_t z_Spec_create(z_String_t programName, z_String_t summary);\n"
    "static z_Spec_t z_Spec_create(z_String_t programName, z_String_t summary) {\n"
    "    z_Spec_t s = {0};\n"
    "    s.programName = programName;\n"
    "    s.summary = summary;\n"
    "    s.flags = z_List_FlagDef_create(0);\n"
    "    s.options = z_List_OptionDef_create(0);\n"
    "    s.positionals = z_List_PositionalDef_create(0);\n"
    "    return s;\n"
    "}\n\n"
)

_Z_CLI_ADD_FLAG = (
    "/* name/shortName/help are .take: stored directly in the flagdef. */\n"
    "static void z_cli_addFlag(z_Spec_t* spec,\n"
    "    z_String_t name, z_String_t shortName, z_String_t help);\n"
    "static void z_cli_addFlag(z_Spec_t* spec,\n"
    "    z_String_t name, z_String_t shortName, z_String_t help) {\n"
    "    z_FlagDef_t f = {0};\n"
    "    f.name = name;\n"
    "    f.shortName = shortName;\n"
    "    f.help = help;\n"
    "    z_List_FlagDef_append(&spec->flags, f);\n"
    "}\n\n"
)

_Z_CLI_ADD_OPTION = (
    "/* name/shortName/help are .take: stored directly in the optiondef. */\n"
    "static void z_cli_addOption(z_Spec_t* spec,\n"
    "    z_String_t name, z_String_t shortName, z_String_t help, bool required);\n"
    "static void z_cli_addOption(z_Spec_t* spec,\n"
    "    z_String_t name, z_String_t shortName, z_String_t help, bool required) {\n"
    "    z_OptionDef_t o = {0};\n"
    "    o.name = name;\n"
    "    o.shortName = shortName;\n"
    "    o.help = help;\n"
    "    o.required = required;\n"
    "    z_List_OptionDef_append(&spec->options, o);\n"
    "}\n\n"
)

_Z_CLI_ADD_POSITIONAL = (
    "/* name/help are .take: stored directly in the positionaldef. */\n"
    "static void z_cli_addPositional(z_Spec_t* spec,\n"
    "    z_String_t name, z_String_t help, bool required);\n"
    "static void z_cli_addPositional(z_Spec_t* spec,\n"
    "    z_String_t name, z_String_t help, bool required) {\n"
    "    z_PositionalDef_t p = {0};\n"
    "    p.name = name;\n"
    "    p.help = help;\n"
    "    p.required = required;\n"
    "    z_List_PositionalDef_append(&spec->positionals, p);\n"
    "}\n\n"
)

# Helpers: byte-compare a z_String_t to a C string, and locate a
# registered flag/option by its long or short name.
_Z_CLI_HELPERS = (
    "/* byte-compare z_String_t vs C string + length */\n"
    "static bool z_cli_str_eq(const z_String_t* a, const char* b, uint64_t blen);\n"
    "static bool z_cli_str_eq(const z_String_t* a, const char* b, uint64_t blen) {\n"
    "    if (a->size != blen) return false;\n"
    "    return memcmp(a->data, b, blen) == 0;\n"
    "}\n\n"
    "/* find flag index by long name */\n"
    "static int64_t z_cli_find_flag_long(const z_List_FlagDef_t* flags, const char* name, uint64_t nlen);\n"
    "static int64_t z_cli_find_flag_long(const z_List_FlagDef_t* flags, const char* name, uint64_t nlen) {\n"
    "    for (uint64_t i = 0; i < flags->length; i++) {\n"
    "        if (z_cli_str_eq(&flags->data[i].name, name, nlen)) return (int64_t)i;\n"
    "    }\n"
    "    return -1;\n"
    "}\n\n"
    "/* find flag index by short name (full short incl. `-`) */\n"
    "static int64_t z_cli_find_flag_short(const z_List_FlagDef_t* flags, const char* name, uint64_t nlen);\n"
    "static int64_t z_cli_find_flag_short(const z_List_FlagDef_t* flags, const char* name, uint64_t nlen) {\n"
    "    for (uint64_t i = 0; i < flags->length; i++) {\n"
    "        if (z_cli_str_eq(&flags->data[i].shortName, name, nlen)) return (int64_t)i;\n"
    "    }\n"
    "    return -1;\n"
    "}\n\n"
    "static int64_t z_cli_findOption_long(const z_List_OptionDef_t* options, const char* name, uint64_t nlen);\n"
    "static int64_t z_cli_findOption_long(const z_List_OptionDef_t* options, const char* name, uint64_t nlen) {\n"
    "    for (uint64_t i = 0; i < options->length; i++) {\n"
    "        if (z_cli_str_eq(&options->data[i].name, name, nlen)) return (int64_t)i;\n"
    "    }\n"
    "    return -1;\n"
    "}\n\n"
    "static int64_t z_cli_findOption_short(const z_List_OptionDef_t* options, const char* name, uint64_t nlen);\n"
    "static int64_t z_cli_findOption_short(const z_List_OptionDef_t* options, const char* name, uint64_t nlen) {\n"
    "    for (uint64_t i = 0; i < options->length; i++) {\n"
    "        if (z_cli_str_eq(&options->data[i].shortName, name, nlen)) return (int64_t)i;\n"
    "    }\n"
    "    return -1;\n"
    "}\n\n"
    "/* build a clierror union with a string payload */\n"
    "static z_CliError_t z_cli_err(z_CliError_tag_t tag, const char* msg, uint64_t mlen);\n"
    "static z_CliError_t z_cli_err(z_CliError_tag_t tag, const char* msg, uint64_t mlen) {\n"
    "    z_CliError_t e = {0};\n"
    "    e.tag = tag;\n"
    "    z_String_t* s = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *s = z_String_create(mlen);\n"
    "    z_String_append(s, msg, mlen);\n"
    "    e.data = s;\n"
    "    return e;\n"
    "}\n\n"
    "/* wrap clierror in result.err */\n"
    "static z_Result_Parsed_CliError_t z_cli_wrap_err(z_CliError_t e);\n"
    "static z_Result_Parsed_CliError_t z_cli_wrap_err(z_CliError_t e) {\n"
    "    z_Result_Parsed_CliError_t r = {0};\n"
    "    z_CliError_t* boxed = (z_CliError_t*)z_xmalloc(sizeof(z_CliError_t));\n"
    "    *boxed = e;\n"
    "    r.tag = Z_RESULT_PARSED_CLIERROR_TAG_ERR;\n"
    "    r.data = boxed;\n"
    "    return r;\n"
    "}\n\n"
)

_Z_CLI_PARSE = (
    "static z_Result_Parsed_CliError_t z_cli_parse(z_Spec_t* spec, z_List_String_t args);\n"
    "static z_Result_Parsed_CliError_t z_cli_parse(z_Spec_t* spec, z_List_String_t args) {\n"
    "    z_Parsed_t* parsed = (z_Parsed_t*)z_xmalloc(sizeof(z_Parsed_t));\n"
    "    parsed->flagSet = z_List_String_create(0);\n"
    "    parsed->optionValues = z_Map_String_String_create(0);\n"
    "    parsed->positionalValues = z_Map_String_String_create(0);\n"
    "    parsed->extraArgs = z_List_String_create(0);\n"
    "    uint64_t next_pos = 0;\n"
    "    uint64_t i = 1;  /* skip argv[0] */\n"
    "    while (i < args.length) {\n"
    "        z_String_t* a = &args.data[i];\n"
    "        /* `--` alone terminates option parsing */\n"
    "        if (a->size == 2 && a->data[0] == '-' && a->data[1] == '-') {\n"
    "            for (uint64_t j = i + 1; j < args.length; j++) {\n"
    "                z_List_String_append(\n"
    "                    &parsed->extraArgs,\n"
    "                    z_String_new(args.data[j].data)\n"
    "                );\n"
    "            }\n"
    "            break;\n"
    "        }\n"
    "        /* long form `--name[=value]` */\n"
    "        if (a->size >= 3 && a->data[0] == '-' && a->data[1] == '-') {\n"
    "            /* find `=` within the arg */\n"
    "            const char* eq = (const char*)memchr(a->data, '=', a->size);\n"
    "            uint64_t name_len = eq ? (uint64_t)(eq - a->data) : a->size;\n"
    "            int64_t fi = z_cli_find_flag_long(&spec->flags, a->data, name_len);\n"
    "            if (fi >= 0) {\n"
    "                if (eq) {\n"
    "                    z_Parsed_destroy(parsed); free(parsed); z_List_String_destroy(&args);\n"
    "                    return z_cli_wrap_err(\n"
    "                        z_cli_err(Z_CLIERROR_TAG_UNEXPECTEDARG,\n"
    "                                  a->data, a->size));\n"
    "                }\n"
    "                z_List_String_append(\n"
    "                    &parsed->flagSet,\n"
    "                    z_String_new(spec->flags.data[fi].name.data)\n"
    "                );\n"
    "                i++;\n"
    "                continue;\n"
    "            }\n"
    "            int64_t oi = z_cli_findOption_long(\n"
    "                &spec->options, a->data, name_len);\n"
    "            if (oi >= 0) {\n"
    "                z_String_t key = z_String_new(spec->options.data[oi].name.data);\n"
    "                z_String_t val = {0};\n"
    "                if (eq) {\n"
    "                    uint64_t vlen = a->size - (name_len + 1);\n"
    "                    val = z_String_create(vlen);\n"
    "                    z_String_append(&val, eq + 1, vlen);\n"
    "                } else {\n"
    "                    if (i + 1 >= args.length) {\n"
    "                        z_String_free(&key);\n"
    "                        z_Parsed_destroy(parsed); free(parsed); z_List_String_destroy(&args);\n"
    "                        return z_cli_wrap_err(\n"
    "                            z_cli_err(Z_CLIERROR_TAG_MISSINGVALUE,\n"
    "                                      a->data, a->size));\n"
    "                    }\n"
    "                    val = z_String_new(args.data[i + 1].data);\n"
    "                    i++;\n"
    "                }\n"
    "                z_Map_String_String_set(\n"
    "                    parsed->optionValues, key, val);\n"
    "                i++;\n"
    "                continue;\n"
    "            }\n"
    "            z_Parsed_destroy(parsed); free(parsed); z_List_String_destroy(&args);\n"
    "            return z_cli_wrap_err(\n"
    "                z_cli_err(Z_CLIERROR_TAG_UNKNOWNFLAG,\n"
    "                          a->data, a->size));\n"
    "        }\n"
    "        /* short form `-x[yz...]` */\n"
    "        if (a->size >= 2 && a->data[0] == '-') {\n"
    "            /* full-token match first (e.g. -v, -o with separate value) */\n"
    "            int64_t fi = z_cli_find_flag_short(&spec->flags, a->data, a->size);\n"
    "            if (fi >= 0) {\n"
    "                z_List_String_append(\n"
    "                    &parsed->flagSet,\n"
    "                    z_String_new(spec->flags.data[fi].name.data)\n"
    "                );\n"
    "                i++;\n"
    "                continue;\n"
    "            }\n"
    "            int64_t oi = z_cli_findOption_short(\n"
    "                &spec->options, a->data, a->size);\n"
    "            if (oi >= 0) {\n"
    "                if (i + 1 >= args.length) {\n"
    "                    z_Parsed_destroy(parsed); free(parsed); z_List_String_destroy(&args);\n"
    "                    return z_cli_wrap_err(\n"
    "                        z_cli_err(Z_CLIERROR_TAG_MISSINGVALUE,\n"
    "                                  a->data, a->size));\n"
    "                }\n"
    "                z_String_t key = z_String_new(spec->options.data[oi].name.data);\n"
    "                z_String_t val = z_String_new(args.data[i + 1].data);\n"
    "                z_Map_String_String_set(\n"
    "                    parsed->optionValues, key, val);\n"
    "                i += 2;\n"
    "                continue;\n"
    "            }\n"
    "            /* bundled short flags: every char after `-` must be\n"
    "               a short flag. A short option terminates and takes\n"
    "               the rest as its value. */\n"
    "            if (a->size > 2) {\n"
    "                bool ok = true;\n"
    "                for (uint64_t k = 1; k < a->size && ok; k++) {\n"
    "                    char buf[2] = { '-', a->data[k] };\n"
    "                    int64_t bfi = z_cli_find_flag_short(&spec->flags, buf, 2);\n"
    "                    if (bfi >= 0) {\n"
    "                        z_List_String_append(\n"
    "                            &parsed->flagSet,\n"
    "                            z_String_new(spec->flags.data[bfi].name.data)\n"
    "                        );\n"
    "                        continue;\n"
    "                    }\n"
    "                    int64_t boi = z_cli_findOption_short(\n"
    "                        &spec->options, buf, 2);\n"
    "                    if (boi >= 0) {\n"
    "                        uint64_t vlen = a->size - (k + 1);\n"
    "                        z_String_t key = z_String_new(\n"
    "                            spec->options.data[boi].name.data);\n"
    "                        z_String_t val = z_String_create(vlen);\n"
    "                        if (vlen > 0) z_String_append(&val, a->data + k + 1, vlen);\n"
    "                        z_Map_String_String_set(\n"
    "                            parsed->optionValues, key, val);\n"
    "                        /* consume rest of arg as value */\n"
    "                        break;\n"
    "                    }\n"
    "                    ok = false;\n"
    "                }\n"
    "                if (!ok) {\n"
    "                    z_Parsed_destroy(parsed); free(parsed); z_List_String_destroy(&args);\n"
    "                    return z_cli_wrap_err(\n"
    "                        z_cli_err(Z_CLIERROR_TAG_UNKNOWNFLAG,\n"
    "                                  a->data, a->size));\n"
    "                }\n"
    "                i++;\n"
    "                continue;\n"
    "            }\n"
    "            /* `-` alone or unrecognised short: fall through to positional */\n"
    "        }\n"
    "        /* positional */\n"
    "        if (next_pos < spec->positionals.length) {\n"
    "            z_String_t key = z_String_new(\n"
    "                spec->positionals.data[next_pos].name.data);\n"
    "            z_String_t val = z_String_new(a->data);\n"
    "            z_Map_String_String_set(\n"
    "                parsed->positionalValues, key, val);\n"
    "            next_pos++;\n"
    "        } else {\n"
    "            z_List_String_append(\n"
    "                &parsed->extraArgs, z_String_new(a->data));\n"
    "        }\n"
    "        i++;\n"
    "    }\n"
    "    /* parse loop finished reading — args copies have been\n"
    "       materialised via z_String_new wherever needed. Free the\n"
    "       incoming list now so every exit path (ok or err) is\n"
    "       leak-free. */\n"
    "    z_List_String_destroy(&args);\n"
    "    /* required checks (v.data is a shallow-copied box; free\n"
    "       the outer pointer without touching the aliased string). */\n"
    "    for (uint64_t k = 0; k < spec->options.length; k++) {\n"
    "        if (!spec->options.data[k].required) continue;\n"
    "        z_Option_String_t v = z_Map_String_String_get(\n"
    "            parsed->optionValues, spec->options.data[k].name);\n"
    "        bool missing = (v.tag == Z_OPTION_STRING_TAG_NONE);\n"
    "        if (v.data) free(v.data);\n"
    "        if (missing) {\n"
    "            z_Parsed_destroy(parsed); free(parsed);\n"
    "            return z_cli_wrap_err(\n"
    "                z_cli_err(Z_CLIERROR_TAG_MISSINGREQUIRED,\n"
    "                          spec->options.data[k].name.data,\n"
    "                          spec->options.data[k].name.size));\n"
    "        }\n"
    "    }\n"
    "    for (uint64_t k = 0; k < spec->positionals.length; k++) {\n"
    "        if (!spec->positionals.data[k].required) continue;\n"
    "        z_Option_String_t v = z_Map_String_String_get(\n"
    "            parsed->positionalValues, spec->positionals.data[k].name);\n"
    "        bool missing = (v.tag == Z_OPTION_STRING_TAG_NONE);\n"
    "        if (v.data) free(v.data);\n"
    "        if (missing) {\n"
    "            z_Parsed_destroy(parsed); free(parsed);\n"
    "            return z_cli_wrap_err(\n"
    "                z_cli_err(Z_CLIERROR_TAG_MISSINGREQUIRED,\n"
    "                          spec->positionals.data[k].name.data,\n"
    "                          spec->positionals.data[k].name.size));\n"
    "        }\n"
    "    }\n"
    "    z_Result_Parsed_CliError_t r = {0};\n"
    "    r.tag = Z_RESULT_PARSED_CLIERROR_TAG_OK;\n"
    "    r.data = parsed;\n"
    "    return r;\n"
    "}\n\n"
)

_Z_CLI_HELP_TEXT = (
    "static z_String_t z_cli_helpText(z_Spec_t* spec);\n"
    "static z_String_t z_cli_helpText(z_Spec_t* spec) {\n"
    "    z_String_t out = z_String_create(128);\n"
    '    z_String_append(&out, "usage: ", 7);\n'
    "    z_String_append(&out, spec->programName.data, spec->programName.size);\n"
    '    z_String_append(&out, " [options]", 10);\n'
    "    for (uint64_t i = 0; i < spec->positionals.length; i++) {\n"
    '        z_String_append(&out, " <", 2);\n'
    "        z_String_append(&out, spec->positionals.data[i].name.data,\n"
    "                        spec->positionals.data[i].name.size);\n"
    '        z_String_append(&out, ">", 1);\n'
    "    }\n"
    "    if (spec->summary.size > 0) {\n"
    '        z_String_append(&out, "\\n\\n", 2);\n'
    "        z_String_append(&out, spec->summary.data, spec->summary.size);\n"
    "    }\n"
    "    if (spec->flags.length > 0) {\n"
    '        z_String_append(&out, "\\n\\nFlags:", 9);\n'
    "        for (uint64_t i = 0; i < spec->flags.length; i++) {\n"
    "            z_FlagDef_t* f = &spec->flags.data[i];\n"
    '            z_String_append(&out, "\\n  ", 3);\n'
    "            if (f->shortName.size > 0) {\n"
    "                z_String_append(&out, f->shortName.data, f->shortName.size);\n"
    '                z_String_append(&out, ", ", 2);\n'
    "            }\n"
    "            z_String_append(&out, f->name.data, f->name.size);\n"
    "            if (f->help.size > 0) {\n"
    '                z_String_append(&out, "    ", 4);\n'
    "                z_String_append(&out, f->help.data, f->help.size);\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    if (spec->options.length > 0) {\n"
    '        z_String_append(&out, "\\n\\nOptions:", 11);\n'
    "        for (uint64_t i = 0; i < spec->options.length; i++) {\n"
    "            z_OptionDef_t* o = &spec->options.data[i];\n"
    '            z_String_append(&out, "\\n  ", 3);\n'
    "            if (o->shortName.size > 0) {\n"
    "                z_String_append(&out, o->shortName.data, o->shortName.size);\n"
    '                z_String_append(&out, ", ", 2);\n'
    "            }\n"
    "            z_String_append(&out, o->name.data, o->name.size);\n"
    "            if (o->help.size > 0) {\n"
    '                z_String_append(&out, "    ", 4);\n'
    "                z_String_append(&out, o->help.data, o->help.size);\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    if (spec->positionals.length > 0) {\n"
    '        z_String_append(&out, "\\n\\nPositionals:", 15);\n'
    "        for (uint64_t i = 0; i < spec->positionals.length; i++) {\n"
    "            z_PositionalDef_t* p = &spec->positionals.data[i];\n"
    '            z_String_append(&out, "\\n  ", 3);\n'
    "            z_String_append(&out, p->name.data, p->name.size);\n"
    "            if (p->help.size > 0) {\n"
    '                z_String_append(&out, "    ", 4);\n'
    "                z_String_append(&out, p->help.data, p->help.size);\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    return out;\n"
    "}\n\n"
)

_Z_PARSED_HAS_FLAG = (
    "static bool z_Parsed_hasFlag(z_Parsed_t* self, const z_StringView_t* name);\n"
    "static bool z_Parsed_hasFlag(z_Parsed_t* self, const z_StringView_t* name) {\n"
    "    for (uint64_t i = 0; i < self->flagSet.length; i++) {\n"
    "        z_String_t* s = &self->flagSet.data[i];\n"
    "        if (s->size == name->length\n"
    "            && memcmp(s->data, name->data, name->length) == 0) {\n"
    "            return true;\n"
    "        }\n"
    "    }\n"
    "    return false;\n"
    "}\n\n"
)

_Z_PARSED_GET_OPTION = (
    "static z_Option_String_t z_Parsed_option(z_Parsed_t* self, const z_StringView_t* name);\n"
    "static z_Option_String_t z_Parsed_option(z_Parsed_t* self, const z_StringView_t* name) {\n"
    "    z_Option_String_t out = {0};\n"
    "    z_String_t key = z_String_create(name->length);\n"
    "    z_String_append(&key, name->data, name->length);\n"
    "    /* map.get returns a shallow-copied box whose inner\n"
    "       z_String_t aliases the map's own heap buffer. Free just\n"
    "       the outer box; the map retains ownership of the bytes. */\n"
    "    z_Option_String_t v = z_Map_String_String_get(\n"
    "        self->optionValues, key);\n"
    "    z_String_free(&key);\n"
    "    if (v.tag == Z_OPTION_STRING_TAG_NONE) {\n"
    "        if (v.data) free(v.data);\n"
    "        out.tag = Z_OPTION_STRING_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *boxed = z_String_new(((z_String_t*)v.data)->data);\n"
    "    free(v.data);\n"
    "    out.tag = Z_OPTION_STRING_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)

_Z_PARSED_GET_POSITIONAL = (
    "static z_Option_String_t z_Parsed_positional(z_Parsed_t* self, const z_StringView_t* name);\n"
    "static z_Option_String_t z_Parsed_positional(z_Parsed_t* self, const z_StringView_t* name) {\n"
    "    z_Option_String_t out = {0};\n"
    "    z_String_t key = z_String_create(name->length);\n"
    "    z_String_append(&key, name->data, name->length);\n"
    "    z_Option_String_t v = z_Map_String_String_get(\n"
    "        self->positionalValues, key);\n"
    "    z_String_free(&key);\n"
    "    if (v.tag == Z_OPTION_STRING_TAG_NONE) {\n"
    "        if (v.data) free(v.data);\n"
    "        out.tag = Z_OPTION_STRING_TAG_NONE;\n"
    "        return out;\n"
    "    }\n"
    "    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
    "    *boxed = z_String_new(((z_String_t*)v.data)->data);\n"
    "    free(v.data);\n"
    "    out.tag = Z_OPTION_STRING_TAG_SOME;\n"
    "    out.data = boxed;\n"
    "    return out;\n"
    "}\n\n"
)


def emit_runtime_cli_natives(
    *, needs_cli: bool, natives: "set[str] | None" = None
) -> str:
    """Emit cli-unit native function bodies, per-name gated."""
    if not needs_cli or not natives:
        return ""
    parts: list[str] = []
    if "spec_create" in natives:
        parts.append(_Z_CLI_SPEC_CREATE)
    if "addFlag" in natives:
        parts.append(_Z_CLI_ADD_FLAG)
    if "addOption" in natives:
        parts.append(_Z_CLI_ADD_OPTION)
    if "addPositional" in natives:
        parts.append(_Z_CLI_ADD_POSITIONAL)
    if "parse" in natives or "helpText" in natives:
        parts.append(_Z_CLI_HELPERS)
    if "parse" in natives:
        parts.append(_Z_CLI_PARSE)
    if "helpText" in natives:
        parts.append(_Z_CLI_HELP_TEXT)
    if "hasFlag" in natives:
        parts.append(_Z_PARSED_HAS_FLAG)
    if "option" in natives:
        parts.append(_Z_PARSED_GET_OPTION)
    if "positional" in natives:
        parts.append(_Z_PARSED_GET_POSITIONAL)
    return "".join(parts)


def emit_runtime_os(*, needs_os: bool, natives: "set[str] | None" = None) -> str:
    """Emit os-unit native function implementations per requested name.

    Mirrors emit_runtime_io's per-name gating so each program only
    pays for the natives it actually calls. `args` additionally pulls
    in two file-scope globals that main() populates from argc/argv.
    """
    if not needs_os or not natives:
        return ""
    parts: list[str] = []
    if "args" in natives:
        parts.append(_Z_OS_ARGV_GLOBALS)
        parts.append(_Z_OS_ARGS)
    if "env" in natives:
        parts.append(_Z_OS_GET_ENV)
    if "setEnv" in natives:
        parts.append(_Z_OS_SET_ENV)
    if "unsetEnv" in natives:
        parts.append(_Z_OS_UNSET_ENV)
    if "envNames" in natives:
        parts.append(_Z_OS_ENV_NAMES)
    if "cwd" in natives:
        parts.append(_Z_OS_CWD)
    if "setCwd" in natives:
        parts.append(_Z_OS_SET_CWD)
    if "pid" in natives:
        parts.append(_Z_OS_PID)
    if "ppid" in natives:
        parts.append(_Z_OS_PPID)
    if "userName" in natives:
        parts.append(_Z_OS_USER_NAME)
    if "homeDir" in natives:
        parts.append(_Z_OS_HOME_DIR)
    if "platform" in natives:
        parts.append(_Z_OS_PLATFORM)
    if "arch" in natives:
        parts.append(_Z_OS_ARCH)
    if "hostname" in natives:
        parts.append(_Z_OS_HOSTNAME)
    if "exit" in natives:
        parts.append(_Z_OS_EXIT)
    return "".join(parts)


def emit_runtime_io(
    *,
    needs_io: bool,
    natives: "set[str] | None" = None,
    os_natives: "set[str] | None" = None,
) -> str:
    """Emit io-unit native function implementations per requested name.

    `natives` is the set of io-native function names the program
    actually calls (e.g. `{"eprintln", "readText"}`). Per-name
    granularity so unused natives never pull in the
    compiler-generated types they would reference.

    `os_natives` is consulted only for the shared errno-mapping
    helper: os.set_env / os.unset_env surface the same `io.ioerror`
    domain and need `z_io_errno_to_IoError` even when no io native
    is used directly.
    """
    os_natives = os_natives or set()
    os_needs_errno = bool(
        os_natives
        & {
            "setEnv",
            "unsetEnv",
            "cwd",
            "setCwd",
            "userName",
            "homeDir",
            "hostname",
        }
    )
    # os natives whose stringview args need the shared `z_sv_to_cstr`
    # helper. Independent of errno_map so a get_env-only program still
    # gets the helper without pulling in the errno table.
    os_needs_sv_cstr = bool(os_natives & {"env", "setEnv", "unsetEnv", "setCwd"})
    if not needs_io:
        return ""
    if not natives and not os_needs_errno and not os_needs_sv_cstr:
        return ""
    natives = natives or set()
    parts: list[str] = []
    # errno mapping is shared; include if any fallible native is used
    fallible = natives & {
        "readText",
        "writeText",
        "appendText",
        "mkdir",
        "mkdirp",
        "remove",
        "rename",
        "stat",
        "lstat",
        "listDir",
        "open",
        "file_close",
        "file_read",
        "file_write",
        "file_seek",
        "readlink",
        "symlink",
    }
    if os_needs_errno:
        fallible = fallible | {"_os_set_env"}  # non-empty sentinel
    # result(null, ioerror) wrapper used by mkdir / mkdirp / remove /
    # rename / file_close / symlink
    null_wrap = natives & {
        "mkdir",
        "mkdirp",
        "remove",
        "rename",
        "file_close",
        "symlink",
    }
    # result(u64, ioerror) wrapper used by file_read / file_write /
    # file_seek
    u64_wrap = natives & {
        "file_read",
        "file_write",
        "file_seek",
        "bufwriter_write",
        "textwriter_write_line",
        "bufreader_read",
    }
    if "eprintln" in natives:
        parts.append(_Z_IO_EPRINTLN)
    if fallible:
        parts.append(_Z_IO_ERRNO_MAP)
    # z_sv_to_cstr is shared by every native that takes a `stringview`
    # path/key/value and hands it to a C API expecting a NUL-terminated
    # C string. Emit if any such native is in scope.
    sv_cstr_users = natives & {
        "readText",
        "writeText",
        "appendText",
        "exists",
        "mkdir",
        "mkdirp",
        "remove",
        "rename",
        "stat",
        "lstat",
        "readlink",
        "symlink",
        "listDir",
        "open",
    }
    # os.set_env / unset_env / set_cwd / get_env also use it, signalled via os_natives.
    if not sv_cstr_users and (os_natives & {"env", "setEnv", "unsetEnv", "setCwd"}):
        sv_cstr_users = {"_os_sv_cstr"}  # non-empty sentinel
    if sv_cstr_users:
        parts.append(_Z_SV_TO_CSTR)
    if null_wrap:
        parts.append(_Z_IO_WRAP_NULL_RESULT)
    if "readText" in natives:
        parts.append(_Z_IO_READ_TEXT)
    if natives & {"writeText", "appendText"}:
        parts.append(_Z_IO_WRITE_COMMON)
    if "writeText" in natives:
        parts.append(_Z_IO_WRITE_TEXT)
    if "appendText" in natives:
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
    if "readlink" in natives:
        parts.append(_Z_IO_READLINK)
    if "symlink" in natives:
        parts.append(_Z_IO_SYMLINK)
    if natives & {"stat", "lstat"}:
        parts.append(_Z_IO_STAT_FILL)
    if "stat" in natives:
        parts.append(_Z_IO_STAT)
    if "lstat" in natives:
        parts.append(_Z_IO_LSTAT)
    if "listDir" in natives:
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
    # textreader.call wraps read_line -- must land after the body it
    # delegates to.
    if "textreader_call" in natives:
        parts.append(_Z_TEXTREADER_CALL)
    return "".join(parts)


def emit_runtime(
    *,
    needs_stdio: bool,
    needs_stdint: bool,
    needs_stdlib: bool,
    needs_string: bool,
    needs_stringview: bool = False,
    needs_io: bool = False,
    needs_pwd: bool = False,
) -> str:
    """Return all runtime support code (includes + types + helper functions)."""
    # z_String_t runtime uses malloc/free (stdlib.h) and strlen/memcpy
    # (string.h). <stdbool.h> is always included (see emit_runtime_includes).
    has_z_string = needs_string or needs_stdio
    return (
        emit_runtime_includes(
            needs_stdio=needs_stdio or needs_io,
            needs_stdint=needs_stdint,
            needs_stdlib=needs_stdlib or has_z_string or needs_stringview,
            needs_string=needs_string or has_z_string or needs_stringview,
            needs_io=needs_io,
            needs_pwd=needs_pwd,
        )
        + emit_runtime_z_string(needs_string=needs_string, needs_stdio=needs_stdio)
        + emit_runtime_z_stringview(needs_stringview=needs_stringview)
        # io helpers are NOT emitted here — they reference compiler-generated
        # struct names (z_IoError_t, z_Result_String_IoError_t, ...) that
        # only exist after struct_defs. The emitter calls emit_runtime_io
        # separately, after the struct definitions block.
    )


def emit_static_stringviews(string_literals: dict[str, str]) -> str:
    """Emit per-program static stringview constants.

    Each literal gets a static const char array and a z_StringView_t constant
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
            f"static const z_StringView_t {sname} = {{ {dname}, {byte_len} }};\n"
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
    """Emit a bounds-check that routes through z_panic on failure."""
    lines.append(f"    if ({idx_expr} >= {len_expr}) {{\n")
    lines.append("        char _zp_buf[96];\n")
    lines.append(
        f'        snprintf(_zp_buf, sizeof(_zp_buf), "{label}: index {idx_fmt}'
        f' out of bounds (length {idx_fmt})",'
        f" {idx_cast}{idx_expr}, {idx_cast}{len_expr});\n"
    )
    lines.append("        z_panic(_zp_buf);\n")
    lines.append("    }\n")


def emit_array_bounds_check(
    lines: List[str],
    idx_expr: str,
    arr_len: int,
    label: str,
) -> None:
    """Emit a signed-index bounds-check for fixed-length arrays via z_panic."""
    lines.append(f"    if ({idx_expr} < 0 || {idx_expr} >= {arr_len}) {{\n")
    lines.append("        char _zp_buf[96];\n")
    lines.append(
        f'        snprintf(_zp_buf, sizeof(_zp_buf), "{label}: index %ld'
        f' out of bounds (length {arr_len})", (long){idx_expr});\n'
    )
    lines.append("        z_panic(_zp_buf);\n")
    lines.append("    }\n")


def emit_empty_check(
    lines: List[str],
    label: str,
) -> None:
    """Emit an empty-container check that panics on failure (e.g. list pop)."""
    lines.append("    if (_this->length == 0) {\n")
    lines.append(f'        z_panic("{label}");\n')
    lines.append("    }\n")
