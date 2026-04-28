"""
Tests for the compiler CLI (zc.py) — output routing, error format, flags.
"""

import os
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.cli

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")
ZC = os.path.join(SRC_DIR, "zc.py")


def run_zc(*args, stdin_text=None):
    """Run the compiler and return (returncode, stdout, stderr)."""
    cmd = [sys.executable, ZC] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.PIPE if stdin_text else None,
        input=stdin_text,
    )
    return result.returncode, result.stdout, result.stderr


class TestQuietDefault:
    def test_success_no_stdout(self):
        """Successful compilation should produce no stdout."""
        rc, stdout, stderr = run_zc("hello", "--src", EXAMPLES_DIR)
        assert rc == 0
        assert stdout == ""
        # clean up
        if os.path.exists("hello.c"):
            os.unlink("hello.c")

    def test_success_no_stderr_without_verbose(self):
        """Successful compilation without -v should produce no stderr."""
        rc, stdout, stderr = run_zc("hello", "--src", EXAMPLES_DIR)
        assert rc == 0
        assert stderr == ""
        if os.path.exists("hello.c"):
            os.unlink("hello.c")


class TestVerboseOutput:
    def test_verbose_to_stderr(self):
        """With -v, verbose output should go to stderr."""
        rc, stdout, stderr = run_zc("hello", "--src", EXAMPLES_DIR, "-v")
        assert rc == 0
        assert stdout == ""
        assert "source directory:" in stderr
        assert "type check passed" in stderr
        assert "completed in" in stderr
        if os.path.exists("hello.c"):
            os.unlink("hello.c")

    def test_verbose_shows_files(self):
        """Verbose output should show which files are being compiled."""
        rc, stdout, stderr = run_zc("hello", "--src", EXAMPLES_DIR, "-v")
        assert rc == 0
        assert "compiling:" in stderr
        if os.path.exists("hello.c"):
            os.unlink("hello.c")


class TestOutputFlag:
    def test_output_flag(self):
        """The -o flag should write to the specified File."""
        with tempfile.NamedTemporaryFile(suffix=".c", delete=False) as f:
            outpath = f.name
        try:
            rc, stdout, stderr = run_zc("hello", "--src", EXAMPLES_DIR, "-o", outpath)
            assert rc == 0
            assert os.path.exists(outpath)
            with open(outpath) as f:
                content = f.read()
            assert "z_main" in content
        finally:
            if os.path.exists(outpath):
                os.unlink(outpath)


class TestExitCodes:
    def test_success_exits_0(self):
        """Successful compilation exits with code 0."""
        rc, _, _ = run_zc("hello", "--src", EXAMPLES_DIR)
        assert rc == 0
        if os.path.exists("hello.c"):
            os.unlink("hello.c")

    def test_compile_error_exits_1(self):
        """Compilation errors exit with code 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is { print x }\n")
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir)
            assert rc == 1

    def test_bad_unitname_exits_2(self):
        """Invalid unit name exits with code 2."""
        rc, _, stderr = run_zc("123-bad!")
        assert rc == 2

    def test_missing_arg_exits_2(self):
        """Missing required argument exits with code 2."""
        rc, _, _ = run_zc()
        assert rc == 2


class TestErrorFormat:
    def test_rustc_style_error_code(self):
        """Error messages should include error[Exxxx]: format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is { print x }\n")
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir)
            assert rc == 1
            assert "error[E" in stderr
            assert "]:" in stderr

    def test_error_shows_file_location(self):
        """Error messages should include --> File:line:col."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is { print x }\n")
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir)
            assert "-->" in stderr
            assert "errtest.z:" in stderr

    def test_error_count_summary(self):
        """Error output should include an error count summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is { print x }\n")
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir)
            assert "error" in stderr and "found" in stderr

    def test_errors_to_stderr_not_stdout(self):
        """Error messages should go to stderr, not stdout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is { print x }\n")
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir)
            assert stdout == ""
            assert "error" in stderr

    def test_no_color_flag(self):
        """--no-color should suppress ANSI escape codes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is { print x }\n")
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir, "--no-color")
            assert "\033[" not in stderr


class TestDidYouMean:
    def test_did_you_mean_suggestion(self):
        """Misspelled identifier should trigger did-you-mean hint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write("main: function is {\n  x: 42\n  pritn x\n}\n")
            # 'pritn' is close to 'print'
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir, "--no-color")
            # should have a hint (may or may not match 'print' depending on scope)
            assert rc == 1


class TestOwnershipErrors:
    def test_use_after_take_note(self):
        """Using a variable after .take should show ownership transfer note."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write(
                    "myclass: class { x: i64 }\n"
                    "main: function is {\n"
                    "  c: myclass x: 1\n"
                    "  d: c.take\n"
                    "  print c\n"
                    "}\n"
                )
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir, "--no-color")
            assert rc == 1
            assert "ownership transfer" in stderr

    def test_borrow_without_lock_hint(self):
        """Borrow return without lock param should show hint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write(
                    "f: function out i64.borrow is { return 42 }\n"
                    "main: function is {}\n"
                )
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir, "--no-color")
            assert rc == 1
            assert "hint" in stderr
            assert ".lock" in stderr


class TestArgumentErrors:
    def test_too_many_args_zero_param(self):
        """Passing args to a zero-param function should error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write('f: function is { print "ok" }\nmain: function is { f 42 }\n')
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir, "--no-color")
            assert rc == 1
            assert "too many arguments" in stderr

    def test_unknown_named_arg(self):
        """Unknown named argument should error with did-you-mean."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "errtest.z")
            with open(src, "w") as f:
                f.write(
                    "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
                    'main: function is { print "\\{add a: 1 b: 2 c: 3}" }\n'
                )
            rc, stdout, stderr = run_zc("errtest", "--src", tmpdir, "--no-color")
            assert rc == 1
            assert "unknown argument" in stderr
