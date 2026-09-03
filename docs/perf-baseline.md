# Compiler performance baseline

Hand-maintained ground truth for compiler performance work. Append a row per
landed perf workstream, measured with the commands below, in the same commit
that lands the change. Machine context matters — record it per row when it
changes. Each row is a label plus its numbers; the prose that explains it goes
in [Row detail](#row-detail) at the foot of the file.

## Commands (run from the repo root, warm tree)

**Every row measures `out/zc-perf`, not `bin/zc`.** The two are the same emitted
C: `bin/zc` is the driver you run, built at `OPTFLAGS` (**-O2**), and
`out/zc-perf` is the series binary, built by `PERFCC` at `PERFOPT` (**gcc -O1**)
and rebuilt by every perf target. The split exists because -O2 moves wall by
~30% and allocations by nothing, so a driver rebuilt at another level would
quietly rewrite the wall column while the allocation column still looked right.
Never hand-time `bin/zc` for a row.

`make perf` prints the core row: the zerolang line count, self-compile wall
best-of-5 + peak RSS, the parse/typecheck/emit phase split, and (when valgrind is
installed) the allocation total. It measures with the default hash and
`--emit-c /dev/null`, exactly as below. The remaining columns are manual:

```bash
make pre-push                   # check + test + perf-strict: allocations <= ALLOC_BASELINE (Makefile)
make perf                       # LOC + wall + RSS + phases + allocs (the core row)
# glibc wall: relink the series binary without mimalloc, time it, then restore:
rm -f out/zc-perf && make MIMALLOC=0 out/zc-perf
for i in 1 2 3 4 5; do /usr/bin/time -f "%es %MkB" \
    out/zc-perf zc --src src --system lib/system --emit-c /dev/null 2>&1 | tail -1; done
rm -f out/zc-perf && make out/zc-perf
# corpus wall (bimodal -- see the 2026-07-21 note):
time make test
# allocation-site census (optional, slow):
valgrind --tool=dhat --dhat-out-file=/tmp/zc.dhat out/zc-perf zc --src src \
    --system lib/system --emit-c /dev/null
```

A speed claim needs BOTH binaries on the SAME input -- `perf stat -r 7` over
instructions, which resolves below 2% where wall does not. The wall column is
not an A/B across rows: it times the compiler on its own current source, so it
moves with LOC. See the 2026-08-07 row.

### Why instructions is the axis and cycles is not

`instructions` counts **work**, and for a deterministic program on a fixed
input it is deterministic: the same binary retires the same count every run.
The ±0.01–0.05% left over is the loader and ASLR, not the compiler. That is
why a 0.2% instruction delta is readable.

`cycles` counts **how long the core took to retire that work**, so with the
instruction count pinned, every bit of cycle variance is IPC variance. It is
not clock speed -- a halted core stops counting, so frequency scaling and
thermal throttling move *wall* and leave cycles alone. What moves cycles on an
otherwise identical run is anything that changes how often the core stalls:

- **an SMT sibling.** Two threads on one physical core share the execution
  ports, the L1 and the L2. A concurrent build, a valgrind run, or a test
  suite landing on the sibling is the single largest effect and can cost tens
  of percent.
- **shared L3 and DRAM bandwidth.** A self-compile churns ~370MB; another
  process evicting L3 raises the miss rate for identical code.
- **core migration.** The scheduler moving the task across a CCX/CCD or NUMA
  boundary abandons a warm cache mid-run.
- **transparent hugepages.** Whether the kernel can back the heap with 2MB
  pages depends on host memory fragmentation *at that moment*; without them a
  370MB working set pays far more dTLB misses for exactly the same work.
- **layout.** ASLR shifts code and heap per run, changing how hot loops align
  against cache lines and branch-predictor entries.

Measured here, three interleaved rounds of the SAME binary on the SAME input:
**1,660M (±1.58%), 1,878M (±7.38%), 1,701M (±0.42%)** -- a 13% spread between
rounds on identical work, while instructions held ±0.01%. A cycles delta below
that spread is not a result.

So: **judge on instructions.** Quote cycles only when both binaries' spreads
are tight, the rounds were interleaved, and the machine was quiet -- and if a
cycles delta contradicts the instruction delta, re-run interleaved before
believing it. Allocations and bytes churned are unaffected by any of this:
valgrind counts events, not time.

## Baseline table

Machine: 24-core, gcc 15.2.0, glibc 2.43, Linux. Wall = best of 5.
"allocs" = memcheck total heap blocks for one self-compile. "LOC" = `wc -l` of
`src/*.z` + `lib/system/**/*.z` (the self-hosted compiler + relocated front-end/stdlib,
including system's subunit files under `lib/system/system/`).
LOC tracking starts at the 2026-07-23 row; earlier rows are "—" (not back-measured).

The `change` cell is a short label linking to its section in
[Row detail](#row-detail) at the foot of this file. The table is for reading
the numbers down a column; the reasoning, the oracle and the caveats live in
the linked section. **A new row goes in both places**: a one-line label here,
the account there under its own `<a id="r-<commit>">` anchor.

| date | commit | change | wall (mimalloc) | wall (glibc) | peak RSS (mi/glibc) | phases (parse/check/emit ms) | allocs | bytes churned | make test | LOC |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07-17 | 4f10844 | [GROUND — post emitter-completeness arc](#r-4f10844) | 0.77s | 0.89s | 125MB / 122MB | 86 / 247 / 423 (total 756) | 23,625,212 | 772MB | 11.0s | — |
| 2026-07-17 | 3bcaba2 | [W1: id-space queries and scan removal](#r-3bcaba2) | 0.69s | — | 126MB / — | 92 / 234 / 354 (total 680) | 11,210,996 | 546MB | — | — |
| 2026-07-17 | 1b7c6d0 | [W2: buffer reserves and container pre-size](#r-1b7c6d0) | 0.69s | 0.75s | 117MB / 116MB | 84 / 242 / 351 (total 677) | 11,217,951 | 527MB | 10.7s | — |
| 2026-07-17 | fbb3426 | [capacity inference fixed; stamp maps right-sized](#r-fbb3426) | 0.68s | — | 118MB / — | — | 11,222,033 | 501MB | — | — |
| 2026-07-17 | 81b9297 | [A: tokenizer source-span token text](#r-81b9297) | 0.67s | — | 118MB / — | — | 11,010,123 | 492MB | — | — |
| 2026-07-17 | ab2d177 | [B: move-on-advance and parser payload moves](#r-ab2d177) | 0.65s | — | 119MB / — | — | 10,597,979 | 489MB | — | — |
| 2026-07-17 | 7f8524f | [C: child-edge name interning](#r-7f8524f) | 0.65s | — | 117MB / — | — | 10,093,238 | 482MB | — | — |
| 2026-07-17 | 297f741 | [A1: names-as-nodes interning](#r-297f741) | 0.66s | — | 117MB / — | 87 / 226 / 340 (total 653) | 10,161,794 | 485MB | — | — |
| 2026-07-17 | 8727875 | [C1: Ast carrier threaded through ~570 signatures](#r-8727875) | 0.67s | — | 118MB / — | — | 10,280,730 | 487MB | — | — |
| 2026-07-17 | dbd0899 | [C2: names move to the Ast.names StringPool](#r-dbd0899) | 0.66s | — | 116MB / — | — | 10,235,386 | 484MB | — | — |
| 2026-07-17 | 17d8ba4 | [C3: tree-scoped state onto the carrier](#r-17d8ba4) | 0.66s | — | 117MB / — | — | 10,292,415 | 486MB | — | — |
| 2026-07-17 | c29bf3d | [D1-D4: one string pool, id-keyed lookups](#r-c29bf3d) | 0.67s | — | 116MB / — | — | 10,473,463 | 494MB | — | — |
| 2026-07-18 | 6eee916 | [D5: AST labels become pool ids](#r-6eee916) | 0.66s | — | 115MB / — | — | 10,444,134 | 493MB | — | — |
| 2026-07-18 | (E1 seeded) | [E1: resolveTypeIdByName keyed by id](#r-e1-seeded) | 0.64s | — | 113MB / — | — | 10,089,619 | 481MB | — | — |
| 2026-07-18 | (E2a seeded) | [E2a: the unit list cached on Ctx](#r-e2a-seeded) | 0.64s | — | 111MB / — | — | 9,556,604 | 453MB | — | — |
| 2026-07-21 | 1426aaa | [HEAD re-measure after 72 feature commits](#r-1426aaa) | 0.71s | — | 125MB / — | 90 / 277 / 350 (total 715) | 10,520,665 | 510MB | 13.4s | — |
| 2026-07-21 | (A1 seeded) | [A1: walkedMethodOwners keyed qualified](#r-a1-seeded) | 0.69s | — | 122MB / — | 92 / 250 / 340 (total 690) | 10,203,138 | 491MB | 13.2s | — |
| 2026-07-22 | (A2a seeded) | [A2a: fnAutoCallable walks rows by pool id](#r-a2a-seeded) | 0.67s | — | 122MB / — | 85 / 240 / 340 (total 665) | 9,916,042 | 472MB | — | — |
| 2026-07-22 | (A2b seeded) | [A2b: resolveTypeIdByNameId memoized on Ctx](#r-a2b-seeded) | 0.62s | — | 122MB / — | 86 / 239 / 282 (total 610) | 9,368,977 | 457MB | — | — |
| 2026-07-23 | 5eccf67 | [redesign HEAD re-baseline](#r-5eccf67) | 0.66s | — | 129MB / — | 96 / 254 / 312 (total 662) | 10,163,283 | 469MB | — | 80,396 |
| 2026-07-24 | 6f19e46 | [mangled-mono-name namespace retired (P4)](#r-6f19e46) | 0.55s | 0.63s | 120MB / 112MB | 87 / 246 / 217 (total 550) | 9,665,936 | 458MB | 13.5s | 80,358 |
| 2026-07-26 | ada99da | [child-edge table retired (Q2, 72 commits)](#r-ada99da) | 0.61s | 0.70s | 122MB / 114MB | 90 / 306 / 221 (total 617) | 10,771,277 | 598MB | 14.0s | 81,330 |
| 2026-07-26 | 8551e35 | [post-Q2 cleanup](#r-8551e35) | 0.63s | 0.70s | 119MB / 113MB | 87 / 313 / 232 (total 632) | 9,903,328 | 498MB | 14.0s | 80,833 |
| 2026-07-27 | fb326d9 | [borrowed-element get — a correctness arc](#r-fb326d9) | 0.63s | -- | 118MB / -- | 104 / 302 / 231 (total 637) | 10,004,638 | 496MB | -- | 81,088 |
| 2026-07-28 | 929aede | [ZTyping.decls becomes a List](#r-929aede) | 0.54s | -- | 120MB / -- | 101 / 270 / 212 (total 583) | 10,015,753 | 492MB | -- | 80,965 |
| 2026-07-28 | 31f2de6 | [node ids become one monotonic space](#r-31f2de6) | 0.55s | -- | 115MB / -- | 92 / 247 / 211 (total 550) | 10,007,998 | 493MB | -- | 80,844 |
| 2026-07-28 | (nodeType List) | [ZTyping.nodeType becomes a List](#r-nodetype-list) | 0.50s | -- | 109MB / -- | 93 / 226 / 195 (total 514) | 10,019,704 | 476MB | -- | 80,785 |
| 2026-07-28 | (ctl placeholder) | [break and continue share one placeholder tid](#r-ctl-placeholder) | 0.51s | -- | 109MB / -- | 104 / 229 / 193 (total 526) | 10,008,524 | 476MB | -- | 80,782 |
| 2026-07-28 | (typeById List) | [ZTypeRegistry.typeById becomes a List](#r-typebyid-list) | 0.49s | -- | 107MB / -- | 84 / 213 / 182 (total 479) | 9,993,144 | 473MB | -- | 80,134 |
| 2026-07-28 | 2d8e831 | [monoOriginName call sites take the id form](#r-2d8e831) | 0.48s | -- | 107MB / -- | 88 / 213 / 180 (total 481) | 9,919,102 | 473MB | -- | 80,143 |
| 2026-07-29 | (binding pool ids) | [binding-side payloads carry pool ids](#r-binding-pool-ids) | 0.47s | -- | 105MB / -- | 88 / 213 / 180 (total 481) | 9,847,155 | 472MB | -- | 80,169 |
| 2026-07-29 | (entry pool ids) | [ZEntry names become pool ids](#r-entry-pool-ids) | 0.48s | -- | 105MB / -- | 93 / 212 / 175 (total 482) | 9,669,658 | 460MB | -- | 80,281 |
| 2026-07-29 | (demand marks on the Decl) | [demand marks move onto the Decl](#r-demand-marks-on-the-decl) | 0.48s | -- | 107MB / -- | 101 / 217 / 178 (total 496) | 9,663,488 | 459MB | -- | 80,368 |
| 2026-07-29 | (unit indexes by id) | [unit indexes keyed by name id](#r-unit-indexes-by-id) | 0.49s | -- | 107MB / -- | -- | 9,517,956 | 459MB | -- | 80,406 |
| 2026-07-29 | (dotted type names) | [a type's name is its own name](#r-dotted-type-names) | 0.51s | -- | 109MB / -- | -- | 9,471,881 | 457MB | -- | 80,398 |
| 2026-07-29 | (family C: no composite keys) | [family C: no composite keys](#r-family-c-no-composite-keys) | 0.48s | -- | 106MB / -- | 100 / 219 / 182 (total 501) | 9,467,594 | 457MB | -- | 80,476 |
| 2026-07-29 | (family C tail: `d557d0f`..) | [family C tail — three closing commits](#r-family-c-tail-d557d0f) | 0.48s | -- | 106MB | 91 / 217 / 179 (total 487) | 9,472,314 | 457MB | -- | 80,505 |
| 2026-08-02 | affd7f4 | [OWNERSHIP v2 arc (44 commits)](#r-affd7f4) | 0.51s | -- | 106MB / -- | 102 / 236 / 186 (total 517, medians of 5) | 9,692,057 | 548MB | 15.3s | 83,792 |
| 2026-08-03 | 34647e9e | [VAL/REF SPLIT arc (49 commits)](#r-34647e9e) | 0.56s | -- | 108MB / -- | 138 / 251 / 192 (total 583, medians of 5) | 9,885,811 | 557MB | 15.9s | 85,316 |
| 2026-08-03 | (scalar keyHashKind) | [keyHashKind asks scalarCTypeFor first](#r-scalar-keyhashkind) | 0.51s | -- | 108MB / -- | 99 / 240 / 187 (total 539, medians of 5) | 9,886,822 | 557MB | 16.5s | 85,325 |
| 2026-08-03 | (releaseHeldLocks skip) | [releaseHeldLocks scope-exit skip](#r-releaseheldlocks-skip) | 0.52s | -- | 108MB / -- | 102 / 236 / 188 (total 523, medians of 5) | 9,874,410 | 522MB | -- | 85337 |
| 2026-08-04 | (typedef ids: machinery + fsno) | [typedef ids: machinery and the fsno space](#r-typedef-ids-machinery-fsno) | 0.53s | -- | 108MB / -- | 98 / 248 / 195 (total 544, medians of 5) | 9,916,106 | 524MB | 16.5s | 85656 |
| 2026-08-05 | b5ef6194 | [the id-typedef arc (19 commits)](#r-b5ef6194) | 0.56s | -- | 111MB / -- | 109 / 262 / 203 (medians of 5) | 10,131,192 | 537MB | 16.6s | 88063 |
| 2026-08-06 | 8f3678fc | [N3-c/e: compound-key resolution by ids](#r-8f3678fc) | 0.55s | -- | 118MB / -- | 101 / 262 / 186 (total 549) | 9,338,190 | 530MB | -- | 89020 |
| 2026-08-06 | 5c59c47a | [L022 closed, N4 landed, four defects fixed](#r-5c59c47a) | 0.53s | -- | 115MB / -- | 105 / 247 / 192 (total 544) | 8,999,867 | 527MB | -- | 89382 |
| 2026-08-07 | 347a3bad | [the C clean-up pool goes to zero](#r-347a3bad) | 0.55s | 0.60s | 122MB / 112MB | 95 / 256 / 191 (total 542) | 7,959,463 | 524MB | 16.7s | 89808 |
| 2026-08-10 | 29dd84f8 | [three arcs: tcc backend and two unrowed](#r-29dd84f8) | 0.55s | 0.63s | 116MB / 113MB | 91 / 256 / 201 (total 561, medians of 7) | 8,341,026 | 477MB | 14.0s | 93,020 |
| 2026-08-12 | e9cbb09f | [two arcs: the native table and one unrowed](#r-e9cbb09f) | 0.57s | 0.66s | 119MB / 114MB | 101 / 270 / 205 (total 576, medians of 5) | 8,635,709 | 510MB | 13.8s | 96,257 |
| 2026-08-15 | c335e552 | [five arcs plus three fixes (81 commits)](#r-c335e552) | 0.52s | 0.64s | 138MB / 114MB | 94 / 239 / 198 (total 531, medians of 7) | 8,640,337 | 478MB | 13.9s | 97,366 |
| 2026-08-16 | 500eb312 | [the composite-key sweep (16 commits)](#r-500eb312) | 0.52s | 0.64s | 139MB / 114MB | 88 / 241 / 198 (total 529, medians of 7) | 8,645,497 | 480MB | 13.6s | 97,393 |
| 2026-08-17 | 154095f8 | [the environment arc (66 commits)](#r-154095f8) | 0.60s | 0.71s | 144MB / 124MB | 94 / 294 / 220 (total ~616, medians of 3) | 8,722,220 | 531MB | 34.9s (load ~3, 1092 cases) | 98,816 |
| 2026-08-17 | 4fc75709 | [the environment arc's cost, chased](#r-4fc75709) | 0.54s | — | 120MB / — | 115 / 256 / 208 (total 579, medians of 3) | 8,529,539 | 462MB | — | 98,919 |
| 2026-08-18 | fcc1bf25 | [one-unit arc phases 5-7, plus two arcs](#r-fcc1bf25) | 0.53s | 0.63s | 120MB / 100MB | 93 / 234 / 207 (total 534) | 8,366,482 | 458MB | 14.1s (1095 cases) | 99,056 |
| 2026-08-18 | f4a6ed00 | [the emitter's unit lookups](#r-f4a6ed00) | 0.53s | 0.64s | 118MB / 100MB | 93 / 230 / 203 (total 526) | 8,365,699 | 460MB | 35.8s (load ~2.5, 1095 cases) | 99,100 |
| 2026-08-21 | d8cebf3a | [design-review consolidation, phases 2.5-2.8](#r-d8cebf3a) | 0.46s | 0.54s | 92MB / 79MB | 128 / 181 / 173 (total 482) | 6,722,550 | 348MB | 15.5s (1115 cases) | 101,530 |
| 2026-08-22 | 07c16e90 | [Phase 5: the `:name` shorthand lock rule made unconditional](#r-07c16e90) | 0.45s | 0.54s | 93MB / 81MB | 105 / 180 / 173 (total 458, medians of 5) | 7,412,765 | 355MB | 35.2s (1118 cases, load 1–3.6) | 105,671 |
| 2026-08-22 | 457f609a | [allocation recovery: the walkers, the resolvers and the readable-names collision](#r-457f609a) | 0.45s | 0.54s | 94MB / 81MB | 98 / 175 / 172 (total 444, medians of 5) | 6,720,484 | 346MB | 34.9s (1119 cases, load 1.7–2.9) | 105,814 |
| 2026-08-22 | a47a444c | [an empty view owns no buffer](#r-a47a444c) | 0.44s | 0.54s | 93MB / 81MB | 101 / 177 / 168 (total 447, medians of 5) | 5,701,889 | 346MB | 35.0s (1119 cases, load ~2.0) | 105,814 |
| 2026-08-22 | 2f83d269 | [...and so does an empty copy](#r-a47a444c) | 0.44s | 0.54s | 93MB / 81MB | 101 / 177 / 168 (total 447, medians of 5) | 5,621,131 | 345MB | 35.0s (1120 cases, load ~2.0) | 105,830 |
| 2026-08-23 | e2c867af | [member names, the row accessors and the tokenizer's trivia](#r-e2c867af) | 0.43s | 0.50s | 91MB / 78MB | 113 / 172 / 162 (total 447, medians of 5) | 4,820,294 | 306MB | 35.9s (1124 cases, load ~5.6) | 106,213 |
| 2026-08-24 | 48e5f08d | [the type-alias mechanism](#r-48e5f08d) | 0.45s | -- | 92MB / -- | 134 / 182 / 166 (total 482, medians of 5) | 4,863,437 | 310MB | -- | 107,829 |
| 2026-08-25 | a04609b7 | [the `fold` natives arc](#r-a04609b7) | 0.45s | -- | 93MB / -- | 93 / 198 / 170 (total 461, medians of 5) | 4,899,755 | 312MB | -- | 108,433 |
| 2026-08-28 | 33e4e5fa | [generic families, then four pre-existing defects](#r-33e4e5fa) | 0.45s | -- | 93MB / -- | 108 / 190 / 174 (total 469, medians of 5) | 4,912,311 | 315MB | -- | 110,984 |
| 2026-09-03 | 86e7d6f5 | [the `outx` arc, then three measured reductions](#r-86e7d6f5) | 0.47s | -- | 93MB / -- | 112 / 194 / 179 (total 486, medians of 5) | 5,070,508 | 321MB | -- | 116,954 |
| 2026-09-03 | 49b9b267 | [name-text copies become name ids](#r-49b9b267) | 0.46s | -- | 91MB / -- | 109 / 185 / 174 (total 470, medians of 5) | 4,718,425 | 315MB | -- | 116,971 |
| 2026-09-03 | 56e15e82 | [resolution by id](#r-56e15e82) | 0.46s | -- | 91MB / -- | 109 / 179 / 173 (total 471, medians of 5) | 4,625,858 | 314MB | -- | 117,169 |
| 2026-09-03 | b7fccae6 | [per-site copies by id](#r-b7fccae6) | 0.45s | -- | 92MB / -- | 108 / 179 / 170 (total 461, medians of 5) | 4,566,490 | 312MB | -- | 117,095 |

2026-08-05 note -- **the measurement floor of this setup, established by
repetition, and the trap that produced a fake baseline.**

Only two metrics resolve anything below ~2%: **instructions** (per-binary
drift ~0.3% across rounds) and **allocs / bytes churned** (bit-identical run
to run). `task-clock` drifts ~2% BETWEEN rounds -- more than any of the three
binaries above differed from any other -- so a wall delta of a few percent
from a single round is noise, not a result. Two phase-level "findings" died
to sample count: parse ranged 89-120ms across all three binaries on the SAME
input, and an apparent emit regression (193 -> 203ms, from three consecutive
identical readings) dissolved at 7 samples (mins 199 / 194 / 198). Take a
phase delta seriously only with >=7 samples and a min-of estimator.

**A cross-tree A/B is valid in ONE DIRECTION ONLY: run the OLD binary on the
NEW source, and check the exit code.** The reverse silently measures a
failed compile -- HEAD's `zc` rejects the pre-`5ea85340` tree with 22
`error[E0100]: return type mismatch: function expects declid, got
literal_int`, because the return-check fix catches the `return 0` the hole
used to wave through. That aborted run reads as 0.37s / 4.35B instructions
against a real 0.56s / 6.86B -- 34% "faster", with nothing in `perf stat`
output to say the work never happened. **A 0.37s figure recorded as a
baseline during this arc was exactly this artifact; it is void.**

2026-07-26 note: `--time` totals run 10-80ms above the plain best-of-5 wall
(the instrumentation is not free) and are noisy run to run; take the phase
split as best-of-5+ too, not from a single run.

2026-08-10 arc notes: the C12 regression above is the second time a real
instruction-count change has been invisible in every other column. The wall,
cycles and allocation columns all read flat while instructions were +0.69%,
because the added work was a handful of map probes per bare atom -- superscalar
slack absorbed it, and it allocated nothing. **A change that adds lookups to a
hot path needs `perf stat -r 7` on identical input; no other column here can
see it.** The corpus wall was 35.07s on its first (cold) run and 14.0s on three
consecutive warm ones -- the same bimodality the 2026-07-21 note records, so the
column is a warm best-of-several, never a single reading.

2026-07-21 arc notes: `make test` wall is bimodal — ~13.3s typical with an
occasional ~25s run at identical CPU time (~2m45 user, 24 jobs), so
single-run corpus timings are not comparable; a prior 26s reading was this
mode, not a regression. Per-kind split (A3, temporary section probes,
typical 13.3s run): LEAK 5.5s, LSP 3.2s, RUN 2.4s, DIFFERENTIAL 1.0s,
SMOKE 0.8s, VFS 0.3s, ERROR+DUMP 0.1s. The serial-differential-tail
hypothesis is REFUTED at 24 jobs — the ASan leak pool dominates; any
future runner scheduling work should target LEAK/LSP, and the ~25s
outlier remains unattributed (it is not differential serialization).

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

## Sentinel row 0 in every id space (2026-08-17 @ 436e330e)

Not a perf workstream, so no table row — the change is an index representation:
a Decl / AST node / pool id / constString id now indexes its row directly, and
the ~2,700 `- 1` adjustments, the 115 `declValid` calls and 19 chain hop
counters are gone. Measured only to confirm it costs nothing.

Protocol differs from the table above: **frozen input** (a copy of `src` +
`lib/system` taken at `9ae2cb29`, so both binaries compile identical source)
and the **-O2 driver**, not the -O1 series binary — this is an A/B of two
binaries on one input, which is what `perf stat` resolves, not a row in the
wall series.

| | 9ae2cb29 | 436e330e |
|---|---|---|
| instructions (`-r 5`) | 4,261,181,086 | **4,215,596,496 (-1.07%)** |
| cycles (`-r 5`, ±1.3%) | 1,886,934,992 | 1,857,197,671 (-1.58%) |
| allocs (memcheck) | 8,529,948 | 8,529,758 |
| bytes allocated | 461,804,661 | 461,796,213 |
| peak RSS | 122,148 kB | 121,972 kB |

The four extra sentinel rows cost nothing measurable; the deleted arithmetic
and predicate calls pay for them about 45M instructions over.

## A one-line method inlines at -O2 but not at -O1 (2026-08-16 @ 500eb312)

**Correction to the first version of this section**, which said "the compiler does not
inline it" and read that as a zerolang-side lead. Which compiler matters, and the
answer differs by level:

- The **zerolang emitter** does no inlining. `nameid.isZero` is emitted as
  `static bool z_t3797(uint32_t* p) { return *p == 0; }` plus a call at every site.
- **gcc -O1 — the perf series level — does NOT inline it.** In the pre-fix series
  binary the symbol is present with **100 call sites**. `-O1` enables
  `-finline-functions-called-once`, not `-finline-small-functions`.
- **gcc -O2 — the level `bin/zc` / `bin/zl` / `bin/zls` ship at — DOES.** The symbol
  is absent from the -O2 driver entirely.

So the five hot presence tests cost **0.80% at -O1 and 0.19% at -O2**, both binaries
on identical input:

| level | with `.isZero` | with `.u32 > 0` | delta |
|---|---|---|---|
| gcc -O1 (series) | 6,519,940,768 | 6,467,550,657 | **-0.80%** |
| gcc -O2 (shipped) | 4,052,824,844 | 4,044,930,588 | **-0.19%** |

The change is still worth keeping — 0.19% of the shipped binary for six lines — but
**not for the reason first written here**: gcc already inlines the method where it
matters, and a zerolang-side inliner would mostly duplicate that.

**The calibration fact is the durable one: the -O1 series exaggerates call overhead
about fourfold relative to the binary that ships**, and does the whole self-compile in
1.60x the instructions. A row that attributes a delta to call overhead is measuring
partly the series' own optimisation level. Nothing else in this table changes — every
row is -O1 and self-consistent — but a per-call-site claim should be re-checked at -O2
before it is called a compiler lead.

## The same finding as first written, before the -O2 check (superseded above)

The composite-key sweep replaced packed `(a << 32) | b` keys with typed pair
records. Six of the seven families were CPU-neutral or better. The seventh —
the alias/re-export target, which stopped being a packed u64 and became a
`deftarget` record of two `zast.nameid` fields — cost **+0.90% instructions**,
measured per stage with every binary on one input:

| after | instructions | vs previous |
|---|---|---|
| `c335e552` (packed keys) | 6,472,418,756 | — |
| F1 + F2 + F5 | 6,466,223,780 | -0.10% |
| F7 + F3b + F3a | 6,464,235,241 | -0.03% |
| F4 (mono stamps) | 6,461,573,505 | -0.04% |
| F6 (alias target) | 6,519,940,768 | **+0.90%** |
| the predicate fix | 6,467,550,657 | **-0.80%** |

**The record was not the cost. The predicate was.** `zast.nameid` declares

```zerolang
isZero: function {:this} out bool is { return this.u32 == 0 }
```

and the compiler does not inline it, so `if (target.unitName.isZero) == false`
emits `z_t3797_nameid_isZero(&altK9.unitName)` — an address-of, a call, and a
bool-tag compare — where the packed key it replaced tested `altK9 > 0`. Five of
those sit on the alias-resolution path, which the resolver and the emitter both
walk per reference.

Reading the field directly (`.u32 > 0`) recovered 52.4M of the 58.4M. That is
deliberate spelling: it reaches past the type's own predicate because the
predicate is what costs.

(The paragraph that stood here called for a zerolang-side inliner, sized at ~0.8%
of a self-compile. The -O2 check above retired it: gcc already does this at the
level that ships.)

**Method note.** The regression was invisible in a flat profile — the touched
functions are 0.44% of samples between them, and the cost was spread across
their callers. What found it was measuring each stage of the arc on one input
and reading the EMITTED C at the site that moved.

## The symbol table was rebuilt from the bottom (2026-08-15 @ c335e552)

Bytes churned went 510MB -> 1,129MB over the five arcs above while allocations
stayed flat. Flat blocks and doubled bytes means the same operations moved
bigger buffers, and DHAT named them in one profile pair (`e9cbb09f` vs
`e56e4bf1`, both on HEAD's `src`, symbol names recovered by emitting the same
tree twice — once `--readable-names`, once not — and mapping `z_t<id>` to name
by line, since the two files are line-aligned):

| frame | e9cbb09f | e56e4bf1 | delta |
|---|---|---|---|
| `ZSymbolTable_discardTakenInCurrentScope` | 18.9MB / 10,060 blk | 482.5MB / 36,391 blk | **+463.6MB** |
| `ZSymbolTable_releaseHeldLocks` | 19.1MB / 5,001 blk | 160.9MB / 4,914 blk | **+141.8MB** |
| everything else | | | flat |

Two functions, **98% of a +619MB regression**. Both drained the WHOLE flat
entry list into a reversed copy and appended it all back to drop a few rows.

**What changed was the table, not the functions** — neither had been touched in
the span. Instrumenting `entries.length` and `scopes.length` at the discard
call, one self-compile each:

| | e9cbb09f | e56e4bf1 |
|---|---|---|
| calls | 4,428 | 4,514 |
| entries (mean / max) | **10.4** / 88 | **192.8** / 428 |
| scopes (mean / max) | **5.5** / 21 | **40.0** / 98 |
| entries in the CURRENT scope (mean) | 1.07 | 1.05 |

Walking a definition where it is demanded nests body walks, so the scope stack
is 7x deeper and the flat entry list 18x longer. The call count did not move;
the cost per call did. That depth is the design working — the waste is that a
rebuild was O(whole table) when the work is O(current scope): **the current
scope holds a mean of ONE entry against the 192.8 each call rebuilt.** The
waste was always there (89.7% at `e9cbb09f`); depth made it expensive.

Three commits, each measured on one self-compile at the default hash:

- `9671b2c8` `discardTakenInCurrentScope` drains from `currentScopeStart`, the
  bound its own body already tests against: **1,129,437,607 -> 648,858,600 B**.
- `7acd6886` `releaseHeldLocks` records the index of the first entry the holder
  locked during the scan it already ran, and rebuilds from there instead of
  from a bool: **-> 488,878,419 B**.
- `c335e552` `removeEntryAt` rebuilds from the index it is given:
  **-> 478,437,420 B**.

Allocations moved -34,851 in total and the emitted C is unchanged (corpus gate
green) — this is typecheck-internal bookkeeping. `setTakenLocation` still
drains from the bottom; it indexes by absolute position and never allocated
enough to appear in a profile.

**The column that caught this was bytes churned, and only it.** Wall, phases,
RSS, `make test` and the corpus were all fine or improving while the compiler
moved an extra 619MB per self-compile. It is the one number in this table with
no other guard behind it, which is the argument for keeping it.

## The write-only pool: how it left zero, and what put it back (2026-08-12 @ e9cbb09f)

`make perf-elision` measures what LLVM deletes for us — bytes written, never
read, pointer never escaping — and the target says it belongs at zero. It had not
been zero since the fragment family landed, and the correlation names the cause
outright:

| at | fragment-backed rows | write-only pool |
|---|---|---|
| `29dd84f8` (last rowed) | 0 | 0 |
| `cca9d6b4` (native table done) | 95 | **95** |
| `d88bc68d` (runtime pass, S6a) | 107 | **107** |
| `bd106fc6` | 122 | **122** |
| `e9cbb09f` | 122 | **0** |

Exactly one write-only allocation per fragment row. In `loadNativeTbl`,
`dm9: key9.copy` seeded the demand half with a copy of the row's path, and both
branches below assign it — written, never read, once per row per compile.

`du9: key9.copy` is the same waste with one difference: a path containing no `.`
*would* read the seed, so LLVM cannot prove the buffer dead and **the pool never
showed it**. No path in the table lacks a dot, so it was equally thrown away —
a live wasted allocation the measurement was structurally unable to report. The
lesson is that the pool is a lower bound on this kind of waste, not a census.

**What actually found it, since the method suggested here before did not.** A
DHAT site diff between `out/zc-noelide` and `out/zc-elide` cannot work: the two
builds inline differently, so their stacks do not correspond and the per-site
deltas run to thousands of blocks that cancel to 122. What found it in two
measurements was noticing the pool is **constant at 122 whether the input is
`hello.z` or the whole compiler** — so it is one-time work, not per-node — and
that 122 was the fragment row count. **Check whether a delta scales with the
input before opening a profiler.**

## Assoc-list census (perf, 2026-07-29 @ b3055f6) — `NameVal`/`NameTid` → pool ids is a NO-GO

The last step of the names-are-ids arc (2a `1e7f0c4`, 2b `b3055f6`) was to key the
emitter's five per-function association lists — `ctx.aliases`, `narrowBox`,
`exclArms` (`NameVal`) and `narrowTid`, `exclTids` (`NameTid`) — by pool id
instead of by name text. **It was implemented, measured, and reverted.**

The target surface, flat profile at `b3055f6`:

| symbol | share of cycles |
|---|---|
| `nvGet` | 0.13% |
| `isPtrVar` (the sibling `List String` scan) | 0.09% |
| `nvSet` / `nvHas` / `nvRemove` / `ntGet` / `ntSet` / `ntRemove` | below the sampling floor |

**0.22% is the entire ceiling.** Against that, every one of the 68 call sites
holds *text*, not an id — the names come from the ~192 `nameTextCopy` sites, which
are out of this arc's scope — so keying by id cannot remove a materialisation. It
can only insert a `poolFind` where a 1–5 element `StringView` scan used to be.

Measured against a same-session `b3055f6` binary, identical flags, two interleaved
`-r 7` rounds: **instructions +1.10%** (6,030.6M → 6,097.1M, ±0.15%), cycles
+0.48%, cache-misses and wall flat. The mechanism is visible in the profile:
`StringPool_find` **0.39% → 0.66%** and `StringPool_probeSlot` **1.20% → 1.61%**
— roughly **+0.7pp of probe machinery bought to chase a 0.22% target.** `nvGet`
itself did not shrink (0.13% → 0.35%, plus a new `nameIdE` at 0.18%).

Hoisting would not rescue it: the 68 sites collapse to ~24 distinct names, so at
best it recovers ~65% of the added probes — an *estimated* +0.25pp, still above
the 0.22% ceiling, and that is before the hoists' own complexity.

The change also cost architecture rather than buying it. Reaching the alias list
from `aliasResolve` forced the `Ast` carrier into eight more emitter helpers
(`aliasResolve`, `svValueForm`, `takeSourceResolve`, `emitLocalAtomName`,
`emitStringMethod`, `implicitTakeSourceAtom`, `emitReturnCleanup`) and ~35 call
sites, to relocate a text→id conversion that the callers still perform.

`NameVal`'s own comment already said this — *"a handful of entries at most, so
linear StringView probes beat a map's per-probe owned-key allocation and hash"*.
**The precondition for an id flip is that the callers already hold ids.** 2b met
it (the AST payloads did, after 2a); this does not. Reopen only after the
`nameTextCopy` debt is paid, which would change the premise.

Oracle for the record: the reverted implementation was correct — emitted C,
`dump --canon` and raw `--dump-sql` were all byte-identical across the 148
examples and the self-compile. It was rejected on cost, not on behaviour.

## Registry-row census (perf, 2026-07-28 @ 2d8e831) — `ZType.name` → `nameId` NO-GO

Flat profile of the self-compile (`perf record -F 4999`, 465 symbols, no call
graph). The whole `ZType`-row access surface:

| symbol | share of cycles |
|---|---|
| `List_ZType_get` | **0.58%** |
| `ZTypeRegistry_newEntry` | 0.35% |
| `ZTypeRegistry_typetypeOf` | 0.27% |
| `ztypetype_eq` | 0.22% |
| `ZTypeRegistry_genericOriginOf` | 0.14% |
| **`ZTypeRegistry_nameOf`** | **0.09%** |

Sanity check: the `typeById` → `List` row above independently recorded
`List_ZType_get` at 0.65%; this run says 0.58%. The profile is sound.

**Verdict: do not convert `ZType.name: String` to `nameId: u32`, and do not add
a `List ztypetype` sidecar.** The proposal was that the row shrinks 232 → 208 B
(−10.3%, `_Static_assert`-verified) and ~35 `for tid < nextTypeId` scans walk it,
by analogy with the `typeById` flip. The analogy fails on two measured points:

1. **`List_ZType_get` returns a pointer, not a copy** — `static z_t1413_ZType_t*
   … { return &_this->data[_idx]; }`, since the borrowed-element `get` arc. The
   row size therefore costs **nothing per access**; it is pure cache residency.
2. **That residency is already free.** 3,881 rows × 232 B = 900 KB → 807 KB.
   Both fit in L2 and the scans are sequential, so prefetch covers them.

`typeById` won −6.9% instructions because it *deleted* a 3.43% hash-and-probe.
Here the entire deletable surface is 0.58%, of which a row shrink captures a
fraction — realistically <0.1%, an order of magnitude below the noise floor —
against **~309 reader sites across 41 functions**. The sidecar dies the same way:
`typetypeOf` + `ztypetype_eq` is 0.49%, already a pointer deref plus a field read.

The allocation argument was retired separately and empirically — see `2d8e831`,
which cut 0.76% of all allocations and moved the wall not at all.

**If this is reopened, it must be on an architecture argument, not a perf one:**
lock granularity (a `u32` return composes with live views where a
`String`-returning wrapper cannot — `zls.z:1924`) or consistency with
`Decl.name` / `thisParamNameId` / `destructorNameId`. Both are legitimate;
neither is a speed claim.

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

STALE as of the 2026-07-24 row: `ioCanonCname` is memoized on Ctx and that chain
is dissolved. The bucket shares above have not been re-measured since; re-run the
DHAT census before treating any of them as current. The one line that still
holds is Map/Set storage — rehash churn, pre-sizing target — and it is still
unacted-on: only `nodeType` and `atomVariableId` carry `capacity:`.

## String-side census (DHAT, 2026-07-17 @ 8bd3aef, post-carrier arc)

10.29M blocks total; no single chain exceeds 0.5%. Where owned Strings
still come from, by subsystem (chains containing the frame):

| frames containing | blocks | share | note |
|---|---|---|---|
| resolveTypeIdByName (emitter) | 1.46M | 14.2% | **SUPERSEDED — do not act on this row.** It predates `ec071e6` ("E1: id-key resolveTypeIdByName"), which rewrote the chain to one `poolFind` then id-keyed stages. Re-verified 2026-07-28: `resolveTypeIdByNameId`, `typeIdByNameStages9`, `resolveUnitChildById` and `walkLookupTyperefById` read `ZType.name` **zero** times, so migrating registry names to pool ids buys nothing here. What remains on this path is the `unitNameTid: Map String u64` probe key (an owned String per probe, 7-way fan-out in `walkLookupTyperefById`) and the composed-key fallback — both UNIT names. |
| nameTextCopy | 776k | 7.5% | consumers copying pool text OUT (keys, diagnostics, name lists) instead of borrowing/id-comparing |
| tokSpan (tokenizer) | 556k | 5.4% | one owned String per token from the source span |
| dataFieldNames | 293k | 2.8% | copies edge-name texts into List String on the auto-call path |
| resolvedByKey/childOfWalk | 282k | 2.7% | String keys + Splitter under the emitter resolution chain |
| ZSymbolTable exclude | 134k | 1.3% | narrowing subtype-name copies |
| registerEdgeText + StringPool.set (C3c cost) | 27k | 0.3% | edge caches + interning -- the whole arc bookkeeping |

The pool is the single authoritative copy of AST identifier text; the
remaining churn is (a) the emitter resolving types by NAME over registry
Strings -- ~~migrating registry type names to pool ids is the big lever~~ --
and (b) nameTextCopy call sites that could borrow or id-compare instead.
(The edgeNameId/edgeText caches this once also named are already deleted.)

**SUPERSEDED 2026-07-28 -- do not act on the struck clause**, same defect as
the `resolveTypeIdByName` row above: it is a census of where owned Strings
come from, read as if the pool were free. It is not. Migrating a registry
name to a pool id is **allocation-NEUTRAL on every reader that wants text**:
`poolTextCopy` -> `pool.get` -> `.copy` is exactly one `String_copy`, the
same call `got.name.copy` already makes, and ~190 of the ~215 reader sites
hand the text straight to a C-identifier builder or a diagnostic. The census
counts the copy either way. Two further corrections: the reader count it
implies was inflated ~1.6x by a substring collision (`grep -c typeNameOf`
also counts `typeNameOfReg9` -- 77 real, not 193), and a direct experiment
(`2d8e831`, above) cut 76k allocations, 0.76% of the total, for **zero** wall
movement. The real argument for the `ZType.name` migration is the 232 -> 208 B
**row shrink** across ~35 `for tid < nextTypeId` scans, which is a locality
lever, not an allocation one -- the same distinction the `nodeType` row draws.

Not in the 2026-07-17 census, and visible only after the Decl-tree arc: the
demand-resolution done/grey sets `ZTyping.definedKeys`/`resolving` are
`Set String` probed with a freshly interpolated `"{unit}.{name}"` per probe --
sometimes the same key twice in adjacent statements. Both halves are already
pool ids, so a composite u64 key removes a String allocation from the hottest
loop of the phase that regressed.

## Toolchain findings (2026-08-06 @ ebe15c0d)

The series stays **gcc -O1** (`OPTFLAGS` in the Makefile): every row above was
measured that way, and neither alternative moves what the series tracks.

- **gcc -O2 -- now the RELEASE level** (`OPTFLAGS`, 2026-08-07): allocation
  count identical to -O1 modulo run wobble (9,597,630 vs 9,597,793 when first
  measured), so the wall gap against clang was always -O1 codegen quality and
  nothing allocation-shaped. Wall, best of 7 on the same input: **-O1 0.53s vs
  -O2 0.37s** at `c88e16fb` (0.55s -> 0.40s when first measured at
  `ebe15c0d`), for a `bin/zc.c` compile of 14s -> 20s. `bin/zc`, `bin/zl` and
  `bin/zls` -- the three `make install` ships -- are built at -O2; the series
  keeps its own `out/zc-perf` at gcc -O1 so the table stays one measurement.
  `make warn-check` passes at -O2 under a global `-Werror`.
- **clang -O1**: wall 0.37s and ~750k (-7.8%) fewer allocations. Same-source
  A/B at the String/StringView arc end: gcc 9,576,316 vs clang 8,826,150
  (delta 750,166); at the L022 migration's end (`5c59c47a`) gcc 8,999,867 vs
  clang 8,246,464, delta 753,403 -- unchanged by that migration, because it
  removed copies of a different kind. An allocator attribute on `z_xmalloc`
  (`malloc, returns_nonnull, alloc_size`) changes nothing.

### What the delta was (RESOLVED 2026-08-07, delta now ZERO)

The mechanism recorded here was wrong, and so was the rule built on it.

- **The rule.** LLVM deletes an allocation when nothing ever LOADS from it and
  its pointer never escapes: stores into the buffer, a `memcpy` with it as
  destination, and the `free` are all removable uses. It does **not**
  read-forward to the source bytes -- a `memcmp` consumer, a `contains` loop
  and an escaping `&c` each keep the allocation alive under clang -O1. gcc
  drops a malloc/free pair only when nothing writes the buffer either, so the
  variable-length `memcpy` inside `String_copy` keeps every one of them.
- **Isolate it with one compiler, not two.** `clang -O1` plus
  `-fno-builtin-malloc -fno-builtin-free -fno-builtin-calloc
  -fno-builtin-realloc` stops LLVM recognising the allocator and changes
  nothing else; that build matched a gcc build of the same C to within **4**
  allocations. `make perf-elision` is that A/B. A cross-compiler comparison
  confounds the pool with every other codegen difference.
- **It was ONE call site.** DHAT on both clang builds, diffed by stack:
  `String_copy` inside `Lexer.peek` ran 1,128,811 times per self-compile and
  756,750 of those copies -- the entire net delta, to the allocation -- were
  never read. `peek` handed back an owned `Token`, and 57 of the parser's 64
  peek sites wanted only `toktype` or a line/column.
- **Fixed at the source** (`bdce1ec7`..`347a3bad`): the Lexer answers with
  `peekType` / `peekPos` / `peekText` and no longer copies the buffered token
  at all -- `peek` and `currentCopy` are deleted. Allocations 9,082,959 ->
  7,959,463 (-1,123,496, -12.4%: more than the pool, because the live copies
  went with it), and **`make perf-elision` reports zero** -- gcc and clang
  make the same number of allocations.
- **Standing rule**: the pool belongs at zero. A non-zero `perf-elision`
  means new emitted code allocates a buffer, fills it, and frees it unread --
  find it by diffing DHAT profiles of the two builds, which share inlining and
  frame names, so only the deleted allocations differ.


## Member signatures resolve on demand (Arc 1a)

A type's METHOD signatures no longer resolve when the type is named; they
resolve when a consumer first needs one. Fields, sum arms and the synthesized
`create` / `==` / `hash` stay eager -- a field's type is part of the type's
identity and every arm is part of its layout, so both are needed to lay the
type out at all. A method's signature is neither.

The measurement is emitted C, because that is what the cut is for: less C to
parse is the whole point of the arc.

| | eager | on demand |
|---|---|---|
| `hello.c` | 49,459 B | **37,472 B (-24.2%)** |
| all 148 examples | 8,572,267 B | **6,785,376 B (-20.8%)** |

Not the census's projected floor, and the gap is accounted for: the seeds are
still in. `seedSystemDemands` demands fourteen numeric records unconditionally
in every program, so their RECORDS still resolve -- laziness only stops their
member blocks. Arc 1b removes the seeds, and that is where the rest is.

`--eager` restores the old behaviour and is what `zls`, `zl lint --full` and
`make ci`'s `eager-guard` run, so a definition nothing references is still
type-checked somewhere.

## A create's parameters are its fields

A synthesized `.create` takes one parameter per data field, so
`materializeCreateParams` forced every member of the type before reading the
field list. Every member includes every METHOD, forced only for the field
filter to see a FUNCTION type and drop it again -- the same thing it does with
the type 0 an unresolved member carries. `StringView` has 42 members, two of
them `parseF16` and `parseF128`, so every program that named `StringView` --
which is every program -- resolved f16 and f128 and emitted the two `resultval`
monos that carry `_Float16` and `__float128`.

`forceAllFields` resolves what a field walk reads: everything that is not a
methodDecl, plus the methodDecls holding a function-pointer slot. `fnptrField`
is a declaration flag written before any signature resolves, so the test forces
nothing itself.

| | before | after |
|---|---|---|
| `hello.c` | 37,473 B | **32,814 B (-12.4%)** |
| all 418 programs | 18,381,192 B | **16,453,801 B (-10.5%)** |
| `_Float16` lines, all programs | 429 | **12** |
| `__float128` lines, all programs | 1,684 | **1,267** |

What remains is what the source asks for: `float_widths` and `float_mod` are
the only programs left emitting either type, and the 1,254 `__float128` in the
count are three lines per program inside `#if __SIZEOF_FLOAT128__`, which a
backend without `__float128` preprocesses away.


## IdMap / IdSet — an append-only, id-keyed container

**This arc's metrics are allocation count and bytes churned, not CPU.** The
containers it replaces were already O(1); what they cost was a heap header per
instance and 24 bytes of tombstone-and-hash overhead per entry. A flat wall
column is the expected result, not a failure.

`IdMap` / `IdSet` are `Map` / `Set` with the two things the compiler never uses
removed. Nothing is ever removed from a keyed structure here — across ~99k
lines there are exactly two map removals, both `String`-keyed — so the `alive`
byte and the compaction it forces go, and with no tombstones `entries` is dense
and `length` alone answers "how many", "where next" and "when to grow". The key
is an unsigned scalar by construction, so the stored hash goes too, `indices`
narrows from `int64_t` to `uint32_t` (`pos + 1`, `0` = empty), and the struct
lives on the stack like a `List` rather than on the heap like a `Map`.

Per entry, keyed `u64` → `declid`: 32 bytes becomes 16, a set element 24
becomes 8, an index cell 8 becomes 4, and an empty container costs one
allocation fewer than it has arrays.

### What the migrations moved

Each row is an adjacent A/B — the change stashed and unstashed on the same tree
— because **`perf-strict` measures a SELF-compile, so it conflates "allocates
more" with "has more source"**: this compiler allocates ~84.5 times per source
line, so 547 added lines move the column by ~46,000 on their own. Hold the line
count fixed, or measure a fixed input, or the number is about the diff's size.

| migration | allocations | bytes |
|---|---|---|
| `SetVal u32` → `IdSet` (zenv/checkIf name sets) | −73,842 | −30.9 MB |
| `MapVR u64→String` (5 emitter locals + 3 fields) | −16,965 | −3.2 MB |
| `NameIndex.byName` + 2 ztyping fields | −136 | −8.5 MB |
| six string-valued `Decl` side-tables | −79 | −2.0 MB |
| generic-child edges (two lists → one map) | −3,380 | −0.3 MB |
| **total** | **−94,402** | **−44.9 MB (−9.7%)** |

The first row is 32,602 instances per self-compile, all short-lived locals, and
more allocations vanish than there are instances: a `Set` cost up to three
(header, indices, entries) where an `IdSet` below the small threshold costs one.
The last row was forecast to move nothing — it was filed as a linear scan, a CPU
shape — but two parallel lists became one map, and below the threshold that is
two allocations becoming one.

### Identity indexing was measured and rejected

The design called for `key & mask` with no mixer at all. Over a self-compile,
7,391,762 finds:

| | splitmix64 (before) | identity `key & mask` | golden-ratio multiply |
|---|---|---|---|
| total probe steps | 12,928,227 | **22,850,966** | **12,661,979** |
| finds ≥ 16 steps | 11,975 | **179,537** | **7,868** |
| worst probe run | 73 | **1,241** | 100 |
| arithmetic | 2 imul + 3 shift/xor | none | 1 imul + 1 shift |

Identity *wins the head* — 5,368,390 one-step hits against 4,933,136, and 1.2M
fewer steps in the ≤4 band — and loses 15× in the tail, where 2.43% of lookups
produce the entire regression. Per mono, three tables account for all of it, the
worst being `MapVV u64 → declid` at +7.4M steps: `& mask` keeps only the low
bits, so a table holding a *sparse subset* of a wide id space folds unrelated
groups onto each other. A dense run of ids is exactly as good as predicted —
`MapVV u64 → zownership` went from a worst run of 73 to 1 — the aggregate is
just dominated by the tables that are not dense.

The shipped mixer beats the splitmix64 finalizer it replaces on both total steps
(−2.1%) and tail (−34%) at about a third of the arithmetic. It does not recover
identity's head win; a per-table choice would, and is unexplored.

### What was left, and what that assumption was worth

The composite-keyed tables stay: a two-field record key cannot be an id, and
packing two id spaces into one integer is a hack this compiler does not do.

The `String`-keyed ones were written up here as the ceiling — `Set String`
alone is 61,157 of the 111,607 Map/Set instances a self-compile creates, and
89.9% die empty. **That was wrong, and expensively so.** See the follow-on row
below: the largest of them was keyed by text only because the plumbing rendered
ids to strings and back. Before calling a container unmigratable because its
key is a `String`, check where that string came from.


## IdMap follow-ons — getMut, and narrowing state as ids

Two changes after the arc above was written up as complete. The second is the
largest single win of the whole effort, and it came out of a container the row
above had dismissed.

### `IdMap.getMut`

A Map has no mutable twin by design — its slot depends on a hash *of* the key,
so writing through a view would strand the entry in the wrong bucket. An
IdMap's slot depends on the key alone, so the objection does not apply.

It earns its place on the value family only: `IdMapR.get` already hands back a
pointer into the entry and writing through it works, but `IdMapV.get` returns
an `optionval` — a copy — so updating a valtype value meant read, change,
`set`. No measurable allocation effect; it removes a round trip.

### Narrowing state carries name ids, not name text

| | before | after |
|---|---|---|
| allocations | 8,359,867 | **8,174,339** (−185,528, −2.22%) |
| bytes | 417,626,858 | **409,131,920** (−8.5 MB, −2.03%) |

Net **+24 source lines**, so at this compiler's ~84.5 allocations per source
line the effect is slightly larger than the measurement shows.

`sumArmNames` read a sum type's arms out of `declMemberList` **as name-pool
ids** and immediately rendered each one to an owned `String`; `exclude`
collected them into a `Set String` and compared them as text; both callers
already called `nameIdOf` to turn the *subject* back into an id on the same
line they passed the arm name as a view. The ids existed at both ends and were
discarded in the middle — the thing `CLAUDE.md` names directly: compare by id,
not by name.

**Most of the win is not the container.** `ZEntry.excludedSubtypes` is a field
on every symbol-table row, so a self-compile builds 40,648 of them, but every
entry one held was an *owned String*, re-copied on every row copy, every
`collectExcluded` and every dump row. An `IdSet` of ids copies nothing: the id
is the value. The `Set` header was the small part.

`ZEntry.narrowedSubtype` is a `nameid` for the same reason (`0` where `""`
was), `narrowedArmOf` and `getSubtypeName` return ids, and all three call sites
fed them straight into a `declChildOf` **name** lookup that is now
`declChildOfId`. The SQL dump resolves ids back to text — the one place text
was ever wanted. The `zenv` smoke golden matched unchanged, which is the
behaviour-preserving evidence.

### Running total for the container work

| | allocations | bytes |
|---|---|---|
| the five container migrations above | −94,402 | −44.9 MB |
| narrowing state as ids | −185,528 | −8.5 MB |
| **total** | **−279,930** | **−53.4 MB** |

### The post-guard dead-alias set

| | before | after |
|---|---|---|
| allocations | 8,174,339 | **8,159,064** (−15,275) |
| bytes | 409,131,920 | **408,486,991** (−645 KB) |

`pgDead9` marked which post-guard aliases a reassignment had ended, by name,
while `pgVid9` sat beside it holding the variable id at the same index — and
both `has` sites read that id two lines later anyway. Keyed by the vid, which
is the stronger key: a variable id is minted by the compiler and unique by
construction, where a name raises whether the string was interned at all.

Net −2 source lines. A self-compile builds 15,314 of these; 93.7% of the
`Set String` instances it creates die empty, so most of the win is the header
an empty `IdSet` does not allocate.

### The post-guard subject-seen set

`doneS9`, the same shape again -- `chSubjectVids` parallel to `chSubjects`,
the id read on the line after the membership test. −849 allocations, −41 KB,
net −1 line. Small, and most of these are not empty; the part that does not
show in the allocation column is the inner scan, which looked for other
clauses on the same subject by comparing subject TEXT inside a nested loop
over the same list, and now compares ids.

### Where this stops, and why

After the migrations above a self-compile creates **26,638** Map/Set instances,
down from 111,607. Two sites hold 96% of what is left, and only one of them
should move.

`subs` — the generic-argument substitution maps in `ztypecheck` — is 8,435
instances. It stays on `Map`, and the reason is the test that made the
narrowing migration worth doing in reverse: **its keys are not uniformly
interned.** Some are AST labels, which are; others come from
`firstGenericParamName` off the registry, which carries no guarantee of being
in `ast.names`. `poolFind` answers 0 for a name that is absent, so two distinct
uninterned names would compare EQUAL — a correctness hazard, not a cost. Making
them interned means `poolSet` during typecheck, which grows the pool and
renumbers ids. Against that: 93 references across the monomorphisation core.

The rest were measured rather than estimated, and each fails on one of the
two tests:

| candidate | instances | verdict |
|---|---|---|
| `subs` | 8,435 | keys not uniformly interned; 93 references |
| `seen` (validateGenericTypeArgs) | 1,141 | key is sometimes a derived `primaryName`, so not interned |
| `armNames9` (×2) | 46 | safe — arm names carry their id — but negligible |
| `gpNames9` | 19 | keys are registry names, not pool names; negligible |
| `boundParams` | 0 | a self-compile never constructs it |
| `ArmPredFact` + `covered` / `required9` | 151 facts | **done** — estimated ~300, actual −2,837 |

`ArmPredFact` was done, and is worth recording for how the estimate went.

One of its two constructors took a name id it already held and called
`nameTextCopy` on it; every consumer then called `nameIdOf` to convert both
fields back — the same round trip as the narrowing state, one layer up. At 151
constructions per self-compile the case for fixing it was the code, not the
column, and it was filed here as a readability item worth "a few hundred"
allocations.

It came in at **−2,837 allocations and −129 KB**, an order of magnitude more.
The estimate counted *facts* and forgot what each fact **owned**: two heap
Strings, copied again when facts were duplicated into `guardFacts` and when the
subject was lifted into `pgSubj9`. **Count what the structure owns, not how
many of it there are.**

It also removed six `nameIdOf` calls and a `declChildOf` name lookup, and let
`armPayloadReadonly` / `shadowCollapseIfSingle` / `postGuardRecord` take the
ids each was rebuilding on its first line. Text is now rendered in exactly two
places, both of which need it: the lock path, which is spelled, and
`postGuardName` plus the mangled receiver, which are spelled into the emitted
C.

So the caveat this row opened with survives, but with its converse attached:
**the shape being wrong is not the same as the shape being expensive — and a
cheap-looking shape is worth fixing anyway when the code reads better for it.**

The rule this arrived at, worth keeping: **an id key is only sound when the id
is minted, not looked up.** A variable id or a name a node already carries is
free and exact. A pool lookup on a string of uncertain provenance trades a
copy for a silent collision.

## The id-keyed containers become the storage (2026-08-19, `d5c125c4`..)

The container arc above built `IdMap` / `IdSet` and migrated what was cheapest
to migrate. It left the case that motivated it half done, and it left every
table whose key was an id but whose declaration still said `Map` or `Set`.

### A declaration's namespace was stored twice

`Decl` held `children` — a `ListVal declchild`, always complete and always in
declaration order — and `nameIndex`, a slot into `ZTyping.nameIndexes` naming
a `NameIndex` that held the same edges again, keyed by name, built lazily past
`bucketScanMax` (16) and maintained on every append after that. Both readers
carried the crossing as a branch: `declFindChild` and `declFindLocal` each
tested `nameIndex > 0` and either probed the map or scanned the list.

The reason for the slot is in the field's own comment — *"a map is a heap
object, and every row would own an empty one"* — and it stopped being true
when the container landed. An IdMap is a 24-byte stack struct, the same size
as the `ListVal` it replaces; it allocates nothing while empty; and **it makes
exactly this crossing internally**, which is the point. Below `SMALL_MAX`
there is no index and `find` scans the dense entries — the loop
`declFindChild` was writing out by hand.

So `children` is `IdMapV zast.nameid -> declid` and the rest is deleted:
`declchild`, `NameIndex`, `ZTyping.nameIndexes`, `Decl.nameIndex`,
`bucketScanMax`, `nameIndexLookup`, the promotion block and both fallback
legs. `declAddChildEdge` is one `set`.

**Two soundness questions were measured before anything was written**, over a
self-compile and 488 corpus programs, 1,313,867 edges:

| question | why it matters | answer |
|---|---|---|
| any child with name id 0? | an IdMap collapses every anonymous child of one parent onto one entry | **0** |
| any repeated (parent, name)? | `set` overwrites where `append` appended | **0** |

The `zsqldump` guard on `nameId > 0` suggested the first was possible; it is
not, and the one deliberately unnamed mint (`declNewFrame`) already takes its
parent link directly rather than through an edge.

### The threshold, measured at all four settings it can have

`SMALL_MAX` is compared against `capacity`, which is always a power of two,
so the constant has **four reachable settings and no others** — 12, 20 and 24
are aliases of 8, 16 and 16. `entries` is capped at `capacity * 2 / 3`:

| `SMALL_MAX` | index built at capacity | small mode holds | instructions | allocations | bytes churned |
|---|---|---|---|---|---|
| 8 | 16 | ≤ 5 entries | 3,876,073,515 | 8,150,711 | 390,470,385 |
| **16** | 32 | **≤ 10 entries** | **3,875,096,208** | 8,130,318 | 389,165,236 |
| 32 | 64 | ≤ 21 entries | 3,927,641,704 (+1.36%) | 8,119,419 | 387,770,164 |
| 64 | 128 | ≤ 42 entries | 3,989,780,469 (+2.96%) | 8,115,470 | 386,759,220 |

Fixed input, `perf stat -r 5`, spread ±0.01–0.06%. **16 is the optimum and
stays.** Allocations and bytes improve monotonically as the constant rises,
because a higher ceiling means fewer index arrays — so this is the one knob in
the series that **cannot be read off the allocation column**, which would pick
64 unconditionally and pay 2.96% of instructions for 0.6% of bytes.

The scan stays competitive further than the folklore figure suggests: the
common advice that a map overtakes a list past about eight entries is about
hashed maps with owned keys, and this scan is `==` over a dense array of raw
scalars with no hash and no allocation. 8 (≤5 entries) is already slightly
worse than 16.

### The tables that were id-keyed but not id-keyed containers

Three sweeps, each with its own oracle:

* **Six membership tables in the emitter** (`usedLits`, `emittedUserTypes`,
  `fwdDeclaredTypes`, `demandGatedTids`, `usedTypeIds`, `emittedConformance`)
  were `MapVV u64 -> bool` storing only `true`. Every reader spelled
  membership as a `get` and a two-armed match answering `false` on `none` —
  which is `IdSet.has`. Bytes −566,313, instructions flat.
* **28 `SetVal u64` and 65 scalar-keyed `MapVV` / `MapVR`** become `IdSet` /
  `IdMapV` / `IdMapR`: the node-type and mono stamp tables, `declByTypeId`,
  `publicNsByOwner`, `defUnitId`, `resolveByNameC`, `frameLabel`,
  `delimClose`, `zls`'s position index, the registry's `genericParams`.
  Nothing in the tree calls `.delete` on any of them, which is the
  precondition. **Instructions −1.63%, bytes −14.7 MB (−3.63%).** The bytes
  are the entry shrinking (no `alive`, no stored hash, a 4-byte index cell);
  the instructions are the probe losing its hash.
* **The generic-argument table was a List AND a Map over one key space**, and
  a census found almost none of it live: `genericArgOf` / `hasGenericArgs`
  were called only from this unit's own smoke prints, `setGenericArg` had one
  real caller which always passed `argTypeId: 0`, and
  `ZTypeGenericArg.position` had no reader at all. Its one real consumer,
  `checkGenericCall`, scanned every row in the program per generic call site
  to recover one function's parameter names — which `registerGenericParams`
  had already recorded on the registry, from the same AST items through the
  same filter, on the line above. **The equivalence was probed, not argued:**
  both lists computed at every call and compared element by element, 3,381
  comparisons over 449 programs, zero divergence — and the probe was inverted
  first to prove it fires rather than passing vacuously. −103 lines;
  instructions flat, which is the honest reading: the scan is per call site
  and there are not many. The win is the table, the composite key and the
  scan being gone.

### Arc total

Same input (this row's `src`), gcc -O2, `perf stat -r 5`, spread ±0.01%:

| | before | after | delta |
|---|---|---|---|
| instructions | 3,956,906,257 | 3,875,408,009 | **−81,498,248 (−2.06%)** |
| cycles | 1,811,893,394 | 1,761,005,266 | **−50,888,128 (−2.81%)** |
| allocations | 8,133,049 | 8,130,318 | −2,731 |
| bytes churned | 406,597,946 | 389,165,242 | **−17,432,704 (−4.29%)** |

`allocs == frees` throughout. This closes the `findLocal` item the
environment-arc row (`4fc75709`) left open — "locals as Decl children, a
linear scan per materialised block frame; ~1.3%" — and the "lazy
`byName`/`children` on Decl" follow-up named at step 2 of that arc.

**Oracles, per commit:** the SQL dump byte-identical old-compiler against new
on the same source (3,159,509 rows over the three drivers for the namespace
change, plus 485 corpus programs), and emitted C byte-identical on all 446
corpus programs that emit standalone. `decl_child` carries both the edge set
and its position, so an identical dump is the proof that insertion order
survived the move off a list.

### What the container gained, and what it still refuses

`keyAt` / `valueAt` read an entry by position. `entries` is dense and
insertion-ordered with nothing able to perforate it, so a position is a real
coordinate here in a way it is not on a Map. They exist for the walk an
iterator cannot serve — one whose body must reach the object that OWNS the
container, which an outstanding iterator borrow forbids for the whole loop.
Implementing them by counting through `iterateItems` instead would turn the
ordered walks over a unit root's ~1,400 children into O(n²).

### What is still on `Map`, and which part of that is unfinished

Two groups, and only one of them is a decision.

**Staying on `Map`, by design:** `subs` (keys not uniformly interned), the five
emitter assoc-lists (14 `remove` call sites), the `pend*` triple (a
(parent, name) key, a handful of entries), and the 16 composite-key
instantiations — `monostamp`, `callslot`, `constslot`, `dataconstkey`,
`dataslotkey` — whose keys are two-field records and so cannot be an `idkey`.

**The rest, now done.** The first sweep converted the four literal type
spellings its script matched, not the family; 98 instantiations were still
`MapVV` / `MapVR` over an id key, most of them ztyping's per-node stamp
tables. They are all converted.

Every candidate was checked for the two methods an append-only container
cannot offer *before* anything moved: **not one of them calls `iterate`,
`remove` or `delete`** — the whole set uses get / set / has / iterateItems,
which is the entire IdMap surface. So the bulk was a declaration change with
no call site touched.

One capability had to land first. `capacity:` on a builtin container
construction is a create argument riding the construction sugar, and two lists
in `checkConstruction` admit it by template identity — one to keep it out of
the spec key and subs, one to stop it failing over to field inference. Both
named the eight List / Map / Set templates and neither named the id-keyed
three, so `atomVariableId`'s `capacity: nodeEst / 4` pre-size stopped
compiling the moment its type changed. **The seventh and eighth name-keyed
family list this container has had to join**, and — as ever — it had to be
declared, seeded, and only then used.

Eight of the converted tables were then found to be sets written as maps
(`collectionChildrenBuilt`, `variableMutated`, `variableMoved`, and the
emitter's `group` / `emitted` / `lateSet` / `emptySet` / `emittedSet`): every
writer stored `true` and every reader answered `false` on `none`.
`zemitterc.idSetHas` went with them — a helper whose own comment read
"membership in a Map-u64-bool id set", which is a set with extra steps.

| slice | instructions | cycles | bytes churned |
|---|---|---|---|
| the 98 declarations | −0.83% | −3.35% | **−21.1 MB (−5.43%)** |
| the eight sets + `idSetHas` | +0.04% | — | −183 KB |
| **total** | **−0.75%** | **−2.10%** | **−21.2 MB (−5.48%)** |

Fixed input, `perf stat -r 5`, spread ±0.05%; `allocs == frees` throughout.
The bytes are the entry losing its `alive` byte and its stored hash while the
index cell halves, over tables that carry one row per AST node. The IdSet
slice is recorded honestly: it costs 0.04% of instructions, marginally outside
the spread rather than inside it, and is kept for the 8-byte entry and for
nine `get`-and-match readers becoming one call.

`declPendMemberType` also stops scanning past its hit — `declPendWrite` keeps
one entry per (parent, name), so the first match is the only one, and it sits
on the miss path of `declChildOfId`.

Oracles per commit: emitted C byte-identical on all 446 corpus programs that
emit standalone, and the SQL dump byte-identical (3,153,644 rows for the
declaration slice, 2,279,954 for the sets).

**Nothing id-keyed is left on `Map`.** What remains is the 16 composite-key
instantiations above and the String-keyed tables.

## A write-only local is a dead allocation (2026-08-19, `8a47bbbb`)

Three dead stores surfaced as `-Wunused-but-set-variable` on a fresh clone's
build. Fixing them was tidy-up; the interesting part is why nothing in the
toolchain had caught them, and what that answer was worth.

**L013 (unused-local) could not see them.** `reportUnusedVars` built its
used-set from every `atomVariableId` stamp, and a reassignment's *target* atom
carries one exactly as a read does -- so a local that was only ever assigned
counted as used. The rule fired only on a local never mentioned again at all.
The fix collects the reassignment targets first and leaves them out, and only
**bare** atom targets: reaching `a.b` to write it genuinely reads `a`.
Filtering on the enclosing node being a reassignment does not work, and was
tried -- the stamp sits on the AtomId child, not the statement.

L014 (unused-parameter) and L020 read the same set, so one fix corrected all
three. Checked against the shapes that must not warn: written-then-read, an
accumulator on its own RHS, a write through a path, a write in both arms of an
`if`, a parameter filled through a method call, and a parameter written
through a field path -- the last because that mutation *is* visible to the
caller.

**It then found five more dead stores that gcc cannot see**, because
`-Wunused-but-set-variable` reaches scalars and these were all owned `String`s:
`ctorNm9` (in `emitCallValue`, with three `.copy` assignments on a per-call
path), `meN9`, `sfxC3`, `unm9`, `bnm9`. Removing `sfxC3` took five more locals
with it -- a cascade the fixed rule then caught itself.

| | before | after | delta |
|---|---|---|---|
| allocations | 8,125,212 | 8,114,052 | **−11,160** |
| bytes churned | 366,362,782 | 366,237,615 | −125,167 |
| instructions | 3,820,026,364 | 3,819,325,362 | −701,002 (−0.018%) |

Fixed input, spread ±0.01%; emitted C byte-identical on all 446 corpus
programs. **The rule is linter-only -- `zsource.z` is compiled into `zl` and
`zls`, and `bin/zc.c` contains no lint code at all -- but its findings were in
`zemitterc` and `ztypecheck`, so the compiler does 11,160 fewer allocations
for identical output.** The linter's own added AST walk is free: 2.50-2.55s
before, 2.33-2.65s after over the same scope.

The lesson worth keeping: **a warning class the C compiler cannot express is
one the linter has to own.** An owned-String dead store allocates, copies and
frees on every visit, and no amount of `-Werror` reaches it -- gcc sees a
struct assignment doing real work.

## Row detail

One section per baseline-table row, in table order. The table's
`change` cell links here; this is where the reasoning, the oracle and
the caveats for that row live.

<a id="r-48e5f08d"></a>
### The type-alias mechanism

**2026-08-24 · `99bf159c`..`48e5f08d`**

A CORRECTNESS arc, rowed so it is on record that it did not cost anything. An
alias of a renaming re-export resolved to a shell instead of the type; a
unit-level alias could not name an instantiated generic; and a mono's label
head came from the spelling a use site reached the template by, so whichever
mint site ran first decided what the type was called.

The label is now composed at the mint funnel from the template's DECLARED name.
Measured against the arc's own base (`7e521f94`) on identical input -- the
frozen tree, so the two compilers do the same work:

| | allocs | bytes |
|---|---|---|
| `7e521f94` | 4,858,784 | 310,280,004 |
| after the funnel change | 4,864,320 | 310,372,943 |
| composing past the trie | **4,856,108** | **310,201,302** |

Composing the label at entry cost +5,536 allocations, because a trie HIT -- the
common case -- returns without ever reading it. Composed past the trie it is
-2,676 (-0.06%) against the base: callers now build only the argument suffix,
which is shorter than the whole label they used to build.

The table row above is the standard `make perf` self-compile, which is a
different workload from the frozen-tree A/B and is not comparable to it
line-for-line; the A/B is the attribution.

<a id="r-4f10844"></a>
### GROUND — post emitter-completeness arc

**2026-07-17 · 4f10844**

GROUND (post emitter-completeness arc)

<a id="r-3bcaba2"></a>
### W1: id-space queries and scan removal

**2026-07-17 · 3bcaba2**

W1: id-space queries, regNameIs scans, mainBodyMentions hoist, childOfWalk fast path, Map.getv

<a id="r-1b7c6d0"></a>
### W2: buffer reserves and container pre-size

**2026-07-17 · 1b7c6d0**

W2: emitter buffer reserves, Map/Set/List capacity:, stamp-map pre-size

<a id="r-fbb3426"></a>
### capacity inference fixed; stamp maps right-sized

**2026-07-17 · fbb3426**

capacity-inference fix + value-position capacity threading + right-sized stamp maps (the 1b7c6d0 pre-size was inert: value-position constructions dropped capacity)

<a id="r-81b9297"></a>
### A: tokenizer source-span token text

**2026-07-17 · 81b9297**

A: tokenizer source-span token text (goldens byte-identical)

<a id="r-ab2d177"></a>
### B: move-on-advance and parser payload moves

**2026-07-17 · ab2d177**

B: move-on-advance + parser payload moves (+ D: ctor-arg move gap proved stale, pinned in corpus)

<a id="r-7f8524f"></a>
### C: child-edge name interning

**2026-07-17 · 7f8524f**

C: child-edge name interning (pool + id-keyed buckets)

<a id="r-297f741"></a>
### A1: names-as-nodes interning

**2026-07-17 · 297f741**

A1: names-as-nodes interning (AtomId/LabelValue name -> u32 nameentry ref; hot readers on scoped row views; constVals probes on getv)

<a id="r-8727875"></a>
### C1: Ast carrier threaded through ~570 signatures

**2026-07-17 · 8727875**

C1: Ast carrier threaded (~570 sigs; ast.nodes indirection; ARCHITECTURE landing — B3-as-perf stays shelved) + StringView.hash native + unconditional z_hash.inc

<a id="r-dbd0899"></a>
### C2: names move to the Ast.names StringPool

**2026-07-17 · dbd0899**

C2: names -> Ast.names StringPool; nameentry arm deleted; synth dedup (ref==ref sound); hot readers borrow pooled text

<a id="r-17d8ba4"></a>
### C3: tree-scoped state onto the carrier

**2026-07-17 · 17d8ba4**

C3 (a units, b fileSegs, c edge names, d well-known ids): tree-scoped state consolidated on the carrier; ZTyping's private edge-name pool deleted -- ZTypeChild.nameId IS the Ast.names id, member resolution int-keyed where provenance is certain (ARCHITECTURE landing; +0.5% allocs = edgeText "" fillers + edgeNameId cache)

<a id="r-c29bf3d"></a>
### D1-D4: one string pool, id-keyed lookups

**2026-07-17 · c29bf3d**

D1-D4 single pool: wk member ids 5..31, id-keyed lookups where ids in hand, ZTyping edgeText+edgeNameId DELETED (no name text outside Ast.names; StringPool.find read-only probe). +1.8% allocs = the third nameIds out-list on recFieldLists/variantArms/protoChildMethods call sites (superseded by the registry-ids arc)

<a id="r-6eee916"></a>
### D5: AST labels become pool ids

**2026-07-18 · 6eee916**

D5 (a: namedoperation label -> pool id, last owned Option String on the AST gone, 7 label helpers collapse to nameTextCopy/nameTextEq; b: text-taking setChild deleted -- analysis interns ONLY at 6 explicit ast.internString mint sites, else id-keyed setChildId via wk consts / in-hand ids / poolFind read-probe). Pure refactor: 129/129 examples behaviourally identical. -29k allocs (per-label Option removed)

<a id="r-e1-seeded"></a>
### E1: resolveTypeIdByName keyed by id

**2026-07-18 · (E1 seeded)**

E1: id-key resolveTypeIdByName -- poolFind the name ONCE, then id-keyed stages (childOfId); the composed-key fallback materialises text lazily. The same name was find()-ed ~9+N times per resolve (once per stage + once per unit in the cross-unit loop); now once per resolve. Pure refactor (129/129 examples identical). -354k allocs (-3.4%) vs D5

<a id="r-e2a-seeded"></a>
### E2a: the unit list cached on Ctx

**2026-07-18 · (E2a seeded)**

E2a: cache the unit list. resolveTypeIdByNameId rebuilt utids9 (List u64) + the fallback uks9 (List String, every unit key copied) on EVERY cross-unit resolve, iterating unitNameTid each time. unitNameTid is typecheck-stable, so snapshot it once per Ctx (ensureUnitCache -> ctx.unitTidsC/unitKeysC). Pure refactor (129/129 identical). -533k allocs (-5.3%) vs E1; -8.5% cumulative from D5

<a id="r-1426aaa"></a>
### HEAD re-measure after 72 feature commits

**2026-07-21 · 1426aaa**

HEAD re-measure (+72 feature commits since E2a, incl. the completion sweep): the accumulated regression the perf arc attributes

<a id="r-a1-seeded"></a>
### A1: walkedMethodOwners keyed qualified

**2026-07-21 · (A1 seeded)**

A1: walkedMethodOwners keys qualified "unit.name" everywhere. The sweep probed bare names while depWalkUnit marked qualified, so it re-walked every dep/stdlib type the dep walks had already checked, and same-named types across units masked each other's sweep entry; + mainMemberIndex gated to the unit it indexes (the qualified probe exposed a latent cross-unit index clash)

<a id="r-a2a-seeded"></a>
### A2a: fnAutoCallable walks rows by pool id

**2026-07-22 · (A2a seeded)**

A2a: fnAutoCallable iterates childIndex rows by pool id (wkThis / poolFind'd thisParamName / hasChildDefaultId are all id-keyed) instead of materializing every param name String through dataFieldNames per dotted-reference probe. dhat attribution: the post-E2a alloc growth is the ctor-validation arc (walkCallArgsHoist 698k blk incl. this chain; emitter isCtorOwnerCall 272k blk) + the sweep/dep body walks (~235k blk, paid-for correctness); printableInterpTid measured at only 51k blk / 0.5% — memo not warranted; gpNames9 sub-1% — skipped as predicted

<a id="r-a2b-seeded"></a>
### A2b: resolveTypeIdByNameId memoized on Ctx

**2026-07-22 · (A2b seeded)**

A2b: resolveTypeIdByNameId memoized on Ctx (nameId -> tid, misses cached as 0). isCtorOwnerCall probes every emitted call with base names that are usually VALUE names: each guaranteed miss walked all stages incl. the composed-key loop (one "unit.name" String per unit). The typed model is frozen during emission so per-name resolution is emit-stable; misses dominate, so caching 0 is the whole win. Emit -17%; regression vs E2a fully closed (allocs now 9.37M < 9.56M, wall 0.62s < 0.64s)

<a id="r-5eccf67"></a>
### redesign HEAD re-baseline

**2026-07-23 · 5eccf67**

redesign HEAD re-baseline: name/Decl/Type identity + generic-metadata composite-key arc (first row since A2b -- the +0.8M allocs / +0.04s vs A2b is the whole redesign's Decl-tree build + probes, not one change)

<a id="r-6f19e46"></a>
### mangled-mono-name namespace retired (P4)

**2026-07-24 · 6f19e46**

mangled-mono-name namespace retirement (arc P4, `8640c29`..`6f19e46`): delete the synthetic `unit.<mangled>`/`system.<mangled>` namespace; the emitter no longer resolves io/net mono canon stems or shells by an O(nextTypeId) name scan -- `ioCanonCname` memoized on Ctx (P4.6, dissolves the ground census's single largest chain, ~5.85M blk) and `shellResolveByName`'s all-units brute-name scan deleted (P4.7, the delete's ~48k-alloc regression, DHAT-localized). Emit 312->217ms.

<a id="r-ada99da"></a>
### child-edge table retired (Q2, 72 commits)

**2026-07-26 · ada99da**

child-edge table retirement (arc Q2, `3dca56c`..`ada99da`, 72 commits): the Decl tree becomes the single member index -- visibility, member metadata, member order and member enumeration all answer from declarations; `typeChild`/`childIndex`/`childNameId`/`childTypeById`/`ZTypeChild` and the 8 metadata sidecars are deleted. **Regression owned, not attributed elsewhere**: typecheck 246->306ms, allocs +1.1M, bytes churned +140MB, LOC +972. The edge tables were flat rows read in place; the Decl helpers replacing them allocate per call -- `declMemberList`/`declChildRows` 2 Lists each (the emitter's `recFieldLists` has 33 callers), `declMemberCount` 4 to return a length (2 call sites are inside `walkCallArgs`' per-argument loop), `declMoveChildToEnd` 3 per first-sight member -- and `childOfId`'s pre-index fallback scans a 2-entry buffer 105,592x per self-compile for 0 hits.

<a id="r-8551e35"></a>
### post-Q2 cleanup

**2026-07-26 · 8551e35**

post-Q2 cleanup (`e09f96a`..`8551e35`, 18 commits): retire the migration instrumentation (13 counters + q2SpikeVerify); delete the Decl/ZTyping/ZType state nothing reads; collapse the two `childOf` vocabularies, the two member enumerators, the two field filters, the three sum-arm enumerations and the four zls param walks into one each; stop allocating row lists to answer scalar questions (`declMemberCount` took FOUR, `declMoveChildToEnd` THREE per first-typed member); drop the derived `type_children` dump table; delete 21 parameters kept alive only by a bare-identifier statement. **The allocation regression closes, the wall one does not**: allocs -8.1% and bytes churned -17%, but typecheck stays ~313ms against 246ms pre-arc. Under mimalloc these allocations were not what cost the time -- the remaining lever is Map probing (see the 2026-07-17 cache census), not allocation. DHAT after: `String_copy` 35.9%, `checkCall` 12.2%, the member-enumeration family down from 896k blocks (8.4%) to 232k (2.3%).

<a id="r-fb326d9"></a>
### borrowed-element get — a correctness arc

**2026-07-27 · fb326d9**

borrowed-element `get` (`d62395e`..`fb326d9`, 4 commits): `List.get`/`ListView.get` were declared `out of.borrow` over a `this.lock` receiver but lowered to `return _this->data[_idx]` for EVERY element type -- a dropped write for a class element, and a use-after-free when the element carries an inline container header. `get` moves out of the two runtime templates into `emitColGet`; 40 of 66 monos now return a pointer (19 user classes + String, List and ListView alike), unions stay by value ({tag, void* data} already shares its payload) and valtypes are self-contained. Call sites deref, so every value context stays value-shaped; a binding takes the pointer and aliases the name to the deref. **Correctness arc, not a perf one** -- this row is the checkpoint the next row is measured against.

<a id="r-929aede"></a>
### ZTyping.decls becomes a List

**2026-07-28 · 929aede**

`ZTyping.decls` is a `List Decl`, not a `Map u64 Decl`: DeclIds are minted dense 1..N by `declNew` (the sole writer) and never removed, so keying a hash map on a dense monotonic counter was an array with extra steps -- the row IS the index and `decls.get i: (id - 1)` is exact by construction. `declNew` becomes an append returning the new length; 58 read sites converted by script + 8 by hand across ztyping/ztypecheck/zsqldump/zsource/zls/zemitterc, each dropping an `Option` match for a `declValid` guard (0, the no-declaration sentinel, or past the end are the only misses). Reads BORROW the row rather than copying it out -- the preceding row is what makes that safe, since member writers still land in the stored Decl. **typecheck 302->270ms, emit 231->212ms, wall -14%, LOC -123.** Allocation COUNT rises 0.1%: a List grows by doubling where the compact dict grew an index. Landmine: `declValid` is a ZTyping method, so its receiver locks the WHOLE typing -- E0200 under a live `refDecl` iterator, so `zsource` spells the guard inline.

<a id="r-31f2de6"></a>
### node ids become one monotonic space

**2026-07-28 · 31f2de6**

node ids become one monotonic space (`df3b348`..`31f2de6`, 3 commits): the id space carried a classifier in its numeric range -- `0x20000000` meant generator lowering, `0x40000000` meant post-parse synthesis -- and `zsource.z` read the range back as a proxy for "is this node tabled", a property of the node rather than of its number. Synthesis minted from a disjoint range because it could not index the master table before a node existed, and `tableAppendRebase` renumbered the root on commit to undo the mint. The parser never needed either: it reserves a slot, mints the id of the slot it reserved, and fills it later. `zast.tableReserve` gives synthesis the same mechanism, so a synthesized node carries its final id from construction and commits with `tableAdd`; `tableAppendRebase`, both ranges, and the `GenIds` thread (35 signatures, 158 call sites, kept alive only to reach a counter) are deleted. **Architecture, not perf: wall and allocations are flat** (same-session pre-arc re-measure: 0.55s / 121MB / 10.02M allocs). The point is the downstream unblock -- `nodeType` had 30,846 range-valued keys on a self-compile and now has zero, none past the last node id, 57.3% dense, so `Map u64 u64` -> `List u64` no longer needs an overflow map. Cost: the hoist temps now reserve real slots, +30,313 rows on `ast.nodes` (+7.4%). Peak RSS fell 121->115MB against flat allocation volume, so that is residency, not volume -- measured, not attributed.

<a id="r-nodetype-list"></a>
### ZTyping.nodeType becomes a List

**2026-07-28 · (nodeType List)**

`ZTyping.nodeType` is a `List u64` indexed by nodeid - 1, not a `Map u64 u64`: node ids are dense 1..N (the preceding row is what made that true), so the stamp IS the slot. The map allocated `capacity: nodeEst` = 524,288 slots at 40B each -- 8B index + a 32B `{alive,hash,key,value}` entry -- whether occupied or not, against 253,338 live stamps; the List is 8B per node, all of it live. Landed in three commits so each had its own oracle: (1) all 43 writers funnel through `nodeTypeSet`, which records neither a 0 node id nor a 0 type id -- 0 is the `notype` sentinel and every reader already treated absent as 0, but `checkAtomid` stamped it unguarded for generic-unit template params, so 13 rows really existed; (2) all 56 readers funnel through `nodeTypeAt` returning a plain `u64`, dropping 25 `match … case some/none` blocks and `walkExprStamps`' `optionval` return; (3) the flip itself -- two accessor signatures, the field, the constructor, and three `iterateItems` loops that become index scans. **wall 0.55->0.50s, typecheck 247->226ms, emit 211->195ms, peak RSS 117.7->108.8MB (-8.9MB, predicted -9MB), bytes churned -3.4%, LOC -112.** The instruction count moved only -1.21% (6.618->6.538G, ±0.08%) while cycles fell 6.6% and cache-misses 8.7%: **the win is locality, not instruction count** -- which is why the 2026-07-27 probe-reduction attempt measured flat and this did not. Allocation COUNT rises 0.1%: a List grows 1.5x where the compact dict doubled. Verified by SQL-dump diff, same source through both compilers: the typing model is identical across all 148 examples and the 997,104-row self-compile; only `typed_nodes` row ORDER changes (hash -> nodeid ascending), and no golden carries it. Note `perf stat -r 5` instructions (±0.08%) resolves changes that `make perf` wall (±1.3%) cannot.

<a id="r-ctl-placeholder"></a>
### break and continue share one placeholder tid

**2026-07-28 · (ctl placeholder)**

`break` / `continue` share ONE unregistered type id instead of minting a fresh one each. `ZTypeRegistry` has two minters on one counter: `newType` files a `ZType`, `allocType` returns a bare number that never becomes a type. `checkFor`/`checkDo` called `allocType` purely because `st.define` needs a `ztypeId` slot for names that have no type -- two ids per `for`, one per `do`, again on every generic re-walk. **Measured: 2,214 of the 2,276 holes in the type-id space (97%) were these; the out-less-auto-call placeholders everyone assumes dominate are 62 (3%).** So 36% of the id space was `break`/`continue`. Safe because nothing reads the id: every consumer of those names dispatches on the NAME (`zemitterc.z:9450`/`:9471`, `ztypecheck.z:20107-20115`), and not one of the 2,214 ids ever appears in `typed_nodes` -- they never become a node stamp, so type equality (`tid ==`) cannot reach them. **Density 62.7% -> 98.4%, `nextTypeId` 6,094 -> 3,881**, so the ~35 `for tid < nextTypeId` registry scans are 36% shorter. **instructions -0.71% (6.538->6.492G, ±0.07%), cycles -1.26%, cache-misses -2.76%; wall and RSS flat.** A small CPU win; the point is the density, which flips the `typeById` -> `List ZType` arithmetic from losing (1.59MB vs 1.23MB map resident) to winning (~1.09MB). Type ids renumber downward, so raw SQL dumps move for the 18 examples with walked loops -- verified pure uniform renumbering: identical type name+kind sets in identical order across all 148, same row counts, and the 19 `.canon` goldens are untouched because canon renders an unregistered type as `'?'`.

<a id="r-typebyid-list"></a>
### ZTypeRegistry.typeById becomes a List

**2026-07-28 · (typeById List)**

`ZTypeRegistry.typeById` is a `List ZType` indexed by tid, not a `Map u64 ZType`. Landed in seven commits: an out-less method's auto-call resolves to the canonical `null` type instead of a fresh anonymous id (the last 62 holes, and the semantics the site's own comment already claimed); five spellings of "a type's registered name" collapse to one; then ~86 external raw `typeById.get` sites route through 17 registry accessors (`genericOriginOf`, `nameOf`/`nameIs`, `defOf`, `returnTypeOf`, `thisParamNameOf`/`hasThisParamName`/`thisParamNameIs`, `isGenericOf`, `isNativeOf`, `isValtypeOf`/`isReftypeOf`, `isBoxOf`, `isRuntimeIndexedOf`, `typeValid`); then the flip. Index is `tid` DIRECTLY -- type ids are 0-based (id 0 is `notype`, a real row), unlike `decls`/`nodeType` which are 1-based and use `id - 1`. **wall 0.51->0.49s, parse 104->84ms, typecheck 229->213ms, emit 193->182ms, instructions -6.9% (6.505->6.056G, ±0.06%), cycles -5%, cache-misses -4%, RSS 109->107MB, LOC -648.** The `Map_u64_ZType` find+get pair was 3.43% of cycles; `List_ZType_get` is **0.65%**. **I predicted 1.5-2.5% cycles and under-called it by 2x**: the reasoning was that the working set is only ~1.1MB and already L2-resident, so there was no locality ripple to win -- true, but it missed that a bounds-test-plus-index is small enough for gcc to inline at -O1 where a hash-plus-probe is not, so the accessor calls the funnel added disappear too. The funnel alone measured +0.2% instructions; the flip repaid that and much more. One hole remains (`ctlPlaceholder`), so `allocType` appends a `holeType` row marked by `typeId 0` -- a real row carries its own index and index 0 is always registered, which is the whole validity test. Only `canonTypeName` (must keep rendering `'?'`, or 18 golden rows flip) and `appendTypes` need it. Verified by SQL-dump diff, same source through both compilers: identical across all 148 examples and the 994,125-row self-compile.

<a id="r-2d8e831"></a>
### monoOriginName call sites take the id form

**2026-07-28 · 2d8e831**

28 of `monoOriginName`'s 33 call sites become `originTidOf … > 0`. The function returns an owned `String` and **every** path allocates -- the miss path builds an empty one -- but 28 sites only ever asked whether the result was non-empty, which is the id form's `> 0` with a byte-identical genericParam guard. **Measured against a same-session `af8506d` binary built with identical flags** (the committed seed is -O0 / no-mimalloc and is NOT comparable): **allocs 9,995,184 -> 9,919,102 (-76,082, -0.76%)**, bytes churned -117,594 (-0.02%), **instructions 6,069.9M -> 6,053.0M (-0.28%**, two interleaved `-r 7` rounds, ±0.07%), cycles -0.38% (inside its own ±0.3% band -- call it flat), **wall and RSS flat**. **THE LESSON, and it is the one that governs the `ZType.name` flip: I predicted 5-6k copies removed by counting only the five `for tid < nextTypeId` scans, and was wrong by 13x** -- 23 of the 28 sites sit in per-expression / per-statement emitter paths (`emitExpr`, `emitStmt`, `emitCallValue`, `emitInstanceMethodCall`), not in registry scans. So this is a clean natural experiment: **a 0.76% allocation cut moved the wall not at all.** That retires the allocation argument for `ZType.name -> nameId` entirely -- that flip is allocation-NEUTRAL on readers by construction (`poolTextCopy` is exactly one `String_copy`, the same as `got.name.copy`), so it can only ever win on the 232 -> 208 B row shrink. Oracle: **emitted C byte-identical** to the `af8506d` seed across all 148 examples and the self-compile -- note a SQL-dump diff would NOT have exercised this at all, the change being emitter-only and downstream of the dumped typecheck model. `emitter-guard`'s `monoOriginName` baseline 37 -> 8. Folded in (`b392b2f`): the dead `optStrSome` and `appendTypesCanon`'s unused `pool` param, both `af8506d` debris that had been failing style-lint's L012/L014 tier.

<a id="r-binding-pool-ids"></a>
### binding-side payloads carry pool ids

**2026-07-29 · (binding pool ids)**

`AssignmentData` / `CaseClauseData` / `WithData` `.name` become `Ast.names` pool ids, so binding-side payloads carry ids like the reference side already did. The extern walk's `externs` and `scope` become `List u32`: a scope membership test is an integer compare, `ewEmit` materialises text only for a name new to BOTH lists (guards reordered inScope -> dup -> `isValidUnitName`, all three early returns so the appended set and its order are unchanged), and `collectExterns` renders ids back to file names at its return -- the load driver stays text, because those names ARE file names. Work-list derived by flipping the three fields and compiling `zc` + `zl` + `zls` with the seed: **40 sites across 7 files, complete in ONE round, no cascade** (`zemitterc` 17, `zparser` 6, `zgenerator` 6, `zast` 5, `ztypecheck` 4, `zsource` 1, `zls` 1). **The typechecker misses exactly one reader shape: `\{n.name}` in an interpolation typechecks against `u32` as happily as against `String`** -- six such sites in `zast.printNode` / `printNodeCanonical` would have silently printed ids; only the `parser_golden/*.ast` differential goldens would have caught them. Everything else is `.stringview` / `.length` / `.copy` / `== <StringView>`, all E0100 on `u32`. **Measured against a same-session `8f494d7` binary built with identical flags: allocs 9,919,102 -> 9,847,155 (-71,947, -0.73%)**, bytes churned -0.3%, **instructions -0.40%**, cycles -0.36% (inside its own ±0.5% band), **cache-misses -3.1%**, wall and RSS flat -- two interleaved `-r 7` rounds. `List_String_contains` **2.31% -> 0.13%** (the residual is `coreNames`/`defNames`/`enqueueUnit`, correctly text) and `StringView_eq` **4.65% -> 3.37%**, but `ewEmit` surfaces at **1.82%** with the now-inlined `List_u32_contains`: **the linear scan, not the comparison, was the cost.** The architecture is the win; the 3.18% hypothesis is retired. Oracle, all three legs: emitted C byte-identical across all 148 examples and the `zc`/`zl`/`zls` self-compiles; `dump --canon` byte-identical incl. the 146,532-row self-compile; and raw `--dump-sql` byte-identical too, incl. the 995,092-row self-compile -- the predicted `call_generic_binding` renumbering (its key embeds a pool id) **did not materialise**: every binding name this corpus interns at its binding was already in the pool. Folded in: `assignmentLocalName` answers with an id (its only consumer immediately `poolFind`-ed the text back into one), and `emitMatchStmt`'s per-clause `cnode9.name.stringview == cn0.stringview` hoists to one `poolFind` outside the loop.

<a id="r-entry-pool-ids"></a>
### ZEntry names become pool ids

**2026-07-29 · (entry pool ids)**

`ZEntry.name` and `ZEntryDumpRow.name` become pool ids, and the 22 `ZSymbolTable` methods that took a `name: StringView` take a `nameId: u32`, so **all 12 backwards frame walks in `zenv.z` are integer compares**. `String`-keyed sets in the same family follow: `pop`'s `localDefs`/`mNames`, `allNames`, `getLiveOwnedVars` and the if-arm `liveBefore`/`takenAcross` are `Set u32` / `List u32`; `lookupVarNameById` returns an id (0 = none) and only `formatLockHolder` renders it. Text survives at exactly three edges, each taking the pool rather than the whole carrier: `defineVar` (which owns the `mangleVarName` chokepoint), the lock diagnostics, and the SQL dump's `entry` rows. Callers that hold text resolve it **once** through a new `nameIdOf` boundary helper -- 62 of the 94 `ztypecheck` sites; the ones that hold an id pass it straight through, which is what deletes `checkAssignment`/`checkWith`'s three `poolTextCopy` locals from the row above. `poolFind` at query sites, `poolSet` at the 12 definition sites (a declared name must be findable after), and every scan early-returns on id 0 -- so an uninterned name cannot alias a 0-named entry. Work-list by compiling `zc` + `zl` + `zls`: 34 sites, then 130, then 96, then 7 as each signature wave landed -- four rounds, unlike 2a's one, because this flip changes an API rather than a leaf field. `\{e0.name}` in `ztypes.z`'s smoke is the same silent-interpolation shape 2a hit (now prints `nameId=`; golden updated in this commit). **Measured against a same-session `1e7f0c4` binary, identical flags, two interleaved `-r 7` rounds: allocs 9,847,155 -> 9,669,658 (-177,497, -1.80%)**, bytes churned -2.5% (472 -> 460MB), **cycles -1.18%**, instructions -0.29%, cache-misses flat, **wall and RSS flat** (phase best-of-5 96/212/178 -> 93/212/175). `StringView_eq` **3.28% -> 2.08%**, `List_ZEntry_get` **1.04% -> 0.53%**, and `StringPool_probeSlot` **1.68% -> 1.28%** -- the boundary probes cost less than the text round-trips they replaced. Same lesson as 2a: the frame walk is still O(depth), so the win is the allocation and the compare, not the scan. Oracle, all three legs byte-identical: emitted C over all 148 examples and the `zc`/`zl`/`zls` self-compiles, `dump --canon` incl. the 146,949-row self-compile, raw `--dump-sql` incl. the 997,988-row self-compile. Note the dump's `entry` section had to stay LAST: `appendSymbolTable` needs the pool, which is only reachable inside `dumpSql`'s `case program` guard, so it gets its own guard at the original call position rather than moving up into the existing one.

<a id="r-demand-marks-on-the-decl"></a>
### demand marks move onto the Decl

**2026-07-29 · (demand marks on the Decl)**

The demand resolver's done/grey markers stop being `Set String` probed with a freshly interpolated `"{unit}.{name}"`: `ZTyping.definedKeys` and `resolving` are **deleted**, and the state lives on the `Decl` the walk already reaches (`Decl.defined` / `Decl.resolving`, two bools that land in existing padding). A probe is now `unitRootByName` -> `declFindChild` -> read a bit, via `declOfUnitDef`, which already existed. **11 of the 87 composite string keys are gone (87 -> 76), and two `Set String` tables with them.** Callers that hold the definition's name id (`bmNode9.name`, three of the four done-mark probes) skip the pool entirely through a new `declOfUnitDefId`. **The pre-index window is real and the plan called it**: generator lowering resolves `system.Iterator` / `system.optionval` before `indexUnitDefs` builds the unit roots' children, so those two definitions have no `Decl` to carry the mark -- losing it re-resolved them and minted one extra type id, which showed up as a uniform +1 type-id shift in the emitted C of exactly the 7 `generator_*` examples. Bridged by `ZTyping.preIndexDefined`, a `Set u64` keyed by `(unitNameId << 32) | defNameId` -- an id pair, never a synthesized name -- consulted only when the Decl lookup misses. Verified that `indexDeclDef` never finds an existing child (so nothing duplicates today) before choosing the bridge over minting the Decl early. **Measured against a same-session `812f446` binary, identical flags, two interleaved `-r 7` rounds: allocs 9,669,658 -> 9,663,488 (-6,170), bytes churned -907KB, instructions +0.03%, cycles +0.12%, wall and RSS flat.** A small allocation win and dead-flat CPU -- the point of the commit is that a two-segment name lookup walks the tree instead of building a key. Oracle, all three legs byte-identical after the bridge: emitted C over all 148 examples and the `zc`/`zl`/`zls` self-compiles, `dump --canon` incl. the 147,067-row self-compile, raw `--dump-sql` incl. the 998,960-row self-compile.

<a id="r-unit-indexes-by-id"></a>
### unit indexes keyed by name id

**2026-07-29 · (unit indexes by id)**

`reg.unitNameTid` and `ty.unitRootByName` are keyed by the unit's interned name id, not its text; `setUnitName` / `isUnitName` take an id, so `ztypes` never sees a name. The twelve `isUnitName` sites read the id **straight off the atom node** through new `dottedBaseAtomId` / `outerBaseAtomId` / `atomNameIdOf` siblings, deleting a `dottedBaseAtom`/`atomName` text materialisation at each (the L013 lint found the dead locals). `ty.systemUnitRoot` caches the system unit's root Decl so the per-reference member probe stops re-hashing `"system"`, via a `declProbeUnitChildRoot` split. ~20 `key: X.string` materialisations at probe sites are gone. **This row is a TRADE, and it is recorded because the first attempt at it was rejected on the wrong axis.** Measured against a same-session `f2e874d` binary, identical flags, valgrind on identical input: **allocations 9,691,456 -> 9,517,956 (-173,500, -1.79%)**, bytes churned -1.25MB, **RSS and wall flat**. But **instructions +3.09% and cycles +1.38%** (two interleaved `-r 7` rounds; the cycles figure is outside its own ±0.4% band, so it is real). The added cost is a `poolFind` at the ~40 probes whose callers still hold a `unitName: StringView` threaded down through **133 functions / 322 call sites** -- those are the sites the atom-id sourcing cannot reach, and they are the remaining `strings-in-the-middle` debt. **The lesson: judging an id flip on instructions alone is the wrong test.** The first pass measured +3.4% instructions, concluded NO-GO by analogy with the `2c` assoc-list rejection, and reverted -- without ever running the allocation line. The allocation win here is the same magnitude as the `ZEntry` flip's (-1.80%). `2c` was genuinely negative on both axes; this is not. Oracle, all three legs byte-identical: emitted C over all 148 examples and the `zc`/`zl`/`zls` self-compiles, `dump --canon` incl. the 147,456-row self-compile, raw `--dump-sql`.

<a id="r-dotted-type-names"></a>
### a type's name is its own name

**2026-07-29 · (dotted type names)**

A type's `name` is its OWN name (`create`, `==`, `describe`), never `owner.member`. `ZType` gains `ownerTid`, and `composeCname` builds the C symbol from the two parts, so **every emitted symbol is unchanged**. 24 composite interpolations at `reg.newType` mint sites are deleted (`"{defName}.create"` -> `name: "create" ownerTid: rid`). **Types carrying a dotted name: 1,893 -> 1,036**; composite string keys 76 -> 73. **The probe that shaped this**: of the 1,893, **950 had no Decl** (`defOf == 0`) -- synthesized members (`.==`, `.orPanic`) on mono owners (`List_u8`) -- so the owner is NOT recoverable from the Decl tree and has to live on the row as an id. Also found: the emitter kept a **second, independent cname builder** (`zemitterc.cnameOf`) that recomposed from a caller-supplied name; 44 of its sites now read the stored cname and the one site that deliberately pairs a BASE tid with a qualified member name takes the two parts explicitly. **Measured against a same-session `08aa47c` binary, identical flags: allocations 9,515,844 -> 9,471,881 (-43,963, -0.46%)**, bytes churned -1.33MB, **instructions -0.52%, cycles -0.80%**, wall and RSS flat -- every axis in the right direction, unlike the row above. Oracle: **emitted C byte-identical** across all 148 examples and the `zc`/`zl`/`zls` self-compiles; `dump --canon` and raw `--dump-sql` change by design (a member type's name row) and the 19 `.canon` goldens are re-baselined in this commit -- note the canon row already carried the owner in its first column (`'kind' | '!=' | 'kind.!='`), so the composite was redundant there too. **The consumer census that justified it**: the dotted name had exactly one reader, `cnameOf`, whose first act was to replace every `.` with `_`. Nothing looked a type up by it -- no `nameIs` against a composite, no dotted literal to `resolveTypeIdByName` -- because `declareCanonicalType` resolves through `declChildOf` on the Decl tree. The tid was always the identity; the name was decoration.

<a id="r-family-c-no-composite-keys"></a>
### family C: no composite keys

**2026-07-29 · (family C: no composite keys)**

`declareCanonicalType` and `declOfFnDef` stop taking a qualified `Type.method` name apart: the first had an `indexOf`/`substring` splitter, the second a byte-scan for `.` (byte 46), and **ten call sites interpolated a composite purely so those two could split it back**. The owner now arrives as `ownerNameId: u32` (an interned pool id, 0 = free definition) and the member as its own bare name; both splitters are deleted, and `resolveMethodFn`/`resolveSpecFn` take `(ownerNameId, mname)` instead of `qname`. Every emitted symbol is unchanged **by construction**: `composeCname` is `z_t{tid}_{owner}_{name}` with `.` flattened to `_`, so the 8 dotted sites (`"{defName}.{mname}"` -> `name: mname ownerTid: rid`) and the 2 underscore-separated mangled-mono sites (`"{tmplName}_{fn}"`) both compose to the identical C identifier. **THE WIP WAS INCOMPLETE AND THIS IS THE LESSON**: `resolveFunction` guards Map.get/remove out of the partial-return rewrite with `if defName == "Map.get"`, so that they keep their value-kind wrapper projection (optionval / Option / OptionView by the value's nature). `defName` is now bare `get`, the compare silently stopped matching, `Map.get` on a valtype-valued map resolved to `Option i64`, and `Option`'s `Any.reftype` bound rejected it -- **E0400 on 7 examples and all three self-compiles**. A string compare against a composite literal typechecks *identically* before and after, so neither the typechecker nor the already-run "no composite reaches `declareCanonicalType`" probe could see it: the composite was consumed by a COMPARISON, not by the callee. Fixed by testing the owner explicitly (`poolTextEq ref: ownerNameId s: "Map"` + bare `get`/`remove`) -- the bare name alone over-matches, since List / ListView / ListIter all declare `get`. Found by diffing a name-keyed `getOrMintSpec` constraint trace between a same-session reference binary and this one; the trace showed four extra `tpl=Option pn=t arg=i64` checks appearing immediately after `Map key=String value=i64`. **Oracle -- emitted C is NO LONGER byte-identical and that substitution was a deliberate decision**: `Option` mints during `Map.get`'s signature walk instead of in a later batch, so type ids renumber. Precedent is the `(ctl placeholder)` row. Required leg: same type-symbol count and an identical id-stripped cname multiset (`LC_ALL=C sort` -- an unsorted diff reports a phantom set difference) across all 148 examples plus the `zc`/`zl`/`zls` self-compiles, same source through both compilers -- **151/151**. It then passed two strictly stronger legs that were worth running because no bare type id is emitted (the tid reaches the output only through `composeCname`; `runtimeIndexed` is a bool, not an index): the **whole file** with `z_t[0-9]+_` collapsed is byte-identical on all 151, and the ref->new id map is a proven **BIJECTION** (`zc`: 97,242 occurrences, 1,969 distinct ids, 0 violations; `zl` 1,521; `zls` 1,747). So this is pure uniform renumbering as a fact, not as an inference from a matching symbol set. `dump --canon` moves by design (a member type renders as `'add'`, not `'point.add'`) and 7 of the 19 goldens are re-baselined here -- **1,314 rows on both sides**, which is the cheap proof the Decl tree did not change shape after `linkDeclType` was gated to free definitions (it was a no-op for members anyway: it looks the name up as a UNIT child, and no unit has a child called `Type.method`). Dotted names in those goldens **52 -> 13**; the 13 survivors are `.create` synthesis and generic-unit mono members, unchanged from HEAD and the next slice of this arc. Composite `\{a}.\{b}` interpolations in `src/*.z` 79 -> 71. One user-facing diagnostic degrades and it is **accepted, not overlooked**: a bodyless-spec fn-pointer field now reports `'op' (type: op)` where it reported `(type: Proc.op)` -- the sole site where `(type: ...)` renders a synthesized member type rather than a real one. Restoring the qualification would give `ZType.ownerTid` its FIRST reader, and that field is slated to come off the row; the recovery path is `declIdOfType -> Decl.parent` (measured 857/857 at `1668a54`) once cname generation moves into the emitter. Measured against a same-session `1668a54` binary, identical flags, three interleaved `-r 7` rounds: **instructions -0.18%** (6,232.0 -> 6,220.7M, band +-0.07%, new below ref in every round), cycles and cache-misses **flat** (their between-round spread, 1.1-1.5%, swamps the delta), **allocs 9,471,744 -> 9,467,594 (-4,150)**, bytes churned -162KB, wall flat, **peak RSS 107.7 -> 106.2MB** on non-overlapping five-run ranges -- residency, from shorter `ZType.name` strings. A refactor row: the architecture is the point.

<a id="r-family-c-tail-d557d0f"></a>
### family C tail — three closing commits

**2026-07-29 · (family C tail: `d557d0f`..)**

Three commits closing out family C, each with its own oracle. **(1) The last three `reg.newType` sites that passed a composite name.** `fb56e92` converted ~24 mint sites to `name: <bare> ownerTid: <owner>` and missed the class `create` synthesis -- while converting the record, facet and protocol equivalents AND the `borrow` sibling twelve lines below it -- plus the generic-unit mono method shells in `attachMonoUnitFuncs` and `buildUnitMono`. Each already had its owner tid in scope, so **emitted C is BYTE-IDENTICAL across all 151** (148 examples + `zc`/`zl`/`zls`), not merely renumbered. Dotted names in the 19 `.canon` goldens **13 -> 0** at an unchanged **1,314 rows**; the owner was always already the row's first column, so the third was carrying it twice. `attachMonoUnitFuncs`' `monoName` falls out unused (L014) and goes with its call-site argument -- the redundant (id, text) pair collapsing to the id is the arc's thesis. `buildUnitMono`'s `qn` does NOT: it has a second consumer 170 lines away as `PendingUnitWalk.qualified`, which the lazy body walk passes as `defName`. That is a label, not a type name; it keeps its composite and a comment saying why. **(2) `ZType.ownerTid` deleted -- it was write-only.** `fb56e92` added it so `composeCname` could build `z_t{tid}_{owner}_{name}` from parts, but `composeCname` runs inside `newType` and reads the PARAMETER; the stored copy was **the only ZType field with zero `.ownerTid` reads in `src/*.z`**, checked field by field against the other twenty. So the checkpoint's "comes off the row when cname generation moves to the emitter" understated it -- nothing had to move first, and the emitter can reach the owner through `declIdOfType -> Decl.parent` (857/857 since `1668a54`). Row **232 -> 224B** across the ~35 `for tid < nextTypeId` scans; emitted C byte-identical, as it must be for a field nothing read. **(3) The `Map.get` skip is keyed on the declaration, not on a list of method names.** `resolveFunction` guards Map's get/remove out of the partial-return rewrite so they keep their value-kind wrapper projection, and `e22bc1a` spelled that guard as two method-name compares -- the shape that had just silently stopped matching and cost 7 examples and 3 self-compiles. **The root cause is that `partialReturnTid` is not a query**: it resolves through `resolveMonoRefParts` -> `getOrMintSpec`, so on `Map.get`'s declared `(Option of: value)` it REGISTERS Option over the value parameter, which respecializes to `Option i64` and trips `Option`'s `Any.reftype` bound. It cannot simply be deleted -- the same mint is WANTED for `(ListIter of: of).borrow`, which resolves to a partial spec so clones heal through respecialize. What actually differs is that get/getv/remove declare a **placeholder**: their real return is optionval for a valtype and Option/OptionView otherwise, which cannot be spelled in a return-type position (`match`-based generic dissection reaches bodies, not return types). So the guard now reads the declared return's base atom id and compares it against `Option`'s interned id -- one allocation-free compare via a new `returnWrapperBaseNameId` that resolves and mints nothing. **The tell that the old shape was wrong: `getv` declares the identical return and was NOT in the skip list**, being patched up downstream instead (one declaration shape, two mechanisms, selected by whether the method's OTHER params happen to be generic). Keying on the declaration covers it, and **removes exactly one wasted partial-spec mint per Map-using program** (highest tid 1001 -> 1000 on `maps`, same distinct type count) for an identical program. Oracle: emitted C differs on all 151 by **id renumbering only** -- verified by normalising every id form and re-diffing the whole file, 151/151. **That check needed three widenings to be true, and each was a blind spot in the previous one**: the leading-anchor `z_t[0-9]+_` strip missed tids embedded MID-symbol (`String_to_t973_str_4`), which missed the UPPERCASE macro form (`Z_T953_MAP_..._INDEX_EMPTY`), which missed the interned-temp counter (`_i868`). A partial normaliser reports phantom content changes -- 3, then 16, then 14, then 1, then 0 -- so **widen the normaliser before concluding a diff is semantic.** **Measured against a same-session `e22bc1a` binary, identical flags, two interleaved `-r 7` rounds: everything FLAT.** instructions 6,230.3 -> 6,222.9M (-0.12%, inside the reference's own 0.19% spread), cycles -0.33% and cache-misses -2.4% (both inside ~1-4% spreads), wall and RSS flat, bytes churned -3.7KB. Allocations **9,467,747 -> 9,472,314 (+4,567, +0.05%)**, which is the self-compile input growing: LOC 80,476 -> 80,505 (+29) for the new helper and its comments. **Commit 2's 232 -> 224B row shrink is the one I predicted would move something and it did not** -- same shape as the `2d8e831` lesson, recorded rather than quietly dropped. This is an architecture slice; the point is that no type in the model carries a qualified name any more.

<a id="r-affd7f4"></a>
### OWNERSHIP v2 arc (44 commits)

**2026-08-02 · affd7f4**

**OWNERSHIP v2 ARC, 44 commits `3d8e239`..`affd7f4`** -- the mutability axis (`take`/`hold`/`borrow`/`view`), `takex`, `.lock` and the `lockMode` arm RETIRED, readonly receivers/params/fields/locals, deep readonly, the readonly iterator (`iterate`/`iterateMut`) and `getMut`, ~2400 `.view` annotations, L4 and L020. **Measured against a same-session `4532d68` binary (the arc's parent) built with identical flags** -- NOT against the last recorded row, which predates the arc. **typecheck 218 -> 236ms (+8.3%) is the real cost and it is CONSISTENT** (5 runs each, ranges 217-222 vs 232-240, non-overlapping); parse and emit are FLAT (their ranges overlap -- a single sample had shown parse +30% and resampling killed it). **Peak RSS IMPROVES 6%.** The regression that wants attention is **bytes churned +18% on only +1.7% allocs** -- fewer, bigger allocations, not more of them. Wall +4% follows typecheck. LOC +3.3%. `make test` 15.3s vs 13.3s on 923 vs ~900 tests, i.e. flat per test. No perf work was attempted during the arc: this row is the honest accounting of what correctness cost, not a tuning result.

<a id="r-34647e9e"></a>
### VAL/REF SPLIT arc (49 commits)

**2026-08-03 · 34647e9e**

**VAL/REF SPLIT ARC, 49 commits `946c1303`..`34647e9e`** -- family containers (ListRef/ListVal, SetRef/SetVal, MapRR/RV/VR/VV + the iterator/view/entry satellites), AnyRef/AnyVal/RefHashable/ValHashable bounds, union `(Box T)` arms with collapse-at-instantiation, reftype-only `Result`, `List`/`Set`/`Map` as core.z aliases. **Measured against a same-session `946c1303` worktree binary (the arc's parent), identical flags.** Wall +9.8% (0.51 -> 0.56 best-of-5) and every phase's 5-run ranges are DISJOINT: parse 87-104 -> 122-142ms, typecheck 235-241 -> 247-255, emit 184-185 -> 190-223. Allocs +2.0% and bytes +1.6% on LOC +1.8% -- allocation-flat per line; RSS ~+2%. `make test` 15.9s on 948 tests vs 15.3s on 923, flat per test. **ROOT CAUSE FOUND, fixed in the next row: the split's valtype containers classified builtin numerics as by-value STRUCTS** (`keyHashKind` returns 3 for every recordType and the numerics ARE records), so a scalar-element `contains`/`sort_lt` emitted a libc `memcmp` CALL per element where the pre-split `List` emitted `==`. Parse pays most because `collectExterns` does a per-node linear `contains` over two `(ListVal u32)` lists -- `__memcmp_evex_movbe` was the self-compile's hottest symbol (4.5%).

<a id="r-scalar-keyhashkind"></a>
### keyHashKind asks scalarCTypeFor first

**2026-08-03 · (scalar keyHashKind)**

**`keyHashKind` asks `scalarCTypeFor` before classifying a record/variant as a by-value struct.** A builtin numeric/bool/char whose C type is a scalar classifies 0 with the pointers (C `==`/`<`, cast hash); a USER type shadowing a scalar name still lands 3 -- `scalarCTypeFor` is the shadow-safe gate (`shadow_set_elem.z` pins it). One predicate fixes four emissions: ListVal `contains` and `sort_lt` lose the per-element `memcmp` call (the arc's regression), and scalar Set elements / Map keys upgrade from `fasthash_bytes`/`siphash_bytes` + `memcmp` to the cast hash + `==` -- that half PRE-DATES the split (the base's `Map u64` keys byte-hashed too), an older inefficiency the regression hunt surfaced. Safe because numerics declare `==` but not `hash`, so the declared-key dispatch (which needs BOTH) stays off, and Set/Map iteration is insertion-ordered so bucket order moves no golden. **Wall 0.56 -> 0.51 best-of-5 (parity with the pre-arc base), typecheck median 251 -> 240 (base 239), parse median 138 -> 99 (base 88; best runs 86 vs 87)**. Allocs/bytes unmoved -- the fix changes comparisons, not allocations. 948/948 tests green with ZERO golden churn; `typeNameOfReg9` 90 -> 91 (the one new name lookup). **OPEN LEAD kept: `collectExterns` is O(nodes x externs) by construction** -- cheap per probe now but still quadratic-shaped; a hash-set scope is the lever if parse ever needs more.

<a id="r-releaseheldlocks-skip"></a>
### releaseHeldLocks scope-exit skip

**2026-08-03 · (releaseHeldLocks skip)**

**The bytes-churned lead from the ownership row, hunted with DHAT (4532d68 vs HEAD, both sides instrumented): the +18% was the LOCK MACHINERY's scope-exit rebuild.** `zenv.releaseHeldLocks` drained the ENTIRE entry table into a zero-capacity scratch list and refilled it on every release -- ~21MB/7.5k blocks of pure churn per self-compile, the top identifiable new site. Now it scans first and returns when the holder holds nothing (the common case -- the skip alone removed most of it), and sizes the scratch once when it does rebuild. **bytes 557 -> 522MB (-6.3%), allocs -11.4k, wall/phases flat** (the drain was alloc-bound, not CPU-bound). **The REMAINING ownership-arc residual vs the 465MB pre-arc floor is ~+48MB (+10%), located and RECORDED, not chased:** (1) `pushScope` copies the scope-label String per push (`z_t75_from_view` in the push path -- name ids at the edges would remove it, an architecture change); (2) the per-scope param-stamp history list doubles to ~5.6MB (structural: the readonly model records more); (3) the ZEntry/ZScope tables' rows grew with the mutability axis (same block counts, more bytes). Each is proportional model cost, none is a leak.

<a id="r-typedef-ids-machinery-fsno"></a>
### typedef ids: machinery and the fsno space

**2026-08-04 · (typedef ids: machinery + fsno)**

**The id-typedef arc's first slice: the typedef machinery made correct, and the fsno space migrated end to end** (fsno: record { val: u32.typedef } with a shadowed same-space ==; Token/ErrorData/Diagnostic/zsrcpos/zls all carry it). Seven machinery gaps fixed along the way (hashability chase, ==/!= record-eq legs, numeric-cast source chase + pointer deref, keyHashKind chase, structural-eq field chase staged through its own seed bump, the shadow-safe scalar gate the guard itself caught). **The zero-overhead claim, measured (1cf8bd04 vs HEAD, -r 7): cycles +0.13% (inside the 0.42% spread -- FLAT), wall/phase ranges overlap, allocs +0.18% / bytes +0.14% (input growth); instructions +0.67% -- real but pipeline-hidden, the typedefChase map probes that now run per key classification.** The emitted C carries ZERO trace of the typedef: no z_fsno struct exists, u32-keyed map hashes still take plain uint32_t, comparisons inline. Remaining spaces (declid, vid, tid, nameid, nodeid) follow this recipe.

<a id="r-b5ef6194"></a>
### the id-typedef arc (19 commits)

**2026-08-05 · b5ef6194**

**The id-typedef arc, `ff7d9f31`..`b5ef6194` (19 commits): the `tid` space (type-registry id -- ztypes, ztypecheck, zemitterc, the 11 shared side tables) and the `nameid` space (StringPool id, PUBLIC zast surface), plus five pre-existing compiler bugs the migration exposed** -- the literal pseudo-ids became real registry rows, a numeric type now converts to ITSELF, a tagged arm's payload is checked against its type, and the return check that was OFF for 77% of bodies (`5ea85340`: `rkey` doubled the unit prefix, so 871 of 1136 dependency-unit bodies were never return-checked). No bare `u64` type-id parameter remains in `src/` or `lib/system/`. **The matching pool-id claim was overstated and is corrected here:** `nameRef`, `ownerNameId` and `baseId` stayed bare `u32`, and the whole `nameId: u64` ring of the Decl-tree API (`declChildOfId`, `declSetMemberType`, `setMemberType`, `constKeyIn`, ...) stayed bare `u64` -- each of them silently accepting a `nameid` by widening, which is the hole the space exists to close. All of them are closed in the N3.0 commit below. **Zero-overhead re-measured on IDENTICAL INPUT -- all three binaries compiling HEAD's `src`, `perf stat -r 7` x 4 rounds: the typedef flips alone = instructions +0.82%, cycles +0.31%; the five bug fixes = instructions -0.52%, cycles +0.57%; net vs `ff7d9f31` = instructions +0.30%, cycles +0.88%.** Emitted C still carries ZERO trace of any of the five spaces: `grep -cE 'z_t[0-9]+_(fsno|vid|declid|tid|nameid)_t' bootstrap/zc.c` = 0. Self-compile against `ff7d9f31` re-measured on the same machine the same hour (0.53-0.58s, 108MB, 9,943,747 allocs, 525,424,830 bytes, LOC 85,805): **allocs +1.9% and bytes +2.2% against +2.6% LOC -- sub-linear with input growth, so no per-operation cost.** Wall band across two `make perf` invocations: 0.56-0.58s.

<a id="r-8f3678fc"></a>
### N3-c/e: compound-key resolution by ids

**2026-08-06 · 8f3678fc**

**N3-c/e: compound-key resolution goes by ids end-to-end** -- `resolvedByKey` / `childOfWalk` / `reexportTargetOf` / `dottedDefTarget` DELETED: every caller resolves a (unit, name[, member]) pool-id tuple via `resolvedByIds`/`resolvedByIdsMember`; `checkFunctionBody` resolves its key ONCE (three later reads reuse it) and strips a dependency defName's duplicate unit prefix with view arithmetic, so the `upfx9` composition (N3-e) is gone; `unitSeededDemand` is id-keyed via `memberKey` with unit 0 the `"*"` wildcard. **Three pre-existing bugs fixed en route:** a `:name` shorthand argument at a `.take` parameter never invalidated its source -- a silent use-after-free class (`3993e561`, fixture `shorthand_take_use_after_move`; the lock half of that gap is measured at 1,848 sites and DEFERRED, see typechecker.pdoc); the closure-wide unknown-type fallback composed digit-string keys that could never resolve (`8b7a2e2d`); the cross-unit `unit.Type.create args` explicit create was emitted as an undeclared C call -- its emitter leg was structurally dead (`ce260099`, fixture `cross_unit_explicit_create`). Allocs 9,597,793 -> 9,338,190 (**-259,603, -2.7%**), bytes 532,249,225 -> 530,055,751. The clang-vs-gcc allocation delta stays ~751k -- id-keying removed live allocations, not dead copies; the L022 pool is untouched.

<a id="r-5c59c47a"></a>
### L022 closed, N4 landed, four defects fixed

**2026-08-06 · 5c59c47a**

**L022 closed, N4 (`nodeid`) landed, four compiler defects fixed -- `a0658923`..`5c59c47a` (8 commits).** The arc opened with a parked 940-line migration batch whose error fixtures looped forever at -O1 and double-freed at -O0. **Root cause was ONE EMITTER HOLE, not the batch**: `q: p` over a reftype is a MOVE (the typechecker has always said so, and the REASSIGNMENT leg always emitted `free(dst); dst = src; src = (T){0}`), but the BINDING leg emitted the struct copy and never zeroed the source -- both locals then freed the same pointer. Invisible until then because no bare-bind move of a destructor-carrying type existed in the tree (948 zeroing lines in `bin/zc.c` before AND after the fix), and the L022 migration's natural fix for a flagged local is exactly that shape. With it fixed the parked patch applied UNMODIFIED and all 229 error fixtures passed. **L022 worklist 111 -> ZERO**: the tail migrated, three params/receivers went honest (`tokByteStart`, `ZLS.innerRangeOfKind`, the probe-key locals whose `get`/`remove` already take `key.view`), and the lint learned the two shapes a view cannot serve -- a copy of a FIELD read (the save/restore idiom around a setter that REASSIGNS, so the alias dangles; zls lost `_create` declarations from a freed unit name) and a copy whose SOURCE ROOT is written in the same body. **N4**: `nodeid` (u32 typedef) now covers the 29 Node child-edge fields, ~250 id-carrying parameters under 40 labels, and ztyping's three u64 holders; containers keep their base type and mint at the boundary. **Four defects fixed with fixtures**: the binding-leg move (above), a `.take` of a String payload emitting an undeclared `<cname>_destroy`, a binop operand that accepted an unresolvable name silently (the last leg of that family), and a unit constant's literal suffix seeding no demand for its type (open bug #3). Allocs 9,037,598 -> 8,999,867 (**-37,731 net, -0.4%**) -- the L022 tail paid -66,849 and the typedef/diagnostic work put ~29k back as source growth (LOC 89,020 -> 89,382).

<a id="r-347a3bad"></a>
### the C clean-up pool goes to zero

**2026-08-07 · 347a3bad**

**The C compiler's clean-up pool goes to zero -- `fa1c51fe`..`347a3bad` (6 commits).** `Lexer.peek` handed back an owned `Token`, copying the token text 1,128,811 times per self-compile; **756,750 of those copies were never read**, which was the ENTIRE gcc-vs-clang allocation gap, at one call site (see the Toolchain findings section -- the mechanism recorded there was wrong, and it was never a tree-wide pool). 57 of the parser's 64 peek sites wanted only a token type or a source position. The Lexer now answers those without copying -- `peekType`, `peekPos` (a `tokpos` valtype: fsno, line, column, and the text's byte WIDTH, which is all `mkError` ever read from the text), `peekText` (a view) -- and `peek`/`currentCopy` are DELETED: no reader copies the buffered token at all. `mkErrorAt` builds an error node from a span, the fold family and the three definition parsers carry a lexer or a span instead of a Token, and the string-chunk loop moves its text out via `acceptAny` instead of copying then swapping. **The split is by LIFETIME**: a `tokpos` copies out, so seven rules still name their first token in a diagnostic raised long after the lexer advanced -- a view cannot serve there, which is why one view-shaped token would not have worked. Allocs 9,082,959 (measured at `c5099056`, the arc's start) -> 7,959,463 (**-1,123,496, -12.4%**), bytes 529MB -> 524MB, `String_copy` blocks 3,471,363 -> 2,347,100. **`make perf-elision` (new) reports a write-only pool of ZERO** -- a gcc build and a clang build of `bin/zc.c` now make the same number of allocations. **Speed, measured the only way that means anything here -- BOTH binaries on the SAME input** (this row's tree, `perf stat -r 7`): instructions 6,667,405,704 -> 6,498,566,959 (**-2.53%**, +-0.04%/+-0.01%), cycles -2.04%, task-clock 562.65 -> 551.30ms (-2.02%). The wall COLUMN reads 0.53s -> 0.55s across these two rows and that is **not** a regression: every row times the compiler on its own current source, and the source grew (89,382 -> 89,808, only part of it this arc). Peak RSS likewise -- on identical input both binaries bottom out at ~114MB. **Do not read the wall/RSS columns as an A/B when LOC moved.**

<a id="r-29dd84f8"></a>
### three arcs: tcc backend and two unrowed

**2026-08-10 · 29dd84f8**

**Three arcs, 93 commits `347a3bad`..`29dd84f8`, measured together because the middle two were never rowed: the tcc external backend (`2d48cdc7`..`30ebceb1`), compile-time values (`b112e3d6`..`9e67f615`), and C12 static string ops (`01ec23aa`..`29dd84f8`).** Span vs the 2026-08-07 row on LOC +3.6%: wall FLAT, phases FLAT (542 -> 561 total, parse 95 -> 91, typecheck 256 -> 256, emit 191 -> 201), peak RSS 122 -> 116MB, `make test` 16.7 -> 14.0s on MORE cases (1011 vs ~1006). **allocs +4.8% (7,959,463 -> 8,341,026) against LOC +3.6%, and bytes DOWN 8.9% (524 -> 477MB)** -- the span is allocation-flat-to-better per line, but the two middle arcs were not separately measured so none of it is attributable. **The only rigorous A/B here is C12's, both binaries on IDENTICAL input (HEAD's src), `perf stat -r 7` vs a `9e67f615` worktree binary at the same flags: instructions 6,546,954,470 -> 6,533,736,764 (-0.20%), cycles -0.33%, task-clock 574.2 -> 567.2ms, allocs +15,168 (+0.18%), bytes flat (-27,864).** So C12 is performance-neutral. **It did not start that way, and the miss is the lesson: the bare-atom value check pulled the constant evaluator for EVERY bare atom that resolved to a type**, to catch the one shape where a constant is mistaken for a type alias -- instructions +0.69%, outside the +-0.25% spread, while cycles and wall stayed flat and hid it. Moving the pull BEHIND the alias test (same semantics -- the constant still overrules it) recovered the whole 0.89%. **Allocations did not move either way**: the pull returned early with no allocation for a name that is not a constant, so this was pure CPU in `constUnitTid`/`poolFind`/`constDefNodeId` and the allocation column could never have caught it. Instructions did. `make perf-elision` still reports a **write-only pool of ZERO** -- worth checking here specifically, because C12 added a second literal pool (`_zcs`) and a new arg-hoist path for non-lvalue String operands of `+`.

<a id="r-e9cbb09f"></a>
### two arcs: the native table and one unrowed

**2026-08-12 · e9cbb09f**

**Two arcs, 76 commits `29dd84f8`..`ac6f307f`, measured together because the first was never rowed: the native table (`20b7b7d8`..`cca9d6b4`, 65 commits) and the generic runtime pass (`cca9d6b4`..`ac6f307f`, 11 commits).** Span vs the 2026-08-10 row on LOC +3.5%: **allocs +3.5% (8,341,026 -> 8,635,934) -- exactly proportional to input, so flat per line**; bytes 477 -> 510MB (+6.9%, the one column outpacing LOC and NOT attributed, since the native-table arc was not separately measured); wall 0.55 -> 0.56s, phases 561 -> 580 total, `make test` 14.0 -> 13.9s on more cases. **The rigorous A/B is the runtime-pass arc's, both binaries on IDENTICAL input (HEAD's `src`, `--emit-c /dev/null`), `perf stat -r 7`: instructions 6,667,576,785 -> 6,672,266,203 (+0.07%, spread +-0.02%), cycles -0.09% (+-0.45%), task-clock 595.9 -> 588.5ms (+-1.4%) -- CPU FLAT, the instruction rise real but pipeline-hidden. Allocs +11,510 (+0.13%) and bytes -12,786 (FLAT).** The pass trades seven hand-written emitters for one walk over 122 table rows per unit, so the block count carries a small structural cost while the bytes do not -- it copies no more, it just asks more often. **One avoidable site was found and fixed in the same measurement (`ac6f307f`): `stmtFormName9` copied the callee's pooled name to compare it against four literals, for every dotted call statement in the program -- -2,709 allocs on identical input.** `make perf-elision` read a write-only pool of **122, not zero** when this span was first measured; chased and CLOSED at `e9cbb09f` -- one write-only copy per fragment row in `loadNativeTbl`, plus a second copy the pool could not see (-225 allocs, -7,787 bytes, both counted in this row's columns). See the section below.

<a id="r-c335e552"></a>
### five arcs plus three fixes (81 commits)

**2026-08-15 · c335e552**

**Five arcs plus this measurement's own three fixes, 81 commits `e9cbb09f`..`c335e552`**: demand root + outside-in (`16cbd62e`..`0c9b47c5`), native base types (`04f8a1ff`..`522aea55`), truthiness is a shape (`4dae744b`..`100db88a`), stdlib is not special (`100db88a`..`e82f9a4f`), unified environment (`e82f9a4f`..`e56e4bf1`), then `9671b2c8`..`c335e552`. **Bytes churned had MORE THAN DOUBLED across the span -- 510 -> 1,129MB -- and no other column showed it.** Allocations were flat (8,635,709 -> 8,675,188, +0.5% against LOC +1.15%), wall and phases improved, `make test` and the corpus were green: the same block counts moved ~9x the bytes, and 98% of the excess was two functions. Cause, instrument and fix in the section below; **this row's columns are POST-fix**, and bytes now read 478MB, BELOW the 510MB this span started from. **Per-arc A/B, every binary on IDENTICAL input (HEAD's `src`, `--emit-c /dev/null`), gcc -O1 series binaries, `perf stat -r 7`** -- instruction spread +-0.01..0.03%, and two rounds of ONE binary differ by 0.007%, so a tenth of a percent is signal here: truthiness **+0.13%** instructions, native base types + stdlib **+0.17%**, **unified environment -1.52% (7,054,595,077 -> 6,947,673,171) -- deleting the demand set paid in CPU as well as in architecture**, the three fixes **-7.09% (-> 6,455,199,486)**. Span total **-8.22% instructions, -8.93% cycles, -7.69% task-clock, -57.8% bytes, -0.82% allocations**. `make perf-elision` reports a write-only pool of **ZERO**. **The mimalloc RSS column moved by ENVIRONMENT, not by code**: `e9cbb09f`'s own binary, rebuilt and re-measured here, reads **139MB** against the **119MB** recorded for it on 2026-08-12, while its glibc RSS re-measures at exactly the recorded 114MB. mimalloc retains ~20MB more on this machine now; HEAD's 138MB is 1MB BETTER than the previous row's commit measured beside it today. Read the mi column against 139, not against 119.

<a id="r-500eb312"></a>
### the composite-key sweep (16 commits)

**2026-08-16 · 500eb312**

**The composite-key sweep, 16 commits `c335e552`..`500eb312`: every `(a << 32) | b` key in the compiler is gone** (written `* 4294967296`, since the language has no shift operator). Seven families: `preIndexDefined` DELETED as dead, `genericParamTid` and `protocolThisContext` moved onto the rows they describe (`ParamDesc.paramTid`, `Decl.conformerName`), and `callGenericBinding` / `genericArgTypeBy` / `memberConst`+`constEvalState` / the three mono-stamp tables / the alias-target return value given typed pair RECORDS (`callslot`, `genericslot`, `constslot`, `monostamp`, `deftarget`). `ztypes.memberKey` deleted with the last caller. **This is an architecture change measured as one, and the headline is that it is free: both binaries on IDENTICAL input (HEAD's `src`, `--emit-c /dev/null`), gcc -O1, `perf stat -r 7` -- instructions 6,473,863,458 -> 6,470,996,444 (-0.04%, spread +-0.02%), cycles +0.08%, task-clock +0.53%, allocations -208, bytes +456,292 (+0.10%, the `u32` per Decl the conformer mark added).** LOC +0.03%. **A record key is not slower than a packed u64 -- it is faster.** The mono-stamp family, the hot one (the emitter probes it per node inside a generic body), measured on its own: instructions -0.12%, cycles -0.40%, task-clock -1.56%, allocations flat. The multiply-and-add every probe paid and the divide-and-subtract every unpack paid cost more than hashing eight more bytes. **One family DID regress, and the cause is worth carrying: the alias-target record cost +0.90% instructions** (6,472,418,756 -> 6,519,940,768 across the same span, measured per stage on one input) **because `nameid.isZero` is a declared one-line method that gcc -O1 does not inline** -- five presence tests on the alias-resolution path each became a real call, `z_t3797_nameid_isZero(&altK9.unitName)`, where the packed key tested `altK9 > 0` with a compare. Reading the field directly (`.u32 > 0`) gave back 52.4M of the 58.4M. **-O1 is the SERIES level, not the shipped one: gcc -O2 inlines the method away, and the same five sites are worth 0.19% there rather than 0.80%.** See the section below. `make perf-elision` reports a write-only pool of **ZERO**.

<a id="r-154095f8"></a>
### the environment arc (66 commits)

**2026-08-17 · 154095f8**

**The environment arc, 66 commits `500eb312`..`154095f8`: one resumable environment (frames ARE Decls, `envLookup` + a cursor, locals as Decls, monos on the arg trie under their template, a `public:` block as a nested unit, `zframes`/`ty.nsOwner*`/the parallel-verify scaffolding deleted), closed by the numeric move (`ae083d34`: wideint/halffloat/quadfloat are hidden subunits of system).** **The headline is a cost, measured the rigorous way -- every binary a gcc -O1 series build in its own worktree, all on IDENTICAL input (this row's `src` with the pre-move `lib/system`, `--emit-c /dev/null`), `perf stat -r 7`, spread +-0.01..0.02%: instructions 6,589,939,195 -> 7,571,555,608 (+14.90%), cycles 2,799,748,889 -> 3,148,075,028 (+12.44%), task-clock 549.1 -> 610.4ms (+11.2%).** Per step, same input: steps 0-2 (`ee36a28b`, frames and locals become Decls) **+10.73%** -- the arc's own step-2 A/B read +5.1% because it compiled the source of THAT day; the number that stands is this one; step 3 flat (-0.04%); 5b template bodies resolve lexically **+1.68%**; 5c/5d -0.21%; 5e +0.01%; the 4+7 flip (`ae7b5863`, name-derived frames deleted) -0.17%; step 6b `public:` as a nested unit (`a3ffb1fc`) **+2.43%**; the step-8 preparations +0.02%; the stamp-pass retry list (`2436d8e3`) +0.04%; **the move itself, HEAD's binary on the moved vs the pre-move lib: +0.05% -- three fewer file units cost nothing.** Allocations 8,645,497 -> 8,722,220 (+0.89%) on LOC +1.46%, flat per line; **bytes churned 480 -> 531MB (+10.5%)** -- `Decl` rows for ~42k frames and ~34k locals, each a 17-field record with a List and a Map header (the step-2 measurement said +8.7%). `make perf-elision` not re-run. **Where the +14.9% lives is the next perf arc's brief: `Decl` is fat and every local/frame mints one (lazy `byName`/`children` on Decl was the follow-up named at step 2); 5b and 6b each add a lookup leg per name (template frame, public-block surface). The wall/RSS/phase columns are NOT an A/B (source grew, and this machine ran a 23-day-old orphaned `zc` at 100% until this measurement killed it -- load average ~3): wall 0.52 -> 0.60s, phases 529 -> ~616 total, mi RSS 139 -> 144MB.** LOC now counts `lib/system/**/*.z` (the subunit files moved, not vanished).

<a id="r-4fc75709"></a>
### the environment arc's cost, chased

**2026-08-17 · 4fc75709**

**The environment arc's cost, chased: three commits `aac9077b`, `ea40448c`, `1a2b5b94`.** The previous row measured the arc at +14.9% instructions and asked where it lived; per-function profiles (`--readable-names` builds, `perf record` on identical input) answered, and none of it was the design. (1) `ZSymbolTable.snapshotFrame` laid a dump row (a String copy each) for every local and overlay row as EVERY frame closed, in every compile, for the model dump alone -- and had since long before the arc: gated on `ty.dumpRows` (set by `zc dump`), **-4.55% instructions, -41MB churned, -83k allocations**. (2) every block, arm, loop and call bracket minted a Decl row -- 43,012 in the self-compile, 22k of them empty (a frame that declares nothing is invisible to every walk): a block frame is now a zero slot on the frame stack until its first `defineLocal` (or `markUnreachable`) materialises it under the nearest frame that has a row; a dump opens frames eagerly as before -- **-1.21%, -25MB, -47k allocations**. (3) what a hop of a frame walk paid: `unitFrameOf` decided unit-frame-ness by Decl -> node -> `unitOpDeclaresUnit` -> `unitOpKindIs` on every hop of every `envLookup` / type-ref walk / `frameFor` (now a bit on the row, `Decl.unitFrame`, laid at index time -- a `require:` block is not a frame, which is why a bit and not the kind); `publicNsOf` scanned all ~1,400 children of a unit root per qualified lookup (now `ZTyping.publicNsByOwner`); every Decl row owned a boxed `MapVV` name index used by 140 of 95,779 rows (now a slot into `ZTyping.nameIndexes`, no heap object per row); `frameFor` climbed from the body frame twice (now once, from the unit frame) -- **-2.43%, -73k allocations**. **Rigorous A/B, every binary on IDENTICAL input (this row's `src` + `lib/system`, `--emit-c /dev/null`), `perf stat -r 5`, spread <=0.05%:** series binaries gcc -O1: `500eb312` 6,595,779,475 -> `3283f06e` (before these commits) 7,720,137,748 (+17.0%) -> **6,918,199,917 (+4.9% vs the pre-arc compiler, -10.4% vs before)**, cycles 2,976M -> 3,219M -> 3,025M (+1.6%); the shipped level gcc -O2 (`bin/zc`): 4,137,179,478 -> 4,615,750,089 (+11.6%) -> **4,247,963,181 (+2.7%)**, cycles 1,935M -> 2,155M -> **1,916M (-1.0%: below the pre-arc compiler)**. Allocations 8,645,497 (previous-row input) / 8,722,220 -> **8,529,539**; bytes churned 480MB / 531MB -> **462MB, below the pre-arc row**. Peak RSS (mi) 144 -> 120MB. Same-source A/B raw-identical over 448 programs at every step; dump goldens unchanged. **What remains of the arc's cost, attributed and open:** `findLocal` (locals as Decl children, a linear scan per materialised block frame; ~1.3%), `getLiveOwnedVars` (two SetVals per `if` and per arm; ~1%), step 6a's `ewEmit` (every `:x` label through the extern collector's linear scope scan; ~0.9%), `declIdOfType`+`declFindChild` per member lookup (Q2, not this arc). Row taken with the machine at load ~2 (other sessions); wall/phase columns are on the tree's own source as always.

<a id="r-fcc1bf25"></a>
### one-unit arc phases 5-7, plus two arcs

**2026-08-18 · fcc1bf25**

**The one-unit arc's last three phases plus the two arcs before them, 46 commits `4fc75709`..`fcc1bf25`.** The rowed part is phases 5-7 (`35e959b4`..`fcc1bf25`): the ambient unit becomes a `ztypes.declid` (171 StringView params flipped), the residual name channels close (text->Decl conversions in `ztypecheck.z` 110 -> 17, `declRootUnitName` and `unitTidOfName9` retired), and the composed string key `"{a}.{b}"` goes (`dataKey9` was literally `"\{tid}"`; `methodReturns`, the `defName` prefix-strip and `internUnitId9` deleted). The earlier 20 commits (sentinel row 0, `require:` is a unit, one-unit phases 0-4) were never rowed and are NOT attributed. **The headline: phases 5-7 emit BYTE-IDENTICAL `zc.c` on HEAD's own source -- semantically inert -- and cost 2.56% fewer instructions.** **Rigorous A/B, every binary a gcc -O1 series build in its own worktree, all on IDENTICAL input (this row's `src` + `lib/system`, `--emit-c /dev/null`), `perf stat -r 7`, spread +-0.02%:** span `4fc75709` 6,833,630,258 -> **6,449,413,978 (-5.62%)** instructions, cycles 2,811,955,739 -> 2,726,075,163 (-3.05%), task-clock 584.6 -> 548.7ms (-6.15%); allocations 8,393,352 -> 8,366,589 (-26,763), bytes 458,312,654 -> 457,941,703. **Per phase, same input:** phase 5 (`35e959b4`->`bb81d421`) **-2.35%** instructions and -5,240 allocs -- deleting the `scanUnit` threading and the name->frame lookups is where the CPU is; phase 6 (`->c538abe8`) -0.26% and **-17,220 allocs** -- the door and round-trip deletions (`unitNameTextOf` / `declRootUnitName` / `poolTextAt` each allocate a String); phase 7 (`->fcc1bf25`) **CPU-neutral** and -4,379 allocs, -117,877 bytes. **Phase 7's instruction reading needed care and is a good calibration:** three interleaved rounds gave P6-end 6,448.8M / 6,447.0M / 6,449.6M and P7-end 6,451.3M / 6,451.6M / 6,448.8M -- a +0.03% difference of means against a ~0.04% round-to-round range within EACH binary, with round 3 inverting the sign. **Not resolvable; do not call it a regression.** That phase removed 4,703 futile map lookups and 152 writes per compile and still moved no instructions, because the work it deleted was cheap and skipped -- its win is in the allocation column, which is exactly where composed string keys live. The pre-rowed 20 commits account for the rest of the span (-3.14% instructions) at +76 allocations, i.e. flat. `make perf-elision` reports a write-only pool of **ZERO**. Machine quiet (load 0.05).

<a id="r-f4a6ed00"></a>
### the emitter's unit lookups

**2026-08-18 · f4a6ed00**

**The emitter's unit lookups, 2 commits `fcc1bf25`..`f4a6ed00`: the unit-name index deleted (`6d468c40`) and three registry sweeps turned into declaration lookups (`56f54782`).** `ctxBindMain` stopped resolving a name -- four of its eight call sites are RESTORES of an earlier bind and now hand back a saved declid, and the four real binds each already held the unit by id (`unitDeclOfTid` of the type being emitted, or the nameid on the unit node being walked). `unitIdByName` / `unitIdOf9` are gone; the four stdlib units resolve once into Ctx anchors. `emitIoFileClass` / `emitNetFdClass` / `cliClassTypeId` each swept every id in the registry, text-comparing names, to find ONE member of ONE known unit -- now `declFindChild(unitDecl, nameId)`. **Rigorous A/B, both gcc -O1 series binaries on IDENTICAL input (this row's `src` + `lib/system`, `--emit-c /dev/null`), emitting BYTE-IDENTICAL C: instructions FLAT and below resolution** -- two interleaved rounds gave `fcc1bf25` 6,457.2M / 6,453.3M and `f4a6ed00` 6,453.3M / 6,452.9M, a -0.034% difference of means against a 0.061% round-to-round range in the baseline itself. That is the expected shape: the three sweeps ran once per emission each, ~3x nextTypeId iterations against 6.45 billion instructions, so deleting them is invisible in the CPU column however wrong they were architecturally. **The measurable win is allocations: -2,482 (8,368,288 -> 8,365,806) and -79,617 bytes on identical input.** Attribution is exact: the only call removed from `buildDefUnitIndex`'s per-type loop is `unitNameOfTid`, which ends in `reg.nameOf` -> `got.name.copy`, one owned String per type that has a defining unit -- so the delta IS the number of such types, and -79,617/2,482 = 32 bytes average, the right size for a unit name like `system` or `collections`. `make perf-elision` reports a write-only pool of **ZERO**. **Machine NOT quiet for the timed columns** (load rose from 0.16 to ~2.5 mid-measurement, other sessions; the glibc series degraded 0.65/0.64/0.68/0.81/0.81 within one best-of-5, and `make test` read 35.8s against 14.1s on the previous row). Wall, RSS and `make test` are not an A/B here and should be read against the previous row only with that in mind; the instruction and allocation columns are counts and are unaffected. Own-source bytes rise 458 -> 460MB on LOC +44 while identical-input bytes FALL -- the column compiles the tree's own, larger source.

<a id="r-d8cebf3a"></a>
### design-review consolidation, phases 2.5-2.8

**2026-08-21 · d8cebf3a**

**The 2026-08-20 design review's Phase 2, 167 commits `f4a6ed00`..`d8cebf3a`: identity by id, not by name, and no typechecker function over 150 lines.** The arc is structural, not an optimisation, so the numbers are a consequence rather than a target -- but they are the largest single-row movement the table records. **Rigorous A/B, both gcc -O1 series binaries on IDENTICAL input** (this row cannot use the current tree: the `f4a6ed00` compiler REFUSES it -- `zvfs.z:783` matches on a `u32`, which only the newer checker accepts -- so the common input is `f4a6ed00`'s own `src` + `lib/system`, `--fast-hash --emit-c /dev/null`): **instructions 6,449,910,108 -> 5,647,348,342, −802,561,766 (−12.44%)**, ±0.01% and ±0.03% over `perf stat -r 7`. **Allocations 8,365,663 -> 6,614,031, −1,751,632 (−20.94%); bytes 459.57MB -> 343.50MB, −116.07MB (−25.26%)**, `allocs == frees` on both sides. The old side reproduces this table's own `f4a6ed00` row (8,365,699) to within 36 allocations, which is what makes the pair comparable.

**Where it came from.** Three workstreams dominate. **The pool-borrow seam** (`0ab469ea`, `c76e4d36`, `9c54b51a`, `f3778233`): 148 bindings took an OWNED copy of a name out of the string pool, read it once -- a `.stringview` compare, a `.length` test -- and dropped it; they read the pool directly now, which is a borrow. That alone is −646,053 allocations and −3.5MB. **The IdMap/IdSet container arc and the id-keyed sweep**, which preceded this row's span and continued into it. **`ensureNativeLinkTargets` deleted** (`4d2abff9`): 235 lines that force-descended 28 stdlib templates by name before anything demanded them.

**What the arc actually settled** is identity. A name is a pool id, a type is a tid, a declaration is a declid, and the tables that used to key on composed text now key on those. `systemUnitRoot` in the checker went 34 -> 19 (the residue is not a sweep: 6 sites ATTACH a canonical type, 5 force-descend a built-in where the anchor IS the shadow-bypass mechanism, 3 are identity gates). A001 literal-compares in `ztypecheck.z` 365 -> 269, A003 named-literals 183 -> 106.

**Phase 2.7 split every giant in the typechecker.** 29 functions over 150 lines, the largest 1,562, became zero over 150 with the largest at 149 -- ~20 commits, each byte-identical on a frozen tree for `zc`, `zl` and `zls`. A005 (the 80-line rule) rose 44 -> 77 as a consequence and says so in the baseline file's header: cutting one 1,562-line finding into nineteen functions of 36-149 necessarily adds rows, and the threshold is 80 while the target is 150.

**Phase 2.8 made `callKind` total** over calls in value position: 1,123 unclassified -> 0 on `zc`, 0 on `zl`/`zls`, 0 across all 148 examples. That costs one map entry per call -- allocations +3, bytes +0.42%, instructions +0.125% -- and is the whole of the difference between this row's identical-input figures and the previous commit's. It is what Phase 3.2's total `match` needs.

**Three defects surfaced and were fixed inside the arc**, each pinned: a bare name used as a truthiness condition (`if ghost then`, `when ghost`, `for ghost loop`, `} while ghost`) was never name-resolved and reached the emitter as a raw C identifier (`75dbe93c`); `checkWrappedCall` and `checkMetaCreateCall` fell off the end of the function with no return, and their callers read the indeterminate value as a resolved type (`19c42e19`); and a fn-typed LOCAL shadowing a function of the same name was called BY NAME, so `addit: mulit.take` then `addit a: 3 b: 4` ran the shadowed body and answered 7 instead of 12 (`83603309`).

**Machine quiet for the timed columns** (load < 0.5 throughout; the glibc best-of-5 held 0.54-0.55s and `make test` 15.5s against the previous row's 35.8s at load ~2.5 -- that row's wall columns are the anomaly, not this one's). Own-source LOC rose 99,100 -> 101,530: the split adds function headers, and the arc adds the stamps. Peak RSS 118MB -> 92MB (mimalloc) and 100MB -> 79MB (glibc).

<a id="r-a47a444c"></a>
### An empty view owns no buffer

**2026-08-22 · a47a444c** (one runtime guard plus the Makefile dependency that was hiding it)

**`z_String_from_view` malloc'd `size + 1` unconditionally**, so every `"".string` -- the idiom
this compiler uses for "nothing to say", 479 `return "".string` sites in `src/` alone -- allocated
one byte holding a terminator that nothing reads: a String is read through its `size`. Those were
**1,016,288 blocks, 15% of the self-compile**, and the same share of any program that uses an empty
String as a sentinel. An empty view now yields the zero struct, which is not a new state at all:
`z_String_create(0)` has always produced it (data NULL, capacity 0), `_free` guards it, `_reserve`
reallocs from NULL on the first append, and `from_view` already set `capacity = sv.size`, so length
and capacity read 0 either way.

**`_copy` took the same guard in `2f83d269`, and that one IS a visible change:** copying an empty
String answered `capacity` 1 where `create(0)` and `from_view` answer 0 -- the value was never
consistent between the three producers, so nothing could rely on it meaning more than ">= size",
and all three now agree. Worth **another −80,560 allocations** (`make perf` 5,701,889 ->
5,621,131), more than the census's 38,033: that figure counts only the program points whose blocks
are ALL two bytes, and empty copies also ride in points of mixed size.
`tests/fixtures/emitc_corpus/empty_string_copy` pins all three producers' length and capacity --
recorded against the old behaviour it failed on the change, and it was the only case of 1,120 that
noticed.

**Rigorous A/B, both gcc -O1 series binaries on IDENTICAL input** (current `src` + `lib/system`,
`perf stat -r 7`): **allocations 6,720,706 -> 5,701,889, −1,018,817 (−15.16%)**; instructions
5,667,880,950 -> 5,594,939,972, −72,940,978 (−1.29%) (±0.02% both) -- a malloc/free pair per empty
string is not free; cycles −0.61% and task-clock −1.8%, both inside the noise floor. Bytes
346.65MB -> 345.57MB (−0.31%): one byte per allocation, exactly as predicted. `make test` 1119 with
453 leak-clean ASan runs, ci green.

**The Makefile could not see the change.** `bin/zc.c`, `out/zl.c` and `out/zls.c` listed the `.z`
sources as prerequisites but not the hand-written runtime the emitter inlines into them, so a
fragment edit left every artifact stale -- **the first measurement of this commit read as +1
allocation**, because nothing had been re-emitted. `RT_DEP` is that missing dependency, and the
lesson is the same one this file records about `tail` and `grep`: a measurement that reads as "no
change" is first a question about whether the change was built.

**What the census holds now** (5,701,684 blocks; text copies 2.66M): `Tokenizer_tokSpan` 776,543
(every token's text an owned String -- a view into the source plus intern-on-demand is the
standing proposal), `Lexer_accept` 326,277, `dataFieldNames` 291,867, `ZTyping_declChildWalk`
278,198, `atomTextOf` 216,711, `unitNameOfTid` / `definedInUnitOf` 157,213, `hoistArg` 144,069,
`varCName` 135,358. `String_copy` is now the largest operation at 1,537,645.

<a id="r-457f609a"></a>
### Allocation recovery: the walkers, the resolvers and the readable-names collision

**2026-08-22 · 457f609a** (six commits from `5bd50a3f`: `ee24fb29` the `--readable-names` fix,
`7119894e` argument/typeref resolvers by name id, `f492b258` borrowed String params by tid,
`7c63883b` the conversion legs, `35c1a4f1` the lock answer, `457f609a` the walkers)

**The previous row recorded +474,041 allocations on identical input and named the recovery path;
this row is that recovery, measured the same way.** The census that drove it (valgrind dhat, the
compiler compiling itself) said the +474k was text copies on top of a much larger pre-existing
budget, and it named the biggest single program point nobody had looked at: `zast.childIds`, at
481,033 blocks -- a freshly built list of a node's children for every node two whole-tree walks
visited. What the arc removed, by operation: `ListVal` appends −428,899, `String_copy` −161,458,
`String_from_view` −76,010, `String_create` −16,610.

**Rigorous A/B, both gcc -O1 series binaries on IDENTICAL input** (the `5bd50a3f` compiler accepts
this tree, so the common input is the current `src` + `lib/system`, default hash, `--emit-c
/dev/null`, `perf stat -r 7`): **instructions 5,831,570,591 -> 5,665,978,238, −165,592,353
(−2.84%)** (±0.05% / ±0.02%), cycles 2.359G -> 2.294G (−2.79%), task-clock 466.0ms -> 458.1ms
(−1.7%, inside this setup's noise floor). **Allocations 7,429,618 -> 6,720,484, −709,134
(−9.54%); bytes 355.98MB -> 346.59MB, −9.39MB (−2.64%)**; `allocs == frees` on both sides.

**The Phase 5 cost is repaid with interest: 6,720,484 against the pre-Phase-5 6,941,032, on a tree
4.2% larger.** Where it came from, largest first. The two whole-tree walks stopped building child
lists (−430k): `scanCallsInNode` built one before its `match`, so every atom and every definition
arm paid for a list it ignores, and the block shapes now descend through their own fields;
`ewWalk`'s generic arm was where nearly every node landed, and the nine shapes a program is mostly
made of now spell their children in `childIds`' canonical order -- for the list-shaped ones that
means walking the node's OWN list and copying nothing. Three argument and typeref resolvers stopped
copying interned names to hand them back as lookup keys (−120k: `paramTypeId` carried four copies
per parameter type for a function that usually returns at the stamp one line later). The
conversion legs ask which member before building a `<unit>.<Type>` path for it (−72k). A borrowed
String parameter is recognised by type id instead of by copying its type's name (−42k). A lock
that conflicts with nothing answers with a String that owns no buffer instead of `"".string`
(−44k).

**A pre-existing defect the arc surfaced and fixed first** (`ee24fb29`): `--readable-names` spelled
every local by its source name, which is unique only inside the frame that binds it -- and a bare
block is flattened into its enclosing C block, so two sibling frames binding `n` emitted
`int64_t n` twice. gcc rejected the file, the compiler's own sources carried the shape, and ci
never saw it because `readable-check` compiled six small programs and never the compiler. The
scheme now suffixes every binding after the first with its variable id, and `readable-check`
compiles, links and runs the readable-named compiler.

**What the census still holds, unspent** (the same run, after this arc): text copies are 3.68M of
6.72M blocks. `Tokenizer_tokSpan` 775,787 (every token's text an owned String), `atomTextOf`
240,595 (157k of it behind one emitter helper), `dataFieldNames` 291,499,
`ZTyping_declChildWalk` 277,772, `unitNameOfTid` 164,029, `nodeConstText` 142,897, `varCName`
135,197. Separately, **1,055,727 blocks are one-byte buffers behind `"".string`** -- a `String`
constructed bare owns none, which is what the lock commit above used.

**Machine at load 1.7–2.9 for the timed columns** (other users); `perf stat`'s instruction counts
are load-independent and are the comparison to trust. Own-source LOC 105,671 -> 105,814.

<a id="r-07c16e90"></a>
### Phase 5: the `:name` shorthand lock rule made unconditional

**2026-08-22 · 07c16e90** (the following commit `a8d86669`, L020 promoted to a warning with 165
parameters turned `.view`, emits byte-identical C for `zc` and `zls` and differs in `zl` only by the
rule's severity constant, so this row describes both)

**Phase 5 closed the `:name` shorthand lock gap.** A `:name` argument at a lock/borrow parameter
took no lock, so a readonly binding could be handed to a mutable-borrow callee without a
diagnostic. The rule that closes it reported 3,313 E0200 rows against the compiler's own tree when
first measured, 998 at the start of this arc, and 0 at `88007d62`; the landing (`07c16e90`) deleted
the `--strict-call-locks` flag, the ratchet and the baseline. The cost of the drain is what this row
records: ~50 commits of signature honesty (`ast`/`symtab`/`ctx`/`st` views, records of ids into
the checkValue family, read-only lookups) plus the copies and hoists the strict rule forced where a
borrowed name or a nested read used to ride through a call.

**Rigorous A/B, both gcc -O1 series binaries on IDENTICAL input** (the `d8cebf3a` compiler accepts
this tree, so the common input is the current `src` + `lib/system`, `--fast-hash --emit-c
/dev/null`, `perf stat -r 7`): **instructions 5,926,763,745 -> 5,819,175,877, −107,587,868
(−1.82%)** (±0.01% both), cycles 2.424G -> 2.328G (−3.9%), task-clock 483.8ms -> 461.2ms (−4.7%).
**Allocations 6,941,032 -> 7,415,073, +474,041 (+6.83%); bytes 352.74MB -> 355.52MB, +2.78MB
(+0.79%)**; `allocs == frees` on both sides; peak RSS 93.3MB -> 93.7MB. The old side on its own
tree reproduces its row (6,722,550) to within the input's growth (LOC 101,530 -> 105,671, +4.1%,
worth +218,482 allocations on the old binary), which is what makes the pair comparable.

**Verdict: CPU did not regress — instructions −1.8% and cycles −3.9% on identical input — and
allocations did: +6.8% on identical input, +10.3% row to row.** Where the +474k comes from, by
the own-tree series: 6,722,550 (`d8cebf3a`) -> 7,227,939 (`e7260096`, the first drain session,
3,313 -> 998: pool-text copies where a `String.borrow` of the name pool used to be held across a
call, and argument hoists) -> 7,359,797 (`88007d62`, this arc's 22 commits: `nameTextCopy` on the
definition-side resolvers, the emitter's iteration copies of unit and fragment names) -> 7,412,765
(`07c16e90`: the shorthand locks themselves, now installed — inherent to the rule). The bytes
column moves far less (+0.79%) because the copies are short names. **Recovery path, recorded:**
name ids through the definition resolvers instead of `nameTextCopy` (the checker's value-side
already did this — the `*Ref` records carry ids), and the emitter's per-unit copies read by index.

**Machine NOT quiet for the timed columns** (load 1.0–3.6 from other users; the `make test` column
is at load, as the `f4a6ed00` row's 35.8s was — compare it with `d8cebf3a`'s 15.5s only with that
in mind). The glibc best-of-5 held 0.54s, the mimalloc best-of-5 0.44–0.45s; `perf stat`'s
instruction counts are load-independent and are the comparison to trust.

**Also in this span, not perf:** the emitter's mutable `ast`/`symtab` signatures were borrowed
from four checker lookups (`walkLookupTyperef` ×3, `walkLookupTyperefById`) — read-only forms of
those let one directed descent flip the whole cluster; `tests/unit/zast_smoke.z` carried seven
nested `:ast` readers the three-surface sweep did not cover and the harness caught at landing;
L020 `unmutated-mutable-parameter` lost its reason to be advisory (its "mutated" record is the
lock record the gap used to skip) and is a gate-failing warning as of `a8d86669`, with the
compiler and tool sources clean under it.

<a id="r-e2c867af"></a>
### Member names, the row accessors and the tokenizer's trivia

**2026-08-23 · e2c867af** (seventeen commits since the last row: `a0f4bfad`..`a39ccfa6` unrowed,
then `051a4167`..`e2c867af`)

**5,621,131 -> 4,820,294 allocations over the whole span, and 4,820,294 is -387,578 (-7.44%) on
this arc's own nine commits from `a39ccfa6`'s 5,207,872.** Bytes 345MB -> 306MB. The wall barely
moves, as it does not for allocation work at this size; the phase split is flat.

**Two defect fixes first, and they cost rather than paid.** A record or class field named for a C
reserved word emitted `int64_t default;` -- a legal zerolang program the compiler miscompiled, the
same class as `620db166`'s `&f(x)`. The fix is a PREFIX rather than a mangled copy, and that choice
was measured, not assumed: `mangleMemberName` handing back a String cost **+19,343** allocations
because every caller is already building the C text around the name, while
`mangleMemberPrefix` + `"\{prefix}\{name}"` costs **+2,945**. Twenty-two emission sites move
together -- the declaration, the reads and writes, the constructor parameters, the destructor, the
generated `_eq`, the variant arm payloads -- and `cSlotName` takes the prefix, which covers the four
spellings of an interface vtable slot at once. The oracle that mattered: no member in the tree is
named for a reserved word, so the frozen A/B AND all 455 corpus programs had to emit
**byte-identically**, and a diff would have meant a natives.tbl lookup key got mangled.

Writing that prefix uncovered a **segfault of its own** (`051a4167`): `s: String` then
`s.append ""`. An empty String owns no buffer since `a47a444c`, `reserve(s, 0)` correctly allocates
nothing, and `append` then wrote the terminator through the null. The guard is `if (len == 0)
return;`, deliberately not a reserve that allocates -- appending nothing is a no-op, and making the
buffer appear would undo `a47a444c` at exactly the sites that earned it.

**The largest structural item: ZTyping's walk became row accessors** (`aadc537a`, **-109,329
blocks, -28.9MB**). `declChildWalk` fills two caller-supplied lists, so its 278,774 blocks are its
callers' -- and four of them wanted a scalar. `fnAutoCallable` alone was 96,016 blocks to answer
"is there a non-receiver parameter". The lever was NOT a new predicate restating the filter: that
drift is already in this file, where `declMemberCount` and `declLastChildNameId` are hand-inlined
copies of the walk and disagree about the pended path. Instead `declRowSlots`/`declRowAt` return a
`declrow` whose `keep` IS the filter, and `declChildWalk` is those accessors collected -- one
implementation, so a caller that loops the slots is running the same walk. `ztyping_smoke` walks a
declared type and a pended one both ways; the `agree` word is tautological once the list form is
derived, so **the golden's row count and id checksum are the oracle** -- a fallback that stops
answering turns pended `rows=1` into `rows=0`.

**The largest single item: trivia tokens stopped owning text nobody reads** (`e2c867af`,
**-265,696, -5.2%**). `Tokenizer_tokSpan` is 15% of a self-compile because every token owns its
source bytes, whitespace and comments included -- 42.8% of all tokens, all dropped by
`Lexer.advance` before the parser sees one. `keepTrivia` defaults TRUE, so `tokenize.scan` and the
lexer goldens are exact; `Lexer.create` clears it on the tokenizer it takes. Scoped to WS and
COMMENT: EOL survives into the parser when `filterEol` is off, and `peekPos` derives a diagnostic's
width from `tokstr.length`.

**The token arc, measured for whoever takes it next** (974,566 tokens dumped through `out/zlexer`).
Of the ~512,879 `tokSpan` copies left, **279,729 (55%) are a CONSTANT of the token type** -- a
COLON is always `":"` -- and need no text, no pool and no interning, just a `textOfType` table for
readers that want the spelling. The other 277,431 carry real text over only **27,213 distinct**, of
which REFID and LABEL (23,226) are names the parser interns anyway: **the pool-pollution worry
about interning at tokenize time is not real**, ~3,987 rows are genuinely new. So the order is
`Token.width` first (allocation-neutral, and a token with no text still owes diagnostics a width),
then the fixed-text kinds, then interning, then `Token` as a record to retire `Lexer_accept`'s
327,093 Option boxes.

**Measured and deliberately not done:** `dataFieldNames` still holds 187,761 blocks behind its
twenty-four name-comparing call sites (`missingCtorFields` 52,672, `emitUserFnCall` 36,166,
`fnSignature` 29,247), which is a signature chain through `argSlotList`, `ctorArgByLabel` and
`paramTypeIsPointer` -- the worst effort-to-reward left. `varCName` 135,690 is unchanged.

**`make test` is at load ~5.6 here** (other users on the machine); compare the 35.9s column with
`2f83d269`'s 35.0s only with that in mind.

<a id="r-a04609b7"></a>
### The `fold` natives arc

**2026-08-25 · `288abea5`..`a04609b7`**

Platform's five values became zero-argument `is native` functions answered from
`natives.tbl` `fold` rows, and the declaration check that every native names a
row stopped being main-unit-only.

**It costs +0.10% instructions**, measured the way the header asks: both
binaries on identical input (the frozen tree at `58b06e4e`), interleaved,
`perf stat -r 7`. base 5,541,498,132 / 5,542,036,204; HEAD 5,548,604,514 /
5,545,999,755. Both spreads are tight (0.010% and 0.047%), so a +0.10% delta is
above the noise and is a real, small regression rather than a reading.

The self-compile allocation column moved 4,883,704 -> 4,899,755, +0.33%, on
+361 lines of source. Per line it did not move at all: **45.19 allocations per
line before and after**, so the column tracks the compiler having more source
to compile rather than the compiler having got worse at it.

The part that is a genuine new cost is FIXED per compile, so the self-compile
hides it and a small program does not. On `examples/hello.z`: 68,636 -> 72,847
allocations, **+6.1% on a minimal input**. Attributed by ablation rather than by
reading, since finding where it went was the whole point:

| ablated | allocs | attributes |
|---|---|---|
| base | 68,636 | -- |
| `foldNativePaths` returns before reading | 68,992 | the wider declaration check: **+356** |
| file read, never scanned | 69,003 | reading natives.tbl: **+367 allocs, +394KB** |
| lines walked, no `contains`, empty body | 70,265 | `.lines` over 1,240 lines: **+1,262** |
| shipped | 70,818 | the `contains` prefilter: **+553** |
| before the prefilter | 72,847 | splitting all 917 row heads: **+2,029** |

The prefilter is the fix that came out of it, and it is one condition: the table
is read whole to find five rows, so ask whether the line spells `fold` before
splitting its head and materialising its path. 912 of the 917 rows now answer no
without allocating. A row that merely contains the letters still gets the full
parse, which is what decides -- pinned by decoy rows named `platform.unfolded`
and `platform.foldy`, neither of which folds.

What is left is the read itself plus the stdlib's per-line cost, and both are
inherent rather than this arc's: `.lines` allocates about one block per line and
`contains` about 0.6 per call at the call site. On a self-compile the whole
fixed cost is 0.04% and invisible; where it is worth remembering is `zls`, which
re-reads the 95KB table on every document change.

**Standing worklist, unchanged by this arc:** `zl lint --full` reports **90
L022** findings -- an owned String from `.copy` / `.string` / `nameTextCopy`
that nothing ever writes, where a borrowed view would serve. 73 are in
`ztypecheck.z`, spread thinly across ~40 functions rather than pooled in a
hotspot, so it is a sweep and not a one-liner. dhat on a `hello.z` compile puts
`String.from_view` at 22,562 blocks averaging 7.6 bytes and `String.copy` at
12,068 averaging 16.9 -- name copies, which is what that lint names. Each site
needs its own write-analysis: a binding that is REASSIGNED must keep the copy.

<a id="r-33e4e5fa"></a>
### Generic families, then four pre-existing defects

**2026-08-28 · `35294f1c`..`33e4e5fa`**

Two correctness arcs, no perf work in either. The first closed four
generic-family gaps (a user generic variant mono reaching an emitter; a record's,
variant's and union's as-block methods marked and emitted; a self-typed return
substituting). The second closed the four pre-existing defects that arc
surfaced: a sum type's dotted read asking what the member is, a borrowed reftype
returned bare as owned refused, a pointer receiver in value position
dereferenced, and a fnptr typedef taking the signature's return promotions.

**Instructions are at parity, but only after fixing a regression the first
reading found.** Measured the way the header asks: both binaries on identical
input -- the `69680fd0` tree with only the borrow escape the arc itself fixes
patched in, so both compilers accept it -- `perf stat -r 7`, emitted C
byte-identical both ways.

| | instructions | vs base |
|---|---|---|
| base `69680fd0` | 5,686,366,798 | -- |
| arc as first landed | 5,701,303,536 | **+14,936,738 (+0.263%)** |
| after `b1a4dd5d` | 5,684,653,731 | −1,713,067 (−0.030%) |

Both spreads are ±0.03–0.04%, so +0.26% was above the noise and readable.
Ablation put **95% of it in one predicate**: `needsValueDeref` ran
`pathIsPointer` at every return, typed binding and match subject, and that
function's call and dotted legs walk stamps, names and canonical ids to answer a
question only a bare name can answer yes to. Narrowing it to a bare atom, and
asking it before `.copy` builds its expression rather than after, returned the
arc to parity.

**The other three fixes cost ZERO allocations**, each confirmed by disabling it
and checking the disable took (the new E0200 stops firing). What is left is
+21,551 allocations on that fixed input (4,882,079 → 4,903,630, +0.44%), of
which only +5,752 is attributable to anything nameable; the rest did not respond
to ablation. Two things say it is not a fixed cost: on `examples/hello.z` the
new compiler allocates **fewer** -- 71,646 → 71,590 -- and per line of source
the self-compile column **fell from 45.19 to 44.26 allocations**. The column
rose because there are 2,551 more lines to compile, not because the compiler got
worse at compiling them.

`genericConstraintKind` returning a borrow rather than a copy was tried as a
candidate for the residual and moved nothing, so it was not kept.

<a id="r-86e7d6f5"></a>
### The `outx` arc, then three measured reductions

**2026-09-03 · `4088cc11`..`86e7d6f5`**

The `outx` arc (eleven commits) replaced the `.takex`/`.holdx` return markers
with a return keyword: `out` never pins, `outx` pins every borrow/view input
and the receiver at call strength, four signature/return rules (L1-L4) are
enforced, and a lock-holding value carries its locks when it moves. Three
pre-existing miscompiles were fixed on the way. No perf work in the arc
itself; this row is the arc's cost, measured, then reduced in three commits.

**Attribution, not one number.** The compilers across the arc do not accept
each other's sources (commit 1 adds a keyword, commit 6 deletes two), so the
arc was measured two ways: every commit's series binary on its own source
(`perf stat -r 7` instructions, valgrind allocations), and every compiler
from commit 1 to 5 on the commit-5 tree, the last input they all accept.
Allocation sites came from dhat with `--readable-names` builds; per-function
instructions from callgrind on the same builds -- sampled `perf` profiles
moved samples between unrelated symbols across `-O1` builds and could not
attribute a 0.5% delta, exact counts could.

| commit | what | instructions (own source) | allocations |
|---|---|---|---|
| `97afaad6` pre-arc | -- | 5,726,190,917 | 4,961,170 |
| `c08406f3` pin-all + read rule | +39.5M (+0.69%) | +104,134 |
| `affd2e6a` L2/L1 at the return | +14.9M | +10,351 |
| `23375fdc` lock transfer | +73.1M (+1.26%) | +47,450 (LOC +568 in that file) |
| `d7cbf695` retire takex/holdx | −13.6M | −6,821 |
| `4a97841a` arc HEAD | +129.7M (+2.26%) | +166,422 (+3.35%), LOC +1,091 (+0.94%) |

On the one shared input (commit-5 tree) the same-input deltas were: pin-all
+24.9M and +97.6k allocations; lock transfer +28.8M (callgrind exact) and
+14.2k allocations; the rest within noise. So about half of the own-source
rise is the compiler compiling its own new code.

**Where it went (dhat, pre-arc vs HEAD, +166,739 blocks):** the pin lists
built for every call (+67k: a bare argument's path copied twice, the list
then dropped for the non-`outx` majority of callees), the read check's
per-read path (+29k), the fifteen name copies commit 2 made to avoid a view
of `ast.names` across an exclusive `:ast` (+25k), and source growth.

**Three reductions, each A/B'd on one input against the commit before it:**

| commit | change | instructions | allocations |
|---|---|---|---|
| `3067befc` | pins collected only for a callee that keeps them; the read check asks the root before building a path | −13,660,555 (−0.23%) | −47,316 (−0.92%) |
| `f0d720b1` | receiver path only for a taken argument; a return names its function only when it reports | −10,440,086 (−0.18%) | −13,662 (−0.27%) |
| `86e7d6f5` | a frame close asks a holder's age (`openframe.firstVid`) before its frame; the callee's pinning is one fact per call | −4,491,971 (−0.08%) | −216 |

Net: −28.6M instructions (−0.49%) and −61,194 allocations (−1.2%) off the
arc's HEAD, on identical input; spreads ±0.01–0.05%.

**Left on the table, with numbers.** The compiler's largest allocation family
is unrelated to the arc: 430,460 of 5.13M blocks (8.4%) are owned copies of
interned name text (`nameTextCopy`), the top sites being `atomTextOf` in the
emitter (72.8k), `atomName` (59.3k), the per-argument label copy in
`checkTypedCallArg` (41.7k) and `checkMissingCallArgs` (41.7k). The argument
check chain threads the parameter NAME as text (`pn: StringView` through
`checkNamedArgParam`, `positionalParamName`, `coerceArgToParam`); moving it
to name ids is the "names are pool ids" direction and would retire the copies
rather than borrow them (a borrowed view of `ast.names` is exactly what
commit 2 had to remove under an exclusive `:ast`). Smaller: the four
per-function return-flag maps (`funcPins`, `funcReturnsBorrow`,
`funcReturnsView`, `funcReturnsFrozen`) are four lookups per call where one
record would do; and the pending-pin side channel is drained through a
reversing list on every consume.

<a id="r-49b9b267"></a>
### Name-text copies become name ids

**2026-09-03 · `263dee89`..`49b9b267`**

A dhat census at `86e7d6f5` put 430,460 of 5.07M heap blocks (8.5%) in one
family: an owned `String` copied out of the name pool so a checker or the
emitter could compare it, look its id up again, or thread it through a
text-keyed API. Every site started from a node's `nameid`. The migration
retired the families in measured phases, each a commit A/B'd on identical
input against the commit before it (`out/zc-perf`, gcc -O1, `perf stat -r 7`
+ valgrind):

| commit | phase | instructions | allocations |
|---|---|---|---|
| `263dee89` | P1 argument check chain: the parameter name is an id end to end (`positionalParamNameId`, `declChildOfId`, `provided: ListVal nameid`, `dataFieldIds`) | −65.0M (−1.11%) | −148,053 (−2.92%) |
| `57a9c4c4` | P2 emitter: the seven name helpers answer `outx StringView` views into the pool (the tree is `Ast.view` at all 454 emitter sites); 139 redundant `.stringView` swept by caret; C byte-identical | −14.9M (−0.26%) | −86,178 (−1.75%) |
| `24aa103a` | P3 + P4a: `checkDotted` and 22 helpers take `cnId`; marker predicates by id; typeref shells ask ids | −58.9M (−1.02%) | −79,063 (−1.64%) |
| `593f15dd` | P5 emitter: the last four direct copies are views; C byte-identical | −3.6M (−0.06%) | −24,712 (−0.52%) |
| `49b9b267` | P4 registrar chain: defaults and ownership recorded by id (`setDefault*Id`) | +5.6M (+0.10%, cross-build band) | −9,024 (−0.19%) |

Net over the arc, identical input: **−137M instructions (−2.4%)** and
**−347,030 allocations (−6.8%)**; the census family fell from 430,460 blocks
(8.5%) to 148,930 (3.2%).

**What made it cheap.** Every text lookup in the argument and member chains
already had an id-keyed twin (`declChildOfId`, `declOwnershipOfId`,
`declIsBorrowedFieldId`, `declIsFnptrFieldId`); the text forms were
`pool.find` wrappers. Flipping a producer (`positionalParamNameId`,
`atomTextOf … outx StringView`, `cnId: zast.nameid`) and letting the
typechecker enumerate the consumers found every site; the sentinel-shaped
ones (`x == "word"` → `wkWord`/`nameTextEq`, `isMoveMarker name:` →
`isMoveMarkerId`, `:cn` → `:cnId`, `find`/`poolFind` round trips) swept by
reported line, the report sites got a `cn: nameTextCopy` at the top of their
block (openers computed on the unmodified file, inserted bottom-up), the
rest by hand. Two gates only `make ci` runs caught what `make check` does
not: L012 for a text predicate that lost its last caller, and
`-Werror=unused-variable` for a zerolang binding whose only use was replaced.

**What is left, and why.** The remaining 148,930 (top sites:
`walkDefinitionById` 15.5k, `stampOnePendingTyperef` 15.2k, `atomName`'s
other callers 14.7k, `typedefChaseC` 11.5k, `baseTypeName` 7.7k,
`evalConstDotted` 7.7k, `resolveTyperefAtomShell` 7.2k, `bareAtomName` 5.1k,
`pathRootName` 4.4k) sit behind text-native APIs: the def-resolution core (`descendNamed` → `resolveDef defName:`,
`declareCanonicalType name:`, `newType name:` mint by text), the body-walk
entry points (`checkFunctionBody defName:`, `checkMethodBodies typeName:`,
reached from `walkDefinitionById`, 15.5k), literal parsing in
`resolveNamedType`/`evalConstDotted`/`constOfNamedBase` (byte and `contains`
tests on the spelling), and match-arm labels (`checkCaseClause`). Moving
those is the "resolution by id" arc, not a phase of this one: a name there
is minted, labelled and parsed as text, and a view cannot live across the
exclusive `:ast` those walks pass.

<a id="r-56e15e82"></a>
### Resolution by id

**2026-09-03 · `e4788990`..`56e15e82`**

The successor to *Name-text copies become name ids*: the def-resolution and
body-walk cores took names as text and re-found the id at every step. Each
phase is a commit A/B'd on identical input against the commit before it
(`out/zc-perf`, gcc -O1, `perf stat -r 7` + valgrind). On this input the same
binary moves up to ~20M instructions between invocations, so allocations
(deterministic) are the metric and instruction deltas below that are
reported as "at the floor".

| commit | phase | allocations | instructions |
|---|---|---|---|
| `e4788990` | R1a round trips: typeref-stamp origin id, receiver roots (`methodReceiverRootId`, `lockMethodReceiver rroId:`), generic-param returns, `markerPeeledBaseId`, arm refusals copy inside the refusal | −34,416 (−0.73%) | −7.0M |
| `f308278a` | R1b emitter: 14 more name reads are views; C byte-identical | −13,584 (−0.29%) | floor |
| `f48db995` | R2 body walk: `walkDeclaredDef nameId:`, `checkFunctionBody defNameId:` beside the label, `checkMethodBodies typeNameId:` (one copy per owner type, not per walk), `methodDeclOf`/`setSynthOwnerLabel` by id | −18,571 (−0.40%) | floor |
| `93c5eafa` | R3 resolver core: `resolveAtomType`/`resolveNamedTypeId`/`descendNamedId`/`definingSiteForId`/`nameIsInUnitReftypeId`, `recordResolvedName nameId:`, the alias resolvers by id | −9,452 (−0.20%) | floor |
| `be8645d6` | R3b `declareCanonicalTypeId` / `linkDeclTypeId`: the mint funnel by id | −10,248 (−0.22%) | floor |
| `56e15e82` | R4 constant evaluator, unresolved-name probe, method-walk triggers by id | −7,569 (−0.16%) | floor |

Net over the arc, identical input: **−92,567 allocations (−1.96%)** off `49b9b267` (4,718,425 → 4,625,858 on the self-compile); the
name-copy family fell from 148,930 blocks (3.2%) to 50,835 (1.1%).

**What made it cheap, again.** Every text-keyed API in these cores began
with a find (`nameIdOf`, `poolFind`, `pool.find`): an id twin is the body
after that line, and the text form becomes a one-line wrapper (interning a
spelling it does not find where the body used to mint by text). Flip the
producer, let the typechecker enumerate; bind the id BEFORE a call that also
takes `:ast` (a `poolFind` inside the argument list is refused as `ast.names`
inside `ast`); a spelling that must be parsed is read through a block-scoped
borrow, one that must be minted is copied once in the mint leg; a label is
built from a borrow or from the caller's text, never both (the A002 ratchet
counts composed interpolations). `make style-lint` (the `--full`,
typecheck-tier lint) kept catching what the plain `bin/zl lint` did not: L012
for a definition whose last caller the wave converted -- run it as the last
step before `make ci`.

**Left, and why.** 50,835 blocks (1.1%) remain, all small and text-native or per-site:
`constOfNamedBase`'s two text-keyed legs (6.7k: the arm read and the
unit-qualified arm, which take `typeName`/`armName` text), `atomName`'s
label consumers (6.2k: `checkCaseClause`, `collectMonoRefArgs`, which store
or label with the text), `baseTypeName` (4.9k) and `paramTypeBaseName`
(2.7k) consumers, `resolveFunctionParam` (3.5k, `ptnT` → `walkLookupTyperef
base:`), `emitMatchStmt` (3.3k, arm names into an owning list),
`recordUnitConformerContexts` (2.5k), `resolveDemandedDecl` (2.3k, `resolveDef
defName:`), `stampUnitMonoMembers` (2.2k, template labels), and a tail of
sites under 2k. Each is one more id twin or a label that must be text; none
is a family.

<a id="r-b7fccae6"></a>
### Per-site copies by id

**2026-09-03 · `f213ab57`..`b7fccae6`**

The residue of *Resolution by id*: 50,835 name-text copies (1.1% of
allocations) left at per-site readers, each classified convert or leave by
what the text was FOR. A copy that is compared, looked up or tested for
presence converts; one that is spelled into a label, a C type name or a
registry row stays. Each phase is a commit A/B'd on identical input against
the commit before it (`out/zc-perf`, gcc -O1, `perf stat -r 7` + valgrind);
allocations are the metric, instruction deltas under ~20M are "floor".

| commit | phase | allocations | instructions |
|---|---|---|---|
| `f213ab57` | S1 constants: `constArmRead typeNameId: armNameId:`, `evalConstArmViaUnit nameCountId:`; the platform seeder is the one text-born caller | −7,873 (−0.17%) | floor |
| `23bc8d98` | S2 parameter shells and type-head probes: the value-parameter leg by id (the generic leg keeps its copy for the registry), `paramTypeBaseNameId`/`baseTypeNameId` as the definitions with text copies, `fnReturnBaseNameIdOf`, `forFactoryTid fbnId:`, `nameClaimedByScopeId` (text form retired) | −6,541 (−0.14%) | floor |
| `98cf3fa8` | S3 emitter: match-else narrowing compares arm ids; `sumArmNames` routes through `sumArmNameIds`; C byte-identical | −39,823 (−0.86%) | floor |
| `b7fccae6` | S4 field heads and return heads: `fieldHeadNameId`/`genericApplHeadId` down the class-field chain into `fieldTypeNeedsCleanup nameId:`; the emitter's presence and `this` tests read `fnReturnBaseNameIdOf`; C byte-identical | −1,789 (−0.04%) | floor |

Net over the arc: **−59,368 (−1.28%) allocations** off `56e15e82` (4,625,858 → 4,566,490 on
the self-compile); the name-copy family fell from 50,835 blocks (1.1%) to
30,712 blocks (0.7%).

**What the census was for.** The biggest single step was not in the
typechecker: `emitMatchStmt` built two owning `List String` per match
statement with an else (the covered arms and the sum's arm universe) to find
the residual arm by string compare -- 3.3k blocks in the census, 39.8k
allocations once the lists, their growth and the per-arm copies are counted.
A census names the FUNCTION that copies; the A/B tells what the copy costs.
The other lesson is the seeder: `seedPlatformArm` classifies the host, the
target triple and the flags -- text-born values -- so the text-to-id step
lives there and the reader downstream never sees text again.

**Left, and why.** 30,712 blocks blocks (0.7%) remain, all text-native or stores:
`checkCaseClause` (3.3k, arm labels), `fnSignature` (3.1k, the return head
spelled through `cTypeOf name:`), `recordUnitConformerContexts` (2.5k),
`resolveDemandedDecl` (2.3k, `resolveDef defName:`), `stampUnitMonoMembers`
(2.2k, template labels), `collectMonoRefArgs` (1.9k, labels), the mono parts
stored as text in a record (`resolveMonoRefParts`, `resolveDottedArgTid`,
`recordTyperefArgs`, 3.8k), `paramTypeBaseName`'s suffix builders (0.9k),
`emitRecordMethods` (0.8k, names into owning lists) and a tail of sites under
0.7k each. Each is a label, a store or a spelling; none is a family.
