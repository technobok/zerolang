# Typed-tree migration — session handoff

**Goal:** Treat `zast.Node` as immutable after parsing; have the
typechecker construct a parallel `TypedProgram` (HIR-style) that the
emitter and SQL dump consume. Once complete, the parser produces a
frozen AST and the typechecker's output is a separate hierarchy of
typed nodes that reference parsed nodes for trivia.

The full design rationale + alternatives considered are in
`/home/pawe/.claude/plans/is-this-the-best-virtual-sun.md` (approved
plan). This file is the running implementation log.

## Why we are doing this

- Today ~30 `init=False` fields on parsed `zast.Node` subclasses are
  written by the typechecker after parsing; nothing in the type system
  distinguishes "parsed `Function`" from "typechecked `Function`".
- Project trajectory commits to (a) singular SQL tables that map 1:1
  to compiler structures and (b) self-hosting. Decorating in place
  produces sparse wide tables whose meaning depends on which pass last
  touched them; a clean parsed/typed split maps to two narrow,
  write-once table families joined by FK.
- A real boundary violation already existed: the **emitter** was
  writing scratch state (`For._comprehension_list_var` / `_name`) onto
  the AST. Fixed in Step 1. Two more such violations would normalise
  the pattern; the split prevents that class of mistake.

## Status

| Step | Status | Commit | Notes |
| ---- | ------ | ------ | ----- |
| 1. For comprehension scratch off AST | ✅ done | `2afe83e` | emitter-local dict keyed by `nodeid` |
| 2. Define `src/ztypedast.py` | ✅ done | `43ec658` | data-only, no callers |
| 3a. Typechecker scaffold + `TypedAtomId` mirror | ✅ done | `2033378` | `typed_program` on `TypeChecker`; `_check_atomid` builds `TypedAtomId` via `_register_typed`; invariant test in `tests/test_typed_tree.py` |
| 3b. `TypedDottedPath` mirror | ✅ done | `2428531` | `_check_dotted_path` becomes a thin wrapper; resolution moves to `_check_dotted_path_inner`; `_build_typed_dotted_path` runs on exit and looks up parent via `_typed_path_for_parent` (Expression-unwrapping). Inline parent-ATOMID branch in the inner builds a `TypedAtomId` for the parent so the wrapper finds it. |
| 3c. `TypedAtomString` mirror | ✅ done | `83e810d` | `_build_typed_atomstring` invoked at the two sites that set `AtomString.type`. Interpolation parts unwrap `Expression` and embed the inner subtype's typed counterpart; skips the whole mirror when an interpolation part has no typed counterpart yet (covers AtomId + DottedPath interpolations today, BinOp/Call later). |
| 3d. `TypedBinOp` + `TypedCall` + `TypedNamedOperation` mirrors | ✅ done | `faa5841` | wrapper pattern around `_check_binop` and `_check_call`; `_typed_operation_for` resolves typed counterpart of any Operation-shaped parsed node. Numeric-cast shortcut now builds a TypedAtomId for the literal parent. Synth atoms produced by atomic-call hoisting also get a TypedAtomId via `_build_typed_atomid` at the assignment site. | `_build_typed_atomstring` invoked at the two sites that set `AtomString.type`. Interpolation parts unwrap `Expression` and embed the inner subtype's typed counterpart; skips the whole mirror when an interpolation part has no typed counterpart yet (covers AtomId + DottedPath interpolations today, BinOp/Call later). |
| 3d–3e. Remaining typed-mirror coverage | ⏳ next | — | BinOp, Call, NamedOperation, statements, control flow, top-level |
| 4. Switch emitter to consume typed tree | pending | — | |
| 5. Switch SQL dump to typed tree | pending | — | schema split into `parsed_*` / `typed_*` |
| 6. Remove `init=False` decorations from `zast.py` | pending | — | typechecker stops writing in place |
| 7. Freeze `Node` (`@dataclass(frozen=True)`) | pending | — | invariant: parsed AST never mutated |
| 8. Typechecker cleanup (codereview20260428) | pending | — | the originally-planned next task; cheaper after the split |

`make check` clean and `make test-fast` 1358 passing after Step 3a.

## Step 3a — what landed

- `TypeChecker.__init__` constructs `self.typed_program: TypedProgram`
  (parsed back-ref to `program`, `mainunitname` copied through). `units`
  / side-tables left empty until later sub-steps populate them.
- `TypeChecker._register_typed(parsed, typed)` indexes a typed node by
  its parsed back-reference's `nodeid`, exposed via
  `typed_program.by_parsed_id`. Idempotent (last writer wins, fine for
  a node that gets re-typed under monomorphisation).
- `TypeChecker._build_typed_atomid(atom)` mirrors a parsed `AtomId`
  into a fresh `TypedAtomId` (name + ztype + const_value + narrowing
  fields + child_id + is_label_value derived from `nodetype`) and
  registers it. Called from each return path of `_check_atomid`,
  including the two error returns — so even unresolved AtomIds get a
  typed mirror with `ztype=None`.
- Invariant test `tests/test_typed_tree.py::TestTypedAtomIdInvariants`
  walks every TypedAtomId in `by_parsed_id` and asserts field-for-field
  agreement with its parsed back-reference. As more typed-node kinds
  come online, this test broadens to those kinds.

## Step 3b — what landed

- `_check_dotted_path` is now a thin wrapper around the renamed
  `_check_dotted_path_inner`. After the inner returns, the wrapper
  calls `_build_typed_dotted_path(path)`.
- `_build_typed_dotted_path(path)` resolves the parent's typed
  counterpart through `_typed_path_for_parent`, which unwraps
  `zast.Expression` (the parser's `(parens)` wrapper) before looking
  up `by_parsed_id`. Skips silently when the parent has no typed
  mirror yet (parent is an `AtomString` or an interpolation Expression
  containing an as-yet-untyped subtype).
- The inline ATOMID-parent branch inside `_check_dotted_path_inner`
  used to set `parent_atom.type` without routing through
  `_check_atomid`. It now also calls `_build_typed_atomid(parent_atom)`
  so the wrapping `_build_typed_dotted_path` finds a typed parent in
  `by_parsed_id`.
- `tests/test_typed_tree.py::TestTypedDottedPathInvariants` walks every
  `TypedDottedPath` in `by_parsed_id` and asserts field-for-field
  agreement (parent, child name, ztype, parent_tagged_type, narrowed
  fields, child_id) with the parsed back-reference.

Known gaps (covered in later sub-steps):

- Numeric-cast shortcut (`5.u32`): the inner returns early before the
  parent atom's type is set, so no parent typed mirror exists and the
  wrapper skips. Affects only numeric casts.
- AtomString-as-parent / interpolation-Expression-as-parent: parent
  typed mirror not yet built. Lands with Step 3c (AtomString) and
  Step 3d (BinOp/Call expressions).

## Step 3c — what landed

- `_build_typed_atomstring(atom)` constructs `TypedAtomString` and
  registers it. Each `Expression` interpolation part unwraps to its
  inner subtype, then looks the inner up in `by_parsed_id` and embeds
  the typed counterpart directly (matching the design's "no typed
  Expression wrapper" rule). `StringChunk` parts are passed through
  by reference. Skips the whole mirror when any interpolation part
  has no typed counterpart yet — partial mirrors would weaken the
  invariant test.
- Called from both sites that set `AtomString.type`:
  - `_check_path` ATOMSTRING branch
  - `_check_dotted_path_inner` ATOMSTRING-parent branch
- Tests cover plain string literals (parts pass through identity) and
  AtomId interpolation (`"hi \\{name}"`). DottedPath interpolation is
  already covered structurally via Step 3b's mirrors.

## Step 3d — what landed

- `_check_binop` and `_check_call` follow the same wrapper pattern as
  `_check_dotted_path`: original body renamed to `*_inner`, the wrapper
  calls `_inner` then `_build_typed_*`.
- `_typed_operation_for(node)` is the generic Operation-shaped lookup;
  unwraps `Expression`, validates the parsed nodetype is one of
  ATOMID / LABELVALUE / ATOMSTRING / DOTTEDPATH / BINOP / CALL, returns
  the typed counterpart from `by_parsed_id`. Used by both BinOp and
  Call builders for their operand fields.
- `_build_typed_call` walks `call.arguments`, building a
  `TypedNamedOperation` per parsed `NamedOperation` (registered in
  `by_parsed_id`). Skips the whole Call mirror if any operand has no
  typed counterpart yet.
- Two gaps closed in this step:
  - Numeric-cast shortcut (`5.u32`) inside `_check_dotted_path_inner`
    now also builds a TypedAtomId for the literal parent (with
    `ztype=None`, matching `path.parent.type`). Documented in 3b as
    a deferred gap; needed for BinOp/Call argument lookup.
  - Atomic-call hoisting (`zsynth.make_atom_id` produces a fresh
    AtomId when an arg is hoisted to a synth assignment) now calls
    `_build_typed_atomid` so the synth atom has a typed mirror that
    `_build_typed_call`'s arg lookup can find.

Test coverage in `tests/test_typed_tree.py`:

- `TestTypedBinOpInvariants.test_simple_int_add` — operator is a fresh
  TypedAtomId (not registered in `by_parsed_id`), operands are looked
  up.
- `TestTypedCallInvariants.test_simple_call` — callable + args walk;
  per-argument `TypedNamedOperation` is registered in `by_parsed_id`.
- `TestTypedCallInvariants.test_atomstring_with_binop_interpolation_now_mirrors`
  — `"sum=\\{a + b}"` produces a TypedAtomString whose interpolation
  part is a TypedBinOp, closing the gap noted in Step 3c.

## Step 3e — next

Statements (`TypedAssignment`, `TypedReassignment`, `TypedSwap`,
`TypedStatementLine`, `TypedStatement`) and control flow
(`TypedIf`/`TypedIfClause`, `TypedCase`/`TypedCaseClause`, `TypedFor`,
`TypedDo`, `TypedWith`). Same wrapper pattern. Statements are wrappers
over the operations they contain, so the lookups should be
straightforward once each `_check_*` lands.

After statements + control flow, the top-level (`TypedFunction`,
`TypedObjectDef`, `TypedUnit`, `TypedProgram.units`) closes Step 3.

### Subtle places to remember

- `BinOp.operator`, `CaseClause.match`, and `DottedPath.child` are
  AtomIds that the typechecker never independently types (`type`
  stays None). Don't build standalone `TypedAtomId` for these —
  they're folded into their containing typed node
  (`TypedBinOp.operator`, `TypedCaseClause.match`,
  `TypedDottedPath.child`) at the moment that node is built.
  `TypedDottedPath` already follows this pattern.

## What's in `src/ztypedast.py` (542 lines, frozen interface)

Type hierarchy:

```
TypedNode              (parsed: zast.Node, typedid, synth_origin)
├── TypedExpression    (abstract; ztype, const_value)
│   ├── TypedOperation (abstract)
│   │   ├── TypedPath  (abstract: typeref AND value-yielding path)
│   │   │   ├── TypedAtomId       (name, is_label_value, narrowed_subtype, original_ztype, child_id)
│   │   │   ├── TypedDottedPath   (parent, child, parent_tagged_type, narrowed_subtype, child_id)
│   │   │   └── TypedAtomString   (parts: List[TypedExpression | zast.StringChunk])
│   │   ├── TypedBinOp            (lhs, operator, rhs)
│   │   └── TypedCall             (callable, arguments, call_kind, callable_type_name)
│   ├── TypedIf                   (clauses, elseclause, taken_vars)
│   ├── TypedFor                  (conditions, loop, postconditions, iterator_bindings)
│   ├── TypedDo                   (statement, has_break)
│   ├── TypedWith                 (name, value, doexpr, ownership, alias_of)
│   ├── TypedCase                 (subject, clauses, elseclause, subject_taken, taken_vars)
│   ├── TypedData                 (data)
│   ├── TypedReassignment         (topath, value)  — null-typed
│   └── TypedSwap                 (lhs, rhs)        — null-typed
├── TypedStatement                (statements: List[TypedStatementLine])
├── TypedStatementLine            (wraps TypedAssignment | TypedReassignment | TypedSwap | TypedExpression)
├── TypedAssignment               (name, value, alias_of)
├── TypedNamedOperation           (name, valtype, projected_protocol, projected_label, projected_kind)
├── TypedIfClause                 (conditions, statement)
├── TypedCaseClause               (name, match, statement)
├── TypedFunction                 (parameters, returntype, body, is_native, as_items, ztype)
├── TypedObjectDef                (kind, is_items, as_items, is_native, ztype)
└── TypedUnit                     (body, ztype)
```

**Top level:** `TypedProgram` carries the parsed `Program`,
`Dict[str, TypedUnit]`, plus the already-aggregated side-tables that
were back-doored onto `zast.Program` post-typecheck (`resolved`,
`mono_types`, `mono_functions`, `func_aliases`, `cloned_methods`,
`unit_types_by_id`, `symbol_table`). Plus `by_parsed_id: Dict[int,
TypedNode]` for cross-tree lookup (symbol-table entries reference
parsed nodes; the typechecker resolves them to typed nodes via this
index).

### Design decisions made (do not relitigate)

- **Inert leaves not mirrored.** `StringChunk`, `Error` carry no
  typecheck-derived state and have no typed children. Typed nodes
  that reference them (e.g. `TypedAtomString.parts`) embed the
  parsed node directly. Saves ~10 trivial mirror classes; the
  principle is "mirror what you type."
- **`LabelValue` folded into `TypedAtomId`.** LabelValue (`:x`
  shorthand) carries no typed state distinct from AtomId. Folded via
  `is_label_value: bool`. Cleaner than a `TypedLabelValue(TypedAtomId)`
  with no fields.
- **No `TypedExpression` wrapper class.** The parser-AST `Expression`
  wraps `ExpressionSubTypes` to give `(parens)` an Atom shape. The
  typed tree skips this — when typecheck hits `zast.Expression`, it
  recurses into `expression.expression` and returns the typed
  counterpart of the inner subtype directly. `(x + 1)` → `TypedBinOp`,
  not `TypedExpression(TypedBinOp(...))`.
- **`Path` serves both roles.** In the parser AST, `Path` is a typeref
  *and* a value-producing expression. `TypedPath` keeps this duality
  — used in both `TypedFunction.parameters` (typeref) and
  `TypedCall.callable` (value). The `ztype` field disambiguates by
  context.
- **`_typed_children` dispatches on `parsed.nodetype`.** No isinstance
  (bootstrap-lint forbids it; baseline 0). Same id-based discrimination
  the parser AST uses.
- **Fresh `typedid` on cloned subtrees.** `clone_typed_function` deep
  copies and re-issues typedids on every cloned typed node so
  monomorphization doesn't carry duplicate ids. Parsed back-refs are
  left pointing at the original source.

## Step 3 — Twin-pass typechecker (the next big task)

This is structurally the largest change. The typechecker is
**`src/ztypecheck.py` (9771 lines)**. The goal is to have every
`_check_*` method **return** a `Typed*` node while still populating
the existing in-place decorations on parsed nodes (so the emitter
keeps working unchanged). Both representations live during steps 3–5.

### Recommended approach: leaf-out

Start with leaves where construction is simple and let the structure
grow upward:

1. **`TypedAtomId`, `TypedAtomString`, `TypedDottedPath`** — these
   are leaf-ish. The typechecker already resolves their types; just
   construct the typed counterpart and return it alongside the
   in-place decoration.
2. **`TypedBinOp`, `TypedCall`, `TypedNamedOperation`** — operations.
3. **Statement-line members**: `TypedAssignment`, `TypedReassignment`,
   `TypedSwap`, `TypedStatementLine`, `TypedStatement`.
4. **Control flow**: `TypedIf`/`TypedIfClause`, `TypedCase`/
   `TypedCaseClause`, `TypedFor`, `TypedDo`, `TypedWith`.
5. **Top-level**: `TypedFunction`, `TypedObjectDef`, `TypedUnit`,
   `TypedProgram`.

Each step shippable; the typechecker still works the same way for
the emitter. Only the typed-tree consumers (steps 4 onward) need it.

### Concrete starting point for Step 3

The typechecker's main entry is `TypeChecker.check()` and its
`_check_*` family. Begin by:

1. Add `TypeChecker.typed_program: TypedProgram` initialised in
   `__init__`. Populate `parsed`, `mainunitname`, leave `units`
   empty for now.
2. Add `TypeChecker.by_parsed_id: Dict[int, TypedNode]` (mirror of
   `TypedProgram.by_parsed_id`). Helper:
   `_register_typed(parsed_node: zast.Node, typed_node: TypedNode)`
   that stores the typed node and indexes by parsed id.
3. Pick the first `_check_*` to convert — `_check_atom` /
   `_check_path` are good candidates. Modify the signature to return
   `(old_return_value, TypedAtomId)` (tuple) initially, OR start by
   adding a parallel `_build_typed_atom` method invoked alongside.
4. Walk upward from there. Bootstrap-lint guard: add a ratchet on
   "`<parsed_node>.<typed_field> = ` outside the typechecker" once
   you can — but not before, since the twin-pass needs to keep
   writing both.

### Open questions for Step 3

- **Should `_check_*` return tuples `(extern_or_void, Typed*)` or
  should the typed node carry along via a separate visitor?** The
  current shape of `_check_expression` etc. takes a parsed node and
  produces decorated state via mutation. A tuple return is the
  smallest diff but multiplies signatures. A typed-builder visitor
  alongside is cleaner but doubles traversal cost. **Likely answer:**
  tuple return for the twin-pass period; collapse to single-return
  in step 6 when the in-place decorations come out.
- **What about generics / monomorphization?** Today `clone_function`
  deepcopies a parsed `Function`. After step 3, monomorphization
  produces a typed clone via `clone_typed_function`. During twin-pass
  it needs to do BOTH — clone the parsed Function AND build a typed
  counterpart. The `_mono_functions` accumulator on `TypeChecker`
  needs a sibling for typed clones. **Likely answer:** add a parallel
  `_typed_mono_functions` accumulator, populate both, attach to
  `TypedProgram.mono_functions` at end.
- **`unit_types_by_id` keyed by parsed nodeid:** still works
  unchanged; the typed tree references it via
  `TypedProgram.unit_types_by_id`. No structural change needed.
- **`SymbolTable` references:** entries reference parsed AST nodes by
  identity. After step 3, when something needs the typed node for an
  entry, it goes through `TypedProgram.by_parsed_id`. SymbolTable
  internals don't need to change.

### Verification for Step 3

- `make check` clean.
- `make test-fast` clean (the typechecker is the most-tested
  subsystem).
- New invariant test — gradually broaden:
  - Initial form (after first `_check_*` returns typed): assert
    typed-node fields match the in-place decorations on the
    corresponding parsed node, on a representative sample of programs.
  - Final form (after Step 3 complete): every parsed node reachable
    from `Program` has a `TypedNode` entry in
    `TypedProgram.by_parsed_id`, and every typed node's fields
    agree with the in-place decoration on its parsed back-ref.
- This invariant test stays green for the rest of the migration —
  it's the structural guarantee that the typed tree is an honest
  mirror of the in-place state until step 6 retires the in-place
  state.

## File-size context (informs PR sizing)

```
src/ztypecheck.py  9771 lines  (touched heavily in steps 3, 6, 8)
src/zemitterc.py   9829 lines  (touched heavily in step 4)
src/zast.py        1098 lines  (touched in steps 1, 6, 7)
src/ztypedast.py    542 lines  (just added, step 2)
src/zsqldump.py     411 lines  (touched in step 5)
src/zenv.py        ~?           (review for step 3)
```

Steps 3 and 4 each plausibly span multiple commits. Don't try to
collapse them into one PR.

## Process notes for whoever picks this up

- **Do not skip `make check` before commits.** Bootstrap-lint
  ratchets are real — `isinstance` baseline is 0, `try/except`
  baseline is 8, `getattr` baseline is whatever it currently is. Any
  regression blocks the commit.
- **Do not add Co-Authored-By to commit messages.** Project-wide
  preference; `CLAUDE.md` says this explicitly.
- **Run `make test` before push** for any change touching emitter,
  runtime, examples, or system lib (project policy in `CLAUDE.md`).
  `make test-fast` on its own is enough during inner-loop iteration
  but not for ship.
- **Use field-based dispatch, not isinstance.** Use `nodetype` /
  `parsed.nodetype` / `type(x) is ClassName` / `getattr` — never
  `isinstance`.
- The plan file at
  `/home/pawe/.claude/plans/is-this-the-best-virtual-sun.md`
  has the full design rationale and the alternatives considered.
  Read it once before reopening any of the design choices noted
  above.

## How to start the next session

Open a fresh Claude session in `/home/pawe/dev/zerolang/` and point
it at this file:

> Continuing the typed-tree migration. Read `doc/typed_tree_migration.md`
> for the running log, then `/home/pawe/.claude/plans/is-this-the-best-virtual-sun.md`
> for the original design. Begin Step 3.
