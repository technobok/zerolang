"""
ZeroLang C code emitter

Walks a type-checked AST and emits C source code.
Includes ownership-based memory management for strings (ZStr*).
"""

from typing import Optional, List, Dict

import zast
from ztypechecker import ZType, ZTypeType, parse_number, ZParamOwnership

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
    "null": "void",
    "bool": "int",
    "never": "void",
}

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
    return "void"


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


class CEmitter:
    def __init__(self, program: zast.Program) -> None:
        self.program = program
        self.out: List[str] = []
        self.indent_level = 0
        self.needs_stdio = False
        self.needs_stdint = False
        self.needs_stdlib = False
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
        self._unit_names: set[str] = set()
        # field info per type name (for meta.create calls)
        self._type_field_ctypes: Dict[str, List[str]] = {}
        self._type_field_names: Dict[str, List[str]] = {}
        # temp variable infrastructure for string ownership
        self._temp_counter: int = 0
        self._temp_decls: List[str] = []
        self._temp_frees: List[str] = []
        self._temp_string_set: set[str] = set()  # temps that are ZStr*
        self._func_string_vars: List[str] = []
        self._func_class_vars: List[str] = []
        self._func_union_vars: List[str] = []
        self._union_var_types: Dict[str, str] = {}  # var_name -> union type name
        self._class_var_types: Dict[str, str] = {}  # var_name -> class type name
        self._temp_class_set: Dict[str, str] = {}  # temp_name -> class type name
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

    def _qualify(self, prefix: str, name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

    def _collect_unit_names(self, prefix: str, body: dict) -> None:
        """Recursively collect definition names from a unit body."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if isinstance(defn, zast.Unit):
                self._unit_names.add(qname)
                self._collect_unit_names(qname, defn.body)
            elif isinstance(defn, zast.Function) and defn.body:
                self._func_names.add(qname)
            elif isinstance(defn, zast.Record):
                self._record_names.add(qname)
            elif isinstance(defn, zast.Class):
                self._class_names.add(qname)
            elif isinstance(defn, zast.Union):
                self._union_names.add(qname)
            elif isinstance(defn, zast.Data):
                self._data_names.add(qname)
            elif isinstance(defn, zast.Expression) and isinstance(
                defn.expression, zast.Data
            ):
                self._data_names.add(qname)
            elif isinstance(defn, zast.AtomId) and _is_numeric_id(defn.name):
                self._const_names.add(qname)

    def _emit_unit_definitions(self, prefix: str, body: dict) -> None:
        """Recursively emit definitions from a unit body."""
        for name, defn in body.items():
            qname = self._qualify(prefix, name)
            if isinstance(defn, zast.Unit):
                self._emit_unit_definitions(qname, defn.body)
            elif isinstance(defn, zast.Record):
                self._emit_record(qname, defn)
            elif isinstance(defn, zast.Class):
                self._emit_class(qname, defn)
            elif isinstance(defn, zast.Union):
                self._emit_union(qname, defn)
            elif isinstance(defn, zast.Function) and defn.body:
                self._emit_function(qname, defn)
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

        for unitname, unit in self.program.units.items():
            if unitname in ("system", "core", "io", self.program.mainunitname):
                continue
            for name, defn in unit.body.items():
                if isinstance(defn, zast.Function) and defn.body:
                    self._func_names.add(f"{unitname}.{name}")

        # second pass: emit definitions (recursing into inline units)
        self._emit_unit_definitions("", mainunit.body)

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
        if self.needs_string:
            parts.append("#include <string.h>\n")
        if (
            self.needs_stdio
            or self.needs_stdint
            or self.needs_stdlib
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

        for sd in self.struct_defs:
            parts.append(sd)
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

    def _emit_record(self, name: str, rec: zast.Record) -> None:
        self.needs_stdint = True
        lines: List[str] = []
        lines.append("typedef struct {\n")
        for fname, fpath in rec.items.items():
            ftype = _ctype(fpath.type if hasattr(fpath, "type") else None)
            if ftype == "void":
                if isinstance(fpath, zast.AtomId):
                    ftype = TYPEMAP.get(fpath.name, "int64_t")
            lines.append(f"    {ftype} {fname};\n")
        lines.append(f"}} z_{name}_t;\n\n")
        self.struct_defs.append("".join(lines))

        # emit meta.create constructor
        self._emit_meta_create_record(name, rec)

        all_funcs = list(rec.functions.items()) + list(rec.as_functions.items())
        for mname, mfunc in all_funcs:
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)

    def _resolve_field_ctype(self, fpath: zast.Path) -> str:
        """Resolve the C type for a struct/class field."""
        ftype = _ctype(fpath.type if hasattr(fpath, "type") else None)
        if ftype == "void" and isinstance(fpath, zast.AtomId):
            fname = fpath.name
            if fname == "string":
                return "ZStr*"
            if fname in self._class_names:
                return f"z_{fname}_t*"
            if fname in self._union_names:
                return f"z_{fname}_t*"
            if fname in self._record_names:
                return f"z_{fname}_t"
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
        self._type_field_ctypes[name] = field_ctypes
        self._type_field_names[name] = field_names
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
        self._type_field_ctypes[name] = field_ctypes
        self._type_field_names[name] = field_names
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

        all_funcs = list(cls.functions.items()) + list(cls.as_functions.items())
        for mname, mfunc in all_funcs:
            if mfunc.body:
                self._emit_function(f"{name}.{mname}", mfunc, record_name=name)

    def _resolve_tag_values(self, union_defn: zast.Union) -> Optional[Dict[str, int]]:
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
        if isinstance(ppath, zast.AtomId):
            name = ppath.name
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
        saved_temp_class_set = self._temp_class_set
        saved_temp_counter = self._temp_counter
        saved_record_name = self._current_record_name
        self._func_string_vars = []
        self._func_class_vars = []
        self._func_union_vars = []
        self._union_var_types = {}
        self._class_var_types = {}
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

        # scope-exit cleanup for string/class/union vars (void functions / fall-through)
        if self._func_string_vars or self._func_class_vars or self._func_union_vars:
            indent = self._indent()
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
                result += (
                    f"{indent}{self._emit_class_free(t, self._temp_class_set[t])}\n"
                )
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
        val = self._emit_expression_value(assign.value)
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
            # distinguish union from class for proper destruction
            if assign.type and assign.type.typetype == ZTypeType.UNION:
                self._func_union_vars.append(cname)
                self._union_var_types[cname] = assign.type.name
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

    def _emit_call_stmt(self, call: zast.Call, indent: str) -> str:
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
        code = f"{indent}{_mangle_func(callable_name)}({args});\n"

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
                    result += (
                        f"{indent}{self._emit_class_free(t, self._temp_class_set[t])}\n"
                    )
                else:
                    result += f"{indent}free({t});\n"
            self._temp_frees.clear()

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

        # void return — free all func union/class/string vars
        result = ""
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
            return self._emit_operation_value(inner)
        if isinstance(inner, zast.With):
            return self._emit_expression_value(inner.doexpr)
        return "0"

    def _emit_call_value(self, call: zast.Call) -> str:
        callable_name = self._get_callable_name(call.callable)

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
        result = f"{_mangle_func(callable_name)}({args})"

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
            return str(value)
        return str(int(value))

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
            # data.index call
            if pname in self._data_names and child == "index":
                return _mangle_func(pname)

        # check if parent resolves to a nested inline unit path
        unit_path = self._extract_unit_path(path.parent)
        if unit_path is not None:
            return _mangle_func(f"{unit_path}.{child}")

        # check if the dotted path resolves to a function (method call)
        if (
            hasattr(path, "type")
            and path.type
            and path.type.typetype == ZTypeType.FUNCTION
        ):
            parent = self._emit_path_value(path.parent)
            # determine the record type name from the function name
            func_name = path.type.name  # e.g. "point.distance"
            return f"{_mangle_func(func_name)}({parent})"

        parent = self._emit_path_value(path.parent)
        # use -> for class instances (pointer types)
        if self._is_class_pointer_path(path.parent):
            return f"{parent}->{child}"
        return f"{parent}.{child}"

    def _is_class_pointer_path(self, path: zast.Path) -> bool:
        """Check if a path refers to a class/union pointer (for -> vs . dispatch)."""
        # type annotation from type checker
        parent_type = getattr(path, "type", None)
        if parent_type and parent_type.typetype in (ZTypeType.CLASS, ZTypeType.UNION):
            return True
        # local class/union variable
        if isinstance(path, zast.AtomId):
            cname = _mangle_var(path.name)
            if cname in self._func_class_vars or cname in self._func_union_vars:
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
            union_name = call.callable.parent.name
            subtype_name = call.callable.child.name
        else:
            # bare union name — shouldn't happen for construction but handle gracefully
            return "NULL"

        ctype = f"z_{union_name}_t"
        tag = f"Z_{union_name.upper()}_TAG_{subtype_name.upper()}"

        self._temp_decls.append(
            f"{indent}{ctype}* {tmp} = ({ctype}*)malloc(sizeof({ctype}));\n"
        )
        self._temp_decls.append(f"{indent}{tmp}->tag = {tag};\n")

        # determine subtype info from AST
        # look up the union definition to find the subtype's type
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

        if is_null or not call.arguments:
            self._temp_decls.append(f"{indent}{tmp}->data = NULL;\n")
        else:
            val = self._emit_operation_value(call.arguments[0].valtype)
            # determine if the subtype is a valtype that needs boxing
            subtype_ctype = self._get_subtype_ctype(subtype_path)
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
                # remove from temp frees since union takes ownership
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

        # check if subject is a union type
        subject_type = self._get_operation_type(casenode.subject)
        if subject_type and subject_type.typetype == ZTypeType.UNION:
            return self._emit_union_case(casenode, subject_type)

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


def emit(program: zast.Program) -> str:
    emitter = CEmitter(program)
    return emitter.emit()
