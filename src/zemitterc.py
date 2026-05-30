"""
ZeroLang C code emitter

Walks a type-checked AST and emits C source code.
Includes ownership-based memory management for strings (z_String_t*).
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Callable, cast

import zast
import ztyping
from zast import NodeType
import zemitterc_runtime as zrt
import zemitterc_templates as ztmpl
from ztypes import (
    ZType,
    ZTypeType,
    ZSubType,
    ZBuiltinFunc,
    ZControlKind,
    ZConformance,
    parse_number,
    ZParamOwnership,
    ZOwnership,
    NUMERIC_RANGES,
    _type_by_id,
    mangle_var_name,
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
    is_mapkeyiter_type as _is_mapkeyiter_type,
    is_mapitemiter_type as _is_mapitemiter_type,
    is_mapentry_type as _is_mapentry_type,
    is_map_type as _is_map_type,
    map_key_type as _map_key_type,
    map_value_type as _map_value_type,
    is_set_type as _is_set_type,
    set_element_type as _set_element_type,
    is_setiter_type as _is_setiter_type,
    is_stringview_type as _is_stringview_type,
    _unwrap_typedef,
)


def _is_collection_param_type(ptype: Optional[ZType]) -> bool:
    """True for list / listview / map / set (directly or via typedef) —
    the types that must be passed by pointer across a protocol
    boundary so in-place mutation stays visible."""
    return (
        _is_list_type(ptype)
        or _is_listview_type(ptype)
        or _is_map_type(ptype)
        or _is_set_type(ptype)
    )


def _proto_param_ctype(typing: ztyping.ZTyping, ptype: Optional[ZType]) -> str:
    """C type for a parameter in a protocol vtable / wrapper signature.

    Mutable-collection parameters (list, listview, map — directly or
    via a typedef wrapper like `bytes` / `byteview`) are passed by
    pointer so mutations (append, grow) are visible to the caller
    through the protocol boundary. This matches the native class-
    method ABI, which already takes these by pointer.

    Scalars, variants, records, strings, and other value types keep
    their usual by-value `_ctype`.
    """
    ct = _ctype(typing, ptype)
    if _is_collection_param_type(ptype) and not ct.endswith("*"):
        return f"{ct}*"
    return ct


def _mono_name(ztype: Optional[ZType]) -> str:
    """Return the mangled base name of a (possibly typedef-wrapped) type.

    Typedef wrappers (e.g. `bytes` → `list of: u8`) emit no C struct
    of their own; downstream code must use the wrapped type's name
    when generating mangled identifiers like `z_<name>_create` or
    `z_<name>_append`. Defensive against None (falls back to the
    wrapper name — callers have already checked the predicate)."""
    base = _unwrap_typedef(ztype) if ztype is not None else None
    if base is not None:
        return base.name
    if ztype is not None:
        return ztype.name
    return ""


@dataclass
class ScopeState:
    """Per-function cleanup state, pushed/popped at function boundaries.

    `cleanup_stack` is a stack of block scopes. Each inner list collects
    `(variable_id, ZType)` pairs in insertion order for one block body; the
    emitter resolves the C name from `variable_cname[variable_id]` at flush
    time. The outermost frame (`cleanup_stack[0]`) is the function body
    itself; subsequent frames push when entering a nested block (if/else/
    for/do/with/match-arm) and pop at block close, emitting destructors
    for the popped frame's entries in reverse declaration order.
    """

    cleanup_stack: list = field(default_factory=lambda: [[]])
    temp_counter: int = 0
    record_name: str = ""
    # variable_ids of pointer-passed parameters (class `this`, borrow/lock
    # class params, already-pointer params). Queried to choose `->` vs `.`
    # and to suppress a redundant `&` when forwarding such a param.
    class_params: set = field(default_factory=set)
    func_nodeid: int = 0  # NodeID of enclosing function (for unique temp names)
    # variable_ids of locals bound to a borrow (assigned from `out T.borrow`
    # call result, or from a `.borrow`/`.lock` projection). Used by `.release`
    # emit to skip the destructor — freeing a borrow corrupts the source.
    borrowed_vars: set = field(default_factory=set)

    @property
    def cleanup_vars(self) -> list:
        """Compatibility shim: the *current* (top) block-scope frame.

        Existing `.append((name, type))` sites continue to work; reads
        that need to see ALL active frames should iterate
        `cleanup_stack` directly.
        """
        return self.cleanup_stack[-1]


@dataclass
class TempState:
    """Per-statement temporary variable state, pushed/popped at statement boundaries.

    `decls` flush before the statement's code; `post_code` flushes after.
    Implicit-take invalidations belong in `post_code` — zeroing the source
    BEFORE the call passes an empty value to the callee (was a latent bug
    surfaced by per-call argument hoisting).
    """

    decls: List[str] = field(default_factory=list)
    post_code: List[str] = field(default_factory=list)
    frees: List[str] = field(default_factory=list)
    string_set: set = field(default_factory=set)
    class_set: Dict[str, str] = field(default_factory=dict)
    proto_set: Dict[str, str] = field(default_factory=dict)


TYPEMAP: Dict[str, str] = {
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "i128": "__int128",
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "u128": "unsigned __int128",
    "f32": "float",
    "f64": "double",
    "f128": "long double",
    "c8": "uint8_t",
    "c32": "uint32_t",
    "null": "void",
    "bool": "bool",
    "never": "void",
}

NUMERIC_CAST_TYPES = set(TYPEMAP.keys()) - {"null", "bool", "never"}

# Estimated sizes of C types (bytes) for equality threshold decisions.
CTYPE_SIZES: Dict[str, int] = {
    "int8_t": 1,
    "uint8_t": 1,
    "int16_t": 2,
    "uint16_t": 2,
    "int32_t": 4,
    "uint32_t": 4,
    "float": 4,
    "int64_t": 8,
    "uint64_t": 8,
    "double": 8,
    "__int128": 16,
    "unsigned __int128": 16,
    "long double": 16,
    "bool": 1,  # C99 _Bool, 1 byte
}

# Types larger than this threshold (bytes) use memcmp for simple equality.
# Smaller types use field-by-field comparison (avoids memcmp call overhead).
_EQ_MEMCMP_THRESHOLD = 16

C_OPS: Dict[str, str] = {
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
    "<=": "<=",
    "<": "<",
    ">": ">",
    ">=": ">=",
    "==": "==",
    "!=": "!=",
}


def _ctype(typing: ztyping.ZTyping, ztype: Optional[ZType]) -> str:
    if not ztype:
        return "void"
    if ztype.typedef_base is not None:
        return _ctype(typing, ztype.typedef_base)
    # Data-element synthetic types: the typechecker stores each data
    # block element as a synthetic record whose name is the literal
    # value (so the custom-tag layer can detect duplicate tag values).
    # The underlying C type is the data block's element type — recover
    # it via the data_owner cross-ref. Tag types (also data_owner-
    # linked) are excluded; they have their own C representation.
    if ztype.data_owner_id is not None and not ztype.is_tag_generic_origin:
        data_owner = ztype.data_owner_type()
        if data_owner is not None and data_owner.element_type is not None:
            return _ctype(typing, data_owner.element_type)
    # nullable-ptr option: C type is the inner reftype's ctype (already a pointer)
    if ztype.is_nullable_ptr:
        some_type = typing.child_of(ztype, "some")
        if some_type:
            return _ctype(typing, some_type)
        return "void*"
    # box(valtype): C type is pointer to the inner valtype's ctype
    if ztype.is_box:
        inner_type = typing.generic_arg_of(ztype, "t")
        if inner_type:
            return f"{_ctype(typing, inner_type)}*"
        return "void*"
    name = ztype.name
    if name in TYPEMAP:
        return TYPEMAP[name]
    if ztype.subtype == ZSubType.STRING:
        return "z_String_t"
    if ztype.subtype == ZSubType.STRINGVIEW:
        return "z_StringView_t"
    # use pre-computed cname when available
    if ztype.cname:
        if ztype.is_heap_allocated:
            return f"{ztype.cname}*"
        if ztype.typetype == ZTypeType.FUNCTION:
            return f"{ztype.cname}_ft"
        return ztype.cname
    # fallback for types without cname (e.g. synthesized helper types)
    if ztype.typetype == ZTypeType.RECORD and name not in TYPEMAP:
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.CLASS:
        if ztype.is_heap_allocated:
            return f"z_{name}_t*"
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.UNION:
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.VARIANT:
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.FUNCTION:
        cname = name.replace(".", "_")
        return f"z_{cname}_ft"
    if ztype.typetype == ZTypeType.PROTOCOL:
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.FACET:
        return f"z_{name}_t"
    return "void"


def _mangle_func(name: str) -> str:
    """Mangle a zerolang function/global name for C."""
    if name == "main":
        return "z_main"
    return "z_" + name.replace(".", "_")


def _cname_of(ztype: Optional[ZType], name: str) -> str:
    """C struct type name for a type definition. Reads the typechecker-assigned
    `ztype.cname`; the `name` fallback covers only a synthesized type that never
    received one. The emitter reads stored C names rather than regenerating
    them, so a dependency/inline-unit type's dot-free cname is used at both its
    definition and every reference (see `_assign_cname` in ztypecheck)."""
    if ztype is not None and ztype.cname:
        return ztype.cname
    return f"z_{name}_t"


def _cbase_of(ztype: Optional[ZType], name: str) -> str:
    """C identifier base (cname without the type suffix) for a type. Helper
    names append a literal suffix, e.g. f"{_cbase_of(zt, name)}_meta_create".
    Reads the typechecker-assigned `ztype.cname_base`; the `name` fallback
    covers only a synthesized type that never received one."""
    if ztype is not None and ztype.cname_base:
        return ztype.cname_base
    return f"z_{name}"


def _mangle_var(name: str) -> str:
    """Compose a local variable's C name for sites that have only a name
    string and no `ZVariable` (synthesized wrapper params, string-keyed
    scope sets). Delegates to the single canonical `mangle_var_name` so the
    emitter never carries its own variable-name mangling logic. Sites that
    hold a variable_id read the stored `variable_cname` instead."""
    return mangle_var_name(name)


def _unwrap_outer_parens(s: str) -> str:
    """Strip one outer layer of parens if it wraps the whole expression.

    Used at statement sites (if/while/return/assignment RHS) where the
    surrounding syntax already supplies grouping, so binop expressions
    that defensively wrap themselves in parens produce noisy double-
    parenthesization. Safe on any C expression string: returns input
    unchanged when the outer parens do not match a full-width pair, or
    when the inside is a GCC statement-expression `({ ... })` whose
    outer parens are load-bearing (without them the brace becomes a
    bare block, which is not an expression).
    """
    if len(s) < 2 or s[0] != "(" or s[-1] != ")":
        return s
    if len(s) >= 4 and s[1] == "{" and s[-2] == "}":
        return s
    depth = 0
    for i, c in enumerate(s):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and i != len(s) - 1:
                return s
    return s[1:-1]


class TrackedList(list):
    """A list that records the emitter's _current_node_id alongside each appended item."""

    is_tracked_list: bool = True

    def __init__(self, emitter: "CEmitter"):
        super().__init__()
        self._emitter = emitter
        self.node_ids: List[Optional[int]] = []

    def append(self, item: str) -> None:
        super().append(item)
        self.node_ids.append(self._emitter._current_node_id)


class CEmitter:
    def __init__(self, typing: ztyping.ZTyping) -> None:
        self.typing = typing
        # Convenience alias: most of the emitter walks the parsed AST
        # for tree structure (units, function bodies, etc.); routing
        # those reads through `self.typing.parsed` would be noise.
        # `self.program` is the (frozen) parsed `Program`; typecheck
        # output lives on `self.typing`.
        self.program = self.typing.parsed
        self.out: List[str] = []
        self.indent_level = 0
        # Generator state-machine codegen context (G4). Non-None while
        # emitting the body of a synthesised `.call` method (those have
        # `func.synth_origin == "generator-call"`). Carries the
        # yield-to-state-number map, the return-wrapper monoclass name
        # (e.g. `optionval_i32`), and SOME/NONE tag identifiers. Yield
        # expressions encountered during body emission consult this
        # context and emit the suspension fragment in place. Outside
        # generator-call emission the context stays None and yields
        # produce no special output (the typechecker / desugarer keeps
        # yields out of non-generator code, so this should never fire
        # there in a green compile).
        self._generator_ctx: Optional[Dict[str, object]] = None
        self.needs_stdio = False
        self.needs_stdint = False
        self.needs_stdlib = False
        self.needs_string = False
        self.needs_stringview = False
        self.needs_io = False
        self.needs_os = False
        # SipHash runtime + per-process seed init. Set whenever a Map
        # or Set mono is emitted (their bucket dispatch routes through
        # z_siphash_string / z_hash_u64) or when String.hash /
        # StringView.hash is referenced.
        self.needs_hash = False
        # per-native flags; the runtime emits a helper only when its
        # flag is set, so unused natives do not pull in types the user
        # never monomorphized.
        self.needs_io_natives: set[str] = set()
        self.needs_os_natives: set[str] = set()
        # stringview-method natives (Phase S1+). Tracked separately
        # because these share the z_StringView_t substrate and the
        # late emission slot (after mono types are declared).
        self.needs_stringview_natives: set[str] = set()
        # cli-unit natives (spec_create, add_flag, parse, ...).
        self.needs_cli: bool = False
        self.needs_cli_natives: set[str] = set()
        self.forward_decls: List[str] = []
        # data-block name -> { label -> literal value }. Populated by
        # _emit_data for named-label items so that `<dataname>.LABEL`
        # access sites can lower to the literal constant at emit time.
        self._data_label_values: Dict[str, Dict[str, str]] = {}
        self.struct_defs: "TrackedList" = TrackedList(self)
        self.func_defs: "TrackedList" = TrackedList(self)
        self.data_defs: "TrackedList" = TrackedList(self)
        self.func_aliases: List[str] = []  # #define aliases for deduped functions
        # Per-call scratch: each `_emit_call` resets this list and
        # appends one entry per emitted argument so callers can read
        # back the last-emitted arg expressions (used by implicit-take
        # post-processing). Pre-initialised here so accesses don't have
        # to defend with getattr.
        self._last_emitted_arg_vals: List[str] = []
        # current AST node ID being emitted (set before emission blocks)
        self._current_node_id: Optional[int] = None
        # current enclosing type name (set in _emit_function when record_name
        # is non-empty). Used to resolve `meta.create` at emission time.
        self._current_enclosing_type_name: str = ""
        self._current_enclosing_type: Optional[ZType] = None
        # final source map: C output line (1-based) → AST node ID
        self.source_map: List[Optional[int]] = []
        # track numeric constant names (no distinct ZTypeType for these)
        self._const_names: set[str] = set()
        self._protocol_defs: dict[str, zast.ObjectDef] = {}  # name -> AST node
        # Separate slot for the io stream protocols so io.file's
        # wrapper emission can find them even when user code shadows
        # the short name (e.g. a user-declared `reader` protocol).
        self._io_protocol_defs: dict[str, zast.ObjectDef] = {}
        # Id-keyed facet/protocol lookups, keyed by the spec's type-id
        # (cross-unit-correct — a short-name map misses across units). facet
        # type-id -> AST def; facet type-id -> conforming impl type-ids (sizes
        # the facet's inline data union); protocol type-id -> AST def.
        self._facet_def_by_id: Dict[int, zast.ObjectDef] = {}
        self._facet_conformer_ids: Dict[int, list] = {}
        self._protocol_def_by_id: Dict[int, zast.ObjectDef] = {}
        # (impl_type, proto_name) -> label for owned protocol create
        self._proto_conformance: Dict[tuple, str] = {}
        # Case-A conformance entities, indexed for O(1) emitter lookup by the
        # impl/spec type-id pair (consumer sites that have both resolved types
        # but no label) and the (impl, spec, label) triple (definition sites).
        # The entity carries the pre-composed C names of the conformance
        # helpers, so the emitter reads them instead of rebuilding
        # `z_<impl>_<label>_<...>` strings inline (see ztypes.ZConformance).
        self._conformance_by_pair: Dict[tuple, ZConformance] = {}
        self._conformance_by_triple: Dict[tuple, ZConformance] = {}
        for conf in typing.conformance:
            self._conformance_by_pair[(conf.impl_type_id, conf.spec_type_id)] = conf
            self._conformance_by_triple[
                (conf.impl_type_id, conf.spec_type_id, conf.label)
            ] = conf
        # qualified names like "calculator.op" for func pointer fields in 'is' sections
        self._is_func_fields: set[str] = set()
        # set briefly while emitting a reassignment LHS so function-
        # pointer fields fall through to plain struct-slot access
        # instead of the zero-arg auto-call coercion used in value
        # position (`obj.instancemethod = X` vs `print obj.instancemethod`).
        self._lhs_mode: bool = False
        self.spec_typedefs: List[str] = []
        self.func_typedefs: List[str] = []  # typedefs for functions (after struct defs)
        # field info per type name (for meta.create calls)
        self._type_field_ctypes: Dict[str, List[str]] = {}
        self._type_field_names: Dict[str, List[str]] = {}
        self._unit_aliases: Dict[
            str, ZType
        ] = {}  # compile-time unit instantiation aliases
        self._type_field_defaults: Dict[str, Dict[str, str]] = {}
        # scope and temp state stacks (pushed/popped at function and statement boundaries)
        self._scope_stack: List[ScopeState] = [ScopeState()]
        self._temp_stack: List[TempState] = [TempState()]
        self._in_named_assignment: bool = False  # set during _emit_assignment
        # binding alias substitutions: zerolang name -> C expression (e.g., "r.f").
        # Set by alias-optimized `with` and inline `.take`/`.borrow` bindings so
        # references to the bound name in the body emit as the source expression
        # directly. Reset per function.
        self._alias_map: Dict[str, str] = {}
        # static string literal deduplication
        self._string_literals: Dict[str, str] = {}  # escaped C string → static var name
        self._string_literal_counter: int = 0
        # emitter-local scratch for For nodes used as list comprehensions:
        # nodeid → (C temp name for result list, mangled list-type name).
        # Entry presence signals "this For is being emitted as a comprehension".
        self._comprehension_state: Dict[int, Tuple[str, str]] = {}

    @property
    def _scope(self) -> ScopeState:
        return self._scope_stack[-1]

    @property
    def _temp(self) -> TempState:
        return self._temp_stack[-1]

    def _indent(self) -> str:
        return "    " * self.indent_level

    def _find_unit_def_by_name(self, name: str) -> Optional[zast.Node]:
        """Look up a top-level unit definition by simple name. Used by
        the macro-style inlining of unit-level numeric constants when
        the use-site atom isn't stamped with a const_value."""
        mainname = self.program.mainunitname
        mainunit = self.program.units.get(mainname)
        if mainunit is not None:
            defn = mainunit.body.get(name)
            if defn is not None:
                return defn
        for unitname, unit in self.program.units.items():
            if unitname == mainname:
                continue
            defn = unit.body.get(name)
            if defn is not None:
                return defn
        return None

    def _find_inline_unit_member(
        self, unit_name: str, member: str
    ) -> Optional[zast.Node]:
        """Look up a member of an inline unit by name. Used by macro-style
        inlining of unit-level numeric constants accessed via a dotted
        path (`m.X`)."""
        unit_def = self._find_unit_def_by_name(unit_name)
        if unit_def is None or unit_def.nodetype != NodeType.UNIT:
            return None
        return cast(zast.Unit, unit_def).body.get(member)

    def _inline_const_lookup(self, atom: zast.AtomId, name: str) -> Optional[str]:
        """Resolve a unit-level numeric constant reference to its literal
        C value. Prefers the atom's stamped const_value (carries any
        use-site coerced type); falls back to the defining node."""
        if self._node_const_value(atom) is not None:
            return self._emit_const_value(atom)
        defn = self._find_unit_def_by_name(name)
        if defn is None:
            return None
        return self._inline_const_via_defn(defn)

    def _inline_const_via_defn(self, defn: zast.Node) -> Optional[str]:
        """Inline a unit-level numeric constant from its definition node.
        Reads the typechecker-stamped const_value when present,
        otherwise parses the defn directly (covers patterns the
        typechecker didn't demand-resolve, e.g. match-arm patterns
        under non-const subjects)."""
        if self._node_const_value(defn) is not None:
            return self._emit_const_value(defn)
        if defn.nodetype == NodeType.ATOMID and _is_numeric_id(
            cast(zast.AtomId, defn).name
        ):
            _, value, err = parse_number(cast(zast.AtomId, defn).name)
            if not err and type(value) is int:
                self.needs_stdint = True
                return str(value)
            if not err and type(value) is float:
                return repr(value)
        return None

    def _build_source_map(self, output: str) -> None:
        """Build source_map: for each C output line, the AST node ID that produced it.

        Uses the tracked node IDs from struct_defs, func_defs, data_defs.
        Lines from boilerplate (includes, z_String_t runtime, main wrapper) get None.
        """
        # build a set of (line_start_offset, node_id) from tracked sections
        offset_to_node: List[tuple] = []
        for section in (self.struct_defs, self.func_defs, self.data_defs):
            if section.is_tracked_list:
                for text, nid in zip(section, section.node_ids):
                    pos = output.find(text)
                    if pos >= 0:
                        offset_to_node.append((pos, len(text), nid))

        # sort by position
        offset_to_node.sort()

        # for each output line, find which section block it falls in
        lines = output.split("\n")
        self.source_map = []
        char_pos = 0
        block_idx = 0
        for line in lines:
            line_end = char_pos + len(line)
            node_id = None
            # find the block that contains this line
            while block_idx < len(offset_to_node):
                bpos, blen, bnid = offset_to_node[block_idx]
                if char_pos >= bpos and char_pos < bpos + blen:
                    node_id = bnid
                    break
                if bpos > char_pos:
                    break
                block_idx += 1
            # re-check current block (block_idx may have advanced past)
            if block_idx > 0:
                bpos, blen, bnid = offset_to_node[block_idx - 1]
                if char_pos >= bpos and char_pos < bpos + blen:
                    node_id = bnid
            self.source_map.append(node_id)
            char_pos = line_end + 1  # +1 for the \n

    def _node_const_value(self, node: zast.Node):
        """Read `const_value` for `node` from `ZTyping.node_const_value`.
        Unwraps `zast.Expression` to its inner subtype, then tries the
        inner nodeid first and falls back to the outer Expression's
        nodeid (typecheck stamps both for some paths). Returns `None`
        if no entry exists."""
        target = node
        while target.nodetype == NodeType.EXPRESSION:
            target = cast(zast.Expression, target).expression
        v = self.typing.node_const_value.get(target.nodeid)
        if v is not None:
            return v
        return self.typing.node_const_value.get(node.nodeid)

    def _node_ztype(self, node: zast.Node) -> Optional[ZType]:
        """Read the resolved `ZType` for `node`. Unwraps
        `zast.Expression` to its inner subtype, then tries the inner
        nodeid first, falling back to the outer Expression's nodeid
        (typecheck stamps both for some paths — typeref paths in
        record/class fields, in particular, only carry the entry on
        the outer Expression).

        Reads `ZTyping.node_type` directly."""
        target = node
        while target.nodetype == NodeType.EXPRESSION:
            target = cast(zast.Expression, target).expression
        zt = self.typing.node_type.get(target.nodeid)
        if zt is not None:
            return zt
        return self.typing.node_type.get(node.nodeid)

    def _unit_def_ztype(self, node: zast.Node) -> Optional[ZType]:
        """The unit/core definition `node` resolves to, by id, or None when it
        binds to a local (carries atom_variable_id instead) or is unstamped.
        Honors typecheck's binding decision — never re-resolves by name."""
        tid = self.typing.atom_unit_def_type_id.get(node.nodeid)
        return _type_by_id(tid) if tid is not None else None

    def _dp_unit_type(self, node: zast.Node) -> Optional[ZType]:
        """The UNIT type a composite dotted-path selector resolves to, by id, or
        None. Reads the `dp_unit_type_id` stamp — never re-resolves by name."""
        tid = self.typing.dp_unit_type_id.get(node.nodeid)
        return _type_by_id(tid) if tid is not None else None

    def _node_typetype(self, node: zast.Node) -> "Optional[ZTypeType]":
        """The `typetype` of the type typecheck stamped on `node`, or None.
        Reads node_type — never re-resolves by name."""
        zt = self._node_ztype(node)
        return zt.typetype if zt is not None else None

    def _unit_def_typetype(self, node: zast.Node) -> "Optional[ZTypeType]":
        """The `typetype` of the unit/core definition `node` binds to, or None
        when it is a local or a non-unit-level reference (e.g. a sibling
        method). Distinguishes a top-level function from a sibling method,
        which `node_type` cannot."""
        zt = self._unit_def_ztype(node)
        return zt.typetype if zt is not None else None

    def _is_definition_ref(self, atom: zast.AtomId) -> bool:
        """True when `atom` names a unit-level definition (function, type, or
        numeric-constant macro) rather than a local. Honors typecheck's binding
        decision via the same stamps `_emit_atomid_value` uses: a local carries
        `atom_variable_id`, a unit-level reference carries
        `atom_unit_def_type_id`; constant macros have neither but are tracked in
        `_const_names`."""
        if atom.nodeid in self.typing.atom_variable_id:
            return False
        if self._unit_def_ztype(atom) is not None:
            return True
        return atom.name in self._const_names

    def _var_cname(self, atom: zast.AtomId) -> Optional[str]:
        """Stored C name for a local-variable reference, read by the
        `atom_variable_id` stamp → `variable_cname`. None when `atom` is not a
        local reference (unit-level def, constant, numeric literal)."""
        vid = self.typing.atom_variable_id.get(atom.nodeid)
        if vid is None:
            return None
        return self.typing.variable_cname.get(vid)

    def _def_cname(self, node: zast.Node) -> Optional[str]:
        """Stored C name for a variable *declaration* node (parameter path,
        assignment, with-binding), read by the `def_variable_id` stamp →
        `variable_cname`. None when the node was not stamped (e.g. for-loop
        bindings, which register via `symtab.define` and have no variable_id)."""
        vid = self.typing.def_variable_id.get(node.nodeid)
        if vid is None:
            return None
        return self.typing.variable_cname.get(vid)

    def _synth_method_cname(self, parent: Optional[ZType], method: str) -> str:
        """C name of a synthesised collection method (List/Map/Set/array/str/
        view/iterator). Reads the typechecker-assigned cname off the method
        child of the *unwrapped base* mono (synth methods hang off the base,
        not a typedef wrapper). The inline fallback covers only the case where
        the child or its cname is absent, which is output-identical."""
        base = _unwrap_typedef(parent) if parent is not None else None
        m = self.typing.child_of(base, method) if base is not None else None
        if m is not None and m.cname:
            return m.cname
        return f"z_{_mono_name(parent)}_{method}"

    def _conformance_of(
        self, impl_zt: Optional[ZType], spec_zt: Optional[ZType], label: str
    ) -> Optional[ZConformance]:
        """The conformance entity for `impl as { label: spec }`, by the impl/spec
        type-id pair + label. None when either type is unresolved or no
        conformance was recorded. The entity holds the pre-composed C names of
        the conformance helpers (wrappers / vtable / create / destroy)."""
        if impl_zt is None or spec_zt is None:
            return None
        return self._conformance_by_triple.get(
            (impl_zt.type_id, spec_zt.type_id, label)
        )

    def _conformance_pair(
        self, impl_zt: Optional[ZType], spec_zt: Optional[ZType]
    ) -> Optional[ZConformance]:
        """The conformance entity for an (impl, spec) pair regardless of label,
        for consumer sites that have both resolved types but no label in scope.
        None when unresolved or unrecorded."""
        if impl_zt is None or spec_zt is None:
            return None
        return self._conformance_by_pair.get((impl_zt.type_id, spec_zt.type_id))

    def _def_vid(self, node: zast.Node) -> Optional[int]:
        """The variable_id a declaration node binds (from `def_variable_id`),
        or None when unstamped. Used to register a binding in the id-keyed
        class_params / borrowed_vars / cleanup sets."""
        return self.typing.def_variable_id.get(node.nodeid)

    def _operand_vid(self, op: zast.Node) -> Optional[int]:
        """The variable_id an operand reduces to for set-membership tests:
        unwrap Expression wrappers, strip the value-preserving projections
        (`.take`/`.lock`/`.private`) that emit as the bare parent, then read
        the root AtomId's identity. A hoisted alias temp carries its source
        local in `alias_root_variable_id` (it emits as that local via
        `_alias_map`); a plain reference carries `atom_variable_id`. None when
        the operand does not reduce to a bare local reference. Lets a call-arg /
        `.release` site test membership by identity rather than by the emitted
        string it would otherwise have compared."""
        n: Optional[zast.Node] = op
        while n is not None and n.nodetype == NodeType.EXPRESSION:
            n = cast(zast.Expression, n).expression
        while (
            n is not None
            and n.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, n).child.name in ("take", "lock", "private")
        ):
            n = cast(zast.DottedPath, n).parent
        if n is None or n.nodetype not in (NodeType.ATOMID, NodeType.LABELVALUE):
            return None
        alias_vid = self.typing.alias_root_variable_id.get(n.nodeid)
        if alias_vid is not None:
            return alias_vid
        return self.typing.atom_variable_id.get(n.nodeid)

    def _root_atom_vid(self, path: zast.Node) -> Optional[int]:
        """The variable_id of the root AtomId of a path, walking dotted parents
        (`a.b.c` → `a`); None if the path does not root in a local. Used where
        the prior logic walked to the path root by name."""
        n: Optional[zast.Node] = path
        while n is not None and n.nodetype == NodeType.EXPRESSION:
            n = cast(zast.Expression, n).expression
        while n is not None and n.nodetype == NodeType.DOTTEDPATH:
            n = cast(zast.DottedPath, n).parent
        if n is None or n.nodetype not in (NodeType.ATOMID, NodeType.LABELVALUE):
            return None
        alias_vid = self.typing.alias_root_variable_id.get(n.nodeid)
        if alias_vid is not None:
            return alias_vid
        return self.typing.atom_variable_id.get(n.nodeid)

    def _enclosing_type(self, func: zast.Function) -> Optional[ZType]:
        """The enclosing record/class type of a method, by id, or None for a
        top-level function. Read from the `enclosing_type_id` stamp on the
        function's ZType — never re-resolves the enclosing name."""
        ftype = self._node_ztype(func)
        if ftype is None or ftype.enclosing_type_id < 0:
            return None
        return _type_by_id(ftype.enclosing_type_id)

    def _case_clause_match_child_id(self, clause: zast.CaseClause) -> int:
        """Read the child_id stamped on `clause.match` (the tag selector
        AtomId of a case arm)."""
        return self.typing.atom_child_id.get(clause.match.nodeid, -1)

    def _path_ztype(self, path: zast.Path) -> Optional[ZType]:
        """Resolve the `ZType` of a parser-AST `Path`-shaped node from
        `ZTyping.node_type`. Path nodes have no Expression wrapper, so
        the lookup is a single dict get."""
        return self.typing.node_type.get(path.nodeid)

    def _emit_bounds_check(
        self,
        lines: List[str],
        idx_expr: str,
        len_expr: str,
        label: str,
        idx_fmt: str = "%lu",
        idx_cast: str = "(unsigned long)",
    ) -> None:
        """Emit a bounds-check with error exit for container get/set."""
        zrt.emit_bounds_check(lines, idx_expr, len_expr, label, idx_fmt, idx_cast)

    def _emit_heap_container_create(
        self,
        lines: List[str],
        name: str,
        ctype: str,
        data_ctype: str,
    ) -> None:
        """Emit a heap-allocated container create function (map pattern)."""
        create_name = f"z_{name}_create"
        lines.append(f"static {ctype}* {create_name}(uint64_t _capacity);\n")
        lines.append(f"static {ctype}* {create_name}(uint64_t _capacity) {{\n")
        lines.append(f"    {ctype}* _this = ({ctype}*)z_xmalloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        lines.append("    _this->capacity = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            f"        _this->data = ({data_ctype}*)z_xcalloc(_capacity, sizeof({data_ctype}));\n"
        )
        lines.append("    }\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

    def _emit_stack_container_create(
        self,
        lines: List[str],
        name: str,
        ctype: str,
        data_ctype: str,
    ) -> None:
        """Emit a stack-allocated container create function (list pattern).

        Returns struct by value. Only the data buffer is heap-allocated.
        """
        create_name = f"z_{name}_create"
        lines.append(f"static {ctype} {create_name}(uint64_t _capacity);\n")
        lines.append(f"static {ctype} {create_name}(uint64_t _capacity) {{\n")
        lines.append(f"    {ctype} _this = {{0}};\n")
        lines.append("    _this.capacity = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            f"        _this.data = ({data_ctype}*)z_xcalloc(_capacity, sizeof({data_ctype}));\n"
        )
        lines.append("    }\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

    def _temp_name(self, prefix: str) -> str:
        """Generate a unique temporary variable name with function NodeID."""
        self._scope.temp_counter += 1
        nid = self._scope.func_nodeid
        return f"_{prefix}{nid}_{self._scope.temp_counter}"

    def _alloc_temp(self, expr: str) -> str:
        """Allocate a temporary variable for a stack-allocated string expression."""
        name = self._temp_name("t")
        indent = self._indent()
        self._temp.decls.append(f"{indent}z_String_t {name} = {expr};\n")
        self._temp.frees.append(name)
        self._temp.string_set.add(name)
        return name

    def _alloc_arg_temp(self, ctype: str, expr: str) -> str:
        """Allocate a stable C local for an argument (not freed).

        Phase C step 2 moved per-arg call hoisting into the typechecker
        (see `_hoist_arg` in ztypecheck.py). This helper now has only
        one consumer left — the list `.extend` special path needs a
        named storage slot to take the address of (`&{from_tmp}`); a
        synth typecheck temp would suffice here too, but extend's
        signature wants a pointer at the C level so we keep the
        emit-time temp for that one site.
        """
        name = self._temp_name("a")
        indent = self._indent()
        self._temp.decls.append(f"{indent}{ctype} {name} = {expr};\n")
        return name

    def _emit_field_cleanup(
        self, access: str, ftype: ZType, indent: str = "    "
    ) -> str:
        """Emit cleanup code for a single field/variable given its ZType.

        Returns a C statement string (with newline) or empty string if no cleanup needed.
        For stack-allocated types (non-heap), prepends & to pass address to destructor.
        """
        if (ftype.destructor_name is not None) and ftype.destructor_name:
            # Stack-allocated types need & to get a pointer for the destructor
            if not ftype.is_heap_allocated:
                return f"{indent}{ftype.destructor_name}(&{access});\n"
            return f"{indent}{ftype.destructor_name}({access});\n"
        return ""

    def _emit_scope_cleanup(
        self, indent: str, exclude_var: Optional[str] = None
    ) -> str:
        """Emit cleanup code for all tracked variables across every active
        block scope (used at function exit and at `return`/`panic` sites).

        Flattens `cleanup_stack`: outermost frame first, then any nested
        block frames in push order. Reversed iteration gives reverse
        declaration order across the entire function. If `exclude_var`
        is set (return value), that variable is skipped.
        """
        result = ""
        all_vars: list = []
        for frame in self._scope.cleanup_stack:
            for v in frame:
                all_vars.append(v)
        for var_id, var_type in reversed(all_vars):
            var_name = self.typing.variable_cname.get(var_id)
            if var_name is not None and var_name != exclude_var:
                result += self._emit_field_cleanup(var_name, var_type, indent)
        return result

    def _static_string(self, escaped: str) -> str:
        """Return the name of a static z_String_t for this literal, deduplicating."""
        if escaped in self._string_literals:
            return self._string_literals[escaped]
        self._string_literal_counter += 1
        name = f"_zs{self._string_literal_counter}"
        self._string_literals[escaped] = name
        return name

    def _track_stdlib_unit_native(
        self, mangled: str, ftype: "Optional[ZType]" = None
    ) -> None:
        """Record use of an io- or os-unit native so emit_runtime_io /
        emit_runtime_os includes its C body. Per-name granularity keeps
        unused helpers out of every compiled program. Called from every
        path that turns a callable AST node into a C name, not just
        `_emit_callable_expr` — definition-name dotted paths are
        short-circuited earlier and would otherwise miss tracking.

        `ftype` is the function's ZType (when available at the call
        site) and carries `builtin_func` for header dispatch on
        specific natives — see ZBuiltinFunc."""
        # Splitter / linesiter iterator methods (Phase S3). Track
        # under the stringview natives set so the shared impl struct
        # + call function emit in the correct late slot.
        if mangled == "z_Splitter_call":
            self.needs_stringview = True
            self.needs_string = True
            self.needs_stringview_natives.add("split")
            return
        if mangled == "z_LinesIter_call":
            self.needs_stringview = True
            self.needs_string = True
            self.needs_stringview_natives.add("lines")
            return
        if mangled == "z_CpIter_call":
            self.needs_stringview = True
            self.needs_string = True
            self.needs_stringview_natives.add("codepoints")
            return
        if mangled == "z_stringJoin":
            # Phase S7 free function. Lives in the stringview late
            # slot so it can reference z_List_String_t.
            self.needs_stringview = True
            self.needs_string = True
            self.needs_stringview_natives.add("join")
            return
        # cli unit natives. Registration, parse, help_text, and
        # parsed class accessors. All routed through the cli late
        # emission slot so they can reference spec / parsed /
        # list_<def>_t / result_parsed_clierror_t.
        if mangled == "z_Spec_create":
            self.needs_cli = True
            self.needs_string = True
            self.needs_cli_natives.add("spec_create")
            return
        if mangled in (
            "z_cli_addFlag",
            "z_cli_addOption",
            "z_cli_addPositional",
            "z_cli_parse",
            "z_cli_helpText",
        ):
            self.needs_cli = True
            self.needs_string = True
            self.needs_cli_natives.add(mangled[len("z_cli_") :])
            return
        if mangled in (
            "z_Parsed_hasFlag",
            "z_Parsed_option",
            "z_Parsed_positional",
        ):
            self.needs_cli = True
            self.needs_string = True
            self.needs_cli_natives.add(mangled[len("z_Parsed_") :])
            return
        if mangled.startswith("z_StringView_"):
            # stringview method natives (Phase S1+). Tracked separately
            # so emit_runtime_stringview_natives can per-name gate.
            # Skip the pre-existing comparison / conversion primitives
            # baked into z_StringView.inc — they always emit.
            name = mangled[len("z_StringView_") :]
            if name in {
                "isEmpty",
                "isAscii",
                "startsWith",
                "endsWith",
                "contains",
                "indexOf",
                "lastIndexOf",
                "byteAt",
                "trim",
                "trimStart",
                "trimEnd",
                "stripPrefix",
                "stripSuffix",
                "split",
                "splitOnce",
                "lines",
                "toLowerAscii",
                "toUpperAscii",
                "replace",
                "replaceFirst",
                "repeated",
                "concat",
                "count",
                "codepoints",
                "parseI64",
                "parseU64",
                "parseF64",
            }:
                self.needs_stringview = True
                self.needs_string = True  # memcmp / strchr live in string.h
                self.needs_stringview_natives.add(name)
                # parseF64 uses strtod + errno (ERANGE).
                if ftype is not None and ftype.builtin_func == ZBuiltinFunc.PARSE_F64:
                    self.needs_io = True
            return
        if mangled.startswith("z_io_"):
            self.needs_io = True
            self.needs_stdio = True
            self.needs_io_natives.add(mangled[len("z_io_") :])
        elif mangled.startswith("z_os_"):
            self.needs_os = True
            self.needs_stdlib = True
            name = mangled[len("z_os_") :]
            self.needs_os_natives.add(name)
            # os natives that surface ioerror share io's errno helper
            # and header set. env_names pulls strchr/strlen from string.h.
            # pid/ppid need unistd.h which needs_io also provides.
            # get_env, set_env, unset_env, set_cwd take stringview path/
            # key/value and need the shared `z_sv_to_cstr` helper that
            # emit_runtime_io owns.
            if name in (
                "env",
                "setEnv",
                "unsetEnv",
                "cwd",
                "setCwd",
                "pid",
                "ppid",
                "userName",
                "homeDir",
                "hostname",
            ):
                self.needs_io = True
            if ftype is not None and ftype.builtin_func == ZBuiltinFunc.ENV_NAMES:
                self.needs_string = True

    def _emit_callable_expr(self, call: zast.Call) -> str:
        """Emit the callable expression for a function call.

        Handles function pointer fields (struct field access) vs regular functions.
        """
        ftype = self._node_ztype(call.callable)
        if (
            ftype
            and ftype.typetype == ZTypeType.FUNCTION
            and ftype.generic_origin is not None
        ):
            return (
                ftype.cname
                if ftype.cname and not ftype.is_native
                else _mangle_func(ftype.name)
            )

        # `meta.create` inside a type's method body resolves to that
        # type's raw allocator. The typechecker stamps the callable's
        # node_type as `<EnclosingType>.meta.create`; the emitter must
        # spell that as `z_<type>_meta_create(...)` regardless of
        # whether the call is paren-wrapped or not.
        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
            and cast(zast.AtomId, cast(zast.DottedPath, call.callable).parent).name
            == "meta"  # ztc-string-compare-ok: meta.create dispatch
            and cast(zast.DottedPath, call.callable).child.name
            == "create"  # ztc-string-compare-ok: meta.create dispatch
            and self._current_enclosing_type_name
        ):
            cbase = _cbase_of(
                self._current_enclosing_type, self._current_enclosing_type_name
            )
            return f"{cbase}_meta_create"

        # check if this is a function pointer field call (e.g. c.op)
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            ftype = self._node_ztype(call.callable)
            if ftype and ftype.typetype == ZTypeType.FUNCTION:
                func_name = ftype.name
                if func_name in self._is_func_fields:
                    return self._emit_dotted_path_value(
                        cast(zast.DottedPath, call.callable)
                    )
                # use the resolved type name for proper qualification
                # (handles subunit functions like mymod.helper.square)
                if "." in func_name:
                    mangled = (
                        ftype.cname
                        if ftype.cname and not ftype.is_native
                        else _mangle_func(func_name)
                    )
                    self._track_stdlib_unit_native(mangled, ftype)
                    return mangled
        # Bare free-function call. A dependency unit's sibling call (e.g.
        # `escapeStr` inside zlexer) must emit the function's qualified cname
        # (z_zlexer_escapeStr), not the bare `_mangle_func(name)` (z_escapeStr).
        # Scoped to a definition-ref ATOMID resolving to a non-native function:
        # method calls on variables and natives keep the legacy path (their
        # ftype.cname can name the call site / not match a runtime symbol).
        if call.callable.nodetype == NodeType.ATOMID:
            atom_ft = self._node_ztype(call.callable)
            if (
                atom_ft is not None
                and atom_ft.typetype == ZTypeType.FUNCTION
                and atom_ft.cname
                and not atom_ft.is_native
                and self._is_definition_ref(cast(zast.AtomId, call.callable))
            ):
                self._track_stdlib_unit_native(atom_ft.cname, atom_ft)
                return atom_ft.cname
        callable_name = self._get_callable_name(call.callable)
        callable_ft = self._node_ztype(call.callable)
        # A unit-level definition (dotted name, or a definition-ref atom) is a
        # function: read its stored cname. A bare local holding a callable
        # mangles as a variable.
        is_func_ref = "." in callable_name or (
            call.callable.nodetype == NodeType.ATOMID
            and self._is_definition_ref(cast(zast.AtomId, call.callable))
        )
        if is_func_ref:
            mangled = (
                callable_ft.cname
                if callable_ft is not None
                and callable_ft.typetype == ZTypeType.FUNCTION
                and callable_ft.cname
                and not callable_ft.is_native
                else _mangle_func(callable_name)
            )
        else:
            mangled = _mangle_var(callable_name)
        self._track_stdlib_unit_native(mangled, callable_ft)
        return mangled

    def _qualify(self, prefix: str, name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

    def _is_generic_template(self, defn: zast.TypeDefinition) -> bool:
        """Check if a definition is a generic template (has .generic in items/as_items)."""
        items = None
        if defn.nodetype in (
            NodeType.RECORD,
            NodeType.UNION,
            NodeType.VARIANT,
            NodeType.CLASS,
        ):
            items = cast(zast.ObjectDef, defn).as_items
        elif defn.nodetype == NodeType.FUNCTION:
            func = cast(zast.Function, defn)
            items = func.as_items if func.as_items else func.parameters
        elif defn.nodetype == NodeType.PROTOCOL:
            items = cast(zast.ObjectDef, defn).is_items
        elif defn.nodetype == NodeType.UNIT:
            items = cast(zast.Unit, defn).body
        if items is None:
            return False
        for fpath in items.values():
            # direct form: any.generic
            if (
                fpath.nodetype == NodeType.DOTTEDPATH
                and cast(zast.DottedPath, fpath).child.nodetype == NodeType.ATOMID
            ):
                if cast(zast.DottedPath, fpath).child.name in (
                    "generic",
                    "valtype",
                    "reftype",
                ):
                    return True
            # call form: (any.generic default: type)
            if fpath.nodetype == NodeType.EXPRESSION:
                inner = cast(zast.Expression, fpath).expression
                if inner.nodetype == NodeType.CALL:
                    callable_node = cast(zast.Call, inner).callable
                    if (
                        callable_node.nodetype == NodeType.DOTTEDPATH
                        and cast(zast.DottedPath, callable_node).child.nodetype
                        == NodeType.ATOMID
                        and cast(zast.DottedPath, callable_node).child.name
                        in ("generic", "valtype", "reftype")
                    ):
                        return True
        return False

    def _is_typedef_defn(self, defn: "zast.ObjectDef") -> bool:
        """Check if a type definition is a typedef (single .typedef item)."""
        items = defn.is_items
        if len(items) != 1:
            return False
        fpath = next(iter(items.values()))
        return (
            fpath.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, fpath).child.nodetype == NodeType.ATOMID
            and cast(zast.DottedPath, fpath).child.name == "typedef"
        )

    def _typedef_base_name(self, defn: "zast.ObjectDef") -> str:
        """Extract the base type name from a typedef definition."""
        fpath = next(iter(defn.is_items.values()))
        assert fpath.nodetype == NodeType.DOTTEDPATH
        parent = cast(zast.DottedPath, fpath).parent
        if parent.nodetype == NodeType.ATOMID:
            return cast(zast.AtomId, parent).name
        return ""

    def _collect_pre_emission(self, prefix: str, body: dict) -> None:
        """Pre-emission pass: collect supplementary data not derivable from ZType.

        Gathers _const_names, _protocol_defs, _is_func_fields, _proto_conformance,
        the id-keyed facet/protocol def maps, and _facet_conformer_ids in a single
        walk.
        """
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            defn_type = defn.nodetype
            if defn_type == NodeType.UNIT:
                self._collect_pre_emission(qname, defn.body)
            elif defn_type in (NodeType.RECORD, NodeType.CLASS):
                if not self._is_generic_template(defn):
                    for mname in defn.functions():
                        self._is_func_fields.add(f"{qname}.{mname}")
                    for label, apath in defn.as_items.items():
                        # `:foo` parses as LABELVALUE, `foo: bar` parses as
                        # ATOMID — both carry a .name. LabelValue subclasses
                        # AtomId but has its own NodeType tag.
                        proto_name = (
                            cast(zast.AtomId, apath).name
                            if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                            else None
                        )
                        apath_zt = self._node_ztype(apath)
                        proto_tt = apath_zt.typetype if apath_zt is not None else None
                        if proto_name and proto_tt == ZTypeType.PROTOCOL:
                            self._proto_conformance[(qname, proto_name)] = label
                        if proto_name and proto_tt == ZTypeType.FACET:
                            self._proto_conformance[(qname, proto_name)] = label
                            impl_zt = self._node_ztype(defn)
                            if apath_zt is not None and impl_zt is not None:
                                self._facet_conformer_ids.setdefault(
                                    apath_zt.type_id, []
                                ).append(impl_zt.type_id)
                        # constant in 'as' section
                        if self._node_const_value(apath) is not None:
                            self._const_names.add(f"{qname}.{label}")
            elif defn_type in (NodeType.UNION, NodeType.VARIANT):
                if not self._is_generic_template(defn):
                    for mname in defn.functions():
                        self._is_func_fields.add(f"{qname}.{mname}")
                    for label, apath in defn.as_items.items():
                        if self._node_const_value(apath) is not None:
                            self._const_names.add(f"{qname}.{label}")
            elif defn_type == NodeType.PROTOCOL:
                if not self._is_generic_template(defn):
                    self._protocol_defs[qname] = defn
                    proto_zt = self._node_ztype(defn)
                    if proto_zt is not None:
                        self._protocol_def_by_id[proto_zt.type_id] = defn
            elif defn_type == NodeType.FACET:
                if not self._is_generic_template(defn):
                    facet_zt = self._node_ztype(defn)
                    if facet_zt is not None:
                        self._facet_def_by_id[facet_zt.type_id] = defn
            elif defn_type == NodeType.ATOMID and _is_numeric_id(defn.name):
                self._const_names.add(qname)
            elif self._node_const_value(defn) is not None:
                # unit-level expression that folded to a constant
                self._const_names.add(qname)

    # Library units emitted via the dedicated system-unit paths (or, for
    # generics, as monos). A cross-unit dependency is any OTHER user file unit
    # imported by main.
    _SYSTEM_UNIT_NAMES = ("system", "core", "io", "os", "cli", "collections")

    def _dependency_file_units(self) -> "list[tuple[str, zast.Unit]]":
        """User file units other than main. Their TYPE definitions must be
        emitted cross-unit (their functions are already emitted by the
        file-unit-function loop). System library units and generic templates
        are skipped."""
        out: "list[tuple[str, zast.Unit]]" = []
        for unitname, unit in self.program.units.items():
            if (
                unitname in self._SYSTEM_UNIT_NAMES
                or unitname == self.program.mainunitname
            ):
                continue
            if self._is_generic_template(unit):
                continue
            out.append((unitname, cast(zast.Unit, unit)))
        return out

    def _emit_file_unit_functions(self, prefix: str, body: dict) -> None:
        """Emit functions from a file unit body, recursing into subunits."""
        for name, defn in body.items():
            qname = f"{prefix}.{name}"
            if defn.nodetype == NodeType.FUNCTION and cast(zast.Function, defn).body:
                self._current_node_id = defn.nodeid
                self._emit_function(qname, cast(zast.Function, defn))
            elif defn.nodetype == NodeType.UNIT:
                if not self._is_generic_template(defn):
                    self._emit_file_unit_functions(qname, defn.body)

    def _pre_register_fields(self, prefix: str, body: dict) -> None:
        """Pre-register field names/ctypes for all records and classes.

        This ensures _build_meta_create_args works correctly even when a
        function references a type that appears later in the source file.
        """
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if self._is_generic_template(defn):
                continue
            defn_type = defn.nodetype
            if defn_type == NodeType.UNIT:
                self._pre_register_fields(qname, defn.body)
            elif defn_type in (NodeType.RECORD, NodeType.CLASS):
                # Key field-info by the type's dot-free `ztype.name` (the stable
                # identifier the construction-arg builders look up by, via
                # `rec_type.name`); for a dependency-unit type that differs from
                # the dotted AST path `qname`.
                zt = self._node_ztype(defn)
                key = zt.name if zt is not None and zt.name else qname
                if key not in self._type_field_names:
                    if self._is_typedef_defn(defn):
                        continue
                    _, field_names, field_ctypes = self._collect_field_params(
                        qname, defn.is_items, defn.functions(), zt
                    )
                    self._type_field_names[key] = field_names
                    self._type_field_ctypes[key] = field_ctypes
                    self._type_field_defaults[key] = self._extract_field_defaults(
                        qname, defn.is_items, defn.functions()
                    )

    def _emit_unit_definitions(self, prefix: str, body: dict, defn_filter=None) -> None:
        """Recursively emit definitions from a unit body.

        When `defn_filter` is given, only emit defs for which the
        callable returns True. Inline units are always recursed into;
        the filter is applied to each leaf def. Used by the
        late-mono-aware split: emit user defs that don't reference any
        late mono first, then late monos, then user defs that do.
        """
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if self._is_generic_template(defn):
                continue
            # tag emitted output with the source AST node ID
            self._current_node_id = defn.nodeid if hasattr(defn, "nodeid") else None
            defn_type = defn.nodetype
            if defn_type == NodeType.UNIT:
                # always recurse into units; filter applies per-leaf
                self._emit_unit_definitions(qname, defn.body, defn_filter)
                continue
            if defn_filter is not None and not defn_filter(name, defn):
                continue
            if defn_type == NodeType.RECORD:
                self._emit_record(qname, defn)
            elif defn_type == NodeType.CLASS:
                self._emit_class(qname, defn)
            elif defn_type == NodeType.UNION:
                self._emit_union(qname, defn)
            elif defn_type == NodeType.VARIANT:
                self._emit_variant(qname, defn)
            elif defn_type == NodeType.PROTOCOL:
                self._emit_protocol(qname, defn)
            elif defn_type == NodeType.FACET:
                pass  # facets emitted in deferred pass
            elif defn_type == NodeType.FUNCTION:
                if defn.body:
                    self._emit_func_typedef(qname, defn)
                    self._emit_function(qname, defn)
                else:
                    self._emit_spec_typedef(qname, defn)
            elif defn_type == NodeType.DATA:
                self._emit_data(qname, defn)
            elif (
                defn_type == NodeType.EXPRESSION
                and cast(zast.Expression, defn).expression.nodetype == NodeType.DATA
            ):
                self._emit_data(
                    qname, cast(zast.Data, cast(zast.Expression, defn).expression)
                )
            elif defn_type == NodeType.ATOMID and _is_numeric_id(defn.name):
                pass  # macro-style: value inlined at every use site
            elif type(self._node_const_value(defn)) in (
                int,
                float,
            ):
                pass  # macro-style: value inlined at every use site

    def _emit_folded_constant(self, name: str, node: zast.Node) -> None:
        """Emit a compile-time folded constant as a static const."""
        v = self._node_const_value(node)
        assert v is not None
        self.needs_stdint = True
        cname = _mangle_func(name)
        ctype = "int64_t"
        n_ztype = self._node_ztype(node)
        if n_ztype:
            ctype = TYPEMAP.get(n_ztype.name, "int64_t")
        if type(v) is float:
            self.data_defs.append(f"static const {ctype} {cname} = {repr(v)};\n")
        else:
            self.data_defs.append(f"static const {ctype} {cname} = {int(v)};\n")

    def _emit_as_constants(self, type_name: str, as_items: dict) -> None:
        """Emit static constants defined in an 'as' section."""
        for label, apath in as_items.items():
            v = self._node_const_value(apath)
            if v is not None:
                qname = f"{type_name}.{label}"
                cname = _mangle_func(qname)
                ap_ztype = self._node_ztype(apath)
                if type(v) is str:
                    # string constant: emit static stringview + alias
                    self.needs_stringview = True
                    escaped = self._escape_c_string(v)
                    sname = self._static_string(escaped)
                    self.data_defs.append(f"#define {cname} {sname}\n")
                elif type(v) is float:
                    self.needs_stdint = True
                    ctype = "int64_t"
                    if ap_ztype:
                        ctype = TYPEMAP.get(ap_ztype.name, "int64_t")
                    self.data_defs.append(f"static const {ctype} {cname} = {v};\n")
                else:
                    self.needs_stdint = True
                    ctype = "int64_t"
                    if ap_ztype:
                        ctype = TYPEMAP.get(ap_ztype.name, "int64_t")
                    self.data_defs.append(f"static const {ctype} {cname} = {int(v)};\n")

    def _emit_deferred_facets(self, prefix: str, body: dict) -> None:
        """Emit facet definitions and impls (deferred to after all conforming types)."""
        # first: emit facet struct definitions
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            defn_type = defn.nodetype
            if defn_type == NodeType.UNIT:
                self._emit_deferred_facets(qname, defn.body)
            elif defn_type == NodeType.FACET:
                if not self._is_generic_template(defn):
                    self._emit_facet(qname, defn)
            elif defn_type in (NodeType.RECORD, NodeType.VARIANT):
                if not self._is_generic_template(defn):
                    for label, apath in defn.as_items.items():
                        facet_name = (
                            cast(zast.AtomId, apath).name
                            if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                            else None
                        )
                        facet_zt = self._node_ztype(apath)
                        if (
                            facet_name
                            and facet_zt is not None
                            and facet_zt.typetype == ZTypeType.FACET
                        ):
                            self._emit_facet_impl(qname, label, facet_zt, defn)

    def _emit_io_stream_protocols(self) -> str:
        """Emit the stream protocol struct + vtable types (reader,
        writer, closer, seeker) into a deferred buffer so they land
        after the list_u8 / listview_u8 monomorphizations their
        signatures reference. Only emits when the program references
        io.file or one of the buffered wrappers (otherwise these
        vtables are dead code).
        """
        if not (self._io_file_referenced() or self._io_wrappers_referenced()):
            return ""
        io_unit = self.program.units.get("io")
        if io_unit is None:
            return ""
        saved = self.struct_defs
        self.struct_defs = TrackedList(self)
        for pname in ("Reader", "Writer", "Closer", "Seeker"):
            defn = io_unit.body.get(pname)
            if defn is not None and defn.nodetype == NodeType.PROTOCOL:
                # Only emit protocols whose ZType is resolved — an
                # unresolved protocol has spec parameters with no
                # type information and would emit `void`-typed
                # vtable entries. Programs that don't touch a given
                # stream protocol don't need its vtable struct.
                if self._node_ztype(defn) is None:
                    continue
                self._current_node_id = defn.nodeid
                self._emit_protocol(pname, cast(zast.ObjectDef, defn))
        out = "".join(self.struct_defs)
        self.struct_defs = saved
        return out

    # All four stream protocols can now be wrapped: the vtable ABI
    # takes collection params by pointer (see _proto_param_ctype),
    # which matches the native file impl without an extra adapter.
    _IO_FILE_WRAPPABLE_PROTOCOLS = ("Reader", "Writer", "Closer", "Seeker")

    def _emit_io_file_protocol_impls(self) -> str:
        """Emit vtables + wrappers + create functions for every
        protocol io.file conforms to.

        Depends on (a) the io runtime functions (z_File_read /
        z_File_write / z_File_close / z_File_flush / z_File_seek)
        being declared earlier, and (b) the protocol structs
        (z_Reader_t, z_Writer_vtable_t, ...) existing from the
        deferred stream-protocol emission. Both are guaranteed by
        the emit() pipeline — this buffer lands after both.
        """
        io_unit = self.program.units.get("io")
        if io_unit is None:
            return ""
        file_defn = io_unit.body.get("File")
        if file_defn is None or file_defn.nodetype != NodeType.CLASS:
            return ""
        file_cls = cast(zast.ObjectDef, file_defn)
        # Pull every file native into the runtime block so forward
        # declarations exist when the wrappers are compiled.
        self.needs_io_natives.update(
            {"file_close", "file_read", "file_write", "file_flush", "file_seek"}
        )
        saved = self.struct_defs
        self.struct_defs = TrackedList(self)
        for label, apath in file_cls.as_items.items():
            if label not in self._IO_FILE_WRAPPABLE_PROTOCOLS:
                continue
            proto_name = (
                cast(zast.AtomId, apath).name
                if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                else None
            )
            if proto_name and self._node_ztype(apath) is not None:
                proto = self._io_protocol_defs.get(proto_name)
                if proto is not None:
                    self._emit_protocol_impl(
                        "File", label, proto_name, file_cls, proto=proto
                    )
        out = "".join(self.struct_defs)
        self.struct_defs = saved
        return out

    def _emit_io_wrapper_classes(self) -> str:
        """Emit the bufwriter / bufreader class structs (+ destructors
        + meta_create) into a deferred buffer. Called AFTER the stream
        protocol structs (writer_t / reader_t) so the `sink: writer.lock`
        / `source: reader.lock` field types resolve, and AFTER the
        mono pass so `buf: bytes` → z_List_u8_t is defined.

        Returns the concatenated C string; assembled into parts by
        the emit pipeline before io runtime functions (which dispatch
        z_BufWriter_write / z_BufReader_read through the struct).
        """
        if not self._io_wrappers_referenced():
            return ""
        io_unit = self.program.units.get("io")
        if io_unit is None:
            return ""
        # Compute the transitive closure of wrapper-class references:
        # a class is "needed" if it's directly referenced in the AST
        # OR if a wrapper-class that IS referenced declares it as a
        # required dependency (per `_IO_WRAPPER_REQUIRES`). Without
        # the closure, e.g. a union arm typed `io.TextReader`
        # surfaces TextReader through the AST scan (post the
        # ObjectDef-recursion fix in `_walk_children`) but its
        # required `BufReader` struct never gets emitted, leaving
        # TextReader's `source: BufReader.lock` field referencing an
        # undeclared type.
        needed: set[str] = set()
        pending: list[str] = []
        for name in self._IO_WRAPPER_NAMES:
            if self._io_class_referenced(name):
                pending.append(name)
        while pending:
            n = pending.pop()
            if n in needed:
                continue
            needed.add(n)
            for req in self._IO_WRAPPER_REQUIRES.get(n, ()):
                if req not in needed:
                    pending.append(req)
        saved = self.struct_defs
        self.struct_defs = TrackedList(self)
        for name in self._IO_WRAPPER_NAMES:
            if name not in needed:
                continue
            defn = io_unit.body.get(name)
            if defn is None or defn.nodetype != NodeType.CLASS:
                continue
            defn = cast(zast.ObjectDef, defn)
            self._current_node_id = defn.nodeid
            # Skip protocol-impl emission here; it needs the runtime
            # bodies (z_BufWriter_write, ...) to be declared first.
            # The impls are emitted separately via
            # _emit_io_wrapper_protocol_impls, after emit_runtime_io.
            self._emit_class(name, defn, skip_protocol_impls=True)
            # Record the wrapper's runtime natives so emit_runtime_io
            # emits the C bodies. Granularity is per-class (not per-
            # method) because once the wrapper's struct is emitted its
            # protocol vtable already references every method body via
            # the auto-generated wrapper — emitting a subset would leave
            # undefined references. Cross-class dependencies (textwriter
            # -> bufwriter, textreader -> bufreader) are encoded in
            # _IO_WRAPPER_REQUIRES.
            self.needs_io = True
            self.needs_io_natives.update(self._io_wrapper_required_natives(name))
        out = "".join(self.struct_defs)
        self.struct_defs = saved
        return out

    def _emit_io_wrapper_protocol_impls(self) -> str:
        """Emit bufwriter / bufreader protocol vtables + wrappers. Lands
        AFTER emit_runtime_io (which defines z_BufWriter_write etc.)
        so the vtable wrappers can forward to the runtime functions
        without needing separate forward declarations."""
        io_unit = self.program.units.get("io")
        if io_unit is None:
            return ""
        # Same transitive-closure logic as `_emit_io_wrapper_classes`:
        # a wrapper's protocol-impl emit depends on its struct emit,
        # which in turn depends on its required dependencies. Keep
        # the two pass's "needed" sets aligned.
        needed: set[str] = set()
        pending: list[str] = []
        for name in self._IO_WRAPPER_NAMES:
            if self._io_class_referenced(name):
                pending.append(name)
        while pending:
            n = pending.pop()
            if n in needed:
                continue
            needed.add(n)
            for req in self._IO_WRAPPER_REQUIRES.get(n, ()):
                if req not in needed:
                    pending.append(req)
        saved = self.struct_defs
        self.struct_defs = TrackedList(self)
        for name in self._IO_WRAPPER_NAMES:
            if name not in needed:
                continue
            defn = io_unit.body.get(name)
            if defn is None or defn.nodetype != NodeType.CLASS:
                continue
            defn = cast(zast.ObjectDef, defn)
            for label, apath in defn.as_items.items():
                proto_name = (
                    cast(zast.AtomId, apath).name
                    if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                    else None
                )
                if proto_name and self._node_typetype(apath) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(
                        name, label, proto_name, defn, proto_zt=self._node_ztype(apath)
                    )
        out = "".join(self.struct_defs)
        self.struct_defs = saved
        return out

    def _emit_io_file_class(self) -> None:
        """Emit the io.file struct and its RAII destructor in struct_defs.

        The struct and destructor land early so that other type
        destructors (notably result(file, ioerror)) can call
        z_File_destroy at their own emission site. The public close
        method, z_File_close, lives in the io runtime (after the
        result/ioerror struct defs it depends on) — see
        emit_runtime_io.

        Fields: `fd` is a POSIX file descriptor; `closed` makes the
        destructor idempotent with respect to an explicit close that
        ran earlier in the scope. Destructor errors are swallowed
        (callers that want to surface close errors call .close
        explicitly before scope exit).
        """
        self.needs_stdint = True
        self.needs_io = True
        self.needs_stdio = True
        lines = [
            "typedef struct {\n",
            "    int32_t fd;\n",
            "    bool closed;\n",
            "} z_File_t;\n\n",
            "static void z_File_destroy(z_File_t* p) {\n",
            "    if (!p) return;\n",
            "    if (p->closed) return;\n",
            "    close(p->fd);\n",
            "    p->closed = true;\n",
            "}\n\n",
        ]
        self.struct_defs.append("".join(lines))

    def _emit_system_unit_definitions(
        self,
        include_cli: bool = True,
        only_cli: bool = False,
        cli_records_only: bool = False,
        cli_classes_only: bool = False,
    ) -> None:
        """Emit non-generic non-native union and variant types from
        system units (io today) so user code can reference them.

        Scope is intentionally narrow:

        - Unions/variants only. These are data shapes that the emitter
          can lay out without needing method-body resolution for
          cross-unit types.
        - Classes/records/protocols from system units are NOT emitted
          here; their method signatures can reference generics (e.g.
          `result` in stream protocol methods) that only resolve
          correctly once the per-monomorphization emission pass runs.
          Those types come online as future phases add usage-driven
          emission.

        Generic types (option, optionval, result, list, etc.) are
        emitted per-monomorphization elsewhere and skipped here.
        Native types (bool, str, ...) are in the runtime emitter.
        """
        io_file_used = self._io_file_referenced()
        # system.parseerror is emitted here too (see end of method).
        if only_cli:
            unit_order = ("cli",)
        elif include_cli:
            unit_order = ("io", "os", "cli")
        else:
            unit_order = ("io", "os")
        for unitname in unit_order:
            unit = self.program.units.get(unitname)
            if unit is None:
                continue
            for name, defn in unit.body.items():
                if getattr(defn, "is_native", False):
                    continue
                if self._is_generic_template(defn):
                    continue
                self._current_node_id = defn.nodeid
                defn_type = defn.nodetype
                if defn_type == NodeType.UNION and not cli_classes_only:
                    self._emit_union(name, cast(zast.ObjectDef, defn))
                elif defn_type == NodeType.VARIANT and not cli_classes_only:
                    self._emit_variant(name, cast(zast.ObjectDef, defn))
                elif defn_type == NodeType.RECORD and (
                    (
                        unitname == "cli"
                        and not cli_classes_only
                        and self._system_type_resolved(unitname, name)
                    )
                    or self._io_record_referenced(name)
                ):
                    self._emit_record(name, cast(zast.ObjectDef, defn))
                elif (
                    defn_type == NodeType.CLASS
                    and unitname == "cli"
                    and self._system_type_resolved(unitname, name)
                ):
                    # cli unit classes split across two emission waves:
                    # leaf classes (flagdef / optiondef / positionaldef —
                    # fields only primitives + strings) emit alongside
                    # cli records in the early pass so monos like
                    # `list of: flagdef` can reference a complete
                    # struct. Aggregator classes (spec / parsed — have
                    # list / map fields) emit in the cli_classes_only
                    # pass after the monos exist.
                    is_leaf = self._is_cli_leaf_class(cast(zast.ObjectDef, defn))
                    if cli_records_only and is_leaf:
                        self._emit_class(name, cast(zast.ObjectDef, defn))
                    elif cli_classes_only and not is_leaf:
                        self._emit_class(name, cast(zast.ObjectDef, defn))
                    elif not cli_records_only and not cli_classes_only:
                        self._emit_class(name, cast(zast.ObjectDef, defn))
                elif defn_type == NodeType.CLASS and name == "File" and io_file_used:
                    # io.file: compiler-provided class. struct +
                    # destructor + close method come from the runtime.
                    # Emit inline (here, before mono types) so
                    # result(file, ioerror) destructors can reference
                    # z_File_destroy. Skipped if `file` is never
                    # referenced — otherwise every program would drag
                    # in <unistd.h> for close() via the destructor.
                    self._emit_io_file_class()

        # system.parseerror — concrete variant used by the stringview
        # parse_* natives (Phase S6). Emitted as an inline constant
        # rather than routing through `_emit_variant` so the type
        # registration side-effects of that path don't trigger cname
        # collisions with user-declared `result` variants. Emitted
        # once, on the non-cli-only pass.
        if not only_cli:
            parseerror_c = (
                "typedef enum {\n"
                "    Z_PARSEERROR_TAG_EMPTY,\n"
                "    Z_PARSEERROR_TAG_INVALIDDIGIT,\n"
                "    Z_PARSEERROR_TAG_OVERFLOW,\n"
                "} z_parseerror_tag_t;\n\n"
                "typedef struct {\n"
                "    z_parseerror_tag_t tag;\n"
                "} z_parseerror_t;\n\n"
                "static bool z_parseerror_eq(z_parseerror_t a, z_parseerror_t b) {\n"
                "    return a.tag == b.tag;\n"
                "}\n\n"
                "static void z_parseerror_destroy(z_parseerror_t* p) { (void)p; }\n\n"
            )
            self.struct_defs.append(parseerror_c)

    def _is_cli_leaf_class(self, defn: "zast.ObjectDef") -> bool:
        """A cli-unit class is "leaf" if its fields are only primitives,
        bools, or strings — i.e. the struct can be laid out without
        any user-defined or monomorphized collection types declared
        first. Leaf cli classes (flagdef / optiondef / positionaldef)
        emit in the same early pass as cli records so that monos like
        `list of: flagdef` can resolve in the early-mono phase and the
        cli aggregator classes (spec / parsed) that use those lists
        emit after the monos.
        """
        for _fname, fpath in defn.is_items.items():
            ftype = self._node_ztype(fpath)
            if ftype is None:
                return False
            if ftype.generic_origin is not None:
                return False  # list / map / option / result etc.
            # Numeric / bool / null — native records in TYPEMAP are OK.
            if ftype.name in TYPEMAP:
                continue
            # string / stringview classes are self-contained by layout.
            if ftype.typetype == ZTypeType.CLASS and ftype.subtype in (
                ZSubType.STRING,
                ZSubType.STRINGVIEW,
            ):
                continue
            # Anything else references a user or cli aggregate → not leaf.
            return False
        return True

    def _system_type_resolved(self, unitname: str, name: str) -> bool:
        """True if a type declared in a system unit has been resolved
        by demand-driven lookup (i.e., referenced somewhere in user
        code). Emitting an unresolved system type produces `void`
        fields because its children haven't been type-checked.
        """
        key = f"{unitname}.{name}"
        t = self.typing.resolved.get(key)
        if t is None:
            return False
        # A resolved record/class has its fields registered as
        # children with non-None types.
        if self.typing.child_count(t) == 0:
            return False
        return True

    def _collect_user_type_names(self, body: dict) -> "set[str]":
        """Collect the set of type names declared in the main-unit
        body (and nested inline units) plus late-emitted cli-unit
        classes. Used by the struct-emission ordering pass to split
        monos that depend on these types from monos that only need
        system types.

        cli.spec and cli.parsed are classified as "user-like" here
        because their struct emission is deferred to the
        cli_classes_only pass (after monos). Monos that reference
        them — e.g. `result(parsed, clierror)` — must therefore
        land in the late-mono group too.
        """
        names: set[str] = set()

        def visit(scope_body: dict) -> None:
            for name, defn in scope_body.items():
                if defn.nodetype in (
                    NodeType.RECORD,
                    NodeType.CLASS,
                    NodeType.UNION,
                    NodeType.VARIANT,
                    NodeType.PROTOCOL,
                    NodeType.FACET,
                ):
                    # Use the resolved type's dot-free name (e.g.
                    # `zlexer_tokstatetype` for a dependency-unit member) so it
                    # matches the `ztype.name` that `_mono_depends_on_user`
                    # tests — a monomorphisation embedding a dependency type
                    # must be classified late and emitted after it.
                    zt = self._node_ztype(defn)
                    names.add(zt.name if zt is not None else name)
                if defn.nodetype == NodeType.UNIT:
                    visit(cast(zast.Unit, defn).body)

        visit(body)
        cli_unit = self.program.units.get("cli")
        if cli_unit is not None:
            for name, defn in cli_unit.body.items():
                if defn.nodetype == NodeType.CLASS:
                    # Leaf cli classes (flagdef / optiondef / positionaldef)
                    # are emitted in the early pass alongside cli records;
                    # monos over them are early, not late.
                    if self._is_cli_leaf_class(cast(zast.ObjectDef, defn)):
                        continue
                    names.add(name)
        return names

    def _mono_depends_on_user(self, mono_type: "ZType", user_names: "set[str]") -> bool:
        """Does `mono_type`'s layout reference any user-declared type?
        Used to defer emission of such monos until after user
        struct_defs are produced.

        Walks both `children` (struct fields on user records/classes
        and monomorphized records/unions) AND `generic_args` (element
        types on list / map / array monos, whose element type lives
        under the `of` / `key` / `value` keys rather than as a child
        field).
        """
        seen: set[int] = set()

        def check(t: "ZType") -> bool:
            if id(t) in seen:
                return False
            seen.add(id(t))
            if t.typetype == ZTypeType.FUNCTION:
                return False
            if t.name in user_names:
                return True
            for _, ga in self.typing.generic_args_of(t):
                if ga is None:
                    continue
                if check(ga):
                    return True
            return False

        for child in self.typing.child_types_of(mono_type):
            if check(child):
                return True
        for _, ga in self.typing.generic_args_of(mono_type):
            if ga is None:
                continue
            if check(ga):
                return True
        return False

    def _user_defn_references_type(
        self, defn_name: str, defn: "zast.Node", type_names: "set[str]"
    ) -> bool:
        """True when any of the type def named `defn_name`'s field
        types is named in `type_names`. Used to split user defs into
        "no late-mono deps" (emit before late monos) and "has late-mono
        deps" (emit after). Walks fields shallowly: a field of
        `(List of: T)` is named `List_T` after monomorphization and
        will match if `List_T` is in `type_names`. We don't recurse
        into the field's children because the question is whether THIS
        user def's emitted struct references the late mono by name --
        which is exactly the case a field type captures.

        The binding name is taken from the unit-body dict key (passed
        in by the caller) because ObjectDef has no `.name` attribute.
        """
        if defn.nodetype not in (
            NodeType.RECORD,
            NodeType.CLASS,
            NodeType.UNION,
            NodeType.VARIANT,
        ):
            return False
        ztype = self._node_ztype(defn)
        if ztype is None:
            return False
        for _fn, ft in self.typing.children_of(ztype):
            if ft.typetype == ZTypeType.FUNCTION:
                continue
            if ft.name in type_names:
                return True
        return False

    def _io_record_referenced(self, name: str) -> bool:
        """True when a system io record is used by the program (e.g.
        as the ok-arm payload of a monomorphized result). Avoids
        emitting dead struct definitions for unused types."""
        for mono, _ in self.typing.mono_types:
            for child in self.typing.child_types_of(mono):
                if child.typetype == ZTypeType.RECORD and child.name == name:
                    return True
        return False

    def _io_file_referenced(self) -> bool:
        """True when the program references io.file anywhere that the
        emitter needs to materialise its struct/destructor (e.g. as
        the ok-arm of a result monomorphization, a local, or the
        static backing store for io.stdin / io.stdout / io.stderr)."""
        if self.needs_io_natives & {"stdin", "stdout", "stderr"}:
            return True
        for mono, _ in self.typing.mono_types:
            for child in self.typing.child_types_of(mono):
                if child.typetype == ZTypeType.CLASS and child.name == "File":
                    return True
        # Scan the main unit AST for `io.<std-stream>` paths; those
        # force file-struct emission even before function-body
        # dispatch populates `needs_io_natives`.
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit is not None and self._ast_uses_std_streams(mainunit.body):
            return True
        return False

    _STD_STREAM_NAMES = ("stdin", "stdout", "stderr")
    # Emission order matters: textwriter wraps bufwriter, so
    # bufwriter's struct must be declared before textwriter's.
    _IO_WRAPPER_NAMES = ("BufWriter", "BufReader", "TextWriter", "TextReader")

    # Runtime-native symbols each wrapper class directly needs. Used by
    # _emit_io_wrapper_classes to populate needs_io_natives. Keeping
    # this as data (rather than an if/elif chain) makes the
    # cross-class dependencies in _IO_WRAPPER_REQUIRES explicit.
    _IO_WRAPPER_NATIVES: dict[str, frozenset[str]] = {
        "BufWriter": frozenset(
            {"bufwriter_create", "bufwriter_write", "bufwriter_flush"}
        ),
        "BufReader": frozenset({"bufreader_create", "bufreader_read"}),
        "TextWriter": frozenset(
            {
                "textwriter_create",
                "textwriter_write",
                "textwriter_write_line",
                "textwriter_flush",
            }
        ),
        # textreader_call is the iterator hook used by `for line: tr loop`;
        # always emitted so the protocol vtable has it.
        "TextReader": frozenset(
            {"textreader_create", "textreader_read_line", "textreader_call"}
        ),
    }

    # Wrapper-class -> wrapper-class native dependencies. TextWriter
    # forwards to BufWriter, TextReader to BufReader; the upstream's
    # runtime bodies are needed too. Struct emission order is
    # controlled by _IO_WRAPPER_NAMES (upstream first); this table
    # only governs native-symbol selection.
    _IO_WRAPPER_REQUIRES: dict[str, tuple[str, ...]] = {
        "TextWriter": ("BufWriter",),
        "TextReader": ("BufReader",),
    }

    def _io_wrapper_required_natives(self, name: str) -> set[str]:
        """All runtime natives needed when wrapper class `name` is
        referenced, including transitive dependencies via
        _IO_WRAPPER_REQUIRES. The dependency graph is a DAG of depth 1
        today; the loop is defensive."""
        natives: set[str] = set()
        pending = [name]
        seen: set[str] = set()
        while pending:
            n = pending.pop()
            if n in seen:
                continue
            seen.add(n)
            spec = self._IO_WRAPPER_NATIVES.get(n)
            if spec is None:
                continue
            natives.update(spec)
            pending.extend(self._IO_WRAPPER_REQUIRES.get(n, ()))
        return natives

    def _ast_uses_std_streams(self, body: dict) -> bool:
        return self._ast_uses_io_names(body, self._STD_STREAM_NAMES)

    def _ast_uses_io_names(self, body: dict, names: tuple) -> bool:
        """Does the AST contain a DottedPath `io.<name>` for any name
        in the given tuple? Runs a nodetype-driven walk (no isinstance /
        try-except) so it stays inside the bootstrap-lint baseline."""
        stack: List = []
        for defn in body.values():
            if defn is not None:
                stack.append(defn)
        while stack:
            n = stack.pop()
            if n.nodetype == NodeType.DOTTEDPATH:
                dp = cast(zast.DottedPath, n)
                if (
                    dp.parent.nodetype == NodeType.ATOMID
                    and cast(zast.AtomId, dp.parent).name == "io"
                    and dp.child.name in names
                ):
                    return True
            # walk known child-carrying fields by nodetype
            for child in self._walk_children(n):
                stack.append(child)
        return False

    def _io_wrappers_referenced(self) -> bool:
        """True when the program references any io wrapper class."""
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit is None:
            return False
        return self._ast_uses_io_names(mainunit.body, self._IO_WRAPPER_NAMES)

    def _io_class_referenced(self, name: str) -> bool:
        """True when the program references `io.<name>` in the main
        unit AST. Used to gate emission of individual wrapper class
        structs (so `io.bufwriter`-only programs don't drag in the
        bufreader struct, and vice versa)."""
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit is None:
            return False
        return self._ast_uses_io_names(mainunit.body, (name,))

    def _walk_children(self, n: zast.Node) -> List[zast.Node]:
        """Enumerate zast.Node children of n for the std-stream scan.
        Covers the shapes that can contain DottedPath nodes without
        resorting to isinstance / getattr probing."""
        out: List[zast.Node] = []
        nt = n.nodetype
        if nt == NodeType.FUNCTION:
            fn = cast(zast.Function, n)
            if fn.body is not None:
                out.append(fn.body)
        elif nt == NodeType.STATEMENT:
            for s in cast(zast.Statement, n).statements:
                out.append(s)
        elif nt == NodeType.STATEMENTLINE:
            sl = cast(zast.StatementLine, n)
            if sl.statementline is not None:
                out.append(sl.statementline)
        elif nt == NodeType.ASSIGNMENT:
            out.append(cast(zast.Assignment, n).value)
        elif nt == NodeType.EXPRESSION:
            out.append(cast(zast.Expression, n).expression)
        elif nt == NodeType.CALL:
            c = cast(zast.Call, n)
            out.append(c.callable)
            for a in c.arguments:
                out.append(a)
        elif nt == NodeType.NAMEDOPERATION:
            out.append(cast(zast.NamedOperation, n).valtype)
        elif nt == NodeType.CASE:
            cn = cast(zast.Case, n)
            out.append(cn.subject)
            for cl in cn.clauses:
                if cl.statement is not None:
                    out.append(cl.statement)
            if cn.elseclause is not None:
                out.append(cn.elseclause)
        elif nt == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, n)
            out.append(dp.parent)
        elif nt == NodeType.WITH:
            w = cast(zast.With, n)
            out.append(w.value)
            out.append(w.doexpr)
        elif nt in (
            NodeType.CLASS,
            NodeType.UNION,
            NodeType.RECORD,
            NodeType.VARIANT,
            NodeType.PROTOCOL,
            NodeType.FACET,
            NodeType.ENUM,
        ):
            # ObjectDef holds field / arm / spec paths in is_items
            # and as_items. These can reference cross-unit native
            # types (e.g. `ok: io.TextReader` as a union arm); the
            # io-wrapper-emit pass keys off this scan to decide
            # whether to pull a native's struct + destructor into
            # the emit set. Without the recursion, an arm type that
            # never appears in any function body is invisible and
            # the union's auto-emit destroy references an
            # undeclared struct.
            od = cast(zast.ObjectDef, n)
            for v in od.is_items.values():
                if v is not None:
                    out.append(v)
            for v in od.as_items.values():
                if v is not None:
                    out.append(v)
        return out

    def emit(self) -> str:
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return "/* empty program */\n"

        # User file units imported by main. Their type definitions are emitted
        # cross-unit (interleaved with main's, ordered before it since main may
        # embed a dependency type); their functions are emitted by the existing
        # file-unit-function loop below.
        dep_units = self._dependency_file_units()

        # pre-emission pass: collect supplementary data
        # Collect io first so user-code definitions shadow system
        # ones (e.g. a user-declared `reader` protocol wins the slot
        # in _protocol_defs). The io stream protocols are kept in a
        # separate map so file's wrapper emission can find them.
        io_unit = self.program.units.get("io")
        if io_unit is not None:
            self._collect_pre_emission("", io_unit.body)
            for pname in ("Reader", "Writer", "Closer", "Seeker"):
                proto = self._protocol_defs.get(pname)
                if proto is not None:
                    self._io_protocol_defs[pname] = proto
        self._collect_pre_emission("", mainunit.body)
        for dep_name, dep_unit in dep_units:
            self._collect_pre_emission(dep_name, dep_unit.body)

        # register monomorphized type names before emission
        for mono_type, _ in self.typing.mono_types:
            # register in resolved dict so name-based type lookups resolve
            # monomorphized types
            self.typing.resolved[mono_type.name] = mono_type
            if _is_array_type(mono_type):
                continue
            if _is_str_type(mono_type):
                continue
            if _is_list_type(mono_type):
                continue
            if _is_map_type(mono_type):
                continue
            if _is_set_type(mono_type):
                continue
            if mono_type.typetype == ZTypeType.RECORD:
                # pre-register field info so _build_meta_create_args works
                # during function body emission (before mono type emission)
                name = mono_type.name
                field_names_r: List[str] = []
                field_ctypes_r: List[str] = []
                for fn, ft in self.typing.children_of(mono_type):
                    if ft.typetype == ZTypeType.FUNCTION:
                        continue
                    field_names_r.append(fn)
                    field_ctypes_r.append(_ctype(self.typing, ft))
                self._type_field_names[name] = field_names_r
                self._type_field_ctypes[name] = field_ctypes_r
                defaults_r: Dict[str, str] = {}
                for fn, default_val in self.typing.child_defaults_of(mono_type).items():
                    idx = field_names_r.index(fn) if fn in field_names_r else -1
                    if idx >= 0:
                        ct = field_ctypes_r[idx]
                        if ct == "int64_t":
                            defaults_r[fn] = default_val
                        else:
                            defaults_r[fn] = f"(({ct}){default_val})"
                self._type_field_defaults[name] = defaults_r
            elif mono_type.typetype == ZTypeType.CLASS:
                # pre-register field info so _build_meta_create_args works
                # during function body emission (before mono type emission)
                name = mono_type.name
                field_names: List[str] = []
                field_ctypes_list: List[str] = []
                for fn, ft in self.typing.children_of(mono_type):
                    if ft.typetype == ZTypeType.FUNCTION:
                        continue
                    field_names.append(fn)
                    field_ctypes_list.append(_ctype(self.typing, ft))
                self._type_field_names[name] = field_names
                self._type_field_ctypes[name] = field_ctypes_list
                defaults_c: Dict[str, str] = {}
                for fn, default_val in self.typing.child_defaults_of(mono_type).items():
                    idx = field_names.index(fn) if fn in field_names else -1
                    if idx >= 0:
                        ct = field_ctypes_list[idx]
                        if ct == "int64_t":
                            defaults_c[fn] = default_val
                        else:
                            defaults_c[fn] = f"(({ct}){default_val})"
                self._type_field_defaults[name] = defaults_c
            elif mono_type.typetype == ZTypeType.PROTOCOL:
                pass

        # emit str mono types early (before regular definitions that may reference them)
        for mono_type, template_defn in self.typing.mono_types:
            if _is_str_type(mono_type):
                self._emit_mono_str(mono_type)

        # pre-register field info for all non-generic records/classes
        # so that construction calls work regardless of definition order
        self._pre_register_fields("", mainunit.body)
        for dep_name, dep_unit in dep_units:
            self._pre_register_fields(dep_name, dep_unit.body)

        # Emit non-generic union/variant types from system units
        # (io / os) that don't reference monomorphized collection
        # types — these must come before monos that use them
        # (e.g. `result(T, ioerror)` mono destructors call
        # `z_IoError_destroy`). cli *records* (flagdef / optiondef /
        # positionaldef) also go here so monos like list_flagdef
        # that reference them have a complete type to work with.
        self._emit_system_unit_definitions(include_cli=True, cli_records_only=True)

        # Three-pass emission order resolves the bidirectional type
        # dependency between user struct_defs and monomorphized
        # collection types:
        #
        #   (a) A user class holding a `list of: string` field needs
        #       `z_List_String_t` declared first.
        #   (b) A monomorphized generic class like `holder<mycls>`
        #       needs the user `mycls` struct declared first.
        #
        # We split mono emission into "depends only on system types"
        # (pass 1) vs "depends on user types" (pass 3), with user
        # defs between.
        mono_types_all = list(self.typing.mono_types)
        user_type_names = self._collect_user_type_names(mainunit.body)
        for dep_name, dep_unit in dep_units:
            user_type_names.update(self._collect_user_type_names(dep_unit.body))
        early_monos: list = []
        late_monos: list = []
        for mono_type, template_defn in mono_types_all:
            if _is_str_type(mono_type):
                continue  # str handled earlier
            if self._mono_depends_on_user(mono_type, user_type_names):
                late_monos.append((mono_type, template_defn))
            else:
                early_monos.append((mono_type, template_defn))

        # Source-map attribution uses the template AST node's id so
        # that emitted_lines cross-reference the program's ast_nodes
        # table (ZType.type_id lives in a separate id space and would
        # produce orphan foreign-key references).
        for mono_type, template_defn in early_monos:
            self._current_node_id = template_defn.nodeid
            self._emit_mono_type(mono_type, template_defn)

        # cli classes (spec / parsed) — emitted AFTER early monos
        # because their fields reference `list of: flagdef` etc.
        self._emit_system_unit_definitions(
            include_cli=True, only_cli=True, cli_classes_only=True
        )

        # Resolve the bidirectional case the early/late split alone
        # misses:
        #
        #   class C { xs: (List of: UserT) }   # user class field
        #
        # `List<UserT>` is a late mono (its layout references UserT),
        # so its full definition can only come after UserT is emitted.
        # But the user class `C` embeds the `List<UserT>` struct in
        # its field, so C needs the full layout of `List<UserT>` too.
        #
        # Split user defs into:
        #   - early-user: defs whose fields do NOT reference any late
        #     mono. These can be emitted before late monos.
        #   - late-user: defs whose fields reference one or more late
        #     mono types. Emitted AFTER late monos so the field
        #     references resolve.
        late_mono_names = {m.name for m, _ in late_monos}

        def _is_early_user(name, defn) -> bool:
            return not self._user_defn_references_type(name, defn, late_mono_names)

        def _is_late_user(name, defn) -> bool:
            return self._user_defn_references_type(name, defn, late_mono_names)

        # Dependency-unit type passes exclude FUNCTION defs: a dependency unit's
        # functions are emitted by `_emit_file_unit_functions` below, so the
        # type passes emit only its TYPE / DATA definitions (no double-emit).
        # The main unit emits its functions via these passes.
        def _is_early_user_type(name, defn) -> bool:
            return defn.nodetype != NodeType.FUNCTION and _is_early_user(name, defn)

        def _is_late_user_type(name, defn) -> bool:
            return defn.nodetype != NodeType.FUNCTION and _is_late_user(name, defn)

        # second pass (a): early-user defs (no late-mono field refs). Dependency
        # units first — a main type may embed a dependency type by value.
        for dep_name, dep_unit in dep_units:
            self._emit_unit_definitions(
                dep_name, dep_unit.body, defn_filter=_is_early_user_type
            )
        self._emit_unit_definitions("", mainunit.body, defn_filter=_is_early_user)

        # late mono types (their layouts reference early-user types,
        # which are now emitted)
        for mono_type, template_defn in late_monos:
            self._current_node_id = template_defn.nodeid
            self._emit_mono_type(mono_type, template_defn)

        # second pass (b): late-user defs (reference late monos in fields)
        for dep_name, dep_unit in dep_units:
            self._emit_unit_definitions(
                dep_name, dep_unit.body, defn_filter=_is_late_user_type
            )
        self._emit_unit_definitions("", mainunit.body, defn_filter=_is_late_user)

        # third pass: emit facets (must come after all conforming types are defined)
        for dep_name, dep_unit in dep_units:
            self._emit_deferred_facets(dep_name, dep_unit.body)
        self._emit_deferred_facets("", mainunit.body)

        # emit monomorphized generic functions
        for mono_ftype, cloned_func in self.typing.mono_functions:
            if cloned_func.body:
                self._current_node_id = cloned_func.nodeid
                self._emit_function(mono_ftype.name, cloned_func)

        for unitname, unit in self.program.units.items():
            if unitname in ("system", "core", "io", "os", self.program.mainunitname):
                continue
            # skip generic file unit templates (emitted via mono_types)
            if self._is_generic_template(unit):
                continue
            self._emit_file_unit_functions(unitname, unit.body)

        # assemble output
        parts: List[str] = []
        parts.append("/* Generated by zerolang compiler */\n\n")

        # If io-wrappers will be emitted later (their structs land in
        # the deferred buffer below), set needs_io upfront so the
        # runtime preamble pulls in the io system headers (errno.h,
        # fcntl.h, etc.) that the wrapper bodies use. Without this,
        # a program that only references an io-wrapper class via a
        # union arm (no direct io call) emits the wrapper struct +
        # body but misses the include block.
        if self._io_wrappers_referenced() or self._io_file_referenced():
            self.needs_io = True

        parts.append(
            zrt.emit_runtime(
                needs_stdio=self.needs_stdio,
                needs_stdint=self.needs_stdint,
                needs_stdlib=self.needs_stdlib,
                needs_string=self.needs_string,
                needs_stringview=self.needs_stringview,
                needs_io=self.needs_io,
                needs_pwd="userName" in self.needs_os_natives,
                needs_hash=self.needs_hash,
            )
        )
        parts.append(zrt.emit_static_stringviews(self._string_literals))

        for st in self.spec_typedefs:
            parts.append(st)
        if self.spec_typedefs:
            parts.append("\n")

        for i, sd in enumerate(self.struct_defs):
            parts.append(sd)

        # Stream protocol struct + vtable types (reader/writer/closer/
        # seeker). Deferred to here so their signatures can reference
        # z_List_u8_t / z_ListView_u8_t from the mono pass.
        parts.append(self._emit_io_stream_protocols())

        # Buffered wrappers (bufwriter / bufreader). Lands AFTER the
        # stream protocol structs (so writer.lock / reader.lock field
        # types resolve) and BEFORE the io runtime (which references
        # z_BufWriter_t / z_BufReader_t in its dispatch helpers).
        parts.append(self._emit_io_wrapper_classes())

        # io.file protocol wrappers. Emitted AFTER the stream protocol
        # types above (so z_Reader_t etc. exist) and stored in a
        # buffer, appended after emit_runtime_io. Building the buffer
        # here (before runtime_io) lets us record every z_File_*
        # native the wrappers will call, so the runtime emits them.
        file_impls = ""
        if self.needs_io_natives and self._io_file_referenced():
            file_impls = self._emit_io_file_protocol_impls()

        # Buffered-wrapper protocol vtables + wrappers. Same deferral
        # as file_impls — these reference z_BufWriter_* / z_BufReader_*
        # bodies emitted by emit_runtime_io.
        wrapper_impls = self._emit_io_wrapper_protocol_impls()

        # io runtime helpers reference the compiler-generated struct
        # names (z_IoError_t, z_Result_<T>_ioerror_t, ...) so they land
        # AFTER struct_defs rather than with the base runtime helpers.
        parts.append(
            zrt.emit_runtime_io(
                needs_io=self.needs_io,
                natives=self.needs_io_natives,
                os_natives=self.needs_os_natives,
            )
        )

        parts.append(file_impls)
        parts.append(wrapper_impls)

        # Stringview query natives (Phase S1+). Emitted here so any
        # z_optionval_T_t wrappers they return are already declared
        # by the mono-types pass above.
        parts.append(
            zrt.emit_runtime_stringview_natives(
                needs_stringview=self.needs_stringview,
                natives=self.needs_stringview_natives,
            )
        )

        # cli unit natives. Lands after stringview because
        # `cli.parse` may call z_StringView_* helpers (and the cli
        # runtime uses z_String_t / z_String_new from the base
        # runtime plus mono list / map types from the struct_defs).
        parts.append(
            zrt.emit_runtime_cli_natives(
                needs_cli=self.needs_cli,
                natives=self.needs_cli_natives,
            )
        )

        # io.stdin / io.stdout / io.stderr — emit after file_impls so
        # z_File_Reader_create / z_File_Writer_create are declared.
        parts.append(zrt.emit_io_std_streams(self.needs_io_natives))

        # os-unit helpers (exit / args / get_env). Independent of io;
        # the only shared state is the argc/argv globals emitted below
        # when args is referenced.
        parts.append(
            zrt.emit_runtime_os(
                needs_os=self.needs_os,
                natives=self.needs_os_natives,
            )
        )

        for ft in self.func_typedefs:
            parts.append(ft)
        if self.func_typedefs:
            parts.append("\n")
        for fd in self.forward_decls:
            parts.append(fd)
        if self.forward_decls:
            parts.append("\n")
        for fa in self.func_aliases:
            parts.append(fa)
        if self.func_aliases:
            parts.append("\n")
        for i, dd in enumerate(self.data_defs):
            parts.append(dd)
        for i, fd in enumerate(self.func_defs):
            parts.append(fd)

        parts.append("int main(int argc, char* argv[]) {\n")
        if "args" in self.needs_os_natives:
            # Capture the process argv into module-level globals so
            # os.args can materialise a list of strings without having
            # to thread argc/argv through z_main.
            parts.append("    z_os_argc_g = argc;\n")
            parts.append("    z_os_argv_g = argv;\n")
        if self.needs_hash:
            # Seed SipHash before user code touches Map / Set / String.hash.
            parts.append("    z_siphash_init();\n")
        parts.append("    z_main();\n")
        parts.append("    return 0;\n")
        parts.append("}\n")

        output = "".join(parts)
        output = self._strip_unused_ft_typedefs(output)

        # build source map: for each output line, find the node ID
        # by walking the tracked output sections
        self._build_source_map(output)

        return output

    def _strip_unused_ft_typedefs(self, output: str) -> str:
        """Remove unreferenced auto-generated function typedefs.

        The emitter produces a `typedef ... (*z_X_ft)(...);` for every
        user function via `_emit_func_typedef`; most programs never
        store a function as a value, so these are dead decls.
        Spec typedefs (from `_emit_spec_typedef`) are a user-facing
        contract and always kept.
        """
        # gather the ft-names emitted as auto function typedefs
        strippable: set = set()
        for line in self.func_typedefs:
            lp = line.find("(*")
            rp = line.find(")", lp)
            if lp >= 0 and rp > lp:
                strippable.add(line[lp + 2 : rp])
        if not strippable:
            return output

        result: List[str] = []
        for line in output.splitlines(keepends=True):
            stripped = line.lstrip()
            if (
                stripped.startswith("typedef ")
                and "(*z_" in stripped
                and "_ft)" in stripped
            ):
                lp = stripped.find("(*")
                rp = stripped.find(")", lp)
                if lp >= 0 and rp > lp:
                    ft_name = stripped[lp + 2 : rp]
                    if ft_name in strippable and output.count(ft_name) <= 1:
                        continue
            result.append(line)
        return "".join(result)

    def _emit_func_typedef(
        self, name: str, func: zast.Function, record_name: str = ""
    ) -> None:
        """Emit a C typedef for a function (placed after struct defs).
        When `record_name` is non-empty AND that record is a class,
        any parameter whose type is the receiver class is promoted to
        a pointer — matching the `_emit_function` / forward-decl
        convention that class methods take `this` (and same-typed
        params) by pointer."""
        self.needs_stdint = True
        ret_ctype = self._return_ctype(func)
        record_type = self._enclosing_type(func)
        is_class_method = bool(record_type and record_type.typetype == ZTypeType.CLASS)
        params: List[str] = []
        for _pname, ppath in func.parameters.items():
            ptype_str = _ctype(self.typing, self._node_ztype(ppath))
            if (
                is_class_method
                and self._node_ztype(ppath) is record_type
                and not ptype_str.endswith("*")
            ):
                ptype_str = f"{ptype_str}*"
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        cname = name.replace(".", "_")
        self.func_typedefs.append(
            f"typedef {ret_ctype} (*z_{cname}_ft)({param_str});\n"
        )

    def _emit_spec_typedef(self, name: str, func: zast.Function) -> None:
        """Emit a C typedef for a spec (function pointer type)."""
        self.needs_stdint = True
        ret_ctype = self._return_ctype(func)
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(self.typing, self._node_ztype(ppath))
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        cname = name.replace(".", "_")
        self.spec_typedefs.append(
            f"typedef {ret_ctype} (*z_{cname}_ft)({param_str});\n"
        )

    def _emit_constant(self, name: str, atom: zast.AtomId) -> None:
        typename, value, err = parse_number(atom.name)
        if err:
            return
        self.needs_stdint = True
        ctype = TYPEMAP.get(typename, "int64_t")
        cname = _mangle_func(name)
        if typename.startswith("f"):
            self.data_defs.append(f"static const {ctype} {cname} = {value};\n")
        else:
            self.data_defs.append(f"static const {ctype} {cname} = {int(value)};\n")

    def _emit_protocol(self, name: str, proto: zast.ObjectDef) -> None:
        """Emit vtable struct, instance struct, and destroy function for a protocol."""
        self.needs_stdint = True
        self.needs_stdlib = True

        # Prefer the resolved ZType for param types — the AST's
        # `self._node_ztype(ppath)` can remain None for system-library protocols
        # until they're explicitly instantiated. The resolved type's
        # children hold function ZTypes whose own children hold
        # fully-resolved parameter types.
        proto_type = self._node_ztype(proto)

        # Build the function-pointer block. One line per spec function,
        # `void*` first param, `this`-typed param elided.
        func_lines: List[str] = []
        for sname, sfunc in proto.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            params: List[str] = ["void*"]
            spec_type = self.typing.child_of(proto_type, sname) if proto_type else None
            for pname, ppath in sfunc.parameters.items():
                if spec_type is not None and spec_type.this_param_name == pname:
                    continue
                ptype: Optional[ZType] = self._node_ztype(ppath)
                if ptype is None and spec_type is not None:
                    ptype = self.typing.child_of(spec_type, pname)
                params.append(_proto_param_ctype(self.typing, ptype))
            param_str = ", ".join(params)
            func_lines.append(f"    {ret_ctype} (*{sname})({param_str});\n")

        # The template builds `z_<NAME>_t` / `_vtable_t` / `_destroy`; feed it the
        # resolved type's dot-free name so cross-unit protocols stay valid C
        # (== `name` for a single-unit / top-level protocol).
        vtable_struct_name = proto_type.name if proto_type is not None else name
        self.struct_defs.append(
            ztmpl.apply(
                "z_protocol_vtable",
                {"NAME": vtable_struct_name, "VTABLE_FUNCS": "".join(func_lines)},
            )
        )

    def _emit_protocol_impl(
        self,
        impl_name: str,
        label: str,
        proto_name: str,
        impl_defn: "zast.ObjectDef",
        proto: "Optional[zast.ObjectDef]" = None,
        proto_zt: "Optional[ZType]" = None,
    ) -> None:
        """Emit wrapper functions, static vtable, and create function for a protocol implementation."""
        # Find the protocol def by its type-id when available (cross-unit-correct;
        # the short-name `_protocol_defs` map misses across units). The io caller
        # passes the resolved `proto` directly and bypasses both lookups.
        if proto is None and proto_zt is not None:
            proto = self._protocol_def_by_id.get(proto_zt.type_id)
        if proto is None:
            proto = self._protocol_defs.get(proto_name)
        if not proto:
            return
        is_class = impl_defn.nodetype == NodeType.CLASS
        impl_type = self._node_ztype(impl_defn)
        proto_type = proto_zt if proto_zt is not None else self._node_ztype(proto)
        conf = self._conformance_of(impl_type, proto_type, label)
        # All conformance C names are read from the conformance entity and the
        # resolved spec/impl ZTypes, so they stay dot-free cross-unit. The
        # composed fallbacks (used only when no conformance was recorded) mirror
        # the entity's own composition off the impl type's `cname_base`.
        impl_ctype = _cname_of(impl_type, impl_name)
        impl_base = _cbase_of(impl_type, impl_name)
        proto_ctype = _cname_of(proto_type, proto_name)
        proto_vtable_ctype = f"{_cbase_of(proto_type, proto_name)}_vtable_t"
        vtable_name = conf.vtable_cname if conf else f"{impl_base}_{label}_vtable"
        create_name = conf.create_cname if conf else f"{impl_base}_{label}_create"
        owned_create = (
            conf.create_owned_cname if conf else f"{impl_base}_{label}_create_owned"
        )
        wrapper_names: Dict[str, str] = {}
        for sname in proto.functions():
            if conf is not None and sname in conf.method_wrapper_cnames:
                wrapper_names[sname] = conf.method_wrapper_cnames[sname]
            else:
                wrapper_names[sname] = f"{impl_base}_{label}_{sname}_wrapper"

        lines: List[str] = []

        # forward declarations for methods called by wrappers
        all_methods = dict(impl_defn.as_functions())
        all_methods.update(impl_defn.functions())
        for sname in proto.functions():
            mfunc = all_methods.get(sname)
            if mfunc and mfunc.body:
                ret_ctype = self._return_ctype(mfunc)
                params: List[str] = []
                for pname, ppath in mfunc.parameters.items():
                    ptype_str = _ctype(self.typing, self._node_ztype(ppath))
                    # 'this' parameter: add * for class methods
                    if (
                        impl_type
                        and self._node_ztype(ppath) is impl_type
                        and not ptype_str.endswith("*")
                        and impl_type.typetype == ZTypeType.CLASS
                    ):
                        ptype_str = f"{ptype_str}*"
                    params.append(f"{ptype_str} {_mangle_var(pname)}")
                param_str = ", ".join(params) if params else "void"
                method_cname = _mangle_func(f"{impl_name}.{sname}")
                lines.append(f"static {ret_ctype} {method_cname}({param_str});\n")
        lines.append("\n")

        # wrapper functions for each spec
        for sname, sfunc in proto.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            # wrapper params: void* _data, then remaining non-this params.
            # Collection types travel through the vtable as pointers
            # (see _proto_param_ctype) so they match the native impl's
            # ABI without an extra adaptor.
            spec_type = self.typing.child_of(proto_type, sname) if proto_type else None
            wrapper_params: List[str] = ["void* _data"]
            call_args: List[str] = []
            for pname, ppath in sfunc.parameters.items():
                if spec_type is not None and spec_type.this_param_name == pname:
                    continue
                ptype: Optional[ZType] = self._node_ztype(ppath)
                if ptype is None and spec_type is not None:
                    ptype = self.typing.child_of(spec_type, pname)
                pctype = _proto_param_ctype(self.typing, ptype)
                wrapper_params.append(f"{pctype} {_mangle_var(pname)}")
                call_args.append(_mangle_var(pname))

            wrapper_name = wrapper_names[sname]
            param_str = ", ".join(wrapper_params)
            lines.append(f"static {ret_ctype} {wrapper_name}({param_str}) {{\n")
            lines.append(f"    {impl_ctype}* _self = ({impl_ctype}*)_data;\n")

            # build the method call: dereference for value types, pointer for ref types
            method_cname = _mangle_func(f"{impl_name}.{sname}")
            if is_class:
                this_arg = "_self"
            else:
                this_arg = "*_self"
            all_args = [this_arg] + call_args
            call_expr = f"{method_cname}({', '.join(all_args)})"
            if ret_ctype == "void":
                lines.append(f"    {call_expr};\n")
            else:
                lines.append(f"    return {call_expr};\n")
            lines.append("}\n\n")

        # static vtable instance
        lines.append(f"static {proto_vtable_ctype} {vtable_name} = {{\n")
        for sname in proto.functions():
            lines.append(f"    .{sname} = {wrapper_names[sname]},\n")
        lines.append("};\n\n")

        # create function (borrowed — pointer to original, no copy)
        # Returns protocol struct by value; caller stores on stack.
        lines.append(f"static {proto_ctype} {create_name}({impl_ctype}* val);\n")
        lines.append(f"static {proto_ctype} {create_name}({impl_ctype}* val) {{\n")
        lines.append(f"    {proto_ctype} proto = {{0}};\n")
        lines.append("    proto.data = val;\n")
        lines.append(f"    proto.vtable = &{vtable_name};\n")
        lines.append("    proto.destroy = NULL;\n")
        lines.append("    return proto;\n")
        lines.append("}\n\n")

        # owned create + destroy wrapper
        if is_class:
            # class: destroy frees boxed copy (+ field cleanup if needed)
            destroy_name = (
                conf.destroy_cname if conf else f"{impl_base}_{label}_owned_destroy"
            )
            lines.append(f"static void {destroy_name}(void* p) {{\n")
            if impl_type and impl_type.needs_field_cleanup:
                lines.append(f"    {impl_base}_destroy(({impl_ctype}*)p);\n")
            lines.append("    free(p);\n")
            lines.append("}\n\n")

            # stack class: owned create boxes the struct (malloc + copy)
            lines.append(f"static {proto_ctype} {owned_create}({impl_ctype}* val);\n")
            lines.append(f"static {proto_ctype} {owned_create}({impl_ctype}* val) {{\n")
            lines.append(f"    {proto_ctype} proto = {{0}};\n")
            lines.append(
                f"    {impl_ctype}* boxed = ({impl_ctype}*)z_xmalloc(sizeof({impl_ctype}));\n"
            )
            lines.append("    *boxed = *val;\n")
            lines.append("    proto.data = boxed;\n")
            lines.append(f"    proto.vtable = &{vtable_name};\n")
            lines.append(f"    proto.destroy = {destroy_name};\n")
            lines.append("    return proto;\n")
            lines.append("}\n\n")
        else:
            # record: destroy frees boxed record (+ reftype fields)
            destroy_name = (
                conf.destroy_cname if conf else f"{impl_base}_{label}_boxed_destroy"
            )
            lines.append(f"static void {destroy_name}(void* p) {{\n")
            lines.append(f"    {impl_ctype}* r = ({impl_ctype}*)p;\n")
            # cleanup reftype fields
            for fname, fpath in impl_defn.is_paths().items():
                fpath_t = self._node_ztype(fpath)
                if fpath_t:
                    lines.append(self._emit_field_cleanup(f"r->{fname}", fpath_t))
            lines.append("    free(r);\n")
            lines.append("}\n\n")

            lines.append(f"static {proto_ctype} {owned_create}({impl_ctype} val);\n")
            lines.append(f"static {proto_ctype} {owned_create}({impl_ctype} val) {{\n")
            lines.append(f"    {proto_ctype} proto = {{0}};\n")
            lines.append(
                f"    {impl_ctype}* boxed = ({impl_ctype}*)z_xmalloc(sizeof({impl_ctype}));\n"
            )
            lines.append("    *boxed = val;\n")
            lines.append("    proto.data = boxed;\n")
            lines.append(f"    proto.vtable = &{vtable_name};\n")
            lines.append(f"    proto.destroy = {destroy_name};\n")
            lines.append("    return proto;\n")
            lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_facet(self, name: str, facet: zast.ObjectDef) -> None:
        """Emit vtable struct, data union, and instance struct for a facet."""
        self.needs_stdint = True
        lines: List[str] = []

        # vtable struct — function pointers with void* as first param (same as protocol)
        facet_type = self._node_ztype(facet)
        # Dot-free C names read from the resolved facet type so a cross-unit
        # facet stays valid C (== z_<name>_... for a single-unit facet).
        facet_base = _cbase_of(facet_type, name)
        facet_ctype = _cname_of(facet_type, name)
        lines.append("typedef struct {\n")
        for sname, sfunc in facet.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            params: List[str] = ["void*"]
            spec_type = self.typing.child_of(facet_type, sname) if facet_type else None
            for pname, ppath in sfunc.parameters.items():
                if spec_type is not None and spec_type.this_param_name == pname:
                    continue
                params.append(_ctype(self.typing, self._node_ztype(ppath)))
            param_str = ", ".join(params)
            lines.append(f"    {ret_ctype} (*{sname})({param_str});\n")
        lines.append(f"}} {facet_base}_vtable_t;\n\n")

        # data union — sized to largest conforming type (provides size + alignment).
        # Keyed by the facet's type-id so a cross-unit conformer is found (the
        # short-name map misses across units) and emitted with its dot-free cname.
        conformer_ids = (
            self._facet_conformer_ids.get(facet_type.type_id, [])
            if facet_type is not None
            else []
        )
        lines.append("typedef union {\n")
        if conformer_ids:
            for impl_id in conformer_ids:
                impl_zt = _type_by_id(impl_id)
                if impl_zt is not None:
                    member = _cname_of(impl_zt, impl_zt.name)
                    lines.append(f"    {member} _{impl_zt.name};\n")
        else:
            lines.append("    char _empty;\n")
        lines.append(f"}} {facet_base}_data_u;\n\n")

        # instance struct — vtable first (constant offset), then inline data
        lines.append("typedef struct {\n")
        lines.append(f"    {facet_base}_vtable_t* vtable;\n")
        lines.append(f"    {facet_base}_data_u data;\n")
        lines.append(f"}} {facet_ctype};\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_facet_impl(
        self,
        impl_name: str,
        label: str,
        facet_zt: ZType,
        impl_defn: "zast.ObjectDef",
    ) -> None:
        """Emit wrapper functions, static vtable, and create function for a facet implementation."""
        # The facet def is found by the facet's type-id (cross-unit-correct; a
        # short-name lookup would miss across units).
        facet = self._facet_def_by_id.get(facet_zt.type_id)
        if not facet:
            return
        impl_type = self._node_ztype(impl_defn)
        facet_type = facet_zt
        conf = self._conformance_of(impl_type, facet_type, label)
        # Conformance C names read from the entity / resolved ZTypes (dot-free
        # cross-unit); composed fallbacks mirror the entity off the impl base.
        impl_ctype = _cname_of(impl_type, impl_name)
        impl_base = _cbase_of(impl_type, impl_name)
        facet_ctype = _cname_of(facet_type, facet_type.name)
        facet_vtable_ctype = f"{_cbase_of(facet_type, facet_type.name)}_vtable_t"
        vtable_name = conf.vtable_cname if conf else f"{impl_base}_{label}_vtable"
        owned_create = (
            conf.create_owned_cname if conf else f"{impl_base}_{label}_create_owned"
        )
        wrapper_names: Dict[str, str] = {}
        for sname in facet.functions():
            if conf is not None and sname in conf.method_wrapper_cnames:
                wrapper_names[sname] = conf.method_wrapper_cnames[sname]
            else:
                wrapper_names[sname] = f"{impl_base}_{label}_{sname}_wrapper"

        lines: List[str] = []

        # forward declarations for methods called by wrappers
        all_methods = dict(impl_defn.as_functions())
        all_methods.update(impl_defn.functions())
        for sname in facet.functions():
            mfunc = all_methods.get(sname)
            if mfunc and mfunc.body:
                ret_ctype = self._return_ctype(mfunc)
                params: List[str] = []
                for pname, ppath in mfunc.parameters.items():
                    ptype_str = _ctype(self.typing, self._node_ztype(ppath))
                    params.append(f"{ptype_str} {_mangle_var(pname)}")
                param_str = ", ".join(params) if params else "void"
                method_cname = _mangle_func(f"{impl_name}.{sname}")
                lines.append(f"static {ret_ctype} {method_cname}({param_str});\n")
        lines.append("\n")

        # wrapper functions for each spec
        for sname, sfunc in facet.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            wrapper_params: List[str] = ["void* _data"]
            call_args: List[str] = []
            spec_type = self.typing.child_of(facet_type, sname) if facet_type else None
            for pname, ppath in sfunc.parameters.items():
                if spec_type is not None and spec_type.this_param_name == pname:
                    continue
                pctype = _ctype(self.typing, self._node_ztype(ppath))
                wrapper_params.append(f"{pctype} {_mangle_var(pname)}")
                call_args.append(_mangle_var(pname))

            wrapper_name = wrapper_names[sname]
            param_str = ", ".join(wrapper_params)
            lines.append(f"static {ret_ctype} {wrapper_name}({param_str}) {{\n")
            # facet data is a void* to the inline data union — cast and dereference
            lines.append(f"    {impl_ctype} _self = *({impl_ctype}*)_data;\n")
            method_cname = _mangle_func(f"{impl_name}.{sname}")
            all_args = ["_self"] + call_args
            call_expr = f"{method_cname}({', '.join(all_args)})"
            if ret_ctype == "void":
                lines.append(f"    {call_expr};\n")
            else:
                lines.append(f"    return {call_expr};\n")
            lines.append("}\n\n")

        # static vtable instance
        lines.append(f"static {facet_vtable_ctype} {vtable_name} = {{\n")
        for sname in facet.functions():
            lines.append(f"    .{sname} = {wrapper_names[sname]},\n")
        lines.append("};\n\n")

        # create/take function — copies value into inline data buffer
        lines.append(f"static {facet_ctype} {owned_create}({impl_ctype} val);\n")
        lines.append(f"static {facet_ctype} {owned_create}({impl_ctype} val) {{\n")
        lines.append(f"    {facet_ctype} facet;\n")
        lines.append(f"    facet.vtable = &{vtable_name};\n")
        lines.append(f"    *({impl_ctype}*)&facet.data = val;\n")
        lines.append("    return facet;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_record(self, name: str, rec: zast.ObjectDef) -> None:
        if self._is_typedef_defn(rec):
            # Typedef: no struct, no meta.create — just emit as/is functions
            for mname, mfunc in rec.as_functions().items():
                if mfunc.body:
                    self._emit_func_typedef(f"{name}.{mname}", mfunc)
                    self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
            for label, apath in rec.as_items.items():
                proto_name = (
                    cast(zast.AtomId, apath).name
                    if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                    else None
                )
                if proto_name and self._node_typetype(apath) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(
                        name, label, proto_name, rec, proto_zt=self._node_ztype(apath)
                    )
            return

        self.needs_stdint = True
        ztype = self._node_ztype(rec)
        # see _emit_class for the self-reference rationale.
        needs_forward_typedef = self._class_needs_forward_typedef(name, rec, ztype)
        struct_id = _cname_of(ztype, name)
        lines: List[str] = []
        if needs_forward_typedef:
            lines.append(f"typedef struct {struct_id} {struct_id};\n")
            lines.append(f"struct {struct_id} {{\n")
        else:
            lines.append("typedef struct {\n")
        for fname, fpath in rec.is_paths().items():
            # System-unit records may reach the emitter before the
            # typechecker has attached types to AST paths; fall back
            # to the resolved ZType's children for a concrete type.
            field_type = self._node_ztype(fpath)
            if field_type is None and ztype is not None:
                field_type = self.typing.child_of(ztype, fname)
            # function-pointer field defaulted to a sibling method ref:
            # render inline so the slot's C type matches the referenced
            # method's signature exactly.
            if field_type and field_type.typetype == ZTypeType.FUNCTION:
                decl = self._func_pointer_field_decl_from_ztype(
                    fname, field_type, ztype
                )
                lines.append(f"    {decl};\n")
                continue
            ftype = _ctype(self.typing, field_type)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                ztype
                and self.typing.is_child_lock_field(ztype, fname)
                and field_type
                and field_type.typetype == ZTypeType.CLASS
                and not field_type.is_heap_allocated
                and not ftype.endswith("*")
            ):
                ftype = f"{ftype}*"
            lines.append(f"    {ftype} {fname};\n")
        # emit function pointer fields from 'is' section
        for mname, mfunc in rec.functions().items():
            decl = self._func_pointer_field_decl(name, mname, mfunc)
            lines.append(f"    {decl};\n")
        if needs_forward_typedef:
            lines.append("};\n\n")
        else:
            lines.append(f"}} {struct_id};\n\n")

        self.struct_defs.append("".join(lines))

        # emit meta.create constructor
        self._emit_meta_create(name, rec)

        # emit auto-generated equality function
        self._emit_autogen_eq(name, rec.is_paths(), rec.functions(), ztype)

        # emit 'is' functions with body as regular C functions (for default values)
        for mname, mfunc in rec.functions().items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' functions as methods
        for mname, mfunc in rec.as_functions().items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit protocol implementations
        for label, apath in rec.as_items.items():
            proto_name = (
                cast(zast.AtomId, apath).name
                if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                else None
            )
            if proto_name and self._node_typetype(apath) == ZTypeType.PROTOCOL:
                self._emit_protocol_impl(
                    name, label, proto_name, rec, proto_zt=self._node_ztype(apath)
                )
            # facet impls are deferred to _emit_deferred_facets
        # emit 'as' constants
        self._emit_as_constants(name, rec.as_items)

    def _emit_autogen_eq(
        self,
        name: str,
        items: dict,
        functions: dict,
        ztype: Optional[ZType],
    ) -> None:
        """Emit a static z_{name}_eq() function for auto-generated == on records."""
        if not ztype:
            return
        eq_method = self.typing.child_of(ztype, "==")
        if not eq_method or not eq_method.is_autogen_eq:
            return

        ctype = _cname_of(ztype, name)
        eq_fn = f"{_cbase_of(ztype, name)}_eq"
        lines: List[str] = []
        lines.append(f"static bool {eq_fn}({ctype} a, {ctype} b) {{\n")

        if self._use_memcmp_eq(name, ztype, eq_method):
            self.needs_string = True  # memcmp is in string.h
            lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
        else:
            comparisons: List[str] = []
            # data fields
            for fname, fpath in items.items():
                ft = self._node_ztype(fpath)
                if ft and self._needs_eq_call(ft):
                    field_eq = f"{_cbase_of(ft, ft.name.replace('.', '_'))}_eq"
                    # string/stringview eq functions take pointers
                    # (their C signature is `(z_X_t* a, z_X_t* b)`).
                    # Other auto-generated / native eq functions take
                    # their operands by value.
                    if ft.subtype == ZSubType.STRING:
                        comparisons.append(f"{field_eq}(&a.{fname}, &b.{fname})")
                    else:
                        comparisons.append(f"{field_eq}(a.{fname}, b.{fname})")
                else:
                    comparisons.append(f"(a.{fname} == b.{fname})")
            # function pointer fields
            for mname_f, _mfunc in functions.items():
                comparisons.append(f"(a.{mname_f} == b.{mname_f})")
            if comparisons:
                lines.append(f"    return {' && '.join(comparisons)};\n")
            else:
                lines.append("    return true;\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _needs_eq_call(self, ztype: ZType) -> bool:
        """Check if a type needs a `z_{name}_eq()` call instead of C `==`.

        - Auto-generated equality (record/class/variant synthesised by
          the typechecker): always needs a call — structs have no `==`.
        - Native types that provide an explicit `==` method (e.g.
          `stringview.==`, `string.==`): also need a call, because
          the C-level representation is a struct and `==` on structs
          is not valid C.
        - Primitives (numeric types, bool) use C `==` directly.
        """
        if not ztype:
            return False
        eq = self.typing.child_of(ztype, "==")
        if eq is None:
            return False
        # Primitives (numeric types, bool, floats) map to C primitives and
        # always use C `==` directly, regardless of how their `==` is
        # classified at the type level. `bool` is a variant at the
        # zerolang level with autogen `==`, but compiles to C `bool`.
        if ztype.name in NUMERIC_RANGES or ztype.name in (
            "bool",
            "f32",
            "f64",
            "f128",
        ):
            return False
        if eq.is_autogen_eq:
            return True
        # Native/user-defined ==. Non-primitive types have a struct C-level
        # representation and `==` on structs is not valid C, so a named
        # function call is required.
        return True

    def _use_memcmp_eq(
        self, name: str, ztype: Optional[ZType], eq_method: ZType
    ) -> bool:
        """Check if a type should use memcmp for equality.

        True when is_simple_eq and estimated size exceeds the threshold.
        """
        if not eq_method.is_simple_eq:
            return False
        return self._estimate_type_size(name, ztype) > _EQ_MEMCMP_THRESHOLD

    def _estimate_type_size(self, name: str, ztype: Optional[ZType]) -> int:
        """Estimate byte size of a type from its C fields.

        name keys the cached field-ctype list; ztype supplies the type's
        children when there is no cache entry. Returns 0 if the size cannot
        be determined (conservative: caller falls back to field-by-field).
        """
        # check cached field ctypes from mono/record emission
        field_ctypes = self._type_field_ctypes.get(name)
        if field_ctypes:
            total = 0
            for ct in field_ctypes:
                sz = CTYPE_SIZES.get(ct, 0)
                if sz == 0:
                    # nested struct type: z_foo_t -> foo, recurse. The cache
                    # holds only ctype strings, so the nested ZType is unknown;
                    # the recursion resolves it from its own cache entry.
                    if ct.startswith("z_") and ct.endswith("_t"):
                        inner_name = ct[2:-2]
                        sz = self._estimate_type_size(inner_name, None)
                    if sz == 0:
                        return 0  # unknown size
                total += sz
            return total
        if not ztype:
            return 0
        if ztype.typetype == ZTypeType.VARIANT:
            # variant: tag enum (4 bytes) + max(subtype sizes)
            max_sub = 0
            for fname, ftype in self.typing.children_of(ztype):
                if ftype.typetype in (
                    ZTypeType.FUNCTION,
                    ZTypeType.TAG,
                    ZTypeType.ENUM,
                    ZTypeType.DATA,
                    ZTypeType.NULL,
                ):
                    continue
                if ftype.is_tag_generic_origin:
                    continue
                ct = _ctype(self.typing, ftype)
                sz = CTYPE_SIZES.get(ct, 0)
                if sz == 0 and ct.startswith("z_") and ct.endswith("_t"):
                    sz = self._estimate_type_size(ct[2:-2], ftype)
                if sz > max_sub:
                    max_sub = sz
            return 4 + max_sub  # tag + largest union member
        # record: sum of field sizes
        total = 0
        for fname, ftype in self.typing.children_of(ztype):
            if ftype.typetype == ZTypeType.FUNCTION:
                total += 8  # function pointer
                continue
            if ftype.typetype in (
                ZTypeType.TAG,
                ZTypeType.ENUM,
                ZTypeType.DATA,
                ZTypeType.NULL,
            ):
                continue
            if ftype.is_tag_generic_origin:
                continue
            ct = _ctype(self.typing, ftype)
            sz = CTYPE_SIZES.get(ct, 0)
            if sz == 0 and ct.startswith("z_") and ct.endswith("_t"):
                sz = self._estimate_type_size(ct[2:-2], ftype)
            if sz == 0:
                return 0
            total += sz
        return total

    def _emit_autogen_eq_from_fields(
        self,
        name: str,
        mono_type: ZType,
        field_items: list,
        lines: List[str],
    ) -> None:
        """Emit z_{name}_eq() for a monomorphized record/variant from field_items."""
        eq_method = self.typing.child_of(mono_type, "==")
        if not eq_method or not eq_method.is_autogen_eq:
            return

        ctype = _cname_of(mono_type, name)
        eq_fn = f"{_cbase_of(mono_type, name)}_eq"
        lines.append(f"static bool {eq_fn}({ctype} a, {ctype} b) {{\n")

        if self._use_memcmp_eq(name, mono_type, eq_method):
            self.needs_string = True
            lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
        else:
            comparisons: List[str] = []
            for fname, ct in field_items:
                ftype = self.typing.child_of(mono_type, fname)
                if ftype and self._needs_eq_call(ftype):
                    field_eq = f"{_cbase_of(ftype, ftype.name.replace('.', '_'))}_eq"
                    if ftype.subtype == ZSubType.STRING:
                        comparisons.append(f"{field_eq}(&a.{fname}, &b.{fname})")
                    else:
                        comparisons.append(f"{field_eq}(a.{fname}, b.{fname})")
                else:
                    comparisons.append(f"(a.{fname} == b.{fname})")
            if comparisons:
                lines.append(f"    return {' && '.join(comparisons)};\n")
            else:
                lines.append("    return true;\n")
        lines.append("}\n\n")

    def _func_pointer_field_decl(
        self, parent_name: str, mname: str, mfunc: zast.Function
    ) -> str:
        """Get the full C struct field declaration for a function pointer in an 'is' section.
        Returns e.g. 'int64_t (*callback)(int64_t, int64_t)'.

        For class methods the `this` receiver is emitted as
        `<parent>*` and any stack-allocated class params are also
        emitted as pointers so the slot's C signature matches the
        function it will be assigned to (see `_emit_function`).
        Record methods pass `this` by value (records are valtypes).
        """
        ret_ctype = self._return_ctype(mfunc)
        parent_type = self._enclosing_type(mfunc)
        parent_ctype = _cname_of(parent_type, parent_name) if parent_name else ""
        parent_is_class = (
            parent_type is not None and parent_type.typetype == ZTypeType.CLASS
        )
        params: List[str] = []
        for pname, ppath in mfunc.parameters.items():
            ptype = self._node_ztype(ppath)
            if parent_type is not None and ptype is parent_type and parent_ctype:
                params.append(f"{parent_ctype}*" if parent_is_class else parent_ctype)
                continue
            ptype_str = _ctype(self.typing, ptype)
            if (
                ptype is not None
                and ptype.typetype == ZTypeType.CLASS
                and not ptype.is_heap_allocated
                and not ptype_str.endswith("*")
            ):
                ptype_str = f"{ptype_str}*"
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        return f"{ret_ctype} (*{mname})({param_str})"

    def _method_pointer_param_str(
        self, method_ztype: ZType, parent_ztype: "Optional[ZType]"
    ) -> str:
        """Inline param signature for a function-pointer slot whose type
        is borrowed from a method. For class methods the `this` receiver
        is emitted as `<parent>*` (classes are single-owner reference
        types); record methods pass `this` by value (records are
        copyable). Stack-allocated class params with borrow/lock
        ownership also pass by pointer, matching the C ABI produced by
        `_emit_function`."""
        parent_ctype = _ctype(self.typing, parent_ztype) if parent_ztype else ""
        parent_is_class = (
            parent_ztype is not None and parent_ztype.typetype == ZTypeType.CLASS
        )
        parts: "List[str]" = []
        this_name = method_ztype.this_param_name
        for pname in self.typing.child_names_of(method_ztype):
            ptype = self.typing.child_of(method_ztype, pname)
            if pname == this_name and parent_ctype:
                parts.append(f"{parent_ctype}*" if parent_is_class else parent_ctype)
                continue
            ptype_str = _ctype(self.typing, ptype)
            own = self.typing.child_ownership(method_ztype, pname)
            if (
                ptype is not None
                and ptype.typetype == ZTypeType.CLASS
                and not ptype.is_heap_allocated
                and not ptype_str.endswith("*")
                and own in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ptype_str = f"{ptype_str}*"
            parts.append(ptype_str)
        return ", ".join(parts) if parts else "void"

    def _func_pointer_field_decl_from_ztype(
        self, field_name: str, method_ztype: ZType, parent_ztype: "Optional[ZType]"
    ) -> str:
        """Inline function-pointer field decl built from a resolved
        FUNCTION ZType. Used when an `is`-section field defaults to a
        sibling method reference: the field's C type must match the
        referenced method's C signature so meta_create can wire it
        as a default."""
        ret_ctype = _ctype(self.typing, method_ztype.return_type)
        param_str = self._method_pointer_param_str(method_ztype, parent_ztype)
        return f"{ret_ctype} (*{field_name})({param_str})"

    def _collect_field_params(
        self, name: str, items: dict, functions: dict, ztype: Optional[ZType]
    ) -> tuple:
        """Collect C parameter strings, field names, and field C types.

        Returns (params, field_names, field_ctypes). `ztype` is the type's
        own resolved ZType (passed by the caller, which holds its defn node).
        """
        params: List[str] = []
        field_names: List[str] = []
        field_ctypes: List[str] = []
        for fname, fpath in items.items():
            # System-unit records may reach this path with unresolved
            # AST self._node_ztype(fpath); fall back to the resolved ZType's
            # child for a concrete C type.
            field_type = self._node_ztype(fpath)
            if field_type is None and ztype is not None:
                field_type = self.typing.child_of(ztype, fname)
            # function-pointer field defaulted to a sibling method ref:
            # match the struct slot's inline C signature so meta_create
            # accepts the referenced method's address verbatim.
            if field_type and field_type.typetype == ZTypeType.FUNCTION:
                ret_ct = _ctype(self.typing, field_type.return_type)
                param_inline = self._method_pointer_param_str(field_type, ztype)
                params.append(f"{ret_ct} (*{fname})({param_inline})")
                field_names.append(fname)
                field_ctypes.append(f"{ret_ct} (*)({param_inline})")
                continue
            fct = _ctype(self.typing, field_type)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                ztype
                and self.typing.is_child_lock_field(ztype, fname)
                and field_type
                and field_type.typetype == ZTypeType.CLASS
                and not field_type.is_heap_allocated
                and not fct.endswith("*")
            ):
                fct = f"{fct}*"
            params.append(f"{fct} {fname}")
            field_names.append(fname)
            field_ctypes.append(fct)
        parent_ctype = _cname_of(ztype, name) if name else ""
        parent_is_class = ztype is not None and ztype.typetype == ZTypeType.CLASS
        for mname, mfunc in functions.items():
            ret_ctype = self._return_ctype(mfunc)
            fp_params: List[str] = []
            for pname, ppath in mfunc.parameters.items():
                ptype = self._node_ztype(ppath)
                if ztype is not None and ptype is ztype and parent_ctype:
                    fp_params.append(
                        f"{parent_ctype}*" if parent_is_class else parent_ctype
                    )
                    continue
                ptype_str = _ctype(self.typing, ptype)
                if (
                    ptype is not None
                    and ptype.typetype == ZTypeType.CLASS
                    and not ptype.is_heap_allocated
                    and not ptype_str.endswith("*")
                ):
                    ptype_str = f"{ptype_str}*"
                fp_params.append(ptype_str)
            fp_param_str = ", ".join(fp_params) if fp_params else "void"
            fp_ctype = f"{ret_ctype} (*)({fp_param_str})"
            params.append(f"{ret_ctype} (*{mname})({fp_param_str})")
            field_names.append(mname)
            field_ctypes.append(fp_ctype)
        return params, field_names, field_ctypes

    def _extract_field_defaults(
        self, name: str, items: dict, functions: dict
    ) -> Dict[str, str]:
        """Extract C-level default values for fields and function pointer fields."""
        field_defaults: Dict[str, str] = {}
        for fname, fpath in items.items():
            if fpath.nodetype == NodeType.ATOMID and _is_numeric_id(
                cast(zast.AtomId, fpath).name
            ):
                field_defaults[fname] = self._emit_numeric_literal(
                    cast(zast.AtomId, fpath).name
                )
            elif fpath.nodetype == NodeType.DOTTEDPATH:
                dp = cast(zast.DottedPath, fpath)
                if dp.parent.nodetype == NodeType.ATOMID and _is_numeric_id(
                    cast(zast.AtomId, dp.parent).name
                ):
                    child_name = dp.child.name
                    dct = TYPEMAP.get(child_name, "int64_t")
                    typename, value, err = parse_number(
                        cast(zast.AtomId, dp.parent).name + child_name
                    )
                    if not err:
                        if typename.startswith("f"):
                            field_defaults[fname] = f"(({dct}){value})"
                        else:
                            field_defaults[fname] = f"(({dct}){int(value)})"
                elif dp.parent.nodetype == NodeType.ATOMID:
                    # Case A: variant / union subtype default --
                    # `field: VariantType.arm` emits a tag-only compound
                    # literal. Typecheck has already validated that the
                    # arm is null-payload.
                    parent_type = self._unit_def_ztype(dp.parent)
                    if parent_type is not None and parent_type.typetype in (
                        ZTypeType.VARIANT,
                        ZTypeType.UNION,
                    ):
                        arm_name = dp.child.name
                        arm_type = self.typing.child_of(parent_type, arm_name)
                        if arm_type is not None and arm_type.typetype == ZTypeType.NULL:
                            ctype = _ctype(self.typing, parent_type)
                            tag = f"Z_{parent_type.name.upper()}_TAG_{arm_name.upper()}"
                            field_defaults[fname] = f"({ctype}){{.tag = {tag}}}"
            elif (
                fpath.nodetype == NodeType.ATOMID
                and self._unit_def_typetype(fpath) == ZTypeType.FUNCTION
            ):
                field_defaults[fname] = _mangle_func(cast(zast.AtomId, fpath).name)
            elif fpath.nodetype == NodeType.ATOMID:
                # Sibling-method reference default:
                # `instancemethod: method1` inside the enclosing
                # class/record's `is` block resolves to the address
                # of the qualified function. Mirrors the module-level
                # function-ref branch above; checks the enclosing
                # type's own `functions` dict before falling through.
                ref_name = cast(zast.AtomId, fpath).name
                if ref_name in functions and functions[ref_name].body is not None:
                    field_defaults[fname] = _mangle_func(f"{name}.{ref_name}")
        for mname, mfunc in functions.items():
            if mfunc.body is not None:
                field_defaults[mname] = _mangle_func(f"{name}.{mname}")
        return field_defaults

    def _emit_create_functions(
        self,
        cbase: str,
        ctype: str,
        params: List[str],
        field_names: List[str],
        is_heap: bool,
        has_user_create: bool,
        lines: List[str],
    ) -> None:
        """Emit meta.create and optional create forwarding functions. `cbase`
        is the type's stored cname_base (e.g. "z_point")."""
        param_str = ", ".join(params) if params else "void"
        func_name = f"{cbase}_meta_create"
        accessor = "->" if is_heap else "."
        field_init_parts: List[str] = []
        for fname in field_names:
            field_init_parts.append(f"    _this{accessor}{fname} = {fname};\n")
        template_name = "z_meta_create_heap" if is_heap else "z_meta_create_stack"
        lines.append(
            ztmpl.apply(
                template_name,
                {
                    "CTYPE": ctype,
                    "FUNC_NAME": func_name,
                    "PARAM_STR": param_str,
                    "FIELD_INITS": "".join(field_init_parts),
                },
            )
        )
        if not has_user_create:
            # Trivial delegate: `z_X_create` just forwards to meta_create
            # with the same signature and arguments. A preprocessor alias
            # has the same effect and skips a function body per type.
            # Emit inline next to meta_create so callers within struct_defs
            # (e.g. array default-init) see the alias before use.
            create_name = f"{cbase}_create"
            lines.append(f"#define {create_name} {func_name}\n\n")

    def _emit_meta_create(
        self,
        name: str,
        defn: zast.TypeDefinition,
        lines: Optional[List[str]] = None,
    ) -> None:
        """Emit meta.create constructor for a record or class type.

        Uses ZType.is_heap_allocated to select stack vs heap allocation.
        If lines is None, appends to self.struct_defs.
        """
        assert defn.nodetype in (NodeType.RECORD, NodeType.CLASS)
        rc_defn = cast(zast.ObjectDef, defn)
        ztype = self._node_ztype(defn)
        is_heap = ztype.is_heap_allocated if ztype else False
        ctype = _cname_of(ztype, name)
        # Key field-info by the dot-free ztype.name (matches the construction
        # arg-builders, which look up by `rec_type.name`); falls back to the
        # AST-path name only for an unstamped type.
        fkey = ztype.name if ztype is not None and ztype.name else name
        params, field_names, field_ctypes = self._collect_field_params(
            name, rc_defn.is_paths(), rc_defn.functions(), ztype
        )
        self._type_field_ctypes[fkey] = field_ctypes
        self._type_field_names[fkey] = field_names
        self._type_field_defaults[fkey] = self._extract_field_defaults(
            name, rc_defn.is_paths(), rc_defn.functions()
        )
        has_user_create = (
            "create" in rc_defn.functions() or "create" in rc_defn.as_functions()
        )
        target: List[str] = lines if lines is not None else []
        self._emit_create_functions(
            _cbase_of(ztype, name),
            ctype,
            params,
            field_names,
            is_heap=is_heap,
            has_user_create=has_user_create,
            lines=target,
        )
        if lines is None:
            self.struct_defs.append("".join(target))

    def _arm_type_supports_forward_typedef(self, ztype: "ZType") -> bool:
        """Whether a forward typedef + destructor decl for `ztype` is
        safe to emit at a union-arm site. User-defined classes qualify:
        they always use the named-struct emission form (see
        `_class_needs_forward_typedef`), so `typedef struct z_X_t z_X_t;`
        matches the eventual full definition. Native runtime classes
        keep their anonymous-struct emission and can only be forward-
        declared from the named-struct opt-in list (currently the
        io-wrapper set)."""
        if ztype.typetype != ZTypeType.CLASS:
            return False
        if ztype.is_native:
            return ztype.name in self._IO_WRAPPER_NAMES
        return True

    def _class_needs_forward_typedef(
        self, name: str, cls: zast.ObjectDef, ztype: "Optional[ZType]"
    ) -> bool:
        """User-defined classes and records emit in the named-struct
        form. Native runtime types (String, List, Map, io wrappers,
        ...) keep their anonymous-struct emission to match the runtime
        templates; only the named-form io wrappers opt in via the
        explicit name set so their forward declarations match.

        The named form `typedef struct z_X_t { ... } z_X_t;` is
        functionally identical to anonymous `typedef struct { ... }
        z_X_t;` but lets the type be forward-declared from any earlier
        emit site (union arms, recursive references). C11 6.7p3 permits
        the redundant typedef when both spellings are identical."""
        if ztype is None:
            return False
        if ztype.is_native:
            return name in self._IO_WRAPPER_NAMES
        return True

    def _emit_class(
        self, name: str, cls: zast.ObjectDef, skip_protocol_impls: bool = False
    ) -> None:
        if self._is_typedef_defn(cls):
            # Typedef: no struct, no destructor, no meta.create — just emit as/is functions
            for mname, mfunc in cls.as_functions().items():
                if mfunc.body:
                    self._emit_func_typedef(f"{name}.{mname}", mfunc)
                    self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
            if not skip_protocol_impls:
                for label, apath in cls.as_items.items():
                    proto_name = (
                        cast(zast.AtomId, apath).name
                        if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                        else None
                    )
                    if proto_name and self._node_typetype(apath) == ZTypeType.PROTOCOL:
                        self._emit_protocol_impl(
                            name,
                            label,
                            proto_name,
                            cls,
                            proto_zt=self._node_ztype(apath),
                        )
            return

        self.needs_stdint = True
        self.needs_stdlib = True
        ztype = self._node_ztype(cls)
        # Function-pointer fields whose signature references the
        # enclosing class (via a `this` receiver) need the typedef
        # name to be in scope before the struct body. Emit a forward
        # typedef and a tagged struct in that case; otherwise keep
        # the anonymous-struct form to minimise churn.
        needs_forward_typedef = self._class_needs_forward_typedef(name, cls, ztype)
        struct_id = _cname_of(ztype, name)
        lines: List[str] = []
        if needs_forward_typedef:
            lines.append(f"typedef struct {struct_id} {struct_id};\n")
            lines.append(f"struct {struct_id} {{\n")
        else:
            lines.append("typedef struct {\n")
        for fname, fpath in cls.is_paths().items():
            field_ztype = self._node_ztype(fpath)
            # function-pointer field defaulted to a sibling method ref:
            # render inline so the slot's C type matches the referenced
            # method's signature exactly.
            if field_ztype and field_ztype.typetype == ZTypeType.FUNCTION:
                decl = self._func_pointer_field_decl_from_ztype(
                    fname, field_ztype, ztype
                )
                lines.append(f"    {decl};\n")
                continue
            ftype = _ctype(self.typing, field_ztype)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                ztype
                and self.typing.is_child_lock_field(ztype, fname)
                and field_ztype
                and field_ztype.typetype == ZTypeType.CLASS
                and not field_ztype.is_heap_allocated
                and not ftype.endswith("*")
            ):
                ftype = f"{ftype}*"
            lines.append(f"    {ftype} {fname};\n")
        # emit function pointer fields from 'is' section
        for mname, mfunc in cls.functions().items():
            decl = self._func_pointer_field_decl(name, mname, mfunc)
            lines.append(f"    {decl};\n")
        if needs_forward_typedef:
            lines.append("};\n\n")
        else:
            lines.append(f"}} {struct_id};\n\n")

        # emit destructor (only if class has fields needing cleanup)
        if ztype and ztype.needs_field_cleanup:
            lines.append(
                f"static void {_cbase_of(ztype, name)}_destroy({struct_id}* p) {{\n"
            )
            lines.append("    if (!p) return;\n")
            for fname, fpath in cls.is_paths().items():
                # .lock fields are borrowed references, don't own data
                if ztype and self.typing.is_child_lock_field(ztype, fname):
                    continue
                fpath_t = self._node_ztype(fpath)
                if fpath_t:
                    lines.append(self._emit_field_cleanup(f"p->{fname}", fpath_t))
            lines.append("}\n\n")

        # emit meta.create constructor
        self._emit_meta_create(name, cls, lines)

        self.struct_defs.append("".join(lines))

        # emit 'is' functions with body as regular C functions (for default values).
        # Method-reference fields (e.g. `instancemethod: method1`) and overrides
        # at construction (`c val: 0 instancemethod: c.method2`) bind a function
        # pointer typedef; emit `z_{name}_{mname}_ft` so the assignment-side
        # hoist's local declaration has a real type.
        for mname, mfunc in cls.functions().items():
            if mfunc.body:
                self._emit_func_typedef(f"{name}.{mname}", mfunc, record_name=name)
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' functions as methods
        for mname, mfunc in cls.as_functions().items():
            if mfunc.body:
                self._emit_func_typedef(f"{name}.{mname}", mfunc, record_name=name)
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit protocol implementations
        if not skip_protocol_impls:
            for label, apath in cls.as_items.items():
                proto_name = (
                    cast(zast.AtomId, apath).name
                    if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                    else None
                )
                if proto_name and self._node_typetype(apath) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(
                        name, label, proto_name, cls, proto_zt=self._node_ztype(apath)
                    )
        # emit 'as' constants
        self._emit_as_constants(name, cls.as_items)

    def _resolve_tag_values(
        self, union_defn: "zast.ObjectDef"
    ) -> Optional[Dict[str, int]]:
        """Resolve custom tag values from as_items if a .tag reference exists."""
        for as_name, as_path in union_defn.as_items.items():
            if (
                as_path.nodetype == NodeType.DOTTEDPATH
                and cast(zast.DottedPath, as_path).child.name == "tag"
            ):
                # find the data definition name from the parent
                data_name = None
                if cast(zast.DottedPath, as_path).parent.nodetype == NodeType.ATOMID:
                    data_name = cast(
                        zast.AtomId, cast(zast.DottedPath, as_path).parent
                    ).name
                if not data_name:
                    continue
                # look up the data definition in the program
                for unitname, unit in self.program.units.items():
                    defn = unit.body.get(data_name)
                    if defn is None:
                        continue
                    data_defn = None
                    if defn.nodetype == NodeType.DATA:
                        data_defn = cast(zast.Data, defn)
                    elif (
                        defn.nodetype == NodeType.EXPRESSION
                        and cast(zast.Expression, defn).expression.nodetype
                        == NodeType.DATA
                    ):
                        data_defn = cast(
                            zast.Data, cast(zast.Expression, defn).expression
                        )
                    if data_defn:
                        values: Dict[str, int] = {}
                        ordinal = 0
                        for item in data_defn.data:
                            ename = item.name if item.name is not None else str(ordinal)
                            ordinal += 1
                            if (
                                item.valtype.nodetype == NodeType.ATOMID
                                and _is_numeric_id(cast(zast.AtomId, item.valtype).name)
                            ):
                                _, val, err = parse_number(
                                    cast(zast.AtomId, item.valtype).name
                                )
                                if not err:
                                    values[ename] = int(val)
                        return values
        return None

    def _union_lock_arm_names(self, union_defn: zast.ObjectDef) -> set:
        """Return the set of arm names declared as `name: t.lock` on a union.

        Mirrors the type-checker's `is_lock_arm` flag on ZTypeChild, but
        read straight off the AST so it's available at emit time without
        needing a back-reference to the resolved type. The .lock suffix
        rides on the item's path (DottedPath whose leaf is `lock`).
        """
        out: set = set()
        for sname, spath in union_defn.is_paths().items():
            if (
                spath.nodetype == NodeType.DOTTEDPATH
                and cast(zast.DottedPath, spath).child.name == "lock"
            ):
                out.add(sname)
        return out

    def _emit_union(self, name: str, union_defn: zast.ObjectDef) -> None:
        self.needs_stdint = True
        self.needs_stdlib = True
        lines: List[str] = []

        # Read the stored dot-free C names: `struct` (z_..._t), `cbase`
        # (z_...) for the tag-enum type and destructor, `tagpfx` for the
        # Z_..._TAG_* enumerators. `ztype.name` is dot-free even for a
        # dependency-unit union (see ztypecheck), so the tags are valid C.
        ztype = self._node_ztype(union_defn)
        struct = _cname_of(ztype, name)
        cbase = _cbase_of(ztype, name)
        tagpfx = (ztype.name if ztype and ztype.name else name).upper()

        # resolve custom tag values from as_items
        custom_tag_values = self._resolve_tag_values(union_defn)

        # emit tag enum
        lines.append("typedef enum {\n")
        tag_names = []
        for sname in union_defn.is_paths().keys():
            tag = f"Z_{tagpfx}_TAG_{sname.upper()}"
            tag_names.append(tag)
            if custom_tag_values and sname in custom_tag_values:
                lines.append(f"    {tag} = {custom_tag_values[sname]},\n")
            else:
                lines.append(f"    {tag},\n")
        lines.append(f"}} {cbase}_tag_t;\n\n")

        # emit union struct: always {tag, void*}. Named-struct form so a
        # forward typedef stays compatible with the full definition.
        lines.append(f"typedef struct {struct} {{\n")
        lines.append(f"    {cbase}_tag_t tag;\n")
        lines.append("    void* data;\n")
        lines.append(f"}} {struct};\n\n")

        # No-cleanup arms: `null` (no payload) and `.lock` (borrowed
        # reference — the union doesn't own its payload). When every arm
        # is no-cleanup, the destructor is a no-op (and is unreachable
        # via the type-checker's needs_destructor=False on the resolved
        # ZType, but emit it anyway so a stale call site links cleanly).
        lock_arms = self._union_lock_arm_names(union_defn)

        def _is_no_cleanup(sname: str, spath: zast.Path) -> bool:
            if (
                spath.nodetype == NodeType.ATOMID
                and cast(zast.AtomId, spath).name == "null"
            ):
                return True
            return sname in lock_arms

        all_no_cleanup = all(
            _is_no_cleanup(sname, spath)
            for sname, spath in union_defn.is_paths().items()
        )
        if all_no_cleanup:
            lines.append(f"static void {cbase}_destroy({struct}* u) {{ (void)u; }}\n\n")
            self.struct_defs.append("".join(lines))
            return

        # Owned subtypes (ones whose payload needs freeing). Null and
        # locked tags fall through the switch's implicit default with
        # no work — locked arms hold a borrowed pointer the union does
        # not own.
        non_null_items = [
            (sname, spath)
            for sname, spath in union_defn.is_paths().items()
            if not _is_no_cleanup(sname, spath)
        ]

        def _arm_body(spath: zast.Path) -> tuple[str, ...]:
            stype = self._node_ztype(spath)
            if stype and (stype.destructor_name is not None) and stype.destructor_name:
                if stype.is_heap_allocated:
                    cast_expr = f"({_ctype(self.typing, stype)})u->data"
                    return (f"            {stype.destructor_name}({cast_expr});\n",)
                ptr_ctype = f"{_ctype(self.typing, stype)}*"
                return (
                    f"            {stype.destructor_name}(({ptr_ctype})u->data);\n",
                    "            free(u->data);\n",
                )
            return ("            free(u->data);\n",)

        # Forward-declare arm-type structs + destructor functions so
        # the destroy below resolves when the union is emitted before
        # the arm's actual definition. This is the common case for
        # cross-unit native types (io.TextReader / io.BufReader) whose
        # struct lands in the deferred wrapper-class buffer after the
        # main `struct_defs` block. C11 allows redundant typedefs
        # (6.7p3) and forward-declared structs (6.2.5p23), so emitting
        # these here is safe even when the same names get redeclared
        # at their real definition site. Gated on the arm type using
        # named-struct emission (see `_class_needs_forward_typedef`):
        # an anonymous-struct typedef can't be forward-declared.
        forward_decls_emitted: set[str] = set()
        for sname, spath in non_null_items:
            stype = self._node_ztype(spath)
            if stype is None or not stype.destructor_name:
                continue
            if stype.typetype != ZTypeType.CLASS:
                continue
            if stype.name in forward_decls_emitted:
                continue
            if not self._arm_type_supports_forward_typedef(stype):
                continue
            forward_decls_emitted.add(stype.name)
            cid = _cname_of(stype, stype.name)
            lines.append(f"typedef struct {cid} {cid};\n")
            if stype.is_heap_allocated:
                lines.append(f"static void {stype.destructor_name}({cid}* p);\n")
            else:
                lines.append(f"static void {stype.destructor_name}({cid}* p);\n")

        lines.append(f"static void {cbase}_destroy({struct}* u) {{\n")
        lines.append("    if (!u) return;\n")
        lines.append("    if (!u->data) return;\n")
        lines.append("    switch (u->tag) {\n")
        pending_cases: list[str] = []
        pending_body: tuple[str, ...] | None = None
        for sname, spath in non_null_items:
            tag = f"Z_{tagpfx}_TAG_{sname.upper()}"
            body = _arm_body(spath)
            if pending_body is not None and body != pending_body:
                for t in pending_cases:
                    lines.append(f"        case {t}:\n")
                lines.extend(pending_body)
                lines.append("            break;\n")
                pending_cases = []
            pending_cases.append(tag)
            pending_body = body
        if pending_body is not None:
            for t in pending_cases:
                lines.append(f"        case {t}:\n")
            lines.extend(pending_body)
            lines.append("            break;\n")
        lines.append("        default: break;\n")
        lines.append("    }\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

        # emit methods
        all_funcs = list(union_defn.functions().items()) + list(
            union_defn.as_functions().items()
        )
        for mname, mfunc in all_funcs:
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' constants
        self._emit_as_constants(name, union_defn.as_items)

    def _emit_mono_type(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized type (union, record, class, or protocol)."""
        if _is_array_type(mono_type):
            self._emit_mono_array(mono_type)
            return
        if _is_str_type(mono_type):
            self._emit_mono_str(mono_type)
            return
        if _is_list_type(mono_type):
            self._emit_mono_list(mono_type)
            return
        if _is_listview_type(mono_type):
            self._emit_mono_listview(mono_type)
            return
        if _is_listiter_type(mono_type):
            # listiter is a native generic class. Its struct + .call +
            # the source list's .iterate factory are emitted as a single
            # unit when the source list mono is emitted (see
            # `_emit_listiter_runtime` called from `_emit_mono_list`).
            # Skip the default class-mono emission to avoid an empty
            # struct definition that conflicts with the runtime layout.
            return
        if _is_mapkeyiter_type(mono_type):
            # mapkeyiter: same story — its struct/call/iterate factory
            # are emitted as part of `_emit_mono_map` for the source map.
            return
        if _is_mapitemiter_type(mono_type):
            # mapitemiter: emitted as part of `_emit_mono_map`.
            return
        if _is_mapentry_type(mono_type):
            # mapentry: borrow-only view; the C representation is a
            # bucket pointer typedef emitted with the source map's
            # mono pass. No struct of its own.
            return
        if _is_map_type(mono_type):
            self._emit_mono_map(mono_type)
            return
        if _is_setiter_type(mono_type):
            # setiter: struct + .call factory emitted as part of
            # `_emit_mono_set` for the source set.
            return
        if _is_set_type(mono_type):
            self._emit_mono_set(mono_type)
            return
        if mono_type.typetype == ZTypeType.UNION:
            self._emit_mono_union(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.VARIANT:
            self._emit_mono_variant(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.RECORD:
            self._emit_mono_record(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.CLASS:
            if mono_type.is_box:
                self._emit_mono_box(mono_type)
                return
            self._emit_mono_class(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.PROTOCOL:
            self._emit_mono_protocol(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.UNIT:
            self._emit_mono_unit(mono_type, template_defn)

    def _emit_mono_unit(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized unit: emit its function definitions."""
        mangled = mono_type.name
        cloned_methods = self.typing.cloned_methods.get(mangled, {})
        if template_defn.nodetype != NodeType.UNIT:
            return
        unit_defn = cast(zast.Unit, template_defn)
        for dname, ddefn in unit_defn.body.items():
            if dname in (unit_defn.body.keys() - cloned_methods.keys()):
                # skip generic param declarations and non-function items
                pass
            if dname in cloned_methods:
                func = cloned_methods[dname]
                qualified = f"{mangled}.{dname}"
                # check for func alias (deduplication)
                aliases = self.typing.func_aliases
                if qualified in aliases:
                    # already emitted via canonical name; emit typedef alias
                    canonical = aliases[qualified]
                    alias_c = _mangle_func(qualified)
                    canon_c = _mangle_func(canonical)
                    self.func_aliases.append(f"#define {alias_c} {canon_c}\n")
                else:
                    self._emit_func_typedef(qualified, func)
                    self._emit_function(qualified, func)

    def _emit_mono_union(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized union type."""
        # nullable-ptr option: emit only a destructor (no struct/tag needed)
        if mono_type.is_nullable_ptr:
            self._emit_nullable_ptr_option(mono_type)
            return

        self.needs_stdint = True
        self.needs_stdlib = True
        name = mono_type.name
        struct = _cname_of(mono_type, name)
        cbase = _cbase_of(mono_type, name)
        lines: List[str] = []

        # collect subtypes (non-special children)
        subtype_items: list[tuple[str, ZType]] = []
        for sname, stype in self.typing.children_of(mono_type):
            if sname == "tag":
                continue
            if stype.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if stype.is_tag_generic_origin:
                continue
            subtype_items.append((sname, stype))

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname, _ in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            lines.append(f"    {tag},\n")
        lines.append(f"}} {cbase}_tag_t;\n\n")

        # emit union struct
        lines.append("typedef struct {\n")
        lines.append(f"    {cbase}_tag_t tag;\n")
        lines.append("    void* data;\n")
        lines.append(f"}} {struct};\n\n")

        # destructor: collapse to a no-op when every subtype is `null` or
        # a locked arm (locked arms hold a borrowed pointer the union does
        # not own — no cleanup needed).
        def _is_no_cleanup_mono(sname: str, stype: ZType) -> bool:
            if stype.typetype == ZTypeType.NULL:
                return True
            return self.typing.is_child_lock_arm(mono_type, sname)

        all_no_cleanup_mono = all(
            _is_no_cleanup_mono(sname, stype) for sname, stype in subtype_items
        )
        if all_no_cleanup_mono:
            lines.append(f"static void {cbase}_destroy({struct}* u) {{ (void)u; }}\n\n")
            self.struct_defs.append("".join(lines))
            return

        # Owned subtypes only — null and locked tags go through the
        # implicit default with no work.
        non_null_subtypes = [
            (sname, stype)
            for sname, stype in subtype_items
            if not _is_no_cleanup_mono(sname, stype)
        ]

        def _arm_body_mono(stype: ZType) -> tuple[str, ...]:
            if (stype.destructor_name is not None) and stype.destructor_name:
                if stype.is_heap_allocated:
                    cast_expr = f"({_ctype(self.typing, stype)})u->data"
                    return (f"            {stype.destructor_name}({cast_expr});\n",)
                ptr_ctype = f"{_ctype(self.typing, stype)}*"
                return (
                    f"            {stype.destructor_name}(({ptr_ctype})u->data);\n",
                    "            free(u->data);\n",
                )
            return ("            free(u->data);\n",)

        lines.append(f"static void {cbase}_destroy({struct}* u) {{\n")
        lines.append("    if (!u) return;\n")
        lines.append("    if (!u->data) return;\n")
        lines.append("    switch (u->tag) {\n")
        pending_cases_m: list[str] = []
        pending_body_m: tuple[str, ...] | None = None
        for sname, stype in non_null_subtypes:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            body = _arm_body_mono(stype)
            if pending_body_m is not None and body != pending_body_m:
                for t in pending_cases_m:
                    lines.append(f"        case {t}:\n")
                lines.extend(pending_body_m)
                lines.append("            break;\n")
                pending_cases_m = []
            pending_cases_m.append(tag)
            pending_body_m = body
        if pending_body_m is not None:
            for t in pending_cases_m:
                lines.append(f"        case {t}:\n")
            lines.extend(pending_body_m)
            lines.append("            break;\n")
        lines.append("        default: break;\n")
        lines.append("    }\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_nullable_ptr_option(self, mono_type: ZType) -> None:
        """Emit a nullable-ptr option type (no struct, just a destructor)."""
        self.needs_stdlib = True
        name = mono_type.name
        some_type = self.typing.child_of(mono_type, "some")
        if not some_type:
            return
        inner_ctype = _ctype(self.typing, some_type)
        lines: List[str] = []
        # emit destructor: if non-null, destroy the inner value
        lines.append(
            f"static void {_cbase_of(mono_type, name)}_destroy({inner_ctype} v) {{\n"
        )
        lines.append("    if (!v) return;\n")
        if (some_type.destructor_name is not None) and some_type.destructor_name:
            lines.append(f"    {some_type.destructor_name}(v);\n")
        else:
            lines.append("    free(v);\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mono_variant(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized variant type."""
        self.needs_stdint = True
        name = mono_type.name
        struct = _cname_of(mono_type, name)
        cbase = _cbase_of(mono_type, name)
        lines: List[str] = []

        # collect subtypes (non-special children)
        subtype_items: list[tuple[str, ZType]] = []
        for sname, stype in self.typing.children_of(mono_type):
            if sname == "tag":
                continue
            if stype.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if stype.is_tag_generic_origin:
                continue
            subtype_items.append((sname, stype))

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname, _ in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            lines.append(f"    {tag},\n")
        lines.append(f"}} {cbase}_tag_t;\n\n")

        # check if all subtypes are null (enum pattern)
        all_null = all(stype.typetype == ZTypeType.NULL for _, stype in subtype_items)

        # emit variant struct with inline union
        lines.append("typedef struct {\n")
        lines.append(f"    {cbase}_tag_t tag;\n")
        if not all_null:
            lines.append("    union {\n")
            for sname, stype in subtype_items:
                is_null = stype.typetype == ZTypeType.NULL
                if not is_null:
                    sub_ctype = _ctype(self.typing, stype)
                    if sub_ctype and sub_ctype != "void":
                        lines.append(f"        {sub_ctype} {sname};\n")
            lines.append("    } data;\n")
        lines.append(f"}} {struct};\n\n")

        # emit equality function (if auto-generated)
        eq_method = self.typing.child_of(mono_type, "==")
        if eq_method and eq_method.is_autogen_eq:
            ctype = struct
            lines.append(f"static bool {cbase}_eq({ctype} a, {ctype} b) {{\n")
            if self._use_memcmp_eq(name, mono_type, eq_method):
                self.needs_string = True
                lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
            elif all_null:
                lines.append("    return a.tag == b.tag;\n")
            else:
                lines.append("    if (a.tag != b.tag) return false;\n")
                lines.append("    switch (a.tag) {\n")
                for sname, stype in subtype_items:
                    tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
                    is_null = stype.typetype == ZTypeType.NULL
                    lines.append(f"        case {tag}:")
                    if is_null:
                        lines.append(" return true;\n")
                    elif self._needs_eq_call(stype):
                        tname = stype.name.replace(".", "_")
                        lines.append(
                            f" return {_cbase_of(stype, tname)}_eq"
                            f"(a.data.{sname}, b.data.{sname});\n"
                        )
                    else:
                        lines.append(f" return a.data.{sname} == b.data.{sname};\n")
                lines.append("    }\n")
                lines.append("    return false;\n")
            lines.append("}\n\n")

        # NO destructor — value type
        self.struct_defs.append("".join(lines))

    def _emit_mono_box(self, mono_type: ZType) -> None:
        """Emit a monomorphized box(T) destructor.

        If the inner type has its own destructor (class with heap fields,
        string, etc.), chain it before freeing the box allocation.
        """
        self.needs_stdlib = True
        name = mono_type.name
        inner_type = self.typing.generic_arg_of(mono_type, "t")
        if not inner_type:
            return
        inner_ctype = _ctype(self.typing, inner_type)
        ptr_ctype = f"{inner_ctype}*"
        lines: List[str] = []
        lines.append(f"static void z_{name}_destroy({ptr_ctype} v) {{\n")
        lines.append("    if (!v) return;\n")
        # chain inner destructor for types that own heap resources
        if (inner_type.destructor_name is not None) and inner_type.destructor_name:
            if inner_type.is_heap_allocated:
                # inner is a pointer type (map, etc.); pass as-is
                lines.append(f"    {inner_type.destructor_name}(v);\n")
            else:
                # inner is a stack type (class, string, etc.); pass pointer to it
                lines.append(f"    {inner_type.destructor_name}(v);\n")
        lines.append("    free(v);\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mono_record(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized record type."""
        self.needs_stdint = True
        name = mono_type.name
        ctype = _cname_of(mono_type, name)
        lines: List[str] = []
        lines.append("typedef struct {\n")
        field_items: list = []
        for fname, ftype in self.typing.children_of(mono_type):
            if ftype.typetype == ZTypeType.FUNCTION:
                continue
            ct = _ctype(self.typing, ftype)
            lines.append(f"    {ct} {fname};\n")
            field_items.append((fname, ct))
        lines.append(f"}} {ctype};\n\n")

        # emit meta.create and create functions
        params = [f"{ct} {fn}" for fn, ct in field_items]
        field_names = [fn for fn, _ in field_items]
        self._emit_create_functions(
            _cbase_of(mono_type, name),
            ctype,
            params,
            field_names,
            is_heap=False,
            has_user_create=False,
            lines=lines,
        )

        # register field info for call emission
        self._type_field_ctypes[name] = [ct for _, ct in field_items]
        self._type_field_names[name] = field_names

        # emit auto-generated equality function
        self._emit_autogen_eq_from_fields(name, mono_type, field_items, lines)

        self.struct_defs.append("".join(lines))

    def _emit_mono_array(self, mono_type: ZType) -> None:
        """Emit a monomorphized array type (struct, create, get, set, length).

        Body comes from src/runtime/z_array.c.tmpl. The create body and
        optional equality body vary per monomorphization (element type
        may be a record needing its own constructor; equality may be
        memcmp, elem-eq-call, or raw ==) so they're computed here and
        spliced into the template as @@CREATE_BODY@@ / @@EQ_BODY@@.
        """
        self.needs_stdint = True
        self.needs_stdio = True
        name = mono_type.name
        ctype = f"z_{name}_t"
        elem_type = _array_element_type(self.typing, mono_type)
        arr_len = _array_length(self.typing, mono_type)
        if not elem_type or arr_len is None:
            return
        elem_ctype = _ctype(self.typing, elem_type)

        # create body — record elements need their own constructor calls
        # to populate each slot; everything else zero-initialises.
        if elem_type.typetype == ZTypeType.RECORD and elem_type.name not in TYPEMAP:
            create_body = (
                f"    {ctype} _this;\n"
                f"    for (int _i = 0; _i < {arr_len}; _i++) {{ "
                f"_this.data[_i] = z_{elem_type.name}_create("
                f"{self._zero_args_for_ctypes(elem_type.name)}"
                f"); }}"
            )
        else:
            create_body = f"    {ctype} _this = {{0}};"

        # equality body — only emitted when the equality is auto-generated
        # (user-defined == is emitted separately via the method path).
        eq_body_parts: List[str] = []
        eq_method = self.typing.child_of(mono_type, "==")
        if eq_method and eq_method.is_autogen_eq:
            eq_body_parts.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{")
            if self._use_memcmp_eq(name, mono_type, eq_method):
                self.needs_string = True
                eq_body_parts.append(
                    f"    return memcmp(&a, &b, sizeof({ctype})) == 0;"
                )
            elif self._needs_eq_call(elem_type):
                ename = elem_type.name.replace(".", "_")
                eq_body_parts.append(
                    f"    for (int _i = 0; _i < {arr_len}; _i++) {{ "
                    f"if (!z_{ename}_eq(a.data[_i], b.data[_i])) return false; }}"
                )
                eq_body_parts.append("    return true;")
            else:
                eq_body_parts.append(
                    f"    for (int _i = 0; _i < {arr_len}; _i++) {{ "
                    f"if (a.data[_i] != b.data[_i]) return false; }}"
                )
                eq_body_parts.append("    return true;")
            eq_body_parts.append("}")
            eq_body_parts.append("")  # trailing blank line to match old output
        eq_body = "\n".join(eq_body_parts)

        self.struct_defs.append(
            ztmpl.apply(
                "z_array",
                {
                    "NAME": name,
                    "ELEM_T": elem_ctype,
                    "LEN": str(arr_len),
                    "CREATE_BODY": create_body,
                    "EQ_BODY": eq_body,
                },
            )
        )

    def _emit_mono_str(self, mono_type: ZType) -> None:
        """Emit a monomorphized str type (struct, create, string conversion,
        optional equality). Body comes from src/runtime/z_str.c.tmpl.
        `len` is a uint8/16/32 picked from the capacity so str_N stays
        compact for small N.
        """
        self.needs_stdint = True
        self.needs_string = True
        name = mono_type.name
        ctype = f"z_{name}_t"
        cap = _str_capacity(self.typing, mono_type)
        if cap is None:
            return

        # compact length type based on capacity
        if cap <= 255:
            len_ctype = "uint8_t"
        elif cap <= 65535:
            len_ctype = "uint16_t"
        else:
            len_ctype = "uint32_t"

        self.needs_stdlib = True  # the .string conversion path uses malloc

        # equality is auto-generated (byte-wise) when enabled; keep the
        # branch here so the template stays uniform across str monos.
        eq_method = self.typing.child_of(mono_type, "==")
        eq_body_parts: List[str] = []
        if eq_method and eq_method.is_autogen_eq:
            eq_body_parts.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{")
            eq_body_parts.append(
                "    return a.len == b.len && memcmp(a.data, b.data, a.len) == 0;"
            )
            eq_body_parts.append("}")
            eq_body_parts.append("")  # trailing blank line to match the old output
        eq_body = "\n".join(eq_body_parts)

        self.struct_defs.append(
            ztmpl.apply(
                "z_str",
                {
                    "NAME": name,
                    "CAP": str(cap),
                    "LEN_T": len_ctype,
                    "EQ_BODY": eq_body,
                },
            )
        )

    def _emit_mono_list(self, mono_type: ZType) -> None:
        """Emit a monomorphized list type (struct, create, destroy, methods).

        Body comes from src/runtime/z_List.c.tmpl. Two parts vary per
        monomorphization:

        * `@@DESTROY_ELEMS@@` — empty when the element type is trivial;
          a per-element cleanup loop when the element has a destructor.
        * `@@LISTVIEW_METHODS@@` — empty when the list has no companion
          listview child; the `.listview` / `.extend_view` pair when it
          does.
        """
        self.needs_stdint = True
        self.needs_stdlib = True
        self.needs_stdio = True
        self.needs_string = True
        name = mono_type.name
        cbase = _cbase_of(mono_type, name)
        ctype = _cname_of(mono_type, name)
        elem_type = _list_element_type(self.typing, mono_type)
        if elem_type is None:
            return
        elem_ctype = _ctype(self.typing, elem_type)

        # destroy — per-element cleanup loop only when the element type
        # actually needs it.
        if (elem_type.destructor_name is not None) and elem_type.destructor_name:
            elem_destr = elem_type.destructor_name
            elem_addr = "" if elem_type.is_heap_allocated else "&"
            destroy_elems = (
                f"    for (uint64_t i = 0; i < p->length; i++) {{\n"
                f"        {elem_destr}({elem_addr}p->data[i]);\n"
                f"    }}\n"
            )
        else:
            destroy_elems = ""

        # listview companion methods — only when the list monomorphization
        # carries a `.listview` child (same first-two-field layout as
        # listview, so the cast is zero-cost).
        listview_child = self.typing.child_of(mono_type, "listview")
        listview_methods = ""
        if listview_child and listview_child.return_type:
            lv_name = listview_child.return_type.name
            lv_ctype = _cname_of(listview_child.return_type, lv_name)
            listview_methods = (
                f"static {lv_ctype} {cbase}_listview({ctype}* _this);\n"
                f"static {lv_ctype} {cbase}_listview({ctype}* _this) {{\n"
                f"    return *({lv_ctype}*)_this;\n"
                f"}}\n"
                f"\n"
                f"static void {cbase}_extendView({ctype}* _this, {lv_ctype} _from);\n"
                f"static void {cbase}_extendView({ctype}* _this, {lv_ctype} _from) {{\n"
                f"    {cbase}_grow(_this, _this->length + _from.length);\n"
                f"    memcpy(&_this->data[_this->length], _from.data, "
                f"_from.length * sizeof({elem_ctype}));\n"
                f"    _this->length += _from.length;\n"
                f"}}\n"
                f"\n"
            )

        self.struct_defs.append(
            ztmpl.apply(
                "z_List",
                {
                    "NAME": name,
                    "ELEM_T": elem_ctype,
                    "DESTROY_ELEMS": destroy_elems,
                    "LISTVIEW_METHODS": listview_methods,
                },
            )
        )

        # contains companion: linear scan with hardcoded equality dispatch
        # by element type. Mirrors the Map-key / Set-item equality logic
        # (numeric ==, String size+memcmp, str len+memcmp).
        contains_child = self.typing.child_of(mono_type, "contains")
        if contains_child is not None:
            self._emit_list_contains(cbase, ctype, elem_ctype, elem_type)

        # sort companion: stable in-place mergesort with comparator
        # hardcoded by element type.
        sort_child = self.typing.child_of(mono_type, "sort")
        if sort_child is not None:
            self._emit_list_sort(cbase, ctype, elem_ctype, elem_type)

        # listiter companion: iterator + .iterate factory. Emitted only
        # when the list mono carries an `.iterate` child returning a
        # listiter mono (set up in the type checker for any list with
        # `.iterate` synthesised — currently every concrete list mono).
        iterate_child = self.typing.child_of(mono_type, "iterate")
        if iterate_child and iterate_child.return_type:
            iterate_cn = iterate_child.cname or f"z_{name}_iterate"
            self._emit_listiter_runtime(
                ctype, iterate_cn, elem_ctype, iterate_child.return_type
            )

    def _emit_list_contains(
        self,
        cbase: str,
        ctype: str,
        elem_ctype: str,
        elem_type: ZType,
    ) -> None:
        """Emit `static int <cbase>_contains(...)` -- linear scan with
        equality dispatch by element type. Numeric: `_a == _b`. String
        (`z_String_t`): size + memcmp. `str` valtype: len + memcmp."""
        elem_is_string = elem_ctype == "z_String_t"  # ztc-string-compare-ok: ctype
        lines: List[str] = []
        lines.append(
            f"static int {cbase}_contains({ctype}* _this, {elem_ctype} _item);\n"
        )
        lines.append(
            f"static int {cbase}_contains({ctype}* _this, {elem_ctype} _item) {{\n"
        )
        lines.append("    for (uint64_t i = 0; i < _this->length; i++) {\n")
        if elem_is_string:
            lines.append(
                "        if (_this->data[i].size == _item.size "
                "&& memcmp(_this->data[i].data, _item.data, _item.size) == 0) "
                "return 1;\n"
            )
        elif _is_str_type(elem_type):
            lines.append(
                "        if (_this->data[i].len == _item.len "
                "&& memcmp(_this->data[i].data, _item.data, _item.len) == 0) "
                "return 1;\n"
            )
        else:
            lines.append("        if (_this->data[i] == _item) return 1;\n")
        lines.append("    }\n")
        lines.append("    return 0;\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_list_sort(
        self,
        cbase: str,
        ctype: str,
        elem_ctype: str,
        elem_type: ZType,
    ) -> None:
        """Emit `static void <cbase>_sort(...)` -- stable in-place
        mergesort with hardcoded comparator. Numeric: `<`. String
        (`z_String_t`): z_String_cmp. `str` valtype: byte-lex memcmp
        with shorter-prefix-loses tie-break."""
        elem_is_string = elem_ctype == "z_String_t"  # ztc-string-compare-ok: ctype
        cmp_fn = f"{cbase}_sort_lt"
        merge_fn = f"{cbase}_sort_merge"
        msort_fn = f"{cbase}_sort_rec"
        lines: List[str] = []
        # element-type comparator: returns 1 if a < b, else 0. The merge
        # step uses `!cmp(b, a)` for the "take from left" predicate so
        # equal elements keep their original order (stability).
        lines.append(f"static int {cmp_fn}({elem_ctype} _a, {elem_ctype} _b);\n")
        lines.append(f"static int {cmp_fn}({elem_ctype} _a, {elem_ctype} _b) {{\n")
        if elem_is_string:
            lines.append("    return z_String_cmp(&_a, &_b) < 0;\n")
        elif _is_str_type(elem_type):
            lines.append("    uint64_t n = _a.len < _b.len ? _a.len : _b.len;\n")
            lines.append("    int c = n > 0 ? memcmp(_a.data, _b.data, n) : 0;\n")
            lines.append("    if (c != 0) return c < 0;\n")
            lines.append("    return _a.len < _b.len;\n")
        else:
            lines.append("    return _a < _b;\n")
        lines.append("}\n\n")
        # merge two adjacent runs [lo, mid) and [mid, hi) using `scratch`
        # as auxiliary storage.
        lines.append(
            f"static void {merge_fn}({elem_ctype}* data, {elem_ctype}* scratch, "
            "uint64_t lo, uint64_t mid, uint64_t hi);\n"
        )
        lines.append(
            f"static void {merge_fn}({elem_ctype}* data, {elem_ctype}* scratch, "
            "uint64_t lo, uint64_t mid, uint64_t hi) {\n"
        )
        lines.append("    uint64_t i = lo, j = mid, k = lo;\n")
        lines.append("    while (i < mid && j < hi) {\n")
        # !cmp(b, a) means a <= b — pick from the left to preserve order.
        lines.append(
            f"        if (!{cmp_fn}(data[j], data[i])) scratch[k++] = data[i++];\n"
        )
        lines.append("        else scratch[k++] = data[j++];\n")
        lines.append("    }\n")
        lines.append("    while (i < mid) scratch[k++] = data[i++];\n")
        lines.append("    while (j < hi) scratch[k++] = data[j++];\n")
        lines.append(
            "    memcpy(&data[lo], &scratch[lo], (hi - lo) * sizeof(*data));\n"
        )
        lines.append("}\n\n")
        # recursive top-down split.
        lines.append(
            f"static void {msort_fn}({elem_ctype}* data, {elem_ctype}* scratch, "
            "uint64_t lo, uint64_t hi);\n"
        )
        lines.append(
            f"static void {msort_fn}({elem_ctype}* data, {elem_ctype}* scratch, "
            "uint64_t lo, uint64_t hi) {\n"
        )
        lines.append("    if (hi - lo <= 1) return;\n")
        lines.append("    uint64_t mid = lo + (hi - lo) / 2;\n")
        lines.append(f"    {msort_fn}(data, scratch, lo, mid);\n")
        lines.append(f"    {msort_fn}(data, scratch, mid, hi);\n")
        lines.append(f"    {merge_fn}(data, scratch, lo, mid, hi);\n")
        lines.append("}\n\n")
        # public entry: allocate scratch once, drive the recursion.
        lines.append(f"static void {cbase}_sort({ctype}* _this);\n")
        lines.append(f"static void {cbase}_sort({ctype}* _this) {{\n")
        lines.append("    if (_this->length < 2) return;\n")
        lines.append(
            f"    {elem_ctype}* scratch = ({elem_ctype}*)z_xmalloc("
            f"_this->length * sizeof({elem_ctype}));\n"
        )
        lines.append(f"    {msort_fn}(_this->data, scratch, 0, _this->length);\n")
        lines.append("    free(scratch);\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_listiter_runtime(
        self,
        list_ctype: str,
        iterate_cn: str,
        elem_ctype: str,
        listiter_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a listiter monomorphization.

        Layout: { source list pointer, current index }. Each .call peeks
        at list->data[idx], wraps the address in optionview.some, and
        increments idx; returns optionview.none when idx >= length.
        """
        li_name = listiter_mono.name
        li_ctype = _cname_of(listiter_mono, li_name)
        call_method = self.typing.child_of(listiter_mono, "call")
        if not call_method or not call_method.return_type:
            return
        call_cn = call_method.cname or f"z_{li_name}_call"
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = _cname_of(ov_mono, ov_name)
        ov_some_tag = f"Z_{ov_name.upper()}_TAG_SOME"
        ov_none_tag = f"Z_{ov_name.upper()}_TAG_NONE"

        # listiter is declared `is native` in stdlib so the default
        # mono-class emission is skipped; emit the real layout here as
        # part of the source list's mono pass.
        lines: List[str] = []
        lines.append(f"/* listiter<{elem_ctype}> runtime layout */\n")
        lines.append("typedef struct {\n")
        lines.append(f"    {list_ctype}* list;\n")
        lines.append("    uint64_t idx;\n")
        lines.append(f"}} {li_ctype};\n\n")
        lines.append(f"static {ov_ctype} {call_cn}({li_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} {call_cn}({li_ctype}* _it) {{\n")
        lines.append(f"    {ov_ctype} _out = {{0}};\n")
        lines.append("    if (_it->idx >= _it->list->length) {\n")
        lines.append(f"        _out.tag = {ov_none_tag};\n")
        lines.append("        return _out;\n")
        lines.append("    }\n")
        lines.append(f"    _out.tag = {ov_some_tag};\n")
        lines.append("    _out.data = &_it->list->data[_it->idx];\n")
        lines.append("    _it->idx++;\n")
        lines.append("    return _out;\n")
        lines.append("}\n\n")
        lines.append(f"static {li_ctype} {iterate_cn}({list_ctype}* _this);\n")
        lines.append(f"static {li_ctype} {iterate_cn}({list_ctype}* _this) {{\n")
        lines.append(f"    {li_ctype} _it = {{0}};\n")
        lines.append("    _it.list = _this;\n")
        lines.append("    _it.idx = 0;\n")
        lines.append("    return _it;\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mono_listview(self, mono_type: ZType) -> None:
        """Emit a monomorphized listview type (view into a list).

        Listview has the same first two fields as list ({length, data*})
        for zero-cost casting. No destructor — listview doesn't own data.
        Body comes from src/runtime/z_ListView.c.tmpl.
        """
        self.needs_stdint = True
        self.needs_stdlib = True
        self.needs_stdio = True
        elem_type = _listview_element_type(self.typing, mono_type)
        if elem_type is None:
            return
        self.struct_defs.append(
            ztmpl.apply(
                "z_ListView",
                {"NAME": mono_type.name, "ELEM_T": _ctype(self.typing, elem_type)},
            )
        )

    def _emit_mono_map(self, mono_type: ZType) -> None:
        """Emit a monomorphized map type using a CPython-style compact-dict
        layout: a sparse `indices` array of int64 slots (EMPTY=-1,
        DELETED=-2, else index into `entries`) plus a dense, insertion-
        ordered `entries` array of (alive, hash, key, value) records.
        Iteration walks entries in order; insertion order is preserved
        across deletes and resizes."""
        self.needs_stdint = True
        self.needs_stdlib = True
        self.needs_stdio = True
        self.needs_string = True
        # Bucket dispatch routes through z_siphash_string / z_hash_u64;
        # pull the SipHash runtime in.
        self.needs_hash = True
        name = mono_type.name
        cbase = _cbase_of(mono_type, name)
        ctype = f"{cbase}_t"
        key_type = _map_key_type(self.typing, mono_type)
        value_type = _map_value_type(self.typing, mono_type)
        if key_type is None or value_type is None:
            return
        key_ctype = _ctype(self.typing, key_type)
        val_ctype = _ctype(self.typing, value_type)
        key_is_string = key_ctype == "z_String_t"
        val_is_string = val_ctype == "z_String_t"
        val_is_reftype = val_ctype.endswith("*")
        entry_type = f"{cbase}_entry_t"
        # Kept alias for backward-compatible external references (e.g.
        # mapitemiter emits mapentry as a typedef of the entry type).
        bucket_type = entry_type
        lines: List[str] = []

        # indices sentinels
        lines.append(f"#define Z_{name.upper()}_INDEX_EMPTY (-1)\n")
        lines.append(f"#define Z_{name.upper()}_INDEX_DELETED (-2)\n\n")

        # entry struct (dense, insertion-ordered)
        lines.append("typedef struct {\n")
        lines.append("    uint8_t alive;\n")
        lines.append("    uint64_t hash;\n")
        lines.append(f"    {key_ctype} key;\n")
        lines.append(f"    {val_ctype} value;\n")
        lines.append(f"}} {entry_type};\n\n")

        # map struct -- tagged so the forward-typedef pass for late
        # monos can name it before user defs that reference it
        lines.append(f"typedef struct {ctype} {{\n")
        lines.append("    uint64_t capacity;\n")
        lines.append("    uint64_t length;\n")
        lines.append("    uint64_t entries_len;\n")
        lines.append("    uint64_t entries_cap;\n")
        lines.append("    int64_t* indices;\n")
        lines.append(f"    {entry_type}* entries;\n")
        lines.append(f"}} {ctype};\n\n")

        # hash function -- thin wrapper over the shared SipHash / splitmix64
        # helpers in the runtime preamble. String / str / view keys feed
        # raw bytes into z_siphash_bytes; numeric keys cast to u64 and
        # run through z_hash_u64 (splitmix64 finalizer).
        hash_fn = f"{cbase}_hash_key"
        lines.append(f"static uint64_t {hash_fn}({key_ctype} _key);\n")
        lines.append(f"static uint64_t {hash_fn}({key_ctype} _key) {{\n")
        if key_is_string:
            lines.append("    return z_siphash_string(_key);\n")
        elif _is_str_type(key_type):
            lines.append("    return z_siphash_bytes(_key.data, _key.len);\n")
        else:
            lines.append("    return z_hash_u64((uint64_t)_key);\n")
        lines.append("}\n\n")

        # key equality function
        eq_fn = f"{cbase}_keys_equal"
        lines.append(f"static int {eq_fn}({key_ctype} _a, {key_ctype} _b);\n")
        lines.append(f"static int {eq_fn}({key_ctype} _a, {key_ctype} _b) {{\n")
        if key_is_string:
            lines.append(
                "    return _a.size == _b.size "
                "&& memcmp(_a.data, _b.data, _a.size) == 0;\n"
            )
        elif _is_str_type(key_type):
            lines.append(
                "    return _a.len == _b.len "
                "&& memcmp(_a.data, _b.data, _a.len) == 0;\n"
            )
        else:
            lines.append("    return _a == _b;\n")
        lines.append("}\n\n")

        # helper: free a key if it carries a destructor
        def emit_free_key(var: str, indent: str = "    ") -> str:
            if (
                key_type
                and (key_type.destructor_name is not None)
                and key_type.destructor_name
            ):
                if not key_type.is_heap_allocated:
                    return f"{indent}{key_type.destructor_name}(&{var});\n"
                return f"{indent}{key_type.destructor_name}({var});\n"
            return ""

        # helper: free a value if it carries a destructor
        def emit_free_val(var: str, indent: str = "    ") -> str:
            if (
                value_type
                and (value_type.destructor_name is not None)
                and value_type.destructor_name
            ):
                if not value_type.is_heap_allocated:
                    return f"{indent}{value_type.destructor_name}(&{var});\n"
                return f"{indent}{value_type.destructor_name}({var});\n"
            return ""

        # destroy — walk dense entries[] and free live keys/values.
        lines.append(f"static void {cbase}_destroy({ctype}* p);\n")
        lines.append(f"static void {cbase}_destroy({ctype}* p) {{\n")
        lines.append("    if (!p) return;\n")
        key_needs_free = bool(key_type and (key_type.destructor_name is not None))
        val_needs_free = bool(value_type and (value_type.destructor_name is not None))
        if key_needs_free or val_needs_free:
            lines.append("    for (uint64_t i = 0; i < p->entries_len; i++) {\n")
            lines.append("        if (p->entries[i].alive) {\n")
            lines.append(emit_free_key("p->entries[i].key", "            "))
            lines.append(emit_free_val("p->entries[i].value", "            "))
            lines.append("        }\n")
            lines.append("    }\n")
        lines.append("    free(p->indices);\n")
        lines.append("    free(p->entries);\n")
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # create
        lines.append(f"static {ctype}* {cbase}_create(uint64_t _capacity);\n")
        lines.append(f"static {ctype}* {cbase}_create(uint64_t _capacity) {{\n")
        lines.append(f"    {ctype}* _this = ({ctype}*)z_xmalloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        lines.append("    if (_capacity < 8) _capacity = 0;\n")
        lines.append("    _this->capacity = _capacity;\n")
        lines.append("    _this->entries_cap = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            "        _this->indices = (int64_t*)z_xmalloc(_capacity * sizeof(int64_t));\n"
        )
        lines.append("        for (uint64_t i = 0; i < _capacity; i++) {\n")
        lines.append(f"            _this->indices[i] = Z_{name.upper()}_INDEX_EMPTY;\n")
        lines.append("        }\n")
        lines.append(
            f"        _this->entries = ({entry_type}*)z_xcalloc(_capacity, sizeof({entry_type}));\n"
        )
        lines.append("    }\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

        # grow/resize — rebuild indices (doubled) and compact entries
        # (drop tombstones, preserve insertion order). After grow,
        # entries_len == length and entries_cap == new capacity.
        grow_fn = f"{cbase}_grow"
        lines.append(f"static void {grow_fn}({ctype}* _this);\n")
        lines.append(f"static void {grow_fn}({ctype}* _this) {{\n")
        lines.append("    uint64_t old_cap = _this->capacity;\n")
        lines.append("    uint64_t new_cap = old_cap == 0 ? 8 : old_cap * 2;\n")
        lines.append(f"    {entry_type}* old_entries = _this->entries;\n")
        lines.append("    uint64_t old_entries_len = _this->entries_len;\n")
        lines.append(
            "    int64_t* new_indices = (int64_t*)z_xmalloc(new_cap * sizeof(int64_t));\n"
        )
        lines.append("    for (uint64_t i = 0; i < new_cap; i++) {\n")
        lines.append(f"        new_indices[i] = Z_{name.upper()}_INDEX_EMPTY;\n")
        lines.append("    }\n")
        lines.append(
            f"    {entry_type}* new_entries = ({entry_type}*)z_xcalloc(new_cap, sizeof({entry_type}));\n"
        )
        lines.append("    uint64_t new_entries_len = 0;\n")
        lines.append("    for (uint64_t i = 0; i < old_entries_len; i++) {\n")
        lines.append("        if (!old_entries[i].alive) continue;\n")
        lines.append("        new_entries[new_entries_len] = old_entries[i];\n")
        lines.append("        uint64_t probe = old_entries[i].hash & (new_cap - 1);\n")
        lines.append(
            f"        while (new_indices[probe] != Z_{name.upper()}_INDEX_EMPTY) {{\n"
        )
        lines.append("            probe = (probe + 1) & (new_cap - 1);\n")
        lines.append("        }\n")
        lines.append("        new_indices[probe] = (int64_t)new_entries_len;\n")
        lines.append("        new_entries_len++;\n")
        lines.append("    }\n")
        lines.append("    free(_this->indices);\n")
        lines.append("    free(old_entries);\n")
        lines.append("    _this->indices = new_indices;\n")
        lines.append("    _this->entries = new_entries;\n")
        lines.append("    _this->capacity = new_cap;\n")
        lines.append("    _this->entries_cap = new_cap;\n")
        lines.append("    _this->entries_len = new_entries_len;\n")
        lines.append("}\n\n")

        # find helper — probe indices[]; returns entry index (>=0) or -1.
        # Writes the indices-slot to *_slot_out (caller's reference)
        # when non-NULL; used by delete to mark the slot DELETED.
        find_fn = f"{cbase}_find"
        lines.append(
            f"static int64_t {find_fn}({ctype}* _this, {key_ctype} _key, "
            "uint64_t _hash, int64_t* _slot_out);\n"
        )
        lines.append(
            f"static int64_t {find_fn}({ctype}* _this, {key_ctype} _key, "
            "uint64_t _hash, int64_t* _slot_out) {\n"
        )
        lines.append("    if (_this->capacity == 0) return -1;\n")
        lines.append("    uint64_t probe = _hash & (_this->capacity - 1);\n")
        lines.append("    for (uint64_t i = 0; i < _this->capacity; i++) {\n")
        lines.append("        int64_t slot = _this->indices[probe];\n")
        lines.append(f"        if (slot == Z_{name.upper()}_INDEX_EMPTY) {{\n")
        lines.append("            if (_slot_out) *_slot_out = (int64_t)probe;\n")
        lines.append("            return -1;\n")
        lines.append("        }\n")
        lines.append(f"        if (slot != Z_{name.upper()}_INDEX_DELETED) {{\n")
        lines.append(
            "            if (_this->entries[slot].hash == _hash "
            f"&& {eq_fn}(_this->entries[slot].key, _key)) {{\n"
        )
        lines.append("                if (_slot_out) *_slot_out = (int64_t)probe;\n")
        lines.append("                return slot;\n")
        lines.append("            }\n")
        lines.append("        }\n")
        lines.append("        probe = (probe + 1) & (_this->capacity - 1);\n")
        lines.append("    }\n")
        lines.append("    return -1;\n")
        lines.append("}\n\n")

        # set — replace value if key exists; else append a new entry and
        # write its index into the first empty (or earliest deleted)
        # indices slot encountered on the probe sequence.
        lines.append(
            f"static void {cbase}_set({ctype}* _this, {key_ctype} _key, {val_ctype} _val);\n"
        )
        lines.append(
            f"static void {cbase}_set({ctype}* _this, {key_ctype} _key, {val_ctype} _val) {{\n"
        )
        lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
        lines.append(f"    int64_t existing = {find_fn}(_this, _key, h, NULL);\n")
        lines.append("    if (existing >= 0) {\n")
        lines.append(emit_free_val("_this->entries[existing].value", "        "))
        lines.append("        _this->entries[existing].value = _val;\n")
        lines.append(emit_free_key("_key", "        "))
        lines.append("        return;\n")
        lines.append("    }\n")
        lines.append(
            "    if (_this->capacity == 0 || (_this->length + 1) * 3 >= _this->capacity * 2) {\n"
        )
        lines.append(f"        {grow_fn}(_this);\n")
        lines.append("    }\n")
        # probe for insertion slot; reuse earliest DELETED if before EMPTY.
        lines.append("    uint64_t probe = h & (_this->capacity - 1);\n")
        lines.append("    int64_t first_deleted = -1;\n")
        lines.append("    for (uint64_t i = 0; i < _this->capacity; i++) {\n")
        lines.append("        int64_t slot = _this->indices[probe];\n")
        lines.append(f"        if (slot == Z_{name.upper()}_INDEX_EMPTY) {{\n")
        lines.append(
            "            if (first_deleted >= 0) probe = (uint64_t)first_deleted;\n"
        )
        lines.append("            break;\n")
        lines.append("        }\n")
        lines.append(
            f"        if (slot == Z_{name.upper()}_INDEX_DELETED "
            "&& first_deleted < 0) {\n"
        )
        lines.append("            first_deleted = (int64_t)probe;\n")
        lines.append("        }\n")
        lines.append("        probe = (probe + 1) & (_this->capacity - 1);\n")
        lines.append("    }\n")
        # append entry; grow entries[] if needed (defensive — should not fire
        # because grow keeps entries_cap == capacity).
        lines.append("    if (_this->entries_len >= _this->entries_cap) {\n")
        lines.append(
            "        uint64_t new_ec = _this->entries_cap == 0 ? 8 : _this->entries_cap * 2;\n"
        )
        lines.append(
            f"        _this->entries = ({entry_type}*)z_xrealloc(_this->entries, new_ec * sizeof({entry_type}));\n"
        )
        lines.append(
            "        for (uint64_t i = _this->entries_cap; i < new_ec; i++) {\n"
        )
        lines.append(f"            _this->entries[i] = ({entry_type}){{0}};\n")
        lines.append("        }\n")
        lines.append("        _this->entries_cap = new_ec;\n")
        lines.append("    }\n")
        lines.append("    int64_t new_idx = (int64_t)_this->entries_len;\n")
        lines.append("    _this->entries[new_idx].alive = 1;\n")
        lines.append("    _this->entries[new_idx].hash = h;\n")
        lines.append("    _this->entries[new_idx].key = _key;\n")
        lines.append("    _this->entries[new_idx].value = _val;\n")
        lines.append("    _this->entries_len++;\n")
        lines.append("    _this->indices[probe] = new_idx;\n")
        lines.append("    _this->length++;\n")
        lines.append("}\n\n")

        # get method — returns option (nullable ptr for reftype values) or optionval (variant for valtype values)
        get_type = self.typing.child_of(mono_type, "get")
        ret_type = get_type.return_type if get_type else None
        if ret_type:
            self.needs_stdlib = True
            ret_ctype = _ctype(self.typing, ret_type)
            opt_name = ret_type.name
            get_fn = f"{cbase}_get"

            if ret_type.is_nullable_ptr:
                # nullable-ptr option: return pointer or NULL
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key);\n"
                )
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key) {{\n"
                )
                lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
                lines.append(f"    int64_t idx = {find_fn}(_this, _key, h, NULL);\n")
                lines.append("    if (idx >= 0) {\n")
                if val_is_string:
                    lines.append("        z_String_t _copy = {0};\n")
                    lines.append(
                        "        _copy.size = _this->entries[idx].value.size;\n"
                    )
                    lines.append("        _copy.capacity = _copy.size + 1;\n")
                    lines.append(
                        "        _copy.data = (char*)z_xmalloc(_copy.capacity);\n"
                    )
                    lines.append(
                        "        memcpy(_copy.data, _this->entries[idx].value.data, _copy.size);\n"
                    )
                    lines.append("        _copy.data[_copy.size] = '\\0';\n")
                    lines.append("        return _copy;\n")
                else:
                    lines.append("        return _this->entries[idx].value;\n")
                lines.append("    }\n")
                lines.append("    return NULL;\n")
                lines.append("}\n\n")
            elif ret_type.typetype == ZTypeType.VARIANT:
                # optionval variant: return struct by value
                opt_struct = _cname_of(ret_type, opt_name)
                some_tag = f"Z_{opt_name.upper()}_TAG_SOME"
                none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key);\n"
                )
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key) {{\n"
                )
                lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
                lines.append(f"    int64_t idx = {find_fn}(_this, _key, h, NULL);\n")
                lines.append(f"    {opt_struct} _r;\n")
                lines.append("    if (idx >= 0) {\n")
                lines.append(f"        _r.tag = {some_tag};\n")
                lines.append("        _r.data.some = _this->entries[idx].value;\n")
                lines.append("    } else {\n")
                lines.append(f"        _r.tag = {none_tag};\n")
                lines.append("    }\n")
                lines.append("    return _r;\n")
                lines.append("}\n\n")
            else:
                # regular tagged union (legacy path)
                opt_struct = _cname_of(ret_type, opt_name)
                some_tag = f"Z_{opt_name.upper()}_TAG_SOME"
                none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key);\n"
                )
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key) {{\n"
                )
                lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
                lines.append(f"    int64_t idx = {find_fn}(_this, _key, h, NULL);\n")
                lines.append(f"    {opt_struct} _r = {{0}};\n")
                lines.append("    if (idx >= 0) {\n")
                lines.append(f"        _r.tag = {some_tag};\n")
                if val_is_string:
                    # String values are stack structs that own a heap data
                    # buffer. A shallow struct copy aliases the buffer and
                    # double-frees when both the Map's storage AND the
                    # returned Option's box are destroyed. Deep-copy: malloc
                    # a fresh String + memcpy the buffer.
                    lines.append(
                        "        z_String_t* _copy = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
                    )
                    lines.append(
                        "        _copy->size = _this->entries[idx].value.size;\n"
                    )
                    lines.append("        _copy->capacity = _copy->size + 1;\n")
                    lines.append(
                        "        _copy->data = (char*)z_xmalloc(_copy->capacity);\n"
                    )
                    lines.append(
                        "        memcpy(_copy->data, _this->entries[idx].value.data, _copy->size);\n"
                    )
                    lines.append("        _copy->data[_copy->size] = '\\0';\n")
                    lines.append("        _r.data = _copy;\n")
                elif val_is_reftype:
                    # Heap-allocated reftype: the stored value IS a pointer;
                    # alias it into the Option's data slot. (Note: this is
                    # a borrowed pattern — destroying both Map and Option
                    # will double-free. Revisit when Map.get's ownership
                    # model is settled; for now Map<reftype-other-than-
                    # String> remains untested in the tree.)
                    lines.append("        _r.data = _this->entries[idx].value;\n")
                else:
                    lines.append(
                        f"        {val_ctype}* _d = ({val_ctype}*)z_xmalloc(sizeof({val_ctype}));\n"
                    )
                    lines.append("        *_d = _this->entries[idx].value;\n")
                    lines.append("        _r.data = _d;\n")
                lines.append("    } else {\n")
                lines.append(f"        _r.tag = {none_tag};\n")
                lines.append("        _r.data = NULL;\n")
                lines.append("    }\n")
                lines.append("    return _r;\n")
                lines.append("}\n\n")

        # delete — mark indices slot DELETED, tombstone entries[idx]
        # (free key/value, alive=0). Compaction happens on next resize.
        lines.append(f"static int {cbase}_delete({ctype}* _this, {key_ctype} _key);\n")
        lines.append(
            f"static int {cbase}_delete({ctype}* _this, {key_ctype} _key) {{\n"
        )
        lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
        lines.append("    int64_t slot = -1;\n")
        lines.append(f"    int64_t idx = {find_fn}(_this, _key, h, &slot);\n")
        lines.append("    if (idx < 0) return 0;\n")
        lines.append(emit_free_key("_this->entries[idx].key", "    "))
        lines.append(emit_free_val("_this->entries[idx].value", "    "))
        lines.append("    _this->entries[idx].alive = 0;\n")
        lines.append(f"    _this->indices[slot] = Z_{name.upper()}_INDEX_DELETED;\n")
        lines.append("    _this->length--;\n")
        lines.append("    return 1;\n")
        lines.append("}\n\n")

        # has — returns bool
        lines.append(f"static int {cbase}_has({ctype}* _this, {key_ctype} _key);\n")
        lines.append(f"static int {cbase}_has({ctype}* _this, {key_ctype} _key) {{\n")
        lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
        lines.append(f"    return {find_fn}(_this, _key, h, NULL) >= 0;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

        # mapkeyiter companion: borrowed-key iterator + .iterate factory.
        # Emitted only when the map mono carries an `.iterate` child.
        iterate_child = self.typing.child_of(mono_type, "iterate")
        if iterate_child and iterate_child.return_type:
            iterate_cn = iterate_child.cname or f"z_{name}_iterate"
            self._emit_mapkeyiter_runtime(
                ctype, iterate_cn, key_ctype, iterate_child.return_type
            )

        # mapitemiter + mapentry companion: borrowed-entry iterator +
        # .iterate_items factory. Emitted only when the map mono carries
        # an `.iterate_items` child. mapentry's C representation is a
        # typedef alias for the bucket type; .key / .value emit through
        # the bucket pointer.
        iterate_items_child = self.typing.child_of(mono_type, "iterateItems")
        if iterate_items_child and iterate_items_child.return_type:
            iterate_items_cn = iterate_items_child.cname or f"z_{name}_iterateItems"
            self._emit_mapitemiter_runtime(
                ctype, iterate_items_cn, bucket_type, iterate_items_child.return_type
            )

    def _emit_mapkeyiter_runtime(
        self,
        map_ctype: str,
        iterate_cn: str,
        key_ctype: str,
        mki_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a mapkeyiter monomorphization.

        Layout: { source map pointer, current entries index }. Each .call
        scans forward through `entries[]` (the dense, insertion-ordered
        array), returning the next live entry's key wrapped in
        optionview.some, or optionview.none when entries are exhausted.
        Tombstoned entries (alive == 0) are skipped without altering
        iteration order.
        """
        mki_name = mki_mono.name
        mki_ctype = _cname_of(mki_mono, mki_name)
        call_method = self.typing.child_of(mki_mono, "call")
        if not call_method or not call_method.return_type:
            return
        call_cn = call_method.cname or f"z_{mki_name}_call"
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = _cname_of(ov_mono, ov_name)
        ov_some_tag = f"Z_{ov_name.upper()}_TAG_SOME"
        ov_none_tag = f"Z_{ov_name.upper()}_TAG_NONE"

        lines: List[str] = []
        lines.append(f"/* mapkeyiter<{key_ctype}> runtime layout */\n")
        lines.append("typedef struct {\n")
        lines.append(f"    {map_ctype}* m;\n")
        lines.append("    uint64_t idx;\n")
        lines.append(f"}} {mki_ctype};\n\n")
        lines.append(f"static {ov_ctype} {call_cn}({mki_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} {call_cn}({mki_ctype}* _it) {{\n")
        lines.append(f"    {ov_ctype} _out = {{0}};\n")
        lines.append("    while (_it->idx < _it->m->entries_len) {\n")
        lines.append("        if (_it->m->entries[_it->idx].alive) {\n")
        lines.append(f"            _out.tag = {ov_some_tag};\n")
        lines.append("            _out.data = &_it->m->entries[_it->idx].key;\n")
        lines.append("            _it->idx++;\n")
        lines.append("            return _out;\n")
        lines.append("        }\n")
        lines.append("        _it->idx++;\n")
        lines.append("    }\n")
        lines.append(f"    _out.tag = {ov_none_tag};\n")
        lines.append("    return _out;\n")
        lines.append("}\n\n")
        lines.append(f"static {mki_ctype} {iterate_cn}({map_ctype}* _this);\n")
        lines.append(f"static {mki_ctype} {iterate_cn}({map_ctype}* _this) {{\n")
        lines.append(f"    {mki_ctype} _it = {{0}};\n")
        lines.append("    _it.m = _this;\n")
        lines.append("    _it.idx = 0;\n")
        lines.append("    return _it;\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mapitemiter_runtime(
        self,
        map_ctype: str,
        iterate_items_cn: str,
        bucket_type: str,
        mii_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a mapitemiter
        monomorphization plus the mapentry typedef.

        mapitemiter layout: { source map pointer, current entries index }.
        Each .call scans forward through `entries[]` (dense, insertion-
        ordered), returning the next live entry address wrapped in
        optionview.some, or optionview.none when entries are exhausted.

        mapentry is a borrow-only view: at the C level it is a typedef
        alias for the entry struct. .key / .value access compile to
        field projections through the entry pointer.
        """
        mii_name = mii_mono.name
        mii_ctype = _cname_of(mii_mono, mii_name)
        call_method = self.typing.child_of(mii_mono, "call")
        if not call_method or not call_method.return_type:
            return
        call_cn = call_method.cname or f"z_{mii_name}_call"
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = _cname_of(ov_mono, ov_name)
        ov_some_tag = f"Z_{ov_name.upper()}_TAG_SOME"
        ov_none_tag = f"Z_{ov_name.upper()}_TAG_NONE"

        # mapentry mono: pulled from the optionview's some payload type
        me_mono = self.typing.child_of(ov_mono, "some")
        if me_mono is None:
            return
        me_name = me_mono.name
        me_ctype = _cname_of(me_mono, me_name)

        lines: List[str] = []
        lines.append(f"/* mapentry<{me_name}> = view of {bucket_type} */\n")
        lines.append(f"typedef {bucket_type} {me_ctype};\n\n")
        lines.append(f"/* mapitemiter<{me_name}> runtime layout */\n")
        lines.append("typedef struct {\n")
        lines.append(f"    {map_ctype}* m;\n")
        lines.append("    uint64_t idx;\n")
        lines.append(f"}} {mii_ctype};\n\n")
        lines.append(f"static {ov_ctype} {call_cn}({mii_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} {call_cn}({mii_ctype}* _it) {{\n")
        lines.append(f"    {ov_ctype} _out = {{0}};\n")
        lines.append("    while (_it->idx < _it->m->entries_len) {\n")
        lines.append("        if (_it->m->entries[_it->idx].alive) {\n")
        lines.append(f"            _out.tag = {ov_some_tag};\n")
        lines.append("            _out.data = &_it->m->entries[_it->idx];\n")
        lines.append("            _it->idx++;\n")
        lines.append("            return _out;\n")
        lines.append("        }\n")
        lines.append("        _it->idx++;\n")
        lines.append("    }\n")
        lines.append(f"    _out.tag = {ov_none_tag};\n")
        lines.append("    return _out;\n")
        lines.append("}\n\n")
        lines.append(f"static {mii_ctype} {iterate_items_cn}({map_ctype}* _this);\n")
        lines.append(f"static {mii_ctype} {iterate_items_cn}({map_ctype}* _this) {{\n")
        lines.append(f"    {mii_ctype} _it = {{0}};\n")
        lines.append("    _it.m = _this;\n")
        lines.append("    _it.idx = 0;\n")
        lines.append("    return _it;\n")
        lines.append("}\n\n")
        # mapentry .key and .value — field projections through the
        # bucket pointer. The C functions take the bucket pointer
        # (mapentry is a typedef of the bucket type) and return a
        # by-value copy of the field. For reftype field types, the
        # copy aliases the source's heap data.
        key_method = self.typing.child_of(me_mono, "key")
        value_method = self.typing.child_of(me_mono, "value")
        if key_method is not None and key_method.return_type is not None:
            key_ctype = _ctype(self.typing, key_method.return_type)
            key_cn = key_method.cname or f"z_{me_name}_key"
            lines.append(f"static {key_ctype} {key_cn}({me_ctype}* _e);\n")
            lines.append(f"static {key_ctype} {key_cn}({me_ctype}* _e) {{\n")
            lines.append("    return _e->key;\n")
            lines.append("}\n\n")
        if value_method is not None and value_method.return_type is not None:
            val_ctype = _ctype(self.typing, value_method.return_type)
            value_cn = value_method.cname or f"z_{me_name}_value"
            lines.append(f"static {val_ctype} {value_cn}({me_ctype}* _e);\n")
            lines.append(f"static {val_ctype} {value_cn}({me_ctype}* _e) {{\n")
            lines.append("    return _e->value;\n")
            lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mono_set(self, mono_type: ZType) -> None:
        """Emit a monomorphized set type using a CPython-style
        compact-dict layout: sparse `indices` array of int64 slots plus a
        dense, insertion-ordered `entries` array of (alive, hash, item).
        Structure mirrors `_emit_mono_map` without the value column."""
        self.needs_stdint = True
        self.needs_stdlib = True
        self.needs_stdio = True
        self.needs_string = True
        self.needs_hash = True
        name = mono_type.name
        cbase = _cbase_of(mono_type, name)
        ctype = f"{cbase}_t"
        elem_type = _set_element_type(self.typing, mono_type)
        if elem_type is None:
            return
        elem_ctype = _ctype(self.typing, elem_type)
        elem_is_string = elem_ctype == "z_String_t"  # ztc-string-compare-ok: ctype
        entry_type = f"{cbase}_entry_t"
        lines: List[str] = []

        # indices sentinels
        lines.append(f"#define Z_{name.upper()}_INDEX_EMPTY (-1)\n")
        lines.append(f"#define Z_{name.upper()}_INDEX_DELETED (-2)\n\n")

        # entry struct (dense, insertion-ordered, no value column)
        lines.append("typedef struct {\n")
        lines.append("    uint8_t alive;\n")
        lines.append("    uint64_t hash;\n")
        lines.append(f"    {elem_ctype} item;\n")
        lines.append(f"}} {entry_type};\n\n")

        # set struct -- tagged so the forward-typedef pass for late
        # monos can name it before user defs that reference it
        lines.append(f"typedef struct {ctype} {{\n")
        lines.append("    uint64_t capacity;\n")
        lines.append("    uint64_t length;\n")
        lines.append("    uint64_t entries_len;\n")
        lines.append("    uint64_t entries_cap;\n")
        lines.append("    int64_t* indices;\n")
        lines.append(f"    {entry_type}* entries;\n")
        lines.append(f"}} {ctype};\n\n")

        # hash function -- same dispatch as map; thin wrapper over the
        # shared SipHash / splitmix64 helpers in the runtime preamble.
        hash_fn = f"{cbase}_hash_item"
        lines.append(f"static uint64_t {hash_fn}({elem_ctype} _key);\n")
        lines.append(f"static uint64_t {hash_fn}({elem_ctype} _key) {{\n")
        if elem_is_string:
            lines.append("    return z_siphash_string(_key);\n")
        elif _is_str_type(elem_type):
            lines.append("    return z_siphash_bytes(_key.data, _key.len);\n")
        else:
            lines.append("    return z_hash_u64((uint64_t)_key);\n")
        lines.append("}\n\n")

        # equality function
        eq_fn = f"{cbase}_items_equal"
        lines.append(f"static int {eq_fn}({elem_ctype} _a, {elem_ctype} _b);\n")
        lines.append(f"static int {eq_fn}({elem_ctype} _a, {elem_ctype} _b) {{\n")
        if elem_is_string:
            lines.append(
                "    return _a.size == _b.size "
                "&& memcmp(_a.data, _b.data, _a.size) == 0;\n"
            )
        elif _is_str_type(elem_type):
            lines.append(
                "    return _a.len == _b.len "
                "&& memcmp(_a.data, _b.data, _a.len) == 0;\n"
            )
        else:
            lines.append("    return _a == _b;\n")
        lines.append("}\n\n")

        # helper: free an item if it carries a destructor
        def emit_free_item(var: str, indent: str = "    ") -> str:
            if (
                elem_type
                and (elem_type.destructor_name is not None)
                and elem_type.destructor_name
            ):
                if not elem_type.is_heap_allocated:
                    return f"{indent}{elem_type.destructor_name}(&{var});\n"
                return f"{indent}{elem_type.destructor_name}({var});\n"
            return ""

        item_needs_free = bool(elem_type and (elem_type.destructor_name is not None))

        # destroy — walk dense entries[] and free live items.
        lines.append(f"static void {cbase}_destroy({ctype}* p);\n")
        lines.append(f"static void {cbase}_destroy({ctype}* p) {{\n")
        lines.append("    if (!p) return;\n")
        if item_needs_free:
            lines.append("    for (uint64_t i = 0; i < p->entries_len; i++) {\n")
            lines.append("        if (p->entries[i].alive) {\n")
            lines.append(emit_free_item("p->entries[i].item", "            "))
            lines.append("        }\n")
            lines.append("    }\n")
        lines.append("    free(p->indices);\n")
        lines.append("    free(p->entries);\n")
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # create
        lines.append(f"static {ctype}* {cbase}_create(uint64_t _capacity);\n")
        lines.append(f"static {ctype}* {cbase}_create(uint64_t _capacity) {{\n")
        lines.append(f"    {ctype}* _this = ({ctype}*)z_xmalloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        lines.append("    if (_capacity < 8) _capacity = 0;\n")
        lines.append("    _this->capacity = _capacity;\n")
        lines.append("    _this->entries_cap = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            "        _this->indices = (int64_t*)z_xmalloc(_capacity * sizeof(int64_t));\n"
        )
        lines.append("        for (uint64_t i = 0; i < _capacity; i++) {\n")
        lines.append(f"            _this->indices[i] = Z_{name.upper()}_INDEX_EMPTY;\n")
        lines.append("        }\n")
        lines.append(
            f"        _this->entries = ({entry_type}*)z_xcalloc(_capacity, sizeof({entry_type}));\n"
        )
        lines.append("    }\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

        # grow/resize — rebuild indices (doubled) and compact entries.
        grow_fn = f"{cbase}_grow"
        lines.append(f"static void {grow_fn}({ctype}* _this);\n")
        lines.append(f"static void {grow_fn}({ctype}* _this) {{\n")
        lines.append("    uint64_t old_cap = _this->capacity;\n")
        lines.append("    uint64_t new_cap = old_cap == 0 ? 8 : old_cap * 2;\n")
        lines.append(f"    {entry_type}* old_entries = _this->entries;\n")
        lines.append("    uint64_t old_entries_len = _this->entries_len;\n")
        lines.append(
            "    int64_t* new_indices = (int64_t*)z_xmalloc(new_cap * sizeof(int64_t));\n"
        )
        lines.append("    for (uint64_t i = 0; i < new_cap; i++) {\n")
        lines.append(f"        new_indices[i] = Z_{name.upper()}_INDEX_EMPTY;\n")
        lines.append("    }\n")
        lines.append(
            f"    {entry_type}* new_entries = ({entry_type}*)z_xcalloc(new_cap, sizeof({entry_type}));\n"
        )
        lines.append("    uint64_t new_entries_len = 0;\n")
        lines.append("    for (uint64_t i = 0; i < old_entries_len; i++) {\n")
        lines.append("        if (!old_entries[i].alive) continue;\n")
        lines.append("        new_entries[new_entries_len] = old_entries[i];\n")
        lines.append("        uint64_t probe = old_entries[i].hash & (new_cap - 1);\n")
        lines.append(
            f"        while (new_indices[probe] != Z_{name.upper()}_INDEX_EMPTY) {{\n"
        )
        lines.append("            probe = (probe + 1) & (new_cap - 1);\n")
        lines.append("        }\n")
        lines.append("        new_indices[probe] = (int64_t)new_entries_len;\n")
        lines.append("        new_entries_len++;\n")
        lines.append("    }\n")
        lines.append("    free(_this->indices);\n")
        lines.append("    free(old_entries);\n")
        lines.append("    _this->indices = new_indices;\n")
        lines.append("    _this->entries = new_entries;\n")
        lines.append("    _this->capacity = new_cap;\n")
        lines.append("    _this->entries_cap = new_cap;\n")
        lines.append("    _this->entries_len = new_entries_len;\n")
        lines.append("}\n\n")

        # find — returns entry index (>=0) or -1; writes indices slot
        # to *_slot_out when non-NULL (for delete).
        find_fn = f"{cbase}_find"
        lines.append(
            f"static int64_t {find_fn}({ctype}* _this, {elem_ctype} _item, "
            "uint64_t _hash, int64_t* _slot_out);\n"
        )
        lines.append(
            f"static int64_t {find_fn}({ctype}* _this, {elem_ctype} _item, "
            "uint64_t _hash, int64_t* _slot_out) {\n"
        )
        lines.append("    if (_this->capacity == 0) return -1;\n")
        lines.append("    uint64_t probe = _hash & (_this->capacity - 1);\n")
        lines.append("    for (uint64_t i = 0; i < _this->capacity; i++) {\n")
        lines.append("        int64_t slot = _this->indices[probe];\n")
        lines.append(f"        if (slot == Z_{name.upper()}_INDEX_EMPTY) {{\n")
        lines.append("            if (_slot_out) *_slot_out = (int64_t)probe;\n")
        lines.append("            return -1;\n")
        lines.append("        }\n")
        lines.append(f"        if (slot != Z_{name.upper()}_INDEX_DELETED) {{\n")
        lines.append(
            "            if (_this->entries[slot].hash == _hash "
            f"&& {eq_fn}(_this->entries[slot].item, _item)) {{\n"
        )
        lines.append("                if (_slot_out) *_slot_out = (int64_t)probe;\n")
        lines.append("                return slot;\n")
        lines.append("            }\n")
        lines.append("        }\n")
        lines.append("        probe = (probe + 1) & (_this->capacity - 1);\n")
        lines.append("    }\n")
        lines.append("    return -1;\n")
        lines.append("}\n\n")

        # add — true if new, false if already present. Append to entries
        # and write index to first empty / earliest deleted indices slot.
        lines.append(f"static int {cbase}_add({ctype}* _this, {elem_ctype} _item);\n")
        lines.append(f"static int {cbase}_add({ctype}* _this, {elem_ctype} _item) {{\n")
        lines.append(f"    uint64_t h = {hash_fn}(_item);\n")
        lines.append(f"    int64_t existing = {find_fn}(_this, _item, h, NULL);\n")
        lines.append("    if (existing >= 0) {\n")
        lines.append(emit_free_item("_item", "        "))
        lines.append("        return 0;\n")
        lines.append("    }\n")
        lines.append(
            "    if (_this->capacity == 0 || (_this->length + 1) * 3 >= _this->capacity * 2) {\n"
        )
        lines.append(f"        {grow_fn}(_this);\n")
        lines.append("    }\n")
        lines.append("    uint64_t probe = h & (_this->capacity - 1);\n")
        lines.append("    int64_t first_deleted = -1;\n")
        lines.append("    for (uint64_t i = 0; i < _this->capacity; i++) {\n")
        lines.append("        int64_t slot = _this->indices[probe];\n")
        lines.append(f"        if (slot == Z_{name.upper()}_INDEX_EMPTY) {{\n")
        lines.append(
            "            if (first_deleted >= 0) probe = (uint64_t)first_deleted;\n"
        )
        lines.append("            break;\n")
        lines.append("        }\n")
        lines.append(
            f"        if (slot == Z_{name.upper()}_INDEX_DELETED "
            "&& first_deleted < 0) {\n"
        )
        lines.append("            first_deleted = (int64_t)probe;\n")
        lines.append("        }\n")
        lines.append("        probe = (probe + 1) & (_this->capacity - 1);\n")
        lines.append("    }\n")
        lines.append("    if (_this->entries_len >= _this->entries_cap) {\n")
        lines.append(
            "        uint64_t new_ec = _this->entries_cap == 0 ? 8 : _this->entries_cap * 2;\n"
        )
        lines.append(
            f"        _this->entries = ({entry_type}*)z_xrealloc(_this->entries, new_ec * sizeof({entry_type}));\n"
        )
        lines.append(
            "        for (uint64_t i = _this->entries_cap; i < new_ec; i++) {\n"
        )
        lines.append(f"            _this->entries[i] = ({entry_type}){{0}};\n")
        lines.append("        }\n")
        lines.append("        _this->entries_cap = new_ec;\n")
        lines.append("    }\n")
        lines.append("    int64_t new_idx = (int64_t)_this->entries_len;\n")
        lines.append("    _this->entries[new_idx].alive = 1;\n")
        lines.append("    _this->entries[new_idx].hash = h;\n")
        lines.append("    _this->entries[new_idx].item = _item;\n")
        lines.append("    _this->entries_len++;\n")
        lines.append("    _this->indices[probe] = new_idx;\n")
        lines.append("    _this->length++;\n")
        lines.append("    return 1;\n")
        lines.append("}\n\n")

        # has
        lines.append(f"static int {cbase}_has({ctype}* _this, {elem_ctype} _item);\n")
        lines.append(f"static int {cbase}_has({ctype}* _this, {elem_ctype} _item) {{\n")
        lines.append(f"    uint64_t h = {hash_fn}(_item);\n")
        lines.append(f"    return {find_fn}(_this, _item, h, NULL) >= 0;\n")
        lines.append("}\n\n")

        # delete — mark indices slot DELETED, tombstone entries[idx].
        lines.append(
            f"static int {cbase}_delete({ctype}* _this, {elem_ctype} _item);\n"
        )
        lines.append(
            f"static int {cbase}_delete({ctype}* _this, {elem_ctype} _item) {{\n"
        )
        lines.append(f"    uint64_t h = {hash_fn}(_item);\n")
        lines.append("    int64_t slot = -1;\n")
        lines.append(f"    int64_t idx = {find_fn}(_this, _item, h, &slot);\n")
        lines.append("    if (idx < 0) return 0;\n")
        lines.append(emit_free_item("_this->entries[idx].item", "    "))
        lines.append("    _this->entries[idx].alive = 0;\n")
        lines.append(f"    _this->indices[slot] = Z_{name.upper()}_INDEX_DELETED;\n")
        lines.append("    _this->length--;\n")
        lines.append("    return 1;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

        # setiter companion: emitted only when the set carries an
        # `.iterate` child (it always does, but be defensive).
        iterate_child = self.typing.child_of(mono_type, "iterate")
        if iterate_child and iterate_child.return_type:
            iterate_cn = iterate_child.cname or f"z_{name}_iterate"
            self._emit_setiter_runtime(
                ctype, iterate_cn, elem_ctype, iterate_child.return_type
            )

    def _emit_setiter_runtime(
        self,
        set_ctype: str,
        iterate_cn: str,
        elem_ctype: str,
        si_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a setiter monomorphization.

        Layout: { source set pointer, current entries index }. Each .call
        scans forward through `entries[]` (dense, insertion-ordered),
        returning the next live entry's item wrapped in optionview.some,
        or optionview.none when entries are exhausted. Tombstoned
        entries (alive == 0) are skipped.
        """
        si_name = si_mono.name
        si_ctype = _cname_of(si_mono, si_name)
        call_method = self.typing.child_of(si_mono, "call")
        if not call_method or not call_method.return_type:
            return
        call_cn = call_method.cname or f"z_{si_name}_call"
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = _cname_of(ov_mono, ov_name)
        ov_some_tag = f"Z_{ov_name.upper()}_TAG_SOME"
        ov_none_tag = f"Z_{ov_name.upper()}_TAG_NONE"

        lines: List[str] = []
        lines.append(f"/* setiter<{elem_ctype}> runtime layout */\n")
        lines.append("typedef struct {\n")
        lines.append(f"    {set_ctype}* s;\n")
        lines.append("    uint64_t idx;\n")
        lines.append(f"}} {si_ctype};\n\n")
        lines.append(f"static {ov_ctype} {call_cn}({si_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} {call_cn}({si_ctype}* _it) {{\n")
        lines.append(f"    {ov_ctype} _out = {{0}};\n")
        lines.append("    while (_it->idx < _it->s->entries_len) {\n")
        lines.append("        if (_it->s->entries[_it->idx].alive) {\n")
        lines.append(f"            _out.tag = {ov_some_tag};\n")
        lines.append("            _out.data = &_it->s->entries[_it->idx].item;\n")
        lines.append("            _it->idx++;\n")
        lines.append("            return _out;\n")
        lines.append("        }\n")
        lines.append("        _it->idx++;\n")
        lines.append("    }\n")
        lines.append(f"    _out.tag = {ov_none_tag};\n")
        lines.append("    return _out;\n")
        lines.append("}\n\n")
        lines.append(f"static {si_ctype} {iterate_cn}({set_ctype}* _this);\n")
        lines.append(f"static {si_ctype} {iterate_cn}({set_ctype}* _this) {{\n")
        lines.append(f"    {si_ctype} _it = {{0}};\n")
        lines.append("    _it.s = _this;\n")
        lines.append("    _it.idx = 0;\n")
        lines.append("    return _it;\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mono_class(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized class type."""
        self.needs_stdint = True
        self.needs_stdlib = True
        name = mono_type.name
        lines: List[str] = []

        # collect fields (non-special, non-function children)
        field_items = [
            (fn, ft)
            for fn, ft in self.typing.children_of(mono_type)
            if ft.typetype != ZTypeType.FUNCTION
        ]

        # struct typedef -- tagged so the forward-typedef pass for late
        # monos can name it before user defs that reference it
        lines.append(f"typedef struct z_{name}_t {{\n")
        for fname, ftype in field_items:
            ct = _ctype(self.typing, ftype)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                self.typing.is_child_lock_field(mono_type, fname)
                and ftype.typetype == ZTypeType.CLASS
                and not ftype.is_heap_allocated
                and not ct.endswith("*")
            ):
                ct = f"{ct}*"
            lines.append(f"    {ct} {fname};\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destructor (only if class has fields needing cleanup)
        if mono_type.needs_field_cleanup:
            lines.append(f"static void z_{name}_destroy(z_{name}_t* p) {{\n")
            lines.append("    if (!p) return;\n")
            for fname, ftype in field_items:
                # .lock fields are borrowed references, don't own data
                if self.typing.is_child_lock_field(mono_type, fname):
                    continue
                lines.append(self._emit_field_cleanup(f"p->{fname}", ftype))
            lines.append("}\n\n")

        # meta.create constructor
        self._emit_mono_create(name, mono_type, field_items, lines)

        self.struct_defs.append("".join(lines))

        # emit methods from cloned or template defn with mangled names
        cloned_methods = self.typing.cloned_methods.get(name)
        func_aliases = self.typing.func_aliases
        if template_defn.nodetype == NodeType.CLASS:
            for mname, mfunc in (
                cast(zast.ObjectDef, template_defn).as_functions().items()
            ):
                if mfunc.body:
                    qualified = f"{name}.{mname}"
                    if qualified in func_aliases:
                        canonical = func_aliases[qualified]
                        alias_c = _mangle_func(qualified)
                        canon_c = _mangle_func(canonical)
                        self.func_aliases.append(f"#define {alias_c} {canon_c}\n")
                        # emit forward decl so callers can reference the alias
                        self._emit_alias_forward_decl(
                            qualified, mfunc, record_name=name
                        )
                    else:
                        func_to_emit = (
                            cloned_methods[mname]
                            if cloned_methods and mname in cloned_methods
                            else mfunc
                        )
                        self._emit_function(qualified, func_to_emit, record_name=name)

    def _emit_mono_create(
        self,
        name: str,
        mono_type: ZType,
        field_items: list,
        lines: List[str],
    ) -> None:
        """Emit meta.create and create functions for a monomorphized type.

        Uses mono_type.is_heap_allocated to select stack vs heap allocation.
        """
        ctype = _cname_of(mono_type, name)
        params: List[str] = []
        field_names: List[str] = []
        field_ctypes_list: List[str] = []
        for fname, ftype in field_items:
            ct = _ctype(self.typing, ftype)
            params.append(f"{ct} {fname}")
            field_names.append(fname)
            field_ctypes_list.append(ct)
        self._type_field_ctypes[name] = field_ctypes_list
        self._type_field_names[name] = field_names
        if name not in self._type_field_defaults:
            self._type_field_defaults[name] = {}
        self._emit_create_functions(
            _cbase_of(mono_type, name),
            ctype,
            params,
            field_names,
            is_heap=mono_type.is_heap_allocated,
            has_user_create=False,
            lines=lines,
        )

    def _emit_mono_protocol(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized protocol type."""
        self.needs_stdint = True
        self.needs_stdlib = True
        name = mono_type.name

        # collect spec functions from mono_type.children
        specs = [
            (sn, st)
            for sn, st in self.typing.children_of(mono_type)
            if st.typetype == ZTypeType.FUNCTION
        ]

        # Build the function-pointer block. Shape matches
        # _emit_protocol; only the function-list source differs
        # (typing-table walk vs AST walk).
        func_lines: List[str] = []
        for sname, stype in specs:
            ret_type = stype.return_type
            ret_ctype = _ctype(self.typing, ret_type) if ret_type else "void"
            params = ["void*"]
            for pname, ptype in self.typing.children_of(stype):
                if stype.this_param_name == pname:
                    continue
                params.append(_proto_param_ctype(self.typing, ptype))
            func_lines.append(f"    {ret_ctype} (*{sname})({', '.join(params)});\n")

        self.struct_defs.append(
            ztmpl.apply(
                "z_protocol_vtable",
                {"NAME": name, "VTABLE_FUNCS": "".join(func_lines)},
            )
        )

    def _emit_variant(self, name: str, variant_defn: zast.ObjectDef) -> None:
        self.needs_stdint = True
        lines: List[str] = []

        # Stored dot-free C names (see _emit_union for the rationale).
        vtype = self._node_ztype(variant_defn)
        struct = _cname_of(vtype, name)
        cbase = _cbase_of(vtype, name)
        tagpfx = (vtype.name if vtype and vtype.name else name).upper()

        # resolve custom tag values from as_items
        custom_tag_values = self._resolve_tag_values(variant_defn)

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname in variant_defn.is_paths().keys():
            tag = f"Z_{tagpfx}_TAG_{sname.upper()}"
            if custom_tag_values and sname in custom_tag_values:
                lines.append(f"    {tag} = {custom_tag_values[sname]},\n")
            else:
                lines.append(f"    {tag},\n")
        lines.append(f"}} {cbase}_tag_t;\n\n")

        # check if all subtypes are null (enum pattern)
        all_null = all(
            spath.nodetype == NodeType.ATOMID
            and cast(zast.AtomId, spath).name == "null"
            for spath in variant_defn.is_paths().values()
        )

        # emit variant struct with inline union
        lines.append("typedef struct {\n")
        lines.append(f"    {cbase}_tag_t tag;\n")
        if not all_null:
            lines.append("    union {\n")
            for sname, spath in variant_defn.is_paths().items():
                is_null = (
                    spath.nodetype == NodeType.ATOMID
                    and cast(zast.AtomId, spath).name == "null"
                )
                if not is_null:
                    sub_ctype = self._get_subtype_ctype(spath)
                    if sub_ctype:
                        lines.append(f"        {sub_ctype} {sname};\n")
            lines.append("    } data;\n")
        lines.append(f"}} {struct};\n\n")

        # emit equality function (if auto-generated)
        eq_method = self.typing.child_of(vtype, "==") if vtype else None
        if eq_method and eq_method.is_autogen_eq:
            ctype = struct
            lines.append(f"static bool {cbase}_eq({ctype} a, {ctype} b) {{\n")
            if self._use_memcmp_eq(name, vtype, eq_method):
                self.needs_string = True
                lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
            elif all_null:
                lines.append("    return a.tag == b.tag;\n")
            else:
                lines.append("    if (a.tag != b.tag) return false;\n")
                lines.append("    switch (a.tag) {\n")
                for sname, spath in variant_defn.is_paths().items():
                    tag = f"Z_{tagpfx}_TAG_{sname.upper()}"
                    is_null = (
                        spath.nodetype == NodeType.ATOMID
                        and cast(zast.AtomId, spath).name == "null"
                    )
                    lines.append(f"        case {tag}:")
                    if is_null:
                        lines.append(" return true;\n")
                    else:
                        sub_ctype = self._get_subtype_ctype(spath)
                        sub_type = self._node_ztype(spath)
                        if sub_type and self._needs_eq_call(sub_type):
                            sub_eq = f"{_cbase_of(sub_type, sub_type.name.replace('.', '_'))}_eq"
                            lines.append(
                                f" return {sub_eq}(a.data.{sname}, b.data.{sname});\n"
                            )
                        elif (
                            sub_ctype
                            and sub_ctype.startswith("z_")
                            and sub_ctype.endswith("_t")
                        ):
                            # sub_ctype is `z_<base>_t`; drop the `_t` suffix to
                            # get the eq function's base.
                            lines.append(
                                f" return {sub_ctype[:-2]}_eq"
                                f"(a.data.{sname}, b.data.{sname});\n"
                            )
                        else:
                            lines.append(f" return a.data.{sname} == b.data.{sname};\n")
                lines.append("    }\n")
                lines.append("    return false;\n")
            lines.append("}\n\n")

        # NO destructor — value type
        self.struct_defs.append("".join(lines))

        # emit methods
        all_funcs = list(variant_defn.functions().items()) + list(
            variant_defn.as_functions().items()
        )
        for mname, mfunc in all_funcs:
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' constants
        self._emit_as_constants(name, variant_defn.as_items)

    def _emit_data(self, name: str, data: zast.Data) -> None:
        self.needs_stdint = True
        values: List[str] = []
        label_values: Dict[str, str] = {}
        for item in data.data:
            op = item.valtype
            val_str: Optional[str] = None
            if op.nodetype == NodeType.ATOMID and _is_numeric_id(
                cast(zast.AtomId, op).name
            ):
                _, val, err = parse_number(cast(zast.AtomId, op).name)
                if not err:
                    val_str = str(val) if type(val) is float else str(int(val))
            elif op.nodetype == NodeType.DOTTEDPATH:
                # Typed numeric literal, e.g. `0.u8` — combine the
                # parent's numeric atom with the suffix to parse the
                # full value.
                dp = cast(zast.DottedPath, op)
                if dp.parent.nodetype == NodeType.ATOMID and _is_numeric_id(
                    cast(zast.AtomId, dp.parent).name
                ):
                    combined = cast(zast.AtomId, dp.parent).name + dp.child.name
                    _, val, err = parse_number(combined)
                    if not err:
                        val_str = str(val) if type(val) is float else str(int(val))
            if val_str is not None:
                values.append(val_str)
                # Record name->value for compile-time substitution at
                # `<name>.<label>` access sites. Numeric labels (the
                # bare-positional form) are already covered by `.index N`
                # and `.N` lookups; only the named-label form needs this
                # side table.
                if item.name:
                    label_values[item.name] = val_str
        # Key the array cname and the label table by the data block's
        # dot-free ztype.name. A dependency unit's block has a dotted
        # AST-path `name` (zlexer.ascii); ztype.name is the dot-free
        # zlexer_ascii, matching the parent_def.name the access sites in
        # _emit_dotted_path_value look up by.
        data_ztype = self._node_ztype(data)
        key = data_ztype.name if data_ztype is not None else name
        cname = _mangle_func(key)
        # Pick the C element type from the data block's resolved
        # element_type. Falls back to int64_t when unresolved (defensive
        # against early-error recovery).
        elem_ctype = "int64_t"
        if data_ztype is not None and data_ztype.element_type is not None:
            elem_ctype = _ctype(self.typing, data_ztype.element_type)
        # Skip the static array when typecheck found no runtime access —
        # every use site already carries `node_const_value` and inlines
        # via `_emit_operation_value`'s const-value short-circuit.
        # `label_values` still populated below for the legacy named-
        # label substitution path (kept as a safety net).
        emit_array = values and (data_ztype is None or data_ztype.runtime_indexed)
        if emit_array:
            self.data_defs.append(
                f"static const {elem_ctype} {cname}[] = "
                f"{{{', '.join(values)}}};\n"
                f"static const int64_t {cname}_len = {len(values)};\n\n"
            )
        if label_values:
            self._data_label_values[key] = label_values

    def _return_ctype(self, func: zast.Function) -> str:
        if not func.returntype:
            return "void"
        ct = _ctype(self.typing, self._node_ztype(func.returntype))
        if ct == "z_String_t":
            self.needs_string = True
            self.needs_stdlib = True
        elif ct.endswith("*"):
            self.needs_stdlib = True
        return ct

    def _emit_alias_forward_decl(
        self, name: str, func: zast.Function, record_name: str = ""
    ) -> None:
        """Emit a forward declaration for a deduped alias function."""
        self.needs_stdint = True
        cname = _mangle_func(name)
        ret_ctype = self._return_ctype(func)
        record_type = self._enclosing_type(func)
        is_class_method = bool(record_type and record_type.typetype == ZTypeType.CLASS)
        # Ownership annotations live on the resolved ZType (which carries
        # both syntactic suffixes and the inferred BORROW-default for
        # stack-reftype params); read them from there.
        ftype = self._node_ztype(func)
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(self.typing, self._node_ztype(ppath))
            # this-receiver: pass by pointer
            if (
                is_class_method
                and self._node_ztype(ppath) is record_type
                and not ptype_str.endswith("*")
            ):
                ptype_str = f"{ptype_str}*"
            # stack-allocated class borrow/lock params
            elif (
                self._node_ztype(ppath)
                and cast(ZType, self._node_ztype(ppath)).typetype == ZTypeType.CLASS
                and not cast(ZType, self._node_ztype(ppath)).is_heap_allocated
                and not ptype_str.endswith("*")
                and ftype
                and self.typing.child_ownership(ftype, pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ptype_str = f"{ptype_str}*"
            params.append(
                f"{ptype_str} {self._def_cname(ppath) or mangle_var_name(pname)}"
            )
        param_str = ", ".join(params) if params else "void"
        self.forward_decls.append(f"{ret_ctype} {cname}({param_str});\n")

    def _emit_function(
        self, name: str, func: zast.Function, record_name: str = ""
    ) -> None:
        self.needs_stdint = True
        cname = _mangle_func(name)

        # G4: synthesised generator `.call` body — wire up the
        # state-machine context before normal function emission. The
        # context tells `_emit_expression_stmt` (and friends) how to
        # lower each `yield <expr>` into a suspension fragment, and
        # provides the wrapper-type tag names for `OPTION_SOME` /
        # `OPTION_NONE` emission.
        is_generator_call = func.synth_origin == "generator-call"
        if is_generator_call:
            self._setup_generator_ctx(func, name)

        ret_ctype = self._return_ctype(func)

        # Class methods pass the `this` receiver by pointer so mutations via
        # `it.field = ...` persist across calls.
        record_type = self._enclosing_type(func)
        is_class_method = bool(record_type and record_type.typetype == ZTypeType.CLASS)

        # Ownership annotations live on the resolved ZType (carries
        # both syntactic suffixes and inferred BORROW-default).
        ftype = self._node_ztype(func)

        params: List[str] = []
        pointer_params: List[Optional[int]] = []
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(self.typing, self._node_ztype(ppath))
            # class this-receiver: pass by pointer
            if (
                is_class_method
                and self._node_ztype(ppath) is record_type
                and not ptype_str.endswith("*")
            ):
                ptype_str = f"{ptype_str}*"
                pointer_params.append(self._def_vid(ppath))
            # stack-allocated class borrow/lock params: pass by pointer
            elif (
                self._node_ztype(ppath)
                and cast(ZType, self._node_ztype(ppath)).typetype == ZTypeType.CLASS
                and not cast(ZType, self._node_ztype(ppath)).is_heap_allocated
                and not ptype_str.endswith("*")
                and ftype
                and self.typing.child_ownership(ftype, pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ptype_str = f"{ptype_str}*"
                pointer_params.append(self._def_vid(ppath))
            params.append(
                f"{ptype_str} {self._def_cname(ppath) or mangle_var_name(pname)}"
            )

        param_str = ", ".join(params) if params else "void"

        self.forward_decls.append(f"{ret_ctype} {cname}({param_str});\n")

        # push new scope for this function
        func_nid = func.nodeid if hasattr(func, "nodeid") else 0
        self._scope_stack.append(
            ScopeState(record_name=record_name, func_nodeid=func_nid)
        )
        # binding aliases are scoped to the current function body
        prev_alias_map = self._alias_map
        self._alias_map = {}
        # track enclosing type for meta.create resolution in the body
        prev_enclosing = self._current_enclosing_type_name
        prev_enclosing_type = self._current_enclosing_type
        if record_name:
            self._current_enclosing_type_name = record_name
            self._current_enclosing_type = record_type
        # track all pointer parameters for -> field access dispatch
        for pp in pointer_params:
            if pp is not None:
                self._scope.class_params.add(pp)
        # also track other parameters that are already pointer types (unions, etc.)
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(self.typing, self._node_ztype(ppath))
            if ptype_str.endswith("*") and ptype_str.startswith("z_"):
                vid = self._def_vid(ppath)
                if vid is not None:
                    self._scope.class_params.add(vid)

        # register .take params with a destructor for scope-exit cleanup.
        # Ownership was transferred in from the caller, so the callee owns the
        # heap data and must free it at function exit (or early return).
        for pname, ppath in func.parameters.items():
            if (
                self._node_ztype(ppath)
                and cast(ZType, self._node_ztype(ppath)).destructor_name is not None
                and cast(ZType, self._node_ztype(ppath)).destructor_name
                and ftype
                and self.typing.child_ownership(ftype, pname) == ZParamOwnership.TAKE
            ):
                vid = self._def_vid(ppath)
                if vid is not None:
                    self._scope.cleanup_vars.append((vid, self._node_ztype(ppath)))

        # Register borrow/lock union params in borrowed_vars so any
        # synth-hoisted projection assignment from them (e.g. the
        # typechecker's `_t: n.field` for a call-arg expression) can
        # propagate the borrow to its destination — skipping destructor
        # registration for the projection temp. Classes use pointer-
        # pass + class_params tracking for the same purpose; unions stay
        # value-passed (the union struct is small + value-stable; only
        # its `data` pointer is shared with the source), so the
        # borrowed_vars route is the union-specific channel.
        for pname, ppath in func.parameters.items():
            ptype = self._node_ztype(ppath)
            if (
                ptype is not None
                and ptype.typetype == ZTypeType.UNION
                and ftype
                and self.typing.child_ownership(ftype, pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                vid = self._def_vid(ppath)
                if vid is not None:
                    self._scope.borrowed_vars.add(vid)

        lines: List[str] = []
        lines.append(f"{ret_ctype} {cname}({param_str}) {{\n")
        self.indent_level = 1
        if is_generator_call:
            # Prologue: switch dispatch + L_entry label. The body emits
            # its yields as suspension fragments (set state, return
            # OPT_SOME, label-for-resume) via `_emit_yield_fragment`.
            lines.append(self._emit_generator_prologue())
            if func.body:
                lines.append(self._emit_statement(func.body))
            # Epilogue: terminal-state landing, single OPT_NONE return.
            lines.append(self._emit_generator_epilogue())
        elif func.body:
            implicit = self._is_implicit_return(func)
            if implicit:
                # emit all statements except the last
                for sline in func.body.statements[:-1]:
                    lines.append(self._emit_statement_line(sline))
                # emit last expression as implicit return
                lines.append(self._emit_implicit_return(func.body.statements[-1]))
            else:
                body_code = self._emit_statement(func.body)
                lines.append(body_code)

        # scope-exit cleanup for string/class/union/protocol vars (void functions / fall-through)
        cleanup = self._emit_scope_cleanup(self._indent())
        if cleanup:
            lines.append(cleanup)

        lines.append("}\n\n")
        self.func_defs.append("".join(lines))

        # pop function scope
        self._scope_stack.pop()
        self._current_enclosing_type_name = prev_enclosing
        self._current_enclosing_type = prev_enclosing_type
        self._alias_map = prev_alias_map
        if is_generator_call:
            self._generator_ctx = None

    def _is_implicit_return(self, func: zast.Function) -> bool:
        """Check if the function's last statement is an implicit return candidate."""
        if not func.returntype or not func.body or not func.body.statements:
            return False
        last = func.body.statements[-1].statementline
        if last.nodetype != zast.NodeType.EXPRESSION:
            return False
        last_expr = cast(zast.Expression, last)
        # check call_kind on Expression wrapper for control flow
        if self._expr_call_kind(last_expr) in (
            zast.CallKind.RETURN,
            zast.CallKind.BREAK,
            zast.CallKind.CONTINUE,
            zast.CallKind.ERROR,
            zast.CallKind.PANIC,
        ):
            return False
        # never type means all paths already return explicitly
        if (
            self._node_ztype(last_expr)
            and cast(ZType, self._node_ztype(last_expr)).typetype == ZTypeType.NEVER
        ):
            return False
        return True

    def _emit_implicit_return(self, sline: zast.StatementLine) -> str:
        """Emit the last statement line as an implicit return with scope cleanup."""
        self._temp_stack.append(TempState())
        expr = sline.statementline
        assert expr.nodetype == NodeType.EXPRESSION
        val = self._emit_expression_value(cast(zast.Expression, expr))
        indent = self._indent()

        result = "".join(self._temp.decls)

        # remove return value from temp frees (caller owns it)
        if val in self._temp.frees:
            self._temp.frees.remove(val)

        # free remaining temps before return
        for t in self._temp.frees:
            if t in self._temp.string_set:
                result += f"{indent}z_String_free(&{t});\n"
            elif t in self._temp.proto_set:
                proto_base = self._temp.proto_set[t]
                result += f"{indent}{proto_base}_destroy(&{t});\n"
            elif t in self._temp.class_set:
                tname = self._temp.class_set[t]
                result += f"{indent}{self._emit_class_free(t, tname)}\n"
            else:
                result += f"{indent}free({t});\n"
        self._temp.frees.clear()

        # scope cleanup (excluding return value)
        result += self._emit_scope_cleanup(indent, exclude_var=val)
        result += f"{indent}return {_unwrap_outer_parens(val)};\n"

        self._temp_stack.pop()
        return result

    def _emit_statement(self, stmt: zast.Statement) -> str:
        parts: List[str] = []
        for sline in stmt.statements:
            parts.append(self._emit_statement_line(sline))
        return "".join(parts)

    # ---- Generator state-machine codegen (G4) -------------------------

    def _setup_generator_ctx(self, func: zast.Function, ftype_name: str = "") -> None:
        """Build the per-call generator state-machine context: walks
        `func.body` to assign incrementing state numbers to each
        Yield node, and records the return-wrapper's mono name + tag
        identifiers so the emitter can build `OPT_SOME` / `OPT_NONE`
        return fragments inline.

        State numbering:
          - 0: entry (first call, dispatches to L_entry)
          - 1..N: post-yield resume points (L_resume_K)
          - -1: terminal (any further call returns OPT_NONE)
        """
        yield_states: Dict[int, int] = {}

        def walk(n: zast.Node) -> None:
            if n.nodetype == NodeType.YIELD:
                # Allocate next state number (length+1 since 0 is entry).
                yield_states[n.nodeid] = len(yield_states) + 1
                # descend into the yield's expr too (it might
                # theoretically contain another yield — rejected at
                # parse, but defensive)
            if n.nodetype == NodeType.FUNCTION:
                return  # nested function literal — not our yields
            for child in zast.node_children(n):
                walk(child)

        if func.body is not None:
            walk(func.body)

        # Look at the resolved return type to derive the OPT wrapper
        # tag names. The synthesized `.call` returns one of
        # `optionval_T`, `Option_T`, or `OptionView_T` — we read the
        # ZType name and uppercase it.
        ret_zt = self._node_ztype(func.returntype) if func.returntype else None
        ret_ctype = self._return_ctype(func)
        wrapper_name = ret_zt.name if ret_zt is not None else ""
        upper = wrapper_name.upper()
        # Wrapper shape determines how `_emit_yield_fragment` lowers
        # the per-yield store:
        #   "variant"     -> optionval(T): inline `_ry.data.some = val`
        #   "union_owned" -> Option(T): heap-allocate payload, store
        #                    pointer in `_ry.data`; consumer owns &
        #                    destroys the wrapper (which frees both
        #                    the heap allocation and the inner value)
        #   "union_view"  -> OptionView(T): take address of yielded
        #                    expression, store pointer in `_ry.data`;
        #                    consumer holds a lock on the iterator's
        #                    source so the pointer stays valid
        wrapper_kind = "variant"
        wrapper_elem_ctype = ""
        if ret_zt is not None:
            if ret_zt.typetype == ZTypeType.UNION:
                if self.typing.is_child_lock_arm(ret_zt, "some"):
                    wrapper_kind = "union_view"
                else:
                    wrapper_kind = "union_owned"
                some_child = self.typing.child_of(ret_zt, "some")
                if some_child is not None:
                    wrapper_elem_ctype = _ctype(self.typing, some_child)
        # Bidirectional discriminator: if the function has a `value`
        # parameter, this is a `takes != null` generator. The
        # prologue stores `value` into `this->_resume_input`; the
        # expression-form yield path (Yield as Reassignment/Assignment
        # RHS) consults this flag and emits the post-resume read.
        is_bidirectional = "value" in func.parameters
        # If `value`'s type has a destructor (i.e. reftype `takes`),
        # the prologue must destroy the previous `_resume_input`
        # before overwriting -- otherwise sending a second value
        # leaks the first one. zero-initialised structs are safe to
        # destroy because the standard destructors are NULL-guarded
        # (`z_String_free` checks `s->data`; class destructors are
        # similarly defensive), so we can unconditionally call it
        # on every entry, including state 0.
        resume_destructor: Optional[str] = None
        # value_is_borrow: bidirectional generator whose `value:`
        # parameter is `.lock` (or rewritten-to-`.lock` bare/borrow).
        # The `_resume_input` field stores a pointer, the prologue
        # assigns the caller's pointer (no destructor), and the body's
        # expression-form yield (`name: yield v`) binds via the
        # pointer-alias pattern so accesses go through the source
        # storage and the local doesn't outlive its yield window.
        value_is_borrow = False
        value_elem_ctype = ""
        if is_bidirectional:
            value_path = func.parameters.get("value")
            value_zt = self._node_ztype(value_path) if value_path is not None else None
            value_own: Optional[ZParamOwnership] = None
            if ftype_name:
                fn_ztype = self._node_ztype(func)
                if fn_ztype is not None:
                    value_own = self.typing.child_ownership(fn_ztype, "value")
            if value_zt is not None and value_own == ZParamOwnership.LOCK:
                value_is_borrow = True
                value_elem_ctype = _ctype(self.typing, value_zt)
            elif value_zt is not None and value_zt.destructor_name:
                resume_destructor = value_zt.destructor_name
        self._generator_ctx = {
            "yield_states": yield_states,
            "wrapper_ctype": ret_ctype,
            "some_tag": f"Z_{upper}_TAG_SOME",
            "none_tag": f"Z_{upper}_TAG_NONE",
            "n_yields": len(yield_states),
            "is_bidirectional": is_bidirectional,
            "resume_destructor": resume_destructor,
            "value_is_borrow": value_is_borrow,
            "value_elem_ctype": value_elem_ctype,
            "wrapper_kind": wrapper_kind,
            "wrapper_elem_ctype": wrapper_elem_ctype,
        }

    def _emit_generator_prologue(self) -> str:
        """Switch-table dispatch + entry label. Emitted right after
        the function's opening brace.

        For bidirectional generators, prepends `this->_resume_input
        = value;` so each `.call` invocation refreshes the resume
        slot before the body dispatches. On the very first call the
        resume slot was zero-initialised by `meta.create` and the
        parser/desugarer reject reading it via expression-form
        yield, so over-writing it with the caller's default value
        is harmless."""
        assert self._generator_ctx is not None
        ctx = self._generator_ctx
        n = cast(int, ctx["n_yields"])
        is_bidirectional = cast(bool, ctx["is_bidirectional"])
        resume_destructor = cast(Optional[str], ctx.get("resume_destructor"))
        indent = self._indent()
        lines: List[str] = []
        if is_bidirectional:
            if resume_destructor is not None:
                # destroy the previous value before overwriting
                lines.append(f"{indent}{resume_destructor}(&this->_resume_input);\n")
            lines.append(f"{indent}this->_resume_input = value;\n")
        lines.append(f"{indent}switch (this->state) {{\n")
        # entry: first invocation, dispatched only when state==0.
        lines.append(f"{indent}    case 0: goto L_entry;\n")
        for k in range(1, n + 1):
            lines.append(f"{indent}    case {k}: goto L_resume_{k};\n")
        # Terminal-state dispatch: state==-1 falls through to L_done
        # via the default arm.
        lines.append(f"{indent}    default: goto L_done;\n")
        lines.append(f"{indent}}}\n")
        lines.append("L_entry:;\n")
        return "".join(lines)

    def _emit_generator_epilogue(self) -> str:
        """Terminal-state label + single OPT_NONE return. Reached
        when the body falls through (last yield's resume returned to
        end-of-body) AND when `state==-1` is dispatched."""
        assert self._generator_ctx is not None
        ctx = self._generator_ctx
        wrapper_ctype = cast(str, ctx["wrapper_ctype"])
        none_tag = cast(str, ctx["none_tag"])
        indent = self._indent()
        lines: List[str] = []
        lines.append(f"{indent}/* fallthrough = end of generator body */\n")
        lines.append("L_done:;\n")
        lines.append(f"{indent}this->state = -1;\n")
        lines.append(f"{indent}{wrapper_ctype} _r_done = {{0}};\n")
        lines.append(f"{indent}_r_done.tag = {none_tag};\n")
        lines.append(f"{indent}return _r_done;\n")
        return "".join(lines)

    def _emit_yield_fragment(self, yield_node: zast.Yield) -> str:
        """Emit the suspension fragment for one `yield <expr>`. The
        per-yield store dispatches on `wrapper_kind`:

        - variant (optionval(T)):
              this->state = N;
              <wrapper>_t _ry = {0};
              _ry.tag = TAG_SOME;
              _ry.data.some = <expr>;
              return _ry;
        - union_owned (Option(T)):
              this->state = N;
              <wrapper>_t _ry = {0};
              _ry.tag = TAG_SOME;
              <elem>_t* _payload = z_xmalloc(sizeof(<elem>_t));
              *_payload = <expr>;
              _ry.data = _payload;
              return _ry;
        - union_view (OptionView(T)):
              this->state = N;
              <wrapper>_t _ry = {0};
              _ry.tag = TAG_SOME;
              _ry.data = &(<expr>);
              return _ry;

        The state number `N` was pre-assigned by `_setup_generator_ctx`
        (yield_states[yield_node.nodeid])."""
        assert self._generator_ctx is not None
        ctx = self._generator_ctx
        yield_states = cast(Dict[int, int], ctx["yield_states"])
        wrapper_ctype = cast(str, ctx["wrapper_ctype"])
        some_tag = cast(str, ctx["some_tag"])
        wrapper_kind = cast(str, ctx.get("wrapper_kind", "variant"))
        wrapper_elem_ctype = cast(str, ctx.get("wrapper_elem_ctype", ""))
        state_num = yield_states.get(yield_node.nodeid, 0)
        indent = self._indent()
        # Evaluate the yielded expression. The yield's `.expr` is an
        # Expression wrapper; emit its value through the regular path.
        val = self._emit_expression_value(yield_node.expr)
        # Drain temp decls accumulated by the expression emission.
        # `_emit_statement_line` already wraps callers in a TempState
        # push/pop; we're called from within that, so just splice the
        # decls in before the suspension code.
        decls = "".join(self._temp.decls)
        self._temp.decls.clear()
        lines: List[str] = []
        lines.append(decls)
        lines.append(f"{indent}this->state = {state_num};\n")
        # Free name `_ry_<state>` so multiple yields in one function
        # don't collide if they end up in the same scope.
        ry = f"_ry_{state_num}"
        lines.append(f"{indent}{wrapper_ctype} {ry} = {{0}};\n")
        lines.append(f"{indent}{ry}.tag = {some_tag};\n")
        if wrapper_kind == "union_owned":  # ztc-string-compare-ok: wrapper-kind tag
            payload = f"_payload_{state_num}"
            self.needs_stdlib = True
            lines.append(
                f"{indent}{wrapper_elem_ctype}* {payload} = "
                f"z_xmalloc(sizeof({wrapper_elem_ctype}));\n"
            )
            lines.append(f"{indent}*{payload} = {val};\n")
            lines.append(f"{indent}{ry}.data = {payload};\n")
        elif wrapper_kind == "union_view":  # ztc-string-compare-ok: wrapper-kind tag
            lines.append(f"{indent}{ry}.data = &({val});\n")
        else:
            lines.append(f"{indent}{ry}.data.some = {val};\n")
        lines.append(f"{indent}return {ry};\n")
        # Resume label sits at function scope (column 0) so C `goto`
        # from the switch can reach it across nested blocks. The
        # trailing `;` makes the label a null statement, valid before
        # any closing brace.
        lines.append(f"L_resume_{state_num}:;\n")
        return "".join(lines)

    def _emit_statement_line(self, sline: zast.StatementLine) -> str:
        # save temp state for this statement
        self._temp_stack.append(TempState())

        inner = sline.statementline
        if inner.nodetype == NodeType.ASSIGNMENT:
            code = self._emit_assignment(cast(zast.Assignment, inner))
        elif inner.nodetype == NodeType.REASSIGNMENT:
            code = self._emit_reassignment(cast(zast.Reassignment, inner))
        elif inner.nodetype == NodeType.SWAP:
            code = self._emit_swap(cast(zast.Swap, inner))
        elif inner.nodetype == NodeType.EXPRESSION:
            code = self._emit_expression_stmt(cast(zast.Expression, inner))
        else:
            code = ""

        # build result: temp decls + code + post-code (implicit-take
        # invalidations etc.) + temp frees
        result = "".join(self._temp.decls) + code + "".join(self._temp.post_code)
        indent = self._indent()
        for t in self._temp.frees:
            if t in self._temp.string_set:
                result += f"{indent}z_String_free(&{t});\n"
            elif t in self._temp.proto_set:
                proto_base = self._temp.proto_set[t]
                result += f"{indent}{proto_base}_destroy(&{t});\n"
            elif t in self._temp.class_set:
                tname = self._temp.class_set[t]
                result += f"{indent}{self._emit_class_free(t, tname)}\n"
            else:
                result += f"{indent}free({t});\n"

        self._temp_stack.pop()
        return result

    def _emit_assignment(self, assign: zast.Assignment) -> str:
        indent = self._indent()
        _alias_of = self.typing.assign_alias_of.get(assign.nodeid)
        _assign_ztype = self._node_ztype(assign)
        # Dead hoisted-arg temp: an ANF synth temp (synth_origin "anf")
        # whose value is a compile-time constant is never read — the use
        # site inlines the stamped const_value (propagated in _hoist_arg),
        # so the `int64_t _tN = 92;` decl would be unused. Emit nothing.
        if assign.synth_origin == "anf" and (
            self.typing.node_const_value.get(assign.value.expression.nodeid) is not None
        ):
            return ""
        # Bidirectional generator with `.lock` (or rewritten-to-`.lock`)
        # `accepts:`: `name: yield v` binds `name` as a pointer alias
        # into the synth class's `_resume_input` slot. The body's
        # accesses through `name` are rewritten to `(*__borrow_name)`
        # via `_alias_map`; the storage is the caller's `value:` arg,
        # locked for the duration of the `.call`. The typechecker
        # liveness check prevents `name` from being used past the
        # next yield.
        if (
            self._generator_ctx is not None
            and cast(bool, self._generator_ctx.get("value_is_borrow"))
            and assign.value.expression.nodetype == NodeType.YIELD
            and _assign_ztype is not None
        ):
            yield_fragment = self._emit_yield_fragment(
                cast(zast.Yield, assign.value.expression)
            )
            self._temp.decls.append(yield_fragment)
            decls = "".join(self._temp.decls)
            self._temp.decls.clear()
            cname = self._def_cname(assign) or mangle_var_name(assign.name)
            ptr_name = f"__borrow_{cname}"
            elem_ctype = _ctype(self.typing, _assign_ztype)
            self._alias_map[assign.name] = f"(*{ptr_name})"
            return (
                f"{decls}{indent}{elem_ctype}* {ptr_name} = "
                f"this->_resume_input;\n"
                f"{indent}/* alias: {cname} => (*{ptr_name}) */\n"
            )
        # Phase B alias optimization: inline `x: y.take` or `x: y.borrow`
        # (or similar on a valtype dotted path) becomes a C-level alias —
        # no local declaration, no destructor, substitute at reference
        # sites. The alias lives until the enclosing function ends.
        if _alias_of is not None:
            # If the source expression rooted at a narrowed AtomId
            # (e.g., `stolen: s.take` where s is narrowed), the alias
            # must embed the payload-unwrap — plain name substitution
            # would reference the outer union's C storage and emit
            # invalid field access later. Fall through to the
            # AST-aware path value when the root is stamped; otherwise
            # keep the lightweight string-based alias.
            alias_expr = self._narrowed_alias_expr(assign.value)
            if alias_expr is None:
                alias_expr = self._alias_c_expr(_alias_of)
            cname = self._def_cname(assign) or mangle_var_name(assign.name)
            self._alias_map[assign.name] = alias_expr
            return f"{indent}/* alias: {cname} => {alias_expr} */\n"
        ctype = "int64_t"
        if _assign_ztype:
            # typedef method calls: type is FUNCTION but variable holds the
            # return value, not a function pointer
            _is_typedef_call = False
            if (
                _assign_ztype.typetype == ZTypeType.FUNCTION
                and _assign_ztype.return_type
                and assign.value.expression.nodetype == NodeType.DOTTEDPATH
            ):
                _pt = self._node_ztype(
                    cast(zast.DottedPath, assign.value.expression).parent
                )
                _is_typedef_call = _pt is not None and _pt.typedef_base is not None
            if _is_typedef_call:
                ctype = _ctype(self.typing, _assign_ztype.return_type)
            else:
                ctype = _ctype(self.typing, _assign_ztype)
        cname = self._def_cname(assign) or mangle_var_name(assign.name)
        self._in_named_assignment = True
        val = self._emit_expression_value(assign.value)
        self._in_named_assignment = False
        if _assign_ztype and (_assign_ztype.destructor_name is not None):
            if ctype == "z_String_t":
                self.needs_string = True
            self.needs_stdlib = True
            # the variable now owns the value — remove from temp frees
            if val in self._temp.frees:
                self._temp.frees.remove(val)
            # If the RHS is a call to a function declared `out T.borrow`, the
            # caller does NOT own the return value. Skip cleanup registration
            # so scope exit doesn't double-free the borrowed heap buffer.
            # Same rule applies when the RHS is a field projection from a
            # borrowed local (typically a borrow-by-default union param):
            # the projection aliases the source's heap-owned `data`
            # pointer, so destroying the LHS would free memory still
            # owned by the borrowed source.
            assign_vid = self._def_vid(assign)
            if self._is_borrow_return_call(
                assign.value
            ) or self._rhs_is_borrowed_projection(assign.value):
                if assign_vid is not None:
                    self._scope.borrowed_vars.add(assign_vid)
            elif assign_vid is not None:
                self._scope.cleanup_vars.append((assign_vid, _assign_ztype))
        # check if value is a bare record name (zero-initialization)
        inner = assign.value.expression
        inner_resolved = (
            self._unit_def_ztype(inner) if inner.nodetype == NodeType.ATOMID else None
        )
        if (
            inner.nodetype == NodeType.ATOMID
            and inner_resolved
            and inner_resolved.typetype == ZTypeType.RECORD
            and inner_resolved.name == cast(zast.AtomId, inner).name
        ):
            ctype = f"z_{cast(zast.AtomId, inner).name}_t"
        result = f"{indent}{ctype} {cname} = {val};\n"
        # invalidate source on .take for reftypes
        take_var = self._get_take_var_from_expr(assign.value)
        if take_var:
            result += self._emit_take_invalidation(
                take_var, self._node_ztype(assign.value), indent
            )
        return result

    def _emit_reassignment(self, reassign: zast.Reassignment) -> str:
        indent = self._indent()
        prev_lhs = self._lhs_mode
        self._lhs_mode = True
        lhs = self._emit_path_value(reassign.topath)
        self._lhs_mode = prev_lhs
        rhs = self._emit_expression_value(reassign.value)
        result = ""
        # check if this is a reftype reassignment — free old value first
        lhs_type = self._path_ztype(reassign.topath)
        if lhs_type and (lhs_type.destructor_name is not None):
            result += self._emit_field_cleanup(lhs, lhs_type, indent)
            # the variable now owns the new value — remove from temp frees
            if rhs in self._temp.frees:
                self._temp.frees.remove(rhs)
        result += f"{indent}{lhs} = {rhs};\n"
        # Drop-and-transfer: when the RHS is a named variable (or an
        # explicit `.take`), zero it out so its scope-exit destructor
        # does not free the storage we just moved into the LHS. No-op
        # when the RHS is a fresh constructor (no source name) or a
        # valtype (copy semantics, nothing to invalidate).
        if lhs_type and (lhs_type.destructor_name is not None):
            take_var = self._get_take_var_from_expr(reassign.value)
            if take_var is None:
                inner = reassign.value.expression
                if inner.nodetype in (NodeType.ATOMID, NodeType.DOTTEDPATH):
                    take_var = self._get_implicit_take_var(cast(zast.Operation, inner))
            if take_var is not None and take_var != lhs:
                result += self._emit_take_invalidation(take_var, lhs_type, indent)
        return result

    def _emit_swap(self, swap: zast.Swap) -> str:
        indent = self._indent()
        lhs = self._emit_path_value(swap.lhs)
        rhs = self._emit_path_value(swap.rhs)
        ctype = "int64_t"
        lhs_ztype = self._path_ztype(swap.lhs)
        if lhs_ztype:
            ctype = _ctype(self.typing, lhs_ztype)
        return (
            f"{indent}{{\n"
            f"{indent}    {ctype} _tmp = {lhs};\n"
            f"{indent}    {lhs} = {rhs};\n"
            f"{indent}    {rhs} = _tmp;\n"
            f"{indent}}}\n"
        )

    def _expr_call_kind(self, expr: zast.Expression) -> "zast.CallKind":
        """Look up the Expression-wrapper's control-flow classification
        from `ZTyping.expr_call_kind`."""
        return self.typing.expr_call_kind.get(expr.nodeid, zast.CallKind.UNKNOWN)

    def _emit_expression_stmt(self, expr: zast.Expression) -> str:
        indent = self._indent()
        inner = expr.expression
        # Generator yield as a statement: emit the suspension fragment
        # (set state, return OPT_SOME, label-for-resume). Only fires
        # inside a generator-call body, which is the only place
        # yields legally appear.
        if inner.nodetype == NodeType.YIELD and self._generator_ctx is not None:
            return self._emit_yield_fragment(cast(zast.Yield, inner))
        # Bare `return` inside a generator-call body terminates the
        # generator: jump to the terminal label, which sets
        # `this->state = -1` and returns OPT_NONE. The parser models
        # bare `return` as a plain AtomId reference to the `return`
        # function — without args, it's not a value-return call but a
        # terminator marker for us.
        if (
            self._generator_ctx is not None
            and inner.nodetype == NodeType.ATOMID
            and cast(zast.AtomId, inner).name
            == "return"  # ztc-string-compare-ok: return keyword
        ):
            return f"{indent}goto L_done;\n"
        # handle break/continue/return as standalone statements.
        # Bare `return` (no value) is parsed as a plain AtomId reference
        # to the `return` function (see comment above on the generator
        # path); the typechecker tags the expression with
        # CallKind.RETURN. Mirror the void-return path inside
        # `_emit_return`: function-scope cleanup, then bare C `return;`.
        ck = self._expr_call_kind(expr)
        if ck == zast.CallKind.BREAK:
            return f"{indent}break;\n"
        if ck == zast.CallKind.CONTINUE:
            return f"{indent}continue;\n"
        if ck == zast.CallKind.RETURN and inner.nodetype == zast.NodeType.ATOMID:
            # Bare `return` only — `return X` is a Call and is routed to
            # `_emit_call_stmt` → `_emit_return` further below, which
            # handles the return value.
            return self._emit_scope_cleanup(indent) + f"{indent}return;\n"
        # panic(msg): route through the shared z_panic helper in the runtime
        # preamble. msg is declared as `string`, but after type coercion
        # we may have a z_String_t or a z_StringView_t; both expose `.data`
        # as a `const char*`-compatible pointer. Materialising msg may pull
        # in the string runtime, so set the corresponding needs_* flags.
        if ck == zast.CallKind.PANIC:
            self.needs_stdlib = True
            self.needs_string = True
            self.needs_stringview = True
            if inner.nodetype == zast.NodeType.CALL:
                call = cast(zast.Call, inner)
                if call.arguments:
                    arg_op = call.arguments[0].valtype
                    arg_type = self._get_operation_type(arg_op)
                    arg = self._emit_operation_value(arg_op)
                    if arg_type and arg_type.subtype in (
                        ZSubType.STRING,
                        ZSubType.STRINGVIEW,
                    ):
                        return f"{indent}z_panic((const char*){arg}.data);\n"
            return f'{indent}z_panic("(panic)");\n'
        if inner.nodetype == zast.NodeType.CALL:
            return self._emit_call_stmt(cast(zast.Call, inner), indent)
        if inner.nodetype == zast.NodeType.IF:
            return self._emit_if(cast(zast.If, inner))
        if inner.nodetype == zast.NodeType.FOR:
            return self._emit_for(cast(zast.For, inner))
        if inner.nodetype == zast.NodeType.DO:
            return self._emit_do(cast(zast.Do, inner))
        if inner.nodetype == zast.NodeType.WITH:
            return self._emit_with(cast(zast.With, inner))
        if inner.nodetype == zast.NodeType.CASE:
            return self._emit_case(cast(zast.Case, inner))
        if inner.nodetype == zast.NodeType.REASSIGNMENT:
            return self._emit_reassignment(cast(zast.Reassignment, inner))
        if inner.nodetype == zast.NodeType.SWAP:
            return self._emit_swap(cast(zast.Swap, inner))
        # string.shrink as zero-arg method statement (parsed as dotted path, not call)
        if inner.nodetype == zast.NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, inner)
            dp_parent_type = self._node_ztype(dp.parent)
            if (
                dp_parent_type
                and dp_parent_type.subtype == ZSubType.STRING
                and dp.child.name == "shrink"
            ):
                parent_val = self._emit_path_value(dp.parent)
                recv = (
                    parent_val
                    if self._is_class_pointer_path(dp.parent)
                    else f"&{parent_val}"
                )
                return f"{indent}z_String_shrink({recv});\n"
        if (
            inner.nodetype == zast.NodeType.DOTTEDPATH
            and cast(zast.DottedPath, inner).child.name == "take"
        ):
            dp = cast(zast.DottedPath, inner)
            # function definitions are immutable — .take as statement is a no-op
            if dp.parent.nodetype == zast.NodeType.ATOMID and self._is_definition_ref(
                cast(zast.AtomId, dp.parent)
            ):
                return ""
            var = self._emit_path_value(dp.parent)
            var_type = self._node_ztype(dp)
            result = ""
            if (
                var_type
                and (var_type.destructor_name is not None)
                and var_type.destructor_name
            ):
                if var_type.is_heap_allocated:
                    result += f"{indent}{var_type.destructor_name}({var});\n"
                else:
                    result += f"{indent}{var_type.destructor_name}(&{var});\n"
            result += self._emit_take_invalidation(var, var_type, indent)
            return result
        if (
            inner.nodetype == zast.NodeType.DOTTEDPATH
            and cast(zast.DottedPath, inner).child.name == "release"
        ):
            dp = cast(zast.DottedPath, inner)
            var = self._emit_path_value(dp.parent)
            var_type = self._node_ztype(dp)
            result = ""
            # Borrowed locals (assigned from an `out T.borrow` call) hold
            # a borrow into someone else's heap data; freeing or zeroing
            # here would corrupt the source. `.release` for a borrow is a
            # type-checker-side lock release with no C-level effect.
            release_vid = self._operand_vid(dp.parent)
            is_borrowed = (
                release_vid is not None and release_vid in self._scope.borrowed_vars
            )
            # owned reftypes: call destructor
            if (
                var_type
                and (var_type.destructor_name is not None)
                and var_type.destructor_name
                and not is_borrowed
            ):
                if var_type.is_heap_allocated:
                    result += f"{indent}{var_type.destructor_name}({var});\n"
                else:
                    result += f"{indent}{var_type.destructor_name}(&{var});\n"
            # invalidate so scope-exit destroy is a no-op
            if (
                var_type
                and not is_borrowed
                and (
                    var_type.subtype == ZSubType.STRING
                    or var_type.typetype in (ZTypeType.CLASS, ZTypeType.UNION)
                )
            ):
                result += self._emit_take_invalidation(var, var_type, indent)
            # borrowed variables and valtypes: no C code needed
            return result
        if inner.nodetype in (
            zast.NodeType.BINOP,
            zast.NodeType.DOTTEDPATH,
            zast.NodeType.ATOMID,
            zast.NodeType.ATOMSTRING,
            zast.NodeType.EXPRESSION,
            zast.NodeType.LABELVALUE,
        ):
            val = self._emit_operation_value(cast(zast.Operation, inner))
            return f"{indent}{val};\n"
        return ""

    def _is_data_index_call(self, call: zast.Call) -> bool:
        """Check if this is a data.index call like primes.index i."""
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            parent = cast(zast.DottedPath, call.callable).parent
            if parent.nodetype == NodeType.ATOMID:
                child = cast(zast.DottedPath, call.callable).child.name
                pt = self._unit_def_ztype(parent)
                if (
                    pt is not None
                    and pt.typetype == ZTypeType.DATA
                    and child == "index"
                ):
                    return True
        return False

    def _is_protocol_create(self, call: zast.Call) -> bool:
        """Check if call is protocol.create/take from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name not in ("create", "take"):
            return False
        parent_type = self._node_ztype(dp.parent)
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _is_protocol_borrow(self, call: zast.Call) -> bool:
        """Check if call is protocol.borrow from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name != "borrow":
            return False
        parent_type = self._node_ztype(dp.parent)
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _emit_protocol_create_call(self, call: zast.Call) -> str:
        """Emit owned protocol create: protocol.create from: expr."""
        assert call.callable.nodetype == NodeType.DOTTEDPATH
        proto_type = self._node_ztype(cast(zast.DottedPath, call.callable).parent)
        assert proto_type is not None
        proto_name = proto_type.name

        # find the from: argument
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        assert from_arg is not None

        # emit the from: value
        arg_val = self._emit_operation_value(from_arg.valtype)

        # get impl type name from the argument's resolved type
        arg_type = self._node_ztype(from_arg.valtype)
        if not arg_type:
            # try parent for dotted paths like f.take
            if from_arg.valtype.nodetype == NodeType.DOTTEDPATH:
                arg_type = self._node_ztype(
                    cast(zast.DottedPath, from_arg.valtype).parent
                )
        impl_name = arg_type.name if arg_type else ""

        # owned-create C name + handle type from the conformance entity
        # (dot-free, label-correct cross-unit); legacy compose as fallback.
        conf = self._conformance_pair(arg_type, proto_type)
        if conf is not None:
            owned_create = conf.create_owned_cname
        else:
            label = self._proto_conformance.get((impl_name, proto_name), "")
            owned_create = f"{_cbase_of(arg_type, impl_name)}_{label}_create_owned"
        proto_ctype = _cname_of(proto_type, proto_name)

        # stack-allocated class: pass address to protocol create
        if (
            arg_type
            and arg_type.typetype == ZTypeType.CLASS
            and not arg_type.is_heap_allocated
            and not arg_val.startswith("&")
        ):
            arg_val = f"&{arg_val}"

        # allocate temp and track as protocol var
        tmp = self._temp_name("c")
        indent = self._indent()
        self._temp.decls.append(
            f"{indent}{proto_ctype} {tmp} = {owned_create}({arg_val});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.proto_set[tmp] = _cbase_of(proto_type, proto_name)

        # Ownership of the impl transfers into the protocol. Invalidate
        # the source so scope-exit cleanup doesn't double-destroy the
        # impl's contents (the protocol's destructor already calls the
        # impl's destructor on the wrapped pointer).
        #
        # Two layered cases:
        #   1) Explicit `.take` on a bare AtomId (`sp.take`) —
        #      `_get_take_var` returns the var name; zero-init it.
        #   2) Implicit take via an aliased synth temp (`_tN`) — the
        #      typechecker hoisted `from: sp.take` into a synth
        #      assignment, leaving `from_arg.valtype` as the AtomId
        #      `_tN`. `_transfer_implicit_take` resolves the alias via
        #      `_get_implicit_take_var` and zeros the underlying source.
        if arg_type and arg_type.typetype == ZTypeType.CLASS:
            take_var = self._get_take_var(from_arg.valtype)
            if take_var:
                ct = _ctype(self.typing, arg_type)
                self._temp.decls.append(f"{indent}{take_var} = ({ct}){{0}};\n")
            else:
                self._transfer_implicit_take(arg_val, from_arg.valtype, indent)

        return tmp

    def _emit_protocol_borrow_call(self, call: zast.Call) -> str:
        """Emit borrowed protocol create: protocol.borrow from: expr."""
        assert call.callable.nodetype == NodeType.DOTTEDPATH
        proto_type = self._node_ztype(cast(zast.DottedPath, call.callable).parent)
        assert proto_type is not None
        proto_name = proto_type.name

        # find the from: argument
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        assert from_arg is not None

        # emit the from: value
        arg_val = self._emit_operation_value(from_arg.valtype)

        # get impl type name from the argument's resolved type
        arg_type = self._node_ztype(from_arg.valtype)
        if not arg_type:
            if from_arg.valtype.nodetype == NodeType.DOTTEDPATH:
                arg_type = self._node_ztype(
                    cast(zast.DottedPath, from_arg.valtype).parent
                )
        impl_name = arg_type.name if arg_type else ""

        # borrowed-create C name from the conformance entity; legacy fallback.
        conf = self._conformance_pair(arg_type, proto_type)
        if conf is not None:
            create_name = conf.create_cname
        else:
            label = self._proto_conformance.get((impl_name, proto_name), "")
            create_name = f"{_cbase_of(arg_type, impl_name)}_{label}_create"

        # pass address for stack-allocated types (records, stack classes)
        if arg_type and (
            arg_type.is_valtype
            or (arg_type.typetype == ZTypeType.CLASS and not arg_type.is_heap_allocated)
        ):
            arg_expr = f"&{arg_val}"
        else:
            arg_expr = arg_val

        self.needs_stdlib = True

        # allocate temp and track as protocol var (borrowed: no destroy)
        tmp = self._temp_name("p")
        indent = self._indent()
        proto_ctype = _cname_of(proto_type, proto_name)
        self._temp.decls.append(
            f"{indent}{proto_ctype} {tmp} = {create_name}({arg_expr});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.proto_set[tmp] = _cbase_of(proto_type, proto_name)

        return tmp

    def _is_facet_create(self, call: zast.Call) -> bool:
        """Check if call is facet.create/take from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name not in ("create", "take"):
            return False
        parent_type = self._node_ztype(dp.parent)
        return parent_type is not None and parent_type.typetype == ZTypeType.FACET

    def _is_facet_borrow(self, call: zast.Call) -> bool:
        """Check if call is facet.borrow from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name != "borrow":
            return False
        parent_type = self._node_ztype(dp.parent)
        return parent_type is not None and parent_type.typetype == ZTypeType.FACET

    def _emit_facet_create_call(self, call: zast.Call) -> str:
        """Emit facet.create/take from: expr — returns a value (not pointer)."""
        assert call.callable.nodetype == NodeType.DOTTEDPATH
        facet_type = self._node_ztype(cast(zast.DottedPath, call.callable).parent)
        assert facet_type is not None
        facet_name = facet_type.name

        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        assert from_arg is not None

        arg_val = self._emit_operation_value(from_arg.valtype)
        arg_type = self._node_ztype(from_arg.valtype)
        if not arg_type:
            if from_arg.valtype.nodetype == NodeType.DOTTEDPATH:
                arg_type = self._node_ztype(
                    cast(zast.DottedPath, from_arg.valtype).parent
                )
        impl_name = arg_type.name if arg_type else ""

        # owned-create C name from the conformance entity; legacy fallback.
        conf = self._conformance_pair(arg_type, facet_type)
        if conf is not None:
            owned_create = conf.create_owned_cname
        else:
            label = self._proto_conformance.get((impl_name, facet_name), "")
            owned_create = f"{_cbase_of(arg_type, impl_name)}_{label}_create_owned"
        return f"{owned_create}({arg_val})"

    def _emit_facet_borrow_call(self, call: zast.Call) -> str:
        """Emit facet.borrow from: expr — same as create (copies value)."""
        return self._emit_facet_create_call(call)

    def _emit_facet_dispatch(self, call: zast.Call) -> Optional[str]:
        """If call is a facet method dispatch, return the C expression. Otherwise None."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return None
        dp = cast(zast.DottedPath, call.callable)
        parent_type = self._node_ztype(dp.parent)
        if not parent_type or parent_type.typetype != ZTypeType.FACET:
            return None
        parent_val = self._emit_path_value(dp.parent)
        method = dp.child.name
        args = [f"(void*)&{parent_val}.data"]
        for arg in call.arguments:
            args.append(self._emit_operation_value(arg.valtype))
        return f"{parent_val}.vtable->{method}({', '.join(args)})"

    def _emit_protocol_dispatch(self, call: zast.Call) -> Optional[str]:
        """If call is a protocol method dispatch, return the C expression. Otherwise None."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return None
        dp = cast(zast.DottedPath, call.callable)
        parent_type = self._node_ztype(dp.parent)
        if not parent_type or parent_type.typetype != ZTypeType.PROTOCOL:
            return None
        parent_val = self._emit_path_value(dp.parent)
        method = dp.child.name
        # stack-allocated protocol: use . for locals, -> for pointers
        acc = "->" if self._is_class_pointer_path(dp.parent) else "."
        # Collection-type arguments travel by pointer through the
        # vtable (see _proto_param_ctype). Take the address of the
        # argument expression when the spec declares a collection
        # parameter — looking up the method's spec on the protocol
        # type gives the param-by-position ZType.
        # Id-only child lookup — typecheck stamps child_id on every DottedPath.
        dp_child_id = self.typing.dp_child_id.get(dp.nodeid, -1)
        spec = self.typing.child_by_id(parent_type, dp_child_id)
        spec_params = (
            [(n, t) for n, t in self.typing.children_of(spec) if n != "this"]
            if spec is not None
            else []
        )
        args = [f"{parent_val}{acc}data"]
        # Populate _last_emitted_arg_vals so `_apply_call_implicit_takes`
        # can find the per-arg emitted C exprs after the dispatch.
        self._last_emitted_arg_vals = []
        for i, arg in enumerate(call.arguments):
            val = self._emit_operation_value(arg.valtype)
            if i < len(spec_params):
                _, spec_ptype = spec_params[i]
                if _is_collection_param_type(spec_ptype) and not val.startswith("&"):
                    val = f"&{val}"
            args.append(val)
            self._last_emitted_arg_vals.append(val)
        return f"{parent_val}{acc}vtable->{method}({', '.join(args)})"

    def _emit_callable_dispatch(self, call: zast.Call) -> str:
        """Emit a callable object dispatch: obj(args) -> z_type_call(obj, args)."""
        # The callable object's type is identified by id; resolve it via the
        # global type registry rather than re-resolving its name.
        type_id = self.typing.call_callable_type_id.get(call.nodeid)
        rec_t = _type_by_id(type_id) if type_id is not None else None
        type_name = rec_t.name if rec_t is not None else None
        call_method = self.typing.child_of(rec_t, "call") if rec_t is not None else None
        cname = (
            call_method.cname
            if call_method is not None and call_method.cname
            else _mangle_func(f"{type_name}.call")
        )
        receiver = self._emit_path_value(call.callable)
        # Class methods expect a pointer receiver. Wrap the variable with &
        # when the receiver is a plain atom (not already a pointer).
        if (
            rec_t is not None
            and rec_t.typetype == ZTypeType.CLASS
            and not receiver.startswith("&")
        ):
            receiver = f"&{receiver}"
        args = self._emit_call_args(call)
        arg_str = f"{receiver}, {args}" if args else receiver
        return f"{cname}({arg_str})"

    def _emit_call_stmt(self, call: zast.Call, indent: str) -> str:
        # Step 4d: Call decoration reads via typed mirror.
        _call_kind = self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
        # callable object dispatch as statement
        if _call_kind == zast.CallKind.CALLABLE:
            result = self._emit_callable_dispatch(call)
            return f"{indent}{result};\n"

        # protocol.create/take from: expr (owned protocol creation)
        if self._is_protocol_create(call):
            val = self._emit_protocol_create_call(call)
            return f"{indent}{val};\n"

        # protocol.borrow from: expr (borrowed protocol creation)
        if self._is_protocol_borrow(call):
            val = self._emit_protocol_borrow_call(call)
            return f"{indent}{val};\n"

        # facet.create/take from: expr
        if self._is_facet_create(call):
            val = self._emit_facet_create_call(call)
            return f"{indent}{val};\n"

        # facet.borrow from: expr
        if self._is_facet_borrow(call):
            val = self._emit_facet_borrow_call(call)
            return f"{indent}{val};\n"

        # protocol method dispatch
        proto_expr = self._emit_protocol_dispatch(call)
        if proto_expr is not None:
            result = f"{indent}{proto_expr};\n"
            self._apply_call_implicit_takes(call, indent)
            return result

        # facet method dispatch
        facet_expr = self._emit_facet_dispatch(call)
        if facet_expr is not None:
            result = f"{indent}{facet_expr};\n"
            self._apply_call_implicit_takes(call, indent)
            return result

        callable_name = self._get_callable_name(call.callable)

        if callable_name == "print":
            self.needs_stdio = True
            if call.arguments:
                arg_op = call.arguments[0].valtype
                arg_type = self._get_operation_type(arg_op)
                arg = self._emit_operation_value(arg_op)
                # `print` is generic over `stringlike` (see lib/system/io.z).
                # Monomorphization has already rejected non-member argument
                # types; dispatch here is purely to the per-type runtime
                # primitive, no conversion.
                self.needs_stringview = True
                # Direct stringview argument: pass through.
                if arg_type and arg_type.subtype == ZSubType.STRINGVIEW:
                    return f"{indent}z_StringView_print({arg});\n"
                # String (reftype) fast path — call its existing runtime
                # primitive directly, no projection needed.
                if arg_type and arg_type.subtype == ZSubType.STRING:
                    self.needs_string = True
                    return f"{indent}z_String_print(&{arg});\n"
                # Any other T (str_N or user type) — conforms to `text` by
                # virtue of having passed the constraint check. Project
                # through the conformer's `.stringview` method, which
                # resolves to the concrete type's per-monomorphization
                # emitter and produces a `z_StringView_t`.
                if arg_type and _is_str_type(arg_type):
                    # str_N has (data, len) laid out directly — zero-cost
                    # projection emitted inline rather than through a
                    # function call, matching how str.stringview would
                    # emit anyway.
                    return (
                        f"{indent}z_StringView_print("
                        f"(z_StringView_t){{{arg}.data, {arg}.len}});\n"
                    )
                # User type conforming to `text`: call the type's
                # declared `.stringview` method. The method's C name
                # follows the `z_<typename>_stringview` convention the
                # rest of the emitter uses. Stack-allocated classes
                # pass this by pointer, so prefix `&` for the receiver;
                # records / heap classes / valtypes pass by value.
                if arg_type:
                    tname = arg_type.name.replace(".", "_")
                    recv = arg
                    if (
                        arg_type.typetype == ZTypeType.CLASS
                        and not arg_type.is_heap_allocated
                        and not recv.startswith("&")
                    ):
                        recv = f"&{arg}"
                    return (
                        f"{indent}z_StringView_print(z_{tname}_stringview({recv}));\n"
                    )
                # Unknown type — shouldn't happen post-typecheck, but keep
                # a safe fallback.
                return f"{indent}z_StringView_print({arg});\n"
            return f'{indent}printf("\\n");\n'

        # check call_kind first, then fallback to callable type's control_kind
        _ck = _call_kind
        if _ck == zast.CallKind.UNKNOWN and self._node_ztype(call.callable):
            _ctrl = cast(ZType, self._node_ztype(call.callable)).control_kind
            if _ctrl == ZControlKind.RETURN:
                _ck = zast.CallKind.RETURN
            elif _ctrl == ZControlKind.BREAK:
                _ck = zast.CallKind.BREAK
            elif _ctrl == ZControlKind.CONTINUE:
                _ck = zast.CallKind.CONTINUE

        if _ck == zast.CallKind.RETURN:
            return self._emit_return(call, indent)
        if _ck == zast.CallKind.BREAK:
            return f"{indent}break;\n"
        if _ck == zast.CallKind.CONTINUE:
            return f"{indent}continue;\n"

        # data.index call -> array access
        if (
            self._is_data_index_call(call)
            and call.callable.nodetype == NodeType.DOTTEDPATH
        ):
            _data_parent = cast(zast.DottedPath, call.callable).parent
            assert _data_parent.nodetype == NodeType.ATOMID
            # Dot-free ztype.name (z_zlexer_charflags), not the bare atom,
            # so a dependency unit's data array resolves cross-unit.
            _data_zt = self._unit_def_ztype(_data_parent)
            data_name = (
                _data_zt.name
                if _data_zt is not None
                else cast(zast.AtomId, _data_parent).name
            )
            idx = (
                self._emit_operation_value(call.arguments[0].valtype)
                if call.arguments
                else "0"
            )
            return f"{indent}{_mangle_func(data_name)}[{idx}];\n"

        # array method calls as statements
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_array_type(dp_parent_type):
                val = self._emit_call_value(call)
                return f"{indent}{val};\n"
            if dp_parent_type and _is_str_type(dp_parent_type):
                val = self._emit_call_value(call)
                return f"{indent}{val};\n"
            if dp_parent_type and _is_list_type(dp_parent_type):
                val = self._emit_call_value(call)
                code = f"{indent}{val};\n"
                for arg in call.arguments:
                    take_var = self._get_take_var(arg.valtype)
                    if take_var:
                        arg_t = self._get_operation_type(arg.valtype)
                        code += self._emit_take_invalidation(take_var, arg_t, indent)
                return code
            if dp_parent_type and _is_listview_type(dp_parent_type):
                val = self._emit_call_value(call)
                return f"{indent}{val};\n"
            if dp_parent_type and _is_map_type(dp_parent_type):
                val = self._emit_call_value(call)
                code = f"{indent}{val};\n"
                for arg in call.arguments:
                    take_var = self._get_take_var(arg.valtype)
                    if take_var:
                        arg_t = self._get_operation_type(arg.valtype)
                        code += self._emit_take_invalidation(take_var, arg_t, indent)
                return code
            if dp_parent_type and _is_set_type(dp_parent_type):
                val = self._emit_call_value(call)
                code = f"{indent}{val};\n"
                for arg in call.arguments:
                    take_var = self._get_take_var(arg.valtype)
                    if take_var:
                        arg_t = self._get_operation_type(arg.valtype)
                        code += self._emit_take_invalidation(take_var, arg_t, indent)
                return code

        # string class mutating methods: .append, .reserve, .shrink
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent = cast(zast.DottedPath, call.callable).parent
            dp_parent_type = self._node_ztype(dp_parent)
            if dp_parent_type and dp_parent_type.subtype == ZSubType.STRING:
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(dp_parent)
                # If the receiver is already a pointer (e.g. a borrow-passed
                # param), pass it directly; otherwise take its address.
                recv = (
                    parent_val
                    if self._is_class_pointer_path(dp_parent)
                    else f"&{parent_val}"
                )
                if method_name == "append" and call.arguments:
                    # append's parameter is declared as stringview; the
                    # typechecker rejects other string types. Callers with
                    # a string/str project explicitly with `.stringview`.
                    arg = self._emit_operation_value(call.arguments[0].valtype)
                    self.needs_stringview = True
                    return (
                        f"{indent}z_String_append({recv}, {arg}.data, {arg}.length);\n"
                    )
                if method_name == "appendByte" and call.arguments:
                    arg = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{indent}z_String_append_byte({recv}, (uint8_t)({arg}));\n"
                if method_name == "reserve" and call.arguments:
                    arg = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{indent}z_String_reserve({recv}, (uint64_t){arg});\n"
                if method_name == "shrink":
                    return f"{indent}z_String_shrink({recv});\n"

        args = self._emit_call_args(call)
        args = self._prepend_method_receiver(call, args)
        cname = self._emit_callable_expr(call)
        code = f"{indent}{cname}({args});\n"

        # if call takes a .take argument, invalidate it after the call
        for arg in call.arguments:
            take_var = self._get_take_var(arg.valtype)
            if take_var:
                arg_t = self._get_operation_type(arg.valtype)
                code += self._emit_take_invalidation(take_var, arg_t, indent)

        # implicit take: invalidate args passed to .take parameters
        ftype = self._node_ztype(call.callable)
        emitted_vals = self._last_emitted_arg_vals
        if ftype and self.typing.has_any_ownership(ftype):
            params = self.typing.children_of(ftype)
            # Method calls: `this` is the receiver, prepended at the
            # call site — call.arguments[0] aligns with params[1].
            # The literal "this" mirrors _prepend_method_receiver's
            # early-return predicate (`not self.typing.has_child(ftype, "this")`):
            # the offset must agree with whether the receiver was
            # prepended, and prepending only happens for canonical
            # `:this` form, not named-binding `c: this`.
            offset = (
                1
                if params
                and params[0][0]
                == "this"  # ztc-string-compare-ok: receiver-prepend predicate
                else 0
            )
            for i, arg in enumerate(call.arguments):
                pi = i + offset
                if pi < len(params):
                    pname, _ = params[pi]
                    if (
                        self.typing.child_ownership(ftype, pname)
                        == ZParamOwnership.TAKE
                    ):
                        # skip if already invalidated by explicit .take
                        take_var = self._get_take_var(arg.valtype)
                        if not take_var:
                            root = self._get_implicit_take_var(arg.valtype)
                            if root:
                                # Use storage type so the zero-init cast
                                # matches the variable's C declaration (matters
                                # when the root is a narrowed name — its
                                # typecheck type is the payload, but the C
                                # struct is still the outer union/variant).
                                arg_t = self._get_storage_type(arg.valtype)
                                code += self._emit_take_invalidation(
                                    root, arg_t, indent
                                )
                            elif (
                                i < len(emitted_vals)
                                and emitted_vals[i] in self._temp.string_set
                            ):
                                # temp created by .string conversion — zero-init
                                # so scope cleanup doesn't double-free
                                code += (
                                    f"{indent}{emitted_vals[i]} = (z_String_t){{0}};\n"
                                )

        return code

    def _emit_return(self, call: zast.Call, indent: str) -> str:
        """Emit a return statement with proper string cleanup."""
        if call.arguments:
            val = self._emit_operation_value(call.arguments[0].valtype)
            result = ""

            # remove return value from temp frees (caller owns it)
            if val in self._temp.frees:
                self._temp.frees.remove(val)

            # Flush implicit-take zero-inits (from _transfer_implicit_take)
            # NOW — they must run after the construction reads its args but
            # BEFORE the function-scope cleanup, otherwise the cleanup
            # frees a buffer the returned aggregate has just taken
            # ownership of (UAF). For non-return statements, post_code
            # fires at end-of-statement after any cleanup, which is fine
            # because no scope cleanup runs there; for return-with-value
            # the scope cleanup is inlined here, so the order must be
            # decls -> construction -> implicit-take zero-init ->
            # intermediate frees -> scope cleanup -> return.
            if self._temp.post_code:
                result += "".join(self._temp.post_code)
                self._temp.post_code.clear()

            # free remaining temps (intermediates) before return
            for t in self._temp.frees:
                if t in self._temp.string_set:
                    result += f"{indent}z_String_free(&{t});\n"
                elif t in self._temp.proto_set:
                    proto_base = self._temp.proto_set[t]
                    result += f"{indent}{proto_base}_destroy(&{t});\n"
                elif t in self._temp.class_set:
                    tname = self._temp.class_set[t]
                    result += f"{indent}{self._emit_class_free(t, tname)}\n"
                else:
                    result += f"{indent}free({t});\n"
            self._temp.frees.clear()

            # free func vars (except the return value)
            result += self._emit_scope_cleanup(indent, exclude_var=val)

            result += f"{indent}return {_unwrap_outer_parens(val)};\n"
            return result

        # void return — free all func vars
        result = self._emit_scope_cleanup(indent)
        result += f"{indent}return;\n"
        return result

    def _get_take_var_from_expr(self, expr: zast.Expression) -> Optional[str]:
        """If expr is a var.take expression, return the mangled variable name."""
        inner = expr.expression
        if inner.nodetype == NodeType.DOTTEDPATH:
            return self._get_take_var(cast(zast.DottedPath, inner))
        if inner.nodetype == NodeType.CALL:
            # could be a call with .take arg
            pass
        return None

    def _get_take_var(self, op: zast.Operation) -> Optional[str]:
        """If op is a var.take expression, return the C-level storage to
        invalidate after the take.

        For a plain local, returns the mangled variable name. For a
        narrowed match-arm binding (alias for `(*(T*)X.data)`), returns
        the alias target so downstream invalidation emits a deref-zero
        against the boxed inner — not a wrong-typed zero against the
        outer subject. Matches the alias-aware path that
        `_get_implicit_take_var` already takes.
        """
        if op.nodetype == NodeType.DOTTEDPATH:
            if (
                cast(zast.DottedPath, op).child.name == "take"
                and cast(zast.DottedPath, op).parent.nodetype == NodeType.ATOMID
            ):
                parent_atom = cast(zast.AtomId, cast(zast.DottedPath, op).parent)
                name = parent_atom.name
                if not _is_numeric_id(name):
                    # don't nullify function/spec definitions (immutable program text)
                    if self._is_definition_ref(parent_atom):
                        return None
                    if name in self._alias_map:
                        return self._alias_map[name]
                    return _mangle_var(name)
        return None

    def _get_implicit_take_var(self, op: zast.Operation) -> Optional[str]:
        """Get the variable name for implicit take (plain variable reference).

        For aliased synth temps (`_tN -> b`), the C-level invalidation
        targets the alias's *source* — `_tN` is not a real C local, so
        zeroing it would emit a reference to an undeclared identifier.
        Skip implicit take for alias names with non-trivial alias
        expressions (e.g. dereferences); the source-side invalidation
        is handled by typecheck's own `.take` semantics on the original
        arg before hoisting.
        """
        if op.nodetype == NodeType.ATOMID:
            name = cast(zast.AtomId, op).name
            udt = self._unit_def_ztype(op)
            if (
                not _is_numeric_id(name)
                and (
                    udt is None
                    or udt.typetype not in (ZTypeType.FUNCTION, ZTypeType.DATA)
                )
                and name not in self._const_names
            ):
                # If this name is an alias, redirect implicit-take to
                # the alias's storage:
                # - trivial target (a bare C identifier) -> invalidate that;
                # - non-trivial (dereference / member chain) -> skip,
                #   the underlying storage is managed at its own scope.
                if name in self._alias_map:
                    target = self._alias_map[name]
                    if target.replace("_", "").isalnum():
                        return target
                    return None
                return _mangle_var(name)
        if op.nodetype == NodeType.EXPRESSION and cast(
            zast.Expression, op
        ).expression.nodetype in (
            NodeType.ATOMID,
            NodeType.LABELVALUE,
            NodeType.ATOMSTRING,
            NodeType.EXPRESSION,
            NodeType.DOTTEDPATH,
            NodeType.BINOP,
        ):
            return self._get_implicit_take_var(
                cast(zast.Operation, cast(zast.Expression, op).expression)
            )
        return None

    def _emit_projected_arg(self, arg: zast.NamedOperation) -> str:
        """Emit an implicit protocol-projected argument.

        Typecheck stamps the projection on `TypedNamedOperation`
        (`projected_protocol` / `projected_label` / `projected_kind`)
        when the caller passed a concrete arg to a protocol parameter.
        We synthesise the wrapper here — same C pattern as the explicit
        `proto.borrow` / `proto.create` forms emitted by
        `_emit_protocol_borrow_call` / `_emit_protocol_create_call`."""
        _proj = self.typing.projected_args.get(arg.nodeid)
        assert _proj is not None, "expected projected-arg stamp for projected arg"
        proj_proto, proj_label, proj_kind = _proj
        assert proj_proto is not None
        assert proj_label is not None
        proto_type = proj_proto
        label = proj_label
        arg_val = self._emit_operation_value(arg.valtype)
        arg_type = self._get_operation_type(arg.valtype)
        impl_name = arg_type.name if arg_type else ""
        # Conformance helper names + handle type read from the entity / resolved
        # protocol type (dot-free cross-unit); legacy compose as fallback.
        conf = self._conformance_of(arg_type, proto_type, label)
        proto_ctype = _cname_of(proto_type, proto_type.name)
        proto_base = _cbase_of(proto_type, proto_type.name)
        # Stack-allocated CLASS conformers are passed by pointer; RECORD
        # (valtype) conformers are passed by value. Heap classes already
        # flow as pointers at the call site.
        needs_addr = (
            arg_type is not None
            and arg_type.typetype == ZTypeType.CLASS
            and not arg_type.is_heap_allocated
        )
        arg_expr = (
            f"&{arg_val}" if needs_addr and not arg_val.startswith("&") else arg_val
        )
        if proj_kind == "take":
            create_name = (
                conf.create_owned_cname
                if conf
                else f"{_cbase_of(arg_type, impl_name)}_{label}_create_owned"
            )
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(
                f"{indent}{proto_ctype} {tmp} = {create_name}({arg_expr});\n"
            )
            self._temp.frees.append(tmp)
            self._temp.proto_set[tmp] = proto_base
            # invalidate the source (take semantics) when the arg was a
            # named variable — mirror _apply_take_to_arg's C-side zero.
            src = self._get_implicit_take_var(arg.valtype) if arg_type else None
            if src is not None:
                self._temp.decls.append(
                    self._emit_take_invalidation(src, arg_type, indent)
                )
            return tmp
        # borrow (default): stack-allocated protocol handle, no destroy.
        create_name = (
            conf.create_cname
            if conf
            else f"{_cbase_of(arg_type, impl_name)}_{label}_create"
        )
        tmp = self._temp_name("p")
        indent = self._indent()
        self._temp.decls.append(
            f"{indent}{proto_ctype} {tmp} = {create_name}({arg_expr});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.proto_set[tmp] = proto_base
        return tmp

    def _prepend_method_receiver(self, call: zast.Call, args: str) -> str:
        """If `call` is a method call via a dotted path (`obj.method`) and the
        callable's FUNCTION type has a `this` parameter, prepend the
        receiver to the emitted args. Stack-allocated class receivers get
        `&` added. No-op in every other case (protocol/facet dispatch has
        its own path; free function calls have no receiver).
        """
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return args
        ftype = self._node_ztype(call.callable)
        if not (ftype and ftype.typetype == ZTypeType.FUNCTION):
            return args
        if not self.typing.has_child(ftype, "this"):
            return args
        parent_path = cast(zast.DottedPath, call.callable).parent
        receiver = self._emit_path_value(parent_path)
        parent_type = self._node_ztype(parent_path)
        if (
            parent_type
            and not parent_type.is_heap_allocated
            and parent_type.typetype == ZTypeType.CLASS
            and not receiver.startswith("&")
            and not self._is_class_pointer_path(parent_path)
        ):
            receiver = f"&{receiver}"
        return f"{receiver}, {args}" if args else receiver

    def _emit_call_args(self, call: zast.Call) -> str:
        parts: List[str] = []

        # determine which arg names are generic type args (to skip in emission)
        generic_param_names: set = set()
        ftype = self._node_ztype(call.callable)
        if ftype is not None and ftype.generic_origin is not None:
            gp = ftype.generic_origin.generic_params
            if gp:
                generic_param_names = set(gp.keys())

        # track emitted C values per argument index for implicit-take
        self._last_emitted_arg_vals: List[str] = []
        ctype_idx = 0
        # Method calls: `this` is prepended by `_prepend_method_receiver`
        # at the call site, so call.arguments starts at the first
        # non-this param. Align the ctype lookup by skipping the
        # `this` slot in the callable's param list.
        method_offset = 0
        if ftype and ftype.typetype == ZTypeType.FUNCTION:
            children_keys = self.typing.child_names_of(ftype)
            # Mirror _prepend_method_receiver's early-return predicate
            # (`not self.typing.has_child(ftype, "this")`): only canonical `:this`
            # form is prepended, so only that form needs an offset here.
            if (
                children_keys
                and children_keys[0]
                == "this"  # ztc-string-compare-ok: receiver-prepend predicate
            ):
                method_offset = 1
        for i, arg in enumerate(call.arguments):
            # skip generic type args (they are compile-time only)
            if arg.name and arg.name in generic_param_names:
                self._last_emitted_arg_vals.append("")
                continue
            # Implicit protocol projection stamped by typecheck: emit
            # `z_<impl>_<label>_create` over the concrete argument value
            # so the callee sees a protocol handle.
            _proj = self.typing.projected_args.get(arg.nodeid)
            arg_proj_proto = _proj[0] if _proj else None
            arg_proj_label = _proj[1] if _proj else None
            if arg_proj_proto is not None and arg_proj_label is not None:
                val = self._emit_projected_arg(arg)
                parts.append(val)
                self._last_emitted_arg_vals.append(val)
                ctype_idx += 1
                continue
            val = self._emit_operation_value(arg.valtype)
            param_idx = ctype_idx + method_offset
            # Pre-Phase-C: nested-call args were hoisted here via
            # _alloc_arg_temp to give them a stable C name. Now the
            # typechecker hoists every non-trivial arg into a synth
            # `_tN: <expr>` Assignment in the parent Statement before
            # this point arrives, so arg.valtype is already a bare
            # AtomId at emit time and no per-emit hoisting is needed.
            # stack-allocated class passed as 'this': add &.
            # The C function expects a pointer for 'this' parameters, but the
            # argument is a stack-allocated struct. Detect 'this' by checking
            # if the function is a method and the parameter type matches the
            # enclosing class type.
            arg_type = self._get_operation_type(arg.valtype)
            if (
                arg_type
                and not arg_type.is_heap_allocated
                and arg_type.typetype == ZTypeType.CLASS
                and not val.startswith("&")
                and ftype
                and param_idx < len(self.typing.children_of(ftype))
            ):
                param_name = self.typing.child_names_of(ftype)[param_idx]
                param_type = self.typing.child_of(ftype, param_name)
                # Native instance methods (StringView.replace,
                # BufWriter.write, TextWriter.write, ...) hard-code
                # pointer-typed signatures for non-self class args of
                # the same type as the receiver, so we need to take
                # the address of those args at the call site to
                # match. The C ABI of user-defined methods follows
                # the typecheck-declared ownership (TAKE → value,
                # BORROW/LOCK → pointer), so this fixup only fires
                # for natives. Free natives without a :this receiver
                # — io.readText, os.env, ... — take StringView by
                # value in the runtime and are skipped by the
                # this_param_name guard.
                is_this_param = ftype.this_param_name == param_name or (
                    ftype.is_native
                    and ftype.this_param_name is not None
                    and param_type is arg_type
                )
                # borrow/lock class params also need &. .take on a
                # string param means the callee owns by value — no
                # `&` should be added (that would pass a pointer to
                # a by-value parameter).
                own = self.typing.child_ownership(ftype, param_name)
                is_borrow_lock = own in (
                    ZParamOwnership.BORROW,
                    ZParamOwnership.LOCK,
                )
                is_take_string = (
                    own == ZParamOwnership.TAKE and arg_type.subtype == ZSubType.STRING
                )
                # Suppress a redundant `&` when the argument is already a
                # pointer-typed param of the enclosing function (e.g. a class
                # borrow forwarded into a nested call): taking its address
                # would make `z_DentryTable_t**` and the callee would read
                # garbage. Membership is by identity (variable_id), not by the
                # emitted string. Mirrors the sibling guards in
                # _build_create_args / _build_meta_create_args.
                if (
                    (is_this_param or is_borrow_lock)
                    and not is_take_string
                    and self._operand_vid(arg.valtype) not in self._scope.class_params
                ):
                    val = f"&{val}"
            parts.append(val)
            self._last_emitted_arg_vals.append(val)
            ctype_idx += 1

        # fill defaults for missing trailing params
        ftype = self._node_ztype(call.callable)
        if ftype and self.typing.has_any_default(ftype):
            params = self.typing.children_of(ftype)
            for i in range(len(call.arguments), len(params)):
                pname, ptype = params[i]
                default = self.typing.child_default(ftype, pname)
                if default is not None:
                    parts.append(self._render_default(default, ptype))

        return ", ".join(parts)

    def _render_default(self, default: str, target_type: ZType) -> str:
        """Render a stored default string as a C expression.

        Numeric defaults pass through verbatim. Tagged defaults are encoded
        `#<kind>:<payload>` and dispatched on `<kind>`: `function` mangles the
        payload to its C symbol; `variant` (case-A variant / union null-payload
        subtype default) renders the tag struct literal.
        """
        if default and default[0] == "#":
            sep = default.find(":")
            if sep > 0:
                kind = default[1:sep]
                payload = default[sep + 1 :]
                if kind == "function":  # ztc-string-compare-ok: default-kind tag
                    return _mangle_func(payload)
                if kind == "variant":  # ztc-string-compare-ok: default-kind tag
                    ctype = _ctype(self.typing, target_type)
                    tag = f"Z_{target_type.name.upper()}_TAG_{payload.upper()}"
                    return f"({ctype}){{.tag = {tag}}}"
        return default

    def _zero_args_for_ctypes(self, type_name: str) -> str:
        """Build a zero-default argument list for bare-name construction.

        Each field uses its declared default if one was registered; falls
        back to `0` / `NULL` for fields that don't carry an explicit
        default. Struct-typed fields with no default get a `{0}` compound
        literal so the C signature stays type-correct.
        """
        field_ctypes = self._type_field_ctypes.get(type_name, [])
        field_names = self._type_field_names.get(type_name, [])
        field_defaults = self._type_field_defaults.get(type_name, {})
        parts: List[str] = []
        for i, fct in enumerate(field_ctypes):
            fname = field_names[i] if i < len(field_names) else None
            if fname is not None and fname in field_defaults:
                parts.append(field_defaults[fname])
                continue
            if fct.endswith("*"):
                parts.append("NULL")
            elif (
                fct[:2] == "z_"  # ztc-string-compare-ok: emitted ctype prefix
                and fct.endswith("_t")
                and not fct.endswith("_ft")
            ):
                parts.append(f"({fct}){{0}}")
            else:
                parts.append("0")
        return ", ".join(parts)

    def _build_create_args(
        self,
        type_name: str,
        type_obj: Optional[ZType],
        arguments: list,
        skip_first: int = 0,
    ) -> tuple:
        """Build ordered arguments for a type's `create` call.

        When the type has a user-defined `create` (distinct from the compiler's
        `meta_create` wrapper), use the user's parameter order. Otherwise
        fall back to the default meta-create arg builder which orders by the
        full field declaration list (including function-pointer fields).
        """
        create_fn = None
        meta_fn = None
        if type_obj is not None:
            create_fn = self.typing.child_of(type_obj, "create")
            meta_fn = type_obj.meta_create
        # Default case: no user override — delegate to the meta-create builder.
        if (
            create_fn is None
            or create_fn.typetype != ZTypeType.FUNCTION
            or create_fn is meta_fn
        ):
            return self._build_meta_create_args(
                type_name, type_obj, arguments, skip_first
            )

        # User-defined create: use its parameter order. Include function-typed
        # params (function-pointer field params) since the user's signature is
        # the authoritative source.
        param_names: List[str] = []
        param_ctypes: Dict[str, str] = {}
        for pname, ptype in self.typing.children_of(create_fn):
            param_names.append(pname)
            ct = _ctype(self.typing, ptype)
            # class borrow/lock params are emitted as pointers in C
            if (
                ptype
                and ptype.typetype == ZTypeType.CLASS
                and not ptype.is_heap_allocated
                and not ct.endswith("*")
                and self.typing.child_ownership(create_fn, pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ct = f"{ct}*"
            param_ctypes[pname] = ct

        field_defaults = self._type_field_defaults.get(type_name, {})

        arg_map: Dict[str, str] = {}
        arg_types: Dict[str, Optional[ZType]] = {}
        arg_vids: Dict[str, Optional[int]] = {}
        take_vars: Dict[str, Optional[str]] = {}
        indent = self._indent()
        for arg in arguments[skip_first:]:
            if arg.name:
                val = self._emit_operation_value(arg.valtype)
                arg_map[arg.name] = val
                arg_types[arg.name] = self._get_operation_type(arg.valtype)
                arg_vids[arg.name] = self._operand_vid(arg.valtype)
                take_vars[arg.name] = self._get_take_var(arg.valtype)
                param_own = self.typing.child_ownership(create_fn, arg.name)
                if (
                    param_own != ZParamOwnership.LOCK
                    and param_own != ZParamOwnership.BORROW
                ):
                    self._transfer_implicit_take(val, arg.valtype, indent)

        parts: List[str] = []
        for pname in param_names:
            if pname in arg_map:
                val = arg_map[pname]
                # stack-class passed to pointer param (e.g. .lock): add &
                ct = param_ctypes.get(pname, "")
                at = arg_types.get(pname)
                if (
                    ct.endswith("*")
                    and at is not None
                    and at.typetype == ZTypeType.CLASS
                    and not at.is_heap_allocated
                    and not val.startswith("&")
                    and arg_vids.get(pname) not in self._scope.class_params
                ):
                    val = f"&{val}"
                parts.append(val)
            elif pname in field_defaults:
                parts.append(field_defaults[pname])
            else:
                ct = param_ctypes.get(pname, "int64_t")
                if ct.endswith("*"):
                    parts.append("NULL")
                else:
                    parts.append("0")

        return ", ".join(parts), take_vars

    def _build_meta_create_args(
        self,
        type_name: str,
        type_obj: "Optional[ZType]",
        arguments: list,
        skip_first: int = 0,
    ) -> tuple:
        """Build ordered argument list for meta.create call.

        Maps named call arguments to field declaration order.
        Missing fields get zero values.
        """
        field_names = self._type_field_names.get(type_name, [])
        field_ctypes = self._type_field_ctypes.get(type_name, [])
        field_defaults = self._type_field_defaults.get(type_name, {})

        # The meta.create function holds per-field param ownership
        # (TAKE for owning reftypes, LOCK for `.lock` lock-fields).
        # Implicit-take must only fire for TAKE params — invalidating
        # the source of a LOCK/BORROW param at the C level emits
        # `h = (z_T){0}` against a pointer variable. `type_obj` is supplied
        # by the caller (the type being constructed).
        meta_fn = type_obj.meta_create if type_obj is not None else None

        # build dict from call arguments
        arg_map: Dict[str, str] = {}
        arg_types: Dict[str, Optional[ZType]] = {}
        arg_vids: Dict[str, Optional[int]] = {}
        take_vars: Dict[str, Optional[str]] = {}
        indent = self._indent()
        for arg in arguments[skip_first:]:
            if arg.name:
                val = self._emit_operation_value(arg.valtype)
                arg_map[arg.name] = val
                arg_types[arg.name] = self._get_operation_type(arg.valtype)
                arg_vids[arg.name] = self._operand_vid(arg.valtype)
                take_vars[arg.name] = self._get_take_var(arg.valtype)
                param_own = (
                    self.typing.child_ownership(meta_fn, arg.name)
                    if meta_fn is not None
                    else None
                )
                if (
                    param_own != ZParamOwnership.LOCK
                    and param_own != ZParamOwnership.BORROW
                ):
                    self._transfer_implicit_take(val, arg.valtype, indent)

        # build ordered args
        parts: List[str] = []
        for i, fname in enumerate(field_names):
            if fname in arg_map:
                val = arg_map[fname]
                # stack-class passed to pointer field (e.g. .lock): add &
                fct = field_ctypes[i] if i < len(field_ctypes) else ""
                at = arg_types.get(fname)
                if (
                    fct.endswith("*")
                    and at is not None
                    and at.typetype == ZTypeType.CLASS
                    and not at.is_heap_allocated
                    and not val.startswith("&")
                    and arg_vids.get(fname) not in self._scope.class_params
                ):
                    val = f"&{val}"
                parts.append(val)
            elif fname in field_defaults:
                parts.append(field_defaults[fname])
            else:
                # zero value based on C type. Struct-typed fields
                # (anything starting with `z_` and ending with `_t`
                # that's neither a pointer nor a primitive numeric
                # alias) need a compound literal `(ctype){0}` --
                # passing a literal `0` would fail the function
                # signature's struct-typed parameter. Falls through
                # for `int64_t` and similar numeric aliases.
                fct = field_ctypes[i] if i < len(field_ctypes) else "int64_t"
                if fct.endswith("*"):
                    parts.append("NULL")
                elif (
                    fct[:2] == "z_"  # ztc-string-compare-ok: emitted ctype prefix
                    and fct.endswith("_t")
                    and not fct.endswith("_ft")
                ):
                    parts.append(f"({fct}){{0}}")
                else:
                    parts.append("0")

        return ", ".join(parts), take_vars

    def _get_callable_name(self, path: zast.Path) -> str:
        if path.nodetype == NodeType.ATOMID:
            # resolve unit aliases to their actual type name
            if cast(zast.AtomId, path).name in self._unit_aliases:
                return self._unit_aliases[cast(zast.AtomId, path).name].name
            return cast(zast.AtomId, path).name
        if path.nodetype == NodeType.DOTTEDPATH:
            parent = self._get_callable_name(cast(zast.DottedPath, path).parent)
            return f"{parent}.{cast(zast.DottedPath, path).child.name}"
        return "unknown"

    def _emit_expression_value(self, expr: zast.Expression) -> str:
        inner = expr.expression
        # Generator yield as a value (RHS of `x: yield v` /
        # `this.x = yield v`): emit the suspension fragment as a
        # preamble (set state, return OPT_SOME, label-for-resume),
        # then return `this->_resume_input` as the value the
        # surrounding assignment binds. Only fires for bidirectional
        # generators — the desugarer rejects expression-form yield
        # as the *first* reachable yield (rule 11), so by the time
        # the assignment lands `this->_resume_input` has been
        # written by the caller's `.call value: ...` arg.
        if inner.nodetype == NodeType.YIELD and self._generator_ctx is not None:
            fragment = self._emit_yield_fragment(cast(zast.Yield, inner))
            self._temp.decls.append(fragment)
            return "this->_resume_input"
        if inner.nodetype == zast.NodeType.CALL:
            return self._emit_call_value(cast(zast.Call, inner))
        if inner.nodetype in (
            zast.NodeType.BINOP,
            zast.NodeType.DOTTEDPATH,
            zast.NodeType.ATOMID,
            zast.NodeType.ATOMSTRING,
            zast.NodeType.EXPRESSION,
            zast.NodeType.LABELVALUE,
        ):
            # bare function name = call with all-default args. Use the
            # binding stamp (not node_type.typetype) so a FUNCTION-typed local
            # is not mistaken for a unit-level function reference.
            inner_def = (
                self._unit_def_ztype(inner)
                if inner.nodetype == zast.NodeType.ATOMID
                else None
            )
            if inner_def is not None and inner_def.typetype == ZTypeType.FUNCTION:
                atom = cast(zast.AtomId, inner)
                ftype = self._node_ztype(atom)
                if ftype and self.typing.has_any_default(ftype):
                    # only emit bare call when ALL params have defaults
                    real_params = self.typing.children_of(ftype)
                    all_defaulted = all(
                        self.typing.has_child_default(ftype, p) for p, _ in real_params
                    )
                    if all_defaulted:
                        cname = (
                            ftype.cname
                            if ftype.cname and not ftype.is_native
                            else _mangle_func(atom.name)
                        )
                        defaults: List[str] = []
                        for pname, ptype in real_params:
                            d = self.typing.child_default(ftype, pname)
                            if d is None:
                                continue
                            defaults.append(self._render_default(d, ptype))
                        return f"{cname}({', '.join(defaults)})"
            return self._emit_operation_value(cast(zast.Operation, inner))
        if inner.nodetype == zast.NodeType.WITH:
            return self._emit_expression_value(cast(zast.With, inner).doexpr)
        if inner.nodetype == zast.NodeType.DO and self._node_ztype(inner):
            return self._emit_do_expression_value(cast(zast.Do, inner))
        if inner.nodetype == zast.NodeType.IF:
            return self._emit_if_expression_value(cast(zast.If, inner))
        if inner.nodetype == zast.NodeType.FOR and self._node_ztype(inner):
            return self._emit_for_expression_value(cast(zast.For, inner))
        if inner.nodetype == zast.NodeType.CASE and self._node_ztype(inner):
            return self._emit_case_expression_value(cast(zast.Case, inner))
        return "0"

    def _emit_call_value(self, call: zast.Call) -> str:
        # Step 4d: Call decoration reads via typed mirror.
        _call_kind = self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
        _call_ztype = self._node_ztype(call)
        # callable object dispatch: obj(args) -> z_type_call(obj, args)
        if _call_kind == zast.CallKind.CALLABLE:
            result = self._emit_callable_dispatch(call)
            if _call_ztype:
                if _call_ztype.subtype == ZSubType.STRING:
                    return self._alloc_temp(result)
                if _call_ztype.typetype == ZTypeType.CLASS:
                    ctype = _cname_of(_call_ztype, _call_ztype.name)
                    tmp = self._temp_name("c")
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                    if _call_ztype.destructor_name is not None:
                        self._temp.frees.append(tmp)
                        self._temp.class_set[tmp] = _call_ztype.name
                    return tmp
                if _call_ztype.typetype == ZTypeType.UNION:
                    ctype = _cname_of(_call_ztype, _call_ztype.name)
                    tmp = self._temp_name("c")
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                    if _call_ztype.destructor_name is not None:
                        self._temp.frees.append(tmp)
                        self._temp.class_set[tmp] = _call_ztype.name
                    return tmp
            return result

        # protocol.create/take from: expr (owned protocol creation)
        if self._is_protocol_create(call):
            return self._emit_protocol_create_call(call)

        # protocol.borrow from: expr (borrowed protocol creation)
        if self._is_protocol_borrow(call):
            return self._emit_protocol_borrow_call(call)

        # facet.create/take from: expr
        if self._is_facet_create(call):
            return self._emit_facet_create_call(call)

        # facet.borrow from: expr
        if self._is_facet_borrow(call):
            return self._emit_facet_borrow_call(call)

        # protocol method dispatch
        proto_expr = self._emit_protocol_dispatch(call)
        if proto_expr is not None:
            self._apply_call_implicit_takes(call, self._indent())
            return proto_expr

        # facet method dispatch
        facet_expr = self._emit_facet_dispatch(call)
        if facet_expr is not None:
            self._apply_call_implicit_takes(call, self._indent())
            return facet_expr

        # data.index call -> array access
        if (
            self._is_data_index_call(call)
            and call.callable.nodetype == NodeType.DOTTEDPATH
        ):
            _data_parent = cast(zast.DottedPath, call.callable).parent
            assert _data_parent.nodetype == NodeType.ATOMID
            # Dot-free ztype.name (z_zlexer_charflags), not the bare atom,
            # so a dependency unit's data array resolves cross-unit.
            _data_zt = self._unit_def_ztype(_data_parent)
            data_name = (
                _data_zt.name
                if _data_zt is not None
                else cast(zast.AtomId, _data_parent).name
            )
            idx = (
                self._emit_operation_value(call.arguments[0].valtype)
                if call.arguments
                else "0"
            )
            return f"{_mangle_func(data_name)}[{idx}]"

        # typedef create/take/borrow: identity — just emit the from: argument
        if _call_ztype and _call_ztype.typedef_base is not None:
            if call.arguments:
                return self._emit_operation_value(call.arguments[0].valtype)
            return "0"

        # array construction: zero-initialized, no field args
        if self._node_ztype(call.callable) and _is_array_type(
            self._node_ztype(call.callable)
        ):
            return f"z_{cast(ZType, self._node_ztype(call.callable)).name}_create()"

        # stringview method calls: .string, .length
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_stringview_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                if method_name == "string":
                    self.needs_stringview = True
                    self.needs_stdlib = True
                    result = f"z_String_from_view({parent_val})"
                    return self._alloc_temp(result)
                if method_name == "length":
                    self.needs_stringview = True
                    return f"{parent_val}.length"
                if method_name == "compare" and call.arguments:
                    rhs_val = self._emit_operation_value(call.arguments[0].valtype)
                    self.needs_stringview = True
                    self.needs_stdint = True
                    return f"z_StringView_cmp({parent_val}, {rhs_val})"
                if method_name == "hash":  # ztc-string-compare-ok: stdlib mthd
                    self.needs_stringview = True
                    self.needs_hash = True
                    return f"z_siphash_stringview({parent_val})"

        # string and str method calls: .stringview (both), .length / .capacity (string)
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, call.callable)
            dp_parent_type = self._node_ztype(dp.parent)
            is_string = dp_parent_type is not None and (
                dp_parent_type.subtype == ZSubType.STRING
            )
            is_str = dp_parent_type is not None and _is_str_type(dp_parent_type)
            if dp_parent_type is not None and (is_string or is_str):
                method_name = dp.child.name
                parent_val = self._emit_path_value(dp.parent)
                if method_name == "stringview":
                    from_val = None
                    to_val = None
                    for arg in call.arguments:
                        if arg.name == "from":
                            from_val = self._emit_operation_value(arg.valtype)
                        elif arg.name == "to":
                            to_val = self._emit_operation_value(arg.valtype)
                    is_pointer_path = is_string and self._is_class_pointer_path(
                        dp.parent
                    )
                    return self._emit_stringview_value(
                        parent_val,
                        dp_parent_type,
                        is_pointer_path,
                        from_val,
                        to_val,
                    )
                if is_string and method_name == "length":
                    return f"{parent_val}->size"
                if is_string and method_name == "capacity":
                    return f"{parent_val}->capacity"
                if is_string and method_name == "compare" and call.arguments:
                    rhs_val = self._emit_operation_value(call.arguments[0].valtype)
                    self.needs_stdint = True
                    self.needs_string = True
                    return f"z_String_cmp(&{parent_val}, &{rhs_val})"
                if is_string and method_name == "copy":
                    self.needs_string = True
                    self.needs_stdlib = True
                    is_pointer_path = self._is_class_pointer_path(dp.parent)
                    arg = parent_val if is_pointer_path else f"&{parent_val}"
                    return self._alloc_temp(f"z_String_copy({arg})")
                # fmt: off
                if is_string and method_name == "hash":  # ztc-string-compare-ok: stdlib mthd
                    # fmt: on
                    self.needs_string = True
                    self.needs_hash = True
                    is_pointer_path = self._is_class_pointer_path(dp.parent)
                    arg = f"*{parent_val}" if is_pointer_path else parent_val
                    return f"z_siphash_string({arg})"

        # string construction: string or string capacity: N
        if (
            self._node_ztype(call.callable)
            and cast(ZType, self._node_ztype(call.callable)).subtype == ZSubType.STRING
        ):
            self.needs_string = True
            self.needs_stdlib = True
            cap = "0"
            for arg in call.arguments:
                if arg.name == "capacity":
                    cap = self._emit_operation_value(arg.valtype)
                    break
            return self._alloc_temp(f"z_String_create((uint64_t){cap})")

        # str construction: (str to: N) — always empty
        if self._node_ztype(call.callable) and _is_str_type(
            self._node_ztype(call.callable)
        ):
            str_name = _mono_name(self._node_ztype(call.callable))
            return f"z_{str_name}_create()"

        # list construction: (list of: T) or (list of: T) capacity: N
        if self._node_ztype(call.callable) and _is_list_type(
            self._node_ztype(call.callable)
        ):
            list_name = _mono_name(self._node_ztype(call.callable))
            cap_arg = None
            for arg in call.arguments:
                if arg.name == "capacity":
                    cap_arg = arg
                    break
            if cap_arg is not None:
                cap_val = self._emit_operation_value(cap_arg.valtype)
                return f"z_{list_name}_create({cap_val})"
            return f"z_{list_name}_create(0)"

        # map construction: (map key: K value: V) or with capacity:
        if self._node_ztype(call.callable) and _is_map_type(
            self._node_ztype(call.callable)
        ):
            map_name = _mono_name(self._node_ztype(call.callable))
            cap_arg = None
            for arg in call.arguments:
                if arg.name == "capacity":
                    cap_arg = arg
                    break
            if cap_arg is not None:
                cap_val = self._emit_operation_value(cap_arg.valtype)
                return f"z_{map_name}_create({cap_val})"
            return f"z_{map_name}_create(0)"

        # set construction: (set of: T) or with capacity:
        if self._node_ztype(call.callable) and _is_set_type(
            self._node_ztype(call.callable)
        ):
            set_name = _mono_name(self._node_ztype(call.callable))
            cap_arg = None
            for arg in call.arguments:
                if arg.name == "capacity":  # ztc-string-compare-ok: stdlib arg
                    cap_arg = arg
                    break
            if cap_arg is not None:
                cap_val = self._emit_operation_value(cap_arg.valtype)
                return f"z_{set_name}_create({cap_val})"
            return f"z_{set_name}_create(0)"

        # array method calls: .get and .set
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_array_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return (
                        f"{self._synth_method_cname(dp_parent_type, 'get')}"
                        f"({parent_val}, {idx_val})"
                    )
                if method_name == "set" and len(call.arguments) >= 2:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    val_val = self._emit_operation_value(call.arguments[1].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'set')}(&{parent_val}, {idx_val}, {val_val})"

        # str method calls: .string
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_str_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                if method_name == "string":
                    result = f"{self._synth_method_cname(dp_parent_type, 'string')}({parent_val})"
                    return self._alloc_temp(result)

        # stringview method calls: .string
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_stringview_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                if method_name == "string":
                    self.needs_stringview = True
                    self.needs_stdlib = True
                    result = f"z_String_from_view({parent_val})"
                    return self._alloc_temp(result)

        # .str conversion method on string, str, and stringview types
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, call.callable)
            dp_parent_type = self._node_ztype(dp.parent)
            method_name = dp.child.name
            if (
                method_name == "str"
                and dp_parent_type
                and (
                    dp_parent_type.subtype == ZSubType.STRING
                    or _is_str_type(dp_parent_type)
                    or _is_stringview_type(dp_parent_type)
                )
            ):
                target_type = _call_ztype
                if target_type and _is_str_type(target_type):
                    target_name = target_type.name
                    parent_val = self._emit_path_value(dp.parent)
                    # string literal optimization: direct struct init
                    inner = dp.parent
                    if inner.nodetype == NodeType.EXPRESSION:
                        inner = cast(zast.Expression, inner).expression
                    if inner.nodetype == NodeType.ATOMSTRING and not any(
                        p.nodetype != zast.NodeType.STRINGCHUNK
                        for p in cast(zast.AtomString, inner).stringparts
                    ):
                        cap = _str_capacity(self.typing, target_type)
                        literal = self._collect_string_literal(
                            cast(zast.AtomString, inner).stringparts
                        )
                        lit_len = len(
                            literal.encode("utf-8")
                            .decode("unicode_escape")
                            .encode("utf-8")
                        )
                        if cap is not None and lit_len <= cap:
                            ctype = _cname_of(target_type, target_name)
                            return f'({ctype}){{{lit_len}, "{literal}"}}'
                    # emit call to shared converter
                    if dp_parent_type.subtype == ZSubType.STRING:
                        return (
                            f"z_String_to_{target_name}"
                            f"({parent_val}->data, {parent_val}->size)"
                        )
                    elif _is_stringview_type(dp_parent_type):
                        self.needs_stringview = True
                        return (
                            f"z_String_to_{target_name}"
                            f"({parent_val}.data, {parent_val}.length)"
                        )
                    else:
                        return (
                            f"z_String_to_{target_name}"
                            f"({parent_val}.data, {parent_val}.len)"
                        )

        # io.file method calls: .close / .read / .write
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            parent_path = cast(zast.DottedPath, call.callable).parent
            if self._effective_file_type(parent_path):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(parent_path)
                if not self._is_class_pointer_path(
                    parent_path
                ) and not parent_val.startswith("&"):
                    parent_val = f"&{parent_val}"
                if method_name == "close":
                    self.needs_io = True
                    self.needs_stdio = True
                    self.needs_io_natives.add("file_close")
                    return f"z_File_close({parent_val})"
                if method_name == "read":
                    # args: into: (list of u8), max: u64
                    into_val = None
                    max_val = None
                    for arg in call.arguments:
                        if arg.name == "into":
                            into_val = self._emit_operation_value(arg.valtype)
                            # list is stack-allocated; pass pointer for
                            # in-place mutation.
                            if not into_val.startswith("&"):
                                into_val = f"&{into_val}"
                        elif arg.name == "max":
                            max_val = self._emit_operation_value(arg.valtype)
                    self.needs_io = True
                    self.needs_stdio = True
                    self.needs_io_natives.add("file_read")
                    return f"z_File_read({parent_val}, {into_val}, {max_val})"
                if method_name == "write":
                    # args: from: (list of u8)
                    from_val = None
                    for arg in call.arguments:
                        if arg.name == "from":
                            from_val = self._emit_operation_value(arg.valtype)
                            if not from_val.startswith("&"):
                                from_val = f"&{from_val}"
                    self.needs_io = True
                    self.needs_stdio = True
                    self.needs_io_natives.add("file_write")
                    return f"z_File_write({parent_val}, {from_val})"
                if method_name == "seek":
                    # args: to: i64, from: seekorigin
                    to_val = None
                    origin_val = None
                    for arg in call.arguments:
                        if arg.name == "to":
                            to_val = self._emit_operation_value(arg.valtype)
                        elif arg.name == "from":
                            origin_val = self._emit_operation_value(arg.valtype)
                    self.needs_io = True
                    self.needs_stdio = True
                    self.needs_io_natives.add("file_seek")
                    return f"z_File_seek({parent_val}, {to_val}, {origin_val})"
                if method_name == "flush":
                    # no-op on raw POSIX fds; declared for writer
                    # protocol conformance.
                    self.needs_io = True
                    self.needs_stdio = True
                    self.needs_io_natives.add("file_flush")
                    return f"z_File_flush({parent_val})"

        # list method calls: .append, .insert, .extend, .get, .set, .pop
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_list_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                # stack-allocated list: pass address to methods
                parent_path = cast(zast.DottedPath, call.callable).parent
                if not self._is_class_pointer_path(
                    parent_path
                ) and not parent_val.startswith("&"):
                    parent_val = f"&{parent_val}"
                list_type_name = _mono_name(dp_parent_type)
                if method_name == "append" and call.arguments:
                    from_arg = call.arguments[0]
                    val = self._emit_operation_value(from_arg.valtype)
                    # The appended element is consumed by the list
                    # (struct copy aliases its heap buffer). Zero-init
                    # the source so its scope-exit destructor does not
                    # double-free the element's buffer. Safe no-op for
                    # pure valtype elements (i64 etc.).
                    indent = self._indent()
                    self._transfer_implicit_take(val, from_arg.valtype, indent)
                    return f"{self._synth_method_cname(dp_parent_type, 'append')}({parent_val}, {val})"
                if method_name == "insert" and len(call.arguments) >= 2:
                    from_val = None
                    at_val = None
                    for arg in call.arguments:
                        if arg.name == "from":
                            from_val = self._emit_operation_value(arg.valtype)
                        elif arg.name == "at":
                            at_val = self._emit_operation_value(arg.valtype)
                    if from_val is None:
                        from_val = self._emit_operation_value(call.arguments[0].valtype)
                    if at_val is None:
                        at_val = self._emit_operation_value(call.arguments[1].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'insert')}({parent_val}, {from_val}, {at_val})"
                if method_name == "extend" and call.arguments:
                    from_arg = call.arguments[0]
                    from_val = self._emit_operation_value(from_arg.valtype)
                    # extend takes a pointer to the source list, then
                    # consumes its data (struct-copies the buffer). Zero
                    # the source post-call so scope exit doesn't free a
                    # buffer the destination now owns.
                    indent = self._indent()
                    from_tmp = self._alloc_arg_temp(f"z_{list_type_name}_t", from_val)
                    self._transfer_implicit_take(from_val, from_arg.valtype, indent)
                    return f"{self._synth_method_cname(dp_parent_type, 'extend')}({parent_val}, &{from_tmp})"
                if method_name == "extendView" and call.arguments:
                    # extendView takes a listview by value (copies, does
                    # not consume). The argument is typed as a listview of
                    # the list's element; the mono emitter generates a
                    # z_{listname}_extendView(z_{listname}_t*, z_ListView_T_t).
                    from_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'extendView')}({parent_val}, {from_val})"
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'get')}({parent_val}, {idx_val})"
                if method_name == "set" and len(call.arguments) >= 2:
                    idx_val = None
                    val_val = None
                    for arg in call.arguments:
                        if arg.name == "i":
                            idx_val = self._emit_operation_value(arg.valtype)
                        elif arg.name == "val":
                            val_val = self._emit_operation_value(arg.valtype)
                    if idx_val is None:
                        idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    if val_val is None:
                        val_val = self._emit_operation_value(call.arguments[1].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'set')}({parent_val}, {idx_val}, {val_val})"
                if method_name == "pop":
                    return f"{self._synth_method_cname(dp_parent_type, 'pop')}({parent_val})"
                # fmt: off
                if method_name == "contains" and call.arguments:  # ztc-string-compare-ok: stdlib mthd
                    # fmt: on
                    item_arg = None
                    for arg in call.arguments:
                        if arg.name == "item":  # ztc-string-compare-ok: stdlib arg
                            item_arg = arg
                            break
                    if item_arg is None:
                        item_arg = call.arguments[0]
                    item_val = self._emit_operation_value(item_arg.valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'contains')}({parent_val}, {item_val})"
                if method_name == "sort":  # ztc-string-compare-ok: stdlib mthd
                    return f"{self._synth_method_cname(dp_parent_type, 'sort')}({parent_val})"
                if method_name == "listview":
                    return f"{self._synth_method_cname(dp_parent_type, 'listview')}({parent_val})"

        # listview method calls: .get
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_listview_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                parent_path = cast(zast.DottedPath, call.callable).parent
                if not self._is_class_pointer_path(
                    parent_path
                ) and not parent_val.startswith("&"):
                    parent_val = f"&{parent_val}"
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'get')}({parent_val}, {idx_val})"

        # map method calls: .set, .get, .delete, .has
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = self._node_ztype(
                cast(zast.DottedPath, call.callable).parent
            )
            if dp_parent_type and _is_map_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                if method_name == "set" and len(call.arguments) >= 2:
                    key_arg = None
                    val_arg = None
                    for arg in call.arguments:
                        if arg.name == "key":
                            key_arg = arg
                        elif arg.name == "value":
                            val_arg = arg
                    if key_arg is None:
                        key_arg = call.arguments[0]
                    if val_arg is None:
                        val_arg = call.arguments[1]
                    key_val = self._emit_operation_value(key_arg.valtype)
                    val_val = self._emit_operation_value(val_arg.valtype)
                    # map.set takes ownership of both key and value — apply
                    # implicit-take semantics for heap-backed stack structs.
                    indent = self._indent()
                    self._transfer_implicit_take(key_val, key_arg.valtype, indent)
                    self._transfer_implicit_take(val_val, val_arg.valtype, indent)
                    return f"{self._synth_method_cname(dp_parent_type, 'set')}({parent_val}, {key_val}, {val_val})"
                if method_name == "get" and call.arguments:
                    key_val = self._emit_operation_value(call.arguments[0].valtype)
                    result = f"{self._synth_method_cname(dp_parent_type, 'get')}({parent_val}, {key_val})"
                    ret_type = _call_ztype
                    if ret_type and ret_type.is_nullable_ptr:
                        # nullable-ptr option: track as temp for destroy
                        tmp = self._temp_name("c")
                        indent = self._indent()
                        inner_ctype = _ctype(self.typing, ret_type)
                        self._temp.decls.append(
                            f"{indent}{inner_ctype} {tmp} = {result};\n"
                        )
                        self._temp.frees.append(tmp)
                        return tmp
                    if ret_type and ret_type.typetype == ZTypeType.VARIANT:
                        # optionval variant: no temp tracking needed (value type)
                        return result
                    if ret_type and ret_type.typetype == ZTypeType.UNION:
                        # Value-returned union: must match the function's
                        # declared return type (e.g. `z_Option_String_t`
                        # for `Map<u32, String>.get`). Heap-allocated
                        # payloads route through the nullable_ptr branch
                        # above; anything reaching here is a value-returned
                        # union whose inner ownership is tracked via frees
                        # + scope-exit destroy. Use _ctype so the struct
                        # type resolves correctly instead of hand-spelling
                        # a pointer that disagrees with the signature.
                        tmp = self._temp_name("c")
                        indent = self._indent()
                        ctype = _ctype(self.typing, ret_type)
                        self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                        self._temp.frees.append(tmp)
                        return tmp
                    return result
                if method_name == "delete" and call.arguments:
                    key_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'delete')}({parent_val}, {key_val})"
                if method_name == "has" and call.arguments:
                    key_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"{self._synth_method_cname(dp_parent_type, 'has')}({parent_val}, {key_val})"

            # set method calls: .add / .has / .delete
            if dp_parent_type and _is_set_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                # Set has three mutating methods that all follow the
                # same `item:` arg shape: add / has / delete. Look up
                # the named `item` arg (or fall back to positional),
                # emit `z_<set>_<method>(parent, item)`.
                _SET_METHODS = ("add", "has", "delete")
                if method_name in _SET_METHODS and call.arguments:
                    item_arg = None
                    for arg in call.arguments:
                        if arg.name == "item":  # ztc-string-compare-ok: stdlib arg
                            item_arg = arg
                            break
                    if item_arg is None:
                        item_arg = call.arguments[0]
                    item_val = self._emit_operation_value(item_arg.valtype)
                    if method_name == "add":  # ztc-string-compare-ok: stdlib mthd
                        indent = self._indent()
                        self._transfer_implicit_take(item_val, item_arg.valtype, indent)
                    return f"{self._synth_method_cname(dp_parent_type, method_name)}({parent_val}, {item_val})"

        if (
            self._node_ztype(call.callable)
            and cast(ZType, self._node_ztype(call.callable)).typetype
            == ZTypeType.RECORD
        ):
            rec_type = cast(ZType, self._node_ztype(call.callable))
            args_str, take_vars = self._build_create_args(
                rec_type.name, rec_type, call.arguments
            )
            result = f"{_cbase_of(rec_type, rec_type.name)}_create({args_str})"
            # handle .take nullification
            for fname, tv in take_vars.items():
                if tv:
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{tv} = NULL;\n")
            return result

        # box construction: box from: val
        if _call_kind == zast.CallKind.BOX_CREATE:
            return self._emit_box_create(call)
        if _call_kind == zast.CallKind.BOX_PASSTHROUGH:
            return self._emit_box_passthrough(call)

        # union construction: union.subtype expr
        if self._is_union_construction(call):
            return self._emit_union_construction(call)

        # variant construction: variant.subtype expr
        if self._is_variant_construction(call):
            return self._emit_variant_construction(call)

        # meta.create: route through `_build_meta_create_args` so
        # missing fields get zero-init defaults (the synth class
        # `.create` only passes explicit args; locals / `_resume_input`
        # / etc. fall through to the field-default machinery).
        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
            and cast(zast.AtomId, cast(zast.DottedPath, call.callable).parent).name
            == "meta"  # ztc-string-compare-ok: meta.create dispatch
            and cast(zast.DottedPath, call.callable).child.name
            == "create"  # ztc-string-compare-ok: meta.create dispatch
            and self._current_enclosing_type_name
        ):
            enclosing_t = self._current_enclosing_type
            # Use the enclosing type's dot-free ztype.name as the field-info key
            # (matches how field-info is registered); the AST-path name is the
            # fallback for an unstamped enclosing type.
            type_name = (
                enclosing_t.name
                if enclosing_t is not None and enclosing_t.name
                else self._current_enclosing_type_name
            )
            args_str, take_vars = self._build_meta_create_args(
                type_name, enclosing_t, call.arguments
            )
            result = f"{_cbase_of(enclosing_t, type_name)}_meta_create({args_str})"
            ctype = _cname_of(enclosing_t, type_name)
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
            for fname, tv in take_vars.items():
                if tv:
                    ft = (
                        self.typing.child_of(enclosing_t, fname)
                        if enclosing_t
                        else None
                    )
                    self._temp.decls.append(
                        self._emit_take_invalidation(tv, ft, indent)
                    )
            if _call_ztype and _call_ztype.destructor_name is not None:
                self._temp.frees.append(tmp)
                self._temp.class_set[tmp] = type_name
            return tmp

        if (
            self._node_ztype(call.callable)
            and cast(ZType, self._node_ztype(call.callable)).typetype == ZTypeType.CLASS
        ):
            cls_type = cast(ZType, self._node_ztype(call.callable))
            ctype = _cname_of(cls_type, cls_type.name)
            self.needs_stdlib = True
            args_str, take_vars = self._build_create_args(
                cls_type.name, cls_type, call.arguments
            )
            result = f"{_cbase_of(cls_type, cls_type.name)}_create({args_str})"
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
            # handle .take invalidation
            for fname, tv in take_vars.items():
                if tv:
                    # get field type for proper invalidation
                    ft = self.typing.child_of(cls_type, fname)
                    self._temp.decls.append(
                        self._emit_take_invalidation(tv, ft, indent)
                    )
            if cls_type.destructor_name is not None:
                self._temp.frees.append(tmp)
                self._temp.class_set[tmp] = cls_type.name
            return tmp

        args = self._emit_call_args(call)
        args = self._prepend_method_receiver(call, args)

        cname = self._emit_callable_expr(call)
        result = f"{cname}({args})"
        indent = self._indent()

        # if call returns a reftype, wrap in temp for cleanup
        if _call_ztype:
            if _call_ztype.subtype == ZSubType.STRING:
                # A callee declared `out string.borrow` returns a borrowed
                # view — caller does NOT own it and must not free it.
                ftype = self._node_ztype(call.callable)
                if ftype and ftype.return_ownership == ZParamOwnership.BORROW:
                    tmp = self._temp_name("t")
                    self._temp.decls.append(f"{indent}z_String_t {tmp} = {result};\n")
                else:
                    tmp = self._alloc_temp(result)
                self._apply_call_implicit_takes(call, indent)
                return tmp
            if _call_ztype.typetype == ZTypeType.CLASS:
                ctype = _cname_of(_call_ztype, _call_ztype.name)
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                if _call_ztype.destructor_name is not None:
                    self._temp.frees.append(tmp)
                    self._temp.class_set[tmp] = _call_ztype.name
                self._apply_call_implicit_takes(call, indent)
                return tmp
            if _call_ztype.typetype == ZTypeType.UNION:
                # The callee returns a union by value; wrap in a local
                # so the subsequent assignment/destroy can take its
                # address. Cleanup routes through class_set so scope
                # exit emits `z_<T>_destroy(&tmp)` (freeing the inner
                # payload without trying to free the stack slot).
                ctype = _cname_of(_call_ztype, _call_ztype.name)
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                if _call_ztype.destructor_name is not None:
                    self._temp.frees.append(tmp)
                    self._temp.class_set[tmp] = _call_ztype.name
                self._apply_call_implicit_takes(call, indent)
                return tmp

        # non-reftype return (int/float/void): wrap in a stmt-expr temp so that
        # implicit-take invalidations for string args are ordered AFTER the
        # call. Only do this when a string arg is present, to avoid disturbing
        # the cleanup of other heap-backed stack-struct args (protocols, etc.).
        if self._call_has_string_arg(call):
            ret_type = _call_ztype
            if ret_type is None or _ctype(self.typing, ret_type) == "void":
                self._temp.decls.append(f"{indent}{result};\n")
                self._apply_call_implicit_takes(call, indent)
                return "0"
            tmp = self._temp_name("r")
            ct = _ctype(self.typing, ret_type)
            self._temp.decls.append(f"{indent}{ct} {tmp} = {result};\n")
            self._apply_call_implicit_takes(call, indent)
            return tmp

        return result

    def _emit_const_value(self, node: zast.Node) -> str:
        """Emit a compile-time constant value as a C literal."""
        v = self._node_const_value(node)
        assert v is not None
        self.needs_stdint = True
        if type(v) is bool:
            return "1" if v else "0"
        if type(v) is float:
            # emit with full precision for f64
            return repr(v)
        raw = str(int(v))
        n_ztype = self._node_ztype(node)
        if n_ztype and n_ztype.name != "i64":
            ctype = TYPEMAP.get(n_ztype.name, "int64_t")
            if ctype != "int64_t":
                return f"(({ctype}){raw})"
        return raw

    def _emit_operation_value(self, op: zast.Operation) -> str:
        if self._node_const_value(op) is not None:
            return self._emit_const_value(op)
        if op.nodetype == NodeType.BINOP:
            return self._emit_binop_value(cast(zast.BinOp, op))
        if op.nodetype == NodeType.CALL:
            return self._emit_call_value(cast(zast.Call, op))
        if op.nodetype in (
            NodeType.ATOMID,
            NodeType.LABELVALUE,
            NodeType.ATOMSTRING,
            NodeType.EXPRESSION,
            NodeType.DOTTEDPATH,
        ):
            return self._emit_path_value(cast(zast.Path, op))
        return "0"

    def _emit_binop_value(self, binop: zast.BinOp) -> str:
        _binop_const = self.typing.node_const_value.get(binop.nodeid)
        if _binop_const is not None:
            return self._emit_const_value(binop)
        lhs = self._emit_operation_value(binop.lhs)
        rhs = self._emit_path_value(binop.rhs)
        # auto-deref boxed valtypes in binary operations
        if (
            binop.lhs.nodetype == NodeType.ATOMID
            and self._node_ztype(binop.lhs)
            and cast(ZType, self._node_ztype(binop.lhs)).is_box
        ):
            lhs = f"(*{lhs})"
        if (
            binop.rhs.nodetype == NodeType.ATOMID
            and self._node_ztype(binop.rhs)
            and cast(ZType, self._node_ztype(binop.rhs)).is_box
        ):
            rhs = f"(*{rhs})"
        op = binop.operator.name
        # route == and != through z_{name}_eq() for autogen equality types
        if op in ("==", "!=") and self._node_ztype(binop.lhs):
            lhs_zt = cast(ZType, self._node_ztype(binop.lhs))
            rhs_zt_obj = self._node_ztype(binop.rhs)
            rhs_sub = rhs_zt_obj.subtype if rhs_zt_obj else None
            eq_method = self.typing.child_of(lhs_zt, "==")
            if eq_method and eq_method.is_autogen_eq:
                tname = lhs_zt.name.replace(".", "_")
                call = f"z_{tname}_eq({lhs}, {rhs})"
                if op == "!=":
                    return f"(!{call})"
                return call
            # String / StringView content comparison. Delegate to
            # z_StringView_eq over views of both sides — this unifies
            # the four (S/SV × S/SV) cases and avoids reinterpreting a
            # 16-byte StringView as a 24-byte String (which silently
            # produced false at runtime under gcc 13's warning-only
            # incompatible-pointer-types).
            str_subs = (ZSubType.STRING, ZSubType.STRINGVIEW)
            if lhs_zt.subtype in str_subs and rhs_sub in str_subs:
                self.needs_stringview = True
                if lhs_zt.subtype == ZSubType.STRINGVIEW:
                    l_expr = lhs
                else:
                    l_expr = f"((z_StringView_t){{ {lhs}.data, {lhs}.size }})"
                if rhs_sub == ZSubType.STRINGVIEW:
                    r_expr = rhs
                else:
                    r_expr = f"((z_StringView_t){{ {rhs}.data, {rhs}.size }})"
                call = f"z_StringView_eq({l_expr}, {r_expr})"
                if op == "!=":
                    return f"(!{call})"
                return call
        # Ordering comparisons on string / stringview: route through the
        # shared cmp primitive. `z_{type}_cmp` returns -1 / 0 / 1 so the
        # four operators map to plain C comparisons against zero.
        if op in ("<", "<=", ">", ">=") and self._node_ztype(binop.lhs):
            if cast(ZType, self._node_ztype(binop.lhs)).subtype == ZSubType.STRING:
                self.needs_stdint = True
                cop = C_OPS.get(op, op)
                return f"(z_String_cmp(&{lhs}, &{rhs}) {cop} 0)"
            if cast(ZType, self._node_ztype(binop.lhs)).subtype == ZSubType.STRINGVIEW:
                self.needs_stdint = True
                self.needs_stringview = True
                cop = C_OPS.get(op, op)
                return f"(z_StringView_cmp({lhs}, {rhs}) {cop} 0)"
        cop = C_OPS.get(op, op)
        return f"({lhs} {cop} {rhs})"

    def _emit_path_value(self, path: zast.Path) -> str:
        if path.nodetype == NodeType.EXPRESSION:
            return self._emit_expression_value(cast(zast.Expression, path))
        if path.nodetype == NodeType.ATOMSTRING:
            return self._emit_string_value(cast(zast.AtomString, path))
        if path.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE):
            return self._emit_atomid_value(cast(zast.AtomId, path))
        if path.nodetype == NodeType.DOTTEDPATH:
            return self._emit_dotted_path_value(cast(zast.DottedPath, path))
        return "0"

    def _narrow_unwrap_expr(
        self,
        outer: ZType,
        subtype_name: str,
        child_id: int,
        subject_cexpr: str,
    ) -> Optional[str]:
        """Render the C unwrap expression for a match-arm-narrowed reference.

        Returns None when the subtype has a null payload (nothing to unwrap).
        """
        payload_type = self.typing.child_by_id(outer, child_id)
        if payload_type is None or payload_type.typetype == ZTypeType.NULL:
            return None
        if outer.typetype == ZTypeType.UNION:
            payload_ctype = _ctype(self.typing, payload_type)
            return f"(*({payload_ctype}*){subject_cexpr}.data)"
        if outer.typetype == ZTypeType.VARIANT:
            return f"{subject_cexpr}.data.{subtype_name}"
        return None

    def _narrow_alias_name(self, subject: zast.Operation) -> Optional[str]:
        """Zerolang-level name of the match subject, or None if not a bare name.

        Narrowing is only applied by the typechecker when the subject is a
        simple addressable name (ztypecheck.py `_check_case`); for anything
        else there is nothing to alias.
        """
        node: zast.Node = subject
        while node.nodetype == NodeType.EXPRESSION:
            node = cast(zast.Expression, node).expression
        if node.nodetype != NodeType.ATOMID:
            return None
        name = cast(zast.AtomId, node).name
        if _is_numeric_id(name):
            return None
        return name

    def _push_narrow_alias(
        self,
        alias_name: Optional[str],
        outer: ZType,
        subtype_name: str,
        child_id: int,
        subject_cexpr: str,
        parts: List[str],
        arm_indent: str,
    ) -> Callable[[], None]:
        """Seed `_alias_map` with the arm's unwrap expression and return a
        restore callback to run after the arm body.

        Routes arm-narrowed references through the same substitution path used
        by Phase B `with` / inline alias bindings. When aliasing doesn't apply
        (no bare-name subject, null-payload arm), returns a no-op restorer.
        """
        if alias_name is None:
            return lambda: None
        unwrap = self._narrow_unwrap_expr(outer, subtype_name, child_id, subject_cexpr)
        if unwrap is None:
            return lambda: None
        parts.append(f"{arm_indent}/* alias: {alias_name} => {unwrap} */\n")
        prev = self._alias_map.get(alias_name)
        self._alias_map[alias_name] = unwrap

        def restore() -> None:
            if prev is None:
                self._alias_map.pop(alias_name, None)
            else:
                self._alias_map[alias_name] = prev

        return restore

    def _emit_atomid_value(self, atom: zast.AtomId) -> str:
        name = atom.name
        if _is_numeric_id(name):
            return self._emit_numeric_literal(name)
        narrowed_subtype = self.typing.atom_narrowed_subtype.get(atom.nodeid)
        original_ztype = self.typing.atom_original_ztype.get(atom.nodeid)
        child_id = self.typing.atom_child_id.get(atom.nodeid, -1)
        atom_ztype = self._node_ztype(atom)
        # binding alias: substitute the source expression at the reference site
        # (set by alias-optimized `with`, inline .take/.borrow bindings, and
        # match-arm narrowed subjects). Chain lookups so a hoisted-arg synth
        # alias `_tN -> w` flows through any pre-existing narrowing alias
        # `w -> (*(z_T_t*)w.data)` rather than emitting the raw name.
        if name in self._alias_map:
            target = self._alias_map[name]
            seen = {name}
            while target.replace("_", "").isalnum() and target in self._alias_map:
                if target in seen:
                    break
                seen.add(target)
                target = self._alias_map[target]
            return target
        # Narrowing unwrap fallback: handles any AtomId stamped with
        # `narrowed_subtype` that wasn't seeded into `_alias_map` (e.g.
        # subjects not bound to a simple addressable name). Match-arm emission
        # seeds the alias map for the common case.
        if narrowed_subtype and original_ztype is not None:
            unwrap = self._narrow_unwrap_expr(
                original_ztype,
                narrowed_subtype,
                child_id,
                self._var_cname(atom) or _mangle_var(name),
            )
            if unwrap is not None:
                return unwrap
        # Honor typecheck's resolution: a reference stamped with a local
        # variable_id is a local — emit the bare (un-prefixed) name and skip
        # the unit-level name classification below. This is the local-scope-
        # first precedence the typechecker already applied; re-deriving it by
        # name here would lose to a unit-level namesake (e.g. a local that
        # shadows a `data` block).
        _vc = self._var_cname(atom)
        if _vc is not None:
            return _vc
        # Resolve the unit-level definition this name binds to by id (typecheck
        # stamped it); locals already returned above via atom_variable_id.
        udt = self.typing.atom_unit_def_type_id.get(atom.nodeid)
        resolved = _type_by_id(udt) if udt is not None else None
        tt = resolved.typetype if resolved else None
        if tt in (ZTypeType.FUNCTION, ZTypeType.DATA):
            if (
                tt == ZTypeType.FUNCTION
                and resolved is not None
                and resolved.cname
                and not resolved.is_native
            ):
                return resolved.cname
            return _mangle_func(name)
        if name in self._const_names:
            # Unit-level numeric constants are macros: inline the value
            # at every reference site (no backing static decl exists).
            inlined = self._inline_const_lookup(atom, name)
            if inlined is not None:
                return inlined
            return _mangle_func(name)
        # only match user-defined records (not numeric constant aliases like north: 0)
        if tt == ZTypeType.RECORD and resolved is not None and resolved.name == name:
            zero_args = self._zero_args_for_ctypes(name)
            return f"z_{name}_create({zero_args})"
        # string class: bare class name as value -> empty string constructor.
        # Disambiguator (`atom_ztype.name == name`) rejects variables of String
        # type — same idiom as the record case above.
        if (
            atom_ztype
            and atom_ztype.subtype == ZSubType.STRING
            and atom_ztype.name == name
        ):
            self.needs_string = True
            self.needs_stdlib = True
            return self._alloc_temp("z_String_create((uint64_t)0)")
        if tt == ZTypeType.CLASS and resolved is not None and resolved.name == name:
            self.needs_stdlib = True
            # Follow typedef wrappers (e.g. `bytes` → `list of: u8`)
            # so construction uses the base type's emitted `create`.
            # The wrapped collection's signature takes a single
            # capacity argument, not per-field zeroes.
            base = _unwrap_typedef(resolved)
            mangled = base.name if base is not None else name
            ctype = _ctype(self.typing, resolved)
            if (
                base is not None
                and base is not resolved
                and (
                    _is_list_type(base)
                    or _is_map_type(base)
                    or _is_set_type(base)
                    or _is_str_type(base)
                )
            ):
                create_args = "0"
            else:
                # Route through `_build_create_args` so classes with a
                # user-defined `create` (which may have fewer or
                # different params than the full field list -- e.g.
                # the synth class for a generator factory whose
                # `create` takes only the captured params, not the
                # internal `state`) get the right arg shape. Falls
                # back to the field-zero meta-create args for classes
                # without a user override.
                create_args, _ = self._build_create_args(mangled, resolved, [])
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(
                f"{indent}{ctype} {tmp} = z_{mangled}_create({create_args});\n"
            )
            if resolved.destructor_name is not None:
                self._temp.frees.append(tmp)
                self._temp.class_set[tmp] = mangled
            return tmp
        return _mangle_var(name)

    def _emit_numeric_literal(self, name: str) -> str:
        typename, value, err = parse_number(name)
        if err:
            return "0"
        self.needs_stdint = True
        if typename.startswith("f"):
            if typename != "f64":
                ctype = TYPEMAP.get(typename, "double")
                return f"(({ctype}){value})"
            return str(value)
        raw = str(int(value))
        if typename == "i64":
            return raw
        ctype = TYPEMAP.get(typename, "int64_t")
        return f"(({ctype}){raw})"

    def _emit_numeric_cast(self, val: str, src_type: str, dst_type: str) -> str:
        src_ctype = TYPEMAP.get(src_type, "int64_t")
        dst_ctype = TYPEMAP.get(dst_type, "int64_t")
        self.needs_stdint = True
        if dst_type not in NUMERIC_RANGES:
            return f"(({dst_ctype}){val})"
        dst_lo, dst_hi = NUMERIC_RANGES[dst_type]
        # When src is an integer with a statically-known range, omit
        # the half of the bounds check that the source type can never
        # reach — emitting `_v > LLONG_MAX` for an int8_t source would
        # otherwise produce comparisons outside the src type's range,
        # which gcc warns about and clang evaluates differently after
        # integer promotion (real divergent behavior was observed in
        # generic-unit emit on gcc 13 vs clang 18).
        needs_lower = True
        needs_upper = True
        if src_type in NUMERIC_RANGES:
            src_lo, src_hi = NUMERIC_RANGES[src_type]
            if src_lo >= dst_lo:
                needs_lower = False
            if src_hi <= dst_hi:
                needs_upper = False
        if not needs_lower and not needs_upper:
            # Widening cast — source range fully contained in dest;
            # no overflow possible, no runtime check needed.
            return f"(({dst_ctype}){val})"
        self.needs_stdio = True
        self.needs_stdlib = True
        parts: List[str] = []
        if needs_lower:
            parts.append(f"_v < {dst_lo}")
        if needs_upper:
            parts.append(f"_v > {dst_hi}")
        cond = " || ".join(parts)
        return (
            f"({{ {src_ctype} _v = {val}; "
            f"if ({cond})"
            f' z_panic("numeric cast overflow: {src_type} to {dst_type}"); '
            f"({dst_ctype})_v; }})"
        )

    def _extract_unit_path(self, path: zast.Path) -> Optional[str]:
        """If path resolves to an inline unit, return its dotted name. Otherwise None."""
        if path.nodetype == NodeType.ATOMID:
            path_def = self._unit_def_ztype(path)
            if path_def is not None and path_def.typetype == ZTypeType.UNIT:
                return cast(zast.AtomId, path).name
            return None
        if path.nodetype == NodeType.DOTTEDPATH:
            parent_path = self._extract_unit_path(cast(zast.DottedPath, path).parent)
            if parent_path is not None and self._dp_unit_type(path) is not None:
                return f"{parent_path}.{cast(zast.DottedPath, path).child.name}"
        return None

    def _emit_dotted_path_value(self, path: zast.DottedPath) -> str:
        # Step 4c: route decoration reads through the typed mirror.
        # `_pt_*` locals shadow the parsed `path.{type,const_value,child_id}`
        # field reads sprinkled through this method body; falls back to
        # parsed fields when the typed mirror is missing (e.g. a path
        # synthesized by the emitter or a test program built without
        # the full typecheck).
        _pt_ztype = self._node_ztype(path)
        _pt_const = self._node_const_value(path)
        _pt_child_id = self.typing.dp_child_id.get(path.nodeid, -1)
        child = path.child.name

        # .take emits just the variable value (nullification handled at call site)
        if child == "take":
            return self._emit_path_value(path.parent)

        # .lock emits just the variable value (locking handled at type-check time)
        if child == "lock":
            return self._emit_path_value(path.parent)

        # .private emits just the variable value (access control at type-check time)
        if child == "private":
            return self._emit_path_value(path.parent)

        # Null-arm union/variant construction (no-args form like
        # `io.IoError.notfound` or `io.openmode.read`). The
        # typechecker stamps `dp_parent_tagged_type` on any dotted
        # path whose parent resolves to a tagged type, regardless of
        # whether the parent itself is an ATOMID (2-segment) or a
        # DOTTEDPATH (3-segment cross-unit). Trusting the stamp here
        # is the analogue of the UNION_CREATE early-return in
        # `_is_union_construction` for the call-with-args path.
        parent_tagged = self.typing.dp_parent_tagged_type.get(path.nodeid)
        if parent_tagged is not None:
            if parent_tagged.typetype == ZTypeType.UNION:
                return self._emit_union_null_construction(parent_tagged, child)
            if parent_tagged.typetype == ZTypeType.VARIANT:
                return self._emit_variant_null_construction(parent_tagged, child)

        if path.parent.nodetype == NodeType.ATOMID:
            pname = cast(zast.AtomId, path.parent).name
            # The unit/core definition the parent binds to (by id), or None
            # when the parent is a local — typecheck's binding decision.
            parent_def = self._unit_def_ztype(path.parent)
            # numeric dotted path: 0.u32, 42.i8, 0xff.u16
            if _is_numeric_id(pname):
                child_name = path.child.name
                ctype = TYPEMAP.get(child_name, "int64_t")
                self.needs_stdint = True
                typename, value, err = parse_number(pname + child_name)
                if err:
                    return "0"
                if typename.startswith("f"):
                    return f"(({ctype}){value})"
                return f"(({ctype}){int(value)})"
            # Zero-arg native unit-level functions accessed as a bare
            # dotted path: the typechecker coerces their type to the
            # return type (so `w: io.stdout` binds w to a writer, not a
            # function pointer). The emitter must match by inserting
            # `()` here; otherwise the generic path handler below emits
            # a bare function name, yielding invalid C. Covers io.stdin/
            # stdout/stderr as well as os.args and any future
            # analogous helpers.
            if (
                pname in self.program.units
                and _pt_ztype is not None
                and _pt_ztype.typetype != ZTypeType.FUNCTION
            ):
                unit_body = self.program.units[pname].body
                child_defn = unit_body.get(child)
                child_is_native = (
                    cast(zast.Function, child_defn).is_native
                    if child_defn is not None
                    and child_defn.nodetype == NodeType.FUNCTION
                    else False
                )
                if (
                    child_defn is not None
                    and child_defn.nodetype == NodeType.FUNCTION
                    and child_is_native
                ):
                    fn_type = self._node_ztype(child_defn) or self._node_ztype(
                        child_defn
                    )
                    has_runtime_params = False
                    if fn_type is not None:
                        for p in self.typing.child_names_of(fn_type):
                            if p != "this":
                                has_runtime_params = True
                                break
                    if not has_runtime_params:
                        # Always a native (child_is_native guard above); native
                        # cnames don't match the qualified runtime symbol.
                        mangled = _mangle_func(f"{pname}.{child}")
                        self._track_stdlib_unit_native(mangled, fn_type)
                        if pname == "io":
                            self.needs_stdio = True
                        return f"{mangled}()"
            # unit.name reference (file-level units)
            if pname in self.program.units and pname not in (
                "system",
                "core",
                "io",
                "os",
            ):
                return _mangle_func(f"{pname}.{child}")
            # inline unit.name reference
            if parent_def is not None and parent_def.typetype == ZTypeType.UNIT:
                qname = f"{pname}.{child}"
                # check if the child is itself a unit (nested)
                if self._dp_unit_type(path) is not None:
                    # will be resolved by further dotted path traversal
                    return _mangle_func(qname)
                # Unit-level numeric constant referenced via the unit:
                # inline the value (macro semantics; no backing decl).
                if qname in self._const_names:
                    child_defn = self._find_inline_unit_member(pname, child)
                    if child_defn is not None:
                        inlined = self._inline_const_via_defn(child_defn)
                        if inlined is not None:
                            return inlined
                return _mangle_func(qname)
            # record_name.method or class_name.method — method call with no
            # extra args. Only fire when `pname` resolves to the type itself
            # (e.g. `myclass.method`), not a variable that happens to have
            # class / record type — variables flow to the per-type dispatch
            # branches below so zero-arg methods like `list.listview` emit
            # as method calls instead of bare field accesses.
            # `parent_def is not None` already means the parent names the type
            # itself (a variable carries atom_variable_id, not this stamp), so
            # it stands in for the old `resolved.name == pname` disambiguator.
            ptt = parent_def.typetype if parent_def is not None else None
            if ptt in (ZTypeType.RECORD, ZTypeType.CLASS) and parent_def is not None:
                # Only a method (FUNCTION child) has a usable function cname; an
                # `as`-section constant resolves to its value type, whose cname
                # is a struct name — fall back to the qualified mangle there.
                method_ct = self.typing.child_of(parent_def, child)
                return (
                    method_ct.cname
                    if method_ct is not None
                    and method_ct.typetype == ZTypeType.FUNCTION
                    and method_ct.cname
                    and not method_ct.is_native
                    else _mangle_func(f"{pname}.{child}")
                )
            # union_name.subtype — emit null subtype construction. Use the
            # type's dot-free ztype.name (not the bare atom `pname`) so a
            # dependency-unit variant/union emits a valid C name.
            if parent_def is not None and ptt == ZTypeType.UNION:
                return self._emit_union_null_construction(parent_def, child)
            # variant_name.subtype — emit null subtype construction
            if parent_def is not None and ptt == ZTypeType.VARIANT:
                return self._emit_variant_null_construction(parent_def, child)
            # data.index call. Use the data type's dot-free ztype.name
            # (parent_def.name), not the bare atom pname, so a dependency
            # unit's block emits its qualified array cname.
            if parent_def is not None and ptt == ZTypeType.DATA and child == "index":
                return _mangle_func(parent_def.name)
            # data.LABEL — compile-time substitution for named items.
            # data.N — array index access for ordinal lookups.
            if parent_def is not None and ptt == ZTypeType.DATA:
                labels = self._data_label_values.get(parent_def.name)
                if labels is not None and child in labels:
                    return labels[child]
                if child.isdigit():
                    return f"{_mangle_func(parent_def.name)}[{child}]"

        # check if parent resolves to a nested inline unit path
        unit_path = self._extract_unit_path(path.parent)
        if unit_path is not None:
            return _mangle_func(f"{unit_path}.{child}")

        # array: numeric index access (a.0 → a.data[0])
        parent_type_dp = self._node_ztype(path.parent)
        if parent_type_dp and _is_array_type(parent_type_dp) and child.isdigit():
            parent = self._emit_path_value(path.parent)
            return f"{parent}.data[{child}]"
        # array: .length constant access
        if parent_type_dp and _is_array_type(parent_type_dp) and child == "length":
            return f"z_{parent_type_dp.name}_length"
        # str: .length field access
        if parent_type_dp and _is_str_type(parent_type_dp) and child == "length":
            parent = self._emit_path_value(path.parent)
            return f"{parent}.len"
        # str: .size constant access
        if parent_type_dp and _is_str_type(parent_type_dp) and child == "size":
            return f"z_{parent_type_dp.name}_size"
        # str: .string conversion (str -> z_String_t*)
        if parent_type_dp and _is_str_type(parent_type_dp) and child == "string":
            parent = self._emit_path_value(path.parent)
            result = f"z_{parent_type_dp.name}_string({parent})"
            return self._alloc_temp(result)
        # stringview: .length field access
        if parent_type_dp and _is_stringview_type(parent_type_dp) and child == "length":
            self.needs_stringview = True
            parent = self._emit_path_value(path.parent)
            return f"{parent}.length"
        # stringview: .string conversion (stringview -> z_String_t*)
        if parent_type_dp and _is_stringview_type(parent_type_dp) and child == "string":
            self.needs_stringview = True
            self.needs_stdlib = True
            parent = self._emit_path_value(path.parent)
            result = f"z_String_from_view({parent})"
            return self._alloc_temp(result)
        # string literal .string: literal is a z_StringView_t constant;
        # .string creates an owned heap copy via z_String_from_view
        if (
            child == "string"
            and path.parent.nodetype == NodeType.ATOMSTRING
            and not any(
                p.nodetype != zast.NodeType.STRINGCHUNK
                for p in cast(zast.AtomString, path.parent).stringparts
            )
        ):
            self.needs_stringview = True
            self.needs_string = True
            self.needs_stdlib = True
            sname = self._emit_path_value(path.parent)
            result = f"z_String_from_view({sname})"
            return self._alloc_temp(result)
        # string: .string identity (no-op for already-owned strings)
        if (
            parent_type_dp
            and parent_type_dp.subtype == ZSubType.STRING
            and child == "string"
        ):
            return self._emit_path_value(path.parent)
        # string: .length field access
        if (
            parent_type_dp
            and parent_type_dp.subtype == ZSubType.STRING
            and child == "length"
        ):
            parent = self._emit_path_value(path.parent)
            acc = "->" if self._is_class_pointer_path(path.parent) else "."
            return f"{parent}{acc}size"
        # string: .copy — deep copy producing a fresh owned string
        if (
            parent_type_dp
            and parent_type_dp.subtype == ZSubType.STRING
            and child == "copy"
        ):
            self.needs_string = True
            self.needs_stdlib = True
            parent = self._emit_path_value(path.parent)
            arg = parent if self._is_class_pointer_path(path.parent) else f"&{parent}"
            return self._alloc_temp(f"z_String_copy({arg})")
        # string: .hash — SipHash-1-3 of the byte contents (per-process seed)
        # fmt: off
        if (
            parent_type_dp
            and parent_type_dp.subtype == ZSubType.STRING
            and child == "hash"  # ztc-string-compare-ok: stdlib mthd
        ):
            self.needs_string = True
            self.needs_hash = True
            parent = self._emit_path_value(path.parent)
            arg = f"*{parent}" if self._is_class_pointer_path(path.parent) else parent
            return f"z_siphash_string({arg})"
        # stringview: .hash — SipHash-1-3 of the viewed bytes (per-process seed)
        if parent_type_dp and _is_stringview_type(parent_type_dp) and child == "hash":  # ztc-string-compare-ok: stdlib mthd
            # fmt: on
            self.needs_stringview = True
            self.needs_hash = True
            parent = self._emit_path_value(path.parent)
            return f"z_siphash_stringview({parent})"
        # .stringview conversion at path-access position, for both `string`
        # (reftype) and `str_N` (valtype). Only str differs in the length
        # field name (`len` vs `size`); pointer-vs-value access depends on
        # whether the parent path traverses a class pointer.
        if (
            parent_type_dp
            and child == "stringview"
            and (
                parent_type_dp.subtype == ZSubType.STRING
                or _is_str_type(parent_type_dp)
            )
        ):
            parent = self._emit_path_value(path.parent)
            is_pointer_path = (
                parent_type_dp.subtype == ZSubType.STRING
                and self._is_class_pointer_path(path.parent)
            )
            return self._emit_stringview_value(
                parent, parent_type_dp, is_pointer_path, None, None
            )
        # list: .length field access
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "length":
            parent = self._emit_path_value(path.parent)
            acc = "->" if self._is_class_pointer_path(path.parent) else "."
            return f"{parent}{acc}length"
        # list: .capacity field access
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "capacity":
            parent = self._emit_path_value(path.parent)
            acc = "->" if self._is_class_pointer_path(path.parent) else "."
            return f"{parent}{acc}capacity"
        # listview: .length field access
        if parent_type_dp and _is_listview_type(parent_type_dp) and child == "length":
            parent = self._emit_path_value(path.parent)
            acc = "->" if self._is_class_pointer_path(path.parent) else "."
            return f"{parent}{acc}length"
        # Zero-arg class method accessed as a path value. Mirrors the
        # typechecker coercion in ztypecheck._resolve_dotted_child: a
        # zero-arg method on a concrete class resolves to its return
        # type here rather than a function-pointer, so we must emit a
        # call. Matches the call-form dispatch in _emit_call_value.
        #
        # Parent may be either a direct class local or a union subtype
        # selection `r.ok` whose resolved subtype is the class — in
        # the latter case `self._node_ztype(path.parent)` is the enclosing union,
        # handled below via _effective_class_zero_arg_method.
        cls_name, method_fn = self._effective_class_zero_arg_method(path)
        if cls_name is not None and method_fn is not None:
            # Discriminate: a real method `obj.method1` has the
            # resolved method type named `<cls>.method1`. A
            # function-pointer field defaulted to a sibling method
            # (`instancemethod: method1`) resolves to the SAME
            # method type — but accessed via a different path
            # child name. In that case lower as an indirect call
            # through the struct slot, not a direct call to a
            # non-existent `z_<cls>_<field>` function.
            if method_fn.name != f"{cls_name}.{child}":  # ztc-string-compare-ok: emit field vs method
                parent = self._emit_path_value(path.parent)
                is_ptr = self._is_class_pointer_path(path.parent)
                acc = "->" if is_ptr else "."
                field_expr = parent[1:] if parent[:1] == "&" else parent
                if self._lhs_mode:
                    # reassignment LHS — emit slot access only
                    return f"{field_expr}{acc}{child}"
                recv = parent if is_ptr else f"&{field_expr}"
                return f"{field_expr}{acc}{child}({recv})"
            parent = self._emit_path_value(path.parent)
            if not self._is_class_pointer_path(path.parent) and not parent.startswith(
                "&"
            ):
                parent = f"&{parent}"
            self._record_io_native_for_class_method(cls_name, child)
            # bytes/byteview are transparent typedefs over list/listview of u8
            # (lib/system/system.z:586-590). bytes.byteview is the same C-level
            # operation as list_u8.listview, so route through the existing
            # z_List_u8_listview helper auto-emitted by _emit_mono_listview.
            if cls_name == "Bytes" and child == "byteview":
                return f"z_List_u8_listview({parent})"
            return f"z_{cls_name}_{child}({parent})"

        # io.file: protocol projection. `f.closer` / `f.seeker` emit
        # a call to the matching `z_File_<proto>_create` wrapper
        # (borrowed — no copy, no destroy). `reader` / `writer` are
        # held back until the vtable collection-param ABI mismatch
        # is resolved; they typecheck but don't project yet.
        if child in self._IO_FILE_WRAPPABLE_PROTOCOLS and (
            self._effective_file_type(path.parent)
        ):
            parent = self._emit_path_value(path.parent)
            if not self._is_class_pointer_path(path.parent) and not parent.startswith(
                "&"
            ):
                parent = f"&{parent}"
            return f"z_File_{child}_create({parent})"

        # stringview: zero-arg query methods (Phase S1 / S2). These
        # need a call emission — the field-access path below would
        # produce `sv.is_empty` which is not a struct field.
        if (
            parent_type_dp
            and _is_stringview_type(parent_type_dp)
            and child
            in (
                "isEmpty",
                "isAscii",
                "trim",
                "trimStart",
                "trimEnd",
                "lines",
                "toLowerAscii",
                "toUpperAscii",
                "count",
                "codepoints",
                "parseI64",
                "parseU64",
                "parseF64",
            )
        ):
            self.needs_stringview = True
            self.needs_string = True
            self.needs_stringview_natives.add(child)
            # strtod + errno (ERANGE) — pull in errno.h / stdlib.h.
            method_ztype = self.typing.child_of(parent_type_dp, child)
            if (
                method_ztype is not None
                and method_ztype.builtin_func == ZBuiltinFunc.PARSE_F64
            ):
                self.needs_io = True
            parent = self._emit_path_value(path.parent)
            if not parent.startswith("&"):
                parent = f"&{parent}"
            return f"z_StringView_{child}({parent})"
        # list: .pop as dotted path (zero-arg method call)
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "pop":
            parent = self._emit_path_value(path.parent)
            if not self._is_class_pointer_path(path.parent) and not parent.startswith(
                "&"
            ):
                parent = f"&{parent}"
            return f"z_{_mono_name(parent_type_dp)}_pop({parent})"
        # list: .listview as dotted path (zero-arg method call)
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "listview":
            parent = self._emit_path_value(path.parent)
            if not self._is_class_pointer_path(path.parent) and not parent.startswith(
                "&"
            ):
                parent = f"&{parent}"
            return f"z_{_mono_name(parent_type_dp)}_listview({parent})"
        # map: .length field access
        if parent_type_dp and _is_map_type(parent_type_dp) and child == "length":
            parent = self._emit_path_value(path.parent)
            return f"{parent}->length"
        # map: .capacity field access
        if parent_type_dp and _is_map_type(parent_type_dp) and child == "capacity":
            parent = self._emit_path_value(path.parent)
            return f"{parent}->capacity"
        # set: .length / .capacity field access
        if (
            parent_type_dp
            and _is_set_type(parent_type_dp)
            and child in ("length", "capacity")
        ):
            parent = self._emit_path_value(path.parent)
            return f"{parent}->{child}"
        # data.array: copy data into new array
        if (
            parent_type_dp
            and parent_type_dp.typetype == ZTypeType.DATA
            and child == "array"
        ):
            arr_type = _pt_ztype
            if arr_type and _is_array_type(arr_type):
                arr_len = _array_length(self.typing, arr_type)
                arr_ctype = _ctype(self.typing, arr_type)
                parent = self._emit_path_value(path.parent)
                tmp = self._temp_name("da")
                indent = self._indent()
                self._temp.decls.append(
                    f"{indent}{arr_ctype} {tmp};\n"
                    f"{indent}for (int64_t _i = 0; _i < {arr_len}; _i++) "
                    f"{{ {tmp}.data[_i] = {parent}[_i]; }}\n"
                )
                return tmp
        # check if the dotted path resolves to a constant (from 'as' section)
        if _pt_ztype and _pt_ztype.const_value is not None:
            parent_type_dp = self._node_ztype(path.parent)
            if parent_type_dp:
                const_qname = f"{parent_type_dp.name}.{child}"
                if const_qname in self._const_names:
                    return _mangle_func(const_qname)

        # protocol method call via path (zero-arg form). When the
        # parent is a protocol value and the child names a spec, emit
        # `proto.vtable->spec(proto.data)`. Method calls with args go
        # through _emit_protocol_dispatch on the Call node. This path
        # form handles cases where a zero-arg spec appears as an
        # rvalue (assignment, return, condition) — `c.close`.
        # Must run before the generic FUNCTION-typed dotted-path
        # branch below, which would otherwise emit a function name
        # like `z_Closer_close` (no such free function exists — the
        # dispatch is vtable-based).
        if (
            self._node_ztype(path.parent)
            and cast(ZType, self._node_ztype(path.parent)).typetype
            == ZTypeType.PROTOCOL
        ):
            parent_type_p = cast(ZType, self._node_ztype(path.parent))
            # Id-only child lookup — PROTOCOL parent is always stamped.
            spec = self.typing.child_by_id(parent_type_p, _pt_child_id)
            if spec is not None and spec.typetype == ZTypeType.FUNCTION:
                parent = self._emit_path_value(path.parent)
                acc = "->" if self._is_class_pointer_path(path.parent) else "."
                return f"{parent}{acc}vtable->{child}({parent}{acc}data)"

        # check if the dotted path resolves to a function (method call or
        # field access). Two shapes hit this branch:
        #   - _pt_ztype is FUNCTION directly (legacy: only happens now
        #     when we explicitly opted out of auto-call coercion in the
        #     typechecker — i.e. `_pt_ztype` was stamped via
        #     `_check_path(..., coerce_method_to_return=False)` from
        #     `_check_call`).
        #   - _pt_ztype is the method's return type and the parent's
        #     child by this name is a FUNCTION (the post-`546f7fd`
        #     auto-call coercion in `_check_dotted_path`). For value-
        #     position dotted paths (`p1.distance` inside a string
        #     interpolation, or `v: s.stringview`), the typechecker
        #     coerced _pt_ztype to the return type — but the path is
        #     still semantically a no-arg method call and must lower
        #     as one.
        method_type: "ZType | None" = None
        if _pt_ztype and _pt_ztype.typetype == ZTypeType.FUNCTION:
            method_type = _pt_ztype
        elif (
            self._node_ztype(path.parent)
            and cast(ZType, self._node_ztype(path.parent)).typetype
            in (ZTypeType.CLASS, ZTypeType.RECORD, ZTypeType.UNION)
            # Numeric casts (`x.u32`) lower to a C cast, not a method
            # call, even though the typechecker now coerces the dotted
            # path to the cast target's type. Defer to the dedicated
            # numeric-cast branch below.
            and child not in NUMERIC_CAST_TYPES
            # Native classes (string, list, map, ...) declare zero-arg
            # methods like `length`, `capacity` for typechecking
            # convenience but lower to struct-field access at emit time
            # (`s->length`, not `z_String_length(s)`). Skip the
            # method-call dispatch for them — the same reason
            # `_effective_class_zero_arg_method` returns None for
            # `is_native` classes.
            and not cast(ZType, self._node_ztype(path.parent)).is_native
        ):
            cand = self.typing.child_of(
                cast(ZType, self._node_ztype(path.parent)), child
            )
            if (
                cand is not None
                and cand.typetype == ZTypeType.FUNCTION
                and (
                    cand.return_type is _pt_ztype
                    # Methods with no explicit `out` clause have
                    # `return_type == None`; the typechecker's
                    # auto-call coercion stamps `_pt_ztype` to the
                    # null singleton in that case. Treat both shapes
                    # the same so the path lowers as a method call.
                    or (
                        cand.return_type is None
                        and _pt_ztype is not None
                        and _pt_ztype.typetype == ZTypeType.NULL
                    )
                )
            ):
                method_type = cand
        if method_type is not None:
            func_name = method_type.name  # e.g. "calculator.op" or "point.distance"
            # function pointer fields (from 'is' section) → struct field access
            if func_name in self._is_func_fields:
                parent = self._emit_path_value(path.parent)
                if self._is_class_pointer_path(path.parent):
                    return f"{parent}->{child}"
                return f"{parent}.{child}"
            # regular methods (from 'as' section) → method call
            parent = self._emit_path_value(path.parent)
            # stack-allocated class: wrap with & for this pointer
            parent_type_m = self._node_ztype(path.parent)
            if (
                parent_type_m
                and not parent_type_m.is_heap_allocated
                and parent_type_m.typetype == ZTypeType.CLASS
                and not parent.startswith("&")
                and not self._is_class_pointer_path(path.parent)
            ):
                parent = f"&{parent}"
            method_cn = (
                method_type.cname
                if method_type.cname and not method_type.is_native
                else _mangle_func(func_name)
            )
            return f"{method_cn}({parent})"

        # protocol instance creation: obj.label where label maps to a
        # protocol conformance (synthesize a protocol value via
        # z_<type>_<label>_create). Must NOT fire for regular fields
        # whose declared type happens to be a protocol (`source:
        # reader.lock` on a class) — those need a plain struct-field
        # access which falls through to the general path below.
        if _pt_ztype and _pt_ztype.typetype == ZTypeType.PROTOCOL:
            parent_type = self._node_ztype(path.parent)
            if (
                parent_type
                and parent_type.typetype in (ZTypeType.RECORD, ZTypeType.CLASS)
                and self._proto_conformance.get((parent_type.name, _pt_ztype.name))
                == child
            ):
                self.needs_stdlib = True
                parent_val = self._emit_path_value(path.parent)
                conf = self._conformance_of(parent_type, _pt_ztype, child)
                create_name = (
                    conf.create_cname
                    if conf
                    else f"z_{parent_type.name}_{child}_create"
                )
                # pass address for stack-allocated types
                if parent_type.is_valtype or not parent_type.is_heap_allocated:
                    arg = f"&{parent_val}"
                else:
                    arg = parent_val
                tmp = self._temp_name("p")
                indent = self._indent()
                proto_ctype = _cname_of(_pt_ztype, _pt_ztype.name)
                # stack-allocate: protocol struct is now stack-based
                self._temp.decls.append(
                    f"{indent}{proto_ctype} {tmp} = {create_name}({arg});\n"
                )
                self._temp.frees.append(tmp)
                self._temp.proto_set[tmp] = _cbase_of(_pt_ztype, _pt_ztype.name)
                return tmp

        # runtime numeric cast: x.u32 where x is a numeric variable
        if child in NUMERIC_CAST_TYPES:
            parent_type = self._node_ztype(path.parent)
            if parent_type and parent_type.name in TYPEMAP:
                parent_val = self._emit_path_value(path.parent)
                return self._emit_numeric_cast(parent_val, parent_type.name, child)
            # box(numeric): auto-deref then cast
            if parent_type and parent_type.is_box:
                inner_type = self.typing.generic_arg_of(parent_type, "t")
                if inner_type and inner_type.name in TYPEMAP:
                    parent_val = self._emit_path_value(path.parent)
                    return self._emit_numeric_cast(parent_val, inner_type.name, child)

        parent = self._emit_path_value(path.parent)
        # use -> for class instances (pointer types)
        if self._is_class_pointer_path(path.parent):
            return f"{parent}->{child}"
        # variant payload access: v.subname → v.data.subname
        parent_type = self._node_ztype(path.parent)
        if parent_type and parent_type.typetype == ZTypeType.VARIANT:
            # Id-only lookup — typecheck stamps child_id on every DottedPath
            # with a known parent_type, so we should never see -1 here.
            child_type = self.typing.child_by_id(parent_type, _pt_child_id)
            if child_type and child_type.typetype != ZTypeType.FUNCTION:
                return f"{parent}.data.{child}"
        # union payload access: u.subname → *(T*)u.data (heap-boxed)
        # Non-null subtypes are stored as malloc'd boxes behind a void*
        # data pointer; deref and cast to T. Null subtypes have no
        # payload and should not be accessed this way — the typechecker
        # rejects it.
        if parent_type and parent_type.typetype == ZTypeType.UNION:
            child_type = self.typing.child_by_id(parent_type, _pt_child_id)
            if (
                child_type
                and child_type.typetype != ZTypeType.FUNCTION
                and child_type.typetype != ZTypeType.NULL
            ):
                inner_ctype = _ctype(self.typing, child_type)
                return f"(*({inner_ctype}*){parent}.data)"
        return f"{parent}.{child}"

    def _effective_file_type(self, path: zast.Path) -> bool:
        """True if `path` resolves (semantically) to an io.file value.

        A file can appear in two AST shapes:
          * Direct path (local of class type) — `self._node_ztype(path)` is the
            file class.
          * Union subtype selection — `self._node_ztype(path)` is the enclosing
            union (per the typechecker's parent_tagged_type rule); the
            selected child is the real file. E.g. `r.ok` where
            `r: result(file, ioerror)`.
        """
        pt = self._node_ztype(path)
        if pt and pt.typetype == ZTypeType.CLASS and pt.name == "File":
            return True
        if (
            pt
            and pt.typetype == ZTypeType.UNION
            and path.nodetype == NodeType.DOTTEDPATH
        ):
            dp = cast(zast.DottedPath, path)
            sub = self.typing.child_of(pt, dp.child.name)
            if sub and sub.typetype == ZTypeType.CLASS and sub.name == "File":
                return True
        return False

    def _effective_class_type(
        self, path: zast.Path, class_name: "str | None" = None
    ) -> "ZType | None":
        """Return the concrete class ZType `path.parent` resolves to,
        or None. Handles both direct class locals and union-subtype
        selections (see `_effective_file_type` for the latter pattern).

        If `class_name` is given, only return a match for that class.
        """
        pt = self._node_ztype(path)
        if pt and pt.typetype == ZTypeType.CLASS:
            if class_name is None or pt.name == class_name:
                return pt
        if (
            pt
            and pt.typetype == ZTypeType.UNION
            and path.nodetype == NodeType.DOTTEDPATH
        ):
            dp = cast(zast.DottedPath, path)
            sub = self.typing.child_of(pt, dp.child.name)
            if sub and sub.typetype == ZTypeType.CLASS:
                if class_name is None or sub.name == class_name:
                    return sub
        return None

    def _effective_class_zero_arg_method(
        self, path: zast.DottedPath
    ) -> "tuple[str | None, ZType | None]":
        """If `path` resolves to a zero-arg method on a concrete
        non-native class, return (class_name, method_type). Otherwise
        (None, None).

        Mirrors the typechecker coercion in
        ztypecheck._resolve_dotted_child, but excludes classes marked
        `is native` (string, list, listview, map, ...). Native classes
        declare `.length`/`.capacity`/etc. as 0-arg `is native` methods
        in system.z / collections.z for typechecking convenience, but
        at emit time those resolve to struct-field access
        (`s->capacity`) rather than a C function call — routing them
        through this branch would produce calls to non-existent
        `z_String_capacity` symbols. Field-access emission happens in
        the fallthrough path at the end of _emit_dotted_path_value.
        """
        cls = self._effective_class_type(path.parent)
        if cls is None:
            return (None, None)
        if cls.is_native:
            return (None, None)
        method = self.typing.child_of(cls, path.child.name)
        if method is None or method.typetype != ZTypeType.FUNCTION:
            return (None, None)
        if method.return_type is None:
            return (None, None)
        recv = method.this_param_name
        for p in self.typing.child_names_of(method):
            if p != "this" and p != recv:
                return (None, None)
        return (cls.name, method)

    _IO_CLASS_METHOD_NATIVES: "dict[tuple[str, str], str]" = {
        ("File", "close"): "file_close",
        ("BufWriter", "flush"): "bufwriter_flush",
        ("TextWriter", "flush"): "textwriter_flush",
    }

    def _record_io_native_for_class_method(
        self, class_name: str, method_name: str
    ) -> None:
        """Populate needs_io_natives for zero-arg methods on io-provided
        classes so emit_runtime_io emits the corresponding C bodies.
        No-op for user classes — their method bodies are emitted via
        the normal _emit_function path and don't need runtime
        registration."""
        native = self._IO_CLASS_METHOD_NATIVES.get((class_name, method_name))
        if native is None:
            return
        self.needs_io = True
        if native == "file_close":
            self.needs_stdio = True
        self.needs_io_natives.add(native)

    def _is_class_pointer_path(self, path: zast.Path) -> bool:
        """Check if a path refers to a pointer type (for -> vs . dispatch).

        Returns True for heap-allocated types (union, protocol, string, box)
        and for method 'this' parameters (tracked in class_params).
        Stack-allocated class locals use '.' — only 'this' in methods uses '->'.
        """
        # type annotation from type checker — only heap-allocated types are pointers
        parent_type = self._node_ztype(path)
        # heap-allocated types (string, box) are always pointers
        if parent_type and parent_type.is_heap_allocated:
            return True
        # .lock field of stack-allocated class type: stored as pointer
        if (
            path.nodetype == NodeType.DOTTEDPATH
            and parent_type
            and parent_type.typetype == ZTypeType.CLASS
            and not parent_type.is_heap_allocated
        ):
            dp = cast(zast.DottedPath, path)
            grandparent_type = self._node_ztype(dp.parent)
            if grandparent_type and self.typing.is_child_lock_field(
                grandparent_type, dp.child.name
            ):
                return True
        # local heap-allocated variable tracked for cleanup — check every
        # active block frame, not just the innermost, so a var declared
        # in an outer scope is still recognised inside a nested body.
        if path.nodetype == NodeType.ATOMID:
            vid = self.typing.atom_variable_id.get(path.nodeid)
            for frame in self._scope.cleanup_stack:
                for var_id, vtype in frame:
                    if var_id == vid and vtype.is_heap_allocated:
                        return True
            # method 'this' parameter (class) is a pointer
            if vid is not None and vid in self._scope.class_params:
                return True
        return False

    def _emit_class_free(self, var: str, type_name: Optional[str]) -> str:
        """Emit the right destroy call for a class variable (stack-allocated).

        `type_name` is the type's display name as tracked in `class_set`; dots
        from a dependency/inline-unit qualifier are mangled so the destructor
        identifier matches the dot-free name emitted at its definition site."""
        if type_name:
            return f"z_{type_name.replace('.', '_')}_destroy(&{var});"
        return ""

    def _narrowed_alias_expr(self, value: "zast.Expression") -> "Optional[str]":
        """If `value` is `<narrowed_atomid>.take` (or `.borrow`),
        return the emitter's AtomId lowering of that AtomId (which
        includes the payload-unwrap), suitable to seed _alias_map.
        Otherwise return None so the caller falls back to the string
        alias path.
        """
        inner = value.expression if value.nodetype == NodeType.EXPRESSION else value
        if inner is None or inner.nodetype != NodeType.DOTTEDPATH:
            return None
        dp = cast(zast.DottedPath, inner)
        if dp.child.name not in ("take", "borrow"):
            return None
        if dp.parent.nodetype != NodeType.ATOMID:
            return None
        atom = cast(zast.AtomId, dp.parent)
        narrowed = self.typing.atom_narrowed_subtype.get(atom.nodeid)
        original = self.typing.atom_original_ztype.get(atom.nodeid)
        if narrowed is None or original is None:
            return None
        return self._emit_atomid_value(atom)

    def _emit_stringview_value(
        self,
        parent_val: str,
        parent_type: ZType,
        is_pointer_path: bool,
        from_val: Optional[str],
        to_val: Optional[str],
    ) -> str:
        """Build a `z_StringView_t` literal from a string or str operand.

        `string` exposes `size`; `str_N` exposes `len`. Field access is via
        `->` when reading through a class-pointer path, `.` otherwise.
        """
        self.needs_stringview = True
        is_string = parent_type.subtype == ZSubType.STRING
        acc = "->" if is_pointer_path else "."
        len_field = "size" if is_string else "len"
        data_access = f"{parent_val}{acc}data"
        len_access = f"{parent_val}{acc}{len_field}"
        if from_val is None or to_val is None:
            return f"(z_StringView_t){{ {data_access}, {len_access} }}"
        self.needs_stdlib = True
        self.needs_stdio = True
        indent = self._indent()
        self._temp.decls.append(
            f"{indent}if ((uint64_t){from_val} > {len_access}"
            f" || (uint64_t){to_val} > {len_access}"
            f" || (uint64_t){from_val} > (uint64_t){to_val})"
            f' z_panic("stringview: bounds error");\n'
        )
        return (
            f"(z_StringView_t){{ {data_access}"
            f" + (uint64_t){from_val},"
            f" (uint64_t){to_val} - (uint64_t){from_val} }}"
        )

    def _alias_c_expr(self, path: str) -> str:
        """Render a zerolang-level alias path (e.g. `r.f.g`) as a C
        expression. Only the root component is mangled; field names after
        a dot are passed through unchanged (they must be valtype-field
        accesses — the type checker rejects reftype pointer hops).
        """
        parts = path.split(".")
        out = _mangle_var(parts[0])
        for p in parts[1:]:
            out = f"{out}.{p}"
        return out

    def _emit_take_invalidation(
        self, var: str, ztype: Optional[ZType], indent: str
    ) -> str:
        """Emit invalidation code for a variable after .take.

        For heap-allocated types (union, protocol): set to NULL.
        For stack-allocated types (class, string): zero-initialize the struct.
        """
        if ztype and not ztype.is_heap_allocated:
            ct = _ctype(self.typing, ztype)
            return f"{indent}{var} = ({ct}){{0}};\n"
        return f"{indent}{var} = NULL;\n"

    def _push_block_scope(self) -> None:
        """Enter a new block body (if/else/for/do/with/match-arm).

        Subsequent `cleanup_vars.append(...)` lands on this frame.
        Pair with `_pop_block_scope_and_emit()` at block close.
        """
        self._scope.cleanup_stack.append([])

    def _pop_block_scope_and_emit(self) -> str:
        """Close the topmost block scope; return destructors for its locals.

        Vars are destroyed in reverse declaration order, then dropped
        from the scope stack so they aren't double-freed at function
        exit (which sees the outermost frame).
        """
        indent = self._indent()
        frame = self._scope.cleanup_stack.pop()
        result = ""
        for var_id, var_type in reversed(frame):
            var_name = self.typing.variable_cname.get(var_id)
            if var_name is not None:
                result += self._emit_field_cleanup(var_name, var_type, indent)
        return result

    def _emit_taken_vars_cleanup(
        self,
        taken_vars: "List[tuple]",
        indent: str,
    ) -> str:
        """Emit destroy+zero for variables taken in some arm of a case/if block.

        For each (name, type) in taken_vars, emit the appropriate
        destructor call and zero-initialization so the variable is safe
        for scope-exit cleanup (no double-free, no leak).
        """
        parts: List[str] = []
        for vname, vtype in taken_vars:
            if vtype and (vtype.destructor_name is not None) and vtype.destructor_name:
                var = _mangle_var(vname)
                if vtype.is_heap_allocated:
                    parts.append(
                        f"{indent}if ({var}) {{ {vtype.destructor_name}({var}); }}\n"
                    )
                    parts.append(f"{indent}{var} = NULL;\n")
                else:
                    parts.append(f"{indent}{vtype.destructor_name}(&{var});\n")
                    parts.append(
                        f"{indent}{var} = ({_ctype(self.typing, vtype)}){{0}};\n"
                    )
        return "".join(parts)

    def _needs_implicit_take(self, ztype: Optional[ZType]) -> bool:
        """True if ztype owns heap data and so requires source-side
        invalidation at an ownership-transfer site.

        Two shapes both need this:
        - Stack structs that own heap data (e.g. string): pass-by-value
          copies the outer struct but aliases the inner heap pointer.
        - Heap-allocated reftypes (class, union, map, list): pass-by-
          pointer copies the pointer; both sides hold it. Either freeing
          the source independently of the destination double-frees the
          underlying data.

        In both cases the source-side variable must be invalidated at
        the transfer site so scope-exit cleanup is a safe no-op.
        """
        if ztype is None:
            return False
        return bool(ztype.destructor_name is not None)

    def _apply_call_implicit_takes(self, call: zast.Call, indent: str) -> None:
        """Apply implicit TAKE to ownership-transferring args of a function call.

        Runs AFTER the call's result has been emitted (so invalidation decls are
        appended to ``_temp.decls`` after the call's result-temp decl). Scope:

          * ``string`` args: always transferred at the C level (strings are
            passed by value and the callee is expected to own the heap buffer).
          * Other heap-backed stack structs (protocols, unions, classes): only
            transferred when the param is annotated ``.take`` — default for
            these is BORROW and the caller retains ownership.
        """
        emitted_vals = self._last_emitted_arg_vals
        ftype = self._node_ztype(call.callable)
        for i, arg in enumerate(call.arguments):
            if i >= len(emitted_vals) or not emitted_vals[i]:
                continue
            arg_type = self._get_operation_type(arg.valtype)
            if arg_type is None:
                continue
            explicit_own = (
                self.typing.child_ownership(ftype, arg.name)
                if ftype and arg.name
                else None
            )
            if explicit_own in (ZParamOwnership.BORROW, ZParamOwnership.LOCK):
                continue
            is_string = arg_type.subtype == ZSubType.STRING
            is_explicit_take = explicit_own == ZParamOwnership.TAKE
            if is_string or (is_explicit_take and self._needs_implicit_take(arg_type)):
                self._transfer_implicit_take(emitted_vals[i], arg.valtype, indent)

    def _is_borrow_return_call(self, expr: zast.Expression) -> bool:
        """True if ``expr`` is a call whose callee returns a borrowed value.

        A `out T.borrow` function declares that the return is aliased from an
        input — the caller must not free it.
        """
        inner = expr.expression
        if inner.nodetype != NodeType.CALL:
            return False
        call = cast(zast.Call, inner)
        ftype = self._node_ztype(call.callable)
        return bool(ftype and ftype.return_ownership == ZParamOwnership.BORROW)

    def _rhs_is_borrowed_projection(self, expr: zast.Expression) -> bool:
        """True if ``expr`` is a dotted-path projection whose root variable
        is a borrowed local (registered in ``self._scope.borrowed_vars``).

        Borrow-by-default union params live in ``borrowed_vars`` (see
        ``_emit_function``), so a synth-hoisted call-arg temp like
        ``_t: n.lhs`` (where ``n`` is the borrowed union param)
        resolves here. The destination temp aliases ``n``'s
        heap-owned payload — destroying it would corrupt the borrowed
        source. Marking the temp as borrowed routes it past the
        cleanup-vars register so block-scope cleanup is skipped.
        """
        inner = expr.expression
        if inner.nodetype != NodeType.DOTTEDPATH:
            return False
        # The projection's root local; borrowed-ness is an identity property.
        vid = self._root_atom_vid(inner)
        return vid is not None and vid in self._scope.borrowed_vars

    def _call_has_string_arg(self, call: zast.Call) -> bool:
        """True if any arg to ``call`` is a string (needs post-call ordering)."""
        for arg in call.arguments:
            at = self._get_operation_type(arg.valtype)
            if at and at.subtype == ZSubType.STRING:
                return True
        return False

    def _transfer_implicit_take(
        self,
        emitted_val: str,
        arg_op: zast.Operation,
        indent: str,
    ) -> None:
        """Apply TAKE semantics for an arg whose type owns heap data.

        Mirrors the `_emit_box_create` pattern: drop the arg from the scope's
        free list (the destination now owns the heap buffer) and, for the
        implicit case (no explicit `.take`), zero-init the source variable
        so any residual reference or scope-exit free is a safe no-op.

        The explicit `.take` case is detected via `_get_take_var` and left to
        the caller's existing `take_vars` invalidation loop — but we still
        drop from frees here, since ownership is transferred either way.
        """
        arg_type = self._get_operation_type(arg_op)
        if not self._needs_implicit_take(arg_type):
            return
        if emitted_val in self._temp.frees:
            self._temp.frees.remove(emitted_val)
        if self._get_take_var(arg_op):
            return  # explicit .take invalidation handled by caller
        root = self._get_implicit_take_var(arg_op)
        if root:
            # post_code, not decls: zeroing must happen AFTER the
            # consumer reads the value, not before (otherwise the
            # callee receives an empty/zero struct).
            self._temp.post_code.append(
                self._emit_take_invalidation(root, arg_type, indent)
            )

    def _is_union_construction(self, call: zast.Call) -> bool:
        """Check if a call is a union construction (union.subtype or bare union name)."""
        # Step 4d: Call decoration reads via typed mirror.
        _call_kind = self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
        _call_ztype = self._node_ztype(call)
        # a regular function call that happens to return a union is NOT a
        # union construction; defer to the standard call emission path.
        if _call_kind == zast.CallKind.REGULAR:
            return False
        # Trust the typechecker's UNION_CREATE stamp, but only when the
        # call's stamped type IS a union — UNION_CREATE is also set on
        # VARIANT subtype construction (typecheck shares the dispatch
        # branch at ztypecheck.py:8390-8416). The fallback detection
        # below only handles 2-segment (ATOMID-parented) dotted paths;
        # 3-segment paths like `io.IoError.other` rely on this stamp
        # because their parent is itself a DOTTEDPATH.
        if (
            _call_kind == zast.CallKind.UNION_CREATE
            and _call_ztype is not None
            and _call_ztype.typetype == ZTypeType.UNION
        ):
            return True
        # check type annotation for monomorphized union types
        call_type = _call_ztype
        if (
            call_type
            and call_type.typetype == ZTypeType.UNION
            and call_type.generic_origin
        ):
            return True
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            parent = cast(zast.DottedPath, call.callable).parent
            if parent.nodetype == NodeType.ATOMID:
                pt = self._unit_def_ztype(parent)
                if pt is not None and pt.typetype == ZTypeType.UNION:
                    return True
        if call.callable.nodetype == NodeType.ATOMID:
            ct = self._unit_def_ztype(call.callable)
            if ct is not None and ct.typetype == ZTypeType.UNION:
                return True
        return False

    def _emit_union_construction(self, call: zast.Call) -> str:
        """Emit C code for union construction."""
        # Step 4d: Call decoration reads via typed mirror.
        _call_ztype = self._node_ztype(call)
        call_type = _call_ztype

        # nullable-ptr option: .some val → val, .none → NULL
        if call_type and call_type.is_nullable_ptr:
            return self._emit_nullable_ptr_construction(call)

        self.needs_stdlib = True
        indent = self._indent()
        tmp = self._temp_name("c")

        # subtype name comes from the last segment of the dotted path
        # — same whether the callable is 2-segment (`U.arm`) or
        # 3-segment (`unit.U.arm`).
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            subtype_name = cast(zast.DottedPath, call.callable).child.name
        else:
            # bare union name — shouldn't happen for construction but handle gracefully
            return "NULL"

        # Prefer the typechecker's stamped union type for the union
        # name (works for monomorphized generics AND for 3-segment
        # cross-unit construction like `io.IoError.other`). Fall back
        # to walking the AST for legacy / synth call paths where the
        # type annotation may be missing.
        if call_type and call_type.typetype == ZTypeType.UNION:
            union_name = call_type.name
            ctype = _cname_of(call_type, union_name)
        elif cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
            parent_atom = cast(zast.AtomId, cast(zast.DottedPath, call.callable).parent)
            union_name = parent_atom.name
            ctype = _cname_of(self._unit_def_ztype(parent_atom), union_name)
        else:
            return "NULL"

        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp.decls.append(f"{indent}{ctype} {tmp} = {{0}};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")

        # determine subtype info — check monomorphized type first
        is_null = False
        subtype_ctype_resolved = None
        if call_type and call_type.generic_origin:
            # monomorphized: look up subtype from the mono ZType
            sub_ztype = self.typing.child_of(call_type, subtype_name)
            if sub_ztype:
                is_null = sub_ztype.typetype == ZTypeType.NULL
                if not is_null:
                    subtype_ctype_resolved = _ctype(self.typing, sub_ztype)
        else:
            # non-generic: look up from AST. Prefer mainunit (so a user
            # definition shadows any system namesake), then search other
            # units for unions defined only in the system library (e.g.
            # `ioerror` in lib/system/io.z).
            mainunit = self.program.units.get(self.program.mainunitname)
            union_defn = mainunit.body.get(union_name) if mainunit else None
            if union_defn is None or union_defn.nodetype != NodeType.UNION:
                union_defn = None
                for _uname, _u in self.program.units.items():
                    if _uname == self.program.mainunitname:
                        continue
                    cand = _u.body.get(union_name)
                    if cand is not None and cand.nodetype == NodeType.UNION:
                        union_defn = cand
                        break
            subtype_path = None
            if union_defn is not None and union_defn.nodetype == NodeType.UNION:
                subtype_path = (
                    cast(zast.ObjectDef, union_defn).is_paths().get(subtype_name)
                )
            is_null = (
                subtype_path is not None
                and subtype_path.nodetype == NodeType.ATOMID
                and cast(zast.AtomId, subtype_path).name == "null"
            )
            if not is_null and subtype_path:
                subtype_ctype_resolved = self._get_subtype_ctype(subtype_path)

        # find the value arg: from: takes priority, then first positional arg
        value_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                value_arg = arg
                break
        if value_arg is None:
            for arg in call.arguments:
                if not arg.name:
                    value_arg = arg
                    break

        # determine whether this arm is a locked arm — locked arms hold a
        # borrowed pointer into the source (no ownership, no boxing).
        is_locked_arm = bool(
            call_type and self.typing.is_child_lock_arm(call_type, subtype_name)
        )

        if is_null or value_arg is None:
            self._temp.decls.append(f"{indent}{tmp}.data = NULL;\n")
        else:
            # for monomorphized null subtype with explicit type arg, skip the arg
            # (it's a type name, not a value)
            if call_type and call_type.generic_origin and is_null:
                self._temp.decls.append(f"{indent}{tmp}.data = NULL;\n")
            elif is_locked_arm:
                # locked arm: take the address of the source's storage. The
                # source must be a local variable / addressable lvalue; the
                # type-checker's borrow-lock machinery enforces that the
                # union cannot outlive the source.
                val = self._emit_operation_value(value_arg.valtype)
                # ownership stays with the source; don't add to frees.
                if val in self._temp.frees:
                    self._temp.frees.remove(val)
                # for reftype sources, val is already a pointer; for valtype,
                # val is the lvalue and we take its address.
                subtype_ctype = subtype_ctype_resolved
                if subtype_ctype and (
                    subtype_ctype.startswith("z_") and subtype_ctype.endswith("_t*")
                ):
                    self._temp.decls.append(f"{indent}{tmp}.data = {val};\n")
                else:
                    self._temp.decls.append(f"{indent}{tmp}.data = &{val};\n")
            else:
                val = self._emit_operation_value(value_arg.valtype)
                subtype_ctype = subtype_ctype_resolved
                if subtype_ctype and (
                    subtype_ctype.startswith("z_") and subtype_ctype.endswith("_t*")
                ):
                    # reftype: store pointer directly
                    self._temp.decls.append(f"{indent}{tmp}.data = {val};\n")
                    # ownership transferred into the union — drop the temp
                    # from frees AND invalidate the source-side variable
                    # (e.g. zero-init the local that held the value) so
                    # scope-exit cleanup doesn't double-free.
                    self._transfer_implicit_take(val, value_arg.valtype, indent)
                    take_var = self._get_take_var(value_arg.valtype)
                    if take_var:
                        val_type = self._get_operation_type(value_arg.valtype)
                        self._temp.decls.append(
                            self._emit_take_invalidation(take_var, val_type, indent)
                        )
                else:
                    # valtype: box it (malloc + copy)
                    box_ctype = subtype_ctype or "int64_t"
                    box_tmp = self._temp_name("Box")
                    self._temp.decls.append(
                        f"{indent}{box_ctype}* {box_tmp} = ({box_ctype}*)z_xmalloc(sizeof({box_ctype}));\n"
                    )
                    self._temp.decls.append(f"{indent}*{box_tmp} = {val};\n")
                    self._temp.decls.append(f"{indent}{tmp}.data = {box_tmp};\n")
                    # ownership transferred to boxed copy — drop from
                    # frees AND invalidate the source-side variable.
                    # Mirrors `_emit_box_create` (line ~9965).
                    self._transfer_implicit_take(val, value_arg.valtype, indent)
                    take_var = self._get_take_var(value_arg.valtype)
                    if take_var:
                        val_type = self._get_operation_type(value_arg.valtype)
                        self._temp.decls.append(
                            self._emit_take_invalidation(take_var, val_type, indent)
                        )

        self._temp.frees.append(tmp)
        self._temp.class_set[tmp] = union_name
        return tmp

    def _emit_nullable_ptr_construction(self, call: zast.Call) -> str:
        """Emit nullable-ptr option construction: .some val → val, .none → NULL."""
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            subtype_name = cast(zast.DottedPath, call.callable).child.name
        else:
            return "NULL"

        if subtype_name == "none":
            return "NULL"

        # .some val: emit the value directly (take ownership)
        value_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                value_arg = arg
                break
        if value_arg is None:
            for arg in call.arguments:
                if not arg.name:
                    value_arg = arg
                    break

        if value_arg is None:
            return "NULL"

        val = self._emit_operation_value(value_arg.valtype)
        # take ownership: remove from frees list
        if val in self._temp.frees:
            self._temp.frees.remove(val)
        return val

    def _emit_box_create(self, call: zast.Call) -> str:
        """Emit box from: val for valtype — malloc + copy."""
        # Step 4d: Call decoration reads via typed mirror.
        _call_ztype = self._node_ztype(call)
        self.needs_stdlib = True
        indent = self._indent()
        call_type = _call_ztype
        if not call_type:
            return "NULL"
        inner_type = self.typing.generic_arg_of(call_type, "t")
        if not inner_type:
            return "NULL"
        inner_ctype = _ctype(self.typing, inner_type)
        ptr_ctype = f"{inner_ctype}*"
        tmp = self._temp_name("Box")

        # find the from: argument
        value_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                value_arg = arg
                break
        if value_arg is None:
            for arg in call.arguments:
                if not arg.name:
                    value_arg = arg
                    break
        if value_arg is None:
            return "NULL"

        val = self._emit_operation_value(value_arg.valtype)
        self._temp.decls.append(
            f"{indent}{ptr_ctype} {tmp} = ({ptr_ctype})z_xmalloc(sizeof({inner_ctype}));\n"
        )
        self._temp.decls.append(f"{indent}*{tmp} = {val};\n")
        # Transfer ownership: drop the source from frees (boxed copy now
        # owns the heap data) and zero-init the source so any scope-exit
        # cleanup is a safe no-op. Mirrors the standard call's
        # _apply_call_implicit_takes loop now that constructor-site
        # hoisting routes hoisted args (`_tN`) through here.
        self._transfer_implicit_take(val, value_arg.valtype, indent)
        # handle explicit .take suffix — invalidate source variable
        take_var = self._get_take_var(value_arg.valtype)
        if take_var:
            val_type = self._get_operation_type(value_arg.valtype)
            self._temp.decls.append(
                self._emit_take_invalidation(take_var, val_type, indent)
            )
        self._temp.frees.append(tmp)
        return tmp

    def _emit_box_passthrough(self, call: zast.Call) -> str:
        """Emit box from: val for reftype — just take ownership."""
        # find the from: argument
        value_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                value_arg = arg
                break
        if value_arg is None:
            for arg in call.arguments:
                if not arg.name:
                    value_arg = arg
                    break
        if value_arg is None:
            return "NULL"

        val = self._emit_operation_value(value_arg.valtype)
        # take ownership: remove from frees list
        if val in self._temp.frees:
            self._temp.frees.remove(val)
        return val

    def _get_subtype_ctype(self, subtype_path: Optional[zast.Path]) -> Optional[str]:
        """Get the C type for a union subtype path."""
        if not subtype_path:
            return None
        ct = _ctype(self.typing, self._node_ztype(subtype_path))
        return ct if ct != "void" else None

    def _emit_union_null_construction(
        self, union_ztype: ZType, subtype_name: str
    ) -> str:
        """Emit construction for a null-subtype union (no data)."""
        self.needs_stdlib = True
        indent = self._indent()
        tmp = self._temp_name("c")
        union_name = union_ztype.name
        ctype = _cname_of(union_ztype, union_name)
        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"
        self._temp.decls.append(f"{indent}{ctype} {tmp} = {{0}};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")
        self._temp.decls.append(f"{indent}{tmp}.data = NULL;\n")
        self._temp.frees.append(tmp)
        self._temp.class_set[tmp] = union_name
        return tmp

    def _is_variant_construction(self, call: zast.Call) -> bool:
        """Check if a call is a variant construction (variant.subtype expr)."""
        # Step 4d: Call decoration reads via typed mirror.
        _call_kind = self.typing.call_kind.get(call.nodeid, zast.CallKind.UNKNOWN)
        _call_ztype = self._node_ztype(call)
        # A regular function call whose return type happens to be a
        # variant is NOT a construction — defer to the standard call
        # emission path (same guard as _is_union_construction).
        if _call_kind == zast.CallKind.REGULAR:
            return False
        # Trust the typechecker's UNION_CREATE stamp, gated on the
        # call's stamped type being a variant (UNION_CREATE is shared
        # between union and variant subtype construction). Handles
        # 3-segment cross-unit construction like `io.openmode.read`
        # when called with payload args.
        if (
            _call_kind == zast.CallKind.UNION_CREATE
            and _call_ztype is not None
            and _call_ztype.typetype == ZTypeType.VARIANT
        ):
            return True
        # check type annotation for monomorphized variant types
        call_type = _call_ztype
        if (
            call_type
            and call_type.typetype == ZTypeType.VARIANT
            and call_type.generic_origin
        ):
            return True
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            parent = cast(zast.DottedPath, call.callable).parent
            if parent.nodetype == NodeType.ATOMID:
                pt = self._unit_def_ztype(parent)
                if pt is not None and pt.typetype == ZTypeType.VARIANT:
                    return True
        if call.callable.nodetype == NodeType.ATOMID:
            ct = self._unit_def_ztype(call.callable)
            if ct is not None and ct.typetype == ZTypeType.VARIANT:
                return True
        return False

    def _emit_variant_construction(self, call: zast.Call) -> str:
        """Emit C code for variant construction (stack-allocated, no malloc)."""
        # Step 4d: Call decoration reads via typed mirror.
        _call_ztype = self._node_ztype(call)
        indent = self._indent()
        tmp = self._temp_name("c")

        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
        ):
            subtype_name = cast(zast.DottedPath, call.callable).child.name
        else:
            return "(z_unknown_t){0}"

        # Prefer the stamped variant type for the name + cname: works for
        # monomorphized generics AND for non-mono / cross-unit variants (whose
        # ztype.name is dot-free, e.g. `zlexer_tokstatetype`). Fall back to the
        # parent atom name only when the call carries no variant stamp.
        call_type = _call_ztype
        if call_type and call_type.typetype == ZTypeType.VARIANT:
            variant_name = call_type.name
            ctype = _cname_of(call_type, variant_name)
        elif cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
            parent_atom = cast(zast.AtomId, cast(zast.DottedPath, call.callable).parent)
            variant_name = parent_atom.name
            ctype = _cname_of(self._unit_def_ztype(parent_atom), variant_name)
        else:
            return "(z_unknown_t){0}"

        tag = f"Z_{variant_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp.decls.append(f"{indent}{ctype} {tmp};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")

        # null-subtype detection: read the subtype off the stamped variant type
        # when available (covers mono + cross-unit); else look up by name.
        is_null = False
        if call_type and call_type.typetype == ZTypeType.VARIANT:
            sub_ztype = self.typing.child_of(call_type, subtype_name)
            is_null = sub_ztype is not None and sub_ztype.typetype == ZTypeType.NULL
        else:
            mainunit = self.program.units.get(self.program.mainunitname)
            variant_defn = mainunit.body.get(variant_name) if mainunit else None
            subtype_path = None
            if variant_defn is not None and variant_defn.nodetype == NodeType.VARIANT:
                subtype_path = (
                    cast(zast.ObjectDef, variant_defn).is_paths().get(subtype_name)
                )
            is_null = (
                subtype_path is not None
                and subtype_path.nodetype == NodeType.ATOMID
                and cast(zast.AtomId, subtype_path).name == "null"
            )

        if not is_null:
            # find value arg: from: takes priority, then first positional
            value_arg = None
            for arg in call.arguments:
                if arg.name == "from":
                    value_arg = arg
                    break
            if value_arg is None:
                for arg in call.arguments:
                    if not arg.name:
                        value_arg = arg
                        break
            if value_arg:
                val = self._emit_operation_value(value_arg.valtype)
                self._temp.decls.append(f"{indent}{tmp}.data.{subtype_name} = {val};\n")

        # no temp_frees — value type, no cleanup needed
        return tmp

    def _emit_variant_null_construction(
        self, variant_ztype: ZType, subtype_name: str
    ) -> str:
        """Emit construction for a null-subtype variant (tag only, no data)."""
        indent = self._indent()
        tmp = self._temp_name("c")
        variant_name = variant_ztype.name
        ctype = _cname_of(variant_ztype, variant_name)
        tag = f"Z_{variant_name.upper()}_TAG_{subtype_name.upper()}"
        self._temp.decls.append(f"{indent}{ctype} {tmp};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")
        return tmp

    def _emit_string_value(self, atom: zast.AtomString) -> str:
        has_interp = any(
            p.nodetype != zast.NodeType.STRINGCHUNK for p in atom.stringparts
        )

        if not has_interp:
            self.needs_stringview = True
            literal = self._collect_string_literal(atom.stringparts)
            return self._static_string(literal)

        self.needs_string = True
        self.needs_stdlib = True
        self.needs_stdio = True

        # append chain: one allocation, no intermediates
        indent = self._indent()
        result = self._temp_name("s")
        # estimate capacity: literal lengths + 16 per expression
        est_cap = 0
        for p in atom.stringparts:
            if p.nodetype != NodeType.STRINGCHUNK:
                est_cap += 16
            else:
                est_cap += len(cast(zast.StringChunk, p).text)
        self._temp.decls.append(
            f"{indent}z_String_t {result} = z_String_create((uint64_t){est_cap});\n"
        )
        self._temp.frees.append(result)
        self._temp.string_set.add(result)

        for p in atom.stringparts:
            if p.nodetype != NodeType.STRINGCHUNK:
                val = self._emit_expression_value(cast(zast.Expression, p))
                val_type = self._get_expression_type(cast(zast.Expression, p))
                if val_type and val_type.name in (
                    "i8",
                    "i16",
                    "i32",
                    "i64",
                    "u8",
                    "u16",
                    "u32",
                    "u64",
                ):
                    buf = self._temp_name("b")
                    self._temp.decls.append(
                        f"{indent}char {buf}[32]; int {buf}_n = snprintf({buf},"
                        f' 32, "%ld", (long)(int64_t){val});\n'
                    )
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result},"
                        f" {buf}, (uint64_t){buf}_n);\n"
                    )
                elif val_type and val_type.name in ("f32", "f64"):
                    buf = self._temp_name("b")
                    self._temp.decls.append(
                        f"{indent}char {buf}[64]; int {buf}_n = snprintf({buf},"
                        f' 64, "%g", (double){val});\n'
                    )
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result},"
                        f" {buf}, (uint64_t){buf}_n);\n"
                    )
                elif val_type and val_type.subtype == ZSubType.STRING:
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result}, {val}.data, {val}.size);\n"
                    )
                elif val_type and _is_stringview_type(val_type):
                    self.needs_stringview = True
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result},"
                        f" {val}.data, {val}.length);\n"
                    )
                elif val_type and _is_str_type(val_type):
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result}, {val}.data, {val}.len);\n"
                    )
                else:
                    buf = self._temp_name("b")
                    self._temp.decls.append(
                        f"{indent}char {buf}[32]; int {buf}_n = snprintf({buf},"
                        f' 32, "%ld", (long)(int64_t){val});\n'
                    )
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result},"
                        f" {buf}, (uint64_t){buf}_n);\n"
                    )
            else:
                literal = self._escape_c_string(cast(zast.StringChunk, p).text)
                if literal:
                    self._temp.decls.append(
                        f"{indent}z_String_append(&{result},"
                        f' "{literal}", sizeof("{literal}")-1);\n'
                    )

        return result

    def _collect_string_literal(self, parts: list) -> str:
        result: List[str] = []
        for p in parts:
            if p.nodetype == NodeType.STRINGCHUNK:
                result.append(self._escape_c_string(cast(zast.StringChunk, p).text))
        return "".join(result)

    def _escape_c_string(self, s: str) -> str:
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\r", "\\r")
        )

    def _get_expression_type(self, expr: zast.Expression) -> Optional[ZType]:
        if self._node_ztype(expr):
            return self._node_ztype(expr)
        inner = expr.expression
        if self._node_ztype(inner):
            return self._node_ztype(inner)
        return None

    def _get_operation_type(self, op: zast.Operation) -> Optional[ZType]:
        if self._node_ztype(op):
            return self._node_ztype(op)
        # look inside Expression wrapper
        if op.nodetype == NodeType.EXPRESSION:
            return self._get_expression_type(cast(zast.Expression, op))
        return None

    def _get_storage_type(self, op: zast.Operation) -> Optional[ZType]:
        """Storage (declared) type for an expression — for a narrowed
        AtomId, return the ORIGINAL union/variant type (which matches
        the C-level variable declaration), not the narrowed payload.

        Used when the emitter needs the actual C struct shape for
        casts, zero-initializers, or scope-exit destructors. Regular
        type lookup via `_get_operation_type` returns the narrowed
        view, which is wrong for those sites.
        """
        if op.nodetype == NodeType.ATOMID:
            atom = cast(zast.AtomId, op)
            original = self.typing.atom_original_ztype.get(atom.nodeid)
            if original is not None:
                return original
        if op.nodetype == NodeType.EXPRESSION:
            expr = cast(zast.Expression, op)
            inner = expr.expression
            if inner is not None and inner.nodetype == NodeType.ATOMID:
                return self._get_storage_type(cast(zast.Operation, inner))
        return self._get_operation_type(op)

    def _emit_if(self, ifnode: zast.If) -> str:
        _if_taken_vars = self.typing.if_taken_vars.get(ifnode.nodeid, [])
        indent = self._indent()
        parts: List[str] = []

        # constant folding: check if any clause has all-constant conditions
        emitted_true_branch = False
        non_const_clauses: List[tuple] = []  # (index, clause) for non-constant clauses

        for i, clause in enumerate(ifnode.clauses):
            # check if all conditions in this clause are compile-time constants
            all_const = all(
                self._node_const_value(cond_op) is not None
                for _, cond_op in clause.conditions.items()
            )
            if all_const and not emitted_true_branch:
                all_true = all(
                    bool(self._node_const_value(cond_op))
                    for _, cond_op in clause.conditions.items()
                )
                if all_true:
                    # emit just the branch body in a new scope
                    parts.append(f"{indent}{{\n")
                    self.indent_level += 1
                    self._push_block_scope()
                    parts.append(self._emit_statement(clause.statement))
                    parts.append(self._pop_block_scope_and_emit())
                    self.indent_level -= 1
                    parts.append(f"{indent}}}\n")
                    emitted_true_branch = True
                # else: all-false constant, skip this clause
            else:
                if not emitted_true_branch:
                    non_const_clauses.append((i, clause))

        if not emitted_true_branch and non_const_clauses:
            # emit remaining non-constant clauses as normal if/else-if
            for j, (_, clause) in enumerate(non_const_clauses):
                keyword = "if" if j == 0 else "} else if"
                conds: List[str] = []
                for _, cond_op in clause.conditions.items():
                    conds.append(self._emit_operation_value(cond_op))
                if not conds:
                    cond_str = "1"
                elif len(conds) == 1:
                    cond_str = _unwrap_outer_parens(conds[0])
                else:
                    cond_str = " && ".join(conds)
                parts.append(f"{indent}{keyword} ({cond_str}) {{\n")
                self.indent_level += 1
                self._push_block_scope()
                parts.append(self._emit_statement(clause.statement))
                parts.append(self._pop_block_scope_and_emit())
                self.indent_level -= 1

            if ifnode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                self._push_block_scope()
                parts.append(self._emit_statement(ifnode.elseclause))
                parts.append(self._pop_block_scope_and_emit())
                self.indent_level -= 1

            parts.append(f"{indent}}}\n")
        elif not emitted_true_branch and ifnode.elseclause:
            # all clauses were constant-false, emit else branch in a scope
            parts.append(f"{indent}{{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(ifnode.elseclause))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        # post-if cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(_if_taken_vars, indent))

        return "".join(parts)

    def _emit_branch_with_result(self, stmt: zast.Statement, result_var: str) -> str:
        """Emit a branch body, assigning the last expression's value to result_var."""
        parts: List[str] = []
        lines = stmt.statements
        for i, sline in enumerate(lines):
            is_last = i == len(lines) - 1
            inner = sline.statementline

            if is_last and inner.nodetype == zast.NodeType.EXPRESSION:
                last_expr = cast(zast.Expression, inner)
                # non-completing: emit normally (return/break/continue)
                if self._expr_call_kind(last_expr) in (
                    zast.CallKind.RETURN,
                    zast.CallKind.BREAK,
                    zast.CallKind.CONTINUE,
                    zast.CallKind.ERROR,
                    zast.CallKind.PANIC,
                ):
                    parts.append(self._emit_statement_line(sline))
                else:
                    # value-producing: assign to result_var
                    self._temp_stack.append(TempState())
                    val = self._emit_expression_value(cast(zast.Expression, inner))
                    indent = self._indent()
                    code = f"{indent}{result_var} = {val};\n"
                    result = "".join(self._temp.decls) + code
                    self._temp_stack.pop()
                    parts.append(result)
            else:
                parts.append(self._emit_statement_line(sline))

        return "".join(parts)

    def _emit_branch_with_result_optional(
        self, stmt: zast.Statement, result_var: str, some_tag: str
    ) -> str:
        """Emit a branch body, wrapping the last value in an optional some."""
        parts: List[str] = []
        lines = stmt.statements
        for i, sline in enumerate(lines):
            is_last = i == len(lines) - 1
            inner = sline.statementline

            if is_last and inner.nodetype == zast.NodeType.EXPRESSION:
                last_expr = cast(zast.Expression, inner)
                if self._expr_call_kind(last_expr) in (
                    zast.CallKind.RETURN,
                    zast.CallKind.BREAK,
                    zast.CallKind.CONTINUE,
                    zast.CallKind.ERROR,
                    zast.CallKind.PANIC,
                ):
                    parts.append(self._emit_statement_line(sline))
                else:
                    # value-producing: wrap in some
                    self._temp_stack.append(TempState())
                    val = self._emit_expression_value(cast(zast.Expression, inner))
                    indent = self._indent()
                    code = (
                        f"{indent}{result_var}.tag = {some_tag};\n"
                        f"{indent}{result_var}.data.some = {val};\n"
                    )
                    result = "".join(self._temp.decls) + code
                    self._temp_stack.pop()
                    parts.append(result)
            else:
                parts.append(self._emit_statement_line(sline))

        return "".join(parts)

    def _emit_if_expression_value(self, ifnode: zast.If) -> str:
        """Emit if-as-expression using temp variable pattern."""
        ctype = "int64_t"
        if self._node_ztype(ifnode):
            ctype = _ctype(self.typing, self._node_ztype(ifnode))

        tmp = self._temp_name("if")
        indent = self._indent()

        # declare temp variable
        self._temp.decls.append(f"{indent}{ctype} {tmp};\n")

        # build the if/else-if/else structure
        parts: List[str] = []

        # handle constant folding (reuse logic from _emit_if)
        emitted_true_branch = False
        non_const_clauses: List[tuple] = []

        for i, clause in enumerate(ifnode.clauses):
            all_const = all(
                self._node_const_value(cond_op) is not None
                for _, cond_op in clause.conditions.items()
            )
            if all_const and not emitted_true_branch:
                all_true = all(
                    bool(self._node_const_value(cond_op))
                    for _, cond_op in clause.conditions.items()
                )
                if all_true:
                    parts.append(f"{indent}{{\n")
                    self.indent_level += 1
                    parts.append(self._emit_branch_with_result(clause.statement, tmp))
                    self.indent_level -= 1
                    parts.append(f"{indent}}}\n")
                    emitted_true_branch = True
            else:
                if not emitted_true_branch:
                    non_const_clauses.append((i, clause))

        if not emitted_true_branch and non_const_clauses:
            for j, (_, clause) in enumerate(non_const_clauses):
                keyword = "if" if j == 0 else "} else if"
                conds: List[str] = []
                for _, cond_op in clause.conditions.items():
                    conds.append(self._emit_operation_value(cond_op))
                if not conds:
                    cond_str = "1"
                elif len(conds) == 1:
                    cond_str = _unwrap_outer_parens(conds[0])
                else:
                    cond_str = " && ".join(conds)
                parts.append(f"{indent}{keyword} ({cond_str}) {{\n")
                self.indent_level += 1
                parts.append(self._emit_branch_with_result(clause.statement, tmp))
                self.indent_level -= 1

            if ifnode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                parts.append(self._emit_branch_with_result(ifnode.elseclause, tmp))
                self.indent_level -= 1

            parts.append(f"{indent}}}\n")
        elif not emitted_true_branch and ifnode.elseclause:
            # all clauses constant-false, emit else
            parts.append(f"{indent}{{\n")
            self.indent_level += 1
            parts.append(self._emit_branch_with_result(ifnode.elseclause, tmp))
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        self._temp.decls.append("".join(parts))

        # track reftype ownership
        if (
            self._node_ztype(ifnode)
            and cast(ZType, self._node_ztype(ifnode)).destructor_name is not None
        ):
            self._temp.frees.append(tmp)
            if ctype == "z_String_t":
                self._temp.string_set.add(tmp)
                self.needs_string = True

        return tmp

    def _iter_binding_destructor(
        self, var_name: str, elem_type: Optional[ZType]
    ) -> Optional[str]:
        """Return a single-line C statement to destroy a for-loop iteration
        binding at end-of-iteration, or None when no cleanup is needed.

        Ownership was moved out of the option's box into the binding (see
        the reftype-union path in `_emit_for`), so the binding now owns the
        heap allocation and must free it before the loop head re-runs.
        Mirrors the scope-exit cleanup dispatch used by `_emit_scope_exit`.
        """
        if elem_type is None:
            return None
        if elem_type.subtype == ZSubType.STRING:
            self.needs_string = True
            return f"z_String_free(&{var_name});"
        if not (elem_type.destructor_name is not None):
            return None
        destructor = elem_type.destructor_name
        if destructor:
            return f"{destructor}(&{var_name});"
        return None

    def _emit_for(self, fornode: zast.For) -> str:
        _for_iter_bindings = self.typing.for_iter_bindings.get(fornode.nodeid, set())
        indent = self._indent()
        parts: List[str] = []

        init_vars: List[str] = []
        cond_exprs: List[str] = []
        # iterator bindings: (name, op, opt_ctype, elem_ctype, opt_name, callable_ztype, opt_type, elem_type)
        iter_bindings: List[
            Tuple[
                str,
                zast.Operation,
                str,
                str,
                str,
                Optional[ZType],
                Optional[ZType],
                Optional[ZType],
            ]
        ] = []
        # each bindings: (name, limit_expr, from_expr, elem_ctype) — optimized C for loop
        each_bindings: List[Tuple[str, str, str, str]] = []

        for name, cond_op in fornode.conditions.items():
            if name.startswith(" "):
                cond_exprs.append(self._emit_operation_value(cond_op))
            elif name in _for_iter_bindings:
                # check for .each / .iterate on integer types (C for-loop
                # optimization). Both names are recognised; `.iterate` is the
                # canonical name with `.each` retained as a deprecated alias.
                is_each = False
                actual_op = cond_op
                while actual_op.nodetype == NodeType.EXPRESSION:
                    actual_op = cast(zast.Expression, actual_op).expression
                if actual_op.nodetype in (NodeType.DOTTEDPATH, NodeType.CALL):
                    each_path = None
                    from_val = "0"
                    if actual_op.nodetype == NodeType.DOTTEDPATH and cast(
                        zast.DottedPath, actual_op
                    ).child.name in ("each", "iterate"):
                        each_path = cast(zast.DottedPath, actual_op)
                    elif (
                        actual_op.nodetype == NodeType.CALL
                        and cast(zast.Call, actual_op).callable.nodetype
                        == NodeType.DOTTEDPATH
                        and cast(
                            zast.DottedPath, cast(zast.Call, actual_op).callable
                        ).child.name
                        in ("each", "iterate")
                    ):
                        each_path = cast(
                            zast.DottedPath, cast(zast.Call, actual_op).callable
                        )
                        for arg in cast(zast.Call, actual_op).arguments:
                            if arg.name == "from" or arg.name is None:
                                from_val = self._emit_operation_value(arg.valtype)
                    if each_path:
                        parent_type = self._node_ztype(each_path.parent)
                        if parent_type and parent_type.name in {
                            "i8",
                            "i16",
                            "i32",
                            "i64",
                            "i128",
                            "u8",
                            "u16",
                            "u32",
                            "u64",
                            "u128",
                        }:
                            limit_val = self._emit_path_value(each_path.parent)
                            elem_ctype = _ctype(self.typing, parent_type)
                            each_bindings.append(
                                (name, limit_val, from_val, elem_ctype)
                            )
                            is_each = True

                if not is_each:
                    t = self._get_operation_type(cond_op)
                    if t:
                        call_method = (
                            self.typing.child_of(t, "call")
                            if t.typetype != ZTypeType.FUNCTION
                            else None
                        )
                        if call_method and call_method.return_type:
                            opt_type = call_method.return_type
                            opt_ctype = _ctype(self.typing, opt_type)
                            opt_name = opt_type.name
                            some_type = self.typing.child_of(opt_type, "some")
                            elem_ctype = (
                                _ctype(self.typing, some_type)
                                if some_type
                                else "int64_t"
                            )
                            iter_bindings.append(
                                (
                                    name,
                                    cond_op,
                                    opt_ctype,
                                    elem_ctype,
                                    opt_name,
                                    t,
                                    opt_type,
                                    some_type,
                                )
                            )
                        else:
                            opt_type = t
                            opt_ctype = _ctype(self.typing, t)
                            opt_name = t.name
                            some_type = self.typing.child_of(t, "some")
                            elem_ctype = (
                                _ctype(self.typing, some_type)
                                if some_type
                                else "int64_t"
                            )
                            iter_bindings.append(
                                (
                                    name,
                                    cond_op,
                                    opt_ctype,
                                    elem_ctype,
                                    opt_name,
                                    None,
                                    opt_type,
                                    some_type,
                                )
                            )
            else:
                val = self._emit_operation_value(cond_op)
                ctype = "int64_t"
                t = self._get_operation_type(cond_op)
                if t:
                    ctype = _ctype(self.typing, t)
                init_vars.append(f"{indent}{ctype} {_mangle_var(name)} = {val};\n")

        for iv in init_vars:
            parts.append(iv)

        # emit post-condition expressions
        post_exprs: List[str] = []
        for postcond in fornode.postconditions:
            post_exprs.append(self._emit_operation_value(postcond))

        has_pre = bool(cond_exprs)
        has_post = bool(post_exprs)
        has_iter = bool(iter_bindings)
        has_each = bool(each_bindings)

        if has_each and not has_iter:
            # each-based loop: emit optimized C for loop
            for ename, limit_val, from_val, elem_ctype in each_bindings:
                cvar = _mangle_var(ename)
                parts.append(
                    f"{indent}for ({elem_ctype} {cvar} = {from_val}; "
                    f"{cvar} < {limit_val}; {cvar}++) {{\n"
                )
            if fornode.loop:
                self.indent_level += 1
                parts.append(self._emit_for_body(fornode))
                self.indent_level -= 1
            if has_post:
                self.indent_level += 1
                inner = self._indent()
                self.indent_level -= 1
                post_str = " && ".join(post_exprs)
                parts.append(f"{inner}if (!({post_str})) break;\n")
            for _ in each_bindings:
                parts.append(f"{indent}}}\n")
        elif has_iter:
            # iterator-based loop: while(1) with per-iteration call + option unwrap
            if not cond_exprs:
                cond_str = "1"
            elif len(cond_exprs) == 1:
                cond_str = _unwrap_outer_parens(cond_exprs[0])
            else:
                cond_str = " && ".join(cond_exprs)
            parts.append(f"{indent}while ({cond_str}) {{\n")
            self.indent_level += 1
            inner = self._indent()
            # Per-iteration cleanup accumulated by the reftype-option
            # branch below. Ownership was moved out of the option's box
            # into the iteration binding, so the binding needs its own
            # scope-exit destruction at the bottom of each iteration.
            iter_cleanup: List[str] = []
            # Optionview-with-reftype-payload bindings emit a borrow
            # pointer rather than a value-copy. Aliases set into
            # `_alias_map` so AtomId references in the body resolve to
            # `(*__borrow_<name>)`; entries are restored after body emit
            # below. Collect (name, prev_alias) so multi-binding loops
            # restore each correctly.
            optionview_aliases: List[Tuple[str, Optional[str]]] = []
            for (
                iname,
                iop,
                opt_ctype,
                elem_ctype,
                opt_name,
                callable_ztype,
                opt_type,
                elem_type,
            ) in iter_bindings:
                if callable_ztype is not None:
                    obj_val = self._emit_operation_value(iop)
                    _cm = self.typing.child_of(callable_ztype, "call")
                    call_fn = (
                        _cm.cname
                        if _cm is not None and _cm.cname
                        else _mangle_func(f"{callable_ztype.name}.call")
                    )
                    # Class iterators take a pointer receiver since 'this' is
                    # always a pointer.
                    rec_t = callable_ztype
                    if (
                        rec_t is not None
                        and rec_t.typetype == ZTypeType.CLASS
                        and not obj_val.startswith("&")
                    ):
                        obj_val = f"&{obj_val}"
                    iter_val = f"{call_fn}({obj_val})"
                else:
                    iter_val = self._emit_operation_value(iop)
                tmp = f"__iter_{_mangle_var(iname)}"
                iname_c = _mangle_var(iname)
                if opt_type and opt_type.is_nullable_ptr:
                    # nullable-ptr option: NULL = none
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(f"{inner}if ({tmp} == NULL) break;\n")
                    parts.append(f"{inner}{elem_ctype} {iname_c} = {tmp};\n")
                elif opt_type and opt_type.typetype == ZTypeType.VARIANT:
                    # optionval variant: check tag, extract data.some
                    none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(f"{inner}if ({tmp}.tag == {none_tag}) break;\n")
                    parts.append(f"{inner}{elem_ctype} {iname_c} = {tmp}.data.some;\n")
                elif (
                    opt_type
                    and opt_type.typetype == ZTypeType.UNION
                    and self.typing.is_child_lock_arm(opt_type, "some")
                ):
                    # optionview: borrowed-view union. data is a pointer to
                    # the source's storage. For valtype payloads (e.g.
                    # i64) bind by value-copy — copies are safe and don't
                    # need aliasing. For reftype payloads (string, classes)
                    # bind by pointer and seed the alias map so the body's
                    # `s.field` / `s.method ...` accesses go through the
                    # source storage, not a stack copy. The borrow_origin
                    # marking on the loop var blocks reassign / take /
                    # move-out; the iterator's lock on the source list
                    # excludes concurrent writers.
                    none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(f"{inner}if ({tmp}.tag == {none_tag}) break;\n")
                    elem_is_valtype = bool(
                        elem_type is not None and elem_type.is_valtype
                    )
                    if elem_is_valtype:
                        parts.append(
                            f"{inner}{elem_ctype} {iname_c} = "
                            f"*({elem_ctype}*){tmp}.data;\n"
                        )
                    else:
                        ptr_name = f"__borrow_{iname_c}"
                        parts.append(
                            f"{inner}{elem_ctype}* {ptr_name} = "
                            f"({elem_ctype}*){tmp}.data;\n"
                        )
                        prev_alias = self._alias_map.get(iname)
                        self._alias_map[iname] = f"(*{ptr_name})"
                        optionview_aliases.append((iname, prev_alias))
                else:
                    # Tagged-union `option t: ref`: the payload is a heap-
                    # boxed value whose destructor would free the inner
                    # allocation. A shallow copy into the binding followed
                    # by the union destructor double-frees — instead, move
                    # the payload out, free just the box, and register the
                    # per-iteration cleanup so the binding owns the value
                    # for the loop body.
                    none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(
                        f"{inner}if ({tmp}.tag == {none_tag}) "
                        f"{{ z_{opt_name}_destroy(&{tmp}); break; }}\n"
                    )
                    parts.append(
                        f"{inner}{elem_ctype} {iname_c} = *({elem_ctype}*){tmp}.data;\n"
                    )
                    parts.append(f"{inner}free({tmp}.data);\n")
                    # queue a matching destructor for end-of-iteration
                    cleanup = self._iter_binding_destructor(iname_c, elem_type)
                    if cleanup is not None:
                        iter_cleanup.append(f"{inner}{cleanup}\n")
            if fornode.loop:
                parts.append(self._emit_for_body(fornode))
            for c in iter_cleanup:
                parts.append(c)
            if has_post:
                post_str = " && ".join(post_exprs)
                parts.append(f"{inner}if (!({post_str})) break;\n")
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
            # Restore optionview aliases now that the body has been emitted
            # (innermost-set first so an outer for-loop's alias for the same
            # name is restored last).
            for alias_name, prev_alias in reversed(optionview_aliases):
                if prev_alias is None:
                    self._alias_map.pop(alias_name, None)
                else:
                    self._alias_map[alias_name] = prev_alias
        elif has_post and not has_pre:
            # pure post-condition: do { body } while (postcond);
            parts.append(f"{indent}do {{\n")
            if fornode.loop:
                self.indent_level += 1
                parts.append(self._emit_for_body(fornode))
                self.indent_level -= 1
            post_str = " && ".join(post_exprs)
            parts.append(f"{indent}}} while ({post_str});\n")
        else:
            # pre-condition (with optional post-condition break)
            if not cond_exprs:
                cond_str = "1"
            elif len(cond_exprs) == 1:
                cond_str = _unwrap_outer_parens(cond_exprs[0])
            else:
                cond_str = " && ".join(cond_exprs)
            # `for_cond_preamble` carries synth Assignments hoisted out
            # of the while-form cond's arg processing. They must
            # re-evaluate each iteration; emit them inside the loop
            # body alongside an early-break check so the cond is
            # re-tested against fresh per-iteration values. Without
            # this, the parent statement's preamble drains the synth
            # once before the loop and the cond spins on a stale temp.
            cond_preamble_stmts = self.typing.for_cond_preamble.get(fornode.nodeid, [])
            if cond_preamble_stmts:
                parts.append(f"{indent}while (1) {{\n")
                self.indent_level += 1
                for sl in cond_preamble_stmts:
                    parts.append(self._emit_statement_line(sl))
                inner = self._indent()
                parts.append(f"{inner}if (!({cond_str})) break;\n")
                if fornode.loop:
                    parts.append(self._emit_for_body(fornode))
                if has_post:
                    post_str = " && ".join(post_exprs)
                    parts.append(f"{inner}if (!({post_str})) break;\n")
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            else:
                parts.append(f"{indent}while ({cond_str}) {{\n")
                if fornode.loop:
                    self.indent_level += 1
                    parts.append(self._emit_for_body(fornode))
                    self.indent_level -= 1
                if has_post:
                    self.indent_level += 1
                    inner = self._indent()
                    self.indent_level -= 1
                    post_str = " && ".join(post_exprs)
                    parts.append(f"{inner}if (!({post_str})) break;\n")
                parts.append(f"{indent}}}\n")

        return "".join(parts)

    def _emit_for_body(self, fornode: zast.For) -> str:
        """Emit for-loop body, with optional list comprehension append.

        Wraps the body in a block scope so any class-typed locals
        declared inside the loop get destroyed at iteration end (not
        leaked to function exit, where their C-variable names are out
        of scope).
        """
        if not fornode.loop:
            return ""
        self._push_block_scope()
        state = self._comprehension_state.get(fornode.nodeid)
        if state is None:
            body = self._emit_statement(fornode.loop)
        else:
            list_var, list_name = state
            parts: List[str] = []
            stmts = fornode.loop.statements
            for sl in stmts[:-1]:
                parts.append(self._emit_statement_line(sl))
            last = stmts[-1].statementline
            if last.nodetype == NodeType.EXPRESSION:
                val = self._emit_expression_value(cast(zast.Expression, last))
                indent = self._indent()
                parts.append(f"{indent}z_{list_name}_append(&{list_var}, {val});\n")
            else:
                parts.append(self._emit_statement_line(stmts[-1]))
            body = "".join(parts)
        return body + self._pop_block_scope_and_emit()

    def _emit_for_expression_value(self, fornode: zast.For) -> str:
        """Emit for-as-expression (list comprehension): returns a list."""
        list_type = self._node_ztype(fornode)
        assert list_type is not None
        list_ctype = _ctype(self.typing, list_type)
        list_name = list_type.name
        tmp = self._temp_name("fl")
        indent = self._indent()
        self._temp.decls.append(
            f"{indent}{list_ctype} {tmp} = z_{list_name}_create(0);\n"
        )
        self._temp.frees.append(tmp)
        if list_name not in self._temp.class_set:
            self._temp.class_set[tmp] = list_name
        self._comprehension_state[fornode.nodeid] = (tmp, list_name)
        self._temp.decls.append(self._emit_for(fornode))
        del self._comprehension_state[fornode.nodeid]
        if tmp in self._temp.frees:
            self._temp.frees.remove(tmp)
        return tmp

    def _emit_do(self, donode: zast.Do) -> str:
        _do_has_break = self.typing.do_has_break.get(donode.nodeid, False)
        if _do_has_break:
            indent = self._indent()
            parts = [f"{indent}do {{\n"]
            self.indent_level += 1
            parts.append(self._emit_statement(donode.statement))
            self.indent_level -= 1
            parts.append(f"{indent}}} while (0);\n")
            return "".join(parts)
        return self._emit_statement(donode.statement)

    def _emit_do_expression_value(self, donode: zast.Do) -> str:
        """Emit a bare block as an expression, returning the last expression's value."""
        _do_has_break = self.typing.do_has_break.get(donode.nodeid, False)
        _do_ztype = self._node_ztype(donode)
        ctype = _ctype(self.typing, _do_ztype)
        tmp = self._temp_name("do")
        indent = self._indent()
        if _do_has_break:
            # optional result: default to none, set to some on normal completion
            opt_type = _do_ztype
            opt_name = opt_type.name if opt_type else ""
            none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
            some_tag = f"Z_{opt_name.upper()}_TAG_SOME"
            self._temp.decls.append(f"{indent}{ctype} {tmp};\n")
            self._temp.decls.append(f"{indent}{tmp}.tag = {none_tag};\n")
            self._temp.decls.append(f"{indent}do {{\n")
            self.indent_level += 1
            body = self._emit_branch_with_result_optional(
                donode.statement, tmp, some_tag
            )
            self._temp.decls.append(body)
            self.indent_level -= 1
            self._temp.decls.append(f"{indent}}} while (0);\n")
        else:
            self._temp.decls.append(f"{indent}{ctype} {tmp};\n")
            self._temp.decls.append(f"{indent}{{\n")
            self.indent_level += 1
            body = self._emit_branch_with_result(donode.statement, tmp)
            self._temp.decls.append(body)
            self.indent_level -= 1
            self._temp.decls.append(f"{indent}}}\n")
        return tmp

    def _emit_with(self, withnode: zast.With) -> str:
        indent = self._indent()
        parts: List[str] = []

        val_type = self._get_expression_type(withnode.value)
        ctype = "int64_t"
        if val_type:
            ctype = _ctype(self.typing, val_type)

        is_string = ctype == "z_String_t"
        is_class = ctype.startswith("z_") and ctype.endswith("_t*")
        is_union = val_type and val_type.typetype == ZTypeType.UNION
        cname = self._def_cname(withnode) or mangle_var_name(withnode.name)

        _with_ownership = self.typing.with_ownership.get(withnode.nodeid)
        _with_alias_of = self.typing.with_alias_of.get(withnode.nodeid)
        # BORROW bindings do not own the value — no destructor at scope exit
        # and no adoption of reftype temps.
        is_owned = _with_ownership != ZOwnership.BORROWED

        # Phase B alias optimization: when the RHS is a plain path reference,
        # skip the C local declaration and substitute at reference sites.
        if _with_alias_of is not None:
            alias_expr = self._alias_c_expr(_with_alias_of)
            prev = self._alias_map.get(withnode.name)
            self._alias_map[withnode.name] = alias_expr
            parts.append(f"{indent}{{\n")
            self.indent_level += 1
            inner_indent = self._indent()
            parts.append(f"{inner_indent}/* alias: {cname} => {alias_expr} */\n")
            self._temp_stack.append(TempState())
            doexpr_code = self._emit_expression_stmt(withnode.doexpr)
            parts.append("".join(self._temp.decls))
            parts.append(doexpr_code)
            for t in self._temp.frees:
                if t in self._temp.string_set:
                    parts.append(f"{inner_indent}z_String_free(&{t});\n")
                elif t in self._temp.class_set:
                    parts.append(
                        f"{inner_indent}{self._emit_class_free(t, self._temp.class_set[t])}\n"
                    )
                else:
                    parts.append(f"{inner_indent}free({t});\n")
            self._temp_stack.pop()
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
            if prev is None:
                del self._alias_map[withnode.name]
            else:
                self._alias_map[withnode.name] = prev
            return "".join(parts)

        val = self._emit_expression_value(withnode.value)

        # if value is a reftype temp and the with var owns it, adopt it
        if is_owned and (is_string or is_class) and val in self._temp.frees:
            self._temp.frees.remove(val)

        parts.append(f"{indent}{{\n")
        self.indent_level += 1
        inner_indent = self._indent()
        parts.append(f"{inner_indent}{ctype} {cname} = {val};\n")

        # If the RHS is `source.take`, invalidate the outer-scope source so
        # scope exit doesn't double-free (ownership moved into cname).
        if is_owned:
            take_var = self._get_take_var_from_expr(withnode.value)
            if take_var:
                parts.append(
                    self._emit_take_invalidation(
                        take_var, self._node_ztype(withnode.value), inner_indent
                    )
                )

        # doexpr may reference the with variable, so its temps must be
        # declared inside the block (not prepended to the outer statement)
        self._temp_stack.append(TempState())
        # Block-local user bindings (anything cleanup_stack grew during
        # the doexpr — notably typecheck-hoisted synth temps `_tN`) are
        # destroyed at *this* block's scope-exit rather than leaking to
        # function-exit, where they'd reference out-of-scope C locals.
        self._push_block_scope()

        doexpr_code = self._emit_expression_stmt(withnode.doexpr)

        # emit doexpr temps inside the with block
        parts.append("".join(self._temp.decls))
        parts.append(doexpr_code)
        parts.append(self._pop_block_scope_and_emit())
        for t in self._temp.frees:
            if t in self._temp.string_set:
                parts.append(f"{inner_indent}z_String_free(&{t});\n")
            elif t in self._temp.class_set:
                parts.append(
                    f"{inner_indent}{self._emit_class_free(t, self._temp.class_set[t])}\n"
                )
            else:
                parts.append(f"{inner_indent}free({t});\n")

        self._temp_stack.pop()

        if is_owned:
            if is_union and val_type:
                parts.append(f"{inner_indent}z_{val_type.name}_destroy({cname});\n")
            elif is_string:
                parts.append(f"{inner_indent}z_String_free(&{cname});\n")
            elif is_class and val_type:
                parts.append(f"{inner_indent}z_{val_type.name}_destroy({cname});\n")
            elif is_class:
                parts.append(f"{inner_indent}if ({cname}) free({cname});\n")
        self.indent_level -= 1
        parts.append(f"{indent}}}\n")
        return "".join(parts)

    def _emit_case(self, casenode: zast.Case) -> str:
        _case_taken_vars = self.typing.case_taken_vars.get(casenode.nodeid, [])
        _case_subject_taken = self.typing.case_subject_taken.get(casenode.nodeid, False)
        # (case decorations already read above from ZTyping)
        indent = self._indent()
        parts: List[str] = []

        # check if subject is a union or variant type
        subject_type = self._get_operation_type(casenode.subject)
        if subject_type and subject_type.typetype == ZTypeType.UNION:
            return self._emit_union_case(casenode, subject_type)
        if subject_type and subject_type.typetype == ZTypeType.VARIANT:
            return self._emit_variant_case(casenode, subject_type)

        # constant folding: if subject has const_value, emit only matching arm
        subject_cv = self._node_const_value(casenode.subject)
        if subject_cv is not None:
            matched_clause = None
            for clause in casenode.clauses:
                clause_match_cv = self._node_const_value(clause.match)
                if clause_match_cv is not None and subject_cv == clause_match_cv:
                    matched_clause = clause
                    break
            if matched_clause is not None:
                parts.append(f"{indent}{{\n")
                self.indent_level += 1
                parts.append(self._emit_statement(matched_clause.statement))
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
                return "".join(parts)
            if all(
                self._node_const_value(c.match) is not None for c in casenode.clauses
            ):
                # all patterns const, none matched — emit else if present
                if casenode.elseclause:
                    parts.append(f"{indent}{{\n")
                    self.indent_level += 1
                    parts.append(self._emit_statement(casenode.elseclause))
                    self.indent_level -= 1
                    parts.append(f"{indent}}}\n")
                return "".join(parts)

        subject = self._emit_operation_value(casenode.subject)

        # use if/else if chain (case values may not be compile-time constants in C)
        for i, clause in enumerate(casenode.clauses):
            match_val = self._emit_atomid_value(clause.match)
            keyword = "if" if i == 0 else "} else if"
            parts.append(f"{indent}{keyword} ({subject} == {match_val}) {{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1

        if casenode.elseclause:
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1

        parts.append(f"{indent}}}\n")

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(_case_taken_vars, indent))

        return "".join(parts)

    def _emit_union_case(self, casenode: zast.Case, union_type: ZType) -> str:
        _case_taken_vars = self.typing.case_taken_vars.get(casenode.nodeid, [])
        _case_subject_taken = self.typing.case_subject_taken.get(casenode.nodeid, False)
        _case_subject_taken_arms = self.typing.case_subject_taken_arms.get(
            casenode.nodeid, set()
        )
        # nullable-ptr option: if (ptr != NULL) / else
        if union_type.is_nullable_ptr:
            return self._emit_nullable_ptr_case(casenode, union_type)

        indent = self._indent()
        parts: List[str] = []
        union_name = union_type.name
        union_ctype = _cname_of(union_type, union_name)
        union_cbase = _cbase_of(union_type, union_name)

        subject = self._emit_operation_value(casenode.subject)
        alias_name = self._narrow_alias_name(casenode.subject)

        parts.append(f"{indent}switch ({subject}.tag) {{\n")
        for clause in casenode.clauses:
            sname = clause.match.name
            tag = f"Z_{union_name.upper()}_TAG_{sname.upper()}"
            parts.append(f"{indent}    case {tag}: {{\n")
            self.indent_level += 2
            arm_indent = self._indent()
            restore = self._push_narrow_alias(
                alias_name,
                union_type,
                sname,
                self._case_clause_match_child_id(clause),
                subject,
                parts,
                arm_indent,
            )
            self._push_block_scope()
            parts.append(self._emit_statement(clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            restore()
            # Per-arm subject-take cleanup: when the arm took the
            # subject, the heap payload has been moved into another
            # owner. Zero the inner payload so the post-switch
            # `z_<union>_destroy` calls the payload's destructor on a
            # zeroed struct (free(NULL) no-op) while still freeing the
            # `void* data` wrapper allocation. Non-taking arms leave
            # the subject untouched and the post-switch destroy frees
            # the full chain normally.
            if sname in _case_subject_taken_arms:
                payload_type = self.typing.child_by_id(
                    union_type, self._case_clause_match_child_id(clause)
                )
                if payload_type is not None and payload_type.typetype != ZTypeType.NULL:
                    payload_ctype = _ctype(self.typing, payload_type)
                    parts.append(
                        f"{arm_indent}*({payload_ctype}*){subject}.data = "
                        f"({payload_ctype}){{0}};\n"
                    )
            parts.append(f"{arm_indent}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            self._push_block_scope()
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(self._pop_block_scope_and_emit())
            if "else" in _case_subject_taken_arms:
                # Else-arm narrowing keeps the full union type, so
                # there's no per-arm payload to zero — only the union
                # wrapper. Zero the whole union; post-switch destroy
                # will no-op on the zeroed value.
                parts.append(f"{self._indent()}{subject} = ({union_ctype}){{0}};\n")
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")

        # post-match cleanup: destroy subject if taken in any arm but not all
        if _case_subject_taken:
            parts.append(
                f"{indent}{union_cbase}_destroy(&{subject});\n"
                f"{indent}{subject} = ({union_ctype}){{0}};\n"
            )

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(_case_taken_vars, indent))

        return "".join(parts)

    def _emit_nullable_ptr_case(self, casenode: zast.Case, union_type: ZType) -> str:
        """Emit case matching for nullable-ptr option: if (ptr != NULL) / else."""
        _case_taken_vars = self.typing.case_taken_vars.get(casenode.nodeid, [])
        _case_subject_taken = self.typing.case_subject_taken.get(casenode.nodeid, False)
        indent = self._indent()
        parts: List[str] = []
        subject = self._emit_operation_value(casenode.subject)

        # find some and none clauses
        some_clause = None
        none_clause = None
        for clause in casenode.clauses:
            if clause.match.name == "some":
                some_clause = clause
            elif clause.match.name == "none":
                none_clause = clause

        if some_clause and none_clause:
            parts.append(f"{indent}if ({subject} != NULL) {{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(some_clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(none_clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif some_clause:
            parts.append(f"{indent}if ({subject} != NULL) {{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(some_clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                self._push_block_scope()
                parts.append(self._emit_statement(casenode.elseclause))
                parts.append(self._pop_block_scope_and_emit())
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif none_clause:
            parts.append(f"{indent}if ({subject} == NULL) {{\n")
            self.indent_level += 1
            self._push_block_scope()
            parts.append(self._emit_statement(none_clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                self._push_block_scope()
                parts.append(self._emit_statement(casenode.elseclause))
                parts.append(self._pop_block_scope_and_emit())
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        # post-match cleanup for nullable-ptr
        if _case_subject_taken:
            union_name = union_type.name
            parts.append(
                f"{indent}if ({subject} != NULL) {{\n"
                f"{indent}    {_cbase_of(union_type, union_name)}_destroy({subject});\n"
                f"{indent}}}\n"
                f"{indent}{subject} = NULL;\n"
            )

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(_case_taken_vars, indent))

        return "".join(parts)

    def _emit_variant_case(self, casenode: zast.Case, variant_type: ZType) -> str:
        _case_taken_vars = self.typing.case_taken_vars.get(casenode.nodeid, [])
        indent = self._indent()
        parts: List[str] = []
        variant_name = variant_type.name

        subject = self._emit_operation_value(casenode.subject)

        # Bool special case: the subject is a primitive C `bool`, not a
        # variant struct with a `.tag` field. Emit each arm as a plain
        # `if (subject)` / `if (!subject)` dispatch. Binary choice, no
        # narrowing needed (null-payload arms carry no payload).
        if variant_name == "bool":
            for i, clause in enumerate(casenode.clauses):
                arm = clause.match.name  # "true" or "false"
                cond = subject if arm == "true" else f"!{subject}"
                keyword = "if" if i == 0 else "else if"
                parts.append(f"{indent}{keyword} ({cond}) {{\n")
                self.indent_level += 1
                self._push_block_scope()
                parts.append(self._emit_statement(clause.statement))
                parts.append(self._pop_block_scope_and_emit())
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            if casenode.elseclause:
                parts.append(f"{indent}else {{\n")
                self.indent_level += 1
                self._push_block_scope()
                parts.append(self._emit_statement(casenode.elseclause))
                parts.append(self._pop_block_scope_and_emit())
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            parts.append(self._emit_taken_vars_cleanup(_case_taken_vars, indent))
            return "".join(parts)

        alias_name = self._narrow_alias_name(casenode.subject)

        parts.append(f"{indent}switch ({subject}.tag) {{\n")
        for clause in casenode.clauses:
            sname = clause.match.name
            tag = f"Z_{variant_name.upper()}_TAG_{sname.upper()}"
            parts.append(f"{indent}    case {tag}: {{\n")
            self.indent_level += 2
            arm_indent = self._indent()
            restore = self._push_narrow_alias(
                alias_name,
                variant_type,
                sname,
                self._case_clause_match_child_id(clause),
                subject,
                parts,
                arm_indent,
            )
            self._push_block_scope()
            parts.append(self._emit_statement(clause.statement))
            parts.append(self._pop_block_scope_and_emit())
            restore()
            parts.append(f"{arm_indent}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            self._push_block_scope()
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(self._pop_block_scope_and_emit())
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(_case_taken_vars, indent))

        return "".join(parts)

    def _emit_case_expression_value(self, casenode: zast.Case) -> str:
        """Emit match-as-expression using temp variable pattern."""
        ctype = "int64_t"
        if self._node_ztype(casenode):
            ctype = _ctype(self.typing, self._node_ztype(casenode))

        tmp = self._temp_name("match")
        indent = self._indent()

        # declare temp variable
        self._temp.decls.append(f"{indent}{ctype} {tmp};\n")

        subject_type = self._get_operation_type(casenode.subject)

        if subject_type and subject_type.typetype == ZTypeType.UNION:
            code = self._emit_union_case_expr(casenode, subject_type, tmp)
        elif subject_type and subject_type.typetype == ZTypeType.VARIANT:
            code = self._emit_variant_case_expr(casenode, subject_type, tmp)
        else:
            code = self._emit_simple_case_expr(casenode, tmp)

        self._temp.decls.append(code)

        # track reftype ownership
        if (
            self._node_ztype(casenode)
            and cast(ZType, self._node_ztype(casenode)).destructor_name is not None
        ):
            self._temp.frees.append(tmp)
            if ctype == "z_String_t":
                self._temp.string_set.add(tmp)
                self.needs_string = True

        return tmp

    def _emit_simple_case_expr(self, casenode: zast.Case, result_var: str) -> str:
        """Emit simple enum match-as-expression using if/else-if chain."""
        indent = self._indent()
        parts: List[str] = []

        # constant folding: emit only the matching arm's result
        subject_cv = self._node_const_value(casenode.subject)
        if subject_cv is not None:
            matched_clause = None
            for clause in casenode.clauses:
                clause_match_cv = self._node_const_value(clause.match)
                if clause_match_cv is not None and subject_cv == clause_match_cv:
                    matched_clause = clause
                    break
            if matched_clause is not None:
                parts.append(f"{indent}{{\n")
                self.indent_level += 1
                parts.append(
                    self._emit_branch_with_result(matched_clause.statement, result_var)
                )
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
                return "".join(parts)
            if all(
                self._node_const_value(c.match) is not None for c in casenode.clauses
            ):
                if casenode.elseclause:
                    parts.append(f"{indent}{{\n")
                    self.indent_level += 1
                    parts.append(
                        self._emit_branch_with_result(casenode.elseclause, result_var)
                    )
                    self.indent_level -= 1
                    parts.append(f"{indent}}}\n")
                return "".join(parts)

        subject = self._emit_operation_value(casenode.subject)

        for i, clause in enumerate(casenode.clauses):
            match_val = self._emit_atomid_value(clause.match)
            keyword = "if" if i == 0 else "} else if"
            parts.append(f"{indent}{keyword} ({subject} == {match_val}) {{\n")
            self.indent_level += 1
            parts.append(self._emit_branch_with_result(clause.statement, result_var))
            self.indent_level -= 1

        if casenode.elseclause:
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            parts.append(self._emit_branch_with_result(casenode.elseclause, result_var))
            self.indent_level -= 1

        parts.append(f"{indent}}}\n")
        return "".join(parts)

    def _emit_union_case_expr(
        self, casenode: zast.Case, union_type: ZType, result_var: str
    ) -> str:
        """Emit union match-as-expression using switch on tag."""
        if union_type.is_nullable_ptr:
            return self._emit_nullable_ptr_case_expr(casenode, union_type, result_var)

        indent = self._indent()
        parts: List[str] = []
        union_name = union_type.name
        subject = self._emit_operation_value(casenode.subject)
        alias_name = self._narrow_alias_name(casenode.subject)

        parts.append(f"{indent}switch ({subject}.tag) {{\n")
        for clause in casenode.clauses:
            sname = clause.match.name
            tag = f"Z_{union_name.upper()}_TAG_{sname.upper()}"
            parts.append(f"{indent}    case {tag}: {{\n")
            self.indent_level += 2
            arm_indent = self._indent()
            restore = self._push_narrow_alias(
                alias_name,
                union_type,
                sname,
                self._case_clause_match_child_id(clause),
                subject,
                parts,
                arm_indent,
            )
            parts.append(self._emit_branch_with_result(clause.statement, result_var))
            restore()
            parts.append(f"{arm_indent}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            parts.append(self._emit_branch_with_result(casenode.elseclause, result_var))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")
        return "".join(parts)

    def _emit_nullable_ptr_case_expr(
        self, casenode: zast.Case, union_type: ZType, result_var: str
    ) -> str:
        """Emit nullable-ptr match-as-expression using if/else."""
        indent = self._indent()
        parts: List[str] = []
        subject = self._emit_operation_value(casenode.subject)

        some_clause = None
        none_clause = None
        for clause in casenode.clauses:
            if clause.match.name == "some":
                some_clause = clause
            elif clause.match.name == "none":
                none_clause = clause

        if some_clause and none_clause:
            parts.append(f"{indent}if ({subject} != NULL) {{\n")
            self.indent_level += 1
            parts.append(
                self._emit_branch_with_result(some_clause.statement, result_var)
            )
            self.indent_level -= 1
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            parts.append(
                self._emit_branch_with_result(none_clause.statement, result_var)
            )
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif some_clause:
            parts.append(f"{indent}if ({subject} != NULL) {{\n")
            self.indent_level += 1
            parts.append(
                self._emit_branch_with_result(some_clause.statement, result_var)
            )
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                parts.append(
                    self._emit_branch_with_result(casenode.elseclause, result_var)
                )
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif none_clause:
            parts.append(f"{indent}if ({subject} == NULL) {{\n")
            self.indent_level += 1
            parts.append(
                self._emit_branch_with_result(none_clause.statement, result_var)
            )
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                parts.append(
                    self._emit_branch_with_result(casenode.elseclause, result_var)
                )
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        return "".join(parts)

    def _emit_variant_case_expr(
        self, casenode: zast.Case, variant_type: ZType, result_var: str
    ) -> str:
        """Emit variant match-as-expression using switch on tag."""
        indent = self._indent()
        parts: List[str] = []
        variant_name = variant_type.name
        subject = self._emit_operation_value(casenode.subject)

        # Bool special case: subject is a primitive C bool, not a struct.
        # Emit as if / else if on truthiness rather than switch-on-tag.
        if variant_name == "bool":
            for i, clause in enumerate(casenode.clauses):
                arm = clause.match.name
                cond = subject if arm == "true" else f"!{subject}"
                keyword = "if" if i == 0 else "else if"
                parts.append(f"{indent}{keyword} ({cond}) {{\n")
                self.indent_level += 1
                parts.append(
                    self._emit_branch_with_result(clause.statement, result_var)
                )
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            if casenode.elseclause:
                parts.append(f"{indent}else {{\n")
                self.indent_level += 1
                parts.append(
                    self._emit_branch_with_result(casenode.elseclause, result_var)
                )
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            return "".join(parts)

        alias_name = self._narrow_alias_name(casenode.subject)

        parts.append(f"{indent}switch ({subject}.tag) {{\n")
        for clause in casenode.clauses:
            sname = clause.match.name
            tag = f"Z_{variant_name.upper()}_TAG_{sname.upper()}"
            parts.append(f"{indent}    case {tag}: {{\n")
            self.indent_level += 2
            arm_indent = self._indent()
            restore = self._push_narrow_alias(
                alias_name,
                variant_type,
                sname,
                self._case_clause_match_child_id(clause),
                subject,
                parts,
                arm_indent,
            )
            parts.append(self._emit_branch_with_result(clause.statement, result_var))
            restore()
            parts.append(f"{arm_indent}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            parts.append(self._emit_branch_with_result(casenode.elseclause, result_var))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")
        return "".join(parts)


def emit(typing: ztyping.ZTyping) -> str:
    """Emit C source from a populated `ZTyping`."""
    emitter = CEmitter(typing)
    return emitter.emit()
