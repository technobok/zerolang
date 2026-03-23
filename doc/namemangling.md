# Name Mangling: Zerolang to C Identifier Mapping

## Problem

C has a flat identifier namespace. Zerolang has units (modules), types with methods,
generic types with monomorphization, and local scopes. The compiler must map all of these
into unique C identifiers.

---

## Current Scheme

### Naming conventions

All compiler-generated C identifiers use a `z_` prefix to avoid collisions with C standard
library names and user-defined C code (when linking with external C).

| Zerolang entity | C pattern | Example |
|-----------------|-----------|---------|
| Record type | `z_{name}_t` | `point` → `z_point_t` |
| Class type | `z_{name}_t` (used as pointer) | `calculator` → `z_calculator_t*` |
| Union type | `z_{name}_t` (used as pointer) | `shape` → `z_shape_t*` |
| Variant type | `z_{name}_t` | `color` → `z_color_t` |
| Protocol type | `z_{name}_t` (used as pointer) | `printable` → `z_printable_t*` |
| Facet type | `z_{name}_t` | `hashable` → `z_hashable_t` |
| Function | `z_{name}` | `add` → `z_add` |
| main | `z_main` | (called from generated `main()`) |
| Qualified function | `z_{unit}_{name}` | `math.add` → `z_math_add` |
| Method | `z_{type}_{method}` | `point.distance` → `z_point_distance` |
| Constructor | `z_{name}_meta_create` | `point` → `z_point_meta_create` |
| Destructor | `z_{name}_destroy` | `node` → `z_node_destroy` |
| Generic instance | `z_{name}_{args}_t` | `list[of i64]` → `z_list_i64_t` |
| Generic method | `z_{name}_{args}_{method}` | `list[of i64].append` → `z_list_i64_append` |
| Protocol vtable | `z_{proto}_vtable_t` | `printable` → `z_printable_vtable_t` |
| Protocol wrapper | `z_{impl}_{label}_{method}_wrapper` | (see below) |
| Protocol create | `z_{impl}_{label}_create` | (see below) |

### Mangling functions

Three functions in `src/zemitterc.py` handle all name generation:

**`_ctype(ztype)` (line 147)** — converts a `ZType` to its C type string. Handles the
`z_{name}_t` pattern for all type kinds, plus primitive type mappings (`i64` → `int64_t`,
`f64` → `double`, etc.).

**`_mangle_func(name)` (line 189)** — converts a zerolang qualified function name to a C
identifier. Replaces dots with underscores and adds the `z_` prefix:
```python
return "z_" + name.replace(".", "_")
```

**`_mangle_var(name)` (line 196)** — handles local variable names. Most pass through
unchanged. C reserved words get a `v_` prefix (`return` → `v_return`, `int` → `v_int`).

### Monomorphization naming

Generic types are mangled during type checking (`src/ztypecheck.py:2096-2098`):
```python
arg_names = [generic_args[k].name for k in template_type.generic_params]
mangled = f"{template_type.name}_{'_'.join(arg_names)}"
```

The mangled name is stored in `ZType.name` and used by the emitter directly. This is the
only case where the C-facing name is computed once and stored.

### Emitter temporaries

The emitter generates temporary variables with prefixed counters, scoped per function:

| Prefix | Purpose | Example |
|--------|---------|---------|
| `_t` | String concatenation temps | `_t1`, `_t2` |
| `_a` | Argument evaluation temps | `_a1`, `_a2` |
| `_c` | Class construction temps | `_c1`, `_c2` |
| `_p` | Protocol creation temps | `_p1`, `_p2` |
| `_zs` | Static string literals | `_zs1`, `_zs2` |
| `_r` | Return value temps | `_r` |

Counters reset per function via `ScopeState.temp_counter`.

---

## Current Scheme vs Tagged Separators

### Tagged separator alternative

A tagged scheme uses different separator tokens for different namespace boundaries:

| Boundary | Tag | Example |
|----------|-----|---------|
| Module | `_m_` | `math.add` → `z_math_m_add` |
| Generic arg | `_g_` | `list[of i64]` → `z_list_g_i64_t` |
| Method | `_f_` | `point.distance` → `z_point_f_distance` |

**Advantage**: structurally impossible to have collisions. The tag encodes which namespace
boundary produced each underscore, so `foo.bar` (`z_foo_m_bar`) can never collide with
`foo_bar` (`z_foo_bar`). The mangling is also reversible — given a C identifier, you can
reconstruct the original zerolang path.

**Disadvantage**: longer identifiers, harder to read in debugger output and generated C.
For a zerolang programmer who lives in zerolang source code rather than reading generated C,
this readability cost matters little. But for compiler developers debugging the emitter,
shorter names are easier to work with.

### Why keep the current scheme

The current scheme is simpler and produces shorter, more readable C identifiers. Collisions
are rare in practice — they require a deliberate naming coincidence like defining both
`foo.bar` and `foo_bar` at the same level. Rather than preventing collisions structurally
(which costs readability), we detect them at assignment time and auto-resolve. This gives
the best of both worlds: clean names in the common case, guaranteed correctness in all cases.

---

## Collision Analysis

### Class 1: Dot-to-underscore ambiguity

`_mangle_func()` replaces `.` with `_`. Two different zerolang paths can produce the same
C identifier:

- `foo.bar` → `z_foo_bar`
- `foo_bar` (flat name) → `z_foo_bar`

This affects functions, methods, and type names.

### Class 2: Generic argument ambiguity

Generic mangling joins template name and arguments with `_`:

- `list[of some_type]` → `z_list_some_type_t`
- `list_some[of type]` → `z_list_some_type_t`

Nested generics compound this: `map[of str, list[of i64]]` → `z_map_str_list_i64_t`
which is ambiguous about where template boundaries fall.

### Class 3: Emitter temporaries vs user locals

A user variable named `_t1` collides with the emitter's first string temporary. The
`_mangle_var()` function only escapes C reserved words, not emitter-internal names.

### Class 4: Method vs function ambiguity

A method `type.foo` and a function in unit `type` named `foo` both produce `z_type_foo`.
The type checker resolves this because methods and unit functions occupy different semantic
spaces, but the C identifiers collide.

### Class 5: Constructor/destructor suffix collisions

If a type is named `foo_meta_create` or `foo_destroy`, its constructor/destructor names
would collide with themselves. This is extremely unlikely but structurally possible.

---

## Implementation Roadmap

### Phase 1: Add `cname` to ZType

Add a `cname` field to `ZType` in `src/ztypes.py` so that every type carries its
C identifier. Populate it during type checking when the type is first resolved.

- [x] Add `cname: str = ""` field to the `ZType` dataclass in `src/ztypes.py`
- [x] In type checking (`src/ztypecheck.py`), after resolving each type definition
  (record, class, union, variant, protocol, facet), compute and assign `cname` using
  the existing `z_{name}_t` pattern
- [x] For monomorphized types, the mangled name is already stored in `ZType.name` —
  compute `cname` from it at the end of `_monomorphize()`
- [x] For function types, assign `cname` using the `z_{qualified_name}` pattern
  (same as `_mangle_func()` currently produces)

### Phase 2: Inline collision detection with auto-resolve

Maintain a `set[str]` of assigned cnames in the type checker. When assigning a `cname`,
check for collision and auto-resolve by appending the definition's existing integer ID.

- [x] Add `self._assigned_cnames: set[str] = set()` to `TypeChecker.__init__`
- [x] Create a helper method `_assign_cname(ztype: ZType, base_cname: str)` that:
  1. If `base_cname` is not in `_assigned_cnames`, set `ztype.cname = base_cname`
  2. If collision, set `ztype.cname = f"{base_cname}_{ztype.nodeid}"` (using the type's
     existing integer ID, which is guaranteed unique)
  3. Add the final `cname` to `_assigned_cnames`
- [x] Call `_assign_cname()` everywhere a type's cname is first computed (Phase 1 sites)
- [x] For function-level cnames, use the function's `NodeID` for disambiguation

### Phase 3: Update emitter to use `cname`

Replace on-the-fly mangling in the emitter with reads from the pre-computed `cname`.

- [x] Replace `_ctype()` calls that build `z_{name}_t` with reads from `ztype.cname`
  (keep `_ctype()` as a fallback for primitive types which don't have `cname`)
- [x] Replace `_mangle_func(name)` calls with reads from the function's type `cname`
  where available
- [x] Remove the name-building logic from `_ctype()` for user-defined types — it
  should just return `ztype.cname` (with `*` suffix for pointer types)

### Phase 4: NodeID-scoped emitter temporaries

Include the function's NodeID in temporary variable names so they are globally unique
and traceable.

- [x] Change temporary naming from `_t{counter}` to `_t{nodeid}_{counter}` where
  `nodeid` is the enclosing function's NodeID. Same for `_a`, `_c`, `_p` prefixes
- [x] ~~Change static string literal naming~~ — not applicable: `_zs` literals are
  global (file-scope `ZSTR_STATIC`), deduplicated across functions, so per-function
  nodeid scoping would prevent deduplication
- [x] The `ScopeState` already tracks `temp_counter` — add the function's NodeID to
  `ScopeState` (or pass it through) so temporaries can include it
- [x] This eliminates Class 3 collisions entirely — no user identifier starts with
  `_t{number}_` and even if it did, the NodeID makes each temp globally unique

### Phase 5: Add `cname` to SQL dump

Extend the SQL schema to include the C identifier for diagnostic queries.

- [x] Add `cname TEXT` column to the `types` table in `src/zsqldump.py`
- [x] Add `cname TEXT` column to the `ast_nodes` table for function definitions
- [x] Populate from `ztype.cname`

### Phase 6: Update tests

- [x] Add test: two types whose names collide after mangling are auto-resolved
  (unit function `m.f` vs top-level `m_f` — both mangle to `z_m_f`, collision auto-resolved)
- [x] Add test: monomorphized generic collision is auto-resolved
  (generic `box[of i64]` → `box_i64` vs plain `box_i64` record — collision auto-resolved)
- [x] Add test: emitter temporaries include NodeID
- [x] Add test: `cname` appears in SQL dump output
- [x] Verify all existing tests still pass (962 tests pass)

---

## Verification

After implementation:

```bash
# All tests pass
uv run python -m pytest tests/ -x -v

# Compile all examples and check for duplicate C identifiers
for ex in examples/*.z; do
    name=$(basename "$ex" .z)
    uv run python src/zc.py "$name" --src examples 2>/dev/null
    if [ -f "${name}.c" ]; then
        # Check no duplicate function/type definitions
        grep -oP 'z_\w+' "${name}.c" | sort | uniq -d
    fi
done

# SQL dump includes cname
uv run python src/zc.py hello --src examples --dump-sql - | grep cname
```
