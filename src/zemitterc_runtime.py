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


_Z_IO_RUNTIME = (
    "static void z_io_eprintln(z_stringview_t sv) {\n"
    '    fprintf(stderr, "%.*s\\n", (int)sv.length, sv.data);\n'
    "}\n\n"
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


def emit_runtime_io(*, needs_io: bool) -> str:
    """Emit io-unit native function implementations (stderr / file helpers).

    Depends on z_stringview_t (needs_stringview must also be set by the
    caller for the stringview struct to be in scope). Conditional so
    programs that do not use io pay no bloat.
    """
    if needs_io:
        return _Z_IO_RUNTIME
    return ""


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
