"""
ZTyping — typecheck-output container.

The parser produces a `zast.Program` (immutable post-parse). The
typechecker takes that `Program` and produces a `ZTyping`: the
container of every typecheck-derived datum the downstream consumers
(emitter, SQL dumper, asthash) need to read.

Architecturally:

    zast.Program (frozen tree of parsed nodes)
        ↓  TypeChecker(...).check()
    ztyping.ZTyping (mutable container; component tables)
        ↓
    CEmitter / zsqldump / zasthash

`ZTyping` is owned by the typechecker module conceptually but lives
in its own file so consumers can import it without depending on
`ztypecheck`. Mirrors the `zast` / `zparser` split: data module
separate from producer.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import zast
from ztypes import ZConformance, ZType, ZOwnership, ZParamOwnership
from zast import CallKind
from zsymtab_proto import ZSymbolTableProto


@dataclass
class ZTypeChild:
    """One row of the flat children table.

    Each row represents a `(parent_ztype, child_name) → child_ztype`
    edge. `child_name_id` is the monotonic id minted by
    `ZType.child_id_for(child_name)`; `position` preserves
    declaration order (rows appear in insertion order in
    `ZTyping.type_child` per parent).

    `child_type` is the in-memory ZType reference, kept alongside the
    id for fast resolution. SQL dumps write `child_type_id` only;
    in-memory consumers read `child_type`.
    """

    parent_type_id: int
    child_name: str
    child_name_id: int
    child_type_id: int
    position: int
    child_type: "ZType"

    # Per-(parent, child-name) sidecars carried alongside the edge.
    is_private: bool = False  # field declared with .private modifier
    is_lock_field: bool = False  # class field declared with .lock modifier
    is_lock_arm: bool = False  # union arm declared with .lock modifier
    default: "Optional[str]" = None  # C-level default expression for the param/field
    param_ownership: "Optional[ZParamOwnership]" = None  # take/borrow/lock annotation


@dataclass
class ZTypeGenericArg:
    """One row of the flat generic-args table.

    Each row represents a `(parent_ztype, param_name) → arg_ztype`
    edge. `arg_type` is the in-memory ZType reference (carried
    alongside the id for fast resolution).
    """

    parent_type_id: int
    param_name: str
    arg_type_id: int
    arg_type: "ZType"


@dataclass
class ZTyping:
    """Result of typechecking. See module docstring for context.

    The nodeid-keyed component tables below hold typecheck-derived
    data; each table is one row per parsed node. A future SQL
    representation maps each dict to one column (or one child table
    when the value is a list/set), keyed by `node_id`.
    """

    parsed: zast.Program

    # Errors collected during typecheck. `is_error` is True iff non-empty.
    errors: List["zast.Error"] = field(default_factory=list, init=False)
    is_error: bool = field(default=False, init=False)

    # ----- Aggregate typecheck state.

    # monomorphized generic types: list of (mono_ztype, original_ast_node)
    mono_types: List[Tuple[ZType, "zast.TypeDefinition"]] = field(
        default_factory=list, init=False
    )
    # monomorphized generic functions: list of (mono_ztype, cloned_function)
    mono_functions: List[Tuple[ZType, "zast.Function"]] = field(
        default_factory=list, init=False
    )
    # protocol/facet conformance entities (Case A): one per (impl, spec, label),
    # carrying the pre-composed C names of its conformance helpers.
    conformance: List[ZConformance] = field(default_factory=list, init=False)
    # dedup aliases: {qualified_alias_name: qualified_canonical_name}
    func_aliases: Dict[str, str] = field(default_factory=dict, init=False)
    # cloned methods per mono type: {mono_name: {mname: Function}}
    cloned_methods: Dict[str, Dict[str, "zast.Function"]] = field(
        default_factory=dict, init=False
    )
    # resolved type names: {qualified_name: ZType}
    resolved: Dict[str, ZType] = field(default_factory=dict, init=False)
    # Unit AST nodeid → resolved unit ZType.
    unit_types_by_id: Dict[int, ZType] = field(default_factory=dict, init=False)
    # Symbol table (scope/entry/variable hierarchy). Typed via
    # `ZSymbolTableProto` to keep `ztyping` decoupled from `zenv`.
    symbol_table: Optional[ZSymbolTableProto] = field(default=None, init=False)

    # ----- Per-node component tables, keyed by parsed-AST nodeid.

    # Per-Node resolved type.
    node_type: Dict[int, "Optional[ZType]"] = field(default_factory=dict, init=False)
    # Per-Node compile-time constant value (int / float / bool / str).
    node_const_value: Dict[int, "int | float | bool | str"] = field(
        default_factory=dict, init=False
    )
    # Per-Node literal-base flavour ("dec"/"nondec"/"float"). Populated
    # for every node whose `node_type` is LITERAL_INT or LITERAL_FLOAT
    # — the default-resolution late pass reads it to pick the
    # concrete fallback type (i64/u64/f64). For BinOp results, the
    # base flavour propagates from the operands (see
    # `_check_binop_inner`).
    node_literal_base: Dict[int, str] = field(default_factory=dict, init=False)
    # Per-Call classification + resolved callable's type_id (for CALLABLE
    # dispatch; the emitter resolves the ZType by id, not by name).
    call_kind: Dict[int, CallKind] = field(default_factory=dict, init=False)
    call_callable_type_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-Call set of generic-arg names (e.g. `t` in `myrec t: i64 x: 5`)
    # that the dispatch stamped before falling through to the standard
    # call pipeline. `_check_call_arguments` skips any arg whose name
    # is in this set so the standard per-param matching loop sees only
    # value args.
    call_generic_param_names: Dict[int, "set[str]"] = field(
        default_factory=dict, init=False
    )
    # Per-Expression wrapper control-flow classification.
    expr_call_kind: Dict[int, CallKind] = field(default_factory=dict, init=False)
    # Per-Do break flag.
    do_has_break: Dict[int, bool] = field(default_factory=dict, init=False)
    # Per-Case subject-taken flag.
    case_subject_taken: Dict[int, bool] = field(default_factory=dict, init=False)
    # Per-Case set of arm match names that consumed the subject via
    # `.take`. The emitter zeroes the subject at the end of these
    # arms so the post-switch destroy doesn't double-free the heap
    # that the take already moved into another owner. Empty for cases
    # whose arms only read or borrow the subject.
    case_subject_taken_arms: Dict[int, "set[str]"] = field(
        default_factory=dict, init=False
    )
    # Per-For iterator-binding names.
    for_iter_bindings: Dict[int, "set[str]"] = field(default_factory=dict, init=False)
    # Per-For while-cond preamble: synth Assignment statements hoisted
    # out of the while-form cond's arg processing. The emitter prepends
    # these to the loop body and rewrites the loop into the `while (1) {
    # decls; if (!cond) break; body }` shape so the cond re-evaluates
    # each iteration. Empty for trivial conds (no hoist needed).
    for_cond_preamble: Dict[int, "list"] = field(default_factory=dict, init=False)
    # Per-If / per-Case post-block ownership cleanup.
    if_taken_vars: Dict[int, "list[tuple[str, Optional[ZType]]]"] = field(
        default_factory=dict, init=False
    )
    case_taken_vars: Dict[int, "list[tuple[str, Optional[ZType]]]"] = field(
        default_factory=dict, init=False
    )
    # Per-AtomId narrowing stamps.
    atom_narrowed_subtype: Dict[int, str] = field(default_factory=dict, init=False)
    atom_original_ztype: Dict[int, ZType] = field(default_factory=dict, init=False)
    atom_child_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-AtomId reference → resolved local variable_id. Present iff the
    # bare reference binds to a symbol-table variable (param / local /
    # with-binding / for-iterator / narrowed subject); absent for unit-level
    # definitions, core names, and numeric literals. The emitter reads this
    # to choose local vs unit-level emission without re-resolving by name,
    # so a local that shadows a unit-level namesake emits correctly.
    atom_variable_id: Dict[int, int] = field(default_factory=dict, init=False)
    # variable_id → C identifier, set once at the `define_var` chokepoint. The
    # emitter reads this (via atom_variable_id for refs, or the def-node stamp
    # for declarations) instead of re-mangling the variable's name string.
    variable_cname: Dict[int, str] = field(default_factory=dict, init=False)
    # Per-definition-node nodeid → the variable_id it binds. The complement of
    # atom_variable_id (which keys reference nodes): this keys the *declaring*
    # node — a parameter path, an assignment, a with-binding — so the emitter
    # reads `variable_cname[def_variable_id[node.nodeid]]` at declaration sites
    # instead of re-mangling the binding's name.
    def_variable_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-synth-temp-atom nodeid → the variable_id of the local the temp's
    # alias resolves to. Call-arg hoisting turns an alias-safe projection
    # (`x.lock` / `x.private` / `x.borrow` / `x.take` of a bare local) into a
    # synth temp that the emitter renders as the bare source via `_alias_map`.
    # The temp atom never goes through `_check_atomid`, so it carries no
    # `atom_variable_id`; this records the source local so the emitter can test
    # set membership (class_params / borrowed_vars) by the identity the temp
    # actually emits as.
    alias_root_variable_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-AtomId reference → resolved unit-level definition's type_id. Present
    # iff the bare reference resolves to a unit/core definition (function, data
    # block, record/class/variant used as a value, unit-level const) rather
    # than a local — the exact complement of atom_variable_id. The emitter
    # resolves the def by id (via _type_by_id) instead of re-resolving the name.
    atom_unit_def_type_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-DottedPath stamps.
    dp_parent_tagged_type: Dict[int, ZType] = field(default_factory=dict, init=False)
    dp_child_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-DottedPath selector → the UNIT type_id it resolves to, when the path
    # names a (file-level or inline) unit. Lets the emitter classify a composite
    # `a.b` unit selector by id instead of re-resolving the qualified name.
    dp_unit_type_id: Dict[int, int] = field(default_factory=dict, init=False)
    # Per-With binding ownership + alias target.
    with_ownership: Dict[int, ZOwnership] = field(default_factory=dict, init=False)
    with_alias_of: Dict[int, "Optional[str]"] = field(default_factory=dict, init=False)
    # Per-Assignment alias target.
    assign_alias_of: Dict[int, "Optional[str]"] = field(
        default_factory=dict, init=False
    )
    # Per-argument protocol projection stamps.
    projected_args: Dict[
        int,
        "tuple[Optional[ZType], Optional[str], Optional[str]]",
    ] = field(default_factory=dict, init=False)

    # ----- Flat children / generic_args tables (relational form of
    # the per-type edges that used to be dicts on `ZType`).
    type_child: List[ZTypeChild] = field(default_factory=list, init=False)
    type_generic_arg: List[ZTypeGenericArg] = field(default_factory=list, init=False)

    # ---- children / generic_arg setters + accessors ----
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
            ZTypeChild(parent.type_id, name, name_id, child.type_id, pos, child)
        )

    def set_generic_arg(self, parent: ZType, name: str, arg: ZType) -> None:
        for row in self.type_generic_arg:
            if row.parent_type_id == parent.type_id and row.param_name == name:
                row.arg_type_id = arg.type_id
                row.arg_type = arg
                return
        self.type_generic_arg.append(
            ZTypeGenericArg(parent.type_id, name, arg.type_id, arg)
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

    def set_default_numeric(self, parent: ZType, name: str, value: "int | str") -> None:
        """Stash a numeric-literal default. Stored as the value's
        string form -- the emitter dumps it verbatim into C."""
        self.set_child_default(parent, name, str(value))

    def set_default_function(self, parent: ZType, name: str, funcname: str) -> None:
        """Stash a function-reference default as `#function:<name>`; the emitter
        mangles the payload to `z_<name>` at use. The `#function:` tag lets the
        emitter classify the default mechanically instead of re-resolving the
        name by type."""
        self.set_child_default(parent, name, f"#function:{funcname}")

    def set_default_variant_arm(self, parent: ZType, name: str, arm_name: str) -> None:
        """Stash a variant / union null-payload subtype default.
        Stored as `#variant:<arm>`; the emitter renders the struct
        literal at use using the param / field's declared type."""
        self.set_child_default(parent, name, f"#variant:{arm_name}")

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
