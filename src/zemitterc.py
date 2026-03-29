"""
ZeroLang C code emitter

Walks a type-checked AST and emits C source code.
Includes ownership-based memory management for strings (ZStr*).
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

import zast
from ztypes import ZType, ZTypeType, parse_number, ZParamOwnership, NUMERIC_RANGES


@dataclass
class ScopeState:
    """Per-function cleanup state, pushed/popped at function boundaries."""

    # (mangled_var_name, ZType) pairs in insertion order for scope-exit cleanup
    cleanup_vars: list = field(default_factory=list)
    temp_counter: int = 0
    record_name: str = ""
    class_params: set = field(default_factory=set)
    func_nodeid: int = 0  # NodeID of enclosing function (for unique temp names)


@dataclass
class TempState:
    """Per-statement temporary variable state, pushed/popped at statement boundaries."""

    decls: List[str] = field(default_factory=list)
    frees: List[str] = field(default_factory=list)
    string_set: set = field(default_factory=set)
    class_set: Dict[str, str] = field(default_factory=dict)


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
    "bool": "int",
    "never": "void",
}

NUMERIC_CAST_TYPES = set(TYPEMAP.keys()) - {"null", "bool", "never"}

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


def _is_numeric_id(name: str) -> bool:
    c0 = name[0]
    return c0.isdigit() or (c0 in ("+", "-") and len(name) > 1 and name[1].isdigit())


def _is_array_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized array type."""
    if not ztype:
        return False
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "array"
    )


def _array_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of an array type."""
    return ztype.generic_args.get("of")


def _array_length(ztype: ZType) -> Optional[int]:
    """Get the length of an array type."""
    to_arg = ztype.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def _is_str_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized str type."""
    if not ztype:
        return False
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "str"
    )


def _str_capacity(ztype: ZType) -> Optional[int]:
    """Get the capacity of a str type."""
    to_arg = ztype.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def _is_list_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized list type."""
    if not ztype:
        return False
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "list"
    )


def _list_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of a list type."""
    return ztype.generic_args.get("of")


def _is_map_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized map type."""
    if not ztype:
        return False
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "map"
    )


def _map_key_type(ztype: ZType) -> Optional[ZType]:
    """Get the key type of a map type."""
    return ztype.generic_args.get("key")


def _map_value_type(ztype: ZType) -> Optional[ZType]:
    """Get the value type of a map type."""
    return ztype.generic_args.get("value")


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
    if name == "string":
        return "ZStr*"
    # use pre-computed cname when available
    if ztype.cname:
        if ztype.typetype in (ZTypeType.CLASS, ZTypeType.UNION, ZTypeType.PROTOCOL):
            return f"{ztype.cname}*"
        if ztype.typetype == ZTypeType.FUNCTION:
            return f"{ztype.cname}_ft"
        return ztype.cname
    # fallback for types without cname (e.g. synthesized helper types)
    if ztype.typetype == ZTypeType.RECORD and name not in TYPEMAP:
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.CLASS:
        return f"z_{name}_t*"
    if ztype.typetype == ZTypeType.UNION:
        return f"z_{name}_t*"
    if ztype.typetype == ZTypeType.VARIANT:
        return f"z_{name}_t"
    if ztype.typetype == ZTypeType.FUNCTION:
        cname = name.replace(".", "_")
        return f"z_{cname}_ft"
    if ztype.typetype == ZTypeType.PROTOCOL:
        return f"z_{name}_t*"
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
        if not k.startswith(":"):
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


class TrackedList(list):
    """A list that records the emitter's _current_node_id alongside each appended item."""

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
        self.needs_stdbool = False
        self.needs_string = False
        self.forward_decls: List[str] = []
        self.struct_defs: "TrackedList" = TrackedList(self)
        self.func_defs: "TrackedList" = TrackedList(self)
        self.data_defs: "TrackedList" = TrackedList(self)
        self.func_aliases: List[str] = []  # #define aliases for deduped functions
        # current AST node ID being emitted (set before emission blocks)
        self._current_node_id: Optional[int] = None
        # final source map: C output line (1-based) → AST node ID
        self.source_map: List[Optional[int]] = []
        # track numeric constant names (no distinct ZTypeType for these)
        self._const_names: set[str] = set()
        self._protocol_defs: dict[str, zast.Protocol] = {}  # name -> AST node
        self._facet_defs: dict[str, zast.Facet] = {}  # name -> AST node
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
        # static string literal deduplication
        self._string_literals: Dict[str, str] = {}  # escaped C string → static var name
        self._string_literal_counter: int = 0

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

        Tries the bare name first, then prefixed with the main unit name.
        This bridges the emitter's convention (bare names for main unit defs)
        with the type checker's convention (unitname.name keys).
        """
        t = self.program.resolved.get(name)
        if t is None:
            t = self.program.resolved.get(f"{self.program.mainunitname}.{name}")
        return t

    def _typetype_of(self, name: str) -> Optional[ZTypeType]:
        """Get the ZTypeType for a resolved name, or None if not found."""
        t = self._resolved_type(name)
        return t.typetype if t else None

    def _build_source_map(self, output: str) -> None:
        """Build source_map: for each C output line, the AST node ID that produced it.

        Uses the tracked node IDs from struct_defs, func_defs, data_defs.
        Lines from boilerplate (includes, ZStr runtime, main wrapper) get None.
        """
        # build a set of (line_start_offset, node_id) from tracked sections
        offset_to_node: List[tuple] = []
        for section in (self.struct_defs, self.func_defs, self.data_defs):
            if isinstance(section, TrackedList):
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
        lines.append(f"    if ({idx_expr} >= {len_expr}) {{\n")
        lines.append(
            f'        fprintf(stderr, "{label}: index {idx_fmt} out of bounds'
            f' (length {idx_fmt})\\n", {idx_cast}{idx_expr}, {idx_cast}{len_expr});\n'
        )
        lines.append("        exit(1);\n")
        lines.append("    }\n")

    def _emit_heap_container_create(
        self,
        lines: List[str],
        name: str,
        ctype: str,
        data_ctype: str,
    ) -> None:
        """Emit a heap-allocated container create function (list/map pattern)."""
        create_name = f"z_{name}_create"
        lines.append(f"static {ctype}* {create_name}(uint64_t _capacity);\n")
        lines.append(f"static {ctype}* {create_name}(uint64_t _capacity) {{\n")
        lines.append(f"    {ctype}* _this = ({ctype}*)malloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        lines.append("    _this->capacity = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            f"        _this->data = ({data_ctype}*)calloc(_capacity, sizeof({data_ctype}));\n"
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
        """Allocate a temporary variable for a heap-allocated string expression."""
        name = self._temp_name("t")
        indent = self._indent()
        self._temp.decls.append(f"{indent}ZStr* {name} = {expr};\n")
        self._temp.frees.append(name)
        self._temp.string_set.add(name)
        return name

    def _alloc_arg_temp(self, ctype: str, expr: str) -> str:
        """Allocate a temporary for a non-string argument (not freed)."""
        name = self._temp_name("a")
        indent = self._indent()
        self._temp.decls.append(f"{indent}{ctype} {name} = {expr};\n")
        return name

    def _emit_field_cleanup(
        self, access: str, ftype: ZType, indent: str = "    "
    ) -> str:
        """Emit cleanup code for a single field/variable given its ZType.

        Returns a C statement string (with newline) or empty string if no cleanup needed.
        """
        if ftype.needs_destructor and ftype.destructor_name:
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
        """Return the name of a static ZStr for this literal, deduplicating."""
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

    def _emit_callable_expr(self, call: zast.Call) -> str:
        """Emit the callable expression for a function call.

        Handles function pointer fields (struct field access) vs regular functions.
        """
        # check if this is a function pointer field call (e.g. c.op)
        if isinstance(call.callable, zast.DottedPath):
            ftype = call.callable.type
            if ftype and ftype.typetype == ZTypeType.FUNCTION:
                func_name = ftype.name
                if func_name in self._is_func_fields:
                    return self._emit_dotted_path_value(call.callable)
                # use the resolved type name for proper qualification
                # (handles subunit functions like mymod.helper.square)
                if "." in func_name:
                    return _mangle_func(func_name)
        callable_name = self._get_callable_name(call.callable)
        return self._mangle_callable(callable_name)

    def _qualify(self, prefix: str, name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

    def _is_generic_template(self, defn: zast.TypeDefinition) -> bool:
        """Check if a definition is a generic template (has .generic in items/as_items)."""
        items = None
        if isinstance(defn, (zast.Record, zast.Union, zast.Variant, zast.Class)):
            items = defn.as_items
        elif isinstance(defn, zast.Function):
            items = defn.parameters
        elif isinstance(defn, zast.Protocol):
            items = defn.parameters
        elif isinstance(defn, zast.Unit):
            items = defn.body
        if items is None:
            return False
        for fpath in items.values():
            if isinstance(fpath, zast.DottedPath) and isinstance(
                fpath.child, zast.AtomId
            ):
                if fpath.child.name in ("generic", "valtype", "reftype"):
                    return True
        return False

    def _is_typedef_defn(
        self, defn: "zast.Record | zast.Class | zast.Union | zast.Variant"
    ) -> bool:
        """Check if a type definition is a typedef (single .typedef item)."""
        items = defn.items
        if len(items) != 1:
            return False
        fpath = next(iter(items.values()))
        return (
            isinstance(fpath, zast.DottedPath)
            and isinstance(fpath.child, zast.AtomId)
            and fpath.child.name == "typedef"
        )

    def _typedef_base_name(
        self, defn: "zast.Record | zast.Class | zast.Union | zast.Variant"
    ) -> str:
        """Extract the base type name from a typedef definition."""
        fpath = next(iter(defn.items.values()))
        assert isinstance(fpath, zast.DottedPath)
        parent = fpath.parent
        if isinstance(parent, zast.AtomId):
            return parent.name
        return ""

    def _collect_pre_emission(self, prefix: str, body: dict) -> None:
        """Pre-emission pass: collect supplementary data not derivable from ZType.

        Gathers _const_names, _protocol_defs, _facet_defs, _is_func_fields,
        _proto_conformance, and _facet_conformers in a single walk.
        """
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            defn_type = type(defn)
            if defn_type == zast.Unit:
                self._collect_pre_emission(qname, defn.body)
            elif defn_type in (zast.Record, zast.Class):
                if not self._is_generic_template(defn):
                    for mname in defn.functions:
                        self._is_func_fields.add(f"{qname}.{mname}")
                    for label, apath in defn.as_items.items():
                        proto_name = (
                            apath.name if isinstance(apath, zast.AtomId) else None
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
            elif defn_type == zast.Protocol:
                if not self._is_generic_template(defn):
                    self._protocol_defs[qname] = defn
            elif defn_type == zast.Facet:
                if not self._is_generic_template(defn):
                    self._facet_defs[qname] = defn
            elif defn_type == zast.AtomId and _is_numeric_id(defn.name):
                self._const_names.add(qname)
            elif hasattr(defn, "const_value") and defn.const_value is not None:
                # unit-level expression that folded to a constant
                self._const_names.add(qname)

    def _emit_file_unit_functions(self, prefix: str, body: dict) -> None:
        """Emit functions from a file unit body, recursing into subunits."""
        for name, defn in body.items():
            qname = f"{prefix}.{name}"
            if isinstance(defn, zast.Function) and defn.body:
                self._current_node_id = defn.nodeid
                self._emit_function(qname, defn)
            elif isinstance(defn, zast.Unit):
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
            defn_type = type(defn)
            if defn_type == zast.Unit:
                self._pre_register_fields(qname, defn.body)
            elif defn_type in (zast.Record, zast.Class):
                if qname not in self._type_field_names:
                    if self._is_typedef_defn(defn):
                        continue
                    _, field_names, field_ctypes = self._collect_field_params(
                        qname, defn.items, defn.functions
                    )
                    self._type_field_names[qname] = field_names
                    self._type_field_ctypes[qname] = field_ctypes
                    self._type_field_defaults[qname] = self._extract_field_defaults(
                        qname, defn.items, defn.functions
                    )

    def _emit_unit_definitions(self, prefix: str, body: dict) -> None:
        """Recursively emit definitions from a unit body."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if self._is_generic_template(defn):
                continue
            # tag emitted output with the source AST node ID
            self._current_node_id = defn.nodeid if hasattr(defn, "nodeid") else None
            defn_type = type(defn)
            if defn_type == zast.Unit:
                self._emit_unit_definitions(qname, defn.body)
            elif defn_type == zast.Record:
                self._emit_record(qname, defn)
            elif defn_type == zast.Class:
                self._emit_class(qname, defn)
            elif defn_type == zast.Union:
                self._emit_union(qname, defn)
            elif defn_type == zast.Variant:
                self._emit_variant(qname, defn)
            elif defn_type == zast.Protocol:
                self._emit_protocol(qname, defn)
            elif defn_type == zast.Facet:
                pass  # facets emitted in deferred pass
            elif defn_type == zast.Function:
                if defn.body:
                    self._emit_func_typedef(qname, defn)
                    self._emit_function(qname, defn)
                else:
                    self._emit_spec_typedef(qname, defn)
            elif defn_type == zast.Data:
                self._emit_data(qname, defn)
            elif defn_type == zast.Expression and isinstance(
                defn.expression, zast.Data
            ):
                self._emit_data(qname, defn.expression)
            elif defn_type == zast.AtomId and _is_numeric_id(defn.name):
                self._emit_constant(qname, defn)
            elif hasattr(defn, "const_value") and isinstance(defn.const_value, int):
                # unit-level expression that folded to an integer constant
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
        self.data_defs.append(f"static const {ctype} {cname} = {int(v)};\n")

    def _emit_deferred_facets(self, prefix: str, body: dict) -> None:
        """Emit facet definitions and impls (deferred to after all conforming types)."""
        # first: emit facet struct definitions
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            defn_type = type(defn)
            if defn_type == zast.Unit:
                self._emit_deferred_facets(qname, defn.body)
            elif defn_type == zast.Facet:
                if not self._is_generic_template(defn):
                    self._emit_facet(qname, defn)
            elif defn_type in (zast.Record, zast.Variant):
                if not self._is_generic_template(defn):
                    for label, apath in defn.as_items.items():
                        facet_name = (
                            apath.name if isinstance(apath, zast.AtomId) else None
                        )
                        if (
                            facet_name
                            and self._typetype_of(facet_name) == ZTypeType.FACET
                        ):
                            self._emit_facet_impl(qname, label, facet_name, defn)

    def emit(self) -> str:
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return "/* empty program */\n"

        # pre-emission pass: collect supplementary data
        self._collect_pre_emission("", mainunit.body)

        # register monomorphized type names before emission
        for mono_type, _ in getattr(self.program, "mono_types", []):
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
                    if fn.startswith(":") or ft.typetype == ZTypeType.FUNCTION:
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
                    if fn.startswith(":") or ft.typetype == ZTypeType.FUNCTION:
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
        for mono_type, template_defn in getattr(self.program, "mono_types", []):
            if _is_str_type(mono_type):
                self._emit_mono_str(mono_type)

        # pre-register field info for all non-generic records/classes
        # so that construction calls work regardless of definition order
        self._pre_register_fields("", mainunit.body)

        # second pass: emit definitions (recursing into inline units)
        self._emit_unit_definitions("", mainunit.body)

        # third pass: emit facets (must come after all conforming types are defined)
        self._emit_deferred_facets("", mainunit.body)

        # emit monomorphized types (str already emitted above)
        for mono_type, template_defn in getattr(self.program, "mono_types", []):
            if _is_str_type(mono_type):
                continue
            self._current_node_id = mono_type.nodeid
            self._emit_mono_type(mono_type, template_defn)

        for unitname, unit in self.program.units.items():
            if unitname in ("system", "core", "io", self.program.mainunitname):
                continue
            # skip generic file unit templates (emitted via mono_types)
            if self._is_generic_template(unit):
                continue
            self._emit_file_unit_functions(unitname, unit.body)

        # assemble output
        parts: List[str] = []
        parts.append("/* Generated by zerolang compiler */\n\n")

        if self.needs_stdio:
            parts.append("#include <stdio.h>\n")
        if self.needs_stdint:
            parts.append("#include <stdint.h>\n")
        if self.needs_stdlib:
            parts.append("#include <stdlib.h>\n")
        if self.needs_stdbool:
            parts.append("#include <stdbool.h>\n")
        if self.needs_string:
            parts.append("#include <string.h>\n")
        if (
            self.needs_stdio
            or self.needs_stdint
            or self.needs_stdlib
            or self.needs_stdbool
            or self.needs_string
        ):
            parts.append("\n")

        if self.needs_string or self.needs_stdio:
            parts.append(
                "typedef struct {\n"
                "    uint64_t size;     /* bits 62-0: byte count; bit 63: static flag */\n"
                "    char data[];       /* NUL-terminated, starts at 8-byte boundary */\n"
                "} ZStr;\n\n"
                "#define ZSTR_STATIC_FLAG  0x8000000000000000ull\n"
                "#define ZSTR_SIZE(z)      ((z)->size & ~ZSTR_STATIC_FLAG)\n"
                "#define ZSTR_IS_STATIC(z) ((z)->size & ZSTR_STATIC_FLAG)\n\n"
                "#define ZSTR_STATIC(name, str) \\\n"
                "    static struct { uint64_t size; char data[sizeof(str)]; } \\\n"
                "    name##_storage = { (sizeof(str)-1) | ZSTR_STATIC_FLAG, str }; \\\n"
                "    static ZStr* name = (ZStr*)&name##_storage\n\n"
                "static ZStr* zstr_new(const char* s) {\n"
                "    uint64_t size = (uint64_t)strlen(s);\n"
                "    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + size + 1);\n"
                "    z->size = size;\n"
                "    memcpy(z->data, s, size + 1);\n"
                "    return z;\n"
                "}\n\n"
                "static ZStr* zstr_cat(ZStr* a, ZStr* b) {\n"
                "    uint64_t size = ZSTR_SIZE(a) + ZSTR_SIZE(b);\n"
                "    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + size + 1);\n"
                "    z->size = size;\n"
                "    memcpy(z->data, a->data, ZSTR_SIZE(a));\n"
                "    memcpy(z->data + ZSTR_SIZE(a), b->data, ZSTR_SIZE(b) + 1);\n"
                "    return z;\n"
                "}\n\n"
                "static ZStr* zstr_from_i64(int64_t n) {\n"
                "    char buf[32];\n"
                '    snprintf(buf, sizeof(buf), "%ld", (long)n);\n'
                "    return zstr_new(buf);\n"
                "}\n\n"
                "static ZStr* zstr_from_f64(double n) {\n"
                "    char buf[64];\n"
                '    snprintf(buf, sizeof(buf), "%g", n);\n'
                "    return zstr_new(buf);\n"
                "}\n\n"
                "static void zstr_print(ZStr* s) {\n"
                '    printf("%.*s\\n", (int)ZSTR_SIZE(s), s->data);\n'
                "}\n\n"
                "static void zstr_free(ZStr* s) {\n"
                "    if (s && !ZSTR_IS_STATIC(s)) free(s);\n"
                "}\n\n"
            )

            # emit static string literals
            for escaped, sname in self._string_literals.items():
                parts.append(f'ZSTR_STATIC({sname}, "{escaped}");\n')
            if self._string_literals:
                parts.append("\n")

        for st in self.spec_typedefs:
            parts.append(st)
        if self.spec_typedefs:
            parts.append("\n")
        for i, sd in enumerate(self.struct_defs):
            parts.append(sd)
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
        parts.append("    z_main();\n")
        parts.append("    return 0;\n")
        parts.append("}\n")

        output = "".join(parts)

        # build source map: for each output line, find the node ID
        # by walking the tracked output sections
        self._build_source_map(output)

        return output

    def _emit_func_typedef(self, name: str, func: zast.Function) -> None:
        """Emit a C typedef for a function (placed after struct defs)."""
        self.needs_stdint = True
        ret_ctype = self._return_ctype(func)
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            if pname.startswith(":"):
                continue
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
            if pname.startswith(":"):
                continue
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

    def _emit_protocol(self, name: str, proto: zast.Protocol) -> None:
        """Emit vtable struct, instance struct, and destroy function for a protocol."""
        self.needs_stdint = True
        self.needs_stdlib = True
        lines: List[str] = []

        # vtable struct — function pointers with void* as first param
        lines.append("typedef struct {\n")
        for sname, sfunc in proto.specs.items():
            ret_ctype = self._return_ctype(sfunc)
            params: List[str] = ["void*"]
            for pname, ppath in sfunc.parameters.items():
                if pname.startswith(":") or pname == "this":
                    continue
                params.append(_ctype(ppath.type))
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
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto);\n")
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto) {{\n")
        lines.append("    if (!proto) return;\n")
        lines.append("    if (proto->destroy) proto->destroy(proto->data);\n")
        lines.append("    free(proto);\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_protocol_impl(
        self,
        impl_name: str,
        label: str,
        proto_name: str,
        impl_defn: "zast.Record | zast.Class",
    ) -> None:
        """Emit wrapper functions, static vtable, and create function for a protocol implementation."""
        proto = self._protocol_defs.get(proto_name)
        if not proto:
            return
        is_class = isinstance(impl_defn, zast.Class)
        impl_ctype = f"z_{impl_name}_t"

        lines: List[str] = []

        # forward declarations for methods called by wrappers
        all_methods = dict(impl_defn.as_functions)
        all_methods.update(impl_defn.functions)
        for sname in proto.specs:
            mfunc = all_methods.get(sname)
            if mfunc and mfunc.body:
                ret_ctype = self._return_ctype(mfunc)
                params: List[str] = []
                for pname, ppath in mfunc.parameters.items():
                    if pname.startswith(":"):
                        continue
                    ptype_str = _ctype(ppath.type)
                    params.append(f"{ptype_str} {_mangle_var(pname)}")
                param_str = ", ".join(params) if params else "void"
                method_cname = _mangle_func(f"{impl_name}.{sname}")
                lines.append(f"static {ret_ctype} {method_cname}({param_str});\n")
        lines.append("\n")

        # wrapper functions for each spec
        for sname, sfunc in proto.specs.items():
            ret_ctype = self._return_ctype(sfunc)
            # wrapper params: void* _data, then remaining non-this params
            wrapper_params: List[str] = ["void* _data"]
            call_args: List[str] = []
            for pname, ppath in sfunc.parameters.items():
                if pname.startswith(":") or pname == "this":
                    continue
                pctype = _ctype(ppath.type)
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
        for sname in proto.specs:
            wrapper_name = f"z_{impl_name}_{label}_{sname}_wrapper"
            lines.append(f"    .{sname} = {wrapper_name},\n")
        lines.append("};\n\n")

        # create function (borrowed — pointer to original, no copy)
        create_name = f"z_{impl_name}_{label}_create"
        lines.append(f"static z_{proto_name}_t* {create_name}({impl_ctype}* val);\n")
        lines.append(f"static z_{proto_name}_t* {create_name}({impl_ctype}* val) {{\n")
        lines.append(
            f"    z_{proto_name}_t* proto = (z_{proto_name}_t*)malloc(sizeof(z_{proto_name}_t));\n"
        )
        lines.append("    proto->data = val;\n")
        lines.append(f"    proto->vtable = &{vtable_name};\n")
        lines.append("    proto->destroy = NULL;\n")
        lines.append("    return proto;\n")
        lines.append("}\n\n")

        # owned create + destroy wrapper
        if is_class:
            # class: destroy calls the class destructor
            destroy_name = f"z_{impl_name}_{label}_owned_destroy"
            lines.append(f"static void {destroy_name}(void* p) {{\n")
            lines.append(f"    z_{impl_name}_destroy(({impl_ctype}*)p);\n")
            lines.append("}\n\n")

            owned_create = f"z_{impl_name}_{label}_create_owned"
            lines.append(
                f"static z_{proto_name}_t* {owned_create}({impl_ctype}* val);\n"
            )
            lines.append(
                f"static z_{proto_name}_t* {owned_create}({impl_ctype}* val) {{\n"
            )
            lines.append(
                f"    z_{proto_name}_t* proto = (z_{proto_name}_t*)malloc(sizeof(z_{proto_name}_t));\n"
            )
            lines.append("    proto->data = val;\n")
            lines.append(f"    proto->vtable = &{vtable_name};\n")
            lines.append(f"    proto->destroy = {destroy_name};\n")
            lines.append("    return proto;\n")
            lines.append("}\n\n")
        else:
            # record: destroy frees boxed record (+ reftype fields)
            destroy_name = f"z_{impl_name}_{label}_boxed_destroy"
            lines.append(f"static void {destroy_name}(void* p) {{\n")
            lines.append(f"    {impl_ctype}* r = ({impl_ctype}*)p;\n")
            # cleanup reftype fields
            for fname, fpath in impl_defn.items.items():
                if fpath.type:
                    lines.append(self._emit_field_cleanup(f"r->{fname}", fpath.type))
            lines.append("    free(r);\n")
            lines.append("}\n\n")

            owned_create = f"z_{impl_name}_{label}_create_owned"
            lines.append(
                f"static z_{proto_name}_t* {owned_create}({impl_ctype} val);\n"
            )
            lines.append(
                f"static z_{proto_name}_t* {owned_create}({impl_ctype} val) {{\n"
            )
            lines.append(
                f"    z_{proto_name}_t* proto = (z_{proto_name}_t*)malloc(sizeof(z_{proto_name}_t));\n"
            )
            lines.append(
                f"    {impl_ctype}* boxed = ({impl_ctype}*)malloc(sizeof({impl_ctype}));\n"
            )
            lines.append("    *boxed = val;\n")
            lines.append("    proto->data = boxed;\n")
            lines.append(f"    proto->vtable = &{vtable_name};\n")
            lines.append(f"    proto->destroy = {destroy_name};\n")
            lines.append("    return proto;\n")
            lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_facet(self, name: str, facet: zast.Facet) -> None:
        """Emit vtable struct, data union, and instance struct for a facet."""
        self.needs_stdint = True
        lines: List[str] = []

        # vtable struct — function pointers with void* as first param (same as protocol)
        lines.append("typedef struct {\n")
        for sname, sfunc in facet.specs.items():
            ret_ctype = self._return_ctype(sfunc)
            params: List[str] = ["void*"]
            for pname, ppath in sfunc.parameters.items():
                if pname.startswith(":") or pname == "this":
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
        impl_defn: "zast.Record | zast.Variant",
    ) -> None:
        """Emit wrapper functions, static vtable, and create function for a facet implementation."""
        facet = self._facet_defs.get(facet_name)
        if not facet:
            return
        impl_ctype = f"z_{impl_name}_t"

        lines: List[str] = []

        # forward declarations for methods called by wrappers
        all_methods = dict(impl_defn.as_functions)
        all_methods.update(impl_defn.functions)
        for sname in facet.specs:
            mfunc = all_methods.get(sname)
            if mfunc and mfunc.body:
                ret_ctype = self._return_ctype(mfunc)
                params: List[str] = []
                for pname, ppath in mfunc.parameters.items():
                    if pname.startswith(":"):
                        continue
                    ptype_str = _ctype(ppath.type)
                    params.append(f"{ptype_str} {_mangle_var(pname)}")
                param_str = ", ".join(params) if params else "void"
                method_cname = _mangle_func(f"{impl_name}.{sname}")
                lines.append(f"static {ret_ctype} {method_cname}({param_str});\n")
        lines.append("\n")

        # wrapper functions for each spec
        for sname, sfunc in facet.specs.items():
            ret_ctype = self._return_ctype(sfunc)
            wrapper_params: List[str] = ["void* _data"]
            call_args: List[str] = []
            for pname, ppath in sfunc.parameters.items():
                if pname.startswith(":") or pname == "this":
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
        for sname in facet.specs:
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

    def _emit_record(self, name: str, rec: zast.Record) -> None:
        if self._is_typedef(name):
            # Typedef: no struct, no meta.create — just emit as/is functions
            for mname, mfunc in rec.as_functions.items():
                if mfunc.body:
                    self._emit_func_typedef(f"{name}.{mname}", mfunc)
                    self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
            for label, apath in rec.as_items.items():
                proto_name = apath.name if isinstance(apath, zast.AtomId) else None
                if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(name, label, proto_name, rec)
            return

        self.needs_stdint = True
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in rec.items.items():
            ftype = _ctype(fpath.type)
            lines.append(f"    {ftype} {fname};\n")
        # emit function pointer fields from 'is' section
        for mname, mfunc in rec.functions.items():
            decl = self._func_pointer_field_decl(name, mname, mfunc)
            lines.append(f"    {decl};\n")
        lines.append(f"}} z_{name}_t;\n\n")
        self.struct_defs.append("".join(lines))

        # emit meta.create constructor
        self._emit_meta_create(name, rec)

        # emit 'is' functions with body as regular C functions (for default values)
        for mname, mfunc in rec.functions.items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' functions as methods
        for mname, mfunc in rec.as_functions.items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit protocol implementations
        for label, apath in rec.as_items.items():
            proto_name = apath.name if isinstance(apath, zast.AtomId) else None
            if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                self._emit_protocol_impl(name, label, proto_name, rec)
            # facet impls are deferred to _emit_deferred_facets

    def _func_pointer_field_decl(
        self, parent_name: str, mname: str, mfunc: zast.Function
    ) -> str:
        """Get the full C struct field declaration for a function pointer in an 'is' section.
        Returns e.g. 'int64_t (*callback)(int64_t, int64_t)'"""
        ret_ctype = self._return_ctype(mfunc)
        params: List[str] = []
        for pname, ppath in mfunc.parameters.items():
            if pname.startswith(":"):
                continue
            ptype_str = _ctype(ppath.type)
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        return f"{ret_ctype} (*{mname})({param_str})"

    def _collect_field_params(self, name: str, items: dict, functions: dict) -> tuple:
        """Collect C parameter strings, field names, and field C types.

        Returns (params, field_names, field_ctypes).
        """
        params: List[str] = []
        field_names: List[str] = []
        field_ctypes: List[str] = []
        for fname, fpath in items.items():
            fct = _ctype(fpath.type)
            params.append(f"{fct} {fname}")
            field_names.append(fname)
            field_ctypes.append(fct)
        for mname, mfunc in functions.items():
            ret_ctype = self._return_ctype(mfunc)
            fp_params: List[str] = []
            for pname, ppath in mfunc.parameters.items():
                if pname.startswith(":"):
                    continue
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
            if isinstance(fpath, zast.AtomId) and _is_numeric_id(fpath.name):
                field_defaults[fname] = self._emit_numeric_literal(fpath.name)
            elif isinstance(fpath, zast.DottedPath):
                if isinstance(fpath.parent, zast.AtomId) and _is_numeric_id(
                    fpath.parent.name
                ):
                    child_name = fpath.child.name
                    dct = TYPEMAP.get(child_name, "int64_t")
                    typename, value, err = parse_number(fpath.parent.name + child_name)
                    if not err:
                        if typename.startswith("f"):
                            field_defaults[fname] = f"(({dct}){value})"
                        else:
                            field_defaults[fname] = f"(({dct}){int(value)})"
            elif (
                isinstance(fpath, zast.AtomId)
                and self._typetype_of(fpath.name) == ZTypeType.FUNCTION
            ):
                field_defaults[fname] = _mangle_func(fpath.name)
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
        arg_str = ", ".join(field_names) if field_names else ""
        func_name = f"z_{name}_meta_create"
        ret_type = f"{ctype}*" if is_heap else ctype
        lines.append(f"static {ret_type} {func_name}({param_str});\n")
        lines.append(f"static {ret_type} {func_name}({param_str}) {{\n")
        if is_heap:
            lines.append(f"    {ctype}* _this = ({ctype}*)malloc(sizeof({ctype}));\n")
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
            create_name = f"z_{name}_create"
            lines.append(f"static {ret_type} {create_name}({param_str});\n")
            lines.append(f"static {ret_type} {create_name}({param_str}) {{\n")
            lines.append(f"    return {func_name}({arg_str});\n")
            lines.append("}\n\n")

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
        assert isinstance(defn, (zast.Record, zast.Class))
        ztype = self._resolved_type(name)
        is_heap = ztype.is_heap_allocated if ztype else False
        ctype = f"z_{name}_t"
        params, field_names, field_ctypes = self._collect_field_params(
            name, defn.items, defn.functions
        )
        self._type_field_ctypes[name] = field_ctypes
        self._type_field_names[name] = field_names
        self._type_field_defaults[name] = self._extract_field_defaults(
            name, defn.items, defn.functions
        )
        has_user_create = "create" in defn.functions or "create" in defn.as_functions
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

    def _emit_class(self, name: str, cls: zast.Class) -> None:
        if self._is_typedef(name):
            # Typedef: no struct, no destructor, no meta.create — just emit as/is functions
            for mname, mfunc in cls.as_functions.items():
                if mfunc.body:
                    self._emit_func_typedef(f"{name}.{mname}", mfunc)
                    self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
            for label, apath in cls.as_items.items():
                proto_name = apath.name if isinstance(apath, zast.AtomId) else None
                if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                    self._emit_protocol_impl(name, label, proto_name, cls)
            return

        self.needs_stdint = True
        self.needs_stdlib = True
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in cls.items.items():
            ftype = _ctype(fpath.type)
            lines.append(f"    {ftype} {fname};\n")
        # emit function pointer fields from 'is' section
        for mname, mfunc in cls.functions.items():
            decl = self._func_pointer_field_decl(name, mname, mfunc)
            lines.append(f"    {decl};\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # emit destructor
        lines.append(f"static void z_{name}_destroy(z_{name}_t* p);\n")
        lines.append(f"static void z_{name}_destroy(z_{name}_t* p) {{\n")
        lines.append("    if (!p) return;\n")
        for fname, fpath in cls.items.items():
            if fpath.type:
                lines.append(self._emit_field_cleanup(f"p->{fname}", fpath.type))
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # emit meta.create constructor
        self._emit_meta_create(name, cls, lines)

        self.struct_defs.append("".join(lines))

        # emit 'is' functions with body as regular C functions (for default values)
        for mname, mfunc in cls.functions.items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit 'as' functions as methods
        for mname, mfunc in cls.as_functions.items():
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)
        # emit protocol implementations
        for label, apath in cls.as_items.items():
            proto_name = apath.name if isinstance(apath, zast.AtomId) else None
            if proto_name and self._typetype_of(proto_name) == ZTypeType.PROTOCOL:
                self._emit_protocol_impl(name, label, proto_name, cls)

    def _resolve_tag_values(
        self, union_defn: "zast.Union | zast.Variant"
    ) -> Optional[Dict[str, int]]:
        """Resolve custom tag values from as_items if a .tag reference exists."""
        for as_name, as_path in union_defn.as_items.items():
            if isinstance(as_path, zast.DottedPath) and as_path.child.name == "tag":
                # find the data definition name from the parent
                data_name = None
                if isinstance(as_path.parent, zast.AtomId):
                    data_name = as_path.parent.name
                if not data_name:
                    continue
                # look up the data definition in the program
                for unitname, unit in self.program.units.items():
                    defn = unit.body.get(data_name)
                    if defn is None:
                        continue
                    data_defn = None
                    if isinstance(defn, zast.Data):
                        data_defn = defn
                    elif isinstance(defn, zast.Expression) and isinstance(
                        defn.expression, zast.Data
                    ):
                        data_defn = defn.expression
                    if data_defn:
                        values: Dict[str, int] = {}
                        ordinal = 0
                        for item in data_defn.data:
                            ename = item.name if item.name is not None else str(ordinal)
                            ordinal += 1
                            if isinstance(item.valtype, zast.AtomId) and _is_numeric_id(
                                item.valtype.name
                            ):
                                _, val, err = parse_number(item.valtype.name)
                                if not err:
                                    values[ename] = int(val)
                        return values
        return None

    def _emit_union(self, name: str, union_defn: zast.Union) -> None:
        self.needs_stdint = True
        self.needs_stdlib = True
        lines: List[str] = []

        # resolve custom tag values from as_items
        custom_tag_values = self._resolve_tag_values(union_defn)

        # emit tag enum
        lines.append("typedef enum {\n")
        tag_names = []
        for sname in union_defn.items.keys():
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

        # emit destructor
        lines.append(f"static void z_{name}_destroy(z_{name}_t* u) {{\n")
        lines.append("    if (!u) return;\n")
        lines.append("    switch (u->tag) {\n")
        for sname, spath in union_defn.items.items():
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            is_null = isinstance(spath, zast.AtomId) and spath.name == "null"
            lines.append(f"        case {tag}:\n")
            if is_null:
                lines.append("            break;\n")
            else:
                stype = spath.type
                if stype and stype.needs_destructor and stype.destructor_name:
                    cast = f"({_ctype(stype)})u->data"
                    lines.append(f"            {stype.destructor_name}({cast});\n")
                else:
                    lines.append("            free(u->data);\n")
                lines.append("            break;\n")
        lines.append("    }\n")
        lines.append("    free(u);\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

        # emit methods
        all_funcs = list(union_defn.functions.items()) + list(
            union_defn.as_functions.items()
        )
        for mname, mfunc in all_funcs:
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)

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
        cloned_methods = getattr(self.program, "cloned_methods", {}).get(mangled, {})
        if not isinstance(template_defn, zast.Unit):
            return
        for dname, ddefn in template_defn.body.items():
            if dname in (template_defn.body.keys() - cloned_methods.keys()):
                # skip generic param declarations and non-function items
                pass
            if dname in cloned_methods:
                func = cloned_methods[dname]
                qualified = f"{mangled}.{dname}"
                # check for func alias (deduplication)
                aliases = getattr(self.program, "func_aliases", {})
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
            if sname.startswith(":") or sname == "tag":
                continue
            if stype.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if getattr(stype, "generic_origin", None) == "tag":
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

        # emit destructor
        lines.append(f"static void z_{name}_destroy(z_{name}_t* u) {{\n")
        lines.append("    if (!u) return;\n")
        lines.append("    switch (u->tag) {\n")
        for sname, stype in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            is_null = stype.name == "null" and stype.typetype == ZTypeType.RECORD
            lines.append(f"        case {tag}:\n")
            if is_null:
                lines.append("            break;\n")
            else:
                if stype.needs_destructor and stype.destructor_name:
                    cast = f"({_ctype(stype)})u->data"
                    lines.append(f"            {stype.destructor_name}({cast});\n")
                else:
                    lines.append("            free(u->data);\n")
                lines.append("            break;\n")
        lines.append("    }\n")
        lines.append("    free(u);\n")
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
        self.needs_stdbool = True
        name = mono_type.name
        lines: List[str] = []

        # collect subtypes (non-special children)
        subtype_items: list[tuple[str, ZType]] = []
        for sname, stype in mono_type.children.items():
            if sname.startswith(":") or sname == "tag":
                continue
            if stype.typetype in (
                ZTypeType.FUNCTION,
                ZTypeType.DATA,
                ZTypeType.TAG,
                ZTypeType.ENUM,
            ):
                continue
            if getattr(stype, "generic_origin", None) == "tag":
                continue
            subtype_items.append((sname, stype))

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname, _ in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            lines.append(f"    {tag},\n")
        lines.append(f"}} z_{name}_tag_t;\n\n")

        # check if all subtypes are null (enum pattern)
        all_null = all(
            stype.name == "null" and stype.typetype == ZTypeType.RECORD
            for _, stype in subtype_items
        )

        # emit variant struct with inline union
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_tag_t tag;\n")
        if not all_null:
            lines.append("    union {\n")
            for sname, stype in subtype_items:
                is_null = stype.name == "null" and stype.typetype == ZTypeType.RECORD
                if not is_null:
                    sub_ctype = _ctype(stype)
                    if sub_ctype and sub_ctype != "void":
                        lines.append(f"        {sub_ctype} {sname};\n")
            lines.append("    } data;\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # emit equality function
        lines.append(f"static bool z_{name}_eq(z_{name}_t a, z_{name}_t b) {{\n")
        lines.append("    if (a.tag != b.tag) return false;\n")
        lines.append("    switch (a.tag) {\n")
        for sname, stype in subtype_items:
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            is_null = stype.name == "null" and stype.typetype == ZTypeType.RECORD
            lines.append(f"        case {tag}:")
            if is_null:
                lines.append(" return true;\n")
            else:
                lines.append(f" return a.data.{sname} == b.data.{sname};\n")
        lines.append("    }\n")
        lines.append("    return false;\n")
        lines.append("}\n\n")

        # NO destructor — value type
        self.struct_defs.append("".join(lines))

    def _emit_mono_box(self, mono_type: ZType) -> None:
        """Emit a monomorphized box(valtype) type — just a destructor."""
        self.needs_stdlib = True
        name = mono_type.name
        inner_type = mono_type.generic_args.get("t")
        if not inner_type:
            return
        inner_ctype = _ctype(inner_type)
        ptr_ctype = f"{inner_ctype}*"
        lines: List[str] = []
        # destructor: free the heap-allocated value
        lines.append(f"static void z_{name}_destroy({ptr_ctype} v) {{\n")
        lines.append("    if (v) free(v);\n")
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
            if fname.startswith(":"):
                continue
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

        self.struct_defs.append("".join(lines))

    def _emit_mono_array(self, mono_type: ZType) -> None:
        """Emit a monomorphized array type (struct, create, get, set, length)."""
        self.needs_stdint = True
        self.needs_stdio = True
        name = mono_type.name
        ctype = f"z_{name}_t"
        elem_type = _array_element_type(mono_type)
        arr_len = _array_length(mono_type)
        if not elem_type or arr_len is None:
            return
        elem_ctype = _ctype(elem_type)
        lines: List[str] = []

        # struct definition
        lines.append("typedef struct {\n")
        lines.append(f"    {elem_ctype} data[{arr_len}];\n")
        lines.append(f"}} {ctype};\n\n")

        # length define
        lines.append(f"#define z_{name}_length {arr_len}\n\n")

        # create constructor (zero-initialized)
        create_name = f"z_{name}_create"
        lines.append(f"static {ctype} {create_name}(void);\n")
        lines.append(f"static {ctype} {create_name}(void) {{\n")
        # check if element type is a record (needs constructor call)
        if elem_type.typetype == ZTypeType.RECORD and elem_type.name not in TYPEMAP:
            lines.append(f"    {ctype} _this;\n")
            lines.append(
                f"    for (int _i = 0; _i < {arr_len}; _i++) {{ "
                f"_this.data[_i] = z_{elem_type.name}_create("
                f"{self._zero_args_for_ctypes(elem_type.name)}"
                f"); }}\n"
            )
        else:
            lines.append(f"    {ctype} _this = {{0}};\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

        # get method: returns element, runtime error on OOB
        self.needs_stdio = True
        get_name = f"z_{name}_get"
        lines.append(f"static {elem_ctype} {get_name}({ctype} _this, int64_t _idx);\n")
        lines.append(
            f"static {elem_ctype} {get_name}({ctype} _this, int64_t _idx) {{\n"
        )
        lines.append(f"    if (_idx < 0 || _idx >= {arr_len}) {{\n")
        lines.append(
            f'        fprintf(stderr, "array get: index %ld out of bounds (length {arr_len})\\n", (long)_idx);\n'
        )
        lines.append("        exit(1);\n")
        lines.append("    }\n")
        lines.append("    return _this.data[_idx];\n")
        lines.append("}\n\n")

        # set method: returns old element, runtime error on OOB
        set_name = f"z_{name}_set"
        lines.append(
            f"static {elem_ctype} {set_name}({ctype}* _this, int64_t _idx, {elem_ctype} _val);\n"
        )
        lines.append(
            f"static {elem_ctype} {set_name}({ctype}* _this, int64_t _idx, {elem_ctype} _val) {{\n"
        )
        lines.append(f"    if (_idx < 0 || _idx >= {arr_len}) {{\n")
        lines.append(
            f'        fprintf(stderr, "array set: index %ld out of bounds (length {arr_len})\\n", (long)_idx);\n'
        )
        lines.append("        exit(1);\n")
        lines.append("    }\n")
        lines.append(f"    {elem_ctype} _old = _this->data[_idx];\n")
        lines.append("    _this->data[_idx] = _val;\n")
        lines.append("    return _old;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_mono_str(self, mono_type: ZType) -> None:
        """Emit a monomorphized str type (struct, capacity define, create, string)."""
        self.needs_stdint = True
        self.needs_string = True
        name = mono_type.name
        ctype = f"z_{name}_t"
        cap = _str_capacity(mono_type)
        if cap is None:
            return
        lines: List[str] = []

        # struct definition
        lines.append("typedef struct {\n")
        lines.append("    uint64_t len;\n")
        lines.append(f"    char data[{cap + 1}];\n")
        lines.append(f"}} {ctype};\n\n")

        # capacity define
        lines.append(f"#define z_{name}_capacity {cap}\n\n")

        # create constructor
        create_name = f"z_{name}_create"
        lines.append(f"static {ctype} {create_name}(ZStr* _from);\n")
        lines.append(f"static {ctype} {create_name}(ZStr* _from) {{\n")
        lines.append(f"    {ctype} _this = {{0}};\n")
        lines.append("    if (_from) {\n")
        lines.append("        uint64_t slen = ZSTR_SIZE(_from);\n")
        lines.append(f"        if (slen > {cap}) slen = {cap};\n")
        lines.append("        _this.len = slen;\n")
        lines.append("        memcpy(_this.data, _from->data, slen);\n")
        lines.append("        _this.data[slen] = '\\0';\n")
        lines.append("    }\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")

        # .string method (str -> ZStr*)
        self.needs_stdlib = True
        string_name = f"z_{name}_string"
        lines.append(f"static ZStr* {string_name}({ctype} _this);\n")
        lines.append(f"static ZStr* {string_name}({ctype} _this) {{\n")
        lines.append("    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + _this.len + 1);\n")
        lines.append("    z->size = _this.len;\n")
        lines.append("    memcpy(z->data, _this.data, _this.len);\n")
        lines.append("    z->data[_this.len] = '\\0';\n")
        lines.append("    return z;\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_mono_list(self, mono_type: ZType) -> None:
        """Emit a monomorphized list type (struct, create, destroy, methods)."""
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
        elem_is_reftype = elem_ctype.endswith("*")
        lines: List[str] = []

        # struct definition
        lines.append("typedef struct {\n")
        lines.append("    uint64_t capacity;\n")
        lines.append("    uint64_t length;\n")
        lines.append(f"    {elem_ctype}* data;\n")
        lines.append(f"}} {ctype};\n\n")

        # destroy
        lines.append(f"static void z_{name}_destroy({ctype}* p);\n")
        lines.append(f"static void z_{name}_destroy({ctype}* p) {{\n")
        lines.append("    if (!p) return;\n")
        if elem_is_reftype:
            if elem_type and elem_type.needs_destructor and elem_type.destructor_name:
                lines.append("    for (uint64_t i = 0; i < p->length; i++) {\n")
                lines.append(f"        {elem_type.destructor_name}(p->data[i]);\n")
                lines.append("    }\n")
            else:
                lines.append("    for (uint64_t i = 0; i < p->length; i++) {\n")
                lines.append("        if (p->data[i]) free(p->data[i]);\n")
                lines.append("    }\n")
        lines.append("    free(p->data);\n")
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # create constructor
        self._emit_heap_container_create(lines, name, ctype, elem_ctype)

        # growth helper (inline in append/insert/extend via macro-like pattern)
        grow_fn = f"z_{name}_grow"
        lines.append(f"static void {grow_fn}({ctype}* _this, uint64_t _needed);\n")
        lines.append(f"static void {grow_fn}({ctype}* _this, uint64_t _needed) {{\n")
        lines.append("    if (_needed <= _this->capacity) return;\n")
        lines.append(
            "    uint64_t newcap = _this->capacity + (_this->capacity >> 1) + 4;\n"
        )
        lines.append("    if (newcap < _needed) newcap = _needed;\n")
        lines.append("    _this->capacity = newcap;\n")
        lines.append(
            f"    _this->data = ({elem_ctype}*)realloc(_this->data, newcap * sizeof({elem_ctype}));\n"
        )
        lines.append("}\n\n")

        # append
        lines.append(
            f"static void z_{name}_append({ctype}* _this, {elem_ctype} _val);\n"
        )
        lines.append(
            f"static void z_{name}_append({ctype}* _this, {elem_ctype} _val) {{\n"
        )
        lines.append(f"    {grow_fn}(_this, _this->length + 1);\n")
        lines.append("    _this->data[_this->length] = _val;\n")
        lines.append("    _this->length++;\n")
        lines.append("}\n\n")

        # insert
        lines.append(
            f"static void z_{name}_insert({ctype}* _this, {elem_ctype} _val, uint64_t _at);\n"
        )
        lines.append(
            f"static void z_{name}_insert({ctype}* _this, {elem_ctype} _val, uint64_t _at) {{\n"
        )
        self._emit_bounds_check(lines, "_at", "_this->length + 1", "list insert")
        lines.append(f"    {grow_fn}(_this, _this->length + 1);\n")
        lines.append(
            f"    memmove(&_this->data[_at + 1], &_this->data[_at], (_this->length - _at) * sizeof({elem_ctype}));\n"
        )
        lines.append("    _this->data[_at] = _val;\n")
        lines.append("    _this->length++;\n")
        lines.append("}\n\n")

        # extend
        lines.append(f"static void z_{name}_extend({ctype}* _this, {ctype}* _from);\n")
        lines.append(
            f"static void z_{name}_extend({ctype}* _this, {ctype}* _from) {{\n"
        )
        lines.append("    if (!_from) return;\n")
        lines.append(f"    {grow_fn}(_this, _this->length + _from->length);\n")
        lines.append(
            f"    memcpy(&_this->data[_this->length], _from->data, _from->length * sizeof({elem_ctype}));\n"
        )
        lines.append("    _this->length += _from->length;\n")
        lines.append("    free(_from->data);\n")
        lines.append("    free(_from);\n")
        lines.append("}\n\n")

        # get
        lines.append(
            f"static {elem_ctype} z_{name}_get({ctype}* _this, uint64_t _idx);\n"
        )
        lines.append(
            f"static {elem_ctype} z_{name}_get({ctype}* _this, uint64_t _idx) {{\n"
        )
        self._emit_bounds_check(lines, "_idx", "_this->length", "list get")
        lines.append("    return _this->data[_idx];\n")
        lines.append("}\n\n")

        # set
        lines.append(
            f"static {elem_ctype} z_{name}_set({ctype}* _this, uint64_t _idx, {elem_ctype} _val);\n"
        )
        lines.append(
            f"static {elem_ctype} z_{name}_set({ctype}* _this, uint64_t _idx, {elem_ctype} _val) {{\n"
        )
        self._emit_bounds_check(lines, "_idx", "_this->length", "list set")
        lines.append(f"    {elem_ctype} _old = _this->data[_idx];\n")
        lines.append("    _this->data[_idx] = _val;\n")
        lines.append("    return _old;\n")
        lines.append("}\n\n")

        # pop
        lines.append(f"static {elem_ctype} z_{name}_pop({ctype}* _this);\n")
        lines.append(f"static {elem_ctype} z_{name}_pop({ctype}* _this) {{\n")
        lines.append("    if (_this->length == 0) {\n")
        lines.append('        fprintf(stderr, "list pop: empty list\\n");\n')
        lines.append("        exit(1);\n")
        lines.append("    }\n")
        lines.append("    _this->length--;\n")
        lines.append("    return _this->data[_this->length];\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

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
        key_is_string = key_ctype == "ZStr*"
        val_is_string = val_ctype == "ZStr*"
        key_is_reftype = key_ctype.endswith("*")
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
            lines.append("    uint64_t len = ZSTR_SIZE(_key);\n")
            lines.append("    for (uint64_t i = 0; i < len; i++) {\n")
            lines.append("        h ^= (uint8_t)_key->data[i];\n")
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
                "    return ZSTR_SIZE(_a) == ZSTR_SIZE(_b) "
                "&& memcmp(_a->data, _b->data, ZSTR_SIZE(_a)) == 0;\n"
            )
        elif _is_str_type(key_type):
            lines.append(
                "    return _a.len == _b.len "
                "&& memcmp(_a.data, _b.data, _a.len) == 0;\n"
            )
        else:
            lines.append("    return _a == _b;\n")
        lines.append("}\n\n")

        # helper: free a key if reftype
        def emit_free_key(var: str, indent: str = "    ") -> str:
            if key_type and key_type.needs_destructor and key_type.destructor_name:
                return f"{indent}{key_type.destructor_name}({var});\n"
            if key_is_reftype:
                return f"{indent}if ({var}) free({var});\n"
            return ""

        # helper: free a value if reftype
        def emit_free_val(var: str, indent: str = "    ") -> str:
            if (
                value_type
                and value_type.needs_destructor
                and value_type.destructor_name
            ):
                return f"{indent}{value_type.destructor_name}({var});\n"
            if val_is_reftype:
                return f"{indent}if ({var}) free({var});\n"
            return ""

        # destroy
        lines.append(f"static void z_{name}_destroy({ctype}* p);\n")
        lines.append(f"static void z_{name}_destroy({ctype}* p) {{\n")
        lines.append("    if (!p) return;\n")
        if key_is_reftype or val_is_reftype:
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
        lines.append(f"    {ctype}* _this = ({ctype}*)malloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        lines.append("    if (_capacity < 8) _capacity = 0;\n")
        lines.append("    _this->capacity = _capacity;\n")
        lines.append("    if (_capacity > 0) {\n")
        lines.append(
            f"        _this->buckets = ({bucket_type}*)calloc(_capacity, sizeof({bucket_type}));\n"
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
            f"    {bucket_type}* new_buckets = ({bucket_type}*)calloc(new_cap, sizeof({bucket_type}));\n"
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
                    lines.append(
                        "        ZStr* _copy = (ZStr*)malloc(sizeof(ZStr) + ZSTR_SIZE(_this->buckets[idx].value) + 1);\n"
                    )
                    lines.append(
                        "        _copy->size = ZSTR_SIZE(_this->buckets[idx].value);\n"
                    )
                    lines.append(
                        "        memcpy(_copy->data, _this->buckets[idx].value->data, ZSTR_SIZE(_this->buckets[idx].value) + 1);\n"
                    )
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
                lines.append(
                    f"    {opt_struct}* _r = ({opt_struct}*)malloc(sizeof({opt_struct}));\n"
                )
                lines.append("    if (idx >= 0) {\n")
                lines.append(f"        _r->tag = {some_tag};\n")
                if val_is_reftype:
                    if val_is_string:
                        lines.append(
                            "        ZStr* _copy = (ZStr*)malloc(sizeof(ZStr) + ZSTR_SIZE(_this->buckets[idx].value) + 1);\n"
                        )
                        lines.append(
                            "        _copy->size = ZSTR_SIZE(_this->buckets[idx].value);\n"
                        )
                        lines.append(
                            "        memcpy(_copy->data, _this->buckets[idx].value->data, ZSTR_SIZE(_this->buckets[idx].value) + 1);\n"
                        )
                        lines.append("        _r->data = _copy;\n")
                    else:
                        lines.append("        _r->data = _this->buckets[idx].value;\n")
                else:
                    lines.append(
                        f"        {val_ctype}* _d = ({val_ctype}*)malloc(sizeof({val_ctype}));\n"
                    )
                    lines.append("        *_d = _this->buckets[idx].value;\n")
                    lines.append("        _r->data = _d;\n")
                lines.append("    } else {\n")
                lines.append(f"        _r->tag = {none_tag};\n")
                lines.append("        _r->data = NULL;\n")
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
            if not fn.startswith(":") and ft.typetype != ZTypeType.FUNCTION
        ]

        # struct typedef
        lines.append("typedef struct {\n")
        for fname, ftype in field_items:
            ct = _ctype(ftype)
            lines.append(f"    {ct} {fname};\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destructor
        lines.append(f"static void z_{name}_destroy(z_{name}_t* p);\n")
        lines.append(f"static void z_{name}_destroy(z_{name}_t* p) {{\n")
        lines.append("    if (!p) return;\n")
        for fname, ftype in field_items:
            lines.append(self._emit_field_cleanup(f"p->{fname}", ftype))
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # meta.create constructor
        self._emit_mono_create(name, mono_type, field_items, lines)

        self.struct_defs.append("".join(lines))

        # emit methods from cloned or template defn with mangled names
        cloned_methods = getattr(self.program, "cloned_methods", {}).get(name)
        func_aliases = getattr(self.program, "func_aliases", {})
        if isinstance(template_defn, zast.Class):
            for mname, mfunc in template_defn.as_functions.items():
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
            if not sn.startswith(":") and st.typetype == ZTypeType.FUNCTION
        ]

        # vtable struct
        lines.append("typedef struct {\n")
        for sname, stype in specs:
            ret_type = stype.return_type
            ret_ctype = _ctype(ret_type) if ret_type else "void"
            params = ["void*"]
            for pname, ptype in stype.children.items():
                if pname.startswith(":") or pname == "this":
                    continue
                params.append(_ctype(ptype))
            lines.append(f"    {ret_ctype} (*{sname})({', '.join(params)});\n")
        lines.append(f"}} z_{name}_vtable_t;\n\n")

        # instance struct
        lines.append("typedef struct {\n")
        lines.append("    void* data;\n")
        lines.append(f"    z_{name}_vtable_t* vtable;\n")
        lines.append("    void (*destroy)(void*);\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destroy function
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto);\n")
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto) {{\n")
        lines.append("    if (!proto) return;\n")
        lines.append("    if (proto->destroy) proto->destroy(proto->data);\n")
        lines.append("    free(proto);\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_variant(self, name: str, variant_defn: zast.Variant) -> None:
        self.needs_stdint = True
        self.needs_stdbool = True
        lines: List[str] = []

        # resolve custom tag values from as_items
        custom_tag_values = self._resolve_tag_values(variant_defn)

        # emit tag enum
        lines.append("typedef enum {\n")
        for sname in variant_defn.items.keys():
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            if custom_tag_values and sname in custom_tag_values:
                lines.append(f"    {tag} = {custom_tag_values[sname]},\n")
            else:
                lines.append(f"    {tag},\n")
        lines.append(f"}} z_{name}_tag_t;\n\n")

        # check if all subtypes are null (enum pattern)
        all_null = all(
            isinstance(spath, zast.AtomId) and spath.name == "null"
            for spath in variant_defn.items.values()
        )

        # emit variant struct with inline union
        lines.append("typedef struct {\n")
        lines.append(f"    z_{name}_tag_t tag;\n")
        if not all_null:
            lines.append("    union {\n")
            for sname, spath in variant_defn.items.items():
                is_null = isinstance(spath, zast.AtomId) and spath.name == "null"
                if not is_null:
                    sub_ctype = self._get_subtype_ctype(spath)
                    if sub_ctype:
                        lines.append(f"        {sub_ctype} {sname};\n")
            lines.append("    } data;\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # emit equality function
        lines.append(f"static bool z_{name}_eq(z_{name}_t a, z_{name}_t b) {{\n")
        lines.append("    if (a.tag != b.tag) return false;\n")
        lines.append("    switch (a.tag) {\n")
        for sname, spath in variant_defn.items.items():
            tag = f"Z_{name.upper()}_TAG_{sname.upper()}"
            is_null = isinstance(spath, zast.AtomId) and spath.name == "null"
            lines.append(f"        case {tag}:")
            if is_null:
                lines.append(" return true;\n")
            else:
                sub_ctype = self._get_subtype_ctype(spath)
                if (
                    sub_ctype
                    and sub_ctype.startswith("z_")
                    and sub_ctype.endswith("_t")
                ):
                    sub_name = sub_ctype[2:-2]  # z_foo_t -> foo
                    if self._typetype_of(sub_name) == ZTypeType.VARIANT:
                        # variant subtype: use its eq function
                        lines.append(
                            f" return z_{sub_name}_eq(a.data.{sname}, b.data.{sname});\n"
                        )
                    else:
                        # record subtype: compare with memcmp
                        lines.append(
                            f" return memcmp(&a.data.{sname}, &b.data.{sname}, sizeof({sub_ctype})) == 0;\n"
                        )
                        self.needs_string = True  # memcmp is in string.h
                else:
                    lines.append(f" return a.data.{sname} == b.data.{sname};\n")
        lines.append("    }\n")
        lines.append("    return false;\n")
        lines.append("}\n\n")

        # NO destructor — value type
        self.struct_defs.append("".join(lines))

        # emit methods
        all_funcs = list(variant_defn.functions.items()) + list(
            variant_defn.as_functions.items()
        )
        for mname, mfunc in all_funcs:
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)

    def _emit_data(self, name: str, data: zast.Data) -> None:
        self.needs_stdint = True
        values: List[str] = []
        for item in data.data:
            op = item.valtype
            if isinstance(op, zast.AtomId) and _is_numeric_id(op.name):
                _, val, err = parse_number(op.name)
                if not err:
                    if isinstance(val, float):
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
        if ct == "ZStr*":
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
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            if pname.startswith(":"):
                continue
            ptype_str = _ctype(ppath.type)
            params.append(f"{ptype_str} {_mangle_var(pname)}")
        param_str = ", ".join(params) if params else "void"
        self.forward_decls.append(f"{ret_ctype} {cname}({param_str});\n")

    def _emit_function(
        self, name: str, func: zast.Function, record_name: str = ""
    ) -> None:
        self.needs_stdint = True
        cname = _mangle_func(name)

        ret_ctype = self._return_ctype(func)

        params: List[str] = []
        for pname, ppath in func.parameters.items():
            # skip hidden :this parameter (unnamed first param of methods)
            if pname.startswith(":"):
                continue
            ptype_str = _ctype(ppath.type)
            params.append(f"{ptype_str} {_mangle_var(pname)}")

        param_str = ", ".join(params) if params else "void"

        self.forward_decls.append(f"{ret_ctype} {cname}({param_str});\n")

        # push new scope for this function
        func_nid = func.nodeid if hasattr(func, "nodeid") else 0
        self._scope_stack.append(
            ScopeState(record_name=record_name, func_nodeid=func_nid)
        )
        # track parameters that are class pointers
        if self._typetype_of(record_name) == ZTypeType.CLASS:
            for pname, ppath in func.parameters.items():
                if pname.startswith(":"):
                    continue
                ptype_str = _ctype(ppath.type)
                if ptype_str.endswith("*") and ptype_str.startswith("z_"):
                    self._scope.class_params.add(_mangle_var(pname))

        lines: List[str] = []
        lines.append(f"{ret_ctype} {cname}({param_str}) {{\n")
        self.indent_level = 1
        if func.body:
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

    def _emit_statement(self, stmt: zast.Statement) -> str:
        parts: List[str] = []
        for sline in stmt.statements:
            parts.append(self._emit_statement_line(sline))
        return "".join(parts)

    def _emit_statement_line(self, sline: zast.StatementLine) -> str:
        # save temp state for this statement
        self._temp_stack.append(TempState())

        inner = sline.statementline
        if isinstance(inner, zast.Assignment):
            code = self._emit_assignment(inner)
        elif isinstance(inner, zast.Reassignment):
            code = self._emit_reassignment(inner)
        elif isinstance(inner, zast.Swap):
            code = self._emit_swap(inner)
        elif isinstance(inner, zast.Expression):
            code = self._emit_expression_stmt(inner)
        else:
            code = ""

        # build result: temp decls + code + temp frees
        result = "".join(self._temp.decls) + code
        indent = self._indent()
        for t in self._temp.frees:
            if t in self._temp.string_set:
                result += f"{indent}zstr_free({t});\n"
            elif t in self._temp.class_set:
                tname = self._temp.class_set[t]
                if tname.startswith(":proto:"):
                    proto_name = tname[7:]  # strip ":proto:" prefix
                    result += f"{indent}z_{proto_name}_destroy({t});\n"
                else:
                    result += f"{indent}{self._emit_class_free(t, tname)}\n"
            else:
                result += f"{indent}free({t});\n"

        self._temp_stack.pop()
        return result

    def _emit_assignment(self, assign: zast.Assignment) -> str:
        indent = self._indent()
        # unit instantiation is compile-time only — no C code needed
        if assign.type and assign.type.typetype == ZTypeType.UNIT:
            # register the unit alias so dotted path resolution works
            self._unit_aliases[assign.name] = assign.type
            return ""
        ctype = "int64_t"
        if assign.type:
            ctype = _ctype(assign.type)
        cname = _mangle_var(assign.name)
        self._in_named_assignment = True
        val = self._emit_expression_value(assign.value)
        self._in_named_assignment = False
        if assign.type and assign.type.needs_destructor:
            if ctype == "ZStr*":
                self.needs_string = True
            self.needs_stdlib = True
            # the variable now owns the value — remove from temp frees
            if val in self._temp.frees:
                self._temp.frees.remove(val)
            self._scope.cleanup_vars.append((cname, assign.type))
        # check if value is a bare record name (zero-initialization)
        inner = assign.value.expression
        inner_resolved = (
            self._resolved_type(inner.name) if isinstance(inner, zast.AtomId) else None
        )
        if (
            isinstance(inner, zast.AtomId)
            and inner_resolved
            and inner_resolved.typetype == ZTypeType.RECORD
            and inner_resolved.name == inner.name
        ):
            ctype = f"z_{inner.name}_t"
        result = f"{indent}{ctype} {cname} = {val};\n"
        # nullify source on .take for class pointers
        take_var = self._get_take_var_from_expr(assign.value)
        if take_var:
            result += f"{indent}{take_var} = NULL;\n"
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
        if isinstance(inner, zast.AtomId) and inner.name == "break":
            return f"{indent}break;\n"
        if isinstance(inner, zast.AtomId) and inner.name == "continue":
            return f"{indent}continue;\n"
        if isinstance(inner, zast.Call):
            return self._emit_call_stmt(inner, indent)
        if isinstance(inner, zast.If):
            return self._emit_if(inner)
        if isinstance(inner, zast.For):
            return self._emit_for(inner)
        if isinstance(inner, zast.Do):
            return self._emit_do(inner)
        if isinstance(inner, zast.With):
            return self._emit_with(inner)
        if isinstance(inner, zast.Case):
            return self._emit_case(inner)
        if isinstance(inner, zast.DottedPath) and inner.child.name == "take":
            # function definitions are immutable — .take as statement is a no-op
            if isinstance(inner.parent, zast.AtomId) and _is_definition_name(
                inner.parent.name, self
            ):
                return ""
            var = self._emit_path_value(inner.parent)
            var_type = inner.type
            result = ""
            if var_type and var_type.name == "string":
                result += f"{indent}zstr_free({var});\n"
            elif var_type and var_type.typetype == ZTypeType.CLASS:
                result += f"{indent}z_{var_type.name}_destroy({var});\n"
            elif var_type and var_type.typetype == ZTypeType.UNION:
                result += f"{indent}z_{var_type.name}_destroy({var});\n"
            result += f"{indent}{var} = NULL;\n"
            return result
        if isinstance(inner, zast.Operation):
            val = self._emit_operation_value(inner)
            return f"{indent}{val};\n"
        return ""

    def _is_data_index_call(self, call: zast.Call) -> bool:
        """Check if this is a data.index call like primes.index i."""
        if isinstance(call.callable, zast.DottedPath):
            if isinstance(call.callable.parent, zast.AtomId):
                pname = call.callable.parent.name
                child = call.callable.child.name
                if self._typetype_of(pname) == ZTypeType.DATA and child == "index":
                    return True
        return False

    def _is_protocol_create(self, call: zast.Call) -> bool:
        """Check if call is protocol.create/take from: expr."""
        if not isinstance(call.callable, zast.DottedPath):
            return False
        if call.callable.child.name not in ("create", "take"):
            return False
        parent_type = call.callable.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _is_protocol_borrow(self, call: zast.Call) -> bool:
        """Check if call is protocol.borrow from: expr."""
        if not isinstance(call.callable, zast.DottedPath):
            return False
        if call.callable.child.name != "borrow":
            return False
        parent_type = call.callable.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _emit_protocol_create_call(self, call: zast.Call) -> str:
        """Emit owned protocol create: protocol.create from: expr."""
        assert isinstance(call.callable, zast.DottedPath)
        proto_type = call.callable.parent.type
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
            if isinstance(from_arg.valtype, zast.DottedPath):
                arg_type = from_arg.valtype.parent.type
        impl_name = arg_type.name if arg_type else ""

        # look up label
        label = self._proto_conformance.get((impl_name, proto_name), "")
        owned_create = f"z_{impl_name}_{label}_create_owned"

        # allocate temp and track as protocol var
        tmp = self._temp_name("c")
        indent = self._indent()
        self._temp.decls.append(
            f"{indent}z_{proto_name}_t* {tmp} = {owned_create}({arg_val});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.class_set[tmp] = f":proto:{proto_name}"

        # handle .take nullification for class (reftype) arguments only
        if arg_type and arg_type.typetype == ZTypeType.CLASS:
            take_var = self._get_take_var(from_arg.valtype)
            if take_var:
                self._temp.decls.append(f"{indent}{take_var} = NULL;\n")

        return tmp

    def _emit_protocol_borrow_call(self, call: zast.Call) -> str:
        """Emit borrowed protocol create: protocol.borrow from: expr."""
        assert isinstance(call.callable, zast.DottedPath)
        proto_type = call.callable.parent.type
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
            if isinstance(from_arg.valtype, zast.DottedPath):
                arg_type = from_arg.valtype.parent.type
        impl_name = arg_type.name if arg_type else ""

        # look up label
        label = self._proto_conformance.get((impl_name, proto_name), "")
        create_name = f"z_{impl_name}_{label}_create"

        # for value types (records): pass address; for reference types: pass directly
        if arg_type and arg_type.is_valtype:
            arg_expr = f"&{arg_val}"
        else:
            arg_expr = arg_val

        self.needs_stdlib = True

        # allocate temp and track as protocol var (borrowed: no destroy)
        tmp = self._temp_name("p")
        indent = self._indent()
        proto_ctype = f"z_{proto_name}_t"
        self._temp.decls.append(
            f"{indent}{proto_ctype}* {tmp} = {create_name}({arg_expr});\n"
        )
        self._temp.frees.append(tmp)
        self._temp.class_set[tmp] = f":proto:{proto_name}"

        return tmp

    def _is_facet_create(self, call: zast.Call) -> bool:
        """Check if call is facet.create/take from: expr."""
        if not isinstance(call.callable, zast.DottedPath):
            return False
        if call.callable.child.name not in ("create", "take"):
            return False
        parent_type = call.callable.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.FACET

    def _is_facet_borrow(self, call: zast.Call) -> bool:
        """Check if call is facet.borrow from: expr."""
        if not isinstance(call.callable, zast.DottedPath):
            return False
        if call.callable.child.name != "borrow":
            return False
        parent_type = call.callable.parent.type
        return parent_type is not None and parent_type.typetype == ZTypeType.FACET

    def _emit_facet_create_call(self, call: zast.Call) -> str:
        """Emit facet.create/take from: expr — returns a value (not pointer)."""
        assert isinstance(call.callable, zast.DottedPath)
        facet_type = call.callable.parent.type
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
            if isinstance(from_arg.valtype, zast.DottedPath):
                arg_type = from_arg.valtype.parent.type
        impl_name = arg_type.name if arg_type else ""

        label = self._proto_conformance.get((impl_name, facet_name), "")
        owned_create = f"z_{impl_name}_{label}_create_owned"
        return f"{owned_create}({arg_val})"

    def _emit_facet_borrow_call(self, call: zast.Call) -> str:
        """Emit facet.borrow from: expr — same as create (copies value)."""
        return self._emit_facet_create_call(call)

    def _emit_facet_dispatch(self, call: zast.Call) -> Optional[str]:
        """If call is a facet method dispatch, return the C expression. Otherwise None."""
        if not isinstance(call.callable, zast.DottedPath):
            return None
        parent_type = call.callable.parent.type
        if not parent_type or parent_type.typetype != ZTypeType.FACET:
            return None
        parent_val = self._emit_path_value(call.callable.parent)
        method = call.callable.child.name
        args = [f"(void*)&{parent_val}.data"]
        for arg in call.arguments:
            args.append(self._emit_operation_value(arg.valtype))
        return f"{parent_val}.vtable->{method}({', '.join(args)})"

    def _emit_protocol_dispatch(self, call: zast.Call) -> Optional[str]:
        """If call is a protocol method dispatch, return the C expression. Otherwise None."""
        if not isinstance(call.callable, zast.DottedPath):
            return None
        parent_type = call.callable.parent.type
        if not parent_type or parent_type.typetype != ZTypeType.PROTOCOL:
            return None
        parent_val = self._emit_path_value(call.callable.parent)
        method = call.callable.child.name
        args = [f"{parent_val}->data"]
        for arg in call.arguments:
            args.append(self._emit_operation_value(arg.valtype))
        return f"{parent_val}->vtable->{method}({', '.join(args)})"

    def _emit_callable_dispatch(self, call: zast.Call) -> str:
        """Emit a callable object dispatch: obj(args) -> z_type_call(obj, args)."""
        type_name = call.callable_type_name
        cname = _mangle_func(f"{type_name}.call")
        receiver = self._emit_path_value(call.callable)
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
            self.needs_string = True
            self.needs_stdlib = True
            if call.arguments:
                arg_op = call.arguments[0].valtype
                arg_type = self._get_operation_type(arg_op)
                if arg_type and _is_str_type(arg_type):
                    arg = self._emit_operation_value(arg_op)
                    return f'{indent}printf("%.*s\\n", (int){arg}.len, {arg}.data);\n'
                arg = self._emit_operation_value(arg_op)
                return f"{indent}zstr_print({arg});\n"
            t = self._static_string("")
            return f"{indent}zstr_print({t});\n"

        if callable_name == "return":
            return self._emit_return(call, indent)

        if callable_name == "break":
            return f"{indent}break;\n"
        if callable_name == "continue":
            return f"{indent}continue;\n"

        # data.index call -> array access
        if self._is_data_index_call(call) and isinstance(
            call.callable, zast.DottedPath
        ):
            assert isinstance(call.callable.parent, zast.AtomId)
            data_name = call.callable.parent.name
            idx = (
                self._emit_operation_value(call.arguments[0].valtype)
                if call.arguments
                else "0"
            )
            return f"{indent}{_mangle_func(data_name)}[{idx}];\n"

        # array method calls as statements
        if isinstance(call.callable, zast.DottedPath):
            dp_parent_type = call.callable.parent.type
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
                        code += f"{indent}{take_var} = NULL;\n"
                return code
            if dp_parent_type and _is_map_type(dp_parent_type):
                val = self._emit_call_value(call)
                code = f"{indent}{val};\n"
                for arg in call.arguments:
                    take_var = self._get_take_var(arg.valtype)
                    if take_var:
                        code += f"{indent}{take_var} = NULL;\n"
                return code

        args = self._emit_call_args(call)
        cname = self._emit_callable_expr(call)
        code = f"{indent}{cname}({args});\n"

        # if call takes a .take argument, nullify it after the call
        for arg in call.arguments:
            take_var = self._get_take_var(arg.valtype)
            if take_var:
                code += f"{indent}{take_var} = NULL;\n"

        # implicit take: nullify args passed to .take parameters
        ftype = call.callable.type
        if ftype and ftype.param_ownership:
            params = [
                (k, v) for k, v in ftype.children.items() if not k.startswith(":")
            ]
            for i, arg in enumerate(call.arguments):
                if i < len(params):
                    pname, _ = params[i]
                    if ftype.param_ownership.get(pname) == ZParamOwnership.TAKE:
                        # skip if already nullified by explicit .take
                        take_var = self._get_take_var(arg.valtype)
                        if not take_var:
                            root = self._get_implicit_take_var(arg.valtype)
                            if root:
                                code += f"{indent}{root} = NULL;\n"

        return code

    def _emit_return(self, call: zast.Call, indent: str) -> str:
        """Emit a return statement with proper string cleanup."""
        if call.arguments:
            # check for inline class construction: return ClassName field: val ...
            first_arg = call.arguments[0].valtype
            if (
                isinstance(first_arg, zast.AtomId)
                and first_arg.type
                and first_arg.type.typetype == ZTypeType.CLASS
                and len(call.arguments) > 1
            ):
                # emit as meta.create call
                self.needs_stdlib = True
                args_str, take_vars = self._build_meta_create_args(
                    first_arg.name, call.arguments, skip_first=1
                )
                result_expr = f"z_{first_arg.name}_create({args_str})"
                ctype = f"z_{first_arg.name}_t"
                tmp = self._temp_name("c")
                self._temp.decls.append(f"{indent}{ctype}* {tmp} = {result_expr};\n")
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
                    result += f"{indent}zstr_free({t});\n"
                elif t in self._temp.class_set:
                    tname = self._temp.class_set[t]
                    if tname.startswith(":proto:"):
                        proto_name = tname[7:]
                        result += f"{indent}z_{proto_name}_destroy({t});\n"
                    else:
                        result += f"{indent}{self._emit_class_free(t, tname)}\n"
                else:
                    result += f"{indent}free({t});\n"
            self._temp.frees.clear()

            # free func vars (except the return value)
            result += self._emit_scope_cleanup(indent, exclude_var=val)

            result += f"{indent}return {val};\n"
            return result

        # void return — free all func vars
        result = self._emit_scope_cleanup(indent)
        result += f"{indent}return;\n"
        return result

    def _get_take_var_from_expr(self, expr: zast.Expression) -> Optional[str]:
        """If expr is a var.take expression, return the mangled variable name."""
        inner = expr.expression
        if isinstance(inner, zast.DottedPath):
            return self._get_take_var(inner)
        if isinstance(inner, zast.Call):
            # could be a call with .take arg
            pass
        return None

    def _get_take_var(self, op: zast.Operation) -> Optional[str]:
        """If op is a var.take expression, return the mangled variable name."""
        if isinstance(op, zast.DottedPath):
            if op.child.name == "take" and isinstance(op.parent, zast.AtomId):
                name = op.parent.name
                if not _is_numeric_id(name):
                    # don't nullify function/spec definitions (immutable program text)
                    if _is_definition_name(name, self):
                        return None
                    return _mangle_var(name)
        return None

    def _get_implicit_take_var(self, op: zast.Operation) -> Optional[str]:
        """Get the variable name for implicit take (plain variable reference)."""
        if isinstance(op, zast.AtomId):
            name = op.name
            if (
                not _is_numeric_id(name)
                and self._typetype_of(name) != ZTypeType.FUNCTION
                and self._typetype_of(name) != ZTypeType.DATA
                and name not in self._const_names
            ):
                return _mangle_var(name)
        if isinstance(op, zast.Expression) and isinstance(
            op.expression, zast.Operation
        ):
            return self._get_implicit_take_var(op.expression)
        return None

    def _has_call(self, op: zast.Operation) -> bool:
        """Check if an operation contains a function call."""
        if isinstance(op, zast.Expression):
            inner = op.expression
            if isinstance(inner, zast.Call):
                return True
            if isinstance(inner, zast.Operation):
                return self._has_call(inner)
        if isinstance(op, zast.BinOp):
            return self._has_call(op.lhs) or self._has_call(op.rhs)
        return False

    def _get_param_ctypes(self, call: zast.Call) -> List[str]:
        """Get C types for each parameter of the called function."""
        if not call.callable.type:
            return []
        ftype = call.callable.type
        if ftype.typetype not in (ZTypeType.FUNCTION, ZTypeType.NULL):
            return []
        return [_ctype(v) for k, v in ftype.children.items() if not k.startswith(":")]

    def _emit_call_args(self, call: zast.Call) -> str:
        parts: List[str] = []
        param_ctypes = self._get_param_ctypes(call)
        for i, arg in enumerate(call.arguments):
            val = self._emit_operation_value(arg.valtype)
            if self._has_call(arg.valtype):
                ctype = param_ctypes[i] if i < len(param_ctypes) else "int64_t"
                # string-returning calls are already temped by _alloc_temp
                if ctype != "ZStr*":
                    val = self._alloc_arg_temp(ctype, val)
            parts.append(val)

        # fill defaults for missing trailing params
        ftype = call.callable.type
        if ftype and ftype.param_defaults:
            params = [
                (k, v) for k, v in ftype.children.items() if not k.startswith(":")
            ]
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
        take_vars: Dict[str, Optional[str]] = {}
        for arg in arguments[skip_first:]:
            if arg.name:
                val = self._emit_operation_value(arg.valtype)
                arg_map[arg.name] = val
                take_vars[arg.name] = self._get_take_var(arg.valtype)

        # build ordered args
        parts: List[str] = []
        for i, fname in enumerate(field_names):
            if fname in arg_map:
                parts.append(arg_map[fname])
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
        if isinstance(path, zast.AtomId):
            # resolve unit aliases to their actual type name
            if path.name in self._unit_aliases:
                return self._unit_aliases[path.name].name
            return path.name
        if isinstance(path, zast.DottedPath):
            parent = self._get_callable_name(path.parent)
            return f"{parent}.{path.child.name}"
        return "unknown"

    def _emit_expression_value(self, expr: zast.Expression) -> str:
        inner = expr.expression
        if isinstance(inner, zast.Call):
            return self._emit_call_value(inner)
        if isinstance(inner, zast.Operation):
            # bare function name = call with all-default args
            if (
                isinstance(inner, zast.AtomId)
                and self._typetype_of(inner.name) == ZTypeType.FUNCTION
            ):
                ftype = inner.type
                if ftype and ftype.param_defaults:
                    cname = _mangle_func(inner.name)
                    defaults: List[str] = []
                    for pname, _ in ftype.children.items():
                        if pname.startswith(":"):
                            continue
                        if pname in ftype.param_defaults:
                            d = ftype.param_defaults[pname]
                            if self._typetype_of(d) == ZTypeType.FUNCTION:
                                d = _mangle_func(d)
                            defaults.append(d)
                    return f"{cname}({', '.join(defaults)})"
            return self._emit_operation_value(inner)
        if isinstance(inner, zast.With):
            return self._emit_expression_value(inner.doexpr)
        if isinstance(inner, zast.If):
            return self._emit_if_expression_value(inner)
        if isinstance(inner, zast.For) and inner.type:
            return self._emit_for_expression_value(inner)
        return "0"

    def _emit_call_value(self, call: zast.Call) -> str:
        # callable object dispatch: obj(args) -> z_type_call(obj, args)
        if call.call_kind == zast.CallKind.CALLABLE:
            result = self._emit_callable_dispatch(call)
            if call.type:
                if call.type.name == "string":
                    return self._alloc_temp(result)
                if call.type.typetype == ZTypeType.CLASS:
                    ctype = f"z_{call.type.name}_t"
                    tmp = self._temp_name("c")
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
                    self._temp.frees.append(tmp)
                    self._temp.class_set[tmp] = call.type.name
                    return tmp
                if call.type.typetype == ZTypeType.UNION:
                    ctype = f"z_{call.type.name}_t"
                    tmp = self._temp_name("c")
                    indent = self._indent()
                    self._temp.decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
                    self._temp.frees.append(tmp)
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
        if self._is_data_index_call(call) and isinstance(
            call.callable, zast.DottedPath
        ):
            assert isinstance(call.callable.parent, zast.AtomId)
            data_name = call.callable.parent.name
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

        # str construction: (str to: N) or (str to: N) from: expr
        if call.callable.type and _is_str_type(call.callable.type):
            str_name = call.callable.type.name
            # find from: argument
            from_arg = None
            for arg in call.arguments:
                if arg.name == "from":
                    from_arg = arg
                    break
            if from_arg is not None:
                # check for string literal optimization
                from_val_inner = from_arg.valtype
                if isinstance(from_val_inner, zast.Expression):
                    from_val_inner = from_val_inner.expression
                if isinstance(from_val_inner, zast.AtomString) and not any(
                    isinstance(p, zast.Expression) for p in from_val_inner.stringparts
                ):
                    # literal string — emit direct struct initialization
                    cap = _str_capacity(call.callable.type)
                    literal = self._collect_string_literal(from_val_inner.stringparts)
                    lit_len = len(
                        literal.encode("utf-8").decode("unicode_escape").encode("utf-8")
                    )
                    if cap is not None and lit_len <= cap:
                        ctype = f"z_{str_name}_t"
                        return f'({ctype}){{{lit_len}, "{literal}"}}'
                from_val = self._emit_operation_value(from_arg.valtype)
                return f"z_{str_name}_create({from_val})"
            return f"z_{str_name}_create(NULL)"

        # list construction: (list of: T) or (list of: T) capacity: N
        if call.callable.type and _is_list_type(call.callable.type):
            list_name = call.callable.type.name
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
            map_name = call.callable.type.name
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
        if isinstance(call.callable, zast.DottedPath):
            dp_parent_type = call.callable.parent.type
            if dp_parent_type and _is_array_type(dp_parent_type):
                method_name = call.callable.child.name
                parent_val = self._emit_path_value(call.callable.parent)
                arr_type_name = dp_parent_type.name
                if method_name == "get" and call.arguments:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{arr_type_name}_get({parent_val}, {idx_val})"
                if method_name == "set" and len(call.arguments) >= 2:
                    idx_val = self._emit_operation_value(call.arguments[0].valtype)
                    val_val = self._emit_operation_value(call.arguments[1].valtype)
                    return f"z_{arr_type_name}_set(&{parent_val}, {idx_val}, {val_val})"

        # str method calls: .string
        if isinstance(call.callable, zast.DottedPath):
            dp_parent_type = call.callable.parent.type
            if dp_parent_type and _is_str_type(dp_parent_type):
                method_name = call.callable.child.name
                parent_val = self._emit_path_value(call.callable.parent)
                str_type_name = dp_parent_type.name
                if method_name == "string":
                    result = f"z_{str_type_name}_string({parent_val})"
                    return self._alloc_temp(result)

        # list method calls: .append, .insert, .extend, .get, .set, .pop
        if isinstance(call.callable, zast.DottedPath):
            dp_parent_type = call.callable.parent.type
            if dp_parent_type and _is_list_type(dp_parent_type):
                method_name = call.callable.child.name
                parent_val = self._emit_path_value(call.callable.parent)
                list_type_name = dp_parent_type.name
                if method_name == "append" and call.arguments:
                    from_arg = call.arguments[0]
                    val = self._emit_operation_value(from_arg.valtype)
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
                    from_val = self._emit_operation_value(call.arguments[0].valtype)
                    return f"z_{list_type_name}_extend({parent_val}, {from_val})"
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

        # map method calls: .set, .get, .delete, .has
        if isinstance(call.callable, zast.DottedPath):
            dp_parent_type = call.callable.parent.type
            if dp_parent_type and _is_map_type(dp_parent_type):
                method_name = call.callable.child.name
                parent_val = self._emit_path_value(call.callable.parent)
                map_type_name = dp_parent_type.name
                if method_name == "set" and len(call.arguments) >= 2:
                    key_val = None
                    val_val = None
                    for arg in call.arguments:
                        if arg.name == "key":
                            key_val = self._emit_operation_value(arg.valtype)
                        elif arg.name == "value":
                            val_val = self._emit_operation_value(arg.valtype)
                    if key_val is None:
                        key_val = self._emit_operation_value(call.arguments[0].valtype)
                    if val_val is None:
                        val_val = self._emit_operation_value(call.arguments[1].valtype)
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
            args_str, take_vars = self._build_meta_create_args(
                rec_type.name, call.arguments
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
            args_str, take_vars = self._build_meta_create_args(
                cls_type.name, call.arguments
            )
            result = f"z_{cls_type.name}_create({args_str})"
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
            # handle .take nullification
            for fname, tv in take_vars.items():
                if tv:
                    self._temp.decls.append(f"{indent}{tv} = NULL;\n")
            self._temp.frees.append(tmp)
            self._temp.class_set[tmp] = cls_type.name
            return tmp

        args = self._emit_call_args(call)
        cname = self._emit_callable_expr(call)
        result = f"{cname}({args})"

        # if call returns a reftype, wrap in temp for cleanup
        if call.type:
            if call.type.name == "string":
                return self._alloc_temp(result)
            if call.type.typetype == ZTypeType.CLASS:
                ctype = f"z_{call.type.name}_t"
                tmp = self._temp_name("c")
                indent = self._indent()
                self._temp.decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
                self._temp.frees.append(tmp)
                self._temp.class_set[tmp] = call.type.name
                return tmp
            if call.type.typetype == ZTypeType.UNION:
                ctype = f"z_{call.type.name}_t"
                tmp = self._temp_name("c")
                indent = self._indent()
                self._temp.decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
                self._temp.frees.append(tmp)
                return tmp

        return result

    def _emit_const_value(self, node: zast.Node) -> str:
        """Emit a compile-time constant value as a C literal."""
        v = node.const_value
        assert v is not None
        self.needs_stdint = True
        if isinstance(v, bool):
            return "1" if v else "0"
        raw = str(int(v))
        if node.type and node.type.name != "i64":
            ctype = TYPEMAP.get(node.type.name, "int64_t")
            if ctype != "int64_t":
                return f"(({ctype}){raw})"
        return raw

    def _emit_operation_value(self, op: zast.Operation) -> str:
        if op.const_value is not None:
            return self._emit_const_value(op)
        if isinstance(op, zast.BinOp):
            return self._emit_binop_value(op)
        if isinstance(op, zast.Path):
            return self._emit_path_value(op)
        return "0"

    def _emit_binop_value(self, binop: zast.BinOp) -> str:
        if binop.const_value is not None:
            return self._emit_const_value(binop)
        lhs = self._emit_operation_value(binop.lhs)
        rhs = self._emit_path_value(binop.rhs)
        # auto-deref boxed valtypes in binary operations
        if (
            isinstance(binop.lhs, zast.AtomId)
            and binop.lhs.type
            and binop.lhs.type.is_box
        ):
            lhs = f"(*{lhs})"
        if (
            isinstance(binop.rhs, zast.AtomId)
            and binop.rhs.type
            and binop.rhs.type.is_box
        ):
            rhs = f"(*{rhs})"
        op = binop.operator.name
        cop = C_OPS.get(op, op)
        return f"({lhs} {cop} {rhs})"

    def _emit_path_value(self, path: zast.Path) -> str:
        if isinstance(path, zast.Expression):
            return self._emit_expression_value(path)
        if isinstance(path, zast.AtomString):
            return self._emit_string_value(path)
        if isinstance(path, zast.AtomId):
            return self._emit_atomid_value(path)
        if isinstance(path, zast.DottedPath):
            return self._emit_dotted_path_value(path)
        return "0"

    def _emit_atomid_value(self, atom: zast.AtomId) -> str:
        name = atom.name
        if _is_numeric_id(name):
            return self._emit_numeric_literal(name)
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
        if tt == ZTypeType.CLASS and resolved is not None and resolved.name == name:
            self.needs_stdlib = True
            ctype = f"z_{name}_t"
            zero_args = self._zero_args_for_ctypes(name)
            tmp = self._temp_name("c")
            indent = self._indent()
            self._temp.decls.append(
                f"{indent}{ctype}* {tmp} = z_{name}_create({zero_args});\n"
            )
            self._temp.frees.append(tmp)
            self._temp.class_set[tmp] = name
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
                f'if (_v < {lo} || _v > {hi}) {{ fprintf(stderr, "numeric cast overflow: {src_type} to {dst_type}\\n"); exit(1); }} '
                f"({dst_ctype})_v; }})"
            )
        return f"(({dst_ctype}){val})"

    def _extract_unit_path(self, path: zast.Path) -> Optional[str]:
        """If path resolves to an inline unit, return its dotted name. Otherwise None."""
        if isinstance(path, zast.AtomId):
            if self._typetype_of(path.name) == ZTypeType.UNIT:
                return path.name
            return None
        if isinstance(path, zast.DottedPath):
            parent_path = self._extract_unit_path(path.parent)
            if parent_path is not None:
                qname = f"{parent_path}.{path.child.name}"
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

        if isinstance(path.parent, zast.AtomId):
            pname = path.parent.name
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
            # unit.name reference (file-level units)
            if pname in self.program.units and pname not in ("system", "core", "io"):
                return _mangle_func(f"{pname}.{child}")
            # inline unit.name reference
            if self._typetype_of(pname) == ZTypeType.UNIT:
                qname = f"{pname}.{child}"
                # check if the child is itself a unit (nested)
                if self._typetype_of(qname) == ZTypeType.UNIT:
                    # will be resolved by further dotted path traversal
                    return _mangle_func(qname)
                return _mangle_func(qname)
            # record_name.method or class_name.method — method call with no extra args
            ptt = self._typetype_of(pname)
            if ptt in (ZTypeType.RECORD, ZTypeType.CLASS):
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
        # str: .capacity constant access
        if parent_type_dp and _is_str_type(parent_type_dp) and child == "capacity":
            return f"z_{parent_type_dp.name}_capacity"
        # str: .string conversion (str -> ZStr*)
        if parent_type_dp and _is_str_type(parent_type_dp) and child == "string":
            parent = self._emit_path_value(path.parent)
            result = f"z_{parent_type_dp.name}_string({parent})"
            return self._alloc_temp(result)
        # list: .length field access
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "length":
            parent = self._emit_path_value(path.parent)
            return f"{parent}->length"
        # list: .capacity field access
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "capacity":
            parent = self._emit_path_value(path.parent)
            return f"{parent}->capacity"
        # list: .pop as dotted path (zero-arg method call)
        if parent_type_dp and _is_list_type(parent_type_dp) and child == "pop":
            parent = self._emit_path_value(path.parent)
            return f"z_{parent_type_dp.name}_pop({parent})"
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
        # check if the dotted path resolves to a function (method call or field access)
        if path.type and path.type.typetype == ZTypeType.FUNCTION:
            func_name = path.type.name  # e.g. "calculator.op" or "point.distance"
            # function pointer fields (from 'is' section) → struct field access
            if func_name in self._is_func_fields:
                parent = self._emit_path_value(path.parent)
                if self._is_class_pointer_path(path.parent):
                    return f"{parent}->{child}"
                return f"{parent}.{child}"
            # regular methods (from 'as' section) → method call
            parent = self._emit_path_value(path.parent)
            return f"{_mangle_func(func_name)}({parent})"

        # protocol instance creation: obj.label where label maps to a protocol
        if path.type and path.type.typetype == ZTypeType.PROTOCOL:
            parent_type = path.parent.type
            if parent_type and parent_type.typetype in (
                ZTypeType.RECORD,
                ZTypeType.CLASS,
            ):
                self.needs_stdlib = True
                parent_val = self._emit_path_value(path.parent)
                create_name = f"z_{parent_type.name}_{child}_create"
                if parent_type.is_valtype:
                    arg = f"&{parent_val}"
                else:
                    arg = parent_val
                tmp = self._temp_name("p")
                indent = self._indent()
                proto_ctype = f"z_{path.type.name}_t"
                if self._in_named_assignment:
                    # named var: heap-allocate via create function
                    self._temp.decls.append(
                        f"{indent}{proto_ctype}* {tmp} = {create_name}({arg});\n"
                    )
                    self._temp.frees.append(tmp)
                    self._temp.class_set[tmp] = f":proto:{path.type.name}"
                else:
                    # temp: stack-allocate (no malloc/free needed)
                    stk = f"{tmp}s"  # companion stack var for protocol temp
                    vtable_name = f"z_{parent_type.name}_{child}_vtable"
                    self._temp.decls.append(
                        f"{indent}{proto_ctype} {stk};\n"
                        f"{indent}{stk}.data = {arg};\n"
                        f"{indent}{stk}.vtable = &{vtable_name};\n"
                        f"{indent}{stk}.destroy = NULL;\n"
                        f"{indent}{proto_ctype}* {tmp} = &{stk};\n"
                    )
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
            # check if child is a subtype name (not a method)
            child_type = parent_type.children.get(child)
            if child_type and child_type.typetype != ZTypeType.FUNCTION:
                return f"{parent}.data.{child}"
        return f"{parent}.{child}"

    def _is_class_pointer_path(self, path: zast.Path) -> bool:
        """Check if a path refers to a class/union/protocol pointer (for -> vs . dispatch)."""
        # type annotation from type checker
        parent_type = path.type
        if parent_type and parent_type.typetype in (
            ZTypeType.CLASS,
            ZTypeType.UNION,
            ZTypeType.PROTOCOL,
        ):
            return True
        # local class/union/protocol variable tracked for cleanup
        if isinstance(path, zast.AtomId):
            cname = _mangle_var(path.name)
            for vname, vtype in self._scope.cleanup_vars:
                if vname == cname and vtype.is_heap_allocated:
                    return True
            # class method parameter (this/type resolves to class pointer)
            if self._typetype_of(self._scope.record_name) == ZTypeType.CLASS:
                if cname in self._scope.class_params:
                    return True
        return False

    def _emit_class_free(self, var: str, type_name: Optional[str]) -> str:
        """Emit the right destroy call for a class variable."""
        if type_name:
            return f"z_{type_name}_destroy({var});"
        return f"if ({var}) free({var});"

    def _is_union_construction(self, call: zast.Call) -> bool:
        """Check if a call is a union construction (union.subtype or bare union name)."""
        # check type annotation for monomorphized union types
        call_type = call.type
        if (
            call_type
            and call_type.typetype == ZTypeType.UNION
            and call_type.generic_origin
        ):
            return True
        if isinstance(call.callable, zast.DottedPath):
            if isinstance(call.callable.parent, zast.AtomId):
                if self._typetype_of(call.callable.parent.name) == ZTypeType.UNION:
                    return True
        if isinstance(call.callable, zast.AtomId):
            if self._typetype_of(call.callable.name) == ZTypeType.UNION:
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

        if isinstance(call.callable, zast.DottedPath) and isinstance(
            call.callable.parent, zast.AtomId
        ):
            subtype_name = call.callable.child.name
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
        elif isinstance(call.callable.parent, zast.AtomId):
            union_name = call.callable.parent.name
        else:
            return "NULL"

        ctype = f"z_{union_name}_t"
        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp.decls.append(
            f"{indent}{ctype}* {tmp} = ({ctype}*)malloc(sizeof({ctype}));\n"
        )
        self._temp.decls.append(f"{indent}{tmp}->tag = {tag};\n")

        # determine subtype info — check monomorphized type first
        is_null = False
        subtype_ctype_resolved = None
        if call_type and call_type.generic_origin:
            # monomorphized: look up subtype from the mono ZType
            sub_ztype = call_type.children.get(subtype_name)
            if sub_ztype:
                is_null = (
                    sub_ztype.name == "null" and sub_ztype.typetype == ZTypeType.RECORD
                )
                if not is_null:
                    subtype_ctype_resolved = _ctype(sub_ztype)
        else:
            # non-generic: look up from AST
            mainunit = self.program.units.get(self.program.mainunitname)
            union_defn = mainunit.body.get(union_name) if mainunit else None
            subtype_path = None
            if isinstance(union_defn, zast.Union):
                subtype_path = union_defn.items.get(subtype_name)
            is_null = (
                subtype_path is not None
                and isinstance(subtype_path, zast.AtomId)
                and subtype_path.name == "null"
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

        if is_null or value_arg is None:
            self._temp.decls.append(f"{indent}{tmp}->data = NULL;\n")
        else:
            # for monomorphized null subtype with explicit type arg, skip the arg
            # (it's a type name, not a value)
            if call_type and call_type.generic_origin and is_null:
                self._temp.decls.append(f"{indent}{tmp}->data = NULL;\n")
            else:
                val = self._emit_operation_value(value_arg.valtype)
                subtype_ctype = subtype_ctype_resolved
                if (
                    subtype_ctype
                    and subtype_ctype in ("ZStr*",)
                    or (
                        subtype_ctype
                        and subtype_ctype.startswith("z_")
                        and subtype_ctype.endswith("_t*")
                    )
                ):
                    # reftype: store pointer directly
                    if val in self._temp.frees:
                        self._temp.frees.remove(val)
                    self._temp.decls.append(f"{indent}{tmp}->data = {val};\n")
                else:
                    # valtype: box it (malloc + copy)
                    box_ctype = subtype_ctype or "int64_t"
                    box_tmp = self._temp_name("box")
                    self._temp.decls.append(
                        f"{indent}{box_ctype}* {box_tmp} = ({box_ctype}*)malloc(sizeof({box_ctype}));\n"
                    )
                    self._temp.decls.append(f"{indent}*{box_tmp} = {val};\n")
                    self._temp.decls.append(f"{indent}{tmp}->data = {box_tmp};\n")

        self._temp.frees.append(tmp)
        return tmp

    def _emit_nullable_ptr_construction(self, call: zast.Call) -> str:
        """Emit nullable-ptr option construction: .some val → val, .none → NULL."""
        if isinstance(call.callable, zast.DottedPath):
            subtype_name = call.callable.child.name
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
        tmp = self._temp_name("box")

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
            f"{indent}{ptr_ctype} {tmp} = ({ptr_ctype})malloc(sizeof({inner_ctype}));\n"
        )
        self._temp.decls.append(f"{indent}*{tmp} = {val};\n")
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
        self._temp.decls.append(
            f"{indent}{ctype}* {tmp} = ({ctype}*)malloc(sizeof({ctype}));\n"
        )
        self._temp.decls.append(f"{indent}{tmp}->tag = {tag};\n")
        self._temp.decls.append(f"{indent}{tmp}->data = NULL;\n")
        self._temp.frees.append(tmp)
        return tmp

    def _is_variant_construction(self, call: zast.Call) -> bool:
        """Check if a call is a variant construction (variant.subtype expr)."""
        # check type annotation for monomorphized variant types
        call_type = call.type
        if (
            call_type
            and call_type.typetype == ZTypeType.VARIANT
            and call_type.generic_origin
        ):
            return True
        if isinstance(call.callable, zast.DottedPath):
            if isinstance(call.callable.parent, zast.AtomId):
                if self._typetype_of(call.callable.parent.name) == ZTypeType.VARIANT:
                    return True
        if isinstance(call.callable, zast.AtomId):
            if self._typetype_of(call.callable.name) == ZTypeType.VARIANT:
                return True
        return False

    def _emit_variant_construction(self, call: zast.Call) -> str:
        """Emit C code for variant construction (stack-allocated, no malloc)."""
        indent = self._indent()
        tmp = self._temp_name("c")

        if isinstance(call.callable, zast.DottedPath) and isinstance(
            call.callable.parent, zast.AtomId
        ):
            subtype_name = call.callable.child.name
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
        elif isinstance(call.callable.parent, zast.AtomId):
            variant_name = call.callable.parent.name
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
                is_null = (
                    sub_ztype.name == "null" and sub_ztype.typetype == ZTypeType.RECORD
                )
        else:
            mainunit = self.program.units.get(self.program.mainunitname)
            variant_defn = mainunit.body.get(variant_name) if mainunit else None
            subtype_path = None
            if isinstance(variant_defn, zast.Variant):
                subtype_path = variant_defn.items.get(subtype_name)
            is_null = (
                subtype_path is not None
                and isinstance(subtype_path, zast.AtomId)
                and subtype_path.name == "null"
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
        self.needs_string = True
        self.needs_stdlib = True
        self.needs_stdio = True

        has_interp = any(isinstance(p, zast.Expression) for p in atom.stringparts)

        if not has_interp:
            literal = self._collect_string_literal(atom.stringparts)
            return self._static_string(literal)

        parts: List[str] = []
        for p in atom.stringparts:
            if isinstance(p, zast.Expression):
                val = self._emit_expression_value(p)
                val_type = self._get_expression_type(p)
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
                    parts.append(self._alloc_temp(f"zstr_from_i64((int64_t){val})"))
                elif val_type and val_type.name in ("f32", "f64"):
                    parts.append(self._alloc_temp(f"zstr_from_f64((double){val})"))
                elif val_type and val_type.name == "string":
                    # string variable reference — no temp needed
                    parts.append(val)
                elif val_type and _is_str_type(val_type):
                    # str valtype — convert to ZStr* for concatenation
                    str_type_name = val_type.name
                    parts.append(self._alloc_temp(f"z_{str_type_name}_string({val})"))
                else:
                    parts.append(self._alloc_temp(f"zstr_from_i64((int64_t){val})"))
            else:
                literal = self._escape_c_string(p.tokstr)
                if literal:
                    parts.append(self._static_string(literal))

        if not parts:
            return self._static_string("")
        if len(parts) == 1:
            return parts[0]
        result = parts[0]
        for p in parts[1:]:
            result = self._alloc_temp(f"zstr_cat({result}, {p})")
        return result

    def _collect_string_literal(self, parts: list) -> str:
        result: List[str] = []
        for p in parts:
            if not isinstance(p, zast.Expression):
                result.append(self._escape_c_string(p.tokstr))
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
        if isinstance(op, zast.Expression):
            return self._get_expression_type(op)
        return None

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
                    parts.append(self._emit_statement(clause.statement))
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
                cond_str = " && ".join(conds) if conds else "1"
                parts.append(f"{indent}{keyword} ({cond_str}) {{\n")
                self.indent_level += 1
                parts.append(self._emit_statement(clause.statement))
                self.indent_level -= 1

            if ifnode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                parts.append(self._emit_statement(ifnode.elseclause))
                self.indent_level -= 1

            parts.append(f"{indent}}}\n")
        elif not emitted_true_branch and ifnode.elseclause:
            # all clauses were constant-false, emit else branch in a scope
            parts.append(f"{indent}{{\n")
            self.indent_level += 1
            parts.append(self._emit_statement(ifnode.elseclause))
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        return "".join(parts)

    def _emit_branch_with_result(self, stmt: zast.Statement, result_var: str) -> str:
        """Emit a branch body, assigning the last expression's value to result_var."""
        parts: List[str] = []
        lines = stmt.statements
        for i, sline in enumerate(lines):
            is_last = i == len(lines) - 1
            inner = sline.statementline

            if is_last and isinstance(inner, zast.Expression):
                expr_inner = inner.expression
                # non-completing: emit normally (return/break/continue)
                if isinstance(expr_inner, zast.AtomId) and expr_inner.name in (
                    "break",
                    "continue",
                ):
                    parts.append(self._emit_statement_line(sline))
                elif (
                    isinstance(expr_inner, zast.Call)
                    and expr_inner.call_kind == zast.CallKind.RETURN
                ):
                    parts.append(self._emit_statement_line(sline))
                else:
                    # value-producing: assign to result_var
                    self._temp_stack.append(TempState())
                    val = self._emit_expression_value(inner)
                    indent = self._indent()
                    code = f"{indent}{result_var} = {val};\n"
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
                cond_str = " && ".join(conds) if conds else "1"
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
            if ctype == "ZStr*":
                self._temp.string_set.add(tmp)
                self.needs_string = True

        return tmp

    def _emit_for(self, fornode: zast.For) -> str:
        indent = self._indent()
        parts: List[str] = []

        init_vars: List[str] = []
        cond_exprs: List[str] = []
        # iterator bindings: (name, op, opt_ctype, elem_ctype, opt_name, callable_type, opt_type)
        iter_bindings: List[
            Tuple[str, zast.Operation, str, str, str, Optional[str], Optional[ZType]]
        ] = []
        # each bindings: (name, limit_expr, from_expr, elem_ctype) — optimized C for loop
        each_bindings: List[Tuple[str, str, str, str]] = []

        for name, cond_op in fornode.conditions.items():
            if name.startswith(" "):
                cond_exprs.append(self._emit_operation_value(cond_op))
            elif name in fornode.iterator_bindings:
                # check for .each on integer types (C for-loop optimization)
                is_each = False
                actual_op = cond_op
                while isinstance(actual_op, zast.Expression):
                    actual_op = actual_op.expression
                if isinstance(actual_op, (zast.DottedPath, zast.Call)):
                    each_path = None
                    from_val = "0"
                    if (
                        isinstance(actual_op, zast.DottedPath)
                        and actual_op.child.name == "each"
                    ):
                        each_path = actual_op
                    elif (
                        isinstance(actual_op, zast.Call)
                        and isinstance(actual_op.callable, zast.DottedPath)
                        and actual_op.callable.child.name == "each"
                    ):
                        each_path = actual_op.callable
                        for arg in actual_op.arguments:
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
            cond_str = " && ".join(cond_exprs) if cond_exprs else "1"
            parts.append(f"{indent}while ({cond_str}) {{\n")
            self.indent_level += 1
            inner = self._indent()
            for (
                iname,
                iop,
                opt_ctype,
                elem_ctype,
                opt_name,
                callable_type,
                opt_type,
            ) in iter_bindings:
                if callable_type:
                    obj_val = self._emit_operation_value(iop)
                    call_fn = _mangle_func(f"{callable_type}.call")
                    iter_val = f"{call_fn}({obj_val})"
                else:
                    iter_val = self._emit_operation_value(iop)
                tmp = f"__iter_{_mangle_var(iname)}"
                if opt_type and opt_type.is_nullable_ptr:
                    # nullable-ptr option: NULL = none
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(f"{inner}if ({tmp} == NULL) break;\n")
                    parts.append(f"{inner}{elem_ctype} {_mangle_var(iname)} = {tmp};\n")
                elif opt_type and opt_type.typetype == ZTypeType.VARIANT:
                    # optionval variant: check tag, extract data.some
                    none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(f"{inner}if ({tmp}.tag == {none_tag}) break;\n")
                    parts.append(
                        f"{inner}{elem_ctype} {_mangle_var(iname)} = {tmp}.data.some;\n"
                    )
                else:
                    # regular tagged union (legacy path)
                    none_tag = f"Z_{opt_name.upper()}_TAG_NONE"
                    parts.append(f"{inner}{opt_ctype} {tmp} = {iter_val};\n")
                    parts.append(
                        f"{inner}if ({tmp}->tag == {none_tag}) {{ free({tmp}); break; }}\n"
                    )
                    parts.append(
                        f"{inner}{elem_ctype} {_mangle_var(iname)} = *({elem_ctype}*){tmp}->data;\n"
                    )
                    parts.append(f"{inner}free({tmp});\n")
            if fornode.loop:
                parts.append(self._emit_for_body(fornode))
            if has_post:
                post_str = " && ".join(post_exprs)
                parts.append(f"{inner}if (!({post_str})) break;\n")
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
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
            cond_str = " && ".join(cond_exprs) if cond_exprs else "1"
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
        list_var = getattr(fornode, "_comprehension_list_var", None)
        if not list_var:
            return self._emit_statement(fornode.loop)
        list_name = fornode._comprehension_list_name  # type: ignore[attr-defined]
        parts: List[str] = []
        stmts = fornode.loop.statements
        for sl in stmts[:-1]:
            parts.append(self._emit_statement_line(sl))
        last = stmts[-1].statementline
        if isinstance(last, zast.Expression):
            val = self._emit_expression_value(last)
            indent = self._indent()
            parts.append(f"{indent}z_{list_name}_append({list_var}, {val});\n")
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
        fornode._comprehension_list_var = tmp  # type: ignore[attr-defined]
        fornode._comprehension_list_name = list_name  # type: ignore[attr-defined]
        self._temp.decls.append(self._emit_for(fornode))
        if tmp in self._temp.frees:
            self._temp.frees.remove(tmp)
        return tmp

    def _emit_do(self, donode: zast.Do) -> str:
        return self._emit_statement(donode.statement)

    def _emit_with(self, withnode: zast.With) -> str:
        indent = self._indent()
        parts: List[str] = []

        val = self._emit_expression_value(withnode.value)
        val_type = self._get_expression_type(withnode.value)
        ctype = "int64_t"
        if val_type:
            ctype = _ctype(val_type)

        is_string = ctype == "ZStr*"
        is_class = ctype.startswith("z_") and ctype.endswith("_t*")
        is_union = val_type and val_type.typetype == ZTypeType.UNION
        cname = _mangle_var(withnode.name)

        # if value is a reftype temp, the with var now owns it
        if (is_string or is_class) and val in self._temp.frees:
            self._temp.frees.remove(val)

        parts.append(f"{indent}{{\n")
        self.indent_level += 1
        inner_indent = self._indent()
        parts.append(f"{inner_indent}{ctype} {cname} = {val};\n")

        # doexpr may reference the with variable, so its temps must be
        # declared inside the block (not prepended to the outer statement)
        self._temp_stack.append(TempState())

        doexpr_code = self._emit_expression_stmt(withnode.doexpr)

        # emit doexpr temps inside the with block
        parts.append("".join(self._temp.decls))
        parts.append(doexpr_code)
        for t in self._temp.frees:
            if t in self._temp.string_set:
                parts.append(f"{inner_indent}zstr_free({t});\n")
            elif t in self._temp.class_set:
                parts.append(
                    f"{inner_indent}{self._emit_class_free(t, self._temp.class_set[t])}\n"
                )
            else:
                parts.append(f"{inner_indent}free({t});\n")

        self._temp_stack.pop()

        if is_union and val_type:
            parts.append(f"{inner_indent}z_{val_type.name}_destroy({cname});\n")
        elif is_string:
            parts.append(f"{inner_indent}zstr_free({cname});\n")
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

        subject = self._emit_operation_value(casenode.subject)

        # use if/else if chain (case values may not be compile-time constants in C)
        for i, clause in enumerate(casenode.clauses):
            match_val = self._emit_atomid_value(clause.match)
            keyword = "if" if i == 0 else "} else if"
            parts.append(f"{indent}{keyword} ({subject} == {match_val}) {{\n")
            self.indent_level += 1
            parts.append(self._emit_statement(clause.statement))
            self.indent_level -= 1

        if casenode.elseclause:
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            parts.append(self._emit_statement(casenode.elseclause))
            self.indent_level -= 1

        parts.append(f"{indent}}}\n")
        return "".join(parts)

    def _emit_union_case(self, casenode: zast.Case, union_type: ZType) -> str:
        # nullable-ptr option: if (ptr != NULL) / else
        if union_type.is_nullable_ptr:
            return self._emit_nullable_ptr_case(casenode, union_type)

        indent = self._indent()
        parts: List[str] = []
        union_name = union_type.name

        subject = self._emit_operation_value(casenode.subject)

        parts.append(f"{indent}switch ({subject}->tag) {{\n")
        for clause in casenode.clauses:
            sname = clause.match.name
            tag = f"Z_{union_name.upper()}_TAG_{sname.upper()}"
            parts.append(f"{indent}    case {tag}: {{\n")
            self.indent_level += 2
            parts.append(self._emit_statement(clause.statement))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")
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
            parts.append(self._emit_statement(some_clause.statement))
            self.indent_level -= 1
            parts.append(f"{indent}}} else {{\n")
            self.indent_level += 1
            parts.append(self._emit_statement(none_clause.statement))
            self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif some_clause:
            parts.append(f"{indent}if ({subject} != NULL) {{\n")
            self.indent_level += 1
            parts.append(self._emit_statement(some_clause.statement))
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                parts.append(self._emit_statement(casenode.elseclause))
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")
        elif none_clause:
            parts.append(f"{indent}if ({subject} == NULL) {{\n")
            self.indent_level += 1
            parts.append(self._emit_statement(none_clause.statement))
            self.indent_level -= 1
            if casenode.elseclause:
                parts.append(f"{indent}}} else {{\n")
                self.indent_level += 1
                parts.append(self._emit_statement(casenode.elseclause))
                self.indent_level -= 1
            parts.append(f"{indent}}}\n")

        return "".join(parts)

    def _emit_variant_case(self, casenode: zast.Case, variant_type: ZType) -> str:
        indent = self._indent()
        parts: List[str] = []
        variant_name = variant_type.name

        subject = self._emit_operation_value(casenode.subject)

        parts.append(f"{indent}switch ({subject}.tag) {{\n")
        for clause in casenode.clauses:
            sname = clause.match.name
            tag = f"Z_{variant_name.upper()}_TAG_{sname.upper()}"
            parts.append(f"{indent}    case {tag}: {{\n")
            self.indent_level += 2
            parts.append(self._emit_statement(clause.statement))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        if casenode.elseclause:
            parts.append(f"{indent}    default: {{\n")
            self.indent_level += 2
            parts.append(self._emit_statement(casenode.elseclause))
            parts.append(f"{self._indent()}break;\n")
            self.indent_level -= 2
            parts.append(f"{indent}    }}\n")

        parts.append(f"{indent}}}\n")
        return "".join(parts)


def emit(program: zast.Program) -> str:
    emitter = CEmitter(program)
    return emitter.emit()
