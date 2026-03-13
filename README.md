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

### Compile and Run

From the `examples/` directory:

```bash
# Compile a zerolang program to C
uv run python ../src/zc.py hello

# Compile the generated C to a binary
gcc -Wall -Wno-unused-function -o hello hello.c

# Run
./hello
```

From anywhere, specify source and system directories:

```bash
uv run python src/zc.py hello --src examples/ --system lib/system/
gcc -Wall -Wno-unused-function -o hello hello.c
```

### Compiler Options

```
usage: zc.py [-h] [--system SYSTEM] [--src SRC] [--full-typecheck] unitname

  unitname          Name of the unit to compile
  --system SYSTEM   Path to the system directory (default: <project_root>/lib/system)
  --src SRC         Path to the user source directory (default: current directory)
  --full-typecheck  Type-check all definitions, not just those reachable from main
```

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
src/
  zc.py           # Compiler entry point
  zlexer.py       # Lexer
  zparser.py      # Parser
  zast.py         # AST node definitions
  ztypecheck.py   # Type checker
  zemitterc.py    # C code emitter
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
