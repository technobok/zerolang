# Styleguide Apply Tooling

Tooling that applied `doc/styleguide.pdoc` naming rules across the
codebase. The rename was landed in commits `8ffc9d3` (stdlib + compiler
+ tests + runtime), `aec5efa` (user-defined types in examples),
`ae7d115` (documentation), and `2be6c0a` (editor highlighters). These
scripts are preserved as a template for future sweeping renames.

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
  needed to change, classified by kind (reftype → PascalCase, function
  → camelCase / get-prefix-drop, constant → camelCase, etc.).
  Generated against the snapshot of `lib/system/*.z` + `examples/*.z`
  at the time the styleguide was authored.

## Pipeline (for future reference)

The end-to-end flow used to land the rename:

```bash
# 1. Generate fresh SQL dumps from a clean tree.
for ex in examples/*.z; do
    name=$(basename "$ex" .z)
    uv run python compiler0/zc.py --src examples "$name" --dump-sql /tmp/dump_$name.sql
done

# 2. Apply SQL-driven rename to lib/system + cross-referenced examples.
python3 tools/styleguide_apply/sql_rename.py /tmp/dump_*.sql .

# 3. Apply heuristic rename to remaining examples (those not covered by SQL).
python3 tools/styleguide_apply/rename_zerolang.py examples/*.z

# 4. Hand-port compiler refs and tests. The Python heuristic mode
#    substitutes inside Python string literals only — for compiler
#    files (zemitterc.py, zemitterc_runtime.py, ztypecheck.py,
#    ztypeutil.py) and test files this corrupts C-code-in-strings,
#    so the apply phase used a more targeted rewriter that only
#    touches `z_*` C identifiers + `_resolve_name("…")` lookups +
#    `name == "…"` comparisons. See git log for that work.

# 5. Verify.
make check
make test
make build
```

## Lessons for future sweeping renames

- The SQL renamer's `REFTYPES` map should include any user-defined
  type that needs renaming, not just the predefined stdlib types.
- The heuristic renamer applied to `.py` files mangles C-code embedded
  in string literals. For files that emit C source (notably
  `zemitterc_runtime.py`), prefer a targeted rewriter that only
  touches `z_*` C identifiers and known string-keyed lookups.
- After renaming method names from snake_case to camelCase, the
  generated tag-enum constants change too: `Z_PARSEERROR_TAG_INVALID_DIGIT`
  becomes `Z_PARSEERROR_TAG_INVALIDDIGIT` because the `.upper()` of
  `invalidDigit` collapses the underscore. Runtime helpers that
  reference these constants by hand need updating.
- Synthesised method dict keys (e.g. `mono.children["listview"]`)
  must keep the lowercase form for carve-out methods (`.string`,
  `.stringview`, `.list`, `.listview`, `.byteview`) per styleguide §3.5.
- Several string-keyed gates inside the runtime emitter test
  `if "<old_snake>" in natives:` — these need updating to the
  new camelCase form when the corresponding stdlib function is
  renamed.

## Why this lives in `tools/` not `scripts/` or elsewhere

These scripts are one-shot project-wide refactor tooling, not part of
the build. They remain useful as a template for future sweeping renames
(the SQL-driven approach generalises to any identifier-class change).
