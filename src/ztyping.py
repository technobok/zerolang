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

`TypedProgramView` is a thin compat shim exposing a few `Typing`
component tables under their legacy `typed_program.X` access path.
Pre-F5.E.4.d this was the full structural typed-tree mirror in
`ztypedast.py`; F5.E.4.d deleted that hierarchy and replaced it
with this view object. The canonical access path is `typing.X`
directly; `typed_program.X` survives only for the test corpus.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import zast
from ztypes import ZType, ZOwnership
from zast import CallKind
from zsymtab_proto import SymbolTableProto


@dataclass
class TypeChild:
    """One row of the flat children table (F5.H).

    Replaces a `(parent_ztype, name) → child_ztype` entry from
    `ZType.children`. `child_name_id` is the monotonic id minted by
    `ZType.child_id_for(child_name)` (Phase 7b children_id_map);
    `position` preserves declaration order (rows appear in
    insertion order in `Typing.type_child` per parent).

    `child_type` is the in-memory ZType reference, kept alongside the
    id for fast resolution during the F5.H transition. SQL dumps
    write `child_type_id` only; in-memory consumers read `child_type`.
    A future refactor (post-F5.H) can drop the ref once a global
    nodeid → ZType registry is in place.
    """

    parent_type_id: int
    child_name: str
    child_name_id: int
    child_type_id: int
    position: int
    child_type: "ZType"


@dataclass
class TypeGenericArg:
    """One row of the flat generic-args table (F5.H).

    Replaces a `(parent_ztype, param_name) → arg_ztype` entry from
    `ZType.generic_args`. `arg_type` is the in-memory ZType
    reference (transitional; see TypeChild docstring).
    """

    parent_type_id: int
    param_name: str
    arg_type_id: int
    arg_type: "ZType"


@dataclass
class TypedProgramView:
    """Thin compat namespace exposing legacy `typed_program.X` access
    to a few `Typing` component tables. Each field aliases the
    corresponding `Typing.X` dict (same object, not a copy)."""

    node_types: Dict[int, "Optional[ZType]"]
    expr_call_kinds: Dict[int, CallKind]
    node_const_value: Dict[int, "int | float | bool | str"]
    call_kind: Dict[int, CallKind]
    dp_child_id: Dict[int, int]
    atom_child_id: Dict[int, int]


@dataclass
class Typing:
    """Result of typechecking. See module docstring for context.

    The 19 nodeid-keyed component tables below hold typecheck-derived
    data that used to live as `init=False` columns on parsed AST
    nodes (Step 6.x of the typed-tree migration). Each table is one
    row per parsed node — a future SQL representation has one column
    per dict (or one child table when the value is a list/set),
    keyed by `node_id`.
    """

    parsed: zast.Program

    # Errors collected during typecheck. `is_error` is True iff non-empty.
    errors: List["zast.Error"] = field(default_factory=list, init=False)
    is_error: bool = field(default=False, init=False)

    # ----- Aggregate typecheck state (F5.E.3: relocated from zast.Program).

    # monomorphized generic types: list of (mono_ztype, original_ast_node)
    mono_types: List = field(default_factory=list, init=False)
    # monomorphized generic functions: list of (mono_ztype, cloned_function)
    mono_functions: List = field(default_factory=list, init=False)
    # dedup aliases: {qualified_alias_name: qualified_canonical_name}
    func_aliases: Dict[str, str] = field(default_factory=dict, init=False)
    # cloned methods per mono type: {mono_name: {mname: Function}}
    cloned_methods: Dict[str, Dict[str, "zast.Function"]] = field(
        default_factory=dict, init=False
    )
    # resolved type names: {qualified_name: ZType}
    resolved: Dict[str, ZType] = field(default_factory=dict, init=False)
    # Unit AST nodeid → resolved unit ZType (Phase 7d).
    unit_types_by_id: Dict[int, ZType] = field(default_factory=dict, init=False)
    # Phase-7c symbol table (scope/entry/variable hierarchy). Typed via
    # `SymbolTableProto` to keep `ztyping` decoupled from `zenv`.
    symbol_table: Optional[SymbolTableProto] = field(default=None, init=False)
    # F5.E.4.d compat shim — see `TypedProgramView` docstring.
    typed_program: Optional[TypedProgramView] = field(default=None, init=False)

    # ----- Component tables (F5.E.2: relocated from zast.Program).

    # Per-Node resolved type (was `zast.Node.type`, stripped in Step 6.9.b).
    node_type: Dict[int, "Optional[ZType]"] = field(default_factory=dict, init=False)
    # Per-Node compile-time constant value (was `zast.Node.const_value`,
    # stripped in Step 6.9.a). Values are int / float / bool / str.
    node_const_value: Dict[int, "int | float | bool | str"] = field(
        default_factory=dict, init=False
    )
    # Per-Call classification (was `zast.Call.call_kind` /
    # `.callable_type_name`).
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
    for_iter_bindings: Dict[int, "set[str]"] = field(default_factory=dict, init=False)
    # Per-If / per-Case post-block ownership cleanup (was
    # `zast.If.taken_vars` / `zast.Case.taken_vars`).
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
    # / `.child_id`).
    dp_parent_tagged_type: Dict[int, ZType] = field(default_factory=dict, init=False)
    dp_child_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-With binding ownership + alias target (was `zast.With.ownership`
    # / `.alias_of`).
    with_ownership: Dict[int, ZOwnership] = field(default_factory=dict, init=False)
    with_alias_of: Dict[int, "Optional[str]"] = field(default_factory=dict, init=False)
    # Per-Assignment alias target (was `zast.Assignment.alias_of`).
    assign_alias_of: Dict[int, "Optional[str]"] = field(
        default_factory=dict, init=False
    )
    # Per-argument protocol projection stamps (was
    # `zast.NamedOperation.projected_*`).
    projected_args: Dict[
        int,
        "tuple[Optional[ZType], Optional[str], Optional[str]]",
    ] = field(default_factory=dict, init=False)

    # ----- Flat ZType.children / generic_args tables (F5.H).
    #
    # F5.H.1: introduced empty alongside the existing `ZType.children`
    # / `ZType.generic_args` dicts. F5.H.2 populates them at every
    # write site; F5.H.3/4 migrate consumers via the helper methods
    # below; F5.H.5 removes the dicts and the helpers become the
    # only access path.
    type_child: List[TypeChild] = field(default_factory=list, init=False)
    type_generic_arg: List[TypeGenericArg] = field(default_factory=list, init=False)

    # ---- F5.H.3 read accessors (table-backed) ----

    def child_of(self, parent: ZType, name: str) -> "Optional[ZType]":
        """`parent.children.get(name)` equivalent. Linear scan over
        `type_child`; child counts per parent are small (handful of
        methods/fields per type), so this is acceptable."""
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid and row.child_name == name:
                return row.child_type
            i += 1
        return None

    def children_of(self, parent: ZType) -> "List[tuple[str, ZType]]":
        """`list(parent.children.items())` equivalent. Pairs are in
        declaration order."""
        out: "List[tuple[str, ZType]]" = []
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid:
                out.append((row.child_name, row.child_type))
            i += 1
        return out

    def child_names_of(self, parent: ZType) -> "List[str]":
        """`list(parent.children.keys())` equivalent."""
        out: "List[str]" = []
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid:
                out.append(row.child_name)
            i += 1
        return out

    def child_types_of(self, parent: ZType) -> "List[ZType]":
        """`list(parent.children.values())` equivalent."""
        out: "List[ZType]" = []
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid:
                out.append(row.child_type)
            i += 1
        return out

    def has_child(self, parent: ZType, name: str) -> bool:
        """`name in parent.children` equivalent."""
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid and row.child_name == name:
                return True
            i += 1
        return False

    def child_by_id(self, parent: ZType, cid: int) -> "Optional[ZType]":
        """Reverse lookup: child ZType whose name has minted id `cid`
        on `parent`. Returns None if no live row matches. Replaces the
        legacy `ZType.resolve_child_by_id` once the dict goes away."""
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid and row.child_name_id == cid:
                return row.child_type
            i += 1
        return None

    def child_count(self, parent: ZType) -> int:
        """`len(parent.children)` equivalent."""
        c = 0
        rows = self.type_child
        n = len(rows)
        i = 0
        while i < n:
            if rows[i].parent_type_id == parent.nodeid:
                c += 1
            i += 1
        return c

    def generic_arg_of(self, parent: ZType, name: str) -> "Optional[ZType]":
        """`parent.generic_args.get(name)` equivalent."""
        rows = self.type_generic_arg
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid and row.param_name == name:
                return row.arg_type
            i += 1
        return None

    def generic_args_of(self, parent: ZType) -> "List[tuple[str, ZType]]":
        """`list(parent.generic_args.items())` equivalent."""
        out: "List[tuple[str, ZType]]" = []
        rows = self.type_generic_arg
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if row.parent_type_id == parent.nodeid:
                out.append((row.param_name, row.arg_type))
            i += 1
        return out

    def has_generic_args(self, parent: ZType) -> bool:
        """Truthy `parent.generic_args` equivalent (non-empty test)."""
        rows = self.type_generic_arg
        n = len(rows)
        i = 0
        while i < n:
            if rows[i].parent_type_id == parent.nodeid:
                return True
            i += 1
        return False
