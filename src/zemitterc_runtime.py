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
    needs_sys_wait: bool = False,
    needs_hash: bool = False,
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

    `needs_hash` pulls in <sys/random.h> + <sys/syscall.h> + <unistd.h>
    + <errno.h> so z_siphash_init can call getrandom (or fall back via
    syscall(SYS_getrandom)) and read /dev/urandom on failure.
    """
    # The panic/x-alloc helpers below need fprintf/stderr, so stdlib implies stdio.
    if needs_stdlib:
        needs_stdio = True
    # z_siphash_init needs fopen, syscall, errno -- pull stdio + stdlib in.
    if needs_hash:
        needs_stdio = True
        needs_string = True
    # Feature-test macro must precede every standard header so glibc
    # exposes POSIX.1-2008 declarations (setenv/unsetenv/realpath/...)
    # under -std=c17. Without this, gcc 14+ rejects implicit
    # declarations as a hard error; gcc 13 emits a warning that the
    # build silently linked through. Always-on is fine — the runtime
    # targets POSIX (Linux/macOS); MSVC ignores the macro and Windows
    # porting will gate function bodies on `#ifdef _WIN32` regardless.
    parts: list[str] = ["#define _POSIX_C_SOURCE 200809L\n"]
    # _DEFAULT_SOURCE exposes <sys/random.h>'s getrandom under -std=c17
    # in addition to the POSIX.1-2008 declarations the _POSIX_C_SOURCE
    # macro selects. Only set when the hash runtime is on so unrelated
    # programs aren't pulled into the glibc default-feature surface.
    if needs_hash:
        parts.append("#define _DEFAULT_SOURCE\n")
    parts.append("\n")
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
    if needs_sys_wait:
        parts.append("#include <sys/wait.h>\n")
        parts.append("#include <signal.h>\n")  # os.spawn timeout: alarm/sigaction/kill
    if needs_hash and not needs_io:
        parts.append("#include <unistd.h>\n")
        parts.append("#include <errno.h>\n")
    if needs_hash:
        parts.append("#include <sys/random.h>\n")
        parts.append("#include <sys/syscall.h>\n")
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


_NATIVES_DIR = os.path.join(_RUNTIME_DIR, "natives")
_NATIVE_CACHE: "dict[str, str]" = {}


def _load_native(stem: str) -> str:
    """Read a native C fragment verbatim from src/runtime/natives/<stem>.inc.

    The `.inc` files are the canonical source for the native fragments; both
    this emitter and the self-hosted `src/zemitterc.z` read them from disk.
    Read byte-for-byte (no normalisation) so the emitted runtime is identical
    to the text the fragment ships with.
    """
    cached = _NATIVE_CACHE.get(stem)
    if cached is not None:
        return cached
    with open(os.path.join(_NATIVES_DIR, stem + ".inc"), encoding="utf-8") as fh:
        content = fh.read()
    _NATIVE_CACHE[stem] = content
    return content


def _z_string_runtime() -> str:
    return _load_runtime_fragment("z_String.inc")


def _z_stringview_runtime() -> str:
    return _load_runtime_fragment("z_StringView.inc")


def _z_hash_runtime() -> str:
    return _load_runtime_fragment("z_hash.inc")


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


def emit_runtime_z_hash(*, needs_hash: bool) -> str:
    """Emit SipHash + splitmix64 helpers + z_siphash_init.

    Source lives in src/runtime/z_hash.inc. Lands after z_String /
    z_StringView so the helpers can take those types by value;
    references syscall(SYS_getrandom) / getrandom + /dev/urandom so
    the runtime preamble also pulls <sys/random.h>, <sys/syscall.h>,
    <unistd.h>, and <errno.h> when this is enabled.
    """
    if needs_hash:
        return _z_hash_runtime()
    return ""


_Z_IO_EPRINTLN = _load_native("_Z_IO_EPRINTLN")

_Z_IO_ERRNO_MAP = _load_native("_Z_IO_ERRNO_MAP")

# Helper used by every native taking a `stringview` it must hand
# off to a C API expecting a NUL-terminated string. Allocates a
# heap copy because views may be substrings (no NUL guarantee at
# v.data[v.length]). Caller is responsible for free().
_Z_SV_TO_CSTR = _load_native("_Z_SV_TO_CSTR")

_Z_IO_READ_TEXT = _load_native("_Z_IO_READ_TEXT")

_Z_IO_WRITE_COMMON = _load_native("_Z_IO_WRITE_COMMON")

_Z_IO_WRITE_TEXT = _load_native("_Z_IO_WRITE_TEXT")

_Z_IO_APPEND_TEXT = _load_native("_Z_IO_APPEND_TEXT")

_Z_IO_READLINK = _load_native("_Z_IO_READLINK")

_Z_IO_SYMLINK = _load_native("_Z_IO_SYMLINK")

_Z_IO_EXISTS = _load_native("_Z_IO_EXISTS")

# shared helper: wrap an int return (0 ok, -1 err-with-errno) + path
# free into a z_Result_null_IoError_t. Used by mkdir / remove / rename.
_Z_IO_WRAP_NULL_RESULT = _load_native("_Z_IO_WRAP_NULL_RESULT")

_Z_IO_MKDIR = _load_native("_Z_IO_MKDIR")

_Z_IO_REMOVE = _load_native("_Z_IO_REMOVE")

_Z_IO_RENAME = _load_native("_Z_IO_RENAME")

_Z_IO_STAT_FILL = _load_native("_Z_IO_STAT_FILL")

_Z_IO_STAT = _load_native("_Z_IO_STAT")

_Z_IO_LSTAT = _load_native("_Z_IO_LSTAT")

_Z_IO_MKDIRP = _load_native("_Z_IO_MKDIRP")

_Z_IO_LIST_DIR = _load_native("_Z_IO_LIST_DIR")

_Z_IO_OPEN = _load_native("_Z_IO_OPEN")

_Z_FILE_CLOSE = _load_native("_Z_FILE_CLOSE")

# shared helper: box a u64 into result(u64, ioerror) ok arm
_Z_IO_WRAP_U64_RESULT = _load_native("_Z_IO_WRAP_U64_RESULT")

_Z_FILE_READ = _load_native("_Z_FILE_READ")

_Z_FILE_WRITE = _load_native("_Z_FILE_WRITE")

_Z_FILE_FLUSH = _load_native("_Z_FILE_FLUSH")


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


_Z_BUFWRITER_CREATE = _load_native("_Z_BUFWRITER_CREATE")

_Z_BUFWRITER_FLUSH = _load_native("_Z_BUFWRITER_FLUSH")

_Z_BUFWRITER_WRITE = _load_native("_Z_BUFWRITER_WRITE")

_Z_BUFREADER_CREATE = _load_native("_Z_BUFREADER_CREATE")

_Z_TEXTWRITER_CREATE = _load_native("_Z_TEXTWRITER_CREATE")

_Z_TEXTWRITER_WRITE = _load_native("_Z_TEXTWRITER_WRITE")

_Z_TEXTWRITER_WRITE_LINE = _load_native("_Z_TEXTWRITER_WRITE_LINE")

_Z_TEXTWRITER_FLUSH = _load_native("_Z_TEXTWRITER_FLUSH")

_Z_BUFREADER_READ = _load_native("_Z_BUFREADER_READ")

_Z_IO_UTF8_VALIDATE = _load_native("_Z_IO_UTF8_VALIDATE")

_Z_TEXTREADER_CREATE = _load_native("_Z_TEXTREADER_CREATE")

_Z_TEXTREADER_READ_LINE = _load_native("_Z_TEXTREADER_READ_LINE")

_Z_TEXTREADER_CALL = _load_native("_Z_TEXTREADER_CALL")

_Z_FILE_SEEK = _load_native("_Z_FILE_SEEK")


_Z_OS_ARGV_GLOBALS = _load_native("_Z_OS_ARGV_GLOBALS")

_Z_OS_ARGS = _load_native("_Z_OS_ARGS")

_Z_OS_GET_ENV = _load_native("_Z_OS_GET_ENV")

_Z_OS_SET_ENV = _load_native("_Z_OS_SET_ENV")

_Z_OS_SPAWN = _load_native("_Z_OS_SPAWN")

_Z_OS_UNSET_ENV = _load_native("_Z_OS_UNSET_ENV")

_Z_OS_ENV_NAMES = _load_native("_Z_OS_ENV_NAMES")

_Z_OS_CWD = _load_native("_Z_OS_CWD")

_Z_OS_EXE_PATH = _load_native("_Z_OS_EXE_PATH")

_Z_OS_SET_CWD = _load_native("_Z_OS_SET_CWD")

_Z_OS_PID = _load_native("_Z_OS_PID")

_Z_OS_PPID = _load_native("_Z_OS_PPID")

_Z_OS_USER_NAME = _load_native("_Z_OS_USER_NAME")

_Z_OS_HOME_DIR = _load_native("_Z_OS_HOME_DIR")

_Z_OS_PLATFORM = _load_native("_Z_OS_PLATFORM")

_Z_OS_ARCH = _load_native("_Z_OS_ARCH")

_Z_OS_HOSTNAME = _load_native("_Z_OS_HOSTNAME")

_Z_OS_EXIT = _load_native("_Z_OS_EXIT")


# -- Phase S1: non-allocating query natives on stringview ---------
# Emitted AFTER mono types so the option/optionval wrappers they
# return (e.g. z_optionval_u64_t) are already declared. Per-name
# gated like io/os.

_Z_SV_IS_EMPTY = _load_native("_Z_SV_IS_EMPTY")

_Z_SV_IS_ASCII = _load_native("_Z_SV_IS_ASCII")

_Z_SV_STARTS_WITH = _load_native("_Z_SV_STARTS_WITH")

_Z_SV_ENDS_WITH = _load_native("_Z_SV_ENDS_WITH")

# Raw byte-search helper used by contains / index_of. Returns\n
# UINT64_MAX on miss. Empty needle returns 0.
_Z_SV_INDEX_OF_RAW = _load_native("_Z_SV_INDEX_OF_RAW")

_Z_SV_CONTAINS = _load_native("_Z_SV_CONTAINS")

_Z_SV_INDEX_OF = _load_native("_Z_SV_INDEX_OF")

_Z_SV_LAST_INDEX_OF = _load_native("_Z_SV_LAST_INDEX_OF")

_Z_SV_BYTE_AT = _load_native("_Z_SV_BYTE_AT")

# -- Phase S2: view-returning slicing helpers ---------------------

# ASCII whitespace: space, tab, LF, VT, FF, CR. Matches Rust's
# `is_ascii_whitespace`. Small inline helper shared by trim.
_Z_SV_IS_ASCII_WS = _load_native("_Z_SV_IS_ASCII_WS")

_Z_SV_TRIM = _load_native("_Z_SV_TRIM")

_Z_SV_TRIM_START = _load_native("_Z_SV_TRIM_START")

_Z_SV_TRIM_END = _load_native("_Z_SV_TRIM_END")

# strip_prefix / strip_suffix return option(stringview). The some
# arm is a heap-boxed stringview (union payloads are void*); the
# compiler-generated destructor frees it on scope exit. One tiny
# (16 byte) allocation per call.
_Z_SV_STRIP_PREFIX = _load_native("_Z_SV_STRIP_PREFIX")

# -- Phase S3: splitter / linesiter ------------------------------

# Shared struct layout for both splitter and linesiter. Fields are
# not exposed to zerolang; the classes are declared `is native`.
# The emitter emits a typedef for each class based on the declared
# fields — since there are none, the runtime must provide the full
# struct here. We shadow the compiler-emitted empty typedef by
# defining the struct first and using a different name with a
# forward `typedef`.
_Z_SV_SPLITTER_STRUCT = _load_native("_Z_SV_SPLITTER_STRUCT")

_Z_SV_SPLIT = _load_native("_Z_SV_SPLIT")

_Z_SV_SPLITTER_CALL = _load_native("_Z_SV_SPLITTER_CALL")

_Z_SV_SPLIT_ONCE = _load_native("_Z_SV_SPLIT_ONCE")

_Z_SV_LINES = _load_native("_Z_SV_LINES")

# -- Phase S4: allocating transforms -------------------------------
# All return a freshly-allocated z_String_t; caller's scope cleanup
# frees the heap buffer.

_Z_SV_TO_LOWER_ASCII = _load_native("_Z_SV_TO_LOWER_ASCII")

_Z_SV_TO_UPPER_ASCII = _load_native("_Z_SV_TO_UPPER_ASCII")

# Shared replace implementation. `once=true` replaces only the
# first occurrence.
_Z_SV_REPLACE_IMPL = _load_native("_Z_SV_REPLACE_IMPL")

_Z_SV_REPLACE = _load_native("_Z_SV_REPLACE")

_Z_SV_REPLACE_FIRST = _load_native("_Z_SV_REPLACE_FIRST")

_Z_SV_REPEAT = _load_native("_Z_SV_REPEAT")

# -- Phase S5: codepoint iteration + count -------------------------

_Z_SV_CPITER_STRUCT = _load_native("_Z_SV_CPITER_STRUCT")

# Decode one UTF-8 codepoint starting at data[i]. Advances i past
# the consumed bytes. Ill-formed sequences return U+FFFD and
# advance one byte. Assumes i < length on entry.
_Z_SV_UTF8_DECODE = _load_native("_Z_SV_UTF8_DECODE")

_Z_SV_COUNT = _load_native("_Z_SV_COUNT")

_Z_SV_CODEPOINTS = _load_native("_Z_SV_CODEPOINTS")

_Z_SV_CPITER_CALL = _load_native("_Z_SV_CPITER_CALL")

# -- Phase S6: numeric parsing -----------------------------------

_Z_SV_PARSE_I64 = _load_native("_Z_SV_PARSE_I64")

_Z_SV_PARSE_U64 = _load_native("_Z_SV_PARSE_U64")

_Z_SV_PARSE_F64 = _load_native("_Z_SV_PARSE_F64")

_Z_SV_CONCAT = _load_native("_Z_SV_CONCAT")

_Z_SV_LINES_CALL = _load_native("_Z_SV_LINES_CALL")

_Z_SV_STRIP_SUFFIX = _load_native("_Z_SV_STRIP_SUFFIX")


# Phase S7: string.join free function. Lives alongside the
# stringview natives because it shares the same late emission slot
# (it references z_List_String_t which is a mono type).
_Z_COLL_STRING_JOIN = _load_native("_Z_COLL_STRING_JOIN")


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

_Z_CLI_SPEC_CREATE = _load_native("_Z_CLI_SPEC_CREATE")

_Z_CLI_ADD_FLAG = _load_native("_Z_CLI_ADD_FLAG")

_Z_CLI_ADD_OPTION = _load_native("_Z_CLI_ADD_OPTION")

_Z_CLI_ADD_POSITIONAL = _load_native("_Z_CLI_ADD_POSITIONAL")

# Helpers: byte-compare a z_String_t to a C string, and locate a
# registered flag/option by its long or short name.
_Z_CLI_HELPERS = _load_native("_Z_CLI_HELPERS")

_Z_CLI_PARSE = _load_native("_Z_CLI_PARSE")

_Z_CLI_HELP_TEXT = _load_native("_Z_CLI_HELP_TEXT")

_Z_PARSED_HAS_FLAG = _load_native("_Z_PARSED_HAS_FLAG")

_Z_PARSED_GET_OPTION = _load_native("_Z_PARSED_GET_OPTION")

_Z_PARSED_GET_POSITIONAL = _load_native("_Z_PARSED_GET_POSITIONAL")


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
    if "exePath" in natives:
        parts.append(_Z_OS_EXE_PATH)
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
    if "spawn" in natives:
        parts.append(_Z_OS_SPAWN)
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
            "exePath",
        }
    )
    # os natives whose stringview args need the shared `z_sv_to_cstr`
    # helper. Independent of errno_map so a get_env-only program still
    # gets the helper without pulling in the errno table.
    os_needs_sv_cstr = bool(
        os_natives & {"env", "setEnv", "unsetEnv", "setCwd", "spawn"}
    )
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
        # bufreader_read / bufwriter_write propagate underlying
        # source/sink errors via `z_io_errno_to_IoError`, so the
        # helper must be in scope when either is used standalone
        # (without a file op already pulling it in).
        "bufreader_read",
        "bufwriter_write",
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
    # os.get_env / set_env / unset_env / set_cwd / spawn also use it,
    # signalled via os_natives (os_needs_sv_cstr, computed above).
    if not sv_cstr_users and os_needs_sv_cstr:
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
    needs_sys_wait: bool = False,
    needs_hash: bool = False,
) -> str:
    """Return all runtime support code (includes + types + helper functions)."""
    # z_String_t runtime uses malloc/free (stdlib.h) and strlen/memcpy
    # (string.h). <stdbool.h> is always included (see emit_runtime_includes).
    has_z_string = needs_string or needs_stdio
    # The hash runtime references z_String_t / z_StringView_t by value;
    # pull both in whenever it's enabled.
    if needs_hash:
        needs_string = True
        needs_stringview = True
    return (
        emit_runtime_includes(
            needs_stdio=needs_stdio or needs_io,
            needs_stdint=needs_stdint,
            needs_stdlib=needs_stdlib or has_z_string or needs_stringview,
            needs_string=needs_string or has_z_string or needs_stringview,
            needs_io=needs_io,
            needs_pwd=needs_pwd,
            needs_sys_wait=needs_sys_wait,
            needs_hash=needs_hash,
        )
        + emit_runtime_z_string(needs_string=needs_string, needs_stdio=needs_stdio)
        + emit_runtime_z_stringview(needs_stringview=needs_stringview)
        + emit_runtime_z_hash(needs_hash=needs_hash)
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
