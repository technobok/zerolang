# Code Review — 2026-04-28

## Executive Summary

Since the 2026-03-29 review the compiler has absorbed several large
phases (atomic-call refactor, lock-escape, born-borrowed ownership,
falsy-first, for-loop iterator overhaul, borrow-correctness) and the
front-end is now in genuinely good shape: `isinstance` is held at 0 by
`bootstrap-lint`, AST dispatch goes through `nodetype` + `cast()`, the
`native` keyword has absorbed the bulk of "compiler knows about builtin
type X" specials, and most major data carries integer ids. The lexer,
parser, and AST modules have very few remaining smells.

The remaining gaps are concentrated in the **back-end** and in the
**lint coverage** itself:

1. `getattr(...)` is used ~125 times across the back-end — both as
   defensive AST-field access (where direct field reads work) and as a
   stand-in for "this `program.*` field may or may not have been
   attached yet". Neither pattern survives a port to zerolang. `getattr`
   is not yet tracked by `bootstrap-lint`, so it has been growing
   silently.
2. The C emitter still does ~134 string-literal compares (`name ==
   "this"`, `name == "String"`, IO-class names, special-method names).
   These are the largest remaining source of the "compiler hardcodes
   the name of a builtin" pattern that goal 4 is trying to eliminate.
3. The `TypeChecker` carries 33+ instance attributes, several of them
   poked from many call sites (`self._pending_borrow_lock`,
   `self._pending_private_access`); save/restore prologues for those
   are now a recognisable smell.
4. `zsqldump.py` is a useful diagnostic of the in-memory shape: it
   still has to probe with `getattr(...)` and dual-walk
   `symtab._history + symtab._scopes`, which means in-memory state is
   not yet quite table-flat.
5. A handful of low-cost portability and hygiene items remain:
   `copy.deepcopy(func)` for monomorphization (resolved 7bb5020),
   the last `isinstance` (resolved 32c779a), stale TODOs, the
   `getattr`-driven visitor in `zsynth.py` (resolved 366d0d6).
6. Several findings from `codereview20260322.md` were left in unclear
   status; the audit at the end of this review closes them out.

The proposed plan is four phases — easy wins first, then back-end
cleanup, then architectural records, then docs — followed by a
concrete `bootstrap-lint` expansion that prevents the cleaned-up
patterns from regressing.

Status legend: `[ ]` open, `[x]` done, `[~]` partial.

---

## Findings

### F1. Hardcoded IO-class dispatch in the emitter — Med (goal 1) \[RESOLVED\]

`src/zemitterc.py:1201-1235` dispatches on a literal class-name string
when emitting the IO wrapper natives:

```
if name == "BufWriter":   self.needs_io_natives.update({...})
elif name == "BufReader": self.needs_io_natives.update({...})
elif name == "TextWriter":self.needs_io_natives.update({..., "bufwriter_*", ...})
elif name == "TextReader":self.needs_io_natives.update({..., "bufreader_*", ...})
```

The cross-class dependency (`TextWriter` requires the `BufWriter`
natives, `TextReader` requires the `BufReader` natives) is encoded
implicitly via duplicated `update` calls. The whole table is small,
static, and would naturally live as data:

```
IO_WRAPPER_NATIVES: Dict[int, IoWrapperSpec] = {
    BUFWRITER_TYPE_ID: IoWrapperSpec(natives=[...], requires=[]),
    BUFREADER_TYPE_ID: IoWrapperSpec(natives=[...], requires=[]),
    TEXTWRITER_TYPE_ID: IoWrapperSpec(natives=[...], requires=[BUFWRITER_TYPE_ID]),
    TEXTREADER_TYPE_ID: IoWrapperSpec(natives=[...], requires=[BUFREADER_TYPE_ID]),
}
```

Action items:
- [ ] Allocate a stable `nodeid` (or interned name id) for each IO
      wrapper class once at type-checking time.
      *(deferred — not needed for the data-table fix; revisit alongside
      F4's `BuiltinName` work, which would also id-ify the AST-walk
      helpers `_ast_uses_io_names` / `_io_class_referenced`).*
- [x] Replace the if/elif chain with a single `IO_WRAPPER_NATIVES`
      dict; flatten the `requires` chain at lookup time.
- [x] If `_IO_WRAPPER_NAMES` is still consulted by name elsewhere,
      switch those sites to id lookup too.
      *(audited 2026-04-28: only the dispatch site at `:1199-1235` had
      a per-name branching pattern. `:1254` iterates classes but uses
      `defn.as_items` directly; `:1611` passes the tuple as a name set
      to `_ast_uses_io_names`, which compares against AST atom names
      — both would change shape only when F4 lands.)*

**Resolved 2026-04-28** — `src/zemitterc.py` now defines
`_IO_WRAPPER_NATIVES` (per-class native sets) and
`_IO_WRAPPER_REQUIRES` (cross-class dependency edges) as class-level
data, plus a `_io_wrapper_required_natives(name)` helper that flattens
the dependency chain. The dispatch site collapses from 35 lines of
if/elif to a single `self.needs_io_natives.update(...)` call. The
nodeid action item is deferred as noted.

### F2. `getattr` proliferation in the back-end — High (goals 3, 5) \[RESOLVED\]

Counts (today):

| File | `getattr` |
|---|---:|
| `ztypecheck.py` | 65 |
| `zemitterc.py` | 39 |
| `zsqldump.py` | 15 |
| `zasthash.py`, `zast.py`, `zenv.py`, `zparser.py`, `zprettyprint.py`, `zsynth.py`, `ztypes.py` | 1 each |
| **Total** | **~125** |

Two distinct patterns are responsible:

**(a) Defensive AST/field access.** `getattr(node, "is_node", False)`,
`getattr(node, "nodeid", None)`, `getattr(defn, "is_native", False)`,
`getattr(v, "generic_origin", None)`, `getattr(node, "is_token",
False)`. These fields are guaranteed dataclass fields on every Node /
Token / `ZVariable`. Direct attribute access works and is faster.
Sites:
  - `src/zsqldump.py:161-213` — every dump path begins with such a guard
  - `src/ztypecheck.py:302, 309, 312, 332` (and similar)
  - `src/zemitterc.py:1110, 1188, 1344, 1483, 1492, 1499`
  - `src/zparser.py:894` — `getattr(el, "is_token", False)` redundant
  - `src/zenv.py:563` — `getattr(v, "generic_origin", None)`
  - `src/ztypes.py:177` — `getattr(origin, "nodeid", None)`
  - `src/zasthash.py:300` — `getattr(part, "nodetype", None)`
  - `src/zast.py:` — defensive guards in node walks

**(b) Optional `Program` metadata.** `mono_types`, `cloned_methods`,
`func_aliases`, `unit_types_by_id`, `symbol_table`, `resolved` etc. are
attached to `Program` conditionally (only after typechecking, only if
non-empty, etc.). Sites that paper over this with default-`getattr`:
  - `src/zemitterc.py:1454, 1483, 1499, 1550, 1563, 1688, 1749, 1778`
  - `src/zsqldump.py:344, 359, 361-362`

Pattern (b) is the deeper smell: `Program` does not have a stable
shape, so any consumer of it has to defend against missing fields.
This is exactly the in-memory layout that won't dump cleanly to a SQL
schema (goal 5).

Action items:
- [x] Pre-initialize all `Program` metadata fields to empty
      lists/dicts in the parser (or in `Program.__init__`); never
      attach conditionally.
      *(Discovered already-true: `Program` declares `mono_types`,
      `mono_functions`, `func_aliases`, `cloned_methods`, `resolved`,
      `unit_types_by_id` with `default_factory=list/dict` and
      `symbol_table` with `default=None` — see `src/zast.py:255-298`.
      The cleanup happened at the call sites: `getattr(self.program,
      "X", default)` → `self.program.X`.)*
- [x] Replace pattern (a) sites with direct field access. Guarantee
      every Node has `is_node = True`, `is_token = False`, `nodeid:
      int` (or use the existing `is_*` fields directly).
      *(Done across `ztypecheck.py`, `zemitterc.py`, `zsqldump.py`,
      `zparser.py`, `zenv.py`, `ztypes.py`, `zasthash.py`,
      `zprettyprint.py`. The two reflection-based AST walkers were
      replaced with a typed walker — see Stage C below.)*
- [x] After cleanup, `getattr` count should be in the single digits
      (only legitimately optional cases). Add a `bootstrap-lint`
      baseline at that count (see F3).
      *(Final count: 4. Lint baseline added in F3a.)*

**Resolved 2026-04-28** — landed in five staged commits:

- **Stage A** (`emitter: F2/A — drop redundant getattr on Program metadata`):
  13 sites in `zemitterc.py` + `zsqldump.py` cleaned to direct
  attribute access; the resolved-lookup site lost an unreachable
  `if resolved is None` branch. Count 125 → 112.
- **Stage B** (`typecheck/emitter: F2/B — drop defensive getattr on
  typed Node/ZType values`): ~80 sites covering Node-from-typed-dict
  iteration, AtomString stringparts (`Token | Expression`
  discriminated via `is_node`), Path/Node `.type` direct access, ZType
  field direct access, Expression.expression direct access. `_NORETURN`
  upgraded to `_NoReturnSentinel` with `is_ztype = False` so callers
  can use `t.is_ztype` after a None check; `is_tag_origin` parameter
  type tightened to `Optional[ZType | _TagOrigin]`. Count 112 → 28.
- **Stage C** (`zsqldump: F2/C — typed AST walker in place of
  __dataclass_fields__ reflection`): added `zast.node_children` and
  `zast.node_tokens` — exhaustive typed enumerations keyed by
  `NodeType`. Rewrote zsqldump's `_collect_tokens` /
  `_collect_ast_nodes` and ztypecheck's `audit_type_annotations` on
  top of the typed helpers; structural skip rules in the audit walker
  now check identity against `parent.<slot>` plus a `Data`-ancestry
  flag. Count 28 → 12.
- **Stage D** (`compiler: F2/D — pre-init transient state;
  SymbolTable Protocol module breaks zast<->zenv cycle`):
  `_last_emitted_arg_vals` declared in `__init__`,
  `_comprehension_list_var` / `_name` declared on the `For` dataclass.
  New `src/zsymtab_proto.py` defines `SymbolTableProto`; zast types
  `Program.symbol_table` against it; zsqldump uses direct
  `symtab._scopes` / `_history`. Visitor-by-name dispatch removed:
  `TypeChecker._definition_resolvers` is now a `dict[type, Callable]`
  of bound methods built in `__init__`; `zsynth.Rewriter.handlers` is
  a `Dict[NodeType, Callable]` populated by subclasses. Count 12 → 4.
- **Stage E** (`Makefile: F3a — add getattr bootstrap-lint baseline`):
  baseline locked at 4 (see F3 below).

Total: getattr count 125 → 4. Files touched (commits 1a0756a, 2c16fa3,
75afd74, 366d0d6, 7d34d5a):
`src/zemitterc.py`, `src/ztypecheck.py`, `src/zsqldump.py`,
`src/zast.py`, `src/zenv.py`, `src/ztypes.py`, `src/zasthash.py`,
`src/zprettyprint.py`, `src/zparser.py`, `src/zsynth.py`, plus the
new `src/zsymtab_proto.py`. The four surviving sites are all
legitimately heterogeneous unions (Token-or-NodeX in `zparser.py`;
`is_native` / `functions` only on some Unit.body members in
`zemitterc.py` / `ztypecheck.py`).

### F3. Add `getattr`, `startswith`, name-literal compares to bootstrap-lint — High (goals 3, 4) \[~partial\]

Today's `Makefile:25-72` baselines: `isinstance:1, comprehension:14,
lambda:0, try/except:8, hasattr:16, name-compare:14`. The lint
mechanism works — `isinstance` was driven from 370 to 1 under it. But
three patterns aren't tracked, and they are exactly the patterns this
review keeps flagging:

| Pattern | Current count |
|---|---:|
| `getattr(` | ~125 |
| `startswith(` | 42 (38 in `zemitterc.py`) |
| Literal `== "..."` compare in `src/*.py` | ~140 (rough) |

Once F2 and F4 land, lock in the post-cleanup numbers as new
baselines.

Concrete `Makefile` patch (insert after the `hasattr` block, around
line 64):

```make
count=$$(grep -rn 'getattr(' src/*.py | wc -l); \
if [ $$count -gt N_GETATTR ]; then \
    echo "ERROR: getattr() usage increased ($$count > N_GETATTR baseline)"; \
    echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
    grep -rn 'getattr(' src/*.py | tail -5; fail=1; \
fi; \
count=$$(grep -rn 'startswith(' src/*.py | wc -l); \
if [ $$count -gt N_STARTSWITH ]; then \
    echo "ERROR: startswith() usage increased ($$count > N_STARTSWITH baseline)"; \
    echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
    grep -rn 'startswith(' src/*.py | tail -5; fail=1; \
fi; \
count=$$(grep -rnE '== *"[A-Za-z_][A-Za-z0-9_]*"' src/*.py | grep -v 'ztc-string-compare-ok' | wc -l); \
if [ $$count -gt N_NAMELIT ]; then \
    echo "ERROR: literal name compares increased ($$count > N_NAMELIT baseline)"; \
    echo "  Compare by id (BuiltinName / nodeid / name_id) instead."; \
    echo "  Intentional? Add '# ztc-string-compare-ok: <reason>' on the same line."; \
    echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
    grep -rnE '== *"[A-Za-z_][A-Za-z0-9_]*"' src/*.py | grep -v 'ztc-string-compare-ok' | tail -5; fail=1; \
fi; \
```

Set `N_GETATTR`, `N_STARTSWITH`, `N_NAMELIT` to the post-cleanup
counts after Phase 2. Same `# ztc-string-compare-ok:` escape hatch
already used by the `name-compare` rule.

Action items:
- [x] After F2 lands: add `getattr` baseline.
      *(Done in commit `7d34d5a`. Baseline locked at 4. Sanity-checked
      with a synthetic regression in `src/zc.py`.)*
- [x] After F4 lands: add `startswith` and name-literal-compare
      baselines. *(Both landed without waiting for F4 C/D under the
      "no new violations" policy. `startswith` baseline locked at 42
      (no escape hatch — every prefix test is bootstrap-hostile).
      `name-literal-compare` baseline locked at 272 covering both
      `==` and `!=` literal compares via
      `(==|!=) *"[A-Za-z_][A-Za-z0-9_]*"`, reusing the existing
      `# ztc-string-compare-ok:` escape mechanism. Note: count is
      higher than the codereview's ~140 rough estimate because the
      regex covers both directions across all `src/*.py`, not just
      the emitter buckets the audit focused on. Sanity-checked by
      running with stricter baselines — both rules trip and emit
      sample violations.)*
- [x] Update the comment block at `Makefile:24-26` to reflect the
      expanded set. *(Added `startswith:42` and
      `name-literal-compare:272` lines.)*

### F4. String-literal compares in the C emitter — High (goal 4) \[~partial\]

`zemitterc.py` has ~134 `== "..."` literals against names. The hot ones:

- **Receiver-parameter detection: `pname == "this"`** at
  `src/zemitterc.py:2061, 2142, 2260, 2322` (four sites; same check
  every time). `Entry` already exists for parameters; add an
  `is_receiver: bool` field at the time the receiver entry is
  allocated (in the parser or method-binder), and the four sites
  become `entry.is_receiver`.
- **String / StringView checks**: `src/zemitterc.py:203-207, 6959`,
  and many more reachable via `grep -n '"String"' src/zemitterc.py`.
  Switch to `ztype.subtype == ZSubType.STRING` (already exists) /
  `ZSubType.STRINGVIEW`.
- **Special methods**: `name == "parseF64"` at `:735`, `name ==
  "envNames"` at `:766`. These are all `is_native` symbols — they
  should be tagged at type-check time with a `BuiltinFunc` enum (or
  reuse the existing `is_native` plus a small id). Replace the
  string-literal check with id compare.
- **IO class names** — covered by F1.

A single helper carries the rest of the cleanup:

```python
class BuiltinName(IntEnum):
    NONE = 0
    THIS = 1
    STRING = 2
    STRING_VIEW = 3
    PARSE_F64 = 4
    ENV_NAMES = 5
    # ...
```

…interned once when names are first seen, attached to the relevant
record (`Entry.builtin_name`, `ZType.builtin_name`,
`ZFunction.builtin_func`).

Action items:
- [x] Add `Entry.is_receiver: bool`; stamp at parameter binding;
      replace the receiver-detection sites in the emitter that aren't
      coupled to `_prepend_method_receiver`'s prepend predicate.
      *(F4.1+F4.2: 5 of the 8 `pname == "this"` sites migrate to
      `ftype.this_param_name == pname` (a string-to-string compare,
      not a literal compare). The remaining 2 — `_emit_call_args`
      `params[0][0] == "this"` and `children_keys[0] == "this"` —
      are the prepend predicate, semantically coupled to
      `_prepend_method_receiver`'s `"this" not in ftype.children`
      early return; left as literals with `# ztc-string-compare-ok`
      escape and explanatory comment.)*
- [x] **Bucket B — String/StringView checks.** *(F4.3: 3 sites in
      `_ctype` and `_emit_atom` migrated to
      `ztype.subtype == ZSubType.STRING/STRINGVIEW`. The bare-class
      disambiguator follows the surrounding record-case idiom
      (`atom_ztype.name == name`).)*
- [x] **Bucket C — `parseF64` / `envNames`** at `src/zemitterc.py:785, 816, 7578`.
      *(F4.4: `BuiltinFunc(IntEnum)` added to `ztypes.py` alongside
      `ZSubType`/`ControlKind`; `ZType.builtin_func` field stamped in
      `_resolve_function_type` via the same name-table pattern as
      `_CONTROL_KINDS`. Three emitter literal compares replaced —
      `_track_stdlib_unit_native` now takes an optional `ftype`
      threaded from its three callers; the stringview dotted-path
      site reads via `typing.child_of(parent_type_dp, child)`.
      name-literal-compare baseline 272 → 269; sanity-checked by
      tripping with stricter baseline. Codereview's draft enum name
      `BuiltinName` not used — `BuiltinFunc` is more precise for the
      function-only scope.)*
- [ ] **Bucket D — meta / method names**: `create`, `take`, `borrow`,
      `box`, `main`, `copy`, `length`, `private`, `lock`, `release`
      (~50 sites). *(Deferred from F4. Single-character role-names
      are more readable as literals than `BuiltinName.CREATE` plus a
      module-level frozenset of constants. Revisit only if a
      self-hosting port hits a concrete portability blocker.)*
- [ ] Replace literal compares with enum compares (covers buckets C/D
      above).
- [~] When F3's name-literal-compare lint goes in, set the baseline
      at the post-cleanup count. *(See F3b — lint baseline set at the
      post-F4-scoped count, not zero, since buckets C/D are deferred.)*

**Resolved-as-scoped 2026-04-30** — landed in three sub-commits:

- `94fcb42` (F4.1): `Entry.is_receiver: bool` added; stamped in
  `_check_function_body_inner` via `ftype.this_param_name == pname`.
  No emitter changes; 1962 tests passing.
- `55246a9` (F4.2): emitter receiver detection — 5 of 8 sites
  migrated to `ftype.this_param_name == pname` /
  `spec_type.this_param_name == pname`. `_emit_facet` and
  `_emit_facet_impl` gain a single `_resolved_type(...)` lookup
  each. The 2 prepend-predicate sites are kept as literals with
  ztc-string-compare-ok escapes — see action-item note above.
  Byte-identical C output verified across all 85 examples.
- `9e519b5` (F4.3): `_ctype` String/StringView dispatch and
  `_emit_atom` bare-class branch use `ztype.subtype == ZSubType.*`.
  Byte-identical C output verified.
- F4.4 (this entry): bucket C done via `BuiltinFunc` enum.
  Three sites in `zemitterc.py` (header-dispatch for `parseF64` /
  `envNames`) now use `ftype.builtin_func == BuiltinFunc.*`.
  Stamping mirrors the `_CONTROL_KINDS` pattern in
  `_resolve_function_type`. `make test` 1943+ passing; byte-identical
  C verified.

Bucket D remains open by design (see action item: ~50 syntax-keyword
sites where literals are more readable than enum constants).

### F5. TypeChecker state sprawl — Med (goals 1, 5)

`TypeChecker.__init__` (and ad-hoc setattrs across the file) holds 33+
instance attributes. Cross-cutting state is the bigger problem than
the count: `self._pending_borrow_lock` and
`self._pending_private_access` are mutated from ~14 sites
(`src/ztypecheck.py:1818, 6007, 6008, 6236, 6257, 6289, 6421, 6435,
6576, 6945, 7112, 7128, 7972, 8025, 8069, 8111, 8228, 8844`) and
require save/restore prologues at function-body boundaries (a clear
example at `:5437-5483`).

Group into context records (each becomes one row of a table; goal 5):

```python
@dataclass
class ResolverState:
    resolving: List[str]
    resolved: Dict[str, ZType]
    resolved_file_units: Set[int]

@dataclass
class FunctionContext:
    return_type: Optional[ZType]
    func_ownership: Dict[str, ZParamOwnership]
    func_return_ownership: ZOwnership
    body_stack: List[int]
    enclosing_type_stack: List[ZType]

@dataclass
class BorrowState:
    pending_borrow_lock: Optional[Path]
    pending_private_access: bool
    call_id_stack: List[int]
    call_preamble: List[Statement]

@dataclass
class MonoState:
    cache: Dict[str, ZType]
    types: List[Tuple[ZType, Defn]]
    functions: List[ZFunction]
    generic_context: Optional[GenericContext]
    func_hashes: Dict[str, str]
    func_aliases: Dict[str, str]
    cloned_methods: Dict[str, ZFunction]
    assigned_cnames: Set[str]

@dataclass
class TemplateIds:
    option: int
    optionval: int
    optionview: int
```

Save/restore at function-body boundaries becomes a single
`prev = self.func_ctx; self.func_ctx = FunctionContext(...); ...;
self.func_ctx = prev` block per record, instead of N parallel
prologues.

Action items (see plan-list at line 667 for landed-commit citations):
- [~] Define the five dataclasses above. *(3 of 5 landed: MonoState
      `fb8360a`, FunctionContext `1224b59`, TemplateIds `3ec7802`.
      BorrowState substituted with scope-containment via
      `ExprResult.borrow_target` — F5.A `4e4ba47`/`ebf9638`/`909267d`.
      ResolverState explicitly deferred — `unit_types` / `_resolved` /
      `_resolved_file_units` are publicly named on `TypeChecker` and
      read by ~100 test sites; cosmetic gain doesn't justify churn.)*
- [x] Move attributes off `TypeChecker` onto the records, one record
      at a time. *(Done for the three landed records: 8/5/3 fields
      respectively; 59/63/9 access rebases.)*
- [x] Replace each save/restore prologue site (start at
      `src/ztypecheck.py:5437-5483`) with a single record swap.
      *(Done for FunctionContext at function-body boundaries.)*
- [x] Each record has stable `*_id` fields where applicable, so
      `zsqldump.py` can dump them as one row each (links to F6).
      *(F5.H landed flat `type_child` / `type_generic_arg` tables;
      remaining row-shape work for the three records folds into F6.)*

### F6. `zsqldump.py` exposes the in-memory shape — Med (goals 5, 6)

`zsqldump.py` does more than write rows — it has to *adapt* the
in-memory shape because that shape is not yet table-flat:

- `:161-213` — every dump path begins with a defensive
  `getattr(node, "is_node", False)` / `getattr(val, "is_node",
  False)` walk (links to F2).
- `:344, 359, 361-362` — pulls program metadata via `getattr` and
  dual-walks scopes: `list(getattr(symtab, "_history", [])) +
  list(getattr(symtab, "_scopes", []))`. Closed and live scopes are
  in two lists. Replace with a single append-only `scope_log` (rows:
  `scope_id, parent_id, kind, opened_at_seq, closed_at_seq`).
- `:366-382` — narrowed-subtype data flattened to a CSV string. Per
  CLAUDE.md ("sql tables should have singular names") this should be
  a child table:
  ```
  CREATE TABLE narrowed_subtype (
      scope_id INTEGER REFERENCES scope(id),
      type_id INTEGER REFERENCES ztype(id),
      excluded INTEGER NOT NULL  -- 0|1
  );
  ```
- `:424` — `for i, (text, nid) in enumerate(zip(c_lines,
  emitter.source_map))` will silently truncate if the lengths
  diverge. Pre-compute `source_map` so it has exactly one entry per
  emitted line, or use `zip(strict=True)` (Python 3.10+).
- `emitted_lines` (line 406+) is populated *during* emission rather
  than from a pre-built node→line index — so the SQL dump's content
  depends on the emitter's traversal order, not on a stable model.

Action items:
- [ ] After F2's `Program` cleanup, drop the `getattr` defaults from
      the dumper.
- [ ] Replace `_history + _scopes` dual-walk with an append-only
      `scope_log` in `SymbolTable`.
- [ ] Move `excluded_subtypes` from CSV to a child table.
- [ ] Replace the `zip` at `:424` with `zip(..., strict=True)` and
      assert pre-emission that `len(source_map) == c_lines`.
- [ ] Build `node_id → emitted_line` index after emission completes;
      have the dumper read from the index, not from emit-time hooks.

### F7. `zemitterc_templates.py` underused — Low (goals 1, 2)

`src/zemitterc_templates.py` is 60 lines, with 4 callers in
`zemitterc.py` (`apply()` for `z_array.c.tmpl`, `z_str.c.tmpl`,
`z_List.c.tmpl`, `z_ListView.c.tmpl`). The other ~9700 lines of
emitter output come from ad-hoc string concatenation. This is not
itself a bug — extracting templates is a per-feature project — but
documenting what's deliberate vs. what's pending helps prioritize.

Action items:
- [ ] Add a header comment to `zemitterc_templates.py` listing what is
      currently template-driven and what is ad-hoc.
- [ ] Pick one well-bounded subsystem as the next template target.
      Recommendation: **vtable emission** (the dispatch tables for
      protocol conformance) — fixed shape, 100% mechanical, no
      branching on ad-hoc state.

### F8. `copy.deepcopy(func)` is non-portable — Low (goal 2) \[RESOLVED\]

`src/zast.py:301` previously used `copy.deepcopy` inside
`clone_function`, which runs once per generic monomorphization.
That won't survive a port to zerolang (no equivalent
reflection-driven deep copy). The deepcopy also shared `nodeid`s
across clones (verified — `__dict__`-based copy preserves
init=False fields), which would collide in
`TypeChecker._node_*` side-tables when multiple monos of the
same generic function are materialised.

Action items:
- [x] Replaced `copy.deepcopy(func)` with an explicit nodetype-driven
      clone visitor in `zast.py` (one branch per `NodeType`).
      Cloned nodes get fresh `nodeid`s via the dataclass
      default_factory; `synth_origin` preserved as a constructor
      kwarg; Tokens (`start`) shared by reference.
- [x] Helper `_clone_list` / `_clone_dict` use loop form rather than
      list comprehension to stay under the bootstrap-lint cap.
- [x] Removed `import copy` from `zast.py`.

`make check` clean, `make test` 1962 passing.

### F9. Single remaining `isinstance` — Low (goal 3) \[RESOLVED\]

`src/zparser.py:1621` previously held the last `isinstance(field_node,
zast.Path)`. That site fell out of the parser as a side-effect of
`32c779a` (W0 — parser cleanup that dropped the dead enum +
unlabelled-path machinery the isinstance was guarding). The
bootstrap-lint baseline in `Makefile:25` is now `isinstance:0`;
`grep -rn 'isinstance(' src/*.py` returns zero hits.

Action items:
- [x] Replaced the `isinstance` at `zparser.py:1621`. *(Resolved
      incidentally in `32c779a` (2026-04-28) when the parser dropped
      the dead unlabelled-path branch the isinstance was guarding;
      no separate cleanup commit needed.)*
- [x] `bootstrap-lint` baseline already at `isinstance:0`.

### F10. Stale TODO comments — Low (goal 6) \[RESOLVED\]

- [x] `src/zast.py:266` — converted into a normal comment that
      documents the design choice and points at the codereview for
      the future-simplification idea (single top-level unit with
      submodules in the body).
- [x] `src/zast.py:324` — TODO removed; the field it referenced
      (`Node.type` with `ZTypeCheckInProgress` sentinel) was
      stripped in Step 6.9.b. Surrounding stale commentary
      (uninstantiated `definition: ZSymbol` placeholder, "filled in
      typechecking pass" note) cleaned up too.
- [x] `src/zparser.py:271-272` — Parser docstring rewritten;
      typecheck has been a separate module (`ztypecheck.py`) for
      a long time, so the "TODO: pass2 in a separate class" line
      no longer applies.
- [x] `src/zparser.py:541` — comment expanded with concrete
      citations: `this` is the method receiver
      (`ztypecheck.py:1214–1336`); `type` is reserved for type-of
      expressions (`ztypecheck.py:3413`); `meta.create` is the
      compiler's allocator (`ztypecheck.py:3591`).

### F11. `getattr`-driven visitor dispatch in `zsynth.py` — Low (goal 3) \[RESOLVED\]

`src/zsynth.py:120` previously held `handler = getattr(self,
handler_name, None)` for visitor-name lookup by string. The
recommended replacement was an explicit
`Dict[NodeType, Callable]` mirroring the shape in `zasthash.py`.

Resolved incidentally in `366d0d6` (F2/D state pre-init).
`Rewriter.handlers: Dict[NodeType, Callable]` is now declared as
a `field(default_factory=dict)` populated by subclasses; `visit`
dispatches via `self.handlers.get(node.nodetype)`. Zero `getattr`
calls remain in `zsynth.py`.

Action items:
- [x] Replaced the `getattr` lookup in `zsynth.py` with an explicit
      `Dict[NodeType, Callable]`. *(Resolved `366d0d6`.)*

### F12. Documentation drift — Med (goal 6)

Several earlier-flagged drifts deserve a fresh pass against the
current state:

- `doc/compiler.pdoc` — was flagged in 20260321 for placeholder text
  ("xxx The Parser maintains…"), missing sections on monomorphization,
  demand-driven resolution, lock checking, and the 2-state ownership
  model. Verify and fill in.
- `doc/roadmap.pdoc` — 20260321 flagged outdated v1 deferral list.
  Confirm classes/unions/variants/protocols/facets/generics are
  marked complete.
- `doc/Design-OPEN.pdoc` — 20260329 flagged that implemented proposals
  (`native`, collection methods) are not marked resolved.
- `src/zemitterc.py:1585-1626` — the docstring advertises "no
  isinstance / getattr probing" but the code below uses `getattr` for
  optional `Program` metadata. Once F2 lands, the docstring becomes
  accurate; until then, tighten its claim.
- `src/zparser.py:271-272` — docstring claims `pass2 is self.typecheck`
  but typecheck is in `ztypecheck.py`.

Action items:
- [ ] Pass over `doc/compiler.pdoc` against current implementation;
      add sections for monomorphization, demand-driven resolution,
      lock checking, and ownership.
- [ ] Mark resolved items in `doc/Design-OPEN.pdoc`.
- [ ] Verify `doc/roadmap.pdoc` reflects current phase status.
- [ ] Fix `zparser.py:271-272` docstring.
- [ ] After F2 lands, tighten the `zemitterc.py:1585-1626` docstring.

### F13. Carry-forward audit from `codereview20260322.md` — Low (goal 6) \[Phase 1 RESOLVED\]

That review left several items in unclear status. Resolved as of
2026-04-28:

- [x] Debug `print(...)` statements in parser — none remain.
- [x] `_fixcalloperation` stub — removed.
- [x] Big commented-out blocks in `zlexer.py` — none ≥15 lines remain.

Phase 1 sweep (resolved 2026-04-30):

- [x] **Lexer private state read from parser**: added
      `Lexer.filtereol_state() -> bool` public read accessor; the
      three parser sites (`zparser.py:548, 2435, 2648`) that
      previously read `lex._filtereol` directly now go through it.
      The setter `lex.filtereol(bool)` and the underlying
      `_filtereol` attribute are unchanged.
- [x] **Typo in lexer docstring**: `src/zlexer.py` "undercores" →
      "underscores".
- [x] **Source-map zip truncation**: `src/zsqldump.py` —
      pre-assertion on `len(c_lines) == len(emitter.source_map)`
      with a descriptive message, plus `zip(..., strict=True)`.
      A length mismatch now raises with a clear message rather
      than silently dropping rows.
- [ ] **Generated-C `malloc` NULL checks** (20260322 finding 2) —
      still open; blocked on the `libzrt.a` runtime library work
      (Phase 40 in the roadmap).
- [ ] **12 examples not exercised through the emitter** (20260322
      finding) — confirm current state by running `make build` and
      cross-checking against `tests/test_emitter.py` coverage.

---

## Phased Refactor Plan

### Phase 1 — Easy wins (parallel-safe)

Independent, low-risk; can be done in any order or in parallel.

- [x] F8 — replace `copy.deepcopy(func)` with explicit clone visitor. *(Resolved 7bb5020.)*
- [x] F9 — replace last `isinstance`; drop lint baseline 1 → 0. *(Resolved incidentally in `32c779a`.)*
- [x] F10 — close out the four `TODO` comments. *(Resolved; see section F10 for per-TODO citations.)*
- [x] F11 — `zsynth.py` visitor → `Dict[NodeType, Callable]`. *(Resolved incidentally in `366d0d6`.)*
- [x] F13 — `_filtereol` accessor, "undercores" typo,
      `zip(..., strict=True)` in `zsqldump.py`. *(Resolved 59ef5eb; remaining items in F13 — `malloc` NULL checks blocked on libzrt.a, examples-coverage audit — out of Phase 1 scope.)*

### Phase 2 — Back-end cleanup (sequential)

Each step depends on the previous one. F4 is the largest.

1. [x] F2 — pre-initialize `Program` metadata fields; drop defensive
       `getattr`s. *(Resolved 2026-04-28; count 125 → 4.)*
2. [x] F3a — add `getattr` baseline to `bootstrap-lint`.
       *(Resolved 2026-04-28; baseline 4.)*
3. [~] F4 — `Entry.is_receiver`, `BuiltinName`/`BuiltinFunc` enums,
       replace literal compares. *(Partial 2026-04-30 in commits
       `94fcb42`, `55246a9`, `9e519b5`: receiver-detection bucket A
       and String/StringView bucket B done. Buckets C
       (`parseF64`/`envNames`) and D (~50 meta/method-name literals)
       deferred — would hurt readability for limited gain; revisit
       only if a self-hosting port hits a concrete portability
       blocker.)*
4. [~] F3b — add `startswith` and `name-literal-compare` baselines.
       *(Partial: ready to add at the current post-F4-scoped count
       rather than zero, since buckets C/D leave ~50+ literals.)*

### Phase 3 — Architecture

Larger refactors; do in order.

5. [x] F5 — TypeChecker context records + decomposition + ECS shape.
       *(Scoped expansion 2026-04-30: see plan
       `~/.claude/plans/start-planning-for-f5-fluffy-dragonfly.md`. F5
       split into 8 sub-items. F5.A (BorrowState + ExprResult flow)
       resolved in `4e4ba47`/`ebf9638`/`909267d` — the cross-cutting
       `_pending_borrow_lock` / `_pending_private_access` flag is
       contained: external readers/setters are gone; mutation is
       scope-contained to `_check_dotted_path_inner`,
       `_check_call_inner`, and three helpers; capture happens at
       `_check_path` / `_check_call` / `_check_operation` boundaries.
       F5.F (TAG_ORIGIN sentinel removal) resolved in `feba9e5`.
       F5.D (move side tables to `Program` as ECS components) resolved
       in `f75c0d5` — first relocated 19 nodeid-keyed dicts off
       `TypeChecker` onto `zast.Program`. F5.E (typecheck-output
       container + drop typed-tree mirror) resolved across
       `744620f` (E.1: empty `Typing` module),
       `6dfef80` (E.2: 19 component dicts moved from Program to Typing),
       `fd646e4` (E.3: aggregate state moved to Typing,
       `typecheck() -> Typing`, Program-compat shims),
       `1976ccd`/`d061291`/`0199f89` (E.4.a–c: emitter consumers
       routed through Typing) and `64ba8a2` (E.4.d: typed-tree
       mirror module + 19 `_build_typed_*` methods + `tests/test_typed_tree.py`
       deleted; replaced by `TypedProgramView` thin compat shim).
       Net effect: typecheck output lives in its own module
       (`src/ztyping.py`); `zast.Program` is mutable only via the
       transitional compat shims (canonical access path is `typing.X`);
       parallel typed-tree class hierarchy gone. Repo −1828 lines
       in F5.E.4.d alone; `ztypecheck.py` from 10,777 → 9,995 lines.
       `make check`, `make test` (1943, was 1962 minus 19 typed-mirror
       tests), byte-identical C output across all 85 examples at
       every sub-commit.
       F5.E.5 (`1249112`) closed out F5.E: `zast.Program` is
       `@dataclass(frozen=True)`, the 8 typecheck-output compat shims
       are gone, the `typing_or_program` dual-accept dispatch in
       emitter/sqldump collapsed to single-arg `Typing`, and ~280
       test sites rebased to read `typing.<field>` directly.
       F5.C (decompose `_monomorphize`) resolved across `a2d3712`
       (F5.C.1: extract `_check_mono_constraints`,
       `_synth_collection_methods`, `_clone_mono_methods` — 750→339
       lines) and `535ee74` (F5.C.2: extract `_make_mono_shell`,
       `_substitute_mono_children`, `_recompute_mono_typetype_marks`,
       `_rebuild_mono_tag`, `_setup_mono_meta_create`,
       `_register_mono`, `_mark_mono_native` — 339→64 lines).
       The coordinator is a pure sequence of 11 named helper calls;
       all helpers ≤80 lines except `_synth_collection_methods`
       (371 lines, cohesive — further per-collection-type splitting
       is deferred). Pilot success: the same technique applies to
       the other 4 monsters (F5.G).
       F5.G (decompose remaining monsters) resolved across:
       `a60c961` (F5.G.1: `_check_call_inner` 591→55, 5 helpers
       — control-flow dispatch, construction dispatch, arg loop,
       missing-args, finalize),
       `eaa174d` (F5.G.2: `_check_case_inner` 427→352, 2 helpers
       — result-type unification, take-invalidation),
       `6523dde` (F5.G.3: `_check_dotted_path_inner` 433→235, 5
       intrinsic helpers — `.take`/`.release`/`.borrow`/`.lock`/
       `.private`),
       `bc75996` (F5.G.4: `_resolve_dotted_path` 375→252, 1 helper
       — parent-type resolution).
       _check_case_inner's residual 352 lines is heavily state-coupled
       (per-arm clause loop sharing 10+ mutable variables); further
       reduction would need a context object or wide parameter
       lists, deferred.
       F5.B (state records — partial) resolved across `fb8360a`
       (F5.B.1: MonoState — 8 mono-related fields, 59 access rebases),
       `1224b59` (F5.B.2: FunctionContext — 5 function-body fields,
       63 access rebases), `3ec7802` (F5.B.3: TemplateIds — 3 lazy
       template-id fields, 9 access rebases). The fourth planned
       grouping (ResolverState — `unit_types`/`_resolved`/etc.) is
       deferred: those fields are publicly named on `TypeChecker`
       and read by ~100 test sites; the cosmetic gain doesn't
       justify the test churn.
       F5.H (flatten `ZType.children` / `ZType.generic_args` to flat
       relational tables) resolved 2026-05-01 across 13 commits:
       `5f56621` (H.1: `TypeChild` + `TypeGenericArg` dataclasses +
       `Typing.type_child` / `type_generic_arg` empty fields),
       `dbe52f7` (H.2.a/b: `_set_child` helper + 137 single-key
       `parent.children[k] = v` writes routed),
       `2d74ae9` (H.2.c: 3 `dst.children = src.children` aliasing
       sites + 4 `mono.generic_args = dict(...)` bulk writes routed),
       `3ddf300` (H.3.a: `child_type` ref column + Typing read
       accessors `child_of` / `children_of` / `has_child` / `child_count`
       / `child_names_of` / `child_types_of` / `child_by_id` /
       `generic_arg_of` / `generic_args_of` / `has_generic_args`),
       `57ab204` (H.3.b: ~70 zemitterc consumer sites migrated),
       `a785d4e` (H.3.c: zsqldump consumers migrated; type_children
       INSERTs now generated directly from `typing.type_child` rows),
       `d06076d` (H.3.d: ~110 ztypecheck reads migrated),
       `ce7232f` (H.4.a: `typing` plumbed through 14 ztypeutil
       generic-args helpers + 25 callers in zemitterc/ztypecheck),
       `aec8d90` (H.4.b/c: remaining 11 generic_args reads migrated),
       `24ec3c8` (H.5.a: F5.H module-level residuals eliminated —
       dead `_ctype_func_inline` deleted, `typing` plumbed through
       `_ctype` + `_proto_param_ctype` + `_set_field_cleanup_metadata`
       at 95+9+9 call sites; `Typing.child_by_id` replaces
       `ZType.resolve_child_by_id` at 5 zemitter call sites),
       `1a8455d` (H.5.b: setter logic moved to `Typing.set_child` /
       `Typing.set_generic_arg`; TypeChecker helpers shrink to thin
       wrappers; `SymbolTable` takes a `typing` ref so `narrow` /
       `exclude` read children via the table; ~210 test sites in
       `test_typecheck.py` migrated; `_hash_type_structure` deleted
       as dead code), `ebe440e` (H.5.c: `ZType.children`,
       `ZType.generic_args`, and `ZType.resolve_child_by_id` removed
       — flat tables become the sole source). `children_id_map`
       survives as the per-process id allocator backing
       `child_id_for`; deferred F5.H.5.d would collapse it once
       narrowing/dotted-path stamping is rewritten to mint ids
       directly off `typing.type_child` rows.
       `make check`, `make test` (1943), and byte-identical C across
       all 85 examples verified at every sub-commit. Honest line
       accounting (post-`0dce458` tighten) for the affected src/
       subset (8 files): 22,804 → 22,999 (+195). Breakdown:
       `ztyping.py` +143 (accessor API + 2 row dataclasses — replacing
       per-ZType dicts with a relational table materialises a query
       surface that wasn't there before),
       `ztypecheck.py` +57 (plumbed `self.typing` params, ruff
       multi-line wraps, `cast(ZType, ...)` at 9 sites where dict
       subscript was non-Optional and `child_of` returns Optional),
       `zenv.py` +9 (typing param + `child_of`/`children_of` swaps),
       `ztypeutil.py` +3, `zsqldump.py` +2, `zemitterc.py` +5,
       `ztypes.py` -7 (children/generic_args dicts + resolve_child_by_id
       removed), `zasthash.py` -17 (dead code).
       The architectural goal — single flat table consumable by SQL
       dump (basis for F6's per-table dump rework) — is achieved;
       the line count is the cost of materialising an accessor API
       where dict syntax used to suffice.)*
6. [ ] F6 — `zsqldump.py` table-flat shape (scope_log,
       narrowed_subtype child table, source_map index).
7. [x] F1 — IO-wrapper natives as a data table (resolved 2026-04-28;
       nodeid-keyed variant deferred to F4).
8. [ ] F7 — pick one subsystem (recommend vtable emission) as the
       next template target.

### Phase 4 — Documentation

Can run in parallel with Phase 3.

9. [ ] F12 — `compiler.pdoc`, `roadmap.pdoc`, `Design-OPEN.pdoc`,
       `zparser.py:271-272`, `zemitterc.py:1585-1626` docstring.

---

## Bootstrap-lint expansion proposal (target end-state)

After Phase 2, `Makefile:24-26` should read approximately:

```
# Baseline counts of existing violations (update when migrating away)
# isinstance:0  comprehension:14  lambda:0  try/except:8  hasattr:16
# getattr:N_GETATTR  startswith:N_STARTSWITH  name-literal-compare:N_NAMELIT
# name-compare:14 (Phase 7e — cross-structure .name ==/!= in src/*.py)
```

…with `N_*` filled in at the post-cleanup numbers, plus the three
additional lint blocks shown in F3.

---

## Verification

This document changes nothing in `src/`. Verification steps:

1. `make check` should still pass — only documentation added.
2. Each finding F1–F13 has: severity tag, `src/...:LINE` citation,
   `[ ]` / `[x]` action items.
3. Each `## Phase` block contains at least one finding.

Each finding's resolution is its own follow-up commit.
