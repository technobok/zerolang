# Code Review — 2026-04-28

## Executive Summary

Since the 2026-03-29 review the compiler has absorbed several large
phases (atomic-call refactor, lock-escape, born-borrowed ownership,
falsy-first, for-loop iterator overhaul, borrow-correctness) and the
front-end is now in genuinely good shape: `isinstance` is held at 1 by
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
   `copy.deepcopy(func)` for monomorphization, the last `isinstance`,
   stale TODOs, the `getattr`-driven visitor in `zsynth.py`.
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
- [ ] After F4 lands: add `startswith` and name-literal-compare baselines.
- [~] Update the comment block at `Makefile:24-26` to reflect the
      expanded set. *(getattr line added; startswith / name-literal
      pending F4.)*

### F4. String-literal compares in the C emitter — High (goal 4)

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
- [ ] Add `Entry.is_receiver: bool`; set in parser; replace the four
      `pname == "this"` sites with the bool.
- [ ] Add `BuiltinName`/`BuiltinFunc` enums for the small set of
      compiler-known names (audit `grep -nE '"(this|String|StringView|parseF64|envNames|none|some|err|ok|...)"' src/zemitterc.py`).
- [ ] Replace literal compares with enum compares.
- [ ] When F3's name-literal-compare lint goes in, set the baseline
      at the post-cleanup count.

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

Action items:
- [ ] Define the five dataclasses above.
- [ ] Move attributes off `TypeChecker` onto the records, one record
      at a time.
- [ ] Replace each save/restore prologue site (start at
      `src/ztypecheck.py:5437-5483`) with a single record swap.
- [ ] Each record has stable `*_id` fields where applicable, so
      `zsqldump.py` can dump them as one row each (links to F6).

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

### F8. `copy.deepcopy(func)` is non-portable — Low (goal 2)

`src/zast.py:301` uses `copy.deepcopy` inside `clone_function`, which
runs once per generic monomorphization. This won't survive a port to
zerolang (no equivalent reflection-driven deep copy). It also means
cloned nodes initially share their source `nodeid`, which has to be
patched up afterwards (verify whether this is currently consistent —
deferred to the action item below).

Action items:
- [ ] Replace `copy.deepcopy(func)` with an explicit nodetype-driven
      clone visitor in `zast.py` (one branch per `NodeType`).
- [ ] Renumber `nodeid` on cloned nodes during the visit so each
      clone has its own id; document the policy in `compiler.pdoc`.
- [ ] Remove `import copy` from `zast.py` once unused.

### F9. Single remaining `isinstance` — Low (goal 3)

`src/zparser.py:1621`: `if isinstance(field_node, zast.Path):`. `Path`
is the parent of `DottedPath` and `AtomId`; replace with:

```python
if field_node.nodetype in (NodeType.DOTTEDPATH, NodeType.ATOMID):
```

…or add `field_node.is_path` discriminator if other sites need the
same check.

Action items:
- [ ] Replace the `isinstance` at `zparser.py:1621`.
- [ ] Drop the `bootstrap-lint` `isinstance` baseline from 1 to 0 in
      the same commit.

### F10. Stale TODO comments — Low (goal 6)

- [ ] `src/zast.py:266` — "TODO: change this into a single top level
      unit (not a dict)" — resolve or convert to issue.
- [ ] `src/zast.py:324` — "TODO: maybe Union(None, ZType,
      ZTypeCheckInProgress)" — decide and apply.
- [ ] `src/zparser.py:271-272` — docstring "pass2 is self.typecheck
      TODO: pass2 in a separate class" — `typecheck` is now in
      `ztypecheck.py`; update the docstring (links to F12).
- [ ] `src/zparser.py:541` — "TODO: this and type are predefined for
      units (?)" — investigate; remove the question mark either way.

### F11. `getattr`-driven visitor dispatch in `zsynth.py` — Low (goal 3)

`src/zsynth.py:120`: `handler = getattr(self, handler_name, None)` —
visitor name lookup by string. Replace with an explicit dispatch
table registered at class init:

```python
self._handlers: Dict[NodeType, Callable] = {
    NodeType.X: self._visit_x,
    NodeType.Y: self._visit_y,
    ...
}
```

This is the shape used in `zasthash.py` (`:72`); making `zsynth.py`
match removes one `getattr` and lines the file up with the rest of
the front-end.

Action items:
- [ ] Replace the `getattr` lookup in `zsynth.py` with an explicit
      `Dict[NodeType, Callable]`.

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

### F13. Carry-forward audit from `codereview20260322.md` — Low (goal 6)

That review left several items in unclear status. Resolved as of
2026-04-28:

- [x] Debug `print(...)` statements in parser — none remain.
- [x] `_fixcalloperation` stub — removed.
- [x] Big commented-out blocks in `zlexer.py` — none ≥15 lines remain.

Still open:

- [ ] **Lexer private state read from parser**: `src/zparser.py:548,
      2435, 2648` read `lex._filtereol` directly. Add a public
      `Lexer.filtereol_state -> bool` accessor (or just remove the
      leading underscore) and switch the parser to it. The parser
      also calls `lex.filtereol(...)` correctly through the public
      API, which is the existing contract — only the read side needs
      a public hook.
- [ ] **Typo in lexer docstring**: `src/zlexer.py:788` says
      "undercores"; should be "underscores".
- [ ] **Source-map zip truncation**: `src/zsqldump.py:424` —
      `zip(c_lines, emitter.source_map)` will silently drop rows on
      mismatch. Use `zip(..., strict=True)` and pre-assert lengths
      (links to F6).
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

- [ ] F8 — replace `copy.deepcopy(func)` with explicit clone visitor.
- [ ] F9 — replace last `isinstance`; drop lint baseline 1 → 0.
- [ ] F10 — close out the four `TODO` comments.
- [ ] F11 — `zsynth.py` visitor → `Dict[NodeType, Callable]`.
- [ ] F13 — `_filtereol` accessor, "undercores" typo,
      `zip(..., strict=True)` in `zsqldump.py`.

### Phase 2 — Back-end cleanup (sequential)

Each step depends on the previous one. F4 is the largest.

1. [x] F2 — pre-initialize `Program` metadata fields; drop defensive
       `getattr`s. *(Resolved 2026-04-28; count 125 → 4.)*
2. [x] F3a — add `getattr` baseline to `bootstrap-lint`.
       *(Resolved 2026-04-28; baseline 4.)*
3. [ ] F4 — `Entry.is_receiver`, `BuiltinName`/`BuiltinFunc` enums,
       replace literal compares.
4. [ ] F3b — add `startswith` and `name-literal-compare` baselines.

### Phase 3 — Architecture

Larger refactors; do in order.

5. [ ] F5 — TypeChecker context records (`ResolverState`,
       `FunctionContext`, `BorrowState`, `MonoState`, `TemplateIds`).
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
