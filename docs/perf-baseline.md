# Compiler performance baseline

Hand-maintained ground truth for compiler performance work. Append a row per
landed perf workstream, measured with the commands below, in the same commit
that lands the change. Machine context matters — record it per row when it
changes.

## Commands (run from the repo root, warm tree)

`make perf` prints the core row: the zerolang line count, self-compile wall
best-of-5 + peak RSS, the parse/typecheck/emit phase split, and (when valgrind is
installed) the allocation total. It measures with the default hash and
`--emit-c /dev/null`, exactly as below. The remaining columns are manual:

```bash
make perf                       # LOC + wall + RSS + phases + allocs (the core row)
# glibc wall: rebuild pure-glibc, time it, then rebuild the mimalloc driver back:
touch src/zc.z && make MIMALLOC=0 zc
for i in 1 2 3 4 5; do /usr/bin/time -f "%es %MkB" \
    bin/zc zc --src src --system lib/system --emit-c /dev/null 2>&1 | tail -1; done
touch src/zc.z && make zc
# corpus wall (bimodal -- see the 2026-07-21 note):
time make test
# allocation-site census (optional, slow):
valgrind --tool=dhat --dhat-out-file=/tmp/zc.dhat bin/zc zc --src src \
    --system lib/system --emit-c /dev/null
```

## Baseline table

Machine: 24-core, gcc 15.2.0, glibc 2.43, Linux. Wall = best of 5.
"allocs" = memcheck total heap blocks for one self-compile. "LOC" = `wc -l` of
`src/*.z` + `lib/system/*.z` (the self-hosted compiler + relocated front-end/stdlib).
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

- **gcc -O2**: wall 0.55s -> 0.40s (bin/zc.c compile time 14s -> 20s);
  allocation count 9,597,630 vs -O1's 9,597,793 -- identical modulo run
  wobble. The wall gap against clang is -O1 codegen quality, nothing
  allocation-shaped; if a faster release build is ever wanted, -O2 is the
  flag, on a build OUTSIDE the perf series.
- **clang -O1**: wall 0.37s and ~750k (-7.8%) fewer allocations. Same-source
  A/B at the String/StringView arc end: gcc 9,576,316 vs clang 8,826,150
  (delta 750,166). The delta is dead malloc->memcpy->free chain elision --
  LLVM read-forwards to the source bytes and deletes the provably-dead copy
  chain -- which gcc performs at no tested level; an allocator attribute on
  `z_xmalloc` (`malloc, returns_nonnull, alloc_size`) changes nothing.
- **Rule**: the clang-vs-gcc allocation delta approximates the remaining
  trivially-dead-copy pool -- a machine-level estimate of what the L022
  viewable-local migration can reclaim at the source (the ~692-site worklist
  overlaps it). Re-measure the pair (perf-strict vs a scratch clang build)
  as the migration lands; the delta should shrink from gcc's side, and the
  migration improves both builds.

