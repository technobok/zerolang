# Compiler performance baseline

Hand-maintained ground truth for compiler performance work. Append a row per
landed perf workstream, measured with the commands below, in the same commit
that lands the change. Machine context matters — record it per row when it
changes.

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

## Baseline table

Machine: 24-core, gcc 15.2.0, glibc 2.43, Linux. Wall = best of 5.
"allocs" = memcheck total heap blocks for one self-compile. "LOC" = `wc -l` of
`src/*.z` + `lib/system/**/*.z` (the self-hosted compiler + relocated front-end/stdlib,
including system's subunit files under `lib/system/system/`).
LOC tracking starts at the 2026-07-23 row; earlier rows are "—" (not back-measured).

| date | commit | change | wall (mimalloc) | wall (glibc) | peak RSS (mi/glibc) | phases (parse/check/emit ms) | allocs | bytes churned | make test | LOC |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07-17 | 4f10844 | GROUND (post emitter-completeness arc) | 0.77s | 0.89s | 125MB / 122MB | 86 / 247 / 423 (total 756) | 23,625,212 | 772MB | 11.0s | — |
| 2026-07-17 | 3bcaba2 | W1: id-space queries, regNameIs scans, mainBodyMentions hoist, childOfWalk fast path, Map.getv | 0.69s | — | 126MB / — | 92 / 234 / 354 (total 680) | 11,210,996 | 546MB | — | — |
| 2026-07-17 | 1b7c6d0 | W2: emitter buffer reserves, Map/Set/List capacity:, stamp-map pre-size | 0.69s | 0.75s | 117MB / 116MB | 84 / 242 / 351 (total 677) | 11,217,951 | 527MB | 10.7s | — |
| 2026-07-17 | fbb3426 | capacity-inference fix + value-position capacity threading + right-sized stamp maps (the 1b7c6d0 pre-size was inert: value-position constructions dropped capacity) | 0.68s | — | 118MB / — | — | 11,222,033 | 501MB | — | — |
| 2026-07-17 | 81b9297 | A: tokenizer source-span token text (goldens byte-identical) | 0.67s | — | 118MB / — | — | 11,010,123 | 492MB | — | — |
| 2026-07-17 | ab2d177 | B: move-on-advance + parser payload moves (+ D: ctor-arg move gap proved stale, pinned in corpus) | 0.65s | — | 119MB / — | — | 10,597,979 | 489MB | — | — |
| 2026-07-17 | 7f8524f | C: child-edge name interning (pool + id-keyed buckets) | 0.65s | — | 117MB / — | — | 10,093,238 | 482MB | — | — |
| 2026-07-17 | 297f741 | A1: names-as-nodes interning (AtomId/LabelValue name -> u32 nameentry ref; hot readers on scoped row views; constVals probes on getv) | 0.66s | — | 117MB / — | 87 / 226 / 340 (total 653) | 10,161,794 | 485MB | — | — |
| 2026-07-17 | 8727875 | C1: Ast carrier threaded (~570 sigs; ast.nodes indirection; ARCHITECTURE landing — B3-as-perf stays shelved) + StringView.hash native + unconditional z_hash.inc | 0.67s | — | 118MB / — | — | 10,280,730 | 487MB | — | — |
| 2026-07-17 | dbd0899 | C2: names -> Ast.names StringPool; nameentry arm deleted; synth dedup (ref==ref sound); hot readers borrow pooled text | 0.66s | — | 116MB / — | — | 10,235,386 | 484MB | — | — |
| 2026-07-17 | 17d8ba4 | C3 (a units, b fileSegs, c edge names, d well-known ids): tree-scoped state consolidated on the carrier; ZTyping's private edge-name pool deleted -- ZTypeChild.nameId IS the Ast.names id, member resolution int-keyed where provenance is certain (ARCHITECTURE landing; +0.5% allocs = edgeText "" fillers + edgeNameId cache) | 0.66s | — | 117MB / — | — | 10,292,415 | 486MB | — | — |
| 2026-07-17 | c29bf3d | D1-D4 single pool: wk member ids 5..31, id-keyed lookups where ids in hand, ZTyping edgeText+edgeNameId DELETED (no name text outside Ast.names; StringPool.find read-only probe). +1.8% allocs = the third nameIds out-list on recFieldLists/variantArms/protoChildMethods call sites (superseded by the registry-ids arc) | 0.67s | — | 116MB / — | — | 10,473,463 | 494MB | — | — |
| 2026-07-18 | 6eee916 | D5 (a: namedoperation label -> pool id, last owned Option String on the AST gone, 7 label helpers collapse to nameTextCopy/nameTextEq; b: text-taking setChild deleted -- analysis interns ONLY at 6 explicit ast.internString mint sites, else id-keyed setChildId via wk consts / in-hand ids / poolFind read-probe). Pure refactor: 129/129 examples behaviourally identical. -29k allocs (per-label Option removed) | 0.66s | — | 115MB / — | — | 10,444,134 | 493MB | — | — |
| 2026-07-18 | (E1 seeded) | E1: id-key resolveTypeIdByName -- poolFind the name ONCE, then id-keyed stages (childOfId); the composed-key fallback materialises text lazily. The same name was find()-ed ~9+N times per resolve (once per stage + once per unit in the cross-unit loop); now once per resolve. Pure refactor (129/129 examples identical). -354k allocs (-3.4%) vs D5 | 0.64s | — | 113MB / — | — | 10,089,619 | 481MB | — | — |
| 2026-07-18 | (E2a seeded) | E2a: cache the unit list. resolveTypeIdByNameId rebuilt utids9 (List u64) + the fallback uks9 (List String, every unit key copied) on EVERY cross-unit resolve, iterating unitNameTid each time. unitNameTid is typecheck-stable, so snapshot it once per Ctx (ensureUnitCache -> ctx.unitTidsC/unitKeysC). Pure refactor (129/129 identical). -533k allocs (-5.3%) vs E1; -8.5% cumulative from D5 | 0.64s | — | 111MB / — | — | 9,556,604 | 453MB | — | — |
| 2026-07-21 | 1426aaa | HEAD re-measure (+72 feature commits since E2a, incl. the completion sweep): the accumulated regression the perf arc attributes | 0.71s | — | 125MB / — | 90 / 277 / 350 (total 715) | 10,520,665 | 510MB | 13.4s | — |
| 2026-07-21 | (A1 seeded) | A1: walkedMethodOwners keys qualified "unit.name" everywhere. The sweep probed bare names while depWalkUnit marked qualified, so it re-walked every dep/stdlib type the dep walks had already checked, and same-named types across units masked each other's sweep entry; + mainMemberIndex gated to the unit it indexes (the qualified probe exposed a latent cross-unit index clash) | 0.69s | — | 122MB / — | 92 / 250 / 340 (total 690) | 10,203,138 | 491MB | 13.2s | — |
| 2026-07-22 | (A2a seeded) | A2a: fnAutoCallable iterates childIndex rows by pool id (wkThis / poolFind'd thisParamName / hasChildDefaultId are all id-keyed) instead of materializing every param name String through dataFieldNames per dotted-reference probe. dhat attribution: the post-E2a alloc growth is the ctor-validation arc (walkCallArgsHoist 698k blk incl. this chain; emitter isCtorOwnerCall 272k blk) + the sweep/dep body walks (~235k blk, paid-for correctness); printableInterpTid measured at only 51k blk / 0.5% — memo not warranted; gpNames9 sub-1% — skipped as predicted | 0.67s | — | 122MB / — | 85 / 240 / 340 (total 665) | 9,916,042 | 472MB | — | — |
| 2026-07-22 | (A2b seeded) | A2b: resolveTypeIdByNameId memoized on Ctx (nameId -> tid, misses cached as 0). isCtorOwnerCall probes every emitted call with base names that are usually VALUE names: each guaranteed miss walked all stages incl. the composed-key loop (one "unit.name" String per unit). The typed model is frozen during emission so per-name resolution is emit-stable; misses dominate, so caching 0 is the whole win. Emit -17%; regression vs E2a fully closed (allocs now 9.37M < 9.56M, wall 0.62s < 0.64s) | 0.62s | — | 122MB / — | 86 / 239 / 282 (total 610) | 9,368,977 | 457MB | — | — |
| 2026-07-23 | 5eccf67 | redesign HEAD re-baseline: name/Decl/Type identity + generic-metadata composite-key arc (first row since A2b -- the +0.8M allocs / +0.04s vs A2b is the whole redesign's Decl-tree build + probes, not one change) | 0.66s | — | 129MB / — | 96 / 254 / 312 (total 662) | 10,163,283 | 469MB | — | 80,396 |
| 2026-07-24 | 6f19e46 | mangled-mono-name namespace retirement (arc P4, `8640c29`..`6f19e46`): delete the synthetic `unit.<mangled>`/`system.<mangled>` namespace; the emitter no longer resolves io/net mono canon stems or shells by an O(nextTypeId) name scan -- `ioCanonCname` memoized on Ctx (P4.6, dissolves the ground census's single largest chain, ~5.85M blk) and `shellResolveByName`'s all-units brute-name scan deleted (P4.7, the delete's ~48k-alloc regression, DHAT-localized). Emit 312->217ms. | 0.55s | 0.63s | 120MB / 112MB | 87 / 246 / 217 (total 550) | 9,665,936 | 458MB | 13.5s | 80,358 |
| 2026-07-26 | ada99da | child-edge table retirement (arc Q2, `3dca56c`..`ada99da`, 72 commits): the Decl tree becomes the single member index -- visibility, member metadata, member order and member enumeration all answer from declarations; `typeChild`/`childIndex`/`childNameId`/`childTypeById`/`ZTypeChild` and the 8 metadata sidecars are deleted. **Regression owned, not attributed elsewhere**: typecheck 246->306ms, allocs +1.1M, bytes churned +140MB, LOC +972. The edge tables were flat rows read in place; the Decl helpers replacing them allocate per call -- `declMemberList`/`declChildRows` 2 Lists each (the emitter's `recFieldLists` has 33 callers), `declMemberCount` 4 to return a length (2 call sites are inside `walkCallArgs`' per-argument loop), `declMoveChildToEnd` 3 per first-sight member -- and `childOfId`'s pre-index fallback scans a 2-entry buffer 105,592x per self-compile for 0 hits. | 0.61s | 0.70s | 122MB / 114MB | 90 / 306 / 221 (total 617) | 10,771,277 | 598MB | 14.0s | 81,330 |
| 2026-07-26 | 8551e35 | post-Q2 cleanup (`e09f96a`..`8551e35`, 18 commits): retire the migration instrumentation (13 counters + q2SpikeVerify); delete the Decl/ZTyping/ZType state nothing reads; collapse the two `childOf` vocabularies, the two member enumerators, the two field filters, the three sum-arm enumerations and the four zls param walks into one each; stop allocating row lists to answer scalar questions (`declMemberCount` took FOUR, `declMoveChildToEnd` THREE per first-typed member); drop the derived `type_children` dump table; delete 21 parameters kept alive only by a bare-identifier statement. **The allocation regression closes, the wall one does not**: allocs -8.1% and bytes churned -17%, but typecheck stays ~313ms against 246ms pre-arc. Under mimalloc these allocations were not what cost the time -- the remaining lever is Map probing (see the 2026-07-17 cache census), not allocation. DHAT after: `String_copy` 35.9%, `checkCall` 12.2%, the member-enumeration family down from 896k blocks (8.4%) to 232k (2.3%). | 0.63s | 0.70s | 119MB / 113MB | 87 / 313 / 232 (total 632) | 9,903,328 | 498MB | 14.0s | 80,833 |
| 2026-07-27 | fb326d9 | borrowed-element `get` (`d62395e`..`fb326d9`, 4 commits): `List.get`/`ListView.get` were declared `out of.borrow` over a `this.lock` receiver but lowered to `return _this->data[_idx]` for EVERY element type -- a dropped write for a class element, and a use-after-free when the element carries an inline container header. `get` moves out of the two runtime templates into `emitColGet`; 40 of 66 monos now return a pointer (19 user classes + String, List and ListView alike), unions stay by value ({tag, void* data} already shares its payload) and valtypes are self-contained. Call sites deref, so every value context stays value-shaped; a binding takes the pointer and aliases the name to the deref. **Correctness arc, not a perf one** -- this row is the checkpoint the next row is measured against. | 0.63s | -- | 118MB / -- | 104 / 302 / 231 (total 637) | 10,004,638 | 496MB | -- | 81,088 |
| 2026-07-28 | 929aede | `ZTyping.decls` is a `List Decl`, not a `Map u64 Decl`: DeclIds are minted dense 1..N by `declNew` (the sole writer) and never removed, so keying a hash map on a dense monotonic counter was an array with extra steps -- the row IS the index and `decls.get i: (id - 1)` is exact by construction. `declNew` becomes an append returning the new length; 58 read sites converted by script + 8 by hand across ztyping/ztypecheck/zsqldump/zsource/zls/zemitterc, each dropping an `Option` match for a `declValid` guard (0, the no-declaration sentinel, or past the end are the only misses). Reads BORROW the row rather than copying it out -- the preceding row is what makes that safe, since member writers still land in the stored Decl. **typecheck 302->270ms, emit 231->212ms, wall -14%, LOC -123.** Allocation COUNT rises 0.1%: a List grows by doubling where the compact dict grew an index. Landmine: `declValid` is a ZTyping method, so its receiver locks the WHOLE typing -- E0200 under a live `refDecl` iterator, so `zsource` spells the guard inline. | 0.54s | -- | 120MB / -- | 101 / 270 / 212 (total 583) | 10,015,753 | 492MB | -- | 80,965 |
| 2026-07-28 | 31f2de6 | node ids become one monotonic space (`df3b348`..`31f2de6`, 3 commits): the id space carried a classifier in its numeric range -- `0x20000000` meant generator lowering, `0x40000000` meant post-parse synthesis -- and `zsource.z` read the range back as a proxy for "is this node tabled", a property of the node rather than of its number. Synthesis minted from a disjoint range because it could not index the master table before a node existed, and `tableAppendRebase` renumbered the root on commit to undo the mint. The parser never needed either: it reserves a slot, mints the id of the slot it reserved, and fills it later. `zast.tableReserve` gives synthesis the same mechanism, so a synthesized node carries its final id from construction and commits with `tableAdd`; `tableAppendRebase`, both ranges, and the `GenIds` thread (35 signatures, 158 call sites, kept alive only to reach a counter) are deleted. **Architecture, not perf: wall and allocations are flat** (same-session pre-arc re-measure: 0.55s / 121MB / 10.02M allocs). The point is the downstream unblock -- `nodeType` had 30,846 range-valued keys on a self-compile and now has zero, none past the last node id, 57.3% dense, so `Map u64 u64` -> `List u64` no longer needs an overflow map. Cost: the hoist temps now reserve real slots, +30,313 rows on `ast.nodes` (+7.4%). Peak RSS fell 121->115MB against flat allocation volume, so that is residency, not volume -- measured, not attributed. | 0.55s | -- | 115MB / -- | 92 / 247 / 211 (total 550) | 10,007,998 | 493MB | -- | 80,844 |
| 2026-07-28 | (nodeType List) | `ZTyping.nodeType` is a `List u64` indexed by nodeid - 1, not a `Map u64 u64`: node ids are dense 1..N (the preceding row is what made that true), so the stamp IS the slot. The map allocated `capacity: nodeEst` = 524,288 slots at 40B each -- 8B index + a 32B `{alive,hash,key,value}` entry -- whether occupied or not, against 253,338 live stamps; the List is 8B per node, all of it live. Landed in three commits so each had its own oracle: (1) all 43 writers funnel through `nodeTypeSet`, which records neither a 0 node id nor a 0 type id -- 0 is the `notype` sentinel and every reader already treated absent as 0, but `checkAtomid` stamped it unguarded for generic-unit template params, so 13 rows really existed; (2) all 56 readers funnel through `nodeTypeAt` returning a plain `u64`, dropping 25 `match … case some/none` blocks and `walkExprStamps`' `optionval` return; (3) the flip itself -- two accessor signatures, the field, the constructor, and three `iterateItems` loops that become index scans. **wall 0.55->0.50s, typecheck 247->226ms, emit 211->195ms, peak RSS 117.7->108.8MB (-8.9MB, predicted -9MB), bytes churned -3.4%, LOC -112.** The instruction count moved only -1.21% (6.618->6.538G, ±0.08%) while cycles fell 6.6% and cache-misses 8.7%: **the win is locality, not instruction count** -- which is why the 2026-07-27 probe-reduction attempt measured flat and this did not. Allocation COUNT rises 0.1%: a List grows 1.5x where the compact dict doubled. Verified by SQL-dump diff, same source through both compilers: the typing model is identical across all 148 examples and the 997,104-row self-compile; only `typed_nodes` row ORDER changes (hash -> nodeid ascending), and no golden carries it. Note `perf stat -r 5` instructions (±0.08%) resolves changes that `make perf` wall (±1.3%) cannot. | 0.50s | -- | 109MB / -- | 93 / 226 / 195 (total 514) | 10,019,704 | 476MB | -- | 80,785 |
| 2026-07-28 | (ctl placeholder) | `break` / `continue` share ONE unregistered type id instead of minting a fresh one each. `ZTypeRegistry` has two minters on one counter: `newType` files a `ZType`, `allocType` returns a bare number that never becomes a type. `checkFor`/`checkDo` called `allocType` purely because `st.define` needs a `ztypeId` slot for names that have no type -- two ids per `for`, one per `do`, again on every generic re-walk. **Measured: 2,214 of the 2,276 holes in the type-id space (97%) were these; the out-less-auto-call placeholders everyone assumes dominate are 62 (3%).** So 36% of the id space was `break`/`continue`. Safe because nothing reads the id: every consumer of those names dispatches on the NAME (`zemitterc.z:9450`/`:9471`, `ztypecheck.z:20107-20115`), and not one of the 2,214 ids ever appears in `typed_nodes` -- they never become a node stamp, so type equality (`tid ==`) cannot reach them. **Density 62.7% -> 98.4%, `nextTypeId` 6,094 -> 3,881**, so the ~35 `for tid < nextTypeId` registry scans are 36% shorter. **instructions -0.71% (6.538->6.492G, ±0.07%), cycles -1.26%, cache-misses -2.76%; wall and RSS flat.** A small CPU win; the point is the density, which flips the `typeById` -> `List ZType` arithmetic from losing (1.59MB vs 1.23MB map resident) to winning (~1.09MB). Type ids renumber downward, so raw SQL dumps move for the 18 examples with walked loops -- verified pure uniform renumbering: identical type name+kind sets in identical order across all 148, same row counts, and the 19 `.canon` goldens are untouched because canon renders an unregistered type as `'?'`. | 0.51s | -- | 109MB / -- | 104 / 229 / 193 (total 526) | 10,008,524 | 476MB | -- | 80,782 |
| 2026-07-28 | (typeById List) | `ZTypeRegistry.typeById` is a `List ZType` indexed by tid, not a `Map u64 ZType`. Landed in seven commits: an out-less method's auto-call resolves to the canonical `null` type instead of a fresh anonymous id (the last 62 holes, and the semantics the site's own comment already claimed); five spellings of "a type's registered name" collapse to one; then ~86 external raw `typeById.get` sites route through 17 registry accessors (`genericOriginOf`, `nameOf`/`nameIs`, `defOf`, `returnTypeOf`, `thisParamNameOf`/`hasThisParamName`/`thisParamNameIs`, `isGenericOf`, `isNativeOf`, `isValtypeOf`/`isReftypeOf`, `isBoxOf`, `isRuntimeIndexedOf`, `typeValid`); then the flip. Index is `tid` DIRECTLY -- type ids are 0-based (id 0 is `notype`, a real row), unlike `decls`/`nodeType` which are 1-based and use `id - 1`. **wall 0.51->0.49s, parse 104->84ms, typecheck 229->213ms, emit 193->182ms, instructions -6.9% (6.505->6.056G, ±0.06%), cycles -5%, cache-misses -4%, RSS 109->107MB, LOC -648.** The `Map_u64_ZType` find+get pair was 3.43% of cycles; `List_ZType_get` is **0.65%**. **I predicted 1.5-2.5% cycles and under-called it by 2x**: the reasoning was that the working set is only ~1.1MB and already L2-resident, so there was no locality ripple to win -- true, but it missed that a bounds-test-plus-index is small enough for gcc to inline at -O1 where a hash-plus-probe is not, so the accessor calls the funnel added disappear too. The funnel alone measured +0.2% instructions; the flip repaid that and much more. One hole remains (`ctlPlaceholder`), so `allocType` appends a `holeType` row marked by `typeId 0` -- a real row carries its own index and index 0 is always registered, which is the whole validity test. Only `canonTypeName` (must keep rendering `'?'`, or 18 golden rows flip) and `appendTypes` need it. Verified by SQL-dump diff, same source through both compilers: identical across all 148 examples and the 994,125-row self-compile. | 0.49s | -- | 107MB / -- | 84 / 213 / 182 (total 479) | 9,993,144 | 473MB | -- | 80,134 |
| 2026-07-28 | 2d8e831 | 28 of `monoOriginName`'s 33 call sites become `originTidOf … > 0`. The function returns an owned `String` and **every** path allocates -- the miss path builds an empty one -- but 28 sites only ever asked whether the result was non-empty, which is the id form's `> 0` with a byte-identical genericParam guard. **Measured against a same-session `af8506d` binary built with identical flags** (the committed seed is -O0 / no-mimalloc and is NOT comparable): **allocs 9,995,184 -> 9,919,102 (-76,082, -0.76%)**, bytes churned -117,594 (-0.02%), **instructions 6,069.9M -> 6,053.0M (-0.28%**, two interleaved `-r 7` rounds, ±0.07%), cycles -0.38% (inside its own ±0.3% band -- call it flat), **wall and RSS flat**. **THE LESSON, and it is the one that governs the `ZType.name` flip: I predicted 5-6k copies removed by counting only the five `for tid < nextTypeId` scans, and was wrong by 13x** -- 23 of the 28 sites sit in per-expression / per-statement emitter paths (`emitExpr`, `emitStmt`, `emitCallValue`, `emitInstanceMethodCall`), not in registry scans. So this is a clean natural experiment: **a 0.76% allocation cut moved the wall not at all.** That retires the allocation argument for `ZType.name -> nameId` entirely -- that flip is allocation-NEUTRAL on readers by construction (`poolTextCopy` is exactly one `String_copy`, the same as `got.name.copy`), so it can only ever win on the 232 -> 208 B row shrink. Oracle: **emitted C byte-identical** to the `af8506d` seed across all 148 examples and the self-compile -- note a SQL-dump diff would NOT have exercised this at all, the change being emitter-only and downstream of the dumped typecheck model. `emitter-guard`'s `monoOriginName` baseline 37 -> 8. Folded in (`b392b2f`): the dead `optStrSome` and `appendTypesCanon`'s unused `pool` param, both `af8506d` debris that had been failing style-lint's L012/L014 tier. | 0.48s | -- | 107MB / -- | 88 / 213 / 180 (total 481) | 9,919,102 | 473MB | -- | 80,143 |
| 2026-07-29 | (binding pool ids) | `AssignmentData` / `CaseClauseData` / `WithData` `.name` become `Ast.names` pool ids, so binding-side payloads carry ids like the reference side already did. The extern walk's `externs` and `scope` become `List u32`: a scope membership test is an integer compare, `ewEmit` materialises text only for a name new to BOTH lists (guards reordered inScope -> dup -> `isValidUnitName`, all three early returns so the appended set and its order are unchanged), and `collectExterns` renders ids back to file names at its return -- the load driver stays text, because those names ARE file names. Work-list derived by flipping the three fields and compiling `zc` + `zl` + `zls` with the seed: **40 sites across 7 files, complete in ONE round, no cascade** (`zemitterc` 17, `zparser` 6, `zgenerator` 6, `zast` 5, `ztypecheck` 4, `zsource` 1, `zls` 1). **The typechecker misses exactly one reader shape: `\{n.name}` in an interpolation typechecks against `u32` as happily as against `String`** -- six such sites in `zast.printNode` / `printNodeCanonical` would have silently printed ids; only the `parser_golden/*.ast` differential goldens would have caught them. Everything else is `.stringview` / `.length` / `.copy` / `== <StringView>`, all E0100 on `u32`. **Measured against a same-session `8f494d7` binary built with identical flags: allocs 9,919,102 -> 9,847,155 (-71,947, -0.73%)**, bytes churned -0.3%, **instructions -0.40%**, cycles -0.36% (inside its own ±0.5% band), **cache-misses -3.1%**, wall and RSS flat -- two interleaved `-r 7` rounds. `List_String_contains` **2.31% -> 0.13%** (the residual is `coreNames`/`defNames`/`enqueueUnit`, correctly text) and `StringView_eq` **4.65% -> 3.37%**, but `ewEmit` surfaces at **1.82%** with the now-inlined `List_u32_contains`: **the linear scan, not the comparison, was the cost.** The architecture is the win; the 3.18% hypothesis is retired. Oracle, all three legs: emitted C byte-identical across all 148 examples and the `zc`/`zl`/`zls` self-compiles; `dump --canon` byte-identical incl. the 146,532-row self-compile; and raw `--dump-sql` byte-identical too, incl. the 995,092-row self-compile -- the predicted `call_generic_binding` renumbering (its key embeds a pool id) **did not materialise**: every binding name this corpus interns at its binding was already in the pool. Folded in: `assignmentLocalName` answers with an id (its only consumer immediately `poolFind`-ed the text back into one), and `emitMatchStmt`'s per-clause `cnode9.name.stringview == cn0.stringview` hoists to one `poolFind` outside the loop. | 0.47s | -- | 105MB / -- | 88 / 213 / 180 (total 481) | 9,847,155 | 472MB | -- | 80,169 |
| 2026-07-29 | (entry pool ids) | `ZEntry.name` and `ZEntryDumpRow.name` become pool ids, and the 22 `ZSymbolTable` methods that took a `name: StringView` take a `nameId: u32`, so **all 12 backwards frame walks in `zenv.z` are integer compares**. `String`-keyed sets in the same family follow: `pop`'s `localDefs`/`mNames`, `allNames`, `getLiveOwnedVars` and the if-arm `liveBefore`/`takenAcross` are `Set u32` / `List u32`; `lookupVarNameById` returns an id (0 = none) and only `formatLockHolder` renders it. Text survives at exactly three edges, each taking the pool rather than the whole carrier: `defineVar` (which owns the `mangleVarName` chokepoint), the lock diagnostics, and the SQL dump's `entry` rows. Callers that hold text resolve it **once** through a new `nameIdOf` boundary helper -- 62 of the 94 `ztypecheck` sites; the ones that hold an id pass it straight through, which is what deletes `checkAssignment`/`checkWith`'s three `poolTextCopy` locals from the row above. `poolFind` at query sites, `poolSet` at the 12 definition sites (a declared name must be findable after), and every scan early-returns on id 0 -- so an uninterned name cannot alias a 0-named entry. Work-list by compiling `zc` + `zl` + `zls`: 34 sites, then 130, then 96, then 7 as each signature wave landed -- four rounds, unlike 2a's one, because this flip changes an API rather than a leaf field. `\{e0.name}` in `ztypes.z`'s smoke is the same silent-interpolation shape 2a hit (now prints `nameId=`; golden updated in this commit). **Measured against a same-session `1e7f0c4` binary, identical flags, two interleaved `-r 7` rounds: allocs 9,847,155 -> 9,669,658 (-177,497, -1.80%)**, bytes churned -2.5% (472 -> 460MB), **cycles -1.18%**, instructions -0.29%, cache-misses flat, **wall and RSS flat** (phase best-of-5 96/212/178 -> 93/212/175). `StringView_eq` **3.28% -> 2.08%**, `List_ZEntry_get` **1.04% -> 0.53%**, and `StringPool_probeSlot` **1.68% -> 1.28%** -- the boundary probes cost less than the text round-trips they replaced. Same lesson as 2a: the frame walk is still O(depth), so the win is the allocation and the compare, not the scan. Oracle, all three legs byte-identical: emitted C over all 148 examples and the `zc`/`zl`/`zls` self-compiles, `dump --canon` incl. the 146,949-row self-compile, raw `--dump-sql` incl. the 997,988-row self-compile. Note the dump's `entry` section had to stay LAST: `appendSymbolTable` needs the pool, which is only reachable inside `dumpSql`'s `case program` guard, so it gets its own guard at the original call position rather than moving up into the existing one. | 0.48s | -- | 105MB / -- | 93 / 212 / 175 (total 482) | 9,669,658 | 460MB | -- | 80,281 |
| 2026-07-29 | (demand marks on the Decl) | The demand resolver's done/grey markers stop being `Set String` probed with a freshly interpolated `"{unit}.{name}"`: `ZTyping.definedKeys` and `resolving` are **deleted**, and the state lives on the `Decl` the walk already reaches (`Decl.defined` / `Decl.resolving`, two bools that land in existing padding). A probe is now `unitRootByName` -> `declFindChild` -> read a bit, via `declOfUnitDef`, which already existed. **11 of the 87 composite string keys are gone (87 -> 76), and two `Set String` tables with them.** Callers that hold the definition's name id (`bmNode9.name`, three of the four done-mark probes) skip the pool entirely through a new `declOfUnitDefId`. **The pre-index window is real and the plan called it**: generator lowering resolves `system.Iterator` / `system.optionval` before `indexUnitDefs` builds the unit roots' children, so those two definitions have no `Decl` to carry the mark -- losing it re-resolved them and minted one extra type id, which showed up as a uniform +1 type-id shift in the emitted C of exactly the 7 `generator_*` examples. Bridged by `ZTyping.preIndexDefined`, a `Set u64` keyed by `(unitNameId << 32) | defNameId` -- an id pair, never a synthesized name -- consulted only when the Decl lookup misses. Verified that `indexDeclDef` never finds an existing child (so nothing duplicates today) before choosing the bridge over minting the Decl early. **Measured against a same-session `812f446` binary, identical flags, two interleaved `-r 7` rounds: allocs 9,669,658 -> 9,663,488 (-6,170), bytes churned -907KB, instructions +0.03%, cycles +0.12%, wall and RSS flat.** A small allocation win and dead-flat CPU -- the point of the commit is that a two-segment name lookup walks the tree instead of building a key. Oracle, all three legs byte-identical after the bridge: emitted C over all 148 examples and the `zc`/`zl`/`zls` self-compiles, `dump --canon` incl. the 147,067-row self-compile, raw `--dump-sql` incl. the 998,960-row self-compile. | 0.48s | -- | 107MB / -- | 101 / 217 / 178 (total 496) | 9,663,488 | 459MB | -- | 80,368 |
| 2026-07-29 | (unit indexes by id) | `reg.unitNameTid` and `ty.unitRootByName` are keyed by the unit's interned name id, not its text; `setUnitName` / `isUnitName` take an id, so `ztypes` never sees a name. The twelve `isUnitName` sites read the id **straight off the atom node** through new `dottedBaseAtomId` / `outerBaseAtomId` / `atomNameIdOf` siblings, deleting a `dottedBaseAtom`/`atomName` text materialisation at each (the L013 lint found the dead locals). `ty.systemUnitRoot` caches the system unit's root Decl so the per-reference member probe stops re-hashing `"system"`, via a `declProbeUnitChildRoot` split. ~20 `key: X.string` materialisations at probe sites are gone. **This row is a TRADE, and it is recorded because the first attempt at it was rejected on the wrong axis.** Measured against a same-session `f2e874d` binary, identical flags, valgrind on identical input: **allocations 9,691,456 -> 9,517,956 (-173,500, -1.79%)**, bytes churned -1.25MB, **RSS and wall flat**. But **instructions +3.09% and cycles +1.38%** (two interleaved `-r 7` rounds; the cycles figure is outside its own ±0.4% band, so it is real). The added cost is a `poolFind` at the ~40 probes whose callers still hold a `unitName: StringView` threaded down through **133 functions / 322 call sites** -- those are the sites the atom-id sourcing cannot reach, and they are the remaining `strings-in-the-middle` debt. **The lesson: judging an id flip on instructions alone is the wrong test.** The first pass measured +3.4% instructions, concluded NO-GO by analogy with the `2c` assoc-list rejection, and reverted -- without ever running the allocation line. The allocation win here is the same magnitude as the `ZEntry` flip's (-1.80%). `2c` was genuinely negative on both axes; this is not. Oracle, all three legs byte-identical: emitted C over all 148 examples and the `zc`/`zl`/`zls` self-compiles, `dump --canon` incl. the 147,456-row self-compile, raw `--dump-sql`. | 0.49s | -- | 107MB / -- | -- | 9,517,956 | 459MB | -- | 80,406 |
| 2026-07-29 | (dotted type names) | A type's `name` is its OWN name (`create`, `==`, `describe`), never `owner.member`. `ZType` gains `ownerTid`, and `composeCname` builds the C symbol from the two parts, so **every emitted symbol is unchanged**. 24 composite interpolations at `reg.newType` mint sites are deleted (`"{defName}.create"` -> `name: "create" ownerTid: rid`). **Types carrying a dotted name: 1,893 -> 1,036**; composite string keys 76 -> 73. **The probe that shaped this**: of the 1,893, **950 had no Decl** (`defOf == 0`) -- synthesized members (`.==`, `.orPanic`) on mono owners (`List_u8`) -- so the owner is NOT recoverable from the Decl tree and has to live on the row as an id. Also found: the emitter kept a **second, independent cname builder** (`zemitterc.cnameOf`) that recomposed from a caller-supplied name; 44 of its sites now read the stored cname and the one site that deliberately pairs a BASE tid with a qualified member name takes the two parts explicitly. **Measured against a same-session `08aa47c` binary, identical flags: allocations 9,515,844 -> 9,471,881 (-43,963, -0.46%)**, bytes churned -1.33MB, **instructions -0.52%, cycles -0.80%**, wall and RSS flat -- every axis in the right direction, unlike the row above. Oracle: **emitted C byte-identical** across all 148 examples and the `zc`/`zl`/`zls` self-compiles; `dump --canon` and raw `--dump-sql` change by design (a member type's name row) and the 19 `.canon` goldens are re-baselined in this commit -- note the canon row already carried the owner in its first column (`'kind' | '!=' | 'kind.!='`), so the composite was redundant there too. **The consumer census that justified it**: the dotted name had exactly one reader, `cnameOf`, whose first act was to replace every `.` with `_`. Nothing looked a type up by it -- no `nameIs` against a composite, no dotted literal to `resolveTypeIdByName` -- because `declareCanonicalType` resolves through `declChildOf` on the Decl tree. The tid was always the identity; the name was decoration. | 0.51s | -- | 109MB / -- | -- | 9,471,881 | 457MB | -- | 80,398 |
| 2026-07-29 | (family C: no composite keys) | `declareCanonicalType` and `declOfFnDef` stop taking a qualified `Type.method` name apart: the first had an `indexOf`/`substring` splitter, the second a byte-scan for `.` (byte 46), and **ten call sites interpolated a composite purely so those two could split it back**. The owner now arrives as `ownerNameId: u32` (an interned pool id, 0 = free definition) and the member as its own bare name; both splitters are deleted, and `resolveMethodFn`/`resolveSpecFn` take `(ownerNameId, mname)` instead of `qname`. Every emitted symbol is unchanged **by construction**: `composeCname` is `z_t{tid}_{owner}_{name}` with `.` flattened to `_`, so the 8 dotted sites (`"{defName}.{mname}"` -> `name: mname ownerTid: rid`) and the 2 underscore-separated mangled-mono sites (`"{tmplName}_{fn}"`) both compose to the identical C identifier. **THE WIP WAS INCOMPLETE AND THIS IS THE LESSON**: `resolveFunction` guards Map.get/remove out of the partial-return rewrite with `if defName == "Map.get"`, so that they keep their value-kind wrapper projection (optionval / Option / OptionView by the value's nature). `defName` is now bare `get`, the compare silently stopped matching, `Map.get` on a valtype-valued map resolved to `Option i64`, and `Option`'s `Any.reftype` bound rejected it -- **E0400 on 7 examples and all three self-compiles**. A string compare against a composite literal typechecks *identically* before and after, so neither the typechecker nor the already-run "no composite reaches `declareCanonicalType`" probe could see it: the composite was consumed by a COMPARISON, not by the callee. Fixed by testing the owner explicitly (`poolTextEq ref: ownerNameId s: "Map"` + bare `get`/`remove`) -- the bare name alone over-matches, since List / ListView / ListIter all declare `get`. Found by diffing a name-keyed `getOrMintSpec` constraint trace between a same-session reference binary and this one; the trace showed four extra `tpl=Option pn=t arg=i64` checks appearing immediately after `Map key=String value=i64`. **Oracle -- emitted C is NO LONGER byte-identical and that substitution was a deliberate decision**: `Option` mints during `Map.get`'s signature walk instead of in a later batch, so type ids renumber. Precedent is the `(ctl placeholder)` row. Required leg: same type-symbol count and an identical id-stripped cname multiset (`LC_ALL=C sort` -- an unsorted diff reports a phantom set difference) across all 148 examples plus the `zc`/`zl`/`zls` self-compiles, same source through both compilers -- **151/151**. It then passed two strictly stronger legs that were worth running because no bare type id is emitted (the tid reaches the output only through `composeCname`; `runtimeIndexed` is a bool, not an index): the **whole file** with `z_t[0-9]+_` collapsed is byte-identical on all 151, and the ref->new id map is a proven **BIJECTION** (`zc`: 97,242 occurrences, 1,969 distinct ids, 0 violations; `zl` 1,521; `zls` 1,747). So this is pure uniform renumbering as a fact, not as an inference from a matching symbol set. `dump --canon` moves by design (a member type renders as `'add'`, not `'point.add'`) and 7 of the 19 goldens are re-baselined here -- **1,314 rows on both sides**, which is the cheap proof the Decl tree did not change shape after `linkDeclType` was gated to free definitions (it was a no-op for members anyway: it looks the name up as a UNIT child, and no unit has a child called `Type.method`). Dotted names in those goldens **52 -> 13**; the 13 survivors are `.create` synthesis and generic-unit mono members, unchanged from HEAD and the next slice of this arc. Composite `\{a}.\{b}` interpolations in `src/*.z` 79 -> 71. One user-facing diagnostic degrades and it is **accepted, not overlooked**: a bodyless-spec fn-pointer field now reports `'op' (type: op)` where it reported `(type: Proc.op)` -- the sole site where `(type: ...)` renders a synthesized member type rather than a real one. Restoring the qualification would give `ZType.ownerTid` its FIRST reader, and that field is slated to come off the row; the recovery path is `declIdOfType -> Decl.parent` (measured 857/857 at `1668a54`) once cname generation moves into the emitter. Measured against a same-session `1668a54` binary, identical flags, three interleaved `-r 7` rounds: **instructions -0.18%** (6,232.0 -> 6,220.7M, band +-0.07%, new below ref in every round), cycles and cache-misses **flat** (their between-round spread, 1.1-1.5%, swamps the delta), **allocs 9,471,744 -> 9,467,594 (-4,150)**, bytes churned -162KB, wall flat, **peak RSS 107.7 -> 106.2MB** on non-overlapping five-run ranges -- residency, from shorter `ZType.name` strings. A refactor row: the architecture is the point. | 0.48s | -- | 106MB / -- | 100 / 219 / 182 (total 501) | 9,467,594 | 457MB | -- | 80,476 |
| 2026-07-29 | (family C tail: `d557d0f`..) | Three commits closing out family C, each with its own oracle. **(1) The last three `reg.newType` sites that passed a composite name.** `fb56e92` converted ~24 mint sites to `name: <bare> ownerTid: <owner>` and missed the class `create` synthesis -- while converting the record, facet and protocol equivalents AND the `borrow` sibling twelve lines below it -- plus the generic-unit mono method shells in `attachMonoUnitFuncs` and `buildUnitMono`. Each already had its owner tid in scope, so **emitted C is BYTE-IDENTICAL across all 151** (148 examples + `zc`/`zl`/`zls`), not merely renumbered. Dotted names in the 19 `.canon` goldens **13 -> 0** at an unchanged **1,314 rows**; the owner was always already the row's first column, so the third was carrying it twice. `attachMonoUnitFuncs`' `monoName` falls out unused (L014) and goes with its call-site argument -- the redundant (id, text) pair collapsing to the id is the arc's thesis. `buildUnitMono`'s `qn` does NOT: it has a second consumer 170 lines away as `PendingUnitWalk.qualified`, which the lazy body walk passes as `defName`. That is a label, not a type name; it keeps its composite and a comment saying why. **(2) `ZType.ownerTid` deleted -- it was write-only.** `fb56e92` added it so `composeCname` could build `z_t{tid}_{owner}_{name}` from parts, but `composeCname` runs inside `newType` and reads the PARAMETER; the stored copy was **the only ZType field with zero `.ownerTid` reads in `src/*.z`**, checked field by field against the other twenty. So the checkpoint's "comes off the row when cname generation moves to the emitter" understated it -- nothing had to move first, and the emitter can reach the owner through `declIdOfType -> Decl.parent` (857/857 since `1668a54`). Row **232 -> 224B** across the ~35 `for tid < nextTypeId` scans; emitted C byte-identical, as it must be for a field nothing read. **(3) The `Map.get` skip is keyed on the declaration, not on a list of method names.** `resolveFunction` guards Map's get/remove out of the partial-return rewrite so they keep their value-kind wrapper projection, and `e22bc1a` spelled that guard as two method-name compares -- the shape that had just silently stopped matching and cost 7 examples and 3 self-compiles. **The root cause is that `partialReturnTid` is not a query**: it resolves through `resolveMonoRefParts` -> `getOrMintSpec`, so on `Map.get`'s declared `(Option of: value)` it REGISTERS Option over the value parameter, which respecializes to `Option i64` and trips `Option`'s `Any.reftype` bound. It cannot simply be deleted -- the same mint is WANTED for `(ListIter of: of).borrow`, which resolves to a partial spec so clones heal through respecialize. What actually differs is that get/getv/remove declare a **placeholder**: their real return is optionval for a valtype and Option/OptionView otherwise, which cannot be spelled in a return-type position (`match`-based generic dissection reaches bodies, not return types). So the guard now reads the declared return's base atom id and compares it against `Option`'s interned id -- one allocation-free compare via a new `returnWrapperBaseNameId` that resolves and mints nothing. **The tell that the old shape was wrong: `getv` declares the identical return and was NOT in the skip list**, being patched up downstream instead (one declaration shape, two mechanisms, selected by whether the method's OTHER params happen to be generic). Keying on the declaration covers it, and **removes exactly one wasted partial-spec mint per Map-using program** (highest tid 1001 -> 1000 on `maps`, same distinct type count) for an identical program. Oracle: emitted C differs on all 151 by **id renumbering only** -- verified by normalising every id form and re-diffing the whole file, 151/151. **That check needed three widenings to be true, and each was a blind spot in the previous one**: the leading-anchor `z_t[0-9]+_` strip missed tids embedded MID-symbol (`String_to_t973_str_4`), which missed the UPPERCASE macro form (`Z_T953_MAP_..._INDEX_EMPTY`), which missed the interned-temp counter (`_i868`). A partial normaliser reports phantom content changes -- 3, then 16, then 14, then 1, then 0 -- so **widen the normaliser before concluding a diff is semantic.** **Measured against a same-session `e22bc1a` binary, identical flags, two interleaved `-r 7` rounds: everything FLAT.** instructions 6,230.3 -> 6,222.9M (-0.12%, inside the reference's own 0.19% spread), cycles -0.33% and cache-misses -2.4% (both inside ~1-4% spreads), wall and RSS flat, bytes churned -3.7KB. Allocations **9,467,747 -> 9,472,314 (+4,567, +0.05%)**, which is the self-compile input growing: LOC 80,476 -> 80,505 (+29) for the new helper and its comments. **Commit 2's 232 -> 224B row shrink is the one I predicted would move something and it did not** -- same shape as the `2d8e831` lesson, recorded rather than quietly dropped. This is an architecture slice; the point is that no type in the model carries a qualified name any more. | 0.48s | -- | 106MB | 91 / 217 / 179 (total 487) | 9,472,314 | 457MB | -- | 80,505 |
| 2026-08-02 | affd7f4 | **OWNERSHIP v2 ARC, 44 commits `3d8e239`..`affd7f4`** -- the mutability axis (`take`/`hold`/`borrow`/`view`), `takex`, `.lock` and the `lockMode` arm RETIRED, readonly receivers/params/fields/locals, deep readonly, the readonly iterator (`iterate`/`iterateMut`) and `getMut`, ~2400 `.view` annotations, L4 and L020. **Measured against a same-session `4532d68` binary (the arc's parent) built with identical flags** -- NOT against the last recorded row, which predates the arc. **typecheck 218 -> 236ms (+8.3%) is the real cost and it is CONSISTENT** (5 runs each, ranges 217-222 vs 232-240, non-overlapping); parse and emit are FLAT (their ranges overlap -- a single sample had shown parse +30% and resampling killed it). **Peak RSS IMPROVES 6%.** The regression that wants attention is **bytes churned +18% on only +1.7% allocs** -- fewer, bigger allocations, not more of them. Wall +4% follows typecheck. LOC +3.3%. `make test` 15.3s vs 13.3s on 923 vs ~900 tests, i.e. flat per test. No perf work was attempted during the arc: this row is the honest accounting of what correctness cost, not a tuning result. | 0.51s | -- | 106MB / -- | 102 / 236 / 186 (total 517, medians of 5) | 9,692,057 | 548MB | 15.3s | 83,792 |
| 2026-08-03 | 34647e9e | **VAL/REF SPLIT ARC, 49 commits `946c1303`..`34647e9e`** -- family containers (ListRef/ListVal, SetRef/SetVal, MapRR/RV/VR/VV + the iterator/view/entry satellites), AnyRef/AnyVal/RefHashable/ValHashable bounds, union `(Box T)` arms with collapse-at-instantiation, reftype-only `Result`, `List`/`Set`/`Map` as core.z aliases. **Measured against a same-session `946c1303` worktree binary (the arc's parent), identical flags.** Wall +9.8% (0.51 -> 0.56 best-of-5) and every phase's 5-run ranges are DISJOINT: parse 87-104 -> 122-142ms, typecheck 235-241 -> 247-255, emit 184-185 -> 190-223. Allocs +2.0% and bytes +1.6% on LOC +1.8% -- allocation-flat per line; RSS ~+2%. `make test` 15.9s on 948 tests vs 15.3s on 923, flat per test. **ROOT CAUSE FOUND, fixed in the next row: the split's valtype containers classified builtin numerics as by-value STRUCTS** (`keyHashKind` returns 3 for every recordType and the numerics ARE records), so a scalar-element `contains`/`sort_lt` emitted a libc `memcmp` CALL per element where the pre-split `List` emitted `==`. Parse pays most because `collectExterns` does a per-node linear `contains` over two `(ListVal u32)` lists -- `__memcmp_evex_movbe` was the self-compile's hottest symbol (4.5%). | 0.56s | -- | 108MB / -- | 138 / 251 / 192 (total 583, medians of 5) | 9,885,811 | 557MB | 15.9s | 85,316 |
| 2026-08-03 | (scalar keyHashKind) | **`keyHashKind` asks `scalarCTypeFor` before classifying a record/variant as a by-value struct.** A builtin numeric/bool/char whose C type is a scalar classifies 0 with the pointers (C `==`/`<`, cast hash); a USER type shadowing a scalar name still lands 3 -- `scalarCTypeFor` is the shadow-safe gate (`shadow_set_elem.z` pins it). One predicate fixes four emissions: ListVal `contains` and `sort_lt` lose the per-element `memcmp` call (the arc's regression), and scalar Set elements / Map keys upgrade from `fasthash_bytes`/`siphash_bytes` + `memcmp` to the cast hash + `==` -- that half PRE-DATES the split (the base's `Map u64` keys byte-hashed too), an older inefficiency the regression hunt surfaced. Safe because numerics declare `==` but not `hash`, so the declared-key dispatch (which needs BOTH) stays off, and Set/Map iteration is insertion-ordered so bucket order moves no golden. **Wall 0.56 -> 0.51 best-of-5 (parity with the pre-arc base), typecheck median 251 -> 240 (base 239), parse median 138 -> 99 (base 88; best runs 86 vs 87)**. Allocs/bytes unmoved -- the fix changes comparisons, not allocations. 948/948 tests green with ZERO golden churn; `typeNameOfReg9` 90 -> 91 (the one new name lookup). **OPEN LEAD kept: `collectExterns` is O(nodes x externs) by construction** -- cheap per probe now but still quadratic-shaped; a hash-set scope is the lever if parse ever needs more. | 0.51s | -- | 108MB / -- | 99 / 240 / 187 (total 539, medians of 5) | 9,886,822 | 557MB | 16.5s | 85,325 |
| 2026-08-03 | (releaseHeldLocks skip) | **The bytes-churned lead from the ownership row, hunted with DHAT (4532d68 vs HEAD, both sides instrumented): the +18% was the LOCK MACHINERY's scope-exit rebuild.** `zenv.releaseHeldLocks` drained the ENTIRE entry table into a zero-capacity scratch list and refilled it on every release -- ~21MB/7.5k blocks of pure churn per self-compile, the top identifiable new site. Now it scans first and returns when the holder holds nothing (the common case -- the skip alone removed most of it), and sizes the scratch once when it does rebuild. **bytes 557 -> 522MB (-6.3%), allocs -11.4k, wall/phases flat** (the drain was alloc-bound, not CPU-bound). **The REMAINING ownership-arc residual vs the 465MB pre-arc floor is ~+48MB (+10%), located and RECORDED, not chased:** (1) `pushScope` copies the scope-label String per push (`z_t75_from_view` in the push path -- name ids at the edges would remove it, an architecture change); (2) the per-scope param-stamp history list doubles to ~5.6MB (structural: the readonly model records more); (3) the ZEntry/ZScope tables' rows grew with the mutability axis (same block counts, more bytes). Each is proportional model cost, none is a leak. | 0.52s | -- | 108MB / -- | 102 / 236 / 188 (total 523, medians of 5) | 9,874,410 | 522MB | -- | 85337 |
| 2026-08-04 | (typedef ids: machinery + fsno) | **The id-typedef arc's first slice: the typedef machinery made correct, and the fsno space migrated end to end** (fsno: record { val: u32.typedef } with a shadowed same-space ==; Token/ErrorData/Diagnostic/zsrcpos/zls all carry it). Seven machinery gaps fixed along the way (hashability chase, ==/!= record-eq legs, numeric-cast source chase + pointer deref, keyHashKind chase, structural-eq field chase staged through its own seed bump, the shadow-safe scalar gate the guard itself caught). **The zero-overhead claim, measured (1cf8bd04 vs HEAD, -r 7): cycles +0.13% (inside the 0.42% spread -- FLAT), wall/phase ranges overlap, allocs +0.18% / bytes +0.14% (input growth); instructions +0.67% -- real but pipeline-hidden, the typedefChase map probes that now run per key classification.** The emitted C carries ZERO trace of the typedef: no z_fsno struct exists, u32-keyed map hashes still take plain uint32_t, comparisons inline. Remaining spaces (declid, vid, tid, nameid, nodeid) follow this recipe. | 0.53s | -- | 108MB / -- | 98 / 248 / 195 (total 544, medians of 5) | 9,916,106 | 524MB | 16.5s | 85656 |
| 2026-08-05 | b5ef6194 | **The id-typedef arc, `ff7d9f31`..`b5ef6194` (19 commits): the `tid` space (type-registry id -- ztypes, ztypecheck, zemitterc, the 11 shared side tables) and the `nameid` space (StringPool id, PUBLIC zast surface), plus five pre-existing compiler bugs the migration exposed** -- the literal pseudo-ids became real registry rows, a numeric type now converts to ITSELF, a tagged arm's payload is checked against its type, and the return check that was OFF for 77% of bodies (`5ea85340`: `rkey` doubled the unit prefix, so 871 of 1136 dependency-unit bodies were never return-checked). No bare `u64` type-id parameter remains in `src/` or `lib/system/`. **The matching pool-id claim was overstated and is corrected here:** `nameRef`, `ownerNameId` and `baseId` stayed bare `u32`, and the whole `nameId: u64` ring of the Decl-tree API (`declChildOfId`, `declSetMemberType`, `setMemberType`, `constKeyIn`, ...) stayed bare `u64` -- each of them silently accepting a `nameid` by widening, which is the hole the space exists to close. All of them are closed in the N3.0 commit below. **Zero-overhead re-measured on IDENTICAL INPUT -- all three binaries compiling HEAD's `src`, `perf stat -r 7` x 4 rounds: the typedef flips alone = instructions +0.82%, cycles +0.31%; the five bug fixes = instructions -0.52%, cycles +0.57%; net vs `ff7d9f31` = instructions +0.30%, cycles +0.88%.** Emitted C still carries ZERO trace of any of the five spaces: `grep -cE 'z_t[0-9]+_(fsno\|vid\|declid\|tid\|nameid)_t' bootstrap/zc.c` = 0. Self-compile against `ff7d9f31` re-measured on the same machine the same hour (0.53-0.58s, 108MB, 9,943,747 allocs, 525,424,830 bytes, LOC 85,805): **allocs +1.9% and bytes +2.2% against +2.6% LOC -- sub-linear with input growth, so no per-operation cost.** Wall band across two `make perf` invocations: 0.56-0.58s. | 0.56s | -- | 111MB / -- | 109 / 262 / 203 (medians of 5) | 10,131,192 | 537MB | 16.6s | 88063 |
| 2026-08-06 | 8f3678fc | **N3-c/e: compound-key resolution goes by ids end-to-end** -- `resolvedByKey` / `childOfWalk` / `reexportTargetOf` / `dottedDefTarget` DELETED: every caller resolves a (unit, name[, member]) pool-id tuple via `resolvedByIds`/`resolvedByIdsMember`; `checkFunctionBody` resolves its key ONCE (three later reads reuse it) and strips a dependency defName's duplicate unit prefix with view arithmetic, so the `upfx9` composition (N3-e) is gone; `unitSeededDemand` is id-keyed via `memberKey` with unit 0 the `"*"` wildcard. **Three pre-existing bugs fixed en route:** a `:name` shorthand argument at a `.take` parameter never invalidated its source -- a silent use-after-free class (`3993e561`, fixture `shorthand_take_use_after_move`; the lock half of that gap is measured at 1,848 sites and DEFERRED, see typechecker.pdoc); the closure-wide unknown-type fallback composed digit-string keys that could never resolve (`8b7a2e2d`); the cross-unit `unit.Type.create args` explicit create was emitted as an undeclared C call -- its emitter leg was structurally dead (`ce260099`, fixture `cross_unit_explicit_create`). Allocs 9,597,793 -> 9,338,190 (**-259,603, -2.7%**), bytes 532,249,225 -> 530,055,751. The clang-vs-gcc allocation delta stays ~751k -- id-keying removed live allocations, not dead copies; the L022 pool is untouched. | 0.55s | -- | 118MB / -- | 101 / 262 / 186 (total 549) | 9,338,190 | 530MB | -- | 89020 |
| 2026-08-06 | 5c59c47a | **L022 closed, N4 (`nodeid`) landed, four compiler defects fixed -- `a0658923`..`5c59c47a` (8 commits).** The arc opened with a parked 940-line migration batch whose error fixtures looped forever at -O1 and double-freed at -O0. **Root cause was ONE EMITTER HOLE, not the batch**: `q: p` over a reftype is a MOVE (the typechecker has always said so, and the REASSIGNMENT leg always emitted `free(dst); dst = src; src = (T){0}`), but the BINDING leg emitted the struct copy and never zeroed the source -- both locals then freed the same pointer. Invisible until then because no bare-bind move of a destructor-carrying type existed in the tree (948 zeroing lines in `bin/zc.c` before AND after the fix), and the L022 migration's natural fix for a flagged local is exactly that shape. With it fixed the parked patch applied UNMODIFIED and all 229 error fixtures passed. **L022 worklist 111 -> ZERO**: the tail migrated, three params/receivers went honest (`tokByteStart`, `ZLS.innerRangeOfKind`, the probe-key locals whose `get`/`remove` already take `key.view`), and the lint learned the two shapes a view cannot serve -- a copy of a FIELD read (the save/restore idiom around a setter that REASSIGNS, so the alias dangles; zls lost `_create` declarations from a freed unit name) and a copy whose SOURCE ROOT is written in the same body. **N4**: `nodeid` (u32 typedef) now covers the 29 Node child-edge fields, ~250 id-carrying parameters under 40 labels, and ztyping's three u64 holders; containers keep their base type and mint at the boundary. **Four defects fixed with fixtures**: the binding-leg move (above), a `.take` of a String payload emitting an undeclared `<cname>_destroy`, a binop operand that accepted an unresolvable name silently (the last leg of that family), and a unit constant's literal suffix seeding no demand for its type (open bug #3). Allocs 9,037,598 -> 8,999,867 (**-37,731 net, -0.4%**) -- the L022 tail paid -66,849 and the typedef/diagnostic work put ~29k back as source growth (LOC 89,020 -> 89,382). | 0.53s | -- | 115MB / -- | 105 / 247 / 192 (total 544) | 8,999,867 | 527MB | -- | 89382 |
| 2026-08-07 | 347a3bad | **The C compiler's clean-up pool goes to zero -- `fa1c51fe`..`347a3bad` (6 commits).** `Lexer.peek` handed back an owned `Token`, copying the token text 1,128,811 times per self-compile; **756,750 of those copies were never read**, which was the ENTIRE gcc-vs-clang allocation gap, at one call site (see the Toolchain findings section -- the mechanism recorded there was wrong, and it was never a tree-wide pool). 57 of the parser's 64 peek sites wanted only a token type or a source position. The Lexer now answers those without copying -- `peekType`, `peekPos` (a `tokpos` valtype: fsno, line, column, and the text's byte WIDTH, which is all `mkError` ever read from the text), `peekText` (a view) -- and `peek`/`currentCopy` are DELETED: no reader copies the buffered token at all. `mkErrorAt` builds an error node from a span, the fold family and the three definition parsers carry a lexer or a span instead of a Token, and the string-chunk loop moves its text out via `acceptAny` instead of copying then swapping. **The split is by LIFETIME**: a `tokpos` copies out, so seven rules still name their first token in a diagnostic raised long after the lexer advanced -- a view cannot serve there, which is why one view-shaped token would not have worked. Allocs 9,082,959 (measured at `c5099056`, the arc's start) -> 7,959,463 (**-1,123,496, -12.4%**), bytes 529MB -> 524MB, `String_copy` blocks 3,471,363 -> 2,347,100. **`make perf-elision` (new) reports a write-only pool of ZERO** -- a gcc build and a clang build of `bin/zc.c` now make the same number of allocations. **Speed, measured the only way that means anything here -- BOTH binaries on the SAME input** (this row's tree, `perf stat -r 7`): instructions 6,667,405,704 -> 6,498,566,959 (**-2.53%**, +-0.04%/+-0.01%), cycles -2.04%, task-clock 562.65 -> 551.30ms (-2.02%). The wall COLUMN reads 0.53s -> 0.55s across these two rows and that is **not** a regression: every row times the compiler on its own current source, and the source grew (89,382 -> 89,808, only part of it this arc). Peak RSS likewise -- on identical input both binaries bottom out at ~114MB. **Do not read the wall/RSS columns as an A/B when LOC moved.** | 0.55s | 0.60s | 122MB / 112MB | 95 / 256 / 191 (total 542) | 7,959,463 | 524MB | 16.7s | 89808 |
| 2026-08-10 | 29dd84f8 | **Three arcs, 93 commits `347a3bad`..`29dd84f8`, measured together because the middle two were never rowed: the tcc external backend (`2d48cdc7`..`30ebceb1`), compile-time values (`b112e3d6`..`9e67f615`), and C12 static string ops (`01ec23aa`..`29dd84f8`).** Span vs the 2026-08-07 row on LOC +3.6%: wall FLAT, phases FLAT (542 -> 561 total, parse 95 -> 91, typecheck 256 -> 256, emit 191 -> 201), peak RSS 122 -> 116MB, `make test` 16.7 -> 14.0s on MORE cases (1011 vs ~1006). **allocs +4.8% (7,959,463 -> 8,341,026) against LOC +3.6%, and bytes DOWN 8.9% (524 -> 477MB)** -- the span is allocation-flat-to-better per line, but the two middle arcs were not separately measured so none of it is attributable. **The only rigorous A/B here is C12's, both binaries on IDENTICAL input (HEAD's src), `perf stat -r 7` vs a `9e67f615` worktree binary at the same flags: instructions 6,546,954,470 -> 6,533,736,764 (-0.20%), cycles -0.33%, task-clock 574.2 -> 567.2ms, allocs +15,168 (+0.18%), bytes flat (-27,864).** So C12 is performance-neutral. **It did not start that way, and the miss is the lesson: the bare-atom value check pulled the constant evaluator for EVERY bare atom that resolved to a type**, to catch the one shape where a constant is mistaken for a type alias -- instructions +0.69%, outside the +-0.25% spread, while cycles and wall stayed flat and hid it. Moving the pull BEHIND the alias test (same semantics -- the constant still overrules it) recovered the whole 0.89%. **Allocations did not move either way**: the pull returned early with no allocation for a name that is not a constant, so this was pure CPU in `constUnitTid`/`poolFind`/`constDefNodeId` and the allocation column could never have caught it. Instructions did. `make perf-elision` still reports a **write-only pool of ZERO** -- worth checking here specifically, because C12 added a second literal pool (`_zcs`) and a new arg-hoist path for non-lvalue String operands of `+`. | 0.55s | 0.63s | 116MB / 113MB | 91 / 256 / 201 (total 561, medians of 7) | 8,341,026 | 477MB | 14.0s | 93,020 |
| 2026-08-12 | e9cbb09f | **Two arcs, 76 commits `29dd84f8`..`ac6f307f`, measured together because the first was never rowed: the native table (`20b7b7d8`..`cca9d6b4`, 65 commits) and the generic runtime pass (`cca9d6b4`..`ac6f307f`, 11 commits).** Span vs the 2026-08-10 row on LOC +3.5%: **allocs +3.5% (8,341,026 -> 8,635,934) -- exactly proportional to input, so flat per line**; bytes 477 -> 510MB (+6.9%, the one column outpacing LOC and NOT attributed, since the native-table arc was not separately measured); wall 0.55 -> 0.56s, phases 561 -> 580 total, `make test` 14.0 -> 13.9s on more cases. **The rigorous A/B is the runtime-pass arc's, both binaries on IDENTICAL input (HEAD's `src`, `--emit-c /dev/null`), `perf stat -r 7`: instructions 6,667,576,785 -> 6,672,266,203 (+0.07%, spread +-0.02%), cycles -0.09% (+-0.45%), task-clock 595.9 -> 588.5ms (+-1.4%) -- CPU FLAT, the instruction rise real but pipeline-hidden. Allocs +11,510 (+0.13%) and bytes -12,786 (FLAT).** The pass trades seven hand-written emitters for one walk over 122 table rows per unit, so the block count carries a small structural cost while the bytes do not -- it copies no more, it just asks more often. **One avoidable site was found and fixed in the same measurement (`ac6f307f`): `stmtFormName9` copied the callee's pooled name to compare it against four literals, for every dotted call statement in the program -- -2,709 allocs on identical input.** `make perf-elision` read a write-only pool of **122, not zero** when this span was first measured; chased and CLOSED at `e9cbb09f` -- one write-only copy per fragment row in `loadNativeTbl`, plus a second copy the pool could not see (-225 allocs, -7,787 bytes, both counted in this row's columns). See the section below. | 0.57s | 0.66s | 119MB / 114MB | 101 / 270 / 205 (total 576, medians of 5) | 8,635,709 | 510MB | 13.8s | 96,257 |
| 2026-08-15 | c335e552 | **Five arcs plus this measurement's own three fixes, 81 commits `e9cbb09f`..`c335e552`**: demand root + outside-in (`16cbd62e`..`0c9b47c5`), native base types (`04f8a1ff`..`522aea55`), truthiness is a shape (`4dae744b`..`100db88a`), stdlib is not special (`100db88a`..`e82f9a4f`), unified environment (`e82f9a4f`..`e56e4bf1`), then `9671b2c8`..`c335e552`. **Bytes churned had MORE THAN DOUBLED across the span -- 510 -> 1,129MB -- and no other column showed it.** Allocations were flat (8,635,709 -> 8,675,188, +0.5% against LOC +1.15%), wall and phases improved, `make test` and the corpus were green: the same block counts moved ~9x the bytes, and 98% of the excess was two functions. Cause, instrument and fix in the section below; **this row's columns are POST-fix**, and bytes now read 478MB, BELOW the 510MB this span started from. **Per-arc A/B, every binary on IDENTICAL input (HEAD's `src`, `--emit-c /dev/null`), gcc -O1 series binaries, `perf stat -r 7`** -- instruction spread +-0.01..0.03%, and two rounds of ONE binary differ by 0.007%, so a tenth of a percent is signal here: truthiness **+0.13%** instructions, native base types + stdlib **+0.17%**, **unified environment -1.52% (7,054,595,077 -> 6,947,673,171) -- deleting the demand set paid in CPU as well as in architecture**, the three fixes **-7.09% (-> 6,455,199,486)**. Span total **-8.22% instructions, -8.93% cycles, -7.69% task-clock, -57.8% bytes, -0.82% allocations**. `make perf-elision` reports a write-only pool of **ZERO**. **The mimalloc RSS column moved by ENVIRONMENT, not by code**: `e9cbb09f`'s own binary, rebuilt and re-measured here, reads **139MB** against the **119MB** recorded for it on 2026-08-12, while its glibc RSS re-measures at exactly the recorded 114MB. mimalloc retains ~20MB more on this machine now; HEAD's 138MB is 1MB BETTER than the previous row's commit measured beside it today. Read the mi column against 139, not against 119. | 0.52s | 0.64s | 138MB / 114MB | 94 / 239 / 198 (total 531, medians of 7) | 8,640,337 | 478MB | 13.9s | 97,366 |
| 2026-08-16 | 500eb312 | **The composite-key sweep, 16 commits `c335e552`..`500eb312`: every `(a << 32) \| b` key in the compiler is gone** (written `* 4294967296`, since the language has no shift operator). Seven families: `preIndexDefined` DELETED as dead, `genericParamTid` and `protocolThisContext` moved onto the rows they describe (`ParamDesc.paramTid`, `Decl.conformerName`), and `callGenericBinding` / `genericArgTypeBy` / `memberConst`+`constEvalState` / the three mono-stamp tables / the alias-target return value given typed pair RECORDS (`callslot`, `genericslot`, `constslot`, `monostamp`, `deftarget`). `ztypes.memberKey` deleted with the last caller. **This is an architecture change measured as one, and the headline is that it is free: both binaries on IDENTICAL input (HEAD's `src`, `--emit-c /dev/null`), gcc -O1, `perf stat -r 7` -- instructions 6,473,863,458 -> 6,470,996,444 (-0.04%, spread +-0.02%), cycles +0.08%, task-clock +0.53%, allocations -208, bytes +456,292 (+0.10%, the `u32` per Decl the conformer mark added).** LOC +0.03%. **A record key is not slower than a packed u64 -- it is faster.** The mono-stamp family, the hot one (the emitter probes it per node inside a generic body), measured on its own: instructions -0.12%, cycles -0.40%, task-clock -1.56%, allocations flat. The multiply-and-add every probe paid and the divide-and-subtract every unpack paid cost more than hashing eight more bytes. **One family DID regress, and the cause is worth carrying: the alias-target record cost +0.90% instructions** (6,472,418,756 -> 6,519,940,768 across the same span, measured per stage on one input) **because `nameid.isZero` is a declared one-line method that gcc -O1 does not inline** -- five presence tests on the alias-resolution path each became a real call, `z_t3797_nameid_isZero(&altK9.unitName)`, where the packed key tested `altK9 > 0` with a compare. Reading the field directly (`.u32 > 0`) gave back 52.4M of the 58.4M. **-O1 is the SERIES level, not the shipped one: gcc -O2 inlines the method away, and the same five sites are worth 0.19% there rather than 0.80%.** See the section below. `make perf-elision` reports a write-only pool of **ZERO**. | 0.52s | 0.64s | 139MB / 114MB | 88 / 241 / 198 (total 529, medians of 7) | 8,645,497 | 480MB | 13.6s | 97,393 |
| 2026-08-17 | 154095f8 | **The environment arc, 66 commits `500eb312`..`154095f8`: one resumable environment (frames ARE Decls, `envLookup` + a cursor, locals as Decls, monos on the arg trie under their template, a `public:` block as a nested unit, `zframes`/`ty.nsOwner*`/the parallel-verify scaffolding deleted), closed by the numeric move (`ae083d34`: wideint/halffloat/quadfloat are hidden subunits of system).** **The headline is a cost, measured the rigorous way -- every binary a gcc -O1 series build in its own worktree, all on IDENTICAL input (this row's `src` with the pre-move `lib/system`, `--emit-c /dev/null`), `perf stat -r 7`, spread +-0.01..0.02%: instructions 6,589,939,195 -> 7,571,555,608 (+14.90%), cycles 2,799,748,889 -> 3,148,075,028 (+12.44%), task-clock 549.1 -> 610.4ms (+11.2%).** Per step, same input: steps 0-2 (`ee36a28b`, frames and locals become Decls) **+10.73%** -- the arc's own step-2 A/B read +5.1% because it compiled the source of THAT day; the number that stands is this one; step 3 flat (-0.04%); 5b template bodies resolve lexically **+1.68%**; 5c/5d -0.21%; 5e +0.01%; the 4+7 flip (`ae7b5863`, name-derived frames deleted) -0.17%; step 6b `public:` as a nested unit (`a3ffb1fc`) **+2.43%**; the step-8 preparations +0.02%; the stamp-pass retry list (`2436d8e3`) +0.04%; **the move itself, HEAD's binary on the moved vs the pre-move lib: +0.05% -- three fewer file units cost nothing.** Allocations 8,645,497 -> 8,722,220 (+0.89%) on LOC +1.46%, flat per line; **bytes churned 480 -> 531MB (+10.5%)** -- `Decl` rows for ~42k frames and ~34k locals, each a 17-field record with a List and a Map header (the step-2 measurement said +8.7%). `make perf-elision` not re-run. **Where the +14.9% lives is the next perf arc's brief: `Decl` is fat and every local/frame mints one (lazy `byName`/`children` on Decl was the follow-up named at step 2); 5b and 6b each add a lookup leg per name (template frame, public-block surface). The wall/RSS/phase columns are NOT an A/B (source grew, and this machine ran a 23-day-old orphaned `zc` at 100% until this measurement killed it -- load average ~3): wall 0.52 -> 0.60s, phases 529 -> ~616 total, mi RSS 139 -> 144MB.** LOC now counts `lib/system/**/*.z` (the subunit files moved, not vanished). | 0.60s | 0.71s | 144MB / 124MB | 94 / 294 / 220 (total ~616, medians of 3) | 8,722,220 | 531MB | 34.9s (load ~3, 1092 cases) | 98,816 |
| 2026-08-17 | 4fc75709 | **The environment arc's cost, chased: three commits `aac9077b`, `ea40448c`, `1a2b5b94`.** The previous row measured the arc at +14.9% instructions and asked where it lived; per-function profiles (`--readable-names` builds, `perf record` on identical input) answered, and none of it was the design. (1) `ZSymbolTable.snapshotFrame` laid a dump row (a String copy each) for every local and overlay row as EVERY frame closed, in every compile, for the model dump alone -- and had since long before the arc: gated on `ty.dumpRows` (set by `zc dump`), **-4.55% instructions, -41MB churned, -83k allocations**. (2) every block, arm, loop and call bracket minted a Decl row -- 43,012 in the self-compile, 22k of them empty (a frame that declares nothing is invisible to every walk): a block frame is now a zero slot on the frame stack until its first `defineLocal` (or `markUnreachable`) materialises it under the nearest frame that has a row; a dump opens frames eagerly as before -- **-1.21%, -25MB, -47k allocations**. (3) what a hop of a frame walk paid: `unitFrameOf` decided unit-frame-ness by Decl -> node -> `unitOpDeclaresUnit` -> `unitOpKindIs` on every hop of every `envLookup` / type-ref walk / `frameFor` (now a bit on the row, `Decl.unitFrame`, laid at index time -- a `require:` block is not a frame, which is why a bit and not the kind); `publicNsOf` scanned all ~1,400 children of a unit root per qualified lookup (now `ZTyping.publicNsByOwner`); every Decl row owned a boxed `MapVV` name index used by 140 of 95,779 rows (now a slot into `ZTyping.nameIndexes`, no heap object per row); `frameFor` climbed from the body frame twice (now once, from the unit frame) -- **-2.43%, -73k allocations**. **Rigorous A/B, every binary on IDENTICAL input (this row's `src` + `lib/system`, `--emit-c /dev/null`), `perf stat -r 5`, spread <=0.05%:** series binaries gcc -O1: `500eb312` 6,595,779,475 -> `3283f06e` (before these commits) 7,720,137,748 (+17.0%) -> **6,918,199,917 (+4.9% vs the pre-arc compiler, -10.4% vs before)**, cycles 2,976M -> 3,219M -> 3,025M (+1.6%); the shipped level gcc -O2 (`bin/zc`): 4,137,179,478 -> 4,615,750,089 (+11.6%) -> **4,247,963,181 (+2.7%)**, cycles 1,935M -> 2,155M -> **1,916M (-1.0%: below the pre-arc compiler)**. Allocations 8,645,497 (previous-row input) / 8,722,220 -> **8,529,539**; bytes churned 480MB / 531MB -> **462MB, below the pre-arc row**. Peak RSS (mi) 144 -> 120MB. Same-source A/B raw-identical over 448 programs at every step; dump goldens unchanged. **What remains of the arc's cost, attributed and open:** `findLocal` (locals as Decl children, a linear scan per materialised block frame; ~1.3%), `getLiveOwnedVars` (two SetVals per `if` and per arm; ~1%), step 6a's `ewEmit` (every `:x` label through the extern collector's linear scope scan; ~0.9%), `declIdOfType`+`declFindChild` per member lookup (Q2, not this arc). Row taken with the machine at load ~2 (other sessions); wall/phase columns are on the tree's own source as always. | 0.54s | — | 120MB / — | 115 / 256 / 208 (total 579, medians of 3) | 8,529,539 | 462MB | — | 98,919 |
| 2026-08-18 | fcc1bf25 | **The one-unit arc's last three phases plus the two arcs before them, 46 commits `4fc75709`..`fcc1bf25`.** The rowed part is phases 5-7 (`35e959b4`..`fcc1bf25`): the ambient unit becomes a `ztypes.declid` (171 StringView params flipped), the residual name channels close (text->Decl conversions in `ztypecheck.z` 110 -> 17, `declRootUnitName` and `unitTidOfName9` retired), and the composed string key `"{a}.{b}"` goes (`dataKey9` was literally `"\{tid}"`; `methodReturns`, the `defName` prefix-strip and `internUnitId9` deleted). The earlier 20 commits (sentinel row 0, `require:` is a unit, one-unit phases 0-4) were never rowed and are NOT attributed. **The headline: phases 5-7 emit BYTE-IDENTICAL `zc.c` on HEAD's own source -- semantically inert -- and cost 2.56% fewer instructions.** **Rigorous A/B, every binary a gcc -O1 series build in its own worktree, all on IDENTICAL input (this row's `src` + `lib/system`, `--emit-c /dev/null`), `perf stat -r 7`, spread +-0.02%:** span `4fc75709` 6,833,630,258 -> **6,449,413,978 (-5.62%)** instructions, cycles 2,811,955,739 -> 2,726,075,163 (-3.05%), task-clock 584.6 -> 548.7ms (-6.15%); allocations 8,393,352 -> 8,366,589 (-26,763), bytes 458,312,654 -> 457,941,703. **Per phase, same input:** phase 5 (`35e959b4`->`bb81d421`) **-2.35%** instructions and -5,240 allocs -- deleting the `scanUnit` threading and the name->frame lookups is where the CPU is; phase 6 (`->c538abe8`) -0.26% and **-17,220 allocs** -- the door and round-trip deletions (`unitNameTextOf` / `declRootUnitName` / `poolTextAt` each allocate a String); phase 7 (`->fcc1bf25`) **CPU-neutral** and -4,379 allocs, -117,877 bytes. **Phase 7's instruction reading needed care and is a good calibration:** three interleaved rounds gave P6-end 6,448.8M / 6,447.0M / 6,449.6M and P7-end 6,451.3M / 6,451.6M / 6,448.8M -- a +0.03% difference of means against a ~0.04% round-to-round range within EACH binary, with round 3 inverting the sign. **Not resolvable; do not call it a regression.** That phase removed 4,703 futile map lookups and 152 writes per compile and still moved no instructions, because the work it deleted was cheap and skipped -- its win is in the allocation column, which is exactly where composed string keys live. The pre-rowed 20 commits account for the rest of the span (-3.14% instructions) at +76 allocations, i.e. flat. `make perf-elision` reports a write-only pool of **ZERO**. Machine quiet (load 0.05). | 0.53s | 0.63s | 120MB / 100MB | 93 / 234 / 207 (total 534) | 8,366,482 | 458MB | 14.1s (1095 cases) | 99,056 |

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
