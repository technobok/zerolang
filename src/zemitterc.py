"""
ZeroLang C code emitter

Walks a type-checked AST and emits C source code.
Includes ownership-based memory management for strings (z_String_t*).
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Callable, cast

import zast
from zast import NodeType
import zemitterc_runtime as zrt
import zemitterc_templates as ztmpl
from ztypes import (
    ZType,
    ZTypeType,
    ZSubType,
    ControlKind,
    parse_number,
    ZParamOwnership,
    ZOwnership,
    NUMERIC_RANGES,
    is_tag_origin,
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
    is_stringview_type as _is_stringview_type,
    _unwrap_typedef,
)


def _is_collection_param_type(ptype: Optional[ZType]) -> bool:
    """True for list / listview / map (directly or via typedef) —
    the types that must be passed by pointer across a protocol
    boundary so in-place mutation stays visible."""
    return _is_list_type(ptype) or _is_listview_type(ptype) or _is_map_type(ptype)


def _proto_param_ctype(ptype: Optional[ZType]) -> str:
    """C type for a parameter in a protocol vtable / wrapper signature.

    Mutable-collection parameters (list, listview, map — directly or
    via a typedef wrapper like `bytes` / `byteview`) are passed by
    pointer so mutations (append, grow) are visible to the caller
    through the protocol boundary. This matches the native class-
    method ABI, which already takes these by pointer.

    Scalars, variants, records, strings, and other value types keep
    their usual by-value `_ctype`.
    """
    ct = _ctype(ptype)
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
    """Per-function cleanup state, pushed/popped at function boundaries."""

    # (mangled_var_name, ZType) pairs in insertion order for scope-exit cleanup
    cleanup_vars: list = field(default_factory=list)
    temp_counter: int = 0
    record_name: str = ""
    class_params: set = field(default_factory=set)
    func_nodeid: int = 0  # NodeID of enclosing function (for unique temp names)
    # Mangled names of locals bound to a borrow (assigned from `out T.borrow`
    # call result, or from a `.borrow`/`.lock` projection). Used by `.release`
    # emit to skip the destructor — freeing a borrow corrupts the source.
    borrowed_vars: set = field(default_factory=set)


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


def _ctype(ztype: Optional[ZType]) -> str:
    if not ztype:
        return "void"
    if ztype.typedef_base is not None:
        return _ctype(ztype.typedef_base)
    # nullable-ptr option: C type is the inner reftype's ctype (already a pointer)
    if ztype.is_nullable_ptr:
        some_type = ztype.children.get("some")
        if some_type:
            return _ctype(some_type)
        return "void*"
    # box(valtype): C type is pointer to the inner valtype's ctype
    if ztype.is_box:
        inner_type = ztype.generic_args.get("t")
        if inner_type:
            return f"{_ctype(inner_type)}*"
        return "void*"
    name = ztype.name
    if name in TYPEMAP:
        return TYPEMAP[name]
    if name == "String":
        return "z_String_t"
    if name == "StringView":
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


def _ctype_func_inline(ztype: ZType) -> str:
    """Generate an inline C function pointer type for a FUNCTION ZType.
    Returns e.g. 'int64_t (*)(int64_t, int64_t)'.
    """
    ret = ztype.return_type
    ret_ctype = _ctype(ret) if ret else "void"
    params: List[str] = []
    for k, v in ztype.children.items():
        params.append(_ctype(v))
    param_str = ", ".join(params) if params else "void"
    return f"{ret_ctype} (*)({param_str})"


def _mangle_func(name: str) -> str:
    """Mangle a zerolang function/global name for C."""
    if name == "main":
        return "z_main"
    return "z_" + name.replace(".", "_")


def _mangle_var(name: str) -> str:
    """Mangle a local variable name — only escape C reserved words."""
    if name in (
        "main",
        "break",
        "continue",
        "return",
        "switch",
        "case",
        "default",
        "if",
        "else",
        "for",
        "while",
        "do",
        "int",
        "float",
        "double",
        "char",
        "void",
        "struct",
        "union",
        "enum",
        "static",
        "const",
        "auto",
        "register",
        "extern",
        "volatile",
        "signed",
        "unsigned",
        "long",
        "short",
        "sizeof",
        "typedef",
        "goto",
        "abs",
        "exit",
    ):
        return f"v_{name}"
    return name


def _unwrap_outer_parens(s: str) -> str:
    """Strip one outer layer of parens if it wraps the whole expression.

    Used at statement sites (if/while/return/assignment RHS) where the
    surrounding syntax already supplies grouping, so binop expressions
    that defensively wrap themselves in parens produce noisy double-
    parenthesization. Safe on any C expression string: returns input
    unchanged when the outer parens do not match a full-width pair.
    """
    if len(s) < 2 or s[0] != "(" or s[-1] != ")":
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


def _is_definition_name(name: str, emitter: "CEmitter") -> bool:
    """Check if a name refers to a unit-level definition."""
    return emitter._resolved_type(name) is not None or name in emitter._const_names


class CEmitter:
    def __init__(self, program: zast.Program) -> None:
        self.program = program
        self.out: List[str] = []
        self.indent_level = 0
        self.needs_stdio = False
        self.needs_stdint = False
        self.needs_stdlib = False
        self.needs_string = False
        self.needs_stringview = False
        self.needs_io = False
        self.needs_os = False
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
        # final source map: C output line (1-based) → AST node ID
        self.source_map: List[Optional[int]] = []
        # track numeric constant names (no distinct ZTypeType for these)
        self._const_names: set[str] = set()
        self._protocol_defs: dict[str, zast.ObjectDef] = {}  # name -> AST node
        # Separate slot for the io stream protocols so io.file's
        # wrapper emission can find them even when user code shadows
        # the short name (e.g. a user-declared `reader` protocol).
        self._io_protocol_defs: dict[str, zast.ObjectDef] = {}
        self._facet_defs: dict[str, zast.ObjectDef] = {}  # name -> AST node
        self._facet_conformers: dict[
            str, list
        ] = {}  # facet name -> list of impl type names
        # (impl_type, proto_name) -> label for owned protocol create
        self._proto_conformance: Dict[tuple, str] = {}
        # qualified names like "calculator.op" for func pointer fields in 'is' sections
        self._is_func_fields: set[str] = set()
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

    def _resolved_type(self, name: str) -> Optional[ZType]:
        """Look up a name in the type checker's resolved dict.

        Tries the bare name first, then prefixed with the main unit name,
        then each other loaded unit. Mainunit has priority so that user
        definitions shadow any system namesake; the final fallback lets
        names re-exported through core (like `ioerror`, `seekorigin`)
        resolve against their definition unit when the caller writes
        them bare in a mainunit that does not redefine them.
        """
        t = self.program.resolved.get(name)
        if t is None:
            t = self.program.resolved.get(f"{self.program.mainunitname}.{name}")
        if t is None:
            for unitname in self.program.units:
                if unitname == self.program.mainunitname:
                    continue
                t = self.program.resolved.get(f"{unitname}.{name}")
                if t is not None:
                    break
        return t

    def _typetype_of(self, name: str) -> Optional[ZTypeType]:
        """Get the ZTypeType for a resolved name, or None if not found."""
        t = self._resolved_type(name)
        return t.typetype if t else None

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

    def _is_typedef(self, name: str) -> bool:
        """Check if a name is a typedef (has a typedef_base in the resolved type)."""
        t = self._resolved_type(name)
        return t is not None and t.typedef_base is not None

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
        if ftype.needs_destructor and ftype.destructor_name:
            # Stack-allocated types need & to get a pointer for the destructor
            if not ftype.is_heap_allocated:
                return f"{indent}{ftype.destructor_name}(&{access});\n"
            return f"{indent}{ftype.destructor_name}({access});\n"
        return ""

    def _emit_scope_cleanup(
        self, indent: str, exclude_var: Optional[str] = None
    ) -> str:
        """Emit cleanup code for all tracked function-scope variables.

        Uses ZType.destructor_name for type-driven cleanup.
        If exclude_var is set (return value), that variable is skipped.
        """
        result = ""
        for var_name, var_type in reversed(self._scope.cleanup_vars):
            if var_name != exclude_var:
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

    def _mangle_callable(self, name: str) -> str:
        """Mangle a callable name: unit-level definitions use _mangle_func, locals use _mangle_var."""
        # dotted names are always definition references (unit.func, record.method)
        if "." in name:
            return _mangle_func(name)
        if _is_definition_name(name, self):
            return _mangle_func(name)
        return _mangle_var(name)

    def _track_stdlib_unit_native(self, mangled: str) -> None:
        """Record use of an io- or os-unit native so emit_runtime_io /
        emit_runtime_os includes its C body. Per-name granularity keeps
        unused helpers out of every compiled program. Called from every
        path that turns a callable AST node into a C name, not just
        `_emit_callable_expr` — definition-name dotted paths are
        short-circuited earlier and would otherwise miss tracking."""
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
                # parse_f64 uses strtod + errno (ERANGE).
                if name == "parseF64":
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
            if name == "envNames":
                self.needs_string = True

    def _emit_callable_expr(self, call: zast.Call) -> str:
        """Emit the callable expression for a function call.

        Handles function pointer fields (struct field access) vs regular functions.
        """
        # monomorphized generic function call: use the mangled name
        ftype = call.callable.type
        if (
            ftype
            and ftype.typetype == ZTypeType.FUNCTION
            and ftype.generic_origin is not None
        ):
            return _mangle_func(ftype.name)

        # check if this is a function pointer field call (e.g. c.op)
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            ftype = cast(zast.DottedPath, call.callable).type
            if ftype and ftype.typetype == ZTypeType.FUNCTION:
                func_name = ftype.name
                if func_name in self._is_func_fields:
                    return self._emit_dotted_path_value(
                        cast(zast.DottedPath, call.callable)
                    )
                # use the resolved type name for proper qualification
                # (handles subunit functions like mymod.helper.square)
                if "." in func_name:
                    mangled = _mangle_func(func_name)
                    self._track_stdlib_unit_native(mangled)
                    return mangled
        callable_name = self._get_callable_name(call.callable)
        mangled = self._mangle_callable(callable_name)
        self._track_stdlib_unit_native(mangled)
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

        Gathers _const_names, _protocol_defs, _facet_defs, _is_func_fields,
        _proto_conformance, and _facet_conformers in a single walk.
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
                        if (
                            proto_name
                            and self._typetype_of(proto_name) == ZTypeType.PROTOCOL
                        ):
                            self._proto_conformance[(qname, proto_name)] = label
                        if (
                            proto_name
                            and self._typetype_of(proto_name) == ZTypeType.FACET
                        ):
                            self._proto_conformance[(qname, proto_name)] = label
                            self._facet_conformers.setdefault(proto_name, []).append(
                                qname
                            )
                        # constant in 'as' section
                        if apath.const_value is not None:
                            self._const_names.add(f"{qname}.{label}")
            elif defn_type in (NodeType.UNION, NodeType.VARIANT):
                if not self._is_generic_template(defn):
                    for mname in defn.functions():
                        self._is_func_fields.add(f"{qname}.{mname}")
                    for label, apath in defn.as_items.items():
                        if apath.const_value is not None:
                            self._const_names.add(f"{qname}.{label}")
            elif defn_type == NodeType.PROTOCOL:
                if not self._is_generic_template(defn):
                    self._protocol_defs[qname] = defn
            elif defn_type == NodeType.FACET:
                if not self._is_generic_template(defn):
                    self._facet_defs[qname] = defn
            elif defn_type == NodeType.ATOMID and _is_numeric_id(defn.name):
                self._const_names.add(qname)
            elif hasattr(defn, "const_value") and defn.const_value is not None:
                # unit-level expression that folded to a constant
                self._const_names.add(qname)

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
                if qname not in self._type_field_names:
                    if self._is_typedef_defn(defn):
                        continue
                    _, field_names, field_ctypes = self._collect_field_params(
                        qname, defn.is_items, defn.functions()
                    )
                    self._type_field_names[qname] = field_names
                    self._type_field_ctypes[qname] = field_ctypes
                    self._type_field_defaults[qname] = self._extract_field_defaults(
                        qname, defn.is_items, defn.functions()
                    )

    def _emit_unit_definitions(self, prefix: str, body: dict) -> None:
        """Recursively emit definitions from a unit body."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if self._is_generic_template(defn):
                continue
            # tag emitted output with the source AST node ID
            self._current_node_id = defn.nodeid if hasattr(defn, "nodeid") else None
            defn_type = defn.nodetype
            if defn_type == NodeType.UNIT:
                self._emit_unit_definitions(qname, defn.body)
            elif defn_type == NodeType.RECORD:
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
                self._emit_constant(qname, defn)
            elif hasattr(defn, "const_value") and type(defn.const_value) in (
                int,
                float,
            ):
                # unit-level expression that folded to a constant
                self._emit_folded_constant(qname, defn)

    def _emit_folded_constant(self, name: str, node: zast.Node) -> None:
        """Emit a compile-time folded constant as a static const."""
        v = node.const_value
        assert v is not None
        self.needs_stdint = True
        cname = _mangle_func(name)
        ctype = "int64_t"
        if node.type:
            ctype = TYPEMAP.get(node.type.name, "int64_t")
        if type(v) is float:
            self.data_defs.append(f"static const {ctype} {cname} = {repr(v)};\n")
        else:
            self.data_defs.append(f"static const {ctype} {cname} = {int(v)};\n")

    def _emit_as_constants(self, type_name: str, as_items: dict) -> None:
        """Emit static constants defined in an 'as' section."""
        for label, apath in as_items.items():
            if apath.const_value is not None:
                v = apath.const_value
                qname = f"{type_name}.{label}"
                cname = _mangle_func(qname)
                if type(v) is str:
                    # string constant: emit static stringview + alias
                    self.needs_stringview = True
                    escaped = self._escape_c_string(v)
                    sname = self._static_string(escaped)
                    self.data_defs.append(f"#define {cname} {sname}\n")
                elif type(v) is float:
                    self.needs_stdint = True
                    ctype = "int64_t"
                    if apath.type:
                        ctype = TYPEMAP.get(apath.type.name, "int64_t")
                    self.data_defs.append(f"static const {ctype} {cname} = {v};\n")
                else:
                    self.needs_stdint = True
                    ctype = "int64_t"
                    if apath.type:
                        ctype = TYPEMAP.get(apath.type.name, "int64_t")
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
                        if (
                            facet_name
                            and self._typetype_of(facet_name) == ZTypeType.FACET
                        ):
                            self._emit_facet_impl(qname, label, facet_name, defn)

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
                if self._resolved_type(pname) is None:
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
            if proto_name and self._resolved_type(proto_name) is not None:
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
        saved = self.struct_defs
        self.struct_defs = TrackedList(self)
        for name in self._IO_WRAPPER_NAMES:
            if not self._io_class_referenced(name):
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
        saved = self.struct_defs
        self.struct_defs = TrackedList(self)
        for name in self._IO_WRAPPER_NAMES:
            if not self._io_class_referenced(name):
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
                if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(name, label, proto_name, defn)
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
            ftype = fpath.type
            if ftype is None:
                return False
            if ftype.generic_origin is not None and not is_tag_origin(
                ftype.generic_origin
            ):
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
        t = self.program.resolved.get(key)
        if t is None:
            return False
        # A resolved record/class has its fields registered as
        # children with non-None types.
        if not t.children:
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
                    names.add(name)
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
            for ga in t.generic_args.values():
                if ga is None:
                    continue
                if check(ga):
                    return True
            return False

        for child in mono_type.children.values():
            if check(child):
                return True
        for ga in mono_type.generic_args.values():
            if ga is None:
                continue
            if check(ga):
                return True
        return False

    def _io_record_referenced(self, name: str) -> bool:
        """True when a system io record is used by the program (e.g.
        as the ok-arm payload of a monomorphized result). Avoids
        emitting dead struct definitions for unused types."""
        for mono, _ in self.program.mono_types:
            for child in mono.children.values():
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
        for mono, _ in self.program.mono_types:
            for child in mono.children.values():
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
        return out

    def emit(self) -> str:
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return "/* empty program */\n"

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

        # register monomorphized type names before emission
        for mono_type, _ in self.program.mono_types:
            # register in resolved dict so _typetype_of() works for mono types
            self.program.resolved[mono_type.name] = mono_type
            if _is_array_type(mono_type):
                continue
            if _is_str_type(mono_type):
                continue
            if _is_list_type(mono_type):
                continue
            if _is_map_type(mono_type):
                continue
            if mono_type.typetype == ZTypeType.RECORD:
                # pre-register field info so _build_meta_create_args works
                # during function body emission (before mono type emission)
                name = mono_type.name
                field_names_r: List[str] = []
                field_ctypes_r: List[str] = []
                for fn, ft in mono_type.children.items():
                    if ft.typetype == ZTypeType.FUNCTION:
                        continue
                    field_names_r.append(fn)
                    field_ctypes_r.append(_ctype(ft))
                self._type_field_names[name] = field_names_r
                self._type_field_ctypes[name] = field_ctypes_r
                defaults_r: Dict[str, str] = {}
                for fn, default_val in mono_type.param_defaults.items():
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
                for fn, ft in mono_type.children.items():
                    if ft.typetype == ZTypeType.FUNCTION:
                        continue
                    field_names.append(fn)
                    field_ctypes_list.append(_ctype(ft))
                self._type_field_names[name] = field_names
                self._type_field_ctypes[name] = field_ctypes_list
                defaults_c: Dict[str, str] = {}
                for fn, default_val in mono_type.param_defaults.items():
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
        for mono_type, template_defn in self.program.mono_types:
            if _is_str_type(mono_type):
                self._emit_mono_str(mono_type)

        # pre-register field info for all non-generic records/classes
        # so that construction calls work regardless of definition order
        self._pre_register_fields("", mainunit.body)

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
        mono_types_all = list(self.program.mono_types)
        user_type_names = self._collect_user_type_names(mainunit.body)
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
        # table (ZType.nodeid lives in a separate id space and would
        # produce orphan foreign-key references).
        for mono_type, template_defn in early_monos:
            self._current_node_id = template_defn.nodeid
            self._emit_mono_type(mono_type, template_defn)

        # cli classes (spec / parsed) — emitted AFTER early monos
        # because their fields reference `list of: flagdef` etc.
        self._emit_system_unit_definitions(
            include_cli=True, only_cli=True, cli_classes_only=True
        )

        # second pass: emit definitions (recursing into inline units)
        self._emit_unit_definitions("", mainunit.body)

        # third pass: emit facets (must come after all conforming types are defined)
        self._emit_deferred_facets("", mainunit.body)

        # late mono types (depend on user structs emitted above)
        for mono_type, template_defn in late_monos:
            self._current_node_id = template_defn.nodeid
            self._emit_mono_type(mono_type, template_defn)

        # emit monomorphized generic functions
        for mono_ftype, cloned_func in self.program.mono_functions:
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

        parts.append(
            zrt.emit_runtime(
                needs_stdio=self.needs_stdio,
                needs_stdint=self.needs_stdint,
                needs_stdlib=self.needs_stdlib,
                needs_string=self.needs_string,
                needs_stringview=self.needs_stringview,
                needs_io=self.needs_io,
                needs_pwd="userName" in self.needs_os_natives,
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

    def _emit_func_typedef(self, name: str, func: zast.Function) -> None:
        """Emit a C typedef for a function (placed after struct defs)."""
        self.needs_stdint = True
        ret_ctype = self._return_ctype(func)
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(ppath.type)
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
            ptype_str = _ctype(ppath.type)
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
        lines: List[str] = []

        # Prefer the resolved ZType for param types — the AST's
        # `ppath.type` can remain None for system-library protocols
        # until they're explicitly instantiated. The resolved type's
        # children hold function ZTypes whose own children hold
        # fully-resolved parameter types.
        proto_type = self._resolved_type(name)

        # vtable struct — function pointers with void* as first param
        lines.append("typedef struct {\n")
        for sname, sfunc in proto.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            params: List[str] = ["void*"]
            spec_type = proto_type.children.get(sname) if proto_type else None
            for pname, ppath in sfunc.parameters.items():
                if pname == "this":
                    continue
                ptype: Optional[ZType] = ppath.type
                if ptype is None and spec_type is not None:
                    ptype = spec_type.children.get(pname)
                params.append(_proto_param_ctype(ptype))
            param_str = ", ".join(params)
            lines.append(f"    {ret_ctype} (*{sname})({param_str});\n")
        lines.append(f"}} z_{name}_vtable_t;\n\n")

        # instance struct — data pointer + vtable pointer + destructor
        lines.append("typedef struct {\n")
        lines.append("    void* data;\n")
        lines.append(f"    z_{name}_vtable_t* vtable;\n")
        lines.append("    void (*destroy)(void*);\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destroy function
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto) {{\n")
        lines.append("    if (!proto) return;\n")
        lines.append("    if (proto->destroy) proto->destroy(proto->data);\n")
        lines.append("    proto->destroy = NULL;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_protocol_impl(
        self,
        impl_name: str,
        label: str,
        proto_name: str,
        impl_defn: "zast.ObjectDef",
        proto: "Optional[zast.ObjectDef]" = None,
    ) -> None:
        """Emit wrapper functions, static vtable, and create function for a protocol implementation."""
        if proto is None:
            proto = self._protocol_defs.get(proto_name)
        if not proto:
            return
        is_class = impl_defn.nodetype == NodeType.CLASS
        impl_ctype = f"z_{impl_name}_t"

        lines: List[str] = []

        # forward declarations for methods called by wrappers
        all_methods = dict(impl_defn.as_functions())
        all_methods.update(impl_defn.functions())
        impl_type = self._resolved_type(impl_name)
        for sname in proto.functions():
            mfunc = all_methods.get(sname)
            if mfunc and mfunc.body:
                ret_ctype = self._return_ctype(mfunc)
                params: List[str] = []
                for pname, ppath in mfunc.parameters.items():
                    ptype_str = _ctype(ppath.type)
                    # 'this' parameter: add * for class methods
                    if (
                        impl_type
                        and ppath.type is impl_type
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
        proto_type = self._resolved_type(proto_name)
        for sname, sfunc in proto.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            # wrapper params: void* _data, then remaining non-this params.
            # Collection types travel through the vtable as pointers
            # (see _proto_param_ctype) so they match the native impl's
            # ABI without an extra adaptor.
            spec_type = proto_type.children.get(sname) if proto_type else None
            wrapper_params: List[str] = ["void* _data"]
            call_args: List[str] = []
            for pname, ppath in sfunc.parameters.items():
                if pname == "this":
                    continue
                ptype: Optional[ZType] = ppath.type
                if ptype is None and spec_type is not None:
                    ptype = spec_type.children.get(pname)
                pctype = _proto_param_ctype(ptype)
                wrapper_params.append(f"{pctype} {_mangle_var(pname)}")
                call_args.append(_mangle_var(pname))

            wrapper_name = f"z_{impl_name}_{label}_{sname}_wrapper"
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
        vtable_name = f"z_{impl_name}_{label}_vtable"
        lines.append(f"static z_{proto_name}_vtable_t {vtable_name} = {{\n")
        for sname in proto.functions():
            wrapper_name = f"z_{impl_name}_{label}_{sname}_wrapper"
            lines.append(f"    .{sname} = {wrapper_name},\n")
        lines.append("};\n\n")

        # create function (borrowed — pointer to original, no copy)
        # Returns protocol struct by value; caller stores on stack.
        create_name = f"z_{impl_name}_{label}_create"
        lines.append(f"static z_{proto_name}_t {create_name}({impl_ctype}* val);\n")
        lines.append(f"static z_{proto_name}_t {create_name}({impl_ctype}* val) {{\n")
        lines.append(f"    z_{proto_name}_t proto = {{0}};\n")
        lines.append("    proto.data = val;\n")
        lines.append(f"    proto.vtable = &{vtable_name};\n")
        lines.append("    proto.destroy = NULL;\n")
        lines.append("    return proto;\n")
        lines.append("}\n\n")

        # owned create + destroy wrapper
        if is_class:
            # class: destroy frees boxed copy (+ field cleanup if needed)
            destroy_name = f"z_{impl_name}_{label}_owned_destroy"
            lines.append(f"static void {destroy_name}(void* p) {{\n")
            impl_zt = self._resolved_type(impl_name)
            if impl_zt and impl_zt.needs_field_cleanup:
                lines.append(f"    z_{impl_name}_destroy(({impl_ctype}*)p);\n")
            lines.append("    free(p);\n")
            lines.append("}\n\n")

            # stack class: owned create boxes the struct (malloc + copy)
            owned_create = f"z_{impl_name}_{label}_create_owned"
            lines.append(
                f"static z_{proto_name}_t {owned_create}({impl_ctype}* val);\n"
            )
            lines.append(
                f"static z_{proto_name}_t {owned_create}({impl_ctype}* val) {{\n"
            )
            lines.append(f"    z_{proto_name}_t proto = {{0}};\n")
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
            destroy_name = f"z_{impl_name}_{label}_boxed_destroy"
            lines.append(f"static void {destroy_name}(void* p) {{\n")
            lines.append(f"    {impl_ctype}* r = ({impl_ctype}*)p;\n")
            # cleanup reftype fields
            for fname, fpath in impl_defn.is_paths().items():
                if fpath.type:
                    lines.append(self._emit_field_cleanup(f"r->{fname}", fpath.type))
            lines.append("    free(r);\n")
            lines.append("}\n\n")

            owned_create = f"z_{impl_name}_{label}_create_owned"
            lines.append(f"static z_{proto_name}_t {owned_create}({impl_ctype} val);\n")
            lines.append(
                f"static z_{proto_name}_t {owned_create}({impl_ctype} val) {{\n"
            )
            lines.append(f"    z_{proto_name}_t proto = {{0}};\n")
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
        lines.append("typedef struct {\n")
        for sname, sfunc in facet.functions().items():
            ret_ctype = self._return_ctype(sfunc)
            params: List[str] = ["void*"]
            for pname, ppath in sfunc.parameters.items():
                if pname == "this":
                    continue
                params.append(_ctype(ppath.type))
            param_str = ", ".join(params)
            lines.append(f"    {ret_ctype} (*{sname})({param_str});\n")
        lines.append(f"}} z_{name}_vtable_t;\n\n")

        # data union — sized to largest conforming type (provides size + alignment)
        conformers = self._facet_conformers.get(name, [])
        lines.append("typedef union {\n")
        if conformers:
            for impl_name in conformers:
                lines.append(f"    z_{impl_name}_t _{impl_name.replace('.', '_')};\n")
        else:
            lines.append("    char _empty;\n")
        lines.append(f"}} z_{name}_data_u;\n\n")

        # instance struct — vtable first (constant offset), then inline data
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_vtable_t* vtable;\n")
        lines.append(f"    z_{name}_data_u data;\n")
        lines.append(f"}} z_{name}_t;\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_facet_impl(
        self,
        impl_name: str,
        label: str,
        facet_name: str,
        impl_defn: "zast.ObjectDef",
    ) -> None:
        """Emit wrapper functions, static vtable, and create function for a facet implementation."""
        facet = self._facet_defs.get(facet_name)
        if not facet:
            return
        impl_ctype = f"z_{impl_name}_t"

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
                    ptype_str = _ctype(ppath.type)
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
            for pname, ppath in sfunc.parameters.items():
                if pname == "this":
                    continue
                pctype = _ctype(ppath.type)
                wrapper_params.append(f"{pctype} {_mangle_var(pname)}")
                call_args.append(_mangle_var(pname))

            wrapper_name = f"z_{impl_name}_{label}_{sname}_wrapper"
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
        vtable_name = f"z_{impl_name}_{label}_vtable"
        lines.append(f"static z_{facet_name}_vtable_t {vtable_name} = {{\n")
        for sname in facet.functions():
            wrapper_name = f"z_{impl_name}_{label}_{sname}_wrapper"
            lines.append(f"    .{sname} = {wrapper_name},\n")
        lines.append("};\n\n")

        # create/take function — copies value into inline data buffer
        create_name = f"z_{impl_name}_{label}_create_owned"
        lines.append(f"static z_{facet_name}_t {create_name}({impl_ctype} val);\n")
        lines.append(f"static z_{facet_name}_t {create_name}({impl_ctype} val) {{\n")
        lines.append(f"    z_{facet_name}_t facet;\n")
        lines.append(f"    facet.vtable = &{vtable_name};\n")
        lines.append(f"    *({impl_ctype}*)&facet.data = val;\n")
        lines.append("    return facet;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_record(self, name: str, rec: zast.ObjectDef) -> None:
        if self._is_typedef(name):
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
                if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(name, label, proto_name, rec)
            return

        self.needs_stdint = True
        ztype = self._resolved_type(name)
        lock_fields = ztype.lock_field_names if ztype else set()
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in rec.is_paths().items():
            # System-unit records may reach the emitter before the
            # typechecker has attached types to AST paths; fall back
            # to the resolved ZType's children for a concrete type.
            field_type = fpath.type
            if field_type is None and ztype is not None:
                field_type = ztype.children.get(fname)
            ftype = _ctype(field_type)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                fname in lock_fields
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
        lines.append(f"}} z_{name}_t;\n\n")

        self.struct_defs.append("".join(lines))

        # emit meta.create constructor
        self._emit_meta_create(name, rec)

        # emit auto-generated equality function
        self._emit_autogen_eq(name, rec.is_paths(), rec.functions())

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
            if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                self._emit_protocol_impl(name, label, proto_name, rec)
            # facet impls are deferred to _emit_deferred_facets
        # emit 'as' constants
        self._emit_as_constants(name, rec.as_items)

    def _emit_autogen_eq(
        self,
        name: str,
        items: dict,
        functions: dict,
    ) -> None:
        """Emit a static z_{name}_eq() function for auto-generated == on records."""
        ztype = self._resolved_type(name)
        if not ztype:
            return
        eq_method = ztype.children.get("==")
        if not eq_method or not eq_method.is_autogen_eq:
            return

        ctype = f"z_{name}_t"
        lines: List[str] = []
        lines.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{\n")

        if self._use_memcmp_eq(name, eq_method):
            self.needs_string = True  # memcmp is in string.h
            lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
        else:
            comparisons: List[str] = []
            # data fields
            for fname, fpath in items.items():
                ft = fpath.type
                if ft and self._needs_eq_call(ft):
                    tname = ft.name.replace(".", "_")
                    # string/stringview eq functions take pointers
                    # (their C signature is `(z_X_t* a, z_X_t* b)`).
                    # Other auto-generated / native eq functions take
                    # their operands by value.
                    if ft.subtype == ZSubType.STRING:
                        comparisons.append(f"z_{tname}_eq(&a.{fname}, &b.{fname})")
                    else:
                        comparisons.append(f"z_{tname}_eq(a.{fname}, b.{fname})")
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
        eq = ztype.children.get("==")
        if eq is None:
            return False
        if eq.is_autogen_eq:
            return True
        # Native/user-defined ==. Primitives (ints, floats, bool) use
        # C ==; everything else is a struct and needs the named function.
        if ztype.name in NUMERIC_RANGES or ztype.name in (
            "bool",
            "f32",
            "f64",
            "f128",
        ):
            return False
        return True

    def _use_memcmp_eq(self, name: str, eq_method: ZType) -> bool:
        """Check if a type should use memcmp for equality.

        True when is_simple_eq and estimated size exceeds the threshold.
        """
        if not eq_method.is_simple_eq:
            return False
        return self._estimate_type_size(name) > _EQ_MEMCMP_THRESHOLD

    def _estimate_type_size(self, name: str) -> int:
        """Estimate byte size of a type from its C fields.

        Returns 0 if the size cannot be determined (conservative: caller
        should fall back to field-by-field comparison).
        """
        # check cached field ctypes from mono/record emission
        field_ctypes = self._type_field_ctypes.get(name)
        if field_ctypes:
            total = 0
            for ct in field_ctypes:
                sz = CTYPE_SIZES.get(ct, 0)
                if sz == 0:
                    # nested struct type: z_foo_t -> foo, recurse
                    if ct.startswith("z_") and ct.endswith("_t"):
                        inner_name = ct[2:-2]
                        sz = self._estimate_type_size(inner_name)
                    if sz == 0:
                        return 0  # unknown size
                total += sz
            return total
        # try resolved type's children for non-cached types
        ztype = self._resolved_type(name)
        if not ztype:
            return 0
        if ztype.typetype == ZTypeType.VARIANT:
            # variant: tag enum (4 bytes) + max(subtype sizes)
            max_sub = 0
            for fname, ftype in ztype.children.items():
                if ftype.typetype in (
                    ZTypeType.FUNCTION,
                    ZTypeType.TAG,
                    ZTypeType.ENUM,
                    ZTypeType.DATA,
                    ZTypeType.NULL,
                ):
                    continue
                if is_tag_origin(ftype.generic_origin):
                    continue
                ct = _ctype(ftype)
                sz = CTYPE_SIZES.get(ct, 0)
                if sz == 0 and ct.startswith("z_") and ct.endswith("_t"):
                    sz = self._estimate_type_size(ct[2:-2])
                if sz > max_sub:
                    max_sub = sz
            return 4 + max_sub  # tag + largest union member
        # record: sum of field sizes
        total = 0
        for fname, ftype in ztype.children.items():
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
            if is_tag_origin(ftype.generic_origin):
                continue
            ct = _ctype(ftype)
            sz = CTYPE_SIZES.get(ct, 0)
            if sz == 0 and ct.startswith("z_") and ct.endswith("_t"):
                sz = self._estimate_type_size(ct[2:-2])
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
        eq_method = mono_type.children.get("==")
        if not eq_method or not eq_method.is_autogen_eq:
            return

        ctype = f"z_{name}_t"
        lines.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{\n")

        if self._use_memcmp_eq(name, eq_method):
            self.needs_string = True
            lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
        else:
            comparisons: List[str] = []
            for fname, ct in field_items:
                ftype = mono_type.children.get(fname)
                if ftype and self._needs_eq_call(ftype):
                    tname = ftype.name.replace(".", "_")
                    if ftype.subtype == ZSubType.STRING:
                        comparisons.append(f"z_{tname}_eq(&a.{fname}, &b.{fname})")
                    else:
                        comparisons.append(f"z_{tname}_eq(a.{fname}, b.{fname})")
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
        Returns e.g. 'int64_t (*callback)(int64_t, int64_t)'"""
        ret_ctype = self._return_ctype(mfunc)
        params: List[str] = []
        for pname, ppath in mfunc.parameters.items():
            ptype_str = _ctype(ppath.type)
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        return f"{ret_ctype} (*{mname})({param_str})"

    def _collect_field_params(self, name: str, items: dict, functions: dict) -> tuple:
        """Collect C parameter strings, field names, and field C types.

        Returns (params, field_names, field_ctypes).
        """
        # check lock field names for the type (classes with .lock fields
        # store stack-allocated class fields as pointers)
        ztype = self._resolved_type(name)
        lock_fields = ztype.lock_field_names if ztype else set()

        params: List[str] = []
        field_names: List[str] = []
        field_ctypes: List[str] = []
        for fname, fpath in items.items():
            # System-unit records may reach this path with unresolved
            # AST fpath.type; fall back to the resolved ZType's
            # child for a concrete C type.
            field_type = fpath.type
            if field_type is None and ztype is not None:
                field_type = ztype.children.get(fname)
            fct = _ctype(field_type)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                fname in lock_fields
                and field_type
                and field_type.typetype == ZTypeType.CLASS
                and not field_type.is_heap_allocated
                and not fct.endswith("*")
            ):
                fct = f"{fct}*"
            params.append(f"{fct} {fname}")
            field_names.append(fname)
            field_ctypes.append(fct)
        for mname, mfunc in functions.items():
            ret_ctype = self._return_ctype(mfunc)
            fp_params: List[str] = []
            for pname, ppath in mfunc.parameters.items():
                fp_params.append(_ctype(ppath.type))
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
                if cast(
                    zast.DottedPath, fpath
                ).parent.nodetype == NodeType.ATOMID and _is_numeric_id(
                    cast(zast.AtomId, cast(zast.DottedPath, fpath).parent).name
                ):
                    child_name = cast(zast.DottedPath, fpath).child.name
                    dct = TYPEMAP.get(child_name, "int64_t")
                    typename, value, err = parse_number(
                        cast(zast.AtomId, cast(zast.DottedPath, fpath).parent).name
                        + child_name
                    )
                    if not err:
                        if typename.startswith("f"):
                            field_defaults[fname] = f"(({dct}){value})"
                        else:
                            field_defaults[fname] = f"(({dct}){int(value)})"
            elif (
                fpath.nodetype == NodeType.ATOMID
                and self._typetype_of(cast(zast.AtomId, fpath).name)
                == ZTypeType.FUNCTION
            ):
                field_defaults[fname] = _mangle_func(cast(zast.AtomId, fpath).name)
        for mname, mfunc in functions.items():
            if mfunc.body is not None:
                field_defaults[mname] = _mangle_func(f"{name}.{mname}")
        return field_defaults

    def _emit_create_functions(
        self,
        name: str,
        ctype: str,
        params: List[str],
        field_names: List[str],
        is_heap: bool,
        has_user_create: bool,
        lines: List[str],
    ) -> None:
        """Emit meta.create and optional create forwarding functions."""
        param_str = ", ".join(params) if params else "void"
        func_name = f"z_{name}_meta_create"
        ret_type = f"{ctype}*" if is_heap else ctype
        lines.append(f"static {ret_type} {func_name}({param_str}) {{\n")
        if is_heap:
            lines.append(
                f"    {ctype}* _this = ({ctype}*)z_xmalloc(sizeof({ctype}));\n"
            )
            lines.append(f"    *_this = ({ctype}){{0}};\n")
            accessor = "->"
        else:
            lines.append(f"    {ctype} _this = {{0}};\n")
            accessor = "."
        for fname in field_names:
            lines.append(f"    _this{accessor}{fname} = {fname};\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")
        if not has_user_create:
            # Trivial delegate: `z_X_create` just forwards to meta_create
            # with the same signature and arguments. A preprocessor alias
            # has the same effect and skips a function body per type.
            # Emit inline next to meta_create so callers within struct_defs
            # (e.g. array default-init) see the alias before use.
            create_name = f"z_{name}_create"
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
        ztype = self._resolved_type(name)
        is_heap = ztype.is_heap_allocated if ztype else False
        ctype = f"z_{name}_t"
        params, field_names, field_ctypes = self._collect_field_params(
            name, rc_defn.is_paths(), rc_defn.functions()
        )
        self._type_field_ctypes[name] = field_ctypes
        self._type_field_names[name] = field_names
        self._type_field_defaults[name] = self._extract_field_defaults(
            name, rc_defn.is_paths(), rc_defn.functions()
        )
        has_user_create = (
            "create" in rc_defn.functions() or "create" in rc_defn.as_functions()
        )
        target: List[str] = lines if lines is not None else []
        self._emit_create_functions(
            name,
            ctype,
            params,
            field_names,
            is_heap=is_heap,
            has_user_create=has_user_create,
            lines=target,
        )
        if lines is None:
            self.struct_defs.append("".join(target))

    def _emit_class(
        self, name: str, cls: zast.ObjectDef, skip_protocol_impls: bool = False
    ) -> None:
        if self._is_typedef(name):
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
                    if (
                        proto_name
                        and self._typetype_of(proto_name) == ZTypeType.PROTOCOL
                    ):
                        self._emit_protocol_impl(name, label, proto_name, cls)
            return

        self.needs_stdint = True
        self.needs_stdlib = True
        ztype = self._resolved_type(name)
        lock_fields = ztype.lock_field_names if ztype else set()
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in cls.is_paths().items():
            ftype = _ctype(fpath.type)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                fname in lock_fields
                and fpath.type
                and fpath.type.typetype == ZTypeType.CLASS
                and not fpath.type.is_heap_allocated
                and not ftype.endswith("*")
            ):
                ftype = f"{ftype}*"
            lines.append(f"    {ftype} {fname};\n")
        # emit function pointer fields from 'is' section
        for mname, mfunc in cls.functions().items():
            decl = self._func_pointer_field_decl(name, mname, mfunc)
            lines.append(f"    {decl};\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # emit destructor (only if class has fields needing cleanup)
        if ztype and ztype.needs_field_cleanup:
            lines.append(f"static void z_{name}_destroy(z_{name}_t* p) {{\n")
            lines.append("    if (!p) return;\n")
            for fname, fpath in cls.is_paths().items():
                # .lock fields are borrowed references, don't own data
                if fname in lock_fields:
                    continue
                if fpath.type:
                    lines.append(self._emit_field_cleanup(f"p->{fname}", fpath.type))
            lines.append("}\n\n")

        # emit meta.create constructor
        self._emit_meta_create(name, cls, lines)

        self.struct_defs.append("".join(lines))

        # emit 'is' functions with body as regular C functions (for default values)
        for mname, mfunc in cls.functions().items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' functions as methods
        for mname, mfunc in cls.as_functions().items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit protocol implementations
        if not skip_protocol_impls:
            for label, apath in cls.as_items.items():
                proto_name = (
                    cast(zast.AtomId, apath).name
                    if apath.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
                    else None
                )
                if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(name, label, proto_name, cls)
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

        Mirrors the type-checker's `lock_arm_names` on the resolved ZType,
        but read straight off the AST so it's available at emit time without
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

        # resolve custom tag values from as_items
        custom_tag_values = self._resolve_tag_values(union_defn)

        # emit tag enum
        lines.append("typedef enum {\n")
        tag_names = []
        for sname in union_defn.is_paths().keys():
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            tag_names.append(tag)
            if custom_tag_values and sname in custom_tag_values:
                lines.append(f"    {tag} = {custom_tag_values[sname]},\n")
            else:
                lines.append(f"    {tag},\n")
        lines.append(f"}} z_{name}_tag_t;\n\n")

        # emit union struct: always {tag, void*}
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_tag_t tag;\n")
        lines.append("    void* data;\n")
        lines.append(f"}} z_{name}_t;\n\n")

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
            lines.append(
                f"static void z_{name}_destroy(z_{name}_t* u) {{ (void)u; }}\n\n"
            )
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
            stype = spath.type
            if stype and stype.needs_destructor and stype.destructor_name:
                if stype.is_heap_allocated:
                    cast_expr = f"({_ctype(stype)})u->data"
                    return (f"            {stype.destructor_name}({cast_expr});\n",)
                ptr_ctype = f"{_ctype(stype)}*"
                return (
                    f"            {stype.destructor_name}(({ptr_ctype})u->data);\n",
                    "            free(u->data);\n",
                )
            return ("            free(u->data);\n",)

        lines.append(f"static void z_{name}_destroy(z_{name}_t* u) {{\n")
        lines.append("    if (!u) return;\n")
        lines.append("    if (!u->data) return;\n")
        lines.append("    switch (u->tag) {\n")
        pending_cases: list[str] = []
        pending_body: tuple[str, ...] | None = None
        for sname, spath in non_null_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
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
        cloned_methods = self.program.cloned_methods.get(mangled, {})
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
                aliases = self.program.func_aliases
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
        lines: List[str] = []

        # collect subtypes (non-special children)
        subtype_items: list[tuple[str, ZType]] = []
        for sname, stype in mono_type.children.items():
            if sname == "tag":
                continue
            if stype.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if is_tag_origin(stype.generic_origin):
                continue
            subtype_items.append((sname, stype))

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname, _ in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            lines.append(f"    {tag},\n")
        lines.append(f"}} z_{name}_tag_t;\n\n")

        # emit union struct
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_tag_t tag;\n")
        lines.append("    void* data;\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destructor: collapse to a no-op when every subtype is `null` or
        # a locked arm (locked arms hold a borrowed pointer the union does
        # not own — no cleanup needed).
        lock_arms_mono = mono_type.lock_arm_names

        def _is_no_cleanup_mono(sname: str, stype: ZType) -> bool:
            if stype.typetype == ZTypeType.NULL:
                return True
            return sname in lock_arms_mono

        all_no_cleanup_mono = all(
            _is_no_cleanup_mono(sname, stype) for sname, stype in subtype_items
        )
        if all_no_cleanup_mono:
            lines.append(
                f"static void z_{name}_destroy(z_{name}_t* u) {{ (void)u; }}\n\n"
            )
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
            if stype.needs_destructor and stype.destructor_name:
                if stype.is_heap_allocated:
                    cast_expr = f"({_ctype(stype)})u->data"
                    return (f"            {stype.destructor_name}({cast_expr});\n",)
                ptr_ctype = f"{_ctype(stype)}*"
                return (
                    f"            {stype.destructor_name}(({ptr_ctype})u->data);\n",
                    "            free(u->data);\n",
                )
            return ("            free(u->data);\n",)

        lines.append(f"static void z_{name}_destroy(z_{name}_t* u) {{\n")
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
        some_type = mono_type.children.get("some")
        if not some_type:
            return
        inner_ctype = _ctype(some_type)
        lines: List[str] = []
        # emit destructor: if non-null, destroy the inner value
        lines.append(f"static void z_{name}_destroy({inner_ctype} v) {{\n")
        lines.append("    if (!v) return;\n")
        if some_type.needs_destructor and some_type.destructor_name:
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
        lines: List[str] = []

        # collect subtypes (non-special children)
        subtype_items: list[tuple[str, ZType]] = []
        for sname, stype in mono_type.children.items():
            if sname == "tag":
                continue
            if stype.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if is_tag_origin(stype.generic_origin):
                continue
            subtype_items.append((sname, stype))

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname, _ in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            lines.append(f"    {tag},\n")
        lines.append(f"}} z_{name}_tag_t;\n\n")

        # check if all subtypes are null (enum pattern)
        all_null = all(stype.typetype == ZTypeType.NULL for _, stype in subtype_items)

        # emit variant struct with inline union
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_tag_t tag;\n")
        if not all_null:
            lines.append("    union {\n")
            for sname, stype in subtype_items:
                is_null = stype.typetype == ZTypeType.NULL
                if not is_null:
                    sub_ctype = _ctype(stype)
                    if sub_ctype and sub_ctype != "void":
                        lines.append(f"        {sub_ctype} {sname};\n")
            lines.append("    } data;\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # emit equality function (if auto-generated)
        eq_method = mono_type.children.get("==")
        if eq_method and eq_method.is_autogen_eq:
            ctype = f"z_{name}_t"
            lines.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{\n")
            if self._use_memcmp_eq(name, eq_method):
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
                            f" return z_{tname}_eq(a.data.{sname}, b.data.{sname});\n"
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
        inner_type = mono_type.generic_args.get("t")
        if not inner_type:
            return
        inner_ctype = _ctype(inner_type)
        ptr_ctype = f"{inner_ctype}*"
        lines: List[str] = []
        lines.append(f"static void z_{name}_destroy({ptr_ctype} v) {{\n")
        lines.append("    if (!v) return;\n")
        # chain inner destructor for types that own heap resources
        if inner_type.needs_destructor and inner_type.destructor_name:
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
        ctype = f"z_{name}_t"
        lines: List[str] = []
        lines.append("typedef struct {\n")
        field_items: list = []
        for fname, ftype in mono_type.children.items():
            if ftype.typetype == ZTypeType.FUNCTION:
                continue
            ct = _ctype(ftype)
            lines.append(f"    {ct} {fname};\n")
            field_items.append((fname, ct))
        lines.append(f"}} {ctype};\n\n")

        # emit meta.create and create functions
        params = [f"{ct} {fn}" for fn, ct in field_items]
        field_names = [fn for fn, _ in field_items]
        self._emit_create_functions(
            name,
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
        elem_type = _array_element_type(mono_type)
        arr_len = _array_length(mono_type)
        if not elem_type or arr_len is None:
            return
        elem_ctype = _ctype(elem_type)

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
        eq_method = mono_type.children.get("==")
        if eq_method and eq_method.is_autogen_eq:
            eq_body_parts.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{")
            if self._use_memcmp_eq(name, eq_method):
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
        cap = _str_capacity(mono_type)
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
        eq_method = mono_type.children.get("==")
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
        ctype = f"z_{name}_t"
        elem_type = _list_element_type(mono_type)
        if elem_type is None:
            return
        elem_ctype = _ctype(elem_type)

        # destroy — per-element cleanup loop only when the element type
        # actually needs it.
        if elem_type.needs_destructor and elem_type.destructor_name:
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
        listview_child = mono_type.children.get("listview")
        listview_methods = ""
        if listview_child and listview_child.return_type:
            lv_name = listview_child.return_type.name
            lv_ctype = f"z_{lv_name}_t"
            listview_methods = (
                f"static {lv_ctype} z_{name}_listview({ctype}* _this);\n"
                f"static {lv_ctype} z_{name}_listview({ctype}* _this) {{\n"
                f"    return *({lv_ctype}*)_this;\n"
                f"}}\n"
                f"\n"
                f"static void z_{name}_extendView({ctype}* _this, {lv_ctype} _from);\n"
                f"static void z_{name}_extendView({ctype}* _this, {lv_ctype} _from) {{\n"
                f"    z_{name}_grow(_this, _this->length + _from.length);\n"
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

        # listiter companion: iterator + .iterate factory. Emitted only
        # when the list mono carries an `.iterate` child returning a
        # listiter mono (set up in the type checker for any list with
        # `.iterate` synthesised — currently every concrete list mono).
        iterate_child = mono_type.children.get("iterate")
        if iterate_child and iterate_child.return_type:
            self._emit_listiter_runtime(
                ctype, name, elem_ctype, iterate_child.return_type
            )

    def _emit_listiter_runtime(
        self,
        list_ctype: str,
        list_name: str,
        elem_ctype: str,
        listiter_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a listiter monomorphization.

        Layout: { source list pointer, current index }. Each .call peeks
        at list->data[idx], wraps the address in optionview.some, and
        increments idx; returns optionview.none when idx >= length.
        """
        li_name = listiter_mono.name
        li_ctype = f"z_{li_name}_t"
        call_method = listiter_mono.children.get("call")
        if not call_method or not call_method.return_type:
            return
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = f"z_{ov_name}_t"
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
        lines.append(f"static {ov_ctype} z_{li_name}_call({li_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} z_{li_name}_call({li_ctype}* _it) {{\n")
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
        lines.append(f"static {li_ctype} z_{list_name}_iterate({list_ctype}* _this);\n")
        lines.append(
            f"static {li_ctype} z_{list_name}_iterate({list_ctype}* _this) {{\n"
        )
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
        elem_type = _listview_element_type(mono_type)
        if elem_type is None:
            return
        self.struct_defs.append(
            ztmpl.apply(
                "z_ListView",
                {"NAME": mono_type.name, "ELEM_T": _ctype(elem_type)},
            )
        )

    def _emit_mono_map(self, mono_type: ZType) -> None:
        """Emit a monomorphized map type (bucket struct, hash, create, destroy, methods)."""
        self.needs_stdint = True
        self.needs_stdlib = True
        self.needs_stdio = True
        self.needs_string = True
        name = mono_type.name
        ctype = f"z_{name}_t"
        key_type = _map_key_type(mono_type)
        value_type = _map_value_type(mono_type)
        if key_type is None or value_type is None:
            return
        key_ctype = _ctype(key_type)
        val_ctype = _ctype(value_type)
        key_is_string = key_ctype == "z_String_t"
        val_is_string = val_ctype == "z_String_t"
        val_is_reftype = val_ctype.endswith("*")
        bucket_type = f"z_{name}_bucket_t"
        lines: List[str] = []

        # bucket state enum
        lines.append(f"#define Z_{name.upper()}_EMPTY 0\n")
        lines.append(f"#define Z_{name.upper()}_DELETED 1\n")
        lines.append(f"#define Z_{name.upper()}_USED 2\n\n")

        # bucket struct
        lines.append("typedef struct {\n")
        lines.append("    uint8_t state;\n")
        lines.append("    uint64_t hash;\n")
        lines.append(f"    {key_ctype} key;\n")
        lines.append(f"    {val_ctype} value;\n")
        lines.append(f"}} {bucket_type};\n\n")

        # map struct
        lines.append("typedef struct {\n")
        lines.append("    uint64_t capacity;\n")
        lines.append("    uint64_t length;\n")
        lines.append(f"    {bucket_type}* buckets;\n")
        lines.append(f"}} {ctype};\n\n")

        # hash function
        hash_fn = f"z_{name}_hash_key"
        lines.append(f"static uint64_t {hash_fn}({key_ctype} _key);\n")
        lines.append(f"static uint64_t {hash_fn}({key_ctype} _key) {{\n")
        if key_is_string:
            # FNV-1a for strings
            lines.append("    uint64_t h = 14695981039346656037ULL;\n")
            lines.append("    uint64_t len = _key.size;\n")
            lines.append("    for (uint64_t i = 0; i < len; i++) {\n")
            lines.append("        h ^= (uint8_t)_key.data[i];\n")
            lines.append("        h *= 1099511628211ULL;\n")
            lines.append("    }\n")
            lines.append("    return h;\n")
        elif _is_str_type(key_type):
            # FNV-1a for str valtypes
            lines.append("    uint64_t h = 14695981039346656037ULL;\n")
            lines.append("    for (uint64_t i = 0; i < _key.len; i++) {\n")
            lines.append("        h ^= (uint8_t)_key.data[i];\n")
            lines.append("        h *= 1099511628211ULL;\n")
            lines.append("    }\n")
            lines.append("    return h;\n")
        else:
            # splitmix64 finalizer for integer keys
            lines.append("    uint64_t h = (uint64_t)_key;\n")
            lines.append("    h ^= h >> 30;\n")
            lines.append("    h *= 0xbf58476d1ce4e5b9ULL;\n")
            lines.append("    h ^= h >> 27;\n")
            lines.append("    h *= 0x94d049bb133111ebULL;\n")
            lines.append("    h ^= h >> 31;\n")
            lines.append("    return h;\n")
        lines.append("}\n\n")

        # key equality function
        eq_fn = f"z_{name}_keys_equal"
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
            if key_type and key_type.needs_destructor and key_type.destructor_name:
                if not key_type.is_heap_allocated:
                    return f"{indent}{key_type.destructor_name}(&{var});\n"
                return f"{indent}{key_type.destructor_name}({var});\n"
            return ""

        # helper: free a value if it carries a destructor
        def emit_free_val(var: str, indent: str = "    ") -> str:
            if (
                value_type
                and value_type.needs_destructor
                and value_type.destructor_name
            ):
                if not value_type.is_heap_allocated:
                    return f"{indent}{value_type.destructor_name}(&{var});\n"
                return f"{indent}{value_type.destructor_name}({var});\n"
            return ""

        # destroy — iterate buckets when the key or value carries a
        # destructor. needs_destructor is the complete driver: the type
        # system only sets it True when there's actual cleanup work
        # (either heap-internal or the outer heap allocation itself),
        # and clears it for self-contained valtypes. No extra pointer-
        # suffix fallback is needed.
        lines.append(f"static void z_{name}_destroy({ctype}* p);\n")
        lines.append(f"static void z_{name}_destroy({ctype}* p) {{\n")
        lines.append("    if (!p) return;\n")
        key_needs_free = bool(key_type and key_type.needs_destructor)
        val_needs_free = bool(value_type and value_type.needs_destructor)
        if key_needs_free or val_needs_free:
            lines.append("    for (uint64_t i = 0; i < p->capacity; i++) {\n")
            lines.append(
                f"        if (p->buckets[i].state == Z_{name.upper()}_USED) {{\n"
            )
            lines.append(emit_free_key("p->buckets[i].key", "            "))
            lines.append(emit_free_val("p->buckets[i].value", "            "))
            lines.append("        }\n")
            lines.append("    }\n")
        lines.append("    free(p->buckets);\n")
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # create
        lines.append(f"static {ctype}* z_{name}_create(uint64_t _capacity);\n")
        lines.append(f"static {ctype}* z_{name}_create(uint64_t _capacity) {{\n")
        lines.append(f"    {ctype}* _this = ({ctype}*)z_xmalloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        lines.append("    if (_capacity < 8) _capacity = 0;\n")
        lines.append("    _this->capacity = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            f"        _this->buckets = ({bucket_type}*)z_xcalloc(_capacity, sizeof({bucket_type}));\n"
        )
        lines.append("    }\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

        # grow/resize
        grow_fn = f"z_{name}_grow"
        lines.append(f"static void {grow_fn}({ctype}* _this);\n")
        lines.append(f"static void {grow_fn}({ctype}* _this) {{\n")
        lines.append("    uint64_t old_cap = _this->capacity;\n")
        lines.append("    uint64_t new_cap = old_cap == 0 ? 8 : old_cap * 2;\n")
        lines.append(f"    {bucket_type}* old_buckets = _this->buckets;\n")
        lines.append(
            f"    {bucket_type}* new_buckets = ({bucket_type}*)z_xcalloc(new_cap, sizeof({bucket_type}));\n"
        )
        lines.append("    for (uint64_t i = 0; i < old_cap; i++) {\n")
        lines.append(f"        if (old_buckets[i].state == Z_{name.upper()}_USED) {{\n")
        lines.append(
            "            uint64_t idx = old_buckets[i].hash & (new_cap - 1);\n"
        )
        lines.append(
            f"            while (new_buckets[idx].state == Z_{name.upper()}_USED) {{\n"
        )
        lines.append("                idx = (idx + 1) & (new_cap - 1);\n")
        lines.append("            }\n")
        lines.append("            new_buckets[idx] = old_buckets[i];\n")
        lines.append("        }\n")
        lines.append("    }\n")
        lines.append("    free(old_buckets);\n")
        lines.append("    _this->buckets = new_buckets;\n")
        lines.append("    _this->capacity = new_cap;\n")
        lines.append("}\n\n")

        # find_bucket helper (internal)
        find_fn = f"z_{name}_find"
        lines.append(
            f"static int64_t {find_fn}({ctype}* _this, {key_ctype} _key, uint64_t _hash);\n"
        )
        lines.append(
            f"static int64_t {find_fn}({ctype}* _this, {key_ctype} _key, uint64_t _hash) {{\n"
        )
        lines.append("    if (_this->capacity == 0) return -1;\n")
        lines.append("    uint64_t idx = _hash & (_this->capacity - 1);\n")
        lines.append("    for (uint64_t i = 0; i < _this->capacity; i++) {\n")
        lines.append(
            f"        if (_this->buckets[idx].state == Z_{name.upper()}_EMPTY) return -1;\n"
        )
        lines.append(
            f"        if (_this->buckets[idx].state == Z_{name.upper()}_USED "
            f"&& _this->buckets[idx].hash == _hash "
            f"&& {eq_fn}(_this->buckets[idx].key, _key)) return (int64_t)idx;\n"
        )
        lines.append("        idx = (idx + 1) & (_this->capacity - 1);\n")
        lines.append("    }\n")
        lines.append("    return -1;\n")
        lines.append("}\n\n")

        # set method
        lines.append(
            f"static void z_{name}_set({ctype}* _this, {key_ctype} _key, {val_ctype} _val);\n"
        )
        lines.append(
            f"static void z_{name}_set({ctype}* _this, {key_ctype} _key, {val_ctype} _val) {{\n"
        )
        lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
        # check for existing key
        lines.append(f"    int64_t existing = {find_fn}(_this, _key, h);\n")
        lines.append("    if (existing >= 0) {\n")
        # replace: destroy old value, update, free old key if reftype
        lines.append(emit_free_val("_this->buckets[existing].value", "        "))
        lines.append("        _this->buckets[existing].value = _val;\n")
        lines.append(emit_free_key("_key", "        "))
        lines.append("        return;\n")
        lines.append("    }\n")
        # check load factor — grow if length * 3 >= capacity * 2
        lines.append(
            "    if (_this->capacity == 0 || (_this->length + 1) * 3 >= _this->capacity * 2) {\n"
        )
        lines.append(f"        {grow_fn}(_this);\n")
        lines.append("    }\n")
        # insert into new slot
        lines.append("    uint64_t idx = h & (_this->capacity - 1);\n")
        lines.append("    int64_t first_deleted = -1;\n")
        lines.append("    for (uint64_t i = 0; i < _this->capacity; i++) {\n")
        lines.append(
            f"        if (_this->buckets[idx].state == Z_{name.upper()}_EMPTY) {{\n"
        )
        lines.append(
            "            if (first_deleted >= 0) idx = (uint64_t)first_deleted;\n"
        )
        lines.append("            break;\n")
        lines.append("        }\n")
        lines.append(
            f"        if (_this->buckets[idx].state == Z_{name.upper()}_DELETED "
            "&& first_deleted < 0) {\n"
        )
        lines.append("            first_deleted = (int64_t)idx;\n")
        lines.append("        }\n")
        lines.append("        idx = (idx + 1) & (_this->capacity - 1);\n")
        lines.append("    }\n")
        lines.append(
            "    if (first_deleted >= 0 "
            f"&& _this->buckets[idx].state != Z_{name.upper()}_EMPTY) "
            "idx = (uint64_t)first_deleted;\n"
        )
        lines.append(f"    _this->buckets[idx].state = Z_{name.upper()}_USED;\n")
        lines.append("    _this->buckets[idx].hash = h;\n")
        lines.append("    _this->buckets[idx].key = _key;\n")
        lines.append("    _this->buckets[idx].value = _val;\n")
        lines.append("    _this->length++;\n")
        lines.append("}\n\n")

        # get method — returns option (nullable ptr for reftype values) or optionval (variant for valtype values)
        get_type = mono_type.children.get("get")
        ret_type = get_type.return_type if get_type else None
        if ret_type:
            self.needs_stdlib = True
            ret_ctype = _ctype(ret_type)
            opt_name = ret_type.name
            get_fn = f"z_{name}_get"

            if ret_type.is_nullable_ptr:
                # nullable-ptr option: return pointer or NULL
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key);\n"
                )
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key) {{\n"
                )
                lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
                lines.append(f"    int64_t idx = {find_fn}(_this, _key, h);\n")
                lines.append("    if (idx >= 0) {\n")
                if val_is_string:
                    lines.append("        z_String_t _copy = {0};\n")
                    lines.append(
                        "        _copy.size = _this->buckets[idx].value.size;\n"
                    )
                    lines.append("        _copy.capacity = _copy.size + 1;\n")
                    lines.append(
                        "        _copy.data = (char*)z_xmalloc(_copy.capacity);\n"
                    )
                    lines.append(
                        "        memcpy(_copy.data, _this->buckets[idx].value.data, _copy.size);\n"
                    )
                    lines.append("        _copy.data[_copy.size] = '\\0';\n")
                    lines.append("        return _copy;\n")
                else:
                    lines.append("        return _this->buckets[idx].value;\n")
                lines.append("    }\n")
                lines.append("    return NULL;\n")
                lines.append("}\n\n")
            elif ret_type.typetype == ZTypeType.VARIANT:
                # optionval variant: return struct by value
                opt_struct = f"z_{opt_name}_t"
                some_tag = f"Z_{opt_name.upper()}_TAG_SOME"
                none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key);\n"
                )
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key) {{\n"
                )
                lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
                lines.append(f"    int64_t idx = {find_fn}(_this, _key, h);\n")
                lines.append(f"    {opt_struct} _r;\n")
                lines.append("    if (idx >= 0) {\n")
                lines.append(f"        _r.tag = {some_tag};\n")
                lines.append("        _r.data.some = _this->buckets[idx].value;\n")
                lines.append("    } else {\n")
                lines.append(f"        _r.tag = {none_tag};\n")
                lines.append("    }\n")
                lines.append("    return _r;\n")
                lines.append("}\n\n")
            else:
                # regular tagged union (legacy path)
                opt_struct = f"z_{opt_name}_t"
                some_tag = f"Z_{opt_name.upper()}_TAG_SOME"
                none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key);\n"
                )
                lines.append(
                    f"static {ret_ctype} {get_fn}({ctype}* _this, {key_ctype} _key) {{\n"
                )
                lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
                lines.append(f"    int64_t idx = {find_fn}(_this, _key, h);\n")
                lines.append(f"    {opt_struct} _r = {{0}};\n")
                lines.append("    if (idx >= 0) {\n")
                lines.append(f"        _r.tag = {some_tag};\n")
                if val_is_reftype:
                    if val_is_string:
                        lines.append(
                            "        z_String_t* _copy = (z_String_t*)z_xmalloc(sizeof(z_String_t));\n"
                        )
                        lines.append(
                            "        _copy->size = _this->buckets[idx].value.size;\n"
                        )
                        lines.append("        _copy->capacity = _copy->size + 1;\n")
                        lines.append(
                            "        _copy->data = (char*)z_xmalloc(_copy->capacity);\n"
                        )
                        lines.append(
                            "        memcpy(_copy->data, _this->buckets[idx].value.data, _copy->size);\n"
                        )
                        lines.append("        _copy->data[_copy->size] = '\\0';\n")
                        lines.append("        _r.data = _copy;\n")
                    else:
                        lines.append("        _r.data = _this->buckets[idx].value;\n")
                else:
                    lines.append(
                        f"        {val_ctype}* _d = ({val_ctype}*)z_xmalloc(sizeof({val_ctype}));\n"
                    )
                    lines.append("        *_d = _this->buckets[idx].value;\n")
                    lines.append("        _r.data = _d;\n")
                lines.append("    } else {\n")
                lines.append(f"        _r.tag = {none_tag};\n")
                lines.append("        _r.data = NULL;\n")
                lines.append("    }\n")
                lines.append("    return _r;\n")
                lines.append("}\n\n")

        # delete method — returns bool
        lines.append(f"static int z_{name}_delete({ctype}* _this, {key_ctype} _key);\n")
        lines.append(
            f"static int z_{name}_delete({ctype}* _this, {key_ctype} _key) {{\n"
        )
        lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
        lines.append(f"    int64_t idx = {find_fn}(_this, _key, h);\n")
        lines.append("    if (idx < 0) return 0;\n")
        lines.append(emit_free_key("_this->buckets[idx].key", "    "))
        lines.append(emit_free_val("_this->buckets[idx].value", "    "))
        lines.append(f"    _this->buckets[idx].state = Z_{name.upper()}_DELETED;\n")
        lines.append("    _this->length--;\n")
        lines.append("    return 1;\n")
        lines.append("}\n\n")

        # has method — returns bool
        lines.append(f"static int z_{name}_has({ctype}* _this, {key_ctype} _key);\n")
        lines.append(f"static int z_{name}_has({ctype}* _this, {key_ctype} _key) {{\n")
        lines.append(f"    uint64_t h = {hash_fn}(_key);\n")
        lines.append(f"    return {find_fn}(_this, _key, h) >= 0;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

        # mapkeyiter companion: borrowed-key iterator + .iterate factory.
        # Emitted only when the map mono carries an `.iterate` child.
        iterate_child = mono_type.children.get("iterate")
        if iterate_child and iterate_child.return_type:
            self._emit_mapkeyiter_runtime(
                ctype, name, key_ctype, iterate_child.return_type
            )

        # mapitemiter + mapentry companion: borrowed-entry iterator +
        # .iterate_items factory. Emitted only when the map mono carries
        # an `.iterate_items` child. mapentry's C representation is a
        # typedef alias for the bucket type; .key / .value emit through
        # the bucket pointer.
        iterate_items_child = mono_type.children.get("iterateItems")
        if iterate_items_child and iterate_items_child.return_type:
            self._emit_mapitemiter_runtime(
                ctype, name, bucket_type, iterate_items_child.return_type
            )

    def _emit_mapkeyiter_runtime(
        self,
        map_ctype: str,
        map_name: str,
        key_ctype: str,
        mki_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a mapkeyiter monomorphization.

        Layout: { source map pointer, current bucket index }. Each .call
        scans forward through buckets, returning the next USED bucket's
        key wrapped in optionview.some, or optionview.none when no more
        USED buckets remain.
        """
        mki_name = mki_mono.name
        mki_ctype = f"z_{mki_name}_t"
        call_method = mki_mono.children.get("call")
        if not call_method or not call_method.return_type:
            return
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = f"z_{ov_name}_t"
        ov_some_tag = f"Z_{ov_name.upper()}_TAG_SOME"
        ov_none_tag = f"Z_{ov_name.upper()}_TAG_NONE"
        used_macro = f"Z_{map_name.upper()}_USED"

        lines: List[str] = []
        lines.append(f"/* mapkeyiter<{key_ctype}> runtime layout */\n")
        lines.append("typedef struct {\n")
        lines.append(f"    {map_ctype}* m;\n")
        lines.append("    uint64_t idx;\n")
        lines.append(f"}} {mki_ctype};\n\n")
        lines.append(f"static {ov_ctype} z_{mki_name}_call({mki_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} z_{mki_name}_call({mki_ctype}* _it) {{\n")
        lines.append(f"    {ov_ctype} _out = {{0}};\n")
        lines.append("    while (_it->idx < _it->m->capacity) {\n")
        lines.append(
            f"        if (_it->m->buckets[_it->idx].state == {used_macro}) {{\n"
        )
        lines.append(f"            _out.tag = {ov_some_tag};\n")
        lines.append("            _out.data = &_it->m->buckets[_it->idx].key;\n")
        lines.append("            _it->idx++;\n")
        lines.append("            return _out;\n")
        lines.append("        }\n")
        lines.append("        _it->idx++;\n")
        lines.append("    }\n")
        lines.append(f"    _out.tag = {ov_none_tag};\n")
        lines.append("    return _out;\n")
        lines.append("}\n\n")
        lines.append(f"static {mki_ctype} z_{map_name}_iterate({map_ctype}* _this);\n")
        lines.append(
            f"static {mki_ctype} z_{map_name}_iterate({map_ctype}* _this) {{\n"
        )
        lines.append(f"    {mki_ctype} _it = {{0}};\n")
        lines.append("    _it.m = _this;\n")
        lines.append("    _it.idx = 0;\n")
        lines.append("    return _it;\n")
        lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_mapitemiter_runtime(
        self,
        map_ctype: str,
        map_name: str,
        bucket_type: str,
        mii_mono: ZType,
    ) -> None:
        """Emit the runtime implementation of a mapitemiter
        monomorphization plus the mapentry typedef.

        mapitemiter layout: { source map pointer, current bucket index }.
        Each .call scans forward through buckets, returning the next
        USED bucket address wrapped in optionview.some, or
        optionview.none when no more USED buckets remain.

        mapentry is a borrow-only view: at the C level it is a typedef
        alias for the bucket struct. .key / .value access compile to
        field projections through the bucket pointer.
        """
        mii_name = mii_mono.name
        mii_ctype = f"z_{mii_name}_t"
        call_method = mii_mono.children.get("call")
        if not call_method or not call_method.return_type:
            return
        ov_mono = call_method.return_type
        ov_name = ov_mono.name
        ov_ctype = f"z_{ov_name}_t"
        ov_some_tag = f"Z_{ov_name.upper()}_TAG_SOME"
        ov_none_tag = f"Z_{ov_name.upper()}_TAG_NONE"
        used_macro = f"Z_{map_name.upper()}_USED"

        # mapentry mono: pulled from the optionview's some payload type
        me_mono = ov_mono.children.get("some")
        if me_mono is None:
            return
        me_name = me_mono.name
        me_ctype = f"z_{me_name}_t"

        lines: List[str] = []
        lines.append(f"/* mapentry<{me_name}> = view of {bucket_type} */\n")
        lines.append(f"typedef {bucket_type} {me_ctype};\n\n")
        lines.append(f"/* mapitemiter<{me_name}> runtime layout */\n")
        lines.append("typedef struct {\n")
        lines.append(f"    {map_ctype}* m;\n")
        lines.append("    uint64_t idx;\n")
        lines.append(f"}} {mii_ctype};\n\n")
        lines.append(f"static {ov_ctype} z_{mii_name}_call({mii_ctype}* _it);\n")
        lines.append(f"static {ov_ctype} z_{mii_name}_call({mii_ctype}* _it) {{\n")
        lines.append(f"    {ov_ctype} _out = {{0}};\n")
        lines.append("    while (_it->idx < _it->m->capacity) {\n")
        lines.append(
            f"        if (_it->m->buckets[_it->idx].state == {used_macro}) {{\n"
        )
        lines.append(f"            _out.tag = {ov_some_tag};\n")
        lines.append("            _out.data = &_it->m->buckets[_it->idx];\n")
        lines.append("            _it->idx++;\n")
        lines.append("            return _out;\n")
        lines.append("        }\n")
        lines.append("        _it->idx++;\n")
        lines.append("    }\n")
        lines.append(f"    _out.tag = {ov_none_tag};\n")
        lines.append("    return _out;\n")
        lines.append("}\n\n")
        lines.append(
            f"static {mii_ctype} z_{map_name}_iterateItems({map_ctype}* _this);\n"
        )
        lines.append(
            f"static {mii_ctype} z_{map_name}_iterateItems({map_ctype}* _this) {{\n"
        )
        lines.append(f"    {mii_ctype} _it = {{0}};\n")
        lines.append("    _it.m = _this;\n")
        lines.append("    _it.idx = 0;\n")
        lines.append("    return _it;\n")
        lines.append("}\n\n")
        # mapentry .key and .value — field projections through the
        # bucket pointer. The C functions take the bucket pointer
        # (mapentry is a typedef of the bucket type) and return a
        # by-value copy of the field. For reftype field types, the
        # copy aliases the source's heap data — same caveat as
        # optionview iteration today (see Phase 1b deferral).
        key_method = me_mono.children.get("key")
        value_method = me_mono.children.get("value")
        if key_method is not None and key_method.return_type is not None:
            key_ctype = _ctype(key_method.return_type)
            lines.append(f"static {key_ctype} z_{me_name}_key({me_ctype}* _e);\n")
            lines.append(f"static {key_ctype} z_{me_name}_key({me_ctype}* _e) {{\n")
            lines.append("    return _e->key;\n")
            lines.append("}\n\n")
        if value_method is not None and value_method.return_type is not None:
            val_ctype = _ctype(value_method.return_type)
            lines.append(f"static {val_ctype} z_{me_name}_value({me_ctype}* _e);\n")
            lines.append(f"static {val_ctype} z_{me_name}_value({me_ctype}* _e) {{\n")
            lines.append("    return _e->value;\n")
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
            for fn, ft in mono_type.children.items()
            if ft.typetype != ZTypeType.FUNCTION
        ]

        # struct typedef
        lock_fields = mono_type.lock_field_names
        lines.append("typedef struct {\n")
        for fname, ftype in field_items:
            ct = _ctype(ftype)
            # .lock fields of stack-allocated class type: store as pointer
            if (
                fname in lock_fields
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
                if fname in lock_fields:
                    continue
                lines.append(self._emit_field_cleanup(f"p->{fname}", ftype))
            lines.append("}\n\n")

        # meta.create constructor
        self._emit_mono_create(name, mono_type, field_items, lines)

        self.struct_defs.append("".join(lines))

        # emit methods from cloned or template defn with mangled names
        cloned_methods = self.program.cloned_methods.get(name)
        func_aliases = self.program.func_aliases
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
        ctype = f"z_{name}_t"
        params: List[str] = []
        field_names: List[str] = []
        field_ctypes_list: List[str] = []
        for fname, ftype in field_items:
            ct = _ctype(ftype)
            params.append(f"{ct} {fname}")
            field_names.append(fname)
            field_ctypes_list.append(ct)
        self._type_field_ctypes[name] = field_ctypes_list
        self._type_field_names[name] = field_names
        if name not in self._type_field_defaults:
            self._type_field_defaults[name] = {}
        self._emit_create_functions(
            name,
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
        lines: List[str] = []

        # collect spec functions from mono_type.children
        specs = [
            (sn, st)
            for sn, st in mono_type.children.items()
            if st.typetype == ZTypeType.FUNCTION
        ]

        # vtable struct
        lines.append("typedef struct {\n")
        for sname, stype in specs:
            ret_type = stype.return_type
            ret_ctype = _ctype(ret_type) if ret_type else "void"
            params = ["void*"]
            for pname, ptype in stype.children.items():
                if pname == "this":
                    continue
                params.append(_proto_param_ctype(ptype))
            lines.append(f"    {ret_ctype} (*{sname})({', '.join(params)});\n")
        lines.append(f"}} z_{name}_vtable_t;\n\n")

        # instance struct
        lines.append("typedef struct {\n")
        lines.append("    void* data;\n")
        lines.append(f"    z_{name}_vtable_t* vtable;\n")
        lines.append("    void (*destroy)(void*);\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destroy function
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto) {{\n")
        lines.append("    if (!proto) return;\n")
        lines.append("    if (proto->destroy) proto->destroy(proto->data);\n")
        lines.append("    proto->destroy = NULL;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_variant(self, name: str, variant_defn: zast.ObjectDef) -> None:
        self.needs_stdint = True
        lines: List[str] = []

        # resolve custom tag values from as_items
        custom_tag_values = self._resolve_tag_values(variant_defn)

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname in variant_defn.is_paths().keys():
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            if custom_tag_values and sname in custom_tag_values:
                lines.append(f"    {tag} = {custom_tag_values[sname]},\n")
            else:
                lines.append(f"    {tag},\n")
        lines.append(f"}} z_{name}_tag_t;\n\n")

        # check if all subtypes are null (enum pattern)
        all_null = all(
            spath.nodetype == NodeType.ATOMID
            and cast(zast.AtomId, spath).name == "null"
            for spath in variant_defn.is_paths().values()
        )

        # emit variant struct with inline union
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_tag_t tag;\n")
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
        lines.append(f"}} z_{name}_t;\n\n")

        # emit equality function (if auto-generated)
        vtype = self._resolved_type(name)
        eq_method = vtype.children.get("==") if vtype else None
        if eq_method and eq_method.is_autogen_eq:
            ctype = f"z_{name}_t"
            lines.append(f"static bool z_{name}_eq({ctype} a, {ctype} b) {{\n")
            if self._use_memcmp_eq(name, eq_method):
                self.needs_string = True
                lines.append(f"    return memcmp(&a, &b, sizeof({ctype})) == 0;\n")
            elif all_null:
                lines.append("    return a.tag == b.tag;\n")
            else:
                lines.append("    if (a.tag != b.tag) return false;\n")
                lines.append("    switch (a.tag) {\n")
                for sname, spath in variant_defn.is_paths().items():
                    tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
                    is_null = (
                        spath.nodetype == NodeType.ATOMID
                        and cast(zast.AtomId, spath).name == "null"
                    )
                    lines.append(f"        case {tag}:")
                    if is_null:
                        lines.append(" return true;\n")
                    else:
                        sub_ctype = self._get_subtype_ctype(spath)
                        sub_type = spath.type
                        if sub_type and self._needs_eq_call(sub_type):
                            tname = sub_type.name.replace(".", "_")
                            lines.append(
                                f" return z_{tname}_eq(a.data.{sname}, b.data.{sname});\n"
                            )
                        elif (
                            sub_ctype
                            and sub_ctype.startswith("z_")
                            and sub_ctype.endswith("_t")
                        ):
                            sub_name = sub_ctype[2:-2]  # z_foo_t -> foo
                            lines.append(
                                f" return z_{sub_name}_eq(a.data.{sname}, b.data.{sname});\n"
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
        for item in data.data:
            op = item.valtype
            if op.nodetype == NodeType.ATOMID and _is_numeric_id(
                cast(zast.AtomId, op).name
            ):
                _, val, err = parse_number(cast(zast.AtomId, op).name)
                if not err:
                    if type(val) is float:
                        values.append(str(val))
                    else:
                        values.append(str(int(val)))
        cname = _mangle_func(name)
        if values:
            self.data_defs.append(
                f"static const int64_t {cname}[] = {{{', '.join(values)}}};\n"
                f"static const int64_t {cname}_len = {len(values)};\n\n"
            )

    def _return_ctype(self, func: zast.Function) -> str:
        if not func.returntype:
            return "void"
        ct = _ctype(func.returntype.type)
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
        record_type = self._resolved_type(record_name) if record_name else None
        is_class_method = bool(record_type and record_type.typetype == ZTypeType.CLASS)
        # Ownership annotations live on the resolved ZType (which carries
        # both syntactic suffixes and the inferred BORROW-default for
        # stack-reftype params); read them from there.
        ftype = self._resolved_type(name)
        param_own = ftype.param_ownership if ftype else {}
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(ppath.type)
            # this-receiver: pass by pointer
            if (
                is_class_method
                and ppath.type is record_type
                and not ptype_str.endswith("*")
            ):
                ptype_str = f"{ptype_str}*"
            # stack-allocated class borrow/lock params
            elif (
                ppath.type
                and ppath.type.typetype == ZTypeType.CLASS
                and not ppath.type.is_heap_allocated
                and not ptype_str.endswith("*")
                and param_own.get(pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ptype_str = f"{ptype_str}*"
            params.append(f"{ptype_str} {_mangle_var(pname)}")
        param_str = ", ".join(params) if params else "void"
        self.forward_decls.append(f"{ret_ctype} {cname}({param_str});\n")

    def _emit_function(
        self, name: str, func: zast.Function, record_name: str = ""
    ) -> None:
        self.needs_stdint = True
        cname = _mangle_func(name)

        ret_ctype = self._return_ctype(func)

        # Class methods pass the `this` receiver by pointer so mutations via
        # `it.field = ...` persist across calls.
        record_type = self._resolved_type(record_name) if record_name else None
        is_class_method = bool(record_type and record_type.typetype == ZTypeType.CLASS)

        # Ownership annotations live on the resolved ZType (carries
        # both syntactic suffixes and inferred BORROW-default).
        ftype = self._resolved_type(name)
        param_own = ftype.param_ownership if ftype else {}

        params: List[str] = []
        pointer_params: List[str] = []
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(ppath.type)
            # class this-receiver: pass by pointer
            if (
                is_class_method
                and ppath.type is record_type
                and not ptype_str.endswith("*")
            ):
                ptype_str = f"{ptype_str}*"
                pointer_params.append(_mangle_var(pname))
            # stack-allocated class borrow/lock params: pass by pointer
            elif (
                ppath.type
                and ppath.type.typetype == ZTypeType.CLASS
                and not ppath.type.is_heap_allocated
                and not ptype_str.endswith("*")
                and param_own.get(pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ptype_str = f"{ptype_str}*"
                pointer_params.append(_mangle_var(pname))
            params.append(f"{ptype_str} {_mangle_var(pname)}")

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
        if record_name:
            self._current_enclosing_type_name = record_name
        # track all pointer parameters for -> field access dispatch
        for pp in pointer_params:
            self._scope.class_params.add(pp)
        # also track other parameters that are already pointer types (unions, etc.)
        for pname, ppath in func.parameters.items():
            ptype_str = _ctype(ppath.type)
            if ptype_str.endswith("*") and ptype_str.startswith("z_"):
                self._scope.class_params.add(_mangle_var(pname))

        # register .take params with a destructor for scope-exit cleanup.
        # Ownership was transferred in from the caller, so the callee owns the
        # heap data and must free it at function exit (or early return).
        for pname, ppath in func.parameters.items():
            if (
                ppath.type
                and ppath.type.needs_destructor
                and ppath.type.destructor_name
                and param_own.get(pname) == ZParamOwnership.TAKE
            ):
                self._scope.cleanup_vars.append((_mangle_var(pname), ppath.type))

        lines: List[str] = []
        lines.append(f"{ret_ctype} {cname}({param_str}) {{\n")
        self.indent_level = 1
        if func.body:
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
        self._alias_map = prev_alias_map

    def _is_implicit_return(self, func: zast.Function) -> bool:
        """Check if the function's last statement is an implicit return candidate."""
        if not func.returntype or not func.body or not func.body.statements:
            return False
        last = func.body.statements[-1].statementline
        if last.nodetype != zast.NodeType.EXPRESSION:
            return False
        last_expr = cast(zast.Expression, last)
        # check call_kind on Expression wrapper for control flow
        if last_expr.call_kind in (
            zast.CallKind.RETURN,
            zast.CallKind.BREAK,
            zast.CallKind.CONTINUE,
            zast.CallKind.ERROR,
            zast.CallKind.PANIC,
        ):
            return False
        # never type means all paths already return explicitly
        if last_expr.type and last_expr.type.typetype == ZTypeType.NEVER:
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
                proto_name = self._temp.proto_set[t]
                result += f"{indent}z_{proto_name}_destroy(&{t});\n"
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
                proto_name = self._temp.proto_set[t]
                result += f"{indent}z_{proto_name}_destroy(&{t});\n"
            elif t in self._temp.class_set:
                tname = self._temp.class_set[t]
                result += f"{indent}{self._emit_class_free(t, tname)}\n"
            else:
                result += f"{indent}free({t});\n"

        self._temp_stack.pop()
        return result

    def _emit_assignment(self, assign: zast.Assignment) -> str:
        indent = self._indent()
        # Phase B alias optimization: inline `x: y.take` or `x: y.borrow`
        # (or similar on a valtype dotted path) becomes a C-level alias —
        # no local declaration, no destructor, substitute at reference
        # sites. The alias lives until the enclosing function ends.
        if assign.alias_of is not None:
            # If the source expression rooted at a narrowed AtomId
            # (e.g., `stolen: s.take` where s is narrowed), the alias
            # must embed the payload-unwrap — plain name substitution
            # would reference the outer union's C storage and emit
            # invalid field access later. Fall through to the
            # AST-aware path value when the root is stamped; otherwise
            # keep the lightweight string-based alias.
            alias_expr = self._narrowed_alias_expr(assign.value)
            if alias_expr is None:
                alias_expr = self._alias_c_expr(assign.alias_of)
            cname = _mangle_var(assign.name)
            self._alias_map[assign.name] = alias_expr
            return f"{indent}/* alias: {cname} => {alias_expr} */\n"
        ctype = "int64_t"
        if assign.type:
            # typedef method calls: type is FUNCTION but variable holds the
            # return value, not a function pointer
            _is_typedef_call = False
            if (
                assign.type.typetype == ZTypeType.FUNCTION
                and assign.type.return_type
                and assign.value.expression.nodetype == NodeType.DOTTEDPATH
            ):
                _pt = cast(zast.DottedPath, assign.value.expression).parent.type
                _is_typedef_call = _pt is not None and _pt.typedef_base is not None
            if _is_typedef_call:
                ctype = _ctype(assign.type.return_type)
            else:
                ctype = _ctype(assign.type)
        cname = _mangle_var(assign.name)
        self._in_named_assignment = True
        val = self._emit_expression_value(assign.value)
        self._in_named_assignment = False
        if assign.type and assign.type.needs_destructor:
            if ctype == "z_String_t":
                self.needs_string = True
            self.needs_stdlib = True
            # the variable now owns the value — remove from temp frees
            if val in self._temp.frees:
                self._temp.frees.remove(val)
            # If the RHS is a call to a function declared `out T.borrow`, the
            # caller does NOT own the return value. Skip cleanup registration
            # so scope exit doesn't double-free the borrowed heap buffer.
            if self._is_borrow_return_call(assign.value):
                self._scope.borrowed_vars.add(cname)
            else:
                self._scope.cleanup_vars.append((cname, assign.type))
        # check if value is a bare record name (zero-initialization)
        inner = assign.value.expression
        inner_resolved = (
            self._resolved_type(cast(zast.AtomId, inner).name)
            if inner.nodetype == NodeType.ATOMID
            else None
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
            result += self._emit_take_invalidation(take_var, assign.value.type, indent)
        return result

    def _emit_reassignment(self, reassign: zast.Reassignment) -> str:
        indent = self._indent()
        lhs = self._emit_path_value(reassign.topath)
        rhs = self._emit_expression_value(reassign.value)
        result = ""
        # check if this is a reftype reassignment — free old value first
        lhs_type = reassign.topath.type
        if lhs_type and lhs_type.needs_destructor:
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
        if lhs_type and lhs_type.needs_destructor:
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
        if swap.lhs.type:
            ctype = _ctype(swap.lhs.type)
        return (
            f"{indent}{{\n"
            f"{indent}    {ctype} _tmp = {lhs};\n"
            f"{indent}    {lhs} = {rhs};\n"
            f"{indent}    {rhs} = _tmp;\n"
            f"{indent}}}\n"
        )

    def _emit_expression_stmt(self, expr: zast.Expression) -> str:
        indent = self._indent()
        inner = expr.expression
        # handle break/continue as standalone statements
        if expr.call_kind == zast.CallKind.BREAK:
            return f"{indent}break;\n"
        if expr.call_kind == zast.CallKind.CONTINUE:
            return f"{indent}continue;\n"
        # panic(msg): route through the shared z_panic helper in the runtime
        # preamble. msg is declared as `string`, but after type coercion
        # we may have a z_String_t or a z_StringView_t; both expose `.data`
        # as a `const char*`-compatible pointer. Materialising msg may pull
        # in the string runtime, so set the corresponding needs_* flags.
        if expr.call_kind == zast.CallKind.PANIC:
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
            dp_parent_type = dp.parent.type
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
            if dp.parent.nodetype == zast.NodeType.ATOMID and _is_definition_name(
                cast(zast.AtomId, dp.parent).name, self
            ):
                return ""
            var = self._emit_path_value(dp.parent)
            var_type = dp.type
            result = ""
            if var_type and var_type.needs_destructor and var_type.destructor_name:
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
            var_type = dp.type
            result = ""
            # Borrowed locals (assigned from an `out T.borrow` call) hold
            # a borrow into someone else's heap data; freeing or zeroing
            # here would corrupt the source. `.release` for a borrow is a
            # type-checker-side lock release with no C-level effect.
            is_borrowed = var in self._scope.borrowed_vars
            # owned reftypes: call destructor
            if (
                var_type
                and var_type.needs_destructor
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
            if cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
                pname = cast(
                    zast.AtomId, cast(zast.DottedPath, call.callable).parent
                ).name
                child = cast(zast.DottedPath, call.callable).child.name
                if self._typetype_of(pname) == ZTypeType.DATA and child == "index":
                    return True
        return False

    def _is_protocol_create(self, call: zast.Call) -> bool:
        """Check if call is protocol.create/take from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name not in ("create", "take"):
            return False
        parent_type = dp.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _is_protocol_borrow(self, call: zast.Call) -> bool:
        """Check if call is protocol.borrow from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name != "borrow":
            return False
        parent_type = dp.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _emit_protocol_create_call(self, call: zast.Call) -> str:
        """Emit owned protocol create: protocol.create from: expr."""
        assert call.callable.nodetype == NodeType.DOTTEDPATH
        proto_type = cast(zast.DottedPath, call.callable).parent.type
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
        arg_type = from_arg.valtype.type
        if not arg_type:
            # try parent for dotted paths like f.take
            if from_arg.valtype.nodetype == NodeType.DOTTEDPATH:
                arg_type = cast(zast.DottedPath, from_arg.valtype).parent.type
        impl_name = arg_type.name if arg_type else ""

        # look up label
        label = self._proto_conformance.get((impl_name, proto_name), "")
        owned_create = f"z_{impl_name}_{label}_create_owned"

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
            f"{indent}z_{proto_name}_t {tmp} = {owned_create}({arg_val});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.proto_set[tmp] = proto_name

        # handle .take invalidation for class (reftype) arguments
        if arg_type and arg_type.typetype == ZTypeType.CLASS:
            take_var = self._get_take_var(from_arg.valtype)
            if take_var:
                ct = _ctype(arg_type)
                self._temp.decls.append(f"{indent}{take_var} = ({ct}){{0}};\n")

        return tmp

    def _emit_protocol_borrow_call(self, call: zast.Call) -> str:
        """Emit borrowed protocol create: protocol.borrow from: expr."""
        assert call.callable.nodetype == NodeType.DOTTEDPATH
        proto_type = cast(zast.DottedPath, call.callable).parent.type
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
        arg_type = from_arg.valtype.type
        if not arg_type:
            if from_arg.valtype.nodetype == NodeType.DOTTEDPATH:
                arg_type = cast(zast.DottedPath, from_arg.valtype).parent.type
        impl_name = arg_type.name if arg_type else ""

        # look up label
        label = self._proto_conformance.get((impl_name, proto_name), "")
        create_name = f"z_{impl_name}_{label}_create"

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
        proto_ctype = f"z_{proto_name}_t"
        self._temp.decls.append(
            f"{indent}{proto_ctype} {tmp} = {create_name}({arg_expr});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.proto_set[tmp] = proto_name

        return tmp

    def _is_facet_create(self, call: zast.Call) -> bool:
        """Check if call is facet.create/take from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name not in ("create", "take"):
            return False
        parent_type = dp.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.FACET

    def _is_facet_borrow(self, call: zast.Call) -> bool:
        """Check if call is facet.borrow from: expr."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return False
        dp = cast(zast.DottedPath, call.callable)
        if dp.child.name != "borrow":
            return False
        parent_type = dp.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.FACET

    def _emit_facet_create_call(self, call: zast.Call) -> str:
        """Emit facet.create/take from: expr — returns a value (not pointer)."""
        assert call.callable.nodetype == NodeType.DOTTEDPATH
        facet_type = cast(zast.DottedPath, call.callable).parent.type
        assert facet_type is not None
        facet_name = facet_type.name

        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        assert from_arg is not None

        arg_val = self._emit_operation_value(from_arg.valtype)
        arg_type = from_arg.valtype.type
        if not arg_type:
            if from_arg.valtype.nodetype == NodeType.DOTTEDPATH:
                arg_type = cast(zast.DottedPath, from_arg.valtype).parent.type
        impl_name = arg_type.name if arg_type else ""

        label = self._proto_conformance.get((impl_name, facet_name), "")
        owned_create = f"z_{impl_name}_{label}_create_owned"
        return f"{owned_create}({arg_val})"

    def _emit_facet_borrow_call(self, call: zast.Call) -> str:
        """Emit facet.borrow from: expr — same as create (copies value)."""
        return self._emit_facet_create_call(call)

    def _emit_facet_dispatch(self, call: zast.Call) -> Optional[str]:
        """If call is a facet method dispatch, return the C expression. Otherwise None."""
        if call.callable.nodetype != NodeType.DOTTEDPATH:
            return None
        dp = cast(zast.DottedPath, call.callable)
        parent_type = dp.parent.type
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
        parent_type = dp.parent.type
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
        spec = parent_type.resolve_child_by_id(dp.child_id)
        spec_params = (
            [(n, t) for n, t in spec.children.items() if n != "this"]
            if spec is not None
            else []
        )
        args = [f"{parent_val}{acc}data"]
        for i, arg in enumerate(call.arguments):
            val = self._emit_operation_value(arg.valtype)
            if i < len(spec_params):
                _, spec_ptype = spec_params[i]
                if _is_collection_param_type(spec_ptype) and not val.startswith("&"):
                    val = f"&{val}"
            args.append(val)
        return f"{parent_val}{acc}vtable->{method}({', '.join(args)})"

    def _emit_callable_dispatch(self, call: zast.Call) -> str:
        """Emit a callable object dispatch: obj(args) -> z_type_call(obj, args)."""
        type_name = call.callable_type_name
        cname = _mangle_func(f"{type_name}.call")
        receiver = self._emit_path_value(call.callable)
        # Class methods expect a pointer receiver. Wrap the variable with &
        # when the receiver is a plain atom (not already a pointer).
        rec_t = self._resolved_type(type_name) if type_name is not None else None
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
        # callable object dispatch as statement
        if call.call_kind == zast.CallKind.CALLABLE:
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
            return f"{indent}{proto_expr};\n"

        # facet method dispatch
        facet_expr = self._emit_facet_dispatch(call)
        if facet_expr is not None:
            return f"{indent}{facet_expr};\n"

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
        _ck = call.call_kind
        if _ck == zast.CallKind.UNKNOWN and call.callable.type:
            _ctrl = call.callable.type.control_kind
            if _ctrl == ControlKind.RETURN:
                _ck = zast.CallKind.RETURN
            elif _ctrl == ControlKind.BREAK:
                _ck = zast.CallKind.BREAK
            elif _ctrl == ControlKind.CONTINUE:
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
            assert (
                cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
            )
            data_name = cast(
                zast.AtomId, cast(zast.DottedPath, call.callable).parent
            ).name
            idx = (
                self._emit_operation_value(call.arguments[0].valtype)
                if call.arguments
                else "0"
            )
            return f"{indent}{_mangle_func(data_name)}[{idx}];\n"

        # array method calls as statements
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
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

        # string class mutating methods: .append, .reserve, .shrink
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent = cast(zast.DottedPath, call.callable).parent
            dp_parent_type = dp_parent.type
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
        ftype = call.callable.type
        emitted_vals = self._last_emitted_arg_vals
        if ftype and ftype.param_ownership:
            params = list(ftype.children.items())
            # Method calls: `this` is the receiver, prepended at the
            # call site — call.arguments[0] aligns with params[1].
            offset = 1 if params and params[0][0] == "this" else 0
            for i, arg in enumerate(call.arguments):
                pi = i + offset
                if pi < len(params):
                    pname, _ = params[pi]
                    if ftype.param_ownership.get(pname) == ZParamOwnership.TAKE:
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
            # check for inline construction shorthand:
            #   return Type field: val ...
            # for both classes (heap-allocated, returns pointer) and records
            # (stack-allocated, returns by value).
            first_arg = call.arguments[0].valtype
            # `return meta.create field: val ...` — raw allocator of the
            # lexically enclosing type. Emits z_<type>_meta_create(...).
            if (
                first_arg.nodetype == NodeType.DOTTEDPATH
                and cast(zast.DottedPath, first_arg).parent.nodetype == NodeType.ATOMID
                and cast(zast.AtomId, cast(zast.DottedPath, first_arg).parent).name
                == "meta"
                and cast(zast.DottedPath, first_arg).child.name == "create"
                and self._current_enclosing_type_name
            ):
                fa_name = self._current_enclosing_type_name
                args_str, take_vars = self._build_meta_create_args(
                    fa_name, call.arguments, skip_first=1
                )
                enclosing_t = self._resolved_type(fa_name)
                result_expr = f"z_{fa_name}_meta_create({args_str})"
                ctype = f"z_{fa_name}_t"
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result_expr};\n")
                for fname, tv in take_vars.items():
                    if tv:
                        ft = enclosing_t.children.get(fname) if enclosing_t else None
                        self._temp.decls.append(
                            self._emit_take_invalidation(tv, ft, indent)
                        )
                val = tmp
            elif (
                first_arg.nodetype == NodeType.ATOMID
                and first_arg.type
                and first_arg.type.typetype == ZTypeType.CLASS
                and len(call.arguments) > 1
            ):
                # emit as create call
                self.needs_stdlib = True
                fa_name = cast(zast.AtomId, first_arg).name
                # use _build_create_args to respect user-defined create param order
                args_str, take_vars = self._build_create_args(
                    fa_name, first_arg.type, call.arguments, skip_first=1
                )
                result_expr = f"z_{fa_name}_create({args_str})"
                ctype = f"z_{fa_name}_t"
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result_expr};\n")
                for fname, tv in take_vars.items():
                    if tv:
                        cls_t = self._resolved_type(fa_name)
                        ft = cls_t.children.get(fname) if cls_t else None
                        self._temp.decls.append(
                            self._emit_take_invalidation(tv, ft, indent)
                        )
                val = tmp
            elif (
                first_arg.nodetype == NodeType.ATOMID
                and first_arg.type
                and first_arg.type.typetype == ZTypeType.RECORD
                and len(call.arguments) > 1
            ):
                # emit as `z_<type>_create(args)` using the order of the
                # type's children["create"] parameters (which is either
                # user-defined or the default meta-create wrapper).
                fa_name = cast(zast.AtomId, first_arg).name
                rec_t = first_arg.type
                args_str, take_vars = self._build_create_args(
                    fa_name, rec_t, call.arguments, skip_first=1
                )
                result_expr = f"z_{fa_name}_create({args_str})"
                ctype = f"z_{fa_name}_t"
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result_expr};\n")
                for fname, tv in take_vars.items():
                    if tv:
                        self._temp.decls.append(f"{indent}{tv} = NULL;\n")
                val = tmp
            else:
                val = self._emit_operation_value(call.arguments[0].valtype)
            result = ""

            # remove return value from temp frees (caller owns it)
            if val in self._temp.frees:
                self._temp.frees.remove(val)

            # free remaining temps (intermediates) before return
            for t in self._temp.frees:
                if t in self._temp.string_set:
                    result += f"{indent}z_String_free(&{t});\n"
                elif t in self._temp.proto_set:
                    proto_name = self._temp.proto_set[t]
                    result += f"{indent}z_{proto_name}_destroy(&{t});\n"
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
        """If op is a var.take expression, return the mangled variable name."""
        if op.nodetype == NodeType.DOTTEDPATH:
            if (
                cast(zast.DottedPath, op).child.name == "take"
                and cast(zast.DottedPath, op).parent.nodetype == NodeType.ATOMID
            ):
                name = cast(zast.AtomId, cast(zast.DottedPath, op).parent).name
                if not _is_numeric_id(name):
                    # don't nullify function/spec definitions (immutable program text)
                    if _is_definition_name(name, self):
                        return None
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
            if (
                not _is_numeric_id(name)
                and self._typetype_of(name) != ZTypeType.FUNCTION
                and self._typetype_of(name) != ZTypeType.DATA
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

        Typecheck stamped `arg.projected_protocol`, `arg.projected_label`,
        and `arg.projected_kind` when the caller passed a concrete arg
        to a protocol parameter. We synthesise the wrapper here — same
        C pattern as the explicit `proto.borrow` / `proto.create` forms
        emitted by `_emit_protocol_borrow_call` / `_emit_protocol_create_call`.
        """
        assert arg.projected_protocol is not None
        assert arg.projected_label is not None
        proto_type = arg.projected_protocol
        label = arg.projected_label
        arg_val = self._emit_operation_value(arg.valtype)
        arg_type = self._get_operation_type(arg.valtype)
        impl_name = arg_type.name if arg_type else ""
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
        if arg.projected_kind == "take":
            create_name = f"z_{impl_name}_{label}_create_owned"
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(
                f"{indent}z_{proto_type.name}_t {tmp} = {create_name}({arg_expr});\n"
            )
            self._temp.frees.append(tmp)
            self._temp.proto_set[tmp] = proto_type.name
            # invalidate the source (take semantics) when the arg was a
            # named variable — mirror _apply_take_to_arg's C-side zero.
            src = self._get_implicit_take_var(arg.valtype) if arg_type else None
            if src is not None:
                self._temp.decls.append(
                    self._emit_take_invalidation(src, arg_type, indent)
                )
            return tmp
        # borrow (default): stack-allocated protocol handle, no destroy.
        create_name = f"z_{impl_name}_{label}_create"
        tmp = self._temp_name("p")
        indent = self._indent()
        self._temp.decls.append(
            f"{indent}z_{proto_type.name}_t {tmp} = {create_name}({arg_expr});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.proto_set[tmp] = proto_type.name
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
        ftype = cast(zast.DottedPath, call.callable).type
        if not (ftype and ftype.typetype == ZTypeType.FUNCTION):
            return args
        if "this" not in ftype.children:
            return args
        parent_path = cast(zast.DottedPath, call.callable).parent
        receiver = self._emit_path_value(parent_path)
        parent_type = parent_path.type
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
        ftype = call.callable.type
        if (
            ftype is not None
            and ftype.generic_origin is not None
            and ftype.generic_origin.is_ztype
        ):
            gp = cast(ZType, ftype.generic_origin).generic_params
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
            children_keys = list(ftype.children.keys())
            if children_keys and children_keys[0] == "this":
                method_offset = 1
        for i, arg in enumerate(call.arguments):
            # skip generic type args (they are compile-time only)
            if arg.name and arg.name in generic_param_names:
                self._last_emitted_arg_vals.append("")
                continue
            # Implicit protocol projection stamped by typecheck: emit
            # `z_<impl>_<label>_create` over the concrete argument value
            # so the callee sees a protocol handle.
            if arg.projected_protocol is not None and arg.projected_label is not None:
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
                and param_idx < len(list(ftype.children.items()))
            ):
                param_name = list(ftype.children.keys())[param_idx]
                param_type = ftype.children[param_name]
                # 'this' receiver: param type matches enclosing class
                # and the function is a method (dotted name origin)
                is_this_param = param_name == "this" or (
                    param_type is arg_type and ftype.name and "." in ftype.name
                )
                # borrow/lock class params also need &. .take on a
                # string param means the callee owns by value — no
                # `&` should be added (that would pass a pointer to
                # a by-value parameter).
                own = ftype.param_ownership.get(param_name)
                is_borrow_lock = own in (
                    ZParamOwnership.BORROW,
                    ZParamOwnership.LOCK,
                )
                is_take_string = (
                    own == ZParamOwnership.TAKE and arg_type.subtype == ZSubType.STRING
                )
                if (is_this_param or is_borrow_lock) and not is_take_string:
                    val = f"&{val}"
            parts.append(val)
            self._last_emitted_arg_vals.append(val)
            ctype_idx += 1

        # fill defaults for missing trailing params
        ftype = call.callable.type
        if ftype and ftype.param_defaults:
            params = list(ftype.children.items())
            for i in range(len(call.arguments), len(params)):
                pname, _ = params[i]
                if pname in ftype.param_defaults:
                    default = ftype.param_defaults[pname]
                    if self._typetype_of(default) == ZTypeType.FUNCTION:
                        default = _mangle_func(default)
                    parts.append(default)

        return ", ".join(parts)

    def _zero_args_for_ctypes(self, type_name: str) -> str:
        """Build zero-value argument list using stored field C types."""
        field_ctypes = self._type_field_ctypes.get(type_name, [])
        parts: List[str] = []
        for fct in field_ctypes:
            if fct.endswith("*"):
                parts.append("NULL")
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
            create_fn = type_obj.children.get("create")
            meta_fn = type_obj.meta_create
        # Default case: no user override — delegate to the meta-create builder.
        if (
            create_fn is None
            or create_fn.typetype != ZTypeType.FUNCTION
            or create_fn is meta_fn
        ):
            return self._build_meta_create_args(type_name, arguments, skip_first)

        # User-defined create: use its parameter order. Include function-typed
        # params (function-pointer field params) since the user's signature is
        # the authoritative source.
        param_names: List[str] = []
        param_ctypes: Dict[str, str] = {}
        for pname, ptype in create_fn.children.items():
            param_names.append(pname)
            ct = _ctype(ptype)
            # class borrow/lock params are emitted as pointers in C
            if (
                ptype
                and ptype.typetype == ZTypeType.CLASS
                and not ptype.is_heap_allocated
                and not ct.endswith("*")
                and create_fn.param_ownership.get(pname)
                in (ZParamOwnership.BORROW, ZParamOwnership.LOCK)
            ):
                ct = f"{ct}*"
            param_ctypes[pname] = ct

        field_defaults = self._type_field_defaults.get(type_name, {})

        arg_map: Dict[str, str] = {}
        arg_types: Dict[str, Optional[ZType]] = {}
        take_vars: Dict[str, Optional[str]] = {}
        indent = self._indent()
        for arg in arguments[skip_first:]:
            if arg.name:
                val = self._emit_operation_value(arg.valtype)
                arg_map[arg.name] = val
                arg_types[arg.name] = self._get_operation_type(arg.valtype)
                take_vars[arg.name] = self._get_take_var(arg.valtype)
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
                    and val not in self._scope.class_params
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
        self, type_name: str, arguments: list, skip_first: int = 0
    ) -> tuple:
        """Build ordered argument list for meta.create call.

        Maps named call arguments to field declaration order.
        Missing fields get zero values.
        """
        field_names = self._type_field_names.get(type_name, [])
        field_ctypes = self._type_field_ctypes.get(type_name, [])
        field_defaults = self._type_field_defaults.get(type_name, {})

        # build dict from call arguments
        arg_map: Dict[str, str] = {}
        arg_types: Dict[str, Optional[ZType]] = {}
        take_vars: Dict[str, Optional[str]] = {}
        indent = self._indent()
        for arg in arguments[skip_first:]:
            if arg.name:
                val = self._emit_operation_value(arg.valtype)
                arg_map[arg.name] = val
                arg_types[arg.name] = self._get_operation_type(arg.valtype)
                take_vars[arg.name] = self._get_take_var(arg.valtype)
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
                    and val not in self._scope.class_params
                ):
                    val = f"&{val}"
                parts.append(val)
            elif fname in field_defaults:
                parts.append(field_defaults[fname])
            else:
                # zero value based on C type
                fct = field_ctypes[i] if i < len(field_ctypes) else "int64_t"
                if fct.endswith("*"):
                    parts.append("NULL")
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
            # bare function name = call with all-default args
            if (
                inner.nodetype == zast.NodeType.ATOMID
                and self._typetype_of(cast(zast.AtomId, inner).name)
                == ZTypeType.FUNCTION
            ):
                atom = cast(zast.AtomId, inner)
                ftype = atom.type
                if ftype and ftype.param_defaults:
                    # only emit bare call when ALL params have defaults
                    real_params = list(ftype.children.items())
                    all_defaulted = all(
                        p in ftype.param_defaults for p, _ in real_params
                    )
                    if all_defaulted:
                        cname = _mangle_func(atom.name)
                        defaults: List[str] = []
                        for pname, _ in real_params:
                            d = ftype.param_defaults[pname]
                            if self._typetype_of(d) == ZTypeType.FUNCTION:
                                d = _mangle_func(d)
                            defaults.append(d)
                        return f"{cname}({', '.join(defaults)})"
            return self._emit_operation_value(cast(zast.Operation, inner))
        if inner.nodetype == zast.NodeType.WITH:
            return self._emit_expression_value(cast(zast.With, inner).doexpr)
        if inner.nodetype == zast.NodeType.DO and inner.type:
            return self._emit_do_expression_value(cast(zast.Do, inner))
        if inner.nodetype == zast.NodeType.IF:
            return self._emit_if_expression_value(cast(zast.If, inner))
        if inner.nodetype == zast.NodeType.FOR and inner.type:
            return self._emit_for_expression_value(cast(zast.For, inner))
        if inner.nodetype == zast.NodeType.CASE and inner.type:
            return self._emit_case_expression_value(cast(zast.Case, inner))
        return "0"

    def _emit_call_value(self, call: zast.Call) -> str:
        # callable object dispatch: obj(args) -> z_type_call(obj, args)
        if call.call_kind == zast.CallKind.CALLABLE:
            result = self._emit_callable_dispatch(call)
            if call.type:
                if call.type.subtype == ZSubType.STRING:
                    return self._alloc_temp(result)
                if call.type.typetype == ZTypeType.CLASS:
                    ctype = f"z_{call.type.name}_t"
                    tmp = self._temp_name("c")
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                    if call.type.needs_destructor:
                        self._temp.frees.append(tmp)
                        self._temp.class_set[tmp] = call.type.name
                    return tmp
                if call.type.typetype == ZTypeType.UNION:
                    ctype = f"z_{call.type.name}_t"
                    tmp = self._temp_name("c")
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                    if call.type.needs_destructor:
                        self._temp.frees.append(tmp)
                        self._temp.class_set[tmp] = call.type.name
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
            return proto_expr

        # facet method dispatch
        facet_expr = self._emit_facet_dispatch(call)
        if facet_expr is not None:
            return facet_expr

        # data.index call -> array access
        if (
            self._is_data_index_call(call)
            and call.callable.nodetype == NodeType.DOTTEDPATH
        ):
            assert (
                cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
            )
            data_name = cast(
                zast.AtomId, cast(zast.DottedPath, call.callable).parent
            ).name
            idx = (
                self._emit_operation_value(call.arguments[0].valtype)
                if call.arguments
                else "0"
            )
            return f"{_mangle_func(data_name)}[{idx}]"

        # typedef create/take/borrow: identity — just emit the from: argument
        if call.type and call.type.typedef_base is not None:
            if call.arguments:
                return self._emit_operation_value(call.arguments[0].valtype)
            return "0"

        # array construction: zero-initialized, no field args
        if call.callable.type and _is_array_type(call.callable.type):
            return f"z_{call.callable.type.name}_create()"

        # stringview method calls: .string, .length
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
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

        # string and str method calls: .stringview (both), .length / .capacity (string)
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp = cast(zast.DottedPath, call.callable)
            dp_parent_type = dp.parent.type
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

        # string construction: string or string capacity: N
        if call.callable.type and call.callable.type.subtype == ZSubType.STRING:
            self.needs_string = True
            self.needs_stdlib = True
            cap = "0"
            for arg in call.arguments:
                if arg.name == "capacity":
                    cap = self._emit_operation_value(arg.valtype)
                    break
            return self._alloc_temp(f"z_String_create((uint64_t){cap})")

        # str construction: (str to: N) — always empty
        if call.callable.type and _is_str_type(call.callable.type):
            str_name = _mono_name(call.callable.type)
            return f"z_{str_name}_create()"

        # list construction: (list of: T) or (list of: T) capacity: N
        if call.callable.type and _is_list_type(call.callable.type):
            list_name = _mono_name(call.callable.type)
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
        if call.callable.type and _is_map_type(call.callable.type):
            map_name = _mono_name(call.callable.type)
            cap_arg = None
            for arg in call.arguments:
                if arg.name == "capacity":
                    cap_arg = arg
                    break
            if cap_arg is not None:
                cap_val = self._emit_operation_value(cap_arg.valtype)
                return f"z_{map_name}_create({cap_val})"
            return f"z_{map_name}_create(0)"

        # array method calls: .get and .set
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
            if dp_parent_type and _is_array_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                arr_type_name = _mono_name(dp_parent_type)
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{arr_type_name}_get({parent_val}, {idx_val})"
                if method_name == "set" and len(call.arguments) >= 2:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    val_val = self._emit_operation_value(call.arguments[1].valtype)
                    return f"z_{arr_type_name}_set(&{parent_val}, {idx_val}, {val_val})"

        # str method calls: .string
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
            if dp_parent_type and _is_str_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                str_type_name = _mono_name(dp_parent_type)
                if method_name == "string":
                    result = f"z_{str_type_name}_string({parent_val})"
                    return self._alloc_temp(result)

        # stringview method calls: .string
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
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
            dp_parent_type = dp.parent.type
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
                target_type = call.type
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
                        cap = _str_capacity(target_type)
                        literal = self._collect_string_literal(
                            cast(zast.AtomString, inner).stringparts
                        )
                        lit_len = len(
                            literal.encode("utf-8")
                            .decode("unicode_escape")
                            .encode("utf-8")
                        )
                        if cap is not None and lit_len <= cap:
                            ctype = f"z_{target_name}_t"
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
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
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
                    return f"z_{list_type_name}_append({parent_val}, {val})"
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
                    return (
                        f"z_{list_type_name}_insert({parent_val}, {from_val}, {at_val})"
                    )
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
                    return f"z_{list_type_name}_extend({parent_val}, &{from_tmp})"
                if method_name == "extendView" and call.arguments:
                    # extendView takes a listview by value (copies, does
                    # not consume). The argument is typed as a listview of
                    # the list's element; the mono emitter generates a
                    # z_{listname}_extendView(z_{listname}_t*, z_ListView_T_t).
                    from_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{list_type_name}_extendView({parent_val}, {from_val})"
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{list_type_name}_get({parent_val}, {idx_val})"
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
                    return f"z_{list_type_name}_set({parent_val}, {idx_val}, {val_val})"
                if method_name == "pop":
                    return f"z_{list_type_name}_pop({parent_val})"
                if method_name == "listview":
                    return f"z_{list_type_name}_listview({parent_val})"

        # listview method calls: .get
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
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
                lv_type_name = _mono_name(dp_parent_type)
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{lv_type_name}_get({parent_val}, {idx_val})"

        # map method calls: .set, .get, .delete, .has
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            dp_parent_type = cast(zast.DottedPath, call.callable).parent.type
            if dp_parent_type and _is_map_type(dp_parent_type):
                method_name = cast(zast.DottedPath, call.callable).child.name
                parent_val = self._emit_path_value(
                    cast(zast.DottedPath, call.callable).parent
                )
                map_type_name = _mono_name(dp_parent_type)
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
                    return f"z_{map_type_name}_set({parent_val}, {key_val}, {val_val})"
                if method_name == "get" and call.arguments:
                    key_val = self._emit_operation_value(call.arguments[0].valtype)
                    result = f"z_{map_type_name}_get({parent_val}, {key_val})"
                    ret_type = call.type
                    if ret_type and ret_type.is_nullable_ptr:
                        # nullable-ptr option: track as temp for destroy
                        tmp = self._temp_name("c")
                        indent = self._indent()
                        inner_ctype = _ctype(ret_type)
                        self._temp.decls.append(
                            f"{indent}{inner_ctype} {tmp} = {result};\n"
                        )
                        self._temp.frees.append(tmp)
                        return tmp
                    if ret_type and ret_type.typetype == ZTypeType.VARIANT:
                        # optionval variant: no temp tracking needed (value type)
                        return result
                    if ret_type and ret_type.typetype == ZTypeType.UNION:
                        # regular union pointer: track as temp
                        tmp = self._temp_name("c")
                        indent = self._indent()
                        self._temp.decls.append(
                            f"{indent}z_{ret_type.name}_t* {tmp} = {result};\n"
                        )
                        self._temp.frees.append(tmp)
                        return tmp
                    return result
                if method_name == "delete" and call.arguments:
                    key_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{map_type_name}_delete({parent_val}, {key_val})"
                if method_name == "has" and call.arguments:
                    key_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{map_type_name}_has({parent_val}, {key_val})"

        if call.callable.type and call.callable.type.typetype == ZTypeType.RECORD:
            rec_type = call.callable.type
            args_str, take_vars = self._build_create_args(
                rec_type.name, rec_type, call.arguments
            )
            result = f"z_{rec_type.name}_create({args_str})"
            # handle .take nullification
            for fname, tv in take_vars.items():
                if tv:
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{tv} = NULL;\n")
            return result

        # box construction: box from: val
        if call.call_kind == zast.CallKind.BOX_CREATE:
            return self._emit_box_create(call)
        if call.call_kind == zast.CallKind.BOX_PASSTHROUGH:
            return self._emit_box_passthrough(call)

        # union construction: union.subtype expr
        if self._is_union_construction(call):
            return self._emit_union_construction(call)

        # variant construction: variant.subtype expr
        if self._is_variant_construction(call):
            return self._emit_variant_construction(call)

        if call.callable.type and call.callable.type.typetype == ZTypeType.CLASS:
            cls_type = call.callable.type
            ctype = f"z_{cls_type.name}_t"
            self.needs_stdlib = True
            args_str, take_vars = self._build_create_args(
                cls_type.name, cls_type, call.arguments
            )
            result = f"z_{cls_type.name}_create({args_str})"
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
            # handle .take invalidation
            for fname, tv in take_vars.items():
                if tv:
                    # get field type for proper invalidation
                    ft = cls_type.children.get(fname)
                    self._temp.decls.append(
                        self._emit_take_invalidation(tv, ft, indent)
                    )
            if cls_type.needs_destructor:
                self._temp.frees.append(tmp)
                self._temp.class_set[tmp] = cls_type.name
            return tmp

        args = self._emit_call_args(call)
        args = self._prepend_method_receiver(call, args)

        cname = self._emit_callable_expr(call)
        result = f"{cname}({args})"
        indent = self._indent()

        # if call returns a reftype, wrap in temp for cleanup
        if call.type:
            if call.type.subtype == ZSubType.STRING:
                # A callee declared `out string.borrow` returns a borrowed
                # view — caller does NOT own it and must not free it.
                ftype = call.callable.type
                if ftype and ftype.return_ownership == ZParamOwnership.BORROW:
                    tmp = self._temp_name("t")
                    self._temp.decls.append(f"{indent}z_String_t {tmp} = {result};\n")
                else:
                    tmp = self._alloc_temp(result)
                self._apply_call_implicit_takes(call, indent)
                return tmp
            if call.type.typetype == ZTypeType.CLASS:
                ctype = f"z_{call.type.name}_t"
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                if call.type.needs_destructor:
                    self._temp.frees.append(tmp)
                    self._temp.class_set[tmp] = call.type.name
                self._apply_call_implicit_takes(call, indent)
                return tmp
            if call.type.typetype == ZTypeType.UNION:
                # The callee returns a union by value; wrap in a local
                # so the subsequent assignment/destroy can take its
                # address. Cleanup routes through class_set so scope
                # exit emits `z_<T>_destroy(&tmp)` (freeing the inner
                # payload without trying to free the stack slot).
                ctype = f"z_{call.type.name}_t"
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype} {tmp} = {result};\n")
                if call.type.needs_destructor:
                    self._temp.frees.append(tmp)
                    self._temp.class_set[tmp] = call.type.name
                self._apply_call_implicit_takes(call, indent)
                return tmp

        # non-reftype return (int/float/void): wrap in a stmt-expr temp so that
        # implicit-take invalidations for string args are ordered AFTER the
        # call. Only do this when a string arg is present, to avoid disturbing
        # the cleanup of other heap-backed stack-struct args (protocols, etc.).
        if self._call_has_string_arg(call):
            ret_type = call.type
            if ret_type is None or _ctype(ret_type) == "void":
                self._temp.decls.append(f"{indent}{result};\n")
                self._apply_call_implicit_takes(call, indent)
                return "0"
            tmp = self._temp_name("r")
            ct = _ctype(ret_type)
            self._temp.decls.append(f"{indent}{ct} {tmp} = {result};\n")
            self._apply_call_implicit_takes(call, indent)
            return tmp

        return result

    def _emit_const_value(self, node: zast.Node) -> str:
        """Emit a compile-time constant value as a C literal."""
        v = node.const_value
        assert v is not None
        self.needs_stdint = True
        if type(v) is bool:
            return "1" if v else "0"
        if type(v) is float:
            # emit with full precision for f64
            return repr(v)
        raw = str(int(v))
        if node.type and node.type.name != "i64":
            ctype = TYPEMAP.get(node.type.name, "int64_t")
            if ctype != "int64_t":
                return f"(({ctype}){raw})"
        return raw

    def _emit_operation_value(self, op: zast.Operation) -> str:
        if op.const_value is not None:
            return self._emit_const_value(op)
        if op.nodetype == NodeType.BINOP:
            return self._emit_binop_value(cast(zast.BinOp, op))
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
        if binop.const_value is not None:
            return self._emit_const_value(binop)
        lhs = self._emit_operation_value(binop.lhs)
        rhs = self._emit_path_value(binop.rhs)
        # auto-deref boxed valtypes in binary operations
        if (
            binop.lhs.nodetype == NodeType.ATOMID
            and binop.lhs.type
            and binop.lhs.type.is_box
        ):
            lhs = f"(*{lhs})"
        if (
            binop.rhs.nodetype == NodeType.ATOMID
            and binop.rhs.type
            and binop.rhs.type.is_box
        ):
            rhs = f"(*{rhs})"
        op = binop.operator.name
        # route == and != through z_{name}_eq() for autogen equality types
        if op in ("==", "!=") and binop.lhs.type:
            eq_method = binop.lhs.type.children.get("==")
            if eq_method and eq_method.is_autogen_eq:
                tname = binop.lhs.type.name.replace(".", "_")
                call = f"z_{tname}_eq({lhs}, {rhs})"
                if op == "!=":
                    return f"(!{call})"
                return call
            # string content comparison (native == on string class)
            if binop.lhs.type.subtype == ZSubType.STRING:
                call = f"z_String_eq(&{lhs}, &{rhs})"
                if op == "!=":
                    return f"(!{call})"
                return call
            # stringview content comparison
            if binop.lhs.type.subtype == ZSubType.STRINGVIEW:
                self.needs_stringview = True
                call = f"z_StringView_eq({lhs}, {rhs})"
                if op == "!=":
                    return f"(!{call})"
                return call
        # Ordering comparisons on string / stringview: route through the
        # shared cmp primitive. `z_{type}_cmp` returns -1 / 0 / 1 so the
        # four operators map to plain C comparisons against zero.
        if op in ("<", "<=", ">", ">=") and binop.lhs.type:
            if binop.lhs.type.subtype == ZSubType.STRING:
                self.needs_stdint = True
                cop = C_OPS.get(op, op)
                return f"(z_String_cmp(&{lhs}, &{rhs}) {cop} 0)"
            if binop.lhs.type.subtype == ZSubType.STRINGVIEW:
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
        payload_type = outer.resolve_child_by_id(child_id)
        if payload_type is None or payload_type.typetype == ZTypeType.NULL:
            return None
        if outer.typetype == ZTypeType.UNION:
            payload_ctype = _ctype(payload_type)
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
        if atom.narrowed_subtype and atom.original_ztype is not None:
            unwrap = self._narrow_unwrap_expr(
                atom.original_ztype,
                atom.narrowed_subtype,
                atom.child_id,
                _mangle_var(name),
            )
            if unwrap is not None:
                return unwrap
        # check if this refers to a function, constant, data, or record
        resolved = self._resolved_type(name)
        tt = resolved.typetype if resolved else None
        if tt in (ZTypeType.FUNCTION, ZTypeType.DATA):
            return _mangle_func(name)
        if name in self._const_names:
            return _mangle_func(name)
        # only match user-defined records (not numeric constant aliases like north: 0)
        if tt == ZTypeType.RECORD and resolved is not None and resolved.name == name:
            zero_args = self._zero_args_for_ctypes(name)
            return f"z_{name}_create({zero_args})"
        # string class: bare "String" as value -> empty string constructor
        # only when the name IS "String" (not a variable that has string type)
        if name == "String" and atom.type and atom.type.subtype == ZSubType.STRING:
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
            ctype = _ctype(resolved)
            if (
                base is not None
                and base is not resolved
                and (_is_list_type(base) or _is_map_type(base) or _is_str_type(base))
            ):
                create_args = "0"
            else:
                create_args = self._zero_args_for_ctypes(mangled)
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(
                f"{indent}{ctype} {tmp} = z_{mangled}_create({create_args});\n"
            )
            if resolved.needs_destructor:
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
        self.needs_stdio = True
        self.needs_stdlib = True
        if dst_type in NUMERIC_RANGES:
            lo, hi = NUMERIC_RANGES[dst_type]
            return (
                f"({{ {src_ctype} _v = {val}; "
                f"if (_v < {lo} || _v > {hi})"
                f' z_panic("numeric cast overflow: {src_type} to {dst_type}"); '
                f"({dst_ctype})_v; }})"
            )
        return f"(({dst_ctype}){val})"

    def _extract_unit_path(self, path: zast.Path) -> Optional[str]:
        """If path resolves to an inline unit, return its dotted name. Otherwise None."""
        if path.nodetype == NodeType.ATOMID:
            if self._typetype_of(cast(zast.AtomId, path).name) == ZTypeType.UNIT:
                return cast(zast.AtomId, path).name
            return None
        if path.nodetype == NodeType.DOTTEDPATH:
            parent_path = self._extract_unit_path(cast(zast.DottedPath, path).parent)
            if parent_path is not None:
                qname = f"{parent_path}.{cast(zast.DottedPath, path).child.name}"
                if self._typetype_of(qname) == ZTypeType.UNIT:
                    return qname
        return None

    def _emit_dotted_path_value(self, path: zast.DottedPath) -> str:
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

        if path.parent.nodetype == NodeType.ATOMID:
            pname = cast(zast.AtomId, path.parent).name
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
                and path.type is not None
                and path.type.typetype != ZTypeType.FUNCTION
            ):
                unit_body = self.program.units[pname].body
                child_defn = unit_body.get(child)
                if (
                    child_defn is not None
                    and child_defn.nodetype == NodeType.FUNCTION
                    and cast(zast.Function, child_defn).is_native
                ):
                    fn_type = child_defn.type
                    has_runtime_params = False
                    if fn_type is not None:
                        for p in fn_type.children:
                            if p != "this":
                                has_runtime_params = True
                                break
                    if not has_runtime_params:
                        mangled = _mangle_func(f"{pname}.{child}")
                        self._track_stdlib_unit_native(mangled)
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
            if self._typetype_of(pname) == ZTypeType.UNIT:
                qname = f"{pname}.{child}"
                # check if the child is itself a unit (nested)
                if self._typetype_of(qname) == ZTypeType.UNIT:
                    # will be resolved by further dotted path traversal
                    return _mangle_func(qname)
                return _mangle_func(qname)
            # record_name.method or class_name.method — method call with no
            # extra args. Only fire when `pname` resolves to the type itself
            # (e.g. `myclass.method`), not a variable that happens to have
            # class / record type — variables flow to the per-type dispatch
            # branches below so zero-arg methods like `list.listview` emit
            # as method calls instead of bare field accesses.
            ptt = self._typetype_of(pname)
            resolved_pname = self._resolved_type(pname)
            is_type_name = resolved_pname is not None and resolved_pname.name == pname
            if ptt in (ZTypeType.RECORD, ZTypeType.CLASS) and is_type_name:
                return _mangle_func(f"{pname}.{child}")
            # union_name.subtype — emit null subtype construction
            if ptt == ZTypeType.UNION:
                return self._emit_union_null_construction(pname, child)
            # variant_name.subtype — emit null subtype construction
            if ptt == ZTypeType.VARIANT:
                return self._emit_variant_null_construction(pname, child)
            # data.index call
            if ptt == ZTypeType.DATA and child == "index":
                return _mangle_func(pname)

        # check if parent resolves to a nested inline unit path
        unit_path = self._extract_unit_path(path.parent)
        if unit_path is not None:
            return _mangle_func(f"{unit_path}.{child}")

        # array: numeric index access (a.0 → a.data[0])
        parent_type_dp = path.parent.type
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
        # the latter case `path.parent.type` is the enclosing union,
        # handled below via _effective_class_zero_arg_method.
        cls_name, method_fn = self._effective_class_zero_arg_method(path)
        if cls_name is not None and method_fn is not None:
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
            if child == "parseF64":
                # strtod + errno (ERANGE) — pull in errno.h / stdlib.h.
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
        # data.array: copy data into new array
        if (
            parent_type_dp
            and parent_type_dp.typetype == ZTypeType.DATA
            and child == "array"
        ):
            arr_type = path.type
            if arr_type and _is_array_type(arr_type):
                arr_len = _array_length(arr_type)
                arr_ctype = _ctype(arr_type)
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
        if path.type and path.type.const_value is not None:
            parent_type_dp = path.parent.type
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
        if path.parent.type and path.parent.type.typetype == ZTypeType.PROTOCOL:
            parent_type_p = path.parent.type
            # Id-only child lookup — PROTOCOL parent is always stamped.
            spec = parent_type_p.resolve_child_by_id(path.child_id)
            if spec is not None and spec.typetype == ZTypeType.FUNCTION:
                parent = self._emit_path_value(path.parent)
                acc = "->" if self._is_class_pointer_path(path.parent) else "."
                return f"{parent}{acc}vtable->{child}({parent}{acc}data)"

        # check if the dotted path resolves to a function (method call or
        # field access). Two shapes hit this branch:
        #   - path.type is FUNCTION directly (legacy: only happens now
        #     when we explicitly opted out of auto-call coercion in the
        #     typechecker — i.e. `path.type` was stamped via
        #     `_check_path(..., coerce_method_to_return=False)` from
        #     `_check_call`).
        #   - path.type is the method's return type and the parent's
        #     child by this name is a FUNCTION (the post-`546f7fd`
        #     auto-call coercion in `_check_dotted_path`). For value-
        #     position dotted paths (`p1.distance` inside a string
        #     interpolation, or `v: s.stringview`), the typechecker
        #     coerced path.type to the return type — but the path is
        #     still semantically a no-arg method call and must lower
        #     as one.
        method_type: "ZType | None" = None
        if path.type and path.type.typetype == ZTypeType.FUNCTION:
            method_type = path.type
        elif (
            path.parent.type
            and path.parent.type.typetype
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
            and not path.parent.type.is_native
        ):
            cand = path.parent.type.children.get(child)
            if (
                cand is not None
                and cand.typetype == ZTypeType.FUNCTION
                and cand.return_type is path.type
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
            parent_type_m = path.parent.type
            if (
                parent_type_m
                and not parent_type_m.is_heap_allocated
                and parent_type_m.typetype == ZTypeType.CLASS
                and not parent.startswith("&")
                and not self._is_class_pointer_path(path.parent)
            ):
                parent = f"&{parent}"
            return f"{_mangle_func(func_name)}({parent})"

        # protocol instance creation: obj.label where label maps to a
        # protocol conformance (synthesize a protocol value via
        # z_<type>_<label>_create). Must NOT fire for regular fields
        # whose declared type happens to be a protocol (`source:
        # reader.lock` on a class) — those need a plain struct-field
        # access which falls through to the general path below.
        if path.type and path.type.typetype == ZTypeType.PROTOCOL:
            parent_type = path.parent.type
            if (
                parent_type
                and parent_type.typetype in (ZTypeType.RECORD, ZTypeType.CLASS)
                and self._proto_conformance.get((parent_type.name, path.type.name))
                == child
            ):
                self.needs_stdlib = True
                parent_val = self._emit_path_value(path.parent)
                create_name = f"z_{parent_type.name}_{child}_create"
                # pass address for stack-allocated types
                if parent_type.is_valtype or not parent_type.is_heap_allocated:
                    arg = f"&{parent_val}"
                else:
                    arg = parent_val
                tmp = self._temp_name("p")
                indent = self._indent()
                proto_ctype = f"z_{path.type.name}_t"
                # stack-allocate: protocol struct is now stack-based
                self._temp.decls.append(
                    f"{indent}{proto_ctype} {tmp} = {create_name}({arg});\n"
                )
                self._temp.frees.append(tmp)
                self._temp.proto_set[tmp] = path.type.name
                return tmp

        # runtime numeric cast: x.u32 where x is a numeric variable
        if child in NUMERIC_CAST_TYPES:
            parent_type = path.parent.type
            if parent_type and parent_type.name in TYPEMAP:
                parent_val = self._emit_path_value(path.parent)
                return self._emit_numeric_cast(parent_val, parent_type.name, child)
            # box(numeric): auto-deref then cast
            if parent_type and parent_type.is_box:
                inner_type = parent_type.generic_args.get("t")
                if inner_type and inner_type.name in TYPEMAP:
                    parent_val = self._emit_path_value(path.parent)
                    return self._emit_numeric_cast(parent_val, inner_type.name, child)

        parent = self._emit_path_value(path.parent)
        # use -> for class instances (pointer types)
        if self._is_class_pointer_path(path.parent):
            return f"{parent}->{child}"
        # variant payload access: v.subname → v.data.subname
        parent_type = path.parent.type
        if parent_type and parent_type.typetype == ZTypeType.VARIANT:
            # Id-only lookup — typecheck stamps child_id on every DottedPath
            # with a known parent_type, so we should never see -1 here.
            child_type = parent_type.resolve_child_by_id(path.child_id)
            if child_type and child_type.typetype != ZTypeType.FUNCTION:
                return f"{parent}.data.{child}"
        # union payload access: u.subname → *(T*)u.data (heap-boxed)
        # Non-null subtypes are stored as malloc'd boxes behind a void*
        # data pointer; deref and cast to T. Null subtypes have no
        # payload and should not be accessed this way — the typechecker
        # rejects it.
        if parent_type and parent_type.typetype == ZTypeType.UNION:
            child_type = parent_type.resolve_child_by_id(path.child_id)
            if (
                child_type
                and child_type.typetype != ZTypeType.FUNCTION
                and child_type.typetype != ZTypeType.NULL
            ):
                inner_ctype = _ctype(child_type)
                return f"(*({inner_ctype}*){parent}.data)"
        return f"{parent}.{child}"

    def _effective_file_type(self, path: zast.Path) -> bool:
        """True if `path` resolves (semantically) to an io.file value.

        A file can appear in two AST shapes:
          * Direct path (local of class type) — `path.type` is the
            file class.
          * Union subtype selection — `path.type` is the enclosing
            union (per the typechecker's parent_tagged_type rule); the
            selected child is the real file. E.g. `r.ok` where
            `r: result(file, ioerror)`.
        """
        pt = path.type
        if pt and pt.typetype == ZTypeType.CLASS and pt.name == "File":
            return True
        if (
            pt
            and pt.typetype == ZTypeType.UNION
            and path.nodetype == NodeType.DOTTEDPATH
        ):
            dp = cast(zast.DottedPath, path)
            sub = pt.children.get(dp.child.name)
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
        pt = path.type
        if pt and pt.typetype == ZTypeType.CLASS:
            if class_name is None or pt.name == class_name:
                return pt
        if (
            pt
            and pt.typetype == ZTypeType.UNION
            and path.nodetype == NodeType.DOTTEDPATH
        ):
            dp = cast(zast.DottedPath, path)
            sub = pt.children.get(dp.child.name)
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
        method = cls.children.get(path.child.name)
        if method is None or method.typetype != ZTypeType.FUNCTION:
            return (None, None)
        if method.return_type is None:
            return (None, None)
        recv = method.this_param_name
        for p in method.children:
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
        parent_type = path.type
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
            grandparent_type = dp.parent.type
            if grandparent_type and grandparent_type.lock_field_names:
                if dp.child.name in grandparent_type.lock_field_names:
                    return True
        # local heap-allocated variable tracked for cleanup
        if path.nodetype == NodeType.ATOMID:
            cname = _mangle_var(cast(zast.AtomId, path).name)
            for vname, vtype in self._scope.cleanup_vars:
                if vname == cname and vtype.is_heap_allocated:
                    return True
            # method 'this' parameter (class) is a pointer
            if cname in self._scope.class_params:
                return True
        return False

    def _emit_class_free(self, var: str, type_name: Optional[str]) -> str:
        """Emit the right destroy call for a class variable (stack-allocated)."""
        if type_name:
            return f"z_{type_name}_destroy(&{var});"
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
        if atom.narrowed_subtype is None or atom.original_ztype is None:
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
            ct = _ctype(ztype)
            return f"{indent}{var} = ({ct}){{0}};\n"
        return f"{indent}{var} = NULL;\n"

    def _emit_arm_local_cleanup(self, saved_len: int) -> str:
        """Emit cleanup for variables declared inside an arm, then truncate.

        Variables added to cleanup_vars during arm emission (indices saved_len..)
        are local to the arm's C scope. We emit their destructors inside the arm
        block and remove them from cleanup_vars so they are not double-freed at
        function scope exit.
        """
        indent = self._indent()
        result = ""
        arm_vars = self._scope.cleanup_vars[saved_len:]
        for var_name, var_type in reversed(arm_vars):
            result += self._emit_field_cleanup(var_name, var_type, indent)
        del self._scope.cleanup_vars[saved_len:]
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
            if vtype and vtype.needs_destructor and vtype.destructor_name:
                var = _mangle_var(vname)
                if vtype.is_heap_allocated:
                    parts.append(
                        f"{indent}if ({var}) {{ {vtype.destructor_name}({var}); }}\n"
                    )
                    parts.append(f"{indent}{var} = NULL;\n")
                else:
                    parts.append(f"{indent}{vtype.destructor_name}(&{var});\n")
                    parts.append(f"{indent}{var} = ({_ctype(vtype)}){{0}};\n")
        return "".join(parts)

    def _needs_implicit_take(self, ztype: Optional[ZType]) -> bool:
        """True if ztype is a stack struct that owns heap data (e.g. string).

        Passing such a value by value copies the outer struct but aliases the
        inner heap pointer, so ownership MUST be transferred at the call site
        to avoid double-free / leak / use-after-free.
        """
        if ztype is None:
            return False
        return bool(ztype.needs_destructor) and not ztype.is_heap_allocated

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
        ftype = call.callable.type
        for i, arg in enumerate(call.arguments):
            if i >= len(emitted_vals) or not emitted_vals[i]:
                continue
            arg_type = self._get_operation_type(arg.valtype)
            if arg_type is None:
                continue
            explicit_own = (
                ftype.param_ownership.get(arg.name) if ftype and arg.name else None
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
        ftype = call.callable.type
        return bool(ftype and ftype.return_ownership == ZParamOwnership.BORROW)

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
        # a regular function call that happens to return a union is NOT a
        # union construction; defer to the standard call emission path.
        if call.call_kind == zast.CallKind.REGULAR:
            return False
        # check type annotation for monomorphized union types
        call_type = call.type
        if (
            call_type
            and call_type.typetype == ZTypeType.UNION
            and call_type.generic_origin
        ):
            return True
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            if cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
                if (
                    self._typetype_of(
                        cast(
                            zast.AtomId, cast(zast.DottedPath, call.callable).parent
                        ).name
                    )
                    == ZTypeType.UNION
                ):
                    return True
        if call.callable.nodetype == NodeType.ATOMID:
            if (
                self._typetype_of(cast(zast.AtomId, call.callable).name)
                == ZTypeType.UNION
            ):
                return True
        return False

    def _emit_union_construction(self, call: zast.Call) -> str:
        """Emit C code for union construction."""
        call_type = call.type

        # nullable-ptr option: .some val → val, .none → NULL
        if call_type and call_type.is_nullable_ptr:
            return self._emit_nullable_ptr_construction(call)

        self.needs_stdlib = True
        indent = self._indent()
        tmp = self._temp_name("c")

        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
        ):
            subtype_name = cast(zast.DottedPath, call.callable).child.name
        else:
            # bare union name — shouldn't happen for construction but handle gracefully
            return "NULL"

        # check for monomorphized union type (from type annotation)
        if (
            call_type
            and call_type.typetype == ZTypeType.UNION
            and call_type.generic_origin
        ):
            union_name = call_type.name
        elif cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
            union_name = cast(
                zast.AtomId, cast(zast.DottedPath, call.callable).parent
            ).name
        else:
            return "NULL"

        ctype = f"z_{union_name}_t"
        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp.decls.append(f"{indent}{ctype} {tmp} = {{0}};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")

        # determine subtype info — check monomorphized type first
        is_null = False
        subtype_ctype_resolved = None
        if call_type and call_type.generic_origin:
            # monomorphized: look up subtype from the mono ZType
            sub_ztype = call_type.children.get(subtype_name)
            if sub_ztype:
                is_null = sub_ztype.typetype == ZTypeType.NULL
                if not is_null:
                    subtype_ctype_resolved = _ctype(sub_ztype)
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
        is_locked_arm = bool(call_type and subtype_name in call_type.lock_arm_names)

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
                    if val in self._temp.frees:
                        self._temp.frees.remove(val)
                    self._temp.decls.append(f"{indent}{tmp}.data = {val};\n")
                else:
                    # valtype: box it (malloc + copy)
                    box_ctype = subtype_ctype or "int64_t"
                    box_tmp = self._temp_name("Box")
                    self._temp.decls.append(
                        f"{indent}{box_ctype}* {box_tmp} = ({box_ctype}*)z_xmalloc(sizeof({box_ctype}));\n"
                    )
                    self._temp.decls.append(f"{indent}*{box_tmp} = {val};\n")
                    self._temp.decls.append(f"{indent}{tmp}.data = {box_tmp};\n")
                    # ownership transferred to boxed copy — don't free original
                    if val in self._temp.frees:
                        self._temp.frees.remove(val)

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
        self.needs_stdlib = True
        indent = self._indent()
        call_type = call.type
        if not call_type:
            return "NULL"
        inner_type = call_type.generic_args.get("t")
        if not inner_type:
            return "NULL"
        inner_ctype = _ctype(inner_type)
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
        ct = _ctype(subtype_path.type)
        return ct if ct != "void" else None

    def _emit_union_null_construction(self, union_name: str, subtype_name: str) -> str:
        """Emit construction for a null-subtype union (no data)."""
        self.needs_stdlib = True
        indent = self._indent()
        tmp = self._temp_name("c")
        ctype = f"z_{union_name}_t"
        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"
        self._temp.decls.append(f"{indent}{ctype} {tmp} = {{0}};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")
        self._temp.decls.append(f"{indent}{tmp}.data = NULL;\n")
        self._temp.frees.append(tmp)
        self._temp.class_set[tmp] = union_name
        return tmp

    def _is_variant_construction(self, call: zast.Call) -> bool:
        """Check if a call is a variant construction (variant.subtype expr)."""
        # A regular function call whose return type happens to be a
        # variant is NOT a construction — defer to the standard call
        # emission path (same guard as _is_union_construction).
        if call.call_kind == zast.CallKind.REGULAR:
            return False
        # check type annotation for monomorphized variant types
        call_type = call.type
        if (
            call_type
            and call_type.typetype == ZTypeType.VARIANT
            and call_type.generic_origin
        ):
            return True
        if call.callable.nodetype == NodeType.DOTTEDPATH:
            if cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
                if (
                    self._typetype_of(
                        cast(
                            zast.AtomId, cast(zast.DottedPath, call.callable).parent
                        ).name
                    )
                    == ZTypeType.VARIANT
                ):
                    return True
        if call.callable.nodetype == NodeType.ATOMID:
            if (
                self._typetype_of(cast(zast.AtomId, call.callable).name)
                == ZTypeType.VARIANT
            ):
                return True
        return False

    def _emit_variant_construction(self, call: zast.Call) -> str:
        """Emit C code for variant construction (stack-allocated, no malloc)."""
        indent = self._indent()
        tmp = self._temp_name("c")

        if (
            call.callable.nodetype == NodeType.DOTTEDPATH
            and cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID
        ):
            subtype_name = cast(zast.DottedPath, call.callable).child.name
        else:
            return "(z_unknown_t){0}"

        # check for monomorphized variant type (from type annotation)
        call_type = call.type
        if (
            call_type
            and call_type.typetype == ZTypeType.VARIANT
            and call_type.generic_origin
        ):
            variant_name = call_type.name
        elif cast(zast.DottedPath, call.callable).parent.nodetype == NodeType.ATOMID:
            variant_name = cast(
                zast.AtomId, cast(zast.DottedPath, call.callable).parent
            ).name
        else:
            return "(z_unknown_t){0}"

        ctype = f"z_{variant_name}_t"
        tag = f"Z_{variant_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp.decls.append(f"{indent}{ctype} {tmp};\n")
        self._temp.decls.append(f"{indent}{tmp}.tag = {tag};\n")

        # determine if this is a null subtype — check monomorphized type first
        is_null = False
        if call_type and call_type.generic_origin:
            sub_ztype = call_type.children.get(subtype_name)
            if sub_ztype:
                is_null = sub_ztype.typetype == ZTypeType.NULL
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
        self, variant_name: str, subtype_name: str
    ) -> str:
        """Emit construction for a null-subtype variant (tag only, no data)."""
        indent = self._indent()
        tmp = self._temp_name("c")
        ctype = f"z_{variant_name}_t"
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
        if expr.type:
            return expr.type
        inner = expr.expression
        if inner.type:
            return inner.type
        return None

    def _get_operation_type(self, op: zast.Operation) -> Optional[ZType]:
        if op.type:
            return op.type
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
            if atom.original_ztype is not None:
                return atom.original_ztype
        if op.nodetype == NodeType.EXPRESSION:
            expr = cast(zast.Expression, op)
            inner = expr.expression
            if inner is not None and inner.nodetype == NodeType.ATOMID:
                return self._get_storage_type(cast(zast.Operation, inner))
        return self._get_operation_type(op)

    def _emit_if(self, ifnode: zast.If) -> str:
        indent = self._indent()
        parts: List[str] = []

        # constant folding: check if any clause has all-constant conditions
        emitted_true_branch = False
        non_const_clauses: List[tuple] = []  # (index, clause) for non-constant clauses

        for i, clause in enumerate(ifnode.clauses):
            # check if all conditions in this clause are compile-time constants
            all_const = all(
                cond_op.const_value is not None
                for _, cond_op in clause.conditions.items()
            )
            if all_const and not emitted_true_branch:
                all_true = all(
                    bool(cond_op.const_value)
                    for _, cond_op in clause.conditions.items()
                )
                if all_true:
                    # emit just the branch body in a new scope
                    parts.append(f"{indent}{{\n")
                    self.indent_level += 1
                    saved_len = len(self._scope.cleanup_vars)
                    parts.append(self._emit_statement(clause.statement))
                    parts.append(self._emit_arm_local_cleanup(saved_len))
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
                saved_len = len(self._scope.cleanup_vars)
                parts.append(self._emit_statement(clause.statement))
                parts.append(self._emit_arm_local_cleanup(saved_len))
                self.indent_level -= 1

            if ifnode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                saved_len = len(self._scope.cleanup_vars)
                parts.append(self._emit_statement(ifnode.elseclause))
                parts.append(self._emit_arm_local_cleanup(saved_len))
                self.indent_level -= 1

            parts.append(f"{indent}}}\n")
        elif not emitted_true_branch and ifnode.elseclause:
            # all clauses were constant-false, emit else branch in a scope
            parts.append(f"{indent}{{\n")
            self.indent_level += 1
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(ifnode.elseclause))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        # post-if cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(ifnode.taken_vars, indent))

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
                if last_expr.call_kind in (
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
                if last_expr.call_kind in (
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
        if ifnode.type:
            ctype = _ctype(ifnode.type)

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
                cond_op.const_value is not None
                for _, cond_op in clause.conditions.items()
            )
            if all_const and not emitted_true_branch:
                all_true = all(
                    bool(cond_op.const_value)
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
        if ifnode.type and ifnode.type.needs_destructor:
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
        if not elem_type.needs_destructor:
            return None
        destructor = elem_type.destructor_name
        if destructor:
            return f"{destructor}(&{var_name});"
        return None

    def _emit_for(self, fornode: zast.For) -> str:
        indent = self._indent()
        parts: List[str] = []

        init_vars: List[str] = []
        cond_exprs: List[str] = []
        # iterator bindings: (name, op, opt_ctype, elem_ctype, opt_name, callable_type, opt_type)
        iter_bindings: List[
            Tuple[
                str,
                zast.Operation,
                str,
                str,
                str,
                Optional[str],
                Optional[ZType],
                Optional[ZType],
            ]
        ] = []
        # each bindings: (name, limit_expr, from_expr, elem_ctype) — optimized C for loop
        each_bindings: List[Tuple[str, str, str, str]] = []

        for name, cond_op in fornode.conditions.items():
            if name.startswith(" "):
                cond_exprs.append(self._emit_operation_value(cond_op))
            elif name in fornode.iterator_bindings:
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
                        parent_type = each_path.parent.type
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
                            elem_ctype = _ctype(parent_type)
                            each_bindings.append(
                                (name, limit_val, from_val, elem_ctype)
                            )
                            is_each = True

                if not is_each:
                    t = self._get_operation_type(cond_op)
                    if t:
                        call_method = (
                            t.children.get("call")
                            if t.typetype != ZTypeType.FUNCTION
                            else None
                        )
                        if call_method and call_method.return_type:
                            opt_type = call_method.return_type
                            opt_ctype = _ctype(opt_type)
                            opt_name = opt_type.name
                            some_type = opt_type.children.get("some")
                            elem_ctype = _ctype(some_type) if some_type else "int64_t"
                            iter_bindings.append(
                                (
                                    name,
                                    cond_op,
                                    opt_ctype,
                                    elem_ctype,
                                    opt_name,
                                    t.name,
                                    opt_type,
                                    some_type,
                                )
                            )
                        else:
                            opt_type = t
                            opt_ctype = _ctype(t)
                            opt_name = t.name
                            some_type = t.children.get("some")
                            elem_ctype = _ctype(some_type) if some_type else "int64_t"
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
                    ctype = _ctype(t)
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
                callable_type,
                opt_type,
                elem_type,
            ) in iter_bindings:
                if callable_type:
                    obj_val = self._emit_operation_value(iop)
                    call_fn = _mangle_func(f"{callable_type}.call")
                    # Class iterators take a pointer receiver since 'this' is
                    # always a pointer.
                    rec_t = self._resolved_type(callable_type)
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
                    and "some" in opt_type.lock_arm_names
                ):
                    # optionview: borrowed-view union. data is a pointer to
                    # the source's storage. For valtype payloads (e.g.
                    # i64) bind by value-copy — copies are safe and don't
                    # need aliasing. For reftype payloads (string, classes)
                    # bind by pointer and seed the alias map so the body's
                    # `s.field` / `s.method ...` accesses go through the
                    # source storage, not a stack copy. The Phase 1a
                    # borrow_origin marking on the loop var blocks
                    # reassign / take / move-out; the iterator's lock on
                    # the source list excludes concurrent writers.
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
        """Emit for-loop body, with optional list comprehension append."""
        if not fornode.loop:
            return ""
        state = self._comprehension_state.get(fornode.nodeid)
        if state is None:
            return self._emit_statement(fornode.loop)
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
        return "".join(parts)

    def _emit_for_expression_value(self, fornode: zast.For) -> str:
        """Emit for-as-expression (list comprehension): returns a list."""
        list_type = fornode.type
        assert list_type is not None
        list_ctype = _ctype(list_type)
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
        if donode.has_break:
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
        ctype = _ctype(donode.type)
        tmp = self._temp_name("do")
        indent = self._indent()
        if donode.has_break:
            # optional result: default to none, set to some on normal completion
            opt_type = donode.type
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
            ctype = _ctype(val_type)

        is_string = ctype == "z_String_t"
        is_class = ctype.startswith("z_") and ctype.endswith("_t*")
        is_union = val_type and val_type.typetype == ZTypeType.UNION
        cname = _mangle_var(withnode.name)

        # BORROW bindings do not own the value — no destructor at scope exit
        # and no adoption of reftype temps.
        is_owned = withnode.ownership != ZOwnership.BORROWED

        # Phase B alias optimization: when the RHS is a plain path reference,
        # skip the C local declaration and substitute at reference sites.
        if withnode.alias_of is not None:
            alias_expr = self._alias_c_expr(withnode.alias_of)
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
                        take_var, withnode.value.type, inner_indent
                    )
                )

        # doexpr may reference the with variable, so its temps must be
        # declared inside the block (not prepended to the outer statement)
        self._temp_stack.append(TempState())

        doexpr_code = self._emit_expression_stmt(withnode.doexpr)

        # emit doexpr temps inside the with block
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
        indent = self._indent()
        parts: List[str] = []

        # check if subject is a union or variant type
        subject_type = self._get_operation_type(casenode.subject)
        if subject_type and subject_type.typetype == ZTypeType.UNION:
            return self._emit_union_case(casenode, subject_type)
        if subject_type and subject_type.typetype == ZTypeType.VARIANT:
            return self._emit_variant_case(casenode, subject_type)

        # constant folding: if subject has const_value, emit only matching arm
        subject_cv = casenode.subject.const_value
        if subject_cv is not None:
            matched_clause = None
            for clause in casenode.clauses:
                if (
                    clause.match.const_value is not None
                    and subject_cv == clause.match.const_value
                ):
                    matched_clause = clause
                    break
            if matched_clause is not None:
                parts.append(f"{indent}{{\n")
                self.indent_level += 1
                parts.append(self._emit_statement(matched_clause.statement))
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
                return "".join(parts)
            if all(c.match.const_value is not None for c in casenode.clauses):
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
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1

        if casenode.elseclause:
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1

        parts.append(f"{indent}}}\n")

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(casenode.taken_vars, indent))

        return "".join(parts)

    def _emit_union_case(self, casenode: zast.Case, union_type: ZType) -> str:
        # nullable-ptr option: if (ptr != NULL) / else
        if union_type.is_nullable_ptr:
            return self._emit_nullable_ptr_case(casenode, union_type)

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
                clause.match.child_id,
                subject,
                parts,
                arm_indent,
            )
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            restore()
            parts.append(f"{arm_indent}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")

        # post-match cleanup: destroy subject if taken in any arm but not all
        if casenode.subject_taken:
            parts.append(
                f"{indent}z_{union_name}_destroy(&{subject});\n"
                f"{indent}{subject} = (z_{union_name}_t){{0}};\n"
            )

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(casenode.taken_vars, indent))

        return "".join(parts)

    def _emit_nullable_ptr_case(self, casenode: zast.Case, union_type: ZType) -> str:
        """Emit case matching for nullable-ptr option: if (ptr != NULL) / else."""
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
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(some_clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(none_clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif some_clause:
            parts.append(f"{indent}if ({subject} != NULL) {{\n")
            self.indent_level += 1
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(some_clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                saved_len = len(self._scope.cleanup_vars)
                parts.append(self._emit_statement(casenode.elseclause))
                parts.append(self._emit_arm_local_cleanup(saved_len))
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif none_clause:
            parts.append(f"{indent}if ({subject} == NULL) {{\n")
            self.indent_level += 1
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(none_clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                saved_len = len(self._scope.cleanup_vars)
                parts.append(self._emit_statement(casenode.elseclause))
                parts.append(self._emit_arm_local_cleanup(saved_len))
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        # post-match cleanup for nullable-ptr
        if casenode.subject_taken:
            union_name = union_type.name
            parts.append(
                f"{indent}if ({subject} != NULL) {{\n"
                f"{indent}    z_{union_name}_destroy({subject});\n"
                f"{indent}}}\n"
                f"{indent}{subject} = NULL;\n"
            )

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(casenode.taken_vars, indent))

        return "".join(parts)

    def _emit_variant_case(self, casenode: zast.Case, variant_type: ZType) -> str:
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
                saved_len = len(self._scope.cleanup_vars)
                parts.append(self._emit_statement(clause.statement))
                parts.append(self._emit_arm_local_cleanup(saved_len))
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            if casenode.elseclause:
                parts.append(f"{indent}else {{\n")
                self.indent_level += 1
                saved_len = len(self._scope.cleanup_vars)
                parts.append(self._emit_statement(casenode.elseclause))
                parts.append(self._emit_arm_local_cleanup(saved_len))
                self.indent_level -= 1
                parts.append(f"{indent}}}\n")
            parts.append(self._emit_taken_vars_cleanup(casenode.taken_vars, indent))
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
                clause.match.child_id,
                subject,
                parts,
                arm_indent,
            )
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(clause.statement))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            restore()
            parts.append(f"{arm_indent}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            saved_len = len(self._scope.cleanup_vars)
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(self._emit_arm_local_cleanup(saved_len))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")

        # post-match cleanup: destroy+zero variables taken in some arm
        parts.append(self._emit_taken_vars_cleanup(casenode.taken_vars, indent))

        return "".join(parts)

    def _emit_case_expression_value(self, casenode: zast.Case) -> str:
        """Emit match-as-expression using temp variable pattern."""
        ctype = "int64_t"
        if casenode.type:
            ctype = _ctype(casenode.type)

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
        if casenode.type and casenode.type.needs_destructor:
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
        subject_cv = casenode.subject.const_value
        if subject_cv is not None:
            matched_clause = None
            for clause in casenode.clauses:
                if (
                    clause.match.const_value is not None
                    and subject_cv == clause.match.const_value
                ):
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
            if all(c.match.const_value is not None for c in casenode.clauses):
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
                clause.match.child_id,
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
                clause.match.child_id,
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


def emit(program: zast.Program) -> str:
    emitter = CEmitter(program)
    return emitter.emit()
