# Freeze audit — coverage parity before retiring the Python-dependent tests

Evidence for the compiler0 freeze (`doc/bootstrap.pdoc` Phase C→E). Before deleting
the tests that need the Python reference (`compiler0/*.py`) live, this records that
no behavioral / correctness coverage is lost — only fine-grained unit-test
granularity, which is the documented, accepted trade-off of self-hosting sunset
(`doc/bootstrap.pdoc` "What stays Python-only").

Two classes retire: **(1)** the port-vs-reference *differentials* (their job — forcing
the port to match an independent implementation — is complete now that the port is
correct, fixpoint-stable, ASan-clean, and corpus-green), and **(2)** the
*Python-internal* data-structure unit tests (they assert on compiler0's own Python
objects and have no portable analogue). Every durable replacement below is
port-vs-committed-golden or behavioral, with **no Python at gate time**.

## 1. Differentials (PYTHON-ORACLE) → durable Python-free replacement

| Retiring test (function) | File | Covers | Durable replacement (Python-free, survives) |
| --- | --- | --- | --- |
| `test_emitc_matches_reference` | test_emitc_z.py | emitted-C behavior (stdout/exit) vs reference, EMITC_SMOKE | `run_corpus.sh` **run** goldens (`run_golden/*.out`+`.exit`, same program set) ; `test_fixedpoint` smoke goldens |
| `test_emitc_corpus_matches_reference` | test_emitc_z.py | same, EMITC_CORPUS | same |
| `test_dumpsql_matches_python` | test_dumpsql_z.py | files/ast_nodes/unit | lexer+parser binary goldens (`test_z{lexer,parser}_*_matches_golden`); `dump_golden/*.canon` (unit) |
| `test_dumpsql_types_match_python` | test_dumpsql_z.py | types, type_children | `dump_golden/*.canon` (types, type_children) |
| `test_dumpsql_conformance_match_python` | test_dumpsql_z.py | conformance | `dump_golden/*.canon` (conformance) |
| `test_dumpsql_check_match_python` | test_dumpsql_z.py | scope/entry/variable/narrowed_subtype + **mono**/**typed_nodes** | `dump_golden/*.canon` (symbol table) ; mono/typed_nodes → §3 |
| `test_dumpsql_typed_nodes_match_python` | test_dumpsql_z.py | **typed_nodes** | §3 (indirect: behavioral + fixpoint) |
| `test_pure_fn_battery_matches_python` | test_ztypes_z.py | ztypes pure fns (mangle/parse/numeric) | `test_ztypes_smoke_matches_golden` + behavioral corpus |
| `test_battery_matches_python` | test_ztyping_z.py | ztyping ops | `test_ztyping_smoke_matches_golden` + corpus |
| `test_battery_matches_python` | test_zenv_z.py | symbol-table ops | `test_zenv_smoke_matches_golden` + `dump_golden/*.canon` symbol table |
| `test_python_lexer_matches_golden` | test_lexer_differential.py | reference-vs-golden cross-check | `test_zlexer_binary_matches_golden` already pins golden == port output |
| `test_python_parser_matches_golden` | test_parser_differential.py | reference-vs-golden cross-check | `test_zparser_binary_matches_golden` |
| `test_python_program_matches_golden` | test_parser_differential.py | whole-program cross-check | `test_zparser_program_matches_golden` |

**Surviving in those same files** (port-vs-golden, Python-free — KEEP): `test_emitc_scaffold_compiles`, `test_dumpsql_structural_smoke`, `test_z{types,typing,env}_smoke_matches_golden`, `test_z{types,typing,env}_selfhost_matches_golden`, `test_zlexer_binary/selfhost_matches_golden`, `test_zparser_binary/selfhost/program/program_selfhost_matches_golden`.

## 2. Python-internal unit tests → accepted granularity loss

These import `compiler0` modules directly and assert on Python data structures; per
`doc/bootstrap.pdoc` they have no portable analogue and retire with the reference.
`.z`-side coverage (behavioral run goldens + negative `errors/*.err` + `dump_golden/*.canon`
+ the 198-unit corpus typecheck→emit→run + ASan) replaces *behavior*; fine-grained
unit granularity is intentionally not reproduced (future granular tests, if wanted,
are zerolang-written via the corpus/error/dump harness, not a freeze blocker).

`test_typecheck.py` (~1241), `test_emitter.py` (~783), `test_parser.py` (~122),
`test_lexer.py` (~71), `test_vfs.py` (~28), `test_cli.py` (~19), `test_asthash.py`
(~7, superseded — port dedups monos by key, not content-hash), `test_runtime_templates.py`
(~12, `.inc`/`.tmpl` now canonical files guarded by present/unique, not Python sync).

## 3. The typed_nodes / mono gap — DECISION: accept indirect coverage

`typed_nodes` (per-node type stamp) and `mono`/`mono_children`/`mono_typed_nodes`
(monomorphization) are **deliberately excluded from the `dump_golden/*.canon` goldens**
(~1300 library-dominated rows/example → a bloated, noisy golden; the `name` column also
needs a contextual AST-walk label). Their only direct parity check today is the
retiring `test_dumpsql_{typed_nodes,check}_match_python`.

**Decision: rely on indirect Python-free coverage.** Rationale:
- `typed_nodes` *drives cname/cast/dispatch selection in emission*, so a real regression
  changes emitted C across the 198-unit corpus and surfaces in: the **run goldens**
  (~130 behavioral cases), the **smoke goldens** (ztypes/ztyping/zenv compiled-and-run,
  `test_fixedpoint::test_stage2_compiles_unit_to_golden`), **`make build`** (198 units
  must compile), and **`selfhost-asan`**.
- `mono` regressions change which instances are emitted → wrong/no C → caught by the
  same behavioral gates.
- The residual silent case (a wrong-but-C-valid stamp with *no* observable behavior
  change) is a benign no-op not worth a bloated golden.

**Cheap follow-on if a regression ever slips:** add a *main-unit-filtered* `typed_nodes`
+ `mono` section to `dumpCanon` (`src/zsqldump.z`) — small, because dump-canon already
filters to `definedInUnit == <mainUnit>` (the library rows that caused the bloat are
the cross-unit ones the pytest differential widened to). Not done now: not justified
against the strong indirect coverage above.

## Conclusion

Retiring the differentials + Python-internal suite loses **no behavioral or
end-to-end correctness coverage** — the durable replacements (run/leak/error/dump
corpus goldens, lexer/parser/smoke binary goldens, fixpoint, selfhost-asan) are all
Python-free and already green. The only reduction is unit-test *granularity*, which
`doc/bootstrap.pdoc` already designates as Python-only and accepts at sunset.
Retirement (Inc 4) is cleared.
