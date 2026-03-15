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


LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "lib")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


def emit_source(source: str, unitname: str = "test") -> str:
    """Parse, type-check, and emit C source for a zerolang program."""
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=LIB_DIR)
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
        systemdir = os.path.join(LIB_DIR, "system")
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
        """String variables freed at function exit via zstr_free."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        assert "zstr_free(s);" in csource
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
        """Old value freed via zstr_free, new value assigned correctly."""
        csource = emit_source(
            'main: function is {\n  s: "hello"\n  s = "world"\n  print s\n}'
        )
        assert "zstr_free(s);" in csource
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
        """Interpolation intermediates freed via zstr_free."""
        csource = emit_source(
            'main: function is {\n  name: "Zero"\n  print "Hello, \\{name}!"\n}'
        )
        # verify temps are freed (zstr_free(_t...) calls for zstr_cat results)
        assert "zstr_free(_t" in csource
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
        """Scoped string variable freed at end of with block via zstr_free."""
        csource = emit_source('main: function is {\n  with s: "hello" do print s\n}')
        assert "zstr_free(s);" in csource
        output = compile_and_run(csource)
        assert output.strip() == "hello"


class TestEmitterStaticStrings:
    """Tests for ZSTR_STATIC string literal emission."""

    def test_literal_uses_static(self):
        """Plain string literal should emit ZSTR_STATIC, not zstr_new."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        assert "ZSTR_STATIC(_zs" in csource
        assert 'zstr_new("hello")' not in csource

    def test_static_deduplication(self):
        """Same literal used twice should produce one ZSTR_STATIC."""
        csource = emit_source(
            'main: function is {\n  a: "hello"\n  b: "hello"\n  print a\n  print b\n}'
        )
        assert csource.count('ZSTR_STATIC(_zs1, "hello")') == 1
        # only one ZSTR_STATIC for "hello"
        assert (
            csource.count("_zs2") == 0
            or '"hello"' not in csource.split("_zs2")[1].split("\n")[0]
        )

    def test_interp_fragments_use_static(self):
        """Literal fragments in interpolation should use ZSTR_STATIC."""
        csource = emit_source(
            'main: function is {\n  name: "Zero"\n  print "Hello, \\{name}!"\n}'
        )
        assert "ZSTR_STATIC(" in csource
        # "Hello, " and "!" fragments should be static
        assert 'zstr_new("Hello, ")' not in csource
        assert 'zstr_new("!")' not in csource

    def test_static_string_var_no_temp(self):
        """Static literal assigned to var should not create a temp."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        # Should directly assign: ZStr* s = _zs1;
        assert "ZStr* s = _zs" in csource
        # No temp allocation
        assert "ZStr* _t" not in csource or "_t1 = zstr_new" not in csource

    def test_static_string_passed_to_function(self):
        """Static string can be passed to and returned from functions."""
        csource = emit_source(
            "greet: function {n: i64} out string is {\n"
            '  return "hello"\n'
            "}\n"
            "main: function is {\n"
            "  msg: greet 1\n"
            "  print msg\n"
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"

    def test_static_string_asan(self):
        """Static strings should pass ASan (no leaks, no invalid frees)."""
        csource = emit_source(
            'main: function is {\n  s: "hello"\n  s = "world"\n  print s\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "world"

    def test_static_empty_string(self):
        """Empty string literal should use ZSTR_STATIC."""
        csource = emit_source('main: function is {\n  s: ""\n  print s\n}')
        assert "ZSTR_STATIC(" in csource
        assert 'zstr_new("")' not in csource

    def test_zstr_free_in_scope_cleanup(self):
        """Scope cleanup for string vars should use zstr_free."""
        csource = emit_source('main: function is {\n  s: "hello"\n  print s\n}')
        assert "zstr_free(s);" in csource
        assert "if (s) free(s);" not in csource

    def test_uint64_size_field(self):
        """ZStr struct should use uint64_t size field."""
        csource = emit_source('main: function is { print "hello" }')
        assert "uint64_t size;" in csource
        assert "int32_t len;" not in csource


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
        systemdir = os.path.join(LIB_DIR, "system")
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
        systemdir = os.path.join(LIB_DIR, "system")
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

    def test_class_construction_calls_create(self):
        """Class construction should call create instead of inline malloc."""
        csource = emit_source(
            "myclass: class { x: i64 }\nmain: function is { c: myclass x: 5 }"
        )
        assert "z_myclass_create(5)" in csource

    def test_class_field_access_uses_arrow(self):
        """Class field access should use -> operator."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            'main: function is { c: myclass x: 5\n print "\\{c.x}" }'
        )
        assert "c->x" in csource

    def test_class_scope_cleanup(self):
        """Class variables destroyed at function exit."""
        csource = emit_source(
            "myclass: class { x: i64 }\nmain: function is { c: myclass }"
        )
        assert "z_myclass_destroy(c);" in csource

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
        systemdir = os.path.join(LIB_DIR, "system")
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
        systemdir = os.path.join(LIB_DIR, "system")
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


# ---- Phase 4h.1: Class Destructor Tests ----


class TestEmitterClassDestructors:
    """Tests for class destructor generation and usage."""

    def test_class_destructor_with_string_field(self):
        """Class with string field: destructor frees string."""
        csource = emit_source(
            "myclass: class { name: string\n x: i64 }\nmain: function is { c: myclass }"
        )
        assert "z_myclass_destroy" in csource
        assert "zstr_free(p->name);" in csource

    def test_class_destructor_with_class_field(self):
        """Class with class field: destructor recurses."""
        csource = emit_source(
            "inner: class { x: i64 }\n"
            "outer: class { child: inner }\n"
            "main: function is { o: outer }"
        )
        assert "z_inner_destroy(p->child);" in csource

    def test_class_destructor_with_union_field(self):
        """Class with union field: destructor calls union destroy."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "myclass: class { data: myunion }\n"
            "main: function is { c: myclass }"
        )
        assert "z_myunion_destroy(p->data);" in csource

    def test_class_destructor_valtype_only(self):
        """Class with only valtype fields: just NULL check + free."""
        csource = emit_source(
            "myclass: class { x: i64\n y: f64 }\nmain: function is { c: myclass }"
        )
        assert "z_myclass_destroy" in csource
        # destructor should NOT contain zstr_free or z_*_destroy for fields
        # find the destructor body
        idx = csource.index("z_myclass_destroy(z_myclass_t* p)")
        body = csource[idx : csource.index("}\n", idx) + 2]
        assert "zstr_free" not in body
        assert "z_" not in body.replace("z_myclass_destroy", "").replace(
            "z_myclass_t", ""
        )

    def test_scope_exit_calls_destructor(self):
        """Scope-exit cleanup calls z_{name}_destroy."""
        csource = emit_source(
            "myclass: class { name: string }\nmain: function is { c: myclass }"
        )
        # should call destructor, not bare free
        assert "z_myclass_destroy(c);" in csource
        assert "if (c) free(c);" not in csource

    def test_reassignment_calls_destructor(self):
        """Reassignment calls destructor on old value."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: myclass x: 1\n"
            "  c = myclass x: 2\n"
            "}"
        )
        assert "z_myclass_destroy(c);" in csource

    def test_with_block_calls_destructor(self):
        """With-block scope exit calls destructor."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            'main: function is { with c: myclass x: 1 do print "ok" }'
        )
        assert "z_myclass_destroy(c);" in csource

    def test_union_class_subtype_destructor(self):
        """Union with class subtype calls class destructor in union destructor."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "myunion: union { a: myclass\n b: null }\n"
            "main: function is { u: myunion.b }"
        )
        assert "z_myclass_destroy((z_myclass_t*)u->data);" in csource


class TestEmitterClassDestructorIntegration:
    """Integration tests for class destructors using ASan."""

    def test_class_string_field_asan(self):
        """Class with string field: no leak under ASan."""
        csource = emit_source(
            "myclass: class { name: string\n x: i64 }\n"
            'main: function is {\n  c: myclass name: "hello" x: 42\n  print "\\{c.name}"\n}'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "hello"

    def test_class_nested_class_field_asan(self):
        """Nested class field: no leak under ASan."""
        csource = emit_source(
            "inner: class { x: i64 }\n"
            "outer: class { child: inner\n y: i64 }\n"
            "main: function is {\n"
            "  i: inner x: 10\n"
            "  o: outer child: i.take y: 20\n"
            '  print "\\{o.child.x} \\{o.y}"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert result.stdout.strip() == "10 20"

    def test_class_union_field_asan(self):
        """Class with union field: no leak under ASan."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "myclass: class { data: myunion\n x: i64 }\n"
            "main: function is {\n"
            "  u: myunion.a 42\n"
            "  c: myclass data: u.take x: 1\n"
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
        p = Parser(vfs, "classes")
        program = p.parse()
        assert isinstance(program, zast.Program)
        errors = typecheck(program)
        assert errors == [], f"Type errors: {[e.msg for e in errors]}"
        csource = zemitterc.emit(program)
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "named: test=42" in result.stdout


# ---- Phase 4h: Union Emitter Tests ----


class TestEmitterUnions:
    """Tests for union C emission."""

    def test_union_struct_emitted(self):
        """Union should emit tag enum + struct."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        assert "Z_MYUNION_TAG_A" in csource
        assert "Z_MYUNION_TAG_B" in csource
        assert "z_myunion_tag_t" in csource
        assert "z_myunion_t" in csource
        assert "void* data;" in csource

    def test_union_construction_emits_malloc(self):
        """Union construction should emit malloc + tag + data."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 42 }"
        )
        assert "malloc(sizeof(z_myunion_t))" in csource
        assert "Z_MYUNION_TAG_A" in csource

    def test_union_null_construction(self):
        """Null subtype construction emits tag + NULL data."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.b }"
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
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
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
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        assert "z_myunion_destroy" in csource
        assert "switch (u->tag)" in csource
        assert "free(u);" in csource


class TestEmitterUnionCustomTag:
    """Tests for union custom tag C emission (Phase 18)."""

    def test_custom_tag_values_emitted(self):
        """Custom data tag values should appear as explicit enum values."""
        csource = emit_source(
            "pv: data { A: 10 B: 20 }\n"
            "myunion: union { A: null\n B: null } as { tag: pv.tag }\n"
            "main: function is { x: myunion.A }"
        )
        assert "Z_MYUNION_TAG_A = 10" in csource
        assert "Z_MYUNION_TAG_B = 20" in csource

    def test_custom_tag_sparse_values_emitted(self):
        """Sparse custom tag values should be emitted correctly."""
        csource = emit_source(
            "pv: data { LOW: 0 MEDIUM: 1 HIGH: 2 CRITICAL: 10 }\n"
            "priority: union {\n"
            "    LOW: null\n"
            "    MEDIUM: null\n"
            "    HIGH: null\n"
            "    CRITICAL: null\n"
            "} as {\n"
            "    tag: pv.tag\n"
            "}\n"
            "main: function is { x: priority.LOW }"
        )
        assert "Z_PRIORITY_TAG_LOW = 0" in csource
        assert "Z_PRIORITY_TAG_CRITICAL = 10" in csource

    def test_default_tag_uses_sequential_values(self):
        """Without custom tag, enum values should be sequential (auto-increment)."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\nmain: function is { x: myunion.a 1 }"
        )
        # sequential values don't need explicit = N, just comma separation
        assert "Z_MYUNION_TAG_A," in csource
        assert "Z_MYUNION_TAG_B," in csource
        assert "= " not in csource.split("typedef enum")[1].split("}")[0]


    def test_numeric_tag_compiles(self):
        """Union with u16.tag compiles correctly."""
        csource = emit_source(
            "myunion: union { A: null\n B: null } as { tag: u16.tag }\n"
            "main: function is { x: myunion.A }"
        )
        assert "z_myunion_tag_t" in csource

    def test_data_tag_runtime(self):
        """Custom data tag still works at runtime after generic tag change."""
        csource = emit_source(
            "pv: data { A: 10 B: 20 }\n"
            "myunion: union { A: i64\n B: null } as { tag: pv.tag }\n"
            "main: function is {\n"
            "  x: myunion.A 42\n"
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
            "main: function is {\n"
            '  x: myunion.b "hello"\n'
            '  print "ok"\n'
            "}"
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
        systemdir = os.path.join(LIB_DIR, "system")
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
            "myunion: union { a: string\n b: null }\n"
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
        systemdir = os.path.join(LIB_DIR, "system")
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


class TestStandaloneTake:
    """Tests for standalone .take (as expression statement, not in assignment)."""

    def test_standalone_take_class(self):
        """x.take as statement on class → destroy + NULL."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "main: function is {\n"
            "  c: myclass x: 42\n"
            "  c.take\n"
            "}"
        )
        assert "z_myclass_destroy(c);" in csource
        assert "c = NULL;" in csource

    def test_standalone_take_union(self):
        """x.take as statement on union → destroy + NULL."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myunion.a 42\n"
            "  x.take\n"
            "}"
        )
        assert "z_myunion_destroy(x);" in csource
        assert "x = NULL;" in csource

    def test_standalone_take_string(self):
        """s.take as statement on string → free + NULL."""
        csource = emit_source('main: function is {\n  s: "hello"\n  s.take\n}')
        assert "zstr_free(s);" in csource
        assert "s = NULL;" in csource

    def test_standalone_take_class_asan(self):
        """Standalone .take on class with string field → no leak, no double-free."""
        csource = emit_source(
            "myclass: class { name: string }\n"
            "main: function is {\n"
            '  c: myclass name: "hello"\n'
            "  c.take\n"
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
            "myclass: class { x: i64 }\n"
            'consume: function {p: myclass.take} is { print "consumed" }\n'
            "main: function is {\n"
            "  c: myclass x: 42\n"
            "  consume c\n"
            "}"
        )
        # after the call, c should be nullified
        lines = csource.split("\n")
        found_call = False
        found_null = False
        for line in lines:
            if "z_consume(c)" in line:
                found_call = True
            elif found_call and "c = NULL;" in line:
                found_null = True
                break
        assert found_call, "Expected call to z_consume"
        assert found_null, "Expected c = NULL after implicit take call"

    def test_implicit_take_no_double_null(self):
        """Explicit .take with implicit take param → only one NULL."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            'consume: function {p: myclass.take} is { print "consumed" }\n'
            "main: function is {\n"
            "  c: myclass x: 42\n"
            "  consume c.take\n"
            "}"
        )
        # should have exactly one c = NULL (from explicit .take, not doubled)
        assert csource.count("c = NULL;") == 1

    def test_implicit_take_asan(self):
        """Implicit take with class → no double-free."""
        csource = emit_source(
            "myclass: class { x: i64 }\n"
            "consume: function {p: myclass.take} is { p.take }\n"
            "main: function is {\n"
            "  c: myclass x: 42\n"
            "  consume c\n"
            '  print "ok"\n'
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"
        assert "ok" in result.stdout


class TestReturnPathTake:
    """Tests for .take in return-path class construction."""

    def test_return_class_construction_take(self):
        """Return with class construction using .take → source nullified."""
        csource = emit_source(
            "myclass: class { name: string }\n"
            "wrap: function {s: string} out myclass is {\n"
            "  return myclass name: s.take\n"
            "}\n"
            "main: function is {\n"
            '  c: wrap "hello"\n'
            '  print "ok"\n'
            "}"
        )
        assert "s = NULL;" in csource


# ---- Phase 4h.2: Constructor Infrastructure (meta.create) ----


class TestEmitterConstructors:
    """Tests for compiler-generated meta.create constructors."""

    def test_class_meta_create_emitted(self):
        """Class should emit both meta.create and create functions."""
        csource = emit_source(
            "counter: class { value: i64 }\nmain: function is { c: counter }"
        )
        assert "z_counter_meta_create" in csource
        assert "z_counter_create" in csource
        assert "z_counter_t* _this" in csource
        assert "malloc(sizeof(z_counter_t))" in csource
        assert "_this->value = value;" in csource

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
            "counter: class { value: i64 }\n"
            'main: function is { c: counter value: 10\n print "\\{c.value}" }'
        )
        assert "z_counter_create(10)" in csource

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
            "myclass: class { x: i64 }\n"
            "make: function {v: i64} out myclass is { return myclass x: v }\n"
            "main: function is { c: make 99 }"
        )
        assert "z_myclass_create(v)" in csource

    def test_bare_class_calls_create(self):
        """Bare class name should call .create with zeros."""
        csource = emit_source(
            "myclass: class { x: i64 }\nmain: function is { c: myclass }"
        )
        assert "z_myclass_create(0)" in csource

    def test_bare_record_calls_create(self):
        """Bare record name should call .create with zeros."""
        csource = emit_source(
            "point: record { x: i64\n y: i64 }\nmain: function is { p: point }"
        )
        assert "z_point_create(0, 0)" in csource

    def test_out_this_return_type(self):
        """Method with 'out this' return type should resolve correctly."""
        csource = emit_source(
            "myclass: class { x: i64 } as {\n"
            "  make: function {v: i64} out this is { return myclass x: v }\n"
            "}\n"
            "main: function is { c: myclass }"
        )
        assert "z_myclass_t* z_myclass_make" in csource


class TestEmitterConstructorIntegration:
    """Integration tests: compile and run constructor programs."""

    def test_class_meta_create_runtime(self):
        """Class meta.create: construct and access field."""
        csource = emit_source(
            "counter: class { value: i64 }\n"
            'main: function is { c: counter value: 42\n print "\\{c.value}" }'
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
        """Class with string field: take semantics work via meta.create."""
        csource = emit_source(
            "myclass: class { name: string }\n"
            'main: function is {\n  c: myclass name: "hello"\n  print "\\{c.name}"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "hello"


class TestEmitterConstructorMemorySafety:
    """Memory safety tests for constructors using ASan."""

    def test_class_meta_create_no_leak(self):
        """Class meta.create: no leak under ASan."""
        csource = emit_source(
            "counter: class { value: i64 }\n"
            'main: function is { c: counter value: 42\n print "\\{c.value}" }'
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan error:\n{result.stderr}"

    def test_class_string_field_no_leak(self):
        """Class with string field: no leak under ASan."""
        csource = emit_source(
            "myclass: class { name: string }\n"
            'main: function is {\n  c: myclass name: "hello"\n  print "\\{c.name}"\n}'
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
            "myunion: union { :u8\n :u16\n :u32 }\n"
            "main: function is { x: myunion.u8 42 }"
        )
        assert "Z_MYUNION_TAG_U8" in csource
        assert "Z_MYUNION_TAG_U16" in csource
        assert "Z_MYUNION_TAG_U32" in csource
        assert "z_myunion_t" in csource

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
            "myunion: union { :u8\n :string }\n"
            'main: function is { x: myunion.u8 42\n print "ok" }'
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
        """Inline unit constant accessible via dotted path."""
        csource = emit_source(
            'm: unit { X: 42 }\nmain: function is { print "\\{m.X}" }'
        )
        assert "z_m_X" in csource
        output = compile_and_run(csource)
        assert output.strip() == "42"

    def test_nested_inline_unit_function(self):
        """Nested inline unit function emits with full path mangling."""
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
        # there should be no malloc for the variant construction
        # (malloc may exist for string infrastructure but not for variant)
        lines = csource.split("\n")
        variant_lines = [ln for ln in lines if "myvar" in ln.lower() or "_c1" in ln]
        assert not any("malloc" in ln for ln in variant_lines)

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
        """Compile and run: access payload inside match case."""
        csource = emit_source(
            "myvar: variant { a: i64\n b: null }\n"
            "main: function is {\n"
            "  x: myvar.a 42\n"
            "  match (\n"
            "    x\n"
            "  ) case a then {\n"
            '    print "\\{x.a}"\n'
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
        """A spec definition generates a typedef in C output."""
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
        """Record with function pointer field (spec in 'is' section)."""
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
                ["gcc", "-Wall", "-Wno-unused-function", "-o", outpath, cpath],
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
        """Simple single-line string is not affected."""
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
        "reader: protocol {\n"
        "    read: function {:this b: i64} out i64\n"
        "}\n"
        "myfile: record {\n"
        "    fd: i64\n"
        "} as {\n"
        "    myreader: reader\n"
        "    read: function {f: this b: i64} out i64 is {\n"
        "        return f.fd + b\n"
        "    }\n"
        "}\n"
    )

    def test_protocol_vtable_struct(self):
        """C output contains vtable and instance struct."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is { f: myfile fd: 1 }"
        )
        assert "z_reader_vtable_t" in csource
        assert "z_reader_t" in csource
        assert "void* data;" in csource
        assert "z_reader_vtable_t* vtable;" in csource
        assert "void (*destroy)(void*);" in csource

    def test_protocol_impl_wrapper(self):
        """C output contains wrapper function and static vtable."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is { f: myfile fd: 1 }"
        )
        assert "z_myfile_myreader_read_wrapper" in csource
        assert "z_myfile_myreader_vtable" in csource
        assert "z_myfile_myreader_create" in csource

    def test_protocol_dispatch_runtime(self):
        """End-to-end: create record, create protocol instance, call method, verify output."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_protocol_with_class(self):
        """Protocol dispatch with class (pointer semantics)."""
        csource = emit_source(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myobj: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {o: this b: i64} out i64 is {\n"
            "        return o.fd + b\n"
            "    }\n"
            "}\n"
            "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 7\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    o: myobj fd: 20\n"
            "    r: o.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "27"

    def test_protocol_asan(self):
        """ASan clean: no memory leaks or errors with protocol instances."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
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
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            '    print "\\{use_reader f.myreader}"\n'
            "}\n"
        )
        # temp path should have stack alloc pattern, not create() call
        assert "_ps" in csource
        # the temp should not appear in any free() call
        assert "z_reader_destroy(_p" not in csource

    def test_protocol_temp_runtime(self):
        """Stack-allocated temp protocol instance works at runtime."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            '    print "\\{use_reader f.myreader}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_protocol_temp_asan(self):
        """ASan clean with stack-allocated temp protocol instances."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            '    print "\\{use_reader f.myreader}"\n'
            "}\n"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_protocol_named_var_still_heap(self):
        """Named protocol variables still use heap allocation."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: f.myreader\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        # named var should still use create function (heap alloc)
        assert "z_myfile_myreader_create" in csource


    def test_owned_protocol_create_record(self):
        """Owned protocol create from record compiles and runs."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.create from: f.take\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_owned_protocol_create_class(self):
        """Owned protocol create from class compiles and runs."""
        csource = emit_source(
            "reader: protocol {\n"
            "    read: function {:this b: i64} out i64\n"
            "}\n"
            "myobj: class {\n"
            "    fd: i64\n"
            "} as {\n"
            "    myreader: reader\n"
            "    read: function {o: this b: i64} out i64 is {\n"
            "        return o.fd + b\n"
            "    }\n"
            "}\n"
            "main: function is {\n"
            "    o: myobj fd: 20\n"
            "    r: reader.create from: o.take\n"
            '    print "\\{r.read b: 7}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "27"

    def test_owned_protocol_create_asan(self):
        """ASan-clean: no leaks or use-after-free with owned protocol."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.create from: f.take\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_owned_protocol_dispatch(self):
        """Method dispatch on owned protocol works via use_reader."""
        csource = emit_source(
            self.PROTO_SOURCE + "use_reader: function {r: reader} out i64 is {\n"
            "    result: r.read b: 5\n    return result\n"
            "}\n"
            "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.create from: f.take\n"
            '    print "\\{use_reader r}"\n'
            "}\n"
        )
        output = compile_and_run(csource)
        assert output.strip() == "15"

    def test_owned_protocol_destroy_emitted(self):
        """Owned create sets destroy function pointer (not NULL)."""
        csource = emit_source(
            self.PROTO_SOURCE + "main: function is {\n"
            "    f: myfile fd: 10\n"
            "    r: reader.create from: f.take\n"
            '    print "\\{r.read b: 5}"\n'
            "}\n"
        )
        assert "create_owned" in csource
        assert "boxed_destroy" in csource


class TestGenericsEmission:
    """Tests for generic type emission."""

    def test_generic_union_template_not_emitted(self):
        """Generic union template should not produce C struct."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is { x: myopt.some 42 }"
        )
        # template should NOT be emitted
        assert "z_myopt_tag_t" not in csource
        assert "z_myopt_t" not in csource

    def test_monomorphized_union_emitted(self):
        """Monomorphized union emits tag enum + struct + destructor."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is { x: myopt.some 42 }"
        )
        assert "z_myopt_i64_tag_t" in csource
        assert "z_myopt_i64_t" in csource
        assert "Z_MYOPT_I64_TAG_SOME" in csource
        assert "Z_MYOPT_I64_TAG_NONE" in csource
        assert "z_myopt_i64_destroy" in csource

    def test_monomorphized_union_construction_compiles(self):
        """Generic union construction compiles and runs."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "    x: myopt.some 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_monomorphized_union_match(self):
        """Match on monomorphized union works."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "  x: myopt.some 42\n"
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
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is { x: myopt.some 42 }"
        )
        assert "z_myopt_i64_destroy(x);" in csource

    def test_two_different_instantiations(self):
        """Two different instantiations produce two distinct types."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "    x: myopt.some 42\n"
            "    y: myopt.some 3.14\n"
            "}"
        )
        assert "z_myopt_i64_t" in csource
        assert "z_myopt_f64_t" in csource
        assert "z_myopt_i64_destroy" in csource
        assert "z_myopt_f64_destroy" in csource

    def test_system_option_compiles(self):
        """System option type compiles and runs."""
        csource = emit_source(
            'main: function is {\n    x: option.some 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_union_asan(self):
        """Monomorphized union passes AddressSanitizer."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "    x: myopt.some 42\n"
            "    y: myopt.none i32\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan failure: {result.stderr}"

    # ---- Generic from: call syntax ----

    def test_generic_union_from_compiles(self):
        """Generic union construction with from: compiles and runs."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "    x: myopt.some from: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_union_explicit_type_and_from_compiles(self):
        """Generic union with explicit type param and from: compiles and runs."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "    x: myopt.some t: i64 from: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_union_from_emits_correct_type(self):
        """from: syntax produces same monomorphized type as positional."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is { x: myopt.some from: 42 }"
        )
        assert "z_myopt_i64_t" in csource
        assert "Z_MYOPT_I64_TAG_SOME" in csource

    def test_generic_union_from_asan(self):
        """Generic union from: passes AddressSanitizer."""
        csource = emit_source(
            "myopt: union { t: any.generic\n some: t\n none: null }\n"
            "main: function is {\n"
            "    x: myopt.some from: 42\n"
            "    y: myopt.none i32\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan failure: {result.stderr}"

    def test_system_option_from_compiles(self):
        """System option type with from: compiles and runs."""
        csource = emit_source(
            'main: function is {\n    x: option.some from: 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_nongeneric_union_from_compiles(self):
        """Non-generic union construction with from: compiles and runs."""
        csource = emit_source(
            "myunion: union { a: i64\n b: null }\n"
            "main: function is {\n"
            "    x: myunion.a from: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    # ---- any.valtype / any.reftype constraints ----

    def test_valtype_constraint_record_compiles(self):
        """Record with any.valtype constraint compiles and runs."""
        csource = emit_source(
            "myrec: record { t: any.valtype\n x: t }\n"
            'main: function is {\n    r: myrec x: 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_valtype_constraint_union_compiles(self):
        """Union with any.valtype constraint compiles and runs."""
        csource = emit_source(
            "myopt: union { t: any.valtype\n some: t\n none: null }\n"
            'main: function is {\n    x: myopt.some 42\n    print "ok"\n}'
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_reftype_constraint_class_compiles(self):
        """Record with any.reftype constraint compiles and runs with class."""
        csource = emit_source(
            "mycls: class { v: i64 }\n"
            "holder: record { t: any.reftype\n ref: t }\n"
            "main: function is {\n"
            "    c: mycls v: 10\n"
            "    h: holder ref: c\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    # ---- Generic Classes ----

    def test_generic_class_template_not_emitted(self):
        """Generic class template should not produce C struct."""
        csource = emit_source(
            "mycls: class { t: any.generic\n val: t }\n"
            "main: function is { x: mycls val: 42 }"
        )
        assert "z_mycls_t " not in csource or "z_mycls_i64_t" in csource
        # template should not be emitted; only the monomorphized version
        assert "z_mycls_i64_t" in csource

    def test_generic_class_compiles(self):
        """Generic class construction compiles and runs."""
        csource = emit_source(
            "mycls: class { t: any.generic\n val: t }\n"
            "main: function is {\n"
            "    x: mycls val: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_class_asan(self):
        """Monomorphized class passes AddressSanitizer."""
        csource = emit_source(
            "mycls: class { t: any.generic\n val: t }\n"
            "main: function is {\n"
            "    x: mycls val: 42\n"
            "}"
        )
        result = compile_and_run_asan(csource)
        assert result.returncode == 0, f"ASan failure: {result.stderr}"

    def test_generic_class_methods(self):
        """Generic class with as-methods compiles."""
        csource = emit_source(
            "mycls: class { t: any.generic\n val: t } as {\n"
            "  getval: function {c: this} out i64 is { return c.val }\n"
            "}\n"
            "main: function is {\n"
            "    x: mycls val: 42\n"
            '    print "ok"\n'
            "}"
        )
        output = compile_and_run(csource)
        assert output.strip() == "ok"

    def test_generic_class_destructor(self):
        """Monomorphized class has destructor call at scope exit."""
        csource = emit_source(
            "mycls: class { t: any.generic\n val: t }\n"
            "main: function is { x: mycls val: 42 }"
        )
        assert "z_mycls_i64_destroy(x);" in csource

    # ---- Generic Protocols ----

    def test_generic_protocol_compiles(self):
        """Generic protocol definition compiles (template skipped)."""
        csource = emit_source(
            "myproto: protocol {\n"
            "  t: any.generic\n"
            "  get: function {:this} out t\n"
            "}\n"
            'main: function is { print "ok" }'
        )
        # generic template should NOT produce a struct
        assert "z_myproto_vtable_t" not in csource
        assert "z_myproto_t" not in csource
        output = compile_and_run(csource)
        assert output.strip() == "ok"
