"""Runtime C code generation for the zerolang compiler.

Generates the runtime support code (string handling, error helpers) that
is inlined into every compiled .c file.

Future: extract output to runtime/ directory as zrt_string.c,
zrt_error.c, etc. and compile into libzrt.a.
"""

from typing import Dict, List


def emit_runtime_includes(
    *,
    needs_stdio: bool,
    needs_stdint: bool,
    needs_stdlib: bool,
    needs_stdbool: bool,
    needs_string: bool,
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
    if parts:
        parts.append("\n")
    return "".join(parts)


_ZSTR_RUNTIME = (
    "typedef struct {\n"
    "    uint64_t size;     /* bits 62-0: byte count; bit 63: static flag */\n"
    "    char data[];       /* NUL-terminated, starts at 8-byte boundary */\n"
    "} ZStr;\n\n"
    "#define ZSTR_STATIC_FLAG  0x8000000000000000ull\n"
    "#define ZSTR_SIZE(z)      ((z)->size & ~ZSTR_STATIC_FLAG)\n"
    "#define ZSTR_IS_STATIC(z) ((z)->size & ZSTR_STATIC_FLAG)\n\n"
    "#define ZSTR_STATIC(name, str) \\\n"
    "    static struct { uint64_t size; char data[sizeof(str)]; } \\\n"
    "    name##_storage = { (sizeof(str)-1) | ZSTR_STATIC_FLAG, str }; \\\n"
    "    static ZStr* name = (ZStr*)&name##_storage\n\n"
    "static ZStr* zstr_new(const char* s) {\n"
    "    uint64_t size = (uint64_t)strlen(s);\n"
    "    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + size + 1);\n"
    "    z->size = size;\n"
    "    memcpy(z->data, s, size + 1);\n"
    "    return z;\n"
    "}\n\n"
    "static ZStr* zstr_cat(ZStr* a, ZStr* b) {\n"
    "    uint64_t size = ZSTR_SIZE(a) + ZSTR_SIZE(b);\n"
    "    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + size + 1);\n"
    "    z->size = size;\n"
    "    memcpy(z->data, a->data, ZSTR_SIZE(a));\n"
    "    memcpy(z->data + ZSTR_SIZE(a), b->data, ZSTR_SIZE(b) + 1);\n"
    "    return z;\n"
    "}\n\n"
    "static ZStr* zstr_from_i64(int64_t n) {\n"
    "    char buf[32];\n"
    '    snprintf(buf, sizeof(buf), "%ld", (long)n);\n'
    "    return zstr_new(buf);\n"
    "}\n\n"
    "static ZStr* zstr_from_f64(double n) {\n"
    "    char buf[64];\n"
    '    snprintf(buf, sizeof(buf), "%g", n);\n'
    "    return zstr_new(buf);\n"
    "}\n\n"
    "static void zstr_print(ZStr* s) {\n"
    '    printf("%.*s\\n", (int)ZSTR_SIZE(s), s->data);\n'
    "}\n\n"
    "static void zstr_free(ZStr* s) {\n"
    "    if (s && !ZSTR_IS_STATIC(s)) free(s);\n"
    "}\n\n"
    "static bool zstr_eq(ZStr* a, ZStr* b) {\n"
    "    if (a == b) return true;\n"
    "    uint64_t sa = ZSTR_SIZE(a), sb = ZSTR_SIZE(b);\n"
    "    return sa == sb && memcmp(a->data, b->data, sa) == 0;\n"
    "}\n\n"
    "static inline void zstr_copy_to_buf(\n"
    "    char* dst, uint64_t* dst_len, uint64_t dst_cap,\n"
    "    const char* src, uint64_t src_len\n"
    ") {\n"
    "    uint64_t n = src_len < dst_cap ? src_len : dst_cap;\n"
    "    *dst_len = n;\n"
    "    memcpy(dst, src, n);\n"
    "    dst[n] = '\\0';\n"
    "}\n\n"
)


def emit_runtime_zstr(*, needs_string: bool, needs_stdio: bool) -> str:
    """Emit ZStr struct, macros, and helper functions.

    Future: extract to runtime/zrt_string.c / zrt_string.h.
    """
    if needs_string or needs_stdio:
        return _ZSTR_RUNTIME
    return ""


def emit_runtime(
    *,
    needs_stdio: bool,
    needs_stdint: bool,
    needs_stdlib: bool,
    needs_stdbool: bool,
    needs_string: bool,
) -> str:
    """Return all runtime support code (includes + types + helper functions)."""
    # ZStr runtime uses malloc/free (stdlib.h), strlen/memcpy (string.h),
    # and bool (stdbool.h) for zstr_eq
    has_zstr = needs_string or needs_stdio
    return emit_runtime_includes(
        needs_stdio=needs_stdio,
        needs_stdint=needs_stdint,
        needs_stdlib=needs_stdlib or has_zstr,
        needs_stdbool=needs_stdbool or has_zstr,
        needs_string=needs_string or has_zstr,
    ) + emit_runtime_zstr(needs_string=needs_string, needs_stdio=needs_stdio)


def emit_static_strings(string_literals: Dict[str, str]) -> str:
    """Emit per-program ZSTR_STATIC declarations.

    These are program data (one per unique string literal), not runtime code.
    """
    if not string_literals:
        return ""
    parts: list[str] = []
    for escaped, sname in string_literals.items():
        parts.append(f'ZSTR_STATIC({sname}, "{escaped}");\n')
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
