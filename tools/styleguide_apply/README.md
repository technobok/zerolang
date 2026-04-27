# Styleguide Apply Tooling

Tooling for applying `doc/styleguide.pdoc` naming rules across the codebase.
Built during the Phase 1 attempt (2026-04-27); not yet used to land a
green commit. See `/home/pawe/.claude/plans/for-the-zerolang-project-ticklish-dragonfly.md`
for the full project plan.

## Files

- **`sql_rename.py`** — SQL-driven targeted renamer. Consumes one or more
  SQL dumps produced by `zc --dump-sql` and emits precise (file, line,
  col, old, new) edits for `.z` source files. Distinguishes type
  references from method calls, parameter names, variant tags, etc.
  using the dump's `tokens` / `ast_nodes` / `unit` / `type_children`
  tables.

- **`rename_zerolang.py`** — Heuristic word-boundary renamer. Used for
  files that don't have SQL dumps:
  - Python source: only substitutes inside string literals (uses
    `tokenize` so f-strings are skipped; their `{…}` regions hold code).
    Also handles mangled monomorphisation names like `list_i64` →
    `List_i64` via a `(?=_[a-z])` lookahead.
  - `.z` source (fallback when SQL not available): word-boundary
    substitution with comment stripping, LHS-of-colon protection
    (preserves parameter names / variant tag names / method LHSs),
    string-literal masking (preserves user-facing string content), and
    a method-call exemption for type-named return-conversion methods
    (`x.string`, `x.stringview`, `x.list`, `x.listview`, `x.byteview`).

- **`naming_audit.md`** — The rename map: every `.z` definition that
  needs to change, classified by kind (reftype → PascalCase, function
  → camelCase / get-prefix-drop, constant → camelCase, etc.).
  Generated against the snapshot of `lib/system/*.z` + `examples/*.z`
  at the time the styleguide was authored. Used as documentation; the
  actual rename map is hard-coded in the Python scripts so they stay
  self-contained.

## Pipeline

The intended end-to-end flow for a green Phase 1 commit:

```bash
# 1. Generate fresh SQL dumps from a clean tree.
for ex in examples/*.z; do
    name=$(basename "$ex" .z)
    uv run python src/zc.py --src examples "$name" --dump-sql /tmp/dump_$name.sql
done

# 2. Apply SQL-driven rename to lib/system + cross-referenced examples.
python3 tools/styleguide_apply/sql_rename.py /tmp/dump_*.sql .

# 3. Apply heuristic rename to remaining examples (those not covered by SQL).
python3 tools/styleguide_apply/rename_zerolang.py examples/*.z

# 4. Apply heuristic rename to compiler hardcoded refs and tests.
python3 tools/styleguide_apply/rename_zerolang.py \
    src/zemitterc.py src/ztypecheck.py src/ztypeutil.py src/zemitterc_runtime.py
python3 tools/styleguide_apply/rename_zerolang.py tests/*.py

# 5. Manual fix-up: a few compound string literals that the Python scanner
# can't reach by full-string match.
sed -i 's/"any\.valtype"/"Any.valtype"/g; s/"any\.reftype"/"Any.reftype"/g' \
    src/ztypecheck.py

# 6. Verify.
make check
make test
```

## Known limitations

The tooling produced **1237 / 1353 passing** tests (≈91% green) when last
run. The remaining 116 failures all stem from the same class of issue:
the C runtime templates in `src/zemitterc_runtime.py` (~2900 lines) hold
hardcoded lowercase C function names (`z_file_destroy`, `z_io_fill_filestat`,
`z_string_copy`, etc.) embedded in C-source string literals. After the
rename the compiler's mangler emits PascalCase calls (`z_File_destroy`)
because the mangler preserves type-name casing — so the runtime's
function definitions and the generated calls disagree, breaking ~14
`TestIoNativeDispatch`, ~15 `TestLockEnforcement`, ~10 `TestStrStringview`
plus assorted other test classes.

The Python word-boundary scanner can't catch these C identifiers because
`_` is a word character in Python regex (`\bfile\b` does not match
inside `z_file_destroy`).

There's also a related compiler-internal special case in
`ztypecheck.py:_set_field_cleanup_metadata` for the `File` class: the
branch sets `destructor_name = "z_file_destroy"` (lowercase, hardcoded),
but the early return on the children-with-destructor loop fires first
when File's protocol children (Reader/Writer/Closer/Seeker) are present,
so the branch never runs and `_set_destructor_metadata`'s
`f"z_{ztype.name}_destroy"` (PascalCase) wins.

## Resolving the blocker

Two paths to a green Phase 1 (one decision needed before the next session):

**Option A: regenerate the C runtime to PascalCase.** Update every
`z_<lowercase>_…` reference in `zemitterc_runtime.py` to `z_<PascalCase>_…`
where the prefix matches a renamed type. Tedious but mechanical (~200
string substitutions, doable with a targeted regex over the runtime
file). After that, re-run the pipeline above and iterate on residual
failures (likely under 20).

**Option B: lowercase type names at the C-mangling layer.** Modify
`_mangle_func` / `_type_cname` in `src/zemitterc.py` to lowercase the
type name when forming a C identifier. One change (~5 lines), works
against the project's "names mangle as-is" convention. The C runtime
stays as-is.

Option A is more idiomatic for this codebase. Option B is faster.

## Why this lives in `tools/` not `scripts/` or elsewhere

These scripts are one-shot project-wide refactor tooling, not part of
the build. After Phase 1 lands they remain useful as a template for
future sweeping renames (the SQL-driven approach generalises to any
identifier-class change), so they are worth keeping.
