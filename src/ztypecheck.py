"""
ZeroLang type checking pass — single depth-first pass

Starts at main function, resolves names on demand, detects cycles.
"""

from typing import Optional, List, Tuple

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


# Sentinel for definitions currently being resolved
_RESOLVING = object()


class TypeChecker:
    """
    Single-pass demand-driven type checker.

    Starts from main, resolves names as encountered. Uses a resolving
    stack for cycle detection and `type` keyword resolution.
    """

    def __init__(self, program: zast.Program) -> None:
        self.program = program
        self.errors: List[zast.Error] = []
        self.symtab = SymbolTable()

        # well-known types (only null/never are standalone — others come from system.z)
        self.t_null = _make_type("null", ZTypeType.NULL)

        # resolving stack: list of (qualified_name, ZType) for cycle detection
        # and `type` keyword resolution
        self._resolving: List[Tuple[str, ZType]] = []

        # cache of resolved unit-level names: "unit.name" -> ZType
        self._resolved: dict[str, ZType] = {}

        # unit types (for dotted path resolution like mathutil.square)
        self.unit_types: dict[str, ZType] = {}
        for unitname in self.program.units:
            self.unit_types[unitname] = _make_type(unitname, ZTypeType.UNIT)

    def _error(self, msg: str, loc: Optional[Token] = None) -> None:
        self.errors.append(zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=loc))

    def check(self) -> List[zast.Error]:
        """Run the type checker starting from main."""
        mainunit = self.program.units.get(self.program.mainunitname)
        if not mainunit:
            return self.errors

        # type-check all definitions in the main unit that have bodies
        # (starting from main, but also covering other functions)
        main_func = mainunit.body.get("main")
        if main_func and isinstance(main_func, zast.Function):
            # resolve main first to trigger demand-driven resolution
            self._resolve_unit_name(self.program.mainunitname, "main")
            self._check_function_body("main", main_func)

        # also check other functions in the main unit that have bodies
        for name, defn in mainunit.body.items():
            if name == "main":
                continue
            if isinstance(defn, zast.Function) and defn.body:
                self._resolve_unit_name(self.program.mainunitname, name)
                self._check_function_body(name, defn)

        return self.errors

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
                ):
                    return rtype  # valid self-reference via `type`
                # circular alias
                chain = " -> ".join(rk for rk, _ in self._resolving[i:])
                self._error(f"Circular type alias: {chain} -> {key}")
                return None

        unit = self.program.units.get(unitname)
        if not unit:
            return None
        defn = unit.body.get(name)
        if defn is None:
            return None

        t = self._type_of_definition(unitname, name, defn)
        if t:
            self._resolved[key] = t
            # also populate unit_types for dotted path access
            self.unit_types[unitname].children[name] = t
        return t

    def _type_of_definition(
        self, unitname: str, name: str, defn: zast.TypeDefinition
    ) -> Optional[ZType]:
        """Type-check a definition, pushing/popping the resolving stack."""
        if isinstance(defn, zast.Function):
            return self._resolve_function_type(unitname, name, defn)
        if isinstance(defn, zast.Record):
            return self._resolve_record_type(unitname, name, defn)
        if isinstance(defn, zast.Enum):
            return self._resolve_enum_type(unitname, name, defn)
        if isinstance(defn, zast.Union):
            shell = _make_type(name, ZTypeType.UNION)
            self._resolved[f"{unitname}.{name}"] = shell
            return shell
        if isinstance(defn, zast.Unit):
            return self.unit_types.get(name)
        # alias: DottedPath or AtomId reference
        if isinstance(defn, zast.DottedPath):
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)  # placeholder for alias
            self._resolving.append((key, shell))
            t = self._resolve_dotted_path(defn)
            self._resolving.pop()
            return t
        if isinstance(defn, zast.AtomId):
            if _is_numeric_id(defn.name):
                return self._resolve_numeric(defn.name, loc=defn.start)
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)  # placeholder for alias
            self._resolving.append((key, shell))
            t = self._resolve_name(defn.name)
            self._resolving.pop()
            return t
        return None

    def _resolve_function_type(
        self, unitname: str, name: str, func: zast.Function
    ) -> ZType:
        key = f"{unitname}.{name}"
        ftype = _make_type(name, ZTypeType.FUNCTION)
        self._resolved[key] = ftype  # early register for self-reference
        self._resolving.append((key, ftype))

        if func.returntype:
            rt = self._resolve_typeref(func.returntype)
            if rt:
                ftype.children[":return"] = rt
        for pname, ppath in func.parameters.items():
            pt = self._resolve_typeref(ppath)
            if pt:
                ftype.children[pname] = pt

        self._resolving.pop()
        return ftype

    def _resolve_record_type(self, unitname: str, name: str, rec: zast.Record) -> ZType:
        key = f"{unitname}.{name}"
        rtype = _make_type(name, ZTypeType.RECORD)
        self._resolved[key] = rtype  # early register for self-reference
        self._resolving.append((key, rtype))

        for fname, fpath in rec.items.items():
            ft = self._resolve_typeref(fpath)
            if ft:
                rtype.children[fname] = ft
        for mname, mfunc in rec.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            rtype.children[mname] = mt
        # as_functions (methods defined in 'as' block)
        for mname, mfunc in rec.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            rtype.children[mname] = mt

        self._resolving.pop()
        return rtype

    def _resolve_enum_type(self, unitname: str, name: str, enum: zast.Enum) -> ZType:
        key = f"{unitname}.{name}"
        etype = _make_type(name, ZTypeType.ENUM)
        self._resolved[key] = etype
        self._resolving.append((key, etype))

        for vname in enum.items:
            etype.children[vname] = etype  # variants have the enum type

        for mname, mfunc in enum.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            etype.children[mname] = mt

        self._resolving.pop()
        return etype

    # ---- Name resolution (local -> unit body -> core -> system) ----

    def _resolve_name(self, name: str) -> Optional[ZType]:
        """Resolve a name using the scoping order: local, current unit, core, system."""
        # 1. local scope (symtab)
        t = self.symtab.lookup(name)
        if t:
            return t

        # 2. current unit body (main unit)
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit and name in mainunit.body:
            t = self._resolve_unit_name(self.program.mainunitname, name)
            if t:
                return t

        # 3. core unit body
        core = self.program.units.get("core")
        if core and name in core.body:
            t = self._resolve_unit_name("core", name)
            if t:
                return t

        # 4. system unit body
        system = self.program.units.get("system")
        if system and name in system.body:
            t = self._resolve_unit_name("system", name)
            if t:
                return t

        return None

    def _resolve_typeref(self, path: zast.Path) -> Optional[ZType]:
        """Resolve a type reference (used in parameter types, return types, fields)."""
        if isinstance(path, zast.AtomId):
            name = path.name
            if name == "type":
                return self._resolve_type_keyword()
            if name == "this":
                return self._resolve_this_keyword()
            return self._resolve_name(name)
        if isinstance(path, zast.DottedPath):
            return self._resolve_dotted_path(path)
        return None

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

    def _resolve_dotted_path(self, path: zast.DottedPath) -> Optional[ZType]:
        parent_type: Optional[ZType] = None
        if isinstance(path.parent, zast.AtomId):
            pname = path.parent.name
            # check if it's a unit name first
            if pname in self.program.units:
                # resolve the child from that unit on demand
                t = self._resolve_unit_name(pname, path.child.name)
                if t:
                    return t
                # might also be already in unit_types
                parent_type = self.unit_types.get(pname)
                if parent_type:
                    return parent_type.children.get(path.child.name)
                return None
            # otherwise resolve parent as a name
            parent_type = self._resolve_name(pname)
        elif isinstance(path.parent, zast.DottedPath):
            parent_type = self._resolve_dotted_path(path.parent)
        if not parent_type:
            return None
        # for records/enums, look up child in children
        child = parent_type.children.get(path.child.name)
        if child:
            return child
        return None

    def _resolve_numeric(
        self, name: str, loc: Optional[Token] = None
    ) -> Optional[ZType]:
        typename, _, err = parse_number(name)
        if err:
            self._error(f"Invalid numeric literal: {name}: {err}", loc=loc)
            return None
        return self._resolve_name(typename)

    # ---- Function body type checking ----

    def _check_function_body(self, name: str, func: zast.Function) -> None:
        if not func.body:
            return
        self.symtab.push(f"function:{name}")
        for pname, ppath in func.parameters.items():
            pt = self._resolve_typeref(ppath)
            if pt:
                self.symtab.define(pname, pt)
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
        elif isinstance(inner, zast.Swap):
            self._check_swap(inner)
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

    def _check_swap(self, swap: zast.Swap) -> None:
        lhs_t = self._check_path(swap.lhs)
        rhs_t = self._check_path(swap.rhs)
        if lhs_t and rhs_t and lhs_t.name != rhs_t.name:
            self._error(
                f"Cannot swap {lhs_t.name} with {rhs_t.name}",
                loc=swap.start,
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
        if isinstance(inner, zast.With):
            return self._check_with(inner)
        if isinstance(inner, zast.Case):
            return self._check_case(inner)
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
            self._check_string_interpolation(path)
            path.type = self._resolve_name("string")
            return path.type
        if isinstance(path, zast.AtomId):
            return self._check_atomid(path)
        if isinstance(path, zast.DottedPath):
            t = self._resolve_dotted_path(path)
            if t:
                path.type = t
            return t
        return None

    def _check_string_interpolation(self, atom: zast.AtomString) -> None:
        for part in atom.stringparts:
            if isinstance(part, zast.Expression):
                self._check_expression(part)

    def _check_atomid(self, atom: zast.AtomId) -> Optional[ZType]:
        name = atom.name
        if _is_numeric_id(name):
            t = self._resolve_numeric(name, loc=atom.start)
            if t:
                atom.type = t
            return t

        t = self._resolve_name(name)
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

        self._error(
            f"No operator '{op_name}' for types {lhs_type.name} and {rhs_type.name}",
            loc=binop.start,
        )
        return None

    def _check_if(self, ifnode: zast.If) -> Optional[ZType]:
        self.symtab.push("if")
        for clause in ifnode.clauses:
            for _, cond_op in clause.conditions.items():
                self._check_operation(cond_op)
            self._check_statement(clause.statement)
        if ifnode.elseclause:
            self._check_statement(ifnode.elseclause)
        self.symtab.pop()
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

    def _check_with(self, withnode: zast.With) -> Optional[ZType]:
        self.symtab.push("with")
        val_t = self._check_expression(withnode.value)
        if val_t:
            self.symtab.define(withnode.name, val_t)
        result = self._check_expression(withnode.doexpr)
        self.symtab.pop()
        return result

    def _check_case(self, casenode: zast.Case) -> Optional[ZType]:
        self.symtab.push("match")
        self._check_operation(casenode.subject)
        for clause in casenode.clauses:
            self._check_statement(clause.statement)
        if casenode.elseclause:
            self._check_statement(casenode.elseclause)
        self.symtab.pop()
        return self.t_null


def typecheck(program: zast.Program) -> List[zast.Error]:
    """Top-level entry point: type-check a parsed program."""
    tc = TypeChecker(program)
    return tc.check()
