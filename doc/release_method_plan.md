# `.release` Special Method — Design & Implementation Plan

## Context

Zerolang's ownership model cleans up variables at scope exit. When a resource must be
released mid-function, the only option today is wrapping subsequent code in a `with`
block, which adds indentation. The `.release` method provides explicit early cleanup
for both owned and borrowed variables, reducing `with` nesting and making intent clear.

## 1. Pros & Cons

### Pros
- **Reduces nesting.** No more wrapping the rest of a function in `with` just to
  release a resource early.
- **Clearer intent.** `x.release` says "I am done with x." Standalone `x.take`
  (destroy without receiver) is ambiguous — take *what* to *where*?
- **Borrowed early-release.** `.take` errors on borrowed variables. `.release` can end
  a borrow early, unlocking the source — a capability that doesn't exist today.
- **Symmetry.** `.take` = extract value (transfer to receiver). `.release` = end
  lifetime (no value produced).

### Cons
- **More surface area.** One more compiler method to learn.
- **Overlap with standalone `.take`.** For owned reftypes, both destroy and invalidate.
  Mitigated by coexistence: `.take` stays for backwards compat, `.release` is the
  preferred idiom for explicit cleanup.
- **Scope-exit cleanup interaction.** Must handle the case where a variable was already
  released (pointer set to NULL; destroy is a no-op on NULL — already the pattern for
  `.take`).

## 2. Semantic Design

### Core rule
`x.release` immediately ends the lifetime of variable `x`.

| Variable state | Effect |
|---|---|
| OWNED reftype (class, union, string) | Call destructor, set pointer to NULL, invalidate name |
| OWNED valtype (record, enum, numeric) | Invalidate name (no destructor needed) |
| BORROWED variable (any type) | Release held locks (unlock source), invalidate name. No destructor |

### Statement-only
`.release` never produces a value. `y: x.release` is a compile error:
> "`.release` cannot be used as a value; use `.take` to transfer ownership"

### Error conditions

| Condition | Error message |
|---|---|
| Variable has active locks held by others | `Cannot release 'x': {lock_type} lock held by '{holder}'` |
| Variable is a function parameter | `Cannot release parameter 'x': lifetime is managed by the caller` |
| Variable is a top-level/static definition | `Cannot release top-level definition 'x'` |
| Used as a value (`y: x.release`) | `'.release' cannot be used as a value; use '.take' to transfer ownership` |
| Applied to non-variable (field path, literal) | `'.release' can only be applied to a variable name` |
| Variable already released/taken | Standard "undefined name" error (same as double `.take`) |

### Held locks
When releasing a variable that holds locks on others (`var.held_locks` non-empty),
those locks are released first — identical to scope-exit behavior. This is critical
for borrowed variables and born-borrowed records with `.lock` fields.

### Coexistence with `.take`
Both coexist silently. Standalone `.take` (no receiver) continues to work as before.
Documentation will recommend `.release` for explicit cleanup and `.take` for ownership
transfer.

### Borrowed reftypes
`.release` works on all borrows (valtypes and reftypes). It ends the borrow, releases
locks, and invalidates the name. No destructor is called (the owner is responsible).

## 3. Implementation

### 3.1 Type Checker (`src/ztypecheck.py`)

**In `_check_dotted_path` (after the `.take` block, ~line 5010):**

Add a new block:

```python
# handle .release compiler method
if child_name == "release":
    parent_type = self._check_path(path.parent)
    if parent_type:
        # .release only valid on simple variable names
        if path.parent.nodetype != NodeType.ATOMID:
            self._error(
                "'.release' can only be applied to a variable name",
                loc=path.start,
            )
            return parent_type

        release_name = cast(zast.AtomId, path.parent).name

        # cannot release a function parameter
        if release_name in self._current_func_ownership:
            self._error(
                f"Cannot release parameter '{release_name}': "
                f"lifetime is managed by the caller",
                loc=path.start,
            )
            return parent_type

        # cannot release a top-level definition
        defn = self._lookup_definition(release_name)
        if defn is not None:
            self._error(
                f"Cannot release top-level definition '{release_name}'",
                loc=path.start,
            )
            return parent_type

        var = self.symtab.lookup_var(release_name)
        if var:
            # cannot release if someone holds a lock on this variable
            if var.locks:
                entry = var.locks[0]
                self._error(
                    f"Cannot release '{release_name}': "
                    f"{entry.lock_type.name.lower()} lock held by '{entry.holder}'",
                    loc=path.start,
                )
                return parent_type

            # release any locks this variable holds on others
            self.symtab.release_held_locks(release_name)

        # invalidate the variable
        release_loc = (
            (path.start.lineno, path.start.colno, path.start.fsno)
            if path.start
            else None
        )
        self.symtab.invalidate(release_name, loc=release_loc)
        path.type = parent_type
        return parent_type
```

**Value-context check — in `_check_assignment` or wherever assignments resolve the
value expression:**

After checking the value expression, if the value is a DottedPath with
`child.name == "release"`, emit:
```
'.release' cannot be used as a value; use '.take' to transfer ownership
```

The simplest approach: check in `_check_assignment` after `_check_expression(assign.value)`
returns. If the value node is `NodeType.DOTTEDPATH` and `child.name == "release"`, error.

### 3.2 Symbol Table (`src/zenv.py`)

**No changes needed.** The existing `invalidate()`, `release_held_locks()`, and
`release_lock()` provide all required primitives.

### 3.3 Emitter (`src/zemitterc.py`)

**In `_emit_expression_stmt` (~line 3377), add a block for `.release` alongside the
existing `.take` block:**

```python
if (
    inner.nodetype == zast.NodeType.DOTTEDPATH
    and cast(zast.DottedPath, inner).child.name == "release"
):
    dp = cast(zast.DottedPath, inner)
    var = self._emit_path_value(dp.parent)
    var_type = dp.type
    result = ""
    # For owned reftypes: call destructor + nullify
    if var_type and var_type.subtype == ZSubType.STRING:
        result += f"{indent}zstr_free({var});\n"
    elif var_type and var_type.typetype == ZTypeType.CLASS:
        result += f"{indent}z_{var_type.name}_destroy({var});\n"
    elif var_type and var_type.typetype == ZTypeType.UNION:
        result += f"{indent}z_{var_type.name}_destroy({var});\n"
    # Nullify so scope-exit destroy is a no-op
    if var_type and _is_reftype(var_type):
        result += f"{indent}{var} = NULL;\n"
    # For borrowed variables or valtypes: no C code needed
    # (type checker already released locks and invalidated the name)
    return result
```

Note: For borrowed variables, the type checker has already validated the release. The
emitter emits nothing — borrow release is purely compile-time. For owned valtypes on
the stack, no C code is needed either. Only owned reftypes need destructor + NULL.

**Also update `_get_take_var` if it needs to recognize `.release`** — check whether
scope-exit cleanup code uses this to skip already-taken vars. If the NULL pattern
suffices (destroy functions check `if (!p) return;`), no change needed.

### 3.4 Parser (`src/zparser.py`)

**No changes needed.** `x.release` is already parsed as
`DottedPath(parent=AtomId("x"), child=AtomId("release"))`.

### 3.5 AST / Error Codes (`src/zast.py`)

**No changes needed.** The existing `ERR.OWNERERROR` (E0200) category covers
`.release` errors, since error messages containing "release" will be auto-categorized
via the ownership keywords check (may need to add "release" to `_OWNERSHIP_KEYWORDS`
if it's not already there).

Check `_OWNERSHIP_KEYWORDS` and add `"release"` if missing.

## 4. Test Plan

### 4.1 Type Checker Tests (`tests/test_typecheck.py`)

New class `TestReleaseCompilerMethod`:

| # | Test | Input | Expected |
|---|---|---|---|
| 1 | Basic owned valtype release | `x: 42; x.release` | OK, no errors |
| 2 | Release invalidates name | `x: 42; x.release; y: x` | Error: undefined/taken name |
| 3 | Release owned reftype | class instance; `c.release` | OK |
| 4 | Release borrowed valtype | `x: 42; y: x.borrow; y.release` | OK, y invalidated |
| 5 | Release borrowed reftype | class borrow; `d.release` | OK, d invalidated |
| 6 | Source unlocked after borrow release | `x: 42; y: x.borrow; y.release; z: x.take` | OK (x usable again) |
| 7 | Cannot release parameter | `f: function {a: i64} is { a.release }` | Error: cannot release parameter |
| 8 | Cannot release locked variable | `x: 42; y: x.borrow; x.release` | Error: lock held by y |
| 9 | Cannot release top-level definition | top-level name + `.release` | Error: cannot release top-level |
| 10 | Release as value is error | `y: x.release` | Error: cannot use as value |
| 11 | Release non-variable path | `x.field.release` | Error: only variable names |
| 12 | Double release | `x: 42; x.release; x.release` | Error: undefined name |
| 13 | Release born-borrowed with lock fields | born-borrowed record `.release` | OK, held locks released |
| 14 | Use source after born-borrowed release | create view, release it, use container | OK |

### 4.2 Emitter Tests (`tests/test_emitter.py`)

| # | Test | Verify |
|---|---|---|
| 1 | Release reftype emits destroy | C output contains `z_{name}_destroy(var);` and `var = NULL;` |
| 2 | Release valtype emits nothing | No destroy call in C output |
| 3 | Release borrowed emits nothing | No destroy call in C output |
| 4 | Scope cleanup after release | Scope-exit still has destroy call (safe no-op on NULL) |

### 4.3 End-to-End Examples

Add to `examples/ownership.z`:

```zero
# --- .release for early cleanup ---
h: box label: "hotel" value: 8
print "h alive: \{h.label}"
h.release
print "h released early"

# --- .release to end a borrow ---
j: box label: "juliet" value: 10
k: j.borrow
print "borrowed: \{k.label}"
k.release
# j is now unlocked — we can take it
consume j
```

Run `make build` to verify these compile and execute correctly.

## 5. Documentation Updates

### `doc/ownership.pdoc`
Add section "Early Release" after scope-exit explanation:
- Syntax: `x.release`
- Semantics for owned vs borrowed
- Error conditions
- Comparison with `.take` (when to use which)
- Example showing reduced nesting vs `with`

### `doc/spec.pdoc`
Add `.release` to the compiler methods list alongside `.take`, `.borrow`, `.lock`,
`.private`. Specify it is statement-only.

## 6. Implementation Order

```
Step 1: Type checker (ztypecheck.py)
   - Add .release handler in _check_dotted_path
   - Add value-context error check
   - Add "release" to _OWNERSHIP_KEYWORDS if missing
   |
Step 2: Type checker tests (test_typecheck.py)
   - All 14 tests from section 4.1
   - Run: make test (verify all pass)
   |
Step 3: Emitter (zemitterc.py)
   - Add .release handler in _emit_expression_stmt
   |
Step 4: Emitter tests (test_emitter.py)
   - 4 tests from section 4.2
   - Run: make test
   |
Step 5: Examples (examples/ownership.z)
   - Add .release examples
   - Run: make build (verify compile + run)
   |
Step 6: Documentation (doc/ownership.pdoc, doc/spec.pdoc)
   - Add Early Release section
   |
Step 7: make check (final verification)
```

## 7. Design Suggestions & Improvements

1. **Consider `.release` in `for` loop bodies.** If a variable is released inside a
   loop, the second iteration will hit "undefined name." This is correct behavior but
   should be documented with a clear example.

2. **Future: `defer x.release`.** A `defer` mechanism could schedule release at scope
   exit but define it at the point of creation. This is orthogonal to `.release` and
   could be added later.

3. **Linter hint.** When a `with` block's only purpose is scoping a single variable
   that could use `.release`, a future linter could suggest the refactoring.

4. **Error message quality.** The "lock held by" error should include the line where
   the lock was taken, using the existing `LockEntry` metadata (add location to
   `LockEntry` if not already present).
