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
| 2026-07-17 | 297f741 | A1: names-as-nodes interning (AtomId/LabelValue name -> u32 nameentry ref; hot readers on scoped row views; constVals probes on getv) | 0.66s | — | 117MB / — | — | 10,161,794 | 485MB | — |

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
