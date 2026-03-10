"""
ZeroLang type checking pass

Walks the AST, resolves names, and assigns types to nodes.
"""

from typing import Optional, List

import zast
from zast import ERR
from zlexer import Token
from zenv import SymbolTable
from ztypechecker import ZType, ZTypeType, parse_number


def _is_numeric_id(name: str) -> bool:
    c0 = name[0]
    return c0.isdigit() or (c0 in ("+", "-") and len(name) > 1 and name[1].isdigit())


def _make_type(name: str, typetype: ZTypeType, parent: Optional[ZType] = None) -> ZType:
    return ZType(name=name, typetype=typetype, parent=parent)


class TypeChecker:
    """
    Type checker for a parsed zerolang Program.

    Walks the AST, resolves names, and assigns types to nodes.
    Collects errors rather than aborting on the first one.
    """

    def __init__(self, program: zast.Program) -> None:
        self.program = program
        self.errors: List[zast.Error] = []
        self.symtab = SymbolTable()

        # well-known types
        self.t_null = _make_type("null", ZTypeType.NULL)
        self.t_string = _make_type("string", ZTypeType.RECORD)
        self.t_bool = _make_type("bool", ZTypeType.RECORD)
        self.t_never = _make_type("never", ZTypeType.RECORD)

        # numeric types
        self.numeric_types: dict[str, ZType] = {}
        for n in (
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
            "f32",
            "f64",
        ):
            t = _make_type(n, ZTypeType.RECORD)
            self.numeric_types[n] = t

        # unit types (populated during check)
        self.unit_types: dict[str, ZType] = {}

    def _error(self, msg: str, loc: Optional[Token] = None) -> None:
        self.errors.append(zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=loc))

    def check(self) -> List[zast.Error]:
        """Run the type checker. Returns list of errors (empty = success)."""
        # pass 1a: register all units with their concrete definitions
        for unitname, unit in self.program.units.items():
            self._register_unit(unitname, unit)

        # pass 1b: resolve aliases (DottedPath/AtomId refs) now that all units exist
        # iterate until no new aliases are resolved (handles cross-unit deps)
        for _ in range(len(self.program.units)):
            changed = False
            for unitname, unit in self.program.units.items():
                if self._resolve_unit_aliases(unitname, unit):
                    changed = True
            if not changed:
                break

        # pass 2: build the core scope (core unit definitions become global names)
        self.symtab.push("global")
        core = self.program.units.get("core")
        if core:
            self._populate_scope_from_unit("core", core)

        # pass 3: type-check the main unit's function bodies
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit:
            self.symtab.push(self.program.mainunitname)
            self._populate_scope_from_unit(self.program.mainunitname, mainunit)

            # now type-check function bodies in the main unit
            for name, defn in mainunit.body.items():
                if isinstance(defn, zast.Function) and defn.body:
                    self._check_function(name, defn)

            self.symtab.pop()

        self.symtab.pop()  # global
        return self.errors

    # ---- Pass 1: register units ----

    def _register_unit(self, unitname: str, unit: zast.Unit) -> None:
        utype = _make_type(unitname, ZTypeType.UNIT)
        for name, defn in unit.body.items():
            t = self._type_of_definition(name, defn)
            if t:
                utype.children[name] = t
        self.unit_types[unitname] = utype

    def _resolve_unit_aliases(self, unitname: str, unit: zast.Unit) -> bool:
        utype = self.unit_types[unitname]
        changed = False
        for name, defn in unit.body.items():
            if name in utype.children:
                continue  # already resolved
            t = self._resolve_alias(defn)
            if t:
                utype.children[name] = t
                changed = True
        return changed

    def _resolve_alias(self, defn: zast.TypeDefinition) -> Optional[ZType]:
        if isinstance(defn, zast.DottedPath):
            return self._resolve_dotted_path(defn)
        if isinstance(defn, zast.AtomId):
            if _is_numeric_id(defn.name):
                return self._resolve_numeric(defn.name, loc=defn.start)
            return self._resolve_typeref(defn)
        return None

    def _populate_scope_from_unit(self, unitname: str, unit: zast.Unit) -> None:
        utype = self.unit_types.get(unitname)
        if not utype:
            return
        for name, t in utype.children.items():
            self.symtab.define(name, t)

    def _type_of_definition(
        self, name: str, defn: zast.TypeDefinition
    ) -> Optional[ZType]:
        if isinstance(defn, zast.Function):
            return self._type_of_function(name, defn)
        if isinstance(defn, zast.Record):
            return self._type_of_record(name, defn)
        if isinstance(defn, zast.Enum):
            return _make_type(name, ZTypeType.ENUM)
        if isinstance(defn, zast.Union):
            return _make_type(name, ZTypeType.UNION)
        if isinstance(defn, zast.Unit):
            return self.unit_types.get(name)
        return None

    def _type_of_function(self, name: str, func: zast.Function) -> ZType:
        ftype = _make_type(name, ZTypeType.FUNCTION)
        if func.returntype:
            rt = self._resolve_typeref(func.returntype)
            if rt:
                ftype.children[":return"] = rt
        for pname, ppath in func.parameters.items():
            pt = self._resolve_typeref(ppath)
            if pt:
                ftype.children[pname] = pt
        return ftype

    def _type_of_record(self, name: str, rec: zast.Record) -> ZType:
        rtype = _make_type(name, ZTypeType.RECORD)
        for fname, fpath in rec.items.items():
            ft = self._resolve_typeref(fpath)
            if ft:
                rtype.children[fname] = ft
        for mname, mfunc in rec.functions.items():
            mt = self._type_of_function(mname, mfunc)
            rtype.children[mname] = mt
        return rtype

    # ---- Type resolution ----

    def _resolve_typeref(self, path: zast.Path) -> Optional[ZType]:
        if isinstance(path, zast.AtomId):
            name = path.name
            if name == "string":
                return self.t_string
            if name == "bool":
                return self.t_bool
            if name == "never":
                return self.t_never
            if name in self.numeric_types:
                return self.numeric_types[name]
            t = self.symtab.lookup(name)
            if t:
                return t
            return None
        if isinstance(path, zast.DottedPath):
            return self._resolve_dotted_path(path)
        return None

    def _resolve_dotted_path(self, path: zast.DottedPath) -> Optional[ZType]:
        parent_type: Optional[ZType] = None
        if isinstance(path.parent, zast.AtomId):
            pname = path.parent.name
            parent_type = self.unit_types.get(pname)
            if not parent_type:
                parent_type = self.symtab.lookup(pname)
        elif isinstance(path.parent, zast.DottedPath):
            parent_type = self._resolve_dotted_path(path.parent)
        if not parent_type:
            return None
        return parent_type.children.get(path.child.name)

    def _resolve_definition_type(self, defn: zast.TypeDefinition) -> Optional[ZType]:
        if isinstance(defn, (zast.Function, zast.Record)):
            return None  # handled in pass 1
        if isinstance(defn, zast.DottedPath):
            return self._resolve_dotted_path(defn)
        if isinstance(defn, zast.AtomId):
            return self._resolve_typeref(defn)
        if isinstance(defn, zast.Unit):
            return None
        return None

    # ---- Pass 3: function body type checking ----

    def _check_function(self, name: str, func: zast.Function) -> None:
        self.symtab.push(f"function:{name}")
        for pname, ppath in func.parameters.items():
            pt = self._resolve_typeref(ppath)
            if pt:
                self.symtab.define(pname, pt)
        if func.body:
            self._check_statement(func.body)
        self.symtab.pop()

    def _check_statement(self, stmt: zast.Statement) -> None:
        for sline in stmt.statements:
            self._check_statement_line(sline)

    def _check_statement_line(self, sline: zast.StatementLine) -> None:
        inner = sline.statementline
        if isinstance(inner, zast.Assignment):
            self._check_assignment(inner)
        elif isinstance(inner, zast.Reassignment):
            self._check_reassignment(inner)
        elif isinstance(inner, zast.Expression):
            self._check_expression(inner)

    def _check_assignment(self, assign: zast.Assignment) -> None:
        t = self._check_expression(assign.value)
        if t:
            self.symtab.define(assign.name, t)
            assign.type = t

    def _check_reassignment(self, reassign: zast.Reassignment) -> None:
        existing = self._check_path(reassign.topath)
        new_t = self._check_expression(reassign.value)
        if existing and new_t and existing.name != new_t.name:
            self._error(
                f"Cannot assign {new_t.name} to variable of type {existing.name}",
                loc=reassign.start,
            )

    def _check_expression(self, expr: zast.Expression) -> Optional[ZType]:
        inner = expr.expression
        if isinstance(inner, zast.Call):
            return self._check_call(inner)
        if isinstance(inner, zast.If):
            return self._check_if(inner)
        if isinstance(inner, zast.For):
            return self._check_for(inner)
        if isinstance(inner, zast.Do):
            self._check_statement(inner.statement)
            return self.t_null
        if isinstance(inner, zast.Data):
            return None
        if isinstance(inner, zast.Operation):
            return self._check_operation(inner)
        return None

    def _check_operation(self, op: zast.Operation) -> Optional[ZType]:
        if isinstance(op, zast.BinOp):
            return self._check_binop(op)
        if isinstance(op, zast.Path):
            return self._check_path(op)
        return None

    def _check_path(self, path: zast.Path) -> Optional[ZType]:
        if isinstance(path, zast.Expression):
            return self._check_expression(path)
        if isinstance(path, zast.AtomString):
            path.type = self.t_string
            return self.t_string
        if isinstance(path, zast.AtomId):
            return self._check_atomid(path)
        if isinstance(path, zast.DottedPath):
            t = self._resolve_dotted_path(path)
            if t:
                path.type = t
            return t
        return None

    def _resolve_numeric(
        self, name: str, loc: Optional[Token] = None
    ) -> Optional[ZType]:
        typename, _, err = parse_number(name)
        if err:
            self._error(f"Invalid numeric literal: {name}: {err}", loc=loc)
            return None
        return self.numeric_types.get(typename)

    def _check_atomid(self, atom: zast.AtomId) -> Optional[ZType]:
        name = atom.name
        if _is_numeric_id(name):
            t = self._resolve_numeric(name, loc=atom.start)
            if t:
                atom.type = t
            return t

        t = self.symtab.lookup(name)
        if t:
            atom.type = t
            return t

        self._error(f"Undefined identifier: {name}", loc=atom.start)
        return None

    def _check_call(self, call: zast.Call) -> Optional[ZType]:
        callee_type = self._check_path(call.callable)
        if not callee_type:
            return None

        if callee_type.typetype != ZTypeType.FUNCTION:
            self._error(
                f"Cannot call non-function type: {callee_type.name}",
                loc=call.start,
            )
            return None

        # parameter types (skip :return and special entries)
        params = [
            (k, v) for k, v in callee_type.children.items() if not k.startswith(":")
        ]

        for i, arg in enumerate(call.arguments):
            arg_type = self._check_operation(arg.valtype)
            if arg_type and i < len(params):
                _, ptype = params[i]
                if arg_type is not ptype and arg_type.name != ptype.name:
                    self._error(
                        f"Argument type mismatch: expected {ptype.name}, "
                        f"got {arg_type.name}",
                        loc=arg.start,
                    )

        ret = callee_type.children.get(":return")
        call.type = ret if ret else self.t_null
        return call.type

    def _check_binop(self, binop: zast.BinOp) -> Optional[ZType]:
        lhs_type = self._check_operation(binop.lhs)
        rhs_type = self._check_path(binop.rhs)
        if not lhs_type or not rhs_type:
            return None

        # look up operator as method on lhs type
        op_name = binop.operator.name
        method = lhs_type.children.get(op_name)
        if method and method.typetype == ZTypeType.FUNCTION:
            ret = method.children.get(":return")
            if ret:
                binop.type = ret
                return ret

        # fallback: same type → result is same type
        if lhs_type is rhs_type or lhs_type.name == rhs_type.name:
            binop.type = lhs_type
            return lhs_type

        self._error(
            f"No operator '{op_name}' for types {lhs_type.name} and {rhs_type.name}",
            loc=binop.start,
        )
        return None

    def _check_if(self, ifnode: zast.If) -> Optional[ZType]:
        for clause in ifnode.clauses:
            for _, cond_op in clause.conditions.items():
                self._check_operation(cond_op)
            self._check_statement(clause.statement)
        if ifnode.elseclause:
            self._check_statement(ifnode.elseclause)
        return self.t_null

    def _check_for(self, fornode: zast.For) -> Optional[ZType]:
        self.symtab.push("for")
        for name, cond_op in fornode.conditions.items():
            t = self._check_operation(cond_op)
            if t and not name.startswith(" "):
                self.symtab.define(name, t)
        for postcond in fornode.postconditions:
            self._check_operation(postcond)
        if fornode.loop:
            self._check_statement(fornode.loop)
        self.symtab.pop()
        return self.t_null


def typecheck(program: zast.Program) -> List[zast.Error]:
    """Top-level entry point: type-check a parsed program."""
    tc = TypeChecker(program)
    return tc.check()
