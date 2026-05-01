"""
ZeroLang type checking pass — single depth-first pass

Starts at main function, resolves names on demand, detects cycles.
Includes ownership checking (Phase 4c).
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, List, Tuple, cast

import zast
import ztyping
from zast import ERR, NodeType, clone_function
from zlexer import Token
from zenv import SymbolTable
from zsynth import FreshNamer, make_assignment, make_atom_id, register_synth_var
import zasthash
from ztypes import (
    ZType,
    ZTypeType,
    ZSubType,
    ZParamOwnership,
    ZOwnership,
    ZNaming,
    ZVariable,
    ZLockState,
    ControlKind,
    ExprResult,
    NUMERIC_RANGES,
    parse_number,
)
from ztypeutil import (
    is_numeric_id as _is_numeric_id,
    is_array_type as _is_array_type,
    array_element_type as _array_element_type,
    array_length as _array_length,
    is_str_type as _is_str_type,
    str_capacity as _str_capacity,
    is_list_type as _is_list_type,
    list_element_type as _list_element_type,
    is_listview_type as _is_listview_type,
    listview_element_type as _listview_element_type,
    is_listiter_type as _is_listiter_type,
    listiter_element_type as _listiter_element_type,
    is_mapkeyiter_type as _is_mapkeyiter_type,
    mapkeyiter_key_type as _mapkeyiter_key_type,
    is_mapitemiter_type as _is_mapitemiter_type,
    mapitemiter_key_type as _mapitemiter_key_type,
    mapitemiter_value_type as _mapitemiter_value_type,
    is_mapentry_type as _is_mapentry_type,
    mapentry_key_type as _mapentry_key_type,
    mapentry_value_type as _mapentry_value_type,
    is_map_type as _is_map_type,
    map_key_type as _map_key_type,
    map_value_type as _map_value_type,
    is_stringview_type as _is_stringview_type,
)


# -- Constant fold registry ---------------------------------------------------
# Maps operator name to a fold function (lhs, rhs) -> result.
# Extensible: add entries for new foldable native operations.


def _fold_add(lhs: "int | float", rhs: "int | float") -> "int | float":
    return lhs + rhs


def _fold_sub(lhs: "int | float", rhs: "int | float") -> "int | float":
    return lhs - rhs


def _fold_mul(lhs: "int | float", rhs: "int | float") -> "int | float":
    return lhs * rhs


def _fold_div(lhs: "int | float", rhs: "int | float") -> "int | float | None":
    if rhs == 0:
        return None
    if type(lhs) is float or type(rhs) is float:
        return lhs / rhs
    # integer: truncation toward zero (C semantics)
    result = lhs / rhs
    return int(result) if result >= 0 else -int(-result)


def _fold_lt(lhs: "int | float", rhs: "int | float") -> bool:
    return lhs < rhs


def _fold_le(lhs: "int | float", rhs: "int | float") -> bool:
    return lhs <= rhs


def _fold_gt(lhs: "int | float", rhs: "int | float") -> bool:
    return lhs > rhs


def _fold_ge(lhs: "int | float", rhs: "int | float") -> bool:
    return lhs >= rhs


def _fold_eq(lhs: "int | float", rhs: "int | float") -> bool:
    return lhs == rhs


def _fold_ne(lhs: "int | float", rhs: "int | float") -> bool:
    return lhs != rhs


_FOLD_OPS: dict = {
    "+": _fold_add,
    "-": _fold_sub,
    "*": _fold_mul,
    "/": _fold_div,
    "<": _fold_lt,
    "<=": _fold_le,
    ">": _fold_gt,
    ">=": _fold_ge,
    "==": _fold_eq,
    "!=": _fold_ne,
}


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[len(b)]


def _suggest_similar(name: str, candidates, max_distance: int = 2) -> Optional[str]:
    """Find the closest match to name among candidates (Levenshtein distance).

    Returns the best match if distance <= max_distance and it's the unique best,
    otherwise None.
    """
    best = None
    best_dist = max_distance + 1
    tied = False
    for c in candidates:
        if c == name:
            continue
        d = _levenshtein(name, c)
        if d < best_dist:
            best = c
            best_dist = d
            tied = False
        elif d == best_dist:
            tied = True
    if best is not None and best_dist <= max_distance and not tied:
        return best
    return None


def _make_type(name: str, typetype: ZTypeType, parent: Optional[ZType] = None) -> ZType:
    return ZType(name=name, typetype=typetype, parent=parent)


def _is_valtype(ztype: ZType) -> bool:
    """Check if a type is a value type (copied, always owned)."""
    if ztype.is_valtype is not None:
        return ztype.is_valtype
    # types without explicit classification: assume valtype for safety
    # (numerics, strings, bools are all records tagged as valtype)
    return ztype.typetype in (
        ZTypeType.RECORD,
        ZTypeType.ENUM,
        ZTypeType.DATA,
        ZTypeType.VARIANT,
        ZTypeType.FUNCTION,
    )


def _set_destructor_metadata(ztype: ZType) -> None:
    """Set needs_destructor, destructor_name, is_heap_allocated based on type."""
    if ztype.subtype == ZSubType.STRING:
        ztype.needs_destructor = True
        ztype.destructor_name = "z_String_free"
        ztype.is_heap_allocated = False  # stack struct, heap data buffer
    elif ztype.subtype == ZSubType.STRINGVIEW:
        ztype.needs_destructor = False
        ztype.destructor_name = None
        ztype.is_heap_allocated = False
    elif ztype.typetype == ZTypeType.CLASS:
        # Classes are stack-allocated. Destructor provisionally set to True;
        # refined by _set_field_cleanup_metadata after children are resolved.
        ztype.needs_destructor = True
        ztype.destructor_name = f"z_{ztype.name}_destroy"
        ztype.is_heap_allocated = False
    elif ztype.typetype == ZTypeType.UNION:
        ztype.needs_destructor = True
        ztype.destructor_name = f"z_{ztype.name}_destroy"
        ztype.is_heap_allocated = False  # stack struct, heap subtype data
    elif ztype.typetype == ZTypeType.PROTOCOL:
        ztype.needs_destructor = True
        ztype.destructor_name = f"z_{ztype.name}_destroy"
        ztype.is_heap_allocated = False  # stack struct, heap wrapped data
    else:
        ztype.needs_destructor = False
        ztype.destructor_name = None
        ztype.is_heap_allocated = False


def _set_field_cleanup_metadata(typing: ztyping.Typing, ztype: ZType) -> None:
    """Set needs_field_cleanup based on whether any non-function children need cleanup.

    Must be called after children are fully resolved. Scans fields (non-function
    children) and sets needs_field_cleanup=True if any field has needs_destructor=True.
    For stack-allocated classes without heap fields, clears needs_destructor since
    no cleanup is needed.
    """
    for child_name, child_type in typing.children_of(ztype):
        if child_type.typetype == ZTypeType.FUNCTION:
            continue
        if child_type.needs_destructor:
            ztype.needs_field_cleanup = True
            return
    # io.file: compiler-provided class whose destructor closes the
    # underlying fd (RAII). The fd/closed fields don't themselves
    # need cleanup, so the general rule below would wrongly clear
    # the destructor.
    if ztype.typetype == ZTypeType.CLASS and ztype.name == "File":
        ztype.needs_destructor = True
        ztype.destructor_name = "z_File_destroy"
        return

    # Stack-allocated class with no heap fields needs no destructor.
    # Skip types that manage their own heap data: box (is_box=True),
    # list, and map — identified by is_heap_allocated which is set
    # explicitly after monomorphization for these collection types.
    # Also skip string: stack struct with heap data buffer, always needs cleanup.
    if (
        ztype.typetype == ZTypeType.CLASS
        and not ztype.is_heap_allocated
        and not ztype.is_box
        and not ztype.needs_field_cleanup
        and ztype.subtype != ZSubType.STRING
    ):
        ztype.needs_destructor = False
        ztype.destructor_name = None


# Sentinel for definitions currently being resolved
_RESOLVING = object()


_PRIMITIVE_TYPE_NAMES: frozenset[str] = frozenset(NUMERIC_RANGES.keys()) | frozenset(
    {"bool", "null", "f32", "f64", "f128"}
)


def _is_primitive_name(name: str) -> bool:
    """True for globally-singleton primitive type names (numerics, bool,
    null, floats). Used by `_types_compatible` as a safe name-based
    fallback while full ZType interning is pending — these types are
    conceptually unique by name regardless of which resolver produced
    a given ZType instance.
    """
    return name in _PRIMITIVE_TYPE_NAMES


def _mono_arg_key(t: "ZType") -> Tuple:
    """Identity key for a monomorphization argument. ZType interning
    hasn't landed yet, so primitives (u64, i32, bool, ...), generic
    parameters, and numeric-literal value types may be re-created with
    distinct nodeids despite representing the same logical argument.
    Fall back to name + numeric_value for those; use nodeid for
    structural types where identity is stable.
    """
    if t.typetype == ZTypeType.GENERIC_PARAM:
        return ("gp", t.name)
    if _is_primitive_name(t.name):
        return ("p", t.name)
    if t.numeric_value is not None:
        return ("nv", t.numeric_value)
    return ("n", t.nodeid)


def _extract_public_members(as_items: dict) -> Optional[dict[str, str]]:
    """Extract public member mapping from as_items if a public: unit is declared.

    Returns None if no public restriction (all-public default).
    Returns a dict mapping external_name → internal_name if public is declared.
    For label-value shorthand (:field), external and internal names are the same.
    For renaming (api_name: internal_name), they differ.
    """
    public_unit = as_items.get("public")
    if public_unit is None:
        return None
    # must be a Unit AST node
    if public_unit.nodetype != NodeType.UNIT:
        return None
    # build external → internal name mapping
    members: dict[str, str] = {}
    for ext_name, defn in cast(zast.Unit, public_unit).body.items():
        if defn.nodetype == NodeType.LABELVALUE:
            # :field shorthand — external and internal names are the same
            members[ext_name] = ext_name
        elif defn.nodetype in (
            NodeType.ATOMID,
            NodeType.DOTTEDPATH,
        ):
            # renamed: api_name: internal_name
            if defn.nodetype == NodeType.ATOMID:
                members[ext_name] = cast(zast.AtomId, defn).name
            elif defn.nodetype == NodeType.DOTTEDPATH:
                members[ext_name] = cast(zast.DottedPath, defn).child.name
        else:
            # other definitions (functions, etc.) — same name
            members[ext_name] = ext_name
    return members


def _check_private_redefinition(as_items: dict) -> Optional[zast.Unit]:
    """Return the 'private' unit node if it exists in as_items (for error reporting)."""
    private_unit = as_items.get("private")
    if private_unit is not None and private_unit.nodetype == NodeType.UNIT:
        return private_unit
    return None


# Names in 'as' that are structural, not user-defined members
_AS_SPECIAL_NAMES = frozenset({"public", "private", "tag"})

# Ownership annotations recognised as the leaf of a DottedPath in
# field-type / parameter-type / return-type position.
_OWNERSHIP_SUFFIXES = {
    "take": ZParamOwnership.TAKE,
    "borrow": ZParamOwnership.BORROW,
    "lock": ZParamOwnership.LOCK,
}


def _strip_path_ownership(
    path: zast.Operation,
) -> tuple[zast.Operation, Optional[ZParamOwnership]]:
    """If `path` is a DottedPath whose leaf is `.take`/`.borrow`/`.lock`,
    return `(parent_path, ownership)`. Otherwise return `(path, None)`.

    Only Path-shaped operations have a leaf to inspect; non-Path
    operation forms (BinOp constants, unit references) pass through
    unchanged with no ownership.
    """
    if path.nodetype == NodeType.DOTTEDPATH:
        dp = cast(zast.DottedPath, path)
        own = _OWNERSHIP_SUFFIXES.get(dp.child.name)
        if own is not None:
            return dp.parent, own
    return path, None


@dataclass
class TemplateIds:
    """Cached stdlib generic-template ids on `TypeChecker`. Lazily
    populated on first use because the system unit may not yet be
    loaded at `TypeChecker.__init__` time. `-1` means "not yet
    resolved." Used by hot-path identity checks (option /
    optionval / optionview discrimination during call dispatch and
    monomorphization)."""

    option: int = -1
    optionval: int = -1
    optionview: int = -1


@dataclass
class FunctionContext:
    """Function-body context on `TypeChecker`. Holds the expected
    return type, parameter-ownership map, return-ownership annotation,
    plus the enclosing-type and currently-being-checked function
    stacks. The scalar fields (`return_type`, `func_ownership`,
    `func_return_ownership`) are saved and restored at function-body
    boundaries; the stack fields (`enclosing_type`, `body`) are
    pushed/popped per nested method dispatch rather than swapped
    wholesale."""

    return_type: "Optional[ZType]" = None
    func_ownership: "dict[str, ZParamOwnership]" = field(default_factory=dict)
    func_return_ownership: "Optional[ZParamOwnership]" = None
    enclosing_type: "list[ZType]" = field(default_factory=list)
    body: "list[ZType]" = field(default_factory=list)


@dataclass
class MonoState:
    """Monomorphization-related state on `TypeChecker`. Bagged here
    so the cluster has one named home and the pre-relocation 7+ fields
    in `TypeChecker.__init__` collapse to a single line. Each field
    is mutable — `TypeChecker` writes through them during typecheck;
    `typecheck()` (the public entry point) snapshots the relevant
    subset onto `Typing` at the end."""

    # mono dedup cache: key = (template_id, sorted-mono-arg-key tuple)
    cache: "dict[tuple, ZType]" = field(default_factory=dict)
    # monomorphized generic types: list of (mono_ztype, original_ast_node)
    types: "list[tuple[ZType, zast.TypeDefinition]]" = field(default_factory=list)
    # monomorphized generic functions: list of (mono_ztype, cloned_function)
    functions: "list[tuple[ZType, zast.Function]]" = field(default_factory=list)
    # generic-context stack: each frame is the {param_name: concrete_type}
    # bindings active inside a method body or generic instantiation.
    generic_context: "list[dict[str, ZType]]" = field(default_factory=list)
    # dedup: hash -> (canonical_qualified_name, canonical_Function)
    func_hashes: "dict[str, tuple[str, zast.Function]]" = field(default_factory=dict)
    # dedup aliases: alias_qualified_name -> canonical_qualified_name
    func_aliases: "dict[str, str]" = field(default_factory=dict)
    # cloned methods per mono type: mono_name -> {mname: Function}
    cloned_methods: "dict[str, dict[str, zast.Function]]" = field(default_factory=dict)
    # C-name collision tracking: assigned cnames for collision detection
    assigned_cnames: "set[str]" = field(default_factory=set)


class TypeChecker:
    """
    Single-pass demand-driven type checker.

    Starts from main, resolves names as encountered. Uses a resolving
    stack for cycle detection and `type` keyword resolution.
    """

    def __init__(self, program: zast.Program) -> None:
        self.program = program
        # F5.E.1: typecheck-output container. F5.E.2 onwards
        # relocates component tables and aggregate state off
        # `program` onto here; today it carries only the back-ref
        # to the parsed program and `is_error` so the structure
        # exists for callers and for incremental migration.
        self.typing = ztyping.Typing(parsed=program)
        self.errors: List[zast.Error] = []
        self.symtab = SymbolTable(typing=self.typing)

        # well-known types (only null/never are standalone — others come from system.z)
        self.t_null = _make_type("null", ZTypeType.NULL)

        # resolving stack: list of (qualified_name, ZType) for cycle detection
        # and `type` keyword resolution
        self._resolving: List[Tuple[str, ZType]] = []

        # cache of resolved unit-level names: "unit.name" -> ZType
        self._resolved: dict[str, ZType] = {}

        # unit types (for dotted path resolution like mathutil.square)
        self.unit_types: dict[str, ZType] = {}
        # Phase 7d: id-keyed parallel cache. Keyed by unit_ast.nodeid (the
        # Unit AST node's monotonic id from Phase 7a). Populated alongside
        # `unit_types` via `_register_unit_type`. Safe to be incomplete —
        # id-first readers always fall back to the name cache.
        self.unit_types_by_id: dict[int, ZType] = {}
        for unitname, unit_ast in self.program.units.items():
            t = _make_type(unitname, ZTypeType.UNIT)
            self._register_unit_type(unitname, unit_ast, t)
        # track which file units have been fully resolved (generic params detected)
        self._resolved_file_units: set[str] = set()

        # F5.B.2: function-body context grouped into a single record.
        # See `FunctionContext` for per-field documentation.
        self.func_ctx = FunctionContext()

        # pending borrow lock: set by deep methods (.borrow, .lock, .stringview,
        # protocol paths), captured and cleared by _check_expression into
        # ExprResult.borrow_target so it cannot leak between statements. Stored
        # as the addressable path tuple `(root, f1, f2, ...)`.
        self._pending_borrow_lock: Optional[Tuple[str, ...]] = None
        # pending private access: set by .private, captured and cleared by
        # _check_expression into ExprResult.private_access.
        self._pending_private_access: bool = False

        # call-identity stack: pushed in _check_call before arg/receiver
        # processing, popped after. Locks installed during a call's
        # processing carry the topmost identity as their `holder`, and
        # try_lock skips conflict checks where existing.holder matches the
        # current call's identity. Lets a call freely take overlapping
        # locks on receiver + args (e.g. `f.method bv: f.byteview`)
        # without self-blocking.
        self._call_id_stack: List[str] = []

        # Per-call argument hoisting (Phase C step 2). FreshNamer hands
        # out monotonic synth names (`_t0`, `_t1`, ...). The preamble
        # stack mirrors the depth of in-flight Statements: each entry is
        # a list of synth Assignments to inject *before* the current
        # StatementLine in that Statement. _check_statement push/pops
        # entries; _check_call appends to the topmost.
        self._fresh_namer = FreshNamer(prefix="_t")
        self._call_preamble: List[List[zast.StatementLine]] = []

        # inline unit context stack: tracks nesting during resolution
        # each entry is (unitname, zast.Unit) for name lookup chain
        self._unit_context: List[Tuple[str, zast.Unit]] = []

        # maps implementor type name -> list of (label, protocol ZType)
        self._protocol_labels: dict[str, list[tuple[str, ZType]]] = {}

        # F5.B.1: monomorphization-related state grouped into a
        # single record. See `MonoState` for per-field documentation.
        self.mono = MonoState()

        # break target stack: tracks which construct a break binds to
        # Do node = break targets this do block; None = break targets a for loop
        self._break_targets: list[Optional[zast.Do]] = []

        # Step 6 (typed-tree migration): typecheck-set decoration fields
        # used to live on parsed AST nodes as `init=False` columns; in
        # F5.D they moved off TypeChecker onto `Program` as ECS-shaped
        # component dicts (`program.node_type`, `program.call_kind`,
        # `program.atom_*`, ...). See `Program` in `zast.py` for the
        # full set and per-table documentation.

        # flow typing is now tracked via scope-based narrowing entries
        # (TypeState removed — narrowing lives in Scope.entries)

        # compile-time error suppression: when > 0, error() calls do not
        # emit compile-time errors (used inside constant-false if branches)
        self._suppress_compile_error: int = 0

        # F5.B.3: cached stdlib generic-template ids grouped into a
        # single record. See `TemplateIds` for per-field documentation.
        self.template_ids = TemplateIds()

        # _type_of_definition dispatch table: NodeType -> bound resolver
        # method. Keyed by `defn.nodetype` so the lookup is O(1) and
        # avoids a getattr-by-name call.
        self._definition_resolvers: "dict[NodeType, Callable]" = {
            NodeType.FUNCTION: self._resolve_function_type,
            NodeType.RECORD: self._resolve_record_type,
            NodeType.CLASS: self._resolve_class_type,
            NodeType.UNION: self._resolve_union_type,
            NodeType.VARIANT: self._resolve_variant_type,
            NodeType.PROTOCOL: self._resolve_protocol_type,
            NodeType.FACET: self._resolve_facet_type,
            NodeType.UNIT: self._resolve_inline_unit_type,
        }

    # Keywords used to auto-categorise errors when no explicit code is given
    _OWNERSHIP_KEYWORDS = (
        "take",
        "swap",
        "borrowed",
        "borrow",
        "lock",
        "ownership",
    )
    _GENERIC_KEYWORDS = (
        "generic",
        "infer",
        "monomorph",
        "numeric generic",
        "Numeric generic",
    )
    _CALL_KEYWORDS = (
        "operator",
        "argument",
        "exhaustive",
        "missing method",
        "requires",
        "Cannot call",
        "param",
    )

    def _error(
        self,
        msg: str,
        loc: Optional[Token] = None,
        err: ERR = ERR.COMPILERERROR,
        note: Optional[str] = None,
        hint: Optional[str] = None,
    ) -> None:
        # auto-categorise if caller used the default COMPILERERROR
        if err == ERR.COMPILERERROR:
            ml = msg.lower()
            if any(k.lower() in ml for k in self._OWNERSHIP_KEYWORDS):
                err = ERR.OWNERERROR
            elif any(k.lower() in ml for k in self._GENERIC_KEYWORDS):
                err = ERR.GENERICERROR
            elif any(k.lower() in ml for k in self._CALL_KEYWORDS):
                err = ERR.CALLERROR
            else:
                err = ERR.TYPEERROR
        self.errors.append(
            zast.Error(
                start=loc or zast._ERROR_TOKEN, err=err, msg=msg, note=note, hint=hint
            )
        )

    def _set_child(self, parent: ZType, name: str, child: ZType) -> None:
        self.typing.set_child(parent, name, child)

    def _set_generic_arg(self, parent: ZType, name: str, arg: ZType) -> None:
        self.typing.set_generic_arg(parent, name, arg)

    def _copy_children(self, dst: ZType, src: ZType) -> None:
        for k, v in self.typing.children_of(src):
            self.typing.set_child(dst, k, v)

    def _assign_cname(self, ztype: ZType, base_cname: str) -> None:
        """Assign a C identifier to a type, auto-resolving collisions.

        If base_cname is already taken, appends the type's nodeid to
        disambiguate. The final cname is stored on ztype.cname.
        """
        if ztype.cname:
            return  # already assigned via earlier resolution path
        if base_cname not in self.mono.assigned_cnames:
            ztype.cname = base_cname
        else:
            ztype.cname = f"{base_cname}_{ztype.nodeid}"
        self.mono.assigned_cnames.add(ztype.cname)

    # Multi-char operator names (checked first, before per-char mangling)
    _OP_NAMES = {
        "<=": "le",
        ">=": "ge",
        "==": "eq",
        "!=": "ne",
    }

    # Single-char replacements for zerolang identifier chars invalid in C
    # Named after the character glyph, not the operation it performs
    _CHAR_MANGLE = {
        "!": "excl",
        "$": "dollar",
        "%": "perc",
        "&": "amp",
        "'": "tick",
        "*": "star",
        "+": "plus",
        "-": "minus",
        "/": "slash",
        "<": "lt",
        "=": "eq",
        ">": "gt",
        "?": "ques",
        "@": "at",
        "\\": "bslash",
        "^": "caret",
        "|": "pipe",
        "~": "tilde",
    }

    @staticmethod
    def _mangle_name(name: str) -> str:
        """Convert a zerolang qualified name to a valid C identifier fragment.

        Replaces dots with underscores. For each dot-separated part, tries
        multi-char operator lookup first, then falls back to per-character
        replacement of any non-C-identifier characters.
        """
        parts = name.split(".")
        mangled = []
        for part in parts:
            op = TypeChecker._OP_NAMES.get(part)
            if op is not None:
                mangled.append(op)
            elif any(c in TypeChecker._CHAR_MANGLE for c in part):
                result = []
                for c in part:
                    result.append(TypeChecker._CHAR_MANGLE.get(c, c))
                mangled.append("".join(result))
            else:
                mangled.append(part)
        return "_".join(mangled)

    def _assign_cname_type(self, ztype: ZType, qualified_name: str = "") -> None:
        """Assign cname for a type definition.

        For functions, qualified_name should be the dotted name (e.g. "point.distance").
        For other types, the name is taken from ztype.name.
        """
        if ztype.typetype == ZTypeType.FUNCTION:
            name = qualified_name if qualified_name else ztype.name
            base = "z_" + self._mangle_name(name)
            self._assign_cname(ztype, base)
        elif ztype.typetype in (
            ZTypeType.RECORD,
            ZTypeType.CLASS,
            ZTypeType.UNION,
            ZTypeType.VARIANT,
            ZTypeType.PROTOCOL,
            ZTypeType.FACET,
            ZTypeType.ENUM,
            ZTypeType.TAG,
        ):
            base = f"z_{ztype.name}_t"
            self._assign_cname(ztype, base)

    def _release_template_cname(self, ztype: ZType) -> None:
        """Release a generic template's cname slot after generic
        detection. Templates never emit directly — only their
        monomorphizations do — so clinging to a `z_{name}_t` slot
        blocks user-declared non-generic types from using the same
        name (e.g. a user `result: variant { ... }` shadowing
        system's generic `result` union).
        """
        if not ztype.isgeneric:
            return
        if ztype.cname:
            self.mono.assigned_cnames.discard(ztype.cname)
            ztype.cname = ""

    def check(self, full: bool = False) -> List[zast.Error]:
        """Run the type checker starting from main.

        If full=True, also check all definitions in all units (not just
        those reachable from main).
        """
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return self.errors

        # type-check all definitions in the main unit that have bodies
        # (starting from main, but also covering other functions)
        main_func = mainunit.body.get("main")
        if main_func is not None and main_func.nodetype == NodeType.FUNCTION:
            # resolve main first to trigger demand-driven resolution
            self._resolve_unit_name(self.program.mainunitname, "main")
            self._check_function_body("main", cast(zast.Function, main_func))

        # check for native declarations in user code (not allowed)
        self._check_native_in_user_code(mainunit)

        # also check other definitions in the main unit
        for name, defn in mainunit.body.items():
            if name == "main":
                continue
            if defn.nodetype == NodeType.UNIT:
                self._resolve_unit_name(self.program.mainunitname, name)
            elif defn.nodetype == NodeType.FUNCTION and cast(zast.Function, defn).body:
                self._resolve_unit_name(self.program.mainunitname, name)
                # skip body checking for generic functions (checked during monomorphization)
                ftype = self._resolved.get(f"{self.program.mainunitname}.{name}")
                if not (ftype and ftype.isgeneric):
                    self._check_function_body(name, cast(zast.Function, defn))
            elif (
                defn.nodetype == NodeType.FUNCTION
                and cast(zast.Function, defn).body is None
            ):
                # spec (function without body) — resolve type
                self._resolve_unit_name(self.program.mainunitname, name)

        if full:
            for unitname, unit in self.program.units.items():
                for name, defn in unit.body.items():
                    self._resolve_unit_name(unitname, name)
                    if (
                        defn.nodetype == NodeType.FUNCTION
                        and cast(zast.Function, defn).body
                    ):
                        # skip body checking for generic functions
                        ftype = self._resolved.get(f"{unitname}.{name}")
                        if not (ftype and ftype.isgeneric):
                            self._check_function_body(name, cast(zast.Function, defn))

        return self.errors

    def _check_native_in_user_code(self, unit: zast.Unit) -> None:
        """Report errors for native declarations in user code.

        The 'native' keyword is reserved for system library definitions.
        User code should not use 'is native' on functions or types.
        """
        for name, defn in unit.body.items():
            if (
                defn.nodetype == NodeType.FUNCTION
                and cast(zast.Function, defn).is_native
            ):
                self._error(
                    f"'native' is reserved for system library definitions: '{name}'",
                    loc=defn.start,
                    err=ERR.TYPEERROR,
                    hint="remove 'is native' and provide a function body",
                )
            elif defn.nodetype in (
                NodeType.RECORD,
                NodeType.CLASS,
                NodeType.UNION,
                NodeType.VARIANT,
            ):
                # All four types have is_native, as_functions, functions
                defn_typed = cast(zast.ObjectDef, defn)
                if defn_typed.is_native:
                    self._error(
                        f"'native' is reserved for system library definitions: '{name}'",
                        loc=defn.start,
                        err=ERR.TYPEERROR,
                        hint="remove 'is native' and declare fields normally",
                    )
                # also check methods inside the type (both is and as)
                for mname, mfunc in defn_typed.as_functions().items():
                    if mfunc.is_native:
                        self._error(
                            f"'native' is reserved for system library definitions: '{name}.{mname}'",
                            loc=mfunc.start,
                            err=ERR.TYPEERROR,
                            hint="remove 'is native' and provide a function body",
                        )
                for mname, mfunc in defn_typed.functions().items():
                    if mfunc.is_native:
                        self._error(
                            f"'native' is reserved for system library definitions: '{name}.{mname}'",
                            loc=mfunc.start,
                            err=ERR.TYPEERROR,
                            hint="remove 'is native' and provide a function body",
                        )

    # ---- Demand-driven name resolution ----

    def _resolve_unit_name(self, unitname: str, name: str) -> Optional[ZType]:
        """Resolve a name from a unit, type-checking its definition on demand."""
        key = f"{unitname}.{name}"

        # already resolved?
        if key in self._resolved:
            return self._resolved[key]

        # currently being resolved? check for valid self-reference vs circular alias
        for i, (rkey, rtype) in enumerate(self._resolving):
            if rkey == key:
                # on the resolving stack — check if it's a concrete type (valid self-ref)
                if rtype.typetype in (
                    ZTypeType.RECORD,
                    ZTypeType.ENUM,
                    ZTypeType.UNION,
                    ZTypeType.FUNCTION,
                    ZTypeType.CLASS,
                    ZTypeType.PROTOCOL,
                    ZTypeType.FACET,
                ):
                    return rtype  # valid self-reference via `type`
                # NULL shell (alias) — check if the chain contains a concrete
                # type that this alias will eventually resolve to
                for _, rt in self._resolving[i + 1 :]:
                    if rt.typetype in (
                        ZTypeType.RECORD,
                        ZTypeType.ENUM,
                        ZTypeType.UNION,
                        ZTypeType.FUNCTION,
                        ZTypeType.CLASS,
                        ZTypeType.PROTOCOL,
                        ZTypeType.FACET,
                    ):
                        return rt
                # circular alias with no concrete type in chain
                chain = " -> ".join(rk for rk, _ in self._resolving[i:])
                self._error(f"Circular type alias: {chain} -> {key}")
                return None

        unit = self.program.units.get(unitname)
        if not unit:
            return None

        # handle dotted names for inline units (e.g., "m.X" -> unit m, def X)
        defn = unit.body.get(name)
        if defn is None and "." in name:
            parts = name.split(".")
            # walk into nested inline units
            current_body = unit.body
            for i, part in enumerate(parts[:-1]):
                inner = current_body.get(part)
                if inner is not None and inner.nodetype == NodeType.UNIT:
                    current_body = cast(zast.Unit, inner).body
                else:
                    return None
            defn = current_body.get(parts[-1])
        if defn is None:
            return None

        t = self._type_of_definition(unitname, name, defn)
        if t:
            self._resolved[key] = t
            # also populate unit_types for dotted path access
            if unitname in self.unit_types:
                self._set_child(self.unit_types[unitname], name, t)
        return t

    def _type_of_definition(
        self, unitname: str, name: str, defn: zast.TypeDefinition
    ) -> Optional[ZType]:
        """Type-check a definition, pushing/popping the resolving stack."""
        # dispatch structured types via table (built in __init__ so each
        # entry binds to the per-instance bound method — no getattr).
        resolver = self._definition_resolvers.get(defn.nodetype)
        if resolver is not None:
            return resolver(unitname, name, defn)
        # alias: DottedPath reference
        if defn.nodetype == NodeType.DOTTEDPATH:
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)  # placeholder for alias
            self._resolving.append((key, shell))
            dp = cast(zast.DottedPath, defn)
            t = self._resolve_dotted_path(dp)
            self._resolving.pop()
            # Null-subtype construction at unit level: `true: bool.true`
            # resolves `bool.true` to the null arm type, but the actual
            # value is a construction of the outer variant. Promote the
            # definition's type and stamp const_value (bool only) so
            # downstream uses see the variant type and the arm index.
            # Mirrors the logic in _check_path for value-context uses.
            outer = self.typing.dp_parent_tagged_type.get(dp.nodeid)
            if t is not None and t.typetype == ZTypeType.NULL and outer is not None:
                self.typing.node_type[dp.nodeid] = outer
                if outer.name == "bool":
                    arm_name = dp.child.name
                    if self.typing.has_child(outer, arm_name):
                        self.typing.node_const_value[dp.nodeid] = list(
                            self.typing.child_names_of(outer)
                        ).index(arm_name)
                return outer
            return t
        if defn.nodetype == NodeType.LABELVALUE:
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)
            self._resolving.append((key, shell))
            t = self._resolve_name(
                cast(zast.LabelValue, defn).name, skip_unit_def=(unitname, name)
            )
            self._resolving.pop()
            return t
        if (
            defn.nodetype == NodeType.EXPRESSION
            and cast(zast.Expression, defn).expression.nodetype == NodeType.DATA
        ):
            return self._resolve_data_type(
                unitname, name, cast(zast.Data, cast(zast.Expression, defn).expression)
            )
        if (
            defn.nodetype == NodeType.EXPRESSION
            and cast(zast.Expression, defn).expression.nodetype == NodeType.IF
        ):
            return self._resolve_unit_level_if(
                unitname, name, cast(zast.Expression, defn)
            )
        if defn.nodetype == NodeType.EXPRESSION and cast(
            zast.Expression, defn
        ).expression.nodetype in (
            NodeType.BINOP,
            NodeType.CALL,
            NodeType.DOTTEDPATH,
            NodeType.ATOMID,
            NodeType.ATOMSTRING,
            NodeType.EXPRESSION,
            NodeType.NAMEDOPERATION,
            NodeType.LABELVALUE,
        ):
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)
            self._resolving.append((key, shell))
            t = self._check_expression(cast(zast.Expression, defn)).ztype
            self._resolving.pop()
            return t
        if defn.nodetype == NodeType.ATOMID:
            defn_atom = cast(zast.AtomId, defn)
            if _is_numeric_id(defn_atom.name):
                t = self._resolve_numeric(defn_atom.name, loc=defn_atom.start)
                # constant folding: set const_value on the definition node
                if t:
                    typename, value, err = parse_number(defn_atom.name)
                    if not err and type(value) is int:
                        self.typing.node_const_value[defn_atom.nodeid] = value
                    elif not err and type(value) is float and typename == "f64":
                        self.typing.node_const_value[defn_atom.nodeid] = value
                return t
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)  # placeholder for alias
            self._resolving.append((key, shell))
            t = self._resolve_name(defn_atom.name)
            self._resolving.pop()
            return t
        # constant folding: handle BinOp at unit level (e.g., b: a + 2)
        if defn.nodetype == NodeType.BINOP:
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)
            self._resolving.append((key, shell))
            t = self._check_binop(cast(zast.BinOp, defn))
            self._resolving.pop()
            return t
        return None

    def _resolve_unit_level_if(
        self, unitname: str, name: str, defn: zast.Expression
    ) -> Optional[ZType]:
        """Resolve a unit-level if definition (compile-time conditional)."""
        assert defn.expression.nodetype == NodeType.IF
        ifnode = cast(zast.If, defn.expression)
        key = f"{unitname}.{name}"
        shell = _make_type(name, ZTypeType.NULL)
        self._resolving.append((key, shell))

        # type-check all conditions and branches
        for clause in ifnode.clauses:
            for _, cond_op in clause.conditions.items():
                self._check_operation(cond_op)
            self._check_statement(clause.statement)
        if ifnode.elseclause:
            self._check_statement(ifnode.elseclause)

        # find the first clause whose conditions are all constant-true
        taken_stmt = None
        for clause in ifnode.clauses:
            all_const = all(
                self.typing.node_const_value.get(cond_op.nodeid) is not None
                for _, cond_op in clause.conditions.items()
            )
            if not all_const:
                self._error(
                    "unit-level if condition must be a compile-time constant",
                    loc=clause.start,
                )
                self._resolving.pop()
                return None
            all_true = all(
                bool(self.typing.node_const_value.get(cond_op.nodeid))
                for _, cond_op in clause.conditions.items()
            )
            if all_true and taken_stmt is None:
                taken_stmt = clause.statement

        if taken_stmt is None:
            if ifnode.elseclause:
                taken_stmt = ifnode.elseclause
            else:
                self._error(
                    "unit-level if: no branch matched and no else clause",
                    loc=ifnode.start,
                )
                self._resolving.pop()
                return None

        # get type from the taken branch's last expression
        t = self._last_statement_type(taken_stmt)
        if t is None or not t.is_ztype:
            self._error(
                "unit-level if branch must produce a value",
                loc=ifnode.start,
            )
            self._resolving.pop()
            return None

        t_ztype = cast(ZType, t)
        self.typing.node_type[ifnode.nodeid] = t_ztype

        # propagate const_value from taken branch if available
        if taken_stmt.statements:
            last_inner = taken_stmt.statements[-1].statementline
            if last_inner.nodetype == NodeType.EXPRESSION:
                inner_expr = cast(zast.Expression, last_inner).expression
                inner_cv = self.typing.node_const_value.get(inner_expr.nodeid)
                if inner_cv is not None:
                    # Stamp both the Expression wrapper (parsed `defn`)
                    # and the inner If: the emitter's `_node_const_value`
                    # helper unwraps Expression to the inner subtype and
                    # consults the typed mirror keyed on the If's
                    # nodeid.
                    self.typing.node_const_value[defn.nodeid] = inner_cv
                    self.typing.node_const_value[ifnode.nodeid] = inner_cv

        # Unit-level ifs don't go through `_check_if`, so the typed
        # mirror has to be built inline. Emitter / SQL-dump consumers
        # read const_value via the typed tree only after Step 6.9.a.
        self._resolving.pop()
        return t_ztype

    def _detect_generic_param(
        self, ppath: zast.Path
    ) -> tuple[Optional[ZType], Optional[ZType]]:
        """Detect a generic param from an as_items entry.

        Returns (resolved_type, default_type). default_type is non-None when
        the entry uses the (constraint.generic default: type) call form.
        """
        default_type = None
        if ppath.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, ppath).expression
            if inner.nodetype == NodeType.CALL:
                call_node = cast(zast.Call, inner)
                pt = self._resolve_typeref(call_node.callable)
                for arg in call_node.arguments:
                    if arg.name == "default":
                        default_type = self._resolve_typeref_from_operation(arg.valtype)
                self.typing.node_type[ppath.nodeid] = pt
                return pt, default_type
        pt = self._resolve_typeref(ppath)
        return pt, None

    def _resolve_function_type(
        self, unitname: str, name: str, func: zast.Function
    ) -> ZType:
        key = f"{unitname}.{name}"
        ftype = _make_type(name, ZTypeType.FUNCTION)
        ftype.is_native = func.is_native
        self._resolved[key] = ftype  # early register for self-reference
        self._resolving.append((key, ftype))

        # tag control flow functions by name (resolved from system.z)
        _CONTROL_KINDS = {
            "return": ControlKind.RETURN,
            "break": ControlKind.BREAK,
            "continue": ControlKind.CONTINUE,
            "error": ControlKind.ERROR,
            "panic": ControlKind.PANIC,
        }
        if func.is_native and name in _CONTROL_KINDS:
            ftype.control_kind = _CONTROL_KINDS[name]

        # pass 1: detect generic params from 'as' clause
        generic_ctx: dict[str, ZType] = {}
        for pname, ppath in func.as_paths().items():
            pt, default_type = self._detect_generic_param(ppath)
            if (
                pt
                and pt.typetype == ZTypeType.GENERIC_PARAM
                and pt.name == "__generic_param"
            ):
                constraint = pt.parent if pt.parent else self.t_null
                ftype.generic_params[pname] = constraint
                ftype.isgeneric = True
                generic_ctx[pname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    ftype.numeric_generic_params.add(pname)
                # store and validate default type
                if default_type:
                    ftype.generic_defaults[pname] = default_type

        # check: methods (functions with a parameter of type 'this') cannot have 'as'
        if generic_ctx and func.as_items:
            has_this = any(
                (
                    ppath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                    and cast(zast.AtomId, ppath).name == "this"
                )
                or (
                    ppath.nodetype == NodeType.DOTTEDPATH
                    and cast(zast.DottedPath, ppath).parent.nodetype == NodeType.ATOMID
                    and cast(zast.AtomId, cast(zast.DottedPath, ppath).parent).name
                    == "this"
                )
                for ppath in func.parameters.values()
            )
            if has_this:
                self._error(
                    "Methods cannot declare generic parameters; "
                    "move the generic parameter to the type definition, "
                    "or make this a static function",
                    loc=func.start,
                )

        # resolve as_functions (static functions in function's 'as' block)
        for mname, mfunc in func.as_functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(ftype, mname, mt)

        # pass 2: resolve non-generic params with generic context
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        if func.returntype:
            stripped_ret, ret_own = _strip_path_ownership(func.returntype)
            rt = self._resolve_typeref(cast(zast.Path, stripped_ret))
            # mirror the resolved type onto the unstripped path so AST
            # consumers (emitter) reading `func.returntype.type` still
            # see the right ZType when the path carried a `.borrow`
            # / `.lock` / `.take` suffix.
            self.typing.node_type[func.returntype.nodeid] = rt
            if rt:
                if not func.is_native and self._check_non_runtime_type(
                    rt,
                    "a return type",
                    func.returntype.start
                    if hasattr(func.returntype, "start")
                    else func.start,
                ):
                    pass
                else:
                    ftype.return_type = rt
            if ret_own is not None:
                ftype.return_ownership = ret_own
        for pname, ppath in func.parameters.items():
            stripped_ppath, p_own = _strip_path_ownership(ppath)
            pt = self._resolve_typeref(cast(zast.Path, stripped_ppath))
            self.typing.node_type[ppath.nodeid] = pt
            if p_own is not None:
                ftype.param_ownership[pname] = p_own
            if (
                pt
                and pt.typetype == ZTypeType.GENERIC_PARAM
                and pt.name == "__generic_param"
            ):
                self._error(
                    f"Generic parameters must be declared in the 'as' section, not 'in': '{pname}'",
                    loc=func.start,
                )
                continue
            if pt and self._check_non_runtime_type(
                pt,
                "a parameter type",
                ppath.start if hasattr(ppath, "start") else func.start,
            ):
                continue
            if pt:
                self._set_child(ftype, pname, pt)
                # detect defaults — read from the post-ownership-strip
                # path so `u8.5.lock` style still resolves the numeric
                # default while a `.lock`/`.borrow`/`.take` suffix is
                # off the table.
                if stripped_ppath.nodetype in (
                    NodeType.ATOMID,
                    NodeType.LABELVALUE,
                ) and _is_numeric_id(cast(zast.AtomId, stripped_ppath).name):
                    _, val, err = parse_number(cast(zast.AtomId, stripped_ppath).name)
                    if not err:
                        ftype.param_defaults[pname] = str(int(val))
                elif stripped_ppath.nodetype == NodeType.DOTTEDPATH:
                    ppath_dp = cast(zast.DottedPath, stripped_ppath)
                    if ppath_dp.parent.nodetype in (
                        NodeType.ATOMID,
                        NodeType.LABELVALUE,
                    ) and _is_numeric_id(cast(zast.AtomId, ppath_dp.parent).name):
                        child_name = ppath_dp.child.name
                        _, val, err = parse_number(
                            cast(zast.AtomId, ppath_dp.parent).name + child_name
                        )
                        if not err:
                            ftype.param_defaults[pname] = str(int(val))
                elif stripped_ppath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
                    defn = self._lookup_definition(
                        cast(zast.AtomId, stripped_ppath).name
                    )
                    if (
                        defn is not None
                        and defn.nodetype == NodeType.FUNCTION
                        and cast(zast.Function, defn).body is not None
                    ):
                        ftype.param_defaults[pname] = cast(
                            zast.AtomId, stripped_ppath
                        ).name
        if generic_ctx:
            self.mono.generic_context.pop()

        # ownership annotations were filled per-parameter / for the
        # return type during resolution above, by stripping `.take` /
        # `.borrow` / `.lock` suffixes off the path and recording the
        # ownership on `ftype`.

        # Record which parameter (if any) is bound to the receiver — i.e.
        # whose declared TYPE was the `this` keyword. Both surface forms
        # (`:this` -> param name "this", `h: this` -> param name "h")
        # produce a path whose value name resolves to "this". Downstream
        # code (missing-arg check, rvalue method-as-value access) reads
        # this field instead of hardcoding the literal "this" so the
        # named-binding form behaves equivalently to the shorthand.
        # Strip ownership first so `t: this.lock` is recognised as a
        # this-receiver too.
        for pname, ppath in func.parameters.items():
            stripped_p, _ = _strip_path_ownership(ppath)
            value_name: Optional[str] = None
            if stripped_p.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
                value_name = cast(zast.AtomId, stripped_p).name
            if value_name == "this":
                ftype.this_param_name = pname
                break

        # Phase A default: unannotated stack-reftype params (string, file,
        # bufreader, user classes — needs_destructor and not heap-allocated)
        # default to BORROW. The C ABI then passes them by pointer so
        # mutation through the param lands in the caller's storage, and the
        # implicit-take logic at call sites naturally skips them (caller
        # retains ownership). Heap-allocated reftypes (`box`, nullable
        # pointers) are already pointer-typed at the C level. Applies
        # uniformly to user code AND natives — native runtime bodies in
        # src/zemitterc_runtime.py have been migrated to match (read-only
        # natives use stringview directly; store-into-receiver natives
        # carry `.take` annotations).
        for pname in func.parameters:
            if pname in ftype.param_ownership:
                continue
            pt = self.typing.child_of(ftype, pname)
            if (
                pt is not None
                and pt.typetype == ZTypeType.CLASS
                and not pt.is_heap_allocated
                and pt.needs_destructor
            ):
                ftype.param_ownership[pname] = ZParamOwnership.BORROW

        # validate function signature ownership rules
        self._validate_function_ownership(ftype, func)

        self._assign_cname_type(ftype, qualified_name=name)
        self._resolving.pop()
        return ftype

    def _validate_function_ownership(self, ftype: ZType, func: zast.Function) -> None:
        """Validate ownership rules on a function signature."""
        own = ftype.param_ownership
        has_return = ftype.return_type is not None
        ret_is_borrow = ftype.return_ownership == ZParamOwnership.BORROW

        # lock parameters are only valid when there is a return value
        has_lock_param = any(v == ZParamOwnership.LOCK for v in own.values())
        if has_lock_param and not has_return:
            self._error(
                "parameter marked as 'lock' but function has no return value",
                loc=func.start,
                err=ERR.OWNERERROR,
                hint="lock parameters are only useful when the function returns a borrowed value",
            )

        # a function returning borrow must have at least one lock parameter
        if ret_is_borrow and not has_lock_param:
            self._error(
                "function returns 'borrow' but has no 'lock' parameter",
                loc=func.start,
                err=ERR.OWNERERROR,
                hint="add .lock to a parameter to borrow from it",
            )

        # .lock on known valtype parameters is an error (locking requires
        # identity, which valtypes don't have). .borrow is fine on valtypes
        # (it's the default — just means copy without invalidation).
        # Exempt: receiver params (`t: this.lock`) — even valtype-class
        # receivers (e.g. `str`) carry internal heap state whose source
        # slot must be locked against reassignment for borrowed views to
        # remain valid.
        for pname, pown in own.items():
            if pown == ZParamOwnership.LOCK:
                if ftype.this_param_name == pname:
                    continue
                ptype = self.typing.child_of(ftype, pname)
                if (
                    ptype
                    and _is_valtype(ptype)
                    and ptype.typetype != ZTypeType.GENERIC_PARAM
                ):
                    self._error(
                        f"Cannot use '.lock' on valtype parameter '{pname}' "
                        f"(type '{ptype.name}') — locking requires identity (use a class)",
                        loc=func.start,
                        err=ERR.OWNERERROR,
                    )

    def _check_is_as_name_collision(
        self,
        name: str,
        is_items: dict,
        as_items: dict,
        is_functions: dict,
        as_functions: dict,
        loc: Token,
    ) -> None:
        """Check for name collisions between 'is' and 'as' sections."""
        # function name in both sections
        for mname in is_functions.keys() & as_functions.keys():
            self._error(
                f"'{mname}' is defined in both 'is' and 'as' sections of '{name}'",
                loc=loc,
            )
        # field in 'is' clashes with function in 'as'
        for mname in is_items.keys() & as_functions.keys():
            self._error(
                f"'{mname}' is defined in both 'is' and 'as' sections of '{name}'",
                loc=loc,
            )
        # function in 'is' clashes with item in 'as' (skip special names and generics)
        for mname in is_functions.keys() & as_items.keys():
            if mname not in _AS_SPECIAL_NAMES:
                self._error(
                    f"'{mname}' is defined in both 'is' and 'as' sections of '{name}'",
                    loc=loc,
                )
        # field in 'is' clashes with item in 'as' (skip special names and generics)
        for mname in is_items.keys() & as_items.keys():
            if mname not in _AS_SPECIAL_NAMES:
                self._error(
                    f"'{mname}' is defined in both 'is' and 'as' sections of '{name}'",
                    loc=loc,
                )

    def _resolve_class_type(
        self, unitname: str, name: str, cls: zast.ObjectDef
    ) -> ZType:
        key = f"{unitname}.{name}"
        ctype = _make_type(name, ZTypeType.CLASS)
        self._resolved[key] = ctype  # early register for self-reference
        self._resolving.append((key, ctype))

        ctype.is_valtype = False  # classes are reference types
        if cls.is_native:
            ctype.is_native = True
            if name == "String":
                ctype.subtype = ZSubType.STRING
            elif name == "StringView":
                ctype.subtype = ZSubType.STRINGVIEW
        _set_destructor_metadata(ctype)
        self._assign_cname_type(ctype)

        # pass 1: detect generic params (now in as_items)
        generic_ctx: dict[str, ZType] = {}
        for fname, fpath in cls.as_paths().items():
            ft, default_type = self._detect_generic_param(fpath)
            if (
                ft
                and ft.typetype == ZTypeType.GENERIC_PARAM
                and ft.name == "__generic_param"
            ):
                constraint = ft.parent if ft.parent else self.t_null
                ctype.generic_params[fname] = constraint
                ctype.isgeneric = True
                generic_ctx[fname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    ctype.numeric_generic_params.add(fname)
                if default_type:
                    ctype.generic_defaults[fname] = default_type

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(
            cls.is_paths(), cls.start
        )
        if typedef_base_type is not None:
            if typedef_base_type.typetype not in (ZTypeType.CLASS, ZTypeType.PROTOCOL):
                self._error(
                    f"Class typedef must wrap a class or protocol type, not '{typedef_base_type.name}'",
                    loc=cls.start,
                )
            return self._finalize_typedef(
                unitname,
                name,
                ctype,
                typedef_base_type,
                typedef_field,
                cls.as_items,
                cls.as_functions(),
                cls.functions(),
                cls.start,
                generic_ctx,
            )

        # pass 2: resolve non-generic fields with generic context
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        for fname, fpath in cls.is_paths().items():
            stripped_fpath, f_own = _strip_path_ownership(fpath)
            ft = self._resolve_typeref(cast(zast.Path, stripped_fpath))
            if (
                ft
                and ft.typetype == ZTypeType.GENERIC_PARAM
                and ft.name == "__generic_param"
            ):
                self._error(
                    f"Generic parameters must be declared in the 'as' section, not 'is': '{fname}'",
                    loc=cls.start,
                )
                continue
            if ft:
                self._set_child(ctype, fname, ft)
                # detect .private field type (friend access) on the
                # post-ownership-strip path
                if (
                    stripped_fpath.nodetype == NodeType.DOTTEDPATH
                    and cast(zast.DottedPath, stripped_fpath).child.name == "private"
                ):
                    ctype.private_fields.add(fname)
                # Phase 7: .lock fields are now allowed on classes.
                # Classes are stack-allocated with single-owner semantics,
                # so they naturally prevent copies that would duplicate locks.
                if f_own == ZParamOwnership.LOCK:
                    ctype.lock_field_names.add(fname)
                    ctype.has_lock_fields = True
                elif f_own is not None:
                    self._error(
                        f"Only '.lock' is permitted as a field type modifier; "
                        f"got '.{f_own.name.lower()}' on field '{fname}'",
                        loc=cls.start,
                        err=ERR.TYPEERROR,
                    )
                # detect field defaults
                if fpath.nodetype in (
                    NodeType.ATOMID,
                    NodeType.LABELVALUE,
                ) and _is_numeric_id(cast(zast.AtomId, fpath).name):
                    _, val, err = parse_number(cast(zast.AtomId, fpath).name)
                    if not err:
                        ctype.param_defaults[fname] = str(int(val))
                elif fpath.nodetype == NodeType.DOTTEDPATH:
                    fpath_dp = cast(zast.DottedPath, fpath)
                    if fpath_dp.parent.nodetype in (
                        NodeType.ATOMID,
                        NodeType.LABELVALUE,
                    ) and _is_numeric_id(cast(zast.AtomId, fpath_dp.parent).name):
                        child_name = fpath_dp.child.name
                        _, val, err = parse_number(
                            cast(zast.AtomId, fpath_dp.parent).name + child_name
                        )
                        if not err:
                            ctype.param_defaults[fname] = str(int(val))
                elif fpath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
                    defn = self._lookup_definition(cast(zast.AtomId, fpath).name)
                    if (
                        defn is not None
                        and defn.nodetype == NodeType.FUNCTION
                        and cast(zast.Function, defn).body is not None
                    ):
                        ctype.param_defaults[fname] = cast(zast.AtomId, fpath).name
        if generic_ctx:
            self.mono.generic_context.pop()

        self._check_is_as_name_collision(
            name,
            cls.is_paths(),
            cls.as_items,
            cls.functions(),
            cls.as_functions(),
            cls.start,
        )

        # for generic classes, defer method resolution and meta.create to monomorphization
        if not ctype.isgeneric:
            for mname, mfunc in cls.functions().items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                self._set_child(ctype, mname, mt)
            # as_functions (methods defined in 'as' block)
            for mname, mfunc in cls.as_functions().items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                self._set_child(ctype, mname, mt)

            # as_items: protocol satisfaction — must run before method body
            # check so create_disabled flag is set before body-check.
            self._process_as_items_protocols(name, ctype, cls.as_items, cls.start)

            # generate meta.create constructor type — must be available before
            # method bodies are checked so `meta.create` inside a body can
            # resolve to this class's raw allocator.
            is_func_names = set(cls.functions().keys())
            field_names = set(cls.is_paths().keys()) | is_func_names
            create_type = self._make_meta_create_type(
                name, ctype, is_func_names, field_names
            )
            ctype.meta_create = create_type
            # Only install the default 'create' child if the user has not
            # disabled it via 'create: null'.
            if not self.typing.has_child(ctype, "create") and not ctype.create_disabled:
                self._set_child(ctype, "create", create_type)

            # typecheck method bodies
            self.func_ctx.enclosing_type.append(ctype)
            for mname, mfunc in cls.functions().items():
                if mfunc.body:
                    self.func_ctx.body.append(
                        cast(ZType, self.typing.child_of(ctype, mname))
                    )
                    self._check_function_body(f"{name}.{mname}", mfunc)
                    self.func_ctx.body.pop()
            for mname, mfunc in cls.as_functions().items():
                if mfunc.body:
                    self.func_ctx.body.append(
                        cast(ZType, self.typing.child_of(ctype, mname))
                    )
                    self._check_function_body(f"{name}.{mname}", mfunc)
                    self.func_ctx.body.pop()
            self.func_ctx.enclosing_type.pop()

        ctype.public_members = _extract_public_members(cls.as_items)
        priv = _check_private_redefinition(cls.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)
        _set_field_cleanup_metadata(self.typing, ctype)
        self._resolving.pop()
        return ctype

    def _resolve_tag(
        self,
        type_kind: str,
        name: str,
        ztype: ZType,
        as_items: dict,
        subtype_names: list,
        loc: Token,
    ) -> None:
        """Resolve tag discriminator for a union or variant type.

        Scans as_items for a .tag type reference, validates it against
        subtype_names, and populates ztype.tag_type and
        self.typing.child_of(ztype, "tag").

        type_kind is "Union" or "Variant" (for error messages).
        """
        custom_tag_data = None
        tag_count = 0

        for as_name, as_path in as_items.items():
            as_type = (
                self._resolve_dotted_path(cast(zast.DottedPath, as_path))
                if as_path.nodetype == NodeType.DOTTEDPATH
                else self._resolve_typeref(as_path)
            )
            is_tag = (
                (as_type and as_type.typetype == ZTypeType.TAG)
                or (as_type and as_type.is_tag_generic_origin)
                or (as_type and as_type.isgeneric and as_type.name == "tag")
            )
            if is_tag:
                assert as_type is not None
                tag_count += 1
                if tag_count > 1:
                    self._error(
                        f"{type_kind} '{name}' has multiple .tag items in 'as' block",
                        loc=loc,
                    )
                    break
                if as_type.parent:
                    custom_tag_data = as_type.parent
                elif as_path.nodetype == NodeType.DOTTEDPATH and cast(
                    zast.DottedPath, as_path
                ).parent.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
                    as_path_dp = cast(zast.DottedPath, as_path)
                    custom_tag_data = self.typing.node_type.get(
                        as_path_dp.parent.nodeid
                    )
                    if not custom_tag_data:
                        custom_tag_data = self._resolve_name(
                            cast(zast.AtomId, as_path_dp.parent).name
                        )

        if custom_tag_data and custom_tag_data.typetype == ZTypeType.DATA:
            # validate: data labels must match subtypes 1:1
            data_labels = [
                k for k in self.typing.child_names_of(custom_tag_data) if k != "tag"
            ]
            if sorted(data_labels) != sorted(subtype_names):
                missing_in_data = set(subtype_names) - set(data_labels)
                missing_in_type = set(data_labels) - set(subtype_names)
                msg_parts = []
                if missing_in_data:
                    msg_parts.append(
                        f"missing in data: {', '.join(sorted(missing_in_data))}"
                    )
                if missing_in_type:
                    lk = type_kind.lower()
                    msg_parts.append(
                        f"missing in {lk}: {', '.join(sorted(missing_in_type))}"
                    )
                self._error(
                    f"{type_kind} '{name}' tag data labels do not match subtypes: "
                    + "; ".join(msg_parts),
                    loc=loc,
                )
            # validate: data values must be unique
            seen_values: dict = {}
            for dl in data_labels:
                child = self.typing.child_of(custom_tag_data, dl)
                val = child.name if child else None
                if val in seen_values:
                    self._error(
                        f"{type_kind} '{name}' tag data has duplicate value "
                        f"'{val}' for labels '{seen_values[val]}' and '{dl}'",
                        loc=loc,
                    )
                seen_values[val] = dl

            # use custom data values as discriminators
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for sname in subtype_names:
                child = self.typing.child_of(custom_tag_data, sname)
                val = child.name if child else str(subtype_names.index(sname))
                self._set_child(tag_type, sname, _make_type(val, ZTypeType.RECORD))
            ztype.tag_type = tag_type
            self._set_child(ztype, "tag", custom_tag_data)

        elif custom_tag_data and custom_tag_data.typetype == ZTypeType.RECORD:
            # numeric type tag (e.g., u16.tag) — auto-generate sequential values
            num_subtypes = len(subtype_names)
            if custom_tag_data.name == "u8" and num_subtypes > 256:
                self._error(
                    f"{type_kind} '{name}' has {num_subtypes} subtypes, "
                    f"exceeds u8 tag capacity (max 256)",
                    loc=loc,
                )
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                self._set_child(tag_type, sname, _make_type(str(i), ZTypeType.RECORD))
            ztype.tag_type = tag_type
            gen_data = _make_type(f"{name}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                self._set_child(gen_data, sname, _make_type(str(i), ZTypeType.RECORD))
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.is_tag_generic_origin = True
            self._set_child(gen_data, "tag", gen_tag)
            self._set_child(ztype, "tag", gen_data)

        else:
            # no custom tag: auto-generate with u8 default
            num_subtypes = len(subtype_names)
            if num_subtypes > 256:
                self._error(
                    f"{type_kind} '{name}' has {num_subtypes} subtypes, "
                    f"exceeds default u8 tag capacity (max 256). "
                    f"Specify a custom tag type via 'as' block",
                    loc=loc,
                )
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                self._set_child(tag_type, sname, _make_type(str(i), ZTypeType.RECORD))
            ztype.tag_type = tag_type
            gen_data = _make_type(f"{name}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                self._set_child(gen_data, sname, _make_type(str(i), ZTypeType.RECORD))
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.is_tag_generic_origin = True
            self._set_child(gen_data, "tag", gen_tag)
            self._set_child(ztype, "tag", gen_data)

    def _resolve_union_type(
        self, unitname: str, name: str, union_defn: zast.ObjectDef
    ) -> ZType:
        key = f"{unitname}.{name}"
        utype = _make_type(name, ZTypeType.UNION)
        self._resolved[key] = utype  # early register for self-reference
        self._resolving.append((key, utype))

        utype.is_valtype = False  # unions are reference types
        _set_destructor_metadata(utype)
        self._assign_cname_type(utype)

        # pass 1: detect generic params (now in as_items)
        generic_ctx: dict[str, ZType] = {}
        for sname, spath in union_defn.as_paths().items():
            st, default_type = self._detect_generic_param(spath)
            if (
                st
                and st.typetype == ZTypeType.GENERIC_PARAM
                and st.name == "__generic_param"
            ):
                constraint = st.parent if st.parent else self.t_null
                utype.generic_params[sname] = constraint
                utype.isgeneric = True
                generic_ctx[sname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    utype.numeric_generic_params.add(sname)
                if default_type:
                    utype.generic_defaults[sname] = default_type
        self._release_template_cname(utype)

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(
            union_defn.is_paths(), union_defn.start
        )
        if typedef_base_type is not None:
            if typedef_base_type.typetype != ZTypeType.UNION:
                self._error(
                    f"Union typedef must wrap a union type, not '{typedef_base_type.name}'",
                    loc=union_defn.start,
                )
            return self._finalize_typedef(
                unitname,
                name,
                utype,
                typedef_base_type,
                typedef_field,
                union_defn.as_items,
                union_defn.as_functions(),
                union_defn.functions(),
                union_defn.start,
                generic_ctx,
            )

        # pass 2: resolve subtype items with generic context
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        subtype_names = list(union_defn.is_paths().keys())
        for sname, spath in union_defn.is_paths().items():
            stripped_spath, arm_own = _strip_path_ownership(spath)
            stripped_path_typed = cast(zast.Path, stripped_spath)
            st_check = self._resolve_typeref(stripped_path_typed)
            if (
                st_check
                and st_check.typetype == ZTypeType.GENERIC_PARAM
                and st_check.name == "__generic_param"
            ):
                self._error(
                    f"Generic parameters must be declared in the 'as' section, not 'is': '{sname}'",
                    loc=union_defn.start,
                )
                continue
            if (
                stripped_spath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                and cast(zast.AtomId, stripped_spath).name == "null"
            ):
                st = _make_type("null", ZTypeType.NULL)
                st.is_valtype = True
            else:
                st = self._resolve_typeref(stripped_path_typed)
            if st:
                self._set_child(utype, sname, st)
            # detect locked arms: arm declared as `name: t.lock`. Only LOCK is
            # permitted; .take/.borrow on an arm are rejected.
            if arm_own == ZParamOwnership.LOCK:
                utype.lock_arm_names.add(sname)
            elif arm_own is not None:
                self._error(
                    f"Only '.lock' is permitted as a union arm modifier; "
                    f"got '.{arm_own.name.lower()}' on arm '{sname}'",
                    loc=union_defn.start,
                    err=ERR.TYPEERROR,
                )
        if generic_ctx:
            self.mono.generic_context.pop()

        self._check_is_as_name_collision(
            name,
            union_defn.is_paths(),
            union_defn.as_items,
            union_defn.functions(),
            union_defn.as_functions(),
            union_defn.start,
        )

        # for generic unions, skip tag generation (done at monomorphization time)
        if utype.isgeneric:
            # resolve methods
            for mname, mfunc in union_defn.functions().items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                self._set_child(utype, mname, mt)
            for mname, mfunc in union_defn.as_functions().items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                self._set_child(utype, mname, mt)
            utype.public_members = _extract_public_members(union_defn.as_items)
            priv = _check_private_redefinition(union_defn.as_items)
            if priv:
                self._error("'private' cannot be redefined", loc=priv.start)
            _set_field_cleanup_metadata(self.typing, utype)
            self._resolving.pop()
            return utype

        # resolve tag from as_items
        self._resolve_tag(
            "Union", name, utype, union_defn.as_items, subtype_names, union_defn.start
        )

        # resolve methods
        for mname, mfunc in union_defn.functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(utype, mname, mt)
        for mname, mfunc in union_defn.as_functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(utype, mname, mt)

        # typecheck method bodies (non-generic only)
        self.func_ctx.enclosing_type.append(utype)
        for mname, mfunc in union_defn.functions().items():
            if mfunc.body:
                self.func_ctx.body.append(
                    cast(ZType, self.typing.child_of(utype, mname))
                )
                self._check_function_body(f"{name}.{mname}", mfunc)
                self.func_ctx.body.pop()
        for mname, mfunc in union_defn.as_functions().items():
            if mfunc.body:
                self.func_ctx.body.append(
                    cast(ZType, self.typing.child_of(utype, mname))
                )
                self._check_function_body(f"{name}.{mname}", mfunc)
                self.func_ctx.body.pop()
        self.func_ctx.enclosing_type.pop()

        utype.public_members = _extract_public_members(union_defn.as_items)
        priv = _check_private_redefinition(union_defn.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)

        # Unions cannot be constructed via bare-name: a specific subtype must
        # be selected (myunion.subtype value). Mark create as disabled so the
        # unified call dispatch reports a targeted error.
        utype.create_disabled = True

        _set_field_cleanup_metadata(self.typing, utype)
        # Destructor elision: when every arm is either `null` or a `.lock`
        # reference, no runtime cleanup is needed. Locked arms hold a
        # borrowed pointer (no payload to free); null arms have no payload.
        # Mixed unions (some owned, some locked) keep the destructor; the
        # emitter handles per-arm switch elision separately.
        if not utype.isgeneric:
            self._maybe_elide_union_destructor(utype, union_defn)
        self._resolving.pop()
        return utype

    def _lift_locked_arm_borrow(
        self,
        union_type: ZType,
        callable_dp: zast.DottedPath,
        call: zast.Call,
    ) -> None:
        """When constructing a locked union arm, mark the construction as
        borrowing from the `from:` source so the borrow-lock machinery
        propagates the source path to the assignment target.

        Mirrors `_check_protocol_borrow`'s borrow-lift pattern, except the
        check is keyed on the union arm's `lock_arm_names` membership
        rather than a `.borrow` constructor name.
        """
        if union_type.typetype != ZTypeType.UNION:
            return
        if not union_type.lock_arm_names:
            return
        arm_name = callable_dp.child.name
        if arm_name not in union_type.lock_arm_names:
            return
        # locate the from: arg (or the first positional arg if no from:)
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if from_arg is None:
            for arg in call.arguments:
                if not arg.name:
                    from_arg = arg
                    break
        if from_arg is None:
            return
        src_path = self._get_dotted_path_tuple(from_arg.valtype)
        if src_path:
            self._pending_borrow_lock = src_path

    def _maybe_elide_union_destructor(
        self, utype: ZType, union_defn: zast.ObjectDef
    ) -> None:
        """Mark a union's destructor as not-needed when no arm requires
        runtime cleanup (every arm is `null` or a `.lock` reference)."""
        for sname, spath in union_defn.is_paths().items():
            is_null = (
                spath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                and cast(zast.AtomId, spath).name == "null"
            )
            is_locked = sname in utype.lock_arm_names
            if not (is_null or is_locked):
                return
        utype.needs_destructor = False
        utype.destructor_name = None

    def _resolve_variant_type(
        self, unitname: str, name: str, variant_defn: zast.ObjectDef
    ) -> ZType:
        """Resolve a variant definition into a VARIANT ZType.

        Variants are value types (stack-allocated, copy semantics).
        All subtypes must also be value types.
        """
        key = f"{unitname}.{name}"
        vtype = _make_type(name, ZTypeType.VARIANT)
        self._resolved[key] = vtype
        self._resolving.append((key, vtype))

        vtype.is_valtype = True  # variants are value types
        _set_destructor_metadata(vtype)
        self._assign_cname_type(vtype)

        # pass 1: detect generic params (in as_items)
        generic_ctx: dict[str, ZType] = {}
        for sname, spath in variant_defn.as_paths().items():
            st, default_type = self._detect_generic_param(spath)
            if (
                st
                and st.typetype == ZTypeType.GENERIC_PARAM
                and st.name == "__generic_param"
            ):
                constraint = st.parent if st.parent else self.t_null
                vtype.generic_params[sname] = constraint
                vtype.isgeneric = True
                generic_ctx[sname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    vtype.numeric_generic_params.add(sname)
                if default_type:
                    vtype.generic_defaults[sname] = default_type

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(
            variant_defn.is_paths(), variant_defn.start
        )
        if typedef_base_type is not None:
            if typedef_base_type.typetype != ZTypeType.VARIANT:
                self._error(
                    f"Variant typedef must wrap a variant type, not '{typedef_base_type.name}'",
                    loc=variant_defn.start,
                )
            return self._finalize_typedef(
                unitname,
                name,
                vtype,
                typedef_base_type,
                typedef_field,
                variant_defn.as_items,
                variant_defn.as_functions(),
                variant_defn.functions(),
                variant_defn.start,
                {},
            )

        # resolve each subtype item (with generic context if applicable)
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        subtype_names = list(variant_defn.is_paths().keys())
        for sname, spath in variant_defn.is_paths().items():
            stripped_spath, arm_own = _strip_path_ownership(spath)
            if (
                stripped_spath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                and cast(zast.AtomId, stripped_spath).name == "null"
            ):
                st = _make_type("null", ZTypeType.NULL)
                st.is_valtype = True
            else:
                st = self._resolve_typeref(cast(zast.Path, stripped_spath))
                # reject non-valtypes (skip for generic params — checked at instantiation)
                if st and st.typetype != ZTypeType.GENERIC_PARAM:
                    if st.is_valtype is not None and not st.is_valtype:
                        self._error(
                            f"Variant '{name}' subtype '{sname}' must be a value type",
                            loc=variant_defn.start,
                        )
                    elif st.typetype in (ZTypeType.CLASS, ZTypeType.UNION):
                        self._error(
                            f"Variant '{name}' subtype '{sname}' must be a value type",
                            loc=variant_defn.start,
                        )
                    elif st.subtype == ZSubType.STRING:
                        self._error(
                            f"Variant '{name}' subtype '{sname}' must be a value type",
                            loc=variant_defn.start,
                        )
            if st:
                self._set_child(vtype, sname, st)
            # variants are valtype-only; locked arms hold an external pointer
            # (reftype-flavored ownership) which conflicts with the inline
            # storage model. Reject .lock arms here for a clear diagnostic.
            if arm_own == ZParamOwnership.LOCK:
                self._error(
                    f"Variant '{name}' arm '{sname}' cannot use '.lock'; "
                    f"locked arms are only permitted on unions",
                    loc=variant_defn.start,
                    err=ERR.TYPEERROR,
                )
            elif arm_own is not None:
                self._error(
                    f"Only '.lock' is permitted as an arm modifier (and "
                    f"only on unions); got '.{arm_own.name.lower()}' on "
                    f"arm '{sname}'",
                    loc=variant_defn.start,
                    err=ERR.TYPEERROR,
                )
        if generic_ctx:
            self.mono.generic_context.pop()

        self._check_is_as_name_collision(
            name,
            variant_defn.is_paths(),
            variant_defn.as_items,
            variant_defn.functions(),
            variant_defn.as_functions(),
            variant_defn.start,
        )

        # for generic variants, skip tag generation (done at monomorphization time)
        if vtype.isgeneric:
            for mname, mfunc in variant_defn.functions().items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                self._set_child(vtype, mname, mt)
            for mname, mfunc in variant_defn.as_functions().items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                self._set_child(vtype, mname, mt)
            vtype.public_members = _extract_public_members(variant_defn.as_items)
            priv = _check_private_redefinition(variant_defn.as_items)
            if priv:
                self._error("'private' cannot be redefined", loc=priv.start)

            # Variants: no bare-name construction (subtype must be selected).
            vtype.create_disabled = True

            _set_field_cleanup_metadata(self.typing, vtype)
            self._reject_valtype_reftype_fields(
                name,
                vtype,
                set(variant_defn.is_paths().keys()),
                "variant",
                variant_defn.start,
            )
            self._resolving.pop()
            return vtype

        # resolve tag from as_items
        self._resolve_tag(
            "Variant",
            name,
            vtype,
            variant_defn.as_items,
            subtype_names,
            variant_defn.start,
        )

        # resolve methods
        for mname, mfunc in variant_defn.functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(vtype, mname, mt)
        for mname, mfunc in variant_defn.as_functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(vtype, mname, mt)

        # typecheck method bodies (non-generic only — variants don't support generics yet)
        self.func_ctx.enclosing_type.append(vtype)
        for mname, mfunc in variant_defn.functions().items():
            if mfunc.body:
                self.func_ctx.body.append(
                    cast(ZType, self.typing.child_of(vtype, mname))
                )
                self._check_function_body(f"{name}.{mname}", mfunc)
                self.func_ctx.body.pop()
        for mname, mfunc in variant_defn.as_functions().items():
            if mfunc.body:
                self.func_ctx.body.append(
                    cast(ZType, self.typing.child_of(vtype, mname))
                )
                self._check_function_body(f"{name}.{mname}", mfunc)
                self.func_ctx.body.pop()
        self.func_ctx.enclosing_type.pop()

        # auto-generate == and != for non-generic variants
        self._synthesize_eq(vtype)

        vtype.public_members = _extract_public_members(variant_defn.as_items)
        priv = _check_private_redefinition(variant_defn.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)

        # Variants cannot be constructed via bare-name: a specific subtype
        # must be selected (myvariant.subtype value). Mark create as disabled
        # so the unified call dispatch reports a targeted error.
        vtype.create_disabled = True

        _set_field_cleanup_metadata(self.typing, vtype)
        self._reject_valtype_reftype_fields(
            name,
            vtype,
            set(variant_defn.is_paths().keys()),
            "variant",
            variant_defn.start,
        )
        self._resolving.pop()
        return vtype

    def _resolve_data_type(
        self, unitname: str, name: str, data_defn: zast.Data
    ) -> ZType:
        """Resolve a data definition into a DATA ZType with children for each element.

        Children are keyed by element name (text label or ordinal identifier).
        Each child ZType's name stores the literal value (e.g. "10", "0")
        and its type is the resolved numeric type (stored as parent).
        """
        key = f"{unitname}.{name}"
        dtype = _make_type(name, ZTypeType.DATA)
        self._resolved[key] = dtype
        self._resolving.append((key, dtype))

        dtype.is_valtype = False  # data is a reference type (constant array)

        # Resolve each data element, assigning ordinal identifiers to unnamed elements
        element_type: Optional[ZType] = None  # inferred from first element
        ordinal = 0
        for item in data_defn.data:
            if item.name is not None:
                ename = item.name
            else:
                ename = str(ordinal)
            ordinal += 1

            # Resolve the value — store as a type with the value as name
            if item.valtype.nodetype in (
                NodeType.ATOMID,
                NodeType.LABELVALUE,
            ) and _is_numeric_id(cast(zast.AtomId, item.valtype).name):
                item_valtype_atom = cast(zast.AtomId, item.valtype)
                if element_type is None:
                    element_type = self._resolve_numeric(
                        item_valtype_atom.name, loc=item_valtype_atom.start
                    )
                # parse the actual numeric value for storage
                _, val, err = parse_number(item_valtype_atom.name)
                if not err:
                    val_str = str(int(val)) if type(val) is not float else str(val)
                    vt = _make_type(val_str, ZTypeType.RECORD)
                    vt.is_valtype = True
                    self._set_child(dtype, ename, vt)
            elif item.valtype.nodetype in (
                NodeType.ATOMID,
                NodeType.DOTTEDPATH,
                NodeType.ATOMSTRING,
                NodeType.EXPRESSION,
                NodeType.LABELVALUE,
            ):
                et = self._resolve_typeref(cast(zast.Path, item.valtype))
                if et:
                    self._set_child(dtype, ename, et)

        # Store element type for later use
        if element_type:
            dtype.element_type = element_type

        # Generate .tag subtype — monomorphized tag(element_type) with parent=data
        et_name = element_type.name if element_type else "i64"
        tag_type = _make_type(f"tag__{et_name}", ZTypeType.RECORD, parent=dtype)
        tag_type.is_valtype = True
        tag_type.is_tag_generic_origin = True
        self._set_child(dtype, "tag", tag_type)

        self._resolving.pop()
        return dtype

    def _resolve_record_type(
        self, unitname: str, name: str, rec: zast.ObjectDef
    ) -> ZType:
        key = f"{unitname}.{name}"
        rtype = _make_type(name, ZTypeType.RECORD)
        self._resolved[key] = rtype  # early register for self-reference
        self._resolving.append((key, rtype))

        rtype.is_valtype = True  # records are value types
        if rec.is_native:
            rtype.is_native = True
            if name == "never":
                rtype.typetype = ZTypeType.NEVER
            elif name == "null":
                rtype.typetype = ZTypeType.NULL
        _set_destructor_metadata(rtype)
        self._assign_cname_type(rtype)

        # pass 1: detect generic params (now in as_items)
        generic_ctx: dict[str, ZType] = {}
        for fname, fpath in rec.as_paths().items():
            ft, default_type = self._detect_generic_param(fpath)
            if (
                ft
                and ft.typetype == ZTypeType.GENERIC_PARAM
                and ft.name == "__generic_param"
            ):
                constraint = ft.parent if ft.parent else self.t_null
                rtype.generic_params[fname] = constraint
                rtype.isgeneric = True
                generic_ctx[fname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    rtype.numeric_generic_params.add(fname)
                if default_type:
                    rtype.generic_defaults[fname] = default_type

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(
            rec.is_paths(), rec.start
        )
        if typedef_base_type is not None:
            if typedef_base_type.typetype not in (ZTypeType.RECORD, ZTypeType.FACET):
                self._error(
                    f"Record typedef must wrap a record or facet type, not '{typedef_base_type.name}'",
                    loc=rec.start,
                )
            return self._finalize_typedef(
                unitname,
                name,
                rtype,
                typedef_base_type,
                typedef_field,
                rec.as_items,
                rec.as_functions(),
                rec.functions(),
                rec.start,
                generic_ctx,
            )

        # pass 2: resolve non-generic fields with generic context
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        for fname, fpath in rec.is_paths().items():
            stripped_fpath, f_own = _strip_path_ownership(fpath)
            ft = self._resolve_typeref(cast(zast.Path, stripped_fpath))
            if (
                ft
                and ft.typetype == ZTypeType.GENERIC_PARAM
                and ft.name == "__generic_param"
            ):
                self._error(
                    f"Generic parameters must be declared in the 'as' section, not 'is': '{fname}'",
                    loc=rec.start,
                )
                continue
            if ft:
                self._set_child(rtype, fname, ft)
                # detect .private field type (friend access) on the
                # post-ownership-strip path
                if (
                    stripped_fpath.nodetype == NodeType.DOTTEDPATH
                    and cast(zast.DottedPath, stripped_fpath).child.name == "private"
                ):
                    rtype.private_fields.add(fname)
                # detect .lock field annotation (Phase B)
                if f_own == ZParamOwnership.LOCK:
                    rtype.lock_field_names.add(fname)
                    rtype.has_lock_fields = True
                elif f_own is not None:
                    self._error(
                        f"Only '.lock' is permitted as a field type modifier; "
                        f"got '.{f_own.name.lower()}' on field '{fname}'",
                        loc=rec.start,
                        err=ERR.TYPEERROR,
                    )
                # detect field defaults
                if fpath.nodetype in (
                    NodeType.ATOMID,
                    NodeType.LABELVALUE,
                ) and _is_numeric_id(cast(zast.AtomId, fpath).name):
                    _, val, err = parse_number(cast(zast.AtomId, fpath).name)
                    if not err:
                        rtype.param_defaults[fname] = str(int(val))
                elif fpath.nodetype == NodeType.DOTTEDPATH:
                    fpath_dp = cast(zast.DottedPath, fpath)
                    if fpath_dp.parent.nodetype in (
                        NodeType.ATOMID,
                        NodeType.LABELVALUE,
                    ) and _is_numeric_id(cast(zast.AtomId, fpath_dp.parent).name):
                        child_name = fpath_dp.child.name
                        _, val, err = parse_number(
                            cast(zast.AtomId, fpath_dp.parent).name + child_name
                        )
                        if not err:
                            rtype.param_defaults[fname] = str(int(val))
                elif fpath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
                    defn = self._lookup_definition(cast(zast.AtomId, fpath).name)
                    if (
                        defn is not None
                        and defn.nodetype == NodeType.FUNCTION
                        and cast(zast.Function, defn).body is not None
                    ):
                        rtype.param_defaults[fname] = cast(zast.AtomId, fpath).name
        if generic_ctx:
            self.mono.generic_context.pop()
        self._check_is_as_name_collision(
            name,
            rec.is_paths(),
            rec.as_items,
            rec.functions(),
            rec.as_functions(),
            rec.start,
        )
        for mname, mfunc in rec.functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(rtype, mname, mt)
        # as_functions (methods defined in 'as' block)
        for mname, mfunc in rec.as_functions().items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(rtype, mname, mt)

        # Records cannot have .lock fields or this.borrow constructors —
        # use a class instead (classes have identity and single-owner semantics).
        self._reject_record_lock_features(name, rec, rtype)

        # as_items: protocol satisfaction — must run before method body check
        # so create_disabled flag is set before body-check.
        self._process_as_items_protocols(name, rtype, rec.as_items, rec.start)

        # generate meta.create constructor type — must be available before
        # method bodies are checked so `meta.create` inside a body can
        # resolve to this record's raw allocator.
        is_func_names = set(rec.functions().keys())
        field_names = set(rec.is_paths().keys()) | is_func_names
        create_type = self._make_meta_create_type(
            name, rtype, is_func_names, field_names
        )
        rtype.meta_create = create_type
        # Only install the default 'create' child if the user has not
        # disabled it via 'create: null'.
        if not self.typing.has_child(rtype, "create") and not rtype.create_disabled:
            self._set_child(rtype, "create", create_type)

        # typecheck method bodies (non-generic only)
        if not rtype.isgeneric:
            self.func_ctx.enclosing_type.append(rtype)
            for mname, mfunc in rec.functions().items():
                if mfunc.body:
                    self.func_ctx.body.append(
                        cast(ZType, self.typing.child_of(rtype, mname))
                    )
                    self._check_function_body(f"{name}.{mname}", mfunc)
                    self.func_ctx.body.pop()
            for mname, mfunc in rec.as_functions().items():
                if mfunc.body:
                    self.func_ctx.body.append(
                        cast(ZType, self.typing.child_of(rtype, mname))
                    )
                    self._check_function_body(f"{name}.{mname}", mfunc)
                    self.func_ctx.body.pop()
            self.func_ctx.enclosing_type.pop()

        # auto-generate == and != for non-generic records
        if not rtype.isgeneric and not rec.is_native:
            self._synthesize_eq(rtype)

        rtype.public_members = _extract_public_members(rec.as_items)
        priv = _check_private_redefinition(rec.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)
        _set_field_cleanup_metadata(self.typing, rtype)
        self._reject_valtype_reftype_fields(
            name, rtype, set(rec.is_paths().keys()), "record", rec.start
        )
        self._resolving.pop()
        return rtype

    def _is_this_return(self, func: zast.Function) -> bool:
        """Check if a function's return type resolves to 'this' (with or
        without an ownership suffix like `.borrow` / `.lock`)."""
        rt = func.returntype
        if rt is None:
            return False
        stripped_rt, _ = _strip_path_ownership(rt)
        if stripped_rt.nodetype == NodeType.ATOMID:
            return cast(zast.AtomId, stripped_rt).name == "this"
        return False

    @staticmethod
    def _func_return_ownership(func: zast.Function) -> Optional[ZParamOwnership]:
        """Pull the ownership suffix off a function's return type path."""
        if func.returntype is None:
            return None
        _, own = _strip_path_ownership(func.returntype)
        return own

    def _reject_record_lock_features(
        self, name: str, rec: zast.ObjectDef, rtype: ZType
    ) -> None:
        """Reject .lock fields and this.borrow constructors on records.

        Records are valtypes (copyable, no identity). .lock fields and
        this.borrow constructors require single-owner semantics — use a
        class instead.
        """
        if rtype.has_lock_fields:
            self._error(
                f"Record '{name}' has '.lock' field(s) "
                f"({', '.join(sorted(rtype.lock_field_names))}); '.lock' "
                f"fields are only permitted on classes.",
                loc=rec.start,
                err=ERR.TYPEERROR,
                hint=("change the record to a class to use locked references"),
            )

        offending: list[str] = []
        for mname, mfunc in rec.functions().items():
            if (
                self._is_this_return(mfunc)
                and self._func_return_ownership(mfunc) == ZParamOwnership.BORROW
            ):
                offending.append(mname)
        for mname, mfunc in rec.as_functions().items():
            if (
                self._is_this_return(mfunc)
                and self._func_return_ownership(mfunc) == ZParamOwnership.BORROW
            ):
                offending.append(mname)

        if offending:
            self._error(
                f"Record '{name}' constructor(s) "
                f"({', '.join(offending)}) return 'this.borrow'; "
                f"records cannot return 'this.borrow' — use a class with "
                f".lock fields instead.",
                loc=rec.start,
                err=ERR.TYPEERROR,
                hint=(
                    "change the record to a class — classes with .lock fields "
                    "provide the same locked-reference semantics"
                ),
            )

    def _reftype_reason(self, ftype: ZType) -> Optional[str]:
        """If `ftype` would cause a valtype aggregate to transitively
        hold a reftype, return a short human-readable phrase explaining
        why. Otherwise return None.

        The language invariant: valtypes (record / variant / facet) are
        self-contained and cannot hold reftypes either directly or
        indirectly. Reftypes include string, owning classes, unions,
        protocols, and generic collection templates (list / map / box /
        option / result / ...), plus views (stringview / byteview /
        listview) which cannot escape to aggregate storage because they
        lock their source and v2 does not propagate lock state through
        aggregate fields (doc/strings.pdoc).
        """
        if ftype.subtype == ZSubType.STRING:
            return "`String` is a reftype"
        if ftype.subtype == ZSubType.STRINGVIEW:
            return (
                "`StringView` is a view — locks its source; cannot escape "
                "to aggregate storage"
            )
        if ftype.typetype == ZTypeType.CLASS:
            # View-class typedefs (byteview / listview) have non-STRING
            # subtype — reject them the same way as stringview. Generic
            # listview monomorphizations also land here.
            if ftype.name in ("ByteView", "ListView") or _is_listview_type(ftype):
                return (
                    f"`{ftype.name}` is a view — locks its source; cannot "
                    "escape to aggregate storage"
                )
            return f"`{ftype.name}` is a class (reftype)"
        if ftype.typetype == ZTypeType.UNION:
            return f"`{ftype.name}` is a union (reftype)"
        if ftype.typetype == ZTypeType.PROTOCOL:
            return f"`{ftype.name}` is a protocol (reftype)"
        # RECORD / VARIANT / FACET are valtypes (enforced transitively
        # by this same check running on each type). Numerics / bool /
        # null / enum / array / str monos are native valtypes. Generic
        # applications retain the template's typetype (so `myrec t: u`
        # has typetype RECORD — allowed; `list of: u` has typetype
        # CLASS — handled above).
        return None

    def _reject_valtype_reftype_fields(
        self,
        name: str,
        ztype: ZType,
        is_field_names: "set[str]",
        kind: str,
        start: Token,
    ) -> None:
        """Reject reftype IS-section fields on valtype aggregates
        (record / variant / facet). AS-section slots (protocol
        conformance projections, constants) are not part of the
        struct's owned storage and are excluded.

        `is_field_names` is the set of child keys that correspond to
        data fields (not function methods, not as-items). For records
        this is `rec.is_paths().keys()`; for variants,
        `variant_defn.is_paths().keys()`.
        """
        if ztype.is_native:
            return  # native system records (bool, i64, ...) opt out
        for fname in is_field_names:
            ftype = self.typing.child_of(ztype, fname)
            if ftype is None:
                continue
            if ftype.typetype == ZTypeType.FUNCTION:
                continue
            reason = self._reftype_reason(ftype)
            if reason:
                self._error(
                    f"valtype {kind} '{name}' cannot hold a reftype field "
                    f"'{fname}': {reason}",
                    loc=start,
                    err=ERR.TYPEERROR,
                    hint=(
                        f"change '{name}' to a class, or use '(str to: N)' / "
                        "'(array of: T to: N)' for a bounded-length valtype buffer"
                    ),
                )

    _FLOAT_TYPES = frozenset({"f32", "f64", "f128"})

    def _synthesize_eq(self, ztype: ZType) -> None:
        """Auto-generate == and != for a valtype if all fields support ==.

        Skips synthesis if == is already defined (user override) or null-hidden.
        For records: checks all is-section fields support ==.
        For variants: checks all non-null subtypes support ==.

        Sets is_simple_eq on the synthesized == type when equality is fully
        determined by byte representation (no floats, no user overrides
        recursively). The emitter decides whether to use memcmp or
        field-by-field based on estimated type size.
        """
        if self.typing.has_child(ztype, "=="):
            return  # user-defined or null-hidden

        # check all fields/subtypes support == and track memcmp eligibility
        simple_eq = True
        for fname, ftype in self.typing.children_of(ztype):
            if ftype.typetype == ZTypeType.FUNCTION:
                continue  # function pointers compared by address in C
            if ftype.typetype == ZTypeType.TAG:
                continue  # tag discriminator
            if ftype.typetype == ZTypeType.ENUM:
                continue  # tag enum
            if ftype.typetype == ZTypeType.DATA:
                continue  # data/tag data
            if ftype.typetype == ZTypeType.NULL:
                continue  # null subtypes in variants (compared by tag)
            if ftype.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                continue  # protocol/facet conformance entries are converter
                # methods, not value fields — don't participate in equality
            if ftype.typetype == ZTypeType.GENERIC_PARAM:
                return  # cannot verify, skip synthesis
            if ftype.is_tag_generic_origin:
                continue  # tag access helper
            # float fields disqualify memcmp (NaN != NaN, -0.0 == +0.0)
            if ftype.name in self._FLOAT_TYPES:
                simple_eq = False
            # field must have == (native, user-defined, or will be auto-generated)
            if not self.typing.has_child(ftype, "=="):
                # accept records/variants that will get == synthesized
                if ftype.typetype in (ZTypeType.RECORD, ZTypeType.VARIANT):
                    simple_eq = False  # can't verify nested yet
                    continue
                return  # field lacks ==, skip synthesis
            else:
                # nested type has ==; check if it's memcmp-safe
                nested_eq = cast(ZType, self.typing.child_of(ftype, "=="))
                if nested_eq.is_autogen_eq:
                    # auto-generated: safe only if the nested type is also memcmp-safe
                    if not nested_eq.is_simple_eq:
                        simple_eq = False
                elif not nested_eq.is_autogen_eq:
                    # native == on primitives: safe for non-float types
                    # (floats already disqualified above)
                    # user-defined == on non-primitives: not safe
                    if ftype.name not in NUMERIC_RANGES and ftype.name != "bool":
                        simple_eq = False

        t_bool = self._resolve_name("bool")
        if not t_bool:
            return  # bool not resolved yet (shouldn't happen)

        eq_type = _make_type(f"{ztype.name}.==", ZTypeType.FUNCTION)
        eq_type.return_type = t_bool
        self._set_child(eq_type, "rhs", ztype)
        eq_type.is_autogen_eq = True
        eq_type.is_simple_eq = simple_eq
        self._set_child(ztype, "==", eq_type)

        neq_type = _make_type(f"{ztype.name}.!=", ZTypeType.FUNCTION)
        neq_type.return_type = t_bool
        self._set_child(neq_type, "rhs", ztype)
        neq_type.is_autogen_eq = True
        neq_type.is_simple_eq = simple_eq
        self._set_child(ztype, "!=", neq_type)

    def _detect_typedef(self, items: dict, start: Token) -> tuple:
        """Check if items contain a single .typedef field. Returns (base_type, field_name) or (None, None)."""
        typedef_base = None
        typedef_field = None
        for fname, fpath in items.items():
            ft = self._resolve_typeref(fpath)
            if (
                ft
                and ft.typetype == ZTypeType.GENERIC_PARAM
                and ft.name == "__typedef_marker"
            ):
                typedef_base = ft.parent
                typedef_field = fname
        if typedef_base is not None and len(items) > 1:
            self._error("Additional fields on typedef objects are forbidden", loc=start)
            return (None, None)
        return (typedef_base, typedef_field)

    def _finalize_typedef(
        self,
        unitname: str,
        name: str,
        rtype: ZType,
        base_type: ZType,
        field_name: str,
        as_items: dict,
        as_functions: dict,
        is_functions: dict,
        start: Token,
        generic_ctx: dict,
    ) -> ZType:
        """Build a typedef ZType wrapping base_type."""
        rtype.typedef_base = base_type
        rtype.is_valtype = base_type.is_valtype
        # Destructor + heap-allocation state must follow the base so
        # scope cleanup calls the emitted destroy function (the typedef
        # wrapper itself emits no struct / no destructor).
        if base_type.needs_destructor:
            rtype.needs_destructor = True
            rtype.destructor_name = base_type.destructor_name
        else:
            rtype.needs_destructor = False
            rtype.destructor_name = None
        rtype.is_heap_allocated = base_type.is_heap_allocated

        # No function pointer fields allowed in typedef is-section
        if is_functions:
            self._error("Additional fields on typedef objects are forbidden", loc=start)

        # Process as_functions: new/shadowed methods
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        for mname, mfunc in as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            self._set_child(rtype, mname, mt)

        # Process as_items: null hiding, protocol satisfaction, generic params
        for label, apath in as_items.items():
            at = self._resolve_typeref(apath)
            if (
                at
                and at.typetype == ZTypeType.GENERIC_PARAM
                and at.name == "__generic_param"
            ):
                continue  # generic params already handled in pass 1
            if at and at.typetype == ZTypeType.NULL:
                null_type = _make_type("null", ZTypeType.NULL)
                # null_type entry marks the method as hidden
                self._set_child(rtype, label, null_type)
                continue
            # protocol/facet satisfaction
            if at and at.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                self._process_as_items_protocols(name, rtype, {label: apath}, start)
        if generic_ctx:
            self.mono.generic_context.pop()

        # Synthesize constructors: create and borrow. Bare-name `typedef obj`
        # routes through children["create"] via the unified call dispatch.
        if not rtype.isgeneric:
            create_type = _make_type(f"{name}.create", ZTypeType.FUNCTION)
            create_type.return_type = rtype
            self._set_child(create_type, "from", base_type)
            create_type.param_ownership["from"] = ZParamOwnership.TAKE
            self._set_child(rtype, "create", create_type)
            rtype.meta_create = create_type

            borrow_type = _make_type(f"{name}.borrow", ZTypeType.FUNCTION)
            borrow_type.return_type = rtype
            self._set_child(borrow_type, "from", base_type)
            borrow_type.param_ownership["from"] = ZParamOwnership.LOCK
            self._set_child(rtype, "borrow", borrow_type)

        # typecheck method bodies (non-generic only)
        if not rtype.isgeneric:
            for mname, mfunc in as_functions.items():
                if mfunc.body:
                    self._check_function_body(f"{name}.{mname}", mfunc)

        self._resolving.pop()
        return rtype

    def _process_as_items_protocols(
        self, name: str, rtype: ZType, as_items: dict, start: Token
    ) -> None:
        """Process as_items for protocol satisfaction, constants, and other static items."""
        for label, apath in as_items.items():
            # constant value: numeric literal in 'as' section
            if apath.nodetype in (
                NodeType.ATOMID,
                NodeType.LABELVALUE,
            ) and _is_numeric_id(cast(zast.AtomId, apath).name):
                apath_atom = cast(zast.AtomId, apath)
                at = self._resolve_numeric(apath_atom.name, loc=apath_atom.start)
                if at:
                    _, value, err = parse_number(apath_atom.name)
                    if not err and type(value) in (int, float):
                        self.typing.node_const_value[apath_atom.nodeid] = value
                        # create a type that inherits from the canonical numeric type
                        # so operators work, but carries const_value for the emitter
                        ct = _make_type(at.name, at.typetype)
                        self._copy_children(ct, at)  # mirror operator methods
                        ct.const_value = value
                        ct.is_valtype = True
                        self.typing.node_type[apath.nodeid] = ct
                        self._set_child(rtype, label, ct)
                    else:
                        self.typing.node_type[apath.nodeid] = at
                        self._set_child(rtype, label, at)
                    # As-items don't go through `_check_atomid`, so build
                    # the typed mirror inline; emitter / SQL-dump consumers
                    # read const_value via the typed tree only after
                    # Step 6.9.a.
                continue

            # string constant in 'as' section (pure literal, no interpolation)
            if apath.nodetype == NodeType.ATOMSTRING:
                apath_str = cast(zast.AtomString, apath)
                # only allow pure literals (no interpolated expressions)
                has_interpolation = any(
                    p.nodetype == NodeType.EXPRESSION for p in apath_str.stringparts
                )
                if has_interpolation:
                    self._error(
                        "String constants in 'as' must be pure literals"
                        " (no interpolation)",
                        loc=start,
                    )
                else:
                    sv_type = self._resolve_name("StringView")
                    if sv_type:
                        # collect the raw string content from token parts
                        raw = "".join(
                            cast(zast.StringChunk, p).text
                            for p in apath_str.stringparts
                            if p.nodetype == NodeType.STRINGCHUNK
                        )
                        ct = _make_type(sv_type.name, sv_type.typetype)
                        self._copy_children(ct, sv_type)
                        ct.subtype = sv_type.subtype
                        ct.const_value = raw
                        ct.is_valtype = True
                        ct.needs_destructor = False  # static, not freed
                        self.typing.node_type[apath_str.nodeid] = ct
                        self.typing.node_const_value[apath_str.nodeid] = raw
                        self._set_child(rtype, label, ct)
                        # As-items don't go through `_check_path`, so
                        # build the typed mirror inline (see numeric
                        # branch above).
                continue

            # computed constant expression (e.g., max: 2 * 1024)
            if apath.nodetype == NodeType.BINOP:
                t = self._check_binop(cast(zast.BinOp, apath))
                apath_cv = self.typing.node_const_value.get(apath.nodeid)
                if t and apath_cv is not None:
                    ct = _make_type(t.name, t.typetype)
                    self._copy_children(ct, t)
                    ct.const_value = apath_cv
                    ct.is_valtype = True
                    self._set_child(rtype, label, ct)
                continue

            at = self._resolve_typeref(apath)
            if (
                at
                and at.typetype == ZTypeType.GENERIC_PARAM
                and at.name == "__generic_param"
            ):
                continue  # generic params handled in pass 1
            # 'label: null' in 'as' block — disables a compiler-generated
            # method (or declares the label as intentionally unavailable).
            # Used for 'create: null' to suppress the default constructor.
            if at and at.typetype == ZTypeType.NULL:
                if label == "create":
                    rtype.create_disabled = True
                else:
                    self._set_child(rtype, label, self.t_null)
                continue
            if at and at.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                # facet: only valtypes can implement facets
                if at.typetype == ZTypeType.FACET and not _is_valtype(rtype):
                    self._error(
                        f"Only value types can implement facet '{at.name}', "
                        f"but '{name}' is a reference type",
                        loc=start,
                    )
                # conformance check: implementor must have all spec methods
                for spec_name, spec_func in self.typing.children_of(at):
                    if spec_name in (
                        "create",
                        "take",
                        "borrow",
                    ):
                        continue
                    method = self.typing.child_of(rtype, spec_name)
                    if not method:
                        self._error(
                            f"'{name}' satisfies '{at.name}' but missing method '{spec_name}'",
                            loc=start,
                        )
                    elif (
                        method.typetype == ZTypeType.FUNCTION
                        and spec_func.typetype == ZTypeType.FUNCTION
                    ):
                        self._check_protocol_signature(
                            name, spec_name, spec_func, method, at.name, start
                        )
                # register: label becomes a child of type (PROTOCOL or FACET)
                self._set_child(rtype, label, at)
                self._protocol_labels.setdefault(name, []).append((label, at))
            else:
                # non-protocol as_item (existing behavior: tag refs, etc.)
                if at:
                    self._set_child(rtype, label, at)
                    # propagate const_value from referenced definition
                    apath_cv = self.typing.node_const_value.get(apath.nodeid)
                    if at.const_value is not None and apath_cv is None:
                        self.typing.node_const_value[apath.nodeid] = at.const_value
                    elif (
                        apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                        and apath_cv is None
                    ):
                        defn = self._lookup_definition(cast(zast.AtomId, apath).name)
                        if defn is not None:
                            defn_cv = self.typing.node_const_value.get(defn.nodeid)
                            if defn_cv is not None:
                                self.typing.node_const_value[apath.nodeid] = defn_cv
                    # As-items don't go through `_check_atomid` /
                    # `_check_dotted_path`; build the typed mirror inline
                    # F5.E.4.d: typed-mirror registration removed; the
                    # data the emitter/sqldump need is already on
                    # `self.typing.node_type` and friends.

    def _check_protocol_signature(
        self,
        impl_name: str,
        spec_name: str,
        spec_func: ZType,
        impl_func: ZType,
        proto_name: str,
        loc: Token,
    ) -> None:
        """Check that impl method signature matches protocol spec signature."""
        # extract non-receiver params
        # "this" is the receiver in both spec and impl; skip it
        spec_params = [
            (k, v) for k, v in self.typing.children_of(spec_func) if k != "this"
        ]
        impl_params = [
            (k, v)
            for k, v in self.typing.children_of(impl_func)
            if k != "this" and v.name != impl_name
        ]

        if len(spec_params) != len(impl_params):
            self._error(
                f"'{impl_name}.{spec_name}' has {len(impl_params)} param(s) "
                f"but protocol '{proto_name}' expects {len(spec_params)}",
                loc=loc,
            )
            return

        for (sp_name, sp_type), (im_name, im_type) in zip(spec_params, impl_params):
            if sp_name != im_name:
                self._error(
                    f"'{impl_name}.{spec_name}' param '{im_name}' "
                    f"does not match protocol '{proto_name}' expected '{sp_name}'",
                    loc=loc,
                )
            elif sp_type.name != im_type.name:
                self._error(
                    f"'{impl_name}.{spec_name}' param '{sp_name}' has type '{im_type.name}' "
                    f"but protocol '{proto_name}' expects '{sp_type.name}'",
                    loc=loc,
                )

        spec_ret = spec_func.return_type
        impl_ret = impl_func.return_type
        if spec_ret and impl_ret:
            if spec_ret.name != impl_ret.name:
                self._error(
                    f"'{impl_name}.{spec_name}' returns '{impl_ret.name}' "
                    f"but protocol '{proto_name}' expects '{spec_ret.name}'",
                    loc=loc,
                )
        elif spec_ret and not impl_ret:
            self._error(
                f"'{impl_name}.{spec_name}' has no return type "
                f"but protocol '{proto_name}' expects '{spec_ret.name}'",
                loc=loc,
            )
        elif not spec_ret and impl_ret:
            self._error(
                f"'{impl_name}.{spec_name}' returns '{impl_ret.name}' "
                f"but protocol '{proto_name}' expects no return",
                loc=loc,
            )

    def _resolve_protocol_type(
        self, unitname: str, name: str, proto: zast.ObjectDef
    ) -> ZType:
        key = f"{unitname}.{name}"
        ptype = _make_type(name, ZTypeType.PROTOCOL)
        self._resolved[key] = ptype
        self._resolving.append((key, ptype))
        ptype.is_valtype = False  # protocol instances are reference types
        _set_destructor_metadata(ptype)
        self._assign_cname_type(ptype)

        # pass 1: detect generic params from protocol parameters
        generic_ctx: dict[str, ZType] = {}
        for pname, ppath in proto.is_paths().items():
            pt = self._resolve_typeref(ppath)
            if (
                pt
                and pt.typetype == ZTypeType.GENERIC_PARAM
                and pt.name == "__generic_param"
            ):
                constraint = pt.parent if pt.parent else self.t_null
                ptype.generic_params[pname] = constraint
                ptype.isgeneric = True
                generic_ctx[pname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    ptype.numeric_generic_params.add(pname)

        # pass 2: resolve specs with generic context
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        for sname, sfunc in proto.functions().items():
            st = self._resolve_function_type(unitname, f"{name}.{sname}", sfunc)
            self._set_child(ptype, sname, st)
        if generic_ctx:
            self.mono.generic_context.pop()

        # owned create: protocol.create from: expr (bare-name `proto obj`
        # routes through children["create"] via the unified call dispatch)
        if not ptype.isgeneric:
            create_type = _make_type(f"{name}.create", ZTypeType.FUNCTION)
            create_type.return_type = ptype
            # from: parameter — placeholder type (conformance checked in _check_call)
            self._set_child(create_type, "from", self.t_null)
            create_type.param_ownership["from"] = ZParamOwnership.TAKE
            self._set_child(ptype, "create", create_type)

            # borrow: borrowed protocol creation
            borrow_type = _make_type(f"{name}.borrow", ZTypeType.FUNCTION)
            borrow_type.return_type = ptype
            self._set_child(borrow_type, "from", self.t_null)
            borrow_type.param_ownership["from"] = ZParamOwnership.LOCK
            self._set_child(ptype, "borrow", borrow_type)

        _set_field_cleanup_metadata(self.typing, ptype)
        self._resolving.pop()
        return ptype

    def _resolve_facet_type(
        self, unitname: str, name: str, facet: zast.ObjectDef
    ) -> ZType:
        key = f"{unitname}.{name}"
        ftype = _make_type(name, ZTypeType.FACET)
        self._resolved[key] = ftype
        self._resolving.append((key, ftype))
        ftype.is_valtype = True  # facet instances are value types
        _set_destructor_metadata(ftype)
        self._assign_cname_type(ftype)

        # pass 1: detect generic params from facet parameters
        generic_ctx: dict[str, ZType] = {}
        for pname, ppath in facet.is_paths().items():
            pt = self._resolve_typeref(ppath)
            if (
                pt
                and pt.typetype == ZTypeType.GENERIC_PARAM
                and pt.name == "__generic_param"
            ):
                constraint = pt.parent if pt.parent else self.t_null
                ftype.generic_params[pname] = constraint
                ftype.isgeneric = True
                generic_ctx[pname] = constraint
                if constraint.name in NUMERIC_RANGES:
                    ftype.numeric_generic_params.add(pname)

        # pass 2: resolve specs with generic context
        if generic_ctx:
            self.mono.generic_context.append(generic_ctx)
        for sname, sfunc in facet.functions().items():
            st = self._resolve_function_type(unitname, f"{name}.{sname}", sfunc)
            self._set_child(ftype, sname, st)
        if generic_ctx:
            self.mono.generic_context.pop()

        # create: owned facet creation (copies value). Facets are value-type
        # existentials — the source is read and copied into inline storage,
        # the source remains valid afterward. So from: is a COPY, not a
        # TAKE. Bare-name `facet obj` routes through children["create"] via
        # the unified call dispatch.
        if not ftype.isgeneric:
            create_type = _make_type(f"{name}.create", ZTypeType.FUNCTION)
            create_type.return_type = ftype
            self._set_child(create_type, "from", self.t_null)
            # not TAKE: facet.create copies, does not consume
            self._set_child(ftype, "create", create_type)

            # borrow: borrowed facet creation (copies value, locks source)
            borrow_type = _make_type(f"{name}.borrow", ZTypeType.FUNCTION)
            borrow_type.return_type = ftype
            self._set_child(borrow_type, "from", self.t_null)
            borrow_type.param_ownership["from"] = ZParamOwnership.LOCK
            self._set_child(ftype, "borrow", borrow_type)

        _set_field_cleanup_metadata(self.typing, ftype)
        # Facets have specs (functions), not data fields — the reftype
        # check is a no-op but run it for parity.
        self._reject_valtype_reftype_fields(name, ftype, set(), "facet", facet.start)
        self._resolving.pop()
        return ftype

    def _carry_native_method_metadata(
        self, template_type: ZType, defn: object, meth_name: str, synth: ZType
    ) -> None:
        """Copy method-level metadata (return_ownership, is_native) from a
        natively-declared method's AST to a synthesised mono method. Reads
        from the AST because generic templates don't have their methods
        resolved into ZType.children until monomorphisation (see comment
        near `for mname, mfunc in cls.functions().items()` in class
        resolution).

        `is_native` is also propagated uniformly to every FUNCTION child
        of a native parent at the tail of `_monomorphize`; copying it
        here keeps the helper complete in case it's ever called for a
        synthesis context where the parent's native flag is unset.

        `defn` may be a re-export DottedPath (e.g. core.z's
        `list: collections.list` shadows the real class in some lookup
        orders); fall back to scanning unit bodies for a real
        TypeDefinition node carrying `functions`.
        """
        _OBJECT_DEF_KINDS = {
            NodeType.RECORD,
            NodeType.CLASS,
            NodeType.UNION,
            NodeType.VARIANT,
            NodeType.ENUM,
            NodeType.PROTOCOL,
            NodeType.FACET,
        }
        ast_func = None
        defn_nt = getattr(defn, "nodetype", None)
        if defn_nt in _OBJECT_DEF_KINDS:
            functions = cast(zast.ObjectDef, defn).functions()
            if meth_name in functions:
                ast_func = functions[meth_name]
        if ast_func is None:
            for _unitname, unit in self.program.units.items():
                candidate = unit.body.get(template_type.name)
                cand_nt = getattr(candidate, "nodetype", None)
                if cand_nt in _OBJECT_DEF_KINDS:
                    cand_funcs = cast(zast.ObjectDef, candidate).functions()
                    if meth_name in cand_funcs:
                        ast_func = cand_funcs[meth_name]
                        break
        if ast_func is None:
            return
        ast_ret_own = self._func_return_ownership(ast_func)
        if ast_ret_own is not None:
            synth.return_ownership = ast_ret_own
        if ast_func.is_native:
            synth.is_native = True

    def _make_meta_create_type(
        self,
        name: str,
        parent_type: ZType,
        is_func_names: Optional[set] = None,
        field_names: Optional[set] = None,
    ) -> ZType:
        """Build a FUNCTION ZType for the compiler-generated meta.create constructor.

        is_func_names: set of function names from the 'is' section that should
        be included as constructor parameters (function pointer fields).
        field_names: set of actual instance field names (from 'is' section).
        When provided, only these names are included as constructor parameters.
        """
        ftype = _make_type(f"{name}.create", ZTypeType.FUNCTION)
        ftype.return_type = parent_type
        for fname, ft in self.typing.children_of(parent_type):
            # skip non-field children (as constants, protocol satisfaction, etc.)
            if field_names is not None and fname not in field_names:
                continue
            # skip tag fields — managed by the compiler, not user-provided
            if ft.name == "tag" and fname == "tag":
                continue
            if ft.typetype == ZTypeType.FUNCTION:
                # only include function-typed children from the 'is' section
                if is_func_names and fname in is_func_names:
                    self._set_child(ftype, fname, ft)
                    if fname in parent_type.param_defaults:
                        ftype.param_defaults[fname] = parent_type.param_defaults[fname]
                continue
            self._set_child(ftype, fname, ft)
            # propagate field defaults to constructor
            if fname in parent_type.param_defaults:
                ftype.param_defaults[fname] = parent_type.param_defaults[fname]
            # reftype fields need .take ownership
            if not _is_valtype(ft):
                ftype.param_ownership[fname] = ZParamOwnership.TAKE
        return ftype

    def _resolve_inline_unit_type(
        self, unitname: str, name: str, unit: zast.Unit
    ) -> ZType:
        """Resolve an inline unit definition, recursively processing its body."""
        key = f"{unitname}.{name}"
        utype = _make_type(name, ZTypeType.UNIT)
        self._resolved[key] = utype
        self._register_unit_type(name, unit, utype)

        # detect generic params in unit body (DottedPath items like t: any.generic)
        generic_ctx: dict[str, ZType] = {}
        generic_param_names: set[str] = set()
        for dname, ddefn in unit.body.items():
            if ddefn.nodetype == NodeType.DOTTEDPATH:
                ft = self._resolve_typeref(cast(zast.DottedPath, ddefn))
                if (
                    ft
                    and ft.typetype == ZTypeType.GENERIC_PARAM
                    and ft.name == "__generic_param"
                ):
                    constraint = ft.parent if ft.parent else self.t_null
                    utype.generic_params[dname] = constraint
                    utype.isgeneric = True
                    generic_ctx[dname] = constraint
                    generic_param_names.add(dname)
                    if constraint.name in NUMERIC_RANGES:
                        utype.numeric_generic_params.add(dname)

        # push this unit onto the context stack for name resolution
        self._unit_context.append((name, unit))

        # if generic, push generic context so body definitions can reference params
        if utype.isgeneric:
            self.mono.generic_context.append(generic_ctx)

        # resolve each non-generic-param definition in the inline unit's body
        for dname, ddefn in unit.body.items():
            if dname in generic_param_names:
                continue  # skip generic param declarations
            dkey = f"{unitname}.{name}.{dname}"
            if dkey in self._resolved:
                self._set_child(utype, dname, self._resolved[dkey])
                continue
            t = self._type_of_definition(unitname, f"{name}.{dname}", ddefn)
            if t:
                self._resolved[dkey] = t
                self._set_child(utype, dname, t)
            # check function bodies inside inline units (skip for generic units —
            # bodies will be checked after monomorphization)
            if (
                not utype.isgeneric
                and ddefn.nodetype == NodeType.FUNCTION
                and cast(zast.Function, ddefn).body
            ):
                self._check_function_body(f"{name}.{dname}", cast(zast.Function, ddefn))

        if utype.isgeneric:
            self.mono.generic_context.pop()

        self._unit_context.pop()
        return utype

    # ---- Name resolution (local -> unit body -> core -> system) ----

    def _register_unit_type(
        self,
        unitname: str,
        unit_ast: "Optional[zast.Unit]",
        t: ZType,
    ) -> None:
        """Phase 7d: record a unit's ZType in both name- and id-keyed caches.

        `unit_ast` may be None when the caller only has a name (e.g. a
        monomorphization-registration loop re-registering a synthetic
        unit). Callers that hold the AST node SHOULD pass it so the
        id-keyed cache stays populated.
        """
        self.unit_types[unitname] = t
        if unit_ast is not None:
            self.unit_types_by_id[unit_ast.nodeid] = t

    def _current_unit_name(self) -> str:
        """Return the unit name we're currently resolving inside."""
        if self._resolving:
            return self._resolving[-1][0].split(".")[0]
        return self.program.mainunitname

    def _resolve_name(self, name: str, skip_unit_def=None) -> Optional[ZType]:
        """Resolve a name: local scope, current unit, core.

        Resolution order:
        1. Local scope (symtab — runtime variables)
        2. Inline unit context stack
        3. Current unit (the unit we're resolving inside)
        4. Core (which re-exports system types)

        skip_unit_def: optional (unitname, defname) tuple. When set, skip that
        specific definition during unit body lookup (label_value :x semantics).
        """
        # 1. local scope (symtab)
        t = self.symtab.lookup(name)
        if t:
            return t

        # 2. inline unit context stack (innermost first)
        for uname, unode in reversed(self._unit_context):
            if name in unode.body:
                # resolve this definition from the inline unit
                qname = f"{self.program.mainunitname}.{uname}.{name}"
                if qname in self._resolved:
                    return self._resolved[qname]
                t = self._type_of_definition(
                    self.program.mainunitname, f"{uname}.{name}", unode.body[name]
                )
                if t:
                    self._resolved[qname] = t
                    ut = self.unit_types.get(uname)
                    if ut:
                        self._set_child(ut, name, t)
                    return t

        # 3. current unit (the unit we're resolving inside)
        current = self._current_unit_name()
        cunit = self.program.units.get(current)
        if cunit and name in cunit.body:
            if skip_unit_def == (current, name):
                pass  # label_value: skip self-binding
            else:
                t = self._resolve_unit_name(current, name)
                if t:
                    return t

        # 4. core unit (re-exports system types)
        core = self.program.units.get("core")
        if core and name in core.body:
            t = self._resolve_unit_name("core", name)
            if t:
                return t

        # 5. file unit names (for generic unit instantiation)
        if name in self.program.units and name != current:
            return self._ensure_file_unit_resolved(name)

        return None

    def _resolve_typeref(self, path: zast.Path) -> Optional[ZType]:
        """Resolve a type reference (used in parameter types, return types, fields)."""
        # check generic context first for simple names
        if (
            path.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
            and self.mono.generic_context
        ):
            path_atom = cast(zast.AtomId, path)
            for ctx in reversed(self.mono.generic_context):
                if path_atom.name in ctx:
                    gp_ref = _make_type(path_atom.name, ZTypeType.GENERIC_PARAM)
                    gp_ref.parent = ctx[path_atom.name]  # constraint
                    self.typing.node_type[path.nodeid] = gp_ref
                    return gp_ref
        if path.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
            path_atom2 = cast(zast.AtomId, path)
            name = path_atom2.name
            if _is_numeric_id(name):
                t = self._resolve_numeric(name, loc=path_atom2.start)
                if t:
                    self.typing.node_type[path.nodeid] = t
                return t
            if name == "type":
                t = self._resolve_type_keyword()
                if t:
                    self.typing.node_type[path.nodeid] = t
                return t
            if name == "this":
                t = self._resolve_this_keyword()
                if t:
                    self.typing.node_type[path.nodeid] = t
                return t
            t = self._resolve_name(name)
            if t and t.isgeneric:
                # allow bare generic 'tag' as field type (monomorphized on use)
                if name == "tag":
                    self.typing.node_type[path.nodeid] = t
                    return t
                self._error(
                    f"generic type '{name}' requires type arguments",
                    loc=path_atom2.start,
                    err=ERR.GENERICERROR,
                    hint=f"specify type parameters, e.g. ({name} t: i64)",
                )
                return None
            if t:
                self.typing.node_type[path.nodeid] = t
            return t
        if path.nodetype == NodeType.DOTTEDPATH:
            t = self._resolve_dotted_path(cast(zast.DottedPath, path))
            if t:
                self.typing.node_type[path.nodeid] = t
            return t
        if path.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, path).expression
            if inner.nodetype == NodeType.CALL:
                t = self._resolve_typeref_call(cast(zast.Call, inner))
                if t:
                    self.typing.node_type[path.nodeid] = t
                return t
        return None

    def _resolve_numeric_generic_arg(
        self, op: zast.Operation, constraint_name: str, loc: Optional[zast.Token] = None
    ) -> Optional[ZType]:
        """Resolve a numeric generic argument from an AST value expression.

        Parses as numeric literal, validates against constraint range,
        returns a ZType with numeric_value set.
        """
        # extract the numeric string from the operation (negative numbers are AtomId("-5"))
        if op.nodetype != NodeType.ATOMID:
            self._error(
                "Numeric generic argument must be a numeric literal",
                loc=loc,
            )
            return None
        numstr = cast(zast.AtomId, op).name

        if not _is_numeric_id(numstr):
            self._error(
                f"Numeric generic argument must be a numeric literal, got '{numstr}'",
                loc=loc,
            )
            return None

        # parse and validate range
        typename, value, err = parse_number(numstr)
        if err:
            self._error(
                f"Invalid numeric generic value '{numstr}': {err}",
                loc=loc,
            )
            return None

        int_value = int(value)
        lo, hi = NUMERIC_RANGES[constraint_name]
        if int_value < lo or int_value > hi:
            self._error(
                f"Numeric generic value {int_value} out of range for "
                f"{constraint_name} ({lo}..{hi})",
                loc=loc,
            )
            return None

        # build name for mangling: negative values use "neg" prefix
        if int_value < 0:
            mangled_name = f"neg{abs(int_value)}"
        else:
            mangled_name = str(int_value)

        zt = _make_type(mangled_name, ZTypeType.RECORD)
        zt.numeric_value = int_value
        zt.is_valtype = True
        return zt

    def _resolve_typeref_call(self, call: zast.Call) -> Optional[ZType]:
        """Resolve a Call in type position: (myrec t: i64) or (myrec t: u)."""
        if call.callable.nodetype == NodeType.ATOMID:
            template = self._resolve_name(cast(zast.AtomId, call.callable).name)
        elif call.callable.nodetype == NodeType.DOTTEDPATH:
            template = self._check_dotted_path(cast(zast.DottedPath, call.callable))
        else:
            return None
        if not template or not template.isgeneric:
            return None

        generic_args: dict[str, ZType] = {}
        has_unresolved = False
        for arg in call.arguments:
            if not arg.name or arg.name not in template.generic_params:
                continue
            # numeric generic param: resolve as numeric value
            if arg.name in template.numeric_generic_params:
                arg_type = self._resolve_numeric_generic_arg(
                    arg.valtype, template.generic_params[arg.name].name, loc=call.start
                )
                if arg_type:
                    generic_args[arg.name] = arg_type
                else:
                    has_unresolved = True
                continue
            # resolve the type arg — could be a concrete type or a generic param
            if arg.valtype.nodetype not in (
                NodeType.ATOMID,
                NodeType.DOTTEDPATH,
                NodeType.ATOMSTRING,
                NodeType.EXPRESSION,
                NodeType.LABELVALUE,
            ):
                continue
            arg_type = self._resolve_typeref(cast(zast.Path, arg.valtype))
            if arg_type:
                generic_args[arg.name] = arg_type
            else:
                has_unresolved = True

        for param_name in template.generic_params:
            if param_name not in generic_args:
                if has_unresolved:
                    return None  # arg provided but not yet resolvable (pass 1)
                self._error(
                    f"Missing type argument '{param_name}' for "
                    f"generic type '{template.name}'",
                    loc=call.start,
                )
                return None

        defn = self._find_generic_defn(template)
        if not defn:
            return None
        return self._monomorphize(template, generic_args, defn)

    def _resolve_type_keyword(self) -> Optional[ZType]:
        """Resolve `type` to the nearest enclosing concrete type on the resolving stack."""
        for _, rtype in reversed(self._resolving):
            if rtype.typetype in (
                ZTypeType.RECORD,
                ZTypeType.ENUM,
                ZTypeType.UNION,
                ZTypeType.CLASS,
            ):
                return rtype
        return None

    def _resolve_this_keyword(self) -> Optional[ZType]:
        """Resolve `this` to the nearest enclosing record/class type."""
        for _, rtype in reversed(self._resolving):
            if rtype.typetype in (ZTypeType.RECORD, ZTypeType.CLASS):
                return rtype
        return None

    def _resolve_dp_parent_type(
        self, path: zast.DottedPath
    ) -> Tuple[Optional[ZType], bool]:
        """Resolve the parent type of a dotted path. Returns
        `(parent_type, early_handled)`:

        - `(t, False)` — `t` is the parent's resolved type; caller
          continues with child-name dispatch.
        - `(t, True)` — the helper has already returned the dotted
          path's final value via `t` (e.g. `meta.create`, numeric
          cast, unit member lookup). Caller returns `t` immediately.
        - `(None, True)` — error already emitted; caller returns None."""
        # ATOMID / LABELVALUE parent — by far the dominant case
        if path.parent.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
            pname = cast(zast.AtomId, path.parent).name
            # meta.create: compiler-internal raw allocator of the
            # lexically enclosing type. Only resolves inside a type's
            # method body; at top level, falls through to the normal
            # name-resolution path which will error.
            if pname == "meta" and path.child.name == "create":
                if self.func_ctx.enclosing_type:
                    enclosing = self.func_ctx.enclosing_type[-1]
                    raw = enclosing.meta_create
                    if raw is not None:
                        self.typing.node_type[path.nodeid] = raw
                        self.typing.node_type[path.parent.nodeid] = enclosing
                        return raw, True
                self._error(
                    "'meta.create' is only valid inside a type's method body",
                    loc=path.start,
                )
                return None, True
            # numeric dotted path: 0.u32, 42.i8, 0xff.u16. Only treat
            # as a numeric cast when child names a known numeric type;
            # other suffixes (.iterate / .each declared natively on
            # the integer record) fall through to standard child
            # lookup against the inferred numeric type.
            if _is_numeric_id(pname):
                child_name_local = path.child.name
                resolved_child = self._resolve_name(child_name_local)
                if (
                    resolved_child is not None
                    and resolved_child.typetype != ZTypeType.FUNCTION
                ):
                    _, _, err = parse_number(pname + child_name_local)
                    if err:
                        self._error(
                            f"Invalid numeric cast {pname}.{child_name_local}: {err}",
                            loc=path.start,
                        )
                        return None, True
                    return resolved_child, True
            # File-level unit member lookup
            if pname in self.program.units:
                utype = self._ensure_file_unit_resolved(pname)
                if utype and utype.isgeneric:
                    self._error(
                        f"Generic unit '{pname}' must be instantiated"
                        f" with type arguments before use",
                        loc=path.start,
                    )
                    return None, True
                if utype:
                    child = self.typing.child_of(utype, path.child.name)
                    if child:
                        return child, True
                t = self._resolve_unit_name(pname, path.child.name)
                if t:
                    return t, True
                # Phase D: known unit, unknown child — error rather
                # than silent None. Without this, `io.read_only` (or
                # any other typo on a unit-qualified path) would slip
                # through call argument resolution.
                candidates = self.typing.child_names_of(utype) if utype else []
                suggestion = _suggest_similar(path.child.name, candidates)
                self._error(
                    f"unit '{pname}' has no member '{path.child.name}'",
                    loc=path.start,
                    hint=f"did you mean '{suggestion}'?" if suggestion else None,
                )
                return None, True
            # Inline unit member lookup. Phase 7d: prefer id-keyed
            # cache when an inline unit AST handle is reachable via
            # the unit-context stack; fall back to name lookup
            # otherwise.
            inline_unit_type: Optional[ZType] = None
            for _ctx_name, ctx_unit in reversed(self._unit_context):
                inline = ctx_unit.body.get(pname)
                if inline is not None and inline.nodetype == NodeType.UNIT:
                    inline_unit_type = self.unit_types_by_id.get(
                        cast(zast.Unit, inline).nodeid
                    )
                    break
            if inline_unit_type is None and pname in self.unit_types:
                inline_unit_type = self.unit_types[pname]
            if (
                inline_unit_type is not None
                and inline_unit_type.typetype == ZTypeType.UNIT
            ):
                child = self.typing.child_of(inline_unit_type, path.child.name)
                if child:
                    return child, True
                candidates = self.typing.child_names_of(inline_unit_type)
                suggestion = _suggest_similar(path.child.name, candidates)
                self._error(
                    f"unit '{pname}' has no member '{path.child.name}'",
                    loc=path.start,
                    hint=f"did you mean '{suggestion}'?" if suggestion else None,
                )
                return None, True
            # Otherwise resolve parent as a name; for numeric literals
            # (`5.iterate`, `42.each`) resolve via the numeric
            # inference so the standard child lookup finds natives
            # declared on the integer record.
            if _is_numeric_id(pname):
                return self._resolve_numeric(pname, loc=path.parent.start), False
            return self._resolve_name(pname), False
        # DOTTEDPATH parent: recurse
        if path.parent.nodetype == NodeType.DOTTEDPATH:
            return self._resolve_dotted_path(cast(zast.DottedPath, path.parent)), False
        # EXPRESSION parent: take the expression's already-resolved
        # type, falling back to typeref resolution for type-only
        # expressions (`(list of: u8).typedef`).
        if path.parent.nodetype == NodeType.EXPRESSION:
            parent_type = self.typing.node_type.get(path.parent.nodeid)
            if parent_type is None:
                parent_type = self._resolve_typeref(path.parent)
            return parent_type, False
        # ATOMSTRING parent: String / StringView depending on whether
        # the literal has interpolation parts.
        if path.parent.nodetype == NodeType.ATOMSTRING:
            atom_str = cast(zast.AtomString, path.parent)
            has_interp = any(
                p.nodetype != NodeType.STRINGCHUNK for p in atom_str.stringparts
            )
            return (
                self._resolve_name("String" if has_interp else "StringView"),
                False,
            )
        return None, False

    def _resolve_dotted_path(self, path: zast.DottedPath) -> Optional[ZType]:
        parent_type, early_handled = self._resolve_dp_parent_type(path)
        if early_handled:
            return parent_type
        if not parent_type:
            return None
        # check for .typedef — creates a marker detected by type resolvers
        child_name = path.child.name
        if child_name == "typedef":
            marker = _make_type("__typedef_marker", ZTypeType.GENERIC_PARAM)
            marker.parent = parent_type  # the base type being wrapped
            self.typing.node_type[path.nodeid] = marker
            return marker
        # Explicit `Type.create` when create is disabled (either via
        # `create: null` or implicitly for unions/variants). Emit a targeted
        # error rather than falling through to a generic "no such child".
        if child_name == "create" and parent_type.create_disabled:
            if parent_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
                kind = "union" if parent_type.typetype == ZTypeType.UNION else "variant"
                self._error(
                    f"'{parent_type.name}.create' is not available; "
                    f"'{parent_type.name}' is a {kind} and requires a specific "
                    f"subtype. Try '{parent_type.name}.<subtype> value'.",
                    loc=path.start,
                    err=ERR.CALLERROR,
                )
            else:
                self._error(
                    f"'{parent_type.name}.create' is disabled via 'create: null'; "
                    f"use a user-defined constructor explicitly.",
                    loc=path.start,
                    err=ERR.CALLERROR,
                )
            return None
        # `Type.take` on protocol/facet/typedef is no longer a constructor;
        # emit a targeted migration error pointing at `.create` and `.borrow`.
        if child_name == "take" and (
            parent_type.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET)
            or parent_type.typedef_base is not None
        ):
            self._error(
                f"'{parent_type.name}.take' is no longer a constructor. "
                f"Use '{parent_type.name}.create from: ...' (owned) or "
                f"'{parent_type.name}.borrow from: ...' (borrowed).",
                loc=path.start,
                err=ERR.CALLERROR,
            )
            return None
        # check for .generic / .valtype / .reftype — creates a generic type parameter marker
        if child_name in ("generic", "valtype", "reftype"):
            if child_name == "generic":
                constraint = parent_type
            else:
                # any.valtype / any.reftype — create a sentinel constraint
                constraint = _make_type(
                    f"{parent_type.name}.{child_name}", parent_type.typetype
                )
            gp = _make_type("__generic_param", ZTypeType.GENERIC_PARAM)
            gp.parent = constraint
            self.typing.node_type[path.nodeid] = gp
            return gp
        if child_name == "take" and parent_type.typetype not in (
            ZTypeType.PROTOCOL,
            ZTypeType.FACET,
        ):
            # .take returns the same type (ownership transfer)
            self.typing.node_type[path.nodeid] = parent_type
            return parent_type
        if child_name == "borrow" and parent_type.typetype not in (
            ZTypeType.PROTOCOL,
            ZTypeType.FACET,
        ):
            # .borrow returns the same type (borrowed reference)
            self.typing.node_type[path.nodeid] = parent_type
            return parent_type
        if child_name == "lock":
            # .lock is an alias for .borrow (borrowed reference / explicit lock)
            self.typing.node_type[path.nodeid] = parent_type
            return parent_type
        if child_name == "private":
            # .private grants access to all members (friend access)
            self.typing.node_type[path.nodeid] = parent_type
            return parent_type
        # numeric type casting: x.u32 where x is a numeric type
        _NUMERIC_NAMES = set(NUMERIC_RANGES) | {"f32", "f64", "f128"}
        if child_name in _NUMERIC_NAMES and parent_type.name in _NUMERIC_NAMES:
            target_type = self._resolve_name(child_name)
            if target_type:
                self.typing.node_type[path.nodeid] = target_type
                return target_type
        # for unions/variants, store parent type on the path for construction detection
        if parent_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
            # resolve public name (may redirect renamed members)
            resolved_name = self._resolve_public_name(parent_type, child_name, path)
            child = self.typing.child_of(parent_type, resolved_name)
            if not child:
                child = self.typing.child_of(parent_type, child_name)
            if child:
                # public access check
                if self._is_non_public_access(parent_type, child_name, path):
                    self._error(
                        f"'{child_name}' is not public on type '{parent_type.name}'",
                        loc=path.start,
                    )
                    return None
                # Narrowing checks for non-shadow narrowing (assignment-
                # based `x: r.ok 42` — x stays typed as the union with a
                # narrowed_subtype sidecar). Reject wrong-arm access and
                # excluded-arm access.
                if (
                    path.parent.nodetype == NodeType.ATOMID
                    and child_name != "tag"
                    and child.typetype != ZTypeType.FUNCTION
                ):
                    pname = cast(zast.AtomId, path.parent).name
                    if self.symtab.lookup_var(pname) is not None:
                        if self.symtab.is_excluded(pname, child_name):
                            self._error(
                                f"Cannot access '{child_name}' on '{pname}': "
                                f"type has been narrowed to exclude '{child_name}'",
                                loc=path.start,
                            )
                            return None
                        narrowed_subtype_name = self.symtab.get_subtype_name(pname)
                        if (
                            narrowed_subtype_name
                            and child_name != narrowed_subtype_name
                        ):
                            self._error(
                                f"Cannot access '{child_name}' on '{pname}': "
                                f"type has been narrowed to "
                                f"'{narrowed_subtype_name}'",
                                loc=path.start,
                            )
                            return None
                # non-subtype children (tag, :tag, methods) should not be
                # treated as union/variant subtype construction
                if child_name != "tag" and child.typetype != ZTypeType.FUNCTION:
                    self.typing.dp_parent_tagged_type[path.nodeid] = parent_type
                return child
            # child is not an arm of the (narrowed) union/variant. If the
            # parent is a narrowed AtomId and the child is an arm of the
            # shadowed original, emit a targeted error instead of
            # falling through silently to return None.
            self._maybe_report_shadowed_parent_access(path, child_name)
            return None
        # for data: .array method returns a new array of matching type/length
        if parent_type.typetype == ZTypeType.DATA and child_name == "array":
            elem_type = parent_type.element_type
            if elem_type:
                # count data elements (non-special keys)
                data_len = sum(
                    1 for k in self.typing.child_names_of(parent_type) if k != "tag"
                )
                # monomorphize array with matching type and length
                array_template = self._resolve_name("array")
                if array_template and array_template.isgeneric:
                    array_defn = self._find_generic_defn(array_template)
                    if array_defn:
                        len_type = _make_type(str(data_len), ZTypeType.RECORD)
                        len_type.numeric_value = data_len
                        len_type.is_valtype = True
                        mono = self._monomorphize(
                            array_template,
                            {"of": elem_type, "to": len_type},
                            array_defn,
                        )
                        return mono
            return None
        # for arrays: numeric index access (array.0, array.1, etc.)
        if _is_array_type(parent_type) and child_name.isdigit():
            idx = int(child_name)
            arr_len = _array_length(self.typing, parent_type)
            if arr_len is not None and idx >= arr_len:
                self._error(
                    f"Array index {idx} out of bounds for array of length {arr_len}",
                    loc=path.start,
                )
                return None
            elem_type = _array_element_type(self.typing, parent_type)
            return elem_type
        # for str types: .string returns the string type directly (not the function)
        if _is_str_type(parent_type) and child_name == "string":
            return self._resolve_name("String")
        # for stringview types: .string returns the string type directly
        if _is_stringview_type(parent_type) and child_name == "string":
            return self._resolve_name("String")
        # for string class: .string returns the same string type (no-op identity)
        if parent_type.subtype == ZSubType.STRING and child_name == "string":
            return self._resolve_name("String")
        # for string class: .length and .capacity return u64 directly
        if parent_type.subtype == ZSubType.STRING and child_name in (
            "length",
            "capacity",
        ):
            return self._resolve_name("u64")
        # for stringview: .length returns u64 directly
        if _is_stringview_type(parent_type) and child_name == "length":
            return self._resolve_name("u64")
        # .str conversion method on string, str, and stringview types
        # returns a marker function type; actual resolution happens in _check_call
        if child_name == "str" and (
            (parent_type.subtype == ZSubType.STRING)
            or _is_str_type(parent_type)
            or _is_stringview_type(parent_type)
        ):
            marker = _make_type("__str_convert", ZTypeType.FUNCTION)
            marker.is_native = True
            return marker
        # for list types: .pop returns the element type directly (zero-arg method)
        if _is_list_type(parent_type) and child_name == "pop":
            return _list_element_type(self.typing, parent_type)
        # NOTE: the auto-call coercion that previously lived here (returning
        # `method.return_type` and installing `_pending_borrow_lock` for
        # zero-user-arg methods on PROTOCOL / CLASS / str-valtype) has moved
        # to `_check_dotted_path` so the disambiguation has visibility into
        # whether the path is the callable of a Call (callable position) or
        # accessed as a value (value position). Path resolution is now
        # context-free: a dotted path naming a method always returns the
        # FUNCTION type. See `_check_dotted_path` for the value-position
        # auto-call rule, gated on `coerce_method_to_return`.
        # for records/enums, look up child in children
        # resolve public name (may redirect renamed members)
        resolved_name = self._resolve_public_name(parent_type, child_name, path)
        child = self.typing.child_of(parent_type, resolved_name)
        if not child:
            child = self.typing.child_of(parent_type, child_name)
        if child:
            # null-hidden methods on typedefs
            if parent_type.typedef_base and child.typetype == ZTypeType.NULL:
                self._error(
                    f"Method '{child_name}' is not available on type '{parent_type.name}'",
                    loc=path.start,
                )
                return None
            # public access check: restrict external access to public members
            if self._is_non_public_access(parent_type, child_name, path):
                self._error(
                    f"'{child_name}' is not public on type '{parent_type.name}'",
                    loc=path.start,
                )
                return None
            return child
        # Typedef fall-through: walk base chain for unshadowed methods
        base = parent_type.typedef_base
        while base is not None:
            child = self.typing.child_of(base, child_name)
            if child:
                return child
            base = base.typedef_base
        # Targeted errors for failed lookup on a narrowed AtomId parent.
        self._maybe_report_shadowed_parent_access(path, child_name)
        return None

    def _maybe_report_shadowed_parent_access(
        self, path: zast.DottedPath, child_name: str
    ) -> None:
        """Emit a targeted error when a failed field/arm lookup looks
        like reaching back to the shadowed parent union/variant, or an
        unknown field on the narrowed payload. Silent no-op otherwise.
        """
        if path.parent.nodetype != NodeType.ATOMID:
            return
        parent_atom = cast(zast.AtomId, path.parent)
        entry = self.symtab.lookup_entry(parent_atom.name)
        if (
            entry is None
            or entry.narrowed_subtype is None
            or entry.original_ztype is None
        ):
            return
        if self.typing.has_child(entry.original_ztype, child_name):
            self._error(
                f"'{parent_atom.name}' is narrowed to "
                f"'{entry.ztype.name}' in this arm; "
                f"'{parent_atom.name}.{child_name}' would reach "
                f"into the shadowed parent "
                f"'{entry.original_ztype.name}'. Access the "
                f"narrowed value directly, e.g. "
                f"'{parent_atom.name}' on its own.",
                loc=path.start,
            )
        else:
            self._error(
                f"'{parent_atom.name}' is narrowed to "
                f"'{entry.ztype.name}' in this arm; "
                f"'{entry.ztype.name}' has no field "
                f"'{child_name}'.",
                loc=path.start,
            )

    def _resolve_numeric(
        self, name: str, loc: Optional[Token] = None
    ) -> Optional[ZType]:
        typename, _, err = parse_number(name)
        if err:
            self._error(f"Invalid numeric literal: {name}: {err}", loc=loc)
            return None
        return self._resolve_name(typename)

    def _lookup_definition(self, name: str) -> Optional[zast.TypeDefinition]:
        """Look up a unit-level definition by name (inline units then main unit)."""
        # inline unit context stack (innermost first)
        for uname, unode in reversed(self._unit_context):
            defn = unode.body.get(name)
            if defn is not None:
                return defn
        # main unit body
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit:
            defn = mainunit.body.get(name)
            if defn is not None:
                return defn
        return None

    def _types_compatible(self, a: ZType, b: ZType) -> bool:
        """Check if two types are compatible (identity, id match after
        typedef unwrap, or structural equiv for functions).

        Zerolang does not perform implicit conversions between distinct
        types: there are no silent str↔string↔stringview bridges at
        parameter-passing or assignment boundaries. Callers must use the
        explicit zero-cost projections (`.stringview`, `.string`) where
        the receiver expects a specific string type.

        Fast path: identity, then nodeid after typedef unwrap on both
        sides — avoids repeated string compares on a hot path that's
        exercised on every assignment / return-type / arg-match check.
        """
        if a is b:
            return True
        # Fast path: exact id match.
        if a.nodeid == b.nodeid:
            return True
        # Generic parameters (e.g., `t` in `f: function {x: t} out t`) are
        # synthetic placeholders that may be re-created by the resolver;
        # two `t`s in the same template context are the same parameter
        # despite having distinct ZType instances.
        if (
            a.typetype == ZTypeType.GENERIC_PARAM
            and b.typetype == ZTypeType.GENERIC_PARAM
        ):
            return a.name == b.name
        # Primitive types (numerics, bool, null) are conceptually global
        # singletons — the resolver may still hand out separate ZType
        # instances in different contexts, so name is the canonical
        # identity for them until the interning work lands.
        if _is_primitive_name(a.name) and a.name == b.name:
            return True
        if a.typetype == ZTypeType.FUNCTION and b.typetype == ZTypeType.FUNCTION:
            return self._function_types_equivalent(a, b)
        # Typedef backward compat: `a` (actual) may be a typedef wrapping
        # `b` (expected). Walk only `a`'s chain — this is deliberately
        # asymmetric. Passing a `meters` (typedef over i64) where i64 is
        # expected is fine; passing raw i64 where `meters` is expected
        # is not (the typedef carries intent that the base type lacks).
        base = a.typedef_base
        while base is not None:
            if base is b or base.nodeid == b.nodeid:
                return True
            base = base.typedef_base
        return False

    def _find_conformance_label(
        self, impl_type: ZType, proto_type: ZType
    ) -> Optional[str]:
        """If `impl_type` (a class or record) declares conformance to
        `proto_type`, return the conformance label; else None.

        The typechecker records each `as { :proto }` / `as { lbl: proto }`
        entry as a child of the implementor's ZType (child name = label,
        child type = the protocol's ZType). Conformance walks the same
        generic_origin chain as `_type_conforms_to_protocol` so that a
        monomorphized type (e.g. `str_64`) inherits the label from its
        template.
        """
        if impl_type.typetype not in (ZTypeType.CLASS, ZTypeType.RECORD):
            return None
        if proto_type.typetype not in (ZTypeType.PROTOCOL, ZTypeType.FACET):
            return None
        t: Optional[ZType] = impl_type
        seen: set[int] = set()
        while t is not None and id(t) not in seen:
            seen.add(id(t))
            for label, child in self.typing.children_of(t):
                if child is proto_type or child.nodeid == proto_type.nodeid:
                    return label
                if child.typetype in (
                    ZTypeType.PROTOCOL,
                    ZTypeType.FACET,
                ) and self._types_compatible(child, proto_type):
                    return label
            origin = t.generic_origin
            if origin is None or t.is_tag_generic_origin:
                t = None
            else:
                t = origin
        return None

    def _try_protocol_coerce(
        self,
        arg: zast.NamedOperation,
        arg_type: ZType,
        formal_type: ZType,
        ownership: Optional["ZParamOwnership"],
    ) -> bool:
        """Phase: auto-project a concrete arg onto a protocol parameter.

        When the parameter expects a protocol/facet and the concrete arg
        type conforms, stamp `arg` so the emitter synthesises
        `z_<impl>_<label>_create` (borrow or owned, per the declared
        ownership). Returns True if a projection was applied.
        """
        label = self._find_conformance_label(arg_type, formal_type)
        if label is None:
            return False
        # Ownership selection: explicit TAKE or BORROW annotation wins;
        # otherwise reftype parameters default to take semantics (the
        # usual ownership rule).
        if ownership == ZParamOwnership.BORROW or ownership == ZParamOwnership.LOCK:
            kind = "borrow"
        else:
            kind = "take"
        # Step 6: stamps live in the typechecker-side side-table now,
        # not on the parsed `NamedOperation` node. `_build_typed_call`
        # picks them up from this table when constructing the typed
        # mirror.
        self.typing.projected_args[arg.nodeid] = (formal_type, label, kind)
        return True

    def _function_types_equivalent(self, a: ZType, b: ZType) -> bool:
        """Check structural equivalence of two function types (same
        params + return). Recurses through `_types_compatible` so the
        primitive / generic-param fallbacks apply on inner types too.
        """
        a_ret = a.return_type
        b_ret = b.return_type
        if (a_ret is None) != (b_ret is None):
            return False
        if a_ret and b_ret and not self._types_compatible(a_ret, b_ret):
            return False
        a_params = self.typing.children_of(a)
        b_params = self.typing.children_of(b)
        if len(a_params) != len(b_params):
            return False
        for (ak, av), (bk, bv) in zip(a_params, b_params):
            if ak != bk or not self._types_compatible(av, bv):
                return False
        return True

    # ---- Monomorphization ----

    def _monomorphize(
        self,
        template_type: ZType,
        generic_args: dict[str, ZType],
        defn: zast.TypeDefinition,
    ) -> ZType:
        """Monomorphize a generic type with concrete type arguments.

        Returns a cached or newly created concrete type with all generic
        parameters replaced by concrete types.
        """
        # Identity-based cache key via `_mono_arg_key`, which uses the
        # nodeid for structural types and falls back to name / numeric
        # value for primitives + generic params + numeric literals
        # (which aren't interned yet).
        cache_key = (
            template_type.nodeid,
            tuple(sorted((k, _mono_arg_key(v)) for k, v in generic_args.items())),
        )
        if cache_key in self.mono.cache:
            return self.mono.cache[cache_key]

        # check if this is a partial instantiation (some args are GENERIC_PARAM)
        is_partial = any(
            v.typetype == ZTypeType.GENERIC_PARAM for v in generic_args.values()
        )

        self._check_mono_constraints(template_type, generic_args)

        arg_names = [generic_args[k].name for k in template_type.generic_params]
        mangled = f"{template_type.name}_{'_'.join(arg_names)}"

        mono = self._make_mono_shell(template_type, generic_args, mangled, is_partial)
        self._substitute_mono_children(mono, template_type, generic_args, is_partial)
        self._recompute_mono_typetype_marks(mono, template_type)

        if template_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
            self._rebuild_mono_tag(mono, mangled)

        if not is_partial:
            self._synth_collection_methods(mono, mangled, template_type, defn)

        self._setup_mono_meta_create(mono, mangled, template_type, defn)

        if not is_partial and defn.nodetype == NodeType.UNIT:
            self._monomorphize_unit(
                mono, mangled, template_type, generic_args, cast(zast.Unit, defn)
            )

        if not is_partial and defn.nodetype in (
            NodeType.CLASS,
            NodeType.RECORD,
            NodeType.UNION,
            NodeType.VARIANT,
        ):
            self._clone_mono_methods(mono, mangled, generic_args, defn)

        if not is_partial and mono.typetype in (ZTypeType.RECORD, ZTypeType.VARIANT):
            self._synthesize_eq(mono)

        self._register_mono(mono, cache_key, mangled, defn, is_partial)
        self._mark_mono_native(mono)
        return mono

    def _substitute_func_type(
        self,
        name: str,
        func_type: ZType,
        args: dict[str, ZType],
    ) -> ZType:
        """Create a new function type with generic params substituted."""
        new_func = _make_type(name, ZTypeType.FUNCTION)
        for pk, pv in self.typing.children_of(func_type):
            if pv.typetype == ZTypeType.GENERIC_PARAM and pv.name in args:
                self._set_child(new_func, pk, args[pv.name])
            else:
                self._set_child(new_func, pk, pv)
        if func_type.return_type:
            rt = func_type.return_type
            if rt.typetype == ZTypeType.GENERIC_PARAM and rt.name in args:
                new_func.return_type = args[rt.name]
            else:
                new_func.return_type = rt
        new_func.param_ownership = func_type.param_ownership.copy()
        new_func.return_ownership = func_type.return_ownership
        return new_func

    def _monomorphize_unit(
        self,
        mono: ZType,
        mangled: str,
        template_type: ZType,
        generic_args: dict[str, ZType],
        defn: zast.Unit,
    ) -> None:
        """Complete monomorphization of a UNIT type.

        Handles: function child substitution, recursive partial instantiation
        of nested generic subunits, function body cloning and type-checking.
        """
        # 1. substitute generic params in function children
        for child_name, child_type in self.typing.children_of(mono):
            if child_type.typetype == ZTypeType.FUNCTION:
                new_func = self._substitute_func_type(
                    f"{mangled}.{child_name}", child_type, generic_args
                )
                self._set_child(mono, child_name, new_func)
                for unitname_key in self.program.units:
                    self._resolved[f"{unitname_key}.{mangled}.{child_name}"] = new_func
                    break

        # 2. recursively partially instantiate nested generic subunits
        self._partially_instantiate_subunits(mono, mangled, generic_args)

        # 3. register and clone function bodies
        self._register_unit_type(mangled, None, mono)
        cloned_methods: dict[str, zast.Function] = {}
        all_args: dict[str, ZType] = {}
        for ga_name, ga_type in self.typing.generic_args_of(template_type):
            all_args[ga_name] = ga_type
        all_args.update(generic_args)
        for dname, ddefn in defn.body.items():
            if dname in template_type.generic_params:
                continue
            if ddefn.nodetype == NodeType.FUNCTION and cast(zast.Function, ddefn).body:
                qualified = f"{mangled}.{dname}"
                cloned = clone_function(cast(zast.Function, ddefn))
                self.symtab.push(f"unitgeneric:{mangled}")
                for gp_name, concrete_type in all_args.items():
                    self.symtab.define(gp_name, concrete_type)
                self._check_function_body(qualified, cloned)
                self.symtab.pop()
                func_hash = zasthash.hash_function(cloned, self.typing.node_type)
                if func_hash in self.mono.func_hashes:
                    canonical_name, canonical_func = self.mono.func_hashes[func_hash]
                    self.mono.func_aliases[qualified] = canonical_name
                    cloned_methods[dname] = canonical_func
                else:
                    self.mono.func_hashes[func_hash] = (qualified, cloned)
                    cloned_methods[dname] = cloned
        if cloned_methods:
            self.mono.cloned_methods[mangled] = cloned_methods

    def _synth_collection_methods(
        self,
        mono: ZType,
        mangled: str,
        template_type: ZType,
        defn: zast.TypeDefinition,
    ) -> None:
        """Synthesise compiler-managed methods for collection-type
        monomorphisations (array, str, listview, listiter,
        mapkeyiter, mapentry, mapitemiter, list, map). Caller must
        only invoke this for non-partial monos."""
        # for arrays: validate element type, synthesize get/set/length
        if _is_array_type(mono):
            elem_type = _array_element_type(self.typing, mono)
            arr_len = _array_length(self.typing, mono)
            if elem_type and not _is_valtype(elem_type):
                self._error(
                    f"Array element type '{elem_type.name}' is not a value type; "
                    f"arrays require valtype elements"
                )
            # synthesize .length constant
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            self._set_child(mono, "length", length_type)
            if arr_len is not None:
                mono.param_defaults["length"] = str(arr_len)
            # synthesize .get method: function {i: i64} out <elem>
            if elem_type:
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                self._set_child(get_type, "i", self._resolve_name("i64") or self.t_null)
                get_type.return_type = elem_type
                self._set_child(mono, "get", get_type)
                # synthesize .set method: function {i: i64, val: <elem>} out <elem>
                set_type = _make_type(f"{mangled}.set", ZTypeType.FUNCTION)
                self._set_child(set_type, "i", self._resolve_name("i64") or self.t_null)
                self._set_child(set_type, "val", elem_type)
                set_type.return_type = elem_type
                self._set_child(mono, "set", set_type)

        # for str types: set valtype, synthesize length/size/string
        if _is_str_type(mono):
            mono.is_valtype = True
            _set_destructor_metadata(mono)
            str_cap = _str_capacity(self.typing, mono)
            # synthesize .length field (runtime, u64)
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            self._set_child(mono, "length", length_type)
            # synthesize .size constant (compile-time)
            size_type = _make_type("u64", ZTypeType.RECORD)
            size_type.is_valtype = True
            self._set_child(mono, "size", size_type)
            if str_cap is not None:
                mono.param_defaults["size"] = str(str_cap)
            # synthesize .string method: function {} out string
            string_method = _make_type(f"{mangled}.string", ZTypeType.FUNCTION)
            string_method.return_type = self._resolve_name("String") or self.t_null
            self._set_child(mono, "string", string_method)

        # for listview types: set reftype, synthesize methods
        # Listview struct is stack-allocated; no owned data (borrowed from list).
        if _is_listview_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            elem_type = _listview_element_type(self.typing, mono)
            t_u64 = self._resolve_name("u64") or self.t_null
            # synthesize .length field (runtime, u64)
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            self._set_child(mono, "length", length_type)
            if elem_type:
                # synthesize .get method: function {i: u64} out <elem>
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                self._set_child(get_type, "i", t_u64)
                get_type.return_type = elem_type
                get_type.return_ownership = ZParamOwnership.BORROW
                self._set_child(mono, "get", get_type)

        # for listiter types: synthesize the .call method returning
        # (optionview of: elem). listiter holds a borrowed pointer to
        # the source list and an index; .call yields a borrowed view
        # to the element at the current index, or .none when exhausted.
        if _is_listiter_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            elem_type = _listiter_element_type(self.typing, mono)
            if elem_type is not None:
                ov_template = self._resolve_name("OptionView")
                if ov_template:
                    ov_defn = self._find_generic_defn(ov_template)
                    if ov_defn:
                        ov_mono = self._monomorphize(
                            ov_template, {"t": elem_type}, ov_defn
                        )
                        call_type = _make_type(f"{mangled}.call", ZTypeType.FUNCTION)
                        call_type.return_type = ov_mono
                        self._set_child(mono, "call", call_type)
            # listiter holds a borrowed pointer to its source list; no
            # owned data, so no runtime destructor is needed.
            mono.needs_destructor = False
            mono.destructor_name = None

        # for mapkeyiter types: synthesize the .call method returning
        # (optionview of: key). Same shape as listiter — the iterator
        # walks bucket slots and skips empty / deleted ones at runtime.
        if _is_mapkeyiter_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            key_t = _mapkeyiter_key_type(self.typing, mono)
            if key_t is not None:
                ov_template = self._resolve_name("OptionView")
                if ov_template:
                    ov_defn = self._find_generic_defn(ov_template)
                    if ov_defn:
                        ov_mono = self._monomorphize(ov_template, {"t": key_t}, ov_defn)
                        call_type = _make_type(f"{mangled}.call", ZTypeType.FUNCTION)
                        call_type.return_type = ov_mono
                        self._set_child(mono, "call", call_type)
            mono.needs_destructor = False
            mono.destructor_name = None

        # for mapentry types: synthesize .key / .value accessors. mapentry
        # is a borrow-only view — its C representation is a pointer to a
        # source bucket; .key / .value emit as field projections through
        # that pointer. There is no constructor (only iteration yields
        # mapentry values) and no destructor (no owned data).
        if _is_mapentry_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            mono.needs_destructor = False
            mono.destructor_name = None
            mono.create_disabled = True
            key_t = _mapentry_key_type(self.typing, mono)
            value_t = _mapentry_value_type(self.typing, mono)
            if key_t is not None:
                key_method = _make_type(f"{mangled}.key", ZTypeType.FUNCTION)
                key_method.return_type = key_t
                key_method.return_ownership = ZParamOwnership.BORROW
                self._set_child(mono, "key", key_method)
            if value_t is not None:
                val_method = _make_type(f"{mangled}.value", ZTypeType.FUNCTION)
                val_method.return_type = value_t
                val_method.return_ownership = ZParamOwnership.BORROW
                self._set_child(mono, "value", val_method)

        # for mapitemiter types: synthesize the .call method returning
        # (optionview of: mapentry). Walks bucket slots and yields a
        # bucket-pointer view per USED slot.
        if _is_mapitemiter_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            key_t = _mapitemiter_key_type(self.typing, mono)
            value_t = _mapitemiter_value_type(self.typing, mono)
            if key_t is not None and value_t is not None:
                # monomorphise mapentry<K,V> first (the call's payload)
                me_template = self._resolve_name("MapEntry")
                me_mono = None
                if me_template:
                    me_defn = self._find_generic_defn(me_template)
                    if me_defn:
                        me_mono = self._monomorphize(
                            me_template,
                            {"key": key_t, "value": value_t},
                            me_defn,
                        )
                # then optionview<mapentry<K,V>>
                if me_mono is not None:
                    ov_template = self._resolve_name("OptionView")
                    if ov_template:
                        ov_defn = self._find_generic_defn(ov_template)
                        if ov_defn:
                            ov_mono = self._monomorphize(
                                ov_template, {"t": me_mono}, ov_defn
                            )
                            call_type = _make_type(
                                f"{mangled}.call", ZTypeType.FUNCTION
                            )
                            call_type.return_type = ov_mono
                            self._set_child(mono, "call", call_type)
            mono.needs_destructor = False
            mono.destructor_name = None

        # for list types: set reftype, synthesize methods
        # List struct is stack-allocated; only the data buffer is on the heap.
        if _is_list_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            mono.needs_field_cleanup = True  # data buffer needs cleanup
            elem_type = _list_element_type(self.typing, mono)
            t_u64 = self._resolve_name("u64") or self.t_null
            # .length / .capacity expose the global u64 type so arithmetic
            # operators (+, -, <, ...) declared on u64 resolve through
            # children["+"] etc. when users do `l.length + n`. Synthesising
            # a fresh empty u64 record here would drop those methods.
            self._set_child(mono, "length", t_u64)
            self._set_child(mono, "capacity", t_u64)
            if elem_type:
                # synthesize .append method: function {from: <elem>}
                append_type = _make_type(f"{mangled}.append", ZTypeType.FUNCTION)
                self._set_child(append_type, "from", elem_type)
                append_type.param_ownership["from"] = ZParamOwnership.TAKE
                self._set_child(mono, "append", append_type)
                # synthesize .insert method: function {from: <elem> at: u64}
                insert_type = _make_type(f"{mangled}.insert", ZTypeType.FUNCTION)
                self._set_child(insert_type, "from", elem_type)
                self._set_child(insert_type, "at", t_u64)
                insert_type.param_ownership["from"] = ZParamOwnership.TAKE
                self._set_child(mono, "insert", insert_type)
                # synthesize .extend method: function {from: list_T}
                extend_type = _make_type(f"{mangled}.extend", ZTypeType.FUNCTION)
                self._set_child(extend_type, "from", mono)
                extend_type.param_ownership["from"] = ZParamOwnership.TAKE
                self._set_child(mono, "extend", extend_type)
                # synthesize .get method: function {i: u64} out <elem>
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                self._set_child(get_type, "i", t_u64)
                get_type.return_type = elem_type
                get_type.return_ownership = ZParamOwnership.BORROW
                self._set_child(mono, "get", get_type)
                # synthesize .set method: function {i: u64 val: <elem>} out <elem>
                set_type = _make_type(f"{mangled}.set", ZTypeType.FUNCTION)
                self._set_child(set_type, "i", t_u64)
                self._set_child(set_type, "val", elem_type)
                set_type.return_type = elem_type
                set_type.param_ownership["val"] = ZParamOwnership.TAKE
                self._set_child(mono, "set", set_type)
                # synthesize .pop method: function {} out <elem>
                pop_type = _make_type(f"{mangled}.pop", ZTypeType.FUNCTION)
                pop_type.return_type = elem_type
                self._set_child(mono, "pop", pop_type)
                # synthesize .listview method: function {:this.lock} out (listview of: <elem>)
                # Get or create the monomorphized listview type
                listview_template = self._resolve_name("ListView")
                listview_mono = None
                if listview_template:
                    lv_defn = self._find_generic_defn(listview_template)
                    if lv_defn:
                        listview_mono = self._monomorphize(
                            listview_template, {"of": elem_type}, lv_defn
                        )
                        listview_type = _make_type(
                            f"{mangled}.listview", ZTypeType.FUNCTION
                        )
                        listview_type.return_type = listview_mono
                        self._carry_native_method_metadata(
                            template_type, defn, "listview", listview_type
                        )
                        self._set_child(mono, "listview", listview_type)
                # synthesize .extend_view method: function {other: listview<elem>}
                # — copies bytes from a borrowed view (does NOT consume).
                if listview_mono is not None:
                    extend_view_type = _make_type(
                        f"{mangled}.extendView", ZTypeType.FUNCTION
                    )
                    self._set_child(extend_view_type, "other", listview_mono)
                    extend_view_type.param_ownership["other"] = ZParamOwnership.BORROW
                    self._set_child(mono, "extendView", extend_view_type)
                # synthesize .iterate method: function {:this} out (listiter of: elem)
                # — borrowed-view iterator over the list. Triggers
                # monomorphization of listiter<elem> so the emitter can
                # generate the iterator struct + .call function.
                listiter_template = self._resolve_name("ListIter")
                if listiter_template:
                    li_defn = self._find_generic_defn(listiter_template)
                    if li_defn:
                        listiter_mono = self._monomorphize(
                            listiter_template, {"of": elem_type}, li_defn
                        )
                        iterate_type = _make_type(
                            f"{mangled}.iterate", ZTypeType.FUNCTION
                        )
                        iterate_type.return_type = listiter_mono
                        self._carry_native_method_metadata(
                            template_type, defn, "iterate", iterate_type
                        )
                        self._set_child(mono, "iterate", iterate_type)

        # for map types: set reftype, synthesize methods
        # Maps remain heap-allocated for now.
        if _is_map_type(mono):
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            mono.is_heap_allocated = True  # map struct is still heap-allocated
            mono.needs_field_cleanup = True  # data buckets need cleanup
            key_type = _map_key_type(self.typing, mono)
            value_type = _map_value_type(self.typing, mono)
            t_u64 = self._resolve_name("u64") or self.t_null
            t_bool = self._resolve_name("bool") or self.t_null
            # synthesize .length field (runtime, u64)
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            self._set_child(mono, "length", length_type)
            # synthesize .capacity field (runtime, u64)
            cap_type = _make_type("u64", ZTypeType.RECORD)
            cap_type.is_valtype = True
            self._set_child(mono, "capacity", cap_type)
            if key_type and value_type:
                # synthesize .set method: function {key: K value: V}
                set_type = _make_type(f"{mangled}.set", ZTypeType.FUNCTION)
                self._set_child(set_type, "key", key_type)
                self._set_child(set_type, "value", value_type)
                set_type.param_ownership["key"] = ZParamOwnership.TAKE
                set_type.param_ownership["value"] = ZParamOwnership.TAKE
                self._set_child(mono, "set", set_type)
                # synthesize .get method: function {key: K} out option/optionval of: V
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                self._set_child(get_type, "key", key_type)
                if _is_valtype(value_type):
                    opt_template = self._resolve_name("optionval")
                else:
                    opt_template = self._resolve_name("Option")
                if opt_template and opt_template.isgeneric:
                    opt_defn = self._find_generic_defn(opt_template)
                    if opt_defn:
                        opt_mono = self._monomorphize(
                            opt_template, {"t": value_type}, opt_defn
                        )
                        get_type.return_type = opt_mono
                self._set_child(mono, "get", get_type)
                # synthesize .delete method: function {key: K} out bool
                delete_type = _make_type(f"{mangled}.delete", ZTypeType.FUNCTION)
                self._set_child(delete_type, "key", key_type)
                delete_type.return_type = t_bool
                self._set_child(mono, "delete", delete_type)
                # synthesize .has method: function {key: K} out bool
                has_type = _make_type(f"{mangled}.has", ZTypeType.FUNCTION)
                self._set_child(has_type, "key", key_type)
                has_type.return_type = t_bool
                self._set_child(mono, "has", has_type)
                # synthesize .iterate method: function {:this} out
                # (mapkeyiter key: K value: V) — borrowed-key iterator.
                # Triggers monomorphization of mapkeyiter<K,V> so the
                # emitter can generate the iterator struct + .call.
                mki_template = self._resolve_name("MapKeyIter")
                if mki_template:
                    mki_defn = self._find_generic_defn(mki_template)
                    if mki_defn:
                        mki_mono = self._monomorphize(
                            mki_template,
                            {"key": key_type, "value": value_type},
                            mki_defn,
                        )
                        iterate_type = _make_type(
                            f"{mangled}.iterate", ZTypeType.FUNCTION
                        )
                        iterate_type.return_type = mki_mono
                        self._carry_native_method_metadata(
                            template_type, defn, "iterate", iterate_type
                        )
                        self._set_child(mono, "iterate", iterate_type)
                # synthesize .iterate_items: borrowed-entry iterator
                # yielding mapentry views. Triggers mapitemiter<K,V> +
                # mapentry<K,V> monos.
                mii_template = self._resolve_name("MapItemIter")
                if mii_template:
                    mii_defn = self._find_generic_defn(mii_template)
                    if mii_defn:
                        mii_mono = self._monomorphize(
                            mii_template,
                            {"key": key_type, "value": value_type},
                            mii_defn,
                        )
                        iterate_items_type = _make_type(
                            f"{mangled}.iterateItems", ZTypeType.FUNCTION
                        )
                        iterate_items_type.return_type = mii_mono
                        self._carry_native_method_metadata(
                            template_type, defn, "iterateItems", iterate_items_type
                        )
                        self._set_child(mono, "iterateItems", iterate_items_type)

    def _make_mono_shell(
        self,
        template_type: ZType,
        generic_args: dict[str, ZType],
        mangled: str,
        is_partial: bool,
    ) -> ZType:
        """Construct the bare ZType for a mono: mangled name, generic-
        origin/args back-refs, baseline `is_valtype`/`is_native` from
        the template, destructor metadata, cname assignment, and (for
        partial instantiation) the residual `generic_params` /
        `numeric_generic_params` propagation. `_substitute_mono_children`
        then populates `mono.children`."""
        mono = _make_type(mangled, template_type.typetype)
        mono.generic_origin = template_type
        for ga_name, ga_type in generic_args.items():
            self._set_generic_arg(mono, ga_name, ga_type)
        mono.is_valtype = template_type.is_valtype
        mono.is_native = template_type.is_native
        _set_destructor_metadata(mono)
        self._assign_cname_type(mono)
        mono.numeric_generic_params = set(template_type.numeric_generic_params)
        if is_partial:
            mono.isgeneric = True
            for param_name, arg_type in generic_args.items():
                if arg_type.typetype != ZTypeType.GENERIC_PARAM:
                    continue
                mono.generic_params[arg_type.name] = (
                    arg_type.parent if arg_type.parent else self.t_null
                )
                if param_name in template_type.numeric_generic_params:
                    mono.numeric_generic_params.add(arg_type.name)
        return mono

    def _setup_mono_meta_create(
        self,
        mono: ZType,
        mangled: str,
        template_type: ZType,
        defn: zast.TypeDefinition,
    ) -> None:
        """Set `mono.meta_create` for class/record monos. CLASS monos
        (excluding `list`/`map`, which have their own create paths)
        get a freshly synthesised `_make_meta_create_type`; RECORD
        monos point at their existing `create` child."""
        if (
            template_type.typetype == ZTypeType.CLASS
            and not _is_list_type(mono)
            and not _is_map_type(mono)
        ):
            is_func_names: set = set()
            field_names: Optional[set] = None
            if defn.nodetype == NodeType.CLASS:
                is_func_names = set(cast(zast.ObjectDef, defn).functions().keys())
                field_names = set(cast(zast.ObjectDef, defn).is_items.keys())
            create_type = self._make_meta_create_type(
                mangled, mono, is_func_names, field_names
            )
            mono.meta_create = create_type
            if not self.typing.has_child(mono, "create"):
                self._set_child(mono, "create", create_type)
        if template_type.typetype == ZTypeType.RECORD:
            create_child = self.typing.child_of(mono, "create")
            if create_child and create_child.typetype == ZTypeType.FUNCTION:
                mono.meta_create = create_child

    def _register_mono(
        self,
        mono: ZType,
        cache_key: tuple,
        mangled: str,
        defn: zast.TypeDefinition,
        is_partial: bool,
    ) -> None:
        """Finalise a mono: refresh field-cleanup metadata, store in
        the mono cache, and (for non-partial monos) register in the
        global mono-type list and `_resolved` map under one
        `<unit>.<mangled>` key."""
        _set_field_cleanup_metadata(self.typing, mono)
        self.mono.cache[cache_key] = mono
        if not is_partial:
            self.mono.types.append((mono, defn))
            # Register the mono in _resolved under the first unit's
            # qualified name so the emitter can find it. The choice
            # of "first unit" matches pre-F5.C behaviour.
            for unitname in self.program.units:
                self._resolved[f"{unitname}.{mangled}"] = mono
                break

    def _mark_mono_native(self, mono: ZType) -> None:
        """Mark the mono's child function ZTypes `is_native=True` when
        the mono itself is `is_native` (e.g. `is native` class) or is
        a compiler-managed collection (list, map, listview, listiter,
        mapkeyiter, mapitemiter, mapentry, array, str). Each such
        method body is provided by the runtime helper or inlined at
        emit time; the metadata must match. Covers all ~15 synth
        sites uniformly."""
        is_compiler_collection = (
            _is_list_type(mono)
            or _is_listview_type(mono)
            or _is_listiter_type(mono)
            or _is_map_type(mono)
            or _is_mapkeyiter_type(mono)
            or _is_mapitemiter_type(mono)
            or _is_mapentry_type(mono)
            or _is_array_type(mono)
            or _is_str_type(mono)
        )
        if not (mono.is_native or is_compiler_collection):
            return
        for child in self.typing.child_types_of(mono):
            if child.typetype == ZTypeType.FUNCTION:
                child.is_native = True

    def _substitute_mono_children(
        self,
        mono: ZType,
        template_type: ZType,
        generic_args: dict[str, ZType],
        is_partial: bool,
    ) -> None:
        """Walk `template_type.children` and populate `mono.children`,
        replacing GENERIC_PARAM children with their concrete bindings,
        recursing into partially-instantiated non-unit children, and
        passing through structural children unchanged. Also
        auto-synthesises numeric-param fields not referenced by any
        child (size constants for str, length for array, etc.)."""
        numeric_params_referenced: set[str] = set()
        for child_name, child_type in self.typing.children_of(template_type):
            if child_type.typetype == ZTypeType.GENERIC_PARAM:
                param_ref_name = child_type.name
                concrete = generic_args.get(param_ref_name)
                if concrete:
                    if (
                        param_ref_name in template_type.numeric_generic_params
                        and concrete.numeric_value is not None
                    ):
                        numeric_params_referenced.add(param_ref_name)
                        constraint = template_type.generic_params[param_ref_name]
                        resolved_constraint = self._resolve_name(constraint.name)
                        self._set_child(
                            mono,
                            child_name,
                            resolved_constraint if resolved_constraint else constraint,
                        )
                        mono.param_defaults[child_name] = str(concrete.numeric_value)
                    else:
                        self._set_child(mono, child_name, concrete)
                else:
                    self._set_child(mono, child_name, child_type)
            elif (
                child_type.isgeneric
                and child_type.generic_origin is not None
                and not child_type.is_tag_generic_origin
                and not is_partial
                and child_type.typetype != ZTypeType.UNIT
            ):
                # partially-instantiated non-unit child — resolve remaining generic params
                # (UNIT children are handled by _monomorphize_unit)
                child_args: dict[str, ZType] = {}
                for gp_name, gp_arg in self.typing.generic_args_of(child_type):
                    if (
                        gp_arg.typetype == ZTypeType.GENERIC_PARAM
                        and gp_arg.name in generic_args
                    ):
                        child_args[gp_name] = generic_args[gp_arg.name]
                    else:
                        child_args[gp_name] = gp_arg
                child_origin = child_type.generic_origin
                if child_origin is None:
                    continue
                child_defn = self._find_generic_defn(child_origin)
                if child_defn:
                    self._set_child(
                        mono,
                        child_name,
                        self._monomorphize(child_origin, child_args, child_defn),
                    )
                else:
                    self._set_child(mono, child_name, child_type)
            else:
                self._set_child(mono, child_name, child_type)

        # auto-synthesize fields for numeric params not referenced by any child
        if not is_partial:
            for nparam in template_type.numeric_generic_params:
                if nparam in numeric_params_referenced:
                    continue
                concrete = generic_args.get(nparam)
                if concrete is None or concrete.numeric_value is None:
                    continue
                constraint = template_type.generic_params[nparam]
                resolved_constraint = self._resolve_name(constraint.name)
                self._set_child(
                    mono,
                    nparam,
                    resolved_constraint if resolved_constraint else constraint,
                )
                mono.param_defaults[nparam] = str(concrete.numeric_value)

    def _recompute_mono_typetype_marks(self, mono: ZType, template_type: ZType) -> None:
        """Re-derive `is_valtype` from the typetype (pure typetype-based
        rule, not template-inherited), refresh destructor metadata, and
        apply the option / optionview special-case marks."""
        if template_type.typetype == ZTypeType.UNION:
            mono.is_valtype = False
        elif template_type.typetype == ZTypeType.VARIANT:
            mono.is_valtype = True
        elif template_type.typetype == ZTypeType.RECORD:
            mono.is_valtype = True
        elif template_type.typetype == ZTypeType.CLASS:
            mono.is_valtype = False
        elif template_type.typetype == ZTypeType.PROTOCOL:
            mono.is_valtype = False
        elif template_type.typetype == ZTypeType.FACET:
            mono.is_valtype = True
        _set_destructor_metadata(mono)
        # Nullable-ptr Option: mark the mono as nullable_ptr when the
        # some arm is heap-allocated (pointer-based). Stack-allocated
        # types like string cannot use the nullable-ptr optimisation.
        if (
            template_type.typetype == ZTypeType.UNION
            and template_type.nodeid == self._option_template_nodeid()
        ):
            some_child = self.typing.child_of(mono, "some")
            if some_child and some_child.is_heap_allocated:
                mono.is_nullable_ptr = True
        # OptionView: standard {tag, void*} layout. Carry the template's
        # lock_arm_names through and elide the destructor — the union
        # doesn't own its payload.
        if (
            template_type.typetype == ZTypeType.UNION
            and template_type.nodeid == self._optionview_template_nodeid()
        ):
            mono.lock_arm_names = set(template_type.lock_arm_names)
            mono.needs_destructor = False
            mono.destructor_name = None

    def _rebuild_mono_tag(self, mono: ZType, mangled: str) -> None:
        """Rebuild the tag enum + tag-data ZType for a UNION/VARIANT
        mono. Call only for monos whose template is UNION or VARIANT.
        The shape is identical for both kinds; the difference between
        union and variant is captured by `is_valtype` (already set by
        `_recompute_mono_typetype_marks`)."""
        subtype_names: List[str] = []
        for k, ct in self.typing.children_of(mono):
            if k == "tag":
                continue
            if ct.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if ct.is_tag_generic_origin:
                continue
            subtype_names.append(k)
        tag_type = _make_type(f"{mangled}:tag", ZTypeType.ENUM)
        for i, sname in enumerate(subtype_names):
            self._set_child(tag_type, sname, _make_type(str(i), ZTypeType.RECORD))
        mono.tag_type = tag_type
        gen_data = _make_type(f"{mangled}:tag:data", ZTypeType.DATA)
        gen_data.is_valtype = False
        for i, sname in enumerate(subtype_names):
            self._set_child(gen_data, sname, _make_type(str(i), ZTypeType.RECORD))
        gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
        gen_tag.is_valtype = True
        gen_tag.is_tag_generic_origin = True
        self._set_child(gen_data, "tag", gen_tag)
        self._set_child(mono, "tag", gen_data)

    def _check_mono_constraints(
        self, template_type: ZType, generic_args: dict[str, ZType]
    ) -> None:
        """Validate concrete generic args against the template's
        declared `generic_params` constraints (Any.valtype / Any.reftype
        / union subtype set). Emits errors for mismatches; does not
        mutate the type. Skips GENERIC_PARAM args (those are checked at
        final instantiation) and numeric-generic args (validated earlier
        in `_resolve_numeric_generic_arg`)."""
        for param_name, concrete_type in generic_args.items():
            if concrete_type.typetype == ZTypeType.GENERIC_PARAM:
                continue
            if param_name in template_type.numeric_generic_params:
                continue
            constraint = template_type.generic_params.get(param_name)
            if not constraint:
                continue
            if constraint.name == "Any.valtype":
                if not _is_valtype(concrete_type):
                    self._error(
                        f"Type '{concrete_type.name}' is not a value type; "
                        f"generic parameter '{param_name}' requires any.valtype"
                    )
                continue
            if constraint.name == "Any.reftype":
                if _is_valtype(concrete_type):
                    self._error(
                        f"Type '{concrete_type.name}' is not a reference type; "
                        f"generic parameter '{param_name}' requires any.reftype"
                    )
                continue
            if constraint.name == "Any":
                continue
            if constraint.typetype != ZTypeType.UNION:
                continue
            subtype_names = {
                k
                for k, v in self.typing.children_of(constraint)
                if k != "tag"
                and v.typetype != ZTypeType.FUNCTION
                and v.typetype != ZTypeType.DATA
                and v.typetype != ZTypeType.TAG
                and v.typetype != ZTypeType.ENUM
                and not v.is_tag_generic_origin
            }
            if concrete_type.name not in subtype_names:
                self._error(
                    f"Type '{concrete_type.name}' does not satisfy constraint "
                    f"'{constraint.name}' for generic parameter '{param_name}'"
                )

    def _clone_mono_methods(
        self,
        mono: ZType,
        mangled: str,
        generic_args: dict[str, ZType],
        defn: zast.TypeDefinition,
    ) -> None:
        """Clone, typecheck, hash, and dedup method bodies of a
        non-partial monomorphized RECORD / CLASS / UNION / VARIANT.
        Stores the dedup'd method dict on `self.mono.cloned_methods[mangled]`
        and records alias entries on `self.mono.func_aliases` for hashes
        that collapse onto an earlier canonical function."""
        defn_typed = cast(zast.ObjectDef, defn)
        method_sources: list[tuple[str, zast.Function]] = []
        for mname, mfunc in defn_typed.as_functions().items():
            if mfunc.body:
                method_sources.append((mname, mfunc))
        for mname, mfunc in defn_typed.functions().items():
            if mfunc.body:
                method_sources.append((mname, mfunc))

        cloned_methods: dict[str, zast.Function] = {}
        for mname, mfunc in method_sources:
            qualified = f"{mangled}.{mname}"
            cloned = clone_function(mfunc)
            # push mono type onto resolving stack so 'this' resolves
            self._resolving.append((mangled, mono))
            self.mono.generic_context.append({k: v for k, v in generic_args.items()})
            self._check_function_body(qualified, cloned)
            self.mono.generic_context.pop()
            self._resolving.pop()
            # hash and dedup
            func_hash = zasthash.hash_function(cloned, self.typing.node_type)
            if func_hash in self.mono.func_hashes:
                _canonical_name, canonical_func = self.mono.func_hashes[func_hash]
                self.mono.func_aliases[qualified] = _canonical_name
                cloned_methods[mname] = canonical_func
            else:
                self.mono.func_hashes[func_hash] = (qualified, cloned)
                cloned_methods[mname] = cloned

        self.mono.cloned_methods[mangled] = cloned_methods

    def _partially_instantiate_subunits(
        self, parent: ZType, parent_name: str, args: dict[str, ZType]
    ) -> None:
        """Recursively partially instantiate nested generic subunits.

        For each generic UNIT child, substitute the parent's concrete args
        into its function children while keeping its own generic params.
        Recurses to arbitrary depth.
        """
        for child_name, child_type in self.typing.children_of(parent):
            if child_type.typetype != ZTypeType.UNIT or not child_type.isgeneric:
                continue
            sub_name = f"{parent_name}.{child_name}"
            sub_unit = _make_type(sub_name, ZTypeType.UNIT)
            sub_unit.generic_origin = child_type
            for ga_name, ga_type in self.typing.generic_args_of(child_type):
                self._set_generic_arg(sub_unit, ga_name, ga_type)
            for ga_name, ga_type in args.items():
                self._set_generic_arg(sub_unit, ga_name, ga_type)
            for gp_name, gp_constraint in child_type.generic_params.items():
                if gp_name not in args:
                    sub_unit.generic_params[gp_name] = gp_constraint
                    sub_unit.isgeneric = True
            for ck, cv in self.typing.children_of(child_type):
                if cv.typetype == ZTypeType.FUNCTION:
                    self._set_child(
                        sub_unit,
                        ck,
                        self._substitute_func_type(f"{sub_name}.{ck}", cv, args),
                    )
                else:
                    self._set_child(sub_unit, ck, cv)
            self._set_child(parent, child_name, sub_unit)
            self._register_unit_type(sub_name, None, sub_unit)
            self._partially_instantiate_subunits(sub_unit, sub_name, args)

    def _make_optional_type(self, value_type: ZType) -> Optional[ZType]:
        """Wrap a type in option (reftype) or optionval (valtype)."""
        if _is_valtype(value_type):
            template = self._resolve_name("optionval")
        else:
            template = self._resolve_name("Option")
        if template and template.isgeneric:
            defn = self._find_generic_defn(template)
            if defn:
                return self._monomorphize(template, {"t": value_type}, defn)
        return None

    def _find_generic_defn(self, template_type: ZType) -> Optional[zast.TypeDefinition]:
        """Find the AST definition node for a generic template type."""
        name = template_type.name
        for unitname, unit in self.program.units.items():
            defn = unit.body.get(name)
            if defn is not None:
                return defn
        # check if the template is a file unit itself
        file_unit = self.program.units.get(name)
        if file_unit is not None:
            return file_unit
        # for partially-instantiated nested units (e.g., outer_i64.inner):
        # strip the monomorphized prefix and search in the original template
        if "." in name:
            parts = name.rsplit(".", 1)
            origin = template_type.generic_origin
            if origin is not None and not template_type.is_tag_generic_origin:
                # the generic origin IS the original definition
                origin_defn = self._find_generic_defn(origin)
                if origin_defn is not None:
                    return origin_defn
            # also search all unit bodies recursively for the leaf name
            leaf = parts[1]
            result = self._search_unit_bodies_for(leaf)
            if result is not None:
                return result
        return None

    def _search_unit_bodies_for(self, name: str) -> Optional[zast.TypeDefinition]:
        """Recursively search all unit bodies for a definition by name."""
        for _, unit in self.program.units.items():
            result = self._search_body_recursive(unit.body, name)
            if result is not None:
                return result
        return None

    def _search_body_recursive(
        self, body: dict, name: str
    ) -> Optional[zast.TypeDefinition]:
        """Search a unit body (and nested units) for a definition by name."""
        defn = body.get(name)
        if defn is not None:
            return defn
        for dname, ddefn in body.items():
            if ddefn.nodetype == NodeType.UNIT:
                result = self._search_body_recursive(cast(zast.Unit, ddefn).body, name)
                if result is not None:
                    return result
        return None

    def _infer_generic_union_construction(
        self, template: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Infer generic args for union subtype construction and monomorphize."""
        subtype_name = (
            cast(zast.DottedPath, call.callable).child.name
            if call.callable.nodetype == NodeType.DOTTEDPATH
            else None
        )
        if not subtype_name:
            return None

        generic_args: dict[str, ZType] = {}

        # check if this is a null subtype with explicit type arg
        subtype_child = self.typing.child_of(template, subtype_name)
        is_null_subtype = (
            subtype_child is not None and subtype_child.typetype == ZTypeType.NULL
        )

        # separate named args: explicit generic type args vs from: value vs positional
        from_arg = None
        positional_args = []
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
            elif arg.name and arg.name in template.generic_params:
                # explicit generic arg
                if arg.name in template.numeric_generic_params:
                    arg_type = self._resolve_numeric_generic_arg(
                        arg.valtype,
                        template.generic_params[arg.name].name,
                        loc=call.start,
                    )
                else:
                    arg_type = self._resolve_typeref_from_operation(arg.valtype)
                if arg_type:
                    generic_args[arg.name] = arg_type
            else:
                positional_args.append(arg)

        # determine the value argument (from: takes priority over positional)
        value_arg = (
            from_arg if from_arg else (positional_args[0] if positional_args else None)
        )

        if is_null_subtype and not from_arg:
            # option.none i32 — explicit type argument (positional)
            if value_arg and not generic_args:
                arg_type = self._resolve_typeref_from_operation(value_arg.valtype)
                if arg_type:
                    for param_name in template.generic_params:
                        generic_args[param_name] = arg_type
                        break
        elif subtype_child and subtype_child.typetype == ZTypeType.GENERIC_PARAM:
            # option.some 42 or option.some from: 42 — infer from argument type
            if value_arg:
                arg_type = self._check_operation(value_arg.valtype).ztype
                if arg_type:
                    param_ref_name = subtype_child.name
                    if param_ref_name not in generic_args:
                        generic_args[param_ref_name] = arg_type
                    # also check remaining positional args
                    remaining = positional_args[1:] if not from_arg else positional_args
                    for arg in remaining:
                        self._check_operation(arg.valtype)
        else:
            # non-generic subtype — just typecheck args
            if value_arg:
                self._check_operation(value_arg.valtype)
            for arg in positional_args:
                if arg is not value_arg:
                    self._check_operation(arg.valtype)

        # fill in defaults for unresolved generic params
        for param_name in template.generic_params:
            if (
                param_name not in generic_args
                and param_name in template.generic_defaults
            ):
                generic_args[param_name] = template.generic_defaults[param_name]

        if not generic_args:
            self._error(
                f"cannot infer type arguments for generic type "
                f"'{template.name}.{subtype_name}'",
                loc=call.start,
            )
            return None

        # fill in any remaining generic params that weren't inferred
        for param_name in template.generic_params:
            if param_name not in generic_args:
                self._error(
                    f"cannot infer generic parameter '{param_name}' for "
                    f"'{template.name}.{subtype_name}'"
                )
                return None

        defn = self._find_generic_defn(template)
        if not defn:
            return None
        return self._monomorphize(template, generic_args, defn)

    def _resolve_typeref_from_operation(self, op: zast.Operation) -> Optional[ZType]:
        """Try to resolve an operation as a type reference (for explicit type args).

        `null` is accepted as a type argument so generic unions/variants with a
        null payload arm can be constructed explicitly — e.g.
        `result.err e t: null` for a `result<null, E>`. Its stored typetype
        is ZTypeType.NULL even though it is declared as a `record` in the
        stdlib.
        """
        if op.nodetype == NodeType.ATOMID:
            name = cast(zast.AtomId, op).name
            if not _is_numeric_id(name):
                t = self._resolve_name(name)
                if t and t.typetype in (
                    ZTypeType.RECORD,
                    ZTypeType.UNION,
                    ZTypeType.CLASS,
                    ZTypeType.VARIANT,
                    ZTypeType.ENUM,
                    ZTypeType.NULL,
                ):
                    return t
        return None

    def _infer_generic_record_construction(
        self, template: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Infer generic args for record construction and monomorphize."""
        generic_args: dict[str, ZType] = {}

        # build field_to_gparam: field_name -> generic_param_name
        field_to_gparam: dict[str, str] = {}
        field_names: list[str] = []
        for child_name, child_type in self.typing.children_of(template):
            if child_type.typetype == ZTypeType.GENERIC_PARAM:
                field_to_gparam[child_name] = child_type.name
            field_names.append(child_name)

        positional_idx = 0
        for arg in call.arguments:
            # explicit generic arg: named arg matching a generic param
            if arg.name and arg.name in template.generic_params:
                if arg.name in template.numeric_generic_params:
                    # numeric generic param: resolve as numeric value
                    arg_type = self._resolve_numeric_generic_arg(
                        arg.valtype,
                        template.generic_params[arg.name].name,
                        loc=call.start,
                    )
                else:
                    arg_type = self._resolve_typeref_from_operation(arg.valtype)
                if arg_type:
                    generic_args[arg.name] = arg_type
                continue

            # value arg — determine which field it maps to
            if arg.name:
                field_name = arg.name
            else:
                if positional_idx < len(field_names):
                    field_name = field_names[positional_idx]
                    positional_idx += 1
                else:
                    field_name = None

            val_type = self._check_operation(arg.valtype).ztype

            # infer generic param from field type
            if field_name and field_name in field_to_gparam and val_type:
                gparam = field_to_gparam[field_name]
                if gparam in generic_args:
                    # verify compatibility
                    if generic_args[gparam].name != val_type.name:
                        self._error(
                            f"Conflicting types for generic parameter '{gparam}' "
                            f"in '{template.name}': "
                            f"'{generic_args[gparam].name}' vs '{val_type.name}'",
                            loc=call.start,
                        )
                        return None
                else:
                    generic_args[gparam] = val_type

        # fill in defaults for unresolved generic params
        for param_name in template.generic_params:
            if (
                param_name not in generic_args
                and param_name in template.generic_defaults
            ):
                generic_args[param_name] = template.generic_defaults[param_name]

        if not generic_args:
            self._error(
                f"cannot infer type arguments for generic type '{template.name}'",
                loc=call.start,
            )
            return None

        for param_name in template.generic_params:
            if param_name not in generic_args:
                self._error(
                    f"cannot infer generic parameter '{param_name}' for "
                    f"'{template.name}'",
                    loc=call.start,
                )
                return None

        defn = self._find_generic_defn(template)
        if not defn:
            return None
        return self._monomorphize(template, generic_args, defn)

    def _infer_generic_function_call(
        self, template: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Infer generic args for a generic function call and monomorphize."""
        generic_args: dict[str, ZType] = {}

        # build param_to_gparam: param_name -> generic_param_name
        param_to_gparam: dict[str, str] = {}
        param_names: list[str] = []
        for child_name, child_type in self.typing.children_of(template):
            if child_type.typetype == ZTypeType.GENERIC_PARAM:
                param_to_gparam[child_name] = child_type.name
            param_names.append(child_name)

        # separate explicit generic args from value args
        value_args: list[tuple[int, zast.NamedOperation]] = []
        positional_idx = 0
        for i, arg in enumerate(call.arguments):
            if arg.name and arg.name in template.generic_params:
                # explicit generic arg
                if arg.name in template.numeric_generic_params:
                    arg_type = self._resolve_numeric_generic_arg(
                        arg.valtype,
                        template.generic_params[arg.name].name,
                        loc=call.start,
                    )
                else:
                    arg_type = self._resolve_typeref_from_operation(arg.valtype)
                if arg_type:
                    generic_args[arg.name] = arg_type
            else:
                value_args.append((i, arg))

        # infer generic params from value args, and collect checked types
        checked_value_args: list[
            tuple[Optional[str], Optional[ZType], zast.NamedOperation]
        ] = []
        for _, arg in value_args:
            if arg.name:
                param_name = arg.name
            else:
                if positional_idx < len(param_names):
                    param_name = param_names[positional_idx]
                    positional_idx += 1
                else:
                    param_name = None

            val_type = self._check_operation(arg.valtype).ztype
            checked_value_args.append((param_name, val_type, arg))

            if param_name and param_name in param_to_gparam and val_type:
                gparam = param_to_gparam[param_name]
                if gparam in generic_args:
                    if generic_args[gparam].name != val_type.name:
                        self._error(
                            f"Conflicting types for generic parameter '{gparam}' "
                            f"in '{template.name}': "
                            f"'{generic_args[gparam].name}' vs '{val_type.name}'",
                            loc=call.start,
                        )
                        return None
                else:
                    generic_args[gparam] = val_type

        # fill in defaults for unresolved generic params
        for param_name in template.generic_params:
            if (
                param_name not in generic_args
                and param_name in template.generic_defaults
            ):
                generic_args[param_name] = template.generic_defaults[param_name]

        if not generic_args:
            self._error(
                f"cannot infer type arguments for generic function '{template.name}'",
                loc=call.start,
            )
            return None

        for param_name in template.generic_params:
            if param_name not in generic_args:
                self._error(
                    f"cannot infer generic parameter '{param_name}' for "
                    f"'{template.name}'",
                    loc=call.start,
                )
                return None

        mono_ftype = self._monomorphize_function(template, generic_args, call)
        if not mono_ftype:
            return None

        # verify value arg types against monomorphized parameter types
        mono_params = self.typing.children_of(mono_ftype)
        for param_name, val_type, arg in checked_value_args:
            if not val_type or not param_name:
                continue
            # find the matching parameter in the monomorphized function
            matched = None
            for pname, ptype in mono_params:
                if pname == param_name:
                    matched = ptype
                    break
            if matched:
                if not self._types_compatible(val_type, matched):
                    self._error(
                        f"argument '{param_name}' type mismatch: expected "
                        f"{matched.name}, got {val_type.name}",
                        loc=arg.start,
                        err=ERR.CALLERROR,
                    )

        # check for missing required value arguments
        provided_value_params: set = set()
        for param_name, _, _ in checked_value_args:
            if param_name:
                provided_value_params.add(param_name)
        for pname, ptype in mono_params:
            if (
                pname not in provided_value_params
                and pname not in mono_ftype.param_defaults
            ):
                self._error(
                    f"missing required argument '{pname}' (type: {ptype.name})",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )

        return mono_ftype

    def _monomorphize_function(
        self,
        template: ZType,
        generic_args: dict[str, ZType],
        call: zast.Call,
    ) -> Optional[ZType]:
        """Monomorphize a generic function with concrete type arguments."""
        # Identity-based cache key (see _monomorphize above for rationale).
        cache_key = (
            template.nodeid,
            tuple(sorted((k, _mono_arg_key(v)) for k, v in generic_args.items())),
        )
        if cache_key in self.mono.cache:
            return self.mono.cache[cache_key]

        # constraint checking
        for param_name, concrete_type in generic_args.items():
            if concrete_type.typetype == ZTypeType.GENERIC_PARAM:
                continue
            if param_name in template.numeric_generic_params:
                continue
            constraint = template.generic_params.get(param_name)
            if not constraint:
                continue
            if constraint.name == "Any.valtype":
                if not _is_valtype(concrete_type):
                    self._error(
                        f"Type '{concrete_type.name}' is not a value type; "
                        f"generic parameter '{param_name}' requires any.valtype",
                        loc=call.start,
                    )
                continue
            if constraint.name == "Any.reftype":
                if _is_valtype(concrete_type):
                    self._error(
                        f"Type '{concrete_type.name}' is not a reference type; "
                        f"generic parameter '{param_name}' requires any.reftype",
                        loc=call.start,
                    )
                continue
            if constraint.name != "Any":
                if constraint.typetype == ZTypeType.UNION:
                    # Walk union members in declaration order; first match
                    # wins. Concrete members match by name; protocol/facet
                    # members match if the concrete type declares conformance.
                    matched = False
                    concrete_members: list[str] = []
                    proto_members: list[str] = []
                    for k, v in self.typing.children_of(constraint):
                        if (
                            k == "tag"
                            or v.typetype
                            in (
                                ZTypeType.FUNCTION,
                                ZTypeType.DATA,
                                ZTypeType.TAG,
                                ZTypeType.ENUM,
                            )
                            or v.is_tag_generic_origin
                        ):
                            continue
                        if v.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                            proto_members.append(v.name)
                            if self._type_conforms_to_protocol(concrete_type, v):
                                matched = True
                                break
                        else:
                            concrete_members.append(k)
                            if concrete_type.name == k or concrete_type.name == v.name:
                                matched = True
                                break
                    if not matched:
                        parts: list[str] = []
                        if concrete_members:
                            parts.append(
                                "must be one of " + ", ".join(concrete_members)
                            )
                        if proto_members:
                            parts.append(
                                ("or implement " if parts else "must implement ")
                                + ", ".join(proto_members)
                            )
                        detail = f" ({'; '.join(parts)})" if parts else ""
                        self._error(
                            f"Type '{concrete_type.name}' does not satisfy constraint "
                            f"'{constraint.name}' for generic parameter "
                            f"'{param_name}'{detail}",
                            loc=call.start,
                        )

        # build mangled name
        arg_names: list[str] = []
        for k in template.generic_params:
            arg_names.append(generic_args[k].name)
        mangled = f"{template.name}_{'_'.join(arg_names)}"

        # create monomorphized function type
        mono = _make_type(mangled, ZTypeType.FUNCTION)
        mono.generic_origin = template
        for ga_name, ga_type in generic_args.items():
            self._set_generic_arg(mono, ga_name, ga_type)
        mono.is_native = template.is_native

        # copy internal metadata fields
        mono.meta_create = template.meta_create
        mono.tag_type = template.tag_type
        mono.element_type = template.element_type

        # substitute generic params in parameter types
        for child_name, child_type in self.typing.children_of(template):
            if child_type.typetype == ZTypeType.GENERIC_PARAM:
                concrete = generic_args.get(child_type.name)
                if concrete:
                    self._set_child(mono, child_name, concrete)
                else:
                    self._set_child(mono, child_name, child_type)
            else:
                self._set_child(mono, child_name, child_type)

        # substitute generic params in return type
        if template.return_type:
            if template.return_type.typetype == ZTypeType.GENERIC_PARAM:
                concrete = generic_args.get(template.return_type.name)
                if concrete:
                    mono.return_type = concrete
                else:
                    mono.return_type = template.return_type
            else:
                mono.return_type = template.return_type

        # copy ownership annotations
        mono.param_ownership = dict(template.param_ownership)
        mono.param_defaults = dict(template.param_defaults)

        # assign cname
        self._assign_cname_type(mono, qualified_name=mangled)

        # find the original function definition for body cloning
        func_defn = self._find_generic_func_defn(template)

        # clone and type-check the function body
        if func_defn and func_defn.body:
            cloned = clone_function(func_defn)
            self.mono.generic_context.append({k: v for k, v in generic_args.items()})
            self._check_function_body(mangled, cloned)
            self.mono.generic_context.pop()

            # fix up parameter types: replace GENERIC_PARAM with concrete types
            # (_check_function_body sets ppath.type to GENERIC_PARAM; emitter needs concrete)
            for pname, ppath in cloned.parameters.items():
                ppath_t = self.typing.node_type.get(ppath.nodeid)
                if (
                    ppath_t
                    and ppath_t.typetype == ZTypeType.GENERIC_PARAM
                    and ppath_t.name in generic_args
                ):
                    self.typing.node_type[ppath.nodeid] = generic_args[ppath_t.name]
                elif (
                    ppath_t
                    and ppath_t.typetype == ZTypeType.GENERIC_PARAM
                    and ppath_t.parent
                ):
                    # GENERIC_PARAM's parent is the concrete type in generic context
                    self.typing.node_type[ppath.nodeid] = ppath_t.parent
            # fix up return type
            rt = (
                self.typing.node_type.get(cloned.returntype.nodeid)
                if cloned.returntype
                else None
            )
            if cloned.returntype and rt and rt.typetype == ZTypeType.GENERIC_PARAM:
                if rt.name in generic_args:
                    self.typing.node_type[cloned.returntype.nodeid] = generic_args[
                        rt.name
                    ]
                elif rt.parent:
                    self.typing.node_type[cloned.returntype.nodeid] = rt.parent

            # hash and dedup
            func_hash = zasthash.hash_function(cloned, self.typing.node_type)
            if func_hash in self.mono.func_hashes:
                canonical_name, canonical_func = self.mono.func_hashes[func_hash]
                self.mono.func_aliases[mangled] = canonical_name
                self.mono.functions.append((mono, canonical_func))
            else:
                self.mono.func_hashes[func_hash] = (mangled, cloned)
                self.mono.functions.append((mono, cloned))
        elif func_defn and func_defn.is_native:
            # native generic function: no body to clone
            self.mono.functions.append((mono, func_defn))

        # cache and register
        self.mono.cache[cache_key] = mono
        for unitname in self.program.units:
            key = f"{unitname}.{mangled}"
            self._resolved[key] = mono
            break

        return mono

    def _type_conforms_to_protocol(self, concrete: ZType, protocol: ZType) -> bool:
        """Does `concrete` declare conformance to `protocol`?

        Conformance is declared in a type's `as` clause as
        `<label>: <protocol-name>` (or the `:name` shorthand). That
        entry becomes a child of the concrete type whose child-type is
        the protocol ZType. So conformance is a linear scan over the
        concrete's children for an entry whose type is the protocol.

        Also traverses `generic_origin` so that a monomorphized type
        (e.g. `str_64`) inherits conformance from its template (`str`).
        """
        t: Optional[ZType] = concrete
        seen: set[int] = set()
        while t is not None and id(t) not in seen:
            seen.add(id(t))
            for child_type in self.typing.child_types_of(t):
                if child_type is protocol:
                    return True
                if (
                    child_type.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET)
                    and child_type.name == protocol.name
                ):
                    return True
            origin = t.generic_origin
            if origin is None or t.is_tag_generic_origin:
                t = None
            else:
                t = origin
        return False

    def _find_generic_func_defn(self, template: ZType) -> Optional[zast.Function]:
        """Find the Function AST node for a generic function template."""
        for unitname, unit in self.program.units.items():
            result = self._search_body_for_func(unit.body, template.name)
            if result is not None:
                return result
        return None

    def _search_body_for_func(self, body: dict, name: str) -> Optional[zast.Function]:
        """Search a unit body for a function definition by name."""
        if name in body:
            defn = body[name]
            if defn.nodetype == NodeType.FUNCTION:
                return cast(zast.Function, defn)
        for dname, ddefn in body.items():
            if ddefn.nodetype == NodeType.UNIT:
                result = self._search_body_for_func(cast(zast.Unit, ddefn).body, name)
                if result is not None:
                    return result
        return None

    # ---- Function body type checking ----

    def _check_function_body(self, name: str, func: zast.Function) -> None:
        """Type-check a function body. Thin wrapper that builds the
        typed mirror after the inner walks the body."""
        self._check_function_body_inner(name, func)

    def _check_function_body_inner(self, name: str, func: zast.Function) -> None:
        if not func.body:
            return
        self.symtab.push(f"function:{name}")

        # save/restore ownership context
        prev_func_ownership = self.func_ctx.func_ownership
        prev_func_return_ownership = self.func_ctx.func_return_ownership
        # Read ownership from the resolved ZType — it carries both the
        # syntactic annotations AND the inferred BORROW-default for
        # stack-reftype parameters (set during _resolve_function_type).
        ftype = self._resolved.get(name) or self._resolved.get(
            f"{self.program.mainunitname}.{name}"
        )
        if ftype is not None and ftype.typetype == ZTypeType.FUNCTION:
            self.func_ctx.func_ownership = dict(ftype.param_ownership)
            self.func_ctx.func_return_ownership = ftype.return_ownership
        else:
            self.func_ctx.func_ownership = {}
            self.func_ctx.func_return_ownership = None

        for pname, ppath in func.parameters.items():
            stripped_ppath, _ = _strip_path_ownership(ppath)
            pt = self._resolve_typeref(cast(zast.Path, stripped_ppath))
            if pt:
                # determine parameter ownership from annotations
                param_own = self.func_ctx.func_ownership.get(pname)
                if param_own == ZParamOwnership.TAKE:
                    ownership = ZOwnership.OWNED
                elif param_own in (
                    ZParamOwnership.BORROW,
                    ZParamOwnership.LOCK,
                ):
                    # explicit .borrow / .lock — body sees a borrowed binding
                    ownership = ZOwnership.BORROWED
                else:
                    # default: borrow for all types (valtypes are copied,
                    # reftypes are referenced — neither invalidates the source)
                    ownership = ZOwnership.BORROWED
                var = ZVariable(ztype=pt, ownership=ownership, named=ZNaming.NAMED)
                self.symtab.define_var(pname, var)

        # set expected return type for return statement checking
        prev_return_type = self.func_ctx.return_type
        if func.returntype:
            stripped_rt, _ = _strip_path_ownership(func.returntype)
            self.func_ctx.return_type = self._resolve_typeref(
                cast(zast.Path, stripped_rt)
            )
        else:
            self.func_ctx.return_type = None
        self._check_statement(func.body)

        # implicit return validation: last expression type must match 'out'
        if self.func_ctx.return_type and func.body.statements:
            last = func.body.statements[-1]
            last_type = self.typing.node_type.get(last.nodeid)
            if last_type is not None and last_type.typetype != ZTypeType.NEVER:
                if not self._types_compatible(last_type, self.func_ctx.return_type):
                    self._error(
                        f"implicit return type '{last_type.name}' does not match "
                        f"declared return type '{self.func_ctx.return_type.name}'",
                        loc=last.start,
                        err=ERR.TYPEERROR,
                    )

        self.func_ctx.return_type = prev_return_type
        self.func_ctx.func_ownership = prev_func_ownership
        self.func_ctx.func_return_ownership = prev_func_return_ownership
        self.symtab.pop()

    def _check_statement(self, stmt: zast.Statement) -> None:
        """Type-check a statement block. Thin wrapper that builds the
        typed mirror after the inner walks the statement lines."""
        self._check_statement_inner(stmt)

    def _check_statement_inner(self, stmt: zast.Statement) -> None:
        # Phase C step 2: each Statement maintains a preamble buffer for
        # synth temp Assignments hoisted out of nested calls in its
        # current StatementLine. The buffer drains *before* the
        # StatementLine that produced it, preserving source order.
        self._call_preamble.append([])
        out: List[zast.StatementLine] = []
        for sline in stmt.statements:
            # dead code detection: if scope is unreachable, remaining
            # statements are dead code
            if self.symtab.is_unreachable():
                self._error(
                    "Unreachable code",
                    loc=sline.start if hasattr(sline, "start") else None,
                )
                self._call_preamble.pop()
                stmt.statements[:] = out
                return
            self._check_statement_line(sline)
            # drain anything _check_call hoisted into the preamble during
            # this StatementLine's processing — those synth Assignments
            # belong before sline in source order.
            preamble = self._call_preamble[-1]
            if preamble:
                out.extend(preamble)
                preamble.clear()
            out.append(sline)
            # after a non-completing expression, mark scope as unreachable
            inner = sline.statementline
            if inner.nodetype == NodeType.EXPRESSION:
                expr = cast(zast.Expression, inner)
                if self.typing.expr_call_kind.get(
                    expr.nodeid, zast.CallKind.UNKNOWN
                ) in (
                    zast.CallKind.RETURN,
                    zast.CallKind.BREAK,
                    zast.CallKind.CONTINUE,
                    zast.CallKind.ERROR,
                ):
                    self.symtab.mark_unreachable()
        self._call_preamble.pop()
        stmt.statements[:] = out

    def _check_statement_line(self, sline: zast.StatementLine) -> None:
        """Type-check a statement line. Thin wrapper that builds the
        typed mirror after the inner dispatches to assignment / reassign
        / swap / expression."""
        self._check_statement_line_inner(sline)

    def _check_statement_line_inner(self, sline: zast.StatementLine) -> None:
        inner = sline.statementline
        if inner.nodetype == NodeType.ASSIGNMENT:
            self._check_assignment(cast(zast.Assignment, inner))
        elif inner.nodetype == NodeType.REASSIGNMENT:
            self._check_reassignment(cast(zast.Reassignment, inner))
        elif inner.nodetype == NodeType.SWAP:
            self._check_swap(cast(zast.Swap, inner))
        elif inner.nodetype == NodeType.EXPRESSION:
            self._check_expression(cast(zast.Expression, inner))
        # propagate type to statement line wrapper
        inner_t = self.typing.node_type.get(inner.nodeid)
        if inner_t is not None:
            self.typing.node_type[sline.nodeid] = inner_t

    def _check_non_runtime_type(self, t: ZType, context: str, loc: Token) -> bool:
        """Check if a type is non-runtime (null/never/unit). Returns True if error emitted."""
        if t.typetype == ZTypeType.NULL:
            self._error(
                f"'null' cannot be used as {context} — null must be wrapped "
                "in a union or variant (eg. option.none)",
                loc=loc,
            )
            return True
        if t.typetype == ZTypeType.NEVER:
            self._error(
                f"'never' cannot be used as {context} — 'never' represents "
                "a non-completing expression (return, break, continue)",
                loc=loc,
            )
            return True
        if t.typetype == ZTypeType.UNIT:
            self._error(
                f"generic unit instantiation cannot be used as {context} — "
                "define the instantiation at the unit level instead",
                loc=loc,
            )
            return True
        return False

    def _check_assignment(self, assign: zast.Assignment) -> None:
        """Type-check a `name: expr` binding. Thin wrapper that builds
        the typed mirror after the inner runs."""
        self._check_assignment_inner(assign)

    def _check_assignment_inner(self, assign: zast.Assignment) -> None:
        result = self._check_expression(assign.value)
        t = result.ztype
        self._check_exhaustive_if(assign.value)
        if t and self._check_non_runtime_type(t, "a value", assign.start):
            return
        # .release cannot be used as a value
        inner_expr = assign.value.expression
        if (
            inner_expr.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, inner_expr).child.name == "release"
        ):
            self._error(
                "'.release' cannot be used as a value; "
                "use '.take' to transfer ownership",
                loc=assign.start,
                err=ERR.OWNERERROR,
            )
            return
        if t:
            borrow_target = result.borrow_target
            private_access = result.private_access

            if borrow_target:
                # the new variable is borrowed and holds an exclusive lock
                # on the leaf of the source path, plus SHARED on each
                # intermediate so siblings remain accessible.
                var = ZVariable(
                    ztype=t, ownership=ZOwnership.BORROWED, named=ZNaming.NAMED
                )
                var.is_private_access = private_access
                # borrow_origin records only the root for legacy escape-
                # analysis / SQL dump consumers; full path lives on the
                # installed lock entries.
                var.borrow_origin = borrow_target[0]
                self.symtab.define_var(assign.name, var)
                # skip locking for valtypes — they are copies, not references.
                # this handles generic monomorphization where .borrow was allowed
                # at definition but the concrete type is a valtype.
                if not _is_valtype(t):
                    self._install_borrow_locks(borrow_target, assign.name, assign.start)
            else:
                # new local variables are owned by default.
                var = ZVariable(
                    ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED
                )
                var.is_private_access = private_access
                self.symtab.define_var(assign.name, var)
            self.typing.node_type[assign.nodeid] = t

            # Phase B: alias optimization for inline `x: y.take` and
            # `x: y.borrow`. We only alias when ownership is explicitly
            # transferred or borrowed (take or borrow_target). Plain `x: y`
            # is NOT aliased — for valtypes it's a copy and aliasing would
            # silently change semantics; for reftypes the implicit take at
            # this level already does a pointer copy.
            is_explicit_take_or_borrow = (
                inner_expr.nodetype == NodeType.DOTTEDPATH
                and cast(zast.DottedPath, inner_expr).child.name in ("take", "borrow")
            )
            if borrow_target or is_explicit_take_or_borrow:
                self.typing.assign_alias_of[assign.nodeid] = self._alias_target(
                    assign.value
                )

            # assignment-based narrowing: if RHS is a union/variant subtype
            # construction, narrow the variable to that subtype
            subtype_name = self._get_construction_subtype_name(assign.value)
            if subtype_name and t.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
                arm_subtype = self.typing.child_of(t, subtype_name)
                if arm_subtype:
                    self.symtab.narrow(assign.name, arm_subtype, subtype_name)

    def _get_construction_subtype_name(
        self, value: zast.ExpressionSubTypes
    ) -> Optional[str]:
        """Extract the subtype name if value is a union/variant subtype construction.

        Returns the subtype name (e.g., 'ok' for result.ok 42) or None.
        """
        # unwrap Expression wrapper
        inner = value
        if inner.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, inner).expression
        # check for Call with UNION_CREATE call_kind
        if inner.nodetype == NodeType.CALL:
            call = cast(zast.Call, inner)
            if (
                self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
                == zast.CallKind.UNION_CREATE
            ):
                if call.callable.nodetype == NodeType.DOTTEDPATH:
                    return cast(zast.DottedPath, call.callable).child.name
        # check for DottedPath with parent_tagged_type (null subtype construction)
        if inner.nodetype == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, inner)
            if self.typing.dp_parent_tagged_type.get(dp.nodeid):
                return dp.child.name
        return None

    def _check_reassignment(self, reassign: zast.Reassignment) -> None:
        """Type-check a `path = expr` reassignment. Thin wrapper that
        builds the typed mirror after the inner runs."""
        self._check_reassignment_inner(reassign)

    def _check_reassignment_inner(self, reassign: zast.Reassignment) -> None:
        existing = self._check_path(reassign.topath).ztype
        new_t = self._check_expression(reassign.value).ztype
        self._check_exhaustive_if(reassign.value)
        if existing and new_t and not self._types_compatible(existing, new_t):
            self._error(
                f"Cannot assign {new_t.name} to variable of type {existing.name}",
                loc=reassign.start,
            )

        # static constant check: cannot reassign 'as' section constants
        if existing and existing.const_value is not None:
            if reassign.topath.nodetype == NodeType.DOTTEDPATH:
                child_name = cast(zast.DottedPath, reassign.topath).child.name
                self._error(
                    f"Cannot reassign static constant '{child_name}'",
                    loc=reassign.start,
                )

        # Reftype reassignment uses drop-and-transfer semantics:
        # destroy the old LHS value, move the RHS into the slot, and
        # invalidate the RHS source name so its destructor cannot fire
        # on the transferred storage. `swap` remains available when
        # both sides need to stay live (keeps two initialised slots
        # without a move).
        if existing and not _is_valtype(existing):
            rhs_root = self._get_arg_root_name(reassign.value)
            if rhs_root:
                rhs_var = self.symtab.lookup_var(rhs_root)
                if rhs_var and rhs_var.ownership == ZOwnership.BORROWED:
                    self._error(
                        f"Cannot move borrowed variable '{rhs_root}' into "
                        f"reftype field — borrowed names stay bound to their "
                        f"source for the full scope",
                        loc=reassign.start,
                        err=ERR.OWNERERROR,
                    )
                else:
                    take_loc = (
                        (
                            reassign.start.lineno,
                            reassign.start.colno,
                            reassign.start.fsno,
                        )
                        if reassign.start
                        else None
                    )
                    self.symtab.release_held_locks(rhs_root)
                    self.symtab.invalidate(rhs_root, loc=take_loc)

        # Phase B: .lock fields are immutable after construction.
        if reassign.topath.nodetype == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, reassign.topath)
            parent_t = self.typing.node_type.get(dp.parent.nodeid)
            child_name = dp.child.name
            if parent_t and child_name in parent_t.lock_field_names:
                self._error(
                    f"Cannot reassign '.lock' field '{child_name}' — lock "
                    f"fields are set at construction time and immutable",
                    loc=reassign.start,
                    err=ERR.OWNERERROR,
                )

        # Phase A: a borrowed variable cannot be reassigned (the name must
        # remain bound to the same borrowed value for its full scope).
        if reassign.topath.nodetype == NodeType.ATOMID:
            tname = cast(zast.AtomId, reassign.topath).name
            tvar = self.symtab.lookup_var(tname)
            if tvar and tvar.ownership == ZOwnership.BORROWED:
                self._error(
                    f"Cannot reassign borrowed variable '{tname}'",
                    loc=reassign.start,
                    err=ERR.OWNERERROR,
                    hint=(
                        "borrowed names are bound for their full scope; "
                        "shadow in an inner scope instead"
                    ),
                )

        # Borrow-scoped lock enforcement: reject reassignment whose path
        # collides with an outstanding exclusive lock. Works for `x = v`
        # (path is `(x,)`) and `rec.f = v` (path is `(rec, f)`). Sibling
        # field paths don't conflict. See ownership.pdoc.
        target_path = self._get_dotted_path_tuple(reassign.topath)
        if target_path:
            self._check_not_locked(target_path, "Cannot reassign", reassign.start)

        # G1 lock-escape: assigning to a field of an aggregate is a storage
        # transfer. If the RHS is a path that currently carries a lock (or
        # originates from a borrow), the lock would escape into the
        # aggregate's slot — reject.
        if target_path and len(target_path) >= 2 and reassign.value is not None:
            rhs_path = self._get_dotted_path_tuple(reassign.value)
            if rhs_path:
                rhs_root_var = self.symtab.lookup_var(rhs_path[0])
                if rhs_root_var is not None and rhs_root_var.borrow_origin is not None:
                    self._error(
                        f"cannot store lock-carrying value in field "
                        f"'{'.'.join(target_path)}': '{rhs_path[0]}' borrows "
                        f"from '{rhs_root_var.borrow_origin}'",
                        loc=reassign.start,
                        err=ERR.OWNERERROR,
                        hint=(
                            "copy the borrowed value (e.g. `.string` / "
                            "`.list` / `.bytes`) before storing, or release "
                            "the lock first"
                        ),
                    )
                else:
                    rhs_lock = self.symtab.is_path_locked(rhs_path)
                    if rhs_lock is not None:
                        self._error(
                            f"cannot store lock-carrying value in field "
                            f"'{'.'.join(target_path)}': '{rhs_path[0]}' holds "
                            f"a lock on '{'.'.join(rhs_lock.path)}' (held by "
                            f"'{rhs_lock.holder}')",
                            loc=reassign.start,
                            err=ERR.OWNERERROR,
                            hint=(
                                "copy the borrowed value (e.g. `.string` / "
                                "`.list` / `.bytes`) before storing, or "
                                "release the lock first"
                            ),
                        )

        # reassignment narrowing: reset and optionally re-narrow
        if reassign.topath.nodetype == NodeType.ATOMID:
            var_name = cast(zast.AtomId, reassign.topath).name
            self.symtab.reset_narrowing(var_name)
            subtype_name = self._get_construction_subtype_name(reassign.value)
            if (
                subtype_name
                and existing
                and existing.typetype
                in (
                    ZTypeType.UNION,
                    ZTypeType.VARIANT,
                )
            ):
                arm_subtype = self.typing.child_of(existing, subtype_name)
                if arm_subtype:
                    self.symtab.narrow(var_name, arm_subtype, subtype_name)

    def _check_swap(self, swap: zast.Swap) -> None:
        """Type-check a `lhs swap rhs` swap. Thin wrapper that builds
        the typed mirror after the inner runs."""
        self._check_swap_inner(swap)

    def _check_swap_inner(self, swap: zast.Swap) -> None:
        lhs_t = self._check_path(swap.lhs).ztype
        rhs_t = self._check_path(swap.rhs).ztype
        if lhs_t and rhs_t and lhs_t.name != rhs_t.name:
            self._error(
                f"Cannot swap {lhs_t.name} with {rhs_t.name}",
                loc=swap.start,
            )

        # ownership check: swap arguments must be owned (or parent must be owned for dotted)
        self._check_swap_ownership(swap.lhs, "left", swap.start)
        self._check_swap_ownership(swap.rhs, "right", swap.start)

    def _check_not_locked(
        self, path: Tuple[str, ...], context: str, loc: Token
    ) -> None:
        """Emit an error if `path` collides with an outstanding exclusive lock.

        Conflict uses prefix-overlap: the requested path conflicts with any
        EXCLUSIVE lock whose path is a prefix of it or vice versa. `context`
        is a phrase like "Cannot reassign" or "Cannot swap left operand"
        placed at the start of the error message.
        """
        if not path:
            return
        conflict = self.symtab.find_exclusive_lock(path)
        if conflict:
            _, holder = conflict
            self._error(
                f"{context}: '{path[0]}' has exclusive lock held by '{holder}'",
                loc=loc,
                err=ERR.OWNERERROR,
            )

    def _check_swap_ownership(self, path: zast.Path, side: str, loc: Token) -> None:
        """Check that swap argument is owned (or parent is owned for dotted paths)."""
        if path.nodetype == NodeType.ATOMID:
            path_atom = cast(zast.AtomId, path)
            var = self.symtab.lookup_var(path_atom.name)
            if var and var.ownership == ZOwnership.BORROWED:
                self._error(
                    f"Cannot swap {side} operand '{path_atom.name}': variable is borrowed",
                    loc=loc,
                )
        elif path.nodetype == NodeType.DOTTEDPATH:
            # for dotted paths, check that the root parent is owned
            root: zast.Path = path
            while root.nodetype == NodeType.DOTTEDPATH:
                root = cast(zast.DottedPath, root).parent
            if root.nodetype == NodeType.ATOMID:
                root_name = cast(zast.AtomId, root).name
                var = self.symtab.lookup_var(root_name)
                if var and var.ownership == ZOwnership.BORROWED:
                    self._error(
                        f"Cannot swap {side} operand: parent '{root_name}' is borrowed",
                        loc=loc,
                    )

        # Borrow-scoped lock enforcement: reject swap whose path collides
        # with an outstanding exclusive lock. See ownership.pdoc.
        target_path = self._get_dotted_path_tuple(path)
        if target_path:
            self._check_not_locked(target_path, f"Cannot swap {side} operand", loc)

    def _check_expression(self, expr: zast.Expression) -> ExprResult:
        inner = expr.expression
        t: Optional[ZType] = None
        # borrow_target / private_access flow back from the inner CALL or
        # OPERATION through their ExprResult; other branches don't carry
        # borrow intent.
        borrow_target: Optional[Tuple[str, ...]] = None
        private_access: bool = False
        if inner.nodetype == NodeType.CALL:
            call_result = self._check_call(cast(zast.Call, inner))
            t = call_result.ztype
            borrow_target = call_result.borrow_target
            private_access = call_result.private_access
        elif inner.nodetype == NodeType.IF:
            t = self._check_if(cast(zast.If, inner))
        elif inner.nodetype == NodeType.FOR:
            t = self._check_for(cast(zast.For, inner))
        elif inner.nodetype == NodeType.DO:
            inner_do = cast(zast.Do, inner)
            self.symtab.push("block")
            # introduce break (but not continue) for early exit
            t_never = self._resolve_name("never")
            if t_never:
                break_type = _make_type("break", ZTypeType.FUNCTION)
                break_type.return_type = t_never
                break_type.control_kind = ControlKind.BREAK
                self.symtab.define("break", break_type)
            self._break_targets.append(inner_do)
            self._check_statement(inner_do.statement)
            self._break_targets.pop()
            last_type = self._last_statement_type(inner_do.statement)
            if self.typing.do_has_break.get(inner_do.nodeid, False):
                # break makes the do expression type optional
                if (
                    last_type is not None
                    and last_type.is_ztype
                    and cast(ZType, last_type).name != "null"
                ):
                    opt_t = self._make_optional_type(cast(ZType, last_type))
                    if opt_t:
                        t = opt_t
                        self.typing.node_type[inner_do.nodeid] = opt_t
                    else:
                        t = self.t_null
                else:
                    t = self.t_null
            elif last_type is not None and last_type.is_ztype:
                t = cast(ZType, last_type)
                self.typing.node_type[inner_do.nodeid] = t
            else:
                t = self.t_null
            self.symtab.pop()
        elif inner.nodetype == NodeType.WITH:
            t = self._check_with(cast(zast.With, inner))
        elif inner.nodetype == NodeType.CASE:
            t = self._check_case(cast(zast.Case, inner))
        elif inner.nodetype == NodeType.DATA:
            t = None
        elif inner.nodetype == NodeType.REASSIGNMENT:
            self._check_reassignment(cast(zast.Reassignment, inner))
            t = self.t_null
        elif inner.nodetype == NodeType.SWAP:
            self._check_swap(cast(zast.Swap, inner))
            t = self.t_null
        elif inner.nodetype in (
            NodeType.BINOP,
            NodeType.DOTTEDPATH,
            NodeType.ATOMID,
            NodeType.ATOMSTRING,
            NodeType.EXPRESSION,
            NodeType.NAMEDOPERATION,
            NodeType.LABELVALUE,
        ):
            inner_op = cast(zast.Operation, inner)
            op_result = self._check_operation(inner_op)
            t = op_result.ztype
            borrow_target = op_result.borrow_target
            private_access = op_result.private_access
            # propagate const_value from inner operation to expression wrapper
            inner_cv = self.typing.node_const_value.get(inner_op.nodeid)
            if inner_cv is not None:
                self.typing.node_const_value[expr.nodeid] = inner_cv
            # bare function name as value: all params must have defaults
            # (skip control flow: return, break, continue, error)
            # only check when the atom refers to a function definition, not a local var
            if (
                t is not None
                and t.typetype == ZTypeType.FUNCTION
                and t.control_kind == ControlKind.NONE
                and inner.nodetype == NodeType.ATOMID
                and self.typing.child_count(t) > 0
                and self._lookup_definition(cast(zast.AtomId, inner).name) is not None
            ):
                for pname, ptype in self.typing.children_of(t):
                    if pname not in t.param_defaults:
                        self._error(
                            f"missing required argument '{pname}' (type: {ptype.name})",
                            loc=inner.start,
                            err=ERR.CALLERROR,
                        )
                        break
            # bare record/class name as value: all data fields must have defaults
            if (
                t is not None
                and t.typetype in (ZTypeType.RECORD, ZTypeType.CLASS)
                and not t.is_native
                and inner.nodetype == NodeType.ATOMID
                and self._lookup_definition(cast(zast.AtomId, inner).name) is not None
            ):
                create_type = t.meta_create
                if create_type:
                    for pname, ptype in self.typing.children_of(create_type):
                        if ptype.typetype == ZTypeType.FUNCTION:
                            continue
                        if pname not in create_type.param_defaults:
                            self._error(
                                f"missing required field '{pname}' "
                                f"(type: {ptype.name})",
                                loc=inner.start,
                                err=ERR.CALLERROR,
                            )
                            break
        if t is not None:
            self.typing.node_type[expr.nodeid] = t
            # tag control flow expressions using resolved type's control_kind
            if t.control_kind != ControlKind.NONE:
                _CK_MAP = {
                    ControlKind.RETURN: zast.CallKind.RETURN,
                    ControlKind.BREAK: zast.CallKind.BREAK,
                    ControlKind.CONTINUE: zast.CallKind.CONTINUE,
                    ControlKind.ERROR: zast.CallKind.ERROR,
                    ControlKind.PANIC: zast.CallKind.PANIC,
                }
                self.typing.expr_call_kind[expr.nodeid] = _CK_MAP.get(
                    t.control_kind, zast.CallKind.UNKNOWN
                )
                # flag enclosing do block if break targets it
                if t.control_kind == ControlKind.BREAK and self._break_targets:
                    target = self._break_targets[-1]
                    if target is not None:
                        self.typing.do_has_break[target.nodeid] = True
            elif inner.nodetype == NodeType.CALL:
                # propagate call_kind from Call to Expression wrapper
                self.typing.expr_call_kind[expr.nodeid] = self.typing.call_kind.get(
                    inner.nodeid, zast.CallKind.UNKNOWN
                )
        return ExprResult(t, borrow_target, private_access)

    def _check_operation(self, op: zast.Operation) -> ExprResult:
        """Type-check an operation. Returns an ExprResult carrying the
        resolved ztype plus any borrow_target / private_access intent
        that the inner call or path resolution stamped. The CALL branch
        receives the intent via _check_call's ExprResult; the BINOP /
        PATH branches still rely on the legacy _pending_* side-channel
        (cleared at this boundary) until F5.A.3 pushes ExprResult
        through _check_path / _check_dotted_path / _check_atomid.
        """
        t: Optional[ZType] = None
        borrow_target: Optional[Tuple[str, ...]] = None
        private_access: bool = False
        if op.nodetype == NodeType.CALL:
            call_result = self._check_call(cast(zast.Call, op))
            t = call_result.ztype
            borrow_target = call_result.borrow_target
            private_access = call_result.private_access
        elif op.nodetype == NodeType.BINOP:
            t = self._check_binop(cast(zast.BinOp, op))
        elif op.nodetype in (
            NodeType.ATOMID,
            NodeType.DOTTEDPATH,
            NodeType.ATOMSTRING,
            NodeType.EXPRESSION,
            NodeType.LABELVALUE,
        ):
            path_result = self._check_path(cast(zast.Path, op))
            t = path_result.ztype
            borrow_target = path_result.borrow_target
            private_access = path_result.private_access
            if (
                t
                and t.isgeneric
                and t.typetype
                in (
                    ZTypeType.RECORD,
                    ZTypeType.CLASS,
                    ZTypeType.UNION,
                    ZTypeType.PROTOCOL,
                    ZTypeType.FACET,
                )
            ):
                type_desc = t.name
                if op.nodetype == NodeType.DOTTEDPATH:
                    type_desc = f"{t.name}.{cast(zast.DottedPath, op).child.name}"
                self._error(
                    f"cannot infer type arguments for generic type '{type_desc}'",
                    loc=op.start,
                )
                t = None
        return ExprResult(t, borrow_target, private_access)

    def _check_path(
        self, path: zast.Path, coerce_method_to_return: bool = True
    ) -> ExprResult:
        """Type-check a path expression. When `coerce_method_to_return` is
        True (the default for value-position uses), a dotted path naming a
        no-user-arg method auto-calls — its type is the method's return
        type. `_check_call` passes False so explicit method calls
        (`container.slice c: c`) see the function type and dispatch
        normally instead of falling into construction-of-return-type.

        Returns an ExprResult carrying the resolved ztype plus any
        borrow_target / private_access intent that the legacy `_pending_*`
        side-channel was set to during resolution. Captures and clears
        those flags at the boundary so callers consume intent via the
        result instead of poking the flags directly.
        """
        t: Optional[ZType] = None
        if path.nodetype == NodeType.EXPRESSION:
            path_expr = cast(zast.Expression, path)
            t = self._check_expression(path_expr).ztype
            if t and not self.typing.node_type.get(path_expr.nodeid):
                self.typing.node_type[path_expr.nodeid] = t
        elif path.nodetype == NodeType.ATOMSTRING:
            path_str = cast(zast.AtomString, path)
            self._check_string_interpolation(path_str)
            has_interp = any(
                p.nodetype != NodeType.STRINGCHUNK for p in path_str.stringparts
            )
            self.typing.node_type[path_str.nodeid] = self._resolve_name(
                "String" if has_interp else "StringView"
            )
            t = self.typing.node_type.get(path_str.nodeid)
        elif path.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
            t = self._check_atomid(cast(zast.AtomId, path))
        elif path.nodetype == NodeType.DOTTEDPATH:
            t = self._check_dotted_path(
                cast(zast.DottedPath, path),
                coerce_method_to_return=coerce_method_to_return,
            )
        borrow_target = self._pending_borrow_lock
        private_access = self._pending_private_access
        self._pending_borrow_lock = None
        self._pending_private_access = False
        return ExprResult(t, borrow_target, private_access)

    def _method_has_no_user_args(self, method: ZType) -> bool:
        """True if the method has no required user-visible parameters
        beyond the implicit receiver. Three forms qualify:
          (a) sole param literally named `this` (`:this` shorthand)
          (b) sole param matches `this_param_name` (long-form receiver)
          (c) no params recorded at all (synthesized natives like
              `list.listview` after monomorphisation)
        """
        cnt = self.typing.child_count(method)
        if cnt == 0:
            return True
        if cnt != 1:
            return False
        only_param = self.typing.child_names_of(method)[0]
        if only_param == "this":
            return True
        if method.this_param_name == only_param:
            return True
        return False

    def _check_dotted_path(
        self, path: zast.DottedPath, coerce_method_to_return: bool = True
    ) -> Optional[ZType]:
        """Type-check a dotted path. Thin wrapper that builds the
        typed-tree mirror after the resolution body has populated
        `self.typing.node_type.get(path.nodeid)` (and the other in-place decorations). The mirror
        is skipped when the parent has no typed counterpart yet (e.g.
        it's an AtomString or interpolation Expression — both
        scheduled for later sub-steps)."""
        t = self._check_dotted_path_inner(path, coerce_method_to_return)
        return t

    def _check_dp_take(self, path: zast.DottedPath) -> Optional[ZType]:
        """`.take` ownership transfer intrinsic. Resolves the parent,
        propagates its borrow/private intent into the side-channel,
        and either invalidates the parent (when it's an addressable
        variable) or signals fall-through to the regular child lookup
        when the parent type defines a user-level `.take` member,
        is a protocol/facet/typedef whose `.take` is a constructor,
        or has no resolvable type. Returns the resolved type when
        the intrinsic handled the path; None for fall-through."""
        parent_result = self._check_path(path.parent)
        parent_type = parent_result.ztype
        self._pending_borrow_lock = parent_result.borrow_target
        self._pending_private_access = parent_result.private_access
        if parent_type is None:
            return None
        if self.typing.has_child(parent_type, "take"):
            return None
        if parent_type.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
            return None
        if parent_type.typedef_base is not None:
            return None
        # check if parent is a unit-level definition (function or spec)
        if (
            parent_type.typetype == ZTypeType.FUNCTION
            and path.parent.nodetype == NodeType.ATOMID
        ):
            defn = self._lookup_definition(cast(zast.AtomId, path.parent).name)
            if defn is not None and defn.nodetype == NodeType.FUNCTION:
                defn_func = cast(zast.Function, defn)
                if defn_func.body is None and not defn_func.is_native:
                    self._error(
                        f"Cannot take spec '{cast(zast.AtomId, path.parent).name}': "
                        f"specs have no value; use a function name",
                        loc=path.start,
                    )
                    return parent_type
                # real function — immutable program text, no invalidation
                self.typing.node_type[path.nodeid] = parent_type
                return parent_type
        # .take invalidates the source name (variable)
        if path.parent.nodetype == NodeType.ATOMID:
            take_parent_name = cast(zast.AtomId, path.parent).name
            var = self.symtab.lookup_var(take_parent_name)
            if var and var.ownership == ZOwnership.BORROWED:
                self._error(
                    f"Cannot take ownership of borrowed variable '{take_parent_name}'",
                    loc=path.start,
                )
            else:
                self.symtab.release_held_locks(take_parent_name)
                take_loc = (
                    (path.start.lineno, path.start.colno, path.start.fsno)
                    if path.start
                    else None
                )
                self.symtab.invalidate(take_parent_name, loc=take_loc)
        self.typing.node_type[path.nodeid] = parent_type
        return parent_type

    def _check_dp_release(self, path: zast.DottedPath) -> Optional[ZType]:
        """`.release` early scope-exit intrinsic. Returns the parent
        type when handled; None for fall-through (user-defined
        `.release` member or unresolved parent)."""
        parent_result = self._check_path(path.parent)
        parent_type = parent_result.ztype
        self._pending_borrow_lock = parent_result.borrow_target
        self._pending_private_access = parent_result.private_access
        if parent_type is None or self.typing.has_child(parent_type, "release"):
            return None
        if path.parent.nodetype != NodeType.ATOMID:
            self._error(
                "'.release' can only be applied to a variable name",
                loc=path.start,
                err=ERR.OWNERERROR,
            )
            return parent_type
        release_name = cast(zast.AtomId, path.parent).name
        # cannot release a top-level definition
        if self._lookup_definition(release_name) is not None:
            self._error(
                f"Cannot release top-level definition '{release_name}'",
                loc=path.start,
                err=ERR.OWNERERROR,
            )
            return parent_type
        var = self.symtab.lookup_var(release_name)
        if var:
            lock = self.symtab.find_lock(release_name)
            if lock:
                self._error(
                    f"Cannot release '{release_name}': "
                    f"{lock.lock_type.name.lower()} lock held by "
                    f"'{lock.holder}'",
                    loc=path.start,
                    err=ERR.OWNERERROR,
                )
                return parent_type
            self.symtab.release_held_locks(release_name)
        # invalidate the variable
        release_loc = (
            (path.start.lineno, path.start.colno, path.start.fsno)
            if path.start
            else None
        )
        self.symtab.invalidate(release_name, loc=release_loc)
        self.typing.node_type[path.nodeid] = parent_type
        return parent_type

    def _check_dp_borrow(self, path: zast.DottedPath) -> Optional[ZType]:
        """`.borrow` lock-and-share intrinsic. Returns the parent type
        when handled; None for fall-through (user-defined `.borrow`
        member, protocol/facet/typedef constructor, or unresolved
        parent)."""
        parent_result = self._check_path(path.parent)
        parent_type = parent_result.ztype
        self._pending_borrow_lock = parent_result.borrow_target
        self._pending_private_access = parent_result.private_access
        if parent_type is None:
            return None
        if self.typing.has_child(parent_type, "borrow"):
            return None
        if parent_type.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
            return None
        if parent_type.typedef_base is not None:
            return None
        # .borrow takes an exclusive lock on the leaf path and SHARED
        # on intermediates (for reftypes). For valtypes, the lock is
        # skipped in `_check_assignment` — the result is just a copy.
        src_path = self._get_dotted_path_tuple(path.parent)
        if src_path:
            self._pending_borrow_lock = src_path
        else:
            self._error(
                "Cannot borrow temporary expression; "
                "assign the value to a variable first",
                loc=path.start,
                err=ERR.OWNERERROR,
            )
        self.typing.node_type[path.nodeid] = parent_type
        return parent_type

    def _check_dp_lock(self, path: zast.DottedPath) -> Optional[ZType]:
        """`.lock` alias for `.borrow`. Returns the parent type when
        handled; None for fall-through (user-defined `.lock` member
        or unresolved parent)."""
        parent_result = self._check_path(path.parent)
        parent_type = parent_result.ztype
        self._pending_borrow_lock = parent_result.borrow_target
        self._pending_private_access = parent_result.private_access
        if parent_type is None:
            return None
        if self.typing.has_child(parent_type, "lock"):
            return None
        src_path = self._get_dotted_path_tuple(path.parent)
        if src_path:
            self._pending_borrow_lock = src_path
        else:
            self._error(
                "Cannot lock temporary expression; "
                "assign the value to a variable first",
                loc=path.start,
                err=ERR.OWNERERROR,
            )
        self.typing.node_type[path.nodeid] = parent_type
        return parent_type

    def _check_dp_private(self, path: zast.DottedPath) -> Optional[ZType]:
        """`.private` friend-access intrinsic. Returns the parent type
        when handled; None for fall-through (user-defined `.private`
        member or unresolved parent)."""
        parent_result = self._check_path(path.parent)
        parent_type = parent_result.ztype
        self._pending_borrow_lock = parent_result.borrow_target
        self._pending_private_access = parent_result.private_access
        if parent_type is None:
            return None
        if self.typing.has_child(parent_type, "private"):
            return None
        if not self._is_internal_access(parent_type, path):
            # also allow if the variable itself has private access
            # (chained friend: `it.items.private` where items is
            # `bag.private`)
            root_var = self._get_path_root_var(path.parent)
            if not (root_var and root_var.is_private_access):
                self._error(
                    f"Cannot access '{parent_type.name}.private' from outside "
                    f"the type definition",
                    loc=path.start,
                    err=ERR.TYPEERROR,
                    hint="only methods of the type or friend types can use .private",
                )
        self._pending_private_access = True
        self.typing.node_type[path.nodeid] = parent_type
        return parent_type

    def _check_dotted_path_inner(
        self, path: zast.DottedPath, coerce_method_to_return: bool = True
    ) -> Optional[ZType]:
        """Resolution body for `_check_dotted_path`. Handles `.take`,
        `.release`, `.borrow`, `.lock`, `.private`, numeric casts, and
        regular dotted-path resolution. The wrapping `_check_dotted_path`
        builds the typed mirror once this returns."""
        child_name = path.child.name

        # Compiler intrinsics: each helper either handles the case (returns
        # ZType) or signals fall-through (returns None) when the parent
        # type shadows the intrinsic with a user-defined member.
        if child_name == "take":
            handled = self._check_dp_take(path)
            if handled is not None:
                return handled
        if child_name == "release":
            handled = self._check_dp_release(path)
            if handled is not None:
                return handled
        if child_name == "borrow":
            handled = self._check_dp_borrow(path)
            if handled is not None:
                return handled
        if child_name == "lock":
            handled = self._check_dp_lock(path)
            if handled is not None:
                return handled
        if child_name == "private":
            handled = self._check_dp_private(path)
            if handled is not None:
                return handled

        # numeric dotted path: 0.u32, 42.i8, 0xff.u16. Only treat as a
        # numeric cast when child names a known numeric type; other
        # suffixes (e.g. `.iterate`/`.each` declared natively on the
        # integer record) fall through to standard dispatch which
        # resolves the parent atom via _resolve_numeric below.
        if path.parent.nodetype == NodeType.ATOMID and _is_numeric_id(
            cast(zast.AtomId, path.parent).name
        ):
            child_name = path.child.name
            pname = cast(zast.AtomId, path.parent).name
            resolved_child = self._resolve_name(child_name)
            if (
                resolved_child is not None
                and resolved_child.typetype != ZTypeType.FUNCTION
            ):
                _, _, err = parse_number(pname + child_name)
                if err:
                    self._error(
                        f"Invalid numeric cast {pname}.{child_name}: {err}",
                        loc=path.start,
                    )
                    return None
                self.typing.node_type[path.nodeid] = resolved_child
                # Typed mirror: this branch never types `path.parent`
                # F5.E.4.d: typed-mirror registration removed.
                return resolved_child

        # regular dotted path resolution
        # ensure parent type is set for emitter (needed for class -> vs . dispatch)
        if path.parent.nodetype == NodeType.ATOMID:
            parent_atom = cast(zast.AtomId, path.parent)
            # Numeric literal parent (`5.iterate`, `42.each`): resolve
            # via the numeric inference so the standard child lookup
            # finds natives declared on the integer record.
            if _is_numeric_id(parent_atom.name):
                parent_type = self._resolve_numeric(
                    parent_atom.name, loc=parent_atom.start
                )
                if parent_type:
                    self.typing.node_type[parent_atom.nodeid] = parent_type
            else:
                parent_type = self._resolve_name(parent_atom.name)
            if parent_type:
                self.typing.node_type[path.parent.nodeid] = parent_type
                # Narrowing stamp: same as in _check_atomid, so the
                # emitter's AtomId lowering can unwrap the union/variant
                # payload when the parent is a narrowed name.
                entry = self.symtab.lookup_entry(parent_atom.name)
                if (
                    entry is not None
                    and entry.narrowed_subtype is not None
                    and entry.original_ztype is not None
                ):
                    self.typing.atom_narrowed_subtype[parent_atom.nodeid] = (
                        entry.narrowed_subtype
                    )
                    self.typing.atom_original_ztype[parent_atom.nodeid] = (
                        entry.original_ztype
                    )
                    # Phase 7b: stamp narrowed-subtype child_id against the
                    # outer union/variant (mirrors _check_atomid path).
                    if self.typing.atom_child_id.get(parent_atom.nodeid, -1) == -1:
                        self.typing.atom_child_id[parent_atom.nodeid] = (
                            entry.original_ztype.child_id_for(entry.narrowed_subtype)
                        )
                # Borrow-scoped lock enforcement: locked paths are completely
                # unavailable (reads AND writes). Check the full path being
                # accessed so sibling-path reads aren't blocked.
                if self.symtab.lookup_var(parent_atom.name):
                    target_path = self._get_dotted_path_tuple(
                        cast(zast.Operation, path)
                    )
                    if target_path:
                        self._check_not_locked(target_path, "Cannot access", path.start)
                # Typed mirror: this branch sets parent_atom.type without
                # routing through `_check_atomid`, so build the TypedAtomId
                # here so the wrapping `_check_dotted_path` can find a
                # typed parent in `by_parsed_id` when constructing
                # `TypedDottedPath`.
            else:
                taken_loc = self.symtab.get_taken_location(parent_atom.name)
                if taken_loc:
                    tline, tcol, _ = taken_loc
                    self._error(
                        f"cannot use '{parent_atom.name}' after ownership transfer",
                        loc=path.start,
                        err=ERR.OWNERERROR,
                        note=f"ownership of '{parent_atom.name}' was transferred at line {tline}, column {tcol}",
                    )
                    return None
        elif path.parent.nodetype == NodeType.DOTTEDPATH:
            self._check_dotted_path(cast(zast.DottedPath, path.parent))
        elif path.parent.nodetype == NodeType.ATOMSTRING:
            atom_str = cast(zast.AtomString, path.parent)
            self._check_string_interpolation(atom_str)
            has_interp = any(
                p.nodetype != NodeType.STRINGCHUNK for p in atom_str.stringparts
            )
            self.typing.node_type[atom_str.nodeid] = self._resolve_name(
                "String" if has_interp else "StringView"
            )
        elif path.parent.nodetype == NodeType.EXPRESSION:
            self._check_expression(cast(zast.Expression, path.parent))
        t = self._resolve_dotted_path(path)
        if t:
            self.typing.node_type[path.nodeid] = t
            # propagate const_value for numeric generic param fields
            parent_type = self.typing.node_type.get(path.parent.nodeid)
            if parent_type and self.typing.has_generic_args(parent_type):
                garg = self.typing.generic_arg_of(parent_type, child_name)
                if garg and garg.numeric_value is not None:
                    self.typing.node_const_value[path.nodeid] = garg.numeric_value
            # Phase 7b: stamp child_id against parent's ZType so the
            # emitter can dispatch by id on hot paths (union/variant
            # arm access, record field, method dispatch). Falls back to
            # name lookup when child_id stays -1.
            if (
                parent_type is not None
                and self.typing.dp_child_id.get(path.nodeid, -1) == -1
            ):
                self.typing.dp_child_id[path.nodeid] = parent_type.child_id_for(
                    path.child.name
                )
            # Auto-call coercion: a dotted path naming a method with no
            # required user args (just the implicit receiver, or no
            # params at all) is treated as a no-arg call when accessed
            # as a value. `_check_call` opts out via
            # coerce_method_to_return=False so explicit method calls
            # like `container.slice c: c` see the function type and
            # dispatch normally instead of falling into construction-of-
            # return-type. Lock side-effect: when the auto-called
            # method returns BORROW, install `_pending_borrow_lock` on
            # the receiver source so the binding gets a borrow-scoped
            # lock there. Receiver lock-install replaces the earlier
            # in-`_resolve_dotted_path` shortcut and unifies behavior
            # between native and user-defined methods.
            if (
                coerce_method_to_return
                and t.typetype == ZTypeType.FUNCTION
                and t.return_type is not None
                and self._method_has_no_user_args(t)
            ):
                if t.return_ownership == ZParamOwnership.BORROW:
                    src_path = self._get_dotted_path_tuple(path.parent)
                    if src_path:
                        self._pending_borrow_lock = src_path
                    else:
                        self._error(
                            "Cannot create view from temporary expression; "
                            "assign the value to a variable first",
                            loc=path.start,
                            err=ERR.OWNERERROR,
                        )
                self.typing.node_type[path.nodeid] = t.return_type
                return t.return_type
            # protocol/facet borrow: lock the source path
            if t.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                src_path = self._get_dotted_path_tuple(path.parent)
                if src_path:
                    self._pending_borrow_lock = src_path
                else:
                    self._error(
                        "Cannot borrow temporary expression; "
                        "assign the value to a variable first",
                        loc=path.start,
                        err=ERR.OWNERERROR,
                    )
            # Null-subtype construction: a bare `result.ok` written where a
            # value is expected is the null-payload constructor of the outer
            # type. Only apply this override when the parent is a TYPE
            # NAME (no variable binding). For variable access — `s.err`
            # where s is a value — return the arm's payload type, not the
            # outer union, so callers like `match (s.err)` dispatch on the
            # payload's tag.
            outer_pt = self.typing.dp_parent_tagged_type.get(path.nodeid)
            if outer_pt is not None:
                parent_is_variable = (
                    path.parent.nodetype == NodeType.ATOMID
                    and self.symtab.lookup_var(cast(zast.AtomId, path.parent).name)
                    is not None
                )
                if not parent_is_variable:
                    self.typing.node_type[path.nodeid] = outer_pt
                    # Stamp const_value only for bool: the arm's index in the
                    # parent's children (false -> 0, true -> 1). Enables
                    # downstream const-fold: `if bool.true` collapses to
                    # `if 1`, and a unit-level `true: bool.true` propagates
                    # the value to every use site. Restricted to bool
                    # because non-bool variant arms (e.g. `openmode.read`)
                    # still lower to struct construction at emit time;
                    # stamping const_value would cause the emitter's
                    # constant-consult path to short-circuit the struct
                    # emission and pass a bare integer instead.
                    if outer_pt.name == "bool":
                        arm_name = path.child.name
                        if self.typing.has_child(outer_pt, arm_name):
                            self.typing.node_const_value[path.nodeid] = list(
                                self.typing.child_names_of(outer_pt)
                            ).index(arm_name)
                    return outer_pt
        return t

    def _check_string_interpolation(self, atom: zast.AtomString) -> None:
        for part in atom.stringparts:
            if part.nodetype != NodeType.STRINGCHUNK:
                part_expr = cast(zast.Expression, part)
                self._check_expression(part_expr)
                self._check_exhaustive_if(part_expr)

    def _check_atomid(self, atom: zast.AtomId) -> Optional[ZType]:
        name = atom.name
        if _is_numeric_id(name):
            t = self._resolve_numeric(name, loc=atom.start)
            if t:
                self.typing.node_type[atom.nodeid] = t
                # constant folding: set const_value for integer and f64 literals
                typename, value, err = parse_number(name)
                if not err and type(value) is int:
                    self.typing.node_const_value[atom.nodeid] = value
                elif not err and type(value) is float and typename == "f64":
                    self.typing.node_const_value[atom.nodeid] = value
            return t

        t = self._resolve_name(name)
        if t:
            # Borrow-scoped lock enforcement: locked paths are completely
            # unavailable (reads AND writes) for the duration of the lock.
            if self.symtab.lookup_var(name):
                self._check_not_locked((name,), "Cannot access", atom.start)
            self.typing.node_type[atom.nodeid] = t
            # Narrowing stamp: if the name was narrowed via shadow=True
            # (match arm narrowing), record the subtype + original outer
            # type so the emitter can generate the C-level payload unwrap
            # at this AtomId's lowering site.
            entry = self.symtab.lookup_entry(name)
            if entry and entry.narrowed_subtype and entry.original_ztype is not None:
                self.typing.atom_narrowed_subtype[atom.nodeid] = entry.narrowed_subtype
                self.typing.atom_original_ztype[atom.nodeid] = entry.original_ztype
                # Phase 7b: stamp child_id of narrowed subtype against the
                # outer union/variant so the emitter's payload-unwrap can
                # dispatch by id.
                if self.typing.atom_child_id.get(atom.nodeid, -1) == -1:
                    self.typing.atom_child_id[atom.nodeid] = (
                        entry.original_ztype.child_id_for(entry.narrowed_subtype)
                    )
            # constant folding: propagate const_value for true/false literals
            if name == "true":
                self.typing.node_const_value[atom.nodeid] = True
            elif name == "false":
                self.typing.node_const_value[atom.nodeid] = False
            else:
                # propagate const_value from named constants
                defn = self._lookup_definition(name)
                if defn is not None:
                    defn_cv = self.typing.node_const_value.get(defn.nodeid)
                    if defn_cv is not None:
                        self.typing.node_const_value[atom.nodeid] = defn_cv
            return t

        # check if the variable was taken (ownership transferred)
        taken_loc = self.symtab.get_taken_location(name)
        if taken_loc:
            tline, tcol, _ = taken_loc
            self._error(
                f"cannot use '{name}' after ownership transfer",
                loc=atom.start,
                err=ERR.OWNERERROR,
                note=f"ownership of '{name}' was transferred at line {tline}, column {tcol}",
            )
            return None

        # did-you-mean: search available names in scope
        candidates = list(self.symtab.all_names())
        suggestion = _suggest_similar(name, candidates)
        self._error(
            f"undefined identifier: {name}",
            loc=atom.start,
            err=ERR.REFNOTFOUND,
            hint=f"did you mean '{suggestion}'?" if suggestion else None,
        )
        return None

    def _check_call(self, call: zast.Call) -> ExprResult:
        """Type-check a call. Thin wrapper that builds the typed-tree
        mirror after the resolution body has populated `self.typing.node_type.get(call.nodeid)`,
        `self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)`, `self.typing.call_callable_type_name.get(call.nodeid)`, and the per-argument
        `NamedOperation` projection stamps. Captures and clears the legacy
        `_pending_*` side-channel flags at the boundary so the result
        carries the borrow_target / private_access intent explicitly."""
        t = self._check_call_inner(call)
        borrow_target = self._pending_borrow_lock
        private_access = self._pending_private_access
        self._pending_borrow_lock = None
        self._pending_private_access = False
        return ExprResult(t, borrow_target, private_access)

    def _check_call_inner(self, call: zast.Call) -> Optional[ZType]:
        # Resolve the callable as the function type itself, not its
        # return type. The auto-call coercion in `_check_dotted_path`
        # is for value-position uses; in callable position we want the
        # function so the standard method-call dispatch below fires
        # instead of construction-of-return-type fallthrough.
        callee_type = self._check_path(
            call.callable, coerce_method_to_return=False
        ).ztype
        if not callee_type:
            return None
        # `_check_path` on a protocol/facet dotted callable (e.g.
        # `obj.protofield.method`) reports a borrow_target on its result,
        # which we deliberately drop here: in a call context the receiver
        # lock is installed separately by `_lock_receiver`, so retaining
        # the lift would make the first argument's processing see it as
        # if the arg had been a `.lock` / `.borrow` path and try to
        # re-lock the receiver root. The flag was already cleared by
        # `_check_path`'s boundary capture; this assignment is defensive.
        self._pending_borrow_lock = None

        cf_result = self._dispatch_call_control_flow(call, callee_type)
        if cf_result is not None:
            return cf_result

        early, result, callee_type = self._dispatch_call_construction(call, callee_type)
        if early:
            return result

        # parameter types (skip 'this' — handled separately for method calls)
        params = [
            (k, v) for k, v in self.typing.children_of(callee_type) if k != "this"
        ]

        # for callable dispatch, skip the 'this' parameter (first param of call method)
        # — the receiver is passed implicitly
        if (
            self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
            == zast.CallKind.CALLABLE
            and params
        ):
            params = params[1:]

        # push a call scope for call-scoped locking
        call_marker = self.symtab.push_call()
        # push call identity onto the typechecker's stack — locks installed
        # below carry this string as `holder`, and try_lock skips conflicts
        # where existing.holder == this id (so receiver + arg locks owned
        # by the same call merge naturally instead of self-blocking).
        call_id = f"call:{call.nodeid}"
        self._call_id_stack.append(call_id)

        lock_param_targets = self._check_call_arguments(call, callee_type, params)

        self._check_missing_call_args(call, callee_type, params)
        return self._finalize_call(call, callee_type, lock_param_targets, call_marker)

    def _dispatch_call_control_flow(
        self, call: zast.Call, callee_type: ZType
    ) -> Optional[ZType]:
        """If the callable is a control-flow primitive (return / break /
        continue / error / panic), stamp the call_kind, run any
        secondary checks, and return the call's resolved type. Returns
        None when the callable is NOT a control-flow primitive — the
        caller continues with the regular call dispatch."""
        ck = callee_type.control_kind
        if ck == ControlKind.RETURN:
            self.typing.call_kind[call.nodeid] = zast.CallKind.RETURN
            return self._check_return_call(call)
        if ck == ControlKind.BREAK:
            self.typing.call_kind[call.nodeid] = zast.CallKind.BREAK
            # flag enclosing do block if break targets it (not a for loop)
            if self._break_targets:
                target = self._break_targets[-1]
                if target is not None:
                    self.typing.do_has_break[target.nodeid] = True
            return callee_type
        if ck == ControlKind.CONTINUE:
            self.typing.call_kind[call.nodeid] = zast.CallKind.CONTINUE
            return callee_type
        if ck == ControlKind.ERROR:
            self.typing.call_kind[call.nodeid] = zast.CallKind.ERROR
            # type-check the message argument
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            # compile-time error unless suppressed (constant-false if branch)
            if self._suppress_compile_error == 0:
                msg = self._extract_error_message(call)
                self._error(msg, loc=call.start)
            self.typing.node_type[call.nodeid] = callee_type
            return callee_type
        if ck == ControlKind.PANIC:
            self.typing.call_kind[call.nodeid] = zast.CallKind.PANIC
            # type-check the message argument; no compile-time
            # diagnostic (unlike error, panic is a pure runtime
            # terminator).
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            self.typing.node_type[call.nodeid] = callee_type
            return callee_type
        return None

    def _dispatch_call_construction(
        self,
        call: zast.Call,
        callee_type: ZType,
    ) -> Tuple[bool, Optional[ZType], ZType]:
        """Special-case dispatch for non-control-flow calls. Handles
        `.str` conversion, generic function inference, union/variant
        subtype construction, callable-object redirect, disabled-create
        check, constructor-recursion check, `.stringview` substring,
        record/box/class/union/protocol/facet/typedef/unit
        construction, and protocol/facet/typedef `.create`/`.take`/
        `.borrow` from a dotted callable.

        Returns `(early, result, new_callee_type)`:
        - `(True, ZType, _)` — early-return a result.
        - `(True, None, _)` — early-return None (error already emitted).
        - `(False, _, new_callee_type)` — caller falls through to
          regular function-call dispatch with `new_callee_type` (which
          may differ from the input when the callable-object redirect
          fires)."""
        # handle .str conversion: string.str to: N or str.str to: N
        if (
            callee_type.name == "__str_convert"
            and call.callable.nodetype == NodeType.DOTTEDPATH
        ):
            return True, self._check_str_convert_call(call), callee_type

        # handle generic function call: infer type args and monomorphize
        if callee_type.isgeneric and callee_type.typetype == ZTypeType.FUNCTION:
            mono_ftype = self._infer_generic_function_call(callee_type, call)
            if not mono_ftype:
                return True, None, callee_type  # error already emitted
            self.typing.node_type[call.callable.nodeid] = mono_ftype
            # functions with no `out` have return_type None — callers
            # (match/if branch unification, expression typing) expect a
            # ZType, so normalise to `null`.
            ret = mono_ftype.return_type or self.t_null
            self.typing.node_type[call.nodeid] = ret
            if (
                self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
                == zast.CallKind.UNKNOWN
            ):
                self.typing.call_kind[call.nodeid] = zast.CallKind.REGULAR
            return True, ret, callee_type

        # handle union/variant subtype construction: dotted path parent
        # is a tagged type (must be before record/class checks since
        # subtypes may be records).
        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and self.typing.dp_parent_tagged_type.get(call.callable.nodeid) is not None
        ):
            callable_dp = cast(zast.DottedPath, call.callable)
            parent_tagged = self.typing.dp_parent_tagged_type.get(callable_dp.nodeid)
            assert parent_tagged is not None
            # generic union/variant subtype construction
            if parent_tagged.isgeneric and parent_tagged.typetype in (
                ZTypeType.UNION,
                ZTypeType.VARIANT,
            ):
                mono_type = self._infer_generic_union_construction(parent_tagged, call)
                if mono_type:
                    self.typing.node_type[call.nodeid] = mono_type
                    self.typing.call_kind[call.nodeid] = zast.CallKind.UNION_CREATE
                    self.typing.dp_parent_tagged_type[callable_dp.nodeid] = mono_type
                    self._lift_locked_arm_borrow(mono_type, callable_dp, call)
                    return True, mono_type, callee_type
                return True, None, callee_type
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            self.typing.node_type[call.nodeid] = parent_tagged
            self.typing.call_kind[call.nodeid] = zast.CallKind.UNION_CREATE
            self._lift_locked_arm_borrow(parent_tagged, callable_dp, call)
            return True, parent_tagged, callee_type

        # callable-object dispatch: a variable with a 'call' method.
        # Must be before construction checks — a variable of record/class
        # type with a 'call' method should dispatch to call, not
        # construct. This MUTATES `callee_type` to the call method and
        # falls through to the regular function-call dispatch.
        callee_is_var = call.callable.nodetype == NodeType.ATOMID and (
            self.symtab.lookup_var(cast(zast.AtomId, call.callable).name) is not None
        )
        if callee_is_var and callee_type.typetype != ZTypeType.FUNCTION:
            call_method = self.typing.child_of(callee_type, "call")
            if call_method and call_method.typetype == ZTypeType.FUNCTION:
                self.typing.call_kind[call.nodeid] = zast.CallKind.CALLABLE
                self.typing.call_callable_type_name[call.nodeid] = callee_type.name
                callee_type = call_method
                self.typing.node_type[call.callable.nodeid] = call_method
                # fall through

        # Unified call dispatch for types in callable position
        # (bare-name construction). The callable is not a runtime
        # variable; it refers to a type. If the type's 'create' is
        # disabled — either explicitly via 'create: null' or
        # implicitly for unions/variants that require subtype
        # selection — emit a targeted error here. Otherwise fall
        # through to the family-specific construction branches below.
        if (
            not callee_is_var
            and callee_type.typetype != ZTypeType.FUNCTION
            and callee_type.create_disabled
        ):
            if callee_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
                kind = "union" if callee_type.typetype == ZTypeType.UNION else "variant"
                self._error(
                    f"'{callee_type.name}' is a {kind}; a specific subtype must "
                    f"be selected. Try '{callee_type.name}.<subtype> value'.",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )
            else:
                self._error(
                    f"'{callee_type.name}.create' is disabled via 'create: null'; "
                    f"bare-name construction is not available. Use a user-defined "
                    f"constructor explicitly.",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )
            return True, None, callee_type

        # Constructor-recursion detection: reject a call that would
        # route to the type's 'create' function when that function is
        # currently being type-checked.
        if (
            not callee_is_var
            and callee_type.typetype != ZTypeType.FUNCTION
            and self.func_ctx.body
        ):
            create_fn = self.typing.child_of(callee_type, "create")
            if create_fn is self.func_ctx.body[-1]:
                self._error(
                    f"cannot call '{callee_type.name}.create' recursively "
                    f"(directly or via bare-name). Use 'meta.create' for the "
                    f"raw allocator.",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )
                return True, None, callee_type
        # Also catch the explicit form: `Type.create ...` where the
        # callable resolves to the function we're currently in.
        if (
            callee_type.typetype == ZTypeType.FUNCTION
            and self.func_ctx.body
            and callee_type is self.func_ctx.body[-1]
            and call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).child.name == "create"
        ):
            self._error(
                f"cannot call '{callee_type.name}' recursively "
                f"(directly or via bare-name). Use 'meta.create' for the "
                f"raw allocator.",
                loc=call.start,
                err=ERR.CALLERROR,
            )
            return True, None, callee_type

        # .stringview from: to: — substring view on string, str, or
        # stringview (not record construction).
        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).child.name == "stringview"
            and callee_type.typetype == ZTypeType.FUNCTION
            and callee_type.return_type is not None
            and _is_stringview_type(callee_type.return_type)
            and any(arg.name in ("from", "to") for arg in call.arguments)
        ):
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            self.typing.node_type[call.nodeid] = callee_type.return_type
            self.typing.call_kind[call.nodeid] = zast.CallKind.REGULAR
            return True, callee_type.return_type, callee_type

        # handle record construction: calling a record type creates an instance
        if callee_type.typetype == ZTypeType.RECORD:
            if callee_type.isgeneric:
                mono_type = self._infer_generic_record_construction(callee_type, call)
                if mono_type:
                    self.typing.node_type[call.nodeid] = mono_type
                    self.typing.node_type[call.callable.nodeid] = mono_type
                    self.typing.call_kind[call.nodeid] = zast.CallKind.RECORD_CREATE
                    has_value_args = any(
                        arg.name not in callee_type.generic_params
                        for arg in call.arguments
                        if arg.name
                    ) or any(not arg.name for arg in call.arguments)
                    if has_value_args:
                        self._check_missing_create_args(mono_type, call)
                    self._reject_borrow_escape_into_record(call)
                    return True, mono_type, callee_type
                return True, None, callee_type
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            self.typing.node_type[call.nodeid] = callee_type
            self.typing.call_kind[call.nodeid] = zast.CallKind.RECORD_CREATE
            self._check_missing_create_args(callee_type, call)
            self._reject_borrow_escape_into_record(call)
            return True, callee_type, callee_type

        # handle box construction: box from: val (system box only — empty class body)
        if (
            callee_type.typetype == ZTypeType.CLASS
            and callee_type.isgeneric
            and callee_type.name == "Box"
            and "t" in callee_type.generic_params
            and self.typing.child_count(callee_type) == 0
        ):
            return True, self._check_box_construction(call, callee_type), callee_type

        # handle class construction: calling a class type creates a new owned instance
        if callee_type.typetype == ZTypeType.CLASS:
            if callee_type.isgeneric:
                mono_type = self._infer_generic_record_construction(callee_type, call)
                if mono_type:
                    self.typing.node_type[call.nodeid] = mono_type
                    self.typing.node_type[call.callable.nodeid] = mono_type
                    self.typing.call_kind[call.nodeid] = zast.CallKind.CLASS_CREATE
                    has_value_args = any(
                        arg.name not in callee_type.generic_params
                        for arg in call.arguments
                        if arg.name
                    ) or any(not arg.name for arg in call.arguments)
                    if has_value_args:
                        self._check_missing_create_args(mono_type, call)
                    self._check_aggregate_lock_escape(call, mono_type)
                    return True, mono_type, callee_type
                return True, None, callee_type
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            self.typing.node_type[call.nodeid] = callee_type
            self.typing.call_kind[call.nodeid] = zast.CallKind.CLASS_CREATE
            self._check_missing_create_args(callee_type, call)
            self._check_aggregate_lock_escape(call, callee_type)
            return True, callee_type, callee_type

        # handle union construction: union.subtype expr
        if callee_type.typetype == ZTypeType.UNION:
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            self.typing.node_type[call.nodeid] = callee_type
            self.typing.call_kind[call.nodeid] = zast.CallKind.UNION_CREATE
            return True, callee_type, callee_type

        # bare-name protocol construction.
        if (
            not callee_is_var
            and callee_type.typetype == ZTypeType.PROTOCOL
            and self.typing.has_child(callee_type, "create")
        ):
            self.typing.call_kind[call.nodeid] = zast.CallKind.PROTOCOL_CREATE
            return True, self._check_protocol_create(callee_type, call), callee_type
        if (
            not callee_is_var
            and callee_type.typetype == ZTypeType.FACET
            and self.typing.has_child(callee_type, "create")
        ):
            self.typing.call_kind[call.nodeid] = zast.CallKind.FACET_CREATE
            return True, self._check_protocol_create(callee_type, call), callee_type
        if (
            not callee_is_var
            and callee_type.typedef_base is not None
            and self.typing.has_child(callee_type, "create")
        ):
            self.typing.call_kind[call.nodeid] = zast.CallKind.TYPEDEF_CREATE
            return True, self._check_typedef_create(callee_type, call), callee_type

        # generic unit instantiation: (mathops t: i64) → monomorphized unit
        if callee_type.typetype == ZTypeType.UNIT and callee_type.isgeneric:
            mono = self._resolve_typeref_call(call)
            if mono:
                self.typing.node_type[call.nodeid] = mono
                self.typing.call_kind[call.nodeid] = zast.CallKind.UNIT_INSTANTIATE
                return True, mono, callee_type
            return True, None, callee_type

        if callee_type.typetype != ZTypeType.FUNCTION:
            self._error(
                f"Cannot call non-function type: {callee_type.name}",
                loc=call.start,
            )
            return True, None, callee_type

        # protocol/typedef .create/take/borrow from: expr
        if (
            callee_type.typetype == ZTypeType.FUNCTION
            and call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).child.name
            in ("create", "take", "borrow")
        ):
            callable_dp2 = cast(zast.DottedPath, call.callable)
            parent_type = self.typing.node_type.get(callable_dp2.parent.nodeid)
            if parent_type and parent_type.typetype == ZTypeType.PROTOCOL:
                if callable_dp2.child.name == "borrow":
                    self.typing.call_kind[call.nodeid] = zast.CallKind.PROTOCOL_BORROW
                    return (
                        True,
                        self._check_protocol_borrow(parent_type, call),
                        callee_type,
                    )
                self.typing.call_kind[call.nodeid] = zast.CallKind.PROTOCOL_CREATE
                return True, self._check_protocol_create(parent_type, call), callee_type
            if parent_type and parent_type.typetype == ZTypeType.FACET:
                if callable_dp2.child.name == "borrow":
                    self.typing.call_kind[call.nodeid] = zast.CallKind.FACET_BORROW
                    return (
                        True,
                        self._check_protocol_borrow(parent_type, call),
                        callee_type,
                    )
                self.typing.call_kind[call.nodeid] = zast.CallKind.FACET_CREATE
                return True, self._check_protocol_create(parent_type, call), callee_type
            if parent_type and parent_type.typedef_base is not None:
                if callable_dp2.child.name == "borrow":
                    self.typing.call_kind[call.nodeid] = zast.CallKind.TYPEDEF_BORROW
                    return (
                        True,
                        self._check_typedef_borrow(parent_type, call),
                        callee_type,
                    )
                self.typing.call_kind[call.nodeid] = zast.CallKind.TYPEDEF_CREATE
                return True, self._check_typedef_create(parent_type, call), callee_type

        return False, None, callee_type

    def _check_call_arguments(
        self,
        call: zast.Call,
        callee_type: ZType,
        params: List[Tuple[str, ZType]],
    ) -> List[Tuple[Tuple[str, ...], Optional[str]]]:
        """Per-argument typecheck loop: type each arg, hoist non-trivial
        ones, match by name or position, apply protocol coercion where
        appropriate, apply TAKE ownership transfer, and install
        call-scoped locks for reftype args. Returns the
        `(leaf_path, param_name)` list that `_finalize_call` will
        transfer to the binding scope for LOCK params.

        Also enforces reftype-aliasing (same reftype passed as two
        arguments)."""
        # check for reftype aliasing: same reftype arg passed twice
        reftype_args: dict[str, Token] = {}
        # track which lock targets correspond to .lock parameters
        # (for transfer in `_finalize_call`).
        lock_param_targets: List[Tuple[Tuple[str, ...], Optional[str]]] = []

        for i, arg in enumerate(call.arguments):
            arg_result = self._check_operation(arg.valtype)
            arg_type = arg_result.ztype
            # Capture the source path that `.lock` / `.borrow` /
            # `.stringview` / `.listview` or protocol projection would
            # have lifted to the binding. For a bare `m2.lock` arg,
            # this is `(m2,)`, not `(m2, lock)` — the `.lock` suffix
            # is a wrapper marker, not a field access, so the
            # call-scoped lock must target the source.
            arg_borrow_path = arg_result.borrow_target

            # reftype aliasing check — runs against the ORIGINAL arg
            # expression so that two args derived from the same
            # reftype source are caught even after hoisting renames
            # them.
            if arg_type and not _is_valtype(arg_type):
                arg_name = self._get_arg_root_name(arg.valtype)
                if arg_name:
                    if arg_name in reftype_args:
                        self._error(
                            f"reftype aliasing: '{arg_name}' passed as multiple "
                            f"arguments in the same call",
                            loc=arg.start,
                            err=ERR.OWNERERROR,
                            note="passing the same reference type as multiple arguments "
                            "could allow conflicting mutations",
                        )
                    else:
                        reftype_args[arg_name] = arg.start

            # Phase C step 2 / commit 2: hoist non-trivial args into a
            # synth `_tN: <expr>` Assignment in the current Statement's
            # preamble. `arg.valtype` becomes `AtomId(_tN)`; downstream
            # type-matching, TAKE-application, and lock installation
            # see a bare name through the simple-path codepath. Trivial
            # args (bare AtomId / literal) bypass — no temp needed.
            if arg_type is not None and not self._arg_is_trivial(arg):
                self._hoist_arg(arg, arg_type, arg_borrow_path)

            if arg_type and arg.name and params:
                # named argument: match by parameter name
                matched = None
                for pname, ptype in params:
                    if pname == arg.name:
                        matched = ptype
                        break
                if matched:
                    if not self._types_compatible(arg_type, matched):
                        # Try implicit protocol projection: if the parameter
                        # expects a protocol/facet and the concrete arg
                        # type conforms, synthesise the wrapper.
                        own = callee_type.param_ownership.get(arg.name)
                        if self._try_protocol_coerce(arg, arg_type, matched, own):
                            arg_type = matched
                        else:
                            self._error(
                                f"argument '{arg.name}' type mismatch: expected "
                                f"{matched.name}, got {arg_type.name}",
                                loc=arg.start,
                                err=ERR.CALLERROR,
                            )
                else:
                    # unknown named argument — suggest similar parameter names
                    param_names = [p for p, _ in params]
                    suggestion = _suggest_similar(arg.name, param_names)
                    self._error(
                        f"unknown argument '{arg.name}'",
                        loc=arg.start,
                        err=ERR.CALLERROR,
                        hint=f"did you mean '{suggestion}'?" if suggestion else None,
                    )
            elif arg_type and not arg.name and i < len(params):
                # positional argument
                pname, ptype = params[i]
                if not self._types_compatible(arg_type, ptype):
                    own = callee_type.param_ownership.get(pname)
                    if self._try_protocol_coerce(arg, arg_type, ptype, own):
                        arg_type = ptype
                    else:
                        self._error(
                            f"argument type mismatch: expected {ptype.name}, "
                            f"got {arg_type.name}",
                            loc=arg.start,
                            err=ERR.CALLERROR,
                            note=f"parameter '{pname}' expects type {ptype.name}",
                        )
            elif arg_type and not arg.name and i >= len(params):
                # too many positional arguments
                if params:
                    sig = ", ".join(f"{p}: {t.name}" for p, t in params)
                    self._error(
                        f"too many arguments: expected {len(params)}, got at least {i + 1}",
                        loc=arg.start,
                        err=ERR.CALLERROR,
                        note=f"function signature: ({sig})",
                    )
                else:
                    self._error(
                        "too many arguments: function takes no parameters",
                        loc=arg.start,
                        err=ERR.CALLERROR,
                    )

            # ownership check: take parameters consume the argument
            pname_for_lock = None
            if arg_type and i < len(params):
                pname, _ = params[i]
                pname_for_lock = pname
                param_own = callee_type.param_ownership.get(pname)
                # determine the effective ownership: explicit annotation if
                # present, otherwise the default for the type (take for
                # valtypes, borrow for reftypes).
                effective_own = param_own
                if effective_own is None:
                    effective_own = ZParamOwnership.BORROW
                if effective_own == ZParamOwnership.TAKE:
                    self._apply_take_to_arg(arg, pname)

            # locking algorithm: take locks on arguments. Prefer the
            # source path captured from `.lock` / `.borrow` /
            # protocol projection; it points at the true source (e.g.
            # `(m2,)` for `m2.lock`). Fall back to `_lock_arg`
            # building the path from raw syntax for plain dotted
            # arguments without a lifting suffix.
            if arg_type and not _is_valtype(arg_type):
                leaf: Optional[Tuple[str, ...]]
                if arg_borrow_path is not None:
                    leaf = self._lock_source_path(arg_borrow_path, arg.start)
                else:
                    leaf = self._lock_arg(arg.valtype, arg.start)
                if leaf is not None:
                    lock_param_targets.append((leaf, pname_for_lock))

        return lock_param_targets

    def _check_missing_call_args(
        self,
        call: zast.Call,
        callee_type: ZType,
        params: List[Tuple[str, ZType]],
    ) -> None:
        """Emit errors for required parameters (no default) that
        weren't provided. Receiver-bound params (when calling a method
        via dotted-path) count as provided implicitly."""
        if not params:
            return
        provided: set = set()
        for i, arg in enumerate(call.arguments):
            if arg.name:
                provided.add(arg.name)
            elif i < len(params):
                provided.add(params[i][0])
        # Method-call receiver: the dispatch target consumes the
        # receiver as the `this_param_name` parameter implicitly.
        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and callee_type.this_param_name is not None
            and self.typing.has_child(callee_type, callee_type.this_param_name)
        ):
            provided.add(callee_type.this_param_name)
        for pname, ptype in params:
            if pname not in provided and pname not in callee_type.param_defaults:
                self._error(
                    f"missing required argument '{pname}' (type: {ptype.name})",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )

    def _finalize_call(
        self,
        call: zast.Call,
        callee_type: ZType,
        lock_param_targets: List[Tuple[Tuple[str, ...], Optional[str]]],
        call_marker: int,
    ) -> Optional[ZType]:
        """Lock the receiver, transfer LOCK-param locks out to the
        binding scope (via `_pending_borrow_lock`), pop the call
        scope, and stamp the call's resolved type / call_kind."""
        # lock the receiver (dotted chain on the callable) — lock
        # goes in the call scope and vanishes when popped
        self._lock_receiver(call.callable)

        ret = callee_type.return_type
        lock_param_names = {
            k
            for k, v in callee_type.param_ownership.items()
            if v == ZParamOwnership.LOCK
        }
        for target_path, pname in lock_param_targets:
            if pname in lock_param_names:
                # Transfer: set _pending_borrow_lock so the receiving
                # variable installs a borrow-scoped lock in
                # `_check_assignment`. We transfer only the leaf path
                # (the EXCLUSIVE one); any SHARED ancestors will be
                # reinstalled by the consumer's path walk in the
                # result binding's scope.
                self._pending_borrow_lock = self._chain_through_synth_temp(target_path)
        # Receiver-as-.lock-param: when the receiver parameter itself
        # is `.lock`-annotated (e.g. `string.stringview`'s
        # `t: this.lock`), the receiver path must transfer to the
        # binding so the source slot stays locked for the borrowed
        # return's lifetime. The receiver's call-scoped lock (taken
        # by `_lock_receiver`) lives outside `lock_param_targets`,
        # so add the propagation here.
        recv_param = callee_type.this_param_name
        if (
            recv_param is not None
            and recv_param in lock_param_names
            and call.callable.nodetype == NodeType.DOTTEDPATH
        ):
            receiver = cast(zast.DottedPath, call.callable).parent
            recv_path = self._get_dotted_path_tuple(cast(zast.Operation, receiver))
            if recv_path is not None:
                self._pending_borrow_lock = self._chain_through_synth_temp(recv_path)
        # pop the call scope — all call-scoped locks vanish
        self.symtab.pop_to(call_marker)
        self._call_id_stack.pop()

        self.typing.node_type[call.nodeid] = ret if ret else self.t_null
        if (
            self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
            == zast.CallKind.UNKNOWN
        ):
            self.typing.call_kind[call.nodeid] = zast.CallKind.REGULAR
        return self.typing.node_type.get(call.nodeid)

    def _check_missing_create_args(self, type_def: ZType, call: zast.Call) -> None:
        """Check for missing required arguments in bare-name construction.

        Validates against the type's public `create` child (which is either
        user-defined or the compiler's default meta-create wrapper). This
        means a custom `create` with an alternate signature is correctly
        checked against that signature, not the full field list.
        """
        # skip native/collection types — construction is compiler-managed
        if (
            type_def.is_native
            or _is_str_type(type_def)
            or _is_array_type(type_def)
            or _is_list_type(type_def)
            or _is_map_type(type_def)
        ):
            return
        create_type = self.typing.child_of(type_def, "create")
        if not create_type or create_type.typetype != ZTypeType.FUNCTION:
            return
        # collect non-function params (user-visible data fields only)
        data_params = [
            (pname, ptype)
            for pname, ptype in self.typing.children_of(create_type)
            if ptype.typetype != ZTypeType.FUNCTION
        ]
        if not data_params:
            return
        provided: set = set()
        for arg in call.arguments:
            if arg.name:
                provided.add(arg.name)
        for pname, ptype in data_params:
            if pname not in provided and pname not in create_type.param_defaults:
                self._error(
                    f"missing required argument '{pname}' (type: {ptype.name})",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )

    def _install_borrow_locks(
        self,
        target_path: Tuple[str, ...],
        holder: str,
        loc: Token,
    ) -> None:
        """Install borrow-scoped locks on the source path of a binding.

        SHARED on every intermediate prefix, EXCLUSIVE on the leaf. Called
        from `_check_assignment` and `_check_with` after a borrow-bearing
        expression. The locks live for the holder binding's scope; they
        are released when the scope pops (or when the holder itself is
        invalidated via `.take` / `.release`).
        """
        for end in range(1, len(target_path)):
            sub = target_path[:end]
            err = self.symtab.try_lock(sub, ZLockState.SHARED, holder)
            if err:
                self._error(err, loc=loc)
        err = self.symtab.try_lock(target_path, ZLockState.EXCLUSIVE, holder)
        if err:
            self._error(err, loc=loc)

    def _arg_is_trivial(self, arg: zast.NamedOperation) -> bool:
        """True iff `arg` is a hoisting-no-op: a bare AtomId (variable or
        numeric literal), a LabelValue, or an AtomString without
        interpolation. Anything else (Call, BinOp, DottedPath,
        interpolated string) hoists into a synth temp.
        """
        op = arg.valtype
        if op.nodetype == NodeType.ATOMID:
            return True
        if op.nodetype == NodeType.LABELVALUE:
            return True
        if op.nodetype == NodeType.ATOMSTRING:
            atom_str = cast(zast.AtomString, op)
            has_interp = any(
                p.nodetype != NodeType.STRINGCHUNK for p in atom_str.stringparts
            )
            return not has_interp
        if op.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, op).expression
            if inner.nodetype == NodeType.ATOMID:
                return True
            if inner.nodetype == NodeType.LABELVALUE:
                return True
        return False

    def _hoist_arg(
        self,
        arg: zast.NamedOperation,
        arg_type: ZType,
        arg_borrow_path: Optional[Tuple[str, ...]],
    ) -> str:
        """Hoist a non-trivial call argument into a fresh synth temp.

        Side effects, in order:
          1. Allocate `name = self._fresh_namer.next()` (e.g. `_t0`).
          2. Build a synth `name: <arg.valtype>` Assignment via
             `make_assignment` and append it to the current Statement's
             preamble (`self._call_preamble[-1]`). The driver in
             `_check_statement` drains this before the current
             StatementLine.
          3. Register a `ZVariable` for `name` in the current scope so
             downstream lookups find it. `borrow_origin` carries the
             source path captured by `.borrow`/`.lock`/protocol-projection
             handling so the metadata-driven aggregate-escape check
             (commit bde6411) fires on hoisted lock-bearing projections.
          4. Mutate `arg.valtype` in-place to `AtomId(name)` so subsequent
             type-matching, TAKE-application, and lock installation see a
             bare name through the simple-path codepath.

        Returns the synth temp's name (caller may already discard it).
        """
        temp_name = self._fresh_namer.next()
        # Build the synth Assignment binding the temp to the original
        # arg expression. _check_statement will inject this before the
        # containing StatementLine.
        temp_line = make_assignment(
            temp_name, arg.valtype, arg.valtype.start, origin="anf"
        )
        # Stamp the synth Assignment + its wrapping Expression with the
        # resolved arg_type so the emitter picks the right C type
        # (otherwise _emit_assignment defaults to int64_t and breaks
        # any non-i64 hoist).
        temp_assn = cast(zast.Assignment, temp_line.statementline)
        self.typing.node_type[temp_assn.nodeid] = arg_type
        self.typing.node_type[temp_assn.value.nodeid] = arg_type
        # If the source expression is alias-eligible, make the synth
        # temp a C-level alias instead of a real local. Without this,
        # hoisting `w.lock` into `_t1: w.lock` emits a struct copy
        # that destroys an aliased reftype's resources twice.
        #
        # Three paths:
        #   - lock-bearing projection (.lock / .stringview / .listview
        #     / .borrow): the source root path is alias-safe (it
        #     identifies a stack slot, not a fresh struct);
        #   - explicit .take / .borrow suffix on a value path: defer
        #     to _alias_target;
        #   - everything else (protocol projection, method call): NOT
        #     alias-safe — those return fresh structs that can't be
        #     elided into a name-substitution.
        if arg.valtype.nodetype == NodeType.DOTTEDPATH:
            child_name = cast(zast.DottedPath, arg.valtype).child.name
            # Aliasable suffixes: thin reinterpretations of the same
            # storage. .stringview/.listview build fresh view structs
            # of a *different* type (z_StringView_t / z_ListView_T_t)
            # so they can't be elided into a name substitution — emit
            # the real assignment instead.
            if child_name in ("take", "borrow", "lock"):
                alias_target = self._alias_target_inner(
                    cast(
                        zast.Operation,
                        cast(zast.DottedPath, arg.valtype).parent,
                    )
                )
                if alias_target is not None:
                    self.typing.assign_alias_of[temp_assn.nodeid] = alias_target
        # Synth Assignments hoisted out of call args don't go through
        # `_check_assignment` (they're inserted into the preamble
        # buffer and drained back into the parent Statement), so
        # nothing else would build their typed mirror. Build it here
        # so emitter consumers can read `alias_of` via TypedAssignment.
        self._call_preamble[-1].append(temp_line)
        # Ownership of the temp follows the source expression:
        # - explicit `.borrow` / `.lock` / projection captured an
        #   `arg_borrow_path` -> BORROWED temp rooted there;
        # - otherwise, if the source is a method-call whose ZType
        #   carries `return_ownership == BORROW`, the temp inherits
        #   that borrow (e.g. mapentry.key) so downstream TAKE checks
        #   still reject transferring the borrowed value;
        # - otherwise OWNED.
        ownership = ZOwnership.OWNED
        borrow_origin: Optional[str] = None
        if arg_borrow_path is not None:
            ownership = ZOwnership.BORROWED
            borrow_origin = ".".join(arg_borrow_path)
        elif arg.valtype.nodetype == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, arg.valtype)
            parent_t = self.typing.node_type.get(dp.parent.nodeid)
            method = (
                self.typing.child_of(parent_t, dp.child.name)
                if parent_t is not None
                else None
            )
            if (
                method is not None
                and method.typetype == ZTypeType.FUNCTION
                and method.return_ownership == ZParamOwnership.BORROW
            ):
                ownership = ZOwnership.BORROWED
                root_name = self._get_arg_root_name(arg.valtype)
                borrow_origin = root_name
        var = register_synth_var(
            arg_type,
            ownership,
            borrow_origin=borrow_origin,
            origin="anf",
        )
        self.symtab.define_var(temp_name, var)
        # Replace the arg's value with an AtomId reference to the temp.
        atom = make_atom_id(temp_name, arg.valtype.start, origin="anf")
        self.typing.node_type[atom.nodeid] = arg_type
        # `NamedOperation` is frozen post-Step 7; this is the
        # last in-place mutation needed for atomic-call hoisting
        # (rebuilding the parent Call's arguments list with a fresh
        # NamedOperation would require threading the parent through
        # every hoist site, which doesn't scale). Use the documented
        # frozen-dataclass escape hatch.
        object.__setattr__(arg, "valtype", atom)
        # Build the typed mirror for the synth atom so the wrapping
        # _build_typed_call can resolve the argument's typed counterpart
        # via by_parsed_id. The synth atom doesn't go through
        # _check_atomid (it's constructed and pre-typed here).
        return temp_name

    def _current_call_holder(self) -> str:
        """Holder string for locks installed during the topmost in-flight
        call. Used both as the `holder` field on new locks and as the
        `self_holder` predicate for try_lock so the call's own receiver
        and arg locks merge instead of self-blocking. Falls back to the
        legacy `__call` sentinel when no call is in flight (locks taken
        by call-adjacent helpers like for-loop iterator setup).
        """
        if self._call_id_stack:
            return self._call_id_stack[-1]
        return "__call"

    def _chain_through_synth_temp(self, path: Tuple[str, ...]) -> Tuple[str, ...]:
        """If the path roots at a synth temp (Phase C step 2's `_tN`)
        whose `borrow_origin` is set, replace the root with the temp's
        recorded source. Otherwise return the path unchanged.

        Synth temps live in the call's CALL scope and vanish when
        `pop_to(call_marker)` runs — propagating a path that roots at
        such a temp would attach the lock to a name that no longer
        exists in the outer scope. The chain step rewrites those paths
        to refer to the ultimate source recorded at hoist time.
        Restricted to synth-temp names (`_tN`) so it does not rewrite
        real user variables that legitimately have `borrow_origin` set
        (e.g. a borrow holder bound to a longer-lived source).
        """
        root = path[0]
        if not (root.startswith("_t") and root[2:].isdigit()):
            return path
        var = self.symtab.lookup_var(root)
        if var is None or var.borrow_origin is None:
            return path
        origin_parts = tuple(var.borrow_origin.split("."))
        if len(path) == 1:
            return origin_parts
        return origin_parts + path[1:]

    def _lock_source_path(
        self, path_tuple: Tuple[str, ...], loc: Token
    ) -> Optional[Tuple[str, ...]]:
        """Install call-scoped SHARED-on-prefixes + EXCLUSIVE-on-leaf locks
        for a pre-resolved source path (e.g. captured from `.lock`/`.borrow`).

        Returns the leaf path on success (for transfer to a `.lock`
        parameter's binding), or None if the root is not a lockable
        variable.
        """
        if not path_tuple:
            return None
        root_var = self.symtab.lookup_var(path_tuple[0])
        if root_var is None or root_var.ztype.typetype == ZTypeType.DATA:
            return None
        holder = self._current_call_holder()
        for end in range(1, len(path_tuple)):
            sub = path_tuple[:end]
            err = self.symtab.try_lock(
                sub, ZLockState.SHARED, holder, self_holder=holder
            )
            if err:
                self._error(err, loc=loc)
        err = self.symtab.try_lock(
            path_tuple, ZLockState.EXCLUSIVE, holder, self_holder=holder
        )
        if err:
            self._error(err, loc=loc)
            return None
        return path_tuple

    def _lock_arg(self, op: zast.Operation, loc: Token) -> Optional[Tuple[str, ...]]:
        """Take call-scoped locks for a function call argument.

        Builds the full addressable path from the argument source, takes
        SHARED on every intermediate prefix and EXCLUSIVE on the leaf.
        Locks go in the current scope (the call scope). Returns the leaf
        path (the EXCLUSIVE entry) so callers can transfer it out of the
        call scope on `.lock` parameters, or None if no lock was installed
        (temp expressions, DATA, unresolved names).
        """
        holder = self._current_call_holder()

        if op.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, op).expression
            if inner.nodetype == NodeType.CALL:
                return None  # sub-call: locks handled recursively by _check_call
            if inner.nodetype in (
                NodeType.BINOP,
                NodeType.DOTTEDPATH,
                NodeType.ATOMID,
                NodeType.ATOMSTRING,
                NodeType.EXPRESSION,
                NodeType.NAMEDOPERATION,
                NodeType.LABELVALUE,
            ):
                return self._lock_arg(cast(zast.Operation, inner), loc)
            return None

        path_tuple = self._get_dotted_path_tuple(op)
        if not path_tuple:
            return None

        root_var = self.symtab.lookup_var(path_tuple[0])
        if root_var is None or root_var.ztype.typetype == ZTypeType.DATA:
            return None

        # SHARED on each intermediate prefix
        for end in range(1, len(path_tuple)):
            sub = path_tuple[:end]
            err = self.symtab.try_lock(
                sub, ZLockState.SHARED, holder, self_holder=holder
            )
            if err:
                self._error(err, loc=loc)

        # EXCLUSIVE on the leaf path
        err = self.symtab.try_lock(
            path_tuple, ZLockState.EXCLUSIVE, holder, self_holder=holder
        )
        if err:
            self._error(err, loc=loc)
            return None
        return path_tuple

    def _get_dotted_chain(self, path: zast.DottedPath) -> List[str]:
        """Get the chain of variable names in a dotted path (root first)."""
        parts: List[str] = []
        node: zast.Path = path
        while node.nodetype == NodeType.DOTTEDPATH:
            node_dp = cast(zast.DottedPath, node)
            parts.append(node_dp.child.name)
            node = node_dp.parent
        if node.nodetype == NodeType.ATOMID:
            parts.append(cast(zast.AtomId, node).name)
        parts.reverse()
        return parts

    def _lock_receiver(self, callable_path: zast.Path) -> None:
        """Lock the receiver of a method call (dotted chain on the callable).

        Builds the receiver path (everything to the left of the method name)
        and locks SHARED on each intermediate plus EXCLUSIVE on the leaf.
        Locks go in the call scope and vanish when the scope is popped.
        """
        if callable_path.nodetype != NodeType.DOTTEDPATH:
            return
        # the receiver is the parent path; the dotted child is the method name
        receiver = cast(zast.DottedPath, callable_path).parent
        receiver_path = self._get_dotted_path_tuple(cast(zast.Operation, receiver))
        if not receiver_path:
            return
        root = receiver_path[0]
        if root in self.program.units:
            return
        root_var = self.symtab.lookup_var(root)
        if not root_var or root_var.ztype.typetype == ZTypeType.DATA:
            return
        # SHARED on each intermediate prefix
        holder = self._current_call_holder()
        for end in range(1, len(receiver_path)):
            sub = receiver_path[:end]
            err = self.symtab.try_lock(
                sub, ZLockState.SHARED, holder, self_holder=holder
            )
            if err:
                self._error(err, loc=callable_path.start)
        # EXCLUSIVE on the leaf
        err = self.symtab.try_lock(
            receiver_path, ZLockState.EXCLUSIVE, holder, self_holder=holder
        )
        if err:
            self._error(err, loc=callable_path.start)

    def _get_simple_var_source(self, value: zast.ExpressionSubTypes) -> Optional[str]:
        """If `value` is a plain variable reference (no call, dotted path,
        .borrow, .take, etc.), return that variable's name. Otherwise return
        None.

        Used by the borrowed-valtype copy check to detect `y: x` style copies
        from a borrowed source. Calls, constructors and field accesses produce
        fresh values and are not flagged.
        """
        inner: zast.Node = value
        if inner.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, inner).expression
        if inner.nodetype == NodeType.ATOMID:
            atom = cast(zast.AtomId, inner)
            if not _is_numeric_id(atom.name):
                return atom.name
        return None

    def _get_bare_atom_name(self, op: zast.Operation) -> Optional[str]:
        """Return the variable name when ``op`` is a bare AtomId reference
        (optionally wrapped in an Expression). Returns ``None`` for dotted
        paths, calls, projections, or any other compound expression — used
        when the caller cares about an exact identity match (e.g. "is the
        return value the parameter itself, untransformed?")."""
        if op.nodetype == NodeType.ATOMID:
            atom = cast(zast.AtomId, op)
            if not _is_numeric_id(atom.name):
                return atom.name
            return None
        if op.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, op).expression
            if inner.nodetype == NodeType.ATOMID:
                return self._get_bare_atom_name(cast(zast.Operation, inner))
            if inner.nodetype == NodeType.EXPRESSION:
                return self._get_bare_atom_name(cast(zast.Operation, inner))
        return None

    def _get_arg_root_name(self, op: zast.Operation) -> Optional[str]:
        """Get the root variable name from an operation (for aliasing checks)."""
        if op.nodetype == NodeType.ATOMID:
            op_atom = cast(zast.AtomId, op)
            if not _is_numeric_id(op_atom.name):
                return op_atom.name
        elif op.nodetype == NodeType.DOTTEDPATH:
            root: zast.Path = cast(zast.Path, op)
            while root.nodetype == NodeType.DOTTEDPATH:
                root = cast(zast.DottedPath, root).parent
            if root.nodetype == NodeType.ATOMID:
                return cast(zast.AtomId, root).name
        elif op.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, op).expression
            if inner.nodetype in (
                NodeType.BINOP,
                NodeType.DOTTEDPATH,
                NodeType.ATOMID,
                NodeType.ATOMSTRING,
                NodeType.EXPRESSION,
                NodeType.NAMEDOPERATION,
                NodeType.LABELVALUE,
            ):
                return self._get_arg_root_name(cast(zast.Operation, inner))
        return None

    def _get_dotted_path_tuple(self, op: zast.Operation) -> Optional[Tuple[str, ...]]:
        """Build the addressable path tuple from an operation source.

        Returns `(root, f1, f2, ...)` for `root.f1.f2`-style sources,
        or `(name,)` for a bare name. Returns None for temp expressions
        (matches `_get_arg_root_name` semantics — temps have no
        bindable storage to lock).
        """
        if op.nodetype == NodeType.ATOMID:
            op_atom = cast(zast.AtomId, op)
            if not _is_numeric_id(op_atom.name):
                return (op_atom.name,)
            return None
        if op.nodetype == NodeType.DOTTEDPATH:
            parts: List[str] = []
            node: zast.Path = cast(zast.Path, op)
            while node.nodetype == NodeType.DOTTEDPATH:
                node_dp = cast(zast.DottedPath, node)
                parts.append(node_dp.child.name)
                node = node_dp.parent
            if node.nodetype == NodeType.ATOMID:
                root_name = cast(zast.AtomId, node).name
                if _is_numeric_id(root_name):
                    return None
                parts.append(root_name)
                parts.reverse()
                return tuple(parts)
            return None
        if op.nodetype == NodeType.EXPRESSION:
            inner = cast(zast.Expression, op).expression
            if inner.nodetype in (
                NodeType.BINOP,
                NodeType.DOTTEDPATH,
                NodeType.ATOMID,
                NodeType.ATOMSTRING,
                NodeType.EXPRESSION,
                NodeType.NAMEDOPERATION,
                NodeType.LABELVALUE,
            ):
                return self._get_dotted_path_tuple(cast(zast.Operation, inner))
        return None

    def _alias_target(self, expr: zast.Expression) -> Optional[str]:
        """Return the zerolang-level path string to alias for this RHS, or None.

        Aliasing is safe only when the source slot is stable for the binding's
        lifetime (borrow-locked or take-invalidated) AND accessing the source
        does not dereference a reftype pointer at any intermediate step
        (which would silently turn a single register load into N memory loads).

        Eligibility:
        - Bare name (any type) -> "name"
        - Dotted path with valtype parents only -> "r.f.g"
        - Above with inline .take / .borrow suffix -> unwrap, recurse
        - Anything else -> None
        """
        inner = expr.expression if expr.nodetype == NodeType.EXPRESSION else expr
        return self._alias_target_inner(cast(zast.Operation, inner))

    def _alias_target_inner(self, op: zast.Operation) -> Optional[str]:
        nt = op.nodetype
        if nt == NodeType.ATOMID:
            atom = cast(zast.AtomId, op)
            if _is_numeric_id(atom.name):
                return None
            # Must be a runtime variable (type is set by _check_expression).
            # We do not lookup_var here because .take on the path may have
            # already invalidated the source — we still want to alias to
            # that source's storage (the source slot persists until its
            # enclosing scope ends; the alias just names it).
            atom_t = self.typing.node_type.get(atom.nodeid)
            if atom_t is None:
                return None
            # Reject names that resolve to types, functions, data, or
            # constants (we want local/param variables only).
            if atom_t.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
            ):
                return None
            # A bare class/record *type* name (not an instance) — reject.
            if atom_t.name == atom.name and atom_t.typetype in (
                ZTypeType.CLASS,
                ZTypeType.RECORD,
                ZTypeType.UNION,
                ZTypeType.VARIANT,
                ZTypeType.PROTOCOL,
                ZTypeType.FACET,
            ):
                return None
            return atom.name
        if nt == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, op)
            child_name = dp.child.name
            if child_name in ("take", "borrow"):
                # unwrap: alias applies to the underlying path
                return self._alias_target_inner(cast(zast.Operation, dp.parent))
            if child_name in ("release", "lock", "StringView", "ListView"):
                # compiler methods: not a plain path reference
                return None
            # field access — the parent must be a valtype (struct-field
            # addressing is free). Reftype pointer hops are rejected so the
            # programmer's "pin in a register" intent is preserved.
            parent_type = self.typing.node_type.get(dp.parent.nodeid)
            if parent_type is None or not _is_valtype(parent_type):
                return None
            # The child must be a real data field of the parent type, not a
            # method/protocol/facet label or a compiler-special resolution.
            # Protocol/facet/typedef subtype construction (e.g., f.myreader)
            # would not have child_name in parent_type.children.
            child = self.typing.child_of(parent_type, child_name)
            if child is None:
                return None
            # Methods and protocol/facet labels are not data fields.
            if child.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.PROTOCOL,
                ZTypeType.FACET,
            ):
                return None
            if self.typing.dp_parent_tagged_type.get(dp.nodeid) is not None:
                return None
            parent_path = self._alias_target_inner(cast(zast.Operation, dp.parent))
            if parent_path is None:
                return None
            return f"{parent_path}.{child_name}"
        return None

    def _reject_borrow_escape_into_record(self, call: zast.Call) -> None:
        """Reject borrowed-local arguments flowing into a record constructor.

        A record instance may outlive its constructor's scope (returned,
        stored, etc.). Putting a borrow into a field that can escape lets
        the borrow outlive its source, so reject at construction.
        """
        for arg in call.arguments:
            arg_root = self._get_arg_root_name(arg.valtype)
            if not arg_root:
                continue
            var = self.symtab.lookup_var(arg_root)
            if var and var.borrow_origin is not None:
                self._error(
                    f"Cannot store borrowed value '{arg_root}' in a record "
                    f"field; it borrows from local '{var.borrow_origin}' "
                    f"which may die before the record. Use '.create' for an "
                    f"owned value.",
                    loc=arg.start,
                    err=ERR.OWNERERROR,
                )

    def _check_aggregate_lock_escape(self, call: zast.Call, callee_type: ZType) -> None:
        """Reject arguments carrying outstanding locks from escaping into an
        aggregate constructor call. Generalises the V2 view-field rule:
        any value that carries (or would install) a lock cannot be stored
        into a class field unless the matching parameter is
        `.lock`-annotated (in which case the lock transfers with the value).

        Scoped to bare-name class construction. Invocations of the form
        `Class.borrow from: ...` / `Class.take from: ...` / `Class.lock
        from: ...` share the CLASS_CREATE callkind but are ownership-
        transfer operations, not storage into a fresh aggregate; skip them.

        Three cases caught:
          1. Lock-bearing method projection in arg position
             (`b.byteview`, `s.stringview`, `xs.listview`, user methods
             with a `.lock` receiver): a method whose return ownership
             is BORROW carries a lock on its source.
          2. Borrow-holder arg — `var.borrow_origin` is set, meaning `var`
             binds a borrow whose EXCLUSIVE lock lives on the source path
             (not on the holder's name).
          3. Pre-locked source path — `is_path_locked` finds a prefix-
             overlapping lock held under some prior holder.
        """
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_call = cast(zast.DottedPath, call.callable)
            if dp_call.child.name in ("borrow", "take", "lock"):
                return
        lock_param_names = {
            k
            for k, v in callee_type.param_ownership.items()
            if v == ZParamOwnership.LOCK
        }
        for arg in call.arguments:
            if arg.name and arg.name in lock_param_names:
                continue
            # Case 1: lock-bearing method projection — a borrow-returning
            # method (which by validation must have a `.lock` parameter)
            # produces a value whose lock lives on its source path.
            if arg.valtype.nodetype == NodeType.DOTTEDPATH:
                dp = cast(zast.DottedPath, arg.valtype)
                parent_type = self.typing.node_type.get(dp.parent.nodeid)
                method_type = (
                    self.typing.child_of(parent_type, dp.child.name)
                    if parent_type is not None
                    else None
                )
                has_metadata_lock = (
                    method_type is not None
                    and method_type.typetype == ZTypeType.FUNCTION
                    and method_type.return_ownership == ZParamOwnership.BORROW
                )
                if has_metadata_lock:
                    src_path = self._get_dotted_path_tuple(dp.parent)
                    src_name = ".".join(src_path) if src_path else "<expr>"
                    self._error(
                        f"cannot store lock-carrying value in aggregate "
                        f"field: '.{dp.child.name}' on '{src_name}' "
                        f"produces a value locked to its source",
                        loc=arg.start,
                        err=ERR.OWNERERROR,
                        hint=(
                            "copy the borrowed value (e.g. `.string` / "
                            "`.list` / `.bytes`) before storing, or release "
                            "the lock first"
                        ),
                    )
                    continue
            arg_path = self._get_dotted_path_tuple(arg.valtype)
            if not arg_path:
                continue
            # Case 2: borrow-holder variable (lock lives on source path)
            arg_root_var = self.symtab.lookup_var(arg_path[0])
            if arg_root_var is not None and arg_root_var.borrow_origin is not None:
                self._error(
                    f"cannot store lock-carrying value in aggregate field: "
                    f"'{arg_path[0]}' borrows from "
                    f"'{arg_root_var.borrow_origin}'",
                    loc=arg.start,
                    err=ERR.OWNERERROR,
                    hint=(
                        "copy the borrowed value (e.g. `.string` / `.list` "
                        "/ `.bytes`) before storing, or release the lock first"
                    ),
                )
                continue
            # Case 2b: default-borrowed parameter (Phase A) being stored
            # into an owned aggregate field. The param is pointer-passed
            # so the field would alias the caller's storage; the caller
            # still owns it. Reject; user picks `.copy` (clone), `.take`
            # on the param (transfer ownership in), or `.lock` on the
            # field (intentional borrow holder). Exemptions:
            #   - `.copy` projection in arg position: produces a fresh
            #     owned value, breaks the borrow chain.
            #   - `.lock`-annotated params: user explicitly opted into
            #     a lock-carrying value and may legitimately store
            #     `.private` projections of it into matching `.lock`
            #     fields (the borrowed_record / listiter pattern).
            arg_breaks_borrow = (
                arg.valtype.nodetype == NodeType.DOTTEDPATH
                and cast(zast.DottedPath, arg.valtype).child.name == "copy"
            )
            param_own = self.func_ctx.func_ownership.get(arg_path[0])
            # Only fire when the source actually has heap-backed data that
            # would be aliased: string itself, or a struct holding string /
            # other heap-backed fields. A class with only valtype fields
            # is safe to memcpy — no aliasing concern.
            if (
                arg_root_var is not None
                and not arg_breaks_borrow
                and param_own != ZParamOwnership.LOCK
                and arg_root_var.ownership == ZOwnership.BORROWED
                and arg_root_var.borrow_origin is None
                and (
                    arg_root_var.ztype.subtype == ZSubType.STRING
                    or arg_root_var.ztype.needs_field_cleanup
                )
            ):
                self._error(
                    f"cannot store borrowed value '{arg_path[0]}' in "
                    f"aggregate field: the caller still owns it and "
                    f"the field would alias the same heap data.",
                    loc=arg.start,
                    err=ERR.OWNERERROR,
                    hint=(
                        f"use `{arg_path[0]}.copy` to clone, declare the "
                        f"parameter as `{arg_path[0]}: <T>.take` to "
                        "transfer ownership in, or declare the field's "
                        "constructor parameter as `.lock` to hold a borrow"
                    ),
                )
                continue
            # Case 3: pre-locked source path
            info = self.symtab.is_path_locked(arg_path)
            if info is None:
                continue
            self._error(
                f"cannot store lock-carrying value in aggregate field: "
                f"'{arg_path[0]}' holds a lock on "
                f"'{'.'.join(info.path)}' (held by '{info.holder}')",
                loc=arg.start,
                err=ERR.OWNERERROR,
                hint=(
                    "copy the borrowed value (e.g. `.string` / `.list` / "
                    "`.bytes`) before storing, or release the lock first"
                ),
            )

    def _apply_take_to_arg(self, arg: zast.NamedOperation, pname: str) -> None:
        """Apply TAKE semantics to a call argument: reject a borrowed source,
        otherwise release its held locks and invalidate its root name.

        Used by the standard call-ownership loop and by constructor-style
        dispatch paths (`Type.create`, `box from:`, typedef `.create`).
        Both paths now hoist non-trivial args into a synth `_tN` first
        (via `_hoist_arg`), so by the time this runs, `arg.valtype` is
        either a bare AtomId (variable, hoisted temp, or literal) or a
        trivial expression — never a raw method-call DottedPath.
        """
        arg_root = self._get_arg_root_name(arg.valtype)
        if not arg_root:
            return
        var = self.symtab.lookup_var(arg_root)
        if var and var.ownership == ZOwnership.BORROWED:
            self._error(
                f"Cannot pass borrowed variable '{arg_root}' to "
                f"'take' parameter '{pname}'",
                loc=arg.start,
                err=ERR.OWNERERROR,
            )
            return
        self.symtab.release_held_locks(arg_root)
        take_loc = (
            (arg.start.lineno, arg.start.colno, arg.start.fsno) if arg.start else None
        )
        self.symtab.invalidate(arg_root, loc=take_loc)

    def _check_protocol_create(
        self, proto_type: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Check protocol/facet.create from: expr — owned creation.

        Accepts either the explicit `proto.create from: expr` form or the
        bare-name shorthand `proto expr` (single positional argument), which
        routes through the unified call dispatch and is equivalent.
        """
        kind = "facet" if proto_type.typetype == ZTypeType.FACET else "protocol"
        # find the from: argument. Also accept a single positional argument
        # (bare-name construction `proto obj` ≡ `proto.create from: obj`).
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if (
            from_arg is None
            and len(call.arguments) == 1
            and call.arguments[0].name is None
        ):
            from_arg = call.arguments[0]
        if not from_arg:
            self._error(f"{kind}.create requires 'from:' argument", loc=call.start)
            return None

        # type-check the from: argument
        arg_result = self._check_operation(from_arg.valtype)
        arg_type = arg_result.ztype
        if not arg_type:
            return None

        # verify conformance: arg_type must conform to this protocol/facet
        labels = self._protocol_labels.get(arg_type.name, [])
        found_label = None
        for label, pt in labels:
            if pt.name == proto_type.name:
                found_label = label
                break
        if not found_label:
            # when the source is a boxed conformer, steer the user to the
            # direct form: .create already heap-allocates internally, so
            # box+create composition is unnecessary.
            hint = None
            if arg_type.is_box:
                inner = self.typing.generic_arg_of(arg_type, "t")
                inner_name = inner.name if inner else "the inner value"
                inner_labels = (
                    self._protocol_labels.get(inner.name, []) if inner else []
                )
                if any(pt.name == proto_type.name for _, pt in inner_labels):
                    hint = (
                        f"{proto_type.name}.create already heap-allocates "
                        f"internally — pass {inner_name} directly instead "
                        f"of boxing first"
                    )
            self._error(
                f"Type '{arg_type.name}' does not conform to {kind} "
                f"'{proto_type.name}'",
                loc=call.start,
                hint=hint,
            )
            return None

        # `.create` for a protocol takes ownership (move); for a facet it
        # copies the value into inline storage (no ownership change). This
        # dispatch path bypasses the standard call-ownership loop, so apply
        # the declared `from:` param ownership here. Hoist non-trivial args
        # into a synth temp first (mirroring _check_call) so TAKE-application
        # operates on a bare AtomId — unifies the constructor path with the
        # standard call path.
        create_fn = self.typing.child_of(proto_type, "create")
        own = create_fn.param_ownership.get("from") if create_fn is not None else None
        if own == ZParamOwnership.TAKE:
            arg_borrow_path = arg_result.borrow_target
            if not self._arg_is_trivial(from_arg):
                self._hoist_arg(from_arg, arg_type, arg_borrow_path)
            self._apply_take_to_arg(from_arg, "from")

        self.typing.node_type[call.nodeid] = proto_type
        return proto_type

    def _check_protocol_borrow(
        self, proto_type: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Check protocol/facet.borrow from: expr — borrowed creation."""
        kind = "facet" if proto_type.typetype == ZTypeType.FACET else "protocol"
        # find the from: argument
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if not from_arg:
            self._error(f"{kind}.borrow requires 'from:' argument", loc=call.start)
            return None

        # type-check the from: argument. If the arg is `.lock` / `.borrow`,
        # the lifted SOURCE path (e.g. `(m,)` for `m.lock`) comes back on
        # `arg_result.borrow_target`.
        arg_result = self._check_operation(from_arg.valtype)
        arg_type = arg_result.ztype
        source_from_lift = arg_result.borrow_target
        if not arg_type:
            return None

        # verify conformance: arg_type must conform to this protocol/facet
        labels = self._protocol_labels.get(arg_type.name, [])
        found_label = None
        for label, pt in labels:
            if pt.name == proto_type.name:
                found_label = label
                break
        if not found_label:
            self._error(
                f"Type '{arg_type.name}' does not conform to {kind} "
                f"'{proto_type.name}'",
                loc=call.start,
            )
            return None

        # set borrow lock on the source path: prefer the lifted path
        # (`m.lock` → `(m,)`) over the raw arg syntax (which would include
        # the `.lock` suffix as a pseudo-field).
        src_path = source_from_lift
        if src_path is None:
            src_path = self._get_dotted_path_tuple(from_arg.valtype)
        if src_path:
            self._pending_borrow_lock = src_path

        self.typing.node_type[call.nodeid] = proto_type
        return proto_type

    def _check_typedef_create(
        self, typedef_type: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Check typedef.create from: expr — owned typedef creation.

        Also accepts a single positional argument for bare-name construction
        (`mytypedef obj` ≡ `mytypedef.create from: obj`).
        """
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if not from_arg:
            # positional argument
            if call.arguments:
                from_arg = call.arguments[0]
        if not from_arg:
            self._error("typedef.create requires 'from:' argument", loc=call.start)
            return None

        arg_result = self._check_operation(from_arg.valtype)
        arg_type = arg_result.ztype
        if not arg_type:
            return None

        # verify: arg_type must be compatible with the typedef's base type
        base = typedef_type.typedef_base
        if base and not self._types_compatible(base, arg_type):
            self._error(
                f"Type '{arg_type.name}' is not compatible with typedef base type "
                f"'{base.name}'",
                loc=call.start,
            )
            return None

        # `.create` takes ownership of the source. Hoist non-trivial args
        # into a synth temp first (mirroring _check_call) so TAKE-application
        # operates on a bare AtomId.
        arg_borrow_path = arg_result.borrow_target
        if not self._arg_is_trivial(from_arg):
            self._hoist_arg(from_arg, arg_type, arg_borrow_path)
        self._apply_take_to_arg(from_arg, "from")

        self.typing.node_type[call.nodeid] = typedef_type
        return typedef_type

    def _check_typedef_borrow(
        self, typedef_type: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Check typedef.borrow from: expr — borrowed typedef creation."""
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if not from_arg:
            if call.arguments:
                from_arg = call.arguments[0]
        if not from_arg:
            self._error("typedef.borrow requires 'from:' argument", loc=call.start)
            return None

        arg_result = self._check_operation(from_arg.valtype)
        arg_type = arg_result.ztype
        source_from_lift = arg_result.borrow_target
        if not arg_type:
            return None

        base = typedef_type.typedef_base
        if base and not self._types_compatible(base, arg_type):
            self._error(
                f"Type '{arg_type.name}' is not compatible with typedef base type "
                f"'{base.name}'",
                loc=call.start,
            )
            return None

        src_path = source_from_lift
        if src_path is None:
            src_path = self._get_dotted_path_tuple(from_arg.valtype)
        if src_path:
            self._pending_borrow_lock = src_path

        self.typing.node_type[call.nodeid] = typedef_type
        return typedef_type

    def _check_return_call(self, call: zast.Call) -> Optional[ZType]:
        """Check a return statement: verify return value matches function return type."""
        # Detect return-construction shorthand: `return Type field1: x ...`
        # parses with Type as args[0] (a bare AtomId path) and the fields as
        # remaining args. The emitter folds this into meta.create. Under the
        # unified dispatch rule this must route through children["create"]:
        # if we're inside the type's own 'create' body, it is recursion and
        # the user must use `return meta.create field1: x ...` instead.
        if (
            len(call.arguments) >= 1
            and call.arguments[0].name is None
            and call.arguments[0].valtype.nodetype == NodeType.ATOMID
            and any(a.name is not None for a in call.arguments[1:])
            and self.func_ctx.body
        ):
            first = cast(zast.AtomId, call.arguments[0].valtype)
            # only do the recursion check when the first arg refers to a
            # user-defined type (has a resolved children["create"]).
            type_ref = self._resolve_name(first.name)
            if (
                type_ref is not None
                and type_ref.typetype
                in (ZTypeType.RECORD, ZTypeType.CLASS, ZTypeType.VARIANT)
                and self.typing.child_of(type_ref, "create") is self.func_ctx.body[-1]
            ):
                self._error(
                    f"cannot construct '{first.name}' inside '{first.name}.create' "
                    f"via the return shorthand — this would call the constructor "
                    f"recursively. Use 'return meta.create {{fields}}' for the "
                    f"raw allocator.",
                    loc=call.start,
                    err=ERR.CALLERROR,
                )
                return self._resolve_name("never") or self.t_null

        # Detect `return Type.create field1: x ...` — args[0] is a DottedPath
        # pointing at the type's create function, and if we're inside that
        # very function this is explicit recursion.
        if (
            len(call.arguments) >= 1
            and call.arguments[0].name is None
            and call.arguments[0].valtype.nodetype == NodeType.DOTTEDPATH
            and self.func_ctx.body
        ):
            dp_first = cast(zast.DottedPath, call.arguments[0].valtype)
            if (
                dp_first.child.name == "create"
                and dp_first.parent.nodetype == NodeType.ATOMID
            ):
                type_name = cast(zast.AtomId, dp_first.parent).name
                type_ref = self._resolve_name(type_name)
                if (
                    type_ref is not None
                    and self.typing.child_of(type_ref, "create")
                    is self.func_ctx.body[-1]
                ):
                    self._error(
                        f"cannot call '{type_name}.create' recursively (directly "
                        f"or via bare-name). Use 'meta.create' for the raw "
                        f"allocator.",
                        loc=call.start,
                        err=ERR.CALLERROR,
                    )
                    return self._resolve_name("never") or self.t_null

        # Detect return-construction shorthand with meta.create:
        # `return meta.create field1: x ...` inside a type's method body
        # resolves to the enclosing type's :meta.create raw allocator. The
        # compiler-internal meta.create returns the enclosing type, so the
        # return-type check must see that type rather than the function type.
        if (
            len(call.arguments) >= 1
            and call.arguments[0].name is None
            and call.arguments[0].valtype.nodetype == NodeType.DOTTEDPATH
        ):
            dp = cast(zast.DottedPath, call.arguments[0].valtype)
            if (
                dp.parent.nodetype == NodeType.ATOMID
                and cast(zast.AtomId, dp.parent).name == "meta"
                and dp.child.name == "create"
                and self.func_ctx.enclosing_type
            ):
                enclosing = self.func_ctx.enclosing_type[-1]
                # validate the field args by type (no missing-field check yet
                # — that goes through the meta-create signature in Phase 4)
                for a in call.arguments[1:]:
                    self._check_operation(a.valtype)
                self.typing.node_type[call.arguments[0].valtype.nodeid] = enclosing
                ret_type_meta: Optional[ZType] = enclosing
                if self.func_ctx.return_type and ret_type_meta:
                    if not self._types_compatible(
                        ret_type_meta, self.func_ctx.return_type
                    ):
                        self._error(
                            f"return type mismatch: function expects "
                            f"{self.func_ctx.return_type.name}, got "
                            f"{ret_type_meta.name}",
                            loc=call.start,
                            err=ERR.TYPEERROR,
                        )
                never_meta = self._resolve_name("never")
                self.typing.node_type[call.nodeid] = (
                    never_meta if never_meta else self.t_null
                )
                return self.typing.node_type.get(call.nodeid)

        # type-check the return expression (first argument)
        ret_type = None
        inline_borrow_src: Optional[Tuple[str, ...]] = None
        if call.arguments:
            ret_result = self._check_operation(call.arguments[0].valtype)
            ret_type = ret_result.ztype
            # G2: capture any lock source installed by an inline projection
            # (.stringview / .listview / .borrow) in the return expression.
            inline_borrow_src = ret_result.borrow_target
            # Return-construction shorthand: `return Type field: val ...`
            # parses as a Call with the type as args[0] (no name) and the
            # fields as named args[1:]. The emitter folds these via
            # _build_create_args. Without visiting them here, nested paths
            # (e.g. `n.copy`) never get their `.type` stamped, so per-method
            # emit dispatches gated on `parent.type` silently fall through.
            # Also runs the aggregate lock-escape check so storing a
            # borrowed param into an owned field is rejected here, not
            # later as a gcc signature mismatch.
            if (
                len(call.arguments) >= 2
                and call.arguments[0].name is None
                and call.arguments[0].valtype.nodetype == NodeType.ATOMID
                and any(a.name is not None for a in call.arguments[1:])
            ):
                for a in call.arguments[1:]:
                    self._check_operation(a.valtype)
                # Run the aggregate lock-escape check so storing a
                # borrowed param into an owned field is rejected here,
                # not later as a gcc signature mismatch. The check
                # iterates `call.arguments`; args[0] is the bare type
                # name (no name, no symtab var) so all cases skip it.
                if ret_type is not None and ret_type.typetype in (
                    ZTypeType.CLASS,
                    ZTypeType.RECORD,
                ):
                    self._check_aggregate_lock_escape(call, ret_type)

        if self.func_ctx.return_type and ret_type:
            if not self._types_compatible(ret_type, self.func_ctx.return_type):
                self._error(
                    f"return type mismatch: function expects "
                    f"{self.func_ctx.return_type.name}, got {ret_type.name}",
                    loc=call.start,
                    err=ERR.TYPEERROR,
                )

        # G2 lock-escape: returning a value backed by an outstanding lock is
        # only legal when the lock source is supplied by the caller (a
        # `.lock` / `.borrow`-annotated parameter, or a default-borrowed
        # reftype parameter such as a method `this`). A view over a
        # function-local source would outlive its source at function exit.
        # Catches `return local.stringview` and similar inline projections.
        if inline_borrow_src:
            src_root = inline_borrow_src[0]
            src_var = self.symtab.lookup_var(src_root)
            src_is_owned_local = (
                src_var is not None and src_var.ownership == ZOwnership.OWNED
            )
            if src_is_owned_local:
                self._error(
                    f"cannot return lock-carrying value: source '{src_root}' "
                    f"is a function-local, not a parameter supplied by the "
                    f"caller. The borrow would outlive its source at function "
                    f"exit.",
                    loc=call.start,
                    err=ERR.OWNERERROR,
                    hint=(
                        "copy to an owned value (e.g. `.string` / `.list` / "
                        "`.bytes`) before returning, or accept the source as "
                        "a `.lock` / `.borrow` parameter so the lock "
                        "transfers to the caller"
                    ),
                )

        # ownership check: a borrowed return must trace back to a `.lock`
        # parameter. Two sub-cases:
        #   * returned root IS a parameter — that parameter must be `.lock`
        #     (default `.borrow` and `.take` are both rejected; their locks
        #     don't survive the call).
        #   * returned root is a local owned variable — its lifetime ends
        #     at function exit; borrow would dangle.
        ret_own = self.func_ctx.func_return_ownership
        if ret_own == ZParamOwnership.BORROW and call.arguments:
            arg_op = call.arguments[0].valtype
            arg_name = self._get_arg_root_name(arg_op)
            if arg_name:
                var = self.symtab.lookup_var(arg_name)
                param_own = self.func_ctx.func_ownership.get(arg_name)
                if param_own is not None and param_own != ZParamOwnership.LOCK:
                    self._error(
                        f"Cannot return parameter '{arg_name}' as borrowed: "
                        f"the parameter is not declared '.lock'",
                        loc=call.start,
                        err=ERR.OWNERERROR,
                        hint=(
                            f"declare '{arg_name}' as '.lock' so its lock "
                            f"transfers to the returned borrow"
                        ),
                    )
                elif (
                    param_own is None
                    and var is not None
                    and var.ownership == ZOwnership.OWNED
                ):
                    self._error(
                        f"Cannot return local variable '{arg_name}' as borrowed; "
                        f"borrowed return values must originate from a 'lock' parameter",
                        loc=call.start,
                    )

        # escape check: a borrowed local cannot be returned. `borrow_origin`
        # marks variables that borrow from a function-local source; returning
        # such a variable would outlive its source.
        if call.arguments:
            arg_op = call.arguments[0].valtype
            arg_name = self._get_arg_root_name(arg_op)
            if arg_name:
                var = self.symtab.lookup_var(arg_name)
                if var and var.borrow_origin is not None:
                    self._error(
                        f"Cannot return borrowed value '{arg_name}'; "
                        f"it borrows from local '{var.borrow_origin}' which "
                        f"dies at function exit. Use '.create' for an owned "
                        f"value that can escape.",
                        loc=call.start,
                        err=ERR.OWNERERROR,
                    )

        # Phase B: returning a borrowed parameter as owned aliases the
        # caller's data — the caller still owns it and the returned value
        # would carry a duplicate reference to the same heap buffer (silent
        # double-free / use-after-free). Reject for stack-reftypes; users
        # must opt into ownership transfer (`.take` on the param), an owned
        # duplicate (`.copy` on the value), or a borrow return (`out
        # T.borrow` paired with a `.lock` parameter).
        if (
            self.func_ctx.func_return_ownership != ZParamOwnership.BORROW
            and call.arguments
            and ret_type is not None
            and ret_type.needs_destructor
            and not ret_type.is_heap_allocated
        ):
            arg_op = call.arguments[0].valtype
            bare_name = self._get_bare_atom_name(arg_op)
            if bare_name is not None:
                var = self.symtab.lookup_var(bare_name)
                param_own = self.func_ctx.func_ownership.get(bare_name)
                if (
                    var is not None
                    and var.ownership == ZOwnership.BORROWED
                    and var.borrow_origin is None
                    and param_own in (None, ZParamOwnership.BORROW)
                ):
                    self._error(
                        f"Cannot return borrowed parameter '{bare_name}': "
                        "the caller still owns it and would receive a "
                        "duplicate reference to the same heap data.",
                        loc=call.start,
                        err=ERR.OWNERERROR,
                        hint=(
                            f"use `{bare_name}.copy` to return an owned "
                            f"duplicate, declare the parameter as "
                            f"`{bare_name}: <T>.take` to transfer ownership "
                            "in, or declare the function as `out <T>.borrow` "
                            "with a `.lock` parameter to return a borrow"
                        ),
                    )

        # return has type 'never' (control flow doesn't continue)
        never = self._resolve_name("never")
        self.typing.node_type[call.nodeid] = never if never else self.t_null
        return self.typing.node_type.get(call.nodeid)

    def _check_str_convert_call(self, call: zast.Call) -> Optional[ZType]:
        """Check a .str conversion call: string.str to: N or str.str to: N."""
        # find the to: argument
        to_arg = None
        for arg in call.arguments:
            if arg.name == "to":
                to_arg = arg
                break
        if to_arg is None:
            # positional: first argument is the capacity
            if call.arguments:
                to_arg = call.arguments[0]
            else:
                self._error(
                    ".str requires a 'to:' capacity argument",
                    loc=call.start,
                )
                return None
        # resolve the numeric value
        to_type = self._resolve_numeric_generic_arg(
            to_arg.valtype, "u64", loc=call.start
        )
        if not to_type:
            return None
        # find the str template and monomorphize
        str_template = self._resolve_name("str")
        if not str_template or not str_template.isgeneric:
            self._error("str type not found", loc=call.start)
            return None
        defn = self._find_generic_defn(str_template)
        if not defn:
            return None
        mono = self._monomorphize(str_template, {"to": to_type}, defn)
        if not mono:
            return None
        self.typing.node_type[call.nodeid] = mono
        return mono

    @staticmethod
    def _fold_binop(
        op: str, lhs: "int | float", rhs: "int | float"
    ) -> Optional[object]:
        """Evaluate a binary operation on constant values at compile time.

        Returns int/float for arithmetic, bool for comparisons, None if not foldable.
        Uses _FOLD_OPS registry for dispatch.
        """
        fn = _FOLD_OPS.get(op)
        if fn is not None:
            return fn(lhs, rhs)
        return None

    def _check_binop(self, binop: zast.BinOp) -> Optional[ZType]:
        """Type-check a binary operation. Thin wrapper around
        `_check_binop_inner` that builds the typed mirror on exit."""
        t = self._check_binop_inner(binop)
        return t

    def _check_binop_inner(self, binop: zast.BinOp) -> Optional[ZType]:
        lhs_type = self._check_operation(binop.lhs).ztype
        rhs_type = self._check_path(binop.rhs).ztype
        if not lhs_type or not rhs_type:
            return None

        # look up operator as method on lhs type (fall through typedef base)
        op_name = binop.operator.name
        lookup_type = lhs_type
        if not self.typing.child_of(lookup_type, op_name) and lookup_type.typedef_base:
            lookup_type = lookup_type.typedef_base
        method = self.typing.child_of(lookup_type, op_name)
        if method and method.typetype == ZTypeType.FUNCTION:
            ret = method.return_type
            if ret:
                self.typing.node_type[binop.nodeid] = ret
                # constant folding: evaluate when both operands are constant integers
                lhs_cv = self.typing.node_const_value.get(binop.lhs.nodeid)
                rhs_cv = self.typing.node_const_value.get(binop.rhs.nodeid)
                if (
                    lhs_cv is not None
                    and rhs_cv is not None
                    and type(lhs_cv) in (int, float)
                    and type(rhs_cv) in (int, float)
                ):
                    # division by zero: compile-time error
                    if op_name == "/" and rhs_cv == 0:
                        self._error(
                            "division by zero in constant expression",
                            loc=binop.start,
                        )
                        return ret
                    # skip float folding for f32 (precision mismatch with host)
                    if (
                        type(lhs_cv) is float or type(rhs_cv) is float
                    ) and ret.name not in ("f64", "bool"):
                        pass  # f32/f128: do not fold
                    else:
                        folded = self._fold_binop(op_name, lhs_cv, rhs_cv)  # type: ignore[arg-type]
                        if folded is not None and type(folded) is int:
                            # overflow check for integer results
                            rng = NUMERIC_RANGES.get(ret.name)
                            if rng:
                                lo, hi = rng
                                if folded < lo or folded > hi:
                                    self._error(
                                        f"constant expression overflows type '{ret.name}' "
                                        f"(result: {folded}, range: {lo}..{hi})",
                                        loc=binop.start,
                                    )
                                    return ret
                            self.typing.node_const_value[binop.nodeid] = folded
                        elif folded is not None and type(folded) is float:
                            self.typing.node_const_value[binop.nodeid] = folded
                        elif folded is not None and type(folded) is bool:
                            self.typing.node_const_value[binop.nodeid] = folded
                return ret

        self._error(
            f"No operator '{op_name}' for types {lhs_type.name} and {rhs_type.name}",
            loc=binop.start,
        )
        return None

    def _extract_error_message(self, node: zast.Node) -> str:
        """Extract the string literal from an error() call's first argument.

        Returns the literal text or a generic fallback for interpolated strings.
        """
        if node.nodetype != NodeType.CALL:
            return "compile-time error"
        call = cast(zast.Call, node)
        if not call.arguments:
            return "compile-time error"
        msg_op = call.arguments[0].valtype
        if msg_op.nodetype == NodeType.ATOMSTRING:
            atom_str = cast(zast.AtomString, msg_op)
            parts: list[str] = []
            for part in atom_str.stringparts:
                if part.nodetype == NodeType.STRINGCHUNK:
                    parts.append(cast(zast.StringChunk, part).text)
                else:
                    return "compile-time error"
            return "".join(parts)
        return "compile-time error"

    # sentinel for branches that don't complete (return/break/continue).
    # Carries `is_ztype = False` so callers can use `t.is_ztype` to
    # discriminate sentinel-from-ZType without a getattr probe.
    class _NoReturnSentinel:
        is_ztype: bool = False

    _NORETURN = _NoReturnSentinel()

    def _last_statement_type(
        self, stmt: zast.Statement
    ) -> "Optional[ZType | _NoReturnSentinel]":
        """Get the type of the last expression in a statement block.

        Returns ZType for value-producing branches, _NORETURN for
        return/break/continue, or None if no value produced.
        """
        if not stmt.statements:
            return None
        last = stmt.statements[-1].statementline
        if last.nodetype == NodeType.EXPRESSION:
            last_expr = cast(zast.Expression, last)
            inner = last_expr.expression
            # check for non-completing expressions (return/break/continue/error)
            if self.typing.expr_call_kind.get(
                last_expr.nodeid, zast.CallKind.UNKNOWN
            ) in (
                zast.CallKind.RETURN,
                zast.CallKind.BREAK,
                zast.CallKind.CONTINUE,
                zast.CallKind.ERROR,
            ):
                return self._NORETURN
            # get type from the inner expression node (Expression wrapper .type may be None)
            if self.typing.node_type.get(inner.nodeid) is not None:
                return self.typing.node_type.get(inner.nodeid)
            return self.typing.node_type.get(last_expr.nodeid)
        if last.nodetype == NodeType.ASSIGNMENT:
            return self.typing.node_type.get(cast(zast.Assignment, last).nodeid)
        return None

    def _check_exhaustive_if(self, expr: zast.Expression) -> None:
        """Emit error if an if-expression is missing its else clause."""
        inner = expr.expression
        if inner.nodetype == NodeType.IF and not cast(zast.If, inner).elseclause:
            self._error(
                "if-expression is not exhaustive (missing else clause)",
                loc=inner.start,
            )

    def _check_if(self, ifnode: zast.If) -> Optional[ZType]:
        """Type-check an if-expression. Thin wrapper that builds the
        typed mirror after the inner walks the clauses + else branch."""
        t = self._check_if_inner(ifnode)
        return t

    def _check_if_inner(self, ifnode: zast.If) -> Optional[ZType]:
        if_marker = self.symtab.push_block("if")
        all_branches_diverge = True
        const_true_taken = False

        # snapshot live owned variables before arms for take-in-arm tracking
        live_before = self.symtab.get_live_owned_vars()
        # save variable info so we can restore between arms
        saved_vars: dict = {}
        for vname in live_before:
            saved_vars[vname] = (
                self.symtab.lookup_var(vname),
                self.symtab.lookup(vname),
            )
        # track which variables were taken in at least one arm
        taken_in_any_arm: set = set()

        for clause in ifnode.clauses:
            branch_marker = self.symtab.push_block("if_branch")
            for _, cond_op in clause.conditions.items():
                self._check_operation(cond_op)
            # suppress compile-time errors in constant-false branches
            all_const = all(
                self.typing.node_const_value.get(cond_op.nodeid) is not None
                for _, cond_op in clause.conditions.items()
            )
            all_false = all_const and not all(
                bool(self.typing.node_const_value.get(cond_op.nodeid))
                for _, cond_op in clause.conditions.items()
            )
            if all_false or const_true_taken:
                self._suppress_compile_error += 1
            self._check_statement(clause.statement)
            if all_false or const_true_taken:
                self._suppress_compile_error -= 1
            if all_const and not all_false and not const_true_taken:
                const_true_taken = True
            if not self.symtab.is_unreachable():
                all_branches_diverge = False

            # detect variables taken in this arm and restore for next arm
            for vname in live_before:
                if self.symtab.lookup(vname) is None:
                    taken_in_any_arm.add(vname)
                    sv, st = saved_vars[vname]
                    if sv is not None:
                        self.symtab.define_var(vname, sv)
                        self.symtab.clear_taken(vname)

            self.symtab.pop_to(branch_marker)
        if ifnode.elseclause:
            branch_marker = self.symtab.push_block("if_else")
            if const_true_taken:
                self._suppress_compile_error += 1
            self._check_statement(ifnode.elseclause)
            if const_true_taken:
                self._suppress_compile_error -= 1
            if not self.symtab.is_unreachable():
                all_branches_diverge = False

            # detect variables taken in else arm
            for vname in live_before:
                if self.symtab.lookup(vname) is None:
                    taken_in_any_arm.add(vname)

            self.symtab.pop_to(branch_marker)
        else:
            all_branches_diverge = False  # missing else = not all paths diverge

        result_type = self.t_null

        # if-as-expression: compute branch types when else clause is present
        if ifnode.elseclause:
            branch_types = []
            for clause in ifnode.clauses:
                branch_types.append(self._last_statement_type(clause.statement))
            branch_types.append(self._last_statement_type(ifnode.elseclause))

            # filter out non-completing branches (return/break/continue)
            completing = [t for t in branch_types if t is not self._NORETURN]

            if not completing:
                # all branches are non-completing (return/break/continue)
                never = self._resolve_name("never")
                if never:
                    result_type = never
                    self.typing.node_type[ifnode.nodeid] = never
            elif completing:
                first_raw = completing[0]
                if first_raw is not None and first_raw.is_ztype:
                    first = cast(ZType, first_raw)
                    all_ok = all(
                        t is not None
                        and t.is_ztype
                        and self._types_compatible(first, cast(ZType, t))
                        for t in completing[1:]
                    )
                    if all_ok:
                        result_type = first
                        self.typing.node_type[ifnode.nodeid] = first
                    else:
                        # find first incompatible type for error message
                        for t in completing[1:]:
                            if (
                                t is None
                                or not t.is_ztype
                                or not self._types_compatible(first, cast(ZType, t))
                            ):
                                tname = (
                                    cast(ZType, t).name
                                    if t is not None and t.is_ztype
                                    else "null"
                                )
                                self._error(
                                    f"incompatible branch types in if-expression: "
                                    f"'{first.name}' and '{tname}'",
                                    loc=ifnode.start,
                                )
                                break

        self.symtab.pop_to(if_marker)

        # post-if ownership: invalidate variables taken in any arm
        if taken_in_any_arm:
            for vname in taken_in_any_arm:
                _, vtype = saved_vars[vname]
                self.typing.if_taken_vars.setdefault(ifnode.nodeid, []).append(
                    (vname, vtype)
                )
                take_loc = ifnode.start
                loc_tuple = (
                    (take_loc.lineno, take_loc.colno, take_loc.fsno)
                    if take_loc
                    else None
                )
                self.symtab.invalidate(vname, loc=loc_tuple)
                if take_loc:
                    self.symtab.set_taken_location(
                        vname,
                        (take_loc.lineno, take_loc.colno, take_loc.fsno),
                    )

        # mark parent unreachable after popping the if scope
        if all_branches_diverge:
            self.symtab.mark_unreachable()
        return result_type

    # system/library units that should not be resolved as generic file units
    _SYSTEM_UNITS = {"core", "system", "io", "collections", "os", "cli"}

    def _ensure_file_unit_resolved(self, unitname: str) -> Optional[ZType]:
        """Ensure a file unit has been fully resolved (generic params detected).

        File units get bare ZTypes in __init__. This method triggers full
        resolution via _resolve_inline_unit_type on first access.
        Skips system/library units which are handled by the standard pipeline.
        """
        if unitname not in self.program.units:
            return None
        file_unit = self.program.units[unitname]
        # Id-only lookup — __init__ registers every file unit via
        # _register_unit_type, so the id cache is guaranteed to hold an
        # entry for `file_unit.nodeid` on both the already-resolved and
        # system-unit branches.
        if unitname in self._resolved_file_units or unitname in self._SYSTEM_UNITS:
            return self.unit_types_by_id.get(file_unit.nodeid)
        self._resolved_file_units.add(unitname)
        # replace the bare ZType with a fully resolved one
        utype = self._resolve_inline_unit_type(unitname, unitname, file_unit)
        return utype

    def _get_path_root_var(self, path: zast.Path) -> Optional[ZVariable]:
        """Get the ZVariable for the root of a path expression (if any)."""
        if path.nodetype == NodeType.ATOMID:
            return self.symtab.lookup_var(cast(zast.AtomId, path).name)
        if path.nodetype == NodeType.DOTTEDPATH:
            return self._get_path_root_var(cast(zast.DottedPath, path).parent)
        return None

    def _is_internal_access(self, parent_type: ZType, path: zast.DottedPath) -> bool:
        """Check if access is from inside the type definition (private access)."""
        if (
            path.parent.nodetype == NodeType.ATOMID
            and cast(zast.AtomId, path.parent).name == "this"
        ):
            return True
        for _, rtype in self._resolving:
            if rtype is parent_type or rtype.name == parent_type.name:
                return True
        return False

    def _is_non_public_access(
        self, parent_type: ZType, child_name: str, path: zast.DottedPath
    ) -> bool:
        """Check if accessing child_name on parent_type violates public access.

        Returns True if the access should be rejected (non-public external access).
        Returns False if the access is allowed.
        """
        if parent_type.public_members is None:
            return False  # no restriction (all-public default)
        if child_name in ("tag",):
            return False  # tag accessor always accessible
        if self._is_internal_access(parent_type, path):
            return False
        # friend access: variable declared with .private type bypasses restrictions
        root_var = self._get_path_root_var(path.parent)
        if root_var and root_var.is_private_access:
            return False
        # friend access via .private field: it.items.field where items is a private_field
        if path.parent.nodetype == NodeType.DOTTEDPATH:
            path_parent_dp = cast(zast.DottedPath, path.parent)
            grandparent_type = (
                self.typing.node_type.get(path_parent_dp.parent.nodeid)
                if path_parent_dp.parent
                else None
            )
            if (
                grandparent_type
                and path_parent_dp.child.name in grandparent_type.private_fields
            ):
                return False
        # external access: check public_members (keys are external names)
        return child_name not in parent_type.public_members

    def _resolve_public_name(
        self, parent_type: ZType, child_name: str, path: zast.DottedPath
    ) -> str:
        """Resolve a public external name to the internal member name.

        For renamed members (api_name: internal_name), returns the internal name.
        For non-renamed members, returns the same name.
        For internal access, returns the same name (no redirection).
        """
        if parent_type.public_members is None:
            return child_name
        if self._is_internal_access(parent_type, path):
            return child_name
        return parent_type.public_members.get(child_name, child_name)

    def _check_box_construction(
        self, call: zast.Call, box_template: ZType
    ) -> Optional[ZType]:
        """Handle box from: val construction.

        For reftype T: result is T directly (zero-cost passthrough).
        For valtype T: result is monomorphized box(T) reftype.
        """
        # find the from: argument (or first positional)
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if from_arg is None:
            for arg in call.arguments:
                if not arg.name or arg.name == "t":
                    # skip explicit type arg
                    if arg.name == "t":
                        continue
                    from_arg = arg
                    break

        if from_arg is None:
            self._error("box requires a 'from:' argument", loc=call.start)
            return None

        inner_result = self._check_operation(from_arg.valtype)
        inner_type = inner_result.ztype
        if not inner_type:
            return None

        # Boxing transfers ownership of the source into the box. The
        # box-construction dispatch bypasses the standard call-ownership
        # loop at _check_call, so apply TAKE enforcement here. Hoist
        # non-trivial args into a synth temp first (mirroring _check_call)
        # so TAKE-application operates on a bare AtomId. Literals have no
        # root name and are unaffected.
        arg_borrow_path = inner_result.borrow_target
        if not self._arg_is_trivial(from_arg):
            self._hoist_arg(from_arg, inner_type, arg_borrow_path)
        self._apply_take_to_arg(from_arg, "from")

        # With stack-allocated classes and unions, all user-defined types
        # are stack-allocated values. Box always heap-allocates a copy.
        # Only types that are already pointers (heap-allocated: list, map,
        # heap-allocated classes in legacy code) use passthrough.
        if inner_type.is_heap_allocated:
            # Already a pointer: passthrough (just take ownership)
            self.typing.node_type[call.nodeid] = inner_type
            self.typing.call_kind[call.nodeid] = zast.CallKind.BOX_PASSTHROUGH
            return inner_type

        # stack-allocated value: create monomorphized box type
        defn = self._find_generic_defn(box_template)
        if not defn:
            return None
        mono = self._monomorphize(box_template, {"t": inner_type}, defn)
        if mono:
            mono.is_box = True
            mono.is_heap_allocated = True  # box data is on the heap
            mono.needs_destructor = True
            mono.destructor_name = f"z_{mono.name}_destroy"
            # copy children from inner type for transparent access
            for cname, ctype in self.typing.children_of(inner_type):
                if not self.typing.has_child(mono, cname):
                    self._set_child(mono, cname, ctype)
            self.typing.node_type[call.nodeid] = mono
            self.typing.call_kind[call.nodeid] = zast.CallKind.BOX_CREATE
        return mono

    def _option_template_nodeid(self) -> int:
        """Resolve and cache the stdlib `option` generic-template nodeid."""
        if self.template_ids.option == -1:
            t = self._resolve_name("Option")
            if t is not None:
                self.template_ids.option = t.nodeid
        return self.template_ids.option

    def _optionval_template_nodeid(self) -> int:
        """Resolve and cache the stdlib `optionval` generic-template nodeid."""
        if self.template_ids.optionval == -1:
            t = self._resolve_name("optionval")
            if t is not None:
                self.template_ids.optionval = t.nodeid
        return self.template_ids.optionval

    def _optionview_template_nodeid(self) -> int:
        """Resolve and cache the stdlib `optionview` generic-template nodeid."""
        if self.template_ids.optionview == -1:
            t = self._resolve_name("OptionView")
            if t is not None:
                self.template_ids.optionview = t.nodeid
        return self.template_ids.optionview

    def _is_option_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized option type."""
        return (
            t.typetype == ZTypeType.UNION
            and t.generic_origin is not None
            and not t.is_tag_generic_origin
            and t.generic_origin.nodeid == self._option_template_nodeid()
        )

    def _is_optionval_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized optionval type."""
        return (
            t.typetype == ZTypeType.VARIANT
            and t.generic_origin is not None
            and not t.is_tag_generic_origin
            and t.generic_origin.nodeid == self._optionval_template_nodeid()
        )

    def _is_optionview_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized optionview type."""
        return (
            t.typetype == ZTypeType.UNION
            and t.generic_origin is not None
            and not t.is_tag_generic_origin
            and t.generic_origin.nodeid == self._optionview_template_nodeid()
        )

    def _is_option_or_optionval_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized option or optionval type."""
        return self._is_option_type(t) or self._is_optionval_type(t)

    def _is_iterator_wrapper(self, t: ZType) -> bool:
        """Check if a type is one of the iterator-wrapper types: option,
        optionval, or optionview. The for-loop dispatch uses this to
        recognize per-iteration values regardless of ownership shape."""
        return (
            self._is_option_type(t)
            or self._is_optionval_type(t)
            or self._is_optionview_type(t)
        )

    def _check_for(self, fornode: zast.For) -> Optional[ZType]:
        """Type-check a for-expression. Thin wrapper that builds the
        typed mirror after the inner walks conditions / loop / post."""
        t = self._check_for_inner(fornode)
        return t

    def _check_for_inner(self, fornode: zast.For) -> Optional[ZType]:
        self.symtab.push("for")
        # introduce break and continue bindings for this loop
        t_never = self._resolve_name("never")
        if t_never:
            break_type = _make_type("break", ZTypeType.FUNCTION)
            break_type.return_type = t_never
            break_type.control_kind = ControlKind.BREAK
            self.symtab.define("break", break_type)
            continue_type = _make_type("continue", ZTypeType.FUNCTION)
            continue_type.return_type = t_never
            continue_type.control_kind = ControlKind.CONTINUE
            self.symtab.define("continue", continue_type)
        # for loops mask do-block break targets (break binds to the for, not enclosing do)
        self._break_targets.append(None)
        for name, cond_op in fornode.conditions.items():
            t = self._check_operation(cond_op).ztype
            if t and not name.startswith(" "):
                # iterator binding: check if operation type is or returns
                # one of the iterator wrappers (option/optionval/optionview).
                iter_option_type = None
                if self._is_iterator_wrapper(t):
                    # operation directly returns an iterator wrapper
                    # (e.g., function call returning option(t))
                    iter_option_type = t
                elif (
                    t.typetype == ZTypeType.FUNCTION
                    and t.return_type
                    and self._is_iterator_wrapper(t.return_type)
                ):
                    # function reference whose return is an iterator wrapper
                    # (e.g., .each / .iterate on integers — function called
                    # per iteration)
                    iter_option_type = t.return_type
                elif t.typetype != ZTypeType.FUNCTION:
                    # callable object: T has a .call returning a wrapper
                    call_method = self.typing.child_of(t, "call")
                    if (
                        call_method
                        and call_method.typetype == ZTypeType.FUNCTION
                        and call_method.return_type
                        and self._is_iterator_wrapper(call_method.return_type)
                    ):
                        iter_option_type = call_method.return_type

                if iter_option_type:
                    some_type = self.typing.child_of(iter_option_type, "some")
                    if some_type:
                        self.typing.for_iter_bindings.setdefault(
                            fornode.nodeid, set()
                        ).add(name)
                        t = some_type
                # optionview yields borrowed views: mark the loop var with
                # borrow_origin so the existing escape checks (storage,
                # return, aggregate-store sites) reject moves out of the
                # loop body. option / optionval bindings stay owned.
                is_borrowed_view = (
                    iter_option_type is not None
                    and self._is_optionview_type(iter_option_type)
                )
                if is_borrowed_view:
                    src_name = self._iterator_source_name(cond_op) or "<iterator>"
                    var = ZVariable(
                        ztype=t,
                        ownership=ZOwnership.BORROWED,
                        named=ZNaming.NAMED,
                    )
                    var.borrow_origin = src_name
                    self.symtab.define_var(name, var)
                else:
                    self.symtab.define(name, t)
                # lock the iteration target to prevent mutation in body.
                # Skip for borrowed-view bindings: borrow_origin already
                # blocks transfers / aggregate-stores via the escape checks,
                # and an EXCLUSIVE lock here would also forbid plain reads
                # of the binding (lookup_var triggers the lock check on
                # var-ful definitions).
                if not _is_valtype(t) and not is_borrowed_view:
                    self.symtab.try_lock((name,), ZLockState.EXCLUSIVE, "__for")
        for postcond in fornode.postconditions:
            self._check_operation(postcond)
        elem_type = None
        if fornode.loop:
            self._check_statement(fornode.loop)
            # for-as-expression: if the last statement in the loop body is an
            # expression, the for-expression returns a list of that type
            if fornode.loop.statements:
                last = fornode.loop.statements[-1].statementline
                if last.nodetype == NodeType.EXPRESSION:
                    last_expr2 = cast(zast.Expression, last)
                    inner_type = self.typing.node_type.get(
                        last_expr2.nodeid
                    ) or self.typing.node_type.get(last_expr2.expression.nodeid)
                    if inner_type:
                        elem_type = inner_type
        # for-loop locks are released when the for scope is popped
        self._break_targets.pop()
        self.symtab.pop()
        # for-as-expression: return list of elem_type (non-null values only)
        if elem_type and elem_type != self.t_null and elem_type.name != "null":
            list_template = self._resolve_name("List")
            if list_template and list_template.isgeneric:
                list_defn = self._find_generic_defn(list_template)
                if list_defn:
                    list_mono = self._monomorphize(
                        list_template, {"of": elem_type}, list_defn
                    )
                    self.typing.node_type[fornode.nodeid] = list_mono
                    return list_mono
        return self.t_null

    def _iterator_source_name(self, op: zast.Operation) -> Optional[str]:
        """Return the human-readable source name for an iterator binding's
        RHS operation, used as the `borrow_origin` of the loop variable.

        Bare-variable iterators (`for s: it loop`) borrow from `it`.
        Other shapes (calls / construction expressions) return None;
        callers fall back to a synthetic name.
        """
        actual = op
        while actual.nodetype == NodeType.EXPRESSION:
            actual = cast(zast.Expression, actual).expression
        if actual.nodetype == NodeType.ATOMID:
            return cast(zast.AtomId, actual).name
        return None

    def _check_with(self, withnode: zast.With) -> Optional[ZType]:
        """Type-check a with-expression. Thin wrapper that builds the
        typed mirror after the inner runs."""
        t = self._check_with_inner(withnode)
        return t

    def _check_with_inner(self, withnode: zast.With) -> Optional[ZType]:
        """Type-check `with name: value do doexpr`.

        Ownership follows function-argument rules:
        - bare name / dotted path  → BORROW, EXCLUSIVE-lock the source root
        - `.take` inline           → OWNED, source invalidated
        - `.borrow` inline         → BORROW (same as default)
        - call / literal / ctor    → OWNED (fresh value, no source to lock)
        """
        self.symtab.push("with")
        result = self._check_expression(withnode.value)
        t = result.ztype
        borrow_target = result.borrow_target

        if t is None:
            self.symtab.pop()
            return None

        # Reject .release as an RHS value (matches _check_assignment).
        inner_expr = withnode.value.expression
        if (
            inner_expr.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, inner_expr).child.name == "release"
        ):
            self._error(
                "'.release' cannot be used as a value; "
                "use '.take' to transfer ownership",
                loc=withnode.start,
                err=ERR.OWNERERROR,
            )
            self.symtab.pop()
            return None

        # Phase A: default-borrow for plain path RHS. If _check_expression
        # didn't set a borrow_target and didn't invalidate the source (.take),
        # and the RHS is a bare name or a plain dotted path, treat as BORROW
        # and lock the source root.
        if borrow_target is None and inner_expr is not None:
            nt = inner_expr.nodetype
            is_plain_path = False
            if nt == NodeType.ATOMID:
                # bare name bound to a runtime variable
                name = cast(zast.AtomId, inner_expr).name
                if self.symtab.lookup_var(name) is not None:
                    is_plain_path = True
            elif nt == NodeType.DOTTEDPATH:
                dp_child = cast(zast.DottedPath, inner_expr).child.name
                # take/release/borrow/lock/stringview/listview are already
                # handled by _check_expression (take invalidates; the others
                # set borrow_target). Anything else is a plain path access.
                if dp_child not in (
                    "take",
                    "release",
                    "borrow",
                    "lock",
                    "StringView",
                    "ListView",
                ):
                    is_plain_path = True
            if is_plain_path:
                borrow_target = self._get_dotted_path_tuple(
                    cast(zast.Operation, inner_expr)
                )

        # Define the with-bound variable.
        ownership = ZOwnership.BORROWED if borrow_target else ZOwnership.OWNED
        var = ZVariable(ztype=t, ownership=ownership, named=ZNaming.NAMED)
        var.is_private_access = result.private_access
        self.symtab.define_var(withnode.name, var)

        # Acquire borrow-scoped locks on the source path for reftypes only.
        # Valtype borrows are copies; they do not need a lock at this level
        # (matches function-arg and _check_assignment behavior).
        if borrow_target and not _is_valtype(t):
            self._install_borrow_locks(borrow_target, withnode.name, withnode.start)

        self.typing.with_ownership[withnode.nodeid] = ownership
        self.typing.node_type[withnode.nodeid] = t

        # Phase B: alias optimization — if the RHS is a plain path reference
        # (bare name, dotted valtype path, or inline take/borrow of either),
        # emit the binding as a C-level alias instead of a real local.
        # Either the borrow lock or the take-invalidation guarantees the
        # source slot is stable for the binding's lifetime.
        self.typing.with_alias_of[withnode.nodeid] = self._alias_target(withnode.value)

        do_type = self._check_expression(withnode.doexpr).ztype
        self.symtab.pop()
        return do_type

    def _check_generic_type_match(
        self, casenode: zast.Case, concrete_type: ZType
    ) -> Optional[ZType]:
        """Handle match on a generic type parameter (compile-time type switch).

        When the concrete type is known (monomorphized context), the matching
        arm is determined at compile time and dead arms suppress errors.
        """
        concrete_name = concrete_type.name
        const_match_taken = False

        match_marker = self.symtab.push_block("generic_match")

        for clause in casenode.clauses:
            arm_marker = self.symtab.push_block(f"arm:{clause.match.name}")
            arm_matches = clause.match.name == concrete_name
            # tag each clause with its type name for emitter const folding
            self.typing.node_const_value[clause.match.nodeid] = clause.match.name
            if const_match_taken or not arm_matches:
                self._suppress_compile_error += 1
            self._check_statement(clause.statement)
            if const_match_taken or not arm_matches:
                self._suppress_compile_error -= 1
            if arm_matches and not const_match_taken:
                const_match_taken = True
            self.symtab.pop_to(arm_marker)

        if casenode.elseclause:
            arm_marker = self.symtab.push_block("arm:else")
            if const_match_taken:
                self._suppress_compile_error += 1
            self._check_statement(casenode.elseclause)
            if const_match_taken:
                self._suppress_compile_error -= 1
            self.symtab.pop_to(arm_marker)

        self.symtab.pop_to(match_marker)

        # mark the match as a generic type switch for the emitter
        self.typing.node_const_value[casenode.subject.nodeid] = concrete_name
        # Re-stamp the subject's typed mirror so it picks up the
        # F5.E.4.d: typed-mirror rebuild removed.

        # compute result type for match-as-expression
        result_type = self.t_null
        is_exhaustive = bool(casenode.elseclause) or const_match_taken
        if is_exhaustive:
            branch_types: "list[Optional[ZType | TypeChecker._NoReturnSentinel]]" = []
            for c in casenode.clauses:
                branch_types.append(self._last_statement_type(c.statement))
            if casenode.elseclause:
                branch_types.append(self._last_statement_type(casenode.elseclause))
            completing: "list[Optional[ZType | TypeChecker._NoReturnSentinel]]" = []
            for bt in branch_types:
                if bt is not self._NORETURN:
                    completing.append(bt)
            if not completing and branch_types:
                never = self._resolve_name("never")
                if never:
                    result_type = never
                    self.typing.node_type[casenode.nodeid] = never
            elif completing:
                first_raw = completing[0]
                if first_raw is not None and first_raw.is_ztype:
                    result_type = cast(ZType, first_raw)
                    self.typing.node_type[casenode.nodeid] = result_type

        self.symtab.pop()
        return result_type

    def _check_case(self, casenode: zast.Case) -> Optional[ZType]:
        """Type-check a match expression. Thin wrapper that builds the
        typed mirror after the inner walks subject + clauses."""
        t = self._check_case_inner(casenode)
        return t

    def _apply_case_take_invalidation(
        self,
        casenode: zast.Case,
        subject_taken_in_arm: Optional[str],
        subject_name: Optional[str],
        taken_in_any_match_arm: set,
        saved_match_vars: dict,
    ) -> None:
        """Post-arm ownership reconciliation: when an arm body
        consumed (`.take`) the match subject or any other live owned
        variable, invalidate it after the match scope pops so
        post-match code can't reference it. Also stamps
        `Typing.case_subject_taken` / `Typing.case_taken_vars` for
        the emitter."""
        if subject_taken_in_arm and subject_name:
            self.typing.case_subject_taken[casenode.nodeid] = True
            take_loc = casenode.subject.start
            loc_tuple = (
                (take_loc.lineno, take_loc.colno, take_loc.fsno) if take_loc else None
            )
            self.symtab.invalidate(subject_name, loc=loc_tuple)
            if take_loc:
                self.symtab.set_taken_location(
                    subject_name,
                    (take_loc.lineno, take_loc.colno, take_loc.fsno),
                )
        if not taken_in_any_match_arm:
            return
        for vname in taken_in_any_match_arm:
            _, vtype = saved_match_vars[vname]
            self.typing.case_taken_vars.setdefault(casenode.nodeid, []).append(
                (vname, vtype)
            )
            take_loc = casenode.start
            loc_tuple = (
                (take_loc.lineno, take_loc.colno, take_loc.fsno) if take_loc else None
            )
            self.symtab.invalidate(vname, loc=loc_tuple)
            if take_loc:
                self.symtab.set_taken_location(
                    vname,
                    (take_loc.lineno, take_loc.colno, take_loc.fsno),
                )

    def _compute_case_result_type(
        self, casenode: zast.Case, is_exhaustive: bool
    ) -> ZType:
        """Compute the result type of a `match` expression.

        For non-exhaustive matches the result is `null`. For exhaustive
        matches (else clause present, or all union/variant subtypes
        covered), unify the completing arms' types — a NORETURN
        diverging arm contributes `never`; remaining arms must share
        a common type.

        Stamps `Typing.node_type[casenode.nodeid]` when a non-null
        result type is determined."""
        if not is_exhaustive:
            return self.t_null
        branch_types = [
            self._last_statement_type(clause.statement) for clause in casenode.clauses
        ]
        if casenode.elseclause:
            branch_types.append(self._last_statement_type(casenode.elseclause))
        completing = [t for t in branch_types if t is not self._NORETURN]
        if not completing and branch_types:
            never = self._resolve_name("never")
            if never:
                self.typing.node_type[casenode.nodeid] = never
                return never
            return self.t_null
        if not completing:
            return self.t_null
        first_raw = completing[0]
        if first_raw is None or not first_raw.is_ztype:
            return self.t_null
        first = cast(ZType, first_raw)
        all_ok = all(
            t is not None
            and t.is_ztype
            and self._types_compatible(first, cast(ZType, t))
            for t in completing[1:]
        )
        if all_ok:
            self.typing.node_type[casenode.nodeid] = first
            return first
        for t in completing[1:]:
            if (
                t is None
                or not t.is_ztype
                or not self._types_compatible(first, cast(ZType, t))
            ):
                tname = cast(ZType, t).name if t is not None and t.is_ztype else "null"
                self._error(
                    f"incompatible branch types in match-expression: "
                    f"'{first.name}' and '{tname}'",
                    loc=casenode.start,
                )
                break
        return self.t_null

    def _check_case_inner(self, casenode: zast.Case) -> Optional[ZType]:
        self.symtab.push("match")

        # Reject `.take` on the match subject. Narrowing requires the
        # subject to remain addressable across arms; taking ownership
        # at match time would invalidate later arms. (Note: `.take`
        # *inside* an arm body is fine — it's a single arm-local
        # transfer, handled by the take-in-arm tracking below.)
        # Unwrap any Expression wrappers (e.g. `match (u.take) ...`)
        # so the suffix is visible regardless of parenthesisation.
        subj_op: zast.Operation = casenode.subject
        while subj_op.nodetype == NodeType.EXPRESSION:
            subj_op = cast(zast.Operation, cast(zast.Expression, subj_op).expression)
        _stripped, subj_own = _strip_path_ownership(subj_op)
        if subj_own is ZParamOwnership.TAKE:
            self._error(
                "cannot '.take' the subject of 'match'; the subject is "
                "borrowed for arm narrowing",
                loc=casenode.subject.start,
                err=ERR.BADCASE,
            )
            self.symtab.pop()
            return None

        # generic type parameter match: match on t where t is a generic param
        if casenode.subject.nodetype == NodeType.ATOMID and self.mono.generic_context:
            gp_name = cast(zast.AtomId, casenode.subject).name
            for ctx in reversed(self.mono.generic_context):
                if gp_name in ctx:
                    concrete = ctx[gp_name]
                    # only fold when concrete type is known (not still generic)
                    if concrete.typetype != ZTypeType.GENERIC_PARAM:
                        return self._check_generic_type_match(casenode, concrete)
                    break

        subject_type = self._check_operation(casenode.subject).ztype

        # match does NOT lock its subject (unlike 'for' and function calls).
        # Arms are mutually exclusive and the subject is evaluated once, so
        # there is no aliasing or re-evaluation concern. This allows .take
        # inside arms for ownership transfer.

        # determine if subject is a union/variant and get target name for narrowing
        is_sum_type = subject_type is not None and subject_type.typetype in (
            ZTypeType.UNION,
            ZTypeType.VARIANT,
        )
        # Per A3: narrow only when the subject is a simple addressable name
        # (a bare AtomId after stripping any Expression wrappers). Dotted
        # paths and complex expressions share a root whose type must not be
        # re-narrowed by the match; arm bodies for those subjects get no
        # narrowed binding and can only perform side effects predicated on
        # the matched variant.
        target_name: Optional[str] = None
        if is_sum_type:
            subj: zast.Node = casenode.subject
            while subj.nodetype == NodeType.EXPRESSION:
                subj = cast(zast.Expression, subj).expression
            if subj.nodetype == NodeType.ATOMID:
                name = cast(zast.AtomId, subj).name
                if not _is_numeric_id(name):
                    target_name = name

        # save subject variable info for take-in-arms tracking (reftypes only)
        subject_name = self._get_arg_root_name(casenode.subject)
        subject_var: Optional[ZVariable] = None
        subject_sym_type: Optional[ZType] = None
        subject_taken_in_arm: Optional[str] = None  # arm name where take occurred
        if subject_name and subject_type and not _is_valtype(subject_type):
            subject_var = self.symtab.lookup_var(subject_name)
            subject_sym_type = self.symtab.lookup(subject_name)

        # union/variant exhaustiveness check
        if is_sum_type and subject_type:
            kind = "union" if subject_type.typetype == ZTypeType.UNION else "variant"
            # collect subtype names (exclude tag data, and methods)
            subtypes = {
                k
                for k, v in self.typing.children_of(subject_type)
                if v.typetype
                not in (
                    ZTypeType.FUNCTION,
                    ZTypeType.DATA,
                    ZTypeType.TAG,
                    ZTypeType.ENUM,
                )
                and not v.is_tag_generic_origin
            }
            covered = {clause.match.name for clause in casenode.clauses}
            missing = subtypes - covered
            if missing and not casenode.elseclause:
                self._error(
                    f"Non-exhaustive match on {kind} '{subject_type.name}': "
                    f"missing {', '.join(sorted(missing))}",
                    loc=casenode.subject.start
                    if hasattr(casenode.subject, "start")
                    else None,
                )

        # compile-time constant match: for scalar matches, resolve subject
        # const_value to suppress errors in dead arms
        subject_const: object = None
        subject_cv = self.typing.node_const_value.get(casenode.subject.nodeid)
        if not is_sum_type and subject_cv is not None:
            if type(subject_cv) is int or type(subject_cv) is bool:
                subject_const = subject_cv
        const_match_taken = False

        # snapshot live owned variables before arms for generalized take-in-arm tracking
        live_before_match = self.symtab.get_live_owned_vars()
        saved_match_vars: dict = {}
        for vname in live_before_match:
            saved_match_vars[vname] = (
                self.symtab.lookup_var(vname),
                self.symtab.lookup(vname),
            )
        taken_in_any_match_arm: set = set()

        # type narrowing via scope-based overlays
        match_marker = self.symtab.push_block("match_body")
        # reset any existing narrowing for the match target
        if target_name:
            self.symtab.reset_narrowing(target_name)

        # track which arms diverge for post-match exclusion
        diverging_arms: List[str] = []

        for clause in casenode.clauses:
            arm_marker = self.symtab.push_block(f"arm:{clause.match.name}")
            # narrow to this arm's subtype. Shadow mode: the narrowed
            # name resolves directly to the payload type in the arm body,
            # so field access / method dispatch / re-match all work as
            # if the name were the payload. The outer union/variant is
            # stashed in Entry.original_ztype for the emitter's unwrap.
            if target_name and subject_type:
                arm_subtype = self.typing.child_of(subject_type, clause.match.name)
                if arm_subtype:
                    self.symtab.narrow(
                        target_name,
                        arm_subtype,
                        clause.match.name,
                        shadow=True,
                    )
            # Phase 7b: stamp arm-name child id against the scrutinee's
            # union/variant type so the emitter can read clause.match.child_id
            # without another name→id resolution pass.
            if (
                subject_type is not None
                and subject_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT)
                and self.typing.atom_child_id.get(clause.match.nodeid, -1) == -1
            ):
                self.typing.atom_child_id[clause.match.nodeid] = (
                    subject_type.child_id_for(clause.match.name)
                )

            # resolve match pattern const_value for scalar const folding
            suppress_arm = False
            if subject_const is not None:
                match_cv = None
                mname = clause.match.name
                if _is_numeric_id(mname):
                    _, mval, merr = parse_number(mname)
                    if not merr and type(mval) is int:
                        match_cv = mval
                else:
                    # demand-resolve the name to ensure const_value is set
                    self._resolve_name(mname)
                    mdefn = self._lookup_definition(mname)
                    if mdefn is not None:
                        mcv = self.typing.node_const_value.get(mdefn.nodeid)
                        if mcv is not None:
                            match_cv = mcv
                if match_cv is not None:
                    self.typing.node_const_value[clause.match.nodeid] = match_cv
                    if const_match_taken or subject_const != match_cv:
                        suppress_arm = True
                    elif subject_const == match_cv:
                        const_match_taken = True

            if suppress_arm:
                self._suppress_compile_error += 1
            self._check_statement(clause.statement)
            if suppress_arm:
                self._suppress_compile_error -= 1

            # track take-in-arms: if the subject was taken during this arm,
            # restore it for subsequent arms (each arm sees the original state)
            if subject_name and subject_var and subject_sym_type:
                if self.symtab.lookup(subject_name) is None:
                    # subject was taken in this arm — record and restore
                    if subject_taken_in_arm is None:
                        subject_taken_in_arm = clause.match.name
                    # restore the variable so the next arm can reference it
                    self.symtab.define_var(subject_name, subject_var)
                    # clear the taken record so the next arm starts fresh
                    self.symtab.clear_taken(subject_name)

            # generalized take-in-arm tracking for all live owned variables
            for vname in live_before_match:
                if vname == subject_name:
                    continue  # subject handled above
                if self.symtab.lookup(vname) is None:
                    taken_in_any_match_arm.add(vname)
                    sv, st = saved_match_vars[vname]
                    if sv is not None:
                        self.symtab.define_var(vname, sv)
                        self.symtab.clear_taken(vname)

            # track diverging arms for post-match exclusion
            if target_name and subject_type:
                arm_type = self._last_statement_type(clause.statement)
                if arm_type is self._NORETURN:
                    diverging_arms.append(clause.match.name)

            self.symtab.pop_to(arm_marker)

        if casenode.elseclause:
            # else clause: narrow to union minus all explicit case subtypes
            arm_marker = self.symtab.push_block("arm:else")
            if target_name and subject_type:
                for clause in casenode.clauses:
                    self.symtab.exclude(target_name, clause.match.name, subject_type)
            if const_match_taken:
                self._suppress_compile_error += 1
            self._check_statement(casenode.elseclause)
            if const_match_taken:
                self._suppress_compile_error -= 1

            # track take-in-arms for else clause
            if subject_name and subject_var and subject_sym_type:
                if self.symtab.lookup(subject_name) is None:
                    if subject_taken_in_arm is None:
                        subject_taken_in_arm = "else"
                    self.symtab.define_var(subject_name, subject_var)
                    self.symtab.clear_taken(subject_name)

            # generalized take-in-arm tracking for else clause
            for vname in live_before_match:
                if vname == subject_name:
                    continue
                if self.symtab.lookup(vname) is None:
                    taken_in_any_match_arm.add(vname)

            # if else clause diverges, all remaining subtypes are excluded
            else_type = self._last_statement_type(casenode.elseclause)
            if else_type is self._NORETURN and target_name and subject_type:
                all_diverge = all(
                    self._last_statement_type(c.statement) is self._NORETURN
                    for c in casenode.clauses
                )
                if all_diverge:
                    diverging_arms.append("__else__")

            self.symtab.pop_to(arm_marker)

        self.symtab.pop_to(match_marker)

        self._apply_case_take_invalidation(
            casenode,
            subject_taken_in_arm,
            subject_name,
            taken_in_any_match_arm,
            saved_match_vars,
        )

        # determine if match is exhaustive (else clause or all subtypes covered)
        is_exhaustive = bool(casenode.elseclause)
        if (
            not is_exhaustive
            and subject_type
            and subject_type.typetype
            in (
                ZTypeType.UNION,
                ZTypeType.VARIANT,
            )
        ):
            subtypes_for_exhaust = {
                k
                for k, v in self.typing.children_of(subject_type)
                if v.typetype
                not in (
                    ZTypeType.FUNCTION,
                    ZTypeType.DATA,
                    ZTypeType.TAG,
                    ZTypeType.ENUM,
                )
                and not v.is_tag_generic_origin
            }
            covered_for_exhaust = {clause.match.name for clause in casenode.clauses}
            if not (subtypes_for_exhaust - covered_for_exhaust):
                is_exhaustive = True

        result_type = self._compute_case_result_type(casenode, is_exhaustive)

        self.symtab.pop()

        # apply post-match exclusions from diverging arms (after match scope popped)
        if target_name and subject_type:
            for arm_name in diverging_arms:
                if arm_name != "__else__":
                    self.symtab.exclude(target_name, arm_name, subject_type)
            if "__else__" in diverging_arms:
                self.symtab.mark_unreachable()

        return result_type


def typecheck(program: zast.Program, full: bool = False) -> ztyping.Typing:
    """Top-level entry point: type-check a parsed program.

    Returns the populated `Typing` (typecheck-output container).
    `Typing` carries the back-reference to the parsed program, the
    typecheck errors, and every typecheck-derived datum the emitter /
    SQL dumper / asthash need to read. `program` is read-only —
    F5.E.5 froze `zast.Program`.
    """
    tc = TypeChecker(program)
    errors = tc.check(full=full)
    tc.typing.errors = errors
    tc.typing.is_error = bool(errors)
    tc.typing.mono_types = tc.mono.types
    tc.typing.mono_functions = tc.mono.functions
    tc.typing.func_aliases = tc.mono.func_aliases
    tc.typing.cloned_methods = tc.mono.cloned_methods
    tc.typing.resolved = dict(tc._resolved)
    tc.typing.symbol_table = tc.symtab
    tc.typing.unit_types_by_id = dict(tc.unit_types_by_id)
    # `TypedProgramView` exposes a handful of `Typing` component
    # tables under their legacy `typed_program.X` access path for
    # compatibility with the codereview fields (no consumer in src/
    # uses it after F5.E.4; one or two tests still do).
    tc.typing.typed_program = ztyping.TypedProgramView(
        node_types=tc.typing.node_type,
        expr_call_kinds=tc.typing.expr_call_kind,
        node_const_value=tc.typing.node_const_value,
        call_kind=tc.typing.call_kind,
        dp_child_id=tc.typing.dp_child_id,
        atom_child_id=tc.typing.atom_child_id,
    )
    return tc.typing


def audit_type_annotations(typing: ztyping.Typing) -> List[str]:
    """Post-type-check validation: find Path nodes missing .type annotations.

    Returns a list of diagnostic strings for nodes that should have .type
    set but don't. Empty list means all Path nodes are annotated."""
    missing: List[str] = []
    visited: set[int] = set()
    program = typing.parsed
    node_types = typing.node_type

    def _is_skipped_path(
        node: zast.Node, parent: Optional[zast.Node], in_data: bool
    ) -> bool:
        """Skip Path nodes that are structural components (not type
        references): a DottedPath.child name selector, a BinOp.operator
        name, a NamedOperation.valtype anywhere inside a Data.data list,
        a CaseClause.match pattern, or a top-level Unit-body definition
        (`name: 42`).
        """
        if parent is None:
            # top-level Unit-body definition — `name: 42` style consts.
            return True
        if in_data:
            # any path under a Data node is a literal value slot.
            return True
        if (
            parent.nodetype == NodeType.DOTTEDPATH
            and node is cast(zast.DottedPath, parent).child
        ):
            return True
        if (
            parent.nodetype == NodeType.BINOP
            and node is cast(zast.BinOp, parent).operator
        ):
            return True
        if (
            parent.nodetype == NodeType.CASECLAUSE
            and node is cast(zast.CaseClause, parent).match
        ):
            return True
        return False

    def _walk(node: zast.Node, parent: Optional[zast.Node], in_data: bool) -> None:
        nid = id(node)
        if nid in visited:
            return
        visited.add(nid)
        if node.nodetype in (NodeType.ATOMID, NodeType.DOTTEDPATH):
            if node_types.get(node.nodeid) is None and not _is_skipped_path(
                node, parent, in_data
            ):
                loc = f"{node.start.lineno}:{node.start.colno}" if node.start else "?"
                name = (
                    cast(zast.AtomId, node).name
                    if node.nodetype == NodeType.ATOMID
                    else str(node)
                )
                missing.append(f"Path node '{name}' at {loc} has no .type")
        child_in_data = in_data or node.nodetype == NodeType.DATA
        for child in zast.node_children(node):
            _walk(child, node, child_in_data)

    mainunit = program.units.get(program.mainunitname)
    if mainunit:
        # Top-level Unit-body defs are treated as parent=None so they're
        # recognised as toplevel-const slots.
        for _name, defn in mainunit.body.items():
            _walk(defn, None, False)

    return missing
