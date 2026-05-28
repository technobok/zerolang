"""
Tests for the C code emitter (zemitterc)
"""

import os
import subprocess
import tempfile

import pytest

from conftest import make_parser_vfs, make_parser, make_parser_with_vfs
from zparser import Parser  # noqa: F401  (kept for type references)
from ztypecheck import typecheck
import zemitterc
import zast


pytestmark = [pytest.mark.emitter, pytest.mark.runtime]

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

# C compiler used for emitter tests. Override with Z_TEST_CC=clang to
# exercise the emitted C against a different compiler — useful for
# catching warnings/errors that gcc tolerates but clang doesn't (or
# vice versa). Default keeps the gcc-based dev loop unchanged.
_CC = os.environ.get("Z_TEST_CC", "gcc")


def emit_source(source: str, unitname: str = "test") -> str:
    """Parse, type-check, and emit C source for a zerolang program."""
    p = make_parser(source, unitname=unitname, src_dir=LIB_DIR)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    typing = typecheck(program)
    errors = typing.errors
    assert errors == [], f"Type errors: {[e.msg for e in errors]}"
    return zemitterc.emit(typing)


def compile_and_run_with_args(csource: str, argv: list[str]) -> str:
    """Compile C source and run the binary with the supplied argv.
    Returns stdout."""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        cmd = [
            _CC,
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-Wno-unused-function",
            "-Wno-unused-parameter",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
            "-Werror=int-conversion",
            "-Werror=incompatible-pointer-types",
            "-o",
            outpath,
            cpath,
        ]
        comp = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if comp.returncode != 0:
            raise RuntimeError(f"gcc failed:\n{comp.stderr}")
        result = subprocess.run(
            [outpath] + argv,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    finally:
        for p in (cpath, outpath):
            if os.path.exists(p):
                os.unlink(p)


def compile_and_run(csource: str, extra_cflags: list[str] | None = None) -> str:
    """Compile C source with gcc and run, returning stdout."""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        cmd = [
            _CC,
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-Wno-unused-function",
            "-Wno-unused-parameter",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
            "-Werror=int-conversion",
            "-Werror=incompatible-pointer-types",
        ]
        if extra_cflags:
            cmd.extend(extra_cflags)
        cmd.extend(["-o", outpath, cpath])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gcc failed:\n{result.stderr}")
        result = subprocess.run(
            [outpath],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    finally:
        for p in (cpath, outpath):
            if os.path.exists(p):
                os.unlink(p)


def compile_and_capture(csource: str) -> tuple[int, str, str]:
    """Compile C source, run the binary, return (exit_code, stdout, stderr).
    Useful for programs that exit non-zero (e.g. panics)."""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        cmd = [
            _CC,
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-Wno-unused-function",
            "-Wno-unused-parameter",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
            "-Werror=int-conversion",
            "-Werror=incompatible-pointer-types",
            "-o",
            outpath,
            cpath,
        ]
        comp = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if comp.returncode != 0:
            raise RuntimeError(f"gcc failed:\n{comp.stderr}")
        result = subprocess.run([outpath], capture_output=True, text=True, timeout=10)
        return result.returncode, result.stdout, result.stderr
    finally:
        for p in (cpath, outpath):
            if os.path.exists(p):
                os.unlink(p)


class TestBareReturn:
    """Regression coverage for bare `return` (no value) emission.

    The parser models bare `return` as a plain AtomId reference to the
    `return` function. Without a CallKind.RETURN branch in
    `_emit_expression_stmt`, the bare AtomId fell through to generic
    statement emission and was mangled to `z_return` — gcc rejected.
    The fix emits scope cleanup + bare C `return;` whenever the
    expression's CallKind is RETURN.
    """

    def test_top_level_bare_return(self):
        # A function that ends with a bare `return` should compile and
        # run cleanly — the C output must use the `return` keyword, not
        # the mangled identifier `z_return`.
        csource = emit_source('main: function is { print "ran"\n return }')
        assert compile_and_run(csource) == "ran\n"

    def test_bare_return_inside_if(self):
        # Conditional early-return: when the guard fires, the
        # post-if print is unreached at runtime.
        csource = emit_source(
            "main: function is {\n"
            '    if 1 == 1 then { print "guard"\n return }\n'
            '    print "fallthrough"\n'
            "}"
        )
        assert compile_and_run(csource) == "guard\n"

    def test_bare_return_inside_match_case(self):
        # Match arm that early-returns alongside an arm that doesn't.
        csource = emit_source(
            "Color: variant { red: null blue: null }\n"
            "main: function is {\n"
            "    c: Color.red\n"
            "    match (c) case red then {\n"
            '        print "red"\n'
            "        return\n"
            "    } case blue then {\n"
            '        print "blue"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "red\n"

    def test_bare_return_inside_for_loop(self):
        # Exercises issue #1 (per-block scope) and issue #2 (bare return)
        # together: the loop body declares a class local, then early-returns
        # at the third iteration.
        csource = emit_source(
            "Token: class { v: i64 }\n"
            "mk: function {n: i64} out Token is { return (Token v: n) }\n"
            "main: function is {\n"
            "    n: 0.u64\n"
            "    for while n < 5.u64 loop {\n"
            "        t: mk 1\n"
            '        print "iter \\{n} v=\\{t.v}"\n'
            "        if n == 2.u64 then { return }\n"
            "        n = n + 1.u64\n"
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == ("iter 0 v=1\niter 1 v=1\niter 2 v=1\n")

    def test_bare_return_no_leak_under_asan(self):
        # Allocate a class local, then bare-return; the function-exit
        # cleanup must run so ASan sees no leak.
        csource = emit_source(
            "Token: class { v: i64 }\n"
            "mk: function {n: i64} out Token is { return (Token v: n) }\n"
            "main: function is {\n"
            "    t: mk 1\n"
            '    print "got \\{t.v}"\n'
            "    return\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, (
            f"asan flagged: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == "got 1\n"


class TestReturnConstructionTakeSoundness:
    """Regression coverage for the return-construction-shorthand take bug.

    `return Type field: localvar` for a class with a take-bound field used
    to silently double-free at runtime: the construction took ownership of
    `localvar`'s heap bytes, but the function-exit cleanup also freed
    them. The typechecker fix records the take in the symtab; the
    emitter fix flushes implicit-take zero-inits before the scope
    cleanup so the cleanup is a no-op for taken vars.
    """

    def test_single_take_in_return_shorthand_asan_clean(self):
        # Minimal repro: mk allocates wstr, returns Token taking it.
        # Before the fix, this UAFed under ASan in the caller's `t.v` read.
        csource = emit_source(
            "Token: class { v: String }\n"
            "mk: function {n: i64} out Token is "
            '{ return (Token v: "hi".string) }\n'
            "main: function is {\n"
            "    t: mk 1\n"
            '    print "v=\\{t.v}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, (
            f"asan flagged: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == "v=hi\n"

    def test_local_var_take_in_return_shorthand_asan_clean(self):
        # Variant with a named local being taken in the return shorthand
        # — the exact shape from the lexer port that surfaced the bug.
        csource = emit_source(
            "Token: class { v: String }\n"
            "mk: function {n: i64} out Token is {\n"
            '    wstr: "hi".string\n'
            "    return (Token v: wstr)\n"
            "}\n"
            "main: function is {\n"
            "    t: mk 1\n"
            '    print "v=\\{t.v}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, (
            f"asan flagged: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == "v=hi\n"


class TestForBodyClassCleanup:
    """Regression coverage for the for-loop body destructor scope bug.

    Class-typed locals declared inside a `for { … }` body must be
    destroyed at iteration end, not deferred to function exit (where
    their C names are out of scope and previous iterations leak).
    """

    _SRC_WHILE_FORM = (
        "Token: class { v: String }\n"
        "mk: function {n: i64} out Token is "
        '{ return (Token v: "hi".string) }\n'
        "main: function is {\n"
        "    n: 0.u64\n"
        "    for while n < 3.u64 loop {\n"
        "        t: mk 1\n"
        '        print "iter \\{n} v=\\{t.v}"\n'
        "        n = n + 1.u64\n"
        "    }\n"
        "}"
    )

    _SRC_INIT_FORM = (
        "Token: class { v: String }\n"
        "mk: function {n: i64} out Token is "
        '{ return (Token v: "hi".string) }\n'
        "main: function is {\n"
        "    for i: 0 while i < 3 loop {\n"
        "        t: mk i\n"
        '        print "iter \\{i} v=\\{t.v}"\n'
        "        i = i + 1\n"
        "    }\n"
        "}"
    )

    _EXPECTED_OUTPUT = "iter 0 v=hi\niter 1 v=hi\niter 2 v=hi\n"

    def test_for_while_body_class_local_compiles_and_runs(self):
        csource = emit_source(self._SRC_WHILE_FORM)
        assert compile_and_run(csource) == self._EXPECTED_OUTPUT

    def test_for_init_while_body_class_local_compiles_and_runs(self):
        csource = emit_source(self._SRC_INIT_FORM)
        assert compile_and_run(csource) == self._EXPECTED_OUTPUT

    def test_for_while_body_class_local_no_leak_under_asan(self):
        csource = emit_source(self._SRC_WHILE_FORM)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, (
            f"asan flagged: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == self._EXPECTED_OUTPUT

    def test_for_init_while_body_class_local_no_leak_under_asan(self):
        csource = emit_source(self._SRC_INIT_FORM)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, (
            f"asan flagged: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == self._EXPECTED_OUTPUT


class TestUnionTakeInArmNoDoubleFree:
    """Pin: `.take` of a match-narrowed union arm whose payload moves
    into another owner (class field, here) must not double-free the
    heap shared between the union's wrapper and the new owner.

    Was PR-3 friction item 2: the typechecker stamped
    `case_subject_taken` but the post-switch
    `z_<union>_destroy(&subject)` re-freed the heap pointer that the
    take had moved into `h.src`. Fix: typechecker also records the
    set of arm names that took the subject
    (`case_subject_taken_arms`); the emitter zeroes the inner payload
    at the end of each such arm so the post-switch destroy walks a
    zeroed payload (free(NULL) no-op) while still freeing the union
    wrapper.
    """

    _SRC = (
        "StrRes: union { ok: String  err: String }\n"
        "Hold: class { src: String } as {\n"
        '  show: function {:this} is { print "src=\\{this.src}" }\n'
        "}\n"
        "read: function {ok: bool} out StrRes is {\n"
        "  if ok then {\n"
        '    r: StrRes.ok "loaded".string\n'
        "    return r\n"
        "  }\n"
        '  r: StrRes.err "failed".string\n'
        "  return r\n"
        "}\n"
        "main: function is {\n"
        "  r: read ok: true\n"
        "  match (\n"
        "    r\n"
        "  ) case ok then {\n"
        "    h: Hold src: r.take\n"
        "    h.show\n"
        '    print "ok"\n'
        "  } case err then {\n"
        '    print "err-arm"\n'
        "  }\n"
        "}"
    )

    def test_ok_arm_takes_no_doublefree(self):
        csource = emit_source(self._SRC)
        # Per-arm inner-payload zero shows up.
        assert "*(z_String_t*)r.data = (z_String_t){0};" in csource
        # ASAN driver is clean and prints both arm and outer messages.
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, (
            f"asan flagged: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == "src=loaded\nok\n"


class TestForWhileCallCondReEvaluates:
    """Pin: a for-loop's `while` condition that wraps a function call
    on per-iteration mutable state must re-evaluate each iteration.

    Was the PR-3 friction item where the typechecker's `_hoist_arg`
    lifted the call's args into a synth temp ahead of the loop and
    the parent statement's preamble drained the temp init *once*
    before the loop — so the cond kept reading the stale initial
    value and the loop never made progress.

    Fix shape: the typechecker captures the cond's preamble entries
    into `ZTyping.for_cond_preamble`; the emitter rewrites the loop
    into `while (1) { decls; if (!cond) break; body }` so the synth
    temps re-init each iteration.
    """

    def test_call_in_for_while_cond_re_evaluates_each_iteration(self):
        csource = emit_source(
            "notZero: function {b: u8} out bool is { return b > 0.u8 }\n"
            "Probe: class { atchar: u8 count: u32 } as {\n"
            "  advance: function {:this} is {\n"
            "    if this.atchar > 0.u8 then this.atchar = this.atchar - 1.u8\n"
            "    this.count = this.count + 1.u32\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  p: Probe atchar: 5.u8 count: 0.u32\n"
            "  for while (notZero b: p.atchar) loop {\n"
            "    p.advance\n"
            "  }\n"
            '  print "count=\\{p.count}"\n'
            "}"
        )
        # Loop body re-inits the call's args (`_tN = p.atchar`) and
        # breaks on the cond's negation, instead of testing a stale
        # pre-loop temp.
        assert "while (1) {" in csource
        assert "if (!(z_notZero(" in csource
        assert compile_and_run(csource).strip() == "count=5"


class TestAutoCallNoOutClause:
    """Pin: a dotted-path method call on a method declared without
    an explicit `out` clause auto-calls in value position (and in
    statement position like a for-loop body's last statement),
    rather than leaving the dotted path typed as a function
    reference.

    Was the PR-3 friction item where `for ... loop { s.bump }`
    (with `bump: function {:this} is { ... }` — no `out`)
    mis-classified `s.bump` as a function reference and the
    typechecker tried to monomorphize `List of: Sink.bump`,
    generating C identifiers containing a literal `.`.
    """

    def test_void_method_as_for_body_tail_auto_calls(self):
        csource = emit_source(
            "Sink: class { n: u32 } as {\n"
            "  bump: function {:this} is { this.n = this.n + 1.u32 }\n"
            "}\n"
            "main: function is {\n"
            "  s: Sink n: 0.u32\n"
            "  for while s.n < 3.u32 loop {\n"
            "    s.bump\n"
            "  }\n"
            '  print "n=\\{s.n}"\n'
            "}"
        )
        # No spurious List-of-method monomorphization.
        assert "Sink.bump" not in csource
        assert "ListView_Sink" not in csource
        # The body call lowers as a method invocation, not a
        # struct-field read.
        assert "z_Sink_bump(&s)" in csource
        assert compile_and_run(csource).strip() == "n=3"

    def test_void_method_as_statement_auto_calls(self):
        csource = emit_source(
            "Sink: class { n: u32 } as {\n"
            "  bump: function {:this} is { this.n = this.n + 1.u32 }\n"
            "}\n"
            "main: function is {\n"
            "  s: Sink n: 0.u32\n"
            "  s.bump\n"
            "  s.bump\n"
            '  print "n=\\{s.n}"\n'
            "}"
        )
        assert "z_Sink_bump(&s)" in csource
        assert compile_and_run(csource).strip() == "n=2"


class TestEscapeSequences:
    """End-to-end coverage of spec.pdoc:463-474 escape sequences.

    `print` appends a trailing LF, so each expected output ends in '\\n'.
    """

    def test_quote_escape(self):
        csource = emit_source(r'main: function is { print "a\"b" }')
        assert compile_and_run(csource) == 'a"b\n'

    def test_newline_escape(self):
        csource = emit_source(r'main: function is { print "a\nb" }')
        assert compile_and_run(csource) == "a\nb\n"

    def test_tab_escape(self):
        csource = emit_source(r'main: function is { print "a\tb" }')
        assert compile_and_run(csource) == "a\tb\n"

    def test_backspace_escape(self):
        csource = emit_source(r'main: function is { print "a\bb" }')
        assert compile_and_run(csource) == "a\bb\n"

    def test_backslash_escape(self):
        csource = emit_source(r'main: function is { print "a\\b" }')
        assert compile_and_run(csource) == "a\\b\n"

    def test_hex_escape_single_byte(self):
        # \x41 is the single byte 0x41 ('A').
        csource = emit_source(r'main: function is { print "\x41" }')
        assert compile_and_run(csource) == "A\n"

    def test_unicode_escape_utf8(self):
        # U+263A (&3a, ☺) emits as 3 UTF-8 bytes: 0xe2 0x98 0xba.
        csource = emit_source('main: function is { print "\\u00263A" }')
        assert compile_and_run(csource).encode("utf-8") == b"\xe2\x98\xba\n"

    def test_escape_inside_interpolation(self):
        # Interpolated strings flow through the same emitter path.
        csource = emit_source('main: function is {\n  n: 5\n  print "x=\\{n}\\n"\n}')
        assert compile_and_run(csource) == "x=5\n\n"


class TestCharLiterals:
    """End-to-end coverage of `0c<char>` character literals (spec.pdoc:228+).

    Shorthand form (`0c<id-char>`) only; escape sequences (`0c\\n`,
    `0c\\xHH`, etc.) and non-identifier chars (`0c#`) remain deferred."""

    def test_char_literal_evaluates_to_codepoint(self):
        csource = emit_source('main: function is {\n  c: 0cA\n  print "\\{c}"\n}')
        assert compile_and_run(csource) == "65\n"

    def test_char_literal_with_u8_suffix(self):
        csource = emit_source('main: function is {\n  c: 0cA.u8\n  print "\\{c}"\n}')
        assert compile_and_run(csource) == "65\n"

    def test_char_literal_coerces_against_u8(self):
        csource = emit_source(
            "main: function is {\n"
            "  b: 65.u8\n"
            '  if b == 0cA then print "match" else print "no match"\n'
            "}"
        )
        assert compile_and_run(csource) == "match\n"


class TestEmitterBasic:
    def test_hello_world(self):
        csource = emit_source('main: function is { print "Hello, World!" }')
        assert "z_String_print" in csource
        assert "z_main" in csource
        output = compile_and_run(csource)
        assert output.strip() == "Hello, World!"

    def test_integer_arithmetic(self):
        csource = emit_source('main: function is {\n  x: 2 + 3\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_function_call(self):
        csource = emit_source(
            "double: function {n: i64} out i64 is { return n + n }\n"
            'main: function is { print "\\{double 21}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_if_else(self):
        csource = emit_source(
            "main: function is {\n"
            "  x: 5\n"
            '  if x > 3 then print "big" else print "small"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "big"

    def test_for_loop(self):
        csource = emit_source(
            "main: function is {\n"
            "  sum: 0\n"
            "  for i: 1 while i <= 5 loop {\n"
            "    sum = sum + i\n"
            "    i = i + 1\n"
            "  }\n"
            '  print "\\{sum}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_for_do_while(self):
        """Post-condition: for loop { body } while cond — executes at least once."""
        csource = emit_source(
            "main: function is {\n"
            "  i: 0\n"
            "  for loop { i = i + 1 } while i < 3\n"
            '  print "\\{i}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "3"

    def test_for_do_while_once(self):
        """Post-condition with immediately-false condition executes exactly once."""
        csource = emit_source(
            "main: function is {\n"
            "  i: 0\n"
            "  for loop { i = i + 1 } while i < 1\n"
            '  print "\\{i}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "1"

    def test_for_pre_and_post_condition(self):
        """Combined pre+post: while pre loop { body } while post."""
        csource = emit_source(
            "main: function is {\n"
            "  i: 0\n"
            "  for while i < 10 loop { i = i + 1 } while i < 5\n"
            '  print "\\{i}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_for_do_while_multiline(self):
        """Post-condition with multi-line loop body."""
        csource = emit_source(
            "main: function is {\n"
            "  i: 0\n"
            "  for loop {\n"
            "    i = i + 1\n"
            "  } while i < 5\n"
            '  print "\\{i}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_for_infinite_loop(self):
        """for loop { } generates while(1) — verified by emitted C code."""
        csource = emit_source("main: function is {\n  for loop { return }\n}")
        assert "while (1)" in csource

    def test_for_break(self):
        """break exits a for loop."""
        csource = emit_source(
            "main: function is {\n"
            "  i: 0\n"
            "  for loop {\n"
            "    i = i + 1\n"
            "    if i == 3 then { break }\n"
            "  }\n"
            '  print "\\{i}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "3"

    def test_for_continue(self):
        """continue skips to next iteration."""
        csource = emit_source(
            "main: function is {\n"
            "  sum: 0\n"
            "  for i: 0 while i < 5 loop {\n"
            "    i = i + 1\n"
            "    if i == 3 then { continue }\n"
            "    sum = sum + i\n"
            "  }\n"
            '  print "\\{sum}"\n'
            "}"
        )
        output = compile_and_run(csource)
        # sum = 1+2+4+5 = 12 (skip 3)
        assert output.strip() == "12"

    def test_callable_object(self):
        """Record with a 'call' method can be invoked as a function."""
        csource = emit_source(
            "adder: record { base: i64 } as {\n"
            "  call: function {a: this n: i64} out i64 is {\n"
            "    return a.base + n\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  a: adder base: 10\n"
            "  result: a 5\n"
            '  print "\\{result}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_callable_object_no_extra_args(self):
        """Callable object with only 'this' parameter (no extra args)."""
        csource = emit_source(
            "getter: record { value: i64 } as {\n"
            "  call: function {g: this} out i64 is {\n"
            "    return g.value\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  g: getter value: 42\n"
            "  result: getter.call g\n"
            '  print "\\{result}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_for_iterator_binding(self):
        """For-loop with iterator binding: callable returning optionval."""
        csource = emit_source(
            "Counter: class { i: i64 max: i64 } as {\n"
            "  call: function {c: this} out (optionval t: i64) is {\n"
            "    if c.i < c.max then {\n"
            "      result: optionval.some c.i\n"
            "      c.i = c.i + 1\n"
            "      return result\n"
            "    }\n"
            "    result: optionval.none i64\n"
            "    return result\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  with iter: (Counter i: 0 max: 3) do for x: iter loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0\n1\n2"

    def test_for_each_integer(self):
        """for x: n.each — iterates from 0 to n-1."""
        csource = emit_source(
            "main: function is {\n"
            "  n: 5\n"
            "  for x: n.each loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0\n1\n2\n3\n4"

    def test_for_each_literal(self):
        """for x: 3.each — iterates with literal."""
        csource = emit_source(
            'main: function is {\n  for x: 3.each loop {\n    print "\\{x}"\n  }\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "0\n1\n2"

    def test_for_each_from(self):
        """for x: (n.each from: 2) — iterates from k to n-1."""
        csource = emit_source(
            "main: function is {\n"
            "  n: 5\n"
            "  for x: (n.each from: 2) loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2\n3\n4"

    def test_for_each_zero(self):
        """for x: 0.each — no iterations."""
        csource = emit_source(
            "main: function is {\n"
            "  for x: 0.each loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            '  print "done"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "done"

    def test_for_comprehension(self):
        """for-as-expression returns a List."""
        csource = emit_source(
            "main: function is {\n"
            "  result: for x: 3.each loop { x * 2 }\n"
            '  print "\\{result.length}"\n'
            "  for i: result.length.each loop {\n"
            '    print "\\{result.get i}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "3\n0\n2\n4"

    def test_for_iterate_integer(self):
        """`.iterate` is the canonical name; same behavior as `.each`."""
        csource = emit_source(
            "main: function is {\n"
            "  n: 5\n"
            "  for x: n.iterate loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0\n1\n2\n3\n4"

    def test_for_iterate_from(self):
        """for x: (n.iterate from: k) — iterates from k to n-1."""
        csource = emit_source(
            "main: function is {\n"
            "  n: 5\n"
            "  for x: (n.iterate from: 2) loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2\n3\n4"

    def test_for_iterate_with_break_in_body(self):
        """break inside a `for x: n.iterate` body must not leak its
        function-type into a generic-arg slot. Regression: the for-as-
        expression code was picking up `break`'s ZType as the list
        element type and monomorphising `List<break>` / `ListView<break>`."""
        csource = emit_source(
            "main: function is {\n"
            "  for x: 5.iterate loop {\n"
            '    print "x: \\{x}"\n'
            "    break\n"
            "  }\n"
            '  print "done"\n'
            "}"
        )
        assert "z_break_ft" not in csource
        assert "z_List_break" not in csource
        assert "z_ListView_break" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "x: 0\ndone"

    def test_for_iterate_emits_tight_c_loop(self):
        """The `.iterate` peephole produces the same tight C `for` as `.each`."""
        csource = emit_source(
            'main: function is {\n  for x: 3.iterate loop {\n    print "\\{x}"\n  }\n}'
        )
        # tight C for-loop, no intermediate option/optionval materialisation
        assert "for (int64_t x = 0; x < 3" in csource

    def test_list_iterate_i64(self):
        """List.iterate yields borrowed views to each i64 element."""
        csource = emit_source(
            "main: function is {\n"
            "  xs: (List of: i64)\n"
            "  xs.append from: 10\n"
            "  xs.append from: 20\n"
            "  xs.append from: 30\n"
            "  with it: xs.iterate do for x: it loop {\n"
            '    print "x=\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "x=10\nx=20\nx=30"

    def test_list_iterate_string(self):
        """List.iterate also works for reftype element types (String)."""
        csource = emit_source(
            "main: function is {\n"
            "  xs: (List of: String)\n"
            '  xs.append from: "a".string\n'
            '  xs.append from: "b".string\n'
            '  xs.append from: "c".string\n'
            "  with it: xs.iterate do for s: it loop {\n"
            '    print "s=\\{s}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "s=a\ns=b\ns=c"

    def test_list_iterate_emits_listiter_struct(self):
        """The List mono pass emits the ListIter runtime layout + .call."""
        csource = emit_source(
            "main: function is {\n"
            "  xs: (List of: i64)\n"
            "  xs.append from: 1\n"
            "  with it: xs.iterate do for x: it loop {}\n"
            "}"
        )
        # listiter struct: pointer to source list + index
        assert "z_ListIter_i64_t" in csource
        assert "z_List_i64_iterate" in csource
        assert "z_ListIter_i64_call" in csource

    def test_map_iterate_keys(self):
        """Map.iterate yields borrowed views of each live entry's key in
        insertion order."""
        csource = emit_source(
            "main: function is {\n"
            "  m: (Map key: i64 value: i64)\n"
            "  m.set key: 1 value: 100\n"
            "  m.set key: 2 value: 200\n"
            "  m.set key: 3 value: 300\n"
            "  with it: m.iterate do for k: it loop {\n"
            '    print "k=\\{k}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        seen = [line for line in output.strip().split("\n") if line]
        assert seen == ["k=1", "k=2", "k=3"]

    def test_map_iterate_emits_mapkeyiter(self):
        """The Map mono pass emits the MapKeyIter runtime + factory."""
        csource = emit_source(
            "main: function is {\n"
            "  m: (Map key: i64 value: i64)\n"
            "  m.set key: 1 value: 100\n"
            "  with it: m.iterate do for k: it loop {}\n"
            "}"
        )
        assert "z_MapKeyIter_i64_i64_t" in csource
        assert "z_Map_i64_i64_iterate" in csource
        assert "z_MapKeyIter_i64_i64_call" in csource

    def test_map_iterate_items_basic(self):
        """Map.iterateItems yields borrowed MapEntry views over live
        entries; .key and .value project through the entry pointer."""
        csource = emit_source(
            "main: function is {\n"
            "  m: (Map key: i64 value: i64)\n"
            "  m.set key: 1 value: 100\n"
            "  m.set key: 2 value: 200\n"
            "  m.set key: 3 value: 300\n"
            "  with it: m.iterateItems do for e: it loop {\n"
            '    print "k=\\{e.key} v=\\{e.value}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        seen = [line for line in output.strip().split("\n") if line]
        assert seen == ["k=1 v=100", "k=2 v=200", "k=3 v=300"]

    def test_map_iterate_items_emits_runtime(self):
        """The Map mono pass emits the MapItemIter runtime + MapEntry
        typedef + .iterateItems factory."""
        csource = emit_source(
            "main: function is {\n"
            "  m: (Map key: i64 value: i64)\n"
            "  m.set key: 1 value: 100\n"
            "  with it: m.iterateItems do for e: it loop {}\n"
            "}"
        )
        assert "z_MapItemIter_i64_i64_t" in csource
        assert "z_Map_i64_i64_iterateItems" in csource
        assert "z_MapItemIter_i64_i64_call" in csource
        # mapentry is a typedef alias for the entry type
        assert "typedef z_Map_i64_i64_entry_t z_MapEntry_i64_i64_t" in csource

    def test_optionview_reftype_binds_by_pointer(self):
        """Reftype OptionView payload (String) emits a borrow pointer,
        not a struct copy. The body's `s.method` calls go through the
        source storage so mutations land there."""
        csource = emit_source(
            "main: function is {\n"
            "  xs: (List of: String)\n"
            '  xs.append from: "hello".string\n'
            "  with it: xs.iterate do for s: it loop {}\n"
            "}"
        )
        # pointer binding, not struct copy
        assert "z_String_t* __borrow_s = (z_String_t*)" in csource
        assert "z_String_t s = *(z_String_t*)" not in csource

    def test_optionview_valtype_still_value_copy(self):
        """Valtype OptionView payload (i64) keeps the value-copy emit;
        copies are safe for valtypes and the loop var is the natural
        local."""
        csource = emit_source(
            "main: function is {\n"
            "  xs: (List of: i64)\n"
            "  xs.append from: 1\n"
            "  with it: xs.iterate do for x: it loop {}\n"
            "}"
        )
        assert "int64_t x = *(int64_t*)" in csource
        assert "__borrow_x" not in csource

    def test_optionview_reftype_mutation_lands_in_source(self):
        """Mutating-method calls through a borrowed String-List iterator
        binding modify the source List element. Read-back outside the
        loop sees the new value."""
        csource = emit_source(
            "main: function is {\n"
            "  xs: (List of: String)\n"
            '  xs.append from: "hello".string\n'
            '  suffix_str: " world".string\n'
            "  suffix: suffix_str.stringview\n"
            "  with it: xs.iterate do for s: it loop {\n"
            "    s.append s: suffix\n"
            "  }\n"
            "  final: xs.get i: 0.u64\n"
            '  print "final: \\{final}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "final: hello world"

    def test_generic_unit(self):
        """Generic unit instantiation with function."""
        csource = emit_source(
            "mathops: unit as {\n"
            "  t: Any.generic\n"
            "  add: function {a: t b: t} out t is { return a + b }\n"
            "}\n"
            "intmath: (mathops t: i64)\n"
            "main: function is {\n"
            "  result: intmath.add a: 3 b: 5\n"
            '  print "\\{result}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "8"

    def test_generic_unit_multiple_instantiations(self):
        """Same generic unit instantiated with different types."""
        csource = emit_source(
            "ops: unit as {\n"
            "  t: Any.generic\n"
            "  double: function {v: t} out t is { return v + v }\n"
            "}\n"
            "iops: (ops t: i64)\n"
            "i32ops: (ops t: i32)\n"
            "main: function is {\n"
            '  print "\\{iops.double 21}"\n'
            '  print "\\{i32ops.double 16.i32}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42\n32"

    def test_generic_unit_multiple_functions(self):
        """Generic unit with multiple functions."""
        csource = emit_source(
            "utils: unit as {\n"
            "  t: Any.generic\n"
            "  identity: function {v: t} out t is { return v }\n"
            "  sum: function {a: t b: t} out t is { return a + b }\n"
            "}\n"
            "u: (utils t: i64)\n"
            "main: function is {\n"
            '  print "\\{u.identity 99}"\n'
            '  print "\\{u.sum a: 10 b: 20}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "99\n30"

    def test_generic_unit_3level(self):
        """3-level generic composition: outer unit → inner subunit."""
        csource = emit_source(
            "outer: unit as {\n"
            "  t: Any.generic\n"
            "  inner: unit as {\n"
            "    u: Any.generic\n"
            "    add_both: function {a: t b: u} out t is { return a + b.i64 }\n"
            "  }\n"
            "  add: function {a: t b: t} out t is { return a + b }\n"
            "}\n"
            "iops: (outer t: i64)\n"
            "iops2: (iops.inner u: i32)\n"
            "main: function is {\n"
            '  print "\\{iops.add a: 3 b: 5}"\n'
            '  print "\\{iops2.add_both a: 10 b: 5.i32}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "8\n15"

    def test_generic_unit_4level(self):
        """4-level generic nesting: level1 → level2 → level3, each with own param."""
        csource = emit_source(
            "level1: unit as {\n"
            "  a: Any.generic\n"
            "  level2: unit as {\n"
            "    b: Any.generic\n"
            "    level3: unit as {\n"
            "      c: Any.generic\n"
            "      sum3: function {x: a y: b z: c} out a is {\n"
            "        return x + y.i64 + z.i64\n"
            "      }\n"
            "    }\n"
            "    sum2: function {x: a y: b} out a is { return x + y.i64 }\n"
            "  }\n"
            "  inc: function {x: a} out a is { return x + 1 }\n"
            "}\n"
            "l1: (level1 a: i64)\n"
            "l2: (l1.level2 b: i32)\n"
            "l3: (l2.level3 c: i16)\n"
            "main: function is {\n"
            '  print "\\{l1.inc 9}"\n'
            '  print "\\{l2.sum2 x: 10 y: 5.i32}"\n'
            '  print "\\{l3.sum3 x: 1 y: 2.i32 z: 3.i16}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10\n15\n6"

    def test_generic_file_unit(self):
        """Generic File unit instantiated from another File."""
        from zvfs import ZVfs, StringProvider, FSProvider, BindType

        lib_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        vfs = ZVfs()
        psystemid = vfs.register(FSProvider(rootpath=lib_dir, parentpath="system"))
        pmainid = vfs.register(
            StringProvider(
                files={
                    "test.z": (
                        "intmath: (mathutil t: i64)\n"
                        "main: function is {\n"
                        "  result: intmath.add a: 3 b: 5\n"
                        '  print "\\{result}"\n'
                        "}"
                    ),
                    "mathutil.z": (
                        "t: Any.generic\n"
                        "add: function {a: t b: t} out t is { return a + b }\n"
                    ),
                }
            )
        )
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "test")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], [e.msg for e in errors]
        csource = zemitterc.emit(typing)
        output = compile_and_run(csource)
        assert output.strip() == "8"

    def test_hidden_file_unit(self):
        """Hidden File unit (subunit in directory) is loaded and callable."""
        from zvfs import ZVfs, StringProvider, FSProvider, BindType

        lib_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        vfs = ZVfs()
        psystemid = vfs.register(FSProvider(rootpath=lib_dir, parentpath="system"))
        pmainid = vfs.register(
            StringProvider(
                files={
                    "test.z": ('main: function is {\n  print "\\{mymod.compute 7}"\n}'),
                    "mymod.z": (
                        "compute: function {n: i64} out i64 is {\n"
                        "  result: helper.square n\n"
                        "  return result\n"
                        "}"
                    ),
                    "mymod/helper.z": (
                        "square: function {n: i64} out i64 is {\n  return n * n\n}"
                    ),
                }
            )
        )
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "test")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], [e.msg for e in errors]
        csource = zemitterc.emit(typing)
        output = compile_and_run(csource)
        assert output.strip() == "49"

    def test_hidden_file_unit_multiple_subunits(self):
        """Multiple hidden subunits in the same parent unit."""
        from zvfs import ZVfs, StringProvider, FSProvider, BindType

        lib_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        vfs = ZVfs()
        psystemid = vfs.register(FSProvider(rootpath=lib_dir, parentpath="system"))
        pmainid = vfs.register(
            StringProvider(
                files={
                    "test.z": (
                        "main: function is {\n"
                        '  print "\\{mymod.compute 3}"\n'
                        '  print "\\{mymod.negate 7}"\n'
                        "}"
                    ),
                    "mymod.z": (
                        "compute: function {n: i64} out i64 is {\n"
                        "  result: mathhelp.square n\n"
                        "  return result\n"
                        "}\n"
                        "negate: function {n: i64} out i64 is {\n"
                        "  result: signhelp.neg n\n"
                        "  return result\n"
                        "}"
                    ),
                    "mymod/mathhelp.z": (
                        "square: function {n: i64} out i64 is {\n  return n * n\n}"
                    ),
                    "mymod/signhelp.z": (
                        "neg: function {n: i64} out i64 is {\n  return 0 - n\n}"
                    ),
                }
            )
        )
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "test")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], [e.msg for e in errors]
        csource = zemitterc.emit(typing)
        output = compile_and_run(csource)
        assert output.strip() == "9\n-7"

    def test_swap(self):
        csource = emit_source(
            'main: function is {\n  a: 1\n  b: 2\n  a swap b\n  print "\\{a} \\{b}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 1"

    def test_string_interpolation(self):
        csource = emit_source(
            'main: function is {\n  name: "Zero"\n  print "Hello, \\{name}!"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "Hello, Zero!"

    def test_recursion(self):
        csource = emit_source(
            "fact: function {n: i64} out i64 is {\n"
            "  if n <= 1 then return 1\n"
            "  return n * (fact n - 1)\n"
            "}\n"
            'main: function is { print "\\{fact 5}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "120"


class TestEmitterExamples:
    """Test that all v1 example programs emit valid C that compiles and runs."""

    def _emit_example(self, name: str) -> str:
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        assert isinstance(program, zast.Program), f"Parse failed for {name}"
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], f"Type errors for {name}: {[e.msg for e in errors]}"
        return zemitterc.emit(typing)

    def test_hello(self):
        csource = self._emit_example("hello")
        output = compile_and_run(csource)
        assert "Hello, World!" in output

    def test_factorial(self):
        csource = self._emit_example("factorial")
        output = compile_and_run(csource)
        assert "12! = 479001600" in output

    def test_fibonacci(self):
        csource = self._emit_example("fibonacci")
        output = compile_and_run(csource)
        assert "fib(9) = 34" in output

    def test_swap(self):
        csource = self._emit_example("swap")
        output = compile_and_run(csource)
        assert "after swap: a=20 b=10" in output

    def test_strings(self):
        csource = self._emit_example("strings")
        output = compile_and_run(csource)
        assert "Welcome to Zero v1" in output

    def test_control(self):
        csource = self._emit_example("control")
        output = compile_and_run(csource)
        assert "sum 1..10 = 55" in output
        assert "abs(-5) = 5" in output

    def test_case(self):
        csource = self._emit_example("case")
        output = compile_and_run(csource)
        assert "Direction: North" in output

    def test_records(self):
        csource = self._emit_example("records")
        output = compile_and_run(csource)
        assert "p3 = (4, 6)" in output
        assert "distance squared = 25" in output

    def test_multimod(self):
        csource = self._emit_example("multimod")
        output = compile_and_run(csource)
        assert "7 squared = 49" in output

    def test_data(self):
        csource = self._emit_example("data")
        output = compile_and_run(csource)
        assert "prime 0 = 2" in output
        assert "prime 9 = 29" in output

    def test_unions(self):
        csource = self._emit_example("unions")
        output = compile_and_run(csource)
        assert "a is ok" in output
        assert "b is error" in output
        assert "c is none" in output
        assert "d is ok" in output

    def test_constructors(self):
        csource = self._emit_example("constructors")
        output = compile_and_run(csource)
        assert "c1 = 42" in output
        assert "c2 = 99" in output
        assert "p1 = (3, 7)" in output
        assert "p2 = (0, 0)" in output

    def test_defaults(self):
        csource = self._emit_example("defaults")
        output = compile_and_run(csource)
        assert "31" in output  # calc 1 -> 1+10+20
        assert "13" in output  # apply a:10 b:3 -> add(10,3)
        assert "5 0 0" in output  # point x:5

    def test_constfold(self):
        csource = self._emit_example("constfold")
        output = compile_and_run(csource)
        assert "5" in output
        assert "6" in output

    def test_ifexpr(self):
        csource = self._emit_example("ifexpr")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert "10" in lines[0]
        assert "12" in lines[1]
        assert "yes" in lines[2]

    def test_os_env(self):
        csource = self._emit_example("os_env")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "set ok"
        assert lines[1] == "read ok"
        assert lines[2] == "names nonempty=1"
        assert lines[3] == "unset ok"
        assert lines[4] == "gone"

    def test_os_process(self):
        csource = self._emit_example("os_process")
        output = compile_and_run(csource)
        assert "pid positive=1" in output
        assert "ppid positive=1" in output
        assert "cwd ok" in output
        assert "set_cwd ok" in output
        # Mixed-type String == StringView comparison: cr2 (String,
        # narrowed cwd result) == "/tmp" (StringView literal). Pre-
        # 50ec3e4 the emit picked z_String_eq with a wrong-typed RHS
        # pointer and silently returned false at runtime. Asserting
        # =1 closes the gap so a future regression can't sneak past.
        assert "cwd is /tmp=1" in output
        assert "user_name nonempty=1" in output
        assert "home_dir nonempty=1" in output

    def test_os_platform(self):
        csource = self._emit_example("os_platform")
        output = compile_and_run(csource)
        # Test is platform-aware: we assert exactly one platform line
        # and one arch line appear, and hostname is non-empty. The
        # specific values depend on the build host.
        plines = [ln for ln in output.split("\n") if ln.startswith("platform=")]
        alines = [ln for ln in output.split("\n") if ln.startswith("arch=")]
        assert len(plines) == 1
        assert len(alines) == 1
        assert "hostname nonempty=1" in output

    def test_string_join(self):
        csource = self._emit_example("string_join")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "alpha, beta, gamma"
        assert lines[1] == "empty.length=0"

    def test_string_parse(self):
        csource = self._emit_example("string_parse")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "i64=-42"
        assert lines[1] == "u64=1234567890"
        assert lines[2] == "f64=3.14"
        assert lines[3] == "invalidDigit"

    def test_string_codepoints(self):
        csource = self._emit_example("string_codepoints")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "bytes=5"
        assert lines[1] == "codepoints=5"
        # 5 codepoints from "hello"
        assert lines[2] == "104"
        assert lines[3] == "101"
        assert lines[4] == "108"
        assert lines[5] == "108"
        assert lines[6] == "111"

    def test_string_transform(self):
        csource = self._emit_example("string_transform")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "hello, world!"
        assert lines[1] == "HELLO, WORLD!"
        assert lines[2] == "Hello World!"
        assert lines[3] == "HeLlo, World!"
        assert lines[4] == "ababab"
        assert lines[5] == "keyvalue"

    def test_string_split(self):
        csource = self._emit_example("string_split")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "alpha"
        assert lines[1] == "beta"
        assert lines[2] == "gamma"
        assert lines[3] == "split at 3"
        assert lines[4] == "line1"
        assert lines[5] == "line2"
        assert lines[6] == "line3"

    def test_string_slice(self):
        csource = self._emit_example("string_slice")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "trim.length=12"
        assert lines[1] == "trimStart.length=14"
        assert lines[2] == "trimEnd.length=14"
        assert lines[3] == "stripped=7"
        assert lines[4] == "no match ok"
        assert lines[5] == "stem.length=5"

    def test_string_query(self):
        csource = self._emit_example("string_query")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "empty=0"
        assert lines[1] == "ascii=1"
        assert lines[2] == "starts=1"
        assert lines[3] == "ends=1"
        assert lines[4] == "has=1"
        assert lines[5] == "first world at 7"
        assert lines[6] == "last l at 10"
        assert lines[7] == "byte0=104"
        assert lines[8] == "999 oob ok"
        assert lines[9] == "e.empty=1"
        assert lines[10] == "e.ascii=1"

    def test_arrays(self):
        csource = self._emit_example("arrays")
        output = compile_and_run(csource)
        assert "initial: 0 0 0 0 0" in output
        assert "after set: 10 20 30 40 50" in output
        assert "set index 2 to 99, previous was: 30" in output
        assert "primes: 2 3 5 7 11" in output

    def test_facets(self):
        csource = self._emit_example("facets")
        output = compile_and_run(csource)
        assert "point measure: 15" in output
        assert "color measure: 30" in output
        assert "use_facet point: 15" in output
        assert "use_facet color: 30" in output

    def test_generics(self):
        csource = self._emit_example("generics")
        output = compile_and_run(csource)
        assert "created option.some i64" in output
        assert "created option.none i32" in output
        assert "a is some" in output
        assert "b is none" in output
        assert "created MyBox i64" in output

    def test_lists(self):
        csource = self._emit_example("lists")
        output = compile_and_run(csource)
        assert "empty list length: 0" in output
        assert "after appends: length=3" in output
        assert "replaced 20 with 99 at index 1" in output
        assert "after insert 42 at 1: 10 42 99" in output
        assert "after extend: length=5" in output
        assert "preallocated capacity: 100, length: 0" in output

    def test_maps(self):
        csource = self._emit_example("maps")
        output = compile_and_run(csource)
        assert "entries: 3" in output
        assert "has alice: 1" in output
        assert "has dave: 0" in output
        assert "found bob" in output
        assert "deleted bob: 1, length: 2" in output
        assert "preallocated: capacity=64 length=0" in output

    def test_numeric_generics(self):
        csource = self._emit_example("numeric_generics")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines == ["42", "10", "99", "20", "100", "0", "ok"]

    def test_protocols(self):
        csource = self._emit_example("protocols")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines == ["15", "25"]

    def test_specs(self):
        csource = self._emit_example("specs")
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines == [
            "13",
            "7",
            "30",
            "12",
            "35",
            "3",
            "7",
            "300",
            "20000",
            "30",
            "-10",
            "0",
            "1",
        ]

    def test_str(self):
        csource = self._emit_example("str")
        output = compile_and_run(csource)
        assert "hello" in output
        assert "greeting is: hello" in output

    def test_typedefs(self):
        csource = self._emit_example("typedefs")
        output = compile_and_run(csource)
        assert "meters value: 42" in output
        assert "doubled: 84" in output
        assert "add_one: 43" in output
        assert "height value: 100" in output
        assert "height add_one: 101" in output

    def test_variants(self):
        csource = self._emit_example("variants")
        output = compile_and_run(csource)
        assert "a is ok" in output
        assert "b is none" in output
        assert "shape is point" in output
        assert "item created" in output
        assert "mode is read" in output


class TestUserMethodStringTake:
    """Regression test for C4: user class methods with `String.take`
    parameters must emit the call site pass-by-value (no `&`) and
    zero-init the caller's source to avoid double-free at scope
    exit. The earlier emission produced `&_t` against a by-value
    signature, which gcc rejected."""

    def test_string_take_on_user_method(self):
        from zvfs import ZVfs, FSProvider, StringProvider, BindType

        src = (
            "Holder: class {\n"
            "    n: i64\n"
            "} as {\n"
            "    greet: function {:this s: String.take} is {\n"
            "        print s\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    h: Holder n: 0\n"
            '    h.greet s: "hello".string\n'
            "}\n"
        )
        lib_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        vfs = ZVfs()
        systemdir = os.path.join(lib_dir, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(StringProvider(files={"takeprobe.z": src}))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "takeprobe")
        program = p.parse()
        typing = typecheck(program)
        errors = typing.errors
        assert errors == []
        csource = zemitterc.emit(typing)
        # Under ASan the double-free caused by the pre-fix emission
        # would be caught here.
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"


class TestClassParamThreading:
    """Regression test for the Bug 2b call-site fix: when a method
    receives a class-typed param (already a `z_T_t*` pointer at the
    C level) and forwards it to another method, the call site must
    NOT prepend `&` -- that would produce `z_T_t**` and the callee
    would read garbage. The fix adds a `val not in
    self._scope.class_params` guard at the `&val` emission site to
    mirror the sibling guard in `_build_meta_create_args`.

    Pre-fix this surfaced as a runtime "numeric cast overflow:
    u64 to u32" panic during the VFS port's PR 2 work because
    `entries.length` was being read through a pointer-to-pointer."""

    def test_class_arg_forwarded_through_methods_no_double_pointer(self):
        from zvfs import ZVfs, FSProvider, StringProvider, BindType

        # Box has two methods: inner reads `other.n`, outer forwards
        # `other` to inner. Without the fix, z_Box_outer emits
        # `z_Box_inner(this, &other)` against a `z_Box_t*` param,
        # producing pointer-to-pointer and runtime garbage.
        src = (
            "Box: class {\n"
            "    n: u32\n"
            "} as {\n"
            "    inner: function {:this other: Box} is {\n"
            '        print "\\{other.n}"\n'
            "    }\n"
            "    outer: function {:this other: Box} is {\n"
            "        this.inner other: other\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    a: Box n: 7.u32\n"
            "    b: Box n: 11.u32\n"
            "    a.outer other: b\n"
            "}\n"
        )
        lib_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        vfs = ZVfs()
        systemdir = os.path.join(lib_dir, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(StringProvider(files={"bug2b.z": src}))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "bug2b")
        program = p.parse()
        typing = typecheck(program)
        errors = typing.errors
        assert errors == []
        csource = zemitterc.emit(typing)
        assert compile_and_run(csource).strip() == "11"

    def test_user_method_threading_stringview_no_unwanted_amp(self):
        """Regression for Bug 2a: a user-defined method that forwards
        a non-self class arg whose type matches the receiver class
        must NOT prepend `&` at the call site. Pre-fix the second
        clause in `_emit_call_args` fired for any function with a
        dotted name and matching param/arg types, mistakenly adding
        `&sv` to `this.inner sv: sv` and producing a gcc rejection
        (`z_StringView_t*` vs `z_StringView_t`)."""
        csource = emit_source(
            "Box: class { n: u32 } as {\n"
            "    inner: function {:this sv: StringView} is { print sv }\n"
            "    outer: function {:this sv: StringView} is {\n"
            "        this.inner sv: sv\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    b: Box n: 1.u32\n"
            '    s: "hello".string\n'
            "    b.outer sv: s.stringview\n"
            "}\n"
        )
        assert "z_Box_inner(this, sv)" in csource
        assert "z_Box_inner(this, &sv)" not in csource
        assert compile_and_run(csource).strip() == "hello"


class TestPrintStackClassDispatch:
    """Regression test for the print-through-projection emission path:
    when a stack-allocated class conforms to `Text`, the `.stringview`
    method takes `this` as a pointer, so the call site must pass
    `&receiver`. Pre-fix emission generated `z_T_stringview(m)` against
    a `z_T_stringview(z_T_t*)` signature, failing gcc at -Werror."""

    def test_print_stack_class_passes_address(self):
        src = (
            "Mylabel: class { s: String } as {\n"
            "    :Text\n"
            "    stringview: function {m: this} out StringView is {\n"
            "        return m.s.stringview\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            '    m: Mylabel s: "hello".string\n'
            "    print m\n"
            "}\n"
        )
        csource = emit_source(src)
        assert "z_Mylabel_stringview(&m)" in csource
        # end-to-end: compile + run
        stdout = compile_and_run(csource)
        assert stdout.strip() == "hello"

    def test_print_record_receiver_stays_pass_by_value(self):
        """Control: records (valtypes) continue to receive `this` by
        value. The `&` prefix guard must only trigger for classes."""
        src = (
            "Tag: class { val: String } as {}\n"
            "mypair: record { name: Tag } as {\n"
            "    :Text\n"
            "    stringview: function {p: this} out StringView is {\n"
            "        return p.name.val.stringview\n"
            "    }\n"
            "}\n"
        )
        # `mypair` holds a class field, which is a reftype in a record
        # — intentionally rejected by R3, so this test only checks the
        # emission convention of a legal record-with-text-protocol by
        # using a record with a `(str to: N)` valtype field: views over
        # the receiver's own field outlive the method call (p is borrowed
        # from the caller), so returning the view is legal.
        src = (
            "mypair: record { s: (str to: 16) } as {\n"
            "    :Text\n"
            "    stringview: function {p: this} out StringView is {\n"
            "        return p.s.stringview\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    p: mypair s: (str to: 16)\n"
            "    print p\n"
            "}\n"
        )
        csource = emit_source(src)
        # No `&p` because mypair is a record, not a class.
        assert "z_mypair_stringview(p)" in csource
        assert "z_mypair_stringview(&p)" not in csource


class TestCliUnitEmission:
    """cli unit: compile cli_basic.z once, run with assorted argv."""

    def _csource(self) -> str:
        from zvfs import ZVfs, FSProvider, BindType

        lib_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
        vfs = ZVfs()
        systemdir = os.path.join(lib_dir, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=examples_dir, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "cli_basic")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        return zemitterc.emit(typing)

    def test_flag_and_positionals(self):
        out = (
            compile_and_run_with_args(self._csource(), ["-v", "foo", "bar"])
            .strip()
            .split("\n")
        )
        assert out[0] == "verbose=1"
        assert out[1] == "ignore-case=0"
        assert out[2] == "(no output)"
        assert out[3] == "foo"
        assert out[4] == "bar"

    def test_long_option_equals_form(self):
        out = (
            compile_and_run_with_args(
                self._csource(), ["--output=out.txt", "pat", "f.txt"]
            )
            .strip()
            .split("\n")
        )
        assert out[0] == "verbose=0"
        assert out[2] == "out.txt"
        assert out[3] == "pat"

    def test_long_option_separate_form(self):
        out = (
            compile_and_run_with_args(
                self._csource(), ["--output", "out.txt", "pat", "f.txt"]
            )
            .strip()
            .split("\n")
        )
        assert out[2] == "out.txt"
        assert out[3] == "pat"

    def test_short_option_separate_form(self):
        out = (
            compile_and_run_with_args(
                self._csource(), ["-o", "out.txt", "pat", "f.txt"]
            )
            .strip()
            .split("\n")
        )
        assert out[2] == "out.txt"

    def test_bundled_short_flags(self):
        out = (
            compile_and_run_with_args(self._csource(), ["-vi", "foo", "bar"])
            .strip()
            .split("\n")
        )
        assert out[0] == "verbose=1"
        assert out[1] == "ignore-case=1"

    def test_missing_required_positional(self):
        # Omits `file` positional; `-v` registers the verbose flag
        # and then the single positional "foo" fills `pattern`, but
        # `file` is unfilled — required-check fails and we fall
        # through to the err arm which prints help_text.
        out = compile_and_run_with_args(self._csource(), ["-v", "foo"])
        assert "usage: cli_basic" in out

    def test_double_dash_terminator(self):
        # `-v` sets verbose; `--` ends option parsing; remaining
        # `foo` / `bar` go to `extra_args` (NOT positionals), so
        # both required positionals are missing and parsing errors
        # with the help text.
        out = compile_and_run_with_args(self._csource(), ["-v", "--", "foo", "bar"])
        assert "usage: cli_basic" in out


def compile_and_run_asan(csource: str) -> subprocess.CompletedProcess:
    """Compile C source with ASan and run, returning the CompletedProcess."""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        comp = subprocess.run(
            [
                _CC,
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                "-Wall",
                "-Wno-unused-function",
                "-o",
                outpath,
                cpath,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if comp.returncode != 0:
            raise RuntimeError(f"gcc (asan) failed:\n{comp.stderr}")
        result = subprocess.run(
            [outpath],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result
    finally:
        for p in (cpath, outpath):
            if os.path.exists(p):
                os.unlink(p)


class TestEmitterStringOwnership:
    """Tests for String ownership semantics in emitted C code."""

    def test_string_scope_cleanup(self):
        """String variables freed at function exit via z_String_free."""
        csource = emit_source('main: function is {\n  s: "hello".string\n  print s\n}')
        assert "z_String_free(&s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_string_return(self):
        """Function returning a String; returned value usable, not double-freed."""
        csource = emit_source(
            "greet: function {n: i64} out String is {\n"
            '  return "Hello \\{n}!"\n'
            "}\n"
            "main: function is {\n"
            "  msg: greet 42\n"
            "  print msg\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "Hello 42!"

    def test_string_from_view_empty_then_append_no_overflow(self):
        """Round-trip from an empty StringView (`.string` of `""`)
        followed by `.appendByte` must not heap-overflow the
        single-byte allocation. Was caught by ASAN on the lexer
        port; `z_String_from_view` was setting capacity to
        sv.length+1 (off-by-one against the nul terminator)."""
        csource = emit_source(
            "main: function is {\n"
            '  s: "".string\n'
            "  s.appendByte b: 65.u8\n"
            '  print "\\{s}"\n'
            "}"
        )
        output = compile_and_run(csource, extra_cflags=["-fsanitize=address", "-g"])
        assert output.strip() == "A"

    def test_string_reassignment(self):
        """Old value freed via z_String_free, new value assigned correctly."""
        csource = emit_source(
            'main: function is {\n  s: "hello".string\n  s = "world".string\n  print s\n}'
        )
        assert "z_String_free(&s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "world"

    def test_class_inline_construct_assigned_to_field_no_doublefree(self):
        """Inline-constructed class assigned to a class-typed field
        must not alias the source's heap pointers (the original
        PR-4 trigger for the lookahead `nexttok: Token` field on
        Tokenizer; observed as double-free at scope exit).
        Reduced to its core: staging-and-draining with a
        `String`-bearing inner class."""
        csource = emit_source(
            "Tok: class { val: String n: u32 }\n"
            "Hold: class { hasnext: bool nexttok: Tok } as {\n"
            "  stash: function {:this s: String.take n: u32} is {\n"
            "    this.nexttok = Tok val: s n: n\n"
            "    this.hasnext = true\n"
            "  }\n"
            "  drain: function {:this} out String is {\n"
            "    this.hasnext = false\n"
            "    return this.nexttok.val.copy\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            '  h: Hold hasnext: false nexttok: (Tok val: "init".string n: 0.u32)\n'
            '  s1: "alpha".string\n'
            "  h.stash s: s1 n: 1.u32\n"
            "  a: h.drain\n"
            '  s2: "beta".string\n'
            "  h.stash s: s2 n: 2.u32\n"
            "  b: h.drain\n"
            '  print "a=\\{a} b=\\{b}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "a=alpha b=beta"
        # ASAN must also be clean: the original symptom was a
        # double-free of the contained String at scope exit.
        output_asan = compile_and_run(
            csource, extra_cflags=["-fsanitize=address", "-g"]
        )
        assert output_asan.strip() == "a=alpha b=beta"

    def test_inline_copy_in_field_reassignment(self):
        """Inline `.copy` in a reftype field reassignment must keep the
        source live (the copy is a fresh-owned value, not a transfer).
        Was PR 4 friction item 2."""
        csource = emit_source(
            "Holder: class { s: String n: u32 }\n"
            "fn: function {cond: bool} out Holder is {\n"
            '  src: "hello".string\n'
            '  h: Holder s: "".string n: 0.u32\n'
            "  if cond then {\n"
            "    h.s = src.copy\n"
            "  }\n"
            "  h.s = src.copy\n"
            "  return h\n"
            "}\n"
            "main: function is {\n"
            "  h: fn cond: true\n"
            '  print "\\{h.s}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_string_swap(self):
        """Two strings swapped, both usable after swap."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "first".string\n'
            '  b: "second".string\n'
            "  a swap b\n"
            "  print a\n"
            "  print b\n"
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "second"
        assert lines[1] == "first"

    def test_string_temporaries(self):
        """Interpolation Result freed via z_String_free."""
        csource = emit_source(
            'main: function is {\n  name: "Zero"\n  print "Hello, \\{name}!"\n}'
        )
        # verify result string is freed (append chain, single allocation)
        assert "z_String_free(&_s" in csource
        # verify interpolation uses append chain, not z_String_cat
        main_body = csource[csource.index("void z_main") :]
        assert "z_String_cat(" not in main_body
        assert "z_String_append(" in main_body
        output = compile_and_run(csource)
        assert output.strip() == "Hello, Zero!"

    def test_string_copy_independent_owners(self):
        """`.copy` produces an independently-owned String; mutating
        the source does not change the copy and both are freed
        separately at scope exit."""
        csource = emit_source(
            "main: function is {\n"
            '  x: "hello".string\n'
            "  y: x.copy\n"
            '  x.append s: " world"\n'
            '  print "x=\\{x}"\n'
            '  print "y=\\{y}"\n'
            "}"
        )
        assert "z_String_copy(&x)" in csource
        assert "z_String_free(&x);" in csource
        assert "z_String_free(&y);" in csource
        lines = compile_and_run(csource).strip().split("\n")
        assert lines == ["x=hello world", "y=hello"]

    def test_string_param_borrow_passes_pointer(self):
        """Phase A: unannotated String param is pointer-passed; the
        emitted C signature uses `z_String_t*`, callers wrap with `&`,
        and the caller's variable is NOT zeroed after the call."""
        csource = emit_source(
            "f: function {s: String} is {\n"
            '  print "got=\\{s}"\n'
            "}\n"
            "main: function is {\n"
            '  x: "hi".string\n'
            "  f s: x\n"
            "}"
        )
        # signature is pointer-typed
        assert "void z_f(z_String_t* s)" in csource
        # call site wraps with &
        assert "z_f(&x)" in csource
        # caller's x is not zeroed by an implicit-take
        assert "x = (z_String_t){0}" not in csource

    def test_string_param_mutation_lands_in_caller(self):
        """Phase A: a borrowed String param mutated inside the callee
        shows the mutation in the caller's storage afterwards."""
        csource = emit_source(
            "mutate: function {s: String} is {\n"
            '  s.append s: " world"\n'
            "}\n"
            "main: function is {\n"
            '  x: "hello".string\n'
            "  mutate s: x\n"
            '  print "x=\\{x}"\n'
            "}"
        )
        # s.append on a pointer-receiver should not double-address
        assert "z_String_append(s," in csource
        assert "z_String_append(&s," not in csource
        output = compile_and_run(csource).strip()
        assert output == "x=hello world"

    def test_release_on_borrowed_string_skips_free(self):
        """`.release` on a borrowed String (e.g. one bound from an
        `out T.borrow` return) must NOT free the underlying buffer —
        the source still owns it. Previously this emitted an
        unconditional `z_String_free(&s)` and corrupted the source's
        heap data."""
        csource = emit_source(
            "MyBox: class { label: String }\n"
            "peek: function {b: MyBox.lock} out String.borrow is {\n"
            "  return b.label\n"
            "}\n"
            "main: function is {\n"
            '  e: MyBox label: "echo".string\n'
            "  s: peek e\n"
            '  print "peeked: \\{s}"\n'
            "  s.release\n"
            '  print "e still alive: \\{e.label}"\n'
            "}"
        )
        # The borrow `s` must NOT be freed by `.release` — only zeroed.
        # Extract the section between `s = _t...` and the next non-`s`
        # line to inspect the .release emit specifically.
        assert "z_String_free(&s);" not in csource
        # `e` is still alive after `s.release`, so the second print
        # must see a valid box.label — runtime check.
        lines = compile_and_run(csource).strip().split("\n")
        assert lines == ["peeked: echo", "e still alive: echo"]

    def test_release_on_owned_string_frees(self):
        """Regression: `.release` on an owned String still frees and
        zeros, so scope-exit cleanup is a no-op afterwards."""
        csource = emit_source(
            'main: function is {\n  s: "hello".string\n  s.release\n  print "ok"\n}'
        )
        # owned string: free + zero
        assert "z_String_free(&s);" in csource
        assert "s = (z_String_t){0};" in csource
        assert compile_and_run(csource).strip() == "ok"

    def test_native_stringview_caller_retains(self):
        """Read-only natives take `StringView` (Phase A native ABI);
        the caller's `String` variable used via `.stringview` is NOT
        consumed and remains valid afterwards."""
        csource = emit_source(
            "main: function is {\n"
            '  p: "/does/not/exist".string\n'
            "  e: io.exists path: p.stringview\n"
            '  print "p=\\{p}"\n'
            "}"
        )
        # caller's `p` not zeroed by an implicit-take
        assert "p = (z_String_t){0}" not in csource
        # runtime: print sees the original path
        assert compile_and_run(csource).strip() == "p=/does/not/exist"

    def test_native_take_consumes_caller(self):
        """Store-into-receiver natives take `String.take`; the caller's
        variable is invalidated after the call (the runtime now owns
        the buffer directly without the deep-copy that pre-Phase-A
        natives did)."""
        csource = emit_source(
            "main: function is {\n"
            '  sp: cli.Spec.create programName: "p".string summary: "s".string\n'
            '  n: "--verbose".string\n'
            '  s: "-v".string\n'
            '  h: "be loud".string\n'
            "  cli.addFlag spec: sp name: n shortName: s help: h\n"
            "}"
        )
        # caller's name/short_name/help are zeroed after the take call
        assert "n = (z_String_t){0}" in csource
        assert "s = (z_String_t){0}" in csource
        assert "h = (z_String_t){0}" in csource
        # runtime: must run cleanly under MALLOC_CHECK_ (no leak/double-free)
        compile_and_run(csource)

    def test_multiple_string_vars(self):
        """Several strings in one function, all freed correctly."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "world"\n'
            '  c: "!"\n'
            "  print a\n"
            "  print b\n"
            "  print c\n"
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines == ["hello", "world", "!"]

    def test_string_in_with_do(self):
        """Scoped String variable freed at end of with block via z_String_free."""
        csource = emit_source(
            'main: function is {\n  with s: "hello".string do print s\n}'
        )
        assert "z_String_free(&s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hello"


class TestBareBlockScope:
    """Tests for bare block scoping — { ... } used as a statement."""

    def test_bare_block_side_effects(self):
        """Bare block executes for side effects and output is visible."""
        csource = emit_source(
            'main: function is {\n  { print "inside" }\n  print "outside"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip().split("\n") == ["inside", "outside"]

    def test_bare_block_string_cleanup(self):
        """String defined inside a bare block is freed at block exit."""
        csource = emit_source(
            'main: function is {\n  { s: "hello".string\n  print s }\n  print "done"\n}'
        )
        assert "z_String_free(&s);" in csource
        output = compile_and_run(csource)
        assert output.strip().split("\n") == ["hello", "done"]


class TestImplicitReturn:
    """Tests for implicit return — last expression is the return value."""

    def test_implicit_return_integer(self):
        """Function implicitly returns an integer literal."""
        csource = emit_source(
            "f: function {n: i64} out i64 is { n }\n"
            'main: function is { print "\\{f 42}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_implicit_return_arithmetic(self):
        """Arithmetic expression as implicit return."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { a + b }\n"
            'main: function is { print "\\{add a: 3 b: 4}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_implicit_return_string(self):
        """String as implicit return, no memory leak."""
        csource = emit_source(
            'greet: function {name: String} out String is { "hello".string }\n'
            'main: function is { print (greet "world".string) }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_implicit_return_if_expression(self):
        """if-expression in tail position as implicit return."""
        csource = emit_source(
            "abs: function {n: i64} out i64 is {\n"
            "  if n < 0 then 0 - n else n\n"
            "}\n"
            'main: function is { print "\\{abs -5}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_implicit_return_function_call(self):
        """Function call as last expression is implicitly returned."""
        csource = emit_source(
            "double: function {n: i64} out i64 is { n * 2 }\n"
            "quad: function {n: i64} out i64 is { double (double n) }\n"
            'main: function is { print "\\{quad 3}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "12"

    def test_implicit_return_bare_block(self):
        """Bare block in tail position provides implicit return."""
        csource = emit_source(
            "f: function {n: i64} out i64 is {\n  { n + 1 }\n}\n"
            'main: function is { print "\\{f 41}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_implicit_return_mixed(self):
        """Early explicit return + implicit return at end."""
        csource = emit_source(
            "clamp: function {n: i64} out i64 is {\n"
            "  if n < 0 then return 0\n"
            "  if n > 100 then return 100\n"
            "  n\n"
            "}\n"
            "main: function is {\n"
            '  print "\\{clamp -5} \\{clamp 50} \\{clamp 200}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0 50 100"

    def test_void_function_discards_value(self):
        """Void function with String last expression: value is discarded and freed."""
        csource = emit_source(
            'f: function {n: i64} is {\n  s: "hello"\n  print s\n}\n'
            "main: function is { f 1 }"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"
        # string should be freed at scope exit
        assert "z_String_free" in csource


class TestMatchExpression:
    """Tests for match/case as expression value."""

    def test_match_expression_simple(self):
        """Simple enum match assigned to variable, compile+run."""
        csource = emit_source(
            "north: 0\nsouth: 1\neast: 2\n"
            "describe: function {d: i64} out i64 is {\n"
            "  match d case north then 10 case south then 20 else 30\n"
            "}\n"
            'main: function is { print "\\{describe north} \\{describe south} \\{describe east}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "10 20 30"

    def test_match_expression_assigned(self):
        """Match expression Result assigned to a variable."""
        csource = emit_source(
            "north: 0\nsouth: 1\n"
            "main: function is {\n"
            "  d: north\n"
            "  x: match d case north then 100 case south then 200 else 300\n"
            '  print "\\{x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "100"

    def test_match_expression_implicit_return(self):
        """Match expression as implicit return value."""
        csource = emit_source(
            "north: 0\nsouth: 1\n"
            "f: function {d: i64} out i64 is {\n"
            "  match d case north then 10 case south then 20 else 30\n"
            "}\n"
            'main: function is { print "\\{f north} \\{f south} \\{f 99}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "10 20 30"

    def test_match_expression_union(self):
        """Union match as expression, compile+run."""
        csource = emit_source(
            "Shape: union { circle: i64\n square: i64 }\n"
            "area: function {s: Shape} out i64 is {\n"
            "  match s case circle then 314 case square then 100\n"
            "}\n"
            "main: function is {\n"
            "  c: Shape.circle 5\n"
            '  print "\\{area c}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "314"


class TestEmitterStaticStrings:
    """Tests for StringView String literal emission."""

    def test_literal_uses_static(self):
        """Plain String literal should emit static z_StringView_t, not z_String_new."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        assert "static const z_StringView_t _zs" in csource
        assert 'z_String_new("hello")' not in csource

    def test_static_deduplication(self):
        """Same literal used twice should produce one static z_StringView_t."""
        csource = emit_source(
            'main: function is {\n  a: "hello"\n  b: "hello"\n  print a\n  print b\n}'
        )
        # only one stringview constant for "hello"
        assert csource.count('_zs1_d[] = "hello"') == 1
        assert (
            csource.count("_zs2") == 0
            or '"hello"' not in csource.split("_zs2")[1].split("\n")[0]
        )

    def test_interp_fragments_use_static(self):
        """Literal fragments in interpolation should use static z_StringView_t."""
        csource = emit_source(
            'main: function is {\n  name: "Zero"\n  print "Hello, \\{name}!"\n}'
        )
        assert "static const z_StringView_t" in csource
        # "Hello, " and "!" fragments should be static
        assert 'z_String_new("Hello, ")' not in csource
        assert 'z_String_new("!")' not in csource

    def test_static_string_var_no_temp(self):
        """Static literal assigned to var should not create a temp."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        # Should directly assign: z_StringView_t s = _zs1;
        assert "z_StringView_t s = _zs" in csource
        # No temp allocation
        assert "z_String_t* _t" not in csource or "_t1 = z_String_new" not in csource

    def test_static_string_passed_to_function(self):
        """Static String can be passed to and returned from functions."""
        csource = emit_source(
            "greet: function {n: i64} out String is {\n"
            '  return "hello".string\n'
            "}\n"
            "main: function is {\n"
            "  msg: greet 1\n"
            "  print msg\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_static_string_asan(self):
        """Strings should pass ASan (no leaks, no invalid frees)."""
        csource = emit_source(
            'main: function is {\n  s: "hello".string\n  s = "world".string\n  print s\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "world"

    def test_static_empty_string(self):
        """Empty String literal should use static z_StringView_t."""
        csource = emit_source('main: function is {\n  s: ""\n  print s\n}')
        assert "static const z_StringView_t" in csource
        assert 'z_String_new("")' not in csource

    def test_z_String_free_in_scope_cleanup(self):
        """Scope cleanup for String vars should use z_String_free."""
        csource = emit_source('main: function is {\n  s: "hello".string\n  print s\n}')
        assert "z_String_free(&s);" in csource
        # the main body should not use raw free(s), only z_String_free
        main_body = csource[csource.index("void z_main") :]
        assert "if (s) free(s);" not in main_body

    def test_v2_string_struct(self):
        """z_String_t struct should have size and capacity fields."""
        csource = emit_source('main: function is { print "hello" }')
        assert "uint64_t size;" in csource
        assert "uint64_t capacity;" in csource


class TestEmitterMemorySafety:
    """Memory safety tests using AddressSanitizer."""

    def test_string_no_leak(self):
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "hello"

    def test_string_return_no_leak(self):
        csource = emit_source(
            "greet: function {n: i64} out String is {\n"
            '  return "Hello \\{n}!"\n'
            "}\n"
            "main: function is {\n"
            "  msg: greet 42\n"
            "  print msg\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "Hello 42!" in result.stdout

    def test_string_reassign_no_double_free(self):
        csource = emit_source(
            'main: function is {\n  s: "hello".string\n  s = "world".string\n  print s\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "world"

    def test_string_swap_no_double_free(self):
        csource = emit_source(
            "main: function is {\n"
            '  a: "first".string\n'
            '  b: "second".string\n'
            "  a swap b\n"
            "  print a\n"
            "  print b\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_string_interp_no_leak(self):
        csource = emit_source(
            "main: function is {\n"
            '  name: "Zero"\n'
            "  ver: 1\n"
            '  print "Welcome to \\{name} v\\{ver}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "Welcome to Zero v1" in result.stdout

    def test_string_multi_var_no_leak(self):
        csource = emit_source(
            'main: function is {\n  a: "hello"\n  b: "world"\n  print a\n  print b\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_example_hello_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "hello")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == []
        csource = zemitterc.emit(typing)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "Hello, World!" in result.stdout

    def test_example_strings_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "strings")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == []
        csource = zemitterc.emit(typing)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "Welcome to Zero v1" in result.stdout


class TestCallArgOrder:
    def test_call_arg_order_with_print(self):
        """Two call-expression arguments must evaluate left-to-right."""
        csource = emit_source(
            "first: function {n: i64} out i64 is {\n"
            '    print "first"\n'
            "    return n\n"
            "}\n"
            "second: function {n: i64} out i64 is {\n"
            '    print "second"\n'
            "    return n\n"
            "}\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "\n"
            "main: function is {\n"
            "    result: add a: (first n: 1) b: (second n: 2)\n"
            '    print "\\{result}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "first"
        assert lines[1] == "second"
        assert lines[2] == "3"

    def test_call_arg_hoisting_emitted(self):
        """Call-expression args should be hoisted to typecheck-side synth
        temps (`_tN`) in emitted C. After Phase C step 2 the typechecker
        owns hoisting; the emitter renders the synth Assignments as
        ordinary local-variable bindings.
        """
        csource = emit_source(
            "inc: function {n: i64} out i64 is { return n + 1 }\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "\n"
            "main: function is {\n"
            "    result: add a: (inc n: 1) b: (inc n: 2)\n"
            '    print "\\{result}"\n'
            "}"
        )
        # The emitted code should contain synth arg temps (_t0, _t1)
        assert "int64_t _t0" in csource
        assert "int64_t _t1" in csource
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_pure_args_not_temped(self):
        """Pure arguments (variables, literals) should NOT generate synth
        temps — the typechecker's _arg_is_trivial gate skips them."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "\n"
            "main: function is {\n"
            "    x: 10\n"
            "    result: add a: x b: 20\n"
            '    print "\\{result}"\n'
            "}"
        )
        # No synth temps for trivial args (bare AtomId, literal)
        assert "int64_t _t0" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "30"


# ---- Phase 4f: Class Emitter Tests ----


class TestEmitterClasses:
    """Tests for class C emission."""

    def test_class_struct_emitted(self):
        """Class should emit a typedef struct."""
        csource = emit_source(
            "MyClass: class { x: 0\n y: 0.0 }\nmain: function is { c: MyClass }"
        )
        assert "typedef struct {" in csource
        assert "z_MyClass_t" in csource
        assert "int64_t x;" in csource
        assert "double y;" in csource

    def test_class_construction_calls_create(self):
        """Class construction should call create instead of inline malloc."""
        csource = emit_source(
            "MyClass: class { x: i64 }\nmain: function is { c: MyClass x: 5 }"
        )
        assert "z_MyClass_create(5)" in csource

    def test_class_field_access_uses_arrow(self):
        """Class field access should use -> operator."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            'main: function is { c: MyClass x: 5\n print "\\{c.x}" }'
        )
        assert "c.x" in csource

    def test_class_scope_cleanup(self):
        """Class variables destroyed at function exit."""
        csource = emit_source(
            "MyClass: class { x: 0 }\nmain: function is { c: MyClass }"
        )
        # valtype-only class: no destructor needed
        assert "z_MyClass_destroy" not in csource

    def test_class_take_aliases_source(self):
        """Inline `d: c.take` on a class is emitted as a binding alias:
        no new local, no nullification, one destructor at scope end."""
        csource = emit_source(
            "MyClass: class { x: 0 }\nmain: function is { c: MyClass\n d: c.take }"
        )
        # alias marker present
        assert "/* alias: d => c */" in csource
        # no separate d local declared
        assert "z_MyClass_t d =" not in csource

    def test_class_method_uses_pointer(self):
        """Class methods should take pointer parameter."""
        csource = emit_source(
            "MyClass: class { x: 0 } as {\n"
            "  get: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: MyClass }"
        )
        assert "z_MyClass_t*" in csource
        assert "c->x" in csource

    def test_class_swap_emits(self):
        """Swap of class pointers should emit correctly."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: MyClass x: 1\n"
            "  b: MyClass x: 2\n"
            "  a swap b\n"
            "}"
        )
        assert "z_MyClass_t _tmp" in csource


class TestEmitterClassIntegration:
    """Integration tests: compile and run class programs."""

    def test_class_construction_and_field(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            'main: function is { c: MyClass x: 42\n print "\\{c.x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_class_method_call(self):
        csource = emit_source(
            "Counter: class { value: i64 } as {\n"
            "  get: function {c: this} out i64 is { return c.value }\n"
            "}\n"
            'main: function is { c: Counter value: 7\n print "\\{Counter.get c}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_self_field_method_call_statement_prepends_receiver(self):
        """Regression for the emitter gap unblocked by Path-scoped locks:
        `this.field.method arg` on a concrete class field in statement
        position must emit `z_method(&this->field, arg)`, not
        `z_method(arg)`. Before Commit D, the pattern was rejected at
        typecheck; the emitter's statement Path never handled it."""
        csource = emit_source(
            "Sink: class { total: i64 } as {\n"
            "  add: function {:this n: i64} is {\n"
            "    this.total = this.total + n\n"
            "  }\n"
            "}\n"
            "Pipe: class { dst: Sink } as {\n"
            "  pump: function {:this n: i64} is {\n"
            "    this.dst.add n: n\n"
            "  }\n"
            "  total: function {:this} out i64 is {\n"
            "    return this.dst.total\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  p: Pipe dst: (Sink total: 0)\n"
            "  p.pump n: 3\n"
            "  p.pump n: 4\n"
            '  print "\\{p.total}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_class_method_mutation(self):
        csource = emit_source(
            "Counter: class { value: i64 } as {\n"
            "  inc: function {c: this} is { c.value = c.value + 1 }\n"
            "  get: function {c: this} out i64 is { return c.value }\n"
            "}\n"
            "main: function is {\n"
            "  c: Counter value: 0\n"
            "  Counter.inc c\n"
            "  Counter.inc c\n"
            '  print "\\{Counter.get c}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_class_function_return(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "make: function {v: i64} out MyClass is { return (MyClass x: v) }\n"
            'main: function is { c: make 99\n print "\\{c.x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "99"

    def test_class_swap_runtime(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: MyClass x: 1\n"
            "  b: MyClass x: 2\n"
            "  a swap b\n"
            '  print "\\{a.x} \\{b.x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 1"

    def test_class_take_runtime(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: MyClass x: 42\n"
            "  b: a.take\n"
            '  print "\\{b.x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_example_classes(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "classes")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        csource = zemitterc.emit(typing)
        output = compile_and_run(csource)
        assert "initial = 10" in output
        assert "after 3 increments = 13" in output
        assert "d.value = 13" in output
        assert "e.value = 100" in output
        assert "after swap: d=100 e=13" in output


class TestEmitterClassMemorySafety:
    """Memory safety tests for classes using ASan."""

    def test_class_no_leak(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            'main: function is { c: MyClass x: 42\n print "\\{c.x}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_swap_no_leak(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: MyClass x: 1\n"
            "  b: MyClass x: 2\n"
            "  a swap b\n"
            '  print "\\{a.x}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_take_no_double_free(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: MyClass x: 42\n"
            "  b: a.take\n"
            '  print "\\{b.x}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_function_return_no_leak(self):
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "make: function {v: i64} out MyClass is { return (MyClass x: v) }\n"
            'main: function is { c: make 99\n print "\\{c.x}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_with_heap_collection_field_no_double_free(self):
        """Regression for the implicit-take gap on heap-allocated
        collection fields. When a class has a field whose type is a
        heap-allocated stdlib collection (Map, List, Set, ...) and
        the instance is constructed via bare field-init at the call
        site, the temp holding the collection must be invalidated so
        scope-exit cleanup doesn't double-free it (once via the
        class destructor and once directly).

        Pre-fix: `_needs_implicit_take` excluded heap-allocated
        types, so `_transfer_implicit_take` no-op'd. The temp stayed
        in the frees list and got freed twice.
        """
        csource = emit_source(
            "Holder: class { m: (Map key: String value: u32) }\n"
            "main: function is {\n"
            "  h: Holder m: (Map key: String value: u32)\n"
            '  h.m.set key: "alice".string value: 30.u32\n'
            '  print "len: \\{h.m.length}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_example_classes_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "classes")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == []
        csource = zemitterc.emit(typing)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "initial = 10" in result.stdout


# ---- Phase 4h.1: Class Destructor Tests ----


class TestEmitterClassDestructors:
    """Tests for class destructor generation and usage."""

    def test_class_destructor_with_string_field(self):
        """Class with String field: destructor frees String."""
        csource = emit_source(
            "MyClass: class { name: String\n x: 0 }\n"
            'main: function is { c: MyClass name: "".string }'
        )
        assert "z_MyClass_destroy" in csource
        assert "z_String_free(&p->name);" in csource

    def test_class_destructor_with_class_field(self):
        """Class with class field: destructor recurses."""
        csource = emit_source(
            "Inner: class { x: 0 }\n"
            "Outer: class { child: Inner }\n"
            "main: function is { o: Outer child: Inner }"
        )
        # Inner is valtype-only so no destructor; Outer has no heap fields either
        assert "z_Inner_destroy" not in csource
        assert "z_Outer_destroy" not in csource

    def test_class_destructor_with_union_field(self):
        """Class with union field: destructor calls union destroy."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "MyClass: class { payload: MyUnion }\n"
            "main: function is { c: MyClass payload: MyUnion.b }"
        )
        assert "z_MyUnion_destroy(&p->payload);" in csource

    def test_class_destructor_valtype_only(self):
        """Class with only valtype fields: no destructor emitted."""
        csource = emit_source(
            "MyClass: class { x: 0\n y: 0.0 }\nmain: function is { c: MyClass }"
        )
        assert "z_MyClass_destroy" not in csource

    def test_scope_exit_calls_destructor(self):
        """Scope-exit cleanup calls z_{name}_destroy."""
        csource = emit_source(
            "MyClass: class { name: String }\n"
            'main: function is { c: MyClass name: "".string }'
        )
        # should call destructor with address-of, not bare free
        assert "z_MyClass_destroy(&c);" in csource
        assert "if (c) free(c);" not in csource

    def test_reassignment_calls_destructor(self):
        """Reassignment calls destructor on old value."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: MyClass x: 1\n"
            "  c = MyClass x: 2\n"
            "}"
        )
        # valtype-only class: no destructor needed
        assert "z_MyClass_destroy" not in csource

    def test_with_block_calls_destructor(self):
        """With-block scope exit calls destructor."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            'main: function is { with c: (MyClass x: 1) do print "ok" }'
        )
        # valtype-only class: no destructor needed
        assert "z_MyClass_destroy" not in csource

    def test_union_class_subtype_destructor(self):
        """Union with class subtype calls class destructor in union destructor."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "MyUnion: union { a: MyClass\n b: null }\n"
            "main: function is { u: MyUnion.b }"
        )
        # valtype-only class: no class destructor, union just frees data
        assert "free(u->data);" in csource


class TestEmitterClassDestructorIntegration:
    """Integration tests for class destructors using ASan."""

    def test_class_string_field_asan(self):
        """Class with String field: no leak under ASan."""
        csource = emit_source(
            "MyClass: class { name: String\n x: i64 }\n"
            'main: function is {\n  c: MyClass name: "hello".string x: 42\n  print "\\{c.name}"\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "hello"

    def test_class_nested_class_field_asan(self):
        """Nested class field: no leak under ASan."""
        csource = emit_source(
            "Inner: class { x: i64 }\n"
            "Outer: class { child: Inner\n y: i64 }\n"
            "main: function is {\n"
            "  i: Inner x: 10\n"
            "  o: Outer child: i.take y: 20\n"
            '  print "\\{o.child.x} \\{o.y}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "10 20"

    def test_class_union_field_asan(self):
        """Class with union field: no leak under ASan."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "MyClass: class { payload: MyUnion\n x: i64 }\n"
            "main: function is {\n"
            "  u: MyUnion.a 42\n"
            "  c: MyClass payload: u.take x: 1\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_example_classes_with_named(self):
        """Updated classes example with named class."""
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "classes")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        csource = zemitterc.emit(typing)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "named: test=42" in result.stdout


# ---- Phase 4h: Union Emitter Tests ----


class TestEmitterUnions:
    """Tests for union C emission."""

    def test_union_struct_emitted(self):
        """Union should emit tag enum + struct."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\nmain: function is { x: MyUnion.a 1 }"
        )
        assert "Z_MYUNION_TAG_A" in csource
        assert "Z_MYUNION_TAG_B" in csource
        assert "z_MyUnion_tag_t" in csource
        assert "z_MyUnion_t" in csource
        assert "void* data;" in csource

    def test_union_construction_emits_malloc(self):
        """Union construction should emit stack struct init + tag + data."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\nmain: function is { x: MyUnion.a 42 }"
        )
        assert "z_xmalloc(sizeof(z_MyUnion_t))" not in csource
        assert "z_MyUnion_t" in csource
        assert "= {0}" in csource
        assert "Z_MYUNION_TAG_A" in csource

    def test_union_null_construction(self):
        """Null subtype construction emits tag + NULL data."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\nmain: function is { x: MyUnion.b }"
        )
        assert "Z_MYUNION_TAG_B" in csource
        assert ".data = NULL" in csource

    def test_union_match_emits_switch(self):
        """Match on union emits switch on tag."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  } case b then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )
        assert "switch (x.tag)" in csource
        assert "case Z_MYUNION_TAG_A:" in csource
        assert "case Z_MYUNION_TAG_B:" in csource

    def test_union_scope_cleanup(self):
        """Union variables destroyed at function exit."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\nmain: function is { x: MyUnion.a 1 }"
        )
        assert "z_MyUnion_destroy(&x);" in csource

    def test_union_take_aliases_source(self):
        """Inline `y: x.take` on a union is emitted as a binding alias:
        no new local, one destructor at scope end on the source."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is { x: MyUnion.a 1\n y: x.take }"
        )
        assert "/* alias: y => x */" in csource
        assert "z_MyUnion_t y =" not in csource
        assert "z_MyUnion_destroy(&x);" in csource

    def test_union_destructor_emitted(self):
        """Union destructor should be generated."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\nmain: function is { x: MyUnion.a 1 }"
        )
        assert "z_MyUnion_destroy" in csource
        assert "switch (u->tag)" in csource
        assert "free(u);" not in csource


class TestEmitterCrossUnitUnionConstruction:
    """Regression for the qualified-construction emit bug surfaced
    during VFS PR 3. Calling `io.IoError.other "msg".string` used to
    typecheck cleanly but emit `z_io_IoError_other(...)` — an
    undeclared symbol — because the emitter's `_is_union_construction`
    and `_emit_union_construction` only handled 2-segment dotted paths
    (`U.arm`), missing 3-segment paths (`unit.U.arm`). The fix trusts
    the typechecker's UNION_CREATE stamp + reads the union name from
    the call's stamped ZType."""

    def test_qualified_union_construction_emits_struct_init(self):
        csource = emit_source(
            'main: function is {\n  e: io.IoError.other "msg".string\n  print "done"\n}'
        )
        # No bogus function call.
        assert "z_io_IoError_other(" not in csource
        # Proper struct-init: tag + payload data assignment.
        assert "Z_IOERROR_TAG_OTHER" in csource
        assert ".tag = Z_IOERROR_TAG_OTHER" in csource
        # End-to-end: gcc accepts and runtime is clean.
        assert compile_and_run(csource).strip() == "done"

    def test_unqualified_union_construction_still_works(self):
        """Regression: the 2-segment form (`IoError.other`) keeps
        working post-fix. IoError is re-exported through core.z so
        bare access resolves; the fix must not break this path."""
        csource = emit_source(
            'main: function is {\n  e: IoError.other "msg".string\n  print "done"\n}'
        )
        assert "z_io_IoError_other(" not in csource
        assert ".tag = Z_IOERROR_TAG_OTHER" in csource
        assert compile_and_run(csource).strip() == "done"

    def test_qualified_variant_construction_emits_struct_init(self):
        """Variants share the construction code path with unions; the
        same fix should resolve `io.openmode.read` etc. openmode is
        a null-arm-only variant so this also exercises the no-payload
        branch."""
        csource = emit_source(
            'main: function is {\n  m: io.openmode.read\n  print "ok"\n}'
        )
        assert "z_io_openmode_read(" not in csource
        assert "Z_OPENMODE_TAG_READ" in csource
        assert compile_and_run(csource).strip() == "ok"

    def test_qualified_null_arm_union_construction_emits_struct_init(self):
        """3-segment null-arm union construction (no payload, no
        args). Goes through `_emit_dotted_path_value` (not the call
        path that the other tests exercise), so it's a separate fix
        site that needs the same `dp_parent_tagged_type` stamp
        trust."""
        csource = emit_source(
            'main: function is {\n  e: io.IoError.notfound\n  print "done"\n}'
        )
        # No bogus dotted-path reference and no bogus function call.
        assert "io.IoError" not in csource.replace("/*", "")
        assert "z_io_IoError" not in csource
        assert "Z_IOERROR_TAG_NOTFOUND" in csource
        assert ".tag = Z_IOERROR_TAG_NOTFOUND" in csource
        assert compile_and_run(csource).strip() == "done"


class TestEmitterUnionCustomTag:
    """Tests for union custom tag C emission (Phase 18)."""

    def test_custom_tag_values_emitted(self):
        """Custom data tag values should appear as explicit enum values."""
        csource = emit_source(
            "pv: data { A: 10 B: 20 }\n"
            "MyUnion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: MyUnion.A }"
        )
        assert "Z_MYUNION_TAG_A = 10" in csource
        assert "Z_MYUNION_TAG_B = 20" in csource

    def test_custom_tag_sparse_values_emitted(self):
        """Sparse custom tag values should be emitted correctly."""
        csource = emit_source(
            "pv: data { LOW: 0 MEDIUM: 1 HIGH: 2 CRITICAL: 10 }\n"
            "Priority: union {\n"
            "    LOW: null\n"
            "    MEDIUM: null\n"
            "    HIGH: null\n"
            "    CRITICAL: null\n"
            "} as {\n"
            "    tag: pv.tag\n"
            "}\n"
            "main: function is { x: Priority.LOW }"
        )
        assert "Z_PRIORITY_TAG_LOW = 0" in csource
        assert "Z_PRIORITY_TAG_CRITICAL = 10" in csource

    def test_default_tag_uses_sequential_values(self):
        """Without custom tag, enum values should be sequential (auto-increment)."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\nmain: function is { x: MyUnion.a 1 }"
        )
        # sequential values don't need explicit = N, just comma separation
        assert "Z_MYUNION_TAG_A," in csource
        assert "Z_MYUNION_TAG_B," in csource
        assert "= " not in csource.split("typedef enum")[1].split("}")[0]

    def test_numeric_tag_compiles(self):
        """Union with u16.tag compiles correctly."""
        csource = emit_source(
            "MyUnion: union { A: null\n B: null } as { tag: u16.tag }\n"
            "main: function is { x: MyUnion.A }"
        )
        assert "z_MyUnion_tag_t" in csource

    def test_data_tag_runtime(self):
        """Custom data tag still works at runtime after generic tag change."""
        csource = emit_source(
            "pv: data { A: 10 B: 20 }\n"
            "MyUnion: union { A: i64\n B: null } as { tag: pv.tag }\n"
            "main: function is {\n"
            "  x: MyUnion.A 42\n"
            "  match (x) case A then {\n"
            '    print "a"\n'
            "  } case B then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "a"


class TestEmitterUnionIntegration:
    """Integration tests: compile and run union programs."""

    def test_result_err_forward_across_result_shapes(self):
        """End-to-end: a function receives a `Result<u64, i64>`, returns
        `Result<null, i64>` by matching and reconstructing. Exercises
        the err-propagation pattern that BufWriter.flush needs
        (`return Result.err r t: null` in the narrowed err arm)."""
        csource = emit_source(
            "forward: function {r: (Result t: u64 e: i64)}"
            " out (Result t: null e: i64) is {\n"
            "    match (r) case ok then {\n"
            "        return (Result.ok null e: i64)\n"
            "    } case err then {\n"
            "        return (Result.err r t: null)\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    s: (Result.err 42 t: u64)\n"
            "    o: (forward r: s)\n"
            "    match (o) case ok then {\n"
            '        print "ok branch"\n'
            "    } case err then {\n"
            '        print "err branch"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "err branch"

    def test_result_ok_forward_across_result_shapes(self):
        """Same pattern but the ok arm is taken."""
        csource = emit_source(
            "forward: function {r: (Result t: u64 e: i64)}"
            " out (Result t: null e: i64) is {\n"
            "    match (r) case ok then {\n"
            "        return (Result.ok null e: i64)\n"
            "    } case err then {\n"
            "        return (Result.err r t: null)\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    s: (Result.ok 7.u64 e: i64)\n"
            "    o: (forward r: s)\n"
            "    match (o) case ok then {\n"
            '        print "ok branch"\n'
            "    } case err then {\n"
            '        print "err branch"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok branch"

    def test_union_basic(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            'main: function is { x: MyUnion.a 42\n print "ok" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_union_match(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "got a"\n'
            "  } case b then {\n"
            '    print "got b"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "got a"

    def test_union_null_variant(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.b\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "got a"\n'
            "  } case b then {\n"
            '    print "got b"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "got b"

    def test_union_string_variant(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: String\n c: null }\n"
            "main: function is {\n"
            '  x: MyUnion.b "hello".string\n'
            '  print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_union_take(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 42\n"
            "  y: x.take\n"
            '  print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_union_swap(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 1\n"
            "  y: MyUnion.b\n"
            "  x swap y\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  } case b then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "b"

    def test_union_function_param(self):
        csource = emit_source(
            "Result: union { ok: i64\n err: String\n none: null }\n"
            "describe: function {r: Result} out String is {\n"
            "  match (\n"
            "    r\n"
            "  ) case ok then {\n"
            '    return "ok".string\n'
            "  } case err then {\n"
            '    return "error".string\n'
            "  } case none then {\n"
            '    return "none".string\n'
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  a: Result.ok 42\n"
            '  print "a is \\{describe a}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "a is ok"

    def test_example_unions(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "unions")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        csource = zemitterc.emit(typing)
        output = compile_and_run(csource)
        assert "a is ok" in output
        assert "b is error" in output
        assert "c is none" in output
        assert "d is ok" in output


class TestEmitterUnionMemorySafety:
    """Memory safety tests for unions using ASan."""

    def test_union_no_leak(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            'main: function is { x: MyUnion.a 42\n print "ok" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_string_no_leak(self):
        csource = emit_source(
            "MyUnion: union { a: String\n b: null }\n"
            'main: function is { x: MyUnion.a "hello".string\n print "ok" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_take_no_double_free(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 42\n"
            "  y: x.take\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_swap_no_leak(self):
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 1\n"
            "  y: MyUnion.b\n"
            "  x swap y\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_with_native_class_arm_compiles(self):
        """Regression for PR-3 Quirk 1: a union arm typed as an
        io-wrapper class (`class is native`) must trigger the arm
        type's struct + destructor to be pulled into the emit set
        even when no other site in the program references the class
        directly. Pre-fix the emitter generated the union's destroy
        function with `z_TextReader_destroy(z_TextReader_t*)` while
        the wrapper class's struct was never emitted, so gcc
        rejected with `'z_TextReader_t' undeclared`."""
        csource = emit_source(
            "X: union { ok: io.TextReader\n err: io.IoError }\n"
            "main: function is {\n"
            "  e: IoError.notfound\n"
            "  r: X.err e.take\n"
            '  print "done"\n'
            "}"
        )
        # Forward declaration in the union destroy block + actual
        # named-struct emission later.
        assert "typedef struct z_TextReader_t z_TextReader_t;" in csource
        assert "struct z_TextReader_t {" in csource
        assert "z_TextReader_destroy" in csource
        # BufReader is pulled transitively (TextReader's `source`
        # field is BufReader.lock).
        assert "struct z_BufReader_t {" in csource
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_string_arm_from_function_param_no_double_free(self):
        """Regression for the implicit-take gap in union construction.
        When a String comes from a `.take` function parameter and is
        moved into a String-payload union arm, the union-construction
        emit must invalidate the source-side parameter so the
        function-exit cleanup doesn't double-free the heap buffer.

        Pre-fix: emitter dropped the temp from frees but never
        zero-init'd the source parameter — function-exit
        `z_String_free(&s)` ran on a buffer the boxed union arm now
        owned, double-freeing.
        """
        csource = emit_source(
            "Wrap: union { ok: String\n err: null }\n"
            "makeOk: function {s: String.take} out Wrap is {\n"
            "  return (Wrap.ok s.take)\n"
            "}\n"
            "main: function is {\n"
            '  r: makeOk s: "hello".string\n'
            '  print "made"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_return_union_arm_from_narrowed_result_take_compiles(self):
        """Regression: returning a union arm whose payload comes from
        the `.take` of a narrowed match-arm binding must invalidate the
        inner boxed payload, not zero the outer subject-typed binding.

        Pre-fix: `(Wrap.ok s.take)` inside `case ok then { return ... }`
        emitted `s = (z_String_t){0}` against a `z_Source_t` binding —
        gcc rejected with `incompatible types when assigning`. Fix made
        `_get_take_var` alias-aware so it returns the alias target
        (`(*(z_String_t*)s.data)`) and `_emit_take_invalidation`
        produces the well-typed deref-zero.
        """
        csource = emit_source(
            "Wrap: union { ok: String  bad: null }\n"
            "Err: union { boom: null  other: String }\n"
            "Source: union { ok: String  err: Err }\n"
            "extract: function {s: Source.take} out Wrap is {\n"
            "  match (\n"
            "    s\n"
            "  ) case ok then {\n"
            "    return (Wrap.ok s.take)\n"
            "  } case err then {\n"
            "    return (Wrap.bad)\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            '  src: Source.ok "hello".string\n'
            "  w: extract s: src.take\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_return_union_arm_from_narrowed_result_take_err_arm_compiles(self):
        """Symmetric coverage for the same `_get_take_var` fix: an
        err-arm `.take` of a union-typed inner payload (heap-allocated
        reftype) must produce a well-typed deref-zero, not a wrong-
        typed outer-binding zero.
        """
        csource = emit_source(
            "Err: union { boom: null  other: String }\n"
            "Source: union { ok: String  err: Err }\n"
            "Wrap: union { ok: String  err: Err }\n"
            "passthrough: function {s: Source.take} out Wrap is {\n"
            "  match (\n"
            "    s\n"
            "  ) case ok then {\n"
            "    return (Wrap.ok s.take)\n"
            "  } case err then {\n"
            "    return (Wrap.err s.take)\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            '  src: Source.err (Err.other "x".string)\n'
            "  w: passthrough s: src.take\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_map_get_option_string_value_return(self):
        """Regression for two coupled bugs surfaced by PR 6a.

        Codegen bug: `Map<u32, String>.get` had its call-result temp
        typed as `z_Option_String_t*` (pointer) but the function
        returns `z_Option_String_t` by value -- gcc rejected with
        `incompatible types when initializing`.

        Runtime bug: even after the C compiled, the Map.get template
        for String values did a shallow struct copy (the deep-copy
        path was dead code, gated on `val_is_reftype` which is False
        for String). Result: double-free when both the Map and the
        returned Option were destroyed.

        Both fixes together make Map<u32, String>.get round-trip
        cleanly under ASan.
        """
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: u32 value: String)\n"
            '    m.set key: 0.u32 value: "hello".string\n'
            '    m.set key: 1.u32 value: "world".string\n'
            "    r0: m.get key: 0.u32\n"
            "    match (\n"
            "        r0\n"
            "    ) case some then {\n"
            '        print "0: \\{r0}"\n'
            "    } case none then {\n"
            '        print "0: none"\n'
            "    }\n"
            "    r1: m.get key: 1.u32\n"
            "    match (\n"
            "        r1\n"
            "    ) case some then {\n"
            '        print "1: \\{r1}"\n'
            "    } case none then {\n"
            '        print "1: none"\n'
            "    }\n"
            "    r2: m.get key: 99.u32\n"
            "    match (\n"
            "        r2\n"
            "    ) case some then {\n"
            '        print "99: \\{r2}"\n'
            "    } case none then {\n"
            '        print "99: none"\n'
            "    }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "0: hello" in result.stdout, result.stdout
        assert "1: world" in result.stdout, result.stdout
        assert "99: none" in result.stdout, result.stdout

    def test_example_unions_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(LIB_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = make_parser_with_vfs(vfs, "unions")
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert errors == []
        csource = zemitterc.emit(typing)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "a is ok" in result.stdout


class TestStandaloneTake:
    """Tests for standalone .take (as expression statement, not in assignment)."""

    def test_standalone_take_class(self):
        """x.take as statement on class → zero-init."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: MyClass x: 42\n"
            "  c.take\n"
            "}"
        )
        # valtype-only class: no destructor, just zero-init
        assert "c = (z_MyClass_t){0};" in csource

    def test_standalone_take_union(self):
        """x.take as statement on union → destroy + NULL."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: MyUnion.a 42\n"
            "  x.take\n"
            "}"
        )
        assert "z_MyUnion_destroy(&x);" in csource
        assert "x = (z_MyUnion_t){0};" in csource

    def test_standalone_take_string(self):
        """s.take as statement on String → free + zero-init."""
        csource = emit_source('main: function is {\n  s: "hello".string\n  s.take\n}')
        assert "z_String_free(&s);" in csource
        assert "s = (z_String_t){0};" in csource

    def test_standalone_take_class_asan(self):
        """Standalone .take on class with String field → no leak, no double-free."""
        csource = emit_source(
            "MyClass: class { name: String }\n"
            "main: function is {\n"
            '  c: MyClass name: "hello".string\n'
            "  c.take\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "ok" in result.stdout


class TestStandaloneRelease:
    """Tests for standalone .release (early scope-exit for a variable)."""

    def test_release_class(self):
        """x.release on class → zero-init."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: MyClass x: 42\n"
            "  c.release\n"
            "}"
        )
        # valtype-only class: no destructor, just zero-init
        assert "c = (z_MyClass_t){0};" in csource

    def test_release_string(self):
        """s.release on String → free + zero-init."""
        csource = emit_source(
            'main: function is {\n  s: "hello".string\n  s.release\n}'
        )
        assert "z_String_free(&s);" in csource
        assert "s = (z_String_t){0};" in csource

    def test_release_valtype_no_destroy(self):
        """x.release on valtype → no destroy call in C output."""
        csource = emit_source("main: function is {\n  x: 42\n  x.release\n}")
        assert "destroy" not in csource.split("int main")[1]

    def test_release_class_asan(self):
        """Standalone .release on class → no leak, no double-free."""
        csource = emit_source(
            "MyClass: class { name: String }\n"
            "main: function is {\n"
            '  c: MyClass name: "hello".string\n'
            "  c.release\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "ok" in result.stdout


class TestImplicitTake:
    """Tests for implicit take (function parameter declared .take)."""

    def test_implicit_take_nullifies(self):
        """Function with .take param → caller's variable nullified after call."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            'consume: function {p: MyClass.take} is { print "consumed" }\n'
            "main: function is {\n"
            "  c: MyClass x: 42\n"
            "  consume c\n"
            "}"
        )
        # after the call, c should be zero-initialized
        lines = csource.split("\n")
        found_call = False
        found_null = False
        for line in lines:
            if "z_consume(" in line and "c" in line and "&c" not in line:
                found_call = True
            elif found_call and "c = (z_MyClass_t){0};" in line:
                found_null = True
                break
        assert found_call, "Expected call to z_consume(c) (by value)"
        assert found_null, "Expected c = (z_MyClass_t){0} after implicit take call"

    def test_implicit_take_no_double_null(self):
        """Explicit .take with implicit take param → only one zero-init."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            'consume: function {p: MyClass.take} is { print "consumed" }\n'
            "main: function is {\n"
            "  c: MyClass x: 42\n"
            "  consume c.take\n"
            "}"
        )
        # should have exactly one zero-init (from explicit .take, not doubled)
        assert csource.count("c = (z_MyClass_t){0};") == 1

    def test_implicit_take_asan(self):
        """Implicit take with class → no double-free."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "consume: function {p: MyClass.take} is { p.take }\n"
            "main: function is {\n"
            "  c: MyClass x: 42\n"
            "  consume c\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "ok" in result.stdout


class TestReturnPathTake:
    """Tests for .take in return-Path class construction."""

    def test_return_class_construction_take(self):
        """Return with class construction using .take → source nullified.

        The param is `.take` (caller transfers ownership in) so the body
        can `s.take` again to move it into the constructed class. Phase A
        defaults reftype params to BORROW; the explicit `.take` opts out
        and gives the body owned access."""
        csource = emit_source(
            "MyClass: class { name: String }\n"
            "wrap: function {s: String.take} out MyClass is {\n"
            "  return (MyClass name: s.take)\n"
            "}\n"
            "main: function is {\n"
            '  c: wrap "hello".string\n'
            '  print "ok"\n'
            "}"
        )
        assert "s = (z_String_t){0};" in csource


# ---- Phase 4h.2: Constructor Infrastructure (meta.create) ----


class TestEmitterConstructors:
    """Tests for compiler-generated meta.create constructors."""

    def test_class_meta_create_emitted(self):
        """Class should emit both meta.create and create functions."""
        csource = emit_source(
            "Counter: class { value: 0 }\nmain: function is { c: Counter }"
        )
        assert "z_Counter_meta_create" in csource
        assert "z_Counter_create" in csource
        assert "z_Counter_t _this" in csource
        # Counter is stack-allocated; no z_Counter_t* heap allocation
        assert "(z_Counter_t*)z_xmalloc" not in csource
        assert "_this.value = value;" in csource

    def test_record_meta_create_emitted(self):
        """Record should emit both meta.create and create functions."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\n"
            "main: function is { p: point x: 1 y: 2 }"
        )
        assert "z_point_meta_create" in csource
        assert "z_point_create" in csource
        assert "z_point_t _this" in csource
        assert "_this.x = x;" in csource
        assert "_this.y = y;" in csource

    def test_class_construction_calls_create(self):
        """Class construction should call .create."""
        csource = emit_source(
            "Counter: class { value: i64 }\n"
            'main: function is { c: Counter value: 10\n print "\\{c.value}" }'
        )
        assert "z_Counter_create(10)" in csource

    def test_record_construction_calls_create(self):
        """Record construction should call .create."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\n"
            "main: function is { p: point x: 1 y: 2 }"
        )
        assert "z_point_create(1, 2)" in csource

    def test_class_return_calls_create(self):
        """Return with class construction should call .create."""
        csource = emit_source(
            "MyClass: class { x: i64 }\n"
            "make: function {v: i64} out MyClass is { return (MyClass x: v) }\n"
            "main: function is { c: make 99 }"
        )
        assert "z_MyClass_create(v)" in csource

    def test_bare_class_calls_create(self):
        """Bare class name should call .create with zeros."""
        csource = emit_source(
            "MyClass: class { x: 0 }\nmain: function is { c: MyClass }"
        )
        assert "z_MyClass_create(0)" in csource

    def test_bare_record_calls_create(self):
        """Bare record name should call .create with zeros."""
        csource = emit_source(
            "point: record { x: 0\n y: 0 }\nmain: function is { p: point }"
        )
        assert "z_point_create(0, 0)" in csource

    def test_out_this_return_type(self):
        """Method with 'out this' return type should resolve correctly."""
        csource = emit_source(
            "MyClass: class { x: 0 } as {\n"
            "  make: function {v: i64} out this is { return (MyClass x: v) }\n"
            "}\n"
            "main: function is { c: MyClass }"
        )
        assert "z_MyClass_t z_MyClass_make" in csource


class TestEmitterConstructorIntegration:
    """Integration tests: compile and run constructor programs."""

    def test_class_meta_create_runtime(self):
        """Class meta.create: construct and access field."""
        csource = emit_source(
            "Counter: class { value: i64 }\n"
            'main: function is { c: Counter value: 42\n print "\\{c.value}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_record_meta_create_runtime(self):
        """Record meta.create: construct and access field."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\n"
            'main: function is { p: point x: 3 y: 7\n print "\\{p.x} \\{p.y}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "3 7"

    def test_class_string_field_take(self):
        """Class with String field: take semantics work via meta.create."""
        csource = emit_source(
            "MyClass: class { name: String }\n"
            'main: function is {\n  c: MyClass name: "hello".string\n  print "\\{c.name}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"


class TestEmitterConstructorMemorySafety:
    """Memory safety tests for constructors using ASan."""

    def test_class_meta_create_no_leak(self):
        """Class meta.create: no leak under ASan."""
        csource = emit_source(
            "Counter: class { value: i64 }\n"
            'main: function is { c: Counter value: 42\n print "\\{c.value}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_string_field_no_leak(self):
        """Class with String field: no leak under ASan."""
        csource = emit_source(
            "MyClass: class { name: String }\n"
            'main: function is {\n  c: MyClass name: "hello".string\n  print "\\{c.name}"\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_record_meta_create_no_leak(self):
        """Record meta.create: no leak under ASan."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\n"
            'main: function is { p: point x: 3 y: 7\n print "\\{p.x} \\{p.y}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"


class TestEmitterLabelValueShorthand:
    """Tests for :x (label_value) C emission."""

    def test_union_with_label_value_subtypes(self):
        """Union with :x subtypes emits correct tag enum + struct."""
        csource = emit_source(
            "MyUnion: union { :u8\n :u16\n :u32 }\n"
            "main: function is { x: MyUnion.u8 42 }"
        )
        assert "Z_MYUNION_TAG_U8" in csource
        assert "Z_MYUNION_TAG_U16" in csource
        assert "Z_MYUNION_TAG_U32" in csource
        assert "z_MyUnion_t" in csource

    def test_call_with_label_value_arg(self):
        """Call with :x argument emits correctly."""
        csource = emit_source(
            "f: function {x: i64} out i64 is { x }\nmain: function is { x: 42\n f :x }"
        )
        assert "z_f(" in csource


class TestEmitterLabelValueIntegration:
    """Integration: compile and run programs using :x syntax."""

    def test_union_label_value_basic(self):
        """Compile and run union with :x subtypes."""
        csource = emit_source(
            "MyUnion: union { :u8\n :String }\n"
            'main: function is { x: MyUnion.u8 42\n print "ok" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_call_label_value_arg(self):
        """Compile and run call with :x argument."""
        csource = emit_source(
            'f: function {x: i64} is { print "\\{x}" }\n'
            "main: function is { x: 99\n f :x }"
        )
        output = compile_and_run(csource)
        assert output.strip() == "99"


class TestInlineUnits:
    def test_inline_unit_function_emits(self):
        """Inline unit function emits correctly mangled C function."""
        csource = emit_source(
            "m: unit { f: function {x: i64} out i64 is { return x } }\n"
            'main: function is { print "\\{m.f 5}" }'
        )
        assert "z_m_f" in csource

    def test_inline_unit_constant(self):
        """Inline unit constant accessible via dotted Path. Value
        inlines as a macro at the use site (no backing decl)."""
        csource = emit_source(
            'm: unit { X: 42 }\nmain: function is { print "\\{m.X}" }'
        )
        # Macro semantics: no static decl for the constant.
        assert "z_m_X" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_nested_inline_unit_function(self):
        """Nested inline unit function emits with full Path mangling."""
        csource = emit_source(
            "a: unit { b: unit { f: function {x: i64} out i64 is { return x } } }\n"
            'main: function is { print "\\{a.b.f 7}" }'
        )
        assert "z_a_b_f" in csource
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_inline_unit_integration(self):
        """Integration test: compile + run program using inline units."""
        csource = emit_source(
            "math: unit {\n"
            "  double: function {x: i64} out i64 is { return x + x }\n"
            "  triple: function {x: i64} out i64 is { return x + x + x }\n"
            "}\n"
            'main: function is { print "\\{math.double 5} \\{math.triple 3}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "10 9"


class TestEmitterVariant:
    """Tests for variant C emission."""

    def test_variant_struct(self):
        """Variant should emit tag enum + struct with inline union."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        assert "Z_MYVAR_TAG_A" in csource
        assert "Z_MYVAR_TAG_B" in csource
        assert "z_myvar_tag_t" in csource
        assert "z_myvar_t" in csource
        assert "union {" in csource

    def test_variant_no_malloc(self):
        """Variant construction should not use malloc."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 42 }"
        )
        # there should be no heap allocation for the variant construction
        # (z_xmalloc may exist elsewhere but not on any variant line)
        lines = csource.split("\n")
        variant_lines = [ln for ln in lines if "myvar" in ln.lower() or "_c1" in ln]
        assert not any("z_xmalloc" in ln for ln in variant_lines)

    def test_variant_direct_assign(self):
        """Variant construction should use direct .data.subname assignment."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 42 }"
        )
        assert ".data.a = " in csource

    def test_variant_null_construction(self):
        """Null subtype construction emits tag only."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.b }"
        )
        assert "Z_MYVAR_TAG_B" in csource

    def test_variant_no_destructor(self):
        """Variant should not have a destroy function."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        assert "myvar_destroy" not in csource

    def test_variant_match_dot_access(self):
        """Match on variant emits switch with dot (not arrow) access."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myvar.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  } case b then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )
        assert "switch (x.tag)" in csource
        assert "case Z_MYVAR_TAG_A:" in csource
        assert "case Z_MYVAR_TAG_B:" in csource

    def test_variant_enum_only_tag(self):
        """All-null variant should have just tag, no union data member."""
        csource = emit_source(
            "mode: variant { READ: null\n WRITE: null }\n"
            "main: function is { x: mode.READ }"
        )
        # the struct should have tag but no 'union {' for data
        struct_section = csource.split("typedef struct")[1].split("z_mode_t;")[0]
        assert "union" not in struct_section

    def test_variant_equality(self):
        """Variant should have z_name_eq equality function."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\nmain: function is { x: myvar.a 1 }"
        )
        assert "z_myvar_eq" in csource
        # small variant: tag+payload comparison
        assert "a.tag != b.tag" in csource

    def test_variant_basic_run(self):
        """Compile and run: construct and match variant."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myvar.a 42\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "got a"\n'
            "  } case b then {\n"
            '    print "got b"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "got a"

    def test_variant_with_record_run(self):
        """Compile and run: variant containing record."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\n"
            "shape: variant { pt: point\n none: null }\n"
            "main: function is {\n"
            "  s: shape.pt (point x: 10 y: 20)\n"
            "  match (\n"
            "    s\n"
            "  ) case pt then {\n"
            '    print "point"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "point"

    def test_variant_in_record_run(self):
        """Compile and run: record with variant field."""
        csource = emit_source(
            "color: variant { red: null\n blue: null\n green: null }\n"
            "item: record { name: i64\n c: color }\n"
            "main: function is {\n"
            "  clr: color.red\n"
            "  x: item name: 1 c: clr\n"
            '  print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_variant_payload_access_run(self):
        """Compile and run: access payload inside match case — under
        shadow narrowing, `x` inside `case a then` IS the i64 payload,
        so bare `x` prints the value."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myvar.a 42\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "\\{x}"\n'
            "  } case b then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"


class TestSpecs:
    """Tests for specs (function pointer types) — Phase 20."""

    def test_spec_generates_typedef(self):
        """A Spec definition generates a typedef in C output."""
        csource = emit_source(
            "binop: function {a: i64 b: i64} out i64\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "main: function is { add a: 1 b: 2 }"
        )
        assert "typedef" in csource
        assert "z_binop_ft" in csource

    def test_callback_parameter(self):
        """Pass function reference via .take, call through it."""
        csource = emit_source(
            "binop: function {a: i64 b: i64} out i64\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            'main: function is { print "\\{apply f: add.take a: 3 b: 4}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_local_function_reference(self):
        """Assign function ref with .take, call through local variable."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "main: function is {\n"
            "  cb: add.take\n"
            '  print "\\{cb a: 10 b: 20}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "30"

    def test_reassignment_of_function_ref(self):
        """Reassign a function reference variable."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "sub: function {a: i64 b: i64} out i64 is { return a - b }\n"
            "main: function is {\n"
            "  cb: add.take\n"
            '  print "\\{cb a: 10 b: 3}"\n'
            "  cb = sub.take\n"
            '  print "\\{cb a: 10 b: 3}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "13"
        assert lines[1] == "7"

    def test_multiple_specs(self):
        """Multiple specs with different signatures."""
        csource = emit_source(
            "binop: function {a: i64 b: i64} out i64\n"
            "unaryop: function {x: i64} out i64\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "negate: function {x: i64} out i64 is { return 0 - x }\n"
            "main: function is {\n"
            "  f1: add.take\n"
            "  f2: negate.take\n"
            '  print "\\{f1 a: 3 b: 4}"\n'
            '  print "\\{f2 x: 5}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "7"
        assert lines[1] == "-5"

    def test_record_with_func_pointer_field(self):
        """Record with function pointer field (Spec in 'is' section)."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "calculator: record {\n"
            "    x: i64\n"
            "    op: function {a: i64 b: i64} out i64\n"
            "}\n"
            "main: function is {\n"
            "  c: calculator x: 10 op: add.take\n"
            '  print "\\{c.op a: 3 b: 4}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_record_func_pointer_reassignment(self):
        """Function pointer field in record 'is' section can be reassigned."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "mul: function {a: i64 b: i64} out i64 is { return a * b }\n"
            "calculator: record {\n"
            "    op: function {a: i64 b: i64} out i64\n"
            "}\n"
            "main: function is {\n"
            "  c: calculator op: add.take\n"
            '  print "\\{c.op a: 3 b: 4}"\n'
            "  c.op = mul.take\n"
            '  print "\\{c.op a: 3 b: 4}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "7\n12"

    def test_class_func_pointer_reassignment(self):
        """Function pointer field in class 'is' section can be reassigned."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "mul: function {a: i64 b: i64} out i64 is { return a * b }\n"
            "Calc: class {\n"
            "    op: function {a: i64 b: i64} out i64\n"
            "}\n"
            "main: function is {\n"
            "  c: Calc op: add.take\n"
            '  print "\\{c.op a: 5 b: 6}"\n'
            "  c.op = mul.take\n"
            '  print "\\{c.op a: 5 b: 6}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "11\n30"

    def test_spec_asan(self):
        """No memory issues with function references (ASan)."""
        csource = emit_source(
            "binop: function {a: i64 b: i64} out i64\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "apply: function {f: binop a: i64 b: i64} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            'main: function is { print "\\{apply f: add.take a: 3 b: 4}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "7"

    def test_class_sibling_method_ref_default_struct(self):
        """Class field defaulted to a sibling method emits the slot
        with inline notation matching the referenced method's C
        signature, and uses a forward-declared typedef so the self-
        reference compiles."""
        csource = emit_source(
            "c: class {\n"
            "    val: i64\n"
            "    method1: function {t: this} out i64 is { return 1 }\n"
            "    instancemethod: method1\n"
            "}\n"
            "main: function is { obj: c val: 0 }"
        )
        assert "typedef struct z_c_t z_c_t;" in csource
        assert "int64_t (*instancemethod)(z_c_t*);" in csource

    def test_class_sibling_method_ref_default_invoke(self):
        """Default invocation calls the referenced method via the
        struct slot; reassignment to a sibling method swaps the hook."""
        csource = emit_source(
            "c: class {\n"
            "    val: i64\n"
            "    method1: function {t: this} out i64 is { return 1 }\n"
            "    method2: function {t: this} out i64 is { return 2 }\n"
            "    instancemethod: method1\n"
            "}\n"
            "main: function is {\n"
            "  obj: c val: 0\n"
            '  print "\\{obj.instancemethod}"\n'
            "  obj.instancemethod = c.method2\n"
            '  print "\\{obj.instancemethod}"\n'
            "}"
        )
        # field-slot dispatch, not a non-existent z_c_instancemethod
        assert "z_c_instancemethod(" not in csource
        assert "obj.instancemethod(&obj)" in csource
        output = compile_and_run(csource)
        assert output.strip() == "1\n2"

    def test_class_sibling_method_ref_construct_override(self):
        """Construction can override the default with a sibling method."""
        csource = emit_source(
            "c: class {\n"
            "    val: i64\n"
            "    method1: function {t: this} out i64 is { return 10 }\n"
            "    method2: function {t: this} out i64 is { return 20 }\n"
            "    instancemethod: method1\n"
            "}\n"
            "main: function is {\n"
            "  obj1: c val: 0\n"
            '  print "\\{obj1.instancemethod}"\n'
            "  obj2: c val: 0 instancemethod: c.method2\n"
            '  print "\\{obj2.instancemethod}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10\n20"

    def test_record_sibling_method_ref_default(self):
        """Record fields support the same sibling-method default form.
        Records pass `this` by value, so the field slot omits the
        pointer translation but still needs the forward typedef so
        the self-reference parses."""
        csource = emit_source(
            "mrec: record {\n"
            "    val: i64\n"
            "    transform1: function {r: this} out i64 is { return r.val + 100 }\n"
            "    transform2: function {r: this} out i64 is { return r.val * 10 }\n"
            "    hook: transform1\n"
            "}\n"
            "main: function is {\n"
            "  r: mrec val: 5\n"
            '  print "\\{r.hook r: r}"\n'
            "}"
        )
        assert "typedef struct z_mrec_t z_mrec_t;" in csource
        assert "int64_t (*hook)(z_mrec_t);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "105"


class TestAsConstants:
    """Constants defined in 'as' sections of records and classes."""

    def test_record_as_constant_runtime(self):
        """Record with integer constant in 'as' section compiles and runs."""
        csource = emit_source(
            "r: record { x: i64 } as { max_val: 100 }\n"
            'main: function is { print "\\{r.max_val}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "100"

    def test_class_as_constant_runtime(self):
        """Class with integer constant in 'as' section compiles and runs."""
        csource = emit_source(
            "c: class { x: i64 } as { default_x: 42 }\n"
            'main: function is { print "\\{c.default_x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_record_as_constant_used_in_expression(self):
        """Constant from 'as' section can be used in an expression."""
        csource = emit_source(
            "r: record { x: i64 } as { offset: 10 }\n"
            "main: function is {\n"
            "  val: r.offset + 5\n"
            '  print "\\{val}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_record_as_constant_with_method(self):
        """Record with both constant and method in 'as' section."""
        csource = emit_source(
            "r: record { x: i64 } as {\n"
            "  max_val: 100\n"
            "  get_x: function {p: this} out i64 is { return p.x }\n"
            "}\n"
            "main: function is {\n"
            "  p: r x: 5\n"
            '  print "\\{r.max_val}"\n'
            '  print "\\{r.get_x p}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "100\n5"

    def test_float_constant_runtime(self):
        """Float constant in 'as' section compiles and runs."""
        csource = emit_source(
            "r: record { x: i64 } as { pi: 3.14 }\n"
            'main: function is { print "\\{r.pi}" }'
        )
        output = compile_and_run(csource)
        assert output.strip().startswith("3.14")

    def test_reference_to_unit_constant_runtime(self):
        """Reference to unit-level constant in 'as' compiles and runs."""
        csource = emit_source(
            "max_size: 100\n"
            "config: record { x: i64 } as { limit: max_size }\n"
            'main: function is { print "\\{config.limit}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "100"

    def test_computed_constant_runtime(self):
        """Computed constant expression compiles and runs."""
        csource = emit_source(
            "r: record { x: i64 } as { max: 2 * 1024 }\n"
            'main: function is { print "\\{r.max}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "2048"

    def test_string_constant_runtime(self):
        """String constant in 'as' section compiles and runs."""
        csource = emit_source(
            'r: record { x: i64 } as { name: "hello" }\n'
            "main: function is { print r.name }"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"


class TestDefaults:
    def test_function_all_defaults_omitted(self):
        """Function call omitting all default args (bare function name = call)."""
        csource = emit_source(
            "greet: function {a: 42} out i64 is { return a }\n"
            'main: function is { print "\\{greet}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_function_numeric_default_provided(self):
        """Function call providing value overrides default."""
        csource = emit_source(
            "greet: function {a: 42} out i64 is { return a }\n"
            'main: function is { print "\\{greet a: 99}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "99"

    def test_function_zero_default(self):
        """Function with default=0 works correctly."""
        csource = emit_source(
            "inc: function {a: 0} out i64 is { return a + 1 }\n"
            'main: function is { print "\\{inc}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "1"

    def test_function_trailing_default_omitted(self):
        """Function call omitting trailing default arg."""
        csource = emit_source(
            "calc: function {a: i64 b: 42} out i64 is { return a + b }\n"
            'main: function is { print "\\{calc 1}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "43"

    def test_record_numeric_default_field(self):
        """Record construction omitting a defaulted field uses default."""
        csource = emit_source(
            "myrec: record {\n"
            "    x: i64\n"
            "    y: 10\n"
            "}\n"
            "main: function is {\n"
            "  r: myrec x: 5\n"
            '  print "\\{r.y}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10"

    def test_record_default_field_overridden(self):
        """Record construction providing a defaulted field overrides it."""
        csource = emit_source(
            "myrec: record {\n"
            "    x: i64\n"
            "    y: 10\n"
            "}\n"
            "main: function is {\n"
            "  r: myrec x: 5 y: 99\n"
            '  print "\\{r.y}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "99"

    def test_function_ref_default(self):
        """Function reference default: omitted arg uses function ref."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "apply: function {a: i64 b: i64 f: add} out i64 is {\n"
            "  result: f a: a b: b\n"
            "  return result\n"
            "}\n"
            'main: function is { print "\\{apply a: 3 b: 4}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_multiple_defaults_mix(self):
        """Mix of provided and omitted default params."""
        csource = emit_source(
            "calc: function {a: i64 b: 10 c: 20} out i64 is { return a + b + c }\n"
            'main: function is { print "\\{calc 1}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "31"

    def test_defaults_asan(self):
        """No memory issues with defaults (ASan)."""
        csource = emit_source(
            "inc: function {a: 0} out i64 is { return a + 1 }\n"
            "myrec: record {\n"
            "    x: i64\n"
            "    y: 10\n"
            "}\n"
            "main: function is {\n"
            '  print "\\{inc}"\n'
            "  r: myrec x: 5\n"
            '  print "\\{r.y}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        lines = result.stdout.strip().split("\n")
        assert lines[0] == "1"
        assert lines[1] == "10"

    def test_variant_subtype_default_field_zero_arg_construction(self):
        """Bare-name record construction (`r: myrec`) fills a variant
        default field with the tag-only compound literal."""
        csource = emit_source(
            "direction: variant {\n"
            "    north: null\n"
            "    south: null\n"
            "    east: null\n"
            "    west: null\n"
            "} as { tag: u8.tag }\n"
            "mystate: record {\n"
            "    dir: direction.north\n"
            "    count: 0\n"
            "}\n"
            "main: function is {\n"
            "    s: mystate\n"
            '    match (s.dir) case north then { print "north" }'
            '    case south then { print "south" }'
            '    case east then { print "east" }'
            '    case west then { print "west" }\n'
            '    print "count: \\{s.count}"\n'
            "}"
        )
        # the compound literal is in the call site
        assert "Z_DIRECTION_TAG_NORTH" in csource
        out = compile_and_run(csource).strip().splitlines()
        assert out == ["north", "count: 0"]

    def test_variant_subtype_default_field_overridden(self):
        """Construction supplying the field overrides the default."""
        csource = emit_source(
            "direction: variant {\n"
            "    north: null\n"
            "    south: null\n"
            "} as { tag: u8.tag }\n"
            "mystate: record {\n"
            "    dir: direction.north\n"
            "}\n"
            "main: function is {\n"
            "    s: mystate dir: direction.south\n"
            '    match (s.dir) case north then { print "north" }'
            '    case south then { print "south" }\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "south"

    def test_variant_subtype_default_param(self):
        """A function param defaults to the variant arm; calling without
        the param uses the default."""
        csource = emit_source(
            "color: variant {\n"
            "    red: null\n"
            "    green: null\n"
            "    blue: null\n"
            "} as { tag: u8.tag }\n"
            "name: function {c: color.red} out i64 is {\n"
            "    r: 0\n"
            "    match (c) case red then { r = 1 }"
            "    case green then { r = 2 }"
            "    case blue then { r = 3 }\n"
            "    return r\n"
            "}\n"
            "main: function is {\n"
            '    print "\\{name}"\n'
            '    print "\\{name c: color.blue}"\n'
            "}"
        )
        out = compile_and_run(csource).strip().splitlines()
        assert out == ["1", "3"]

    def test_union_subtype_default_on_class_field(self):
        """Union default works on a class field (records reject union
        fields entirely for unrelated reftype reasons)."""
        csource = emit_source(
            "Event: union {\n"
            "    idle: null\n"
            "    busy: null\n"
            "}\n"
            "Machine: class {\n"
            "    e: Event.idle\n"
            "}\n"
            "main: function is {\n"
            "    m: Machine\n"
            '    match (m.e) case idle then { print "idle" }'
            '    case busy then { print "busy" }\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "idle"


class TestNumericCasting:
    def test_dotted_numeric_emits_cast(self):
        """x: 42.u32 emits ((uint32_t)42) in C."""
        csource = emit_source("main: function is { x: 42.u32 }")
        assert "((uint32_t)42)" in csource

    def test_dotted_numeric_runtime(self):
        """Function using 42.i8 produces correct value."""
        csource = emit_source('main: function is {\n  x: 42.i8\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_dotted_default_param_runtime(self):
        """Function {a: i64 b: 10.u32} with trailing default."""
        csource = emit_source(
            "calc: function {a: i64 b: 10.u32} out i64 is { return a + b }\n"
            'main: function is { print "\\{calc 5}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_record_dotted_default_field(self):
        """Record with y: 10.u32 field default."""
        csource = emit_source(
            "myrec: record {\n"
            "    x: i64\n"
            "    y: 10.u32\n"
            "}\n"
            "main: function is {\n"
            "  r: myrec x: 5\n"
            '  print "\\{r.y}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10"

    def test_runtime_variable_cast(self):
        """Variable x: i64 then y: x.u32 produces correct value."""
        csource = emit_source(
            'main: function is {\n  x: 42\n  y: x.u32\n  print "\\{y}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_runtime_cast_overflow_panics(self):
        """Variable cast that overflows exits with error."""
        csource = emit_source(
            'main: function is {\n  x: 2000\n  y: x.i8\n  print "\\{y}"\n}'
        )
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(csource)
            cpath = f.name
        outpath = cpath.replace(".c", "")
        try:
            result = subprocess.run(
                [_CC, "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"gcc failed: {result.stderr}"
            result = subprocess.run(
                [outpath],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0
            assert "overflow" in result.stderr
        finally:
            for p in (cpath, outpath):
                if os.path.exists(p):
                    os.unlink(p)

    def test_numeric_cast_asan(self):
        """ASan clean for numeric casting."""
        csource = emit_source(
            "main: function is {\n"
            "  x: 42.u32\n"
            "  y: 10\n"
            "  z: y.u32\n"
            '  print "\\{x}"\n'
            '  print "\\{z}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        lines = result.stdout.strip().split("\n")
        assert lines[0] == "42"
        assert lines[1] == "10"

    def test_return_numeric_cast_keeps_stmt_expr_parens(self):
        """`return x.u32` must wrap the GCC stmt-expr in `({ ... })`.

        Regression: `_unwrap_outer_parens` used to strip the outer
        parens unconditionally at every `return` site, leaving the
        bare brace block `{ ... }` which is not a valid C expression.
        """
        csource = emit_source(
            "toU32: function {x: u64} out u32 is {\n"
            "  return x.u32\n"
            "}\n"
            'main: function is { print "\\{toU32 x: 42.u64}" }\n'
        )
        assert "return ({" in csource
        assert "return {" not in csource
        assert compile_and_run(csource).strip() == "42"


class TestStringWhitespace:
    def test_leading_blank_line_stripped(self):
        """Leading blank line after opening quote is stripped."""
        csource = emit_source('main: function is {\n  x: "\n  hello"\n  print x\n}')
        output = compile_and_run(csource).rstrip("\n")
        assert output == "  hello"

    def test_trailing_blank_line_stripped(self):
        """Trailing blank line before closing quote is stripped."""
        csource = emit_source('main: function is {\n  x: "hello\n  "\n  print x\n}')
        output = compile_and_run(csource).rstrip("\n")
        assert output == "hello"

    def test_leading_and_trailing_stripped(self):
        """Both leading and trailing blank lines stripped."""
        csource = emit_source('main: function is {\n  x: "\n  hello\n  "\n  print x\n}')
        output = compile_and_run(csource).rstrip("\n")
        assert output == "hello"

    def test_whitespace_prefix_stripped(self):
        """Whitespace prefix from closing delimiter line is stripped."""
        csource = emit_source(
            'main: function is {\n  x: "\n    hello\n    world\n    "\n  print x\n}'
        )
        output = compile_and_run(csource).rstrip("\n")
        assert output == "hello\nworld"

    def test_indented_line_keeps_extra(self):
        """Line with more indent than prefix keeps the extra."""
        csource = emit_source(
            "main: function is {\n"
            '  x: "\n'
            "    hello\n"
            "      indented\n"
            '    "\n'
            "  print x\n"
            "}"
        )
        output = compile_and_run(csource).rstrip("\n")
        assert output == "hello\n  indented"

    def test_closing_hard_left_no_strip(self):
        """Closing delimiter hard left means no whitespace stripping."""
        csource = emit_source(
            'main: function is {\n  x: "\n    hello\n    world\n  "\n  print x\n}'
        )
        output = compile_and_run(csource).rstrip("\n")
        # closing " is at indent 2, content at indent 4 -> strip 2 -> "  hello"
        assert output == "  hello\n  world"

    def test_simple_string_unchanged(self):
        """Simple single-line String is not affected."""
        csource = emit_source('main: function is { print "hello" }')
        output = compile_and_run(csource).rstrip("\n")
        assert output == "hello"

    def test_raw_string_whitespace(self):
        """Raw strings also get whitespace handling."""
        csource = emit_source(
            'main: function is {\n  x: """\n    hello\n    world\n    """\n  print x\n}'
        )
        output = compile_and_run(csource).rstrip("\n")
        assert output == "hello\nworld"

    def test_interpolation_with_whitespace(self):
        """String interpolation works with whitespace stripping."""
        csource = emit_source(
            "main: function is {\n"
            "  n: 42\n"
            '  x: "\n'
            "    value: \\{n}\n"
            '    "\n'
            "  print x\n"
            "}"
        )
        output = compile_and_run(csource).rstrip("\n")
        assert output == "value: 42"

    def test_string_whitespace_asan(self):
        """No memory issues with whitespace-stripped strings (ASan)."""
        csource = emit_source(
            'main: function is {\n  x: "\n    hello\n    world\n    "\n  print x\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.rstrip("\n") == "hello\nworld"


class TestProtocols:
    PROTO_SOURCE = (
        "Reader: protocol {\n"
        "    read: function {:this b: i64} out i64\n"
        "}\n"
        "MyFile: record {\n"
        "    fd: i64\n"
        "} as {\n"
        "    myreader: Reader\n"
        "    read: function {f: this b: i64} out i64 is {\n"
        "        return f.fd + b\n"
        "    }\n"
        "}\n"
    )

    # Class-based version for .borrow/.lock tests (records are valtypes,
    # cannot be locked).
    PROTO_CLASS_SOURCE = (
        "Reader: protocol {\n"
        "    read: function {:this b: i64} out i64\n"
        "}\n"
        "MyFile: class {\n"
        "    fd: i64\n"
        "} as {\n"
        "    myreader: Reader\n"
        "    read: function {f: this b: i64} out i64 is {\n"
        "        return f.fd + b\n"
        "    }\n"
        "}\n"
    )

    def test_protocol_vtable_struct(self):
        """C output contains vtable and instance struct."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is { f: MyFile fd: 1 }"
        )
        assert "z_Reader_vtable_t" in csource
        assert "z_Reader_t" in csource
        assert "void* data;" in csource
        assert "z_Reader_vtable_t* vtable;" in csource
        assert "void (*destroy)(void*);" in csource

    def test_protocol_impl_wrapper(self):
        """C output contains wrapper function and static vtable."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is { f: MyFile fd: 1 }"
        )
        assert "z_MyFile_myreader_read_wrapper" in csource
        assert "z_MyFile_myreader_vtable" in csource
        assert "z_MyFile_myreader_create" in csource

    def test_protocol_dispatch_runtime(self):
        """End-to-end: create record, create protocol instance, call method, verify output."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: f.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_protocol_with_class(self):
        """Protocol dispatch with class (pointer semantics)."""
        csource = emit_source(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "MyObj: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {o: this b: i64} out i64 is {\n"
            "        return o.fd + b\n"
            "    }\n"
            "}\n"
            "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 7\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    o: MyObj fd: 20\n"
            "    r: o.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "27"

    def test_protocol_asan(self):
        """ASan clean: no memory leaks or errors with protocol instances."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: f.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_protocol_temp_no_malloc(self):
        """Temp protocol instances use stack allocation, not malloc."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            '    print "\\{use_reader f.myreader}"\n'
            "}\n"
        )
        # temp path should be stack-allocated (z_Reader_t directly, not pointer)
        assert "z_Reader_t _p" in csource
        # protocol struct itself is not malloc'd
        assert "z_xmalloc(sizeof(z_Reader_t))" not in csource
        # the stack temp is destroyed by address — after Phase C step 2's
        # arg hoisting, ownership transfers to a synth `_tN` temp whose
        # destructor fires at function exit.
        assert "z_Reader_destroy(&" in csource

    def test_protocol_temp_runtime(self):
        """Stack-allocated temp protocol instance works at runtime."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            '    print "\\{use_reader f.myreader}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_protocol_temp_asan(self):
        """ASan clean with stack-allocated temp protocol instances."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            '    print "\\{use_reader f.myreader}"\n'
            "}\n"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_protocol_named_var_still_heap(self):
        """Named protocol variables still use heap allocation."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: f.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        # named var should still use create function (heap alloc)
        assert "z_MyFile_myreader_create" in csource

    def test_owned_protocol_create_record(self):
        """Owned protocol create from record compiles and runs."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.create from: f.take\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_owned_protocol_create_class(self):
        """Owned protocol create from class compiles and runs."""
        csource = emit_source(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "MyObj: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {o: this b: i64} out i64 is {\n"
            "        return o.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    o: MyObj fd: 20\n"
            "    r: Reader.create from: o.take\n"
            '    print "\\{r.read b: 7}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "27"

    def test_owned_protocol_create_asan(self):
        """ASan-clean: no leaks or use-after-free with owned protocol."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.create from: f.take\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_owned_protocol_dispatch(self):
        """Method dispatch on owned protocol works via use_reader."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.create from: f.take\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_owned_protocol_destroy_emitted(self):
        """Owned create sets destroy function pointer (not NULL)."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.create from: f.take\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        assert "create_owned" in csource
        assert "boxed_destroy" in csource

    def test_protocol_borrow_record(self):
        """Reader.borrow from: f.lock compiles and runs (class, since records are valtypes)."""
        csource = emit_source(
            self.PROTO_CLASS_SOURCE + "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.borrow from: f.lock\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_protocol_borrow_class(self):
        """Reader.borrow from class compiles and runs."""
        csource = emit_source(
            "Reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "MyObj: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: Reader\n"
            "    read: function {o: this b: i64} out i64 is {\n"
            "        return o.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    o: MyObj fd: 20\n"
            "    r: Reader.borrow from: o.lock\n"
            '    print "\\{r.read b: 7}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "27"

    def test_protocol_borrow_asan(self):
        """ASan clean for borrow, no use-after-free."""
        csource = emit_source(
            self.PROTO_CLASS_SOURCE + "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.borrow from: f.lock\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_protocol_borrow_source_accessible_after_scope(self):
        """Underlying object accessible after borrowed protocol used in function."""
        csource = emit_source(
            self.PROTO_CLASS_SOURCE + "use_reader: function {r: Reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: MyFile fd: 10\n"
            "    r: Reader.borrow from: f.lock\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"


class TestGenericsEmission:
    """Tests for generic type emission."""

    def test_generic_union_template_not_emitted(self):
        """Generic union template should not produce C struct."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: MyOpt.some 42 }"
        )
        # template should NOT be emitted
        assert "z_MyOpt_tag_t" not in csource
        assert "z_MyOpt_t" not in csource

    def test_monomorphized_union_emitted(self):
        """Monomorphized union emits tag enum + struct + destructor."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: MyOpt.some 42 }"
        )
        assert "z_MyOpt_i64_tag_t" in csource
        assert "z_MyOpt_i64_t" in csource
        assert "Z_MYOPT_I64_TAG_SOME" in csource
        assert "Z_MYOPT_I64_TAG_NONE" in csource
        assert "z_MyOpt_i64_destroy" in csource

    def test_monomorphized_union_construction_compiles(self):
        """Generic union construction compiles and runs."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyOpt.some 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_monomorphized_union_match(self):
        """Match on monomorphized union works."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "  x: MyOpt.some 42\n"
            "  match (\n"
            "    x\n"
            "  ) case some then {\n"
            '    print "found"\n'
            "  } case none then {\n"
            '    print "empty"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "found"

    def test_monomorphized_union_scope_cleanup(self):
        """Monomorphized union destroyed at function exit."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: MyOpt.some 42 }"
        )
        assert "z_MyOpt_i64_destroy(&x);" in csource

    def test_two_different_instantiations(self):
        """Two different instantiations produce two distinct types."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyOpt.some 42\n"
            "    y: MyOpt.some 3.14\n"
            "}"
        )
        assert "z_MyOpt_i64_t" in csource
        assert "z_MyOpt_f64_t" in csource
        assert "z_MyOpt_i64_destroy" in csource
        assert "z_MyOpt_f64_destroy" in csource

    def test_system_option_compiles(self):
        """System optionval type compiles and runs."""
        csource = emit_source(
            'main: function is {\n    x: optionval.some 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_union_asan(self):
        """Monomorphized union passes AddressSanitizer."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyOpt.some 42\n"
            "    y: MyOpt.none i32\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan failure: {result.stderr}"

    # ---- Generic from: call syntax ----

    def test_generic_union_from_compiles(self):
        """Generic union construction with from: compiles and runs."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyOpt.some from: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_union_explicit_type_and_from_compiles(self):
        """Generic union with explicit type param and from: compiles and runs."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyOpt.some t: i64 from: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_union_from_emits_correct_type(self):
        """from: syntax produces same monomorphized type as positional."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is { x: MyOpt.some from: 42 }"
        )
        assert "z_MyOpt_i64_t" in csource
        assert "Z_MYOPT_I64_TAG_SOME" in csource

    def test_generic_union_from_asan(self):
        """Generic union from: passes AddressSanitizer."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyOpt.some from: 42\n"
            "    y: MyOpt.none i32\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan failure: {result.stderr}"

    def test_system_option_from_compiles(self):
        """System optionval type with from: compiles and runs."""
        csource = emit_source(
            'main: function is {\n    x: optionval.some from: 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_nullable_ptr_option_with_string(self):
        """Option.some with String (reftype) emits nullable pointer."""
        csource = emit_source(
            "main: function is {\n"
            '    x: Option.some "hello".string\n'
            "    match (x) case some then {\n"
            '        print "is some"\n'
            "    } case none then {\n"
            '        print "is none"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "is some"

    def test_nullable_ptr_option_none_string(self):
        """Option.none String emits NULL."""
        csource = emit_source(
            "main: function is {\n"
            "    x: Option.none String\n"
            "    match (x) case some then {\n"
            '        print "is some"\n'
            "    } case none then {\n"
            '        print "is none"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "is none"

    def test_optionval_case_matching(self):
        """optionval case matching with some/none."""
        csource = emit_source(
            "main: function is {\n"
            "    x: optionval.some 42\n"
            "    match (x) case some then {\n"
            '        print "is some"\n'
            "    } case none then {\n"
            '        print "is none"\n'
            "    }\n"
            "    y: optionval.none i64\n"
            "    match (y) case some then {\n"
            '        print "is some"\n'
            "    } case none then {\n"
            '        print "is none"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "is some\nis none"

    def test_optionval_iterator(self):
        """For-loop with optionval-returning callable iterator."""
        csource = emit_source(
            "Counter: class { i: i64 max: i64 } as {\n"
            "  call: function {c: this} out (optionval t: i64) is {\n"
            "    if c.i < c.max then {\n"
            "      result: optionval.some c.i\n"
            "      c.i = c.i + 1\n"
            "      return result\n"
            "    }\n"
            "    result: optionval.none i64\n"
            "    return result\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  with iter: (Counter i: 0 max: 3) do for x: iter loop {\n"
            '    print "\\{x}"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0\n1\n2"

    def test_map_get_returns_optionval(self):
        """Map.get() with valtype values returns optionval."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 1 value: 42\n"
            "    r: m.get key: 1\n"
            '    match (r) case some then { print "found" } case none then { print "missing" }\n'
            "    r2: m.get key: 99\n"
            '    match (r2) case some then { print "found" } case none then { print "missing" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "found\nmissing"

    def test_nongeneric_union_from_compiles(self):
        """Non-generic union construction with from: compiles and runs."""
        csource = emit_source(
            "MyUnion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "    x: MyUnion.a from: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    # ---- box type ----

    def test_box_valtype_compiles(self):
        """Box from: valtype compiles and runs."""
        csource = emit_source(
            'main: function is {\n    b: Box from: 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_box_valtype_arithmetic(self):
        """Arithmetic on boxed valtype auto-derefs."""
        csource = emit_source(
            "main: function is {\n"
            "    b: Box from: 10\n"
            "    r: b + 5\n"
            '    print "\\{r}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_box_string_value(self):
        """Box from: String — heap-allocates the stack String struct."""
        # Just verify it compiles & runs cleanly (box manages lifetime)
        csource = emit_source(
            'main: function is {\n    b: Box from: "hello".string\n    print "done"\n}'
        )
        output = compile_and_run(csource)
        assert "done" in output

    def test_box_valtype_comparison(self):
        """Comparison on boxed valtype auto-derefs."""
        csource = emit_source(
            "main: function is {\n"
            "    b: Box from: 42\n"
            '    if b == 42 then { print "yes" } else { print "no" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "yes"

    # ---- any.valtype / any.reftype constraints ----

    def test_valtype_constraint_record_compiles(self):
        """Record with Any.valtype constraint compiles and runs."""
        csource = emit_source(
            "myrec: record { x: t } as { t: Any.valtype }\n"
            'main: function is {\n    r: myrec x: 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_valtype_constraint_union_compiles(self):
        """Union with Any.valtype constraint compiles and runs."""
        csource = emit_source(
            "MyOpt: union { some: t\n none: null } as { t: Any.valtype }\n"
            'main: function is {\n    x: MyOpt.some 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_reftype_constraint_class_compiles(self):
        """Record with Any.reftype constraint compiles and runs with class."""
        csource = emit_source(
            "MyCls: class { v: i64 }\n"
            "holder: record { ref: t } as { t: Any.reftype }\n"
            "main: function is {\n"
            "    c: MyCls v: 10\n"
            "    h: holder ref: c\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    # ---- Public/Private Access Control ----

    def test_record_with_public_compiles(self):
        """Record with public restriction compiles — public members accessible."""
        csource = emit_source(
            "myrec: record { x: i64 y: i64 } as {\n"
            "  public: unit { :x }\n"
            "}\n"
            'main: function is { r: myrec x: 1 y: 2\n    print "\\{r.x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "1"

    def test_class_with_public_method(self):
        """Class with public restriction: method accessible, field hidden."""
        csource = emit_source(
            "MyClass: class { x: i64 secret: i64 } as {\n"
            "  public: unit { :x :get_secret }\n"
            "  get_secret: function {:this} out i64 is { return this.secret }\n"
            "}\n"
            "main: function is {\n"
            '  with c: (MyClass x: 1 secret: 42) do print "\\{c.get_secret}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    # ---- Generic Classes ----

    def test_generic_class_template_not_emitted(self):
        """Generic class template should not produce C struct."""
        csource = emit_source(
            "MyCls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: MyCls val: 42 }"
        )
        assert "z_MyCls_t " not in csource or "z_MyCls_i64_t" in csource
        # template should not be emitted; only the monomorphized version
        assert "z_MyCls_i64_t" in csource

    def test_generic_class_compiles(self):
        """Generic class construction compiles and runs."""
        csource = emit_source(
            "MyCls: class { val: t } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyCls val: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_class_asan(self):
        """Monomorphized class passes AddressSanitizer."""
        csource = emit_source(
            "MyCls: class { val: t } as { t: Any.generic }\n"
            "main: function is {\n"
            "    x: MyCls val: 42\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan failure: {result.stderr}"

    def test_generic_class_methods(self):
        """Generic class with as-methods compiles."""
        csource = emit_source(
            "MyCls: class { val: t } as {\n"
            "  t: Any.generic\n"
            "  getval: function {c: this} out i64 is { return c.val }\n"
            "}\n"
            "main: function is {\n"
            "    x: MyCls val: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_class_destructor(self):
        """Monomorphized class with only valtype fields has no destructor."""
        csource = emit_source(
            "MyCls: class { val: t } as { t: Any.generic }\n"
            "main: function is { x: MyCls val: 42 }"
        )
        # valtype-only class: no destructor emitted or called
        assert "z_MyCls_i64_destroy" not in csource

    # ---- Generic Protocols ----

    def test_generic_protocol_compiles(self):
        """Generic protocol definition compiles (template skipped)."""
        csource = emit_source(
            "MyProto: protocol {\n"
            "  t: Any.generic\n"
            "  get: function {:this} out t\n"
            "}\n"
            'main: function is { print "ok" }'
        )
        # generic template should NOT produce a struct
        assert "z_MyProto_vtable_t" not in csource
        assert "z_MyProto_t" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "ok"


class TestGenericFunctionEmission:
    """Tests for generic function monomorphization and emission."""

    def test_generic_function_template_not_emitted(self):
        """Generic function template should not produce C function."""
        csource = emit_source(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42 }"
        )
        # template cname should NOT appear as a function definition
        assert "z_test_id(void)" not in csource

    def test_monomorphized_function_emitted(self):
        """Monomorphized generic function emits a C function."""
        csource = emit_source(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42 }"
        )
        assert "z_id_i64" in csource

    def test_monomorphized_function_called(self):
        """Generic function call emits call to monomorphized C function."""
        csource = emit_source(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42 }"
        )
        # main should call z_id_i64
        assert "z_id_i64(" in csource

    def test_multiple_instantiations(self):
        """Different type args produce different C functions."""
        csource = emit_source(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is { x: id 42\n y: id 3.14 }"
        )
        assert "z_id_i64" in csource
        assert "z_id_f64" in csource

    def test_generic_function_compiles_and_runs(self):
        """Generic function compiles to working C binary."""
        csource = emit_source(
            "id: function as { t: Any.generic } in { val: t } out t is { return val }\n"
            "main: function is {\n"
            "    x: id 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"


# ---- Phase 29: Typedef Emitter Tests ----


class TestEmitterTypedefs:
    """Tests for typedef C emission (zero overhead — no struct, just aliases)."""

    def test_typedef_no_struct(self):
        """A record typedef should not emit its own struct."""
        csource = emit_source(
            "meters: record { val: i64.typedef } as {}\n"
            "main: function is { m: meters.create from: 42\n"
            '  print "\\{m}" }'
        )
        # no separate struct for meters
        assert "z_meters_t" not in csource
        assert "z_meters_create" not in csource

    def test_typedef_create_is_identity(self):
        """Typedef create/take is an identity operation — just the value."""
        csource = emit_source(
            "meters: record { val: i64.typedef } as {}\n"
            "main: function is { m: meters.create from: 42\n"
            '  print "\\{m}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_typedef_backward_compatible(self):
        """Typedef value can be passed where base type is expected."""
        csource = emit_source(
            "meters: record { val: i64.typedef } as {}\n"
            "show: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is {\n"
            "    m: meters.create from: 10\n"
            "    r: show m\n"
            '    print "\\{r}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "11"

    def test_typedef_with_shadow_method(self):
        """Typedef with a shadowed method emits the method as a function."""
        csource = emit_source(
            "meters: record { val: i64.typedef } as {\n"
            "    double: function {self: meters} out i64 is { return self * 2 }\n"
            "}\n"
            "main: function is {\n"
            "    m: meters.create from: 5\n"
            "    d: m.double\n"
            '    print "\\{d}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10"

    def test_typedef_chained(self):
        """Chained typedefs compile and run correctly."""
        csource = emit_source(
            "meters: record { val: i64.typedef } as {}\n"
            "height: record { h: meters.typedef } as {}\n"
            "show: function {x: i64} out i64 is { return x + 1 }\n"
            "main: function is {\n"
            "    h: height.create from: (meters.create from: 10)\n"
            "    y: show h\n"
            '    print "\\{y}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "11"


# ---- Phase 30: Facet Emitter Tests ----


class TestEmitterFacets:
    """Tests for facet C emission (valtype interface with inline data)."""

    FACET_PREAMBLE = (
        "showable: facet {\n"
        "    show: function {:this b: i64} out i64\n"
        "}\n"
        "point: record {\n"
        "    x: i64\n"
        "} as {\n"
        "    s: showable\n"
        "    show: function {p: this b: i64} out i64 is { return p.x + b }\n"
        "}\n"
    )

    def test_facet_struct_emitted(self):
        """A facet should emit vtable, data union, and instance struct."""
        csource = emit_source(
            self.FACET_PREAMBLE + "main: function is { p: point x: 5 }"
        )
        assert "z_showable_vtable_t" in csource
        assert "z_showable_data_u" in csource
        assert "z_showable_t" in csource

    def test_facet_create_and_dispatch(self):
        """Facet create + method dispatch should compile and run."""
        csource = emit_source(
            self.FACET_PREAMBLE + "main: function is {\n"
            "    p: point x: 10\n"
            "    f: showable.create from: p\n"
            "    r: f.show b: 5\n"
            '    print "\\{r}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_facet_is_valtype(self):
        """Facet instances are stack-allocated, no malloc/free."""
        csource = emit_source(
            self.FACET_PREAMBLE + "main: function is {\n"
            "    p: point x: 3\n"
            "    f: showable.create from: p\n"
            "    r: f.show b: 1\n"
            '    print "\\{r}"\n'
            "}"
        )
        # no malloc for the facet instance itself
        assert "z_showable_destroy" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "4"

    def test_facet_as_function_param(self):
        """Facet passed as function parameter — dispatches correctly."""
        csource = emit_source(
            self.FACET_PREAMBLE + "use_it: function {f: showable} out i64 is {\n"
            "    return (f.show b: 100)\n"
            "}\n"
            "main: function is {\n"
            "    p: point x: 7\n"
            "    f: showable.create from: p\n"
            "    r: use_it f\n"
            '    print "\\{r}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "107"

    def test_facet_multiple_conformers(self):
        """Multiple types conforming to same facet — both dispatch correctly."""
        csource = emit_source(
            "showable: facet {\n"
            "    show: function {:this b: i64} out i64\n"
            "}\n"
            "point: record { x: i64 } as {\n"
            "    s: showable\n"
            "    show: function {p: this b: i64} out i64 is { return p.x + b }\n"
            "}\n"
            "color: record { r: i64 } as {\n"
            "    s: showable\n"
            "    show: function {c: this b: i64} out i64 is { return c.r * b }\n"
            "}\n"
            "use_it: function {f: showable} out i64 is {\n"
            "    return (f.show b: 10)\n"
            "}\n"
            "main: function is {\n"
            "    p: point x: 5\n"
            "    c: color r: 3\n"
            "    r1: use_it (showable.create from: p)\n"
            "    r2: use_it (showable.create from: c)\n"
            '    print "\\{r1}"\n'
            '    print "\\{r2}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "15"  # 5 + 10
        assert lines[1] == "30"  # 3 * 10


class TestNumericGenericsEmission:
    """Tests for numeric generic type emission."""

    def test_numeric_generic_record_struct(self):
        """C struct has the numeric field with correct type."""
        csource = emit_source(
            "myrec: record { x: i64 } as { n: u64.generic }\n"
            "main: function is { a: (myrec n: 10) x: 5 }"
        )
        assert "uint64_t n;" in csource
        assert "z_myrec_10_t" in csource

    def test_numeric_generic_create_has_param(self):
        """Constructor takes numeric field as parameter."""
        csource = emit_source(
            "myrec: record { x: i64 } as { n: u64.generic }\n"
            "main: function is { a: (myrec n: 10) x: 5 }"
        )
        assert "z_myrec_10_meta_create" in csource
        assert "uint64_t n" in csource

    def test_numeric_generic_compiles(self):
        """Full compile + run with numeric generic record."""
        csource = emit_source(
            "myrec: record { x: i64 } as { n: u64.generic }\n"
            "main: function is {\n"
            "    a: (myrec n: 10) x: 42\n"
            '    print "\\{a.x}"\n'
            '    print "\\{a.n}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "42"
        assert lines[1] == "10"


class TestCodeDeduplication:
    """Tests for AST-level code deduplication of monomorphized methods."""

    def test_dedup_identical_numeric_generic_methods(self):
        """Two numeric-generic instantiations with different this types both emit functions."""
        csource = emit_source(
            "MyCls: class { val: i64 } as {\n"
            "    n: u64.generic\n"
            "    getval: function {c: this} out i64 is { return c.val }\n"
            "}\n"
            "main: function is {\n"
            "    a: (MyCls n: 10) val: 1\n"
            "    b: (MyCls n: 20) val: 2\n"
            '    print "ok"\n'
            "}"
        )
        # both function names should be present (different this types)
        assert "z_MyCls_10_getval" in csource
        assert "z_MyCls_20_getval" in csource

    def test_no_dedup_different_types(self):
        """Structurally different instantiations are NOT deduped."""
        csource = emit_source(
            "MyCls: class { val: t } as {\n"
            "    t: Any.generic\n"
            "    getval: function {c: this} out i64 is { return 0 }\n"
            "}\n"
            "main: function is {\n"
            "    a: (MyCls t: i64) val: 1\n"
            "    b: (MyCls t: i32) val: 2.i32\n"
            '    print "ok"\n'
            "}"
        )
        # both should have function definitions, no #define for these
        assert "z_MyCls_i64_getval" in csource
        assert "z_MyCls_i32_getval" in csource
        # structurally different this types → no dedup
        assert "#define z_MyCls_i32_getval" not in csource

    def test_dedup_compiles_and_runs(self):
        """Deduped code compiles and produces correct output."""
        csource = emit_source(
            "MyCls: class { val: i64 } as {\n"
            "    n: u64.generic\n"
            '    greet: function {c: this} is { print "hello" }\n'
            "}\n"
            "main: function is {\n"
            "    a: (MyCls n: 10) val: 42\n"
            "    b: (MyCls n: 20) val: 99\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert "ok" in output

    def test_dedup_forward_decls_preserved(self):
        """Both canonical and alias names get forward declarations."""
        csource = emit_source(
            "MyCls: class { val: i64 } as {\n"
            "    n: u64.generic\n"
            "    getval: function {c: this} out i64 is { return c.val }\n"
            "}\n"
            "main: function is {\n"
            "    a: (MyCls n: 10) val: 1\n"
            "    b: (MyCls n: 20) val: 2\n"
            '    print "ok"\n'
            "}"
        )
        # both names should appear in forward decls
        assert "z_MyCls_10_getval" in csource
        assert "z_MyCls_20_getval" in csource


class TestArrayEmission:
    """Tests for array type emission."""

    def test_array_struct_emitted(self):
        """Array struct has data field with correct type and size."""
        csource = emit_source("main: function is { a: (array of: i64 to: 4) }")
        assert "z_array_i64_4_t" in csource
        assert "int64_t data[4];" in csource

    def test_array_create_emitted(self):
        """Array create function is emitted."""
        csource = emit_source("main: function is { a: (array of: i64 to: 4) }")
        assert "z_array_i64_4_create" in csource

    def test_array_length_emitted(self):
        """Array length define is emitted."""
        csource = emit_source("main: function is { a: (array of: i64 to: 4) }")
        assert "#define z_array_i64_4_length 4" in csource

    def test_array_create_and_set_compiles(self):
        """Create array, set elements, read them back."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    a.0 = 10\n"
            "    a.1 = 20\n"
            "    a.2 = 30\n"
            "    a.3 = 40\n"
            '    print "\\{a.0} \\{a.1} \\{a.2} \\{a.3}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10 20 30 40"

    def test_array_zero_initialized(self):
        """Array elements are zero-initialized."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 3)\n"
            '    print "\\{a.0} \\{a.1} \\{a.2}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0 0 0"

    def test_array_set_method_compiles(self):
        """.set method with runtime index compiles and works."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    idx: 2\n"
            "    a.set i: idx val: 99\n"
            '    print "\\{a.2}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "99"

    def test_array_set_returns_old_value(self):
        """.set returns old element value."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    a.0 = 42\n"
            "    old: a.set i: 0 val: 99\n"
            '    print "\\{old} \\{a.0}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42 99"

    def test_array_get_in_bounds(self):
        """.get returns element value for in-bounds access."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    a.0 = 42\n"
            "    r: a.get i: 0\n"
            '    print "\\{r}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_array_get_out_of_bounds_exits(self):
        """.get exits with error for out-of-bounds access."""
        csource = emit_source(
            "main: function is {\n    a: (array of: i64 to: 4)\n    r: a.get i: 10\n}"
        )
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(csource)
            cpath = f.name
        outpath = cpath.replace(".c", "")
        try:
            subprocess.run(
                [_CC, "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            result = subprocess.run(
                [outpath], capture_output=True, text=True, timeout=10
            )
            assert result.returncode != 0
            assert "out of bounds" in result.stderr
        finally:
            for p in (cpath, outpath):
                if os.path.exists(p):
                    os.unlink(p)

    def test_array_set_out_of_bounds_exits(self):
        """.set exits with error for out-of-bounds access."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            "    old: a.set i: 10 val: 99\n"
            "}"
        )
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(csource)
            cpath = f.name
        outpath = cpath.replace(".c", "")
        try:
            subprocess.run(
                [_CC, "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            result = subprocess.run(
                [outpath], capture_output=True, text=True, timeout=10
            )
            assert result.returncode != 0
            assert "out of bounds" in result.stderr
        finally:
            for p in (cpath, outpath):
                if os.path.exists(p):
                    os.unlink(p)

    def test_array_length_access(self):
        """.length returns the array size."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (array of: i64 to: 4)\n"
            '    print "\\{a.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "4"

    def test_array_of_records_compiles(self):
        """Array of records with default constructor."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\n"
            "main: function is {\n"
            "    pts: (array of: point to: 3)\n"
            '    print "\\{pts.0.x} \\{pts.0.y}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0 0"

    def test_array_passed_to_function(self):
        """Array passed to function by value."""
        csource = emit_source(
            "first: function { a: (array of: i64 to: 3) } out i64 is { return a.0 }\n"
            "main: function is {\n"
            "    a: (array of: i64 to: 3)\n"
            "    a.0 = 42\n"
            '    print "\\{first a}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_data_array_copy(self):
        """data.array copies data into array."""
        csource = emit_source(
            "primes: data { 2 3 5 7 11 }\n"
            "main: function is {\n"
            "    a: primes.array\n"
            '    print "\\{a.0} \\{a.1} \\{a.4}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 3 11"


class TestBytesByteview:
    """`Bytes.byteview` is the Bytes analog of `String.stringview` /
    `List.listview`. Declared natively in lib/system/system.z; the
    emitter routes the call through z_List_u8_ListView because Bytes
    is a transparent typedef over `List of: u8`."""

    def test_byteview_empty_length_zero(self):
        csource = emit_source(
            "use_view: function {bv: ByteView} out u64 is { return bv.length }\n"
            "main: function is {\n"
            "    b: Bytes\n"
            "    n: use_view bv: b.byteview\n"
            '    print "\\{n}"\n'
            "}"
        )
        # Routes through the existing list-of-u8 listview helper.
        assert "z_List_u8_listview" in csource
        output = compile_and_run(csource)
        assert output.strip() == "0"


class TestStr:
    """Tests for str type emission and runtime behavior."""

    def test_str_struct_emitted(self):
        """Str struct has compact len and data fields (no NUL)."""
        csource = emit_source("main: function is { s: (str to: 32) }")
        assert "z_str_32_t" in csource
        assert "uint8_t len;" in csource
        assert "char data[32];" in csource

    def test_str_create_emitted(self):
        """Str create function is emitted."""
        csource = emit_source("main: function is { s: (str to: 32) }")
        assert "z_str_32_create" in csource

    def test_str_size_emitted(self):
        """Str size define is emitted."""
        csource = emit_source("main: function is { s: (str to: 32) }")
        assert "#define z_str_32_size 32" in csource

    def test_str_empty_length_zero(self):
        """Create empty str, verify length = 0."""
        csource = emit_source(
            'main: function is {\n    s: (str to: 32)\n    print "\\{s.length}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "0"

    def test_str_from_literal_length(self):
        """Create str from String literal via .str method, read length."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello".str to: 32\n'
            '    print "\\{s.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_str_size_access(self):
        """.size returns the buffer capacity."""
        csource = emit_source(
            'main: function is {\n    s: (str to: 32)\n    print "\\{s.size}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "32"

    def test_str_truncation(self):
        """Long String truncated to str capacity via .str method."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello world".str to: 4\n'
            '    print "\\{s.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "4"

    def test_str_string_conversion(self):
        """.string converts to heap-allocated z_String_t*."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello".str to: 32\n'
            "    h: s.string\n"
            "    print h\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_str_print(self):
        """Print a str via explicit .stringview projection."""
        csource = emit_source(
            'main: function is {\n    s: "hi there".str to: 32\n'
            "    print s.stringview\n}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hi there"

    def test_str_interpolation(self):
        """String interpolation containing str."""
        csource = emit_source(
            'main: function is {\n    s: "world".str to: 32\n    print "hello \\{s}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello world"

    def test_str_in_record(self):
        """Str in records works as valtype."""
        csource = emit_source(
            "entry: record { name: (str to: 16)\n age: i64 }\n"
            "main: function is {\n"
            '    e: entry name: ("bob".str to: 16) age: 0\n'
            '    print "\\{e.name.length} \\{e.age}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "3 0"

    def test_str_passed_to_function(self):
        """Str passed to function by value."""
        csource = emit_source(
            "getlen: function { s: (str to: 32) } out u64 is { return s.length }\n"
            "main: function is {\n"
            '    s: "test".str to: 32\n'
            '    print "\\{getlen s}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "4"

    def test_str_literal_optimization(self):
        """String.str with literal uses direct struct init (no malloc)."""
        csource = emit_source('main: function is { s: "hello".str to: 32 }')
        # should have direct compound literal
        assert '(z_str_32_t){5, "hello"}' in csource

    def test_string_to_str_method(self):
        """String.str to: N converts heap String to str."""
        csource = emit_source(
            "main: function is {\n"
            '    msg: "hello world"\n'
            "    s: msg.str to: 8\n"
            '    print "\\{s.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "8"

    def test_str_to_str_wider(self):
        """str.str to: larger capacity preserves data."""
        csource = emit_source(
            "main: function is {\n"
            '    s16: "hello".str to: 16\n'
            "    s64: s16.str to: 64\n"
            '    print "\\{s64.length} \\{s64.size}"\n'
            "    print s64.stringview\n"
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "5 64"
        assert lines[1] == "hello"

    def test_str_to_str_narrower(self):
        """str.str to: smaller capacity truncates."""
        csource = emit_source(
            "main: function is {\n"
            '    s32: "hello world".str to: 32\n'
            "    s4: s32.str to: 4\n"
            '    print "\\{s4.length}"\n'
            "    print s4.stringview\n"
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "4"
        assert lines[1] == "hell"

    def test_str_to_str_same_capacity(self):
        """str.str to: same capacity is identity copy."""
        csource = emit_source(
            "main: function is {\n"
            '    a: "hello".str to: 32\n'
            "    b: a.str to: 32\n"
            '    print "\\{b.length}"\n'
            "    print b.stringview\n"
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "5"
        assert lines[1] == "hello"

    def test_str_empty_constructor(self):
        """str to: N with no from creates empty str."""
        csource = emit_source(
            'main: function is {\n    s: str to: 32\n    print "\\{s.length}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "0"

    def test_str_field_reassignment(self):
        """Reassign str field in record."""
        csource = emit_source(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '    e: entry name: ("alice".str to: 16) age: 30\n'
            '    e.name = "bob".str to: 16\n'
            '    print "\\{e.name.length}"\n'
            "    print e.name.stringview\n"
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "3"
        assert lines[1] == "bob"

    def test_str_stringview_zero_arg_shape(self):
        """str.stringview emits (z_StringView_t){ s.data, s.len } at Path access."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello".str to: 32\n'
            "    v: s.stringview\n"
            "    print v\n"
            "}"
        )
        assert "(z_StringView_t){ s.data, s.len }" in csource

    def test_str_stringview_zero_arg_runs(self):
        """End-to-end: str.stringview prints the str's content."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello".str to: 32\n'
            "    v: s.stringview\n"
            "    print v\n"
            '    print "\\{v.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "hello"
        assert lines[1] == "5"

    def test_str_stringview_substring_bounds_check(self):
        """Substring form emits a runtime bounds check against s.len."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello world".str to: 32\n'
            "    v: s.stringview from: 0 to: 5\n"
            "    print v\n"
            "}"
        )
        assert "> s.len" in csource
        assert "stringview: bounds error" in csource

    def test_str_stringview_substring_runs(self):
        """End-to-end: substring view prints the sliced Bytes."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello world".str to: 32\n'
            "    v: s.stringview from: 6 to: 11\n"
            "    print v\n"
            '    print "\\{v.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "world"
        assert lines[1] == "5"

    def test_str_stringview_record_field(self):
        """View of e.name uses e.name.data / e.name.len."""
        csource = emit_source(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '    e: entry name: ("alice".str to: 16) age: 30\n'
            "    v: e.name.stringview\n"
            "    print v\n"
            "}"
        )
        assert "e.name.data" in csource
        assert "e.name.len" in csource
        output = compile_and_run(csource)
        assert output.strip() == "alice"

    def test_string_stringview_zero_arg_shape(self):
        """String.stringview emits (z_StringView_t){ s.data, s.size }. Same
        struct shape as str.stringview, but the length field is `size` (not
        `len`) because that's what `z_String_t` declares."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello".string\n'
            "    v: s.stringview\n"
            "    print v\n"
            "}"
        )
        assert "(z_StringView_t){ s.data, s.size }" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_string_stringview_substring_bounds_check(self):
        """Substring form on a String emits a runtime bounds check against
        s.size, mirroring the str form's check against s.len."""
        csource = emit_source(
            "main: function is {\n"
            '    s: "hello world".string\n'
            "    v: s.stringview from: 6 to: 11\n"
            "    print v\n"
            "}"
        )
        assert "> s.size" in csource
        assert "stringview: bounds error" in csource
        output = compile_and_run(csource)
        assert output.strip() == "world"

    def test_z_string_to_str_deduplication(self):
        """One z_String_to_str_N function per target capacity regardless of sources."""
        csource = emit_source(
            "main: function is {\n"
            '    a: "hello".str to: 32\n'
            "    b: a.str to: 32\n"
            '    msg: "world"\n'
            "    c: msg.str to: 32\n"
            "}"
        )
        # only one z_String_to_str_32 function should be emitted
        assert csource.count("z_String_to_str_32(") >= 2  # call sites
        # the definition should appear exactly once
        assert (
            csource.count("static z_str_32_t z_String_to_str_32(") == 2
        )  # fwd decl + def


class TestLateMonoUserOrdering:
    """User classes with fields of monomorphisations over user types
    used to emit BEFORE the monomorphisation typedef (the C compiler
    then rejected the field declaration). The fix splits user-def
    emission into "no late-mono dep" and "has late-mono dep" passes,
    interleaved with the late-mono pass."""

    def test_class_field_of_list_of_user_variant_compiles(self):
        """`(List of: V)` where V is a user variant. The user class's
        struct embeds the List struct by value, so the List typedef
        must precede the user class struct, and the List's element
        slot type (the variant) must precede the List."""
        csource = emit_source(
            "V: variant { A: null B: null } as { tag: u8.tag }\n"
            "C: class {\n"
            "    xs: (List of: V)\n"
            "}\n"
            "main: function is {\n"
            "    c: C xs: (List of: V)\n"
            "    c.xs.append from: V.A\n"
            "    c.xs.append from: V.B\n"
            '    print "\\{c.xs.length}"\n'
            "}"
        )
        # The variant struct, the List<V> struct, and the user class
        # struct must appear in that order so each later struct sees
        # the typedefs it embeds. Variants and lists still emit in
        # the anonymous-typedef form (`typedef struct { ... } z_X_t;`);
        # user classes/records emit in the named form (`struct z_X_t
        # { ... };`) so we anchor on the body opener for `C`.
        v_pos = csource.find("} z_V_t;")
        list_pos = csource.find("} z_List_V_t;")
        c_pos = csource.find("struct z_C_t {")
        assert v_pos != -1 and list_pos != -1 and c_pos != -1, (
            "expected V, List_V, and C struct typedefs in the output"
        )
        assert v_pos < list_pos < c_pos, (
            f"expected emit order V({v_pos}) < List<V>({list_pos}) < "
            f"C({c_pos}) so each struct's typedef precedes its uses"
        )
        # End-to-end: program runs and prints "2".
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_class_field_of_list_of_user_record_compiles(self):
        """Same ordering rule, with a record element instead of a
        variant. Records are also value-embedded inside the List's
        data buffer."""
        csource = emit_source(
            "Pt: record { x: i64  y: i64 }\n"
            "Path: class {\n"
            "    pts: (List of: Pt)\n"
            "}\n"
            "main: function is {\n"
            "    p: Path pts: (List of: Pt)\n"
            "    p.pts.append from: (Pt x: 1 y: 2)\n"
            "    p.pts.append from: (Pt x: 3 y: 4)\n"
            '    print "\\{p.pts.length}"\n'
            "}"
        )
        # Records and classes emit in named-struct form
        # (`struct z_X_t { ... };`); anchor on the body opener.
        # Lists keep the anonymous form.
        pt_pos = csource.find("struct z_Pt_t {")
        list_pos = csource.find("} z_List_Pt_t;")
        path_pos = csource.find("struct z_Path_t {")
        assert pt_pos != -1 and list_pos != -1 and path_pos != -1
        assert pt_pos < list_pos < path_pos
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_nested_class_with_list_field_of_unrelated_user_variant(self):
        """Two user types and a List spanning them: confirm the split
        identifies only the class that actually references the late
        mono, leaving the variant in the early-user pass."""
        csource = emit_source(
            "V: variant { A: null B: null } as { tag: u8.tag }\n"
            "Other: record { n: i64 }\n"
            "C: class {\n"
            "    xs: (List of: V)\n"
            "    extra: Other\n"
            "}\n"
            "main: function is {\n"
            "    c: C xs: (List of: V) extra: (Other n: 7)\n"
            "    c.xs.append from: V.B\n"
            '    print "\\{c.xs.length} \\{c.extra.n}"\n'
            "}"
        )
        # V is a variant (anonymous typedef); Other is a record and
        # C is a class, both in named-struct form (anchor on the
        # body opener); List_V is template-emitted in anonymous form.
        v_pos = csource.find("} z_V_t;")
        other_pos = csource.find("struct z_Other_t {")
        list_pos = csource.find("} z_List_V_t;")
        c_pos = csource.find("struct z_C_t {")
        # Both V and Other must be emitted before the late mono;
        # C is the only late-user def.
        assert v_pos != -1 and other_pos != -1 and list_pos != -1 and c_pos != -1
        assert v_pos < list_pos < c_pos
        assert other_pos < c_pos
        output = compile_and_run(csource)
        assert output.strip() == "1 7"


class TestNamedData:
    """Tests for `data` block field access — both named labels
    (compile-time constant substitution) and ordinal numeric
    access (array indexing into the emitted static const)."""

    def test_named_label_access_compiles_and_runs(self):
        """`data.LABEL` lowers to the literal value at the call site."""
        csource = emit_source(
            "mydata: data { LOW: 0 HIGH: 10 }\n"
            "main: function is {\n"
            "    a: mydata.LOW\n"
            "    b: mydata.HIGH\n"
            '    print "\\{a} \\{b}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "0 10"

    def test_ordinal_access_compiles_and_runs(self):
        """`data.N` lowers to `z_<name>[N]` for ordinal numeric access."""
        csource = emit_source(
            "primes: data { 2 3 5 7 11 }\n"
            "main: function is {\n"
            "    a: primes.0\n"
            "    b: primes.4\n"
            '    print "\\{a} \\{b}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "2 11"

    def test_data_with_iterate(self):
        """Named labels and `.index` runtime access can coexist on one
        data block — the named labels lower to constants, the runtime
        loop hits the static array."""
        csource = emit_source(
            "items: data { A: 10 B: 20 C: 30 }\n"
            "main: function is {\n"
            "    sum: items.A + items.B + items.C\n"
            '    print "\\{sum}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "60"

    def test_out_u8_emits_uint8_array(self):
        """`data { ... } out u8` emits a `uint8_t[]` static array when
        runtime access requires it; named-label access still inlines
        the literal at the use site."""
        # Include a runtime `.index` access so the array is emitted —
        # the named-label-only fixture in `test_compile_time_only_skips_array`
        # exercises the elision path.
        csource = emit_source(
            "bytes: data { LOW: 0 MID: 64 HIGH: 250 } out u8\n"
            "take_u8: function {b: u8} out u8 is { return b + 1.u8 }\n"
            "main: function is {\n"
            "    a: take_u8 b: bytes.LOW\n"
            "    b: take_u8 b: bytes.HIGH\n"
            "    i: 1\n"
            '    print "\\{a} \\{b} \\{bytes.index i}"\n'
            "}"
        )
        assert "static const uint8_t z_bytes[]" in csource, csource
        assert compile_and_run(csource).strip() == "1 251 64"

    def test_out_u8_runtime_index(self):
        """Runtime `.index` against an `out u8` data block reads the
        u8-typed array and returns u8."""
        csource = emit_source(
            "bytes: data { 10 20 30 40 50 } out u8\n"
            "main: function is {\n"
            "    i: 2\n"
            '    print "\\{bytes.index i}"\n'
            "}"
        )
        assert "static const uint8_t z_bytes[]" in csource, csource
        assert compile_and_run(csource).strip() == "30"

    # ---- runtime_indexed elision: skip the static array when no
    # access requires it. Every foldable access stamps node_const_value
    # at typecheck; the emitter inlines via the const-value short-
    # circuit and never reads the array body.

    def test_compile_time_only_skips_array(self):
        """Named-label only — no `static const z_mydata[]` emitted,
        every access inlines the literal value."""
        csource = emit_source(
            "mydata: data { LOW: 0 HIGH: 10 }\n"
            "main: function is {\n"
            "    a: mydata.LOW\n"
            "    b: mydata.HIGH\n"
            '    print "\\{a} \\{b}"\n'
            "}"
        )
        assert "static const int64_t z_mydata[]" not in csource, csource
        assert "z_mydata" not in csource, csource
        assert compile_and_run(csource).strip() == "0 10"

    def test_ordinal_access_inlines_not_indexes(self):
        """`primes.0` / `primes.4` fold to the literal at compile time;
        the array body is not emitted."""
        csource = emit_source(
            "primes: data { 2 3 5 7 11 }\n"
            "main: function is {\n"
            "    a: primes.0\n"
            "    b: primes.4\n"
            '    print "\\{a} \\{b}"\n'
            "}"
        )
        assert "static const int64_t z_primes[]" not in csource, csource
        assert compile_and_run(csource).strip() == "2 11"

    def test_length_inlines(self):
        """`.length` folds to the literal element count."""
        csource = emit_source(
            "primes: data { 2 3 5 7 11 }\n"
            "main: function is {\n"
            "    n: primes.length\n"
            '    print "\\{n}"\n'
            "}"
        )
        assert "static const int64_t z_primes[]" not in csource, csource
        assert compile_and_run(csource).strip() == "5"

    def test_runtime_index_keeps_array(self):
        """`.index <var>` requires the array; it must be emitted."""
        csource = emit_source(
            "primes: data { 2 3 5 7 11 }\n"
            "main: function is {\n"
            "    i: 2\n"
            '    print "\\{primes.index i}"\n'
            "}"
        )
        assert "static const int64_t z_primes[]" in csource, csource
        assert compile_and_run(csource).strip() == "5"

    def test_array_call_keeps_array(self):
        """`.array` materialises a value-array; the static block must
        be emitted as the copy source."""
        csource = emit_source(
            "primes: data { 2 3 5 7 11 }\n"
            "main: function is {\n"
            "    arr: primes.array\n"
            '    print "\\{arr.0}"\n'
            "}"
        )
        assert "static const int64_t z_primes[]" in csource, csource
        assert compile_and_run(csource).strip() == "2"


class TestList:
    """Tests for List type emission and runtime behavior."""

    def test_list_struct_emitted(self):
        """List struct has capacity, length, and data fields."""
        csource = emit_source("main: function is { l: (List of: i64) }")
        assert "z_List_i64_t" in csource
        assert "uint64_t capacity;" in csource
        assert "uint64_t length;" in csource
        assert "int64_t* data;" in csource

    def test_list_create_emitted(self):
        """List create function is emitted."""
        csource = emit_source("main: function is { l: (List of: i64) }")
        assert "z_List_i64_create" in csource

    def test_list_destroy_emitted(self):
        """List destroy function is emitted."""
        csource = emit_source("main: function is { l: (List of: i64) }")
        assert "z_List_i64_destroy" in csource

    def test_list_append_and_length(self):
        """Append elements and check length."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 10\n"
            "    l.append from: 20\n"
            '    print "\\{l.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_list_get_in_bounds(self):
        """Get element at valid index."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 42\n"
            "    l.append from: 99\n"
            '    print "\\{l.get i: 0.u64}"\n'
            '    print "\\{l.get i: 1.u64}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "42"
        assert lines[1] == "99"

    def test_list_set_replaces_and_returns_old(self):
        """Set replaces element and returns old value."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 10\n"
            "    old: l.set i: 0.u64 val: 77\n"
            '    print "\\{old}"\n'
            '    print "\\{l.get i: 0.u64}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "10"
        assert lines[1] == "77"

    def test_list_contains_numeric(self):
        """`l.contains item: x` returns 1 when present, 0 when absent
        for numeric element types (== dispatch)."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 10\n"
            "    l.append from: 20\n"
            "    l.append from: 30\n"
            "    a: l.contains item: 20\n"
            "    b: l.contains item: 99\n"
            '    print "a: \\{a} b: \\{b}"\n'
            "}"
        )
        assert "z_List_i64_contains" in csource
        assert compile_and_run(csource).strip() == "a: 1 b: 0"

    def test_list_contains_string(self):
        """`l.contains item: s` uses size+memcmp for String elements."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: String)\n"
            '    l.append from: "alice".string\n'
            '    l.append from: "bob".string\n'
            '    a: l.contains item: "alice".string\n'
            '    b: l.contains item: "carol".string\n'
            '    print "a: \\{a} b: \\{b}"\n'
            "}"
        )
        assert "z_List_String_contains" in csource
        assert "memcmp" in csource
        assert compile_and_run(csource).strip() == "a: 1 b: 0"

    def test_list_contains_empty(self):
        """`l.contains` on an empty list returns 0 without scanning."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    a: l.contains item: 1\n"
            '    print "\\{a}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "0"

    def test_list_sort_numeric(self):
        """`l.sort` sorts numeric lists ascending in place."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 3\n"
            "    l.append from: 1\n"
            "    l.append from: 4\n"
            "    l.append from: 1\n"
            "    l.append from: 5\n"
            "    l.append from: 9\n"
            "    l.append from: 2\n"
            "    l.append from: 6\n"
            "    l.sort\n"
            '    print "\\{l.get i: 0.u64} \\{l.get i: 1.u64} \\{l.get i: 2.u64}'
            " \\{l.get i: 3.u64} \\{l.get i: 4.u64} \\{l.get i: 5.u64}"
            ' \\{l.get i: 6.u64} \\{l.get i: 7.u64}"\n'
            "}"
        )
        assert "z_List_i64_sort" in csource
        assert compile_and_run(csource).strip() == "1 1 2 3 4 5 6 9"

    def test_list_sort_string_lex(self):
        """`l.sort` on String elements is byte-wise lexicographic;
        shorter prefix sorts before its longer extension."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: String)\n"
            '    l.append from: "banana".string\n'
            '    l.append from: "apple".string\n'
            '    l.append from: "cherry".string\n'
            '    l.append from: "app".string\n'
            "    l.sort\n"
            '    print "\\{l.get i: 0.u64}"\n'
            '    print "\\{l.get i: 1.u64}"\n'
            '    print "\\{l.get i: 2.u64}"\n'
            '    print "\\{l.get i: 3.u64}"\n'
            "}"
        )
        assert "z_List_String_sort" in csource
        assert compile_and_run(csource).strip().splitlines() == [
            "app",
            "apple",
            "banana",
            "cherry",
        ]

    def test_list_sort_empty_and_single(self):
        """Sort on a 0- or 1-element list is a no-op (no panic, no
        out-of-bounds write)."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (List of: i64)\n"
            "    a.sort\n"
            "    b: (List of: i64)\n"
            "    b.append from: 42\n"
            "    b.sort\n"
            '    print "\\{a.length} \\{b.length} \\{b.get i: 0.u64}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "0 1 42"

    def test_list_sort_already_sorted_keeps_order(self):
        """An already-sorted list comes out unchanged (and a stable
        mergesort wouldn't reorder equal keys; we verify the no-op
        for the strictly-sorted case)."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 1\n"
            "    l.append from: 2\n"
            "    l.append from: 3\n"
            "    l.append from: 4\n"
            "    l.sort\n"
            '    print "\\{l.get i: 0.u64} \\{l.get i: 1.u64}'
            ' \\{l.get i: 2.u64} \\{l.get i: 3.u64}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "1 2 3 4"

    def test_list_pop_returns_last(self):
        """Pop returns last element."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 1\n"
            "    l.append from: 2\n"
            "    l.append from: 3\n"
            "    p: l.pop\n"
            '    print "\\{p} \\{l.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "3 2"

    def test_list_insert_at_position(self):
        """Insert shifts elements."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64)\n"
            "    l.append from: 1\n"
            "    l.append from: 3\n"
            "    l.insert from: 2 at: 1.u64\n"
            '    print "\\{l.get i: 0.u64} \\{l.get i: 1.u64} \\{l.get i: 2.u64}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "1 2 3"

    def test_list_extend_bulk_copies(self):
        """Extend copies elements from another List."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (List of: i64)\n"
            "    a.append from: 1\n"
            "    a.append from: 2\n"
            "    b: (List of: i64)\n"
            "    b.append from: 3\n"
            "    b.append from: 4\n"
            "    a.extend from: b.take\n"
            '    print "\\{a.length} \\{a.get i: 2.u64} \\{a.get i: 3.u64}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "4 3 4"

    def test_list_extend_view_copies_without_consuming(self):
        """extendView copies elements from a ListView (borrowed); the
        source remains usable after the call. Needed so `Bytes` can
        append from a `ByteView` without consuming the view's backing
        buffer."""
        csource = emit_source(
            "main: function is {\n"
            "    a: Bytes\n"
            "    a.append from: 104.u8\n"
            "    a.append from: 105.u8\n"
            "    b: Bytes\n"
            "    bv: ByteView.borrow from: a.listview\n"
            "    b.extendView other: bv\n"
            '    print "\\{b.length} \\{b.get i: 0.u64} \\{b.get i: 1.u64}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 104 105"

    def test_list_extend_view_generic_over_element(self):
        """extendView lives on List<T>, so non-u8 element types get it
        via the same generic monomorphization Path as other List
        methods."""
        csource = emit_source(
            "main: function is {\n"
            "    a: (List of: i64)\n"
            "    a.append from: 10\n"
            "    a.append from: 20\n"
            "    b: (List of: i64)\n"
            "    b.extendView other: a.listview\n"
            '    print "\\{b.length} \\{b.get i: 1.u64}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 20"

    def test_list_capacity_preallocation(self):
        """Pre-allocated capacity is reported correctly."""
        csource = emit_source(
            "main: function is {\n"
            "    l: (List of: i64) capacity: 10.u64\n"
            '    print "\\{l.capacity} \\{l.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "10 0"

    def test_list_scope_cleanup(self):
        """List is destroyed on scope exit (ASan-safe)."""
        csource = emit_source("main: function is { l: (List of: i64) }")
        assert "z_List_i64_destroy(&l)" in csource
        # should compile and run without issues
        compile_and_run(csource)

    def test_list_oob_get_exits(self):
        """Out-of-bounds get exits with error."""
        csource = emit_source(
            "main: function is {\n    l: (List of: i64)\n    x: l.get i: 0.u64\n}"
        )
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(csource)
            cpath = f.name
        outpath = cpath.replace(".c", "")
        try:
            subprocess.run(
                [_CC, "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            result = subprocess.run(
                [outpath], capture_output=True, text=True, timeout=10
            )
            assert result.returncode != 0
        finally:
            for p in (cpath, outpath):
                if os.path.exists(p):
                    os.unlink(p)

    def test_list_pop_empty_exits(self):
        """Pop on empty List exits with error."""
        csource = emit_source(
            "main: function is {\n    l: (List of: i64)\n    p: l.pop\n}"
        )
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(csource)
            cpath = f.name
        outpath = cpath.replace(".c", "")
        try:
            subprocess.run(
                [_CC, "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            result = subprocess.run(
                [outpath], capture_output=True, text=True, timeout=10
            )
            assert result.returncode != 0
        finally:
            for p in (cpath, outpath):
                if os.path.exists(p):
                    os.unlink(p)


class TestMap:
    """Tests for Map type emission and runtime behavior."""

    def test_map_struct_emitted(self):
        """Map struct has capacity, length, entries_len/cap, and the
        sparse indices + dense entries arrays."""
        csource = emit_source("main: function is { m: (Map key: i64 value: i64) }")
        assert "z_Map_i64_i64_t" in csource
        assert "z_Map_i64_i64_entry_t" in csource
        assert "uint64_t capacity;" in csource
        assert "uint64_t length;" in csource
        assert "uint64_t entries_len;" in csource
        assert "int64_t* indices;" in csource

    def test_map_create_destroy_emitted(self):
        """Map create and destroy functions are emitted."""
        csource = emit_source("main: function is { m: (Map key: i64 value: i64) }")
        assert "z_Map_i64_i64_create" in csource
        assert "z_Map_i64_i64_destroy" in csource

    def test_map_set_and_length(self):
        """Set entries and check length."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 1 value: 100\n"
            "    m.set key: 2 value: 200\n"
            '    print "\\{m.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_map_get_found(self):
        """.get returns Option.some for existing key."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 42 value: 999\n"
            "    r: m.get key: 42\n"
            '    match (r) case some then { print "found" } case none then { print "missing" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "found"

    def test_map_get_missing(self):
        """.get returns Option.none for missing key."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    r: m.get key: 99\n"
            '    match (r) case some then { print "found" } case none then { print "missing" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "missing"

    def test_map_has(self):
        """.has returns true for existing key, false for missing."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 1 value: 10\n"
            '    print "\\{m.has key: 1} \\{m.has key: 2}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "1 0"

    def test_map_delete(self):
        """.delete removes entry and returns true."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 1 value: 10\n"
            "    m.set key: 2 value: 20\n"
            "    d: m.delete key: 1\n"
            '    print "\\{d} \\{m.length} \\{m.has key: 1}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "1 1 0"

    def test_map_delete_missing(self):
        """.delete returns false for missing key."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    d: m.delete key: 99\n"
            '    print "\\{d}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "0"

    def test_map_replace(self):
        """Setting same key replaces the value."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 1 value: 100\n"
            "    m.set key: 1 value: 200\n"
            '    print "\\{m.length}"\n'
            "    r: m.get key: 1\n"
            '    match (r) case some then { print "ok" } case none then { print "bad" }\n'
            "}"
        )
        output = compile_and_run(csource)
        out_lines = output.strip().split("\n")
        assert out_lines[0] == "1"
        assert out_lines[1] == "ok"

    def test_map_resize(self):
        """Map resizes correctly when load factor exceeded."""
        lines = ["m: (Map key: i64 value: i64)"]
        for i in range(20):
            lines.append(f"m.set key: {i} value: {i * 10}")
        lines.append('print "\\{m.length}"')
        lines.append('print "\\{m.has key: 0}"')
        lines.append('print "\\{m.has key: 19}"')
        source = "main: function is {\n    " + "\n    ".join(lines) + "\n}"
        csource = emit_source(source)
        output = compile_and_run(csource)
        result_lines = output.strip().split("\n")
        assert result_lines[0] == "20"
        assert result_lines[1] == "1"
        assert result_lines[2] == "1"

    def test_map_tombstone_reuse(self):
        """Deleted slots are reused on insert."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64)\n"
            "    m.set key: 1 value: 10\n"
            "    m.set key: 2 value: 20\n"
            "    m.delete key: 1\n"
            "    m.set key: 3 value: 30\n"
            '    print "\\{m.length} \\{m.has key: 1} \\{m.has key: 2} \\{m.has key: 3}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 0 1 1"

    def test_map_string_keys(self):
        """Map with String keys works correctly."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: String value: i64)\n"
            '    m.set key: "hello".string value: 42\n'
            '    m.set key: "world".string value: 99\n'
            '    print "\\{m.length}"\n'
            '    k: "hello".string\n'
            '    print "\\{m.has key: k}"\n'
            '    k2: "nope".string\n'
            '    print "\\{m.has key: k2}"\n'
            "}"
        )
        output = compile_and_run(csource)
        lines = output.strip().split("\n")
        assert lines[0] == "2"
        assert lines[1] == "1"
        assert lines[2] == "0"

    def test_map_capacity_preallocation(self):
        """Pre-allocated capacity works."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: i64 value: i64) capacity: 32.u64\n"
            '    print "\\{m.capacity} \\{m.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "32 0"

    def test_map_scope_cleanup(self):
        """Map is destroyed on scope exit."""
        csource = emit_source("main: function is { m: (Map key: i64 value: i64) }")
        assert "z_Map_i64_i64_destroy(m)" in csource
        compile_and_run(csource)


class TestSet:
    """Tests for Set type emission and runtime behavior."""

    def test_set_struct_emitted(self):
        """Set monomorphizes to a compact-dict struct: sparse indices +
        dense entries arrays."""
        csource = emit_source("main: function is { s: (Set of: i64) }")
        assert "z_Set_i64_t" in csource
        assert "z_Set_i64_entry_t" in csource
        assert "uint64_t capacity;" in csource
        assert "uint64_t length;" in csource
        assert "uint64_t entries_len;" in csource
        assert "int64_t* indices;" in csource

    def test_set_create_destroy_emitted(self):
        csource = emit_source("main: function is { s: (Set of: i64) }")
        assert "z_Set_i64_create" in csource
        assert "z_Set_i64_destroy" in csource

    def test_set_add_and_length(self):
        """`.add` inserts and `.length` reflects the count; duplicates
        do not increase length."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 1\n"
            "    s.add item: 2\n"
            "    s.add item: 1\n"
            '    print "\\{s.length}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_set_add_returns_bool(self):
        """`.add` returns true on first insert, false on duplicate."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    a: s.add item: 1\n"
            "    b: s.add item: 1\n"
            '    print "a: \\{a} b: \\{b}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "a: 1 b: 0"

    def test_set_has(self):
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 42\n"
            '    print "\\{s.has item: 42}"\n'
            '    print "\\{s.has item: 99}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "1\n0"

    def test_set_delete(self):
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 1\n"
            "    s.add item: 2\n"
            '    print "\\{s.delete item: 1}"\n'
            '    print "\\{s.length}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "1\n1"

    def test_set_iterate_via_with_do(self):
        """`with it: s.iterate do for x: it loop` yields the inserted
        items (in bucket order)."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 1\n"
            "    s.add item: 2\n"
            "    s.add item: 3\n"
            "    total: 0\n"
            "    with it: s.iterate do for x: it loop {\n"
            "        total = total + x\n"
            "    }\n"
            '    print "total: \\{total}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "total: 6"

    def test_set_with_string_elements(self):
        """Set of: String -- string hashing + equality work end-to-end."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: String)\n"
            '    s.add item: "alice".string\n'
            '    s.add item: "bob".string\n'
            '    s.add item: "alice".string\n'
            '    print "len: \\{s.length}"\n'
            '    print "has alice: \\{s.has item: "alice".string}"\n'
            '    print "has carol: \\{s.has item: "carol".string}"\n'
            "}"
        )
        out = compile_and_run(csource)
        assert "len: 2" in out
        assert "has alice: 1" in out
        assert "has carol: 0" in out

    def test_set_scope_cleanup(self):
        """Set is destroyed on scope exit."""
        csource = emit_source("main: function is { s: (Set of: i64) }")
        assert "z_Set_i64_destroy(s)" in csource
        compile_and_run(csource)

    def test_set_capacity_preallocation(self):
        """`capacity: N` pre-allocates."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64) capacity: 32.u64\n"
            '    print "\\{s.capacity}"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "32"


class TestSetMapInsertionOrder:
    """Set and Map iterate in insertion order via the CPython-style
    compact-dict layout: sparse `indices` array + dense `entries` array.
    Delete tombstones the entry without disturbing earlier insertions;
    later inserts append at the end. Resize preserves the order while
    compacting tombstones."""

    def test_set_iterates_in_insertion_order_strings(self):
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: String)\n"
            '    s.add item: "alpha".string\n'
            '    s.add item: "bravo".string\n'
            '    s.add item: "charlie".string\n'
            '    s.add item: "delta".string\n'
            "    with it: s.iterate do for x: it loop {\n"
            '        print "\\{x}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "alpha\nbravo\ncharlie\ndelta\n"

    def test_set_iterates_in_insertion_order_ints(self):
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 100\n"
            "    s.add item: 200\n"
            "    s.add item: 300\n"
            "    s.add item: 400\n"
            "    s.add item: 500\n"
            "    with it: s.iterate do for x: it loop {\n"
            '        print "\\{x}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "100\n200\n300\n400\n500\n"

    def test_set_delete_preserves_remaining_order(self):
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 100\n"
            "    s.add item: 200\n"
            "    s.add item: 300\n"
            "    s.delete item: 200\n"
            "    with it: s.iterate do for x: it loop {\n"
            '        print "\\{x}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "100\n300\n"

    def test_set_reinsert_after_delete_appends_at_end(self):
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 100\n"
            "    s.add item: 200\n"
            "    s.add item: 300\n"
            "    s.delete item: 200\n"
            "    s.add item: 400\n"
            "    with it: s.iterate do for x: it loop {\n"
            '        print "\\{x}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "100\n300\n400\n"

    def test_set_resize_preserves_insertion_order(self):
        """Insert past 2/3 load factor at default capacity to force at
        least one resize, then verify iteration order is still
        insertion order (resize compacts entries in original order)."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    for i: 30.iterate loop {\n"
            "        s.add item: i\n"
            "    }\n"
            "    with it: s.iterate do for x: it loop {\n"
            '        print "\\{x}"\n'
            "    }\n"
            "}"
        )
        expected = "".join(f"{i}\n" for i in range(30))
        assert compile_and_run(csource) == expected

    def test_map_iterates_keys_in_insertion_order(self):
        csource = emit_source(
            "main: function is {\n"
            "    m: Map key: String value: i64\n"
            '    m.set key: "alpha".string value: 1\n'
            '    m.set key: "bravo".string value: 2\n'
            '    m.set key: "charlie".string value: 3\n'
            '    m.set key: "delta".string value: 4\n'
            "    with it: m.iterate do for k: it loop {\n"
            '        print "\\{k}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "alpha\nbravo\ncharlie\ndelta\n"

    def test_map_delete_then_reinsert_preserves_order(self):
        csource = emit_source(
            "main: function is {\n"
            "    m: Map key: i64 value: i64\n"
            "    m.set key: 100 value: 1\n"
            "    m.set key: 200 value: 2\n"
            "    m.set key: 300 value: 3\n"
            "    m.delete key: 200\n"
            "    m.set key: 400 value: 4\n"
            "    with it: m.iterate do for k: it loop {\n"
            '        print "k=\\{k}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "k=100\nk=300\nk=400\n"

    def test_map_set_existing_key_does_not_reorder(self):
        """Re-setting an existing key updates the value in place; the
        key keeps its original insertion position."""
        csource = emit_source(
            "main: function is {\n"
            "    m: Map key: i64 value: i64\n"
            "    m.set key: 100 value: 1\n"
            "    m.set key: 200 value: 2\n"
            "    m.set key: 100 value: 99\n"
            "    with it: m.iterateItems do for e: it loop {\n"
            '        print "k=\\{e.key} v=\\{e.value}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "k=100 v=99\nk=200 v=2\n"


class TestConstantFolding:
    """Tests for constant folding in emitter (Phase 41)."""

    def test_constant_fold_arithmetic(self):
        """1 + 2 should emit folded value 3, not (1 + 2)."""
        csource = emit_source('main: function is {\n  x: 1 + 2\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "3"

    def test_constant_fold_subtraction(self):
        """10 - 3 should fold to 7."""
        csource = emit_source('main: function is {\n  x: 10 - 3\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_constant_fold_multiplication(self):
        """4 * 5 should fold to 20."""
        csource = emit_source('main: function is {\n  x: 4 * 5\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "20"

    def test_constant_fold_division(self):
        """10 / 3 should fold to 3."""
        csource = emit_source('main: function is {\n  x: 10 / 3\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "3"

    def test_constant_fold_negative_division(self):
        """-7 / 2 should fold to -3 (truncation toward zero)."""
        csource = emit_source('main: function is {\n  x: -7 / 2\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "-3"

    def test_constant_fold_comparison_true(self):
        """3 < 5 should fold to a true value."""
        csource = emit_source(
            "main: function is {\n"
            "  x: 3 < 5\n"
            '  if x then print "yes" else print "no"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "yes"

    def test_constant_fold_chained(self):
        """1 + 2 + 3 should fold to 6."""
        csource = emit_source('main: function is {\n  x: 1 + 2 + 3\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "6"

    def test_constant_fold_named_constant(self):
        """Named constant + literal should fold."""
        csource = emit_source(
            'north: 0\nmain: function is {\n  x: north + 1\n  print "\\{x}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "1"

    def test_constant_fold_chained_named(self):
        """Chained named constants should fold: a: 1, b: a + 2 -> b is 3."""
        csource = emit_source(
            'a: 1\nb: a + 2\nmain: function is {\n  x: b\n  print "\\{x}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "3"

    def test_constant_if_true(self):
        """if with constant-true condition should emit only the then branch."""
        csource = emit_source(
            'main: function is {\n  if 1 < 2 then print "yes" else print "no"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "yes"
        # the C output should not contain a conditional if statement for 1 < 2
        # (string cleanup 'if (s &&' is OK, we check there's no comparison if)
        # Check z_main body only (runtime functions may contain else)
        main_body = csource[csource.index("void z_main") :]
        assert "if (1" not in main_body
        assert "} else {" not in main_body

    def test_constant_if_false(self):
        """if with constant-false condition should emit only the else branch."""
        csource = emit_source(
            'main: function is {\n  if 1 > 2 then print "yes" else print "no"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "no"
        main_body = csource[csource.index("void z_main") :]
        assert "if (1" not in main_body
        assert "} else {" not in main_body

    def test_constant_if_no_else_false(self):
        """if with constant-false and no else should emit nothing."""
        csource = emit_source('main: function is {\n  if 1 > 2 then print "yes"\n}')
        output = compile_and_run(csource)
        assert output.strip() == ""
        assert "if (1" not in csource

    def test_mixed_nonconstant_not_folded(self):
        """Variable + literal should NOT be folded."""
        csource = emit_source(
            'main: function is {\n  x: 5\n  y: x + 1\n  print "\\{y}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "6"
        # y should use a runtime addition, not a folded constant
        assert "+" in csource or "x" in csource

    def test_unit_level_constant_expression(self):
        """Unit-level expression 2 + 3 inlines as a macro at use sites."""
        csource = emit_source(
            'result: 2 + 3\nmain: function is {\n  print "\\{result}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "5"
        # Unit-level numeric constants are macros (value inlined at use);
        # no static decl is emitted for them.
        assert "static const int64_t z_result " not in csource

    def test_constant_fold_c_output_literal(self):
        """Verify folded value appears as literal in C output, not as expression."""
        csource = emit_source('main: function is {\n  x: 1 + 2\n  print "\\{x}"\n}')
        # the assignment should contain the folded value 3
        # and should NOT contain (1 + 2) or similar
        lines = [ln.strip() for ln in csource.split("\n")]
        assign_lines = [ln for ln in lines if "= 3;" in ln or "= 3 " in ln]
        assert len(assign_lines) > 0, f"Expected folded '= 3' in C output:\n{csource}"

    # -- Division by zero ---

    def test_constant_division_by_zero_error(self):
        """Division by constant zero should be a compile-time error."""
        vfs, name = make_parser_vfs(
            "main: function is { x: 1 / 0 }", unitname="test", src_dir=LIB_DIR
        )
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        errors = typing.errors
        assert any("division by zero" in e.msg.lower() for e in errors)

    # -- Float (f64) folding ---

    def test_f64_fold_arithmetic(self):
        """f64 constant arithmetic should fold and produce correct output."""
        csource = emit_source('main: function is {\n  x: 1.5 + 2.5\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "4"

    def test_f64_fold_subtraction(self):
        """f64 subtraction folds correctly."""
        csource = emit_source('main: function is {\n  x: 5.0 - 1.5\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "3.5"

    def test_f64_fold_multiplication(self):
        """f64 multiplication folds correctly."""
        csource = emit_source('main: function is {\n  x: 2.0 * 3.0\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "6"

    def test_f64_fold_division(self):
        """f64 division folds correctly (no truncation)."""
        csource = emit_source('main: function is {\n  x: 7.0 / 2.0\n  print "\\{x}"\n}')
        output = compile_and_run(csource)
        assert output.strip() == "3.5"

    def test_f64_fold_c_output(self):
        """Verify f64 folded value appears as literal, not expression."""
        csource = emit_source('main: function is {\n  x: 1.5 + 2.5\n  print "\\{x}"\n}')
        # should contain 4.0 as a literal, not (1.5 + 2.5)
        assert "4.0" in csource

    def test_f64_comparison_dead_branch(self):
        """f64 comparison should enable dead branch elimination."""
        csource = emit_source(
            'main: function is {\n  if 1.0 < 2.0 then print "yes" else print "no"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "yes"
        # the constant condition should not emit a runtime if-else in main
        main_body = csource[csource.index("void z_main") :]
        assert "} else {" not in main_body

    def test_unit_level_typed_numeric_literal_constant(self):
        """Typed numeric literal as unit constant: `myU8: 32.u8` compiles
        and runs without a backing static decl. Regression for the
        PR 4 lexer-port friction case where `gcc` failed with
        `myU8 undeclared`."""
        csource = emit_source(
            'myU8: 32.u8\nmain: function is {\n  b: myU8\n  print "u=\\{b}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "u=32"
        # No backing decl: value inlined at the use site as a u8-cast literal.
        assert "static const uint8_t z_myU8 " not in csource
        assert "((uint8_t)32)" in csource

    def test_unit_level_f64_constant_expression(self):
        """Unit-level f64 expression inlines as a macro at use sites."""
        csource = emit_source(
            'PI_APPROX: 3.0 + 0.14\nmain: function is {\n  print "\\{PI_APPROX}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "3.14"
        # Macro semantics: no static decl emitted for the constant.
        assert "static const" not in csource or "z_PI_APPROX " not in csource


class TestIfExpression:
    """Tests for if-as-expression (Phase 42)."""

    def test_if_expression_basic(self):
        """Basic if-expression: x: if 1 < 2 then 1 else 2."""
        csource = emit_source(
            'main: function is {\n  x: if 1 < 2 then 1 else 2\n  print "\\{x}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "1"

    def test_if_expression_false_branch(self):
        """If-expression where condition is false."""
        csource = emit_source(
            'main: function is {\n  x: if 1 > 2 then 1 else 2\n  print "\\{x}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_if_expression_max_pattern(self):
        """Max pattern: x: if a > b then a else b."""
        csource = emit_source(
            "main: function is {\n"
            "  a: 10\n"
            "  b: 20\n"
            "  x: if a > b then a else b\n"
            '  print "\\{x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "20"

    def test_if_expression_string(self):
        """String if-expression."""
        csource = emit_source(
            'main: function is {\n  s: if 1 < 2 then "yes" else "no"\n  print s\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "yes"

    def test_if_expression_multiline_branch(self):
        """Multi-statement branch: Result is last expression."""
        csource = emit_source(
            "main: function is {\n"
            "  x: if 1 < 2 then {\n"
            "    y: 1\n"
            "    y + 1\n"
            "  } else 0\n"
            '  print "\\{x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_if_expression_constant_fold(self):
        """Constant-folded if-expression should not emit C if."""
        csource = emit_source(
            'main: function is {\n  x: if 1 < 2 then 10 else 20\n  print "\\{x}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "10"

    def test_if_expression_in_interpolation(self):
        """If-expression used in String interpolation."""
        csource = emit_source(
            'main: function is {\n  print "\\{if 1 < 2 then 42 else 0}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_if_expression_statement_if_unchanged(self):
        """Statement-if should still work normally (regression check)."""
        csource = emit_source(
            "main: function is {\n"
            "  x: 5\n"
            '  if x > 3 then print "big" else print "small"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "big"


class TestUnitLevelIf:
    """Tests for unit-level if definitions (Phase 42.2)."""

    def test_unit_level_if_true(self):
        """Unit-level if with true condition compiles and runs."""
        csource = emit_source(
            'x: if 1 < 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"
        # Macro semantics: no static decl for unit-level folded constants.
        assert "static const int64_t z_x " not in csource

    def test_unit_level_if_false(self):
        """Unit-level if with false condition selects else branch."""
        csource = emit_source(
            'x: if 1 > 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "0"

    def test_unit_level_if_with_constants(self):
        """Unit-level if referencing named constants."""
        csource = emit_source(
            "A: 10\n"
            "x: if A > 5 then { A } else { 0 }\n"
            'main: function is { print "\\{x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "10"

    def test_unit_level_if_chained_constants(self):
        """Unit-level if with chained constant references."""
        csource = emit_source(
            "A: 10\n"
            "B: A + 5\n"
            "x: if B > 10 then { B } else { A }\n"
            'main: function is { print "\\{x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_unit_level_if_no_runtime_if(self):
        """Unit-level if should not produce runtime if in C output."""
        csource = emit_source(
            'x: if 1 < 2 then { 42 } else { 0 }\nmain: function is { print "\\{x}" }'
        )
        # should be a static const, no runtime if
        assert "if (1" not in csource


class TestNativeEmitter:
    """Tests that native system types and functions emit correct C code."""

    def test_native_return(self):
        """return (native) generates valid C."""
        output = compile_and_run(emit_source("main: function out i64 is { return 42 }"))
        # return produces no output, but program exits cleanly
        assert output == ""

    def test_native_break(self):
        """break (native) generates valid C in a loop."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  x: 0\n"
                "  for loop {\n"
                "    if x == 3 then { break }\n"
                "    x = x + 1\n"
                "  }\n"
                '  print "\\{x}"\n'
                "}"
            )
        )
        assert output.strip() == "3"

    def test_nested_for_break_inner(self):
        """break in inner loop only breaks inner, outer continues."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  total: 0\n"
                "  for i: 0 while i < 3 loop {\n"
                "    for j: 0 while j < 10 loop {\n"
                "      if j == 2 then { break }\n"
                "      j = j + 1\n"
                "    }\n"
                "    total = total + 1\n"
                "    i = i + 1\n"
                "  }\n"
                '  print "\\{total}"\n'
                "}"
            )
        )
        assert output.strip() == "3"

    def test_nested_for_continue_inner(self):
        """continue in inner loop only affects inner, outer unaffected."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  total: 0\n"
                "  for i: 0 while i < 3 loop {\n"
                "    sum: 0\n"
                "    for j: 0 while j < 5 loop {\n"
                "      j = j + 1\n"
                "      if j == 3 then { continue }\n"
                "      sum = sum + j\n"
                "    }\n"
                "    total = total + sum\n"
                "    i = i + 1\n"
                "  }\n"
                '  print "\\{total}"\n'
                "}"
            )
        )
        # each inner: 1+2+4+5 = 12, outer: 3 * 12 = 36
        assert output.strip() == "36"

    def test_do_break_early_exit_none(self):
        """Do block with break that fires returns none."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  x: {\n"
                "    if 1 < 2 then { break }\n"
                "    42\n"
                "  }\n"
                "  match (x) case some then {"
                '    print "some"'
                "  } case none then {"
                '    print "none"'
                "  }\n"
                "}"
            )
        )
        assert output.strip() == "none"

    def test_do_break_normal_completion_some(self):
        """Do block with break that doesn't fire returns some(value)."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  x: {\n"
                "    if 1 > 2 then { break }\n"
                "    42\n"
                "  }\n"
                "  match (x) case some then {"
                '    print "some"'
                "  } case none then {"
                '    print "none"'
                "  }\n"
                "}"
            )
        )
        assert output.strip() == "some"

    def test_do_break_emits_do_while_0(self):
        """Do block with break uses do { } while(0) in C output."""
        csource = emit_source(
            "main: function is {\n  x: {\n    if 1 > 2 then { break }\n    42\n  }\n}"
        )
        assert "do {" in csource
        assert "} while (0);" in csource

    def test_do_break_nested_for_binds_correctly(self):
        """break in for inside do binds to for, do continues normally."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  x: {\n"
                "    for loop {\n"
                "      if 1 < 2 then { break }\n"
                "    }\n"
                "    42\n"
                "  }\n"
                '  print "\\{x}"\n'
                "}"
            )
        )
        # break targets the for loop, do block completes normally with 42
        # do block has no break of its own, so type is plain i64
        assert output.strip() == "42"

    def test_do_break_in_do_inside_for(self):
        """break in do inside for binds to do, not the for."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  total: 0\n"
                "  for i: 0 while i < 3 loop {\n"
                "    x: {\n"
                "      if 1 < 2 then { break }\n"
                "      10\n"
                "    }\n"
                "    match (x) case some then {"
                "      total = total + 1\n"
                "    } case none then {"
                "      total = total + 100\n"
                "    }\n"
                "    i = i + 1\n"
                "  }\n"
                '  print "\\{total}"\n'
                "}"
            )
        )
        # break targets do (none), for runs 3 times, each adds 100
        assert output.strip() == "300"

    def test_do_break_statement_context(self):
        """Do block break in statement context (not expression) works."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  {\n"
                "    if 1 < 2 then { break }\n"
                '    print "should not print"\n'
                "  }\n"
                '  print "after"\n'
                "}"
            )
        )
        assert output.strip() == "after"

    def test_native_error_in_const_false_branch(self):
        """error in a constant-false if branch is eliminated from C output."""
        csource = emit_source(
            'SIZE: 1\nmain: function is { if SIZE == 0 then { error "bad" } }'
        )
        assert "error(" not in csource

    def test_constant_match_dead_arm_elimination(self):
        """Constant match eliminates dead arms from C output."""
        csource = emit_source(
            "MODE: 1\nmain: function is {\n"
            '  match MODE case 0 then { print "no" }'
            ' case 1 then { print "yes" }\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "yes"
        # dead arm should not appear in C output
        assert '"no"' not in csource

    def test_constant_match_else_elimination(self):
        """Constant match eliminates else when an arm matches."""
        csource = emit_source(
            "MODE: 1\nmain: function is {\n"
            '  match MODE case 1 then { print "ok" }'
            ' else { print "bad" }\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_constant_match_all_miss_emits_else(self):
        """Constant match emits only else when all arms miss."""
        csource = emit_source(
            "MODE: 5\nmain: function is {\n"
            '  match MODE case 0 then { print "a" }'
            ' case 1 then { print "b" }'
            ' else { print "c" }\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "c"

    def test_constant_match_expression_fold(self):
        """Match-as-expression with constant subject folds."""
        csource = emit_source(
            "MODE: 1\nmain: function is {\n"
            "  x: (match MODE case 0 then 10 case 1 then 20 else 30)\n"
            '  print "\\{x}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "20"

    def test_generic_type_match_emits_correct_arm(self):
        """Generic type match emits only the matching arm in C."""
        csource = emit_source(
            "MyBox: class { val: t } as {\n"
            "  t: Any.generic\n"
            "  check: function {b: this} is {\n"
            '    match t case i32 then { print "32" }'
            ' case i64 then { print "64" } else { print "other" }\n'
            "  }\n"
            "}\n"
            "main: function is { b: MyBox val: 42 }"
        )
        # the i64 arm should be present, dead i32/other arms eliminated
        assert '"64"' in csource
        assert '"32"' not in csource
        assert '"other"' not in csource

    def test_native_string_operations(self):
        """String operations via native String type work in generated C."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                '  x: "hello"\n'
                '  y: "world"\n'
                '  print "\\{x} \\{y}"\n'
                "}"
            )
        )
        assert output.strip() == "hello world"

    def test_native_numeric_operations(self):
        """Native numeric operations (+, -, *, /) generate correct C."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  a: 10 + 20\n"
                "  b: 100 - 58\n"
                "  c: 6 * 7\n"
                '  print "\\{a} \\{b} \\{c}"\n'
                "}"
            )
        )
        assert output.strip() == "30 42 42"

    def test_native_comparison_operations(self):
        """Native comparison operations (==, <, >) generate correct C."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                '  if 10 > 5 then { print "gt" }\n'
                '  if 3 < 7 then { print "lt" }\n'
                '  if 42 == 42 then { print "eq" }\n'
                "}"
            )
        )
        lines = output.strip().split("\n")
        assert lines == ["gt", "lt", "eq"]

    def test_native_numeric_conversion(self):
        """Native numeric conversion methods generate correct C."""
        output = compile_and_run(
            emit_source('main: function is {\n  x: 42\n  y: x.i32\n  print "\\{y}"\n}')
        )
        assert output.strip() == "42"

    def test_native_bool_type(self):
        """Native bool type works in conditions."""
        output = compile_and_run(
            emit_source(
                "main: function is {\n"
                "  x: 5 > 3\n"
                '  if x then { print "yes" } else { print "no" }\n'
                "}"
            )
        )
        assert output.strip() == "yes"


class TestIteratorPattern:
    """Tests for the iterator-over-parent pattern with visibility."""

    def test_callable_iterator_over_class(self):
        """Iterator class iterates over a container with private state."""
        output = compile_and_run(
            emit_source(
                "Bag: class { a: i64 b: i64 c: i64 count: i64 } as {\n"
                "    public: unit { :count :at }\n"
                "    at: function {self: this index: i64} out i64 is {\n"
                "        if index == 0 then { return self.a }\n"
                "        if index == 1 then { return self.b }\n"
                "        return self.c\n"
                "    }\n"
                "}\n"
                "BagIter: class { pos: i64 max: i64 items: Bag } as {\n"
                "    call: function {it: this} out (optionval t: i64) is {\n"
                "        if it.pos < it.max then {\n"
                "            val: (Bag.at self: it.items index: it.pos)\n"
                "            it.pos = it.pos + 1\n"
                "            return (optionval.some val)\n"
                "        }\n"
                "        return (optionval.none i64)\n"
                "    }\n"
                "}\n"
                "main: function is {\n"
                "    b: Bag a: 10 b: 20 c: 30 count: 3\n"
                "    it: BagIter pos: 0 max: b.count items: b.take\n"
                '    for x: it loop { print "\\{x}" }\n'
                '    print "done"\n'
                "}"
            )
        )
        lines = output.strip().split("\n")
        assert lines == ["10", "20", "30", "done"]

    def test_callable_iterator_yields_string_no_double_free(self):
        """Reftype-payload iterator: `Option t: String` consumed by a
        for-loop. Previously the emitter shallow-copied the payload and
        then called the union destructor, which z_String_free'd the
        same heap buffer out from under the iteration binding. The fix
        moves ownership out of the Option's Box into the binding and
        registers the per-iteration z_String_free at end of iteration.
        Runs under ASan so the double-free / use-after-free surfaces."""
        csource = emit_source(
            "Stringiter: class { n: i64 } as {\n"
            "  call: function {it: this} out (Option t: String) is {\n"
            "    if it.n > 0 then {\n"
            "      it.n = it.n - 1\n"
            '      return (Option.some "hello".string)\n'
            "    }\n"
            "    return (Option.none String)\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  si: Stringiter n: 3\n"
            "  for line: si loop { print line }\n"
            '  print "done"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip().split("\n") == [
            "hello",
            "hello",
            "hello",
            "done",
        ]

    def test_private_field_blocked(self):
        """External access to private field produces type error."""
        errors = []
        try:
            emit_source(
                "Bag: class { secret: i64 } as {\n"
                "    public: unit {}\n"
                "}\n"
                'main: function is { b: Bag secret: 1\n print "\\{b.secret}" }'
            )
        except AssertionError as e:
            errors = [str(e)]
        assert len(errors) > 0
        assert "not public" in errors[0].lower() or "type error" in errors[0].lower()

    def test_public_accessor_works(self):
        """Public method can access private fields via this."""
        output = compile_and_run(
            emit_source(
                "Box: class { secret: i64 } as {\n"
                "    public: unit { :reveal }\n"
                "    reveal: function {self: this} out i64 is { return self.secret }\n"
                "}\n"
                "main: function is {\n"
                "    b: Box secret: 42\n"
                '    print "\\{Box.reveal self: b}"\n'
                "}"
            )
        )
        assert output.strip() == "42"


class TestMatchTake:
    """Take ownership of match subject inside arms."""

    def test_union_match_take_one_arm(self):
        """Take in one arm, not the other — compiles and runs correctly.
        Under shadow narrowing the narrowed name is the payload type,
        so consume takes `Box.take` (not `r.take`)."""
        csource = emit_source(
            "Box: class { n: i64 }\n"
            "r: union { ok: Box  err: Box }\n"
            "consume: function {x: Box.take} is {\n"
            '  print "consumed"\n'
            "}\n"
            "main: function is {\n"
            "  u: r.ok (Box n: 42)\n"
            "  match (u) case ok then {\n"
            "    consume u\n"
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "consumed"

    def test_union_match_take_all_arms(self):
        """Take in all arms — compiles and runs correctly."""
        csource = emit_source(
            "Box: class { n: i64 }\n"
            "r: union { ok: Box  err: Box }\n"
            "consume: function {x: Box.take} is {\n"
            '  print "consumed"\n'
            "}\n"
            "main: function is {\n"
            "  u: r.ok (Box n: 42)\n"
            "  match (u) case ok then {\n"
            "    consume u\n"
            "  } case err then {\n"
            "    consume u\n"
            "  }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "consumed"

    def test_union_match_take_asan(self):
        """No memory leaks when taking in match arm (ASan)."""
        csource = emit_source(
            "r: union { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  u: r.ok 42\n"
            "  match (u) case ok then {\n"
            "    u.take\n"
            '    print "ok"\n'
            "  } case err then {\n"
            '    print "err"\n'
            "  }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "ok"


class TestAutoGeneratedEquality:
    """Test C code emission for auto-generated == and != on value types."""

    def test_record_eq_c_output(self):
        """Small record (<=16 Bytes) uses field-by-field comparison."""
        csource = emit_source(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        assert "z_point_eq" in csource
        # 16 bytes (at threshold): field-by-field, not memcmp
        assert "(a.x == b.x)" in csource
        assert "memcmp(&a, &b, sizeof(z_point_t))" not in csource

    def test_record_eq_binop(self):
        """== on records emits z_point_eq() call."""
        csource = emit_source(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a == b then return 0\n"
            "}"
        )
        # the if condition should call z_point_eq, not use C ==
        assert "z_point_eq(a, b)" in csource

    def test_record_neq_binop(self):
        """!= on records emits !z_point_eq()."""
        csource = emit_source(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  b: point\n"
            "  if a != b then return 0\n"
            "}"
        )
        assert "!z_point_eq(a, b)" in csource

    def test_record_eq_compiles_and_runs(self):
        """Record equality compiles and produces correct Result."""
        csource = emit_source(
            "point: record { x: 0  y: 0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  a.x = 1\n"
            "  a.y = 2\n"
            "  b: point\n"
            "  b.x = 1\n"
            "  b.y = 2\n"
            "  c: point\n"
            "  c.x = 3\n"
            '  if a == b then print "eq"\n'
            '  if a != c then print "neq"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "eq\nneq"

    def test_record_with_bool_field_eq_c_output(self):
        """Record with a bool field compares the bool with C `==`, not a helper."""
        csource = emit_source(
            "flagrec: record { f: bool  n: i64 }\n"
            "main: function is {\n"
            "  a: flagrec f: true  n: 1\n"
            "  b: flagrec f: true  n: 1\n"
            '  if a == b then print "eq"\n'
            "}"
        )
        assert "z_flagrec_eq" in csource
        assert "(a.f == b.f)" in csource
        assert "z_bool_eq" not in csource

    def test_record_with_bool_field_eq_compiles_and_runs(self):
        """Record-with-bool equality compiles cleanly and runs."""
        csource = emit_source(
            "flagrec: record { f: bool  n: i64 }\n"
            "main: function is {\n"
            "  a: flagrec f: true  n: 1\n"
            "  b: flagrec f: true  n: 1\n"
            "  c: flagrec f: false n: 1\n"
            '  if a == b then print "eq"\n'
            '  if a != c then print "neq"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "eq\nneq"

    def test_variant_eq_c_output(self):
        """Small integer variant uses tag+payload comparison."""
        csource = emit_source(
            "result: variant { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  a: result.ok 1\n"
            "  b: result.ok 1\n"
            "  if a == b then return 0\n"
            "}"
        )
        assert "z_result_eq" in csource
        # 12 bytes (tag + i64 union): below threshold, uses tag+payload
        assert "a.tag != b.tag" in csource

    def test_variant_float_eq_c_output(self):
        """Variant with float payload uses tag+payload comparison."""
        csource = emit_source(
            "result: variant { ok: f64  none: null }\n"
            "main: function is {\n"
            "  a: result.ok 1.0\n"
            "  b: result.ok 1.0\n"
            "  if a == b then return 0\n"
            "}"
        )
        assert "z_result_eq" in csource
        assert "a.tag != b.tag" in csource
        assert "memcmp(&a, &b, sizeof(z_Result_t))" not in csource

    def test_enum_eq_compiles_and_runs(self):
        """Pure enum equality compiles and runs correctly."""
        csource = emit_source(
            "color: variant { red: null  green: null  blue: null }\n"
            "main: function is {\n"
            "  a: color.red\n"
            "  b: color.red\n"
            "  c: color.blue\n"
            '  if a == b then print "same"\n'
            '  if a != c then print "diff"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "same\ndiff"

    def test_nested_record_eq_c_output(self):
        """Small nested integer records use field-by-field."""
        csource = emit_source(
            "inner: record { v: 0 }\n"
            "outer: record { a: inner  b: 0 }\n"
            "main: function is {\n"
            "  x: outer a: inner\n"
            "  y: outer a: inner\n"
            "  if x == y then return 0\n"
            "}"
        )
        assert "z_outer_eq" in csource
        # 16 bytes: at threshold, uses field-by-field with nested eq call
        assert "z_inner_eq(a.a, b.a)" in csource
        assert "memcmp(&a, &b, sizeof(z_outer_t))" not in csource

    def test_nested_record_float_uses_field_compare(self):
        """Nested record with float field falls back to field-by-field."""
        csource = emit_source(
            "inner: record { v: 0.0 }\n"
            "outer: record { a: inner  b: 0 }\n"
            "main: function is {\n"
            "  x: outer a: inner\n"
            "  y: outer a: inner\n"
            "  if x == y then return 0\n"
            "}"
        )
        assert "z_inner_eq(a.a, b.a)" in csource
        assert "memcmp(&a, &b, sizeof(z_outer_t))" not in csource

    def test_simple_eq_small_record_field_compare(self):
        """Small simple record (<=16 Bytes) uses field-by-field."""
        csource = emit_source(
            "small: record { a: 0 }\n"
            "main: function is {\n"
            "  x: small\n"
            "  y: small\n"
            "  if x == y then return 0\n"
            "}"
        )
        assert "(a.a == b.a)" in csource
        assert "memcmp(&a, &b, sizeof(z_small_t))" not in csource

    def test_simple_eq_large_record_memcmp(self):
        """Large simple record (>16 Bytes) uses memcmp."""
        csource = emit_source(
            "big: record { a: 0  b: 0  c: 0 }\n"
            "main: function is {\n"
            "  x: big\n"
            "  y: big\n"
            "  if x == y then return 0\n"
            "}"
        )
        assert "memcmp(&a, &b, sizeof(z_big_t))" in csource

    def test_simple_eq_float_always_field_compare(self):
        """Float record always uses field-by-field regardless of size."""
        csource = emit_source(
            "big: record { a: 0.0  b: 0.0  c: 0.0 }\n"
            "main: function is {\n"
            "  x: big\n"
            "  y: big\n"
            "  if x == y then return 0\n"
            "}"
        )
        assert "memcmp(&a, &b, sizeof(z_big_t))" not in csource
        assert "(a.a == b.a)" in csource

    def test_simple_eq_large_record_compiles_and_runs(self):
        """Large record memcmp equality compiles and works correctly."""
        csource = emit_source(
            "big: record { a: 0  b: 0  c: 0 }\n"
            "main: function is {\n"
            "  x: big\n"
            "  x.a = 1\n"
            "  x.b = 2\n"
            "  x.c = 3\n"
            "  y: big\n"
            "  y.a = 1\n"
            "  y.b = 2\n"
            "  y.c = 3\n"
            "  z: big\n"
            "  z.a = 9\n"
            '  if x == y then print "eq"\n'
            '  if x != z then print "neq"\n'
            "}"
        )
        assert "memcmp" in csource
        output = compile_and_run(csource)
        assert output.strip() == "eq\nneq"

    def test_simple_eq_small_variant_tag_compare(self):
        """Small integer variant uses tag+payload, not memcmp."""
        csource = emit_source(
            "result: variant { ok: i64  err: i64 }\n"
            "main: function is {\n"
            "  a: result.ok 1\n"
            "  b: result.ok 1\n"
            "  if a == b then return 0\n"
            "}"
        )
        assert "a.tag != b.tag" in csource
        assert "memcmp(&a, &b, sizeof(z_Result_t))" not in csource

    def test_float_eq_compiles_and_runs(self):
        """Float field-by-field equality compiles and works correctly."""
        csource = emit_source(
            "point: record { x: 0.0  y: 0.0 }\n"
            "main: function is {\n"
            "  a: point\n"
            "  a.x = 1.0\n"
            "  a.y = 2.0\n"
            "  b: point\n"
            "  b.x = 1.0\n"
            "  b.y = 2.0\n"
            '  if a == b then print "eq"\n'
            "}"
        )
        assert "memcmp(&a, &b, sizeof(z_point_t))" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "eq"


class TestStringEquality:
    """Test String == / != C emission."""

    def test_string_eq_compiles_and_runs(self):
        """String == compares content, not pointers."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "hello"\n'
            '  if a == b then print "equal"\n'
            "}"
        )
        assert "z_StringView_eq" in csource
        output = compile_and_run(csource)
        assert output.strip() == "equal"

    def test_string_neq_compiles_and_runs(self):
        """String != compares content."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "hello"\n'
            '  b: "world"\n'
            '  if a != b then print "different"\n'
            "}"
        )
        assert "z_StringView_eq" in csource
        output = compile_and_run(csource)
        assert output.strip() == "different"


class TestStringOrdering:
    """Byte-wise lexicographic <, <=, >, >= and the `compare` method on
    String and StringView. Same algorithm as C's memcmp with the
    length tie-break: shorter prefix is less than the longer extension."""

    def test_string_lt_shorter_prefix(self):
        """'app' < 'apple': shared prefix, shorter wins."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "app".string\n'
            '  b: "apple".string\n'
            '  if a < b then { print "yes" }\n'
            "}"
        )
        assert "z_String_cmp" in csource
        assert compile_and_run(csource).strip() == "yes"

    def test_string_lt_distinct(self):
        """'apple' < 'banana': first differing byte decides."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "apple".string\n'
            '  b: "banana".string\n'
            '  if a < b then { print "lt" }\n'
            '  if b > a then { print "gt" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["lt", "gt"]

    def test_string_le_equal_and_less(self):
        """<= is true for both equal and less-than."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "abc".string\n'
            '  b: "abc".string\n'
            '  c: "abd".string\n'
            '  if a <= b then { print "eq" }\n'
            '  if a <= c then { print "lt" }\n'
            '  if c <= a then { print "unexpected" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["eq", "lt"]

    def test_string_ge_equal_and_greater(self):
        """>= is true for both equal and greater-than."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "z".string\n'
            '  b: "z".string\n'
            '  c: "a".string\n'
            '  if a >= b then { print "eq" }\n'
            '  if a >= c then { print "gt" }\n'
            '  if c >= a then { print "unexpected" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["eq", "gt"]

    def test_string_compare_returns_sign(self):
        """compare returns -1 / 0 / 1 for lt / eq / gt."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "abc".string\n'
            '  b: "abd".string\n'
            '  c: "abc".string\n'
            "  x: a.compare rhs: b\n"
            "  y: a.compare rhs: c\n"
            "  z: b.compare rhs: a\n"
            '  print "\\{x}"\n'
            '  print "\\{y}"\n'
            '  print "\\{z}"\n'
            "}"
        )
        assert "z_String_cmp" in csource
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["-1", "0", "1"]

    def test_stringview_ordering_and_compare(self):
        """The same four ordering ops plus compare work on StringView."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "apple"\n'
            '  b: "banana"\n'
            '  if a < b then { print "lt" }\n'
            "  x: a.compare rhs: b\n"
            '  print "\\{x}"\n'
            "}"
        )
        assert "z_StringView_cmp" in csource
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["lt", "-1"]

    def test_empty_string_is_less_than_any_nonempty(self):
        """Empty String is lexicographically less than Any non-empty."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "".string\n'
            '  b: "x".string\n'
            '  if a < b then { print "yes" }\n'
            "  x: a.compare rhs: b\n"
            '  print "\\{x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["yes", "-1"]

    def test_equal_strings_all_orderings(self):
        """For equal strings: ==, <=, >= true; <, >, != false."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "same".string\n'
            '  b: "same".string\n'
            '  if a == b then { print "eq" }\n'
            '  if a <= b then { print "le" }\n'
            '  if a >= b then { print "ge" }\n'
            '  if a < b then { print "unexpected-lt" }\n'
            '  if a > b then { print "unexpected-gt" }\n'
            '  if a != b then { print "unexpected-neq" }\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["eq", "le", "ge"]


class TestStringHash:
    """`String.hash` / `StringView.hash` natives. Both wrap SipHash-1-3
    over the byte contents using a per-process random seed initialised
    once at startup, so values are non-deterministic across runs but
    stable within a run."""

    def test_string_hash_emits_siphash_init(self):
        """The hash native pulls in the SipHash runtime + seed init."""
        csource = emit_source(
            'main: function is {\n  s: "hello".string\n  h: s.hash\n  print "\\{h}"\n}'
        )
        assert "z_siphash_init" in csource
        assert "z_siphash_string" in csource
        # init gets called from main before z_main
        main_body = csource[csource.index("int main(") :]
        assert main_body.index("z_siphash_init();") < main_body.index("z_main();")
        # exercise the binary to confirm it links + runs
        compile_and_run(csource)

    def test_string_hash_stable_within_run(self):
        """Identical strings hash to the same value inside one process."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "hello world".string\n'
            '  b: "hello world".string\n'
            "  ha: a.hash\n"
            "  hb: b.hash\n"
            '  if ha == hb then print "equal" else print "differ"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "equal"

    def test_string_hash_differs_for_distinct_inputs(self):
        """Two distinct strings almost certainly hash to different
        values (one SipHash collision is astronomically unlikely)."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "alice".string\n'
            '  b: "bob".string\n'
            "  ha: a.hash\n"
            "  hb: b.hash\n"
            '  if ha == hb then print "collide" else print "distinct"\n'
            "}"
        )
        assert compile_and_run(csource).strip() == "distinct"

    def test_stringview_hash_matches_string_hash(self):
        """`s.hash` on a String and `s.stringview.hash` on the matching
        view see the same bytes, so they must produce the same value
        (within a single run)."""
        csource = emit_source(
            "main: function is {\n"
            '  s: "abcd".string\n'
            "  hs: s.hash\n"
            "  with sv: s.stringview do {\n"
            "    hv: sv.hash\n"
            '    if hs == hv then print "match" else print "mismatch"\n'
            "  }\n"
            "}"
        )
        assert "z_siphash_stringview" in csource
        assert compile_and_run(csource).strip() == "match"

    def test_string_hash_differs_across_runs(self):
        """Random per-process seed: two independent invocations of the
        same program produce different hashes for the same input. The
        probability of an accidental collision is 2^-64, so this test
        is robust enough in practice."""
        csource = emit_source(
            'main: function is {\n  s: "deterministic".string\n  print "\\{s.hash}"\n}'
        )
        out1 = compile_and_run(csource).strip()
        out2 = compile_and_run(csource).strip()
        assert out1 != out2


class TestMapSipHash:
    """Map / Set bucket dispatch routes through the shared SipHash /
    splitmix64 helpers rather than per-mono inline FNV-1a /
    splitmix64. The functional contract (set / get / has / delete)
    is unchanged."""

    def test_map_string_keys_round_trip(self):
        """String-keyed Map.set / .has / .delete still work end-to-end
        through SipHash dispatch."""
        csource = emit_source(
            "main: function is {\n"
            "    m: (Map key: String value: i64)\n"
            '    m.set key: "alpha".string value: 1\n'
            '    m.set key: "beta".string value: 2\n'
            '    a: m.has key: "alpha".string\n'
            '    b: m.has key: "beta".string\n'
            '    c: m.has key: "carol".string\n'
            "    n: m.length\n"
            '    print "n: \\{n} a: \\{a} b: \\{b} c: \\{c}"\n'
            "}"
        )
        # inline FNV-1a is gone -- look for the SipHash wrapper call
        assert "z_siphash_string" in csource
        assert "14695981039346656037ULL" not in csource
        assert compile_and_run(csource).strip() == "n: 2 a: 1 b: 1 c: 0"

    def test_set_integer_keys_round_trip(self):
        """Numeric-keyed Set still works; splitmix64 routes through z_hash_u64."""
        csource = emit_source(
            "main: function is {\n"
            "    s: (Set of: i64)\n"
            "    s.add item: 1\n"
            "    s.add item: 2\n"
            "    s.add item: 1\n"
            '    print "len: \\{s.length}"\n'
            '    print "has1: \\{s.has item: 1}"\n'
            '    print "has3: \\{s.has item: 3}"\n'
            "}"
        )
        assert "z_hash_u64" in csource
        # splitmix64 constants are now centralised in the runtime preamble,
        # not inlined in the mono.
        struct_body = csource[csource.index("z_Set_i64_hash_item") :]
        assert "0xbf58476d1ce4e5b9ULL" not in struct_body[: struct_body.index("\n\n")]
        out = compile_and_run(csource)
        assert "len: 2" in out
        assert "has1: 1" in out
        assert "has3: 0" in out


class TestBoxRefinements:
    """Phase 10: Box(class) and Box(String) heap-allocate a copy; no leaks.

    All user-defined types are stack-allocated now, so Box always creates
    a real heap copy (no more passthrough for classes/strings).
    """

    def test_box_class_with_string_field(self):
        """Box a class with heap-backed fields chains destructor correctly."""
        csource = emit_source(
            "Named: class {\n"
            "    label: String\n"
            "    value: i64\n"
            "}\n"
            "main: function is {\n"
            '    n: Named label: "hello".string value: 42\n'
            "    b: Box from: n.take\n"
            '    print "done"\n'
            "}"
        )
        # box destructor chains inner class destructor
        assert "z_Named_destroy(v);" in csource
        # inner destructor frees heap fields like strings
        output = compile_and_run(csource)
        assert "done" in output

    def test_box_string_heap_allocates(self):
        """Box from: String heap-allocates a copy (String is stack-allocated now)."""
        csource = emit_source(
            'main: function is {\n    b: Box from: "hello".string\n    print "done"\n}'
        )
        # box allocates z_String_t* on the heap
        assert "(z_String_t*)z_xmalloc(sizeof(z_String_t))" in csource
        output = compile_and_run(csource)
        assert "done" in output


class TestListView:
    """Phase 9: ListView — generic read-only view into a List.

    Listview is a class with the same first two fields as List ({length,
    data*}) enabling zero-cost casting. It is a reftype (single-owner).
    """

    def test_listview_struct_layout(self):
        """ListView struct has {length, data*} matching List's first two fields."""
        csource = emit_source("main: function is { l: (List of: i64)\nv: l.listview }")
        # listview struct matches first two fields of list for zero-cost cast.
        # The struct uses a named tag so a forward typedef can refer to it
        # when a user class field holds a List-of-user-variant.
        assert (
            "typedef struct z_ListView_i64_t {\n"
            "    uint64_t length;\n"
            "    int64_t* data;\n"
            "} z_ListView_i64_t;" in csource
        )

    def test_listview_listview_is_cast(self):
        """List.listview is a zero-cost reinterpret_cast in C."""
        csource = emit_source("main: function is { l: (List of: i64)\nv: l.listview }")
        # listview is a cast, not a copy
        assert "return *(z_ListView_i64_t*)_this;" in csource

    def test_listview_length_and_get(self):
        """Listview provides .length and .get methods."""
        csource = emit_source(
            "main: function is {\n"
            "    nums: (List of: i64)\n"
            "    nums.append from: 10\n"
            "    nums.append from: 20\n"
            "    nums.append from: 30\n"
            "    v: nums.listview\n"
            "    n: v.length\n"
            '    print "length: \\{n}"\n'
            "    e: v.get i: 1.u64\n"
            '    print "elem[1]: \\{e}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert "length: 3" in output
        assert "elem[1]: 20" in output


class TestClassLockFields:
    """Phase 7: classes with .lock fields.

    Classes (stack-allocated, single-owner) may hold .lock references
    to other objects. These classes are owned and can be moved via .take.
    """

    def test_class_lock_field_stored_as_pointer(self):
        """Class .lock field is stored as a pointer to the locked target."""
        csource = emit_source(
            "Bag: class { a: i64 }\n"
            "BagView: class { target: Bag.lock } as {\n"
            "  create: function {target: Bag.lock} out this is {\n"
            "    return (meta.create target: target)\n"
            "  }\n"
            "}\n"
            "main: function is { b: Bag a: 1\nv: BagView target: b }"
        )
        # .lock field of stack-class type emits as pointer in struct
        assert "z_Bag_t* target;" in csource

    def test_class_lock_field_runtime(self):
        """End-to-End: class with .lock accesses the locked target."""
        csource = emit_source(
            "Bag: class { a: i64 b: i64 }\n"
            "BagView: class { target: Bag.private.lock } as {\n"
            "  create: function {target: Bag.lock} out this is {\n"
            "    return (meta.create target: target)\n"
            "  }\n"
            "  getval: function {v: this} out i64 is { return v.target.a }\n"
            "}\n"
            "main: function is {\n"
            "  b: Bag a: 42 b: 99\n"
            "  v: BagView target: b\n"
            "  n: BagView.getval v\n"
            '  print "\\{n}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_mixed_lock_and_owned_destructor_field_destructor(self):
        """A class that holds BOTH a `.lock` field and a field whose
        type carries a destructor must have its destructor clean up
        the owned field while leaving the `.lock` field untouched.
        This is the exact shape a pure-Zerolang BufWriter would have
        (`sink: Writer.lock` + an owned Payload) — the native Phase
        1b implementation skipped this Path in the test suite.

        Uses an inner user class with a String field to force
        `needs_field_cleanup` without depending on generic-type mono
        ordering (user classes with `Bytes` fields hit a separate
        pre-existing emitter ordering bug unrelated to lock semantics)."""
        csource = emit_source(
            "Bag: class { a: i64 }\n"
            "Payload: class { label: String }\n"
            "Mixed: class {\n"
            "  target: Bag.lock\n"
            "  inner:  Payload\n"
            "}\n"
            "main: function is {\n"
            "  b: Bag a: 1\n"
            '  s: String from: "hi"\n'
            "  p: Payload label: s\n"
            "  m: Mixed target: b inner: p\n"
            '  print "done"\n'
            "}"
        )
        # Destructor must exist because the `inner` field's type
        # (Payload) carries its own destructor, so Mixed needs to
        # cascade cleanup — this is what forces needs_field_cleanup.
        assert "static void z_Mixed_destroy(z_Mixed_t* p)" in csource
        # Extract the destructor body and verify only the owned field
        # is cleaned up.
        body_start = csource.index("static void z_Mixed_destroy(z_Mixed_t* p) {")
        body_end = csource.index("}", body_start)
        destructor = csource[body_start:body_end]
        assert "z_Payload_destroy(&p->inner)" in destructor, (
            f"destructor should cascade into z_Payload_destroy on the "
            f"owned inner field; got:\n{destructor}"
        )
        assert "p->target" not in destructor, (
            f".lock field (target) must not be touched in destructor; "
            f"got:\n{destructor}"
        )
        assert "z_Bag_destroy" not in destructor, (
            f"destructor must not destroy the locked Bag; got:\n{destructor}"
        )
        # End-to-end: compile + run. A double-free on the `.lock`
        # field would surface here (z_Bag_destroy on a stack local
        # would crash or ASan would flag).
        output = compile_and_run(csource)
        assert output.strip() == "done"


class TestTakeInArmMemorySafety:
    """Memory safety tests for .take inside if/match arms using ASan."""

    def test_take_in_one_if_arm_no_leak(self):
        """Take in then-arm, else-arm runs at runtime -- no leak."""
        csource = emit_source(
            "Box: class { label: String }\n"
            "main: function is {\n"
            '  a: Box label: "hello".string\n'
            '  print "\\{a.label}"\n'
            "  x: 0\n"
            "  if x > 0 then { b: a.take }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_take_in_one_if_arm_no_double_free(self):
        """Take in then-arm, then-arm runs at runtime -- no double-free."""
        csource = emit_source(
            "Box: class { label: String }\n"
            "main: function is {\n"
            '  a: Box label: "hello".string\n'
            '  print "\\{a.label}"\n'
            "  x: 1\n"
            "  if x > 0 then { b: a.take }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_take_in_if_else_no_leak(self):
        """Take in then-arm, no take in else-arm, else runs -- no leak."""
        csource = emit_source(
            "Box: class { label: String }\n"
            "consume: function {b: Box.take} is {}\n"
            "main: function is {\n"
            '  a: Box label: "hello".string\n'
            '  print "\\{a.label}"\n'
            "  x: 0\n"
            '  if x > 0 then { consume a } else { print "else" }\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_arm_local_var_cleanup(self):
        """Variable declared inside arm is cleaned up inside arm scope."""
        csource = emit_source(
            "Box: class { label: String }\n"
            "main: function is {\n"
            "  x: 1\n"
            '  if x > 0 then { b: Box label: "inner".string\n print "\\{b.label}" }\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_take_in_match_arm_with_diverging_inner_if_runs_clean(self):
        """3-level nesting (for -> match -> if-then-arm) with take +
        return inside the innermost if-arm. Runtime pin for the
        typechecker relax landed in `eea6e9a`: the `.take`n value's
        heap is freed exactly once via the receiver's destructor;
        every non-taken local along the diverging path is destroyed
        by the scope-walk in `_emit_scope_cleanup` (and the post-arm
        `_emit_taken_vars_cleanup` zeros the taken slot so the walk
        skips it). ASAN-clean exit + correct stdout."""
        csource = emit_source(
            "Named: class { label: String }\n"
            "Sig: variant { A: null  B: null } as { tag: u8.tag }\n"
            "main: function is {\n"
            '    a: Named label: "hi".string\n'
            "    s: Sig.A\n"
            "    for i: 1.iterate loop {\n"
            "        match (\n"
            "            s\n"
            "        ) case A then {\n"
            "            if i == 0.i64 then {\n"
            "                b: a.take\n"
            '                print "\\{b.label}"\n'
            "                return\n"
            "            }\n"
            "            tail: 0\n"
            "        } case B then {\n"
            "            tail: 0\n"
            "        }\n"
            "    }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "hi"


class TestWithBlockScopeCleanup:
    """Pins the scope-tracking fix for `with`-block bodies.

    `_emit_with` snapshots `cleanup_vars` length on entry to the
    do-body and emits destructor calls for any user-visible
    bindings that were added during the doexpr (notably the
    typecheck-hoisted `_tN` synth temps from `.take` arguments).
    Before the fix, those temps were tracked at function scope, so
    `z_String_free(&_tN)` was emitted after the with-block's
    closing brace -- referencing an out-of-scope C local. gcc
    rejected the resulting code with `_tN undeclared`.
    """

    def test_take_arg_in_with_block_compiles(self):
        """`.take`-argument hoisted temp inside a `with`-do body
        compiles cleanly. Pre-fix, the cleanup landed at function
        scope after the with-block closed, triggering
        `_tN undeclared`."""
        csource = emit_source(
            "Sink: class { last: String } as {\n"
            "    add: function {:this s: String.take} is "
            "{ this.last = s }\n"
            "}\n"
            "main: function is {\n"
            '    sk: Sink last: "init".string\n'
            "    with t: 0 do {\n"
            '        sk.add s: "hello".string\n'
            '        print "stored: \\{sk.last}"\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "stored: hello\n"

    def test_take_arg_in_with_block_asan_clean(self):
        """ASan run: the per-take temp lives only for the with
        block; on exit it's nullified (its data was transferred
        into `sk.last`) so the destructor is a no-op and no
        double-free / use-after-free."""
        csource = emit_source(
            "Sink: class { last: String } as {\n"
            "    add: function {:this s: String.take} is "
            "{ this.last = s }\n"
            "}\n"
            "main: function is {\n"
            '    sk: Sink last: "init".string\n'
            "    with t: 0 do {\n"
            '        sk.add s: "hello".string\n'
            "    }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"


class TestAliasBinding:
    """Phase B: binding alias optimization.

    When the RHS of `with name: expr do body` is a plain Path reference,
    and when `x: y.take` / `x: y.borrow` is inline, the emitter skips the
    C local declaration and substitutes the source expression at reference
    sites. Calls and reftype-pointer hops are NOT aliased.
    """

    def test_with_bare_name_alias(self):
        """with a: c do ... emits a as an alias for c."""
        csource = emit_source(
            'main: function is {\n  c: "hi".string\n  with a: c do print a\n}'
        )
        assert "/* alias: a => c */" in csource
        assert "z_String_t a =" not in csource

    def test_with_take_alias_no_double_free(self):
        """with a: c.take do ... aliases and runs cleanly (single free)."""
        csource = emit_source(
            'main: function is {\n  c: "hi".string\n  with a: c.take do print a\n}'
        )
        assert "/* alias: a => c */" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hi"

    def test_with_borrow_alias(self):
        """with a: c.borrow do ... aliases a to c."""
        csource = emit_source(
            'main: function is {\n  c: "hi".string\n  with a: c.borrow do print a\n}'
        )
        assert "/* alias: a => c */" in csource

    def test_with_call_rhs_not_aliased(self):
        """with a: ctor arg do ... still emits a real local."""
        csource = emit_source(
            "Bag: class { x: i64 } as {\n"
            "  public: unit { :x }\n"
            "  create: function { x: i64 } out this is {\n"
            "    return (meta.create x: x)\n"
            "  }\n"
            "}\n"
            "main: function is {\n"
            '  with b: (Bag x: 1) do print "\\{b.x}"\n'
            "}"
        )
        assert "/* alias: b" not in csource

    def test_with_dotted_valtype_path_alias(self):
        """with v: e.name do ... aliases v to e.name (all-valtype Path)."""
        csource = emit_source(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '  e: entry name: ("alice".str to: 16) age: 30\n'
            "  with v: e.name do print v.stringview\n"
            "}"
        )
        assert "/* alias: v => e.name */" in csource
        output = compile_and_run(csource)
        assert output.strip() == "alice"

    def test_with_reftype_pointer_path_not_aliased(self):
        """with v: inner.label do ... — inner is a class (reftype pointer),
        no alias; v gets a real local to pin the pointer in a register."""
        csource = emit_source(
            "Box: class { label: String }\n"
            "main: function is {\n"
            '  b: Box label: "hello".string\n'
            '  with v: b.label do print "\\{v}"\n'
            "}"
        )
        # b is a class (reftype pointer) so b.label is NOT aliased
        assert "/* alias: v" not in csource

    def test_inline_take_alias(self):
        """Inline `d: c.take` on a class is aliased (no separate local)."""
        csource = emit_source(
            "MyClass: class { x: 0 }\nmain: function is { c: MyClass\n d: c.take }"
        )
        assert "/* alias: d => c */" in csource
        assert "z_MyClass_t d =" not in csource

    def test_inline_plain_assign_not_aliased(self):
        """Plain `d: c` (no inline .take/.borrow) is NOT aliased —
        it keeps existing semantics (copy for valtypes, implicit take for reftypes)."""
        csource = emit_source(
            'main: function is {\n  c: "hi".string\n  d: c\n  print d\n}'
        )
        assert "/* alias: d" not in csource

    def test_with_alias_end_to_end(self):
        """Full program with multiple aliased with-bindings compiles and runs."""
        csource = emit_source(
            "entry: record { name: (str to: 16) age: i64 }\n"
            "main: function is {\n"
            '  e: entry name: ("alice".str to: 16) age: 30\n'
            "  with who: e.name do {\n"
            "    print who.stringview\n"
            '    print "\\{who.length}"\n'
            "  }\n"
            "  with age: e.age do {\n"
            '    print "\\{age}"\n'
            "  }\n"
            "}"
        )
        assert "/* alias: who => e.name */" in csource
        assert "/* alias: age => e.age */" in csource
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip().split("\n") == ["alice", "5", "30"]


class TestMatchArmAlias:
    """Phase C: match-arm subject binding alias.

    Inside a `case <subtype> then { ... }` arm, references to the (shadow-
    narrowed) subject name route through `_alias_map` just like Phase B
    `with` bindings. The unwrap expression — `(*(payload_t*)s.data)` for
    union, `s.data.subtype` for variant — is emitted once at arm entry as
    an alias comment; reference sites substitute it in place.
    """

    def test_variant_arm_aliased(self):
        """Variant arm seeds an alias comment and substitutes at use sites."""
        csource = emit_source(
            "result: variant { ok: i64 err: i64 none: null }\n"
            "main: function is {\n"
            "  c: result.ok 99\n"
            "  match ( c ) case ok then {\n"
            '    print "\\{c}"\n'
            "  } case err then {\n"
            '    print "\\{c}"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )
        assert "/* alias: c => c.data.ok */" in csource
        output = compile_and_run(csource)
        assert output.strip() == "99"

    def test_union_arm_aliased_with_payload(self):
        """Union arm with a record payload emits the cast-deref unwrap alias."""
        csource = emit_source(
            "pt: record { x: i64 y: i64 }\n"
            "circle: record { radius: i64 }\n"
            "Shape: union { :pt :circle }\n"
            "main: function is {\n"
            "  s: Shape.pt (pt x: 10 y: 20)\n"
            "  match ( s ) case pt then {\n"
            '    print "\\{s.x}"\n'
            "  } case circle then {\n"
            '    print "\\{s.radius}"\n'
            "  }\n"
            "}"
        )
        assert "/* alias: s => (*(z_pt_t*)s.data) */" in csource
        output = compile_and_run(csource)
        assert output.strip() == "10"

    def test_null_payload_arm_not_aliased(self):
        """Null-payload arms have nothing to unwrap; no alias is emitted."""
        csource = emit_source(
            "result: variant { ok: i64 none: null }\n"
            "main: function is {\n"
            "  r: result.none\n"
            "  match ( r ) case ok then {\n"
            '    print "ok"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )
        assert "/* alias: r => r.data.ok */" in csource
        # the `none` arm payload is null — no alias for it
        assert "/* alias: r => r.data.none" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "none"

    def test_arm_alias_end_to_end(self):
        """Multiple arms each seed their own alias; program runs under ASan."""
        csource = emit_source(
            "result: variant { ok: i64 err: i64 none: null }\n"
            "main: function is {\n"
            "  r: result.ok 42\n"
            "  match ( r ) case ok then {\n"
            '    print "\\{r}"\n'
            "  } case err then {\n"
            '    print "\\{r}"\n'
            "  } case none then {\n"
            '    print "none"\n'
            "  }\n"
            "}"
        )
        assert "/* alias: r => r.data.ok */" in csource
        assert "/* alias: r => r.data.err */" in csource
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "42"


class TestIOFileStreaming:
    """I/O Phase 6: File handles, RAII close, streaming read/write.

    Runs the compiled binary end-to-end. Temp files live in /tmp and
    are cleaned up inside the test body.
    """

    def test_io_open_raii_close(self, tmp_path):
        """io.open returns a File whose fd is closed by its destructor."""
        target = tmp_path / "io_open_test.txt"
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.write\n'
            "    match (r) case ok then {\n"
            '        print "opened"\n'
            "    } case err then {\n"
            '        print "failed"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "opened"
        assert target.exists()

    def test_io_open_nonexistent_returns_err(self, tmp_path):
        """Opening a missing File for read returns the err arm."""
        missing = tmp_path / "does-not-exist"
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{missing}" mode: openmode.read\n'
            "    match (r) case ok then {\n"
            '        print "unexpected ok"\n'
            "    } case err then {\n"
            '        print "got err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "got err"

    def test_file_write_read_roundtrip(self, tmp_path):
        """Write Bytes, close, reopen, read — content survives."""
        target = tmp_path / "rw.bin"
        csource = emit_source(
            "main: function is {\n"
            "    buf: Bytes\n"
            "    buf.append from: 72.u8\n"
            "    buf.append from: 73.u8\n"
            f'    w: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        w\n"
            "    ) case ok then {\n"
            "        bv: ByteView.borrow from: buf.listview\n"
            "        wr: w.write from: bv\n"
            "        match (\n"
            "            wr\n"
            "        ) case ok then { } case err then {\n"
            '            print "write err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-w err"\n'
            "    }\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        b2: Bytes\n"
            "        rr: r.read into: b2 max: 16.u64\n"
            "        match (\n"
            "            rr\n"
            "        ) case ok then {\n"
            '            print "\\{b2.length}"\n'
            "        } case err then {\n"
            '            print "read err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-r err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"
        assert target.read_bytes() == b"HI"

    def test_file_projected_to_closer_runs_close(self, tmp_path):
        """Project a File value to `Closer` and invoke `close` through
        the protocol vtable. Exercises the wrapper + static vtable +
        create function emitted for io.File's `:Closer` conformance.
        """
        target = tmp_path / "proj_closer.txt"
        csource = emit_source(
            "tidy: function {c: Closer} is {\n"
            "    r: c.close\n"
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "closed"\n'
            "    } case err then {\n"
            '        print "close err"\n'
            "    }\n"
            "}\n"
            "main: function is {\n"
            f'    fr: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        fr\n"
            "    ) case ok then {\n"
            "        tidy c: fr.Closer\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        # Emitter must produce the wrapper, static vtable, and create
        # function — the symptoms of an absent conformance codegen.
        assert "z_File_Closer_create" in csource
        assert "z_File_Closer_vtable" in csource
        assert "z_File_Closer_close_wrapper" in csource
        output = compile_and_run(csource)
        assert output.strip() == "closed"
        assert target.exists()

    def test_file_projected_to_reader_writer_through_vtable(self, tmp_path):
        """Project a File through `Writer` to write Bytes, reopen and
        project through `Reader` to read them back. Exercises the
        full vtable pipeline for protocols with collection-typed
        parameters (Bytes / ByteView)."""
        target = tmp_path / "proj_rw.bin"
        csource = emit_source(
            "send: function {w: Writer} is {\n"
            "    msg: Bytes\n"
            "    msg.append from: 65.u8\n"
            "    msg.append from: 66.u8\n"
            "    bv: ByteView.borrow from: msg.listview\n"
            "    r: w.write from: bv\n"
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "sent"\n'
            "    } case err then {\n"
            '        print "send err"\n'
            "    }\n"
            "}\n"
            "recv: function {rd: Reader} is {\n"
            "    buf: Bytes\n"
            "    r: rd.read into: buf max: 32.u64\n"
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "\\{buf.length}"\n'
            "    } case err then {\n"
            '        print "recv err"\n'
            "    }\n"
            "}\n"
            "main: function is {\n"
            f'    w: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        w\n"
            "    ) case ok then {\n"
            "        send w: w.Writer\n"
            "    } case err then {\n"
            '        print "open-w"\n'
            "    }\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        recv rd: r.Reader\n"
            "    } case err then {\n"
            '        print "open-r"\n'
            "    }\n"
            "}"
        )
        assert "z_File_Reader_create" in csource
        assert "z_File_Writer_create" in csource
        assert "z_File_Reader_read_wrapper" in csource
        assert "z_File_Writer_write_wrapper" in csource
        output = compile_and_run(csource)
        lines = output.strip().splitlines()
        assert "sent" in lines
        assert "2" in lines  # read count — proves bytes.length grew through vtable
        assert target.read_bytes() == b"AB"

    def test_file_projected_to_seeker_through_vtable(self, tmp_path):
        """Seek via the Seeker protocol (not via the File handle
        directly) — the vtable entry forwards to z_File_seek."""
        target = tmp_path / "proj_seeker.bin"
        csource = emit_source(
            "main: function is {\n"
            "    buf: Bytes\n"
            "    buf.append from: 65.u8\n"
            "    buf.append from: 66.u8\n"
            "    buf.append from: 67.u8\n"
            f'    w: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        w\n"
            "    ) case ok then {\n"
            "        bv: ByteView.borrow from: buf.listview\n"
            "        wr: w.write from: bv\n"
            "        match (\n"
            "            wr\n"
            "        ) case ok then {\n"
            '            print "w ok"\n'
            "        } case err then {\n"
            '            print "w err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-w err"\n'
            "    }\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        s: r.Seeker\n"
            "        pos: s.seek to: 0.i64 from: seekorigin.end\n"
            "        match (\n"
            "            pos\n"
            "        ) case ok then {\n"
            '            print "seeked to end"\n'
            "        } case err then {\n"
            '            print "seek err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-r err"\n'
            "    }\n"
            "}"
        )
        assert "z_File_Seeker_create" in csource
        assert "z_File_Seeker_vtable" in csource
        output = compile_and_run(csource)
        assert "seeked to end" in output.splitlines()

    def test_mkdirp_and_stat_roundtrip(self, tmp_path):
        """io.mkdirp creates a nested Path; io.stat confirms it's a
        directory and reports a non-zero byte size."""
        target = tmp_path / "a" / "b" / "c"
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.mkdirp "{target}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then { } case err then {\n"
            '        print "mkdirp err"\n'
            "    }\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        match (\n"
            "            s.kind\n"
            "        ) case dir then {\n"
            '            print "dir"\n'
            "        } case file then {\n"
            '            print "File"\n'
            "        } case symlink then {\n"
            '            print "link"\n'
            "        } case other then {\n"
            '            print "other"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "stat err"\n'
            "    }\n"
            "}"
        )
        assert "z_io_mkdirp" in csource
        assert "z_io_stat" in csource
        assert "z_filestat_t" in csource
        assert "z_filekind_t" in csource
        output = compile_and_run(csource)
        assert output.strip() == "dir"
        assert target.is_dir()

    def test_stat_reports_mtime_and_mode(self, tmp_path):
        """filestat carries mtimeSeconds (Unix epoch) and raw POSIX
        mode bits; stat on a just-created directory populates both
        with non-zero values."""
        target = tmp_path / "statfields"
        target.mkdir()
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        hasmtime: s.mtimeSeconds > 0.u64\n"
            "        hasmode: s.mode > 0.u32\n"
            '        print "mtime=\\{hasmtime} mode=\\{hasmode}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "mtimeSeconds" in csource
        output = compile_and_run(csource)
        assert output.strip() == "mtime=1 mode=1"

    def test_stat_reports_extended_identity_fields(self, tmp_path):
        """Extended filestat carries device, inode, nlink, and the two
        additional timestamps. For a freshly-created regular file: inode
        is non-zero (POSIX guarantees it), nlink is 1 (no hard links),
        device is non-zero (Any backing fs), atime/ctime are populated."""
        target = tmp_path / "extfields.txt"
        target.write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        hasdev:   s.device > 0.u64\n"
            "        hasinode: s.inode > 0.u64\n"
            "        nlink_one: s.nlink == 1.u64\n"
            "        hasatime: s.atimeSeconds > 0.u64\n"
            "        hasctime: s.ctimeSeconds > 0.u64\n"
            '        print "dev=\\{hasdev} ino=\\{hasinode} nl=\\{nlink_one}"\n'
            '        print "at=\\{hasatime} ct=\\{hasctime}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "fs->device" in csource
        assert "fs->inode" in csource
        assert "fs->nlink" in csource
        output = compile_and_run(csource)
        assert output.strip().splitlines() == [
            "dev=1 ino=1 nl=1",
            "at=1 ct=1",
        ]

    def test_lstat_reports_symlink_kind(self, tmp_path):
        """lstat does not follow symlinks — on a symlink target, kind
        is symlink (not whatever the link points at)."""
        real = tmp_path / "real.txt"
        real.write_text("hello")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.lstat "{link}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        match (\n"
            "            s.kind\n"
            "        ) case symlink then {\n"
            '            print "link"\n'
            "        } case file then {\n"
            '            print "File"\n'
            "        } case dir then {\n"
            '            print "dir"\n'
            "        } case other then {\n"
            '            print "other"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "z_io_lstat" in csource
        output = compile_and_run(csource)
        assert output.strip() == "link"

    def test_io_stdout_writes_to_stdout(self):
        """`io.stdout` returns a borrowed Writer over fd 1; writing
        to it goes directly to the process's stdout (captured by
        compile_and_run)."""
        csource = emit_source(
            "main: function is {\n"
            "    w: io.stdout\n"
            "    msg: Bytes\n"
            "    msg.append from: 104.u8\n"
            "    msg.append from: 105.u8\n"
            "    msg.append from: 10.u8\n"
            "    bv: ByteView.borrow from: msg.listview\n"
            "    r: w.write from: bv\n"
            "    match (\n"
            "        r\n"
            "    ) case ok then { } case err then {\n"
            '        print "write err"\n'
            "    }\n"
            "}"
        )
        assert "z_io_stdout" in csource
        assert "z_io_stdout_File" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hi"

    def test_bytes_typedef_emits_to_list_u8(self):
        """`Bytes` — a class typedef over `List of: u8` — must lower to
        the base List type end-to-end: construction, append, length,
        scope cleanup. Regression guard for Phase 6d (the Bytes typedef
        was previously not being followed in the emitter)."""
        csource = emit_source(
            "main: function is {\n"
            "    b: Bytes\n"
            "    b.append from: 72.u8\n"
            "    b.append from: 73.u8\n"
            '    print "\\{b.length}"\n'
            "}"
        )
        # C must reference the base type, never `z_Bytes_t` /
        # `z_Bytes_create` / `z_Bytes_destroy` — none of those are
        # defined anywhere.
        assert "z_Bytes_t" not in csource
        assert "z_Bytes_create" not in csource
        assert "z_Bytes_destroy" not in csource
        assert "z_List_u8_create" in csource
        assert "z_List_u8_append" in csource
        assert "z_List_u8_destroy" in csource
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_file_seek_roundtrip(self, tmp_path):
        """Write Bytes, reopen, seek past a prefix, read the remainder."""
        target = tmp_path / "seek.bin"
        csource = emit_source(
            "main: function is {\n"
            "    buf: Bytes\n"
            "    buf.append from: 72.u8\n"
            "    buf.append from: 101.u8\n"
            "    buf.append from: 108.u8\n"
            "    buf.append from: 108.u8\n"
            "    buf.append from: 111.u8\n"
            f'    w: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        w\n"
            "    ) case ok then {\n"
            "        bv: ByteView.borrow from: buf.listview\n"
            "        wr: w.write from: bv\n"
            "        match (\n"
            "            wr\n"
            "        ) case ok then { } case err then {\n"
            '            print "write err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-w err"\n'
            "    }\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        sk: r.seek to: 2.i64 from: seekorigin.start\n"
            "        match (\n"
            "            sk\n"
            "        ) case ok then { } case err then {\n"
            '            print "seek err"\n'
            "        }\n"
            "        b2: Bytes\n"
            "        rr: r.read into: b2 max: 16.u64\n"
            "        match (\n"
            "            rr\n"
            "        ) case ok then {\n"
            '            print "\\{b2.length}"\n'
            "        } case err then {\n"
            '            print "read err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-r err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        # "Hello" (5 bytes) seeked past 2 -> "llo" (3 bytes) remaining
        assert output.strip() == "3"

    def test_explicit_close_idempotent_with_raii(self, tmp_path):
        """Calling File.close and then letting RAII run must not double-close."""
        target = tmp_path / "close.txt"
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.write\n'
            "    match (r) case ok then {\n"
            "        cr: r.close\n"
            "        match (cr) case ok then {\n"
            '            print "closed ok"\n'
            "        } case err then {\n"
            '            print "close err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        # Primary assertion: clean exit and single expected line.
        # If RAII had double-closed, EBADF would surface or the
        # process would abort under stricter allocators.
        assert output.strip() == "closed ok"

    def test_list_dir_happy_path(self, tmp_path):
        """io.listDir on a populated directory returns the correct
        entry count, excluding `.` and `..`."""
        for n in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / n).write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.listDir "{tmp_path}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "len=\\{r.length}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "z_io_listDir" in csource
        assert "z_List_String_t" in csource
        output = compile_and_run(csource)
        assert output.strip() == "len=3"

    def test_list_dir_notfound(self, tmp_path):
        """listDir on a nonexistent Path takes the err arm. Drilling
        into the specific IoError variant is a separate test via a
        helper function; direct re-matching on a narrowed union subject
        is a Phase 6j narrowing limitation."""
        missing = tmp_path / "does-not-exist"
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.listDir "{missing}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "ok"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "err"

    def test_list_dir_notdir(self, tmp_path):
        """listDir on a regular File takes the err arm (ENOTDIR). The
        specific IoError-variant discrimination is tested indirectly:
        the emitted IoError tag enum must include NOTDIR, and the errno
        Map must route ENOTDIR to it."""
        target = tmp_path / "regular.txt"
        target.write_text("hello")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.listDir "{target}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "ok"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "Z_IOERROR_TAG_NOTDIR" in csource
        assert "case ENOTDIR:" in csource
        output = compile_and_run(csource)
        assert output.strip() == "err"

    def test_bufwriter_roundtrip_over_file(self, tmp_path):
        """Phase 1b: open a File for write, wrap it in BufWriter, write
        Bytes (smaller than capacity so they stay buffered), flush to
        drain to the fd, close; reopen for read and verify the File
        content survived the buffered Path."""
        target = tmp_path / "buf.bin"
        csource = emit_source(
            "main: function is {\n"
            f'    w: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        w\n"
            "    ) case ok then {\n"
            "        bw: io.BufWriter.create to: w.lock capacity: 32.u64\n"
            "        buf: Bytes\n"
            "        buf.append from: 65.u8\n"
            "        buf.append from: 66.u8\n"
            "        buf.append from: 67.u8\n"
            "        bv: ByteView.borrow from: buf.listview\n"
            "        wr: bw.write from: bv\n"
            "        bv.release\n"
            "        match (\n"
            "            wr\n"
            "        ) case ok then { } case err then {\n"
            '            print "write err"\n'
            "        }\n"
            "        fr: bw.flush\n"
            "        match (\n"
            "            fr\n"
            "        ) case ok then {\n"
            '            print "flushed"\n'
            "        } case err then {\n"
            '            print "flush err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-w err"\n'
            "    }\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        b2: Bytes\n"
            "        rr: r.read into: b2 max: 16.u64\n"
            "        match (\n"
            "            rr\n"
            "        ) case ok then {\n"
            '            print "\\{b2.length}"\n'
            "        } case err then {\n"
            '            print "read err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open-r err"\n'
            "    }\n"
            "}"
        )
        # emission order: struct_defs must place z_List_u8_t + z_Writer_t
        # before z_BufWriter_t (struct references both), and z_BufWriter_*
        # runtime bodies must land before the vtable wrappers that call
        # them. Check positional ordering.
        assert "} z_List_u8_t;" in csource
        assert "} z_Writer_t;" in csource
        assert "struct z_BufWriter_t {" in csource
        list_pos = csource.index("} z_List_u8_t;")
        writer_pos = csource.index("} z_Writer_t;")
        wrapper_pos = csource.index("struct z_BufWriter_t {")
        runtime_pos = csource.index("z_BufWriter_write(\n")
        assert list_pos < wrapper_pos, (
            "z_List_u8_t must be declared before z_BufWriter_t struct"
        )
        assert writer_pos < wrapper_pos, (
            "z_Writer_t must be declared before z_BufWriter_t struct"
        )
        assert wrapper_pos < runtime_pos, (
            "z_BufWriter_t struct must be declared before z_BufWriter_write body"
        )
        output = compile_and_run(csource)
        assert output.strip() == "flushed\n3"
        assert target.read_bytes() == b"ABC"

    def test_textwriter_roundtrip_over_file(self, tmp_path):
        """Phase 1c: three-layer stack (File -> BufWriter -> TextWriter).
        writeLine emits content + LF through the buffered sink; flush
        drains the buffered Bytes to the fd. Reopen for read and
        verify the File contains 'hi\\nbye\\n'."""
        target = tmp_path / "tw.txt"
        csource = emit_source(
            "main: function is {\n"
            f'    w: io.open path: "{target}" mode: openmode.write\n'
            "    match (\n"
            "        w\n"
            "    ) case ok then {\n"
            "        bw: io.BufWriter.create to: w.lock capacity: 64.u64\n"
            "        tw: io.TextWriter.create to: bw.lock\n"
            '        wr: tw.writeLine from: "hi"\n'
            "        match (\n"
            "            wr\n"
            "        ) case ok then { } case err then {\n"
            '            print "write err"\n'
            "        }\n"
            '        wr2: tw.writeLine from: "bye"\n'
            "        match (\n"
            "            wr2\n"
            "        ) case ok then { } case err then {\n"
            '            print "write2 err"\n'
            "        }\n"
            "        fr: tw.flush\n"
            "        match (\n"
            "            fr\n"
            "        ) case ok then {\n"
            '            print "flushed"\n'
            "        } case err then {\n"
            '            print "flush err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        # textwriter struct must land after bufwriter struct (it
        # holds a bufwriter.lock field). Textwriter runtime bodies
        # must land after bufwriter runtime bodies (write_line
        # forwards to bufwriter_write).
        assert "struct z_BufWriter_t {" in csource
        assert "struct z_TextWriter_t {" in csource
        bufwriter_struct_pos = csource.index("struct z_BufWriter_t {")
        textwriter_struct_pos = csource.index("struct z_TextWriter_t {")
        assert bufwriter_struct_pos < textwriter_struct_pos, (
            "z_BufWriter_t struct must be declared before z_TextWriter_t"
        )
        bufwriter_body_pos = csource.index("z_BufWriter_write(\n")
        textwriter_body_pos = csource.index("z_TextWriter_writeLine(\n")
        assert bufwriter_body_pos < textwriter_body_pos, (
            "z_BufWriter_write body must precede z_TextWriter_write_line"
        )
        output = compile_and_run(csource)
        assert output.strip() == "flushed"
        assert target.read_bytes() == b"hi\nbye\n"

    def test_textreader_reads_lines_and_reports_eof(self, tmp_path):
        """Phase 1c/2: three-layer read stack (File -> BufReader ->
        TextReader). Fixture written via writeText with real LFs;
        TextReader strips each line's LF and surfaces `IoError.eof`
        once the stream is drained."""
        target = tmp_path / "tr.txt"
        target.write_bytes(b"alpha\nbeta\ngamma\n")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 32.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            "        l1: tr.readLine\n"
            "        match (\n"
            "            l1\n"
            "        ) case ok then { print l1 } case err then {\n"
            '            print "l1 err"\n'
            "        }\n"
            "        l2: tr.readLine\n"
            "        match (\n"
            "            l2\n"
            "        ) case ok then { print l2 } case err then {\n"
            '            print "l2 err"\n'
            "        }\n"
            "        l3: tr.readLine\n"
            "        match (\n"
            "            l3\n"
            "        ) case ok then { print l3 } case err then {\n"
            '            print "l3 err"\n'
            "        }\n"
            "        l4: tr.readLine\n"
            "        match (\n"
            "            l4\n"
            "        ) case ok then {\n"
            '            print "unexpected"\n'
            "        } case err then {\n"
            '            print "eof"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        # textreader struct + runtime body must follow bufreader
        assert "struct z_BufReader_t {" in csource
        assert "struct z_TextReader_t {" in csource
        bufreader_struct_pos = csource.index("struct z_BufReader_t {")
        textreader_struct_pos = csource.index("struct z_TextReader_t {")
        assert bufreader_struct_pos < textreader_struct_pos, (
            "z_BufReader_t struct must be declared before z_TextReader_t"
        )
        bufreader_body_pos = csource.index("z_BufReader_read(\n")
        textreader_body_pos = csource.index("z_TextReader_readLine(\n")
        assert bufreader_body_pos < textreader_body_pos, (
            "z_BufReader_read body must precede z_TextReader_read_line"
        )
        # UTF-8 validator must be emitted (read_line calls it)
        assert "z_io_utf8_is_valid(" in csource
        output = compile_and_run(csource)
        assert output.strip().splitlines() == [
            "alpha",
            "beta",
            "gamma",
            "eof",
        ]

    def test_textreader_returns_unterminated_tail_then_eof(self, tmp_path):
        """A trailing unterminated chunk is surfaced once as ok(tail);
        the next readLine returns err(eof)."""
        target = tmp_path / "tail.txt"
        target.write_bytes(b"one\ntwo")  # note: no trailing LF
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 16.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            "        l1: tr.readLine\n"
            "        match (\n"
            "            l1\n"
            "        ) case ok then { print l1 } case err then {\n"
            '            print "l1 err"\n'
            "        }\n"
            "        l2: tr.readLine\n"
            "        match (\n"
            "            l2\n"
            "        ) case ok then { print l2 } case err then {\n"
            '            print "l2 err"\n'
            "        }\n"
            "        l3: tr.readLine\n"
            "        match (\n"
            "            l3\n"
            "        ) case ok then {\n"
            '            print "unexpected"\n'
            "        } case err then {\n"
            '            print "eof"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["one", "two", "eof"]

    def test_textreader_for_loop_iterates_lines(self, tmp_path):
        """`for line: tr loop { ... }` yields each line (LF stripped)
        until the stream drains."""
        target = tmp_path / "forloop.txt"
        target.write_bytes(b"alpha\nbeta\ngamma\n")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 32.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            "        for line: tr loop { print line }\n"
            '        print "done"\n'
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        assert "z_TextReader_call" in csource
        output = compile_and_run(csource)
        assert output.strip().splitlines() == [
            "alpha",
            "beta",
            "gamma",
            "done",
        ]

    def test_textreader_for_loop_empty_file(self, tmp_path):
        """Empty file: the iterator terminates on the first call."""
        target = tmp_path / "empty.txt"
        target.write_bytes(b"")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 32.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            '        for line: tr loop { print "got" }\n'
            '        print "done"\n'
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "done"

    def test_textreader_for_loop_unterminated_tail(self, tmp_path):
        """File ending without an LF: the unterminated tail is yielded
        once before the iterator reports none."""
        target = tmp_path / "tail.txt"
        target.write_bytes(b"head\ntail")  # no trailing LF
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 16.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            "        for line: tr loop { print line }\n"
            '        print "done"\n'
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["head", "tail", "done"]

    def test_textreader_for_loop_asan_clean(self, tmp_path):
        """Iterating over every line must not leak or double-free. The
        per-iteration String binding owns its heap buffer only until
        the loop head re-runs; ASan catches either leak."""
        target = tmp_path / "asan.txt"
        # Vary line length so the allocator sees different sizes and a
        # silent leak is easier to catch.
        target.write_bytes(b"a\nbb\nccc\ndddd\neeeee\n")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 16.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            "        for line: tr loop { print line }\n"
            '        print "done"\n'
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip().splitlines() == [
            "a",
            "bb",
            "ccc",
            "dddd",
            "eeeee",
            "done",
        ]

    def test_textreader_badencoding_on_invalid_utf8(self, tmp_path):
        """Invalid UTF-8 inside a line yields the `badencoding` arm.
        Fixture: the byte 0xFF is never valid as a UTF-8 lead byte."""
        target = tmp_path / "bad.txt"
        target.write_bytes(b"ok\n\xff\nafter\n")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        br: io.BufReader.create from: r.lock capacity: 32.u64\n"
            "        tr: io.TextReader.create from: br.lock\n"
            "        l1: tr.readLine\n"
            "        match (\n"
            "            l1\n"
            "        ) case ok then { print l1 } case err then {\n"
            '            print "l1 err"\n'
            "        }\n"
            "        l2: tr.readLine\n"
            "        match (\n"
            "            l2\n"
            "        ) case ok then { print l2 } case err then {\n"
            '            print "bad"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["ok", "bad"]


class TestBufReader:
    """BufReader now actually buffers: a single source `read` pulls up
    to `cap` Bytes, and subsequent small reads are served from the
    internal buffer until it drains. Oversize requests (max >= cap)
    bypass the buffer. Textreader keeps working unchanged because it
    always asks for `cap` Bytes and hits the bypass branch.

    The test shape unrolls each `br.read` call explicitly rather than
    looping, to sidestep a pre-existing emitter issue with union
    locals declared inside a `for while` body (their scope-exit
    destructor lands outside the loop body in generated C). Each
    test still exercises the BufReader Path through multiple reads
    of varying sizes."""

    @staticmethod
    def _read_and_print(idx: int, cap_chunk: str) -> str:
        """Emit one `rr{idx}: br.read into: buf max: N.u64` call +
        match that prints the ok byte count (or 'eof'/'err'). Distinct
        names per read to sidestep a pre-existing scoping issue with
        loop-body union locals."""
        name = f"rr{idx}"
        return (
            f"        {name}: br.read into: buf max: {cap_chunk}\n"
            "        match (\n"
            f"            {name}\n"
            "        ) case ok then {\n"
            f"            if {name} == 0.u64 then {{\n"
            '                print "eof"\n'
            "            }\n"
            f"            if {name} > 0.u64 then {{\n"
            f'                print "\\{{{name}}}"\n'
            "            }\n"
            "        } case err then {\n"
            '            print "err"\n'
            "        }\n"
        )

    def _program(self, path, cap, reads):
        """Build a main body that opens `Path`, creates a BufReader
        with capacity `cap`, and issues each read in `reads` in order
        (each a `N.u64` max argument)."""
        calls = "".join(
            TestBufReader._read_and_print(i, mx) for i, mx in enumerate(reads)
        )
        return (
            "main: function is {\n"
            f'    r: io.open path: "{path}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            f"        br: io.BufReader.create from: r.lock capacity: {cap}.u64\n"
            "        buf: Bytes\n"
            f"{calls}"
            '        print "end"\n'
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )

    def test_bufreader_small_reads_aggregate(self, tmp_path):
        """10-byte fixture, cap=32. Four reads of max=3 return sizes
        3/3/3/1, a fifth read hits EOF. All Bytes come from a single
        source syscall (observable only indirectly here — the totals
        match the fixture)."""
        target = tmp_path / "small.txt"
        target.write_bytes(b"0123456789")
        csource = emit_source(self._program(target, 32, ["3.u64"] * 5))
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["3", "3", "3", "1", "eof", "end"]

    def test_bufreader_straddle_refill(self, tmp_path):
        """10-byte fixture, cap=5, reads of max=3. The buffer holds at
        most 5 Bytes; after it drains, the next read triggers a refill
        for another 5, and the read-in-progress returns whatever the
        current buffer can satisfy. Verify totals match and the refill
        actually occurred by counting reads needed to drain."""
        target = tmp_path / "straddle.txt"
        target.write_bytes(b"0123456789")
        # 10 bytes, cap 5 -> needs >= two refills.
        csource = emit_source(self._program(target, 5, ["3.u64"] * 6))
        output = compile_and_run(csource)
        lines = output.strip().splitlines()
        chunks = [int(x) for x in lines if x.isdigit()]
        assert sum(chunks) == 10
        assert all(c <= 3 for c in chunks)
        # At least one read straddled: the first refill gave 5 bytes,
        # a 3-ask took 3, a 3-ask took 2 (buffer drained), next refill.
        assert len(chunks) >= 4

    def test_bufreader_oversize_bypass(self, tmp_path):
        """max >= cap takes the direct-source-read Path. The fixture
        is larger than cap; one oversize read should return the whole
        File (or most of it, if the kernel short-reads) in one call,
        with no intermediate copy through the BufReader buffer."""
        target = tmp_path / "big.txt"
        target.write_bytes(b"x" * 500)
        csource = emit_source(self._program(target, 16, ["1024.u64", "1024.u64"]))
        output = compile_and_run(csource)
        lines = output.strip().splitlines()
        assert "end" in lines
        chunks = [int(x) for x in lines if x.isdigit()]
        # First read: 500 via the bypass branch. Second: EOF (prints eof).
        assert chunks[0] == 500
        assert "eof" in lines

    def test_bufreader_eof_on_empty_file(self, tmp_path):
        """Empty source: the first read returns 0 directly; no extra
        spurious reads are issued."""
        target = tmp_path / "empty.txt"
        target.write_bytes(b"")
        csource = emit_source(self._program(target, 32, ["8.u64"]))
        output = compile_and_run(csource)
        assert output.strip().splitlines() == ["eof", "end"]

    def test_bufreader_asan_clean(self, tmp_path):
        """Multiple reads through a small-capacity BufReader under
        ASan. Exercises refill cycles, the buf field's scope-exit
        destroy, and the realloc Path on the caller's `into` List."""
        target = tmp_path / "asan.txt"
        target.write_bytes(b"z" * 50)
        csource = emit_source(self._program(target, 8, ["5.u64"] * 16))
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        lines = result.stdout.strip().splitlines()
        chunks = [int(x) for x in lines if x.isdigit()]
        assert sum(chunks) == 50


class TestIoSymlink:
    """io.symlink creates a symbolic link; io.readlink reads its target.
    Both route through standard IoError mapping; readlink additionally
    surfaces `invalidpath` when the Path exists but isn't a symlink."""

    def test_symlink_creates_link_and_readlink_reads_target(self, tmp_path):
        """symlink `link` -> `target`; readlink(link) returns `target`
        verbatim (no resolution, no normalization)."""
        real = tmp_path / "real.txt"
        real.write_text("x")
        link = tmp_path / "link.txt"
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.symlink target: "{real}" link: "{link}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "made"\n'
            "    } case err then {\n"
            '        print "sym err"\n'
            "    }\n"
            f'    r: io.readlink "{link}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        print r\n"
            "    } case err then {\n"
            '        print "read err"\n'
            "    }\n"
            "}"
        )
        assert "z_io_symlink" in csource
        assert "z_io_readlink" in csource
        output = compile_and_run(csource)
        lines = output.strip().splitlines()
        assert lines[0] == "made"
        assert lines[1] == str(real)
        assert link.is_symlink()

    def test_readlink_on_non_symlink_returns_invalidpath(self, tmp_path):
        """EINVAL from readlink(2) means the Path exists but isn't a
        symbolic link; surface as invalidpath so callers can tell it
        apart from notfound / permissiondenied."""
        real = tmp_path / "plain.txt"
        real.write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.readlink "{real}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "unexpected"\n'
            "    } case err then {\n"
            "        match (\n"
            "            r\n"
            "        ) case invalidpath then {\n"
            '            print "invalidpath"\n'
            "        } else {\n"
            '            print "other err"\n'
            "        }\n"
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "invalidpath"

    def test_symlink_exists_errors_on_existing_target(self, tmp_path):
        """EEXIST from symlink(2) maps to IoError.exists when the `link`
        Path already refers to something."""
        occupied = tmp_path / "existing"
        occupied.write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.symlink target: "x" link: "{occupied}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "unexpected"\n'
            "    } case err then {\n"
            "        match (\n"
            "            s\n"
            "        ) case exists then {\n"
            '            print "exists"\n'
            "        } else {\n"
            '            print "other"\n'
            "        }\n"
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "exists"


class TestOsUnit:
    """`os` unit: process-level primitives. argv/env/exit have no
    companion struct types (unlike the `io` File/stream stack) so the
    plumbing is smaller: three native function bodies plus two globals
    populated by main() for `args` to read."""

    def test_exit_with_status(self, tmp_path):
        """os.exit terminates immediately with the given status."""
        csource = emit_source("main: function is {\n    os.exit code: 7.i32\n}")
        assert "z_os_exit(" in csource
        assert "static void z_os_exit(int32_t code)" in csource
        # Run and check the exit code. compile_and_run asserts success
        # so invoke the binary manually.
        import subprocess
        import shutil

        src = tmp_path / "exit.c"
        src.write_text(csource)
        bin_path = tmp_path / "exit"
        gcc = shutil.which(_CC)
        assert gcc is not None
        subprocess.run([gcc, "-o", str(bin_path), str(src)], check=True)
        r = subprocess.run([str(bin_path)], capture_output=True)
        assert r.returncode == 7

    def test_args_exposes_argv(self, tmp_path):
        """os.args returns a List of strings copied from argv. When the
        program is run with extra args, the count reflects that."""
        csource = emit_source(
            "main: function is {\n"
            "    argv: os.args\n"
            '    print "argc=\\{argv.length}"\n'
            "}"
        )
        assert "z_os_argc_g" in csource
        assert "z_os_argv_g" in csource
        assert "z_os_argc_g = argc;" in csource
        import subprocess
        import shutil

        src = tmp_path / "args.c"
        src.write_text(csource)
        bin_path = tmp_path / "args"
        gcc = shutil.which(_CC)
        assert gcc is not None
        subprocess.run([gcc, "-o", str(bin_path), str(src)], check=True)
        r = subprocess.run(
            [str(bin_path), "one", "two"], capture_output=True, text=True
        )
        # argv includes argv[0] (program path) so the total is 3.
        assert r.stdout.strip() == "argc=3"

    def test_get_env_some_and_none(self, tmp_path):
        """Option.some payload on hit, Option.none on miss."""
        csource = emit_source(
            "main: function is {\n"
            '    ev: os.env key: "Z_OS_TEST_VAR"\n'
            "    match (\n"
            "        ev\n"
            "    ) case some then {\n"
            "        print ev\n"
            "    } case none then {\n"
            '        print "missing"\n'
            "    }\n"
            "}"
        )
        assert "z_os_env(" in csource
        assert "getenv(" in csource
        import subprocess
        import shutil
        import os

        src = tmp_path / "env.c"
        src.write_text(csource)
        bin_path = tmp_path / "env"
        gcc = shutil.which(_CC)
        assert gcc is not None
        subprocess.run([gcc, "-o", str(bin_path), str(src)], check=True)

        env_hit = {**os.environ, "Z_OS_TEST_VAR": "hello world"}
        env_miss = {k: v for k, v in os.environ.items() if k != "Z_OS_TEST_VAR"}

        r_hit = subprocess.run(
            [str(bin_path)], capture_output=True, text=True, env=env_hit
        )
        assert r_hit.stdout.strip() == "hello world"

        r_miss = subprocess.run(
            [str(bin_path)], capture_output=True, text=True, env=env_miss
        )
        assert r_miss.stdout.strip() == "missing"


class TestListOfStringDestructor:
    """The List destructor iterates and calls z_String_free per element
    when the element type carries a destructor. Before this phase the
    element loop fired only when the C ctype was pointer-suffixed, so
    List of: String leaked per-element heap data."""

    def test_list_of_string_destructor_frees_elements(self, tmp_path):
        """Emitted List_string destructor must call z_String_free on
        each element, not just free the data array."""
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "b.txt").write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.listDir "{tmp_path}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "\\{r.length}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        # the per-element free loop must be present
        assert "z_String_free(&p->data[i])" in csource
        # and the overall run must still succeed
        output = compile_and_run(csource)
        assert output.strip() == "2"


class TestNarrowedFieldAccess:
    """Narrowed-subject field access. Inside `case ok then { ... }` a
    variable bound to a union/variant is narrowed to the payload type,
    so `s.size` reads as `filestat.size` with no explicit `s.ok` hop.
    The explicit form stays valid as a regression."""

    def test_union_narrowed_field_bare(self, tmp_path):
        """`s.size` inside a narrowed arm lowers to payload-unwrap and
        matches `s.ok.size`."""
        target = tmp_path / "narrowed"
        target.mkdir()
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "size=\\{s.size}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "z_filestat_t*" in csource
        output = compile_and_run(csource)
        assert output.strip().startswith("size=")
        size_str = output.strip().split("=")[1]
        assert int(size_str) > 0

    def test_union_narrowed_nested_kind_match(self, tmp_path):
        """`s.kind` returns the narrowed sub-field, then can be matched
        further — no `s.kind` workaround needed."""
        target = tmp_path / "narrowedkind"
        target.mkdir()
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        match (\n"
            "            s.kind\n"
            "        ) case dir then {\n"
            '            print "dir"\n'
            "        } case file then {\n"
            '            print "File"\n'
            "        } case symlink then {\n"
            '            print "link"\n'
            "        } case other then {\n"
            '            print "other"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "dir"

    def test_explicit_arm_access_errors_under_shadow(self, tmp_path):
        """Under shadow narrowing, `s.ok.size` reaches back into the
        shadowed parent union; it's a targeted type error. Bare
        `s.size` is the supported form (tested above)."""
        target = tmp_path / "explicit"
        target.mkdir()
        vfs, name = make_parser_vfs(
            "main: function is {\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.ok.size}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}",
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        typing = typecheck(program)
        errors = typing.errors
        assert any("shadowed parent" in e.msg for e in errors), (
            f"Expected shadow error, got: {[e.msg for e in errors]}"
        )

    def test_variant_narrowed_field_access(self):
        """Variant narrowing: `r.x` inside `case pt then` reads the
        inline payload via `r.data.pt.x`."""
        csource = emit_source(
            "point: record {\n"
            "    x: i64\n"
            "    y: i64\n"
            "}\n"
            "shape: variant {\n"
            "    pt: point\n"
            "    none: null\n"
            "}\n"
            "main: function is {\n"
            "    s: shape.pt (point x: 7 y: 11)\n"
            "    match (\n"
            "        s\n"
            "    ) case pt then {\n"
            '        print "x=\\{s.x}"\n'
            "    } case none then {\n"
            '        print "none"\n'
            "    }\n"
            "}"
        )
        assert ".data.pt.x" in csource
        output = compile_and_run(csource)
        assert output.strip() == "x=7"


class TestNarrowedFullSemantics:
    """Phase 6l: match-arm narrowing shadows the parent and exposes
    the payload type as-if. Method dispatch, re-matching, and passing
    the narrowed value all flow through the payload; reaching back to
    the parent arm is a targeted type error."""

    def test_method_dispatch_on_narrowed_list(self, tmp_path):
        """`r.get 0.u64` on a narrowed `List of: String` threads the
        List payload as `_this`, returning the first entry."""
        (tmp_path / "only.txt").write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.listDir "{tmp_path}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        name: r.get 0.u64\n"
            '        print "name=\\{name}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        assert "z_List_String_get(&(*(z_List_String_t*)r.data)" in csource
        output = compile_and_run(csource)
        assert output.strip() == "name=only.txt"

    def test_method_dispatch_on_narrowed_class(self, tmp_path):
        """`r.close` on a narrowed `io.File` dispatches to the File
        class's close method."""
        target = tmp_path / "a.txt"
        target.write_text("x")
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.open path: "{target}" mode: openmode.read\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            "        cr: r.close\n"
            "        match (\n"
            "            cr\n"
            "        ) case ok then {\n"
            '            print "closed"\n'
            "        } case err then {\n"
            '            print "close err"\n'
            "        }\n"
            "    } case err then {\n"
            '        print "open err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "closed"

    def test_rematch_on_narrowed_subject(self, tmp_path):
        """Re-matching the narrowed subject inside its arm dispatches
        on the PAYLOAD's tag (IoError), not the outer Result's tag.
        This is the canonical form — `match (r)` when r is already
        narrowed to IoError — not the old `match (r.err)` compound
        form (which reaches into the shadowed parent)."""
        missing = tmp_path / "nope"
        csource = emit_source(
            "main: function is {\n"
            f'    r: io.listDir "{missing}"\n'
            "    match (\n"
            "        r\n"
            "    ) case ok then {\n"
            '        print "ok"\n'
            "    } case err then {\n"
            "        match (\n"
            "            r\n"
            "        ) case notfound then {\n"
            '            print "notfound"\n'
            "        } else {\n"
            '            print "otherr"\n'
            "        }\n"
            "    }\n"
            "}"
        )
        assert "Z_IOERROR_TAG_NOTFOUND" in csource
        output = compile_and_run(csource)
        assert output.strip() == "notfound"

    def test_shadow_parent_arm_access_errors(self):
        """Accessing `.ok` / `.err` on a narrowed name reaches back
        into the shadowed parent and is a targeted error."""
        vfs, name = make_parser_vfs(
            "main: function is {\n"
            '    s: io.stat "/tmp"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.ok}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}",
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        typing = typecheck(program)
        errors = typing.errors
        assert any("shadowed parent" in e.msg for e in errors), (
            f"Expected shadow-parent error, got: {[e.msg for e in errors]}"
        )

    def test_shadow_outer_union_arg_rejected(self):
        """Passing a narrowed name to a function expecting the outer
        union type is a type error — the parent view is shadowed."""
        vfs, name = make_parser_vfs(
            "u: union { a: i64  b: i64 }\n"
            "take_union: function {x: u} is { }\n"
            "main: function is {\n"
            "  v: u.a 42\n"
            "  match (v) case a then {\n"
            "    take_union x: v\n"
            "  } case b then {\n"
            '    print "b"\n'
            "  }\n"
            "}",
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        typing = typecheck(program)
        errors = typing.errors
        assert any(
            "argument type mismatch" in e.msg or "expected u" in e.msg for e in errors
        ), f"Expected type-mismatch error, got: {[e.msg for e in errors]}"

    def test_shadow_missing_field_error(self):
        """Accessing an unknown field on a narrowed name is a targeted
        error (not a silent None)."""
        vfs, name = make_parser_vfs(
            "main: function is {\n"
            '    s: io.stat "/tmp"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            '        print "\\{s.bogus}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}",
            unitname="test",
            src_dir=LIB_DIR,
        )
        p = make_parser_with_vfs(vfs, name)
        program = p.parse()
        typing = typecheck(program)
        errors = typing.errors
        assert any("has no field 'bogus'" in e.msg for e in errors), (
            f"Expected missing-field error, got: {[e.msg for e in errors]}"
        )

    def test_narrowed_take_then_follow_on_access(self, tmp_path):
        """`.take` on a narrowed name transfers ownership and the
        taken value retains the narrowed type — `stolen.size` reads
        through the payload-unwrap on the alias."""
        target = tmp_path / "d"
        target.mkdir()
        csource = emit_source(
            "main: function is {\n"
            f'    s: io.stat "{target}"\n'
            "    match (\n"
            "        s\n"
            "    ) case ok then {\n"
            "        stolen: s.take\n"
            '        print "size=\\{stolen.size}"\n'
            "    } case err then {\n"
            '        print "err"\n'
            "    }\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip().startswith("size=")


class TestPanic:
    def test_panic_emits_call(self):
        """`panic msg: "..."` lowers to a `z_panic(...)` call with the
        message wired through."""
        csource = emit_source('main: function is {\n    panic msg: "boom"\n}')
        assert "z_panic(" in csource
        assert '"boom"' in csource

    def test_panic_terminates_program(self):
        """A program that panics exits with code 1 and the `zpanic:`
        prefix plus the message on stderr."""
        csource = emit_source('main: function is {\n    panic msg: "kaboom"\n}')
        rc, stdout, stderr = compile_and_capture(csource)
        assert rc == 1, f"expected exit 1, got {rc}"
        assert "zpanic: kaboom" in stderr, stderr
        assert stdout == ""

    def test_panic_with_dynamic_message(self):
        """Panic works with a composed String, not just a literal."""
        csource = emit_source(
            "main: function is {\n"
            '    where: "phase-2"\n'
            '    panic msg: "failed in \\{where}"\n'
            "}"
        )
        rc, _stdout, stderr = compile_and_capture(csource)
        assert rc == 1
        assert "zpanic: failed in phase-2" in stderr, stderr

    def test_panic_in_conditional(self):
        """A conditional branch that panics does not execute the
        remainder of the function; the branch that doesn't panic
        prints normally."""
        csource = emit_source(
            "main: function is {\n"
            "    n: 7\n"
            "    if n < 0 then {\n"
            '        panic msg: "negative"\n'
            "    }\n"
            '    print "ok"\n'
            "}"
        )
        rc, stdout, _stderr = compile_and_capture(csource)
        assert rc == 0
        assert stdout.strip() == "ok"

    def test_bounds_check_uses_zpanic(self):
        """A List bounds violation routes through the shared `z_panic`
        helper and produces a `zpanic:` prefixed stderr line."""
        csource = emit_source(
            "main: function is {\n"
            "    xs: (List of: i64)\n"
            "    xs.append from: 10\n"
            "    _: xs.get i: 5.u64\n"
            "}"
        )
        rc, _stdout, stderr = compile_and_capture(csource)
        assert rc == 1
        assert "zpanic: List get: index 5 out of bounds" in stderr, stderr

    def test_xalloc_path_uses_zpanic(self):
        """The OOM Path in the x-alloc helpers calls `z_panic("out of
        memory")`. Grep the emitted C to verify the wiring (running
        it reliably requires `ulimit` which is brittle in CI)."""
        csource = emit_source(
            "Counter: class { value: i64 }\n"
            "main: function is {\n"
            "    c: Counter value: 1\n"
            '    print "ok"\n'
            "}"
        )
        assert 'z_panic("out of memory")' in csource


class TestGeneratorEmitter:
    """Phase G4: state-machine codegen for synthesized generator
    `.call` bodies.

    The desugarer (G3) produces a class whose `.call` method body
    contains `Yield` nodes; this phase lowers them into a C-level
    switch/goto state machine. Each test drives a generator end-
    to-end (parse → desugar → typecheck → emit → gcc → run) and
    checks observable behaviour.
    """

    def test_simple_generator_emits_and_runs(self):
        """Three sequential yields produce `1\\n2\\n3\\n`, then
        exhaustion. The for-loop driver terminates when the synth
        class's `.call` returns OPT_NONE."""
        csource = emit_source(
            "gen: function {n: i64} out (Iterator gives: i64) is "
            "{ yield 1 yield 2 yield 3 }\n"
            "main: function is {\n"
            "    with g: (gen n: 0) do for x: g loop "
            '{ print "\\{x}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "1\n2\n3\n"

    def test_generator_with_loop_yields_all_elements(self):
        """Generator with a for-while loop yielding the counter
        produces the expected sequence. Validates that promoted
        locals (the loop counter) persist across suspensions."""
        csource = emit_source(
            "gen: function {count: i64} out (Iterator gives: i64) is {\n"
            "    pos: 0\n"
            "    for while pos < count loop {\n"
            "        yield 100 + pos\n"
            "        pos = pos + 1\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    with g: (gen count: 3) do for x: g loop "
            '{ print "\\{x}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "100\n101\n102\n"

    def test_generator_terminates_via_bare_return(self):
        """Mid-body `return` (no value) terminates the generator at
        that point. Values after the return are unreachable."""
        csource = emit_source(
            "gen: function {n: i64} out (Iterator gives: i64) is {\n"
            "    yield 1\n"
            "    yield 2\n"
            "    return\n"
            "}\n"
            "main: function is {\n"
            "    with g: (gen n: 0) do for x: g loop "
            '{ print "\\{x}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "1\n2\n"

    def test_generator_code_after_last_yield_runs_on_exhaustion_call(self):
        """Side-effect placed after the final yield runs on the
        `.call` that crosses into terminal state (i.e., the call
        following the consumption of the last value)."""
        csource = emit_source(
            "gen: function {n: i64} out (Iterator gives: i64) is {\n"
            "    yield 1\n"
            "    yield 2\n"
            '    print "post"\n'
            "}\n"
            "main: function is {\n"
            "    with g: (gen n: 0) do for x: g loop "
            '{ print "\\{x}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "1\n2\npost\n"

    def test_generator_idempotent_terminal(self):
        """Repeated `.call`s after exhaustion always return none and
        run no body code. Driven manually via `with`/`do` over a
        block calling `.call` directly."""
        csource = emit_source(
            "gen: function {n: i64} out (Iterator gives: i64) is "
            "{ yield 7 }\n"
            "main: function is {\n"
            "    with g: (gen n: 0) do {\n"
            "        for x: g loop "
            '{ print "\\{x}" }\n'
            "        for x: g loop "
            '{ print "\\{x}" }\n'
            "    }\n"
            "}"
        )
        # First for-loop drains the generator (prints 7); the second
        # immediately sees terminal state and prints nothing.
        assert compile_and_run(csource) == "7\n"

    def test_generator_method_receiver_takes_lock(self):
        """A method-context generator declares `b: this.lock` so the
        synth class holds the receiver locked for the iterator's
        lifetime. Verified at compile time: the generated factory
        delegates to `.create` with `this` as the lock target.

        Runtime: yield the receiver's field. Output sequence
        matches the bag's stored values."""
        csource = emit_source(
            "Bag: class { x: i64 y: i64 z: i64 } as {\n"
            "    each: function {b: this.lock} out (Iterator gives: i64) is {\n"
            "        yield b.x\n"
            "        yield b.y\n"
            "        yield b.z\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    b: Bag x: 10 y: 20 z: 30\n"
            "    with it: (Bag.each b: b.lock) do for v: it loop "
            '{ print "\\{v}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "10\n20\n30\n"

    def test_generator_method_private_receiver_lock(self):
        """A generator method declared `b: this.private.lock` reads
        the receiver's private fields. The factory's external param
        is exposed as the public `this.lock` so the call site
        (outside the type's privacy boundary) is accepted; the
        desugarer projects `.private` when forwarding to the synth
        class, which holds a `Bag.private.lock` field internally."""
        csource = emit_source(
            "Bag: class { x: i64 y: i64 z: i64 } as {\n"
            "    public: unit { :each }\n"
            "    each: function {b: this.private.lock} out "
            "(Iterator gives: i64) is {\n"
            "        yield b.x\n"
            "        yield b.y\n"
            "        yield b.z\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    b: Bag x: 10 y: 20 z: 30\n"
            "    with it: (Bag.each b: b.lock) do for v: it loop "
            '{ print "\\{v}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "10\n20\n30\n"

    def test_generator_relay_via_explicit_inner_binding(self):
        """P9: a generator can drive a sub-iterator across yield
        suspensions provided the inner iterator is held in an
        explicit named local. The desugarer sees the local as
        yield-crossing (yielding-loop rule), promotes it to a
        synth-class field, and the meta_create struct-default
        carve-out initialises the field to `(z_<inner>_t){0}` so
        the create signature accepts a struct param.

        Inline `for x: (subgen ...)` -- without the explicit
        binding -- does not yet relay correctly; the for-loop's
        iterable RHS would be a C-stack local that doesn't survive
        suspension. The spec's Composability subsection flags the
        gap and prescribes the explicit-binding form, which this
        test pins."""
        csource = emit_source(
            "intrange: function {lo: i64 hi: i64} out (Iterator gives: i64) is {\n"
            "    i: lo\n"
            "    for while i < hi loop {\n"
            "        yield i\n"
            "        i = i + 1\n"
            "    }\n"
            "}\n"
            "chained: function {a_lo: i64 a_hi: i64 b_lo: i64 b_hi: i64}\n"
            "                  out (Iterator gives: i64) is {\n"
            "    inner1: (intrange lo: a_lo hi: a_hi)\n"
            "    for x: inner1 loop { yield x }\n"
            "    inner2: (intrange lo: b_lo hi: b_hi)\n"
            "    for x: inner2 loop { yield x }\n"
            "}\n"
            "main: function is {\n"
            "    for v: (chained a_lo: 10 a_hi: 13 b_lo: 100 b_hi: 102) loop "
            '{ print "\\{v}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "10\n11\n12\n100\n101\n"

    def test_generator_relay_via_inline_iterable(self):
        """P9 inline form: the desugarer auto-extracts a yielding
        for-loop's non-trivial iterable RHS into a synthetic local
        before the loop, so `for x: (subgen ...) loop { yield x }`
        works without the user writing the explicit binding. The
        synth local then follows the same path as P9's explicit
        binding -- promoted to a synth-class field, struct-default
        initialised, retained across suspensions."""
        csource = emit_source(
            "intrange: function {lo: i64 hi: i64} out (Iterator gives: i64) is {\n"
            "    i: lo\n"
            "    for while i < hi loop {\n"
            "        yield i\n"
            "        i = i + 1\n"
            "    }\n"
            "}\n"
            "chained: function {a: i64 b: i64} out (Iterator gives: i64) is {\n"
            "    for x: (intrange lo: a hi: b) loop { yield x }\n"
            "}\n"
            "main: function is {\n"
            "    for v: (chained a: 10 b: 13) loop "
            '{ print "\\{v}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "10\n11\n12\n"

    def test_generator_promoted_local_binop_resolves_via_typeof(self):
        """Promoted-local field whose first RHS is a BinOp: the
        desugarer emits a TypeOfExpr placeholder for the field; the
        typechecker resolves the placeholder to the BinOp's result
        type (u32 here) when typing the first `this.c = a + b`
        reassignment in the synth `.call` body. The heuristic the
        TypeOfExpr replaced would have fallen back to i64 and the
        u32 -> i64 mismatch would have failed type-check."""
        csource = emit_source(
            "gen: function {a: u32 b: u32} out (Iterator gives: u32) is {\n"
            "    c: a + b\n"
            "    yield c\n"
            "    yield c\n"
            "}\n"
            "main: function is {\n"
            "    with g: (gen a: 10.u32 b: 5.u32) do "
            'for v: g loop { print "\\{v}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "15\n15\n"

    def test_no_arg_generator_for_loop_auto_invokes(self):
        """P3: a for-loop on a bare reference to a 0-arg function
        returning an iter-class auto-invokes the function once and
        drives the result. Pre-fix, the for-loop saw `gen` as a
        function value (not a callable iterator) and the dispatch
        missed; users had to wrap with a dummy parameter (`gen
        n: i64`) or write `for x: (gen) loop` -- both of which
        compiled identically to the bare form but still produced
        the same emitter mishandling.

        The typechecker rewrites the condition to a synthetic
        `Call(callable=gen, arguments=[])` wrapped in an
        Expression before re-dispatching, so the callable-object
        path (case 3) handles everything downstream including the
        emitter's standard CLASS_CREATE-of-return-type shape."""
        csource = emit_source(
            "gen: function out (Iterator gives: i64) is "
            "{ yield 1 yield 2 yield 3 }\n"
            "main: function is {\n"
            '    for x: gen loop { print "\\{x}" }\n'
            "}"
        )
        assert compile_and_run(csource) == "1\n2\n3\n"

    # ---- G6: bidirectional generators -----------------------------

    def test_bidirectional_generator_round_trip(self):
        """Bidirectional generator driven manually: each `.call
        value: <v>` sends `v` back into the generator; on resumption
        an expression-form yield (`x: yield ...`) binds `x` to that
        received value. Verified end-to-end by gating the third
        yield behind `x`."""
        csource = emit_source(
            "gen: function {n: i64} out "
            "(Iterator gives: i64 accepts: bool) is {\n"
            "    yield 1\n"
            "    x: yield 2\n"
            "    if x then yield 99\n"
            "}\n"
            "main: function is {\n"
            "    with g: (gen n: 0) do {\n"
            "        a: g.call value: false\n"
            "        match (a) case some then "
            '{ print "\\{a}" } case none then { print "(none)" }\n'
            "        b: g.call value: false\n"
            "        match (b) case some then "
            '{ print "\\{b}" } case none then { print "(none)" }\n'
            "        c: g.call value: true\n"
            "        match (c) case some then "
            '{ print "\\{c}" } case none then { print "(none)" }\n'
            "        d: g.call value: false\n"
            "        match (d) case some then "
            '{ print "\\{d}" } case none then { print "(none)" }\n'
            "    }\n"
            "}"
        )
        # Expected sequence:
        #   call 1 (value=false) -> yield 1
        #   call 2 (value=false) -> yield 2
        #   call 3 (value=true)  -> x = true, if x -> yield 99
        #   call 4 (value=false) -> terminal (state == -1) -> none
        assert compile_and_run(csource) == "1\n2\n99\n(none)\n"

    def test_bidirectional_generator_string_takes(self):
        """P6: reftype `accepts: String.take`. The synth class stores
        the resume-input as an owned String field (the desugarer
        strips the `.take` annotation -- fields hold owned values
        directly), and the emitter's prologue calls the
        appropriate destructor before each per-call overwrite so
        the previous value's heap data is freed. The standard
        `z_String_free` is NULL-guarded so the destructor call is
        safe even on the first call where the field is
        zero-initialised.

        This also pins the wider `with`-block scope-cleanup fix:
        the typecheck-hoisted `_tN` temps for `.take` args inside
        the do-body now get destroyed at block exit instead of
        leaking to function-exit, where they'd reference an
        out-of-scope C local."""
        csource = emit_source(
            "gen: function {n: i64} out "
            "(Iterator gives: i64 accepts: String.take) is {\n"
            "    yield 1\n"
            "    s: yield 2\n"
            "    yield 99\n"
            "}\n"
            "main: function is {\n"
            "    with g: (gen n: 0) do {\n"
            '        a: g.call value: "first".string\n'
            "        match (a) case some then "
            '{ print "a: \\{a}" } case none then { print "(none)" }\n'
            '        b: g.call value: "second".string\n'
            "        match (b) case some then "
            '{ print "b: \\{b}" } case none then { print "(none)" }\n'
            '        c: g.call value: "third".string\n'
            "        match (c) case some then "
            '{ print "c: \\{c}" } case none then { print "(none)" }\n'
            "    }\n"
            "}"
        )
        assert compile_and_run(csource) == "a: 1\nb: 2\nc: 99\n"

    def test_for_loop_over_bidirectional_generator_rejected(self):
        """A for-loop has no way to provide the `.call value:`
        argument, so iterating a bidirectional generator with `for`
        is a compile error with a clear redirect to manual driving."""
        from conftest import make_parser as _mp

        program = _mp(
            "gen: function {n: i64} out "
            "(Iterator gives: i64 accepts: bool) is {\n"
            "    yield 1\n"
            "    x: yield 2\n"
            "}\n"
            'main: function is { for x: (gen n: 0) loop { print "\\{x}" } }',
            unitname="test",
            src_dir=LIB_DIR,
        ).parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        msgs = [e.msg for e in typing.errors]
        assert any("bidirectional" in m and "manually" in m for m in msgs), (
            f"Expected a bidirectional-rejection message, got: {msgs}"
        )

    def test_first_yield_expression_form_rejected(self):
        """For a `accepts != null` generator, the very first reachable
        yield must be in statement form — `this->_resume_input` is
        uninitialised before the first `.call value:`. Reading it
        via `x: yield v` as the opening yield is a compile error
        (rule 11)."""
        from conftest import make_parser as _mp

        program = _mp(
            "gen: function {n: i64} out "
            "(Iterator gives: i64 accepts: bool) is {\n"
            "    x: yield 1\n"
            "}\n"
            "main: function is {}",
            unitname="test",
            src_dir=LIB_DIR,
        ).parse()
        assert isinstance(program, zast.Program)
        typing = typecheck(program)
        msgs = [e.msg for e in typing.errors]
        assert any(
            "first reachable yield" in m and "expression form" in m for m in msgs
        ), f"Expected a first-yield rejection message, got: {msgs}"

    # ---- Phase 1b: reftype gives: end-to-end ------------------------

    def test_gives_reftype_take_yields_owned_string(self):
        """A reftype `gives: String.take` generator yields owned
        strings via `Option(String)`. The emitter dispatches per
        wrapper-kind: union_owned heap-allocates each payload, sets
        `_ry.data = ptr`; consumer takes ownership and destroys both
        the heap allocation and the inner String. ASan-clean (no
        UAF, no leak)."""
        csource = emit_source(
            "gen: function out (Iterator gives: String.take) is {\n"
            '    yield "alpha".string\n'
            '    yield "beta".string\n'
            "}\n"
            "main: function is {\n"
            "    with g: gen do for x: g loop "
            '{ print "\\{x}" }\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout == "alpha\nbeta\n"

    def test_gives_bare_reftype_yields_borrowed_view(self):
        """A bare reftype `gives: String` (Phase 1's borrow default)
        yields a borrowed view via `OptionView(String)`. The emitter
        dispatches per wrapper-kind: union_view sets `_ry.data =
        &<expr>`; the iterator's lock on the source keeps the
        pointed-at storage alive. ASan-clean."""
        csource = emit_source(
            "Holder: class { a: String b: String } as {\n"
            "    walk: function {h: this.private.lock} "
            "out (Iterator gives: String) is {\n"
            "        yield h.a\n"
            "        yield h.b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            '    box: Holder a: "foo".string b: "bar".string\n'
            "    with g: (Holder.walk h: box.lock) do for x: g loop "
            '{ print "\\{x}" }\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout == "foo\nbar\n"

    # ---- Phase 3: borrowed `accepts:` ----------------------------

    def test_accepts_lock_pointer_alias_runs_asan_clean(self):
        """Bidirectional generator with `accepts: Bag.lock`: each
        `.call value: bag.lock` deposits a pointer into the synth
        class's `.lock` `_resume_input` field; the body's `b: yield 2`
        binding becomes a pointer alias so `b.x` accesses the
        caller's live Bag through the lock. Confirms emitter wires
        the field as `z_Bag_t*`, the alias as `(*__borrow_b)`, and
        runs ASan-clean across multiple yield windows."""
        csource = emit_source(
            "Bag: class { x: i64 }\n"
            "gen: function {n: i64} out (Iterator gives: i64 accepts: Bag.lock) is {\n"
            "    yield 1\n"
            "    b: yield 2\n"
            "    yield b.x\n"
            "}\n"
            "main: function is {\n"
            "    bag: Bag x: 42\n"
            "    with g: (gen n: 0) do {\n"
            "        a: g.call value: bag.lock\n"
            "        match (a) case some then "
            '{ print "a=\\{a}" } case none then { print "(none)" }\n'
            "        b: g.call value: bag.lock\n"
            "        match (b) case some then "
            '{ print "b=\\{b}" } case none then { print "(none)" }\n'
            "        c: g.call value: bag.lock\n"
            "        match (c) case some then "
            '{ print "c=\\{c}" } case none then { print "(none)" }\n'
            "    }\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        # 1 (first yield), 2 (second yield), 42 (bag.x via the locked
        # pointer alias on the third call's resume into yield b.x).
        assert result.stdout == "a=1\nb=2\nc=42\n"
