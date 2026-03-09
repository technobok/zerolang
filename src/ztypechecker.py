#!/usr/bin/python3
"""
ZeroLang type checker

Type definitions and type checking for the AST


TODO:

- remove zlexer and zast imports? type should not refer to these, ast should link to a
    type but that is all (what about error reporting?)


"""

import threading
from enum import IntEnum, unique
from dataclasses import dataclass, field
from typing import Optional, List, NewType, cast, Callable, Tuple
from collections import OrderedDict
from itertools import count

# from zlexer import Token


@unique
class ZTypeType(IntEnum):
    """
    TypeType - types of types
    """

    # TODO: should this be a single (record) type stored in system unit? Yes
    # compiler must ensure there are no instances of this at runtime (ie. error
    # out if code generator hits one of these)
    NULL = 0  # does this exist? '_'? Use this for function that returns nothing

    # a reference (instance) of a type. ie. a variable or argument
    # first parameter[0] of this type points to a *_DEF type
    # REFERENCE = 1
    # TODO: why? don't need this? Top levels are always definition types, lower level are always instance type
    # INSTANCE = 1

    # a type 'call' ie. application of concrete type arguments for a generic
    # type to create a new type
    GENERIC_CALL = 2

    # a directly generic variable (ie. NOT a compound definition with a generic member)
    # TODO: remove this? Just have the generic parameter inline in the parent
    # def (starting with a "~" and it's type is the union/variant?
    # GENERIC_PARAMETER = 5

    # a constant numeric type (strings and data too?)...
    # is this required? eg for parameters with default values?
    # constants are alway typedefs
    # CONSTANT = 10

    # user defined types. all of these (except ENUM) may be generic
    # by having a generic parameter within them
    UNIT = 50
    FUNCTION = 51
    RECORD = 52
    CLASS = 53
    VARIANT = 54
    UNION = 55
    ENUM = 56
    PROTOCOL = 57

    DATA = 60  # constant array data


@unique
class ZOwnership(IntEnum):
    """
    Ownership - ownership info related to variable/expression
    """

    IMMUTABLE = 0  # constant, readonly, can be shared because immutable eg. all unit level declarations
    OWNED = 1  # eg. local var or @parameter, value types are always owned
    BORROWED = 2  # eg. standard (non-@) function parameter
    LINKED = 3  # eg 'this', "shared" mutable ownership


@unique
class ZNaming(IntEnum):
    """
    Naming -  naming info related to variable/expression
    """

    ANONYMOUS = 0  # expression that is not bound to a name (like an rvalue)
    NAMED = 1  # expression/variable is bound to a name (which name?)


# a typesafe type id
TypeID = NewType("TypeID", int)

# new version os ZType:


@dataclass
class ZType:
    """
    ZType - describes a type

    TODO: needs a name string for error reporting? Fully qualified?
    """

    # pylintxx: disable=too-many-instance-attributes

    nodeid: TypeID = field(
        default_factory=cast(Callable[[], TypeID], count().__next__), init=False
    )

    # name of this type (final component of fully qualified path name only)
    # for error reporting.
    # TODO: add specialised types here too? name would be base name with
    # comma/colon separated generic argument typeids? Yes, then this will be a GENERIC_CALL (children specify type values)
    name: str
    typetype: ZTypeType

    # parent unit that where this type is defined. Optional because top level
    # unit has parent=None; this is for error reporting to get the fully
    # qualified name
    # TODO: is this required, or can code generator/ast keep track of the hierarchy? Yes, I think so - keep a stack when processing
    parent: "Optional[ZType]"

    # TODO: ownership should not be on type... should be on variable...
    # but need 'retained' for func parameters... or just look at parameter name (for leading @?)
    # ownership: ZOwnership

    # TODO: named or anonymous ???? Or is that in type environment (typechecker pass 2)?

    # IDEA: special child names:
    # TODO: need a special lead character to separate special members (is,tag,return, yield...? so easier to skip when iterating?)
    #   start with ~ - for generic fields. Type is union/variant/interface for definition or specific type for GENERIC_CALL
    #   start with @ - for fields that take ownership
    #   :return - for return type of function
    #   :yield - for yield type of iterator function
    #   :type - for a typedef - points to underlying type
    #   :tag - descriminator type for union, variant, enum
    #   :is - child type that has a list of all of the additional interfaces
    #       that this object supports (name of child type is ":is" and parent is this type)
    #   :type (again) - More generic parent type (for GENERIC_CALL or application of concrete
    #     types to a generic type to create a new type - args are normal '~'
    #     names but types are concrete types). If this type is generic again, assign NULL to the unspecified ~ parameters.
    #   [others] - all other normal (non-generic, non-ownership taking) field and method names
    children: "OrderedDict[str, ZType]" = field(default_factory=OrderedDict, init=False)

    # TODO: how to specify a constant numeric item here (eg for List length) -- done, numeric constants are types

    # quick check to see if this is a generic type (ie. has 1+ generic fields in
    # children())
    # is this required? why?
    isgeneric: bool = False

    # True if a literal value (name is canonical string of that value).
    # Will be a typedef to another broader type used for parameter type
    isliteral: bool = False


# a typesafe variable id
VariableID = NewType("VariableID", int)


@dataclass
class ZVariable:
    """
    ZVariable - describes details about a variable/expression. eg type, ownership

    This is also used for constants. All parts of the AST have a ZVariable assigned to them.
    """

    variableid: VariableID = field(
        default_factory=cast(Callable[[], VariableID], count().__next__), init=False
    )
    ztype: ZType
    ownership: ZOwnership
    named: ZNaming
    # or instead of ZNaming...
    # name: Optional[Token] # ??? to point to name?


# @dataclass
# class ZCaptive(ZType):  # ZLock?
#     """
#     ZCaptive - a composite type that Narrows or redefines portions of another
#     ZComposite in a scope.

#     This is used in calls and if/do/case to lock part or all of a reftype from further use
#     because all or a subpart of it has been aliased for use via another variable name
#     """
#     originalid: TypeID  # 'points' to original definition that is being narrowed
#     # Token when the narrowing has occurred (by use), usually used for error reporting
#     definition: Token
#     # fields lists the fields that are being redefined by this 'type'
#     fields: "OrderedDict[str, TypeID]" = field(default_factory=OrderedDict, init=False)
#     # if True, all fields are not allowed to pass through to the underlying originalid
#     allfields: bool = False


class TypeTable:
    """
    TypeTable - table of all types for a program
    """

    def __init__(self) -> None:
        self._table: List[ZType] = []
        self._lock = threading.Lock()

        # create TypeID=0. Must always be the UNKNOWN type
        # self.add(typetype=ZTypeType.UNKNOWN)

        # add the system types
        # self.add(typetype=ZTypeType.NULL)

    def __getitem__(self, index: TypeID) -> ZType:
        """
        return a type by its index/id
        """
        return self._table[index]

    def _append(self, typeitem: ZType) -> TypeID:
        """
        _append - append a new type into the table returning its id

        This method is locked to prevent a race if threaded
        """
        with self._lock:
            idx = TypeID(len(self._table))
            self._table.append(typeitem)  # will be appended at idx
        return idx

    # def _addsystemtype(self, name: str, typetype: ZTypeType) -> None:
    #     del name
    #     self.add(typetype=typetype, definition=t)

    # def getsystemtype(self, name: str) -> TypeID:
    #     """
    #     TODO: make a dict for looking up builtins
    #     """
    #     return None

    def add(
        self,
        name: str,
        typetype: ZTypeType,
        # definition: Optional[Token] = None,
        # members: how to add this? Optional?
        # arraycount: int = 1,
        # defaultvalue: Optional[str] = None,
        # constant: bool = False,
        # generic: bool = False,
    ) -> TypeID:
        """
        add - add a new type to this program and return it

        the type would usually then need to be referenced in a
        Frame.local for name lookup
        """
        # pylint: disable=R0913
        t = ZType(
            name=name,
            typetype=typetype,
            parent=None,
            # definition=definition,
            # arraycount=arraycount,
            # defaultvalue=defaultvalue,
            # constant=constant,
            # generic=generic,
        )
        return self._append(t)


def parse_number(numstr: str) -> Tuple[str, float, Optional[str]]:
    """
    Parse a number from source returning a constant type

    Result is tuple of (typespec, number, error)

    Returns type name string and the number. Note that 'float' is a supertype
    of int for mypy.
    """
    # pylintxxx: disable=R0912
    rest = numstr
    numtype: Optional[str] = None
    t = rest[-4:]
    if t in ("i128", "u128", "f128"):
        numtype = t
        rest = rest[:-4]
    if numtype is None:
        t = rest[-3:]
        if t in ("i16", "i32", "i64", "u16", "u32", "u64", "f32", "f64"):
            numtype = t
            rest = rest[:-3]
    if numtype is None:
        t = rest[-2:]
        if t in ("i8", "u8"):
            numtype = t
            rest = rest[:-2]

    if "." in rest:
        # must be float
        if numtype is None:
            numtype = "f64"
        elif numtype[0] != "f":
            return (
                numtype,
                0,
                "Numeric numtype specifier must be float for literals "
                + "with decimal points",
            )
    elif not numtype:
        numtype = "i64"

    rest = rest.replace("_", "")
    prefix = rest[:2]
    base = 10
    if prefix == "0b":
        base = 2
        rest = rest[2:]
    elif prefix == "0o":
        base = 8
        rest = rest[2:]
    elif prefix == "0x":
        base = 16
        rest = rest[2:]

    if numtype[0] == "f":
        if base != 10:
            return (numtype, 0, f"Base must be 10 for float: {numstr}")
        f = float(rest)
        # fvaltype = Float(typename=numtype)
        return numtype, f, None

    i = int(rest, base=base)
    # ivaltype = Integer(typename=numtype)
    return numtype, i, None


########################################### TODO:

# def typecheck(program: zast.Program) -> None:
#     """
#     typecheck
#     """
#     o: List[str] = []
#     for name, unit in program.units.items():
#         o.append(f"\n{name}: ")
#         # o.append(f"{name} = *UNIT {{\n")
#         typechecknode(unit, output=o, depth=1)
#         # o.append("\n}")
#     print("".join(o))


# def typechecknode(node: Node, output: List[str], depth: int) -> List[str]:
#     """
#     typechecknode - pretty print a node
#     """
#     f = nodehandler.get(node.nodetype)
#     if f:
#         f(node, output, depth)
#     else:
#         print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
#         print(f"Cannot handle node type {node.nodetype.name}")
#     return output


# def checkunit(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkunit
#     """
#     # if node.nodetype != NodeType.UNIT:
#     if not isinstance(node, zast.Unit):
#         raise Exception("Error: wrong node type")

#     # TODO: output generic arguments (if any) as well

#     output.append("*UNIT {\n")
#     sepinner = (depth + 1) * "  "

#     output.append(sepinner)
#     checknamedexpressionlist(node.body, output, depth + 1)

#     sep = depth * "  "
#     output.append(f"\n{sep}}}")


# def checknamedexpressionlist(
#     node: List[zast.Definition], output: List[str], depth: int
# ) -> None:
#     """
#     checknamedexpressionlist
#     """
#     sep = depth * "  "
#     for ne in node:
#         checkdefinition(ne, output, depth)
#         output.append(f"\n{sep}")


# def checknamedoperationlist(
#     node: List[zast.Definition], output: List[str], depth: int
# ) -> None:
#     """
#     checknamedoperationlist
#     """
#     for namedop in node:
#         output.append("\n")
#         checknamedoperation(namedop, output, depth + 1)


# def checkdefinition(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkdefinition
#     """
#     if not isinstance(node, zast.Definition):
#         raise Exception("Error: wrong node type")
#     sep = depth * "  "
#     output.append(f"{sep}{node.name}: ")
#     typechecknode(node.expression, output, depth + 1)


# def checknamedoperation(node: Node, output: List[str], depth: int) -> None:
#     """
#     checknamedoperation
#     """
#     if not isinstance(node, zast.Definition):
#         raise Exception("Error: wrong node type")
#     sep = depth * "  "
#     output.append(f"{sep}{node.name}:")
#     typechecknode(node.expression, output, depth + 1)


# def checkassignment(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkassignment
#     """
#     # if node.nodetype != NodeType.ASSIGNMENT:
#     if not isinstance(node, zast.Assignment):
#         raise Exception("Error: wrong node type")
#     output.append(f"*ASSIGNMENT {{\n")
#     sepinner = (depth + 1) * "  "
#     output.append(sepinner)
#     # checkdottedpath(node.lhs, output, depth)
#     typechecknode(node.lhs, output, depth)
#     output.append(" = ")
#     typechecknode(node.rhs, output, depth + 1)
#     sep = depth * "  "
#     output.append(f"\n{sep}}}")


# def checkbinop(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkbinop
#     """
#     if not isinstance(node, zast.Binop):
#         raise Exception("Error: wrong node type")
#     output.append(f"*BINOP {{\n")
#     sepinner = (depth + 1) * "  "

#     if node.lhs:
#         # _pprintatom(node.lhs, output, depth)
#         # output.append(f"*ATOM*\n")
#         output.append(sepinner)
#         typechecknode(node.lhs, output, depth + 1)

#     if node.operator:
#         output.append(f" {node.operator.token} ")

#     if node.rhs:
#         # an atom
#         typechecknode(node.rhs, output, depth + 1)

#     sep = depth * "  "
#     output.append(f"\n{sep}}}")


# def checkcall(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkcall
#     """
#     if not isinstance(node, zast.Call):
#         raise Exception("Error: wrong node type")
#     output.append(f"*CALL (")
#     sepinner = (depth + 1) * "  "

#     output.append("\n" + sepinner)
#     typechecknode(node.callable, output, depth + 1)

#     if len(node.arguments) != 0:
#         output.append("\n" + sepinner + "ARGS*(")
#         checknamedoperationlist(node.arguments, output, depth + 1)
#         output.append("\n" + sepinner + ")")


# def checkfunction(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkfunction
#     """
#     if not isinstance(node, zast.Function):
#         raise Exception("Error: wrong node type")
#     output.append("*FUNCTION {")
#     sepinner = (depth + 1) * "  "

#     output.append("*RESULT(")
#     if node.result:
#         # _pprintdottedpath(node.result, output, depth + 1)
#         typechecknode(node.result, output, depth + 1)
#         output.append("\n")
#     output.append(") ")

#     if len(node.parameters) != 0:
#         output.append("\n" + sepinner + "ARGS*{")
#         checknamedoperationlist(node.parameters, output, depth + 1)
#         output.append("\n" + sepinner + "}")

#     output.append("\n" + sepinner + "BODY*{")
#     typechecknode(node.body, output, depth + 1)
#     output.append("\n" + sepinner + "}")

#     output.append("\n" + sepinner + "}")


# def checkspec(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkspec
#     """
#     if not isinstance(node, zast.Spec):
#         raise Exception("Error: wrong node type")
#     if node.core:
#         # make core string literal
#         sys: List[str] = []
#         for s in node.core.stringparts:
#             if not isinstance(s, Token):
#                 raise Exception("spec core strings must be string literals only")
#             sys.append(s.token)

#         syss = "".join(sys)
#         output.append(f"*SPEC(CORE:{syss}) {{")
#     else:
#         output.append("*SPEC {")
#     sepinner = (depth + 1) * "  "

#     if node.result:
#         output.append("*RESULT(")
#         # _pprintdottedpath(node.result, output, depth + 1)
#         typechecknode(node.result, output, depth + 1)
#         output.append(") ")

#     if len(node.parameters) != 0:
#         output.append("\n" + sepinner + "ARGS*{")
#         checknamedoperationlist(node.parameters, output, depth + 1)
#         output.append("\n" + sepinner + "}")

#     if node.result or node.parameters:
#         output.append("\n" + sepinner + "}")
#     else:
#         output.append("}")


# def checkif(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkif
#     """
#     if not isinstance(node, zast.If):
#         raise Exception("Error: wrong node type")
#     output.append("*if {")
#     sepinner = (depth + 1) * "  "

#     output.append("*CONDITIONS(")
#     checknamedoperationlist(node.conditions, output, depth + 1)
#     output.append(") ")

#     output.append("\n" + sepinner + "STATEMENT*{")
#     typechecknode(node.statement, output, depth + 1)
#     output.append("\n" + sepinner + "}")

#     if node.nextclause:
#         output.append(f"\n{sepinner}")
#         checkif(node.nextclause, output, depth)

#     output.append("\n" + sepinner + "}")


# def checkdo(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkdo
#     """
#     if not isinstance(node, zast.Do):
#         raise Exception("Error: wrong node type")
#     output.append("*do {")
#     sepinner = (depth + 1) * "  "

#     output.append("*CONDITIONS(")
#     checknamedoperationlist(node.conditions, output, depth + 1)
#     output.append(") ")

#     output.append(f"\n{sepinner}LOOP*{{")
#     typechecknode(node.loop, output, depth + 1)
#     output.append(f"\n{sepinner}}}")

#     output.append("*POSTCONDITIONS(")
#     for c in node.postconditions:
#         typechecknode(c, output, depth + 1)
#     output.append(") ")

#     output.append("\n" + sepinner + "}")


# def checkatomid(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkatomid
#     """
#     del depth
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.AtomId):
#         raise Exception("Error: wrong node type")
#     output.append(f"*ATOMID(")
#     # sepinner = (depth + 1) * "  "
#     # output.append(sepinner)
#     output.append(node.name)
#     output.append(")")

#     # sep = depth * "  "
#     # output.append(f"{sep}}}\n")


# def checkatomnumber(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkatomnumber
#     """
#     del depth
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.AtomNumber):
#         raise Exception("Error: wrong node type")
#     output.append(f"*ATOMNUMBER(")
#     # sepinner = (depth + 1) * "  "
#     # output.append(sepinner)
#     output.append(node.start.token)
#     output.append(")")


# def checkatomstring(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkatomstring
#     """
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.AtomString):
#         raise Exception("Error: wrong node type")
#     output.append(f'*ATOMSTRING("')
#     sepinner = (depth + 1) * "  "
#     del sepinner
#     # output.append(sepinner)
#     for s in node.stringparts:
#         if isinstance(s, Token):
#             output.append(f'"{s.token}"')
#         else:
#             output.append("(")
#             typechecknode(s, output, depth)
#             output.append(")")
#     output.append('")')


# def checkatomexpression(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkatomexpression
#     """
#     # if node.nodetype != NodeType.MEMBER:
#     if not isinstance(node, zast.AtomExpr):
#         raise Exception("Error: wrong node type")
#     output.append(f"*ATOMEXPR(\n")
#     # sepinner = (depth + 1) * "  "
#     # output.append(sepinner)
#     sepinner = (depth + 1) * "  "
#     output.append(sepinner)
#     if node.expression:
#         typechecknode(node.expression, output, depth + 1)
#     # output.append(")")

#     sep = depth * "  "
#     output.append(f"\n{sep})")


# def checkdottedpath(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkdottedpath
#     """
#     if not isinstance(node, zast.DottedPath):
#         raise Exception(f"Error: wrong node type got {node!r}")
#     # assume a dotted path fits on a single line
#     output.append("*DOTTEDPATH(")
#     # sepinner = (depth + 1) * "  "

#     if node.lhs:
#         # _pprintatom(node.lhs, output, depth)
#         # output.append(f"*ATOM*\n")
#         # output.append(sepinner)
#         typechecknode(node.lhs, output, depth + 1)

#     if node.rhs:
#         output.append(f" . {node.rhs.token}")

#     # sep = depth * "  "
#     # output.append(f"{sep}}}")
#     output.append(")")


# def checkstatement(node: Node, output: List[str], depth: int) -> None:
#     """
#     checkstatement
#     """
#     if not isinstance(node, zast.Statement):
#         raise Exception("Error: wrong node type")
#     output.append(f"*STATEMENT {{\n")
#     sepinner = (depth + 1) * "  "

#     for s in node.statements:
#         output.append(sepinner)
#         typechecknode(s, output, depth + 1)

#     sep = depth * "  "
#     output.append(f"\n{sep}}}")


# nodehandler: dict = {
#     NodeType.UNIT: checkunit,
#     # NodeType.BLOCK: checkblock,
#     NodeType.STATEMENT: checkstatement,
#     NodeType.DEFINITION: checkdefinition,
#     NodeType.ASSIGNMENT: checkassignment,
#     NodeType.CALL: checkcall,
#     NodeType.FUNCTION: checkfunction,
#     NodeType.SPEC: checkspec,
#     # NodeType.PROTOCOL: checkprotocol,
#     # NodeType.RECORD: checkrecord,
#     # NodeType.CLASS: checkclass,
#     # NodeType.UNION: checkunion,
#     # NodeType.VARIANT: checkvariant,
#     # NodeType.ENUM: checkenum,
#     NodeType.IF: checkif,
#     NodeType.DO: checkdo,
#     NodeType.BINOP: checkbinop,
#     NodeType.DOTTEDPATH: checkdottedpath,
#     # NodeType.ATOMBLOCK: checkatomblock,
#     # NodeType.ATOMEXPR: checkatomexpr,
#     NodeType.ATOMID: checkatomid,
#     NodeType.ATOMNUMBER: checkatomnumber,
#     NodeType.ATOMSTRING: checkatomstring,
#     # NodeType.ARGUMENT: _pprintargument,
#     # NodeType.NAMEDOPERATION: checknamedoperation,
# }
