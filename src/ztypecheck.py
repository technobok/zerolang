"""
ZeroLang type checking pass — single depth-first pass

Starts at main function, resolves names on demand, detects cycles.
Includes ownership checking (Phase 4c).
"""

from typing import Optional, List, Tuple

import zast
from zast import ERR, clone_function
from zlexer import Token
from zenv import SymbolTable
import zasthash
from ztypes import (
    ZType,
    ZTypeType,
    ZParamOwnership,
    ZOwnership,
    ZNaming,
    ZVariable,
    ZLockState,
    NUMERIC_RANGES,
    parse_number,
)


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
        if c.startswith(":") or c == name:
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


def _is_numeric_id(name: str) -> bool:
    c0 = name[0]
    return c0.isdigit() or (c0 in ("+", "-") and len(name) > 1 and name[1].isdigit())


def _make_type(name: str, typetype: ZTypeType, parent: Optional[ZType] = None) -> ZType:
    return ZType(name=name, typetype=typetype, parent=parent)


def _is_array_type(ztype: ZType) -> bool:
    """Check if a type is a monomorphized array type."""
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


def _is_str_type(ztype: ZType) -> bool:
    """Check if a type is a monomorphized str type."""
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "str"
    )


def _str_capacity(ztype: ZType) -> Optional[int]:
    """Get the capacity of a str type."""
    to_arg = ztype.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def _is_list_type(ztype: ZType) -> bool:
    """Check if a type is a monomorphized list type."""
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "list"
    )


def _list_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of a list type."""
    return ztype.generic_args.get("of")


def _is_map_type(ztype: ZType) -> bool:
    """Check if a type is a monomorphized map type."""
    return (
        isinstance(ztype.generic_origin, ZType) and ztype.generic_origin.name == "map"
    )


def _map_key_type(ztype: ZType) -> Optional[ZType]:
    """Get the key type of a map type."""
    return ztype.generic_args.get("key")


def _map_value_type(ztype: ZType) -> Optional[ZType]:
    """Get the value type of a map type."""
    return ztype.generic_args.get("value")


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
    if ztype.name == "string":
        ztype.needs_destructor = True
        ztype.destructor_name = "zstr_free"
        ztype.is_heap_allocated = True
    elif ztype.typetype in (ZTypeType.CLASS, ZTypeType.UNION, ZTypeType.PROTOCOL):
        ztype.needs_destructor = True
        ztype.destructor_name = f"z_{ztype.name}_destroy"
        ztype.is_heap_allocated = True
    else:
        ztype.needs_destructor = False
        ztype.destructor_name = None
        ztype.is_heap_allocated = False


# Sentinel for definitions currently being resolved
_RESOLVING = object()


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
    if not isinstance(public_unit, zast.Unit):
        return None
    # build external → internal name mapping
    members: dict[str, str] = {}
    for ext_name, defn in public_unit.body.items():
        if isinstance(defn, zast.LabelValue):
            # :field shorthand — external and internal names are the same
            members[ext_name] = ext_name
        elif isinstance(defn, (zast.AtomId, zast.DottedPath)):
            # renamed: api_name: internal_name
            if isinstance(defn, zast.AtomId):
                members[ext_name] = defn.name
            elif isinstance(defn, zast.DottedPath):
                members[ext_name] = defn.child.name
        else:
            # other definitions (functions, etc.) — same name
            members[ext_name] = ext_name
    return members


def _check_private_redefinition(as_items: dict) -> Optional[zast.Unit]:
    """Return the 'private' unit node if it exists in as_items (for error reporting)."""
    private_unit = as_items.get("private")
    if private_unit is not None and isinstance(private_unit, zast.Unit):
        return private_unit
    return None


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
        # track which file units have been fully resolved (generic params detected)
        self._resolved_file_units: set[str] = set()

        # current function return type (for return statement checking)
        self._current_return_type: Optional[ZType] = None

        # current function's ownership annotations (for ownership checking)
        self._current_func_ownership: dict[str, ZParamOwnership] = {}
        self._current_func_return_ownership: Optional[ZParamOwnership] = None

        # pending borrow lock: set by .borrow, consumed by _check_assignment
        self._pending_borrow_lock: Optional[str] = None
        # pending private access: set by .private, consumed by _check_assignment
        self._pending_private_access: bool = False

        # inline unit context stack: tracks nesting during resolution
        # each entry is (unitname, zast.Unit) for name lookup chain
        self._unit_context: List[Tuple[str, zast.Unit]] = []

        # maps implementor type name -> list of (label, protocol ZType)
        self._protocol_labels: dict[str, list[tuple[str, ZType]]] = {}

        # monomorphization cache: (template_name, (arg1_name, ...)) -> ZType
        self._mono_cache: dict[tuple, ZType] = {}
        # ordered list of (monomorphized ZType, original AST node) for emitter
        self._mono_types: list[tuple[ZType, zast.TypeDefinition]] = []

        # generic context stack: list of dicts mapping generic param name -> ZType
        self._generic_context: list[dict[str, ZType]] = []

        # dedup: hash -> (canonical_qualified_name, canonical_Function)
        self._func_hashes: dict[str, tuple[str, zast.Function]] = {}
        # dedup aliases: alias_qualified_name -> canonical_qualified_name
        self._func_aliases: dict[str, str] = {}
        # cloned methods per mono type: mono_name -> {mname: Function}
        self._cloned_methods: dict[str, dict[str, zast.Function]] = {}

        # C name collision tracking: assigned cnames -> set for collision detection
        self._assigned_cnames: set[str] = set()

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
        self.errors.append(zast.Error(err=err, msg=msg, loc=loc, note=note, hint=hint))

    def _assign_cname(self, ztype: ZType, base_cname: str) -> None:
        """Assign a C identifier to a type, auto-resolving collisions.

        If base_cname is already taken, appends the type's nodeid to
        disambiguate. The final cname is stored on ztype.cname.
        """
        if ztype.cname:
            return  # already assigned via earlier resolution path
        if base_cname not in self._assigned_cnames:
            ztype.cname = base_cname
        else:
            ztype.cname = f"{base_cname}_{ztype.nodeid}"
        self._assigned_cnames.add(ztype.cname)

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
        if main_func and isinstance(main_func, zast.Function):
            # resolve main first to trigger demand-driven resolution
            self._resolve_unit_name(self.program.mainunitname, "main")
            self._check_function_body("main", main_func)

        # check for native declarations in user code (not allowed)
        self._check_native_in_user_code(mainunit)

        # also check other definitions in the main unit
        for name, defn in mainunit.body.items():
            if name == "main":
                continue
            if isinstance(defn, zast.Unit):
                self._resolve_unit_name(self.program.mainunitname, name)
            elif isinstance(defn, zast.Function) and defn.body:
                self._resolve_unit_name(self.program.mainunitname, name)
                self._check_function_body(name, defn)
            elif isinstance(defn, zast.Function) and defn.body is None:
                # spec (function without body) — resolve type
                self._resolve_unit_name(self.program.mainunitname, name)

        if full:
            for unitname, unit in self.program.units.items():
                for name, defn in unit.body.items():
                    self._resolve_unit_name(unitname, name)
                    if isinstance(defn, zast.Function) and defn.body:
                        self._check_function_body(name, defn)

        return self.errors

    def _check_native_in_user_code(self, unit: zast.Unit) -> None:
        """Report errors for native declarations in user code.

        The 'native' keyword is reserved for system library definitions.
        User code should not use 'is native' on functions or types.
        """
        for name, defn in unit.body.items():
            if isinstance(defn, zast.Function) and defn.is_native:
                self._error(
                    f"'native' is reserved for system library definitions: '{name}'",
                    loc=defn.start,
                    err=ERR.TYPEERROR,
                    hint="remove 'is native' and provide a function body",
                )
            elif isinstance(defn, (zast.Record, zast.Class, zast.Union, zast.Variant)):
                if defn.is_native:
                    self._error(
                        f"'native' is reserved for system library definitions: '{name}'",
                        loc=defn.start,
                        err=ERR.TYPEERROR,
                        hint="remove 'is native' and declare fields normally",
                    )
                # also check methods inside the type
                for mname, mfunc in defn.as_functions.items():
                    if mfunc.is_native:
                        self._error(
                            f"'native' is reserved for system library definitions: '{name}.{mname}'",
                            loc=mfunc.start,
                            err=ERR.TYPEERROR,
                            hint="remove 'is native' and provide a function body",
                        )
                # check functions in the 'is' section too
                if hasattr(defn, "functions"):
                    for mname, mfunc in defn.functions.items():
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
                if isinstance(inner, zast.Unit):
                    current_body = inner.body
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
                self.unit_types[unitname].children[name] = t
        return t

    # dispatch table for _type_of_definition: AST type -> resolver method name
    _DEFINITION_RESOLVERS: dict = {
        zast.Function: "_resolve_function_type",
        zast.Record: "_resolve_record_type",
        zast.Class: "_resolve_class_type",
        zast.Union: "_resolve_union_type",
        zast.Variant: "_resolve_variant_type",
        zast.Protocol: "_resolve_protocol_type",
        zast.Facet: "_resolve_facet_type",
        zast.Unit: "_resolve_inline_unit_type",
    }

    def _type_of_definition(
        self, unitname: str, name: str, defn: zast.TypeDefinition
    ) -> Optional[ZType]:
        """Type-check a definition, pushing/popping the resolving stack."""
        # dispatch structured types via table
        resolver_name = self._DEFINITION_RESOLVERS.get(type(defn))
        if resolver_name:
            return getattr(self, resolver_name)(unitname, name, defn)
        # alias: DottedPath reference
        if isinstance(defn, zast.DottedPath):
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)  # placeholder for alias
            self._resolving.append((key, shell))
            t = self._resolve_dotted_path(defn)
            self._resolving.pop()
            return t
        if isinstance(defn, zast.LabelValue):
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)
            self._resolving.append((key, shell))
            t = self._resolve_name(defn.name, skip_unit_def=(unitname, name))
            self._resolving.pop()
            return t
        if isinstance(defn, zast.Expression) and isinstance(defn.expression, zast.Data):
            return self._resolve_data_type(unitname, name, defn.expression)
        if isinstance(defn, zast.Expression) and isinstance(defn.expression, zast.If):
            return self._resolve_unit_level_if(unitname, name, defn)
        if isinstance(defn, zast.Expression) and isinstance(
            defn.expression, zast.Operation
        ):
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)
            self._resolving.append((key, shell))
            t = self._check_expression(defn)
            self._resolving.pop()
            return t
        if isinstance(defn, zast.AtomId):
            if _is_numeric_id(defn.name):
                t = self._resolve_numeric(defn.name, loc=defn.start)
                # constant folding: set const_value on the definition node
                if t:
                    _, value, err = parse_number(defn.name)
                    if not err and isinstance(value, int):
                        defn.const_value = value
                return t
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)  # placeholder for alias
            self._resolving.append((key, shell))
            t = self._resolve_name(defn.name)
            self._resolving.pop()
            return t
        # constant folding: handle BinOp at unit level (e.g., b: a + 2)
        if isinstance(defn, zast.BinOp):
            key = f"{unitname}.{name}"
            shell = _make_type(name, ZTypeType.NULL)
            self._resolving.append((key, shell))
            t = self._check_binop(defn)
            self._resolving.pop()
            return t
        return None

    def _resolve_unit_level_if(
        self, unitname: str, name: str, defn: zast.Expression
    ) -> Optional[ZType]:
        """Resolve a unit-level if definition (compile-time conditional)."""
        ifnode = defn.expression
        assert isinstance(ifnode, zast.If)
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
                cond_op.const_value is not None
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
                bool(cond_op.const_value) for _, cond_op in clause.conditions.items()
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
        if t is self._NORETURN or t is None or not isinstance(t, ZType):
            self._error(
                "unit-level if branch must produce a value",
                loc=ifnode.start,
            )
            self._resolving.pop()
            return None

        ifnode.type = t

        # propagate const_value from taken branch if available
        if taken_stmt.statements:
            last_inner = taken_stmt.statements[-1].statementline
            if isinstance(last_inner, zast.Expression):
                inner_expr = last_inner.expression
                if (
                    hasattr(inner_expr, "const_value")
                    and inner_expr.const_value is not None
                ):
                    defn.const_value = inner_expr.const_value

        self._resolving.pop()
        return t

    def _resolve_function_type(
        self, unitname: str, name: str, func: zast.Function
    ) -> ZType:
        key = f"{unitname}.{name}"
        ftype = _make_type(name, ZTypeType.FUNCTION)
        self._resolved[key] = ftype  # early register for self-reference
        self._resolving.append((key, ftype))

        # pass 1: detect generic params from 'as' clause
        generic_ctx: dict[str, ZType] = {}
        for pname, ppath in func.as_items.items():
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

        # check: methods (functions with a parameter of type 'this') cannot have 'as'
        if generic_ctx and func.as_items:
            has_this = any(
                (isinstance(ppath, zast.AtomId) and ppath.name == "this")
                or (
                    isinstance(ppath, zast.DottedPath)
                    and isinstance(ppath.parent, zast.AtomId)
                    and ppath.parent.name == "this"
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
        for mname, mfunc in func.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            ftype.children[mname] = mt

        # pass 2: resolve non-generic params with generic context
        if generic_ctx:
            self._generic_context.append(generic_ctx)
        if func.returntype:
            rt = self._resolve_typeref(func.returntype)
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
        for pname, ppath in func.parameters.items():
            pt = self._resolve_typeref(ppath)
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
                ftype.children[pname] = pt
                # detect defaults
                if isinstance(ppath, zast.AtomId) and _is_numeric_id(ppath.name):
                    _, val, err = parse_number(ppath.name)
                    if not err:
                        ftype.param_defaults[pname] = str(int(val))
                elif isinstance(ppath, zast.DottedPath):
                    if isinstance(ppath.parent, zast.AtomId) and _is_numeric_id(
                        ppath.parent.name
                    ):
                        child_name = ppath.child.name
                        _, val, err = parse_number(ppath.parent.name + child_name)
                        if not err:
                            ftype.param_defaults[pname] = str(int(val))
                elif isinstance(ppath, zast.AtomId):
                    defn = self._lookup_definition(ppath.name)
                    if isinstance(defn, zast.Function) and defn.body is not None:
                        ftype.param_defaults[pname] = ppath.name
        if generic_ctx:
            self._generic_context.pop()

        # propagate ownership annotations from AST to ZType
        if func.param_ownership:
            ftype.param_ownership = dict(func.param_ownership)
        if func.return_ownership is not None:
            ftype.return_ownership = func.return_ownership

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

    def _resolve_class_type(self, unitname: str, name: str, cls: zast.Class) -> ZType:
        key = f"{unitname}.{name}"
        ctype = _make_type(name, ZTypeType.CLASS)
        self._resolved[key] = ctype  # early register for self-reference
        self._resolving.append((key, ctype))

        ctype.is_valtype = False  # classes are reference types
        _set_destructor_metadata(ctype)
        self._assign_cname_type(ctype)

        # pass 1: detect generic params (now in as_items)
        generic_ctx: dict[str, ZType] = {}
        for fname, fpath in cls.as_items.items():
            ft = self._resolve_typeref(fpath)
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

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(cls.items, cls.start)
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
                cls.as_functions,
                cls.functions,
                cls.start,
                generic_ctx,
            )

        # pass 2: resolve non-generic fields with generic context
        if generic_ctx:
            self._generic_context.append(generic_ctx)
        for fname, fpath in cls.items.items():
            ft = self._resolve_typeref(fpath)
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
                ctype.children[fname] = ft
                # detect .private field type (friend access)
                if isinstance(fpath, zast.DottedPath) and fpath.child.name == "private":
                    ctype.private_fields.add(fname)
                # detect field defaults
                if isinstance(fpath, zast.AtomId) and _is_numeric_id(fpath.name):
                    _, val, err = parse_number(fpath.name)
                    if not err:
                        ctype.param_defaults[fname] = str(int(val))
                elif isinstance(fpath, zast.DottedPath):
                    if isinstance(fpath.parent, zast.AtomId) and _is_numeric_id(
                        fpath.parent.name
                    ):
                        child_name = fpath.child.name
                        _, val, err = parse_number(fpath.parent.name + child_name)
                        if not err:
                            ctype.param_defaults[fname] = str(int(val))
                elif isinstance(fpath, zast.AtomId):
                    defn = self._lookup_definition(fpath.name)
                    if isinstance(defn, zast.Function) and defn.body is not None:
                        ctype.param_defaults[fname] = fpath.name
        if generic_ctx:
            self._generic_context.pop()

        # for generic classes, defer method resolution and meta.create to monomorphization
        if not ctype.isgeneric:
            for mname, mfunc in cls.functions.items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                ctype.children[mname] = mt
            # as_functions (methods defined in 'as' block)
            for mname, mfunc in cls.as_functions.items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                ctype.children[mname] = mt

            # typecheck method bodies
            for mname, mfunc in cls.functions.items():
                if mfunc.body:
                    self._check_function_body(f"{name}.{mname}", mfunc)
            for mname, mfunc in cls.as_functions.items():
                if mfunc.body:
                    self._check_function_body(f"{name}.{mname}", mfunc)

            # as_items: protocol satisfaction
            self._process_as_items_protocols(name, ctype, cls.as_items, cls.start)

            # generate meta.create constructor type
            is_func_names = set(cls.functions.keys())
            create_type = self._make_meta_create_type(name, ctype, is_func_names)
            ctype.children[":meta.create"] = create_type
            if "create" not in ctype.children:
                ctype.children["create"] = create_type

        ctype.public_members = _extract_public_members(cls.as_items)
        priv = _check_private_redefinition(cls.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)
        self._resolving.pop()
        return ctype

    def _resolve_union_type(
        self, unitname: str, name: str, union_defn: zast.Union
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
        for sname, spath in union_defn.as_items.items():
            st = self._resolve_typeref(spath)
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

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(
            union_defn.items, union_defn.start
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
                union_defn.as_functions,
                union_defn.functions,
                union_defn.start,
                generic_ctx,
            )

        # pass 2: resolve subtype items with generic context
        if generic_ctx:
            self._generic_context.append(generic_ctx)
        subtype_names = list(union_defn.items.keys())
        for sname, spath in union_defn.items.items():
            st_check = self._resolve_typeref(spath)
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
            if isinstance(spath, zast.AtomId) and spath.name == "null":
                st = _make_type("null", ZTypeType.RECORD)
                st.is_valtype = True
            else:
                st = self._resolve_typeref(spath)
            if st:
                utype.children[sname] = st
        if generic_ctx:
            self._generic_context.pop()

        # for generic unions, skip tag generation (done at monomorphization time)
        if utype.isgeneric:
            # resolve methods
            for mname, mfunc in union_defn.functions.items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                utype.children[mname] = mt
            for mname, mfunc in union_defn.as_functions.items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                utype.children[mname] = mt
            utype.public_members = _extract_public_members(union_defn.as_items)
            priv = _check_private_redefinition(union_defn.as_items)
            if priv:
                self._error("'private' cannot be redefined", loc=priv.start)
            self._resolving.pop()
            return utype

        # resolve tag from as_items: look for tag type (monomorphized or generic)
        custom_tag_data = None  # the parent DATA/RECORD type of the .tag
        tag_count = 0

        for as_name, as_path in union_defn.as_items.items():
            as_type = (
                self._resolve_dotted_path(as_path)
                if isinstance(as_path, zast.DottedPath)
                else self._resolve_typeref(as_path)
            )
            is_tag = (
                (as_type and as_type.typetype == ZTypeType.TAG)
                or (as_type and getattr(as_type, "generic_origin", None) == "tag")
                or (as_type and as_type.isgeneric and as_type.name == "tag")
            )
            if is_tag:
                assert as_type is not None
                tag_count += 1
                if tag_count > 1:
                    self._error(
                        f"Union '{name}' has multiple .tag items in 'as' block",
                        loc=union_defn.start,
                    )
                    break
                if as_type.parent:
                    custom_tag_data = as_type.parent
                elif isinstance(as_path, zast.DottedPath) and isinstance(
                    as_path.parent, zast.AtomId
                ):
                    # generic tag from numeric type: u16.tag → parent is u16
                    custom_tag_data = getattr(as_path.parent, "type", None)
                    if not custom_tag_data:
                        custom_tag_data = self._resolve_name(as_path.parent.name)

        if custom_tag_data and custom_tag_data.typetype == ZTypeType.DATA:
            # validate: data labels must match union subtypes 1:1
            data_labels = [
                k
                for k in custom_tag_data.children
                if not k.startswith(":") and k != "tag"
            ]
            if sorted(data_labels) != sorted(subtype_names):
                missing_in_data = set(subtype_names) - set(data_labels)
                missing_in_union = set(data_labels) - set(subtype_names)
                msg_parts = []
                if missing_in_data:
                    msg_parts.append(
                        f"missing in data: {', '.join(sorted(missing_in_data))}"
                    )
                if missing_in_union:
                    msg_parts.append(
                        f"missing in union: {', '.join(sorted(missing_in_union))}"
                    )
                self._error(
                    f"Union '{name}' tag data labels do not match subtypes: "
                    + "; ".join(msg_parts),
                    loc=union_defn.start,
                )
            # validate: data values must be unique
            seen_values: dict = {}
            for dl in data_labels:
                child = custom_tag_data.children[dl]
                val = child.name if child else None
                if val in seen_values:
                    self._error(
                        f"Union '{name}' tag data has duplicate value "
                        f"'{val}' for labels '{seen_values[val]}' and '{dl}'",
                        loc=union_defn.start,
                    )
                seen_values[val] = dl

            # use custom data values as discriminators
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for sname in subtype_names:
                child = custom_tag_data.children.get(sname)
                val = child.name if child else str(subtype_names.index(sname))
                tag_type.children[sname] = _make_type(val, ZTypeType.RECORD)
            utype.children[":tag"] = tag_type
            # store the data type so MyUnion.tag returns it
            utype.children["tag"] = custom_tag_data

        elif custom_tag_data and custom_tag_data.typetype == ZTypeType.RECORD:
            # numeric type tag (e.g., u16.tag) — auto-generate sequential values
            num_subtypes = len(subtype_names)
            # check fits in the type (basic check for u8)
            if custom_tag_data.name == "u8" and num_subtypes > 256:
                self._error(
                    f"Union '{name}' has {num_subtypes} subtypes, "
                    f"exceeds u8 tag capacity (max 256)",
                    loc=union_defn.start,
                )
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                tag_type.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            utype.children[":tag"] = tag_type
            # generate a data type for MyUnion.tag
            gen_data = _make_type(f"{name}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                gen_data.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.generic_origin = "tag"
            gen_data.children["tag"] = gen_tag
            utype.children["tag"] = gen_data

        else:
            # no custom tag: auto-generate with u8 default
            num_subtypes = len(subtype_names)
            if num_subtypes > 256:
                self._error(
                    f"Union '{name}' has {num_subtypes} subtypes, "
                    f"exceeds default u8 tag capacity (max 256). "
                    f"Specify a custom tag type via 'as' block",
                    loc=union_defn.start,
                )
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                tag_type.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            utype.children[":tag"] = tag_type
            # generate a data type for MyUnion.tag
            gen_data = _make_type(f"{name}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                gen_data.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.generic_origin = "tag"
            gen_data.children["tag"] = gen_tag
            utype.children["tag"] = gen_data

        # resolve methods
        for mname, mfunc in union_defn.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            utype.children[mname] = mt
        for mname, mfunc in union_defn.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            utype.children[mname] = mt

        # typecheck method bodies (non-generic only)
        for mname, mfunc in union_defn.functions.items():
            if mfunc.body:
                self._check_function_body(f"{name}.{mname}", mfunc)
        for mname, mfunc in union_defn.as_functions.items():
            if mfunc.body:
                self._check_function_body(f"{name}.{mname}", mfunc)

        utype.public_members = _extract_public_members(union_defn.as_items)
        priv = _check_private_redefinition(union_defn.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)
        self._resolving.pop()
        return utype

    def _resolve_variant_type(
        self, unitname: str, name: str, variant_defn: zast.Variant
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
        for sname, spath in variant_defn.as_items.items():
            st = self._resolve_typeref(spath)
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

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(
            variant_defn.items, variant_defn.start
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
                variant_defn.as_functions,
                variant_defn.functions,
                variant_defn.start,
                {},
            )

        # resolve each subtype item (with generic context if applicable)
        if generic_ctx:
            self._generic_context.append(generic_ctx)
        subtype_names = list(variant_defn.items.keys())
        for sname, spath in variant_defn.items.items():
            if isinstance(spath, zast.AtomId) and spath.name == "null":
                st = _make_type("null", ZTypeType.RECORD)
                st.is_valtype = True
            else:
                st = self._resolve_typeref(spath)
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
                    elif st.name == "string":
                        self._error(
                            f"Variant '{name}' subtype '{sname}' must be a value type",
                            loc=variant_defn.start,
                        )
            if st:
                vtype.children[sname] = st
        if generic_ctx:
            self._generic_context.pop()

        # for generic variants, skip tag generation (done at monomorphization time)
        if vtype.isgeneric:
            for mname, mfunc in variant_defn.functions.items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                vtype.children[mname] = mt
            for mname, mfunc in variant_defn.as_functions.items():
                mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
                vtype.children[mname] = mt
            vtype.public_members = _extract_public_members(variant_defn.as_items)
            priv = _check_private_redefinition(variant_defn.as_items)
            if priv:
                self._error("'private' cannot be redefined", loc=priv.start)
            self._resolving.pop()
            return vtype

        # resolve tag from as_items
        custom_tag_data = None
        tag_count = 0

        for as_name, as_path in variant_defn.as_items.items():
            as_type = (
                self._resolve_dotted_path(as_path)
                if isinstance(as_path, zast.DottedPath)
                else self._resolve_typeref(as_path)
            )
            is_tag = (
                (as_type and as_type.typetype == ZTypeType.TAG)
                or (as_type and getattr(as_type, "generic_origin", None) == "tag")
                or (as_type and as_type.isgeneric and as_type.name == "tag")
            )
            if is_tag:
                assert as_type is not None
                tag_count += 1
                if tag_count > 1:
                    self._error(
                        f"Variant '{name}' has multiple .tag items in 'as' block",
                        loc=variant_defn.start,
                    )
                    break
                if as_type.parent:
                    custom_tag_data = as_type.parent
                elif isinstance(as_path, zast.DottedPath) and isinstance(
                    as_path.parent, zast.AtomId
                ):
                    custom_tag_data = getattr(as_path.parent, "type", None)
                    if not custom_tag_data:
                        custom_tag_data = self._resolve_name(as_path.parent.name)

        if custom_tag_data and custom_tag_data.typetype == ZTypeType.DATA:
            # validate: data labels must match variant subtypes 1:1
            data_labels = [
                k
                for k in custom_tag_data.children
                if not k.startswith(":") and k != "tag"
            ]
            if sorted(data_labels) != sorted(subtype_names):
                missing_in_data = set(subtype_names) - set(data_labels)
                missing_in_variant = set(data_labels) - set(subtype_names)
                msg_parts = []
                if missing_in_data:
                    msg_parts.append(
                        f"missing in data: {', '.join(sorted(missing_in_data))}"
                    )
                if missing_in_variant:
                    msg_parts.append(
                        f"missing in variant: {', '.join(sorted(missing_in_variant))}"
                    )
                self._error(
                    f"Variant '{name}' tag data labels do not match subtypes: "
                    + "; ".join(msg_parts),
                    loc=variant_defn.start,
                )
            # validate: data values must be unique
            seen_values: dict = {}
            for dl in data_labels:
                child = custom_tag_data.children[dl]
                val = child.name if child else None
                if val in seen_values:
                    self._error(
                        f"Variant '{name}' tag data has duplicate value "
                        f"'{val}' for labels '{seen_values[val]}' and '{dl}'",
                        loc=variant_defn.start,
                    )
                seen_values[val] = dl

            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for sname in subtype_names:
                child = custom_tag_data.children.get(sname)
                val = child.name if child else str(subtype_names.index(sname))
                tag_type.children[sname] = _make_type(val, ZTypeType.RECORD)
            vtype.children[":tag"] = tag_type
            vtype.children["tag"] = custom_tag_data

        elif custom_tag_data and custom_tag_data.typetype == ZTypeType.RECORD:
            num_subtypes = len(subtype_names)
            if custom_tag_data.name == "u8" and num_subtypes > 256:
                self._error(
                    f"Variant '{name}' has {num_subtypes} subtypes, "
                    f"exceeds u8 tag capacity (max 256)",
                    loc=variant_defn.start,
                )
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                tag_type.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            vtype.children[":tag"] = tag_type
            gen_data = _make_type(f"{name}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                gen_data.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.generic_origin = "tag"
            gen_data.children["tag"] = gen_tag
            vtype.children["tag"] = gen_data

        else:
            num_subtypes = len(subtype_names)
            if num_subtypes > 256:
                self._error(
                    f"Variant '{name}' has {num_subtypes} subtypes, "
                    f"exceeds default u8 tag capacity (max 256). "
                    f"Specify a custom tag type via 'as' block",
                    loc=variant_defn.start,
                )
            tag_type = _make_type(f"{name}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                tag_type.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            vtype.children[":tag"] = tag_type
            gen_data = _make_type(f"{name}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                gen_data.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.generic_origin = "tag"
            gen_data.children["tag"] = gen_tag
            vtype.children["tag"] = gen_data

        # resolve methods
        for mname, mfunc in variant_defn.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            vtype.children[mname] = mt
        for mname, mfunc in variant_defn.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            vtype.children[mname] = mt

        # typecheck method bodies (non-generic only — variants don't support generics yet)
        for mname, mfunc in variant_defn.functions.items():
            if mfunc.body:
                self._check_function_body(f"{name}.{mname}", mfunc)
        for mname, mfunc in variant_defn.as_functions.items():
            if mfunc.body:
                self._check_function_body(f"{name}.{mname}", mfunc)

        vtype.public_members = _extract_public_members(variant_defn.as_items)
        priv = _check_private_redefinition(variant_defn.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)
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
            if isinstance(item.valtype, zast.AtomId) and _is_numeric_id(
                item.valtype.name
            ):
                if element_type is None:
                    element_type = self._resolve_numeric(
                        item.valtype.name, loc=item.valtype.start
                    )
                # parse the actual numeric value for storage
                _, val, err = parse_number(item.valtype.name)
                if not err:
                    val_str = str(int(val)) if not isinstance(val, float) else str(val)
                    vt = _make_type(val_str, ZTypeType.RECORD)
                    vt.is_valtype = True
                    dtype.children[ename] = vt
            elif isinstance(item.valtype, zast.Path):
                et = self._resolve_typeref(item.valtype)
                if et:
                    dtype.children[ename] = et

        # Store element type for later use
        if element_type:
            dtype.children[":element_type"] = element_type

        # Generate .tag subtype — monomorphized tag(element_type) with parent=data
        et_name = element_type.name if element_type else "i64"
        tag_type = _make_type(f"tag__{et_name}", ZTypeType.RECORD, parent=dtype)
        tag_type.is_valtype = True
        tag_type.generic_origin = "tag"
        dtype.children["tag"] = tag_type

        self._resolving.pop()
        return dtype

    def _resolve_record_type(self, unitname: str, name: str, rec: zast.Record) -> ZType:
        key = f"{unitname}.{name}"
        rtype = _make_type(name, ZTypeType.RECORD)
        self._resolved[key] = rtype  # early register for self-reference
        self._resolving.append((key, rtype))

        rtype.is_valtype = True  # records are value types
        _set_destructor_metadata(rtype)
        self._assign_cname_type(rtype)

        # pass 1: detect generic params (now in as_items)
        generic_ctx: dict[str, ZType] = {}
        for fname, fpath in rec.as_items.items():
            ft = self._resolve_typeref(fpath)
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

        # typedef detection: single item with .typedef type
        typedef_base_type, typedef_field = self._detect_typedef(rec.items, rec.start)
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
                rec.as_functions,
                rec.functions,
                rec.start,
                generic_ctx,
            )

        # pass 2: resolve non-generic fields with generic context
        if generic_ctx:
            self._generic_context.append(generic_ctx)
        for fname, fpath in rec.items.items():
            ft = self._resolve_typeref(fpath)
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
                rtype.children[fname] = ft
                # detect field defaults
                if isinstance(fpath, zast.AtomId) and _is_numeric_id(fpath.name):
                    _, val, err = parse_number(fpath.name)
                    if not err:
                        rtype.param_defaults[fname] = str(int(val))
                elif isinstance(fpath, zast.DottedPath):
                    if isinstance(fpath.parent, zast.AtomId) and _is_numeric_id(
                        fpath.parent.name
                    ):
                        child_name = fpath.child.name
                        _, val, err = parse_number(fpath.parent.name + child_name)
                        if not err:
                            rtype.param_defaults[fname] = str(int(val))
                elif isinstance(fpath, zast.AtomId):
                    defn = self._lookup_definition(fpath.name)
                    if isinstance(defn, zast.Function) and defn.body is not None:
                        rtype.param_defaults[fname] = fpath.name
        if generic_ctx:
            self._generic_context.pop()
        for mname, mfunc in rec.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            rtype.children[mname] = mt
        # as_functions (methods defined in 'as' block)
        for mname, mfunc in rec.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            rtype.children[mname] = mt

        # typecheck method bodies (non-generic only)
        if not rtype.isgeneric:
            for mname, mfunc in rec.functions.items():
                if mfunc.body:
                    self._check_function_body(f"{name}.{mname}", mfunc)
            for mname, mfunc in rec.as_functions.items():
                if mfunc.body:
                    self._check_function_body(f"{name}.{mname}", mfunc)

        # as_items: protocol satisfaction
        self._process_as_items_protocols(name, rtype, rec.as_items, rec.start)

        # generate meta.create constructor type
        is_func_names = set(rec.functions.keys())
        create_type = self._make_meta_create_type(name, rtype, is_func_names)
        rtype.children[":meta.create"] = create_type
        if "create" not in rtype.children:
            rtype.children["create"] = create_type

        rtype.public_members = _extract_public_members(rec.as_items)
        priv = _check_private_redefinition(rec.as_items)
        if priv:
            self._error("'private' cannot be redefined", loc=priv.start)
        self._resolving.pop()
        return rtype

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

        # No function pointer fields allowed in typedef is-section
        if is_functions:
            self._error("Additional fields on typedef objects are forbidden", loc=start)

        # Process as_functions: new/shadowed methods
        if generic_ctx:
            self._generic_context.append(generic_ctx)
        for mname, mfunc in as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            rtype.children[mname] = mt

        # Process as_items: null hiding, protocol satisfaction, generic params
        for label, apath in as_items.items():
            at = self._resolve_typeref(apath)
            if (
                at
                and at.typetype == ZTypeType.GENERIC_PARAM
                and at.name == "__generic_param"
            ):
                continue  # generic params already handled in pass 1
            if at and at.name == "null":
                null_type = _make_type("null", ZTypeType.NULL)
                rtype.children[label] = null_type  # marks method as hidden
                continue
            # protocol/facet satisfaction
            if at and at.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                self._process_as_items_protocols(name, rtype, {label: apath}, start)
        if generic_ctx:
            self._generic_context.pop()

        # Synthesize constructors: take/create and borrow
        if not rtype.isgeneric:
            take_type = _make_type(f"{name}.take", ZTypeType.FUNCTION)
            take_type.return_type = rtype
            take_type.children["from"] = base_type
            take_type.param_ownership["from"] = ZParamOwnership.TAKE
            rtype.children["take"] = take_type
            rtype.children["create"] = take_type
            rtype.children[":meta.create"] = take_type

            borrow_type = _make_type(f"{name}.borrow", ZTypeType.FUNCTION)
            borrow_type.return_type = rtype
            borrow_type.children["from"] = base_type
            borrow_type.param_ownership["from"] = ZParamOwnership.LOCK
            rtype.children["borrow"] = borrow_type

        self._resolving.pop()
        return rtype

    def _process_as_items_protocols(
        self, name: str, rtype: ZType, as_items: dict, start: Token
    ) -> None:
        """Process as_items for protocol satisfaction (labeled protocol refs)."""
        for label, apath in as_items.items():
            at = self._resolve_typeref(apath)
            if (
                at
                and at.typetype == ZTypeType.GENERIC_PARAM
                and at.name == "__generic_param"
            ):
                continue  # generic params handled in pass 1
            if at and at.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                # facet: only valtypes can implement facets
                if at.typetype == ZTypeType.FACET and not _is_valtype(rtype):
                    self._error(
                        f"Only value types can implement facet '{at.name}', "
                        f"but '{name}' is a reference type",
                        loc=start,
                    )
                # conformance check: implementor must have all spec methods
                for spec_name, spec_func in at.children.items():
                    if spec_name.startswith(":") or spec_name in (
                        "create",
                        "take",
                        "borrow",
                    ):
                        continue
                    method = rtype.children.get(spec_name)
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
                rtype.children[label] = at
                self._protocol_labels.setdefault(name, []).append((label, at))
            else:
                # non-protocol as_item (existing behavior: tag refs, etc.)
                if at:
                    rtype.children[label] = at

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
        spec_params = [(k, v) for k, v in spec_func.children.items() if k != "this"]
        impl_params = [
            (k, v)
            for k, v in impl_func.children.items()
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
        self, unitname: str, name: str, proto: zast.Protocol
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
        for pname, ppath in proto.parameters.items():
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
            self._generic_context.append(generic_ctx)
        for sname, sfunc in proto.specs.items():
            st = self._resolve_function_type(unitname, f"{name}.{sname}", sfunc)
            ptype.children[sname] = st
        if generic_ctx:
            self._generic_context.pop()

        # owned create: protocol.create from: expr
        if not ptype.isgeneric:
            create_type = _make_type(f"{name}.create", ZTypeType.FUNCTION)
            create_type.return_type = ptype
            # from: parameter — placeholder type (conformance checked in _check_call)
            create_type.children["from"] = self.t_null
            create_type.param_ownership["from"] = ZParamOwnership.TAKE
            ptype.children["create"] = create_type

            # take: alias for create
            take_type = _make_type(f"{name}.take", ZTypeType.FUNCTION)
            take_type.return_type = ptype
            take_type.children["from"] = self.t_null
            take_type.param_ownership["from"] = ZParamOwnership.TAKE
            ptype.children["take"] = take_type

            # borrow: borrowed protocol creation
            borrow_type = _make_type(f"{name}.borrow", ZTypeType.FUNCTION)
            borrow_type.return_type = ptype
            borrow_type.children["from"] = self.t_null
            borrow_type.param_ownership["from"] = ZParamOwnership.LOCK
            ptype.children["borrow"] = borrow_type

        self._resolving.pop()
        return ptype

    def _resolve_facet_type(self, unitname: str, name: str, facet: zast.Facet) -> ZType:
        key = f"{unitname}.{name}"
        ftype = _make_type(name, ZTypeType.FACET)
        self._resolved[key] = ftype
        self._resolving.append((key, ftype))
        ftype.is_valtype = True  # facet instances are value types
        _set_destructor_metadata(ftype)
        self._assign_cname_type(ftype)

        # pass 1: detect generic params from facet parameters
        generic_ctx: dict[str, ZType] = {}
        for pname, ppath in facet.parameters.items():
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
            self._generic_context.append(generic_ctx)
        for sname, sfunc in facet.specs.items():
            st = self._resolve_function_type(unitname, f"{name}.{sname}", sfunc)
            ftype.children[sname] = st
        if generic_ctx:
            self._generic_context.pop()

        # create/take: owned facet creation (copies value)
        if not ftype.isgeneric:
            create_type = _make_type(f"{name}.create", ZTypeType.FUNCTION)
            create_type.return_type = ftype
            create_type.children["from"] = self.t_null
            create_type.param_ownership["from"] = ZParamOwnership.TAKE
            ftype.children["create"] = create_type

            take_type = _make_type(f"{name}.take", ZTypeType.FUNCTION)
            take_type.return_type = ftype
            take_type.children["from"] = self.t_null
            take_type.param_ownership["from"] = ZParamOwnership.TAKE
            ftype.children["take"] = take_type

            # borrow: borrowed facet creation (copies value, locks source)
            borrow_type = _make_type(f"{name}.borrow", ZTypeType.FUNCTION)
            borrow_type.return_type = ftype
            borrow_type.children["from"] = self.t_null
            borrow_type.param_ownership["from"] = ZParamOwnership.LOCK
            ftype.children["borrow"] = borrow_type

        self._resolving.pop()
        return ftype

    def _make_meta_create_type(
        self,
        name: str,
        parent_type: ZType,
        is_func_names: Optional[set] = None,
    ) -> ZType:
        """Build a FUNCTION ZType for the compiler-generated meta.create constructor.

        is_func_names: set of function names from the 'is' section that should
        be included as constructor parameters (function pointer fields).
        """
        ftype = _make_type(f"{name}.create", ZTypeType.FUNCTION)
        ftype.return_type = parent_type
        for fname, ft in parent_type.children.items():
            if fname.startswith(":"):
                continue
            if ft.typetype == ZTypeType.FUNCTION:
                # only include function-typed children from the 'is' section
                if is_func_names and fname in is_func_names:
                    ftype.children[fname] = ft
                    if fname in parent_type.param_defaults:
                        ftype.param_defaults[fname] = parent_type.param_defaults[fname]
                continue
            ftype.children[fname] = ft
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
        self.unit_types[name] = utype

        # detect generic params in unit body (DottedPath items like t: any.generic)
        generic_ctx: dict[str, ZType] = {}
        generic_param_names: set[str] = set()
        for dname, ddefn in unit.body.items():
            if isinstance(ddefn, zast.DottedPath):
                ft = self._resolve_typeref(ddefn)
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
            self._generic_context.append(generic_ctx)

        # resolve each non-generic-param definition in the inline unit's body
        for dname, ddefn in unit.body.items():
            if dname in generic_param_names:
                continue  # skip generic param declarations
            dkey = f"{unitname}.{name}.{dname}"
            if dkey in self._resolved:
                utype.children[dname] = self._resolved[dkey]
                continue
            t = self._type_of_definition(unitname, f"{name}.{dname}", ddefn)
            if t:
                self._resolved[dkey] = t
                utype.children[dname] = t
            # check function bodies inside inline units (skip for generic units —
            # bodies will be checked after monomorphization)
            if not utype.isgeneric and isinstance(ddefn, zast.Function) and ddefn.body:
                self._check_function_body(f"{name}.{dname}", ddefn)

        if utype.isgeneric:
            self._generic_context.pop()

        self._unit_context.pop()
        return utype

    # ---- Name resolution (local -> unit body -> core -> system) ----

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
                        ut.children[name] = t
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
        if isinstance(path, zast.AtomId) and self._generic_context:
            for ctx in reversed(self._generic_context):
                if path.name in ctx:
                    gp_ref = _make_type(path.name, ZTypeType.GENERIC_PARAM)
                    gp_ref.parent = ctx[path.name]  # constraint
                    path.type = gp_ref
                    return gp_ref
        if isinstance(path, zast.AtomId):
            name = path.name
            if _is_numeric_id(name):
                t = self._resolve_numeric(name, loc=path.start)
                if t:
                    path.type = t
                return t
            if name == "type":
                t = self._resolve_type_keyword()
                if t:
                    path.type = t
                return t
            if name == "this":
                t = self._resolve_this_keyword()
                if t:
                    path.type = t
                return t
            t = self._resolve_name(name)
            if t and t.isgeneric:
                # allow bare generic 'tag' as field type (monomorphized on use)
                if name == "tag":
                    path.type = t
                    return t
                self._error(
                    f"generic type '{name}' requires type arguments",
                    loc=path.start,
                    err=ERR.GENERICERROR,
                    hint=f"specify type parameters, e.g. ({name} t: i64)",
                )
                return None
            if t:
                path.type = t
            return t
        if isinstance(path, zast.DottedPath):
            t = self._resolve_dotted_path(path)
            if t:
                path.type = t
            return t
        if isinstance(path, zast.Expression):
            inner = path.expression
            if isinstance(inner, zast.Call):
                t = self._resolve_typeref_call(inner)
                if t:
                    path.type = t
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
        if not isinstance(op, zast.AtomId):
            self._error(
                "Numeric generic argument must be a numeric literal",
                loc=loc,
            )
            return None
        numstr = op.name

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
        if isinstance(call.callable, zast.AtomId):
            template = self._resolve_name(call.callable.name)
        elif isinstance(call.callable, zast.DottedPath):
            template = self._check_dotted_path(call.callable)
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
            if not isinstance(arg.valtype, zast.Path):
                continue
            arg_type = self._resolve_typeref(arg.valtype)
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

    def _resolve_dotted_path(self, path: zast.DottedPath) -> Optional[ZType]:
        parent_type: Optional[ZType] = None
        if isinstance(path.parent, zast.AtomId):
            pname = path.parent.name
            # numeric dotted path: 0.u32, 42.i8, 0xff.u16
            if _is_numeric_id(pname):
                child_name = path.child.name
                _, _, err = parse_number(pname + child_name)
                if err:
                    self._error(
                        f"Invalid numeric cast {pname}.{child_name}: {err}",
                        loc=path.start,
                    )
                    return None
                return self._resolve_name(child_name)
            # check if it's a unit name first (file-level units)
            if pname in self.program.units:
                # ensure file unit is fully resolved (generic params detected)
                utype = self._ensure_file_unit_resolved(pname)
                if utype and utype.isgeneric:
                    # generic file unit accessed as dotted path without
                    # instantiation — must instantiate first
                    self._error(
                        f"Generic unit '{pname}' must be instantiated"
                        f" with type arguments before use",
                        loc=path.start,
                    )
                    return None
                if utype:
                    child = utype.children.get(path.child.name)
                    if child:
                        return child
                # fallback: demand-resolve the child
                t = self._resolve_unit_name(pname, path.child.name)
                if t:
                    return t
                return None
            # check if it's an inline unit name
            if (
                pname in self.unit_types
                and self.unit_types[pname].typetype == ZTypeType.UNIT
            ):
                parent_type = self.unit_types[pname]
                child = parent_type.children.get(path.child.name)
                if child:
                    return child
                return None
            # otherwise resolve parent as a name
            parent_type = self._resolve_name(pname)
        elif isinstance(path.parent, zast.DottedPath):
            parent_type = self._resolve_dotted_path(path.parent)
        if not parent_type:
            return None
        # check for .typedef — creates a marker detected by type resolvers
        child_name = path.child.name
        if child_name == "typedef":
            marker = _make_type("__typedef_marker", ZTypeType.GENERIC_PARAM)
            marker.parent = parent_type  # the base type being wrapped
            path.type = marker
            return marker
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
            path.type = gp
            return gp
        if child_name == "take" and parent_type.typetype not in (
            ZTypeType.PROTOCOL,
            ZTypeType.FACET,
        ):
            # .take returns the same type (ownership transfer)
            path.type = parent_type
            return parent_type
        if child_name == "borrow" and parent_type.typetype not in (
            ZTypeType.PROTOCOL,
            ZTypeType.FACET,
        ):
            # .borrow returns the same type (borrowed reference)
            path.type = parent_type
            return parent_type
        if child_name == "lock":
            # .lock is an alias for .borrow (borrowed reference / explicit lock)
            path.type = parent_type
            return parent_type
        if child_name == "private":
            # .private grants access to all members (friend access)
            path.type = parent_type
            return parent_type
        # numeric type casting: x.u32 where x is a numeric type
        _NUMERIC_NAMES = set(NUMERIC_RANGES) | {"f32", "f64", "f128"}
        if child_name in _NUMERIC_NAMES and parent_type.name in _NUMERIC_NAMES:
            target_type = self._resolve_name(child_name)
            if target_type:
                path.type = target_type
                return target_type
        # for unions/variants, store parent type on the path for construction detection
        if parent_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
            # resolve public name (may redirect renamed members)
            resolved_name = self._resolve_public_name(parent_type, child_name, path)
            child = parent_type.children.get(resolved_name)
            if not child:
                child = parent_type.children.get(child_name)
            if child:
                # public access check
                if self._is_non_public_access(parent_type, child_name, path):
                    self._error(
                        f"'{child_name}' is not public on type '{parent_type.name}'",
                        loc=path.start,
                    )
                    return None
                # non-subtype children (tag, :tag, methods) should not be
                # treated as union/variant subtype construction
                if (
                    child_name not in ("tag", ":tag")
                    and child.typetype != ZTypeType.FUNCTION
                ):
                    path.parent_tagged_type = parent_type
                return child
            return None
        # for data: .array method returns a new array of matching type/length
        if parent_type.typetype == ZTypeType.DATA and child_name == "array":
            elem_type = parent_type.children.get(":element_type")
            if elem_type:
                # count data elements (non-special keys)
                data_len = sum(
                    1
                    for k in parent_type.children
                    if not k.startswith(":") and k != "tag"
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
            arr_len = _array_length(parent_type)
            if arr_len is not None and idx >= arr_len:
                self._error(
                    f"Array index {idx} out of bounds for array of length {arr_len}",
                    loc=path.start,
                )
                return None
            elem_type = _array_element_type(parent_type)
            return elem_type
        # for str types: .string returns the string type directly (not the function)
        if _is_str_type(parent_type) and child_name == "string":
            return self._resolve_name("string")
        # for list types: .pop returns the element type directly (zero-arg method)
        if _is_list_type(parent_type) and child_name == "pop":
            return _list_element_type(parent_type)
        # for records/enums, look up child in children
        # resolve public name (may redirect renamed members)
        resolved_name = self._resolve_public_name(parent_type, child_name, path)
        child = parent_type.children.get(resolved_name)
        if not child:
            child = parent_type.children.get(child_name)
        if child:
            # null-hidden methods on typedefs
            if (
                parent_type.typedef_base
                and child.typetype == ZTypeType.NULL
                and child.name == "null"
            ):
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
            child = base.children.get(child_name)
            if child:
                return child
            base = base.typedef_base
        return None

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
        """Check if two types are compatible (identity, name match, or structural equiv for functions)."""
        if a is b:
            return True
        if a.name == b.name:
            return True
        if a.typetype == ZTypeType.FUNCTION and b.typetype == ZTypeType.FUNCTION:
            return self._function_types_equivalent(a, b)
        # str types are compatible with string (print, function params)
        if _is_str_type(a) and b.name == "string":
            return True
        # Typedef backward compat: a (actual) is a typedef wrapping b (expected)
        base = a.typedef_base
        while base is not None:
            if base is b or base.name == b.name:
                return True
            base = base.typedef_base
        return False

    def _function_types_equivalent(self, a: ZType, b: ZType) -> bool:
        """Check structural equivalence of two function types (same params + return)."""
        a_ret = a.return_type
        b_ret = b.return_type
        if (a_ret is None) != (b_ret is None):
            return False
        if a_ret and b_ret and a_ret.name != b_ret.name:
            return False
        a_params = [(k, v) for k, v in a.children.items() if not k.startswith(":")]
        b_params = [(k, v) for k, v in b.children.items() if not k.startswith(":")]
        if len(a_params) != len(b_params):
            return False
        for (ak, av), (bk, bv) in zip(a_params, b_params):
            if ak != bk or av.name != bv.name:
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
        # build cache key
        cache_key = (
            template_type.name,
            tuple(sorted((k, v.name) for k, v in generic_args.items())),
        )
        if cache_key in self._mono_cache:
            return self._mono_cache[cache_key]

        # check if this is a partial instantiation (some args are GENERIC_PARAM)
        is_partial = any(
            v.typetype == ZTypeType.GENERIC_PARAM for v in generic_args.values()
        )

        # constraint checking (skip for generic param args — checked at final instantiation)
        for param_name, concrete_type in generic_args.items():
            if concrete_type.typetype == ZTypeType.GENERIC_PARAM:
                continue
            # numeric generic params already validated in _resolve_numeric_generic_arg
            if param_name in template_type.numeric_generic_params:
                continue
            constraint = template_type.generic_params.get(param_name)
            if not constraint:
                continue
            # any.valtype / any.reftype constraints
            if constraint.name == "any.valtype":
                if not _is_valtype(concrete_type):
                    self._error(
                        f"Type '{concrete_type.name}' is not a value type; "
                        f"generic parameter '{param_name}' requires any.valtype"
                    )
                continue
            if constraint.name == "any.reftype":
                if _is_valtype(concrete_type):
                    self._error(
                        f"Type '{concrete_type.name}' is not a reference type; "
                        f"generic parameter '{param_name}' requires any.reftype"
                    )
                continue
            if constraint.name != "any":
                # constraint is a union: check concrete type matches a subtype
                if constraint.typetype == ZTypeType.UNION:
                    subtype_names = {
                        k
                        for k, v in constraint.children.items()
                        if not k.startswith(":")
                        and k != "tag"
                        and v.typetype != ZTypeType.FUNCTION
                        and v.typetype != ZTypeType.DATA
                        and v.typetype != ZTypeType.TAG
                        and v.typetype != ZTypeType.ENUM
                        and getattr(v, "generic_origin", None) != "tag"
                    }
                    if concrete_type.name not in subtype_names:
                        self._error(
                            f"Type '{concrete_type.name}' does not satisfy constraint "
                            f"'{constraint.name}' for generic parameter '{param_name}'"
                        )

        # build mangled name
        arg_names = [generic_args[k].name for k in template_type.generic_params]
        mangled = f"{template_type.name}_{'_'.join(arg_names)}"

        # create monomorphized type
        mono = _make_type(mangled, template_type.typetype)
        mono.generic_origin = template_type
        mono.generic_args = dict(generic_args)
        mono.is_valtype = template_type.is_valtype
        _set_destructor_metadata(mono)
        self._assign_cname_type(mono)

        # propagate numeric_generic_params for partial instantiation
        mono.numeric_generic_params = set(template_type.numeric_generic_params)

        # partial instantiation: result is still generic
        if is_partial:
            mono.isgeneric = True
            for param_name, arg_type in generic_args.items():
                if arg_type.typetype == ZTypeType.GENERIC_PARAM:
                    mono.generic_params[arg_type.name] = (
                        arg_type.parent if arg_type.parent else self.t_null
                    )
                    # propagate numeric-ness
                    if param_name in template_type.numeric_generic_params:
                        mono.numeric_generic_params.add(arg_type.name)

        # track which numeric params are referenced by children
        numeric_params_referenced: set[str] = set()

        # substitute generic params in children
        for child_name, child_type in template_type.children.items():
            if child_type.typetype == ZTypeType.GENERIC_PARAM:
                # replace with concrete type
                param_ref_name = child_type.name
                concrete = generic_args.get(param_ref_name)
                if concrete:
                    # numeric generic param: replace with constraint type, set default
                    if (
                        param_ref_name in template_type.numeric_generic_params
                        and concrete.numeric_value is not None
                    ):
                        numeric_params_referenced.add(param_ref_name)
                        constraint = template_type.generic_params[param_ref_name]
                        resolved_constraint = self._resolve_name(constraint.name)
                        if resolved_constraint:
                            mono.children[child_name] = resolved_constraint
                        else:
                            mono.children[child_name] = constraint
                        mono.param_defaults[child_name] = str(concrete.numeric_value)
                    else:
                        mono.children[child_name] = concrete
                else:
                    mono.children[child_name] = child_type
            elif (
                child_type.isgeneric
                and isinstance(child_type.generic_origin, ZType)
                and not is_partial
                and child_type.typetype != ZTypeType.UNIT
            ):
                # partially-instantiated non-unit child — resolve remaining generic params
                # (UNIT children are handled by _monomorphize_unit)
                child_args: dict[str, ZType] = {}
                for gp_name, gp_arg in child_type.generic_args.items():
                    if (
                        gp_arg.typetype == ZTypeType.GENERIC_PARAM
                        and gp_arg.name in generic_args
                    ):
                        child_args[gp_name] = generic_args[gp_arg.name]
                    else:
                        child_args[gp_name] = gp_arg
                child_defn = self._find_generic_defn(child_type.generic_origin)
                if child_defn:
                    mono.children[child_name] = self._monomorphize(
                        child_type.generic_origin, child_args, child_defn
                    )
                else:
                    mono.children[child_name] = child_type
            else:
                mono.children[child_name] = child_type

        # auto-synthesize fields for numeric params not referenced by any child
        if not is_partial:
            for nparam in template_type.numeric_generic_params:
                if nparam not in numeric_params_referenced:
                    concrete = generic_args.get(nparam)
                    if concrete and concrete.numeric_value is not None:
                        constraint = template_type.generic_params[nparam]
                        resolved_constraint = self._resolve_name(constraint.name)
                        if resolved_constraint:
                            mono.children[nparam] = resolved_constraint
                        else:
                            mono.children[nparam] = constraint
                        mono.param_defaults[nparam] = str(concrete.numeric_value)

        # recompute is_valtype based on concrete types
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

        # for nullable-ptr option (monomorphized option union): mark as nullable ptr
        if template_type.typetype == ZTypeType.UNION and template_type.name == "option":
            mono.is_nullable_ptr = True

        # for unions: rebuild tag enum with the monomorphized name
        if template_type.typetype == ZTypeType.UNION:
            subtype_names = [
                k
                for k in mono.children
                if not k.startswith(":")
                and k != "tag"
                and mono.children[k].typetype != ZTypeType.FUNCTION
                and mono.children[k].typetype != ZTypeType.DATA
                and mono.children[k].typetype != ZTypeType.TAG
                and mono.children[k].typetype != ZTypeType.ENUM
                and getattr(mono.children[k], "generic_origin", None) != "tag"
            ]
            tag_type = _make_type(f"{mangled}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                tag_type.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            mono.children[":tag"] = tag_type
            # generate data type for .tag access
            gen_data = _make_type(f"{mangled}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                gen_data.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.generic_origin = "tag"
            gen_data.children["tag"] = gen_tag
            mono.children["tag"] = gen_data

        # for variants: rebuild tag enum with the monomorphized name
        if template_type.typetype == ZTypeType.VARIANT:
            subtype_names = [
                k
                for k in mono.children
                if not k.startswith(":")
                and k != "tag"
                and mono.children[k].typetype != ZTypeType.FUNCTION
                and mono.children[k].typetype != ZTypeType.DATA
                and mono.children[k].typetype != ZTypeType.TAG
                and mono.children[k].typetype != ZTypeType.ENUM
                and getattr(mono.children[k], "generic_origin", None) != "tag"
            ]
            tag_type = _make_type(f"{mangled}:tag", ZTypeType.ENUM)
            for i, sname in enumerate(subtype_names):
                tag_type.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            mono.children[":tag"] = tag_type
            gen_data = _make_type(f"{mangled}:tag:data", ZTypeType.DATA)
            gen_data.is_valtype = False
            for i, sname in enumerate(subtype_names):
                gen_data.children[sname] = _make_type(str(i), ZTypeType.RECORD)
            gen_tag = _make_type("tag__i64", ZTypeType.RECORD, parent=gen_data)
            gen_tag.is_valtype = True
            gen_tag.generic_origin = "tag"
            gen_data.children["tag"] = gen_tag
            mono.children["tag"] = gen_data

        # for arrays: validate element type, synthesize get/set/length
        if _is_array_type(mono) and not is_partial:
            elem_type = _array_element_type(mono)
            arr_len = _array_length(mono)
            if elem_type and not _is_valtype(elem_type):
                self._error(
                    f"Array element type '{elem_type.name}' is not a value type; "
                    f"arrays require valtype elements"
                )
            # synthesize .length constant
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            mono.children["length"] = length_type
            if arr_len is not None:
                mono.param_defaults["length"] = str(arr_len)
            # synthesize .get method: function {i: i64} out <elem>
            if elem_type:
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                get_type.children["i"] = self._resolve_name("i64") or self.t_null
                get_type.return_type = elem_type
                mono.children["get"] = get_type
                # synthesize .set method: function {i: i64, val: <elem>} out <elem>
                set_type = _make_type(f"{mangled}.set", ZTypeType.FUNCTION)
                set_type.children["i"] = self._resolve_name("i64") or self.t_null
                set_type.children["val"] = elem_type
                set_type.return_type = elem_type
                mono.children["set"] = set_type

        # for str types: set valtype, remove from field, synthesize length/capacity/string
        if _is_str_type(mono) and not is_partial:
            mono.is_valtype = True
            _set_destructor_metadata(mono)
            str_cap = _str_capacity(mono)
            # remove 'from' from children — it's a constructor arg, not a persistent field
            mono.children.pop("from", None)
            # synthesize .length field (runtime, u64)
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            mono.children["length"] = length_type
            # synthesize .capacity constant (compile-time)
            cap_type = _make_type("u64", ZTypeType.RECORD)
            cap_type.is_valtype = True
            mono.children["capacity"] = cap_type
            if str_cap is not None:
                mono.param_defaults["capacity"] = str(str_cap)
            # synthesize .string method: function {} out string
            string_method = _make_type(f"{mangled}.string", ZTypeType.FUNCTION)
            string_method.return_type = self._resolve_name("string") or self.t_null
            mono.children["string"] = string_method

        # for list types: set reftype, synthesize methods
        if _is_list_type(mono) and not is_partial:
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            elem_type = _list_element_type(mono)
            t_u64 = self._resolve_name("u64") or self.t_null
            # synthesize .length field (runtime, u64)
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            mono.children["length"] = length_type
            # synthesize .capacity field (runtime, u64)
            cap_type = _make_type("u64", ZTypeType.RECORD)
            cap_type.is_valtype = True
            mono.children["capacity"] = cap_type
            if elem_type:
                # synthesize .append method: function {from: <elem>}
                append_type = _make_type(f"{mangled}.append", ZTypeType.FUNCTION)
                append_type.children["from"] = elem_type
                append_type.param_ownership["from"] = ZParamOwnership.TAKE
                mono.children["append"] = append_type
                # synthesize .insert method: function {from: <elem> at: u64}
                insert_type = _make_type(f"{mangled}.insert", ZTypeType.FUNCTION)
                insert_type.children["from"] = elem_type
                insert_type.children["at"] = t_u64
                insert_type.param_ownership["from"] = ZParamOwnership.TAKE
                mono.children["insert"] = insert_type
                # synthesize .extend method: function {from: list_T}
                extend_type = _make_type(f"{mangled}.extend", ZTypeType.FUNCTION)
                extend_type.children["from"] = mono
                extend_type.param_ownership["from"] = ZParamOwnership.TAKE
                mono.children["extend"] = extend_type
                # synthesize .get method: function {i: u64} out <elem>
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                get_type.children["i"] = t_u64
                get_type.return_type = elem_type
                get_type.return_ownership = ZParamOwnership.BORROW
                mono.children["get"] = get_type
                # synthesize .set method: function {i: u64 val: <elem>} out <elem>
                set_type = _make_type(f"{mangled}.set", ZTypeType.FUNCTION)
                set_type.children["i"] = t_u64
                set_type.children["val"] = elem_type
                set_type.return_type = elem_type
                set_type.param_ownership["val"] = ZParamOwnership.TAKE
                mono.children["set"] = set_type
                # synthesize .pop method: function {} out <elem>
                pop_type = _make_type(f"{mangled}.pop", ZTypeType.FUNCTION)
                pop_type.return_type = elem_type
                mono.children["pop"] = pop_type

        # for map types: set reftype, synthesize methods
        if _is_map_type(mono) and not is_partial:
            mono.is_valtype = False
            _set_destructor_metadata(mono)
            key_type = _map_key_type(mono)
            value_type = _map_value_type(mono)
            t_u64 = self._resolve_name("u64") or self.t_null
            t_bool = self._resolve_name("bool") or self.t_null
            # synthesize .length field (runtime, u64)
            length_type = _make_type("u64", ZTypeType.RECORD)
            length_type.is_valtype = True
            mono.children["length"] = length_type
            # synthesize .capacity field (runtime, u64)
            cap_type = _make_type("u64", ZTypeType.RECORD)
            cap_type.is_valtype = True
            mono.children["capacity"] = cap_type
            if key_type and value_type:
                # synthesize .set method: function {key: K value: V}
                set_type = _make_type(f"{mangled}.set", ZTypeType.FUNCTION)
                set_type.children["key"] = key_type
                set_type.children["value"] = value_type
                set_type.param_ownership["key"] = ZParamOwnership.TAKE
                set_type.param_ownership["value"] = ZParamOwnership.TAKE
                mono.children["set"] = set_type
                # synthesize .get method: function {key: K} out option/optionval of: V
                get_type = _make_type(f"{mangled}.get", ZTypeType.FUNCTION)
                get_type.children["key"] = key_type
                if _is_valtype(value_type):
                    opt_template = self._resolve_name("optionval")
                else:
                    opt_template = self._resolve_name("option")
                if opt_template and opt_template.isgeneric:
                    opt_defn = self._find_generic_defn(opt_template)
                    if opt_defn:
                        opt_mono = self._monomorphize(
                            opt_template, {"t": value_type}, opt_defn
                        )
                        get_type.return_type = opt_mono
                mono.children["get"] = get_type
                # synthesize .delete method: function {key: K} out bool
                delete_type = _make_type(f"{mangled}.delete", ZTypeType.FUNCTION)
                delete_type.children["key"] = key_type
                delete_type.return_type = t_bool
                mono.children["delete"] = delete_type
                # synthesize .has method: function {key: K} out bool
                has_type = _make_type(f"{mangled}.has", ZTypeType.FUNCTION)
                has_type.children["key"] = key_type
                has_type.return_type = t_bool
                mono.children["has"] = has_type

        # for classes: rebuild meta.create for the monomorphized class
        if (
            template_type.typetype == ZTypeType.CLASS
            and not _is_list_type(mono)
            and not _is_map_type(mono)
        ):
            is_func_names = set()
            if isinstance(defn, zast.Class):
                is_func_names = set(defn.functions.keys())
            create_type = self._make_meta_create_type(mangled, mono, is_func_names)
            mono.children[":meta.create"] = create_type
            if "create" not in mono.children:
                mono.children["create"] = create_type

        # for monomorphized units: all UNIT-specific work in one method
        if not is_partial and isinstance(defn, zast.Unit):
            self._monomorphize_unit(mono, mangled, template_type, generic_args, defn)

        # clone, typecheck, hash, and dedup method bodies for non-partial monos
        cloned_defn = defn
        if not is_partial and isinstance(
            defn, (zast.Class, zast.Record, zast.Union, zast.Variant)
        ):
            # collect method sources from the template definition
            method_sources: list[tuple[str, zast.Function, str]] = []
            for mname, mfunc in defn.as_functions.items():
                if mfunc.body:
                    method_sources.append((mname, mfunc, "as_functions"))
            for mname, mfunc in defn.functions.items():
                if mfunc.body:
                    method_sources.append((mname, mfunc, "functions"))

            # build cloned method dict for each source
            cloned_methods: dict[str, zast.Function] = {}
            for mname, mfunc, source_dict in method_sources:
                qualified = f"{mangled}.{mname}"
                cloned = clone_function(mfunc)

                # push mono type onto resolving stack so 'this' resolves
                self._resolving.append((mangled, mono))
                # push generic context so body checking resolves generic params
                self._generic_context.append({k: v for k, v in generic_args.items()})
                self._check_function_body(qualified, cloned)
                self._generic_context.pop()
                self._resolving.pop()

                # hash and dedup
                func_hash = zasthash.hash_function(cloned)
                if func_hash in self._func_hashes:
                    canonical_name, canonical_func = self._func_hashes[func_hash]
                    self._func_aliases[qualified] = canonical_name
                    cloned_methods[mname] = canonical_func
                else:
                    self._func_hashes[func_hash] = (qualified, cloned)
                    cloned_methods[mname] = cloned

            # store cloned methods for emitter use
            self._cloned_methods[mangled] = cloned_methods

        # cache and register
        self._mono_cache[cache_key] = mono
        if not is_partial:
            self._mono_types.append((mono, cloned_defn))
            # register in _resolved so the emitter can find it
            for unitname in self.program.units:
                key = f"{unitname}.{mangled}"
                self._resolved[key] = mono
                break

        return mono

    def _substitute_func_type(
        self,
        name: str,
        func_type: ZType,
        args: dict[str, ZType],
    ) -> ZType:
        """Create a new function type with generic params substituted."""
        new_func = _make_type(name, ZTypeType.FUNCTION)
        for pk, pv in func_type.children.items():
            if pv.typetype == ZTypeType.GENERIC_PARAM and pv.name in args:
                new_func.children[pk] = args[pv.name]
            else:
                new_func.children[pk] = pv
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
        for child_name, child_type in list(mono.children.items()):
            if child_type.typetype == ZTypeType.FUNCTION:
                new_func = self._substitute_func_type(
                    f"{mangled}.{child_name}", child_type, generic_args
                )
                mono.children[child_name] = new_func
                for unitname_key in self.program.units:
                    self._resolved[f"{unitname_key}.{mangled}.{child_name}"] = new_func
                    break

        # 2. recursively partially instantiate nested generic subunits
        self._partially_instantiate_subunits(mono, mangled, generic_args)

        # 3. register and clone function bodies
        self.unit_types[mangled] = mono
        cloned_methods: dict[str, zast.Function] = {}
        all_args = dict(getattr(template_type, "generic_args", {}) or {})
        all_args.update(generic_args)
        for dname, ddefn in defn.body.items():
            if dname in template_type.generic_params:
                continue
            if isinstance(ddefn, zast.Function) and ddefn.body:
                qualified = f"{mangled}.{dname}"
                cloned = clone_function(ddefn)
                self.symtab.push(f"unitgeneric:{mangled}")
                for gp_name, concrete_type in all_args.items():
                    self.symtab.define(gp_name, concrete_type)
                self._check_function_body(qualified, cloned)
                self.symtab.pop()
                func_hash = zasthash.hash_function(cloned)
                if func_hash in self._func_hashes:
                    canonical_name, canonical_func = self._func_hashes[func_hash]
                    self._func_aliases[qualified] = canonical_name
                    cloned_methods[dname] = canonical_func
                else:
                    self._func_hashes[func_hash] = (qualified, cloned)
                    cloned_methods[dname] = cloned
        if cloned_methods:
            self._cloned_methods[mangled] = cloned_methods

    def _partially_instantiate_subunits(
        self, parent: ZType, parent_name: str, args: dict[str, ZType]
    ) -> None:
        """Recursively partially instantiate nested generic subunits.

        For each generic UNIT child, substitute the parent's concrete args
        into its function children while keeping its own generic params.
        Recurses to arbitrary depth.
        """
        for child_name, child_type in list(parent.children.items()):
            if child_type.typetype != ZTypeType.UNIT or not child_type.isgeneric:
                continue
            sub_name = f"{parent_name}.{child_name}"
            sub_unit = _make_type(sub_name, ZTypeType.UNIT)
            sub_unit.generic_origin = child_type
            sub_unit.generic_args = dict(getattr(child_type, "generic_args", {}) or {})
            sub_unit.generic_args.update(args)
            for gp_name, gp_constraint in child_type.generic_params.items():
                if gp_name not in args:
                    sub_unit.generic_params[gp_name] = gp_constraint
                    sub_unit.isgeneric = True
            for ck, cv in child_type.children.items():
                if cv.typetype == ZTypeType.FUNCTION:
                    sub_unit.children[ck] = self._substitute_func_type(
                        f"{sub_name}.{ck}", cv, args
                    )
                else:
                    sub_unit.children[ck] = cv
            parent.children[child_name] = sub_unit
            self.unit_types[sub_name] = sub_unit
            self._partially_instantiate_subunits(sub_unit, sub_name, args)

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
            if origin and isinstance(origin, ZType):
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
            if isinstance(ddefn, zast.Unit):
                result = self._search_body_recursive(ddefn.body, name)
                if result is not None:
                    return result
        return None

    def _infer_generic_union_construction(
        self, template: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Infer generic args for union subtype construction and monomorphize."""
        subtype_name = (
            call.callable.child.name
            if isinstance(call.callable, zast.DottedPath)
            else None
        )
        if not subtype_name:
            return None

        generic_args: dict[str, ZType] = {}

        # check if this is a null subtype with explicit type arg
        subtype_child = template.children.get(subtype_name)
        is_null_subtype = (
            subtype_child is not None
            and subtype_child.typetype == ZTypeType.RECORD
            and subtype_child.name == "null"
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
                arg_type = self._check_operation(value_arg.valtype)
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
        """Try to resolve an operation as a type reference (for explicit type args)."""
        if isinstance(op, zast.AtomId):
            name = op.name
            if not _is_numeric_id(name):
                t = self._resolve_name(name)
                if t and t.typetype in (
                    ZTypeType.RECORD,
                    ZTypeType.UNION,
                    ZTypeType.CLASS,
                    ZTypeType.VARIANT,
                    ZTypeType.ENUM,
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
        for child_name, child_type in template.children.items():
            if child_name.startswith(":"):
                continue
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

            val_type = self._check_operation(arg.valtype)

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

    # ---- Function body type checking ----

    def _check_function_body(self, name: str, func: zast.Function) -> None:
        if not func.body:
            return
        self.symtab.push(f"function:{name}")

        # save/restore ownership context
        prev_func_ownership = self._current_func_ownership
        prev_func_return_ownership = self._current_func_return_ownership
        self._current_func_ownership = dict(func.param_ownership)
        self._current_func_return_ownership = func.return_ownership

        for pname, ppath in func.parameters.items():
            pt = self._resolve_typeref(ppath)
            if pt:
                # determine parameter ownership from annotations
                param_own = func.param_ownership.get(pname)
                if param_own == ZParamOwnership.TAKE:
                    ownership = ZOwnership.OWNED
                else:
                    # default: borrow for reftypes, owned for valtypes
                    if _is_valtype(pt):
                        ownership = ZOwnership.OWNED
                    else:
                        ownership = ZOwnership.BORROWED
                var = ZVariable(ztype=pt, ownership=ownership, named=ZNaming.NAMED)
                self.symtab.define_var(pname, var)

        # set expected return type for return statement checking
        prev_return_type = self._current_return_type
        if func.returntype:
            self._current_return_type = self._resolve_typeref(func.returntype)
        else:
            self._current_return_type = None
        self._check_statement(func.body)

        # implicit return validation: last expression type must match 'out'
        if self._current_return_type and func.body.statements:
            last = func.body.statements[-1]
            last_type = last.type if hasattr(last, "type") else None
            if last_type is not None and last_type.name != "never":
                if not self._types_compatible(last_type, self._current_return_type):
                    self._error(
                        f"implicit return type '{last_type.name}' does not match "
                        f"declared return type '{self._current_return_type.name}'",
                        loc=last.start,
                        err=ERR.TYPEERROR,
                    )

        self._current_return_type = prev_return_type
        self._current_func_ownership = prev_func_ownership
        self._current_func_return_ownership = prev_func_return_ownership
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
        # propagate type to statement line wrapper
        if inner.type is not None:
            sline.type = inner.type

    # Phase 48c will add typed markers for null/never so these checks
    # can use typetype instead of name strings.
    _NON_RUNTIME_TYPES = frozenset({"null", "never"})

    def _check_non_runtime_type(self, t: ZType, context: str, loc: Token) -> bool:
        """Check if a type is non-runtime (null/never). Returns True if error emitted."""
        if t.name == "null":
            self._error(
                f"'null' cannot be used as {context} — null must be wrapped "
                "in a union or variant (eg. option.none)",
                loc=loc,
            )
            return True
        if t.name == "never":
            self._error(
                f"'never' cannot be used as {context} — 'never' represents "
                "a non-completing expression (return, break, continue)",
                loc=loc,
            )
            return True
        return False

    def _check_assignment(self, assign: zast.Assignment) -> None:
        self._pending_borrow_lock = None
        self._pending_private_access = False
        t = self._check_expression(assign.value)
        self._check_exhaustive_if(assign.value)
        if t and self._check_non_runtime_type(t, "a value", assign.start):
            return
        if t:
            # check if this assignment is from a .borrow call
            borrow_target = self._pending_borrow_lock
            self._pending_borrow_lock = None
            # check if this assignment is from a .private expression
            private_access = self._pending_private_access
            self._pending_private_access = False

            if borrow_target:
                # the new variable is borrowed and holds an exclusive lock on the target
                var = ZVariable(
                    ztype=t, ownership=ZOwnership.BORROWED, named=ZNaming.NAMED
                )
                var.is_private_access = private_access
                self.symtab.define_var(assign.name, var)
                err = self.symtab.try_lock(
                    borrow_target, ZLockState.EXCLUSIVE, assign.name
                )
                if err:
                    self._error(err, loc=assign.start)
            else:
                # new local variables are always owned
                var = ZVariable(
                    ztype=t, ownership=ZOwnership.OWNED, named=ZNaming.NAMED
                )
                var.is_private_access = private_access
                self.symtab.define_var(assign.name, var)
            assign.type = t

    def _check_reassignment(self, reassign: zast.Reassignment) -> None:
        existing = self._check_path(reassign.topath)
        new_t = self._check_expression(reassign.value)
        self._check_exhaustive_if(reassign.value)
        if existing and new_t and not self._types_compatible(existing, new_t):
            self._error(
                f"Cannot assign {new_t.name} to variable of type {existing.name}",
                loc=reassign.start,
            )

        # ownership check: reftype fields can only be changed with swap
        if existing and not _is_valtype(existing):
            if isinstance(reassign.topath, zast.DottedPath):
                self._error(
                    f"Cannot reassign reftype field '{reassign.topath.child.name}' "
                    f"with '='; use 'swap' instead",
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

        # ownership check: swap arguments must be owned (or parent must be owned for dotted)
        self._check_swap_ownership(swap.lhs, "left", swap.start)
        self._check_swap_ownership(swap.rhs, "right", swap.start)

    def _check_swap_ownership(self, path: zast.Path, side: str, loc: Token) -> None:
        """Check that swap argument is owned (or parent is owned for dotted paths)."""
        if isinstance(path, zast.AtomId):
            var = self.symtab.lookup_var(path.name)
            if var and var.ownership == ZOwnership.BORROWED:
                self._error(
                    f"Cannot swap {side} operand '{path.name}': variable is borrowed",
                    loc=loc,
                )
        elif isinstance(path, zast.DottedPath):
            # for dotted paths, check that the root parent is owned
            root = path
            while isinstance(root, zast.DottedPath):
                root = root.parent
            if isinstance(root, zast.AtomId):
                var = self.symtab.lookup_var(root.name)
                if var and var.ownership == ZOwnership.BORROWED:
                    self._error(
                        f"Cannot swap {side} operand: parent '{root.name}' is borrowed",
                        loc=loc,
                    )

    def _check_expression(self, expr: zast.Expression) -> Optional[ZType]:
        inner = expr.expression
        t: Optional[ZType] = None
        if isinstance(inner, zast.Call):
            t = self._check_call(inner)
        elif isinstance(inner, zast.If):
            t = self._check_if(inner)
        elif isinstance(inner, zast.For):
            t = self._check_for(inner)
        elif isinstance(inner, zast.Do):
            self._check_statement(inner.statement)
            last_type = self._last_statement_type(inner.statement)
            if isinstance(last_type, ZType) and last_type is not self._NORETURN:
                t = last_type
                inner.type = t
            else:
                t = self.t_null
        elif isinstance(inner, zast.With):
            t = self._check_with(inner)
        elif isinstance(inner, zast.Case):
            t = self._check_case(inner)
        elif isinstance(inner, zast.Data):
            t = None
        elif isinstance(inner, zast.Operation):
            t = self._check_operation(inner)
            # propagate const_value from inner operation to expression wrapper
            if inner.const_value is not None:
                expr.const_value = inner.const_value
        if t is not None:
            expr.type = t
        return t

    def _check_operation(self, op: zast.Operation) -> Optional[ZType]:
        if isinstance(op, zast.BinOp):
            return self._check_binop(op)
        if isinstance(op, zast.Path):
            t = self._check_path(op)
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
                if isinstance(op, zast.DottedPath):
                    type_desc = f"{t.name}.{op.child.name}"
                self._error(
                    f"cannot infer type arguments for generic type '{type_desc}'",
                    loc=op.start,
                )
                return None
            return t
        return None

    def _check_path(self, path: zast.Path) -> Optional[ZType]:
        if isinstance(path, zast.Expression):
            result = self._check_expression(path)
            if result and not path.type:
                path.type = result
            return result
        if isinstance(path, zast.AtomString):
            self._check_string_interpolation(path)
            path.type = self._resolve_name("string")
            return path.type
        if isinstance(path, zast.AtomId):
            return self._check_atomid(path)
        if isinstance(path, zast.DottedPath):
            return self._check_dotted_path(path)
        return None

    def _check_dotted_path(self, path: zast.DottedPath) -> Optional[ZType]:
        """Check a dotted path, handling .take and .borrow compiler methods."""
        child_name = path.child.name

        # handle .take compiler method (but not protocol/typedef.take constructor)
        if child_name == "take":
            parent_type = self._check_path(path.parent)
            if parent_type:
                # protocol/facet/typedef.take is a constructor, not ownership transfer
                if parent_type.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                    pass  # fall through to normal child lookup below
                elif parent_type.typedef_base is not None:
                    pass  # fall through to normal child lookup below
                else:
                    # check if parent is a unit-level definition (function or spec)
                    if parent_type.typetype == ZTypeType.FUNCTION and isinstance(
                        path.parent, zast.AtomId
                    ):
                        defn = self._lookup_definition(path.parent.name)
                        if isinstance(defn, zast.Function):
                            if defn.body is None and not defn.is_native:
                                # spec — no value to take
                                self._error(
                                    f"Cannot take spec '{path.parent.name}': "
                                    f"specs have no value; use a function name",
                                    loc=path.start,
                                )
                                return parent_type
                            # real function — immutable program text, no invalidation
                            path.type = parent_type
                            return parent_type

                    # .take invalidates the source name (variable)
                    if isinstance(path.parent, zast.AtomId):
                        var = self.symtab.lookup_var(path.parent.name)
                        if var and var.ownership == ZOwnership.BORROWED:
                            self._error(
                                f"Cannot take ownership of borrowed variable "
                                f"'{path.parent.name}'",
                                loc=path.start,
                            )
                        else:
                            # release any locks held by this variable before invalidating
                            self.symtab.release_held_locks(path.parent.name)
                            take_loc = (
                                (path.start.lineno, path.start.colno, path.start.fsno)
                                if path.start
                                else None
                            )
                            self.symtab.invalidate(path.parent.name, loc=take_loc)
                    path.type = parent_type
                    return parent_type

        # handle .borrow compiler method (but not protocol/typedef.borrow constructor)
        if child_name == "borrow":
            parent_type = self._check_path(path.parent)
            if parent_type:
                # protocol/facet/typedef.borrow is a constructor, not ownership borrow
                if parent_type.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET):
                    pass  # fall through to normal child lookup below
                elif parent_type.typedef_base is not None:
                    pass  # fall through to normal child lookup below
                else:
                    # .borrow takes an exclusive lock on the receiver
                    if isinstance(path.parent, zast.AtomId):
                        receiver_name = path.parent.name
                        # the borrow result will be assigned to a name by _check_assignment;
                        # for now, use a placeholder holder that will be updated
                        self._pending_borrow_lock = receiver_name
                    path.type = parent_type
                    return parent_type

        # handle .lock compiler method (alias for .borrow)
        if child_name == "lock":
            parent_type = self._check_path(path.parent)
            if parent_type:
                if isinstance(path.parent, zast.AtomId):
                    self._pending_borrow_lock = path.parent.name
                path.type = parent_type
            return parent_type

        # handle .private (friend access)
        if child_name == "private":
            parent_type = self._check_path(path.parent)
            if parent_type:
                # enforce: only internal access can use .private
                if not self._is_internal_access(parent_type, path):
                    # also allow if the variable itself has private access
                    # (chained friend: it.items.private where items is bag.private)
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
                path.type = parent_type
            return parent_type

        # .each on integer types: synthesize iteration method
        _INTEGER_TYPES = {
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
        }
        if child_name == "each":
            parent_type = self._check_path(path.parent)
            if parent_type and parent_type.name in _INTEGER_TYPES:
                each_fn = _make_type(f"{parent_type.name}.each", ZTypeType.FUNCTION)
                each_fn.children["from"] = parent_type
                optionval_template = self._resolve_name("optionval")
                if optionval_template and optionval_template.isgeneric:
                    optionval_defn = self._find_generic_defn(optionval_template)
                    if optionval_defn:
                        optionval_mono = self._monomorphize(
                            optionval_template, {"t": parent_type}, optionval_defn
                        )
                        each_fn.return_type = optionval_mono
                path.type = each_fn
                return each_fn

        # numeric dotted path: 0.u32, 42.i8, 0xff.u16
        if isinstance(path.parent, zast.AtomId) and _is_numeric_id(path.parent.name):
            child_name = path.child.name
            pname = path.parent.name
            _, _, err = parse_number(pname + child_name)
            if err:
                self._error(
                    f"Invalid numeric cast {pname}.{child_name}: {err}", loc=path.start
                )
                return None
            t = self._resolve_name(child_name)
            if t:
                path.type = t
            return t

        # regular dotted path resolution
        # ensure parent type is set for emitter (needed for class -> vs . dispatch)
        if isinstance(path.parent, zast.AtomId):
            parent_type = self._resolve_name(path.parent.name)
            if parent_type:
                path.parent.type = parent_type
        elif isinstance(path.parent, zast.DottedPath):
            self._check_dotted_path(path.parent)
        t = self._resolve_dotted_path(path)
        if t:
            path.type = t
            # protocol/facet borrow: lock the source variable
            if t.typetype in (ZTypeType.PROTOCOL, ZTypeType.FACET) and isinstance(
                path.parent, zast.AtomId
            ):
                self._pending_borrow_lock = path.parent.name
            # if this is a union subtype reference (null subtype used as value),
            # the type should be the parent union type
            if path.parent_tagged_type:
                path.type = path.parent_tagged_type
                return path.parent_tagged_type
        return t

    def _check_string_interpolation(self, atom: zast.AtomString) -> None:
        for part in atom.stringparts:
            if isinstance(part, zast.Expression):
                self._check_expression(part)
                self._check_exhaustive_if(part)

    def _check_atomid(self, atom: zast.AtomId) -> Optional[ZType]:
        name = atom.name
        if _is_numeric_id(name):
            t = self._resolve_numeric(name, loc=atom.start)
            if t:
                atom.type = t
                # constant folding: set const_value for integer literals
                _, value, err = parse_number(name)
                if not err and isinstance(value, int):
                    atom.const_value = value
            return t

        t = self._resolve_name(name)
        if t:
            atom.type = t
            # constant folding: propagate const_value for true/false literals
            if name == "true":
                atom.const_value = True
            elif name == "false":
                atom.const_value = False
            else:
                # propagate const_value from named constants
                defn = self._lookup_definition(name)
                if (
                    defn is not None
                    and hasattr(defn, "const_value")
                    and defn.const_value is not None
                ):
                    atom.const_value = defn.const_value
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

    def _check_call(self, call: zast.Call) -> Optional[ZType]:
        callee_type = self._check_path(call.callable)
        if not callee_type:
            return None

        # handle return statement: check expression type against function return type
        if callee_type.name == "return" and callee_type.typetype == ZTypeType.FUNCTION:
            call.call_kind = zast.CallKind.RETURN
            return self._check_return_call(call)

        # handle union/variant subtype construction: dotted path parent is a tagged type
        # (must be before record/class checks since subtypes may be records)
        if (
            isinstance(call.callable, zast.DottedPath)
            and call.callable.parent_tagged_type
        ):
            parent_tagged = call.callable.parent_tagged_type

            # generic union/variant subtype construction
            if parent_tagged.isgeneric and parent_tagged.typetype in (
                ZTypeType.UNION,
                ZTypeType.VARIANT,
            ):
                mono_type = self._infer_generic_union_construction(parent_tagged, call)
                if mono_type:
                    call.type = mono_type
                    call.call_kind = zast.CallKind.UNION_CREATE
                    # update the parent_tagged_type to point to the monomorphized type
                    call.callable.parent_tagged_type = mono_type
                    return mono_type
                return None  # error already emitted in inference method

            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = parent_tagged
            call.call_kind = zast.CallKind.UNION_CREATE
            return parent_tagged

        # callable object dispatch: variable with a 'call' method
        # must be before construction checks — a variable of record/class type
        # with a 'call' method should dispatch to call, not construct
        callee_is_var = isinstance(call.callable, zast.AtomId) and (
            self.symtab.lookup_var(call.callable.name) is not None
        )
        if callee_is_var and callee_type.typetype != ZTypeType.FUNCTION:
            call_method = callee_type.children.get("call")
            if call_method and call_method.typetype == ZTypeType.FUNCTION:
                # redirect to the 'call' method
                call.call_kind = zast.CallKind.CALLABLE
                call.callable_type_name = callee_type.name
                callee_type = call_method
                call.callable.type = call_method
                # fall through to function call checking below

        # handle record construction: calling a record type creates an instance
        if callee_type.typetype == ZTypeType.RECORD:
            # generic record construction
            if callee_type.isgeneric:
                mono_type = self._infer_generic_record_construction(callee_type, call)
                if mono_type:
                    call.type = mono_type
                    call.callable.type = mono_type
                    call.call_kind = zast.CallKind.RECORD_CREATE
                    return mono_type
                return None  # error already emitted
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = callee_type
            call.call_kind = zast.CallKind.RECORD_CREATE
            return callee_type

        # handle box construction: box from: val (system box only — empty class body)
        if (
            callee_type.typetype == ZTypeType.CLASS
            and callee_type.isgeneric
            and callee_type.name == "box"
            and "t" in callee_type.generic_params
            and not callee_type.children
        ):
            return self._check_box_construction(call, callee_type)

        # handle class construction: calling a class type creates a new owned instance
        if callee_type.typetype == ZTypeType.CLASS:
            if callee_type.isgeneric:
                mono_type = self._infer_generic_record_construction(callee_type, call)
                if mono_type:
                    call.type = mono_type
                    call.callable.type = mono_type
                    call.call_kind = zast.CallKind.CLASS_CREATE
                    return mono_type
                return None
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = callee_type
            call.call_kind = zast.CallKind.CLASS_CREATE
            return callee_type

        # handle union construction: union.subtype expr
        if callee_type.typetype == ZTypeType.UNION:
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = callee_type
            call.call_kind = zast.CallKind.UNION_CREATE
            return callee_type

        # generic unit instantiation: (mathops t: i64) → monomorphized unit
        if callee_type.typetype == ZTypeType.UNIT and callee_type.isgeneric:
            mono = self._resolve_typeref_call(call)
            if mono:
                call.type = mono
                call.call_kind = zast.CallKind.UNIT_INSTANTIATE
                return mono
            return None

        if callee_type.typetype != ZTypeType.FUNCTION:
            self._error(
                f"Cannot call non-function type: {callee_type.name}",
                loc=call.start,
            )
            return None

        # protocol/typedef .create/take/borrow from: expr
        if (
            callee_type.typetype == ZTypeType.FUNCTION
            and isinstance(call.callable, zast.DottedPath)
            and call.callable.child.name in ("create", "take", "borrow")
        ):
            parent_type = getattr(call.callable.parent, "type", None)
            if parent_type and parent_type.typetype == ZTypeType.PROTOCOL:
                if call.callable.child.name == "borrow":
                    call.call_kind = zast.CallKind.PROTOCOL_BORROW
                    return self._check_protocol_borrow(parent_type, call)
                call.call_kind = zast.CallKind.PROTOCOL_CREATE
                return self._check_protocol_create(parent_type, call)
            if parent_type and parent_type.typetype == ZTypeType.FACET:
                if call.callable.child.name == "borrow":
                    call.call_kind = zast.CallKind.FACET_BORROW
                    return self._check_protocol_borrow(parent_type, call)
                call.call_kind = zast.CallKind.FACET_CREATE
                return self._check_protocol_create(parent_type, call)
            if parent_type and parent_type.typedef_base is not None:
                if call.callable.child.name == "borrow":
                    call.call_kind = zast.CallKind.TYPEDEF_BORROW
                    return self._check_typedef_borrow(parent_type, call)
                call.call_kind = zast.CallKind.TYPEDEF_CREATE
                return self._check_typedef_create(parent_type, call)

        # parameter types (skip special entries like :tag, :meta.create)
        params = [
            (k, v) for k, v in callee_type.children.items() if not k.startswith(":")
        ]

        # for callable dispatch, skip the 'this' parameter (first param of call method)
        # — the receiver is passed implicitly
        if call.call_kind == zast.CallKind.CALLABLE and params:
            params = params[1:]

        # check for reftype aliasing: same reftype arg passed twice
        reftype_args: dict[str, Token] = {}

        # lock tracking: accumulate locks taken during this call
        # each entry: (target_name, holder_placeholder, param_name)
        call_locks: List[Tuple[str, str, Optional[str]]] = []

        for i, arg in enumerate(call.arguments):
            arg_type = self._check_operation(arg.valtype)
            self._pending_borrow_lock = None  # clear protocol borrow for call args

            # reftype aliasing check
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

            if arg_type and arg.name and params:
                # named argument: match by parameter name
                matched = None
                for pname, ptype in params:
                    if pname == arg.name:
                        matched = ptype
                        break
                if matched:
                    if not self._types_compatible(arg_type, matched):
                        self._error(
                            f"argument '{arg.name}' type mismatch: expected "
                            f"{matched.name}, got {arg_type.name}",
                            loc=arg.start,
                            err=ERR.CALLERROR,
                        )
                elif not arg.name.startswith(":"):
                    # unknown named argument — suggest similar parameter names
                    param_names = [p for p, _ in params if not p.startswith(":")]
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
                if param_own == ZParamOwnership.TAKE:
                    # invalidate the caller's name
                    arg_root = self._get_arg_root_name(arg.valtype)
                    if arg_root:
                        var = self.symtab.lookup_var(arg_root)
                        if var and var.ownership == ZOwnership.BORROWED:
                            self._error(
                                f"Cannot pass borrowed variable '{arg_root}' to "
                                f"'take' parameter '{pname}'",
                                loc=arg.start,
                            )
                        else:
                            self.symtab.release_held_locks(arg_root)
                            take_loc = (
                                (arg.start.lineno, arg.start.colno, arg.start.fsno)
                                if arg.start
                                else None
                            )
                            self.symtab.invalidate(arg_root, loc=take_loc)

            # locking algorithm: take locks on arguments
            if arg_type and not _is_valtype(arg_type):
                locks = self._take_arg_locks(arg.valtype, call, arg.start)
                for target_name, holder in locks:
                    call_locks.append((target_name, holder, pname_for_lock))

        # lock the receiver (dotted chain on the callable)
        self._lock_receiver(call.callable, call)

        # after call: transfer lock-param locks to return value, release others
        ret = callee_type.return_type
        lock_param_names = {
            k
            for k, v in callee_type.param_ownership.items()
            if v == ZParamOwnership.LOCK
        }
        for target_name, holder, pname in call_locks:
            if pname in lock_param_names:
                # these locks will be transferred to whoever receives the return value;
                # keep them in place — the holder is the arg variable which remains locked
                pass
            else:
                # release this call's lock
                self.symtab.release_lock(target_name, holder)

        call.type = ret if ret else self.t_null
        if call.call_kind == zast.CallKind.UNKNOWN:
            call.call_kind = zast.CallKind.REGULAR
        return call.type

    def _take_arg_locks(
        self, op: zast.Operation, call: zast.Call, loc: Token
    ) -> List[Tuple[str, str]]:
        """Take locks for a function call argument. Returns list of (target, holder) pairs."""
        locks_taken: List[Tuple[str, str]] = []
        # use a synthetic holder name based on the call for tracking
        holder = f"__call_{id(call)}"

        if isinstance(op, zast.AtomId):
            name = op.name
            if _is_numeric_id(name):
                return locks_taken
            var = self.symtab.lookup_var(name)
            if not var:
                return locks_taken
            # check if this is a data item (exempt from locking)
            if var.ztype.typetype == ZTypeType.DATA:
                return locks_taken
            # exclusive lock on the argument
            err = self.symtab.try_lock(name, ZLockState.EXCLUSIVE, holder)
            if err:
                self._error(err, loc=loc)
            else:
                locks_taken.append((name, holder))

        elif isinstance(op, zast.DottedPath):
            # for dotted paths: exclusive lock on leaf, shared on parent chain
            chain = self._get_dotted_chain(op)
            if chain:
                # exclusive lock on the leaf (last element)
                leaf = chain[-1]
                leaf_var = self.symtab.lookup_var(leaf)
                if leaf_var and leaf_var.ztype.typetype != ZTypeType.DATA:
                    err = self.symtab.try_lock(leaf, ZLockState.EXCLUSIVE, holder)
                    if err:
                        self._error(err, loc=loc)
                    else:
                        locks_taken.append((leaf, holder))
                # shared lock on each parent in the chain
                for parent_name in chain[:-1]:
                    parent_var = self.symtab.lookup_var(parent_name)
                    if parent_var and parent_var.ztype.typetype != ZTypeType.DATA:
                        err = self.symtab.try_lock(
                            parent_name, ZLockState.SHARED, holder
                        )
                        if err:
                            self._error(err, loc=loc)
                        else:
                            locks_taken.append((parent_name, holder))

        elif isinstance(op, zast.Expression):
            inner = op.expression
            if isinstance(inner, zast.Call):
                # sub-call: locks are handled recursively by _check_call
                pass
            elif isinstance(inner, zast.Operation):
                return self._take_arg_locks(inner, call, loc)

        return locks_taken

    def _get_dotted_chain(self, path: zast.DottedPath) -> List[str]:
        """Get the chain of variable names in a dotted path (root first)."""
        parts: List[str] = []
        node = path
        while isinstance(node, zast.DottedPath):
            parts.append(node.child.name)
            node = node.parent
        if isinstance(node, zast.AtomId):
            parts.append(node.name)
        parts.reverse()
        return parts

    def _lock_receiver(self, callable_path: zast.Path, call: zast.Call) -> None:
        """Lock the receiver of a method call (dotted chain on the callable)."""
        if not isinstance(callable_path, zast.DottedPath):
            return
        # only lock if the parent is a variable (not a unit)
        chain = self._get_dotted_chain(callable_path)
        if len(chain) < 2:
            return
        root = chain[0]
        # skip if root is a unit name
        if root in self.program.units:
            return
        root_var = self.symtab.lookup_var(root)
        if not root_var:
            return
        # don't lock data items
        if root_var.ztype.typetype == ZTypeType.DATA:
            return
        holder = f"__recv_{id(call)}"
        # exclusive lock on root (the receiver)
        err = self.symtab.try_lock(root, ZLockState.EXCLUSIVE, holder)
        if err:
            self._error(err, loc=call.start)
        # receiver locks are released after the call
        self.symtab.release_lock(root, holder)

    def _get_arg_root_name(self, op: zast.Operation) -> Optional[str]:
        """Get the root variable name from an operation (for aliasing checks)."""
        if isinstance(op, zast.AtomId):
            if not _is_numeric_id(op.name):
                return op.name
        elif isinstance(op, zast.DottedPath):
            root = op
            while isinstance(root, zast.DottedPath):
                root = root.parent
            if isinstance(root, zast.AtomId):
                return root.name
        elif isinstance(op, zast.Expression):
            inner = op.expression
            if isinstance(inner, zast.Operation):
                return self._get_arg_root_name(inner)
        return None

    def _check_protocol_create(
        self, proto_type: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Check protocol/facet.create from: expr — owned creation."""
        kind = "facet" if proto_type.typetype == ZTypeType.FACET else "protocol"
        # find the from: argument
        from_arg = None
        for arg in call.arguments:
            if arg.name == "from":
                from_arg = arg
                break
        if not from_arg:
            self._error(f"{kind}.create requires 'from:' argument", loc=call.start)
            return None

        # type-check the from: argument
        arg_type = self._check_operation(from_arg.valtype)
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

        call.type = proto_type
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

        # type-check the from: argument
        arg_type = self._check_operation(from_arg.valtype)
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

        # set borrow lock on the source variable (same as obj.label path)
        root_name = self._get_arg_root_name(from_arg.valtype)
        if root_name:
            self._pending_borrow_lock = root_name

        call.type = proto_type
        return proto_type

    def _check_typedef_create(
        self, typedef_type: ZType, call: zast.Call
    ) -> Optional[ZType]:
        """Check typedef.create/take from: expr — owned typedef creation."""
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

        arg_type = self._check_operation(from_arg.valtype)
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

        call.type = typedef_type
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

        arg_type = self._check_operation(from_arg.valtype)
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

        root_name = self._get_arg_root_name(from_arg.valtype)
        if root_name:
            self._pending_borrow_lock = root_name

        call.type = typedef_type
        return typedef_type

    def _check_return_call(self, call: zast.Call) -> Optional[ZType]:
        """Check a return statement: verify return value matches function return type."""
        # type-check the return expression (first argument)
        ret_type = None
        if call.arguments:
            ret_type = self._check_operation(call.arguments[0].valtype)

        if self._current_return_type and ret_type:
            if not self._types_compatible(ret_type, self._current_return_type):
                self._error(
                    f"return type mismatch: function expects "
                    f"{self._current_return_type.name}, got {ret_type.name}",
                    loc=call.start,
                    err=ERR.TYPEERROR,
                )

        # ownership check: cannot return a local variable as borrowed
        ret_own = self._current_func_return_ownership
        if ret_own == ZParamOwnership.BORROW and call.arguments:
            arg_op = call.arguments[0].valtype
            arg_name = self._get_arg_root_name(arg_op)
            if arg_name:
                var = self.symtab.lookup_var(arg_name)
                if var and var.ownership == ZOwnership.OWNED:
                    # check if this is a function parameter (not a local var)
                    # function parameters are in the function scope, locals may shadow
                    # if the var is owned and not a lock parameter, it's a local
                    param_own = self._current_func_ownership.get(arg_name)
                    if param_own != ZParamOwnership.LOCK:
                        self._error(
                            f"Cannot return local variable '{arg_name}' as borrowed; "
                            f"borrowed return values must originate from a 'lock' parameter",
                            loc=call.start,
                        )

        # return has type 'never' (control flow doesn't continue)
        never = self._resolve_name("never")
        call.type = never if never else self.t_null
        return call.type

    @staticmethod
    def _fold_binop(op: str, lhs: int, rhs: int) -> Optional[object]:
        """Evaluate a binary operation on constant integer values at compile time.

        Returns int for arithmetic, bool for comparisons, None if not foldable.
        """
        if op == "+":
            return lhs + rhs
        if op == "-":
            return lhs - rhs
        if op == "*":
            return lhs * rhs
        if op == "/":
            if rhs == 0:
                return None
            # truncation toward zero (C semantics)
            result = lhs / rhs
            return int(result) if result >= 0 else -int(-result)
        if op == "<=":
            return lhs <= rhs
        if op == "<":
            return lhs < rhs
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        return None

    def _check_binop(self, binop: zast.BinOp) -> Optional[ZType]:
        lhs_type = self._check_operation(binop.lhs)
        rhs_type = self._check_path(binop.rhs)
        if not lhs_type or not rhs_type:
            return None

        # look up operator as method on lhs type
        op_name = binop.operator.name
        method = lhs_type.children.get(op_name)
        if method and method.typetype == ZTypeType.FUNCTION:
            ret = method.return_type
            if ret:
                binop.type = ret
                # constant folding: evaluate when both operands are constant integers
                lhs_cv = binop.lhs.const_value
                rhs_cv = binop.rhs.const_value
                if isinstance(lhs_cv, int) and isinstance(rhs_cv, int):
                    folded = self._fold_binop(op_name, lhs_cv, rhs_cv)
                    if folded is not None and isinstance(folded, int):
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
                        binop.const_value = folded
                    elif folded is not None and isinstance(folded, bool):
                        binop.const_value = folded
                return ret

        self._error(
            f"No operator '{op_name}' for types {lhs_type.name} and {rhs_type.name}",
            loc=binop.start,
        )
        return None

    # sentinel for branches that don't complete (return/break/continue)
    _NORETURN = object()

    def _last_statement_type(self, stmt: zast.Statement) -> object:
        """Get the type of the last expression in a statement block.

        Returns ZType for value-producing branches, _NORETURN for
        return/break/continue, or None if no value produced.
        """
        if not stmt.statements:
            return None
        last = stmt.statements[-1].statementline
        if isinstance(last, zast.Expression):
            inner = last.expression
            # check for non-completing expressions
            if isinstance(inner, zast.AtomId) and inner.name in ("break", "continue"):
                return self._NORETURN
            if isinstance(inner, zast.Call) and inner.call_kind == zast.CallKind.RETURN:
                return self._NORETURN
            # get type from the inner expression node (Expression wrapper .type may be None)
            if isinstance(inner, zast.Node) and inner.type is not None:
                return inner.type
            return last.type
        if isinstance(last, zast.Assignment):
            return last.type
        return None

    def _check_exhaustive_if(self, expr: zast.Expression) -> None:
        """Emit error if an if-expression is missing its else clause."""
        inner = expr.expression
        if isinstance(inner, zast.If) and not inner.elseclause:
            self._error(
                "if-expression is not exhaustive (missing else clause)",
                loc=inner.start,
            )

    def _check_if(self, ifnode: zast.If) -> Optional[ZType]:
        self.symtab.push("if")
        for clause in ifnode.clauses:
            for _, cond_op in clause.conditions.items():
                self._check_operation(cond_op)
            self._check_statement(clause.statement)
        if ifnode.elseclause:
            self._check_statement(ifnode.elseclause)

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
                    ifnode.type = never
            elif completing:
                first = completing[0]
                if first is not None and isinstance(first, ZType):
                    all_ok = all(
                        t is not None
                        and isinstance(t, ZType)
                        and self._types_compatible(first, t)
                        for t in completing[1:]
                    )
                    if all_ok:
                        result_type = first
                        ifnode.type = first
                    else:
                        # find first incompatible type for error message
                        for t in completing[1:]:
                            if (
                                t is None
                                or not isinstance(t, ZType)
                                or not self._types_compatible(first, t)
                            ):
                                tname = t.name if isinstance(t, ZType) else "null"
                                self._error(
                                    f"incompatible branch types in if-expression: "
                                    f"'{first.name}' and '{tname}'",
                                    loc=ifnode.start,
                                )
                                break

        self.symtab.pop()
        return result_type

    # system/library units that should not be resolved as generic file units
    _SYSTEM_UNITS = {"core", "system", "io", "collections"}

    def _ensure_file_unit_resolved(self, unitname: str) -> Optional[ZType]:
        """Ensure a file unit has been fully resolved (generic params detected).

        File units get bare ZTypes in __init__. This method triggers full
        resolution via _resolve_inline_unit_type on first access.
        Skips system/library units which are handled by the standard pipeline.
        """
        if unitname in self._resolved_file_units:
            return self.unit_types.get(unitname)
        if unitname not in self.program.units:
            return None
        if unitname in self._SYSTEM_UNITS:
            return self.unit_types.get(unitname)
        self._resolved_file_units.add(unitname)
        file_unit = self.program.units[unitname]
        # replace the bare ZType with a fully resolved one
        utype = self._resolve_inline_unit_type(unitname, unitname, file_unit)
        return utype

    def _get_path_root_var(self, path: zast.Path) -> Optional[ZVariable]:
        """Get the ZVariable for the root of a path expression (if any)."""
        if isinstance(path, zast.AtomId):
            return self.symtab.lookup_var(path.name)
        if isinstance(path, zast.DottedPath):
            return self._get_path_root_var(path.parent)
        return None

    def _is_internal_access(self, parent_type: ZType, path: zast.DottedPath) -> bool:
        """Check if access is from inside the type definition (private access)."""
        if isinstance(path.parent, zast.AtomId) and path.parent.name == "this":
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
        if child_name.startswith(":"):
            return False  # internal/meta fields always accessible
        if child_name in ("tag",):
            return False  # tag accessor always accessible
        if self._is_internal_access(parent_type, path):
            return False
        # friend access: variable declared with .private type bypasses restrictions
        root_var = self._get_path_root_var(path.parent)
        if root_var and root_var.is_private_access:
            return False
        # friend access via .private field: it.items.field where items is a private_field
        if isinstance(path.parent, zast.DottedPath):
            grandparent_type = path.parent.parent.type if path.parent.parent else None
            if (
                grandparent_type
                and path.parent.child.name in grandparent_type.private_fields
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

        inner_type = self._check_operation(from_arg.valtype)
        if not inner_type:
            return None

        if _is_valtype(inner_type):
            # valtype: create monomorphized box type as reftype
            defn = self._find_generic_defn(box_template)
            if not defn:
                return None
            mono = self._monomorphize(box_template, {"t": inner_type}, defn)
            if mono:
                mono.is_box = True
                # copy children from inner type for transparent access
                for cname, ctype in inner_type.children.items():
                    if cname not in mono.children:
                        mono.children[cname] = ctype
                call.type = mono
                call.call_kind = zast.CallKind.BOX_CREATE
            return mono
        else:
            # reftype: passthrough — result IS the inner type
            call.type = inner_type
            call.call_kind = zast.CallKind.BOX_PASSTHROUGH
            return inner_type

    def _is_option_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized option type."""
        return (
            t.typetype == ZTypeType.UNION
            and isinstance(t.generic_origin, ZType)
            and t.generic_origin.name == "option"
        )

    def _is_optionval_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized optionval type."""
        return (
            t.typetype == ZTypeType.VARIANT
            and isinstance(t.generic_origin, ZType)
            and t.generic_origin.name == "optionval"
        )

    def _is_option_or_optionval_type(self, t: ZType) -> bool:
        """Check if a type is a monomorphized option or optionval type."""
        return self._is_option_type(t) or self._is_optionval_type(t)

    def _check_for(self, fornode: zast.For) -> Optional[ZType]:
        self.symtab.push("for")
        # lock tracking for for-loop targets
        locked_targets: List[Tuple[str, str]] = []
        for name, cond_op in fornode.conditions.items():
            t = self._check_operation(cond_op)
            if t and not name.startswith(" "):
                # iterator binding: check if operation type is or returns option/optionval
                iter_option_type = None
                if self._is_option_or_optionval_type(t):
                    # operation directly returns option/optionval (e.g., function call)
                    iter_option_type = t
                elif (
                    t.typetype == ZTypeType.FUNCTION
                    and t.return_type
                    and self._is_option_or_optionval_type(t.return_type)
                ):
                    # function that returns option/optionval (e.g., .each on integers)
                    iter_option_type = t.return_type
                elif t.typetype != ZTypeType.FUNCTION:
                    # check if it's a callable object whose call returns option/optionval
                    call_method = t.children.get("call")
                    if (
                        call_method
                        and call_method.typetype == ZTypeType.FUNCTION
                        and call_method.return_type
                        and self._is_option_or_optionval_type(call_method.return_type)
                    ):
                        iter_option_type = call_method.return_type

                if iter_option_type:
                    some_type = iter_option_type.children.get("some")
                    if some_type:
                        fornode.iterator_bindings.add(name)
                        t = some_type
                self.symtab.define(name, t)
                # lock the iteration target to prevent mutation in body
                if not _is_valtype(t):
                    holder = f"__for_{id(fornode)}"
                    err = self.symtab.try_lock(name, ZLockState.EXCLUSIVE, holder)
                    if not err:
                        locked_targets.append((name, holder))
        for postcond in fornode.postconditions:
            self._check_operation(postcond)
        elem_type = None
        if fornode.loop:
            self._check_statement(fornode.loop)
            # for-as-expression: if the last statement in the loop body is an
            # expression, the for-expression returns a list of that type
            if fornode.loop.statements:
                last = fornode.loop.statements[-1].statementline
                if isinstance(last, zast.Expression):
                    inner_type = last.type or getattr(last.expression, "type", None)
                    if inner_type:
                        elem_type = inner_type
        # release for-loop locks
        for target_name, holder in locked_targets:
            self.symtab.release_lock(target_name, holder)
        self.symtab.pop()
        # for-as-expression: return list of elem_type (non-null values only)
        if elem_type and elem_type != self.t_null and elem_type.name != "null":
            list_template = self._resolve_name("list")
            if list_template and list_template.isgeneric:
                list_defn = self._find_generic_defn(list_template)
                if list_defn:
                    list_mono = self._monomorphize(
                        list_template, {"of": elem_type}, list_defn
                    )
                    fornode.type = list_mono
                    return list_mono
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
        subject_type = self._check_operation(casenode.subject)
        # lock the match subject to prevent mutation in branches
        match_lock_info: Optional[Tuple[str, str]] = None
        if subject_type and not _is_valtype(subject_type):
            subject_name = self._get_arg_root_name(casenode.subject)
            if subject_name:
                holder = f"__match_{id(casenode)}"
                err = self.symtab.try_lock(subject_name, ZLockState.EXCLUSIVE, holder)
                if not err:
                    match_lock_info = (subject_name, holder)
        # union/variant exhaustiveness check
        if subject_type and subject_type.typetype in (
            ZTypeType.UNION,
            ZTypeType.VARIANT,
        ):
            kind = "union" if subject_type.typetype == ZTypeType.UNION else "variant"
            # collect subtype names (exclude :tag, tag data, and methods)
            subtypes = {
                k
                for k, v in subject_type.children.items()
                if not k.startswith(":")
                and v.typetype
                not in (
                    ZTypeType.FUNCTION,
                    ZTypeType.DATA,
                    ZTypeType.TAG,
                    ZTypeType.ENUM,
                )
                and getattr(v, "generic_origin", None) != "tag"
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
        for clause in casenode.clauses:
            self._check_statement(clause.statement)
        if casenode.elseclause:
            self._check_statement(casenode.elseclause)
        # release match lock
        if match_lock_info:
            self.symtab.release_lock(match_lock_info[0], match_lock_info[1])

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
                for k, v in subject_type.children.items()
                if not k.startswith(":")
                and v.typetype
                not in (
                    ZTypeType.FUNCTION,
                    ZTypeType.DATA,
                    ZTypeType.TAG,
                    ZTypeType.ENUM,
                )
                and getattr(v, "generic_origin", None) != "tag"
            }
            covered_for_exhaust = {clause.match.name for clause in casenode.clauses}
            if not (subtypes_for_exhaust - covered_for_exhaust):
                is_exhaustive = True

        result_type = self.t_null

        # match-as-expression: compute branch types when exhaustive
        if is_exhaustive:
            branch_types = [
                self._last_statement_type(clause.statement)
                for clause in casenode.clauses
            ]
            if casenode.elseclause:
                branch_types.append(self._last_statement_type(casenode.elseclause))

            completing = [t for t in branch_types if t is not self._NORETURN]

            if not completing and branch_types:
                never = self._resolve_name("never")
                if never:
                    result_type = never
                    casenode.type = never
            elif completing:
                first = completing[0]
                if first is not None and isinstance(first, ZType):
                    all_ok = all(
                        t is not None
                        and isinstance(t, ZType)
                        and self._types_compatible(first, t)
                        for t in completing[1:]
                    )
                    if all_ok:
                        result_type = first
                        casenode.type = first
                    else:
                        for t in completing[1:]:
                            if (
                                t is None
                                or not isinstance(t, ZType)
                                or not self._types_compatible(first, t)
                            ):
                                tname = t.name if isinstance(t, ZType) else "null"
                                self._error(
                                    f"incompatible branch types in match-expression: "
                                    f"'{first.name}' and '{tname}'",
                                    loc=casenode.start,
                                )
                                break

        self.symtab.pop()
        return result_type


def typecheck(program: zast.Program, full: bool = False) -> List[zast.Error]:
    """Top-level entry point: type-check a parsed program."""
    tc = TypeChecker(program)
    errors = tc.check(full=full)
    program.mono_types = tc._mono_types
    program.func_aliases = tc._func_aliases
    program.cloned_methods = tc._cloned_methods
    program.resolved = dict(tc._resolved)
    return errors


def audit_type_annotations(program: zast.Program) -> List[str]:
    """Post-type-check validation: find Path nodes missing .type annotations.

    Returns a list of diagnostic strings for nodes that should have .type
    set but don't. Empty list means all Path nodes are annotated.
    """
    missing: List[str] = []
    visited: set = set()

    def _walk(node: zast.Node, context: str) -> None:
        nid = id(node)
        if nid in visited:
            return
        visited.add(nid)

        # check Path nodes for .type, skipping structural components:
        # - DottedPath.child: name selector, not a standalone type reference
        # - BinOp operator: operation name, not a type reference
        # - Data item values: literal values in data arrays (not type-checked)
        # - Numeric constant defs: top-level `name: 42` (value is a literal)
        # - Match/case patterns: pattern names for dispatch (not value expressions)
        is_child_of_dotted = context.endswith(".child")
        is_binop_operator = context.endswith(".operator")
        is_data_value = ".data[" in context and context.endswith(".valtype")
        is_case_match = context.endswith(".match")
        is_toplevel_const = "." not in context  # top-level definition like `north: 0`
        if isinstance(node, (zast.AtomId, zast.DottedPath)):
            skip = (
                is_child_of_dotted
                or is_binop_operator
                or is_data_value
                or is_case_match
                or is_toplevel_const
            )
            if node.type is None and not skip:
                loc = f"{node.start.lineno}:{node.start.colno}" if node.start else "?"
                name = node.name if isinstance(node, zast.AtomId) else str(node)
                missing.append(f"{context}: Path node '{name}' at {loc} has no .type")

        # recurse into dataclass fields
        if hasattr(node, "__dataclass_fields__"):
            for fname in node.__dataclass_fields__:
                val = getattr(node, fname, None)
                if val is None:
                    continue
                if isinstance(val, zast.Node):
                    _walk(val, f"{context}.{fname}")
                elif isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, zast.Node):
                            _walk(v, f"{context}.{fname}[{k}]")
                elif isinstance(val, list):
                    for i, v in enumerate(val):
                        if isinstance(v, zast.Node):
                            _walk(v, f"{context}.{fname}[{i}]")

    mainunit = program.units.get(program.mainunitname)
    if mainunit:
        for name, defn in mainunit.body.items():
            if isinstance(defn, zast.Node):
                _walk(defn, name)

    return missing
