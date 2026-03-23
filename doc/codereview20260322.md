# Zerolang Compiler Code Review — Follow-up

**Date**: 2026-03-22
**Scope**: Full compiler and documentation, follow-up to codereview20260321.md
**Previous review**: 12 findings, all implemented across 9 phases

---

## Executive Summary

The first code review delivered significant improvements: ~30% emitter reduction, type metadata
on `ZType`, consolidated scope state, SQL serialization, and removal of Python-specific patterns.
The codebase is in good shape. This follow-up identifies remaining issues in three categories:

1. **Bugs and correctness risks** — a monomorphization registration bug, missing malloc NULL
   checks in generated C code, and debug print statements left in production code.
2. **Simplifications** — duplicated RECORD/CLASS field registration, dead code in the lexer and
   parser, and an unused method stub.
3. **Test gaps** — 12 of 26 example programs have no emitter-level test, and several language
   features (lists, maps, facets, protocols) lack dedicated C emission tests.

None of these are as severe as the original review's findings. The compiler is solid for v1.

---

## Findings

### Finding 1: Monomorphization Registration Uses Only First Unit

**File**: `src/ztypecheck.py:2437-2440`
**Severity**: Medium — potential lookup failure in multi-unit programs

```python
for unitname in self.program.units:
    key = f"{unitname}.{mangled}"
    self._resolved[key] = mono
    break  # Only registers in the first unit
```

The `break` means monomorphized types are only registered under the first unit's namespace.
If a generic is instantiated from a second unit, the lookup `unitname.mangled` may fail to
find the type.

**Action**:
- [ ] Determine whether the intent is "register in all units" (remove `break`) or "register
  in the unit that triggered instantiation" (pass the originating unit name into the method
  instead of iterating).
- [ ] Add a multi-unit test that instantiates the same generic from two different units and
  verify both resolve.

---

### Finding 2: No NULL Check After malloc in Generated C Code

**File**: `src/zemitterc.py` (15+ call sites)
**Severity**: Medium — generated C code will segfault on allocation failure

Every `malloc` call in generated code is used immediately without checking for NULL:

| Line | Context |
|------|---------|
| 395 | Class `_create()` constructor |
| 751, 758 | `zstr_new` / `zstr_cat` |
| 983, 1007, 1034 | Protocol boxing |
| 1297 | Union variant constructors |
| 2007 | List/map `_create()` |
| 2146, 2155, 2170 | Map get/insert helpers |
| 3870, 3942 | Expression-level heap allocations |

**Action**:
- [ ] Decide on an allocation failure strategy. Options:
  - (a) Abort immediately: add `if (!ptr) { fprintf(stderr, "out of memory\n"); abort(); }`
    after every malloc. Simple, appropriate for v1.
  - (b) Centralize: emit a `zrt_malloc(size)` wrapper that does the check once, use it
    everywhere. Aligns with Phase 40 (C Runtime Library) on the roadmap.
- [ ] Whichever strategy, update `_emit_class_create`, `_emit_union_constructors`,
  `_emit_protocol_boxing`, `_emit_list_methods`, `_emit_map_methods`, and inline
  allocations in `_emit_expression`.

---

### Finding 3: Debug print Statements Left in Parser

**File**: `src/zparser.py:295, 347`
**Severity**: Low — noise in compiler output

```python
# Line 295 — fires every time an unresolved extern is pushed up
print(f"pushing up {k}")

# Line 347 — fires for every module file compiled
print(f"Compiling module file at: {self.vfs.path(fsid)}")
```

These are not behind a debug/verbose flag. They produce unexpected output during normal
compilation.

**Action**:
- [ ] Remove both print statements, or gate them behind a `--verbose` CLI flag.

---

### Finding 4: Duplicated RECORD/CLASS Field Registration in Emitter

**File**: `src/zemitterc.py:640-683`
**Severity**: Low — code duplication

The RECORD branch (lines 642-660) and CLASS branch (lines 664-683) contain identical logic:
iterate `mono_type.children`, skip special fields, build `field_names`/`field_ctypes` lists,
build `defaults` dict with the same index lookup. The only difference is local variable names.

Additionally, the defaults loop uses `list.index()` inside a loop (O(n²)) when a dict lookup
would suffice.

**Action**:
- [ ] Extract a helper: `_register_type_fields(self, mono_type)` that handles both RECORD and
  CLASS. Collapse the two branches into one covering both `ZTypeType.RECORD` and
  `ZTypeType.CLASS`.
- [ ] Replace `field_names.index(fn)` with a pre-built `{name: idx}` dict for O(1) lookup.

---

### Finding 5: Dead Code in Lexer

**File**: `src/zlexer.py`
**Severity**: Low — ~170 lines of commented-out code

| Lines | Content |
|-------|---------|
| 422-435 | Commented-out `acceptstringfrag()` method |
| 584-594 | Commented-out `BlockType` enum and partial `_read()` |
| 765-770 | Commented-out `makelexer()` function |
| 797-809 | Commented-out `_test()` function and `__main__` block |

**Action**:
- [ ] Delete all four commented-out blocks. They are in git history if ever needed.

---

### Finding 6: Unused `_fixcalloperation` Stub in Parser

**File**: `src/zparser.py:2015-2026`
**Severity**: Low — dead code

```python
@staticmethod
def _fixcalloperation(opx: NodeX[zast.Operation]) -> NodeX[zast.Operation]:
    """fixcalloperation - check a call value to see if it is a single Id..."""
    return opx  # Does nothing
```

The method has a docstring describing intended behavior but the implementation is a passthrough.

**Action**:
- [ ] If the behavior described in the docstring is still needed, implement it.
- [ ] If not, remove the method and its call sites.

---

### Finding 7: Encapsulation Violation — Parser Accesses Lexer Private State

**File**: `src/zparser.py:466, 2048, 2322`
**Severity**: Low — porting friction

The parser reads `lex._filtereol` directly (4 times) instead of using a public accessor.
The lexer has a setter `filtereol()` at line 721 but no getter.

**Action**:
- [ ] Add a property or method to `Lexer` (e.g. `@property filtereol -> bool`) that returns
  `self._filtereol`.
- [ ] Replace all `lex._filtereol` reads in the parser with `lex.filtereol`.

---

### Finding 8: zip Truncation in SQL Dump Source Map

**File**: `src/zsqldump.py:274`
**Severity**: Low — silent data loss in debug output

```python
for i, (text, nid) in enumerate(zip(c_lines, emitter.source_map)):
```

If `c_lines` and `emitter.source_map` have different lengths, `zip` silently truncates to the
shorter one. This would lose emitted-line data without warning.

**Action**:
- [ ] Add an assertion: `assert len(c_lines) == len(emitter.source_map)` before the loop,
  or handle the mismatch explicitly (pad with None).

---

### Finding 9: Typos in Source

**Severity**: Trivial

| File | Line | Current | Should Be |
|------|------|---------|-----------|
| `src/zlexer.py` | 778 | "undercores" | "underscores" |

**Action**:
- [ ] Fix typo.

---

## Test Coverage Gaps

### Gap 1: 12 Example Programs Not Tested Through Emitter

The parser tests parametrically verify all 26 examples parse successfully. But only 14 are
tested through the full compile-to-C pipeline via `_emit_example()`:

**Tested**: hello, factorial, fibonacci, swap, strings, control, case, records, multimod,
data, unions, constructors, defaults (+ classes via inline tests)

**Not tested through emitter**:
- `arrays.z` — array operations
- `facets.z` — facet definitions and usage
- `generics.z` — generic type instantiation
- `lists.z` — list operations (append, extend, get, insert, remove)
- `maps.z` — map operations (set, get, delete, has)
- `numeric_generics.z` — numeric generic constraints
- `protocols.z` — protocol dispatch
- `specs.z` — spec definitions
- `str.z` — string module operations
- `typedefs.z` — typedef definitions
- `variants.z` — variant types
- `mathutil.z` — math utility module

**Action**:
- [ ] Add `_emit_example()` tests for all 12 missing examples. If any do not yet compile
  to valid C, mark them `xfail` with a ticket reference.

### Gap 2: No Dedicated Error Case Tests for Advanced Features

The type checker tests are comprehensive for core features but thin on error paths for:

- Protocol method signature mismatches (wrong return type, wrong param count)
- Facet implementation failures (missing method, wrong type)
- Generic constraint violations (passing `valtype` where `reftype` required)
- Monomorphization edge cases (partial instantiation, recursive generics)

**Action**:
- [ ] Add a `TestProtocolErrors` class in `test_typecheck.py` covering: missing method,
  wrong signature, wrong return type.
- [ ] Add a `TestFacetErrors` class covering: incomplete facet, type mismatch in
  facet method.
- [ ] Add generic constraint violation tests to `TestGenerics`.

### Gap 3: No Emitter Tests for List and Map Operations

Lists and maps have examples (`lists.z`, `maps.z`) but no targeted emitter tests that
verify the generated C compiles and produces correct output for individual operations.

**Action**:
- [ ] Add `TestEmitterLists` with tests for: create, append, get, insert, remove, extend,
  length, iteration.
- [ ] Add `TestEmitterMaps` with tests for: create, set, get, has, delete, length.
- [ ] Each test should use `compile_and_run` (and ideally `compile_and_run_asan`) to
  verify memory safety.

---

## Architectural Notes

These are not bugs or action items — they are observations for future consideration.

### A. assert for Invariant Checks

`src/ztypecheck.py:666, 882` use `assert as_type is not None` after conditions that
already guarantee `as_type` is set. These are logically redundant but serve as documentation.
For a production compiler, consider replacing with explicit error reporting so that malformed
input never crashes the compiler. Not urgent for v1.

### B. getattr Defensive Access Pattern

The type checker uses `getattr(obj, "type", None)` at ~10 call sites to defensively check
for type annotations on AST nodes. The first code review (Finding 10) replaced `hasattr`
with direct attribute access in most places, but some remain. These should be resolved as the
type annotation coverage improves — eventually all paths through the type checker should have
`.type` set, and the `getattr` calls can become direct access.

### C. Auto-Generated Binding Names

The parser uses `f" *{index}"` as synthetic binding names for unlabelled `when`/`while`/`of`
conditions (lines 1566, 1681, 1790). The leading space prevents collisions with user names.
This works but is fragile — consider a dedicated sentinel type or prefix like `__anon_` when
porting to zerolang where string conventions may differ.

---

## Future Directions

### Near-Term (v1 Completion)

1. **Phase 40: C Runtime Library** — extract `ZStr`, `zstr_new`, `zstr_cat`, allocation
   wrappers into `libzrt.a`. This is already on the roadmap and would resolve Finding 2
   (malloc NULL checks) by centralizing allocation.

2. **Compile and run all examples in CI** — extend the test suite to compile every example
   to C, compile the C with gcc/clang, and run it. This is the single highest-value test
   improvement.

3. **Error message quality** — the compiler currently returns error codes and messages but
   doesn't always include source location context (showing the offending line). Adding a
   simple source-line-in-error-message feature would significantly improve usability.

### Medium-Term (Pre-Self-Hosting)

4. **Visitor / dispatch pattern for AST** — the first review reduced `isinstance` cascades
   but the type checker still has large `if isinstance(expr, Call) ... elif isinstance(expr,
   If) ...` chains. A table-driven or method-per-node-type dispatch would make the code more
   mechanical to port and extend.

5. **Immutable AST** — the type checker mutates AST nodes in place (setting `.type` on
   paths). A separate typed-AST layer would make the pipeline stages cleaner and the SQL
   dump more meaningful (you could dump both the untyped and typed ASTs).

6. **Intermediate Representation (IR)** — currently the emitter walks the AST directly.
   An IR between the type checker and emitter would:
   - Enable backend-independent optimizations
   - Make it easier to add new backends (LLVM, WASM, zerolang)
   - Simplify the emitter significantly

### Long-Term (Language Evolution)

7. **Module system improvements** — the current multi-module system uses VFS path binding.
   Consider adding: explicit export lists, circular dependency detection, and incremental
   compilation (only recompile changed units).

8. **Pattern matching** — the `case` statement currently matches union subtypes. Extending
   it to destructure records, match literals, and support guards would bring the language
   closer to modern systems languages.

9. **Closures and first-class functions** — functions are currently top-level only.
   Supporting closures (even limited ones — no escaping, stack-only) would enable common
   patterns like `list.filter(fn)` and `list.map(fn)`.

10. **Algebraic effects or structured error handling** — the language currently has no
    error handling mechanism beyond return values. A `try`/`catch` or Result-type system
    would be needed for real-world code.

11. **Const and comptime evaluation** — evaluating constant expressions at compile time
    would enable `const` declarations, compile-time array sizes, and eventually
    compile-time function execution (like Zig's `comptime`).

12. **Self-hosting bootstrap** — once the language is expressive enough (strings, hash maps,
    file I/O, dynamic arrays), rewrite the compiler in zerolang. The SQL dump feature will be
    invaluable for comparing the Python compiler's output with the zerolang compiler's output
    during bootstrap.
