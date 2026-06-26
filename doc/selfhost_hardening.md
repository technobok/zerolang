# Self-host hardening: latent `.z`-built UAFs and leaks (RESOLVED)

## Status: SWEEP COMPLETE — freeze unblocked (2026-06-26)

The `.z`-emitted `zc`, built with AddressSanitizer, now compiles the **whole
corpus** — 113 `examples/*.z` + 85 `tests/fixtures/emitc_corpus/*.z` = 198 units,
in both `--emit-c /dev/null` and `--full --dump-sql -` modes, under
`detect_leaks=1` — with **zero** use-after-free / double-free and **0 bytes
leaked** (`clean=396 fails=0`). The compiler0 freeze is no longer blocked on
memory-safety.

The bugs were a class of **latent ownership gaps** in the self-hosted compiler —
code paths the `.z`-built `zc` had **never executed before**, because compiler0
always built the test/corpus/`bin/zc` binary. They are emitter *port* gaps
(`src/zemitterc.z`): harmless in compiler0 (Python is GC'd, no `free`), but
UAF/double-free/leak in the self-hosted compiler. Six root causes were found and
fixed (see the inventory below). Gate: `make selfhost-asan` (whole corpus) +
`tests/test_fixedpoint.py::test_stage2_selfhost_asan_clean` (in-suite subset over
the bug-trigger units × both modes).

## Reproduce

The bug lives in the `.z` emitter, so **any** `.z`-built `zc` has it:

```sh
cc -std=c17 -w -o /tmp/zc bootstrap/zc.c          # the seed IS a .z-built zc
/tmp/zc generator_chain --src examples --system lib/system --emit-c /dev/null
# -> "free(): double free detected" / SIGABRT (exit 134)

# ASan stack:
gcc -fsanitize=address -g -O0 -std=c17 -w -o /tmp/zc_asan bootstrap/zc.c
ASAN_OPTIONS=detect_leaks=0 /tmp/zc_asan generator_chain --src examples --system lib/system --emit-c /dev/null
```

compiler0-built `zc` (`uv run python compiler0/zc.py zc --src src -o /tmp/c.c && cc …`)
does **not** crash — it is the oracle for correct ownership handling.

The differential suite (`tests/test_dumpsql_z.py::test_dumpsql_*_match_python`)
flags the affected examples: **generators** (all), **facets**, **protocols**,
**iterator**, **autoproject**, `atomic_call_temps` (the latter several under
`--full`). Expect several distinct bugs.

## Bug 1 — field-take source not invalidated (ROOT-CAUSED; fix verified)

**Symptom:** UAF/double-free of a `Node` in generator lowering. ASan:
`Node_destroy <- Option_Node_destroy <- GivesParts_destroy <- lowerOne`.

**Root cause:** `src/zemitterc.z` honors `<atom>.take` source-invalidation but NOT
a **field take** `<obj>.field.take` (parent is a `dottedpath`). compiler0's
`_get_take_var` (`compiler0/zemitterc.py` ~line 8112, the "Field take" branch)
emits `obj.field = (T){0};` after the copy; the port omits it. So
`rc3: rhsCar.base.take` in `src/zgenerator.z` `lowerOne` (~line 1849) emits
`rc3 = rhsCar.base;` with no `rhsCar.base = {0};`, and the `GivesParts` destructor
double-frees the payload the take moved out. The `.z` source is correct — purely
an emitter gap.

**Fix (verified ASan-clean via compiler0-build on generators):**
- Add an `isFieldTake` helper in `src/zemitterc.z`: true iff `n` reduces
  (through expression/statementline) to a `dottedpath` whose child is `take` and
  whose **parent is itself a dottedpath** (a field, not a bare atom).
- At the **general binding** site only (`zemitterc.z` ~line 6228, inside
  `if ct.length > 0 then { la: "{ct} {nm} = {rv};" … }`), right after the existing
  `emitValueTakeInvalidations` call, add:
  ```
  if (isFieldTake n: n.value) then {
      ftz9: "\{ind}\{rv} = (\{ct}){0};\n"   # rv == the field lvalue; ct == field type
      buf.append ftz9.stringview
  }
  ```
  Reuse the already-computed `rv` (the take reads the path, so `rv` is the field
  lvalue) and `ct` (binding type == field type). **Do NOT** put this in the shared
  `emitValueTakeInvalidations` — it is also called for call-args and over-zeros
  there. May also be needed at the **reassignment** site (mirror compiler0's
  `_get_take_var_from_expr` usage at reassign, `zemitterc.py` ~7147); verify with
  the corpus.

This fix is correct (compiler0-built `zc` with it is ASan-clean on generators) but
does **not** make the self-emit clean on its own — it unmasks Bug 2.

## Bug 2 — `List u32` freed in `lowerOne` but used by the caller (LOCATED)

**Symptom (after Bug 1 fixed):** ASan heap-use-after-free in `List_u32_get`. A
`List u32` is created/appended in `src/zgenerator.z` `lowerOne`, **destroyed by
`lowerOne`'s scope cleanup**, then **used after return by the caller**
`lowerGeneratorsInUnit`. So a List that `lowerOne` returns (or shares into its
result) is also freed by `lowerOne` — a return/ownership-transfer gap. compiler0
handles it correctly; the `.z` emitter does not. Root cause TBD — same workflow
as Bug 1 (find the emitter divergence vs compiler0; the bug is in `src/zemitterc.z`
ownership/return handling, not `src/zgenerator.z`).

## The sweep plan (to unblock the freeze)

1. Land the Bug-1 fix in `src/zemitterc.z`.
2. Build a fixed `zc` via compiler0; **enumerate all bugs**: run the `.z`-built
   `zc` over the whole corpus under ASan (`detect_leaks=0`, then `=1`) — every
   example + the `*_match_python` differential set — and list every UAF / double-
   free / leak. Don't stop at the first; collect the full set.
3. Fix each (emitter port gaps; **compiler0 is the oracle** — diff the emitted C
   around the crashing function between compiler0-emit and `.z`-emit to find the
   missing invalidation / ownership handling).
4. Gate: the `.z`-built `zc` is ASan-clean on the whole corpus, `make test`
   (built via compiler0 today) stays green, and the fixpoint holds.
5. Only then resume the freeze: re-bump `bootstrap/zc.c` from the hardened
   compiler, switch the build/stage0 to the seed, snapshot the runtime into
   `compiler0/`, and make compiler0 dormant. The freeze mechanics are in the
   approved plan (build switch in `Makefile`/`tests/conftest.py`/the corpus
   shells; runtime snapshot; `compiler0/README.md` FROZEN framing) — see the
   session plan file / `doc/bootstrap.pdoc`. **Keep the differential oracle** until
   the sweep is done (it is what catches these).

## Debugging workflow (reuse)

- compiler0 (`compiler0/*.py`) is FROZEN-correct and is the oracle. The `.z`
  source (`src/*.z`) is also correct; these bugs are in the **`.z` emitter**
  (`src/zemitterc.z`) failing to honor an ownership operation that compiler0
  honors.
- ASan-build the `.z`-emitted `zc.c` (`bin/zc.c` or `bootstrap/zc.c`), run the
  failing example, read the stack. Then `awk`/grep the emitted C around the
  crashing `z_t…_<fn>` in both the compiler0-emit and the `.z`-emit and diff —
  the missing `… = (T){0};` / `… = NULL;` invalidation (or a wrong free) is the
  bug. Map the C function back to the `.z` source by name.
- compiler0 rebuilds are slow (~2 min Python emit + ~1 min cc). Budget for it.

## Sweep inventory (2026-06-26)

Full ASan sweep of the `.z`-emitted `zc` over all 198 units (113 examples + 85
`emitc_corpus`) × 2 modes (`--emit-c /dev/null`, `--full --dump-sql -`),
`detect_leaks=1`. Baseline: **clean=179, fails=217**, grouped by root C function:

| Root | Kind | × | Units | Status |
| --- | --- | --- | --- | --- |
| `Node_destroy` (field-take, `lowerOne`) | UAF | 14 | 7 generators | **fixed** (Bug 1) |
| `Node_destroy` (`GenLowered` List move) | UAF | masked | generators | **fixed** (Bug 2) |
| `Node_destroy` (`buildCallMethod` `vp9: valueParam.take`) | UAF | — | accepts_borrow, bidirectional | **fixed** (Bug 3) |
| `String_append` (`resolveProtocol` `specThisStr = cto.take`) | UAF | 20 | facets, protocols, iterator, autoproject, atomic_call_temps, named_arg_order, protocol_* | **fixed** (Group B) |
| `Map_u64_String_get` (`ZDumper.appendTypes`/`appendSymbolTable`) | leak | 181 | ~all, `--dump-sql` | **fixed** (Group C) |
| `String_create` (`hoistArg`), `Lexer_accept` (`accept`) | leak | 2 | box, genericunit | **fixed** (Group D/E) |

**Bug 3** — `vp9: valueParam.take` (bare-atom take of a whole `Option Node` *param*)
emitted a shallow copy with no source-zeroing; param + binding both freed the box.
Fix: extend the binding-site take guard with the bare-atom case (`takeStringSource`
gate → `rv = (ct){0}`). The reference *aliases* here; the port copies, so it must
zero — behaviorally identical (`test_emitc_z` is a runtime differential).

**Group B** — `specThisStr = cto.take` (take of a *narrowed* match-subject payload)
at a reassignment didn't zero the payload box; the trailing `Option_destroy(&cto)`
freed the buffer `specThisStr` aliased. Fix: call `emitNarrowedTakeZero` in the
reassignment path (the return path already did). Zero the payload box, NOT the whole
Option (that would leak the box).

**Group C** — an owned, destructible, non-lvalue temporary (`Map u64 String` .get →
`Option String`) passed BY VALUE to a borrow param is never freed. The reference
hoists it into a temp freed at scope cleanup; the port inlined it. Fix: extend the
by-value arg branch to hoist+register-destroy when `ptra9==false`,
`isNonLvalueArg`, `pown9!="TAKE"`, and the type is destructible (mirrors the
existing by-ref `&`-hoist). Factored into a shared `hoistBorrowValArg` helper used
by both the method-call and same-unit free-fn arg loops. **Follow-on:** the String
and StringView binding paths emitted a call RHS without calling `flushArgHoists`,
so a hoisted `_ahN` was used before its decl was emitted — they now flush, like the
general binding path.

**Group D/E** — a discarded statement-call (`hoistArg ...` / `lex.accept ...` whose
result is ignored) returning an owned destructible value (String / class / union,
not a `.borrow`) is never freed. Fix: in `emitCallStmt`'s plain-call path, capture
the return into a `_cN` temp and free it when `hasDestructorName` holds,
`funcBorrowFlag` is false, and the type is String/CLASS/UNION (mirrors compiler0
`_emit_call_stmt`).
