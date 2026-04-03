# CLAUDE.md

## Build and Test

```bash
make check       # format (ruff), lint (ruff), type check (ty), bootstrap-lint
make test        # run all tests (pytest)
make build       # compile all examples: .z -> .c -> binary (in out/)
make clean       # remove out/
```

## Architecture

Zerolang is a compiled language targeting C. The compiler pipeline:

1. **Lexer** (`zlexer.py`) - tokenizes `.z` source files
2. **Parser** (`zparser.py`) - builds AST from tokens
3. **Type checker** (`ztypecheck.py`) - resolves types, checks ownership, monomorphizes generics
4. **Emitter** (`zemitterc.py`) - generates C source from typed AST

Supporting modules:
- `zast.py` - AST node definitions (tagged union via `NodeType` enum + `cast()`)
- `ztypes.py` - `ZType` dataclass, `TAG_ORIGIN` sentinel
- `ztypeutil.py` - shared type helpers (collection type checks)
- `zemitterc_runtime.py` - C runtime code generation (ZStr, includes)
- `zvfs.py` - virtual filesystem for source file resolution
- `zenv.py` - symbol table for type checker scopes

## Key Conventions

- Do not use single underscore prefix for "private" methods
- Do not add Co-Authored-By to commits
- Run `make check` before every commit (must pass with zero errors)
- `isinstance` is eliminated from src/ - use field-based dispatch (`is_node`, `is_error`, `is_expression`, `nodetype`, `type() is`, `getattr`)
- AST node dispatch uses `nodetype` enum + `cast()`, not isinstance
- `native` keyword marks compiler-provided implementations in `.z` files

## Standard Library

- `lib/system/system.z` - numeric types, string, control flow, tag, typedef
- `lib/system/collections.z` - array, str, list, map with native methods
- `lib/system/core.z` - imports system.z and collections.z
- `lib/system/io.z` - print function

## Tests

- `tests/` - pytest tests (~1200 tests)
- `tests/conftest.py` - shared test helpers (make_parser_vfs, check_ok)
- `examples/` - 39 example programs that compile to working binaries

## Documentation

- `doc/roadmap.pdoc` - project phases and status
- `doc/spec.pdoc` - language specification
- `doc/compiler.pdoc` - compiler internals
- `doc/codereview*.md` - code review findings and action items
