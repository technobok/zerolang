# Zero Language

Zero is a systems programming language that compiles to C. The compiler is
written in Python and emits C code which is compiled with gcc or clang.

## Language Basics

Zero uses a clean, keyword-driven syntax with no operator precedence (all
binary operations evaluate left-to-right).

### Hello World

```
main: function is {
    print "Hello, World!"
}
```

### Functions

Functions use `in`/`out`/`is` keywords. The first parameter is unnamed;
additional parameters are named:

```
fib: function {n: i64} out i64 is {
    if n < 2 then return n
    a: 0
    b: 1
    for i: 2 while i <= n loop {
        temp: a + b
        a = b
        b = temp
        i = i + 1
    }
    return b
}
```

### Variables

Variables are declared with `:` and reassigned with `=`:

```
x: 42        # declaration (type inferred)
x = x + 1   # reassignment
```

### Control Flow

```
# if/when/then/else
if x > 0 when x < 100 then print "in range" else print "out of range"

# for/while/loop
for i: 0 while i < 10 loop {
    print "\{i}"
    i = i + 1
}

# with/do (scoped binding)
with y: abs -5 do print "abs(-5) = \{y}"
```

### Records

Records are value types (zero-initialized, no constructors):

```
point: record {
    x: f64
    y: f64
} as {
    add: function {a: this b: point} out point is {
        result: point
        result.x = a.x + b.x
        result.y = a.y + b.y
        return result
    }
}
```

### Strings

Strings are immutable. Interpolation uses `\{expr}`:

```
name: "Zero"
print "Hello, \{name}!"
```

### Types

v1 numeric types: `u8` `u16` `u32` `u64` `i8` `i16` `i32` `i64` `f32` `f64`

## Compiling

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- gcc or clang

### Build the compiler

Build the self-hosted compiler once (it bootstraps via the Python
reference and is cached in `bin/`, git-ignored):

```bash
make zc        # -> ./bin/zc
```

Or build it from the committed C seed with **no Python** — just a C
compiler (`cc bootstrap/zc.c` *is* the self-hosted compiler; see
`bootstrap/README.md`):

```bash
cc -std=c17 -o zc bootstrap/zc.c
./zc hello --src examples --system lib/system -o hello
```

### Compile and Run

`zc` builds a native executable by default and self-locates its standard
library, so from the repo:

```bash
# Build and run in one step (trailing args are forwarded to the program)
./bin/zc run hello --src examples

# Build an executable, then run it
./bin/zc build hello --src examples -o hello && ./hello

# Emit C only
./bin/zc emit hello --src examples -o hello.c
```

The Python reference compiler is still available for bootstrapping and
diagnostics:

```bash
uv run python compiler0/zc.py hello --src examples/ -o hello.c
```

### Install

Install a self-contained tree plus a `zc` on your `PATH`:

```bash
make install                                  # ~/.local/lib/zerolang + ~/.local/bin/zc
make install ROOT=/opt/zerolang BINDIR=/usr/local/bin
```

The installed `zc` finds its stdlib and runtime from any directory (it
resolves its own executable path). See [`doc/zc.pdoc`](doc/zc.pdoc) for the
full CLI: subcommands (`build`/`run`/`emit`/`dump`), flags, the
tree-resolution precedence, environment variables, and cross-compilation.

## Example Programs

The `examples/` directory contains v1 target programs:

| Program | Demonstrates |
|---------|-------------|
| `hello.z` | String literals, print |
| `fibonacci.z` | Loops, arithmetic, functions |
| `factorial.z` | Recursion, return values |
| `records.z` | Record types, methods, field access |
| `strings.z` | String interpolation |
| `control.z` | if/when/then/else, for/while/loop, with/do |
| `case.z` | Match expressions on enums |
| `swap.z` | Swap keyword, reassignment |
| `data.z` | Constant data arrays |
| `multimod.z` | Multi-module imports |

## Running Tests

```bash
uv run python -m pytest tests/ -v
```

## Project Structure

```
src/               # the self-hosted compiler, written in Zero
  zc.z            # Compiler entry point / CLI
  zlexer.z        # Lexer
  zparser.z       # Parser
  zast.z          # AST node definitions
  ztypecheck.z    # Type checker
  zemitterc.z     # C code emitter
  runtime/        # C runtime fragments (.inc / .c.tmpl) spliced into output
compiler0/         # original Python reference compiler (bootstrap + oracle)
  zc.py           # see compiler0/README.md — not the way to build the compiler
lib/
  system/         # System library (system.z, core.z, io.z)
doc/
  spec.pdoc       # Language specification
  grammar.pdoc    # Formal grammar
  roadmap.pdoc    # Implementation roadmap
  faq.pdoc        # Design FAQ
examples/         # v1 example programs
tests/            # Test suite
```
