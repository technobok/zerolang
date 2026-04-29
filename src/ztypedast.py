"""
Typed AST — the typechecker's output structure.

The parser produces a frozen `zast.Node` tree. The typechecker walks that
tree and *constructs* a parallel `TypedProgram` whose internal hierarchy
mirrors the parsed tree by role: `TypedUnit` → `TypedFunction` →
`TypedStatement` → `TypedExpression` (`TypedCall`, `TypedBinOp`,
`TypedAtomId`, …). Each typed node carries:

  - `parsed` — back-reference to its parsed counterpart (for source
    location, `start` token, original-source reporting). Never mutated.
  - `nodeid` — its own auto-assigned id (distinct from the parsed
    nodeid). Cloned/monomorphized typed subtrees get fresh ids; the
    parsed reference still points at the original source.
  - typecheck-derived fields it owns (resolved `ZType`, narrowing
    stamps, ownership, call kind, etc.).
  - typed children (e.g. `TypedCall.callable: TypedPath`).

Inert parsed nodes that carry no typecheck-derived state and do not
themselves contain typed children (currently: `StringChunk`, `Error`)
are not mirrored — typed nodes that need to reference them (e.g.
`TypedAtomString.parts`) embed the parsed node directly.

The typed tree is built by the typechecker (`Program → TypedProgram`).
The emitter and SQL dumper consume it; they read `parsed` only for
trivia.
"""

import copy

from dataclasses import dataclass, field
from itertools import count
import typing
from typing import Optional, Dict, List, NewType, Tuple, Union, cast, Callable

import zast
from zast import NodeType, CallKind
from ztypes import ZType, ZOwnership
from zsymtab_proto import SymbolTableProto


# Typed node identity, distinct from parsed `NodeID`. A typed clone of
# a typed function (monomorphization) gets a fresh `TypedNodeID` while
# its `parsed` reference stays the same.
TypedNodeID = NewType("TypedNodeID", int)
_next_typed_id = count()


# Compile-time constant value, surfaced for constant folding.
ConstValue = Union[int, float, bool, str]


# --------------------------------------------------------------------------
# Base classes
# --------------------------------------------------------------------------


@dataclass
class TypedNode:
    """Root of the typed-AST hierarchy. Every typed node carries a
    back-reference to the parsed node it was constructed from, plus its
    own typed-tree identity."""

    parsed: zast.Node
    typedid: TypedNodeID = field(
        default_factory=cast(Callable[[], TypedNodeID], _next_typed_id.__next__),
        init=False,
    )
    # Provenance for synthesised typed nodes (e.g. ANF lowering). None
    # for typed nodes constructed in straight typecheck.
    synth_origin: Optional[str] = field(default=None, init=False)


@dataclass
class TypedExpression(TypedNode):
    """Base for value-producing typed nodes. `ztype` is required —
    by construction every typed expression has a resolved type. The
    `Expression` parser-AST wrapper is *not* mirrored here; each
    grammar `ExpressionSubType` becomes a `TypedExpression` subclass
    directly."""

    ztype: ZType = cast(ZType, None)  # required at construction
    const_value: Optional[ConstValue] = None


@dataclass
class TypedOperation(TypedExpression):
    """Marker for operation-shaped typed expressions (BinOp / Call /
    Path subtypes). Mirrors `zast.Operation`."""


@dataclass
class TypedPath(TypedOperation):
    """Path-shaped operation. Used both as a typeref (in
    `TypedFunction.parameters`, `TypedFunction.returntype`,
    `TypedObjectDef` field paths) and as a value-producing
    path expression (in `TypedCall.callable`, `TypedBinOp.lhs`,
    `TypedDottedPath.parent`).
    Mirrors `zast.Path`."""


# --------------------------------------------------------------------------
# Atomic / path-shaped expressions
# --------------------------------------------------------------------------


@dataclass
class TypedAtomId(TypedPath):
    """Reference to an identifier — the typed counterpart of
    `zast.AtomId`. Also covers `LabelValue` (`:x` shorthand) via
    `is_label_value=True`; LabelValue carries no extra typed state."""

    name: str = ""
    is_label_value: bool = False
    # narrowing stamp set when this AtomId references a variable
    # narrowed in an enclosing match arm.
    narrowed_subtype: Optional[str] = None
    original_ztype: Optional[ZType] = None
    # Phase 7b child id stamped against contextually-known parent type.
    # -1 when unstamped.
    child_id: int = -1


@dataclass
class TypedDottedPath(TypedPath):
    """Mirror of `zast.DottedPath`. The `child` is always a
    `TypedAtomId`; `parent` is any typed path."""

    parent: TypedPath = cast("TypedPath", None)
    child: TypedAtomId = cast(TypedAtomId, None)
    parent_tagged_type: Optional[ZType] = None
    narrowed_subtype: Optional[str] = None
    child_id: int = -1


@dataclass
class TypedAtomString(TypedPath):
    """Mirror of `zast.AtomString`. Each part is either a typed
    expression (interpolation) or an inert parsed `StringChunk`
    embedded directly — `StringChunk` carries no typed state and is
    not mirrored as a typed class."""

    parts: List[Union[TypedExpression, zast.StringChunk]] = field(default_factory=list)


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------


@dataclass
class TypedBinOp(TypedOperation):
    """Mirror of `zast.BinOp`."""

    lhs: TypedOperation = cast(TypedOperation, None)
    operator: TypedAtomId = cast(TypedAtomId, None)
    rhs: TypedPath = cast(TypedPath, None)


@dataclass
class TypedNamedOperation(TypedNode):
    """Mirror of `zast.NamedOperation`. Carries the name (None for
    unnamed) and the typed operation. Protocol-projection stamps set
    by `_check_call` live here."""

    name: Optional[str] = None
    valtype: TypedOperation = cast(TypedOperation, None)
    projected_protocol: Optional[ZType] = None
    projected_label: Optional[str] = None
    # "borrow" or "take" — selected based on parameter ownership.
    projected_kind: Optional[str] = None


@dataclass
class TypedCall(TypedOperation):
    """Mirror of `zast.Call`. `call_kind` classifies the call for the
    emitter; `callable_type_name` is the mangled type name when the
    callable is a callable-object dispatch."""

    callable: TypedPath = cast(TypedPath, None)
    arguments: List[TypedNamedOperation] = field(default_factory=list)
    call_kind: CallKind = CallKind.UNKNOWN
    callable_type_name: Optional[str] = None


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------


@dataclass
class TypedAssignment(TypedNode):
    """Mirror of `zast.Assignment`. Creates a new binding; `alias_of`
    when non-None instructs the emitter to alias `name` to a stable
    C-level expression rather than allocating a local."""

    name: str = ""
    value: TypedExpression = cast(TypedExpression, None)
    alias_of: Optional[str] = None


@dataclass
class TypedReassignment(TypedExpression):
    """Mirror of `zast.Reassignment`. Returns null per grammar."""

    topath: TypedPath = cast(TypedPath, None)
    value: TypedExpression = cast(TypedExpression, None)


@dataclass
class TypedSwap(TypedExpression):
    """Mirror of `zast.Swap`. Returns null per grammar."""

    lhs: TypedPath = cast(TypedPath, None)
    rhs: TypedPath = cast(TypedPath, None)


@dataclass
class TypedStatementLine(TypedNode):
    """Mirror of `zast.StatementLine`. The wrapped value is one of
    `TypedAssignment`, `TypedReassignment`, `TypedSwap`, or any
    `TypedExpression` (control flow, call, …)."""

    statementline: Union[
        TypedAssignment, TypedReassignment, TypedSwap, TypedExpression
    ] = cast(TypedAssignment, None)


@dataclass
class TypedStatement(TypedNode):
    """Mirror of `zast.Statement` — an ordered list of statement
    lines forming a block body."""

    statements: List[TypedStatementLine] = field(default_factory=list)


# --------------------------------------------------------------------------
# Control flow expressions
# --------------------------------------------------------------------------


@dataclass
class TypedIfClause(TypedNode):
    """Mirror of `zast.IfClause`."""

    conditions: Dict[str, TypedOperation] = field(default_factory=dict)
    statement: TypedStatement = cast(TypedStatement, None)


@dataclass
class TypedIf(TypedExpression):
    """Mirror of `zast.If`. `taken_vars` records bindings consumed in
    some arm so the emitter can synthesise post-block cleanup."""

    clauses: List[TypedIfClause] = field(default_factory=list)
    elseclause: Optional[TypedStatement] = None
    taken_vars: List[Tuple[str, Optional[ZType]]] = field(default_factory=list)


@dataclass
class TypedCaseClause(TypedNode):
    """Mirror of `zast.CaseClause`."""

    name: str = ""
    match: TypedAtomId = cast(TypedAtomId, None)
    statement: TypedStatement = cast(TypedStatement, None)


@dataclass
class TypedCase(TypedExpression):
    """Mirror of `zast.Case` (match expression)."""

    subject: TypedOperation = cast(TypedOperation, None)
    clauses: List[TypedCaseClause] = field(default_factory=list)
    elseclause: Optional[TypedStatement] = None
    subject_taken: bool = False
    taken_vars: List[Tuple[str, Optional[ZType]]] = field(default_factory=list)


@dataclass
class TypedFor(TypedExpression):
    """Mirror of `zast.For`. `iterator_bindings` records named
    bindings whose operation returns option (auto-unwrapped each
    iteration; loop terminates on `none`)."""

    conditions: Dict[str, TypedOperation] = field(default_factory=dict)
    loop: Optional[TypedStatement] = None
    postconditions: List[TypedOperation] = field(default_factory=list)
    iterator_bindings: typing.Set[str] = field(default_factory=set)


@dataclass
class TypedDo(TypedExpression):
    """Mirror of `zast.Do` (bare block as expression). `has_break` is
    set when the block contains a `break` reachable from the entry."""

    statement: TypedStatement = cast(TypedStatement, None)
    has_break: bool = False


@dataclass
class TypedWith(TypedExpression):
    """Mirror of `zast.With` (`with name: expr do expr`).
    `ownership` records whether the binding is borrowed or owned;
    `alias_of` (when non-None) lets the emitter elide the binding and
    substitute the source expression."""

    name: str = ""
    value: TypedExpression = cast(TypedExpression, None)
    doexpr: TypedExpression = cast(TypedExpression, None)
    ownership: Optional[ZOwnership] = None
    alias_of: Optional[str] = None


@dataclass
class TypedData(TypedExpression):
    """Mirror of `zast.Data`."""

    data: List[TypedNamedOperation] = field(default_factory=list)


# --------------------------------------------------------------------------
# Top-level (definitions inside a unit)
# --------------------------------------------------------------------------


@dataclass
class TypedFunction(TypedNode):
    """Mirror of `zast.Function`. `as_items` is heterogeneous (typed
    paths for generic params + typed functions for static methods)."""

    parameters: Dict[str, TypedPath] = field(default_factory=dict)
    returntype: Optional[TypedPath] = None
    body: Optional[TypedStatement] = None
    is_native: bool = False
    as_items: Dict[str, TypedNode] = field(default_factory=dict)
    # The function's own resolved type (signature). None until resolved.
    ztype: Optional[ZType] = None


@dataclass
class TypedObjectDef(TypedNode):
    """Unified type-definition node for record / class / variant /
    union / enum / protocol / facet. Discriminator comes from
    `parsed.nodetype`; `kind` is duplicated here for convenience.
    `is_items` / `as_items` mirror the parser shape."""

    kind: NodeType = NodeType.RECORD
    is_items: Dict[str, TypedNode] = field(default_factory=dict)
    as_items: Dict[str, TypedNode] = field(default_factory=dict)
    is_native: bool = False
    # The defined object's own resolved ZType identity.
    ztype: Optional[ZType] = None


@dataclass
class TypedUnit(TypedNode):
    """Mirror of `zast.Unit`. Body is a heterogeneous typed-definition
    map, parallel to `zast.Unit.body`."""

    body: Dict[
        str,
        Union[
            "TypedUnit",
            TypedFunction,
            TypedObjectDef,
            TypedExpression,
            TypedAtomId,  # for label-value definitions
        ],
    ] = field(default_factory=dict)
    # The unit's own resolved type identity.
    ztype: Optional[ZType] = None


# --------------------------------------------------------------------------
# Program
# --------------------------------------------------------------------------


@dataclass
class TypedProgram:
    """Top-level typechecker output. Holds the typed-tree mirror of
    every unit in the parsed program plus the side-table state that
    is genuinely graph-shaped (resolved name table, monomorphizations,
    symbol-table snapshot, alias maps).

    Invariant: `parsed.units.keys() == units.keys()`. Construction is
    write-once at typecheck end; the emitter and SQL dumper read only.
    """

    parsed: zast.Program
    mainunitname: str
    units: Dict[str, TypedUnit] = field(default_factory=dict)
    # cross-tree lookup: parsed nodeid → typed node. Populated as the
    # typechecker constructs typed nodes. Used by the symbol table to
    # resolve entries (which reference parsed nodes by identity) to
    # their typed counterparts.
    by_parsed_id: Dict[int, TypedNode] = field(default_factory=dict)

    # ---- Already-aggregated side-table state (was on Program) ----
    resolved: Dict[str, ZType] = field(default_factory=dict)
    # monomorphized generic types: list of (mono_ztype, original parsed AST node)
    mono_types: List = field(default_factory=list)
    # monomorphized generic functions: list of (mono_ztype, cloned TypedFunction)
    mono_functions: List = field(default_factory=list)
    func_aliases: Dict[str, str] = field(default_factory=dict)
    # cloned methods per mono type: {mono_name: {mname: TypedFunction}}
    cloned_methods: Dict[str, Dict[str, TypedFunction]] = field(default_factory=dict)
    # Unit AST nodeid → resolved unit ZType.
    unit_types_by_id: Dict[int, ZType] = field(default_factory=dict)
    # Parsed nodeid → resolved ZType. Populated for every node the
    # typechecker resolves a type for (atoms, paths, calls, binops,
    # statements, control flow, function params / returntypes / field
    # paths, etc.). Used by emitter / SQL-dump as the fallback for
    # `_node_ztype` / `_path_ztype` after Step 6.9.b stripped
    # `zast.Node.type`. The typed-mirror tree remains the primary
    # source of truth for value-yielding expressions; this dict
    # covers nodes whose typed mirror isn't independently registered
    # (e.g. typeref Path nodes inside Function parameters / field
    # types are reachable through TypedFunction / TypedObjectDef but
    # the emitter often holds the parsed Path directly).
    node_types: Dict[int, Optional[ZType]] = field(default_factory=dict)
    # Per-Expression-wrapper control-flow classification (was
    # `zast.Expression.call_kind`, stripped in Step 6.10). Set by
    # `_check_expression` when an Expression wraps a Call or a Path
    # resolving to a control-flow type. Keys are parsed `Expression`
    # nodeids. Used by the emitter's `_emit_*_block` non-completing-
    # tail detection.
    expr_call_kinds: Dict[int, CallKind] = field(default_factory=dict)
    symbol_table: Optional[SymbolTableProto] = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def clone_typed_function(func: TypedFunction) -> TypedFunction:
    """Deep copy a TypedFunction subtree for monomorphization. The
    parsed back-references inside the clone still point at the
    original source nodes — only the typed structure is duplicated.
    Each cloned typed node also gets a fresh `typedid` because
    `_next_typed_id` is invoked by `__init__`/`field(default_factory=…)`,
    not by `deepcopy`; we reset ids explicitly to preserve uniqueness."""
    clone = copy.deepcopy(func)
    _refresh_typed_ids(clone)
    return clone


def _refresh_typed_ids(node: TypedNode) -> None:
    """Walk a typed subtree and reissue `typedid` on every node so a
    deepcopy of an existing typed subtree doesn't carry duplicate
    identities. Parsed back-references are left alone."""
    node.typedid = TypedNodeID(next(_next_typed_id))
    for child in _typed_children(node):
        _refresh_typed_ids(child)


def _typed_children(node: TypedNode) -> List[TypedNode]:
    """Return direct typed-child nodes of `node`. Mirrors
    `zast.node_children` but for the typed tree, dispatching on
    `node.parsed.nodetype` (id-based, no isinstance). Embedded parsed
    nodes (e.g. inert StringChunks inside a TypedAtomString) are not
    returned — they are not typed nodes."""
    out: List[TypedNode] = []
    nt = node.parsed.nodetype
    if nt == NodeType.UNIT:
        out.extend(cast(TypedUnit, node).body.values())
    elif nt == NodeType.FUNCTION:
        fn = cast(TypedFunction, node)
        if fn.returntype is not None:
            out.append(fn.returntype)
        out.extend(fn.parameters.values())
        if fn.body is not None:
            out.append(fn.body)
        out.extend(fn.as_items.values())
    elif nt in (
        NodeType.RECORD,
        NodeType.CLASS,
        NodeType.UNION,
        NodeType.VARIANT,
        NodeType.ENUM,
        NodeType.PROTOCOL,
        NodeType.FACET,
    ):
        od = cast(TypedObjectDef, node)
        out.extend(od.is_items.values())
        out.extend(od.as_items.values())
    elif nt == NodeType.IF:
        ifn = cast(TypedIf, node)
        out.extend(ifn.clauses)
        if ifn.elseclause is not None:
            out.append(ifn.elseclause)
    elif nt == NodeType.IFCLAUSE:
        ic = cast(TypedIfClause, node)
        out.extend(ic.conditions.values())
        out.append(ic.statement)
    elif nt == NodeType.CASE:
        cn = cast(TypedCase, node)
        out.append(cn.subject)
        out.extend(cn.clauses)
        if cn.elseclause is not None:
            out.append(cn.elseclause)
    elif nt == NodeType.CASECLAUSE:
        cc = cast(TypedCaseClause, node)
        out.append(cc.match)
        out.append(cc.statement)
    elif nt == NodeType.FOR:
        fn2 = cast(TypedFor, node)
        out.extend(fn2.conditions.values())
        if fn2.loop is not None:
            out.append(fn2.loop)
        out.extend(fn2.postconditions)
    elif nt == NodeType.DO:
        out.append(cast(TypedDo, node).statement)
    elif nt == NodeType.WITH:
        w = cast(TypedWith, node)
        out.append(w.value)
        out.append(w.doexpr)
    elif nt == NodeType.DATA:
        out.extend(cast(TypedData, node).data)
    elif nt == NodeType.NAMEDOPERATION:
        out.append(cast(TypedNamedOperation, node).valtype)
    elif nt == NodeType.CALL:
        c = cast(TypedCall, node)
        out.append(c.callable)
        out.extend(c.arguments)
    elif nt == NodeType.BINOP:
        b = cast(TypedBinOp, node)
        out.append(b.lhs)
        out.append(b.operator)
        out.append(b.rhs)
    elif nt == NodeType.DOTTEDPATH:
        dp = cast(TypedDottedPath, node)
        out.append(dp.parent)
        out.append(dp.child)
    elif nt == NodeType.ATOMSTRING:
        # parts mix typed expressions and inert parsed StringChunks.
        # Filter to typed-only via parsed.nodetype on each part — bare
        # parsed StringChunks aren't TypedNode and don't go in `out`.
        for p in cast(TypedAtomString, node).parts:
            # cheaper than isinstance: typed nodes have .typedid;
            # parsed StringChunks don't.
            if hasattr(p, "typedid"):
                out.append(cast(TypedNode, p))
    elif nt == NodeType.STATEMENT:
        out.extend(cast(TypedStatement, node).statements)
    elif nt == NodeType.STATEMENTLINE:
        out.append(cast(TypedStatementLine, node).statementline)
    elif nt == NodeType.ASSIGNMENT:
        out.append(cast(TypedAssignment, node).value)
    elif nt == NodeType.REASSIGNMENT:
        ra = cast(TypedReassignment, node)
        out.append(ra.topath)
        out.append(ra.value)
    elif nt == NodeType.SWAP:
        sw = cast(TypedSwap, node)
        out.append(sw.lhs)
        out.append(sw.rhs)
    # ATOMID / LABELVALUE / STRINGCHUNK / ERROR have no typed children.
    return out
