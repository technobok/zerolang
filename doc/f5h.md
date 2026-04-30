# F5.H — flatten ZType.children + ZType.generic_args

Sole remaining F5 sub-item from `doc/codereview20260428.md`.
Highest blast radius of all F5 sub-items per the original plan.
Tip: stage in 4–6 commits, never skip byte-identical C verification.

## Where we are

HEAD `512a8fe`. F5.A through F5.G done; F5.B partial (ResolverState
deferred). `ztypecheck.py` 10,763 → 10,263 lines; the five monster
functions from codereview20260428 §F5 are all decomposed (largest
residual is `_synth_collection_methods` at 371 lines, cohesive).
`zast.Program` is `@dataclass(frozen=True)`; typecheck output lives
on `ztyping.Typing` in `src/ztyping.py`; the typed-tree mirror
module `ztypedast.py` is deleted.

154 `ZType.children[...]` sites and 29 `ZType.generic_args` sites
in `src/` — that's the F5.H surface area.

## Goal

Replace the dict-valued attributes

    ZType.children: Dict[str, ZType]
    ZType.generic_args: Dict[str, ZType]

with flat relational tables. Rationale: dump-friendliness (one row
per parent × child key), self-hostable in zerolang (zerolang-side
ports of the compiler can use a `List[Record]` without per-instance
dicts), faster iteration in some hot paths.

Endpoint shape (note: per F5.E.5, typecheck output lives on
`Typing`, not on `Program` as the original F5.H plan said):

```python
@dataclass
class TypeChild:
    parent_type_id: int
    child_name: str
    child_name_id: int    # already minted by Phase 7b children_id_map
    child_type_id: int
    position: int

@dataclass
class TypeGenericArg:
    parent_type_id: int
    param_name: str
    arg_type_id: int
```

These tables hang off `ztyping.Typing` as

    typing.type_child: List[TypeChild]
    typing.type_generic_arg: List[TypeGenericArg]

`ZType.children` stays as a transitional property until consumers
migrate; final state is to remove the dict and have a `children_of()`
helper on `Typing`.

## Approach

Follow the F5.E pattern: introduce the new tables alongside the
existing dicts first, write through both during typecheck, then
migrate consumers one at a time. Only remove the dict after all
consumers are off it.

Suggested staging:

- **F5.H.1** — add `TypeChild` + `TypeGenericArg` dataclasses to
  `ztyping.py`. Add empty `type_child` / `type_generic_arg` fields
  to `Typing`. No data flow yet. Just the destination.
- **F5.H.2** — at every `ZType.children[k] = v` write site in
  `src/ztypecheck.py`, also append a `TypeChild` row. Same for
  `generic_args`. Now the data lives in both places. ~50 write sites.
- **F5.H.3** — relocate `ztype.children` consumers (zemitterc.py,
  sqldump, ztypeutil) one cluster at a time. ~80 consumer sites.
- **F5.H.4** — relocate `ztype.generic_args` consumers (~29 sites).
- **F5.H.5** — remove `ZType.children` / `.generic_args`; collapse
  the `children_id_map` (Phase 7b) which becomes redundant once
  the table is authoritative.

## Verification (every commit)

```bash
make check       # ruff + ty + bootstrap-lint
make test        # 1943 tests; full suite includes emitter+gcc
make build       # all 85 examples
diff -r /tmp/zerolang_out_baseline/ out/ --brief
                 # byte-identical C output
```

Snapshot the baseline before starting (the existing
`/tmp/zerolang_out_baseline/` is from a prior session and may be
stale):

```bash
make build && rm -rf /tmp/zerolang_out_baseline
cp -r out/ /tmp/zerolang_out_baseline/
```

## Critical files

| File | Role |
|---|---|
| `src/ztypes.py` | `ZType` definition; `.children` / `.generic_args` live here |
| `src/ztyping.py` | `Typing` container; new tables go here |
| `src/ztypecheck.py` | most write sites |
| `src/zemitterc.py` | most read sites (~80) |
| `src/zsqldump.py` | children-walker collapses to direct table dump |
| `src/ztypeutil.py` | collection-type predicates use `.children` |

## Existing patterns to reuse

- **Phase 7b `children_id_map`** (`ztypes.py:214-215`): proven lazy
  name→id parallel dict pattern. `type_child` becomes the
  authoritative form.
- **F5.E.2 / F5.D rebase scripts** (Python regex over `self.X.field`)
  work for the systematic accesses.

## Constraints (no python-only idioms in new code)

- No list/dict comprehensions in new code (loop form).
- No closures in new code.
- No `getattr` / `setattr` / `**kwargs`.
- String compare once, then key by id (already the existing rule).

## Reference plans

- `~/.claude/plans/goofy-twirling-turtle.md` — F5.E plan
  (completed). Contains the original F5.H high-level outline at the
  bottom; treat its `Program`-based endpoint as superseded by the
  `Typing`-based endpoint above.
- `doc/codereview20260428.md` — F5 status (F5 entry) + F5.H
  description.
- `doc/typed_tree_migration.md` — background on data-flow patterns.

## First step

```bash
cd /home/pawe/dev/zerolang
make build && rm -rf /tmp/zerolang_out_baseline
cp -r out/ /tmp/zerolang_out_baseline/
git status   # should be clean
```

Then begin F5.H.1: add `TypeChild` + `TypeGenericArg` dataclasses
to `ztyping.py` and the empty fields to `Typing`. Verify
`make check` + `make test` (1943) + byte-identical C across 85
examples.
