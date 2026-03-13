"""
ZeroLang type checking pass — single depth-first pass

Starts at main function, resolves names on demand, detects cycles.
Includes ownership checking (Phase 4c).
"""

from typing import Optional, List, Tuple

import zast
from zast import ERR
from zlexer import Token
from zenv import SymbolTable
from ztypechecker import (
    ZType,
    ZTypeType,
    ZParamOwnership,
    ZOwnership,
    ZNaming,
    ZVariable,
    ZLockState,
    parse_number,
)


def _is_numeric_id(name: str) -> bool:
    c0 = name[0]
    return c0.isdigit() or (c0 in ("+", "-") and len(name) > 1 and name[1].isdigit())


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
    )


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

        # current function return type (for return statement checking)
        self._current_return_type: Optional[ZType] = None

        # current function's ownership annotations (for ownership checking)
        self._current_func_ownership: dict[str, ZParamOwnership] = {}

        # pending borrow lock: set by .borrow, consumed by _check_assignment
        self._pending_borrow_lock: Optional[str] = None

        # inline unit context stack: tracks nesting during resolution
        # each entry is (unitname, zast.Unit) for name lookup chain
        self._unit_context: List[Tuple[str, zast.Unit]] = []

    def _error(self, msg: str, loc: Optional[Token] = None) -> None:
        self.errors.append(zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=loc))

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

        # also check other definitions in the main unit
        for name, defn in mainunit.body.items():
            if name == "main":
                continue
            if isinstance(defn, zast.Unit):
                self._resolve_unit_name(self.program.mainunitname, name)
            elif isinstance(defn, zast.Function) and defn.body:
                self._resolve_unit_name(self.program.mainunitname, name)
                self._check_function_body(name, defn)

        if full:
            for unitname, unit in self.program.units.items():
                for name, defn in unit.body.items():
                    self._resolve_unit_name(unitname, name)
                    if isinstance(defn, zast.Function) and defn.body:
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

    def _type_of_definition(
        self, unitname: str, name: str, defn: zast.TypeDefinition
    ) -> Optional[ZType]:
        """Type-check a definition, pushing/popping the resolving stack."""
        if isinstance(defn, zast.Function):
            return self._resolve_function_type(unitname, name, defn)
        if isinstance(defn, zast.Record):
            return self._resolve_record_type(unitname, name, defn)
        if isinstance(defn, zast.Class):
            return self._resolve_class_type(unitname, name, defn)
        if isinstance(defn, zast.Union):
            return self._resolve_union_type(unitname, name, defn)
        if isinstance(defn, zast.Variant):
            return self._resolve_variant_type(unitname, name, defn)
        if isinstance(defn, zast.Unit):
            return self._resolve_inline_unit_type(unitname, name, defn)
        # alias: DottedPath or AtomId reference
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

        # propagate ownership annotations from AST to ZType
        if func.param_ownership:
            ftype.param_ownership = dict(func.param_ownership)

        # validate function signature ownership rules
        self._validate_function_ownership(ftype, func)

        self._resolving.pop()
        return ftype

    def _validate_function_ownership(self, ftype: ZType, func: zast.Function) -> None:
        """Validate ownership rules on a function signature."""
        own = ftype.param_ownership
        has_return = ":return" in ftype.children
        ret_is_borrow = own.get(":return") == ZParamOwnership.BORROW

        # lock parameters are only valid when there is a return value
        has_lock_param = any(
            v == ZParamOwnership.LOCK for k, v in own.items() if k != ":return"
        )
        if has_lock_param and not has_return:
            self._error(
                "Parameter marked as 'lock' but function has no return value",
                loc=func.start,
            )

        # a function returning borrow must have at least one lock parameter
        if ret_is_borrow and not has_lock_param:
            self._error(
                "Function returns 'borrow' but has no 'lock' parameter; "
                "a borrowed return value must live in a locked parameter",
                loc=func.start,
            )

    def _resolve_class_type(self, unitname: str, name: str, cls: zast.Class) -> ZType:
        key = f"{unitname}.{name}"
        ctype = _make_type(name, ZTypeType.CLASS)
        self._resolved[key] = ctype  # early register for self-reference
        self._resolving.append((key, ctype))

        ctype.is_valtype = False  # classes are reference types

        for fname, fpath in cls.items.items():
            ft = self._resolve_typeref(fpath)
            if ft:
                ctype.children[fname] = ft
        for mname, mfunc in cls.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            ctype.children[mname] = mt
        # as_functions (methods defined in 'as' block)
        for mname, mfunc in cls.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            ctype.children[mname] = mt

        # generate meta.create constructor type
        create_type = self._make_meta_create_type(name, ctype)
        ctype.children[":meta.create"] = create_type
        if "create" not in ctype.children:
            ctype.children["create"] = create_type

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

        # resolve each subtype item
        subtype_names = list(union_defn.items.keys())
        for sname, spath in union_defn.items.items():
            if isinstance(spath, zast.AtomId) and spath.name == "null":
                st = _make_type("null", ZTypeType.RECORD)
                st.is_valtype = True
            else:
                st = self._resolve_typeref(spath)
            if st:
                utype.children[sname] = st

        # resolve tag from as_items: look for any item that resolves to a TAG type
        custom_tag_data = None  # the parent DATA type of the .tag
        tag_count = 0

        for as_name, as_path in union_defn.as_items.items():
            as_type = (
                self._resolve_dotted_path(as_path)
                if isinstance(as_path, zast.DottedPath)
                else self._resolve_typeref(as_path)
            )
            if as_type and as_type.typetype == ZTypeType.TAG:
                tag_count += 1
                if tag_count > 1:
                    self._error(
                        f"Union '{name}' has multiple .tag items in 'as' block",
                        loc=union_defn.start,
                    )
                    break
                custom_tag_data = as_type.parent  # the DATA type that owns .tag

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
            gen_tag = _make_type(f"{name}:tag:data:tag", ZTypeType.TAG, parent=gen_data)
            gen_tag.is_valtype = True
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
            gen_tag = _make_type(f"{name}:tag:data:tag", ZTypeType.TAG, parent=gen_data)
            gen_tag.is_valtype = True
            gen_data.children["tag"] = gen_tag
            utype.children["tag"] = gen_data

        # resolve methods
        for mname, mfunc in union_defn.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            utype.children[mname] = mt
        for mname, mfunc in union_defn.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            utype.children[mname] = mt

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

        # resolve each subtype item
        subtype_names = list(variant_defn.items.keys())
        for sname, spath in variant_defn.items.items():
            if isinstance(spath, zast.AtomId) and spath.name == "null":
                st = _make_type("null", ZTypeType.RECORD)
                st.is_valtype = True
            else:
                st = self._resolve_typeref(spath)
                # reject non-valtypes
                if st and st.is_valtype is not None and not st.is_valtype:
                    self._error(
                        f"Variant '{name}' subtype '{sname}' must be a value type",
                        loc=variant_defn.start,
                    )
                elif st and st.typetype in (ZTypeType.CLASS, ZTypeType.UNION):
                    self._error(
                        f"Variant '{name}' subtype '{sname}' must be a value type",
                        loc=variant_defn.start,
                    )
                elif st and st.name == "string":
                    self._error(
                        f"Variant '{name}' subtype '{sname}' must be a value type",
                        loc=variant_defn.start,
                    )
            if st:
                vtype.children[sname] = st

        # resolve tag from as_items
        custom_tag_data = None
        tag_count = 0

        for as_name, as_path in variant_defn.as_items.items():
            as_type = (
                self._resolve_dotted_path(as_path)
                if isinstance(as_path, zast.DottedPath)
                else self._resolve_typeref(as_path)
            )
            if as_type and as_type.typetype == ZTypeType.TAG:
                tag_count += 1
                if tag_count > 1:
                    self._error(
                        f"Variant '{name}' has multiple .tag items in 'as' block",
                        loc=variant_defn.start,
                    )
                    break
                custom_tag_data = as_type.parent

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
            gen_tag = _make_type(f"{name}:tag:data:tag", ZTypeType.TAG, parent=gen_data)
            gen_tag.is_valtype = True
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
            gen_tag = _make_type(f"{name}:tag:data:tag", ZTypeType.TAG, parent=gen_data)
            gen_tag.is_valtype = True
            gen_data.children["tag"] = gen_tag
            vtype.children["tag"] = gen_data

        # resolve methods
        for mname, mfunc in variant_defn.functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            vtype.children[mname] = mt
        for mname, mfunc in variant_defn.as_functions.items():
            mt = self._resolve_function_type(unitname, f"{name}.{mname}", mfunc)
            vtype.children[mname] = mt

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

        # Generate .tag subtype — a TAG type that refers back to this data
        tag_type = _make_type(f"{name}:tag", ZTypeType.TAG, parent=dtype)
        tag_type.is_valtype = True
        dtype.children["tag"] = tag_type

        self._resolving.pop()
        return dtype

    def _resolve_record_type(self, unitname: str, name: str, rec: zast.Record) -> ZType:
        key = f"{unitname}.{name}"
        rtype = _make_type(name, ZTypeType.RECORD)
        self._resolved[key] = rtype  # early register for self-reference
        self._resolving.append((key, rtype))

        rtype.is_valtype = True  # records are value types

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

        # generate meta.create constructor type
        create_type = self._make_meta_create_type(name, rtype)
        rtype.children[":meta.create"] = create_type
        if "create" not in rtype.children:
            rtype.children["create"] = create_type

        self._resolving.pop()
        return rtype

    def _make_meta_create_type(self, name: str, parent_type: ZType) -> ZType:
        """Build a FUNCTION ZType for the compiler-generated meta.create constructor."""
        ftype = _make_type(f"{name}.create", ZTypeType.FUNCTION)
        ftype.children[":return"] = parent_type
        for fname, ft in parent_type.children.items():
            if fname.startswith(":") or ft.typetype == ZTypeType.FUNCTION:
                continue
            ftype.children[fname] = ft
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

        # push this unit onto the context stack for name resolution
        self._unit_context.append((name, unit))

        # resolve each definition in the inline unit's body
        for dname, ddefn in unit.body.items():
            dkey = f"{unitname}.{name}.{dname}"
            if dkey in self._resolved:
                utype.children[dname] = self._resolved[dkey]
                continue
            t = self._type_of_definition(unitname, f"{name}.{dname}", ddefn)
            if t:
                self._resolved[dkey] = t
                utype.children[dname] = t
            # check function bodies inside inline units
            if isinstance(ddefn, zast.Function) and ddefn.body:
                self._check_function_body(f"{name}.{dname}", ddefn)

        self._unit_context.pop()
        return utype

    # ---- Name resolution (local -> unit body -> core -> system) ----

    def _resolve_name(self, name: str, skip_unit_def=None) -> Optional[ZType]:
        """Resolve a name using the scoping order: local, current unit, core, system.

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

        # 3. current unit body (main unit)
        mainunit = self.program.units.get(self.program.mainunitname)
        if mainunit and name in mainunit.body:
            if skip_unit_def == (self.program.mainunitname, name):
                pass  # label_value: skip self-binding
            else:
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
            # check if it's a unit name first (file-level units)
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
        # check for compiler methods: .take and .borrow
        child_name = path.child.name
        if child_name == "take":
            # .take returns the same type (ownership transfer)
            path.type = parent_type
            return parent_type
        if child_name == "borrow":
            # .borrow returns the same type (borrowed reference)
            path.type = parent_type
            return parent_type
        # for unions/variants, store parent type on the path for construction detection
        if parent_type.typetype in (ZTypeType.UNION, ZTypeType.VARIANT):
            child = parent_type.children.get(child_name)
            if child:
                # non-subtype children (tag, :tag, methods) should not be
                # treated as union/variant subtype construction
                if (
                    child_name not in ("tag", ":tag")
                    and child.typetype != ZTypeType.FUNCTION
                ):
                    path.parent_tagged_type = parent_type
                return child
            return None
        # for records/enums, look up child in children
        child = parent_type.children.get(child_name)
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

        # save/restore ownership context
        prev_func_ownership = self._current_func_ownership
        self._current_func_ownership = dict(func.param_ownership)

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
        self._current_return_type = prev_return_type
        self._current_func_ownership = prev_func_ownership
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
        self._pending_borrow_lock = None
        t = self._check_expression(assign.value)
        if t:
            # check if this assignment is from a .borrow call
            borrow_target = self._pending_borrow_lock
            self._pending_borrow_lock = None

            if borrow_target:
                # the new variable is borrowed and holds an exclusive lock on the target
                var = ZVariable(
                    ztype=t, ownership=ZOwnership.BORROWED, named=ZNaming.NAMED
                )
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
                self.symtab.define_var(assign.name, var)
            assign.type = t

    def _check_reassignment(self, reassign: zast.Reassignment) -> None:
        existing = self._check_path(reassign.topath)
        new_t = self._check_expression(reassign.value)
        if existing and new_t and existing.name != new_t.name:
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
            return self._check_dotted_path(path)
        return None

    def _check_dotted_path(self, path: zast.DottedPath) -> Optional[ZType]:
        """Check a dotted path, handling .take and .borrow compiler methods."""
        child_name = path.child.name

        # handle .take compiler method
        if child_name == "take":
            parent_type = self._check_path(path.parent)
            if parent_type:
                # .take invalidates the source name
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
                        self.symtab.invalidate(path.parent.name)
                path.type = parent_type
            return parent_type

        # handle .borrow compiler method
        if child_name == "borrow":
            parent_type = self._check_path(path.parent)
            if parent_type:
                # .borrow takes an exclusive lock on the receiver
                if isinstance(path.parent, zast.AtomId):
                    receiver_name = path.parent.name
                    # the borrow result will be assigned to a name by _check_assignment;
                    # for now, use a placeholder holder that will be updated
                    self._pending_borrow_lock = receiver_name
                path.type = parent_type
            return parent_type

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

        # handle return statement: check expression type against function return type
        if callee_type.name == "return" and callee_type.typetype == ZTypeType.FUNCTION:
            return self._check_return_call(call)

        # handle union/variant subtype construction: dotted path parent is a tagged type
        # (must be before record/class checks since subtypes may be records)
        if (
            isinstance(call.callable, zast.DottedPath)
            and call.callable.parent_tagged_type
        ):
            parent_tagged = call.callable.parent_tagged_type
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = parent_tagged
            return parent_tagged

        # handle record construction: calling a record type creates an instance
        if callee_type.typetype == ZTypeType.RECORD:
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = callee_type
            return callee_type

        # handle class construction: calling a class type creates a new owned instance
        if callee_type.typetype == ZTypeType.CLASS:
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = callee_type
            return callee_type

        # handle union construction: union.subtype expr
        if callee_type.typetype == ZTypeType.UNION:
            for arg in call.arguments:
                self._check_operation(arg.valtype)
            call.type = callee_type
            return callee_type

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

        # check for reftype aliasing: same reftype arg passed twice
        reftype_args: dict[str, Token] = {}

        # lock tracking: accumulate locks taken during this call
        # each entry: (target_name, holder_placeholder, param_name)
        call_locks: List[Tuple[str, str, Optional[str]]] = []

        for i, arg in enumerate(call.arguments):
            arg_type = self._check_operation(arg.valtype)

            # reftype aliasing check
            if arg_type and not _is_valtype(arg_type):
                arg_name = self._get_arg_root_name(arg.valtype)
                if arg_name:
                    if arg_name in reftype_args:
                        self._error(
                            f"Reftype aliasing: '{arg_name}' passed as multiple "
                            f"arguments in the same call",
                            loc=arg.start,
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
                    if arg_type is not matched and arg_type.name != matched.name:
                        self._error(
                            f"Argument '{arg.name}' type mismatch: expected "
                            f"{matched.name}, got {arg_type.name}",
                            loc=arg.start,
                        )
                # don't error on unmatched named args for now (may be :this etc)
            elif arg_type and i < len(params):
                # positional argument
                _, ptype = params[i]
                if arg_type is not ptype and arg_type.name != ptype.name:
                    self._error(
                        f"Argument type mismatch: expected {ptype.name}, "
                        f"got {arg_type.name}",
                        loc=arg.start,
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
                            self.symtab.invalidate(arg_root)

            # locking algorithm: take locks on arguments
            if arg_type and not _is_valtype(arg_type):
                locks = self._take_arg_locks(arg.valtype, call, arg.start)
                for target_name, holder in locks:
                    call_locks.append((target_name, holder, pname_for_lock))

        # lock the receiver (dotted chain on the callable)
        self._lock_receiver(call.callable, call)

        # after call: transfer lock-param locks to return value, release others
        ret = callee_type.children.get(":return")
        lock_param_names = {
            k
            for k, v in callee_type.param_ownership.items()
            if v == ZParamOwnership.LOCK and k != ":return"
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

    def _check_return_call(self, call: zast.Call) -> Optional[ZType]:
        """Check a return statement: verify return value matches function return type."""
        # type-check the return expression (first argument)
        ret_type = None
        if call.arguments:
            ret_type = self._check_operation(call.arguments[0].valtype)

        if self._current_return_type and ret_type:
            if (
                ret_type is not self._current_return_type
                and ret_type.name != self._current_return_type.name
            ):
                self._error(
                    f"Return type mismatch: function expects {self._current_return_type.name}, "
                    f"got {ret_type.name}",
                    loc=call.start,
                )

        # ownership check: cannot return a local variable as borrowed
        ret_own = self._current_func_ownership.get(":return")
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
        # lock tracking for for-loop targets
        locked_targets: List[Tuple[str, str]] = []
        for name, cond_op in fornode.conditions.items():
            t = self._check_operation(cond_op)
            if t and not name.startswith(" "):
                self.symtab.define(name, t)
                # lock the iteration target to prevent mutation in body
                if not _is_valtype(t):
                    holder = f"__for_{id(fornode)}"
                    err = self.symtab.try_lock(name, ZLockState.EXCLUSIVE, holder)
                    if not err:
                        locked_targets.append((name, holder))
        for postcond in fornode.postconditions:
            self._check_operation(postcond)
        if fornode.loop:
            self._check_statement(fornode.loop)
        # release for-loop locks
        for target_name, holder in locked_targets:
            self.symtab.release_lock(target_name, holder)
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
        self.symtab.pop()
        return self.t_null


def typecheck(program: zast.Program, full: bool = False) -> List[zast.Error]:
    """Top-level entry point: type-check a parsed program."""
    tc = TypeChecker(program)
    return tc.check(full=full)
