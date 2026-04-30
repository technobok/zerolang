"""
Typing — typecheck-output container.

The parser produces a `zast.Program` (immutable post-parse). The
typechecker takes that `Program` and produces a `Typing`: the
container of every typecheck-derived datum the downstream consumers
(emitter, SQL dumper, asthash) need to read.

Architecturally:

    zast.Program (frozen tree of parsed nodes)
        ↓  TypeChecker(...).check()
    ztyping.Typing (mutable container; component tables)
        ↓
    CEmitter / zsqldump / zasthash

`Typing` is owned by the typechecker module conceptually but lives
in its own file so consumers can import it without depending on
`ztypecheck`. Mirrors the `zast` / `zparser` split: data module
separate from producer.

F5.E.1 introduced this module as an empty container. F5.E.2 (this
commit) relocated the 19 nodeid-keyed component dicts off
`zast.Program` onto `Typing`. F5.E.3 will relocate the older
typecheck-output fields (`mono_types`, `func_aliases`,
`cloned_methods`, `resolved`, `unit_types_by_id`, `symbol_table`,
`is_error`) and freeze `zast.Program`. F5.E.4 deletes the
typed-tree mirror, after which the component tables here are the
typed program.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

import zast
from ztypes import ZType, ZOwnership
from zast import CallKind


@dataclass
class Typing:
    """Result of typechecking. See module docstring for context.

    The 19 component tables below are keyed by parsed `Node.nodeid`
    and hold typecheck-derived data that used to live as `init=False`
    columns on parsed AST nodes (Step 6.x of the typed-tree
    migration). Each table is one row per parsed node — a future
    SQL representation has one column per dict (or one child table
    when the value is a list/set), keyed by `node_id`.
    """

    parsed: zast.Program

    is_error: bool = field(default=False)

    # ----- Component tables (F5.E.2: relocated from zast.Program).

    # Per-Node resolved type (was `zast.Node.type`, stripped in
    # Step 6.9.b). Populated by every `_check_*` / `_resolve_*` /
    # typeref-resolution path; read by typed-mirror builders, by
    # typecheck-internal cross-method lookups, and via
    # `TypedProgram.node_types` by emitter / SQL-dump / asthash
    # consumers.
    node_type: Dict[int, "Optional[ZType]"] = field(default_factory=dict, init=False)
    # Per-Node compile-time constant value (was `zast.Node.const_value`,
    # stripped in Step 6.9.a). Values are int / float / bool / str
    # — same as `ztypedast.ConstValue`. Inlined rather than imported
    # because ztypedast imports zast.
    node_const_value: Dict[int, "int | float | bool | str"] = field(
        default_factory=dict, init=False
    )
    # Per-Call classification (was `zast.Call.call_kind` /
    # `.callable_type_name`). `call_kind` discriminates the emission
    # shape (REGULAR / RECORD_CREATE / RETURN / CALLABLE / ...);
    # `callable_type_name` is the mangled type name when the call
    # dispatches as a callable-object method.
    call_kind: Dict[int, CallKind] = field(default_factory=dict, init=False)
    call_callable_type_name: Dict[int, str] = field(default_factory=dict, init=False)
    # Per-Expression wrapper control-flow classification (was
    # `zast.Expression.call_kind`, stripped in Step 6.10).
    expr_call_kind: Dict[int, CallKind] = field(default_factory=dict, init=False)
    # Per-Do break flag (was `zast.Do.has_break`).
    do_has_break: Dict[int, bool] = field(default_factory=dict, init=False)
    # Per-Case subject-taken flag (was `zast.Case.subject_taken`).
    case_subject_taken: Dict[int, bool] = field(default_factory=dict, init=False)
    # Per-For iterator-binding names (was `zast.For.iterator_bindings`).
    # The set of `name:` bindings whose operation auto-unwraps an
    # `option` value at each iteration.
    for_iter_bindings: Dict[int, "set[str]"] = field(default_factory=dict, init=False)
    # Per-If / per-Case post-block ownership cleanup (was
    # `zast.If.taken_vars` / `zast.Case.taken_vars`). `(name, ZType)`
    # tuples for variables consumed in some arm so the emitter knows
    # which to destruct on the merge path.
    if_taken_vars: Dict[int, "list[tuple[str, Optional[ZType]]]"] = field(
        default_factory=dict, init=False
    )
    case_taken_vars: Dict[int, "list[tuple[str, Optional[ZType]]]"] = field(
        default_factory=dict, init=False
    )
    # Per-AtomId narrowing stamps (was `zast.AtomId.narrowed_subtype`
    # / `.original_ztype` / `.child_id`).
    atom_narrowed_subtype: Dict[int, str] = field(default_factory=dict, init=False)
    atom_original_ztype: Dict[int, ZType] = field(default_factory=dict, init=False)
    atom_child_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-DottedPath stamps (was `zast.DottedPath.parent_tagged_type`
    # / `.child_id`). `parent_tagged_type` records the outer
    # union/variant when a dotted path resolves to one of its tagged
    # subtypes (`r.ok`, `Result.err`, ...). `child_id` is the
    # Phase-7b stamp resolving the child name against the parent's ZType.
    dp_parent_tagged_type: Dict[int, ZType] = field(default_factory=dict, init=False)
    dp_child_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-With binding ownership + alias target (was
    # `zast.With.ownership` / `.alias_of`).
    with_ownership: Dict[int, ZOwnership] = field(default_factory=dict, init=False)
    with_alias_of: Dict[int, "Optional[str]"] = field(default_factory=dict, init=False)
    # Per-Assignment alias target (was `zast.Assignment.alias_of`).
    assign_alias_of: Dict[int, "Optional[str]"] = field(
        default_factory=dict, init=False
    )
    # Per-argument protocol projection stamps (was
    # `zast.NamedOperation.projected_*`). Read by `_build_typed_call`
    # to populate `TypedNamedOperation`.
    projected_args: Dict[
        int,
        "tuple[Optional[ZType], Optional[str], Optional[str]]",
    ] = field(default_factory=dict, init=False)
