"""
ZeroLang C code emitter

Walks a type-checked AST and emits C source code.
Includes ownership-based memory management for strings (ZStr*).
"""

from typing import Optional, List, Dict

import zast
from ztypechecker import ZType, ZTypeType, parse_number, ZParamOwnership, NUMERIC_RANGES

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


def _ctype(ztype: Optional[ZType]) -> str:
    if not ztype:
        return "void"
    name = ztype.name
    if name in TYPEMAP:
        return TYPEMAP[name]
    if name == "string":
        return "ZStr*"
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
    return "void"


def _ctype_func_inline(ztype: ZType) -> str:
    """Generate an inline C function pointer type for a FUNCTION ZType.
    Returns e.g. 'int64_t (*)(int64_t, int64_t)'.
    """
    ret = ztype.children.get(":return")
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


def _is_definition_name(name: str, emitter: "CEmitter") -> bool:
    """Check if a name refers to a unit-level definition."""
    return (
        name in emitter._func_names
        or name in emitter._spec_names
        or name in emitter._record_names
        or name in emitter._class_names
        or name in emitter._union_names
        or name in emitter._variant_names
        or name in emitter._data_names
        or name in emitter._const_names
        or name in emitter._unit_names
        or name in emitter._protocol_names
    )


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
        self.struct_defs: List[str] = []
        self.func_defs: List[str] = []
        self.data_defs: List[str] = []
        # track which names are functions/records/data (unit-level defs)
        self._func_names: set[str] = set()
        self._data_names: set[str] = set()
        self._const_names: set[str] = set()
        self._record_names: set[str] = set()
        self._class_names: set[str] = set()
        self._union_names: set[str] = set()
        self._variant_names: set[str] = set()
        self._unit_names: set[str] = set()
        self._spec_names: set[str] = set()
        self._protocol_names: set[str] = set()
        self._protocol_defs: dict[str, zast.Protocol] = {}  # name -> AST node
        # (impl_type, proto_name) -> label for owned protocol create
        self._proto_conformance: Dict[tuple, str] = {}
        # qualified names like "calculator.op" for func pointer fields in 'is' sections
        self._is_func_fields: set[str] = set()
        self.spec_typedefs: List[str] = []
        self.func_typedefs: List[str] = []  # typedefs for functions (after struct defs)
        # field info per type name (for meta.create calls)
        self._type_field_ctypes: Dict[str, List[str]] = {}
        self._type_field_names: Dict[str, List[str]] = {}
        self._type_field_defaults: Dict[str, Dict[str, str]] = {}
        # temp variable infrastructure for string ownership
        self._temp_counter: int = 0
        self._temp_decls: List[str] = []
        self._temp_frees: List[str] = []
        self._temp_string_set: set[str] = set()  # temps that are ZStr*
        self._func_string_vars: List[str] = []
        self._func_class_vars: List[str] = []
        self._func_union_vars: List[str] = []
        self._func_protocol_vars: List[str] = []
        self._union_var_types: Dict[str, str] = {}  # var_name -> union type name
        self._class_var_types: Dict[str, str] = {}  # var_name -> class type name
        self._protocol_var_types: Dict[str, str] = {}  # var_name -> protocol type name
        self._temp_class_set: Dict[str, str] = {}  # temp_name -> class type name
        self._in_named_assignment: bool = False  # set during _emit_assignment
        self._current_record_name: str = ""
        self._func_class_params: set[str] = set()
        # static string literal deduplication
        self._string_literals: Dict[str, str] = {}  # escaped C string → static var name
        self._string_literal_counter: int = 0

    def _indent(self) -> str:
        return "    " * self.indent_level

    def _alloc_temp(self, expr: str) -> str:
        """Allocate a temporary variable for a heap-allocated string expression."""
        self._temp_counter += 1
        name = f"_t{self._temp_counter}"
        indent = self._indent()
        self._temp_decls.append(f"{indent}ZStr* {name} = {expr};\n")
        self._temp_frees.append(name)
        self._temp_string_set.add(name)
        return name

    def _alloc_arg_temp(self, ctype: str, expr: str) -> str:
        """Allocate a temporary for a non-string argument (not freed)."""
        self._temp_counter += 1
        name = f"_a{self._temp_counter}"
        indent = self._indent()
        self._temp_decls.append(f"{indent}{ctype} {name} = {expr};\n")
        return name

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
            ftype = getattr(call.callable, "type", None)
            if ftype and ftype.typetype == ZTypeType.FUNCTION:
                func_name = ftype.name
                if func_name in self._is_func_fields:
                    return self._emit_dotted_path_value(call.callable)
        callable_name = self._get_callable_name(call.callable)
        return self._mangle_callable(callable_name)

    def _qualify(self, prefix: str, name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

    def _is_generic_template(self, defn: zast.TypeDefinition) -> bool:
        """Check if a definition is a generic template (has .generic in items)."""
        items = None
        if isinstance(defn, (zast.Record, zast.Union, zast.Variant, zast.Class)):
            items = defn.items
        elif isinstance(defn, zast.Function):
            items = defn.parameters
        elif isinstance(defn, zast.Protocol):
            items = defn.parameters
        if items is None:
            return False
        for fpath in items.values():
            if isinstance(fpath, zast.DottedPath) and isinstance(
                fpath.child, zast.AtomId
            ):
                if fpath.child.name == "generic":
                    return True
        return False

    def _collect_unit_names(self, prefix: str, body: dict) -> None:
        """Recursively collect definition names from a unit body."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if isinstance(defn, zast.Unit):
                self._unit_names.add(qname)
                self._collect_unit_names(qname, defn.body)
            elif isinstance(defn, zast.Function) and defn.body:
                if not self._is_generic_template(defn):
                    self._func_names.add(qname)
            elif isinstance(defn, zast.Function) and defn.body is None:
                self._spec_names.add(qname)
            elif isinstance(defn, zast.Record):
                if not self._is_generic_template(defn):
                    self._record_names.add(qname)
                    for mname in defn.functions:
                        self._is_func_fields.add(f"{qname}.{mname}")
            elif isinstance(defn, zast.Class):
                if not self._is_generic_template(defn):
                    self._class_names.add(qname)
                    for mname in defn.functions:
                        self._is_func_fields.add(f"{qname}.{mname}")
            elif isinstance(defn, zast.Union):
                if not self._is_generic_template(defn):
                    self._union_names.add(qname)
            elif isinstance(defn, zast.Variant):
                self._variant_names.add(qname)
            elif isinstance(defn, zast.Protocol):
                if not self._is_generic_template(defn):
                    self._protocol_names.add(qname)
                    self._protocol_defs[qname] = defn
            elif isinstance(defn, zast.Data):
                self._data_names.add(qname)
            elif isinstance(defn, zast.Expression) and isinstance(
                defn.expression, zast.Data
            ):
                self._data_names.add(qname)
            elif isinstance(defn, zast.AtomId) and _is_numeric_id(defn.name):
                self._const_names.add(qname)

    def _collect_proto_conformance(self, prefix: str, body: dict) -> None:
        """Build (impl_type, proto_name) -> label mapping for owned protocol create."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if isinstance(defn, zast.Unit):
                self._collect_proto_conformance(qname, defn.body)
            elif isinstance(defn, (zast.Record, zast.Class)):
                if not self._is_generic_template(defn):
                    for label, apath in defn.as_items.items():
                        proto_name = apath.name if isinstance(apath, zast.AtomId) else None
                        if proto_name and proto_name in self._protocol_names:
                            self._proto_conformance[(qname, proto_name)] = label

    def _emit_unit_definitions(self, prefix: str, body: dict) -> None:
        """Recursively emit definitions from a unit body."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if self._is_generic_template(defn):
                continue  # skip generic templates
            if isinstance(defn, zast.Unit):
                self._emit_unit_definitions(qname, defn.body)
            elif isinstance(defn, zast.Record):
                self._emit_record(qname, defn)
            elif isinstance(defn, zast.Class):
                self._emit_class(qname, defn)
            elif isinstance(defn, zast.Union):
                self._emit_union(qname, defn)
            elif isinstance(defn, zast.Variant):
                self._emit_variant(qname, defn)
            elif isinstance(defn, zast.Protocol):
                self._emit_protocol(qname, defn)
            elif isinstance(defn, zast.Function) and defn.body:
                self._emit_func_typedef(qname, defn)
                self._emit_function(qname, defn)
            elif isinstance(defn, zast.Function) and defn.body is None:
                self._emit_spec_typedef(qname, defn)
            elif isinstance(defn, zast.Data):
                self._emit_data(qname, defn)
            elif isinstance(defn, zast.Expression) and isinstance(
                defn.expression, zast.Data
            ):
                self._emit_data(qname, defn.expression)
            elif isinstance(defn, zast.AtomId) and _is_numeric_id(defn.name):
                self._emit_constant(qname, defn)

    def emit(self) -> str:
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return "/* empty program */\n"

        # first pass: collect all unit-level definition names (recursing into inline units)
        self._collect_unit_names("", mainunit.body)

        # build protocol conformance map for owned create
        self._collect_proto_conformance("", mainunit.body)

        for unitname, unit in self.program.units.items():
            if unitname in ("system", "core", "io", self.program.mainunitname):
                continue
            for name, defn in unit.body.items():
                if isinstance(defn, zast.Function) and defn.body:
                    self._func_names.add(f"{unitname}.{name}")

        # register monomorphized type names before emission
        for mono_type, _ in getattr(self.program, "mono_types", []):
            if mono_type.typetype == ZTypeType.UNION:
                self._union_names.add(mono_type.name)
            elif mono_type.typetype == ZTypeType.RECORD:
                self._record_names.add(mono_type.name)
            elif mono_type.typetype == ZTypeType.CLASS:
                self._class_names.add(mono_type.name)
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
                self._type_field_defaults[name] = {}
            elif mono_type.typetype == ZTypeType.PROTOCOL:
                self._protocol_names.add(mono_type.name)

        # second pass: emit definitions (recursing into inline units)
        self._emit_unit_definitions("", mainunit.body)

        # emit monomorphized types
        for mono_type, template_defn in getattr(self.program, "mono_types", []):
            self._emit_mono_type(mono_type, template_defn)

        for unitname, unit in self.program.units.items():
            if unitname in ("system", "core", "io", self.program.mainunitname):
                continue
            for name, defn in unit.body.items():
                if isinstance(defn, zast.Function) and defn.body:
                    self._emit_function(f"{unitname}.{name}", defn)

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
        for sd in self.struct_defs:
            parts.append(sd)
        for ft in self.func_typedefs:
            parts.append(ft)
        if self.func_typedefs:
            parts.append("\n")
        for fd in self.forward_decls:
            parts.append(fd)
        if self.forward_decls:
            parts.append("\n")
        for dd in self.data_defs:
            parts.append(dd)
        for fd in self.func_defs:
            parts.append(fd)

        parts.append("int main(int argc, char* argv[]) {\n")
        parts.append("    z_main();\n")
        parts.append("    return 0;\n")
        parts.append("}\n")

        return "".join(parts)

    def _emit_func_typedef(self, name: str, func: zast.Function) -> None:
        """Emit a C typedef for a function (placed after struct defs)."""
        self.needs_stdint = True
        ret_ctype = self._resolve_return_ctype(func)
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            if pname.startswith(":"):
                continue
            ptype_str = self._resolve_param_ctype(ppath)
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        cname = name.replace(".", "_")
        self.func_typedefs.append(
            f"typedef {ret_ctype} (*z_{cname}_ft)({param_str});\n"
        )

    def _emit_spec_typedef(self, name: str, func: zast.Function) -> None:
        """Emit a C typedef for a spec (function pointer type)."""
        self.needs_stdint = True
        ret_ctype = self._resolve_return_ctype(func)
        params: List[str] = []
        for pname, ppath in func.parameters.items():
            if pname.startswith(":"):
                continue
            ptype_str = self._resolve_param_ctype(ppath)
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
            ret_ctype = self._resolve_return_ctype(sfunc)
            params: List[str] = ["void*"]
            for pname, ppath in sfunc.parameters.items():
                if pname.startswith(":") or pname == "this":
                    continue
                params.append(self._resolve_param_ctype(ppath))
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
                ret_ctype = self._resolve_return_ctype(mfunc, record_name=impl_name)
                params: List[str] = []
                for pname, ppath in mfunc.parameters.items():
                    if pname.startswith(":"):
                        continue
                    ptype_str = self._resolve_param_ctype(ppath, record_name=impl_name)
                    params.append(f"{ptype_str} {_mangle_var(pname)}")
                param_str = ", ".join(params) if params else "void"
                method_cname = _mangle_func(f"{impl_name}.{sname}")
                lines.append(f"static {ret_ctype} {method_cname}({param_str});\n")
        lines.append("\n")

        # wrapper functions for each spec
        for sname, sfunc in proto.specs.items():
            ret_ctype = self._resolve_return_ctype(sfunc)
            # wrapper params: void* _data, then remaining non-this params
            wrapper_params: List[str] = ["void* _data"]
            call_args: List[str] = []
            for pname, ppath in sfunc.parameters.items():
                if pname.startswith(":") or pname == "this":
                    continue
                pctype = self._resolve_param_ctype(ppath)
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
                ftype_name = None
                ftype_type = None
                if hasattr(fpath, "type") and fpath.type:
                    ftype_name = fpath.type.name
                    ftype_type = fpath.type.typetype
                elif isinstance(fpath, zast.AtomId):
                    ftype_name = fpath.name
                if ftype_name == "string":
                    lines.append(f"    zstr_free(r->{fname});\n")
                elif ftype_type == ZTypeType.CLASS or (
                    ftype_name and ftype_name in self._class_names
                ):
                    lines.append(f"    z_{ftype_name}_destroy(r->{fname});\n")
                elif ftype_type == ZTypeType.UNION or (
                    ftype_name and ftype_name in self._union_names
                ):
                    lines.append(f"    z_{ftype_name}_destroy(r->{fname});\n")
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

    def _emit_record(self, name: str, rec: zast.Record) -> None:
        self.needs_stdint = True
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in rec.items.items():
            ftype = self._resolve_field_ctype(fpath)
            lines.append(f"    {ftype} {fname};\n")
        # emit function pointer fields from 'is' section
        for mname, mfunc in rec.functions.items():
            decl = self._func_pointer_field_decl(name, mname, mfunc)
            lines.append(f"    {decl};\n")
        lines.append(f"}} z_{name}_t;\n\n")
        self.struct_defs.append("".join(lines))

        # emit meta.create constructor
        self._emit_meta_create_record(name, rec)

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
            if proto_name and proto_name in self._protocol_names:
                self._emit_protocol_impl(name, label, proto_name, rec)

    def _func_pointer_field_decl(
        self, parent_name: str, mname: str, mfunc: zast.Function
    ) -> str:
        """Get the full C struct field declaration for a function pointer in an 'is' section.
        Returns e.g. 'int64_t (*callback)(int64_t, int64_t)'"""
        ret_ctype = self._resolve_return_ctype(mfunc, record_name=parent_name)
        params: List[str] = []
        for pname, ppath in mfunc.parameters.items():
            if pname.startswith(":"):
                continue
            ptype_str = self._resolve_param_ctype(ppath, record_name=parent_name)
            params.append(ptype_str)
        param_str = ", ".join(params) if params else "void"
        return f"{ret_ctype} (*{mname})({param_str})"

    def _resolve_field_ctype(self, fpath: zast.Path) -> str:
        """Resolve the C type for a struct/class field."""
        ftype = _ctype(fpath.type if hasattr(fpath, "type") else None)
        if ftype == "void" and isinstance(fpath, zast.DottedPath):
            if isinstance(fpath.parent, zast.AtomId) and _is_numeric_id(
                fpath.parent.name
            ):
                return TYPEMAP.get(fpath.child.name, "int64_t")
        if ftype == "void" and isinstance(fpath, zast.AtomId):
            fname = fpath.name
            if _is_numeric_id(fname):
                return "int64_t"
            if fname == "string":
                return "ZStr*"
            if fname in self._class_names:
                return f"z_{fname}_t*"
            if fname in self._union_names:
                return f"z_{fname}_t*"
            if fname in self._variant_names:
                return f"z_{fname}_t"
            if fname in self._record_names:
                return f"z_{fname}_t"
            if fname in self._spec_names:
                cname = fname.replace(".", "_")
                return f"z_{cname}_ft"
            if fname in self._protocol_names:
                return f"z_{fname}_t*"
            return TYPEMAP.get(fname, "int64_t")
        return ftype

    def _emit_meta_create_record(self, name: str, rec: zast.Record) -> None:
        """Emit a meta.create constructor function for a record type."""
        ctype = f"z_{name}_t"
        params: List[str] = []
        field_names: List[str] = []
        field_ctypes: List[str] = []
        for fname, fpath in rec.items.items():
            fct = self._resolve_field_ctype(fpath)
            params.append(f"{fct} {fname}")
            field_names.append(fname)
            field_ctypes.append(fct)
        # include function pointer fields from 'is' section
        for mname, mfunc in rec.functions.items():
            ret_ctype = self._resolve_return_ctype(mfunc, record_name=name)
            fp_params: List[str] = []
            for pname, ppath in mfunc.parameters.items():
                if pname.startswith(":"):
                    continue
                fp_params.append(self._resolve_param_ctype(ppath, record_name=name))
            fp_param_str = ", ".join(fp_params) if fp_params else "void"
            fp_ctype = f"{ret_ctype} (*)({fp_param_str})"
            params.append(f"{ret_ctype} (*{mname})({fp_param_str})")
            field_names.append(mname)
            field_ctypes.append(fp_ctype)
        self._type_field_ctypes[name] = field_ctypes
        self._type_field_names[name] = field_names
        # extract field defaults
        field_defaults: Dict[str, str] = {}
        for fname, fpath in rec.items.items():
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
            elif isinstance(fpath, zast.AtomId) and fpath.name in self._func_names:
                field_defaults[fname] = _mangle_func(fpath.name)
        for mname, mfunc in rec.functions.items():
            if mfunc.body is not None:
                field_defaults[mname] = _mangle_func(f"{name}.{mname}")
        self._type_field_defaults[name] = field_defaults
        param_str = ", ".join(params) if params else "void"
        arg_str = ", ".join(field_names) if field_names else ""
        lines: List[str] = []
        func_name = f"z_{name}_meta_create"
        lines.append(f"static {ctype} {func_name}({param_str});\n")
        lines.append(f"static {ctype} {func_name}({param_str}) {{\n")
        lines.append(f"    {ctype} _this = {{0}};\n")
        for fname in field_names:
            lines.append(f"    _this.{fname} = {fname};\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")
        # emit z_{name}_create forwarding function if user didn't define create
        has_user_create = "create" in rec.functions or "create" in rec.as_functions
        if not has_user_create:
            create_name = f"z_{name}_create"
            lines.append(f"static {ctype} {create_name}({param_str});\n")
            lines.append(f"static {ctype} {create_name}({param_str}) {{\n")
            lines.append(f"    return {func_name}({arg_str});\n")
            lines.append("}\n\n")
        self.struct_defs.append("".join(lines))

    def _emit_meta_create_class(
        self, name: str, cls: zast.Class, lines: List[str]
    ) -> None:
        """Emit a meta.create constructor function for a class type."""
        ctype = f"z_{name}_t"
        params: List[str] = []
        field_names: List[str] = []
        field_ctypes: List[str] = []
        for fname, fpath in cls.items.items():
            fct = self._resolve_field_ctype(fpath)
            params.append(f"{fct} {fname}")
            field_names.append(fname)
            field_ctypes.append(fct)
        # include function pointer fields from 'is' section
        for mname, mfunc in cls.functions.items():
            ret_ctype = self._resolve_return_ctype(mfunc, record_name=name)
            fp_params: List[str] = []
            for pname, ppath in mfunc.parameters.items():
                if pname.startswith(":"):
                    continue
                fp_params.append(self._resolve_param_ctype(ppath, record_name=name))
            fp_param_str = ", ".join(fp_params) if fp_params else "void"
            fp_ctype = f"{ret_ctype} (*)({fp_param_str})"
            params.append(f"{ret_ctype} (*{mname})({fp_param_str})")
            field_names.append(mname)
            field_ctypes.append(fp_ctype)
        self._type_field_ctypes[name] = field_ctypes
        self._type_field_names[name] = field_names
        # extract field defaults
        field_defaults: Dict[str, str] = {}
        for fname, fpath in cls.items.items():
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
            elif isinstance(fpath, zast.AtomId) and fpath.name in self._func_names:
                field_defaults[fname] = _mangle_func(fpath.name)
        for mname, mfunc in cls.functions.items():
            if mfunc.body is not None:
                field_defaults[mname] = _mangle_func(f"{name}.{mname}")
        self._type_field_defaults[name] = field_defaults
        param_str = ", ".join(params) if params else "void"
        arg_str = ", ".join(field_names) if field_names else ""
        func_name = f"z_{name}_meta_create"
        lines.append(f"static {ctype}* {func_name}({param_str});\n")
        lines.append(f"static {ctype}* {func_name}({param_str}) {{\n")
        lines.append(f"    {ctype}* _this = ({ctype}*)malloc(sizeof({ctype}));\n")
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        for fname in field_names:
            lines.append(f"    _this->{fname} = {fname};\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")
        # emit z_{name}_create forwarding function if user didn't define create
        has_user_create = "create" in cls.functions or "create" in cls.as_functions
        if not has_user_create:
            create_name = f"z_{name}_create"
            lines.append(f"static {ctype}* {create_name}({param_str});\n")
            lines.append(f"static {ctype}* {create_name}({param_str}) {{\n")
            lines.append(f"    return {func_name}({arg_str});\n")
            lines.append("}\n\n")

    def _emit_class(self, name: str, cls: zast.Class) -> None:
        self.needs_stdint = True
        self.needs_stdlib = True
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in cls.items.items():
            ftype = self._resolve_field_ctype(fpath)
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
            ftype_name = None
            ftype_type = None
            if hasattr(fpath, "type") and fpath.type:
                ftype_name = fpath.type.name
                ftype_type = fpath.type.typetype
            elif isinstance(fpath, zast.AtomId):
                ftype_name = fpath.name
            if ftype_name == "string":
                lines.append(f"    zstr_free(p->{fname});\n")
            elif ftype_type == ZTypeType.CLASS or (
                ftype_name and ftype_name in self._class_names
            ):
                lines.append(f"    z_{ftype_name}_destroy(p->{fname});\n")
            elif ftype_type == ZTypeType.UNION or (
                ftype_name and ftype_name in self._union_names
            ):
                lines.append(f"    z_{ftype_name}_destroy(p->{fname});\n")
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # emit meta.create constructor
        self._emit_meta_create_class(name, cls, lines)

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
            if proto_name and proto_name in self._protocol_names:
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
                # check subtype: class subtypes need their own destroyer
                stype_name = spath.name if isinstance(spath, zast.AtomId) else None
                if stype_name and stype_name == "string":
                    lines.append("            zstr_free((ZStr*)u->data);\n")
                elif stype_name and stype_name in self._class_names:
                    lines.append(
                        f"            z_{stype_name}_destroy((z_{stype_name}_t*)u->data);\n"
                    )
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
        if mono_type.typetype == ZTypeType.UNION:
            self._emit_mono_union(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.RECORD:
            self._emit_mono_record(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.CLASS:
            self._emit_mono_class(mono_type, template_defn)
        elif mono_type.typetype == ZTypeType.PROTOCOL:
            self._emit_mono_protocol(mono_type, template_defn)

    def _emit_mono_union(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized union type."""
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
                stype_name = stype.name
                if stype_name == "string":
                    lines.append("            zstr_free((ZStr*)u->data);\n")
                elif stype_name in self._class_names:
                    lines.append(
                        f"            z_{stype_name}_destroy((z_{stype_name}_t*)u->data);\n"
                    )
                elif stype_name in self._union_names:
                    lines.append(
                        f"            z_{stype_name}_destroy((z_{stype_name}_t*)u->data);\n"
                    )
                else:
                    lines.append("            free(u->data);\n")
                lines.append("            break;\n")
        lines.append("    }\n")
        lines.append("    free(u);\n")
        lines.append("}\n\n")

        self.struct_defs.append("".join(lines))

    def _emit_mono_record(
        self, mono_type: ZType, template_defn: zast.TypeDefinition
    ) -> None:
        """Emit a monomorphized record type."""
        self.needs_stdint = True
        name = mono_type.name
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, ftype in mono_type.children.items():
            if fname.startswith(":"):
                continue
            if ftype.typetype == ZTypeType.FUNCTION:
                continue
            ct = _ctype(ftype)
            lines.append(f"    {ct} {fname};\n")
        lines.append(f"}} z_{name}_t;\n\n")
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
            if ftype.name == "string":
                lines.append(f"    zstr_free(p->{fname});\n")
            elif ftype.name in self._class_names:
                lines.append(f"    z_{ftype.name}_destroy(p->{fname});\n")
            elif ftype.name in self._union_names:
                lines.append(f"    z_{ftype.name}_destroy(p->{fname});\n")
        lines.append("    free(p);\n")
        lines.append("}\n\n")

        # meta.create constructor
        self._emit_mono_class_create(name, mono_type, field_items, lines)

        self.struct_defs.append("".join(lines))

        # emit methods from template_defn with mangled names
        if isinstance(template_defn, zast.Class):
            for mname, mfunc in template_defn.as_functions.items():
                if mfunc.body:
                    self._emit_function(
                        f"{name}.{mname}", mfunc, record_name=name
                    )

    def _emit_mono_class_create(
        self,
        name: str,
        mono_type: ZType,
        field_items: list,
        lines: List[str],
    ) -> None:
        """Emit meta.create and create functions for a monomorphized class."""
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
        self._type_field_defaults[name] = {}
        param_str = ", ".join(params) if params else "void"
        arg_str = ", ".join(field_names)
        func_name = f"z_{name}_meta_create"
        lines.append(f"static {ctype}* {func_name}({param_str});\n")
        lines.append(f"static {ctype}* {func_name}({param_str}) {{\n")
        lines.append(
            f"    {ctype}* _this = ({ctype}*)malloc(sizeof({ctype}));\n"
        )
        lines.append(f"    *_this = ({ctype}){{0}};\n")
        for fname in field_names:
            lines.append(f"    _this->{fname} = {fname};\n")
        lines.append("    return _this;\n")
        lines.append("}\n\n")
        create_name = f"z_{name}_create"
        lines.append(f"static {ctype}* {create_name}({param_str});\n")
        lines.append(f"static {ctype}* {create_name}({param_str}) {{\n")
        lines.append(f"    return {func_name}({arg_str});\n")
        lines.append("}\n\n")

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
            ret_type = stype.children.get(":return")
            ret_ctype = _ctype(ret_type) if ret_type else "void"
            params = ["void*"]
            for pname, ptype in stype.children.items():
                if pname.startswith(":") or pname == "this":
                    continue
                params.append(_ctype(ptype))
            lines.append(
                f"    {ret_ctype} (*{sname})({', '.join(params)});\n"
            )
        lines.append(f"}} z_{name}_vtable_t;\n\n")

        # instance struct
        lines.append("typedef struct {\n")
        lines.append("    void* data;\n")
        lines.append(f"    z_{name}_vtable_t* vtable;\n")
        lines.append("    void (*destroy)(void*);\n")
        lines.append(f"}} z_{name}_t;\n\n")

        # destroy function
        lines.append(f"static void z_{name}_destroy(z_{name}_t* proto);\n")
        lines.append(
            f"static void z_{name}_destroy(z_{name}_t* proto) {{\n"
        )
        lines.append("    if (!proto) return;\n")
        lines.append(
            "    if (proto->destroy) proto->destroy(proto->data);\n"
        )
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
                    if sub_name in self._variant_names:
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

    def _resolve_param_ctype(self, ppath: zast.Path, record_name: str = "") -> str:
        if hasattr(ppath, "type") and ppath.type:
            return _ctype(ppath.type)
        if isinstance(ppath, zast.DottedPath):
            if isinstance(ppath.parent, zast.AtomId) and _is_numeric_id(
                ppath.parent.name
            ):
                return TYPEMAP.get(ppath.child.name, "int64_t")
        if isinstance(ppath, zast.AtomId):
            name = ppath.name
            if _is_numeric_id(name):
                return "int64_t"
            if name == "this" and record_name:
                if record_name in self._class_names:
                    return f"z_{record_name}_t*"
                return f"z_{record_name}_t"
            if name == "type" and record_name:
                if record_name in self._class_names:
                    return f"z_{record_name}_t*"
                return f"z_{record_name}_t"
            if name in TYPEMAP:
                return TYPEMAP[name]
            if name == "string":
                return "ZStr*"
            # check if it's a record name defined in the main unit
            if name in self._record_names:
                return f"z_{name}_t"
            if name in self._class_names:
                return f"z_{name}_t*"
            if name in self._union_names:
                return f"z_{name}_t*"
            if name in self._variant_names:
                return f"z_{name}_t"
            if name in self._spec_names:
                cname = name.replace(".", "_")
                return f"z_{cname}_ft"
            if name in self._func_names:
                cname = name.replace(".", "_")
                return f"z_{cname}_ft"
            if name in self._protocol_names:
                return f"z_{name}_t*"
        return "int64_t"

    def _resolve_return_ctype(self, func: zast.Function, record_name: str = "") -> str:
        if not func.returntype:
            return "void"
        if hasattr(func.returntype, "type") and func.returntype.type:
            return _ctype(func.returntype.type)
        if isinstance(func.returntype, zast.AtomId):
            name = func.returntype.name
            if name in ("type", "this") and record_name:
                if record_name in self._class_names:
                    return f"z_{record_name}_t*"
                return f"z_{record_name}_t"
            if name in TYPEMAP:
                return TYPEMAP[name]
            if name == "string":
                self.needs_string = True
                self.needs_stdlib = True
                return "ZStr*"
            if name in self._record_names:
                return f"z_{name}_t"
            if name in self._class_names:
                self.needs_stdlib = True
                return f"z_{name}_t*"
            if name in self._union_names:
                self.needs_stdlib = True
                return f"z_{name}_t*"
            if name in self._variant_names:
                return f"z_{name}_t"
            if name in self._spec_names:
                cname = name.replace(".", "_")
                return f"z_{cname}_ft"
            if name in self._func_names:
                cname = name.replace(".", "_")
                return f"z_{cname}_ft"
            if name in self._protocol_names:
                return f"z_{name}_t*"
        return "void"

    def _emit_function(
        self, name: str, func: zast.Function, record_name: str = ""
    ) -> None:
        self.needs_stdint = True
        cname = _mangle_func(name)

        ret_ctype = self._resolve_return_ctype(func, record_name)

        params: List[str] = []
        for pname, ppath in func.parameters.items():
            # skip hidden :this parameter (unnamed first param of methods)
            if pname.startswith(":"):
                continue
            ptype_str = self._resolve_param_ctype(ppath, record_name)
            params.append(f"{ptype_str} {_mangle_var(pname)}")

        param_str = ", ".join(params) if params else "void"

        self.forward_decls.append(f"{ret_ctype} {cname}({param_str});\n")

        # save/reset per-function state
        saved_string_vars = self._func_string_vars
        saved_class_vars = self._func_class_vars
        saved_union_vars = self._func_union_vars
        saved_union_var_types = self._union_var_types
        saved_class_var_types = self._class_var_types
        saved_protocol_vars = self._func_protocol_vars
        saved_protocol_var_types = self._protocol_var_types
        saved_temp_class_set = self._temp_class_set
        saved_temp_counter = self._temp_counter
        saved_record_name = self._current_record_name
        self._func_string_vars = []
        self._func_class_vars = []
        self._func_union_vars = []
        self._func_protocol_vars = []
        self._union_var_types = {}
        self._class_var_types = {}
        self._protocol_var_types = {}
        self._temp_class_set = {}
        self._temp_counter = 0
        self._current_record_name = record_name
        saved_class_params = self._func_class_params
        self._func_class_params = set()
        # track parameters that are class pointers
        if record_name in self._class_names:
            for pname, ppath in func.parameters.items():
                if pname.startswith(":"):
                    continue
                ptype_str = self._resolve_param_ctype(ppath, record_name)
                if ptype_str.endswith("*") and ptype_str.startswith("z_"):
                    self._func_class_params.add(_mangle_var(pname))

        lines: List[str] = []
        lines.append(f"{ret_ctype} {cname}({param_str}) {{\n")
        self.indent_level = 1
        if func.body:
            body_code = self._emit_statement(func.body)
            lines.append(body_code)

        # scope-exit cleanup for string/class/union/protocol vars (void functions / fall-through)
        if (
            self._func_string_vars
            or self._func_class_vars
            or self._func_union_vars
            or self._func_protocol_vars
        ):
            indent = self._indent()
            for sv in reversed(self._func_protocol_vars):
                ptype_name = self._protocol_var_types.get(sv)
                if ptype_name:
                    lines.append(f"{indent}z_{ptype_name}_destroy({sv});\n")
                else:
                    lines.append(f"{indent}free({sv});\n")
            for sv in reversed(self._func_union_vars):
                utype_name = self._union_var_type_name(sv)
                if utype_name:
                    lines.append(f"{indent}z_{utype_name}_destroy({sv});\n")
                else:
                    lines.append(f"{indent}if ({sv}) free({sv});\n")
            for sv in reversed(self._func_class_vars):
                lines.append(
                    f"{indent}{self._emit_class_free(sv, self._class_var_type_name(sv))}\n"
                )
            for sv in reversed(self._func_string_vars):
                lines.append(f"{indent}zstr_free({sv});\n")

        lines.append("}\n\n")
        self.func_defs.append("".join(lines))

        # restore
        self._func_string_vars = saved_string_vars
        self._func_class_vars = saved_class_vars
        self._func_union_vars = saved_union_vars
        self._union_var_types = saved_union_var_types
        self._class_var_types = saved_class_var_types
        self._func_protocol_vars = saved_protocol_vars
        self._protocol_var_types = saved_protocol_var_types
        self._temp_class_set = saved_temp_class_set
        self._temp_counter = saved_temp_counter
        self._current_record_name = saved_record_name
        self._func_class_params = saved_class_params

    def _emit_statement(self, stmt: zast.Statement) -> str:
        parts: List[str] = []
        for sline in stmt.statements:
            parts.append(self._emit_statement_line(sline))
        return "".join(parts)

    def _emit_statement_line(self, sline: zast.StatementLine) -> str:
        # save temp state for this statement
        saved_decls = self._temp_decls
        saved_frees = self._temp_frees
        saved_string_set = self._temp_string_set
        saved_class_set = self._temp_class_set
        self._temp_decls = []
        self._temp_frees = []
        self._temp_string_set = set()
        self._temp_class_set = {}

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
        result = "".join(self._temp_decls) + code
        indent = self._indent()
        for t in self._temp_frees:
            if t in self._temp_string_set:
                result += f"{indent}zstr_free({t});\n"
            elif t in self._temp_class_set:
                tname = self._temp_class_set[t]
                if tname.startswith(":proto:"):
                    proto_name = tname[7:]  # strip ":proto:" prefix
                    result += f"{indent}z_{proto_name}_destroy({t});\n"
                else:
                    result += f"{indent}{self._emit_class_free(t, tname)}\n"
            else:
                result += f"{indent}free({t});\n"

        # restore temp state
        self._temp_decls = saved_decls
        self._temp_frees = saved_frees
        self._temp_string_set = saved_string_set
        self._temp_class_set = saved_class_set
        return result

    def _emit_assignment(self, assign: zast.Assignment) -> str:
        indent = self._indent()
        ctype = "int64_t"
        if assign.type:
            ctype = _ctype(assign.type)
        cname = _mangle_var(assign.name)
        self._in_named_assignment = True
        val = self._emit_expression_value(assign.value)
        self._in_named_assignment = False
        if ctype == "ZStr*":
            self.needs_string = True
            self.needs_stdlib = True
            # the variable now owns the value — remove from temp frees
            if val in self._temp_frees:
                self._temp_frees.remove(val)
            self._func_string_vars.append(cname)
        elif ctype.startswith("z_") and ctype.endswith("_t*"):
            self.needs_stdlib = True
            # class/union pointer — the variable now owns it
            if val in self._temp_frees:
                self._temp_frees.remove(val)
            # distinguish union/class/protocol for proper destruction
            if assign.type and assign.type.typetype == ZTypeType.UNION:
                self._func_union_vars.append(cname)
                self._union_var_types[cname] = assign.type.name
            elif assign.type and assign.type.typetype == ZTypeType.PROTOCOL:
                self._func_protocol_vars.append(cname)
                self._protocol_var_types[cname] = assign.type.name
            else:
                self._func_class_vars.append(cname)
                if assign.type:
                    self._class_var_types[cname] = assign.type.name
        # check if value is a bare record name (zero-initialization)
        inner = assign.value.expression
        if isinstance(inner, zast.AtomId) and inner.name in self._record_names:
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
        lhs_type = getattr(reassign.topath, "type", None)
        if lhs_type and lhs_type.name == "string":
            result += f"{indent}zstr_free({lhs});\n"
            # the variable now owns the new value — remove from temp frees
            if rhs in self._temp_frees:
                self._temp_frees.remove(rhs)
        elif lhs_type and lhs_type.typetype == ZTypeType.CLASS:
            result += f"{indent}z_{lhs_type.name}_destroy({lhs});\n"
            if rhs in self._temp_frees:
                self._temp_frees.remove(rhs)
        elif lhs_type and lhs_type.typetype == ZTypeType.UNION:
            result += f"{indent}z_{lhs_type.name}_destroy({lhs});\n"
            if rhs in self._temp_frees:
                self._temp_frees.remove(rhs)
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
            var_type = getattr(inner, "type", None)
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
                if pname in self._data_names and child == "index":
                    return True
        return False

    def _is_protocol_create(self, call: zast.Call) -> bool:
        """Check if call is protocol.create from: expr."""
        if not isinstance(call.callable, zast.DottedPath):
            return False
        if call.callable.child.name != "create":
            return False
        parent_type = getattr(call.callable.parent, "type", None)
        return parent_type is not None and parent_type.typetype == ZTypeType.PROTOCOL

    def _emit_protocol_create_call(self, call: zast.Call) -> str:
        """Emit owned protocol create: protocol.create from: expr."""
        proto_type = call.callable.parent.type
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
        arg_type = getattr(from_arg.valtype, "type", None)
        if not arg_type:
            # try parent for dotted paths like f.take
            if isinstance(from_arg.valtype, zast.DottedPath):
                arg_type = getattr(from_arg.valtype.parent, "type", None)
        impl_name = arg_type.name if arg_type else ""

        # look up label
        label = self._proto_conformance.get((impl_name, proto_name), "")
        owned_create = f"z_{impl_name}_{label}_create_owned"

        # allocate temp and track as protocol var
        self._temp_counter += 1
        tmp = f"_c{self._temp_counter}"
        indent = self._indent()
        self._temp_decls.append(
            f"{indent}z_{proto_name}_t* {tmp} = {owned_create}({arg_val});\n"
        )
        self._temp_frees.append(tmp)
        self._temp_class_set[tmp] = f":proto:{proto_name}"

        # handle .take nullification for class (reftype) arguments only
        if arg_type and arg_type.typetype == ZTypeType.CLASS:
            take_var = self._get_take_var(from_arg.valtype)
            if take_var:
                self._temp_decls.append(f"{indent}{take_var} = NULL;\n")

        return tmp

    def _emit_protocol_dispatch(self, call: zast.Call) -> Optional[str]:
        """If call is a protocol method dispatch, return the C expression. Otherwise None."""
        if not isinstance(call.callable, zast.DottedPath):
            return None
        parent_type = getattr(call.callable.parent, "type", None)
        if not parent_type or parent_type.typetype != ZTypeType.PROTOCOL:
            return None
        parent_val = self._emit_path_value(call.callable.parent)
        method = call.callable.child.name
        args = [f"{parent_val}->data"]
        for arg in call.arguments:
            args.append(self._emit_operation_value(arg.valtype))
        return f"{parent_val}->vtable->{method}({', '.join(args)})"

    def _emit_call_stmt(self, call: zast.Call, indent: str) -> str:
        # protocol.create from: expr (owned protocol creation)
        if self._is_protocol_create(call):
            val = self._emit_protocol_create_call(call)
            return f"{indent}{val};\n"

        # protocol method dispatch
        proto_expr = self._emit_protocol_dispatch(call)
        if proto_expr is not None:
            return f"{indent}{proto_expr};\n"

        callable_name = self._get_callable_name(call.callable)

        if callable_name == "print":
            self.needs_stdio = True
            self.needs_string = True
            self.needs_stdlib = True
            if call.arguments:
                arg = self._emit_operation_value(call.arguments[0].valtype)
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

        args = self._emit_call_args(call)
        cname = self._emit_callable_expr(call)
        code = f"{indent}{cname}({args});\n"

        # if call takes a .take argument, nullify it after the call
        for arg in call.arguments:
            take_var = self._get_take_var(arg.valtype)
            if take_var:
                code += f"{indent}{take_var} = NULL;\n"

        # implicit take: nullify args passed to .take parameters
        ftype = getattr(call.callable, "type", None)
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
                and first_arg.name in self._class_names
                and len(call.arguments) > 1
            ):
                # emit as meta.create call
                self.needs_stdlib = True
                args_str, take_vars = self._build_meta_create_args(
                    first_arg.name, call.arguments, skip_first=1
                )
                result_expr = f"z_{first_arg.name}_create({args_str})"
                ctype = f"z_{first_arg.name}_t"
                self._temp_counter += 1
                tmp = f"_c{self._temp_counter}"
                self._temp_decls.append(f"{indent}{ctype}* {tmp} = {result_expr};\n")
                for fname, tv in take_vars.items():
                    if tv:
                        self._temp_decls.append(f"{indent}{tv} = NULL;\n")
                val = tmp
            else:
                val = self._emit_operation_value(call.arguments[0].valtype)
            result = ""

            # remove return value from temp frees (caller owns it)
            if val in self._temp_frees:
                self._temp_frees.remove(val)

            # free remaining temps (intermediates) before return
            for t in self._temp_frees:
                if t in self._temp_string_set:
                    result += f"{indent}zstr_free({t});\n"
                elif t in self._temp_class_set:
                    tname = self._temp_class_set[t]
                    if tname.startswith(":proto:"):
                        proto_name = tname[7:]
                        result += f"{indent}z_{proto_name}_destroy({t});\n"
                    else:
                        result += f"{indent}{self._emit_class_free(t, tname)}\n"
                else:
                    result += f"{indent}free({t});\n"
            self._temp_frees.clear()

            # free func protocol vars (except the return value)
            for sv in reversed(self._func_protocol_vars):
                if sv != val:
                    ptype_name = self._protocol_var_types.get(sv)
                    if ptype_name:
                        result += f"{indent}z_{ptype_name}_destroy({sv});\n"
                    else:
                        result += f"{indent}free({sv});\n"
            # free func union vars (except the return value)
            for sv in reversed(self._func_union_vars):
                if sv != val:
                    utype_name = self._union_var_type_name(sv)
                    if utype_name:
                        result += f"{indent}z_{utype_name}_destroy({sv});\n"
                    else:
                        result += f"{indent}if ({sv}) free({sv});\n"
            # free func class vars (except the return value)
            for sv in reversed(self._func_class_vars):
                if sv != val:
                    result += f"{indent}{self._emit_class_free(sv, self._class_var_type_name(sv))}\n"
            # free func string vars (except the return value)
            for sv in reversed(self._func_string_vars):
                if sv != val:
                    result += f"{indent}zstr_free({sv});\n"

            result += f"{indent}return {val};\n"
            return result

        # void return — free all func protocol/union/class/string vars
        result = ""
        for sv in reversed(self._func_protocol_vars):
            ptype_name = self._protocol_var_types.get(sv)
            if ptype_name:
                result += f"{indent}z_{ptype_name}_destroy({sv});\n"
            else:
                result += f"{indent}free({sv});\n"
        for sv in reversed(self._func_union_vars):
            utype_name = self._union_var_type_name(sv)
            if utype_name:
                result += f"{indent}z_{utype_name}_destroy({sv});\n"
            else:
                result += f"{indent}if ({sv}) free({sv});\n"
        for sv in reversed(self._func_class_vars):
            result += (
                f"{indent}{self._emit_class_free(sv, self._class_var_type_name(sv))}\n"
            )
        for sv in reversed(self._func_string_vars):
            result += f"{indent}zstr_free({sv});\n"
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
                and name not in self._func_names
                and name not in self._data_names
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
        if not hasattr(call.callable, "type") or not call.callable.type:
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
        ftype = call.callable.type if hasattr(call.callable, "type") else None
        if ftype and ftype.param_defaults:
            params = [
                (k, v) for k, v in ftype.children.items() if not k.startswith(":")
            ]
            for i in range(len(call.arguments), len(params)):
                pname, _ = params[i]
                if pname in ftype.param_defaults:
                    default = ftype.param_defaults[pname]
                    if default in self._func_names:
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
            if isinstance(inner, zast.AtomId) and inner.name in self._func_names:
                ftype = inner.type if hasattr(inner, "type") else None
                if ftype and ftype.param_defaults:
                    cname = _mangle_func(inner.name)
                    defaults: List[str] = []
                    for pname, _ in ftype.children.items():
                        if pname.startswith(":"):
                            continue
                        if pname in ftype.param_defaults:
                            d = ftype.param_defaults[pname]
                            if d in self._func_names:
                                d = _mangle_func(d)
                            defaults.append(d)
                    return f"{cname}({', '.join(defaults)})"
            return self._emit_operation_value(inner)
        if isinstance(inner, zast.With):
            return self._emit_expression_value(inner.doexpr)
        return "0"

    def _emit_call_value(self, call: zast.Call) -> str:
        # protocol.create from: expr (owned protocol creation)
        if self._is_protocol_create(call):
            return self._emit_protocol_create_call(call)

        # protocol method dispatch
        proto_expr = self._emit_protocol_dispatch(call)
        if proto_expr is not None:
            return proto_expr

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
                    self._temp_decls.append(f"{indent}{tv} = NULL;\n")
            return result

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
            self._temp_counter += 1
            tmp = f"_c{self._temp_counter}"
            indent = self._indent()
            self._temp_decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
            # handle .take nullification
            for fname, tv in take_vars.items():
                if tv:
                    self._temp_decls.append(f"{indent}{tv} = NULL;\n")
            self._temp_frees.append(tmp)
            self._temp_class_set[tmp] = cls_type.name
            return tmp

        args = self._emit_call_args(call)
        cname = self._emit_callable_expr(call)
        result = f"{cname}({args})"

        # if call returns a reftype, wrap in temp for cleanup
        if hasattr(call, "type") and call.type:
            if call.type.name == "string":
                return self._alloc_temp(result)
            if call.type.typetype == ZTypeType.CLASS:
                ctype = f"z_{call.type.name}_t"
                self._temp_counter += 1
                tmp = f"_c{self._temp_counter}"
                indent = self._indent()
                self._temp_decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
                self._temp_frees.append(tmp)
                self._temp_class_set[tmp] = call.type.name
                return tmp
            if call.type.typetype == ZTypeType.UNION:
                ctype = f"z_{call.type.name}_t"
                self._temp_counter += 1
                tmp = f"_c{self._temp_counter}"
                indent = self._indent()
                self._temp_decls.append(f"{indent}{ctype}* {tmp} = {result};\n")
                self._temp_frees.append(tmp)
                return tmp

        return result

    def _emit_operation_value(self, op: zast.Operation) -> str:
        if isinstance(op, zast.BinOp):
            return self._emit_binop_value(op)
        if isinstance(op, zast.Path):
            return self._emit_path_value(op)
        return "0"

    def _emit_binop_value(self, binop: zast.BinOp) -> str:
        lhs = self._emit_operation_value(binop.lhs)
        rhs = self._emit_path_value(binop.rhs)
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
        if (
            name in self._func_names
            or name in self._data_names
            or name in self._const_names
        ):
            return _mangle_func(name)
        if name in self._record_names:
            zero_args = self._zero_args_for_ctypes(name)
            return f"z_{name}_create({zero_args})"
        if name in self._class_names:
            self.needs_stdlib = True
            ctype = f"z_{name}_t"
            zero_args = self._zero_args_for_ctypes(name)
            self._temp_counter += 1
            tmp = f"_c{self._temp_counter}"
            indent = self._indent()
            self._temp_decls.append(
                f"{indent}{ctype}* {tmp} = z_{name}_create({zero_args});\n"
            )
            self._temp_frees.append(tmp)
            self._temp_class_set[tmp] = name
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
            if path.name in self._unit_names:
                return path.name
            return None
        if isinstance(path, zast.DottedPath):
            parent_path = self._extract_unit_path(path.parent)
            if parent_path is not None:
                qname = f"{parent_path}.{path.child.name}"
                if qname in self._unit_names:
                    return qname
        return None

    def _emit_dotted_path_value(self, path: zast.DottedPath) -> str:
        child = path.child.name

        # .take emits just the variable value (nullification handled at call site)
        if child == "take":
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
            if pname in self._unit_names:
                qname = f"{pname}.{child}"
                # check if the child is itself a unit (nested)
                if qname in self._unit_names:
                    # will be resolved by further dotted path traversal
                    return _mangle_func(qname)
                return _mangle_func(qname)
            # record_name.method or class_name.method — method call with no extra args
            if pname in self._record_names or pname in self._class_names:
                return _mangle_func(f"{pname}.{child}")
            # union_name.subtype — emit null subtype construction
            if pname in self._union_names:
                return self._emit_union_null_construction(pname, child)
            # variant_name.subtype — emit null subtype construction
            if pname in self._variant_names:
                return self._emit_variant_null_construction(pname, child)
            # data.index call
            if pname in self._data_names and child == "index":
                return _mangle_func(pname)

        # check if parent resolves to a nested inline unit path
        unit_path = self._extract_unit_path(path.parent)
        if unit_path is not None:
            return _mangle_func(f"{unit_path}.{child}")

        # check if the dotted path resolves to a function (method call or field access)
        if (
            hasattr(path, "type")
            and path.type
            and path.type.typetype == ZTypeType.FUNCTION
        ):
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
        if (
            hasattr(path, "type")
            and path.type
            and path.type.typetype == ZTypeType.PROTOCOL
        ):
            parent_type = getattr(path.parent, "type", None)
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
                self._temp_counter += 1
                tmp = f"_p{self._temp_counter}"
                indent = self._indent()
                proto_ctype = f"z_{path.type.name}_t"
                if self._in_named_assignment:
                    # named var: heap-allocate via create function
                    self._temp_decls.append(
                        f"{indent}{proto_ctype}* {tmp} = {create_name}({arg});\n"
                    )
                    self._temp_frees.append(tmp)
                    self._temp_class_set[tmp] = f":proto:{path.type.name}"
                else:
                    # temp: stack-allocate (no malloc/free needed)
                    stk = f"_ps{self._temp_counter}"
                    vtable_name = f"z_{parent_type.name}_{child}_vtable"
                    self._temp_decls.append(
                        f"{indent}{proto_ctype} {stk};\n"
                        f"{indent}{stk}.data = {arg};\n"
                        f"{indent}{stk}.vtable = &{vtable_name};\n"
                        f"{indent}{stk}.destroy = NULL;\n"
                        f"{indent}{proto_ctype}* {tmp} = &{stk};\n"
                    )
                return tmp

        # runtime numeric cast: x.u32 where x is a numeric variable
        if child in NUMERIC_CAST_TYPES:
            parent_type = getattr(path.parent, "type", None)
            if parent_type and parent_type.name in TYPEMAP:
                parent_val = self._emit_path_value(path.parent)
                return self._emit_numeric_cast(parent_val, parent_type.name, child)

        parent = self._emit_path_value(path.parent)
        # use -> for class instances (pointer types)
        if self._is_class_pointer_path(path.parent):
            return f"{parent}->{child}"
        # variant payload access: v.subname → v.data.subname
        parent_type = getattr(path.parent, "type", None)
        if parent_type and parent_type.typetype == ZTypeType.VARIANT:
            # check if child is a subtype name (not a method)
            child_type = parent_type.children.get(child)
            if child_type and child_type.typetype != ZTypeType.FUNCTION:
                return f"{parent}.data.{child}"
        return f"{parent}.{child}"

    def _is_class_pointer_path(self, path: zast.Path) -> bool:
        """Check if a path refers to a class/union/protocol pointer (for -> vs . dispatch)."""
        # type annotation from type checker
        parent_type = getattr(path, "type", None)
        if parent_type and parent_type.typetype in (
            ZTypeType.CLASS,
            ZTypeType.UNION,
            ZTypeType.PROTOCOL,
        ):
            return True
        # local class/union variable
        if isinstance(path, zast.AtomId):
            cname = _mangle_var(path.name)
            if (
                cname in self._func_class_vars
                or cname in self._func_union_vars
                or cname in self._func_protocol_vars
            ):
                return True
            # class method parameter (this/type resolves to class pointer)
            if self._current_record_name in self._class_names:
                if cname in self._func_class_params:
                    return True
        return False

    def _union_var_type_name(self, var_name: str) -> Optional[str]:
        """Get the union type name for a union variable."""
        return self._union_var_types.get(var_name)

    def _class_var_type_name(self, var_name: str) -> Optional[str]:
        """Get the class type name for a class variable."""
        return self._class_var_types.get(var_name)

    def _emit_class_free(self, var: str, type_name: Optional[str]) -> str:
        """Emit the right destroy call for a class variable."""
        if type_name:
            return f"z_{type_name}_destroy({var});"
        return f"if ({var}) free({var});"

    def _is_union_construction(self, call: zast.Call) -> bool:
        """Check if a call is a union construction (union.subtype or bare union name)."""
        # check type annotation for monomorphized union types
        call_type = getattr(call, "type", None)
        if (
            call_type
            and call_type.typetype == ZTypeType.UNION
            and call_type.generic_origin
        ):
            return True
        if isinstance(call.callable, zast.DottedPath):
            if isinstance(call.callable.parent, zast.AtomId):
                if call.callable.parent.name in self._union_names:
                    return True
        if isinstance(call.callable, zast.AtomId):
            if call.callable.name in self._union_names:
                return True
        return False

    def _emit_union_construction(self, call: zast.Call) -> str:
        """Emit C code for union construction."""
        self.needs_stdlib = True
        indent = self._indent()
        self._temp_counter += 1
        tmp = f"_c{self._temp_counter}"

        if isinstance(call.callable, zast.DottedPath) and isinstance(
            call.callable.parent, zast.AtomId
        ):
            subtype_name = call.callable.child.name
        else:
            # bare union name — shouldn't happen for construction but handle gracefully
            return "NULL"

        # check for monomorphized union type (from type annotation)
        call_type = getattr(call, "type", None)
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

        self._temp_decls.append(
            f"{indent}{ctype}* {tmp} = ({ctype}*)malloc(sizeof({ctype}));\n"
        )
        self._temp_decls.append(f"{indent}{tmp}->tag = {tag};\n")

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

        if is_null or not call.arguments:
            self._temp_decls.append(f"{indent}{tmp}->data = NULL;\n")
        else:
            # for monomorphized null subtype with explicit type arg, skip the arg
            # (it's a type name, not a value)
            if call_type and call_type.generic_origin and is_null:
                self._temp_decls.append(f"{indent}{tmp}->data = NULL;\n")
            else:
                val = self._emit_operation_value(call.arguments[0].valtype)
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
                    if val in self._temp_frees:
                        self._temp_frees.remove(val)
                    self._temp_decls.append(f"{indent}{tmp}->data = {val};\n")
                else:
                    # valtype: box it (malloc + copy)
                    box_ctype = subtype_ctype or "int64_t"
                    self._temp_counter += 1
                    box_tmp = f"_box{self._temp_counter}"
                    self._temp_decls.append(
                        f"{indent}{box_ctype}* {box_tmp} = ({box_ctype}*)malloc(sizeof({box_ctype}));\n"
                    )
                    self._temp_decls.append(f"{indent}*{box_tmp} = {val};\n")
                    self._temp_decls.append(f"{indent}{tmp}->data = {box_tmp};\n")

        self._temp_frees.append(tmp)
        return tmp

    def _get_subtype_ctype(self, subtype_path: Optional[zast.Path]) -> Optional[str]:
        """Get the C type for a union subtype path."""
        if not subtype_path:
            return None
        # use type annotation from type checker if available
        if hasattr(subtype_path, "type") and subtype_path.type:
            return _ctype(subtype_path.type)
        if isinstance(subtype_path, zast.AtomId):
            name = subtype_path.name
            if name == "null":
                return None
            if name in TYPEMAP:
                return TYPEMAP[name]
            if name == "string":
                return "ZStr*"
            if name in self._record_names:
                return f"z_{name}_t"
            if name in self._class_names:
                return f"z_{name}_t*"
            if name in self._union_names:
                return f"z_{name}_t*"
            if name in self._variant_names:
                return f"z_{name}_t"
        # DottedPath: resolve via last component name
        if isinstance(subtype_path, zast.DottedPath):
            return self._get_subtype_ctype(subtype_path.child)
        return None

    def _emit_union_null_construction(self, union_name: str, subtype_name: str) -> str:
        """Emit construction for a null-subtype union (no data)."""
        self.needs_stdlib = True
        indent = self._indent()
        self._temp_counter += 1
        tmp = f"_c{self._temp_counter}"
        ctype = f"z_{union_name}_t"
        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"
        self._temp_decls.append(
            f"{indent}{ctype}* {tmp} = ({ctype}*)malloc(sizeof({ctype}));\n"
        )
        self._temp_decls.append(f"{indent}{tmp}->tag = {tag};\n")
        self._temp_decls.append(f"{indent}{tmp}->data = NULL;\n")
        self._temp_frees.append(tmp)
        return tmp

    def _is_variant_construction(self, call: zast.Call) -> bool:
        """Check if a call is a variant construction (variant.subtype expr)."""
        if isinstance(call.callable, zast.DottedPath):
            if isinstance(call.callable.parent, zast.AtomId):
                if call.callable.parent.name in self._variant_names:
                    return True
        if isinstance(call.callable, zast.AtomId):
            if call.callable.name in self._variant_names:
                return True
        return False

    def _emit_variant_construction(self, call: zast.Call) -> str:
        """Emit C code for variant construction (stack-allocated, no malloc)."""
        indent = self._indent()
        self._temp_counter += 1
        tmp = f"_c{self._temp_counter}"

        if isinstance(call.callable, zast.DottedPath) and isinstance(
            call.callable.parent, zast.AtomId
        ):
            variant_name = call.callable.parent.name
            subtype_name = call.callable.child.name
        else:
            return "(z_unknown_t){0}"

        ctype = f"z_{variant_name}_t"
        tag = f"Z_{variant_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp_decls.append(f"{indent}{ctype} {tmp};\n")
        self._temp_decls.append(f"{indent}{tmp}.tag = {tag};\n")

        # determine if this is a null subtype
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

        if not is_null and call.arguments:
            val = self._emit_operation_value(call.arguments[0].valtype)
            self._temp_decls.append(f"{indent}{tmp}.data.{subtype_name} = {val};\n")

        # no temp_frees — value type, no cleanup needed
        return tmp

    def _emit_variant_null_construction(
        self, variant_name: str, subtype_name: str
    ) -> str:
        """Emit construction for a null-subtype variant (tag only, no data)."""
        indent = self._indent()
        self._temp_counter += 1
        tmp = f"_c{self._temp_counter}"
        ctype = f"z_{variant_name}_t"
        tag = f"Z_{variant_name.upper()}_TAG_{subtype_name.upper()}"
        self._temp_decls.append(f"{indent}{ctype} {tmp};\n")
        self._temp_decls.append(f"{indent}{tmp}.tag = {tag};\n")
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
        if hasattr(expr, "type") and expr.type:
            return expr.type
        inner = expr.expression
        if hasattr(inner, "type") and inner.type:
            return inner.type
        return None

    def _get_operation_type(self, op: zast.Operation) -> Optional[ZType]:
        if hasattr(op, "type") and op.type:
            return op.type
        # look inside Expression wrapper
        if isinstance(op, zast.Expression):
            return self._get_expression_type(op)
        return None

    def _emit_if(self, ifnode: zast.If) -> str:
        indent = self._indent()
        parts: List[str] = []

        for i, clause in enumerate(ifnode.clauses):
            keyword = "if" if i == 0 else "} else if"
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
        return "".join(parts)

    def _emit_for(self, fornode: zast.For) -> str:
        indent = self._indent()
        parts: List[str] = []

        init_vars: List[str] = []
        cond_exprs: List[str] = []
        for name, cond_op in fornode.conditions.items():
            if name.startswith(" "):
                cond_exprs.append(self._emit_operation_value(cond_op))
            else:
                val = self._emit_operation_value(cond_op)
                ctype = "int64_t"
                t = self._get_operation_type(cond_op)
                if t:
                    ctype = _ctype(t)
                init_vars.append(f"{indent}{ctype} {_mangle_var(name)} = {val};\n")

        for iv in init_vars:
            parts.append(iv)

        cond_str = " && ".join(cond_exprs) if cond_exprs else "1"
        parts.append(f"{indent}while ({cond_str}) {{\n")

        if fornode.loop:
            self.indent_level += 1
            parts.append(self._emit_statement(fornode.loop))
            self.indent_level -= 1

        parts.append(f"{indent}}}\n")
        return "".join(parts)

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
        if (is_string or is_class) and val in self._temp_frees:
            self._temp_frees.remove(val)

        parts.append(f"{indent}{{\n")
        self.indent_level += 1
        inner_indent = self._indent()
        parts.append(f"{inner_indent}{ctype} {cname} = {val};\n")

        # doexpr may reference the with variable, so its temps must be
        # declared inside the block (not prepended to the outer statement)
        saved_decls = self._temp_decls
        saved_frees = self._temp_frees
        saved_string_set = self._temp_string_set
        saved_class_set = self._temp_class_set
        self._temp_decls = []
        self._temp_frees = []
        self._temp_string_set = set()
        self._temp_class_set = {}

        doexpr_code = self._emit_expression_stmt(withnode.doexpr)

        # emit doexpr temps inside the with block
        parts.append("".join(self._temp_decls))
        parts.append(doexpr_code)
        for t in self._temp_frees:
            if t in self._temp_string_set:
                parts.append(f"{inner_indent}zstr_free({t});\n")
            elif t in self._temp_class_set:
                parts.append(
                    f"{inner_indent}{self._emit_class_free(t, self._temp_class_set[t])}\n"
                )
            else:
                parts.append(f"{inner_indent}free({t});\n")

        self._temp_decls = saved_decls
        self._temp_frees = saved_frees
        self._temp_string_set = saved_string_set
        self._temp_class_set = saved_class_set

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
