# Function-Level Generic Parameters

**Date**: 2026-03-26
**Status**: Implemented (parser, AST, type checker)

---

## Summary

Add an `as` clause to function definitions for declaring generic type parameters. This
makes functions consistent with records, classes, unions, variants, and other compound
types which already use `as` for generic parameter declarations.

---

## Motivation

Currently, generic parameters can only be declared on compound types (records, classes,
unions, variants, facets, protocols, units). Functions detect generics by scanning their
`in` parameters for `any.generic` references (pass 1 in `_resolve_function_type`,
`ztypecheck.py:640-654`). This has several problems:

1. **Inconsistency**: Every other construct uses `as` for generics; functions are the
   exception.

2. **Ambiguous instantiation interface**: When a generic function is used in a type
   context (e.g. as a callback parameter), the caller must provide concrete types for
   all generic parameters. With the current approach, the generic parameters are mixed
   in with regular parameters, making it unclear which arguments are type-level and
   which are value-level.

3. **No visible ordering for positional generic args**: Zerolang's call convention
   allows the first argument to be unnamed. With an explicit `as` block, the ordering
   and naming of generic parameters is self-documenting — the first parameter in `as`
   is the positional one, the rest require names.

---

## Design

### Syntax

The `as` clause is added to the function definition grammar as an additional optional
named argument, following the same any-order convention as `in`, `out`, and `is`:

```
function_definition:
    "function" {
        ( [ "in" ] "{" { parameteritem | newline } "}" )
        | ( "out" typeref )
        | ( "is" primary-expression )
        | ( "as" "{" { label constant-expression | label-value } "}" )
    }
```

All four clauses (`in`, `out`, `is`, `as`) can appear in any order. When `as` is
present and appears before the parameter block, `in` must be explicitly stated (it can
no longer be the unnamed first argument since `as` occupies that first position).

### Examples

Single generic parameter:

```zerolang
identity: function as { t: any.generic } in { val: t } out t is {
    return val
}
```

Multiple generic parameters:

```zerolang
map: function
    as { t: any.generic u: any.generic }
    in { list: list.of(t) f: function in { item: t } out u }
    out list.of(u)
is {
    # ...
}
```

Numeric generic parameter:

```zerolang
repeat: function
    as { n: u64.generic }
    in { val: i64 }
    out i64
is {
    result: 0
    for n loop { result = result + val }
    return result
}
```

Clauses in any order (equivalent to the identity example above):

```zerolang
identity: function in { val: t } out t as { t: any.generic } is {
    return val
}
```

Function spec (no body):

```zerolang
transformer: function as { t: any.generic u: any.generic } in { val: t } out u
```

### Instantiation at call sites

In code, generic type arguments are specified inline with value arguments.
The compiler infers generic types from concrete value arguments where possible:

```zerolang
# given: as { t: any.generic u: any.generic }

# inferred from value arguments (preferred)
result: map list: mylist f: to_string

# explicit generic args inline with value args
result: map t: i64 u: str list: mylist f: to_string
```

Note: parenthesized instantiation like `(map i64 u: str)` is not valid in
code — every expression in code must be executable. Generic instantiation
without a call belongs at the unit level.

### Explicit `as` is required

Generic parameters declared directly in the `in` block (the current mechanism) become
a compilation error. The compiler must emit an error such as:

> Generic parameters must be declared in the 'as' section, not 'in': 'x'

This error message already exists for compound types (`ztypecheck.py:785`). The same
rule now applies to functions.

### Methods vs static functions

**Methods** (functions with a `this` parameter in `in`) cannot have their own `as`
block. They inherit the generic parameters of their enclosing type. Rationale:
method-level generics cannot affect the object's stored fields (the layout is fixed at
type-instantiation time), and all use cases for method-level generics (map, fold,
conversion) are well-served by free functions or static functions.

**Static functions** (functions in a type's `as` block that do not take `this`) may
have their own `as` block. They are essentially namespaced free functions and are
independently monomorphized.

If a method (has `this`) also has an `as` block, the compiler must emit an error:

> Methods cannot declare generic parameters; move the generic parameter to the type
> definition, or make this a static function

---

## Implementation Plan

### Phase 1: Parser — accept `as` on function definitions [done]

**File**: `src/zparser.py`, method `_acceptfunction()`

Added `elif` branch for `TT.AS` in the function parser loop. Parses the block
using `_getobjectbody()` with `allowtag=False`, `unlabelledpath=False`,
`unlabelledid=False`. Sets `first = False` so `in` requires explicit keyword
after `as`. Handles extern promotion from `as_body`, removing as-locals from
the extern set.

### Phase 2: AST — add `as` fields to Function node [done]

**File**: `src/zast.py`, class `Function`

Added `as_items: Dict[str, "Path"]` and `as_functions: Dict[str, "Function"]`
fields with `field(default_factory=dict)`, matching the pattern used by Record,
Class, Union, Variant.

### Phase 3: Type checker — resolve generics from `as` instead of `in` [done]

**File**: `src/ztypecheck.py`, method `_resolve_function_type()`

Pass 1 now scans `func.as_items` for generic params. Pass 2 scans
`func.parameters` and errors if any resolves to `__generic_param`. Static
functions in the `as` block (`func.as_functions`) are resolved via recursive
`_resolve_function_type` calls.

### Phase 4: Type checker — method restriction [done]

**File**: `src/ztypecheck.py`, method `_resolve_function_type()`

After pass 1, checks if the function has both generic params (from `as`) and a
parameter whose *type* is `this` (checking both bare `AtomId` and `DottedPath`
with parent `this`). If so, emits the error. This check is in
`_resolve_function_type` itself so it applies regardless of where the method is
defined.

### Phase 5: Monomorphization — support function-level generics [done]

**File**: `src/ztypecheck.py`

Added `_infer_generic_function_call()` and `_monomorphize_function()` methods.
When a generic function is called:

1. Explicit type arguments (named args matching `generic_params`) are extracted.
2. Remaining value arguments infer generic params from their types.
3. Default types fill in any still-unresolved params.
4. Conflicts between explicit and inferred types produce errors.
5. The monomorphized function gets a mangled name (`funcname_arg1_arg2`), cloned
   body, and is type-checked with the concrete generic context.
6. Results are cached and stored in `program.mono_functions`.

Generic function bodies are not type-checked at definition time (only during
monomorphization), since parameter types reference unresolved generic params.

### Phase 6: Emitter — emit monomorphized functions [done]

**File**: `src/zemitterc.py`

1. Each monomorphized function instance is emitted as a separate C function.
2. `_emit_callable_expr()` detects calls to monomorphized functions (via
   `generic_origin`) and uses the mangled name.
3. `_is_generic_template()` updated to detect generics in `as_items` (not just
   `parameters`) and to handle the `(constraint.generic default: type)` call form.

### Phase 7: Grammar and spec documentation [done]

**Files**: `doc/grammar.pdoc`, `doc/spec.pdoc`

Updated the `function_definition` production in the grammar to include the `as`
clause. Updated the spec's Function Definition Arguments section: revised the
arguments table, added `as` clause documentation, updated `in` parameter
description to remove generic references, added method restriction
documentation, replaced the old TODO generic example with correct `as`-based
examples. Updated the Generic Types section to reference function `as` clauses.

### Phase 8: Tests [done]

Added 8 parser tests (`TestFunctionAsClause`):
- `as` before `in`, after `out`, between `in` and `out`
- Multiple generic params
- Duplicate `as` error
- Empty `as_items` default
- Static function in `as`
- Explicit `in` required after `as`

Added 5 type checker tests:
- Multiple generic params resolve correctly
- Any clause order resolves correctly
- Generic params in `in` produce error
- Method with `as` produces error
- Static function in type's `as` with own `as` is allowed

Added 14 type checker tests for generic function calls:
- Single/multiple arg inference, conflict detection, explicit args
- Constraint violation, monomorphization caching, return type resolution
- Inline generic + value args, multiple inline generic args
- Default types: used, overridden by explicit, overridden by inference

Added 5 emitter tests for generic function emission:
- Template not emitted, monomorphized function emitted
- Call site uses mangled name, multiple instantiations
- Compile and run end-to-end test

### Phase 9: Migration [done]

Migrated `lib/system/system.z` `return` function from inline generic param to
`as` clause. Migrated `test_generic_function_resolution` test to use new
syntax. No example files required migration.

---

## Breaking Changes

This is a **breaking change** for any code that declares generic parameters directly in
function parameter blocks. All such declarations must move to an `as` block. The error
message guides users to the fix.

Migrated in this change: `lib/system/system.z` `return` function. No example files
required migration (generics were only on compound types).

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| `as` is required (no inference-only) | Explicit is better than implicit. The `as` block documents the instantiation interface. |
| Methods cannot have `as` | Method-level generics can't affect stored fields. Use free/static functions instead. |
| Static functions can have `as` | They are namespaced free functions, independently monomorphized. |
| Generic params in `in` are an error | Clean break, no ambiguity. Same rule already enforced for compound types. |
| Any-order clauses | Consistent with existing function syntax. No special position required for `as`. |

---

## Open Questions

1. **Should `as` on a function ever hold non-generic items?** For compound types, `as`
   holds methods, constants, and generics. For functions, it currently only holds
   generics. This is a narrowing, not a conflict — but worth noting if function-level
   `as` might grow in scope later.

2. **Inference rules**: When a generic function is called and all generic params can be
   inferred from value arguments, is the `as` block at the *call site* omittable? (Yes
   — the `as` block is on the *definition*, not the call. Inference at call sites works
   exactly as it does for compound types today.)
