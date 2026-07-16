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

Arc total (GROUND -> W2): allocations -52.5%, bytes churned -32%, wall
(mimalloc) -10%, wall (glibc) -16%, peak RSS -8MB, emit phase -17%.
Rejected with evidence: String SSO (String structs are bytewise-relocated by
every container realloc/memmove and move site -- interior pointers cannot
survive; see the W0 census discussion), Node payload inlining (<1% of blocks).
Checker gap noted: `capacity:` + a dotted cross-unit value type trips Map
generic inference (callKind stays unsized).

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
