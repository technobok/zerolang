# Zerolang Compiler Code Review — Architectural & Self-Hosting Readiness

**Date**: 2026-03-29
**Scope**: Full compiler, standard library, documentation, language design
**Focus**: System type hardcoding, self-hosting readiness, `isinstance` reduction, compiler/library boundary
**Previous reviews**: codereview20260321.md (12 findings, all resolved), codereview20260322.md (9 findings)

---

## Implementation Status

The `native` keyword was implemented on 2026-03-29. Functions use `is native` as the body,
and object types use `record is native` (or `class is native`, etc.) for compiler-provided
state. The keyword is used in system.z and collections.z for compiler-provided definitions.

---

## Executive Summary

The zerolang compiler is in strong shape: all 1118 tests pass, all 36 examples compile and link to
working binaries, and the two previous code reviews drove significant architectural improvements.
The codebase is approximately 17,500 lines of Python producing clean, working C output.

This review identifies **5 major architectural themes** and **12 specific findings** that together
address the central concern: **system types and methods are hardcoded into the compiler rather than
being discovered through the normal resolution path from system.z/core.z**. This hardcoding creates
a coupling that will impede self-hosting, makes the compiler fragile to standard library changes,
and prevents a future language server from understanding system types without compiler internals.

The key design is the **`native` keyword** — a marker in `.z` files that tells the emitter "this
definition's implementation is provided by the compiler backend." Functions use `is native` as
their body; objects use `record is native` for compiler-provided state. This separates *what the
type is* (declared in system.z, visible to type checker and language server) from *how it is
implemented* (emitter-specific C code). Combined with reducing `isinstance` usage and extracting
duplicated patterns, this makes the compiler significantly more portable and the standard library
self-documenting.

### Metrics

| Metric | Value |
|--------|-------|
| Total Python source lines | ~17,570 |
| `isinstance` calls in ztypecheck.py | 153 |
| `isinstance` calls in zemitterc.py | 147 |
| `isinstance` calls in zparser.py | 70 |
| Hardcoded system type name checks (typecheck) | 16 |
| Hardcoded system type name checks (emitter) | 23 |
| Tests passing | 1118/1118 |
| Examples compiling | 36/36 |

---

## Theme 1: The Compiler/Library Boundary Problem

### The Current State

System types are defined in three layers with inconsistent boundaries:

1. **system.z** declares type shapes (e.g., `string: class {}`, `option: union { some: t, none: null }`)
   but methods for numeric types are declared using the magic `type` keyword and the implementations
   are invisible — they exist nowhere in the `.z` files.

2. **The type checker** (ztypecheck.py) has hardcoded knowledge of system types scattered throughout:
   - `_is_array_type()`, `_is_str_type()`, `_is_list_type()`, `_is_map_type()` — 4 functions that
     check `generic_origin.name == "array"` etc. (lines 77-138)
   - `_set_destructor_metadata()` — hardcodes `"string"` → `zstr_free` (line 158)
   - Special handling for `option` monomorphization (line 2691)
   - String type compatibility override (line 2482)
   - `return`, `break`, `continue`, `error` as magic function names (lines 3745, 4426)

3. **The emitter** (zemitterc.py) duplicates all of these checks plus adds more:
   - Identical `_is_array_type()`, `_is_str_type()`, `_is_list_type()`, `_is_map_type()` functions
     (lines 79-148) — **copy-pasted from the type checker**
   - 800+ lines of hand-crafted C code generators for collections (array, str, list, map)
   - Inline ZStr runtime (~80 lines of C string handling)
   - Special-case emission for `string`, `option`, `box`, `return`, `break`, `continue`

### The Problem

A future language server reading system.z sees `string: class {}` — an empty class with no methods.
It cannot discover that string has concatenation, comparison, length, or interpolation support without
parsing the compiler source code. Similarly, `list` appears as `class {} as { of: any.generic }` with
no indication of `append`, `get`, `set`, `remove`, `pop`, `length`, or `extend`.

For self-hosting, the entire body of knowledge hardcoded in `_emit_mono_list()` (160 lines),
`_emit_mono_map()` (330 lines), and `_emit_mono_str()` (48 lines) would need to be re-implemented
in zerolang — duplicating what should be declared in the standard library.

### The Solution: `native` Keyword

The `native` keyword marks compiler-provided implementations. It is recognized by the parser as
a body alternative for functions (`is native`) and as a state marker for objects (`record is native`).

**Final design:**

```zero
# Functions: is native replaces the body
string: class {
    length: function {:this} out u64 is native
    ==: function {:this rhs: string} out bool is native
    +: function {:this rhs: string} out string is native
}

# Objects with compiler-provided state: record is native
tag: record is native as { t: any.generic }
typedef: record is native as { from: any.generic }
```

**Why `native`:**
- It is syntactically clear and unambiguous — a single keyword in the body position
- The type checker resolves the full method signature normally
- The emitter checks for the `native` marker and supplies the C implementation
- A language server can enumerate all methods without compiler internals
- Consistent with other languages' use of `native` for platform-provided implementations

**Impact**: The type checker resolves `string.length` through the normal dotted-path resolution
instead of special-casing it. The emitter matches `native`-marked functions by qualified name
to its C implementation table. The implementation table becomes the *only* place where system types
are hardcoded in the emitter.

---

## Theme 2: isinstance Reduction for Self-Hosting

### Finding 1: isinstance Cascades Still Dominate Control Flow

**Files**: ztypecheck.py (153), zemitterc.py (147), zparser.py (70)
**Severity**: Medium — porting blocker for self-hosting

The previous review (Finding 5) converted `_type_of_definition()` to a dispatch table and changed
some `isinstance` checks to `type()` equality. However, 370 `isinstance` calls remain across the
three main compiler files.

**Categories of isinstance usage:**

| Category | Count (approx) | Portable? | Action |
|----------|----------------|-----------|--------|
| AST node dispatch (`isinstance(expr, zast.Call)`) | ~200 | No | Use nodetype enum |
| Type checking (`isinstance(origin, ZType)`) | ~50 | No | Use explicit flag |
| Mixed AST/non-AST (`isinstance(defn, zast.Function)`) | ~70 | No | Use dispatch table |
| Python builtins (`isinstance(value, int)`) | ~20 | No | Use type tags |
| Legitimate runtime type narrowing | ~30 | Maybe | Case-by-case |

**Recommended approach for self-hosting:**

Every AST node already has a `nodetype: NodeType` enum field. Instead of:
```python
if isinstance(expr.expression, zast.Call):
    return self._check_call(expr.expression)
elif isinstance(expr.expression, zast.If):
    return self._check_if(expr.expression)
```

Use the nodetype for dispatch:
```python
handler = self._EXPRESSION_HANDLERS.get(expr.expression.nodetype)
if handler:
    return handler(self, expr.expression)
```

This maps directly to a zerolang `case` statement:
```zero
match expr.expression.nodetype
    case call then check_call expr.expression
    case if then check_if expr.expression
```

**Action items:**
- [x] Audit all 153 isinstance calls in ztypecheck.py; categorize by whether they check AST
  node type (replaceable with nodetype) vs other uses
- [x] Convert the major cascades in `_check_expression()`, `_check_statement_line()`,
  `_check_operation()` to dispatch tables keyed on `NodeType`
- [x] For `isinstance(origin, ZType)` checks (in monomorphization), add an explicit
  `is_ztype: bool` field or use a sentinel value instead
- [x] For `isinstance(value, int)` in constant folding, use a tagged union or type field
- [x] Same treatment for zemitterc.py's 147 instances

---

### Finding 2: Duplicated Collection Type Helpers

**Files**: ztypecheck.py:77-138, zemitterc.py:79-148
**Severity**: Low — code duplication, maintenance risk

Eight functions are copy-pasted between the type checker and emitter:

| Function | ztypecheck.py | zemitterc.py | Identical? |
|----------|---------------|--------------|------------|
| `_is_array_type()` | line 77 | line 79 | Nearly (emitter adds None check) |
| `_array_element_type()` | line 84 | line 88 | Yes |
| `_array_length()` | line 89 | line 93 | Yes |
| `_is_str_type()` | line 97 | line 101 | Nearly |
| `_str_capacity()` | line 104 | line 110 | Yes |
| `_is_list_type()` | line 112 | line 118 | Nearly |
| `_list_element_type()` | line 119 | line 127 | Yes |
| `_is_map_type()` | line 125 | line 132 | Nearly |
| `_map_key_type()` | line 131 | line 141 | Yes |
| `_map_value_type()` | line 136 | line 146 | Yes |
| `_is_numeric_id()` | line 68 | line 74 | Yes |

All of these check `generic_origin.name` — they rely on string matching against hardcoded type names.
With the `native` directive, these could instead check a flag on the ZType (e.g.,
`ztype.is_collection_type` or `ztype.generic_origin.typetype == ZTypeType.COLLECTION`).

**Action items:**
- [x] Move the 11 shared helper functions to ztypes.py or a new shared module (e.g., ztypeutil.py)
- [x] Import them in both ztypecheck.py and zemitterc.py
- [ ] Consider adding `is_container` or similar metadata to ZType during resolution

---

### Finding 3: Tag Resolution Code Duplicated Between Union and Variant

**File**: ztypecheck.py:1016-1145 (union) vs 1273-1394 (variant)
**Severity**: Medium — 250 lines of near-identical code

The tag resolution logic for unions (`_resolve_union_type`, lines 1016-1145) and variants
(`_resolve_variant_type`, lines 1273-1394) is structurally identical:

1. Scan as_items for tag type (same 15-line pattern)
2. If custom DATA tag: validate labels match subtypes, check unique values, build enum
3. If custom RECORD tag (numeric type): auto-generate sequential values
4. If no custom tag: default u8, auto-generate

The only differences are the error message strings ("Union" vs "Variant") and the variable name
(`utype` vs `vtype`).

**Action items:**
- [x] Extract a shared `_resolve_tag(type_name: str, ztype: ZType, as_items, subtype_names, loc)` method
- [x] Call it from both `_resolve_union_type` and `_resolve_variant_type`
- [x] This would eliminate ~130 lines of duplication

---

## Theme 3: system.z Completeness

### Finding 4: system.z Numeric Type Declarations Are Highly Repetitive

**File**: lib/system/system.z
**Severity**: Low — maintenance burden, documentation concern

The 12 numeric types (u8, i8, u16, i16, u32, i32, u64, i64, u128, i128, f32, f64) each declare
identical method signatures. The file is ~310 lines, of which ~290 are repetitive numeric type
declarations. Each type has:
- 6 arithmetic operators (+, -, *, /)
- 6 comparison operators (<=, <, >, >=, ==, !=)
- 10-11 conversion methods (to other numeric types)
- A `tag` field

**Issues:**
1. Adding a new numeric operation (e.g., modulo `%`, bitwise ops) requires editing 12 blocks
2. Float types declare integer conversion methods but the compiler may not support all of them
3. There is no `negate` (unary minus) method declared
4. Missing: modulo/remainder, bitwise AND/OR/XOR/NOT/shift, min/max

**Options:**
- **Short term**: Accept the repetition as explicit documentation
- **Medium term**: If zerolang gains a template/macro mechanism, generate the numeric types
- **With native**: Each method body would be `is native`, making the file serve as
  authoritative documentation of what numeric operations exist

**Action items:**
- [ ] Add missing operators to system.z (modulo, bitwise ops) if they are on the roadmap
- [ ] Consider whether unary operators (negate, bitwise NOT) need declaration
- [ ] Document which numeric conversions are actually implemented vs declared

---

### Finding 5: Collection Types Have No Declared Methods

**File**: lib/system/collections.z (10 lines)
**Severity**: High — this is the core of the compiler/library boundary problem

```zero
# Current state:
list: class {} as {
    of: any.generic
}
```

The `list` type is declared as an empty generic class. All of its methods (append, get, set,
insert, remove, pop, extend, length, iterate) exist only in the emitter as hardcoded C generators
(`_emit_mono_list`, ~160 lines). A language server or self-hosted compiler has no way to discover
these methods.

**With `native` directive**, collections.z would become:

```zero
list: class {
    append: function {:this item: of} is native
    get: function {:this index: u64} out of is native
    set: function {:this index: u64 item: of} is native
    insert: function {:this index: u64 item: of} is native
    remove: function {:this index: u64} is native
    pop: function {:this} out of is native
    extend: function {:this other: type.borrow} is native
    length: function {:this} out u64 is native
    create: function {capacity: 0u64} out type is native
} as {
    of: any.generic
}
```

Similarly for `map`, `array`, `str`, and `string`.

**Action items:**
- [x] Implement the `native` directive (see Theme 1)
- [x] Expand collections.z with full method signatures for list, map, array, str
- [ ] Expand system.z string class with method signatures (length, ==, +, etc.)
- [ ] The emitter's C generators would key off the qualified name + `native` flag
  rather than checking generic_origin.name

---

### Finding 6: Control Flow Functions Lack Proper Signatures

**File**: lib/system/system.z (bottom)
**Severity**: Medium — magic names in compiler

```zero
# Current:
return: function as { t: any.generic } in { result: t } out never is { error "return not erased by compiler" }
break: function out never is { error "break not erased by compiler" }
continue: function out never is { error "continue not erased by compiler" }
error: function {msg: string} out never
```

These are good — they use `error` as a sentinel body, which is essentially the `native` pattern
already. The type checker resolves `return`, `break`, `continue` through the normal name resolution
from core.z → system.z. However, the emitter then special-cases them by checking the function name
string.

The `error` function itself has no body, which means it is parsed as a spec (bodyless function).
The emitter must know to emit it as `fprintf(stderr, ...) + exit(1)`.

**With native:**
```zero
error: function {msg: string} out never is native
return: function as { t: any.generic } in { result: t } out never is native
break: function out never is native
continue: function out never is native
```

This is cleaner than using `error` as a sentinel because:
1. It is explicit — no runtime message about compiler erasure
2. The type checker can distinguish between "user called error as a runtime abort" and
   "this is a compiler-provided implementation"
3. Consistent with all other compiler-provided definitions

**Action items:**
- [x] Change return/break/continue/error bodies to `native` once the directive exists
- [x] The emitter already handles these by name; the change is mechanical

---

## Theme 4: Emitter Architecture

### Finding 7: Monomorphized Collection Emitters Are Standalone C Libraries

**File**: zemitterc.py
**Severity**: Medium — 800+ lines of hand-crafted C templates

| Emitter method | Lines | What it generates |
|----------------|-------|-------------------|
| `_emit_mono_list()` | ~160 | Full dynamic list implementation (struct, create, destroy, append, get, set, insert, remove, pop, extend, length) |
| `_emit_mono_map()` | ~330 | Full hash map implementation (struct, create, destroy, get, set, has, delete, keys, length, hash, equality, probing) |
| `_emit_mono_str()` | ~48 | Fixed-size string operations |
| `_emit_mono_array()` | ~75 | Fixed-size array struct and create |
| ZStr runtime | ~80 | String type struct, create, cat, free, compare, print |

These 700 lines of Python that generate C are the most complex part of the emitter and the hardest
to port to self-hosting. They also represent the biggest risk for correctness — the map implementation
alone has hash functions, equality comparisons, open-addressing probe sequences, and resize logic.

**Near-term improvement** (no language change needed):
Extract the C code into template strings or a separate C runtime file. Instead of generating C
line-by-line in Python, the emitter would read a C template and substitute type names.

**Medium-term** (Phase 40 on roadmap: C Runtime Library):
Move ZStr, list, map, and array implementations into a proper `libzrt.c` / `libzrt.h` that is
compiled and linked rather than inlined into every output file. The emitter would generate only
the type-specific wrappers, not the core algorithms.

**Long-term** (with native):
Once `native` is in place, the emitter's job is to map qualified names to C implementations.
The collection emitters become lookup tables:
```python
_COMPILER_IMPLS = {
    "list.append": lambda self, mono: ...,
    "list.get": lambda self, mono: ...,
    "map.set": lambda self, mono: ...,
}
```

**Action items:**
- [ ] **Near-term**: Extract ZStr runtime into a C header/source file (libzrt.h)
- [ ] **Near-term**: Extract collection struct definitions into C templates
- [ ] **Medium-term**: Create libzrt.a with list/map/str core implementations
- [ ] **Long-term**: Refactor collection emitters to dispatch from native-marked methods

---

### Finding 8: Emitter _emit_call_value Still Has Large Dispatch Cascade

**File**: zemitterc.py (not read in full, but identified in previous review)
**Severity**: Medium — largest single function in the emitter

The `CallKind` enum (12 values) was added per the previous review, and the type checker classifies
calls during `_check_call()`. However, the emitter's `_emit_call_value()` still needs to handle
each kind differently. With the `native` directive, several of these would collapse:

- `PROTOCOL_CREATE` / `PROTOCOL_BORROW` / `FACET_CREATE` / `FACET_BORROW` → compiler-provided
  create/borrow methods on the protocol/facet type
- `BOX_CREATE` / `BOX_PASSTHROUGH` → compiler-provided box.create
- `TYPEDEF_CREATE` / `TYPEDEF_BORROW` → compiler-provided typedef methods

The remaining kinds (REGULAR, RECORD_CREATE, CLASS_CREATE, UNION_CREATE, CALLABLE) are genuine
structural differences that the emitter must handle.

**Action items:**
- [ ] Verify that CallKind is being used effectively in the emitter dispatch
- [ ] With native, the protocol/facet/box creation paths would become standard
  native method lookups rather than special call kinds

---

## Theme 5: Documentation and Future Directions

### Finding 9: Documentation Inconsistencies

**Severity**: Low-Medium

| Document | Issue |
|----------|-------|
| roadmap.pdoc | Still says v1 defers classes/unions/variants (all implemented) |
| compiler.pdoc | Ownership section still says "deferred to v2" for enforcement |
| spec.pdoc | Describes `enum` as a keyword but it's in the reserved word list |
| Design-OPEN.pdoc | Many proposals now implemented but not marked as resolved |
| ownership.pdoc | Comprehensive but doesn't reflect the current 2-state implementation |
| TODO.pdoc | Lists generators/iterators — no status update on what's implemented |

The spec describes many features that are fully implemented but the roadmap and Design-OPEN
documents still treat them as proposals. This creates confusion about the actual state of the
language.

**Action items:**
- [ ] Audit roadmap.pdoc: mark implemented phases, update v1/v2/v3 staging
- [ ] Audit Design-OPEN.pdoc: move resolved items to a Design-RESOLVED.pdoc or mark inline
- [ ] Update compiler.pdoc ownership section to reflect current enforcement status
- [ ] Add `enum` to the language if it's planned, or remove it from reserved words if not
- [ ] Update TODO.pdoc with implementation status for generators/iterators

---

### Finding 10: No CLAUDE.md in Zerolang Project

**File**: /home/pawe/dev/zerolang/ (missing)
**Severity**: Low — tooling convenience

The parent dev/ directory has a CLAUDE.md but the zerolang project itself does not. Adding one
would help AI-assisted development (including future self-hosting work) by documenting build
commands, architecture, and conventions specific to zerolang.

**Action items:**
- [ ] Create /home/pawe/dev/zerolang/CLAUDE.md with:
  - Build/test commands (make check, make test, make build)
  - Architecture overview (lexer → parser → typecheck → emitter)
  - Key conventions (no single underscore prefix, no Co-Authored-By)
  - Pointer to doc/ for full documentation

---

### Finding 11: Missing Unary Operator Support

**File**: system.z, ztypecheck.py, zemitterc.py
**Severity**: Medium — language expressiveness gap

The language currently has no unary minus (`-x`), bitwise NOT (`~x`), or logical NOT (`!x`).
Binary operations are declared in system.z but there is no mechanism for unary prefix operators.

For a systems language targeting zero-overhead C generation, bitwise operations and unary operators
are essential. The current workaround (`0 - x` for negation) is verbose and may not optimize
identically.

**Action items:**
- [ ] Design unary operator syntax (prefix operators? method calls like `x.negate`?)
- [ ] Add to system.z numeric type declarations
- [ ] Implement in parser, type checker, and emitter
- [ ] Add bitwise operations (AND, OR, XOR, shift) — critical for systems programming

---

### Finding 12: String Comparison Missing from system.z

**File**: lib/system/system.z
**Severity**: Low — string has no declared comparison operators

The `string: class {}` declaration in system.z has no methods at all. The emitter generates
comparison functions (`zstr_eq`) internally but these are not discoverable from the type definition.
Same issue for concatenation (`+`), length, and string interpolation support.

**Action items:**
- [ ] Add method declarations to string in system.z (requires native or similar)
- [ ] At minimum, document which string operations are supported in the spec

---

## Open Questions

These require design decisions before implementation:

### Q1: native Keyword — RESOLVED
The `native` keyword was implemented as a keyword in the body position (`is native` for functions,
`record is native` for objects). It is a reserved keyword not available for user identifiers.

### Q2: Should the Emitter's C Implementation Table Be Exhaustive?
With `native`, every system type method needs an entry in the emitter's implementation table.
Should the emitter fail on missing entries (strict) or silently skip them (permissive)?

**Recommendation**: Fail with a clear error. If system.z declares a method as `native` and
the emitter has no implementation, that is a compiler bug, not a user error.

### Q3: How Should native Interact with Generics?
A generic type like `list(of: string)` is monomorphized. When emitting `list.append`, the
emitter needs to know the concrete element type. Currently this is done via `_is_list_type()` +
`_list_element_type()`. With native, would the emitter receive the monomorphized ZType
directly?

**Recommendation**: Yes. The emitter would look up `native` methods by their
fully-qualified template name (e.g., `list.append`) and receive the monomorphized ZType as
context for code generation. The generic_args on the ZType provide element types.

### Q4: Tag and Typedef Special Types
`tag` and `typedef` are declared in system.z as `record {} as { ... }` but they are not
real records — they are compiler magic. Should they use `native` too?

**Recommendation**: Yes. `tag: record is native as { t: any.generic }` and
`typedef: record is native as { from: any.generic }` would make it clear these are compiler-provided
type constructors, not empty records.

### Q5: self-Referential Type Keyword
The `type` keyword in method signatures (e.g., `+: function {:this rhs: type} out type`) is
powerful but unusual. In self-hosting, this would need to be a first-class concept. Is the
current implementation (resolving stack lookup) sufficient, or does `type` need its own ZType?

### Q6: Enum Status
`enum` is in the reserved words list but not a keyword. The `Enum` AST node and `ZTypeType.ENUM`
exist in the compiler. What is the planned status? Should `enum` be promoted from reserved to
keyword, or is it intentionally deferred?

### Q7: Missing Language Features for Self-Hosting
To self-host, the compiler needs:
- File I/O (reading source files)
- String manipulation (tokenization, name mangling)
- Hash maps (symbol tables, caches)
- Dynamic arrays (token lists, AST node lists)
- Integer arithmetic and comparison
- Pattern matching on enum/variant tags
- Some form of error handling (the compiler currently returns Error objects)

Which of these are available today and which need implementation? The current examples show
strings, lists, maps, match/case, and records/classes are working. File I/O is declared in io.z
but appears to be just `print`. Reading files is not yet available.

---

## Step-by-Step Implementation Plan

### Phase 1: Foundation (No Language Changes)

**Goal**: Reduce duplication and hardcoding without changing the language.

1. **Extract shared type helpers** (Finding 2)
   - Move `_is_array_type()`, `_is_list_type()`, etc. to a shared module
   - Import in both ztypecheck.py and zemitterc.py
   - Estimated: 1-2 hours

2. **Extract tag resolution** (Finding 3)
   - Create shared `_resolve_tag()` method for union/variant
   - Eliminate ~130 lines of duplication
   - Estimated: 2-3 hours

3. **Convert major isinstance cascades to dispatch tables** (Finding 1)
   - Start with ztypecheck.py `_check_expression()`, `_check_statement_line()`
   - Then zemitterc.py's major emission methods
   - Estimated: 1-2 days

### Phase 2: native Directive

**Goal**: Introduce the compiler directive and expand system.z.

4. **Parser: recognize native as a function body**
   - Add `native` to the lexer as a special token (or recognize `@` + `compiler`)
   - In the parser's function body parsing, accept `native` as an alternative to a statement block
   - Store a flag on the `Function` AST node: `is_compiler_provided: bool`
   - Estimated: 2-4 hours

5. **Type checker: resolve native methods normally**
   - No special handling needed — the method has parameters and return type, just no body
   - Skip body type-checking for `native` functions (similar to specs)
   - Estimated: 1 hour

6. **Emitter: build implementation dispatch table**
   - Create a registry mapping qualified method names to C emission functions
   - For each `native`-marked function, look up the registry
   - Error if no implementation found
   - Estimated: 1 day

7. **Expand system.z with full method signatures**
   - Add all numeric operators with `native` bodies
   - Add string methods (length, ==, +, etc.)
   - Estimated: 2-4 hours

8. **Expand collections.z with full method signatures**
   - list: append, get, set, insert, remove, pop, extend, length, create
   - map: get, set, has, delete, keys, length, create
   - array: get, set, length, create
   - str: get, set, length, string, create
   - Estimated: 2-4 hours

9. **Refactor emitter collection generators**
   - Change `_emit_mono_list()` etc. to dispatch from `native` registry
   - The generated C code stays the same; only the dispatch mechanism changes
   - Estimated: 1-2 days

### Phase 3: C Runtime Extraction

**Goal**: Move generated C runtime code into a linkable library.

10. **Extract ZStr to libzrt.h**
    - Move ZStr struct, zstr_new, zstr_cat, zstr_eq, zstr_free, zstr_print to a C header
    - Emitter includes the header instead of inlining
    - Estimated: 2-4 hours

11. **Extract collection core to libzrt**
    - Move generic list/map infrastructure (resize, hash, probe) to C templates
    - Emitter generates only type-specific wrappers
    - Estimated: 1-2 days

### Phase 4: Documentation

12. **Update roadmap.pdoc** (Finding 9)
    - Mark all implemented features
    - Update v1/v2/v3 staging to reflect reality
    - Estimated: 1-2 hours

13. **Update Design-OPEN.pdoc** (Finding 9)
    - Move resolved items
    - Add native directive as a design decision
    - Estimated: 1-2 hours

14. **Create CLAUDE.md for zerolang** (Finding 10)
    - Build commands, architecture, conventions
    - Estimated: 30 minutes

15. **Update compiler.pdoc**
    - Document native directive
    - Update ownership section
    - Add monomorphization details
    - Estimated: 2-4 hours

### Phase 5: Language Extensions (Future)

16. **Unary operators** (Finding 11)
    - Design syntax, implement parser/typecheck/emitter
    - Estimated: 1-2 days

17. **Bitwise operations**
    - AND, OR, XOR, shift left/right
    - Add to system.z numeric types
    - Estimated: 1 day

18. **File I/O for self-hosting** (Q7)
    - Design file reading API
    - Implement in system.z with native
    - Estimated: 2-3 days

---

## Summary of All Action Items

| # | Finding | Priority | Effort | Status |
|---|---------|----------|--------|--------|
| 1 | Extract shared type helpers to shared module | High | Small | **Done** |
| 2 | Extract tag resolution for union/variant | Medium | Small | **Done** |
| 3 | Convert isinstance cascades to dispatch tables | High | Medium | **Done** (Phase 48d) |
| 4 | Implement native directive in parser | High | Medium | **Done** |
| 5 | native in type checker (skip body check) | High | Small | **Done** |
| 6 | native dispatch table in emitter | High | Medium | **Done** |
| 7 | Expand system.z method signatures | High | Small | **Partial** (numerics done; string has no user-callable methods yet) |
| 8 | Expand collections.z method signatures | High | Small | **Done** |
| 9 | Refactor emitter collection generators | Medium | Large | Open |
| 10 | Extract ZStr to libzrt.h | Medium | Small | Open |
| 11 | Extract collection core to libzrt | Medium | Medium | Open |
| 12 | Update roadmap.pdoc | Low | Small | **Done** |
| 13 | Update Design-OPEN.pdoc | Low | Small | Open |
| 14 | Create CLAUDE.md | Low | Tiny | **Done** |
| 15 | Update compiler.pdoc | Low | Medium | Open |
| 16 | Unary operators | Medium | Medium | Open |
| 17 | Bitwise operations | Medium | Medium | Open |
| 18 | File I/O for self-hosting | Medium | Medium | Open |

---

## No Additional Tools Required

The current toolchain (Python 3.12+, uv, ruff, ty, pytest, gcc) is sufficient for all proposed
changes. No new tools need to be installed.

---

## Previous Review Status

### codereview20260321.md — All 12 findings resolved
All action items marked [x]. Significant improvements delivered: ~30% emitter reduction, type
metadata on ZType, consolidated scope state, SQL serialization, isinstance reduction started.

### codereview20260322.md — Status mixed
- Finding 1 (mono registration break): Status unclear, needs verification
- Finding 2 (malloc NULL checks): Still open, would be resolved by Phase 3 (libzrt)
- Finding 3 (debug prints): Status unclear
- Finding 4 (RECORD/CLASS field duplication): Status unclear
- Finding 5 (dead code in lexer): Status unclear
- Finding 6 (unused _fixcalloperation): Status unclear
- Finding 7 (lexer private state access): Status unclear
- Finding 8 (zip truncation in SQL dump): Status unclear
- Finding 9 (typo): Status unclear
- Test coverage gaps: Status unclear

**Action item**: Audit codereview20260322.md action items and mark resolved/remaining.
