# Compiler performance baseline

Hand-maintained ground truth for compiler performance work. Append a row per
landed perf workstream, measured with the commands below, in the same commit
that lands the change. Machine context matters — record it per row when it
changes.

## Commands (run from the repo root, warm tree)

```bash
# self-compile wall + peak RSS, best of 5 (drop the first run):
for i in 1 2 3 4 5; do /usr/bin/time -f "%es %MkB" \
    bin/zc zc --src src --system lib/system --emit-c /dev/null 2>&1 | tail -1; done
# glibc variant: rebuild first with `touch src/zc.z && make MIMALLOC=0 zc`
# phase split:
bin/zc zc --src src --system lib/system --time --emit-c /dev/null
# allocation totals:
valgrind --tool=memcheck bin/zc zc --src src --system lib/system \
    --emit-c /dev/null 2>&1 | grep 'total heap usage'
# corpus wall:
time make test
# allocation-site census (optional, slow):
valgrind --tool=dhat --dhat-out-file=/tmp/zc.dhat bin/zc zc --src src \
    --system lib/system --emit-c /dev/null
```

## Baseline table

Machine: 24-core, gcc 15.2.0, glibc 2.43, Linux. Wall = best of 5.
"allocs" = memcheck total heap blocks for one self-compile.

| date | commit | change | wall (mimalloc) | wall (glibc) | peak RSS (mi/glibc) | phases (parse/check/emit ms) | allocs | bytes churned | make test |
|---|---|---|---|---|---|---|---|---|---|
| 2026-07-17 | 4f10844 | GROUND (post emitter-completeness arc) | 0.77s | 0.89s | 125MB / 122MB | 86 / 247 / 423 (total 756) | 23,625,212 | 772MB | 11.0s |
| 2026-07-17 | 3bcaba2 | W1: id-space queries, regNameIs scans, mainBodyMentions hoist, childOfWalk fast path, Map.getv | 0.69s | — | 126MB / — | 92 / 234 / 354 (total 680) | 11,210,996 | 546MB | — |
| 2026-07-17 | 1b7c6d0 | W2: emitter buffer reserves, Map/Set/List capacity:, stamp-map pre-size | 0.69s | 0.75s | 117MB / 116MB | 84 / 242 / 351 (total 677) | 11,217,951 | 527MB | 10.7s |
| 2026-07-17 | fbb3426 | capacity-inference fix + value-position capacity threading + right-sized stamp maps (the 1b7c6d0 pre-size was inert: value-position constructions dropped capacity) | 0.68s | — | 118MB / — | — | 11,222,033 | 501MB | — |
| 2026-07-17 | 81b9297 | A: tokenizer source-span token text (goldens byte-identical) | 0.67s | — | 118MB / — | — | 11,010,123 | 492MB | — |
| 2026-07-17 | ab2d177 | B: move-on-advance + parser payload moves (+ D: ctor-arg move gap proved stale, pinned in corpus) | 0.65s | — | 119MB / — | — | 10,597,979 | 489MB | — |
| 2026-07-17 | 7f8524f | C: child-edge name interning (pool + id-keyed buckets) | 0.65s | — | 117MB / — | — | 10,093,238 | 482MB | — |
| 2026-07-17 | 297f741 | A1: names-as-nodes interning (AtomId/LabelValue name -> u32 nameentry ref; hot readers on scoped row views; constVals probes on getv) | 0.66s | — | 117MB / — | 87 / 226 / 340 (total 653) | 10,161,794 | 485MB | — |
| 2026-07-17 | 8727875 | C1: Ast carrier threaded (~570 sigs; ast.nodes indirection; ARCHITECTURE landing — B3-as-perf stays shelved) + StringView.hash native + unconditional z_hash.inc | 0.67s | — | 118MB / — | — | 10,280,730 | 487MB | — |
| 2026-07-17 | dbd0899 | C2: names -> Ast.names StringPool; nameentry arm deleted; synth dedup (ref==ref sound); hot readers borrow pooled text | 0.66s | — | 116MB / — | — | 10,235,386 | 484MB | — |
| 2026-07-17 | 17d8ba4 | C3 (a units, b fileSegs, c edge names, d well-known ids): tree-scoped state consolidated on the carrier; ZTyping's private edge-name pool deleted -- ZTypeChild.nameId IS the Ast.names id, member resolution int-keyed where provenance is certain (ARCHITECTURE landing; +0.5% allocs = edgeText "" fillers + edgeNameId cache) | 0.66s | — | 117MB / — | — | 10,292,415 | 486MB | — |
| 2026-07-17 | c29bf3d | D1-D4 single pool: wk member ids 5..31, id-keyed lookups where ids in hand, ZTyping edgeText+edgeNameId DELETED (no name text outside Ast.names; StringPool.find read-only probe). +1.8% allocs = the third nameIds out-list on recFieldLists/variantArms/protoChildMethods call sites (superseded by the registry-ids arc) | 0.67s | — | 116MB / — | — | 10,473,463 | 494MB | — |

A1 notes: 121,797 per-node name Strings collapse to ~one interned nameentry
row per distinct identifier; name equality on refs becomes available (A2).
Alloc cost +0.7% (the intern pool rows + map keys + residual cold-path
copies) inside the arc's <=2% wall budget; leak-free (allocs == frees).
Landmine: post-guard narrowing ignored bare `return` (atomid, not a return
call-kind) -- fixed in checkStmtInner, usable after the seed bump.

Token/name arc total: 11.22M -> 10.09M allocs (-10%), wall 0.68 -> ~0.65s.
Notes: the tokenizer's 570k "reserve" census line was mostly first-allocs that
the span-slice replaces 1:1 -- A's real win was deleting the word-path copy
staging. The construction-arg move restriction documented in old zlexer
comments does NOT exist in the self-hosted checker (pinned by
emitc_corpus/ctor_arg_move). Parser label-fanout clusters keep their copies
(bespoke hoisting per site; ~50-150k remaining, diminishing). Raw-string
tokens keep appendByte (rare; negligible).

Arc total (GROUND -> W2): allocations -52.5%, bytes churned -32%, wall
(mimalloc) -10%, wall (glibc) -16%, peak RSS -8MB, emit phase -17%.
Rejected with evidence: String SSO (String structs are bytewise-relocated by
every container realloc/memmove and move site -- interior pointers cannot
survive; see the W0 census discussion), Node payload inlining (<1% of blocks).
Checker gap noted: `capacity:` + a dotted cross-unit value type trips Map
generic inference (callKind stays unsized).

## Cache census (perf, 2026-07-17 @ 476ce11) — union→variant flip NO-GO

Self-compile, 5-run perf stat: IPC 2.42 (8.18G instr / 3.38G cycles), L1-dcache
miss rate 1.5% (61.5M / 4.07G loads), cache-misses 10.0M, dTLB misses 0.8M,
frontend stalls 19% of cycles. Cache-miss attribution (perf record):
Map_u64_u64_find 10.6%, memcmp 6.5%, allocator ~7.6%, fasthash 4.2%,
List_Node_get 3.9%, other Map finds ~5%. Buckets: Map/Set probes ~17%,
node fetch + child lists ~4-6% — far under the >=15% flip gate, and the
pipeline is not memory-bound. VERDICT: the inline-payload variant flip (and the
B3 carrier as its enabler) is SHELVED on evidence; name interning proceeds on
its own merits (allocs + the visible memcmp/String traffic); future cache work
should target Map probing, not node layout.

## Wrapper-elision audit (2026-07-17, post-A1) — NO-GO

Question: elide the 76,090 parsed expression/statementline wrapper rows (22%
of the node table; each also boxes an ExpressionData/StatementLineData
payload). Audit of all 224 expression/statementline match arms, every
ZTyping per-node map's key domain, statement-list consumers, and the
parser/formatter: 180 arms are pure descent, but elision is NOT
parser+re-key+regen only. Blockers, with the payoff they'd buy weighed in:

- **Deliberate wrapper-keyed semantics in monomorphization**: for
  `(T args)`-shaped typerefs the wrapper carries the reference's instance
  stamp while the inner call resolves to a filtered NULL-defined mono
  (ztypecheck.z ~1985-2004, instFillIds -> completeUnitInstantiations
  ~6560/7911, funcReturnNode values). Re-keying makes one node carry both
  roles; every reader of those stamps needs its own audit.
- **~10 statementline-shape helpers** else-null past non-statementline kinds
  and would silently skip work: checkStatementLine/checkStmtInner,
  lastStmtType, lastStmtLineId, blockEndsInReturn, assignmentLocalName,
  constantBranchWalk, tryDebrace/forceBodyBreak (zl L008 anchors),
  slIsBareNull/isBareNullBody, firstRhsOf.
- **Position identity fails for `(`-led statement lines**: the statementline
  sits on the `(` column, the inner call one column right — diagnostics and
  the zsource byte anchors shift, violating the "only parser goldens regen"
  gate.
- checkValue leaves yieldexpr unstamped on purpose ("the expression wrapper
  takes the type", ~16680); implicit-return restamp (17143/17338) uses the
  wrapper to shadow the inner literal stamp.

Payoff at current evidence: ~76k allocs (0.75%), ~3-4MB table+box RSS, and a
dispatch hop inside the node-fetch bucket Stage 0 measured at only 4-6% of
cache misses (pipeline not memory-bound, IPC 2.42). Sub-1% wall for surgery
on the two most subtle subsystems (mono stamps, implicit-return coercion).
VERDICT: NO-GO — do not relitigate without new evidence that node-table
locality or parse-phase allocs became a measured bottleneck. Synth wrappers
(zgenerator wrapExpr/hoistArg) are load-bearing regardless; no wrapper match
arm may be deleted.

## Ground allocation census (DHAT, 2026-07-17 @ 4f10844)

| bucket | blocks | bytes | note |
|---|---|---|---|
| String buffers | 19.2M (81%) | 458MB | String_copy 10.0M, from_view 6.4M, create 1.1M |
| other (iterator/option boxes, parser) | 4.0M | 148MB | Splitter_call 1.3M boxed payloads |
| List storage growth | 191k | 40MB | node table + string lists |
| Node payload boxes | ~182k | 33MB | <1% of blocks — payload inlining rejected on this evidence |
| Map/Set storage | 63k | 93MB | rehash churn; pre-sizing target |

Single largest chain: ~5.85M blocks (25%) under the emitter's
`ioCanonCname` → `definedInNonMain`/`definedInUnitOf`/`typeNameOfReg9` queries
(linear registry scan with per-iteration String materialization).

## String-side census (DHAT, 2026-07-17 @ 8bd3aef, post-carrier arc)

10.29M blocks total; no single chain exceeds 0.5%. Where owned Strings
still come from, by subsystem (chains containing the frame):

| frames containing | blocks | share | note |
|---|---|---|---|
| resolveTypeIdByName (emitter) | 1.46M | 14.2% | name-keyed type resolution: composed "unit.name" String keys + split iterators; REGISTRY names, not pool names |
| nameTextCopy | 776k | 7.5% | consumers copying pool text OUT (keys, diagnostics, name lists) instead of borrowing/id-comparing |
| tokSpan (tokenizer) | 556k | 5.4% | one owned String per token from the source span |
| dataFieldNames | 293k | 2.8% | copies edge-name texts into List String on the auto-call path |
| resolvedByKey/childOfWalk | 282k | 2.7% | String keys + Splitter under the emitter resolution chain |
| ZSymbolTable exclude | 134k | 1.3% | narrowing subtype-name copies |
| registerEdgeText + StringPool.set (C3c cost) | 27k | 0.3% | edge caches + interning -- the whole arc bookkeeping |

The pool is the single authoritative copy of AST identifier text; the
remaining churn is (a) the emitter resolving types by NAME over registry
Strings -- migrating registry type names to pool ids is the big lever and
would also dissolve ZTyping's edgeNameId/edgeText caches -- and (b)
nameTextCopy call sites that could borrow or id-compare instead.

