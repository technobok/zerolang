# TODO: String ownership bugs found by ASan sweep

Post-refactor ASan sweep across all 46 examples found:
- **42 pass clean**
- **4 have real issues**:
  1. `arrays.z` — intentional OOB in example (exits with error code from
     bounds check). Not a bug, but the example should be updated so it
     demonstrates the return-false behavior it describes, or the runtime
     should change to return false instead of aborting.
  2. `linkedlist.z` — string passed to class constructor leaks (never freed)
  3. `maps.z` — string passed as map key leaks (never freed)
  4. `ownership.z` — string passed to class constructor causes use-after-free

## Root cause

Before Phase 4, `string` was `z_string_t*` (heap pointer). Passing a string
by value copied the pointer; freeing either side freed the shared heap.

After Phase 4, `string` is `z_string_t` (stack struct with `{data*, size,
capacity}`). Passing by value copies the struct — both the source and
destination now hold the same `data*` pointer. Without explicit
ownership-transfer invalidation:
- If source is freed → destination's `data` becomes dangling (use-after-free)
- If source is NOT freed → no one frees the heap `data` buffer (leak)

The current emitter is inconsistent:
- **linkedlist**: source NOT removed from frees BUT NOT freed either
  (scope cleanup seems to have skipped it but not for the right reason).
  Result: leak.
- **ownership**: source freed at scope exit; destination retains the
  now-dangling pointer. Result: use-after-free.

## The pattern

All three bugs match this shape:
```
t: string = <string producer>    // _t = z_string_from_view(...)
c: class_with_string_field ... label: t   // _c = z_class_create(_t, ...)
// what should happen: _t is invalidated (data = NULL) here
// what actually happens: inconsistent — freed or leaked
```

## Proposed fix direction

Treat string arguments passed to constructor parameters as
ownership-transfer (implicit `.take`). At the call site:
1. Remove the source temp from `frees` (destination now owns the data)
2. Zero-init the source's struct (so if something else references it
   by name, the pointer is NULL and `z_string_free(NULL)` is a safe no-op)

This is the same pattern already implemented for `box from: val`
(see Phase 10, `_emit_box_create` in `src/zemitterc.py`):
```python
# ownership transferred to boxed copy — remove source from frees
if val in self._temp.frees:
    self._temp.frees.remove(val)
# handle explicit .take — invalidate source variable
take_var = self._get_take_var(value_arg.valtype)
if take_var:
    val_type = self._get_operation_type(value_arg.valtype)
    self._temp.decls.append(
        self._emit_take_invalidation(take_var, val_type, indent)
    )
```

The same logic needs to apply for:
- Class constructor args where the param is a heap-backed type
- Map `.set` key/value args where the type is heap-backed
- Any function param with effective TAKE ownership where the type is
  a stack-struct + heap-data like `string` (and future similar types)

## Files to investigate

- `src/zemitterc.py` — function call emission, particularly class
  construction (`_emit_call_value` around the class path) and the
  implicit-take logic
- `_build_meta_create_args` and `_build_create_args` — already remove
  source from frees for some paths; needs auditing for string fields
- `src/ztypecheck.py` — parameter ownership inference for string params

## Verification

After fix, these should be ASan-clean:
```
gcc -fsanitize=address,leak -o /tmp/t out/ownership.c && /tmp/t
gcc -fsanitize=address,leak -o /tmp/t out/linkedlist.c && /tmp/t
gcc -fsanitize=address,leak -o /tmp/t out/maps.c && /tmp/t
```

And the full 46-example ASan sweep (minus arrays.z's intentional OOB)
should pass clean.
