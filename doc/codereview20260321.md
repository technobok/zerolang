# Zerolang Compiler Code Review

## Original Instruction

> Do a code review of the codebase. There is too much hardcoded `if isinstance` / `if type ==` dispatching instead of embedding knowledge in the structures themselves. There is too much semantic/compilation logic in the emitter (`zemitterc.py`, 4666 lines) — the type system should do the bulk of this work. Future goals: self-hosting (rewrite compiler in zerolang), SQL dump of compiler state for analysis. Code should be simple and straightforward for future conversion.

---

## Executive Summary

The zerolang compiler has three core architectural problems:

1. **The emitter duplicates the type checker.** `zemitterc.py` (4666 lines) maintains 11+ name-classification sets, 6 parallel type-resolution functions, and 40+ `getattr(…, "type", None)` fallbacks — all re-deriving information the type checker already computed and attached to AST nodes.

2. **Memory management knowledge lives in the emitter, not the type system.** Destructor generation, scope-exit cleanup, reassignment cleanup, and return cleanup are each hand-coded with `if name == "string" → zstr_free, elif name in _class_names → z_X_destroy` cascades scattered across 6+ call sites. The type system already has `is_valtype`, `ZOwnership`, and `ZTypeType` — it should also carry `needs_destructor`, `destructor_name`, and `is_heap_allocated`.

3. **`isinstance` cascades replace polymorphism.** At least 8 major cascades (15–20 branches each) walk AST node types to dispatch behavior. This logic should be driven by type metadata or visitor patterns, not runtime type checks.

Fixing these three problems would reduce `zemitterc.py` by an estimated 30–40%, make the code straightforward enough to port to zerolang for self-hosting, and create clean stage boundaries suitable for SQL serialization.

---

## Detailed Findings

### Finding 1: Duplicated Type Resolution Logic (Critical)

The emitter has **6 separate type-resolution functions** that duplicate the type checker's work:

| Function | File:Line | Lines | Purpose |
|----------|-----------|-------|---------|
| `_ctype()` | `zemitterc.py:127` | 26 | Top-level ZType → C type string |
| `_resolve_param_ctype()` | `zemitterc.py:2534` | 47 | Parameter type resolution |
| `_resolve_return_ctype()` | `zemitterc.py:2582` | 41 | Return type resolution |
| `_resolve_field_ctype()` | `zemitterc.py:1169` | 29 | Struct/class field type resolution |
| `_resolve_typedef_ctype()` | `zemitterc.py:401` | 17 | Typedef chain resolution |
| `_get_subtype_ctype()` | `zemitterc.py:4257` | 27 | Union subtype resolution |

**The problem:** `_ctype()` at line 127 already handles all type resolution correctly when given a `ZType`. The other 5 functions exist because the emitter doesn't trust that AST nodes have `.type` annotations. Each one follows the same pattern:

```python
# Pattern repeated 5 times with minor variations:
if hasattr(fpath, "type") and fpath.type:
    return _ctype(fpath.type)           # ← happy path: use type checker's work
# ... 15-20 lines of fallback isinstance/name-set cascades ...
```

For example, `_resolve_field_ctype()` at line 1169:

```python
def _resolve_field_ctype(self, fpath: zast.Path) -> str:
    ftype = _ctype(fpath.type if hasattr(fpath, "type") else None)
    if ftype == "void" and isinstance(fpath, zast.DottedPath):
        # ... numeric literal fallback ...
    if ftype == "void" and isinstance(fpath, zast.AtomId):
        fname = fpath.name
        if _is_numeric_id(fname): return "int64_t"
        if fname == "string": return "ZStr*"
        if fname in self._class_names: return f"z_{fname}_t*"
        if fname in self._union_names: return f"z_{fname}_t*"
        if fname in self._variant_names: return f"z_{fname}_t"
        if fname in self._record_names: return f"z_{fname}_t"
        if fname in self._spec_names: ...
        if fname in self._protocol_names: ...
    return ftype
```

Compare with `_resolve_param_ctype()` at line 2534 — nearly identical cascade with minor additions for `this`/`type` keywords and facets.

**Target state:** All 5 fallback functions should be eliminated. Every AST node should have a `.type` annotation after the type checker runs. The emitter should only ever call `_ctype(node.type)`.

#### Action Items

- [x] Audit the type checker to find which AST nodes are not getting `.type` annotations
- [x] Ensure the type checker annotates every `Path` node (parameters, return types, field types, union subtypes)
- [x] Remove `_resolve_param_ctype()`, replace all call sites with `_ctype(ppath.type)`
- [x] Remove `_resolve_return_ctype()`, replace all call sites with `_ctype(func.returntype.type)`
- [x] Remove `_resolve_field_ctype()`, replace all call sites with `_ctype(fpath.type)`
- [x] Remove `_resolve_typedef_ctype()` — typedef resolution should be handled by `_ctype()` via `ZType.typedef_base`
- [x] Remove `_get_subtype_ctype()`, replace with `_ctype(subtype_path.type)`
- [x] Remove all `hasattr(…, "type")` and `getattr(…, "type", None)` fallback patterns (41 occurrences in zemitterc.py)

---

### Finding 2: Emitter Maintains Parallel Symbol Tables (Critical)

`CEmitter.__init__` (lines 237–298) creates 11 name-classification sets:

```python
self._func_names: set[str] = set()        # line 252
self._data_names: set[str] = set()        # line 253
self._const_names: set[str] = set()       # line 254
self._record_names: set[str] = set()      # line 255
self._class_names: set[str] = set()       # line 256
self._union_names: set[str] = set()       # line 257
self._variant_names: set[str] = set()     # line 258
self._unit_names: set[str] = set()        # line 259
self._spec_names: set[str] = set()        # line 260
self._protocol_names: set[str] = set()    # line 261
self._facet_names: set[str] = set()       # line 263
```

These are populated by `_collect_unit_names()` (lines 419–467), a 49-line function with 15 `isinstance` checks that re-walks the entire AST. This is a complete re-derivation of information the type checker already computed: `TypeChecker._resolved` (line 142 in `ztypecheck.py`) maps qualified names to `ZType`, and `ZType.typetype` is exactly the `ZTypeType` enum (`RECORD`, `CLASS`, `UNION`, etc.) that classifies every definition.

**Why this matters for self-hosting:** These sets create implicit coupling — the emitter consults `self._class_names` to decide whether a field needs a pointer, a destructor, or heap allocation. When porting to zerolang, these would need to be hash sets of strings, duplicating the type table. Instead, the emitter should query the type directly: `ftype.typetype == ZTypeType.CLASS`.

#### Action Items

- [x] Replace all `name in self._class_names` checks with `resolved_type.typetype == ZTypeType.CLASS`
- [x] Replace all `name in self._union_names` checks with `resolved_type.typetype == ZTypeType.UNION`
- [x] Do the same for `_record_names`, `_variant_names`, `_protocol_names`, `_facet_names`, `_spec_names`, `_func_names`
- [x] Remove `_collect_unit_names()` entirely — reduced to collecting only `_const_names`, `_protocol_defs`, `_facet_defs`, `_is_func_fields`
- [x] Remove all 11 name sets from `__init__` — removed 10 of 11; kept `_const_names` (no distinct ZTypeType for numeric constants)
- [x] Keep `_typedef_base` only if needed; prefer `ZType.typedef_base` (already exists at `ztypechecker.py:163`) — removed `_typedef_base`, using `ZType.typedef_base`
- [x] Keep `_protocol_defs` and `_facet_defs` only if AST node references are genuinely needed beyond what ZType provides — kept, AST nodes needed for emission

---

### Finding 3: Destructor Logic Duplicated and Scattered (High)

Destructor generation code appears in **6 separate locations**, each implementing the same pattern:

| Location | File:Line | Context |
|----------|-----------|---------|
| `_emit_class()` | `zemitterc.py:1370–1389` | Class destructor |
| `_emit_union()` | `zemitterc.py:1476–1499` | Union destructor |
| `_emit_mono_union()` | `zemitterc.py:1575–1601` | Monomorphized union destructor |
| `_emit_mono_class()` | `zemitterc.py:2305–2316` | Monomorphized class destructor |
| `_emit_mono_list()` | `zemitterc.py:1797–1820` | List destructor |
| Protocol boxed_destroy | `zemitterc.py:949–971` | Protocol impl record destructor |

Each site repeats the same decision cascade:

```python
# Pattern repeated 6 times:
if ftype_name == "string":
    lines.append(f"    zstr_free(p->{fname});\n")
elif ftype_name in self._class_names:
    lines.append(f"    z_{ftype_name}_destroy(p->{fname});\n")
elif ftype_name in self._union_names:
    lines.append(f"    z_{ftype_name}_destroy(p->{fname});\n")
else:
    lines.append(f"    free(p->data);\n")
```

Some sites also check `hasattr(fpath, "type")` with fallback to `isinstance(fpath, zast.AtomId)` (class destructor at line 1370, protocol boxed_destroy at line 952).

**Target state:** A single function `_emit_field_cleanup(field_name: str, field_type: ZType) -> str` that returns the appropriate cleanup code. Better still, `ZType` itself should carry `destructor_name` so the emitter just calls `type.destructor_name`.

#### Action Items

- [x] Add `needs_destructor: bool` property to `ZType` (True for string, class, union, protocol; False for records, numerics, variants)
- [x] Add `destructor_name: Optional[str]` property to `ZType` (e.g., `"zstr_free"`, `"z_foo_destroy"`, `None`)
- [x] Add `is_heap_allocated: bool` property to `ZType` (True for class, union, protocol, string)
- [x] Create `_emit_field_cleanup(fname: str, ftype: ZType) -> str` that uses these properties
- [x] Replace all 6 destructor sites with calls to `_emit_field_cleanup()` — also replaced map free helpers and reassignment cleanup
- [x] Verify edge cases: nullable fields, recursive types, protocol boxed values — all 882 tests pass

---

### Finding 4: Memory Management Strategy Not in Type System (High)

Beyond destructors, the emitter decides at emit time:

**Reassignment cleanup** (`_emit_reassignment`, lines 2837–2858):
```python
lhs_type = getattr(reassign.topath, "type", None)
if lhs_type and lhs_type.name == "string":
    result += f"{indent}zstr_free({lhs});\n"
elif lhs_type and lhs_type.typetype == ZTypeType.CLASS:
    result += f"{indent}z_{lhs_type.name}_destroy({lhs});\n"
elif lhs_type and lhs_type.typetype == ZTypeType.UNION:
    result += f"{indent}z_{lhs_type.name}_destroy({lhs});\n"
```

**Scope-exit cleanup** (lines 2699–2724):
```python
for sv in reversed(self._func_protocol_vars):
    # ... destroy protocol ...
for sv in reversed(self._func_union_vars):
    # ... destroy union ...
for sv in reversed(self._func_class_vars):
    # ... destroy class ...
for sv in reversed(self._func_string_vars):
    lines.append(f"{indent}zstr_free({sv});\n")
```

**Return cleanup** (lines 3265–3333) — 69 lines of cleanup before return, duplicating scope-exit logic with "except the return value" filtering.

**`.take` nullification** (lines 2831–2834, 2980, 3212, 3218, 3733, 3760):
```python
if child == "take":
    return self._emit_path_value(path.parent)
```

The type system already has `is_valtype` (`ztypechecker.py:138`), `ZOwnership` (lines 41–50), and `ZTypeType`. It should also provide the cleanup strategy.

#### Action Items

- [x] Add `ZType.needs_destructor` / `ZType.destructor_name` / `ZType.is_heap_allocated` (see Finding 3) — done in Finding 3
- [x] Unify scope-exit and return cleanup into a single `_emit_scope_cleanup(exclude_var: Optional[str] = None) -> str`
- [x] Unify reassignment cleanup to use the same `_emit_field_cleanup()` from Finding 3 — done in Finding 3
- [x] Consider whether `.take` nullification should be a type-system concern (ownership transfer annotation) — `.take` is already modeled via `ZParamOwnership.TAKE`; emitter nullification is the C-level implementation of that annotation, appropriate to keep in the emitter

---

### Finding 5: Massive `isinstance` Cascades (High)

Major cascades in the emitter:

| Function | File:Line | Lines | Branches |
|----------|-----------|-------|----------|
| `_collect_unit_names()` | `zemitterc.py:419` | 49 | 15 `isinstance` |
| `_emit_unit_definitions()` | `zemitterc.py:489` | 33 | 14 `isinstance` |
| `_emit_call_value()` | `zemitterc.py:3505` | ~290 | 20+ type checks |
| `_emit_dotted_path_value()` | `zemitterc.py:3895` | ~190 | 15+ type checks |
| `_emit_call_stmt()` | `zemitterc.py:3113` | ~120 | 10+ type checks |

In the type checker:

| Function | File:Line | Branches |
|----------|-----------|----------|
| `_type_of_definition()` | `ztypecheck.py:293` | 13 `isinstance` |

**`_collect_unit_names()` and `_emit_unit_definitions()` are structurally identical** — they walk the same AST in the same order, one collecting names, the other emitting code. They would collapse if the emitter didn't need pre-collected name sets (see Finding 2).

**`_emit_call_value()` (290 lines)** is the most complex cascade. It checks for protocol creates, facet creates, meta creates, list/map/array constructors, string methods, union constructors, data constructors, and finally regular function calls. Much of this could be simplified if the type checker attached a `call_kind` annotation (e.g., `PROTOCOL_CREATE`, `META_CREATE`, `REGULAR`) to `Call` nodes.

#### Action Items

- [x] Remove `_collect_unit_names()` entirely (see Finding 2) — merged with `_collect_proto_conformance` into single `_collect_pre_emission` pass
- [x] Simplify `_emit_unit_definitions()` — converted `isinstance` checks to `type()` equality checks; same for `_collect_pre_emission` and `_emit_deferred_facets`
- [ ] Consider adding a `call_kind` enum to typed `Call` nodes to simplify `_emit_call_value()` — deferred; requires annotating all Call nodes in the type checker
- [x] Consider using a dispatch table pattern: `{ZTypeType.RECORD: self._emit_record, ZTypeType.CLASS: self._emit_class, ...}` — used dispatch table for `_type_of_definition()` in type checker
- [x] For `_type_of_definition()` in the type checker: this cascade is more defensible since it maps AST→ZType, but could use a registry pattern — converted to dispatch table for the 8 structured types

---

### Finding 6: Constructor/Create Logic Duplicated (Medium)

`_emit_meta_create_record()` (lines 1199–1267, 69 lines) and `_emit_meta_create_class()` (lines 1269–1338, 70 lines) are **nearly identical**, differing only in:

| Aspect | Record | Class |
|--------|--------|-------|
| Allocation | `{0}` (stack) | `malloc` (heap) |
| Return type | `z_X_t` (value) | `z_X_t*` (pointer) |
| Field access | `_this.field` | `_this->field` |

Both functions:
1. Iterate `cls.items` / `rec.items` to collect field types (identical)
2. Iterate `cls.functions` / `rec.functions` for function pointer fields (identical)
3. Extract field defaults with identical `isinstance` cascades (lines 1226–1246 ≈ 1298–1318)
4. Emit `z_X_meta_create()` and optionally `z_X_create()` forwarding functions

The monomorphized versions (`_emit_mono_class_create`, etc.) add further copies.

#### Action Items

- [ ] Extract shared logic into `_emit_meta_create_common(name, items, functions, is_heap: bool) -> str`
- [ ] Pass `is_heap_allocated` from the type (see Finding 3) to select stack vs heap
- [ ] Deduplicate the field-default extraction into a shared helper
- [ ] Apply the same refactor to mono variants

---

### Finding 7: No Integer IDs Linking Compiler Stages (Medium)

For the SQL dump goal, each compiler stage's output needs integer IDs that cross-reference the previous stage:

**Current state:**
- `TypeID` exists (`ztypechecker.py:94`) — `NewType("TypeID", int)`, auto-assigned via `count().__next__` at `ZType.nodeid` (line 119)
- `VariableID` exists (`ztypechecker.py:167`) — same pattern at `ZVariable.variableid` (line 189)
- `NodeID` exists in `zvfs.py:17` — but this is the VFS inode ID, not an AST node ID
- **No AST NodeID** — AST nodes (`zast.py`) have no integer IDs, only file/line/col start positions
- **No Token IDs** — tokens carry file/line/col but no sequential integer ID
- **No emitter back-references** — emitted C code has no link back to typed AST nodes

**What's needed for SQL serialization:**

```
Token(token_id, file_id, line, col, kind, text)
  ↓ parser
ASTNode(node_id, kind, parent_node_id, token_id, ...)
  ↓ type checker
TypedNode(node_id, type_id, ownership, ...)
  ↓ emitter
EmittedLine(line_id, node_id, c_line_number, c_text)
```

#### Action Items

- [ ] Add `node_id: int` to AST node base class (auto-incrementing)
- [ ] Add `token_id: int` to Token class (sequential within file, or global)
- [ ] Link AST nodes to their originating token via `token_id`
- [ ] Add `file_id: int` to VFS (expose `ProviderID` or `DEntryID` as the file ID)
- [ ] Track emitter output lines → source AST node IDs for debuggability
- [ ] Design SQL tables (see Schema Design section below)

---

### Finding 8: VFS Has No Exposed File Integer IDs (Low)

`zvfs.py` (761 lines) uses `ProviderID` (line 16) and `DEntryID` (line 15) internally, both `NewType("...", int)`. However:

- Provider registration (`ZVfs.register()`, line 271) assigns sequential ProviderIDs but this mapping isn't exposed in a SQL-friendly way
- Tokens reference files by string path, not integer ID
- No API to enumerate all registered files with their integer IDs

#### Action Items

- [ ] Add a method `zvfs.file_table() -> List[Tuple[int, str]]` returning `(file_id, path)` pairs
- [ ] Store `file_id` in tokens instead of (or alongside) the string path
- [ ] Ensure `file_id` is used consistently through parser → type checker → emitter

---

### Finding 9: Monomorphized Type Emission is Sprawling (Medium)

The emitter contains complete standalone C library generators for generic container types:

| Function | File:Line | Lines | Generates |
|----------|-----------|-------|-----------|
| `_emit_mono_array()` | `zemitterc.py:1650` | ~75 | Fixed-size array struct + create + destroy |
| `_emit_mono_str()` | `zemitterc.py:1726` | ~48 | String operations |
| `_emit_mono_list()` | `zemitterc.py:1775` | ~160 | Dynamic list + push/pop/get/set/destroy/create |
| `_emit_mono_map()` | `zemitterc.py:1946` | ~330 | Hash map + get/set/has/delete/keys/destroy |

Each of these is a hand-crafted C code generator with inlined string templates. The list destructor (lines 1797–1820) and map destructor repeat the same `if string → zstr_free, elif class → z_X_destroy, else free` pattern from Finding 3.

**Target state:** These should either be:
1. Driven by type metadata — a single `_emit_container_type(container_kind, element_type)` function
2. Or generated from C templates with `{{element_ctype}}`, `{{element_destructor}}` placeholders

#### Action Items

- [ ] Extract the common container patterns (create, destroy, struct typedef) into shared helpers
- [ ] Use `ZType.destructor_name` from Finding 3 to eliminate inline cleanup cascades
- [ ] Consider a template-based approach for the largest generators (map: 330 lines)
- [ ] Deduplicate list/array create patterns (both are `malloc + zero-init`)

---

### Finding 10: `hasattr`/`getattr` Pattern Indicates Incomplete Type Annotation (Medium)

There are **41 occurrences** of `getattr(…, "type", None)` in `zemitterc.py`. A sampling:

```
Line 346:  getattr(call.callable, "type", None)
Line 1171: fpath.type if hasattr(fpath, "type") else None
Line 2535: hasattr(ppath, "type") and ppath.type
Line 2585: hasattr(func.returntype, "type") and func.returntype.type
Line 2843: getattr(reassign.topath, "type", None)
Line 3613: getattr(call.callable, "type", None)
Line 4262: hasattr(subtype_path, "type") and subtype_path.type
```

This pattern means the type checker doesn't consistently annotate all AST nodes. The emitter then has ~200 lines of fallback resolution logic (the 5 extra resolution functions from Finding 1).

**Root cause:** The type checker annotates nodes as it resolves them, but some paths (field type declarations, parameter type declarations, union subtype declarations) may not go through the annotation path.

#### Action Items

- [ ] Add a post-type-check validation pass that asserts every `Path` node has a `.type` attribute
- [ ] Fix the type checker to annotate all missing nodes
- [ ] Once all nodes are annotated, remove all `hasattr`/`getattr` fallbacks
- [ ] Remove the 5 fallback resolution functions (Finding 1)
- [ ] Consider making `.type` a required field on AST Path nodes (default `None`, assert not-None after type checking)

---

### Finding 11: Scope Cleanup State Management (Medium)

The emitter tracks per-function cleanup state in **12+ instance variables** (lines 281–292):

```python
self._temp_counter: int = 0
self._temp_decls: List[str] = []
self._temp_frees: List[str] = []
self._temp_string_set: set[str] = set()
self._func_string_vars: List[str] = []
self._func_class_vars: List[str] = []
self._func_union_vars: List[str] = []
self._func_protocol_vars: List[str] = []
self._union_var_types: Dict[str, str] = {}
self._class_var_types: Dict[str, str] = {}
self._protocol_var_types: Dict[str, str] = {}
self._temp_class_set: Dict[str, str] = {}
```

These are manually saved and restored at function boundaries (lines 2660–2682 save, lines 2730–2739 restore). This is error-prone and adds 20 lines of boilerplate per function emission.

**Target state:** A `ScopeState` dataclass:

```python
@dataclass
class ScopeState:
    string_vars: List[str] = field(default_factory=list)
    class_vars: List[str] = field(default_factory=list)
    union_vars: List[str] = field(default_factory=list)
    protocol_vars: List[str] = field(default_factory=list)
    union_var_types: Dict[str, str] = field(default_factory=dict)
    class_var_types: Dict[str, str] = field(default_factory=dict)
    protocol_var_types: Dict[str, str] = field(default_factory=dict)
    temp_class_set: Dict[str, str] = field(default_factory=dict)
    temp_counter: int = 0
```

Then save/restore becomes:
```python
saved = self._scope
self._scope = ScopeState()
# ... emit function body ...
self._scope = saved
```

Better yet, use a stack: `self._scope_stack.append(ScopeState())` / `self._scope_stack.pop()`.

#### Action Items

- [ ] Create a `ScopeState` dataclass grouping all per-function cleanup state
- [ ] Replace the 12 instance variables with a single `self._scope: ScopeState`
- [ ] Replace save/restore blocks with push/pop on a scope stack
- [ ] Simplify the cleanup emission to iterate `self._scope.all_vars()` with type-driven cleanup

---

### Finding 12: Python-Specific Patterns That Complicate Self-Hosting (Low)

Patterns in `ztypechecker.py` that would be difficult to express in zerolang:

| Pattern | Location | Replacement |
|---------|----------|-------------|
| `count().__next__` for auto-IDs | `ztypechecker.py:120, 190` | Explicit module-level counter variable |
| `OrderedDict` | `ztypechecker.py:127, 144, 152` | List of `(key, value)` pairs or zerolang's built-in map |
| `dataclass` with `field(default_factory=…)` | `ztypechecker.py:97–164` | Explicit constructor with initialization |
| `NewType` | `ztypechecker.py:94, 167` | Plain `int` with naming convention (e.g., `type_id: int`) |
| `threading.Lock` in TypeTable | `ztypechecker.py:201+` | Remove — compiler is single-threaded |
| `cast(Callable[[], TypeID], …)` | `ztypechecker.py:120` | Direct counter increment |

These aren't bugs but they add translation friction. Each pattern should be replaced with the simplest equivalent before self-hosting.

#### Action Items

- [ ] Replace `count().__next__` with an explicit `_next_type_id: int` module variable and `_alloc_type_id()` function
- [ ] Evaluate whether `OrderedDict` insertion order matters (Python 3.7+ dicts are ordered) — if so, use plain `dict`; if key ordering matters, use `list[tuple]`
- [ ] Remove `threading.Lock` from `TypeTable` (single-threaded compiler)
- [ ] Replace `NewType` with plain `int` aliases with naming convention
- [ ] Add a comment noting each replacement for future reference

---

## Documentation Review

### Mismatches Between Documentation and Implementation

#### 1. roadmap.pdoc: v1 scope is outdated
**roadmap.pdoc lines 65–77** say v1 defers classes, unions, variants, protocols, facets, generics, and typedefs to v2/v3. The implementation already has **all of these** fully working. The roadmap's v1/v2/v3 staging no longer reflects reality.

- [ ] Update roadmap.pdoc to reflect which features are actually implemented
- [ ] Either remove the v1/v2/v3 staging or mark completed items

#### 2. roadmap.pdoc: ZStr struct definition is outdated
**roadmap.pdoc lines 92–97** show:
```c
typedef struct {
    int32_t len;    // byte length (excl NUL)
    char data[];    // NUL-terminated UTF-8
} ZStr;
```

The actual implementation (`zemitterc.py:676–682`) uses:
```c
typedef struct {
    uint64_t size;     /* bits 62-0: byte count; bit 63: static flag */
    char data[];       /* NUL-terminated, starts at 8-byte boundary */
} ZStr;
```

This is a significant difference: `uint64_t` vs `int32_t`, field named `size` vs `len`, and the static flag mechanism.

- [ ] Update roadmap.pdoc ZStr definition to match implementation

#### 3. compiler.pdoc: Placeholder text
**compiler.pdoc line 103**: `xxx The Parser maintains an Environment stack...` — contains `xxx` placeholder indicating unfinished text.

- [ ] Remove `xxx` placeholder and finalize the Parser section

#### 4. compiler.pdoc: Ownership model is outdated
**compiler.pdoc line 142**: `The ownership status of each expression is also determined (free, owned, borrowed, linked).`

The implementation has a **2-state model** (`ztypechecker.py:41–50`):
```python
class ZOwnership(IntEnum):
    OWNED = 0
    BORROWED = 1
```

There is no `free` or `linked` state.

- [ ] Update compiler.pdoc ownership section to describe the 2-state OWNED/BORROWED model

#### 5. compiler.pdoc: Missing major features
The compiler.pdoc has **no mention** of:
- Monomorphization and generic type resolution (only briefly touched in the Code Deduplication subsection)
- Demand-driven type checking (`TypeChecker` is demand-driven, starting from `main`)
- The emitter's memory management (ZStr lifecycle, destructors, scope cleanup)
- Lock checking (`ZLockState` enum exists in `ztypechecker.py:54–65`)

- [ ] Add a section on monomorphization (how generic types are instantiated)
- [ ] Add a section on demand-driven type checking
- [ ] Expand the Code Generator section substantially (currently 3 sentences at lines 150–156 for 4666 lines of code)
- [ ] Document memory management strategy (destructors, scope cleanup, .take ownership transfer)

#### 6. compiler.pdoc: No mention of lock checking
`ZLockState` and `ZParamOwnership` enums exist in `ztypechecker.py` (lines 54–80). The roadmap says lock checking is deferred to v3, but partial infrastructure exists.

- [ ] Document current lock checking status (infrastructure exists, enforcement scope TBD)

---

## SQL Schema Design

For the goal of dumping compiler state to SQL for analysis, here is a complete schema design showing how integer IDs flow through each stage.

### Stage 1: VFS / Source Files

```sql
CREATE TABLE files (
    file_id     INTEGER PRIMARY KEY,  -- from ProviderID or DEntryID
    path        TEXT NOT NULL,         -- virtual path in VFS
    provider    TEXT,                  -- provider type (filesystem, memory, etc.)
    content     TEXT                   -- full source text (optional, for completeness)
);
```

### Stage 2: Tokens

```sql
CREATE TABLE tokens (
    token_id    INTEGER PRIMARY KEY,  -- sequential, global
    file_id     INTEGER NOT NULL REFERENCES files(file_id),
    line        INTEGER NOT NULL,
    col         INTEGER NOT NULL,
    kind        TEXT NOT NULL,         -- 'IDENT', 'KEYWORD', 'NUMBER', 'STRING', 'DELIM', etc.
    text        TEXT NOT NULL,         -- raw token text
    end_line    INTEGER,
    end_col     INTEGER
);
```

### Stage 3: AST Nodes

```sql
CREATE TABLE ast_nodes (
    node_id         INTEGER PRIMARY KEY,  -- auto-incrementing
    kind            TEXT NOT NULL,         -- 'Function', 'Record', 'Call', 'AtomId', etc.
    parent_node_id  INTEGER REFERENCES ast_nodes(node_id),
    token_id        INTEGER REFERENCES tokens(token_id),  -- first token of this node
    name            TEXT,                  -- definition name (if applicable)
    qualified_name  TEXT,                  -- fully qualified name (e.g., "math.square")
    file_id         INTEGER REFERENCES files(file_id),
    start_line      INTEGER,
    start_col       INTEGER
);
```

### Stage 4: Types

```sql
CREATE TABLE types (
    type_id         INTEGER PRIMARY KEY,  -- from ZType.nodeid (TypeID)
    name            TEXT NOT NULL,
    typetype        TEXT NOT NULL,         -- 'RECORD', 'CLASS', 'UNION', 'FUNCTION', etc.
    parent_type_id  INTEGER REFERENCES types(type_id),
    is_valtype      BOOLEAN,
    is_generic      BOOLEAN DEFAULT FALSE,
    is_literal       BOOLEAN DEFAULT FALSE,
    typedef_base_id INTEGER REFERENCES types(type_id),
    generic_origin_id INTEGER REFERENCES types(type_id),
    needs_destructor BOOLEAN,             -- new field (from Finding 3)
    destructor_name  TEXT,                -- new field (from Finding 3)
    is_heap_allocated BOOLEAN             -- new field (from Finding 3)
);

CREATE TABLE type_children (
    type_id     INTEGER NOT NULL REFERENCES types(type_id),
    child_name  TEXT NOT NULL,
    child_type_id INTEGER NOT NULL REFERENCES types(type_id),
    position    INTEGER NOT NULL,         -- order within parent
    PRIMARY KEY (type_id, child_name)
);

CREATE TABLE type_param_ownership (
    type_id     INTEGER NOT NULL REFERENCES types(type_id),
    param_name  TEXT NOT NULL,
    ownership   TEXT NOT NULL,            -- 'TAKE', 'BORROW', 'LOCK'
    PRIMARY KEY (type_id, param_name)
);

CREATE TABLE generic_args (
    type_id     INTEGER NOT NULL REFERENCES types(type_id),
    param_name  TEXT NOT NULL,
    arg_type_id INTEGER NOT NULL REFERENCES types(type_id),
    PRIMARY KEY (type_id, param_name)
);
```

### Stage 5: Variables

```sql
CREATE TABLE variables (
    variable_id INTEGER PRIMARY KEY,  -- from ZVariable.variableid
    name        TEXT NOT NULL,
    type_id     INTEGER NOT NULL REFERENCES types(type_id),
    ownership   TEXT NOT NULL,        -- 'OWNED', 'BORROWED'
    naming      TEXT NOT NULL,        -- 'NAMED', 'ANONYMOUS'
    scope_node_id INTEGER REFERENCES ast_nodes(node_id)
);
```

### Stage 6: Typed AST (annotations)

```sql
CREATE TABLE typed_nodes (
    node_id     INTEGER PRIMARY KEY REFERENCES ast_nodes(node_id),
    type_id     INTEGER REFERENCES types(type_id),
    variable_id INTEGER REFERENCES variables(variable_id),
    ownership   TEXT,                 -- resolved ownership at this point
    lock_state  TEXT                  -- 'UNLOCKED', 'EXCLUSIVE', 'SHARED'
);
```

### Stage 7: Emitted Code

```sql
CREATE TABLE emitted_lines (
    line_id     INTEGER PRIMARY KEY,
    c_line      INTEGER NOT NULL,     -- line number in output .c file
    node_id     INTEGER REFERENCES ast_nodes(node_id),  -- source AST node
    section     TEXT NOT NULL,         -- 'struct_def', 'forward_decl', 'func_def', 'main'
    text        TEXT NOT NULL          -- the emitted C line
);
```

### ID Flow Summary

```
file_id (VFS)
  → token_id (Tokenizer) via file_id
    → node_id (Parser) via token_id
      → type_id (Type Checker) via typed_nodes.node_id
        → line_id (Emitter) via emitted_lines.node_id
```

Every stage links back to the previous one via integer foreign keys. This enables queries like:
- "Show me all emitted C lines that came from function definitions in file X"
- "Find all types that need destructors but don't have one"
- "Which AST nodes have no type annotation?" (typed_nodes.type_id IS NULL)

#### Action Items

- [ ] Add `node_id: int` to AST base class (auto-incrementing counter)
- [ ] Add `token_id: int` to Token
- [ ] Add `file_id: int` to VFS file entries
- [ ] Implement `dump_sql()` method on each stage (VFS, Tokenizer, Parser, TypeChecker, Emitter)
- [ ] Wire up back-references: AST nodes store `token_id`, typed nodes store `node_id`, emitted lines store `node_id`
- [ ] Write integration test: compile a program, dump SQL, verify foreign key integrity

---

## Phased Refactoring Plan

### Phase 1: Enrich ZType (Foundation — no emitter changes yet)

**Goal:** Add memory management metadata to `ZType` so subsequent phases can query it.

**Changes to `ztypechecker.py`:**
```python
# Add to ZType dataclass (after is_valtype):
needs_destructor: Optional[bool] = field(default=None, init=False)
destructor_name: Optional[str] = field(default=None, init=False)
is_heap_allocated: Optional[bool] = field(default=None, init=False)
```

**Changes to `ztypecheck.py`:**
After resolving each type, set the new fields:
```python
# In _resolve_class_type():
ztype.needs_destructor = True
ztype.destructor_name = f"z_{name}_destroy"
ztype.is_heap_allocated = True

# In _resolve_record_type():
ztype.needs_destructor = False
ztype.is_heap_allocated = False

# In _resolve_union_type():
ztype.needs_destructor = True
ztype.destructor_name = f"z_{name}_destroy"
ztype.is_heap_allocated = True

# For "string" built-in type:
ztype.needs_destructor = True
ztype.destructor_name = "zstr_free"
ztype.is_heap_allocated = True
```

**Verification:** Existing tests should continue to pass unchanged. Add new unit tests asserting the metadata values.

- [ ] Add `needs_destructor`, `destructor_name`, `is_heap_allocated` fields to `ZType`
- [ ] Set these fields in the type checker for all type kinds (record, class, union, variant, protocol, facet, string, numerics)
- [ ] Add unit tests for the new metadata
- [ ] Verify all existing tests pass

### Phase 2: Complete Type Annotations on AST Nodes

**Goal:** Eliminate all `hasattr`/`getattr` fallbacks by ensuring every AST `Path` node gets a `.type` annotation.

**Diagnosis step:** Add a debug pass after type checking that walks all AST nodes and reports which ones lack `.type`:
```python
def _audit_type_annotations(program: zast.Program) -> List[str]:
    """Return list of nodes missing .type annotation."""
    missing = []
    # walk all definitions, parameters, return types, field types...
    return missing
```

**Fix:** For each missing case, add annotation logic in the type checker.

**Verification:** The audit pass returns an empty list for all test programs.

- [ ] Write the audit pass
- [ ] Run it against the test suite to identify all missing annotations
- [ ] Fix each missing case in the type checker
- [ ] Verify audit returns empty for all tests
- [ ] Remove the audit pass (or keep as a debug-mode assertion)

### Phase 3: Unify Destructor Emission

**Goal:** Replace 6 destructor sites with one function.

**Depends on:** Phase 1 (ZType has `destructor_name`), Phase 2 (all nodes have `.type`)

**New function:**
```python
def _emit_field_cleanup(self, field_access: str, ftype: ZType) -> str:
    """Emit cleanup code for a single field/variable."""
    if not ftype.needs_destructor:
        return ""
    return f"{ftype.destructor_name}({field_access});\n"
```

**Replace sites:**
1. `_emit_class()` destructor (lines 1370–1388)
2. `_emit_union()` destructor (lines 1476–1499)
3. `_emit_mono_union()` destructor (lines 1575–1601)
4. `_emit_mono_class()` destructor (lines 2305–2316)
5. `_emit_mono_list()` destructor (lines 1797–1820)
6. Protocol `boxed_destroy` (lines 949–971)

- [ ] Implement `_emit_field_cleanup()`
- [ ] Replace class destructor with call to `_emit_field_cleanup()` per field
- [ ] Replace union destructor (switch cases) to use `_emit_field_cleanup()`
- [ ] Replace mono_union destructor
- [ ] Replace mono_class destructor
- [ ] Replace mono_list destructor
- [ ] Replace protocol boxed_destroy
- [ ] Verify all emitter tests pass

### Phase 4: Remove Parallel Symbol Tables

**Goal:** Remove 11 name sets and `_collect_unit_names()`.

**Depends on:** Phase 2 (all nodes have `.type`), Phase 3 (destructors use `ZType` not name sets)

**Strategy:** Replace `name in self._class_names` with lookups against the type checker's `_resolved` dict. The emitter needs access to the resolved types — either pass the `TypeChecker` instance or extract a `Dict[str, ZType]` from it.

**Before:**
```python
if fname in self._class_names:
    return f"z_{fname}_t*"
```

**After:**
```python
ftype = self._resolved.get(fname)
if ftype and ftype.typetype == ZTypeType.CLASS:
    return f"z_{fname}_t*"
```

Or better, since Phase 2 ensures `.type` annotations exist:
```python
return _ctype(fpath.type)  # no name-set lookup needed at all
```

- [ ] Pass resolved type dict to emitter (or access via program metadata)
- [ ] Replace each `_class_names` usage with `ZType.typetype` check (or direct `_ctype()` call)
- [ ] Replace each `_union_names` usage
- [ ] Replace each `_record_names` usage
- [ ] Replace each `_variant_names` usage
- [ ] Replace each `_protocol_names` / `_facet_names` / `_spec_names` / `_func_names` usage
- [ ] Remove `_collect_unit_names()` (lines 419–467)
- [ ] Remove `_collect_proto_conformance()` if protocol conformance info is available from types
- [ ] Remove the 11 name sets from `__init__`
- [ ] Remove the 5 fallback resolution functions (Finding 1)
- [ ] Verify all emitter tests pass

### Phase 5: Scope Cleanup Refactor

**Goal:** Replace 12 instance variables with a `ScopeState` object.

**Depends on:** Phase 3 (unified cleanup emission)

- [ ] Create `ScopeState` dataclass
- [ ] Replace instance variables with `self._scope: ScopeState`
- [ ] Replace save/restore blocks with scope stack push/pop
- [ ] Unify scope-exit cleanup and return cleanup into single method
- [ ] Verify all tests pass

### Phase 6: Constructor Deduplication

**Goal:** Merge `_emit_meta_create_record()` and `_emit_meta_create_class()`.

**Depends on:** Phase 1 (ZType has `is_heap_allocated`), Phase 4 (no name-set lookups)

- [ ] Extract shared create logic into `_emit_meta_create(name, type, items, functions)`
- [ ] Use `type.is_heap_allocated` to select stack vs heap allocation
- [ ] Apply same deduplication to mono create variants
- [ ] Verify all tests pass

### Phase 7: Add Integer IDs for SQL (can be done in parallel with Phases 3–6)

**Depends on:** Phase 1 only (for type metadata fields in SQL schema)

- [ ] Add `node_id` to AST nodes
- [ ] Add `token_id` to tokens
- [ ] Expose `file_id` from VFS
- [ ] Implement `dump_sql()` on each compiler stage
- [ ] Write integration tests for SQL dump

### Phase 8: Simplify Monomorphized Container Emission

**Depends on:** Phase 3 (unified cleanup), Phase 4 (no name sets)

- [ ] Extract common container struct/create/destroy patterns
- [ ] Consider C template approach for map (330 lines)
- [ ] Reduce code duplication between list/array/map generators
- [ ] Verify all tests pass

### Phase 9: Self-Hosting Preparation

**Depends on:** All previous phases

- [ ] Replace `count().__next__` with explicit counters
- [ ] Replace `OrderedDict` with plain dict (or list of pairs if order matters)
- [ ] Remove `threading.Lock` from TypeTable
- [ ] Replace `NewType` with plain int type aliases
- [ ] Audit remaining Python-specific patterns
- [ ] Document the zerolang-to-zerolang compiler bootstrap plan

---

## Self-Hosting Considerations

For rewriting the compiler in zerolang, the code must avoid Python features that don't have zerolang equivalents. Beyond Finding 12, consider:

1. **Dynamic attribute setting** — The type checker sets `.type` on AST nodes dynamically (`node.type = resolved_type`). In zerolang, AST nodes would need an explicit `type: ZType?` field.

2. **Dict comprehensions and generators** — Used throughout. Replace with explicit loops.

3. **Multiple return via tuple** — Used in some places. Replace with out-parameters or result records.

4. **String interpolation (f-strings)** — Used extensively in emitter. Map to zerolang string interpolation.

5. **isinstance dispatching** — After the refactoring above, remaining `isinstance` checks should map to `match` on tagged unions (zerolang's native pattern).

6. **Default mutable arguments** — Python's `field(default_factory=list)` pattern. Use explicit initialization in zerolang constructors.

The phased refactoring plan above progressively simplifies the Python code toward patterns that map directly to zerolang constructs, making the eventual self-hosting translation mechanical rather than architectural.
