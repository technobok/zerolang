"""
Tests for the C code emitter (zemitterc)
"""

import os
import subprocess
import tempfile

from conftest import make_parser_vfs
from zparser import Parser
from ztypecheck import typecheck
import zemitterc
import zast


SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


def emit_source(source: str, unitname: str = "test") -> str:
    """Parse, type-check, and emit C source for a zerolang program."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=SRC_DIR)
    p = Parser(vfs, name)
    program = p.parse()
    assert isinstance(program, zast.Program), f"Parse failed: {program!r}"
    errors = typecheck(program)
    assert errors == [], f"Type errors: {[e.msg for e in errors]}"
    return zemitterc.emit(program)


def compile_and_run(csource: str) -> str:
    """Compile C source with gcc and run, returning stdout."""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        result = subprocess.run(
            ["gcc", "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
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


class TestEmitterBasic:
    def test_hello_world(self):
        csource = emit_source('main: function is { print "Hello, World!" }')
        assert "zstr_print" in csource
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
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, name)
        program = p.parse()
        assert isinstance(program, zast.Program), f"Parse failed for {name}"
        errors = typecheck(program)
        assert errors == [], f"Type errors for {name}: {[e.msg for e in errors]}"
        return zemitterc.emit(program)

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


def compile_and_run_asan(csource: str) -> subprocess.CompletedProcess:
    """Compile C source with ASan and run, returning the CompletedProcess."""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(csource)
        cpath = f.name
    outpath = cpath.replace(".c", "")
    try:
        comp = subprocess.run(
            [
                "gcc",
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
    """Tests for string ownership semantics in emitted C code."""

    def test_string_scope_cleanup(self):
        """String variables freed at function exit."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        assert "if (s) free(s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_string_return(self):
        """Function returning a string; returned value usable, not double-freed."""
        csource = emit_source(
            "greet: function {n: i64} out string is {\n"
            '  return "Hello \\{n}!"\n'
            "}\n"
            "main: function is {\n"
            "  msg: greet 42\n"
            "  print msg\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "Hello 42!"

    def test_string_reassignment(self):
        """Old value freed, new value assigned correctly."""
        csource = emit_source(
            'main: function is {\n  s: "hello"\n  s = "world"\n  print s\n}'
        )
        assert "free(s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "world"

    def test_string_swap(self):
        """Two strings swapped, both usable after swap."""
        csource = emit_source(
            "main: function is {\n"
            '  a: "first"\n'
            '  b: "second"\n'
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
        """Interpolation intermediates freed."""
        csource = emit_source(
            'main: function is {\n  name: "Zero"\n  print "Hello, \\{name}!"\n}'
        )
        # verify temps are freed (free(_t...) calls)
        assert "free(_t" in csource
        output = compile_and_run(csource)
        assert output.strip() == "Hello, Zero!"

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
        """Scoped string variable freed at end of with block."""
        csource = emit_source('main: function is {\n  with s: "hello" do print s\n}')
        assert "free(s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hello"


class TestEmitterMemorySafety:
    """Memory safety tests using AddressSanitizer."""

    def test_string_no_leak(self):
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "hello"

    def test_string_return_no_leak(self):
        csource = emit_source(
            "greet: function {n: i64} out string is {\n"
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
            'main: function is {\n  s: "hello"\n  s = "world"\n  print s\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "world"

    def test_string_swap_no_double_free(self):
        csource = emit_source(
            "main: function is {\n"
            '  a: "first"\n'
            '  b: "second"\n'
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
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "hello")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == []
        csource = zemitterc.emit(program)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "Hello, World!" in result.stdout

    def test_example_strings_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "strings")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == []
        csource = zemitterc.emit(program)
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
        """Call-expression args should be hoisted to temps in emitted C."""
        csource = emit_source(
            "inc: function {n: i64} out i64 is { return n + 1 }\n"
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "\n"
            "main: function is {\n"
            "    result: add a: (inc n: 1) b: (inc n: 2)\n"
            '    print "\\{result}"\n'
            "}"
        )
        # The emitted code should contain arg temps (_a) for the call args
        assert "int64_t _a" in csource
        output = compile_and_run(csource)
        assert output.strip() == "5"

    def test_pure_args_not_temped(self):
        """Pure arguments (variables, literals) should NOT generate arg temps."""
        csource = emit_source(
            "add: function {a: i64 b: i64} out i64 is { return a + b }\n"
            "\n"
            "main: function is {\n"
            "    x: 10\n"
            "    result: add a: x b: 20\n"
            '    print "\\{result}"\n'
            "}"
        )
        # No arg temps needed for pure args
        assert "int64_t _a" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "30"


# ---- Phase 4f: Class Emitter Tests ----


class TestEmitterClasses:
    """Tests for class C emission."""

    def test_class_struct_emitted(self):
        """Class should emit a typedef struct."""
        csource = emit_source(
            "myclass: class { x: i64\n y: f64 }\nmain: function is { c: myclass }"
        )
        assert "typedef struct {" in csource
        assert "z_myclass_t" in csource
        assert "int64_t x;" in csource
        assert "double y;" in csource

    def test_class_construction_emits_malloc(self):
        """Class construction should emit malloc."""
        csource = emit_source(
            "myclass: class { x: i64 }\nmain: function is { c: myclass x: 5 }"
        )
        assert "malloc(sizeof(z_myclass_t))" in csource

    def test_class_field_access_uses_arrow(self):
        """Class field access should use -> operator."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            'main: function is { c: myclass x: 5\n print "\\{c.x}" }'
        )
        assert "c->x" in csource

    def test_class_scope_cleanup(self):
        """Class variables freed at function exit."""
        csource = emit_source(
            "myclass: class { x: i64 }\nmain: function is { c: myclass }"
        )
        assert "if (c) free(c);" in csource

    def test_class_take_nullifies(self):
        """After .take, source variable should be nullified."""
        csource = emit_source(
            "myclass: class { x: i64 }\nmain: function is { c: myclass\n d: c.take }"
        )
        assert "= NULL;" in csource

    def test_class_method_uses_pointer(self):
        """Class methods should take pointer parameter."""
        csource = emit_source(
            "myclass: class { x: i64 } as {\n"
            "  get: function {c: this} out i64 is { return c.x }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        assert "z_myclass_t*" in csource
        assert "c->x" in csource

    def test_class_swap_emits(self):
        """Swap of class pointers should emit correctly."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass x: 1\n"
            "  b: myclass x: 2\n"
            "  a swap b\n"
            "}"
        )
        assert "z_myclass_t* _tmp" in csource


class TestEmitterClassIntegration:
    """Integration tests: compile and run class programs."""

    def test_class_construction_and_field(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            'main: function is { c: myclass x: 42\n print "\\{c.x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_class_method_call(self):
        csource = emit_source(
            "counter: class { value: i64 } as {\n"
            "  get: function {c: this} out i64 is { return c.value }\n"
            "}\n"
            'main: function is { c: counter value: 7\n print "\\{counter.get c}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "7"

    def test_class_method_mutation(self):
        csource = emit_source(
            "counter: class { value: i64 } as {\n"
            "  inc: function {c: this} is { c.value = c.value + 1 }\n"
            "  get: function {c: this} out i64 is { return c.value }\n"
            "}\n"
            "main: function is {\n"
            "  c: counter value: 0\n"
            "  counter.inc c\n"
            "  counter.inc c\n"
            '  print "\\{counter.get c}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2"

    def test_class_function_return(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "make: function {v: i64} out myclass is { return myclass x: v }\n"
            'main: function is { c: make 99\n print "\\{c.x}" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "99"

    def test_class_swap_runtime(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass x: 1\n"
            "  b: myclass x: 2\n"
            "  a swap b\n"
            '  print "\\{a.x} \\{b.x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "2 1"

    def test_class_take_runtime(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass x: 42\n"
            "  b: a.take\n"
            '  print "\\{b.x}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_example_classes(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "classes")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        csource = zemitterc.emit(program)
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
            "myclass: class { x: i64 }\n"
            'main: function is { c: myclass x: 42\n print "\\{c.x}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_swap_no_leak(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass x: 1\n"
            "  b: myclass x: 2\n"
            "  a swap b\n"
            '  print "\\{a.x}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_take_no_double_free(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  a: myclass x: 42\n"
            "  b: a.take\n"
            '  print "\\{b.x}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_function_return_no_leak(self):
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "make: function {v: i64} out myclass is { return myclass x: v }\n"
            'main: function is { c: make 99\n print "\\{c.x}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_example_classes_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "classes")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == []
        csource = zemitterc.emit(program)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "initial = 10" in result.stdout


# ---- Phase 4h: Union Emitter Tests ----


class TestEmitterUnions:
    """Tests for union C emission."""

    def test_union_struct_emitted(self):
        """Union should emit tag enum + struct."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        assert "Z_MYUNION_TAG_A" in csource
        assert "Z_MYUNION_TAG_B" in csource
        assert "z_myunion_tag_t" in csource
        assert "z_myunion_t" in csource
        assert "void* data;" in csource

    def test_union_construction_emits_malloc(self):
        """Union construction should emit malloc + tag + data."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is { x: myunion.a 42 }"
        )
        assert "malloc(sizeof(z_myunion_t))" in csource
        assert "Z_MYUNION_TAG_A" in csource

    def test_union_null_construction(self):
        """Null subtype construction emits tag + NULL data."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is { x: myunion.b }"
        )
        assert "Z_MYUNION_TAG_B" in csource
        assert "->data = NULL" in csource

    def test_union_match_emits_switch(self):
        """Match on union emits switch on tag."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "a"\n'
            "  } case b then {\n"
            '    print "b"\n'
            "  }\n"
            "}"
        )
        assert "switch (x->tag)" in csource
        assert "case Z_MYUNION_TAG_A:" in csource
        assert "case Z_MYUNION_TAG_B:" in csource

    def test_union_scope_cleanup(self):
        """Union variables destroyed at function exit."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        assert "z_myunion_destroy(x);" in csource

    def test_union_take_nullifies(self):
        """After .take, source variable should be nullified."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is { x: myunion.a 1\n y: x.take }"
        )
        assert "= NULL;" in csource

    def test_union_destructor_emitted(self):
        """Union destructor should be generated."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is { x: myunion.a 1 }"
        )
        assert "z_myunion_destroy" in csource
        assert "switch (u->tag)" in csource
        assert "free(u);" in csource


class TestEmitterUnionIntegration:
    """Integration tests: compile and run union programs."""

    def test_union_basic(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            'main: function is { x: myunion.a 42\n print "ok" }'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_union_match(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
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
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.b\n"
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
            "myunion: union { a: i64\n b: string\n c: null }\n"
            'main: function is {\n'
            '  x: myunion.b "hello"\n'
            '  print "ok"\n'
            '}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_union_take(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 42\n"
            "  y: x.take\n"
            '  print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_union_swap(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  y: myunion.b\n"
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
            "result: union { ok: i64\n err: string\n none: null }\n"
            "describe: function {r: result} out string is {\n"
            "  match (\n"
            "    r\n"
            "  ) case ok then {\n"
            '    return "ok"\n'
            "  } case err then {\n"
            '    return "error"\n'
            "  } case none then {\n"
            '    return "none"\n'
            "  }\n"
            "}\n"
            "main: function is {\n"
            "  a: result.ok 42\n"
            '  print "a is \\{describe a}"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "a is ok"

    def test_example_unions(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "unions")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        csource = zemitterc.emit(program)
        output = compile_and_run(csource)
        assert "a is ok" in output
        assert "b is error" in output
        assert "c is none" in output
        assert "d is ok" in output


class TestEmitterUnionMemorySafety:
    """Memory safety tests for unions using ASan."""

    def test_union_no_leak(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            'main: function is { x: myunion.a 42\n print "ok" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_string_no_leak(self):
        csource = emit_source(
            'myunion: union { a: string\n b: null }\n'
            'main: function is { x: myunion.a "hello"\n print "ok" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_take_no_double_free(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 42\n"
            "  y: x.take\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_union_swap_no_leak(self):
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 1\n"
            "  y: myunion.b\n"
            "  x swap y\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_example_unions_asan(self):
        from zvfs import ZVfs, FSProvider, BindType

        vfs = ZVfs()
        systemdir = os.path.join(SRC_DIR, "system")
        psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
        pmainid = vfs.register(FSProvider(rootpath=EXAMPLES_DIR, parentpath=""))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
        )
        p = Parser(vfs, "unions")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == []
        csource = zemitterc.emit(program)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "a is ok" in result.stdout
