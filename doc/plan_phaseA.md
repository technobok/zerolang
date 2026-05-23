# Phase A Plan — Four Language Items Before Self-Host

Status: draft, awaiting approval.
Strategy parent: `doc/bootstrap.pdoc`.
Date: 2026-05-23.

The four items below are the genuinely-open Phase A items, in the order the user requested. Each gets its own section with context, scope, approach, and verification. A short sequencing summary follows.

## Items, in order

1. ~~Branch ownership flow tracking for reftypes~~ — CLOSED 2026-05-23
2. ~~Field-access locking for reftypes~~ — CLOSED 2026-05-23 (already done via path-scoped locks)
3. ~~Stable iteration order for `Set` (and `Map`)~~ — CLOSED 2026-05-23 (compact-dict rewrite)
4. Inline definitions in `as` blocks

## Sequencing summary

- **Items 1 and 2 are orthogonal but related.** Both touch ownership/lock enforcement in the typechecker; both share scope-walking and assignment paths (`_check_assignment`, `_check_dotted_path_inner`). Doing them back-to-back keeps the mental model warm.
- **Item 3 is isolated to the emitter** (`src/zemitterc.py`) plus the runtime templates. It doesn't touch the typechecker.
- **Item 4 is isolated to the parser** (`src/zparser.py`). It doesn't touch the typechecker or emitter.

The user-requested order (1→2→3→4) is also the "hardest first while context is warm" order. Recommend keeping it.

If at any point the team wants a break, item 4 (parser-only, ~30-50 lines, low risk) is a good palate cleanser to slot in.

---

## Item 1 — Branch ownership flow tracking for reftypes

### Context

Original deferral from `doc/roadmap.pdoc` Phase 7:

> Track ownership flow through if/match/for branches: if a variable is taken in one branch, it must be taken in all branches (or not used after the branch). [DEFERRED — requires reftype usage in branches; valtypes are always owned by copy so this rule is trivially satisfied for v1 programs.]

The deferral condition has lifted. Classes (reftypes) are in wide use; reftype-with-`.lock`-field patterns exist in shipped examples (`examples/borrowed_record.z`, `examples/ListIter.z`).

What works today: `_check_if_inner` (`src/ztypecheck.py:10809-10961`) snapshots `live_before`, restores per-arm, tracks `taken_in_any_arm`, and invalidates at the join. For **valtypes** this is sufficient — no destructor at scope exit. For **reftypes** the destructor *does* run at scope exit, so a divergent ownership state at the join causes the destructor to fire on a moved-from value in some path → memory corruption.

### Decided semantics (user direction 2026-05-23)

The rule is **not** "error if not taken in all arms." It is:

> If a reftype variable is ownership-transferred (`.take`, `.box`, `.release`, return, etc.) in any arm, then in every arm where the transfer did NOT happen, the compiler must auto-insert the destructor call before that arm's terminator. After the join, the variable is invalidated.

This matches `doc/ownership.pdoc:263-276` (".take in Conditional Arms" rule) and avoids forcing the user to write balanced takes by hand.

### Scope

Two sub-rules to enforce:

1. **Auto-destroy on ownership divergence.** Whenever the set of arms that ownership-transferred `v` is a strict subset of all arms, the emitter inserts destruction in the complementary arms.
2. **Lock-state consistency.** If `v` holds a `.lock` on another value, the auto-destroy must release that lock symmetrically across arms. The post-join lock table must be the same on all paths.

Liveness is preserved by construction: the destruction is inserted at the arm's terminator, after the last legitimate use. There is no "use-after-destroy" risk because the join invalidates the name.

### Approach

1. **Identify divergent reftype variables at arm-exit.** Extend the existing per-arm tracking in `_check_if_inner` / `_check_match_inner` to record, per arm, the set of reftype variables that were ownership-transferred. At the join, compute the divergence: `taken_in_any_arm \ taken_in_this_arm`.
2. **Emit destruction calls in non-take arms.** Hook into the emitter so that, when an arm's terminator is reached, the divergence set is destructed in reverse-declaration order (matching the standard scope-exit order). This reuses the existing destructor-emission path; the new piece is *where* it fires.
3. **Lock release falls out of destruction.** A reftype's destructor already releases its held locks. Symmetric destruction implies symmetric lock release. No new lock-table logic needed at the join — only confirm via tests.
4. **Loops.** A reftype `.take`-d inside a loop body executes 0+ times. After the second iteration, the second `.take` would fire on a destroyed value. This case is structurally always wrong and must be a typecheck error (not an auto-insert), because there is no arm structure to balance. Confirm the rule fires for reftypes (it already does for valtypes via the `taken_in_loop_body` check).
5. **Diagnostic on the loop error.** Point at the take site inside the loop body.

### Verification

- New tests under `tests/test_typecheck.py::TestBranchOwnership` and `tests/test_emitter.py::TestBranchOwnership` covering:
  - Reftype taken in one arm, untaken in other → compiles; emitted C destructs in the untaken arm; runs without leak.
  - Reftype taken in all arms → compiles; no extra destruction inserted; runs without double-free.
  - Reftype with `.lock` field, taken in one arm → emitted destruction in the other arm releases the lock; post-join lock table is empty in both paths.
  - Match expression with mixed take/non-take arms → same auto-insert behavior.
  - `for` body taking a reftype → typecheck error (no auto-insert in loops).
- Memory check: run the new examples under valgrind (or whatever the project's leak-check is) — no leaks, no double-frees.
- All existing tests still pass.
- `make check && make test` green.

### Files touched

- `src/ztypecheck.py` (`_check_if_inner`, `_check_match_inner`, `_check_for_inner`, divergence-set computation).
- `src/zemitterc.py` (per-arm destructor insertion at the terminator; reuses existing destructor emission).
- `tests/test_typecheck.py` and `tests/test_emitter.py` (new test classes).
- `doc/ownership.pdoc` (clarify the "auto-destroy in non-take arms" rule with a concrete example; HTML rebuilt per `[[feedback_zerolang_rebuild_docs]]`).
- `doc/roadmap.pdoc` Phase 7 (promote from `[DEFERRED]` to `[RESOLVED]`).

---

## Item 2 — Field-access locking for reftypes

### Context

Original deferral from `doc/roadmap.pdoc` Phase 8:

> Implement: field access locking — shared lock on parent, exclusive on field. [DEFERRED — requires reftype fields (classes) for meaningful testing.]

The deferral condition has lifted. The infrastructure is in place:

- Path-scoped locks (`[[project_zerolang_path_scoped_locks]]`): SHARED on prefixes + EXCLUSIVE on leaf, sibling paths independent.
- Lock-escape enforcement (`[[project_zerolang_lock_escape]]`): G1/G2/G3 landed for value-level paths.

What's in place but incomplete: `_check_dp_borrow` (`src/ztypecheck.py:7743-7774`) sets `_pending_borrow_lock` from `_get_dotted_path_tuple(path.parent)`, and `_check_dotted_path_inner` (`:7830+`) resolves fields. Locks are applied on `.borrow` / `.lock` *expressions*, not on plain field read/write of a locked reftype.

Existing test `test_class_factory_with_lock_field_arg_retains_source_lock` (`tests/test_typecheck.py:1485-1502`) confirms whole-instance lock enforcement works. Per-field cases (lock one field, mutate another) are not separately enforced.

### Decided semantics (user direction 2026-05-23)

**Apply at both reads and writes.** Read needs SHARED on the prefix; write needs EXCLUSIVE on the leaf. This is the full multi-granularity rule matching the existing path-scoped lock model. Field reads consult the lock table just like field writes.

### Scope

1. **Plain field write while locked.** If `b` has an exclusive lock outstanding (via `.lock` field on another value `it`), then `b.x = 99` should error. Today the whole-instance case errors; the per-field case may not.
2. **Per-field locks for disjoint paths.** `b.x.lock` + `b.y = 99` allowed (disjoint paths). `b.x.lock` + `b.x.subfield = 99` errors (overlapping).
3. **Reads consult the lock table.** `b.x.lock` (exclusive) + read of `b.x` errors. `b.x.lock` (shared) + read of `b.x` ok. Disjoint reads (`b.y`) always ok.

### Approach

1. **Extend the path-tuple check at field-write sites.** Field-write goes through `_check_assignment` for dotted-path LHS. Before allowing the write, compute the path tuple `(root_var_id, field_id_1, ...)` and check the active lock table for any lock whose path overlaps.
2. **Same check at field-read sites.** Hook into `_check_dotted_path_inner` for the read path. Reads check the table for an exclusive lock on any prefix of the read path; shared locks are read-compatible.
3. **Reuse `_get_dotted_path_tuple` and the overlap predicate.** Both exist for borrows; do not reinvent.
4. **Audit step.** Before landing, grep `examples/` and `tests/` for class field accesses alongside `.lock` fields and confirm no shipped pattern is newly rejected. If any are, decide case-by-case whether to tighten the example or refine the rule — but do not silently weaken the rule.

### Verification

- New tests under `tests/test_typecheck.py::TestFieldAccessLocking` covering:
  - Whole-instance lock + sibling field write → error (current behavior — confirm).
  - `b.x.lock` + `b.y = 99` → ok (disjoint).
  - `b.x.lock` + `b.x.sub = 99` → error (overlapping).
  - `b.x.lock` (shared) + read of `b.x` → ok.
  - `b.x.lock` (exclusive) + read of `b.x` → error.
  - `b.x.lock` + read of `b.y` → ok (disjoint).
- Differential C-output check on `examples/*.z`: byte-identical before/after (this is enforcement, not codegen).
- `make check && make test` green.

### Files touched

- `src/ztypecheck.py` (`_check_assignment`, `_check_dotted_path_inner`).
- `tests/test_typecheck.py` (new test class).
- `doc/ownership.pdoc` (clarify the field-level rule with read-vs-write granularity; HTML rebuilt).
- `doc/roadmap.pdoc` Phase 8 (promote to `[RESOLVED]`).

---

## Item 3 — Stable iteration order for `Set` (and `Map`) — CLOSED 2026-05-23

Closed via the CPython compact-dict rewrite of `_emit_mono_map`,
`_emit_mono_set`, and the three iterator runtimes in `src/zemitterc.py`.
8 new tests in `TestSetMapInsertionOrder`. Full suite green. See
`[[project-zerolang-compact-dict]]` memory entry. The remaining text in
this section describes the plan as executed; kept for reference.

### Context

Original deferral from `doc/roadmap.pdoc` Phase 53:

> Bucket layout iteration order — not insertion order. Stable iteration deferred.

Today both `Set.iterate` and `Map.iterate` walk the bucket array in layout order (hash modulo capacity), so iteration order varies with hash seed and capacity. The `doc/stdlib_collections.pdoc` API explicitly warns users not to rely on order.

Self-host motivation: a compiler in zerolang will use `Map<id, *>` and `Set<id>` heavily (symbol tables, visited sets, monomorphization caches). For byte-identical C output between Python compiler and zerolang compiler (a verification gate from the bootstrap strategy), the zerolang compiler's emission order must be deterministic. Today it can't be, because the underlying hash collections aren't.

### Scope

Both `Map` and `Set`. They share the bucket primitive in `src/zemitterc.py`. Iteration becomes insertion-order. The user-facing API doesn't change; only the runtime structure and `_emit_setiter_runtime` / `_emit_mapiter_runtime` change.

### Decided design (user direction 2026-05-23): CPython compact-dict layout

Adopt the CPython-style compact dict (PEP 468, Raymond Hettinger). Two arrays:

```c
typedef struct {
    uint64_t  capacity;        // size of indices[]
    uint64_t  length;          // live entry count (excludes tombstones)
    uint64_t  entries_len;     // total appended (including tombstones)
    uint64_t  entries_cap;     // allocated size of entries[]
    int64_t  *indices;         // sparse: EMPTY (-1), DELETED (-2), or index into entries
    z_{Name}_entry_t *entries; // dense: (hash, item) in insertion order
} z_{Name}_t;
```

**Insert** `x`:
1. `h = hash(x)`; linear-probe `indices[h % capacity]` until EMPTY or a matching live entry.
2. Append `(h, x)` to `entries`; capture `i = entries_len++`.
3. Write `i` into the probed `indices` slot.
4. Increment `length`. Resize when `length > capacity * 2/3`.

**Delete** `x`:
1. Probe `indices` to find the slot. Read `i = indices[slot]`.
2. Write `DELETED` into `indices[slot]` (sparse tombstone — needed so subsequent probes continue past).
3. Mark `entries[i]` tombstoned (sentinel hash value, e.g. `UINT64_MAX`).
4. Decrement `length`. **Do not compact** — that's resize's job.

**Iterate**:
- Walk `entries[0..entries_len)` in order, skipping tombstones. Order is insertion order by construction.

**Resize** (triggered by `length > capacity * 2/3`):
1. Allocate new `indices` (e.g. `2 * capacity`), filled with EMPTY.
2. Allocate new `entries` sized to `length` (compacted).
3. Walk old `entries` in order; for each live entry, append to new `entries` and re-probe into new `indices`. Tombstones dropped.

**Memory cost**: roughly comparable to today. Current per-bucket footprint (state + hash + item, padded) is around 24 bytes for u64 items × capacity. New layout: `indices` is `8 * capacity` bytes plus `entries` at `(hash + item) ≈ 16 bytes × ~length`. At 2/3 load, total is about `8c + 10.6c ≈ 18.6c` — a slight win.

### Deferred Python optimizations (record for future followup)

These match CPython's mature dict implementation but are not required for the initial landing. Record in a memory entry / open issue so they're not lost:

1. **Variable-width indices array.** CPython picks `int8_t`, `int16_t`, `int32_t`, or `int64_t` for `indices` depending on `entries_cap`. Saves significant memory for small maps. Add later if profiling shows memory pressure.
2. **Small-resize on tombstone density.** CPython triggers a same-capacity rebuild when tombstones exceed a threshold without growth — bounds iteration overhead on delete-heavy workloads. Initially we only compact on growth resize. Add later if delete-heavy patterns surface.
3. **Combined-table vs split-table for shared keys.** CPython's optimization for object instances sharing a key set. Not relevant to zerolang Set/Map at v1; revisit if/when keyword/attribute interning matters.

### Verification

- New tests under `tests/test_emitter.py::TestStableIteration` covering:
  - Insert (a, b, c); iterate → (a, b, c).
  - Delete b; iterate → (a, c).
  - Insert d after delete; iterate → (a, c, d).
  - Resize trigger (insert past 2/3 load factor); iterate → original order preserved, tombstones dropped.
  - Same checks for `Map`.
- `examples/set_uniq.z` and similar examples produce deterministic output across runs.
- A regression run: compile the example corpus twice with different hash seeds, diff the binaries' stdout; must be byte-identical (this is the bootstrap-strategy verification gate).
- `make check && make test` green.

### Files touched

- `src/zemitterc.py` (Set/Map struct emission, insert/delete/resize/lookup templates, `_emit_setiter_runtime`, `_emit_mapiter_runtime`).
- `src/zemitterc_runtime.py` (shared hash/probe scaffolding if any moves).
- `tests/test_emitter.py` (new test class).
- `doc/stdlib_collections.pdoc` (remove the "do not rely on order" warning, document insertion-order guarantee, update HTML).
- `doc/roadmap.pdoc` Phase 53 (promote to `[RESOLVED]`).
- A new memory entry `project_zerolang_compact_dict_followups.md` recording the three deferred optimizations.

---

## Item 4 — Inline definitions in `as` blocks

### Context

Original deferral from `doc/roadmap.pdoc` Phase 49d:

> Inline definitions (data, records) in `as` — deferred (references to external definitions cover the common cases).

Parser change only. Today `_get_object_body` in `src/zparser.py:1474-1594` accepts functions, units, and constant expressions in `as` blocks but rejects nested `record`, `class`, `variant`, `union`, `data` definitions with a generic error.

The typechecker requires no changes: `_process_as_items_protocols` already iterates heterogeneous `as_items` dicts and dispatches on `nodetype`; nested scope is safe because the parent type is fully resolved before `as` is processed.

The deferral was a design call ("external refs are good enough for Phase 49"), not a structural blocker. Use-case evidence in the wild: `examples/borrowed_record.z` defines a `CView` class at unit level solely to be referenced by `Container.slice`. Inlining would clarify intent and contain the helper's namespace.

### Decided visibility model (user direction 2026-05-23)

Inline definitions inside `as` follow the same namespace and visibility rules as definitions in the parent type's other member sections. The model:

- **Public by default.** All items inside an `as` block (functions, units, constants, *and now* inline type definitions) are publicly accessible via qualified path, e.g. `Container.CView`.
- **Visibility controlled by an explicit `public:` unit.** If the `as` block contains a unit-typed field named `public`, that unit lists exactly the items that are public; everything else in the `as` block becomes private (callable only from sibling methods in the same `as`/`is` namespace).
- This is an opt-in *manifest* model. The mere presence of `public:` flips the default from "all public" to "only listed items public."

Example (after this item lands):

```zerolang
Container: class { x: i64 y: i64 } as {
    CView: class {                                  # inline definition
        offset: i64
        source: Container.private.lock
    } as { ... }

    slice: function {c: this.lock} out CView is { ... }

    public: unit {                                  # optional public manifest
        slice                                       # slice is public
        # CView omitted → private
    }
}
```

If the `public:` unit is absent, both `CView` and `slice` are public. If `public:` is present, only items it lists are public.

### Scope

1. Allow `record`, `class`, `variant`, `union`, `data` (and any other type-defining keywords) inside `as` blocks.
2. The inline definition's qualified name is `ParentType.InlineName`.
3. The `public:` manifest mechanism applies to inline type definitions the same way it applies to functions and other items today.

### Approach

1. **Parser branch.** In `_get_object_body`, add detection for item-definition keywords (`TT.RECORD`, `TT.CLASS`, `TT.VARIANT`, `TT.UNION`, `TT.DATA`). Call the existing `_accept_item_definition` (or its current name; confirm during implementation). Merge externs as is done for functions/units.
2. **Namespace promotion.** Confirm the nested definition's mangled name uses the parent's path as prefix. Verify with a `make build` example.
3. **Forward references.** Methods inside `as` referencing the nested type by short name must resolve. The existing dispatch already handles this for label-paths; verify for type references.
4. **Public-manifest integration.** Verify that the existing `public:` unit handling (for functions today) extends to type definitions without modification. If it doesn't (e.g. the lookup is keyed on a specific item kind), generalize it.

### Verification

- New tests under `tests/test_parser.py::TestInlineAsDefinitions` and `tests/test_typecheck.py` covering:
  - Inline `class` in `as`, referenced by a sibling method → ok.
  - Inline `record` in `as` → ok.
  - Inline definition referenced from outside as `Container.CView` (no `public:` block) → ok (public by default).
  - Inline definition NOT listed in a `public:` manifest → external reference rejected; sibling reference ok.
  - Inline definition WITH the same name as a unit-level definition → parses cleanly as a different scope; qualified path disambiguates.
  - `examples/borrowed_record.z` refactored to inline `CView` (`Container.CView`) — confirm the example still builds and runs.
- `make check && make test` green.

### Files touched

- `src/zparser.py` (`_get_object_body`, ~30-50 lines).
- `src/ztypecheck.py` (only if public-manifest handling needs generalization to type items; expect zero changes).
- `tests/test_parser.py` and `tests/test_typecheck.py` (new tests).
- `examples/borrowed_record.z` (optional refactor to use the new form).
- `doc/grammar.pdoc` (update the `as` body production).
- `doc/spec.pdoc` (note inline definitions are allowed; document the public-manifest model if it isn't already documented).
- `doc/roadmap.pdoc` Phase 49d (promote to `[RESOLVED]`).
- HTML regen.

---

## Cross-cutting verification

After all four land, before tagging `python-stage0`:

1. `make check && make test` green.
2. Full example corpus builds and runs.
3. Bootstrap-lint baselines unchanged or tightened.
4. `doc/roadmap.pdoc` reflects all four `[RESOLVED]`.
5. `doc/bootstrap.pdoc` Phase A list updated; Phase A marked complete; ready for the tag.

## Decisions log

All four pre-start questions resolved 2026-05-23:

- **Item 1** — Semantics: auto-destroy in non-take arms (not error). Liveness preserved by inserting destruction at arm terminator; lock release falls out of symmetric destruction. Loops keep the error path (no auto-insert in loops). Rule generalizes across `.take`, `.box`, `.release`, return.
- **Item 2** — Apply at both reads and writes. Full multi-granularity per the existing path-scoped lock model.
- **Item 3** — CPython compact-dict layout with tombstones. Three Python optimizations recorded as future followups: variable-width indices, small-resize on tombstone density, combined-vs-split tables.
- **Item 4** — Public by default; explicit `public:` unit acts as a manifest that flips the default to private-by-omission. Same rules as elsewhere in the language's `as`/`is` member sections.
