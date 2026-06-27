# Self-host: remaining work & compiler0 sunset

Planning inventory of what's left to *complete* the self-hosted zerolang compiler
(`src/*.z`) and *retire* compiler0 (the Python reference, now isolated in
`compiler0/*.py`). See `doc/bootstrap.pdoc` for the phase strategy; this doc is
the concrete task list.

## Status

The port is **functionally complete and self-hosting**:
- Every core compiler module has a `.z` port (table below); the language was frozen
  for the port on 2026-05-23 (`doc/bootstrap.pdoc` Phase A).
- Byte-identity fixpoint holds: `emit(zc, stage1) == emit(zc, stage2)`
  (`tests/test_fixedpoint.py`, landed `5d439a5`).
- `os.spawn` (`85ac2af`) + `src/ztestrunner.z` (`6e44463`) make the corpus gate
  runnable with no shell/Python at gate time (`make test-corpus-z` reproduces
  `run_corpus.sh`).

**FROZEN 2026-06-27 (`python-stage0-final`).** compiler0 is no longer in the build
or test path: the build bootstraps from the committed `bootstrap/zc.c` seed
(`make`/`tests/conftest.py` default to it; `BOOTSTRAP=python` is an opt-in escape
hatch), and the suite compares the port against committed goldens — the
port-vs-reference differentials and the Python-internal suite are retired
(`doc/freeze_audit.md`). compiler0 stays in-tree as a frozen historical artifact
(`compiler0/README.md`); its last in-tree consumer is the style linter
(`tools/lint_style.py`).

What remains is **(1)** the style-linter `.z` port + `rm compiler0/`, **(2)**
deferred language features, **(3)** deferred port internals. None block
self-hosting or the freeze; they are *polish*.

## 1. Module parity (`compiler0/*.py` ↔ `src/*.z`)

**Ported (12, dual today):** `zast`, `zc`, `zemitterc`, `zenv`, `zgenerator`,
`zlexer`, `zparser`, `zsqldump`, `ztypecheck`, `ztypes`, `ztyping`, `zvfs`.

**Python-only (11)** — disposition for sunset:

| Module | Role | Disposition |
| --- | --- | --- |
| `zemitterc_runtime` | emits the runtime preamble; *loads* the 104 `src/runtime/natives/*.inc` (canonical) via `_load_native` at import, like the 3 top-level `.inc` | retires with `.py` (no longer the fragment source-of-truth; `export_native_fragments.py` + the sync test are gone) |
| `zemitterc_templates` | pure loader/substituter for the hand-authored `src/runtime/*.c.tmpl` (already canonical) | retires with `.py` |
| `zastdump`, `ztokendump` | dump oracles, now demoted to reference cross-checks (`test_python_{parser,lexer}_matches_golden`) — golden *regen* is `.z` (`make regen-goldens`, §2) | retire with `.py`; the `tools/{lexdump,astdump}.py` regen wrappers are already deleted |
| `zasthash` | AST content-hash for mono dedup | **superseded** — the port dedups monos *by name/key* (`ztypecheck.z:5104`), not content-hash; retires with `.py` |
| `zchar`, `zcharclass` | lexer char tables | logic lives in `zlexer.z`; retire with `.py` |
| `zsynth`, `ztypeutil` | typecheck helpers | logic lives in `ztypecheck.z`/`ztypes.z`; retire with `.py` |
| `ztokentype` | `TT` enum for the Python modules | `.z` side has its own token kinds; retire with `.py` |
| `zsymtab_proto` | live typing-time `Protocol` (`ZSymbolTableProto`) imported by `ztyping.py` to type `symbol_table` without importing `zenv` — breaks the `zast`↔`zenv` cycle | retires with `.py` (not separately deletable: `ztyping.py:130` uses it) |

**`.z`-only (1):** `ztestrunner` (no Python counterpart by design).

## 2. Compiler0 sunset blockers

Concrete work before `compiler0/*.py` can be deleted:

- **Runtime artifact source-of-truth.** **RESOLVED** (promoted to canonical).
  All runtime artifacts are now committed-canonical files read from disk by both
  emitters: the 104 `src/runtime/natives/*.inc`, the 10 `src/runtime/*.c.tmpl`,
  and the 3 top-level `src/runtime/{z_String,z_StringView,z_hash}.inc`. The `.inc`
  natives used to live as `_Z_*` string constants in `zemitterc_runtime.py`
  (exported by `tools/export_native_fragments.py`, pinned by
  `test_native_fragments_in_sync`); the Python emitter now binds each `_Z_*`
  global by loading its `.inc` at import (`_load_native`), so the file is the
  single source — editing a fragment is a plain `.inc` edit, no Python. The
  export tool is deleted and the sync test replaced by a 1:1 present/unique guard
  (`test_native_fragments_present_and_unique`). The `.c.tmpl` and top-level `.inc`
  were already hand-authored canonical files (only loaded by Python, never
  generated).
- **Golden regeneration.** **DONE.** Lexer/parser/program goldens regenerate via
  `make regen-goldens`, which builds the standalone `.z` dump binaries with the
  ported `zc` (`out/zlexer`, `out/zparser`) and writes the `tests/fixtures/`
  goldens — no Python. The AST-dump "gap" is closed: AST dumping is folded into
  `out/zparser` (single-file `out/zparser <file>` and whole-program
  `out/zparser --program <dir> main`), so no separate `zastdump.z`/`--dump-ast`
  is needed. The Python regen wrappers `tools/lexdump.py` / `tools/astdump.py`
  are deleted; the `*_differential` `.z`-binary tests already pin
  goldens == `.z` output, and `test_python_{lexer,parser}_matches_golden` stays
  as the reference cross-check (retires with `.py`).
- **Bootstrap stage0.** **Python-free seed INTRODUCED** (`bootstrap/zc.c`): a
  committed, self-reproducing C dump of the compiler — `cc bootstrap/zc.c` builds
  a working `zc` with no Python; `make test-bootstrap` gates it (double-bootstrap
  fixpoint + a unit-to-golden correctness check), `make bump-seed` refreshes it
  (Zig/OCaml-style recent seed, bumped occasionally, *not* per commit; *not* a
  binary). See `bootstrap/README.md`. **DONE (freeze):** `make` (`ZC`) and
  `tests/conftest.py` (`_ZC`/`_seed_zc`) now default to the seed, and the
  corpus/leak/asan shell gates `cc bootstrap/zc.c` directly. `BOOTSTRAP=python` /
  `Z_BOOTSTRAP=python` remain as an opt-in fallback to `compiler0/zc.py`.
- **Reference-only oracles.** **DONE (freeze).** The port-vs-reference differentials
  and the `test_python_{lexer,parser}_matches_golden` cross-checks are deleted; the
  surviving lexer/parser/smoke tests pin port output == committed golden, fully
  `.z`-only. Coverage parity recorded in `doc/freeze_audit.md`.
- **Python-internal test suite.** **DONE (freeze).** The Python data-structure
  suites (`test_typecheck`/`test_emitter`/`test_parser`/`test_lexer`/`test_vfs`/
  `test_cli`/`test_asthash`/`test_runtime_templates` plus 6 more the freeze audit
  surfaced) are deleted; behavioral run/leak/error/dump goldens + the corpus replace
  their coverage (granularity loss accepted per `doc/bootstrap.pdoc`; audit in
  `doc/freeze_audit.md`).
- **Parallel-maintenance window + freeze.** **DONE 2026-06-27.** The self-host
  hardening that blocked this (latent UAF/double-free in the `.z`-built compiler on
  paths compiler0 always built — generators, facets, protocols, iterator,
  autoproject) was resolved 2026-06-26 (`make selfhost-asan` clean over the whole
  corpus; `doc/selfhost_hardening.md`). With it cleared, the documented ~6-month
  parallel window was **collapsed** (solo project, no external users, all gates
  green): Phase C verified + tagged `python-final-parallel`; the dual gate landed as
  a local `make ci` (no external CI); build/test defaulted onto the seed; the
  differentials + Python-internal suite retired; compiler0 frozen + tagged
  `python-stage0-final`.

## 3. Deferred language features

Verify each against HEAD before scheduling (sources are point-in-time):

- **Lean `for x: N loop` range-for** + intrange records — sugar for counted loops;
  deferred from the for-loop iterator overhaul. (`project_zerolang_for_loop_iterator`)
- **Inline `as`-block type definitions** — deferred-by-design; workaround is
  unit-level types referenced from the `as` block (`examples/borrowed_record.z`).
  (`project_zerolang_inline_as_deferred`)
- **`int.each` iterator-object** — deferred from the falsy-first/iterator work.
  (`project_zerolang_falsy_first`)
- **Owned-pair `Map` iteration** — deferred until a port slice needs it.
  (`project_zerolang_selfhost_readiness`)
- **Generic `Any.valtype` / `Any.reftype` constraints** and **protocol
  composition/inheritance** — partial (`from: <protocol>` works); the broader
  constraint/inheritance surface is future. (`doc/roadmap.pdoc`)

## 4. Deferred port internals

- **Emitter C-name full purity** — `project_zerolang_emitter_cname_purity` P3
  (variable names `z_v{id}_`) and P4 (native-fn symbols) are deferred; natives +
  locals stay canonical. The de-lookup ratchet is pinned at 0; do not revive
  `_resolved_type`.
- **Codereview deferrals** — F4 bucket D (~50 role-keyword string-compare sites)
  and F7 sub-candidates (protocol_impl/mono_map/create_functions) are
  deferred-by-design; revisit only on a concrete self-host blocker, don't
  pre-emptively enumify. (`project_zerolang_codereview20260428`)

## 5. Tooling / harness gaps

- **`ztestrunner` per-test timeout** — DONE (`66ad669`): `os.spawn` gained a
  native `timeoutSecs: i32` (alarm + SIGKILL → 124, like `subprocess.run(timeout=)`);
  `ztestrunner --timeout 60` applies it to the run/leak-binary spawns. (The trusted
  `zc`/`gcc` spawns are still un-timed — add later if a compiler hang surfaces.)
- **`cli_basic` arg-path leak** — DONE: was two bugs (41 B under args, not 15).
  (a) the native cli runtime (`_Z_CLI_PARSE.inc` required-checks +
  `_Z_PARSED_GET_{OPTION,POSITIONAL}.inc`) did `free(v.data)` on a `Map.get`
  result, but `get` returns an *owned* deep copy — leaking the inner bytes in
  both emitters (26 B); fixed by freeing/moving the owned result. (b) the port
  emitter's Map codegen (`mapDestroyEntries`/`mapFreeKeyDel` + `z_Map.c.tmpl`)
  freed only the key, never the value, at destroy/delete/overwrite — leaking
  owned values (15 B, port-only); fixed by adding a value-free (`mapValFreeStmt`,
  mirroring `emitStructDestructor`). Corpus gate green (`leak-clean=172 xleak=0`);
  `KNOWN_LEAKY` emptied in `run_corpus.sh` + `ztestrunner.z`.
- **`ztestrunner` "dump" case-kind** — DONE (first increment): the PORT dumper
  (`zsqldump.z` `dumpCanon`, behind `zc --dump-canon`) emits a canonical,
  id-normalized rendering (PK ids omitted, FK ids resolved to names, cnames
  dropped, rows sorted, filtered to the example's own unit); `ztestrunner.z` +
  `run_corpus.sh do_dump` compare the port's output to committed
  `tests/fixtures/dump_golden/*.canon` goldens (port-sourced via `--update`;
  correctness vouched for by the `test_dumpsql_z` pytest differential). Covers
  `unit` / `types` / `type_children` / `conformance` and the body-walk symbol
  table (`scope` / `entry` / `variable` / `narrowed_subtype`). **`typed_nodes` and
  mono are deliberately left to the `test_dumpsql_z` pytest differential, NOT the
  canon goldens:** the `typed_nodes` projection widens to `defined_in_unit IN (unit,
  'system', 'collections')`, so it is ~1300 rows/example dominated by library
  internals -- a bloated, noisy committed golden -- and its `name` column needs the
  contextual AST-walk label (no node-id -> node map exists). The pytest differential
  compares these in-memory and is the right tool. (A main-unit-only filtered canon
  would be small but diverges from the oracle's widened filter; revisit only if a
  Python-free `typed_nodes` gate is specifically needed.)
- **CLI parity (`zc.z` vs `zc.py`)** — RESOLVED. `zc.z` is now the primary CLI: a
  self-locating, go-style tool (`build`/`run`/`emit`/`dump`/`dump-canon` subcommands;
  bare `zc <unit>` builds a native executable; build/run via the system C compiler;
  `--target` cross-compile plumbing) with the legacy `--emit-c`/`--dump-sql`/
  `--dump-canon`/`--src`/`--system`/`--runtime`/`--full` flags preserved so the
  harness is unchanged. See `doc/zc.pdoc`. (`zc.py` keeps its minimal CLI as compiler0.)

## 6. Sequencing — status

1. **Unblock sunset — DONE.** Golden regen on the `.z` dump binaries
   (`make regen-goldens`); runtime `.inc`/`.tmpl` canonical (§2); committed-stage0
   bootstrap (`bootstrap/zc.c`).
2. **Freeze — DONE 2026-06-27.** Dual gate as a local `make ci`; build/test default
   on the seed; differentials + Python-internal suite retired; compiler0 frozen;
   tags `python-final-parallel` → `python-stage0-final`. (CLI parity, `cli_basic`
   leak, `ztestrunner` timeout — already DONE.)
3. **Remaining follow-up:** port the style linter (`tools/lint_style.py`) to `.z`,
   then `rm compiler0/` + drop its `pyproject`/`conftest` references (the linter is
   compiler0's last in-tree consumer). `zsymtab_proto.py` and the other Python-only
   modules go with that deletion (see §1).
4. **Medium / ongoing:** deferred language features as needs surface (range-for,
   `int.each`, owned-pair Map iter); adopt N-built-by-N−1 (`doc/bootstrap.pdoc`
   Phase E); revisit port-internal deferrals only on concrete blockers.
