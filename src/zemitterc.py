"""
ZeroLang C code emitter

Walks a type-checked AST and emits C source code.
"""

from typing import Optional, List, Dict

import zast
from ztypechecker import ZType, ZTypeType, parse_number

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
    return "void"


def _mangle_func(name: str) -> str:
    """Mangle a zerolang function/global name for C."""
    if name == "main":
        return "z_main"
    return "z_" + name.replace(".", "_")


def _mangle_var(name: str) -> str:
    """Mangle a local variable name — only escape C reserved words."""
    if name in ("main", "break", "continue", "return", "switch", "case",
                "default", "if", "else", "for", "while", "do", "int",
                "float", "double", "char", "void", "struct", "union",
                "enum", "static", "const", "auto", "register", "extern",
                "volatile", "signed", "unsigned", "long", "short", "sizeof",
                "typedef", "goto", "abs", "exit"):
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

    def _indent(self) -> str:
        return "    " * self.indent_level

    def emit(self) -> str:
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return "/* empty program */\n"

        # first pass: collect all unit-level definition names
        for name, defn in mainunit.body.items():
            if isinstance(defn, zast.Function) and defn.body:
                self._func_names.add(name)
            elif isinstance(defn, zast.Record):
                self._record_names.add(name)
            elif isinstance(defn, zast.Data):
                self._data_names.add(name)
            elif isinstance(defn, zast.Expression) and isinstance(defn.expression, zast.Data):
                self._data_names.add(name)
            elif isinstance(defn, zast.AtomId) and _is_numeric_id(defn.name):
                self._const_names.add(name)

        for unitname, unit in self.program.units.items():
            if unitname in ("system", "core", "io", self.program.mainunitname):
                continue
            for name, defn in unit.body.items():
                if isinstance(defn, zast.Function) and defn.body:
                    self._func_names.add(f"{unitname}.{name}")

        # second pass: emit definitions
        for name, defn in mainunit.body.items():
            if isinstance(defn, zast.Record):
                self._emit_record(name, defn)
            elif isinstance(defn, zast.Function) and defn.body:
                self._emit_function(name, defn)
            elif isinstance(defn, zast.Data):
                self._emit_data(name, defn)
            elif isinstance(defn, zast.Expression) and isinstance(defn.expression, zast.Data):
                self._emit_data(name, defn.expression)
            elif isinstance(defn, zast.AtomId) and _is_numeric_id(defn.name):
                self._emit_constant(name, defn)

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
        if self.needs_stdio or self.needs_stdint or self.needs_stdlib or self.needs_string:
            parts.append("\n")

        if self.needs_string or self.needs_stdio:
            parts.append(
                "typedef struct {\n"
                "    int32_t len;\n"
                "    char data[];\n"
                "} ZStr;\n\n"
                "static ZStr* zstr_new(const char* s) {\n"
                "    int32_t len = (int32_t)strlen(s);\n"
                "    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + len + 1);\n"
                "    z->len = len;\n"
                "    memcpy(z->data, s, len + 1);\n"
                "    return z;\n"
                "}\n\n"
                "static ZStr* zstr_cat(ZStr* a, ZStr* b) {\n"
                "    int32_t len = a->len + b->len;\n"
                "    ZStr* z = (ZStr*)malloc(sizeof(ZStr) + len + 1);\n"
                "    z->len = len;\n"
                "    memcpy(z->data, a->data, a->len);\n"
                "    memcpy(z->data + a->len, b->data, b->len + 1);\n"
                "    return z;\n"
                "}\n\n"
                "static ZStr* zstr_from_i64(int64_t n) {\n"
                "    char buf[32];\n"
                "    snprintf(buf, sizeof(buf), \"%ld\", (long)n);\n"
                "    return zstr_new(buf);\n"
                "}\n\n"
                "static ZStr* zstr_from_f64(double n) {\n"
                "    char buf[64];\n"
                "    snprintf(buf, sizeof(buf), \"%g\", n);\n"
                "    return zstr_new(buf);\n"
                "}\n\n"
                "static void zstr_print(ZStr* s) {\n"
                "    printf(\"%.*s\\n\", s->len, s->data);\n"
                "}\n\n"
            )

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

        all_funcs = list(rec.functions.items()) + list(rec.as_functions.items())
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
                return f"z_{record_name}_t"
            if name == "type" and record_name:
                return f"z_{record_name}_t"
            if name in TYPEMAP:
                return TYPEMAP[name]
            if name == "string":
                return "ZStr*"
            # check if it's a record name defined in the main unit
            if name in self._record_names:
                return f"z_{name}_t"
        return "int64_t"

    def _resolve_return_ctype(self, func: zast.Function, record_name: str = "") -> str:
        if not func.returntype:
            return "void"
        if hasattr(func.returntype, "type") and func.returntype.type:
            return _ctype(func.returntype.type)
        if isinstance(func.returntype, zast.AtomId):
            name = func.returntype.name
            if name == "type" and record_name:
                return f"z_{record_name}_t"
            if name in TYPEMAP:
                return TYPEMAP[name]
            if name == "string":
                self.needs_string = True
                self.needs_stdlib = True
                return "ZStr*"
            if name in self._record_names:
                return f"z_{name}_t"
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

        lines: List[str] = []
        lines.append(f"{ret_ctype} {cname}({param_str}) {{\n")
        self.indent_level = 1
        if func.body:
            body_code = self._emit_statement(func.body)
            lines.append(body_code)
        lines.append("}\n\n")
        self.func_defs.append("".join(lines))

    def _emit_statement(self, stmt: zast.Statement) -> str:
        parts: List[str] = []
        for sline in stmt.statements:
            parts.append(self._emit_statement_line(sline))
        return "".join(parts)

    def _emit_statement_line(self, sline: zast.StatementLine) -> str:
        inner = sline.statementline
        if isinstance(inner, zast.Assignment):
            return self._emit_assignment(inner)
        if isinstance(inner, zast.Reassignment):
            return self._emit_reassignment(inner)
        if isinstance(inner, zast.Swap):
            return self._emit_swap(inner)
        if isinstance(inner, zast.Expression):
            return self._emit_expression_stmt(inner)
        return ""

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
        # check if value is a bare record name (zero-initialization)
        inner = assign.value.expression
        if isinstance(inner, zast.AtomId) and inner.name in self._record_names:
            ctype = f"z_{inner.name}_t"
            val = f"({ctype}){{0}}"
        return f"{indent}{ctype} {cname} = {val};\n"

    def _emit_reassignment(self, reassign: zast.Reassignment) -> str:
        indent = self._indent()
        lhs = self._emit_path_value(reassign.topath)
        rhs = self._emit_expression_value(reassign.value)
        return f"{indent}{lhs} = {rhs};\n"

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
            return f"{indent}zstr_print(zstr_new(\"\"));\n"

        if callable_name == "return":
            if call.arguments:
                val = self._emit_operation_value(call.arguments[0].valtype)
                return f"{indent}return {val};\n"
            return f"{indent}return;\n"

        if callable_name == "break":
            return f"{indent}break;\n"
        if callable_name == "continue":
            return f"{indent}continue;\n"

        # data.index call -> array access
        if self._is_data_index_call(call):
            data_name = call.callable.parent.name
            idx = self._emit_operation_value(call.arguments[0].valtype) if call.arguments else "0"
            return f"{indent}{_mangle_func(data_name)}[{idx}];\n"

        args = self._emit_call_args(call)
        return f"{indent}{_mangle_func(callable_name)}({args});\n"

    def _emit_call_args(self, call: zast.Call) -> str:
        parts: List[str] = []
        for arg in call.arguments:
            parts.append(self._emit_operation_value(arg.valtype))
        return ", ".join(parts)

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
        if self._is_data_index_call(call):
            data_name = call.callable.parent.name
            idx = self._emit_operation_value(call.arguments[0].valtype) if call.arguments else "0"
            return f"{_mangle_func(data_name)}[{idx}]"

        if call.callable.type and call.callable.type.typetype == ZTypeType.RECORD:
            rec_type = call.callable.type
            ctype = f"z_{rec_type.name}_t"
            fields: List[str] = []
            for arg in call.arguments:
                if arg.name:
                    val = self._emit_operation_value(arg.valtype)
                    fields.append(f".{arg.name} = {val}")
            if fields:
                return f"({ctype}){{{', '.join(fields)}}}"
            return f"({ctype}){{0}}"

        args = self._emit_call_args(call)
        return f"{_mangle_func(callable_name)}({args})"

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
        if name in self._func_names or name in self._data_names or name in self._const_names:
            return _mangle_func(name)
        if name in self._record_names:
            return f"(z_{name}_t){{0}}"
        return _mangle_var(name)

    def _emit_numeric_literal(self, name: str) -> str:
        typename, value, err = parse_number(name)
        if err:
            return "0"
        self.needs_stdint = True
        if typename.startswith("f"):
            return str(value)
        return str(int(value))

    def _emit_dotted_path_value(self, path: zast.DottedPath) -> str:
        child = path.child.name

        if isinstance(path.parent, zast.AtomId):
            pname = path.parent.name
            # unit.name reference
            if pname in self.program.units and pname not in ("system", "core", "io"):
                return _mangle_func(f"{pname}.{child}")
            # record_name.method — method call with no extra args (implicit this)
            if pname in self._record_names:
                return _mangle_func(f"{pname}.{child}")
            # data.index call
            if pname in self._data_names and child == "index":
                return _mangle_func(pname)

        # check if the dotted path resolves to a function (method call)
        if hasattr(path, "type") and path.type and path.type.typetype == ZTypeType.FUNCTION:
            parent = self._emit_path_value(path.parent)
            # determine the record type name from the function name
            func_name = path.type.name  # e.g. "point.distance"
            return f"{_mangle_func(func_name)}({parent})"

        parent = self._emit_path_value(path.parent)
        return f"{parent}.{child}"

    def _emit_string_value(self, atom: zast.AtomString) -> str:
        self.needs_string = True
        self.needs_stdlib = True
        self.needs_stdio = True

        has_interp = any(isinstance(p, zast.Expression) for p in atom.stringparts)

        if not has_interp:
            literal = self._collect_string_literal(atom.stringparts)
            return f'zstr_new("{literal}")'

        parts: List[str] = []
        for p in atom.stringparts:
            if isinstance(p, zast.Expression):
                val = self._emit_expression_value(p)
                val_type = self._get_expression_type(p)
                if val_type and val_type.name in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"):
                    parts.append(f"zstr_from_i64((int64_t){val})")
                elif val_type and val_type.name in ("f32", "f64"):
                    parts.append(f"zstr_from_f64((double){val})")
                elif val_type and val_type.name == "string":
                    parts.append(val)
                else:
                    parts.append(f"zstr_from_i64((int64_t){val})")
            else:
                literal = self._escape_c_string(p.tokstr)
                if literal:
                    parts.append(f'zstr_new("{literal}")')

        if not parts:
            return 'zstr_new("")'
        if len(parts) == 1:
            return parts[0]
        result = parts[0]
        for p in parts[1:]:
            result = f"zstr_cat({result}, {p})"
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

        parts.append(f"{indent}{{\n")
        self.indent_level += 1
        inner_indent = self._indent()
        parts.append(f"{inner_indent}{ctype} {_mangle_var(withnode.name)} = {val};\n")
        parts.append(self._emit_expression_stmt(withnode.doexpr))
        self.indent_level -= 1
        parts.append(f"{indent}}}\n")
        return "".join(parts)

    def _emit_case(self, casenode: zast.Case) -> str:
        indent = self._indent()
        parts: List[str] = []

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


def emit(program: zast.Program) -> str:
    emitter = CEmitter(program)
    return emitter.emit()
