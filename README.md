# Zerolang

Zerolang is a systems programming language that compiles to C. The compiler is
self-hosted — written in Zerolang — and bootstraps from a small committed C
seed, so a C toolchain is all you need to build it. It emits C, which is
compiled with gcc or clang.

## Documentation

The full documentation is rendered to HTML and published with GitHub Pages:

- **[Documentation home](https://technobok.github.io/zerolang/)** — language
  spec, grammar, ownership model, and standard library reference.
- **[`zc` command reference](https://technobok.github.io/zerolang/zc.html)** —
  the compiler CLI: commands, flags, tree resolution, and cross-compilation.

## Language Basics

Zerolang uses a clean, keyword-driven syntax with no operator precedence (all
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
name: "Zerolang"
print "Hello, \{name}!"
```

### Types

v1 numeric types: `u8` `u16` `u32` `u64` `i8` `i16` `i32` `i64` `f32` `f64`

## Quick Start

### Prerequisites

- a C compiler — `gcc` or `clang`
- `make`
- `git`

Nothing else: the compiler is self-hosted and bootstraps from a committed C
seed, so no other tooling is required.

### Build the compiler

```bash
git clone https://github.com/technobok/zerolang.git
cd zerolang
make zc            # -> ./bin/zc
```

`make zc` compiles the committed seed (`bootstrap/zc.c`) with your C compiler,
then uses it to build the current compiler from `src/` into `./bin/zc`. Use a
different compiler with `make zc CC=clang`.

You can also build straight from the seed with nothing but a C compiler — the
seed *is* the compiler:

```bash
cc -std=c17 -o zc bootstrap/zc.c
```

### Run the tests

The gates are self-contained — they need only the compiler and a C toolchain
(no Python, no shell). The test runner and linter are themselves zerolang
programs (`src/ztestrunner.z`, `src/zl.z`):

```bash
make check             # fast style lint (zl) over src + front-end
make test              # compile + run the example/corpus programs, check output
make ci                # full gate: style-lint + corpus (--heavy) + seed bootstrap
make test-bootstrap    # rebuild the compiler from the seed, check the fixpoint
```

### Write and run a program

Create `hello.z`:

```
main: function is {
    print "Hello, World!"
}
```

Then build and run it — `zc` self-locates its standard library, so no flags are
needed:

```bash
./bin/zc run hello                  # build and run in one step
./bin/zc build hello -o hello       # build an executable...
./hello                             # ...then run it
```

The bundled programs live in `examples/`, so point `--src` at them:

```bash
./bin/zc run fibonacci --src examples
```

See the [`zc` command reference](https://technobok.github.io/zerolang/zc.html)
for every command and flag (`emit`, `dump`, `explain`, `env`, `--release`,
`--target`, …).

### Install

Install a self-contained tree plus a `zc` on your `PATH`:

```bash
make install                                  # ~/.local/bin/zc + ~/.local/lib/zerolang
make install ROOT=/opt/zerolang BINDIR=/usr/local/bin
```

The installed `zc` finds its standard library from any directory (it resolves
its own executable path).

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

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or
  <http://www.apache.org/licenses/LICENSE-2.0>)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or <http://opensource.org/licenses/MIT>)

at your option. SPDX: `MIT OR Apache-2.0`.

Copyright is held collectively by The Zerolang Authors; see [AUTHORS](AUTHORS).

### Contribution

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in this project by you, as defined in the Apache-2.0 license,
shall be dual licensed as above, without any additional terms or conditions.

### Compiled output

`zc` copies the C runtime fragments in `src/runtime/` into the C it emits, so
they end up in the binary you build. That embedding carries no attribution or
notice requirement: you may distribute programs built with `zc` under any terms
you choose, and nothing in this license applies to your own source.

## Third-party components

The compiler vendors third-party source under `vendor/`. Each directory keeps
its upstream license alongside a `VERSION.md` recording the origin, the pinned
commit, and exactly what was copied.

| Component | License | Linked |
|---|---|---|
| [mimalloc](vendor/mimalloc) | MIT | statically, into the driver binaries; `make MIMALLOC=0` builds without it |
| [tinycc](vendor/tinycc) | LGPL-2.1 | never. `zc` reaches `libtcc.so` through `dlopen` only, so no binary here is a combined work; `make static-tcc-guard` fails the build if a libtcc symbol appears in one |

These licenses govern those components, not the rest of the project.

`bin/tcc` and `lib/tcc/` in an installed tree are tinycc itself, unmodified and
under its own license; `make install` places them, and `zc --cc tcc` invokes
them the way it would any other C compiler on the system.
