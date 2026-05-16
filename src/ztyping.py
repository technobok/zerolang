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
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import zast
from ztypes import ZType, ZOwnership, ZParamOwnership
from zast import CallKind
from zsymtab_proto import ZSymbolTableProto


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

    # Folded sidecars (formerly per-(parent, child-name) sets/dicts on
    # ZType — see plans/line-count-increase-is-twinkling-willow.md):
    is_private: bool = False  # field declared with .private modifier
    is_lock_field: bool = False  # class field declared with .lock modifier
    is_lock_arm: bool = False  # union arm declared with .lock modifier
    default: "Optional[str]" = None  # C-level default expression for the param/field
    param_ownership: "Optional[ZParamOwnership]" = None  # take/borrow/lock annotation


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
    mono_types: List[Tuple[ZType, "zast.TypeDefinition"]] = field(
        default_factory=list, init=False
    )
    # monomorphized generic functions: list of (mono_ztype, cloned_function)
    mono_functions: List[Tuple[ZType, "zast.Function"]] = field(
        default_factory=list, init=False
    )
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
    # `ZSymbolTableProto` to keep `ztyping` decoupled from `zenv`.
    symbol_table: Optional[ZSymbolTableProto] = field(default=None, init=False)

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

    # ---- F5.H children/generic_arg setters + accessors ----
    #
    # `set_*` is idempotent on the (parent, name) key — repeat call
    # updates the existing row in place. Reads scan linearly; per-parent
    # row counts are small (handful of methods/fields per type).

    def set_child(self, parent: ZType, name: str, child: ZType) -> None:
        name_id = parent.child_id_for(name)
        pos = 0
        for row in self.type_child:
            if row.parent_type_id != parent.type_id:
                continue
            if row.child_name_id == name_id:
                row.child_type_id = child.type_id
                row.child_type = child
                return
            pos += 1
        self.type_child.append(
            TypeChild(parent.type_id, name, name_id, child.type_id, pos, child)
        )

    def set_generic_arg(self, parent: ZType, name: str, arg: ZType) -> None:
        for row in self.type_generic_arg:
            if row.parent_type_id == parent.type_id and row.param_name == name:
                row.arg_type_id = arg.type_id
                row.arg_type = arg
                return
        self.type_generic_arg.append(
            TypeGenericArg(parent.type_id, name, arg.type_id, arg)
        )

    def child_of(self, parent: ZType, name: str) -> "Optional[ZType]":
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                return row.child_type
        return None

    def child_by_id(self, parent: ZType, cid: int) -> "Optional[ZType]":
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name_id == cid:
                return row.child_type
        return None

    def has_child(self, parent: ZType, name: str) -> bool:
        return self.child_of(parent, name) is not None

    def set_child_private(self, parent: ZType, name: str) -> None:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                row.is_private = True
                return

    def is_child_private(self, parent: ZType, name: str) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                return row.is_private
        return False

    def set_child_lock_field(self, parent: ZType, name: str) -> None:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                row.is_lock_field = True
                return

    def is_child_lock_field(self, parent: ZType, name: str) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                return row.is_lock_field
        return False

    def lock_field_names_of(self, parent: ZType) -> "List[str]":
        out: "List[str]" = []
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.is_lock_field:
                out.append(row.child_name)
        return out

    def has_any_lock_field(self, parent: ZType) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.is_lock_field:
                return True
        return False

    def set_child_lock_arm(self, parent: ZType, name: str) -> None:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                row.is_lock_arm = True
                return

    def is_child_lock_arm(self, parent: ZType, name: str) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                return row.is_lock_arm
        return False

    def lock_arm_names_of(self, parent: ZType) -> "List[str]":
        out: "List[str]" = []
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.is_lock_arm:
                out.append(row.child_name)
        return out

    def has_any_lock_arm(self, parent: ZType) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.is_lock_arm:
                return True
        return False

    def set_child_default(self, parent: ZType, name: str, default: str) -> None:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                row.default = default
                return

    def child_default(self, parent: ZType, name: str) -> "Optional[str]":
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                return row.default
        return None

    def has_child_default(self, parent: ZType, name: str) -> bool:
        return self.child_default(parent, name) is not None

    def child_defaults_of(self, parent: ZType) -> "Dict[str, str]":
        out: "Dict[str, str]" = {}
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.default is not None:
                out[row.child_name] = row.default
        return out

    def has_any_default(self, parent: ZType) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.default is not None:
                return True
        return False

    def set_child_ownership(
        self, parent: ZType, name: str, ownership: ZParamOwnership
    ) -> None:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                row.param_ownership = ownership
                return

    def child_ownership(self, parent: ZType, name: str) -> "Optional[ZParamOwnership]":
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.child_name == name:
                return row.param_ownership
        return None

    def has_child_ownership(self, parent: ZType, name: str) -> bool:
        return self.child_ownership(parent, name) is not None

    def child_ownerships_of(self, parent: ZType) -> "Dict[str, ZParamOwnership]":
        out: "Dict[str, ZParamOwnership]" = {}
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.param_ownership is not None:
                out[row.child_name] = row.param_ownership
        return out

    def has_any_ownership(self, parent: ZType) -> bool:
        for row in self.type_child:
            if row.parent_type_id == parent.type_id and row.param_ownership is not None:
                return True
        return False

    def children_of(self, parent: ZType) -> "List[tuple[str, ZType]]":
        out: "List[tuple[str, ZType]]" = []
        for row in self.type_child:
            if row.parent_type_id == parent.type_id:
                out.append((row.child_name, row.child_type))
        return out

    def child_names_of(self, parent: ZType) -> "List[str]":
        out: "List[str]" = []
        for row in self.type_child:
            if row.parent_type_id == parent.type_id:
                out.append(row.child_name)
        return out

    def child_types_of(self, parent: ZType) -> "List[ZType]":
        out: "List[ZType]" = []
        for row in self.type_child:
            if row.parent_type_id == parent.type_id:
                out.append(row.child_type)
        return out

    def child_count(self, parent: ZType) -> int:
        c = 0
        for row in self.type_child:
            if row.parent_type_id == parent.type_id:
                c += 1
        return c

    def generic_arg_of(self, parent: ZType, name: str) -> "Optional[ZType]":
        for row in self.type_generic_arg:
            if row.parent_type_id == parent.type_id and row.param_name == name:
                return row.arg_type
        return None

    def generic_args_of(self, parent: ZType) -> "List[tuple[str, ZType]]":
        out: "List[tuple[str, ZType]]" = []
        for row in self.type_generic_arg:
            if row.parent_type_id == parent.type_id:
                out.append((row.param_name, row.arg_type))
        return out

    def has_generic_args(self, parent: ZType) -> bool:
        for row in self.type_generic_arg:
            if row.parent_type_id == parent.type_id:
                return True
        return False
