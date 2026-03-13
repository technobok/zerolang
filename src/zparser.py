#!/usr/bin/python3
"""
ZeroLang parser
"""

# pylint: disable=too-many-lines
from typing import List, Dict, Optional, Union, TypeVar, Generic, Set
from dataclasses import dataclass
from zvfs import ZVfs, DEntryID, DEntryType, ZVfsOpenFile
from zlexer import Lexer, Tokenizer, Token, isvalidunitname
from ztokentype import TT
import zast
from zast import ERR
from ztypechecker import ZParamOwnership

# ownership annotation suffixes recognized on dotted type paths
_OWNERSHIP_SUFFIXES = {
    "take": ZParamOwnership.TAKE,
    "borrow": ZParamOwnership.BORROW,
    "lock": ZParamOwnership.LOCK,
}

# A Node type.
TN = TypeVar("TN", bound=zast.Node, covariant=True)


@dataclass
class NodeX(Generic[TN]):
    """
    NodeX - a zast.Node bundled with extra data
    """

    node: TN
    # list of external references to be resolved
    extern: Dict[str, zast.AtomId]


# a list of elements that are possible operation elements (operands and operators)
# OpListType = List[Union[NodeX[zast.Path], NodeX[zast.AtomId], Token]]
# OpListType = List[NodeX[zast.Path]]


def promoteexterns(
    addto: Dict[str, zast.AtomId],
    addfrom: Dict[str, zast.AtomId],
    local: Optional[Set[str]] = None,
) -> None:
    """

    promoteexterns - update an externs dict (addto) with some new externs
    (addfrom). Only add if not already in addto and (if supplied) not in
    locals.

    """
    for k, v in addfrom.items():
        if local and k in local:
            continue  # skip local
        if k not in addto:
            addto[k] = v
    # return addto


@dataclass
class ObjectBody:
    """
    Object body components for a record, class, variant or union
    """

    items: Dict[str, zast.Path]  # generic and normal (???)
    islist: List[zast.Path]  # 'is' interfaces implimented/included by this record
    functions: Dict[str, zast.Function]
    tag: Optional[zast.Path]
    extern: Dict[str, zast.AtomId]


class Parser:
    """
    Parser

    Parse source into an AST, starting from mainunit but potentially from
    several files as referenced.

    pass1 is self.parse
    pass2 is self.typecheck TODO: pass2 in a separate class
        (somehow have to transfer state). TODO: have a seprate class for typecheck

    resulting program is in self.program (zast.Program)
    errors in self.numerrors and self.state.errors (expose these?)
    """

    # pylint: disable=R0903

    def __init__(self, vfs: ZVfs, mainunitname: str):
        """
        vfs: Virtual Filesystem instance to gain access to the module source(s)
        mainunitname: name of main unit (within main; not path, no slashes) to compile
        """
        self.vfs: ZVfs = vfs
        self.mainunitname: str = mainunitname

        # self.nodetable: zast.NodeTable = zast.NodeTable()
        # self.environment = Environment()
        # self.environment: ParserEnvironment  # initialised below in parse()

        self.rootid: DEntryID

    def parse(self) -> Union[zast.Program, zast.Error]:
        """
        Parse into an AST (First pass)

        Returns an Error if cannot begin parsing (no core, no main unit)
        Returns a Program otherwise. Program may still have an error if parsing
        failed, check .error field

        """
        self.rootid = self.vfs.walk()

        # list of external references (units) to be resolved
        # add in reverse order, will resolve bottom up
        unitstocompile: Dict[str, Optional[zast.AtomId]] = {}
        unitstocompile[self.mainunitname] = None
        # core must be first (so added last, will pop first)
        unitstocompile["core"] = None
        definitions: Dict[str, zast.Unit] = {}  # top level definition Nodes
        core: Optional[zast.Unit] = None

        # loop like this because we will be adding/removing each loop
        while unitstocompile:
            if not core and "core" in definitions:
                core = definitions["core"]
            refname, atomid = unitstocompile.popitem()
            reftoken: Optional[Token] = None
            if atomid:
                reftoken = atomid.start
            unit = self._acceptunitfile(
                self.rootid, unitname=refname, reference=reftoken, core=core
            )
            if isinstance(unit, zast.Error):
                err = unit
                if err.err == ERR.FILENOTFOUND and err.loc:
                    # could be a bad variable name as well as FILENOTFOUND
                    msg = f'Unknown reference "{err.loc.tokstr}" and {err.msg}'
                    err = zast.Error(err=ERR.BADREFERENCE, msg=msg, loc=reftoken)
                    return err
                return err  # propagate error - all other types

            if refname in definitions:
                # can this happen?
                msg = f"Unit {refname} already exists"
                err = zast.Error(err=ERR.BADUNIT, msg=msg, loc=reftoken)
                return err

            # push up any (new) unresolved refs (units allows None so has to be done manually)
            # print(f"have units {unitstocompile.keys()}")
            for k, v in unit.extern.items():
                # skip nodes we've already compiled or already have in the queue to compile
                if k not in definitions and k not in unitstocompile:
                    print(f"pushing up {k}")
                    unitstocompile[k] = v

            definitions[refname] = unit.node

        return zast.Program(
            vfs=self.vfs, units=definitions, mainunitname=self.mainunitname
        )

    def _acceptunitfile(
        self,
        parentid: DEntryID,
        unitname: str,
        reference: Optional[Token],
        core: Optional[zast.Unit],
        # parentnamespace: Dict[str, zast.Unit],
    ) -> Union[NodeX[zast.Unit], zast.Error]:
        """
        acceptunitfile - parse a unit file (or subunit file)

        parentid: DEntryID of parent directory that should contain this unit
        unitname: name of unit (no slashes and no .z)
        reference: the token that caused this unit to be referenced
            (None for the main unit). For error reporting
        core: core definitions (from core module if available)

        Return a NodeX (of a unit expression) or an error

            unitfile
                { definition | newline } eof
        """
        # pylint: disable=too-many-locals, too-many-branches

        extern: Dict[str, zast.AtomId] = {}
        toresolve: Dict[str, zast.AtomId]  # references to resolve, set below

        if not isvalidunitname(unitname):
            msg = (
                f"Invalid unit name for top level unit: {unitname}\n"
                + "Unit names must begin with a letter and contain only letters and digits\n"
                + "Unit names must be lowercase ASCII only\n"
            )
            return zast.Error(err=ERR.BADUNITNAME, msg=msg, loc=reference)

        openfile = self._unitfileopen(parentid, unitname, reference)
        if isinstance(openfile, zast.Error):
            return openfile  # propagate error

        with openfile:
            tokenizer = Tokenizer(openfile)
            lex = Lexer(tokenizer)
            fsid = openfile.entryid
            print(f"Compiling module file at: {self.vfs.path(fsid)}")

            unitorerr = self._acceptunitbody(lex)
            if isinstance(unitorerr, zast.Error):
                return unitorerr  # propagate error

            unit = unitorerr.node
            toresolve = unitorerr.extern

            # check EOF
            if not lex.accept(TT.EOF):
                msg = "Expected EOF at end of Unit file"
                err = zast.Error(err=ERR.BADUNIT, msg=msg, loc=lex.peek())
                return err

        # possible directory of same name as unit, for subunits
        dirid = self.vfs.walk([unitname], parentid)
        dirstat = self.vfs.stat(dirid)
        hassubunitdir = dirstat.entrytype == DEntryType.DIR

        # loop like this because we will be adding/removing each loop
        while toresolve:
            refname, refid = toresolve.popitem()
            if refname in unit.body:  # found ref in ourself (may be forward ref)
                continue
            if core and refname in core.body:  # found in core
                continue
            if not refid.canbemoduleref:
                # this reference is local only, cannot be a module
                # assume it is a reference against an argument type
                continue

            if hassubunitdir:  # look for subunit
                # could have sub-extern references too...
                start: Optional[Token] = None
                if refid:
                    start = refid.start
                subunitx = self._acceptunitfile(
                    parentid=dirid, unitname=refname, reference=start, core=core
                )
                if isinstance(subunitx, zast.Error):
                    err = subunitx
                    if err.err != ERR.FILENOTFOUND:
                        # propagate error
                        return err
                    # else FILENOTFOUND, not a subunit, keep looking
                else:
                    # got a subunit (without error)
                    # add externs to our list(they have already been checked against
                    # local and core definitions)
                    promoteexterns(addto=toresolve, addfrom=subunitx.extern)
                    # add this unit
                    unit.body[refname] = subunitx.node
                    continue

            # else, subunit so assume this is an extern reference
            extern[refname] = refid

        return NodeX(node=unit, extern=extern)

    def _unitfileopen(
        self, parentid: DEntryID, unitname: str, reference: Optional[Token]
    ) -> Union[ZVfsOpenFile, zast.Error]:
        """
        unitfileopen - open a unit file

        parentid: DEntryID of parent directory that should contain this unit
        unitname: name of unit (no slashes and no .z)
        reference: the token that caused this unit to be referenced
        (None for the main unit)

        Return a lexer or an error
        """
        fn = unitname + ".z"
        fsid = self.vfs.walk([fn], parentid)
        stat = self.vfs.stat(fsid)
        if stat.entrytype == DEntryType.NOTFOUND:
            fullname = self.vfs.path(fsid)
            msg = f'Unit file "{fullname}" does not exist'
            return zast.Error(err=ERR.FILENOTFOUND, msg=msg, loc=reference)

        try:
            openfile = self.vfs.open(fsid)
        except IOError:
            msg = f"IO Error opening file [{fn}]"
            return zast.Error(err=ERR.IOERROR, msg=msg, loc=reference)

        # must have opened ok
        return openfile

    @staticmethod
    def _make_label_value(tok: Token) -> NodeX[zast.LabelValue]:
        """Create a LabelValue node from a LABELPRE token."""
        lv = zast.LabelValue(start=tok, name=tok.tokstr, canbemoduleref=True)
        return NodeX(node=lv, extern={tok.tokstr: lv})

    def _acceptunitbody(self, lex: Lexer) -> Union[NodeX[zast.Unit], zast.Error]:
        """
        _acceptunitbody = accept the body of a unit. Used by unitfile and
        for subunits

        lex: lexer to read from (will stop before EOF for file unit and before
        closing '}' for subunit; or may fail on error

            { definition | newline }

        Returns a Unit (and external references that need to be resolved). Or
        may be an error.

        """
        definitions: Dict[str, zast.TypeDefinition] = {}
        extern: Dict[str, zast.AtomId] = {}
        # TODO: this and type are predefined for units (?)
        localdefs: Set[str] = set(("this", "type"))

        start = lex.peek()

        prev_filtereol = lex._filtereol
        lex.filtereol(False)  # need EOLs as definition separators
        error: Optional[zast.Error] = None

        while True:
            if lex.accept(TT.EOL):
                continue

            label: Optional[Token] = lex.accept(TT.LABEL)
            is_label_value = False
            if not label:
                label = lex.accept(TT.LABELPRE)
                is_label_value = True
            if not label:
                break  # no definition

            if is_label_value:
                lvx = self._make_label_value(label)
                typedefinitionx = NodeX(node=lvx.node, extern=lvx.extern)
            else:
                lex.accept(TT.EOL)  # optional EOL after label

                t = lex.peek()
                lex.filtereol(True)  # filter EOLs within definition value
                typedefinitionx = self._accepttypedefinition(lex)
                lex.filtereol(False)  # restore for next definition boundary
                if typedefinitionx is None:
                    msg = (
                        f"Expected TypeDefinition after Definition name, got '{t.tokstr}' "
                        + repr(t)
                    )
                    error = zast.Error(err=ERR.EXPECTEDTYPEDEF, msg=msg, loc=t)
                    break
                if isinstance(typedefinitionx, zast.Error):
                    error = typedefinitionx
                    break

            definition = typedefinitionx.node
            name = label.tokstr
            if name in definitions:
                error = zast.Error(
                    ERR.DUPLICATEDEF,
                    f"Duplicate definition of {name}",
                    definition.start,
                )
                break
            # extern references that need to be push upwards....
            promoteexterns(
                addto=extern, addfrom=typedefinitionx.extern, local=localdefs
            )
            definitions[name] = definition

        lex.filtereol(prev_filtereol)  # restore previous state
        if error:
            return error

        return NodeX(node=zast.Unit(start=start, body=definitions), extern=extern)

    def _accepttypedefinition(
        self, lex: Lexer
    ) -> Union[NodeX[zast.TypeDefinition], zast.Error, None]:
        """
        accept a typedefinition

            typedefinition
                unit
                | function
                | record
                | class
                | variant
                | union
                | enum
                | protocol
                | expression

        Returns the type definition or an error or None if no type definition keyword or expression
        """
        # pylint: disable=too-many-return-statements
        t = lex.peek()
        tt = t.toktype
        if tt == TT.UNIT:
            return self._acceptsubunit(lex)
        if tt == TT.FUNCTION:
            return self._acceptfunction(lex)
        if tt == TT.RECORD:
            return self._acceptrecord(lex)
        if tt == TT.CLASS:
            return self._acceptclass(lex)
        if tt == TT.VARIANT:
            return self._acceptvariant(lex)
        if tt == TT.UNION:
            return self._acceptunion(lex)
        if tt == TT.ENUM:
            return self._acceptenum(lex)
        if tt == TT.PROTOCOL:
            return self._acceptprotocol(lex)
        if tt == TT.FACET:
            return self._acceptfacet(lex)
        if tt == TT.IF:
            return self._acceptifastypedef(lex)
        if tt == TT.MATCH:
            return self._acceptmatchastypedef(lex)
        if tt == TT.DATA:
            return self._acceptdataastypedef(lex)
        # at unit level, only operations (not calls) are valid
        return self._acceptoperation(lex)

    def _acceptifastypedef(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """Wrap _acceptif result as an Expression for use at type definition level."""
        node = self._acceptif(lex)
        if isinstance(node, zast.Error) or node is None:
            return node
        expression = zast.Expression(expression=node.node, start=node.node.start)
        return NodeX(node=expression, extern=node.extern)

    def _acceptmatchastypedef(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """Wrap _acceptmatch result as an Expression for use at type definition level."""
        node = self._acceptmatch(lex)
        if isinstance(node, zast.Error) or node is None:
            return node
        expression = zast.Expression(expression=node.node, start=node.node.start)
        return NodeX(node=expression, extern=node.extern)

    def _acceptdataastypedef(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """Wrap _acceptdata result as an Expression for use at type definition level."""
        node = self._acceptdata(lex)
        if isinstance(node, zast.Error) or node is None:
            return node
        expression = zast.Expression(expression=node.node, start=node.node.start)
        return NodeX(node=expression, extern=node.extern)

    def _acceptexpression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """
        acceptexpression

            expression
                if
                | for
                | case
                | data
                | operation
                | call

            call
                path [ operation ] { label operation }

            operation
                path | binop

        Returns the Expression or an error or None
        """
        # pylint: disable=R0911,R0912,R0914
        t = lex.peek()
        tt = t.toktype
        node: Union[NodeX[zast.ExpressionSubTypes], zast.Error, None]
        if tt == TT.IF:
            node = self._acceptif(lex)
        elif tt == TT.FOR:
            node = self._acceptfor(lex)
        elif tt == TT.MATCH:
            node = self._acceptmatch(lex)
        elif tt == TT.DATA:
            node = self._acceptdata(lex)
        elif tt == TT.WITH:
            node = self._acceptwith(lex)
        # elif tt == TT.ARRAY:
        #     node = self._acceptarray(lex)
        # elif tt == TT.LIST:
        #     node = self._acceptlist(lex)
        else:
            oplist = self._getoplist(lex)
            if isinstance(oplist, zast.Error):
                return oplist  # propagate error
            node = self._acceptoperationorcall(oplist, lex)

        if isinstance(node, zast.Error):
            return node
        if not node:
            return None

        expression = zast.Expression(expression=node.node, start=node.node.start)
        return NodeX(node=expression, extern=node.extern)

    def _acceptoperationorcall(
        self, oplist: List[NodeX[zast.Path]], lex: Lexer
    ) -> Union[NodeX[zast.Operation], NodeX[zast.Call], zast.Error, None]:
        """

        acceptoperationorcall - accept an operation or a call. These nodes may
        need additional lookahead to discern.

        oplist: a list of ops already consumed
        lex: lexer to possibly get remainder of operation or call

        Returns an Operation or a Call or an error or None

        If an error is returned, items may have been consumed...
        """

        # ops = get as many operands and operators as possible (via getoplist)
        # if ops is error -> error
        # x = len(ops)
        # if x is 0 -> None
        # if x is 1 and peek() as a label -> call
        # if x is even -> call
        # else (x is odd) -> operation (... single item is still an operation,
        #    as long as no label following)
        # if we found a call, always assume the first op is callable.
        #   Let the typechecker do further checking

        # oplist = self._getoplist(lex)
        # if isinstance(oplist, zast.Error):
        #     return oplist  # propagate error

        if not oplist:  # len(oplist) == 0
            return None

        if (len(oplist) == 1 and lex.peek().toktype in (TT.LABEL, TT.LABELPRE)) or (
            len(oplist) % 2 == 0
        ):
            return self._acceptcall(lex=lex, paths=oplist)

        # else: op or error...
        opx = self._getop(
            paths=oplist, nexttoken=lex.peek()
        )  # don't propagate error, may be call
        return opx

    def _getoplist(self, lex: Lexer) -> Union[List[NodeX[zast.Path]], zast.Error]:
        """

        getoplist - get a list of possible operation elements (operands and
        operators). Take as many elements of these types as possible. This is
        used to determine if parser is at an expression or a call and is used
        in getop. Each element is a Path

        Returns the List (possibly empty) or an Error
        """
        ret: List[NodeX[zast.Path]] = []
        while True:
            path = self._acceptpath(lex)
            if isinstance(path, zast.Error):
                return path  # propagate error
            if not path:
                break
            ret.append(path)

        return ret

    @staticmethod
    def _getop(
        paths: List[NodeX[zast.Path]], nexttoken: Token
    ) -> Union[NodeX[zast.Operation], zast.Error, None]:
        """
        Given a list of paths (from getoplist), return an Operation. Helper
        method for acceptexpression.

        Note that a single element in paths is still an operation (just not a
        Binop)

        paths = list of pathx's
        nexttoken = next token after paths, for error reporting

        Returns Error if paths cannot be fully converted to an Operation.
        Returns None if no paths. Otherwise, returns the Operation

        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements
        if not paths:
            return None

        el = paths[0]

        if isinstance(el, Token):
            # 'as' cannot be first, must be path/atom
            msg = "Expected an operand for the left hand side of operation"
            return zast.Error(err=ERR.BADEXPRESSION, msg=msg, loc=el)
        start = el.node.start

        # operations: List[zast.OpArg] = []
        operation: zast.Operation = el.node
        extern: Dict[str, zast.AtomId] = el.extern
        # el must be a Path
        # lhs: zast.Path = el.node

        # loop over the remaining elements 2 at a time making OpArgs
        idx = 1
        n = len(paths)
        while idx < n:
            el = paths[idx]
            path = el.node
            if not isinstance(path, zast.AtomId):
                # print(repr(path))
                msg = "Expected an operator (single identifier)"
                return zast.Error(err=ERR.BADEXPRESSION, msg=msg, loc=el.node.start)
            # start = path.start
            # take the single Id out of the AtomId, nb: externs from here are
            # ignored (operator is a local ref againt the lhs)
            # operator = path.lhs.ids[0]  # too many dots
            operator = path
            idx += 1
            if idx >= n:
                msg = "Expected an operand for the right hand side of operation"
                return zast.Error(err=ERR.BADEXPRESSION, msg=msg, loc=nexttoken)
            el = paths[idx]

            # el is a path, make a BinOp
            operation = zast.BinOp(
                lhs=operation, operator=operator, rhs=el.node, start=start
            )
            promoteexterns(addto=extern, addfrom=el.extern)

            idx += 1

        return NodeX(operation, extern=extern)

    def _acceptcall(
        self, lex: Lexer, paths: List[NodeX[zast.Path]]
    ) -> Union[NodeX[zast.Call], zast.Error, None]:
        """
        Given a list of paths (from getoplist) and a Lexer, return a Call

        lex = lexer, which should be pointing after the last token in paths.
        Named arguments will be consumed from here onward

        paths = list of path/"as" for the callable (in [0]) and the first
        unnamed operation argument (if any)

        Returns Error if cannot accept a Call, otherwise, returns the Call,
        returns None if no paths
        """
        # pylint: disable=too-many-return-statements
        if not paths:
            return None

        el = paths[0]

        # el is a Path
        callablenode: zast.Path = el.node
        arguments: List[zast.NamedOperation] = []
        extern: Dict[str, zast.AtomId] = el.extern

        # assume the rest of paths is a first unnamed argument
        rest = paths[1:]
        opx = self._getop(rest, nexttoken=lex.peek())
        if isinstance(opx, zast.Error):
            return opx  # propagate error
        if opx and isinstance(opx.node, zast.Operation):
            opx = self._fixcalloperation(opx)  # correct single Id's
            namedop = zast.NamedOperation(
                name=None, valtype=opx.node, start=opx.node.start
            )
            arguments.append(namedop)
            promoteexterns(addto=extern, addfrom=opx.extern)
        # else None... no Operation, op is empty (no unnamed first argument)

        # get named arguments
        argnames: Set[str] = set()
        while True:
            label = lex.accept(TT.LABEL)
            is_label_value = False
            if not label:
                label = lex.accept(TT.LABELPRE)
                is_label_value = True
            if not label:
                break
            if label.tokstr in argnames:
                msg = f"Duplicate argument name: {label.tokstr}"
                return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())
            argnames.add(label.tokstr)

            if is_label_value:
                lvx = self._make_label_value(label)
                namedop = zast.NamedOperation(
                    name=label.tokstr, valtype=lvx.node, start=label
                )
                arguments.append(namedop)
                promoteexterns(addto=extern, addfrom=lvx.extern)
            else:
                opx = self._acceptoperation(lex)
                if isinstance(opx, zast.Error):
                    return opx  # propagate error
                if opx:
                    opx = self._fixcalloperation(opx)  # correct single Id's
                    namedop = zast.NamedOperation(
                        name=label.tokstr, valtype=opx.node, start=label
                    )
                    arguments.append(namedop)
                    promoteexterns(addto=extern, addfrom=opx.extern)
                else:
                    msg = f"Expected an Operation after label: {label.tokstr}"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())

        # got a call
        call = zast.Call(
            callable=callablenode, arguments=arguments, start=callablenode.start
        )
        return NodeX(node=call, extern=extern)

    @staticmethod
    def _strip_ownership(
        path: zast.Path,
    ) -> tuple[zast.Path, Optional[ZParamOwnership]]:
        """Check if a type path ends with an ownership annotation (.take/.borrow/.lock).

        Returns (stripped_path, ownership) where ownership is None if no annotation found.
        If the path is e.g. DottedPath(parent=AtomId("point"), child=AtomId("borrow")),
        returns (AtomId("point"), ZParamOwnership.BORROW).
        """
        if isinstance(path, zast.DottedPath):
            suffix = path.child.name
            own = _OWNERSHIP_SUFFIXES.get(suffix)
            if own is not None:
                return path.parent, own
        return path, None

    def _acceptfunction(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Function], zast.Error, None]:
        """
        accept a function

            function
                "function"
                [ [ "in" ] "{" { parameteritem | newline } "}" ]
                [ "out" typeref ]
                [ "is" statement ]

        Returns a Function or Error or None (if no 'function' keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals

        start = lex.peek()
        if not lex.accept(TT.FUNCTION):
            return None

        returntype: Optional[zast.Path] = None
        parameters: Dict[str, zast.Path] = {}
        param_ownership: Dict[str, ZParamOwnership] = {}
        # externs from 'accept' function parameters
        externparam: Dict[str, zast.AtomId] = {}
        # parameter names - local definitions for determining externs
        localparam: Set[str] = set()
        gotaccept = False  # need this because accept could be empty block
        body: Optional[zast.Statement] = None  # None for spec
        externbody: Dict[str, zast.AtomId] = {}  # externs from 'is' function body
        first = True  # true for first arg only (unnamed arg allowed)

        while True:
            tok = lex.peek()

            if lex.accept(TT.OUT):
                if returntype:
                    msg = "Duplicate 'out'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=tok)

                typeref = self._acceptpath(lex)
                if typeref is None:
                    msg = "Expected type reference for 'out'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())

                if isinstance(typeref, zast.Error):
                    return typeref  # propagate any other error

                # check for ownership annotation on the return type path
                stripped_ret, ret_own = self._strip_ownership(typeref.node)
                returntype = stripped_ret
                if ret_own is not None:
                    param_ownership[":return"] = ret_own
                first = False

            elif lex.accept(TT.IS):
                if body:
                    msg = "Duplicate 'is'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=tok)

                statement = self._acceptstatement(lex)
                if statement is None:
                    msg = "Expected Statement for 'is'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())

                if isinstance(statement, zast.Error):
                    return statement  # propagate any other error

                body = statement.node
                externbody = statement.extern
                first = False
            elif (
                lex.accept(TT.IN) or first
            ):  # must be last to handle other keywords first
                if not first:
                    lex.accept(TT.IN)

                if gotaccept:
                    msg = "Duplicate 'in'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=tok)

                if not lex.accept(TT.BRACEOPEN):
                    msg = "Expected open brace '{' after 'in'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())

                while True:
                    lex.accept(TT.EOL)  # optional newline

                    paramnametok = lex.peek()
                    if paramnametok.toktype == TT.LABELPRE:
                        lex.acceptany()
                        paramname = paramnametok.tokstr
                        if paramname in parameters:
                            msg = f"Duplicate parameter name: {paramname}"
                            return zast.Error(err=ERR.BADPARAMETER, msg=msg, loc=tok)
                        lvx = self._make_label_value(paramnametok)
                        parameters[paramname] = lvx.node
                        promoteexterns(addto=externparam, addfrom=lvx.extern)
                        localparam.add(paramname)
                        continue

                    if paramnametok.toktype != TT.LABEL:
                        break

                    lex.acceptany()  # label
                    paramname = paramnametok.tokstr
                    if paramname in parameters:
                        msg = f"Duplicate parameter name: {paramname}"
                        return zast.Error(err=ERR.BADPARAMETER, msg=msg, loc=tok)

                    val = self._acceptpath(lex)
                    if val is None:
                        msg = "Expected typeref or number for parameter type"
                        return zast.Error(
                            err=ERR.BADPARAMETER, msg=msg, loc=lex.acceptany()
                        )

                    if isinstance(val, zast.Error):
                        return val  # propagate error

                    # params cann refer to other params, do local below
                    promoteexterns(addto=externparam, addfrom=val.extern)
                    # check for ownership annotation on the type path
                    stripped, own = self._strip_ownership(val.node)
                    parameters[paramname] = stripped
                    if own is not None:
                        param_ownership[paramname] = own
                    localparam.add(paramname)

                if not lex.accept(TT.BRACECLOSE):
                    msg = "Expected closing brace '}' after function parameters"
                    return zast.Error(
                        err=ERR.BADPARAMETERBLOCK, msg=msg, loc=lex.acceptany()
                    )

                gotaccept = True
                first = False
            else:
                break  # nothing matched, end of function def

        extern: Dict[str, zast.AtomId] = {}
        # promote externs references in params. NB: params can be self referential
        # (ie. for generic params), so check is made against localparams
        promoteexterns(addto=extern, addfrom=externparam, local=localparam)
        promoteexterns(addto=extern, addfrom=externbody, local=localparam)

        func = zast.Function(
            returntype=returntype,
            parameters=parameters,
            body=body,
            start=start,
            param_ownership=param_ownership,
        )
        return NodeX(node=func, extern=extern)

    def _acceptrecord(self, lex: Lexer) -> Union[NodeX[zast.Record], zast.Error, None]:
        """
        accept a record

            record
                "record" [ "is" ] "{" { recorditem | newline } "}"

        Returns a Record or Error or None (if no 'record' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.RECORD):
            return None

        is_body, as_body, extern, err = self._acceptitembodies(
            lex, allowtag=False, unlabelledpath=True, unlabelledid=False
        )
        if err:
            return err

        record = zast.Record(
            items=is_body.items,
            implements=is_body.islist,
            functions=is_body.functions,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
        )
        return NodeX(node=record, extern=extern)

    def _acceptclass(self, lex: Lexer) -> Union[NodeX[zast.Class], zast.Error, None]:
        """
        accept a class

            class
                "class" [ "is" ] "{" { recorditem | newline } "}"

        Returns a Class or Error or None (if no 'class' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.CLASS):
            return None

        is_body, as_body, extern, err = self._acceptitembodies(
            lex, allowtag=False, unlabelledpath=True, unlabelledid=False
        )
        if err:
            return err

        c = zast.Class(
            items=is_body.items,
            implements=is_body.islist,
            functions=is_body.functions,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
        )
        return NodeX(node=c, extern=extern)

    def _acceptvariant(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Variant], zast.Error, None]:
        """
        accept a variant

            variant
                "variant" [ "is" ] "{" { unionitem | newline } "}"

        Returns a Variant or Error or None (if no 'variant' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.VARIANT):
            return None

        is_body, as_body, extern, err = self._acceptitembodies(
            lex, allowtag=True, unlabelledpath=True, unlabelledid=False
        )
        if err:
            return err

        variant = zast.Variant(
            items=is_body.items,
            implements=is_body.islist,
            functions=is_body.functions,
            tag=is_body.tag,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
        )
        return NodeX(node=variant, extern=extern)

    def _acceptunion(self, lex: Lexer) -> Union[NodeX[zast.Union], zast.Error, None]:
        """
        accept a union

            union
                "union" [ "is" ] "{" { unionitem | newline } "}"

        Returns a Union or Error or None (if no 'record' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.UNION):
            return None

        is_body, as_body, extern, err = self._acceptitembodies(
            lex, allowtag=True, unlabelledpath=True, unlabelledid=False
        )
        if err:
            return err

        union = zast.Union(
            items=is_body.items,
            implements=is_body.islist,
            functions=is_body.functions,
            tag=is_body.tag,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
        )
        return NodeX(node=union, extern=extern)

    def _acceptprotocol(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Protocol], zast.Error, None]:
        """
        accept a protocol

            protocol
                "protocol" [ "is" ] "{" { protocolitem | newline } "}"

        Returns a Protocol or Error or None (if no 'protocol' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.PROTOCOL):
            return None

        lex.accept(TT.IS)  # optional 'is'

        b = self._getobjectbody(
            lex, allowtag=False, unlabelledpath=False, unlabelledid=False
        )
        if isinstance(b, zast.Error):
            return b  # propagate error

        protocol = zast.Protocol(
            parameters=b.items,  # 'item's are the generic parameters for protocols
            specs=b.functions,
            includes=b.islist,
            start=start,
        )
        return NodeX(node=protocol, extern=b.extern)

    def _acceptenum(self, lex: Lexer) -> Union[NodeX[zast.Enum], zast.Error, None]:
        """
        accept an enum

            enum
                "enum" [ "is" ] "{" { enumitem | newline } "}"

        Returns an Enum or Error or None (if no 'enum' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.ENUM):
            return None

        is_body, as_body, extern, err = self._acceptitembodies(
            lex, allowtag=True, unlabelledpath=False, unlabelledid=True
        )
        if err:
            return err

        enum = zast.Enum(
            items=is_body.items,
            implements=is_body.islist,
            functions=is_body.functions,
            tag=is_body.tag,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
        )
        return NodeX(node=enum, extern=extern)

    def _acceptfacet(self, lex: Lexer) -> Union[NodeX[zast.Facet], zast.Error, None]:
        """
        accept a facet

            facet
                "facet" [ "is" ] "{" { item | newline } "}"
                [ "as" "{" { item | newline } "}" ]

        Returns a Facet or Error or None (if no 'facet' keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.FACET):
            return None

        is_body, as_body, extern, err = self._acceptitembodies(
            lex, allowtag=False, unlabelledpath=True, unlabelledid=False
        )
        if err:
            return err

        facet = zast.Facet(
            items=is_body.items,
            implements=is_body.islist,
            functions=is_body.functions,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
        )
        return NodeX(node=facet, extern=extern)

    def _acceptitembodies(
        self,
        lex: Lexer,
        allowtag: bool,
        unlabelledpath: bool,
        unlabelledid: bool,
    ) -> tuple:
        """
        Parse 'is' and 'as' bodies for item definitions.
        'is' and 'as' can appear in any order if named.
        Unnamed first arg defaults to 'is'.

        Returns (is_body, as_body, extern, error)
        where is_body is ObjectBody, as_body is Optional[ObjectBody],
        extern is Dict, error is Optional[Error]
        """
        is_body: Optional[ObjectBody] = None
        as_body: Optional[ObjectBody] = None
        extern: Dict[str, zast.AtomId] = {}

        for _ in range(2):  # max 2 iterations: one for is, one for as
            t = lex.peek()
            if t.toktype == TT.IS:
                if is_body is not None:
                    msg = "Duplicate 'is' clause"
                    return None, None, None, zast.Error(err=ERR.BADITEM, msg=msg, loc=t)
                lex.acceptany()
                b = self._getobjectbody(
                    lex,
                    allowtag=allowtag,
                    unlabelledpath=unlabelledpath,
                    unlabelledid=unlabelledid,
                )
                if isinstance(b, zast.Error):
                    return None, None, None, b
                is_body = b
                promoteexterns(addto=extern, addfrom=b.extern)
            elif t.toktype == TT.AS:
                if as_body is not None:
                    msg = "Duplicate 'as' clause"
                    return None, None, None, zast.Error(err=ERR.BADITEM, msg=msg, loc=t)
                lex.acceptany()
                b = self._getobjectbody(
                    lex,
                    allowtag=False,
                    unlabelledpath=unlabelledpath,
                    unlabelledid=False,
                )
                if isinstance(b, zast.Error):
                    return None, None, None, b
                as_body = b
                promoteexterns(addto=extern, addfrom=b.extern)
            elif t.toktype == TT.BRACEOPEN and is_body is None:
                # unnamed first arg defaults to 'is'
                b = self._getobjectbody(
                    lex,
                    allowtag=allowtag,
                    unlabelledpath=unlabelledpath,
                    unlabelledid=unlabelledid,
                )
                if isinstance(b, zast.Error):
                    return None, None, None, b
                is_body = b
                promoteexterns(addto=extern, addfrom=b.extern)
            else:
                break

        if is_body is None:
            msg = "Expected '{' for item body"
            return (
                None,
                None,
                None,
                zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.peek()),
            )

        return is_body, as_body, extern, None

    def _getobjectbody(
        self,
        lex: Lexer,
        # nodetype: zast.NodeType, TODO: add this and remove allowtag, unlabelled...
        allowtag: bool,
        unlabelledpath: bool,
        unlabelledid: bool,
    ) -> Union[ObjectBody, zast.Error]:
        """
        getobjectbody - parse an object body (after 'is') for a record, class,
        variant, union, enum or protocol

        allowtag = allows a 'tag' param. For union, variant, enum

        unlabelledpath = allow an unlabelled path that is named for the id
        after the last dot (if present). (For record, class, union, variant)

        unlabelledid = allow an unlabelled id. name is id, value is None.
        (For enum)

        Only one of allowunlabelled... can be set. Or neither (For protocol)

        Return an Error or the body components in an ObjectBody
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        if not lex.accept(TT.BRACEOPEN):
            msg = "Expected open brace '{' for 'is' argument"
            return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())

        # items=items, islist=islist, functions=functions, tag=tag, extern=extern
        items: Dict[str, zast.Path] = {}  # generic and normal fields
        islist: List[zast.Path] = []  # protocols that this object satisfies
        functions: Dict[str, zast.Function] = {}
        tag: Optional[zast.Path] = None
        extern: Dict[str, zast.AtomId] = {}

        # externs from each item typedefinition
        local: Set[str] = set()  # set of locally defined items
        # 'this' and 'type' are predefined for
        # record, class, variant, enum, protocol(?)
        localthis: Set[str] = set(("this", "type"))
        externitems: Dict[str, zast.AtomId] = {}

        while not lex.accept(TT.BRACECLOSE):
            if lex.accept(TT.EOL):  # optional newline
                continue  # could be trailing, check for BRACECLOSE

            t = lex.peek()
            tt = t.toktype
            if tt == TT.IS:
                # type being implemented (or included for protocols)
                lex.acceptany()
                typerefx = self._acceptpath(lex)
                if typerefx is None:
                    msg = "Expected type reference for 'is'"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())

                if isinstance(typerefx, zast.Error):
                    return typerefx  # propagate any other error

                islist.append(typerefx.node)
                # add directly to extern.. these cannot refer locally
                promoteexterns(addto=extern, addfrom=typerefx.extern)

            elif tt in (TT.LABEL, TT.LABELPRE):
                label = lex.acceptany()
                if tt == TT.LABELPRE:
                    if label.tokstr in items or label.tokstr in functions:
                        msg = f"Duplicate item name: {label.tokstr}"
                        return zast.Error(err=ERR.BADITEM, msg=msg, loc=label)
                    lvx = self._make_label_value(label)
                    local.add(label.tokstr)
                    items[label.tokstr] = lvx.node
                    promoteexterns(addto=externitems, addfrom=lvx.extern)
                else:
                    lex.accept(TT.EOL)  # optional newline
                    # function
                    funcx = self._acceptfunction(lex)
                    if isinstance(funcx, zast.Error):
                        return funcx  # propagate error
                    if funcx:
                        if label.tokstr in items or label.tokstr in functions:
                            msg = f"Duplicate item name: {label.tokstr}"
                            return zast.Error(err=ERR.BADITEM, msg=msg, loc=label)
                        functions[label.tokstr] = funcx.node
                        # add directly to extern.. these cannot refer locally, except via 'this'
                        promoteexterns(addto=extern, addfrom=funcx.extern, local=localthis)
                    else:
                        # path/typeref/typeref_or_num
                        pathx = self._acceptpath(lex)
                        if isinstance(pathx, zast.Error):
                            return pathx  # propagate error
                        if pathx:
                            if label.tokstr in items or label.tokstr in functions:
                                msg = f"Duplicate item name: {label.tokstr}"
                                return zast.Error(err=ERR.BADITEM, msg=msg, loc=label)
                            local.add(label.tokstr)
                            items[label.tokstr] = pathx.node
                            # promote to externitems (will be promoted to extern below, after locals)
                            promoteexterns(addto=externitems, addfrom=pathx.extern)
                        else:
                            # error
                            msg = f"Expected a function or expression for item: {label.tokstr}"
                            return zast.Error(err=ERR.BADITEM, msg=msg, loc=lex.acceptany())

            elif allowtag and tt == TT.TAG:
                lex.acceptany()
                if tag:
                    msg = "Duplicate 'tag' definition"
                    return zast.Error(err=ERR.BADITEM, msg=msg, loc=t)

                typerefx = self._acceptpath(lex)
                if isinstance(typerefx, zast.Error):
                    return typerefx  # propagate error
                if typerefx:
                    tag = typerefx.node
                    promoteexterns(addto=externitems, addfrom=typerefx.extern)
                else:
                    # error
                    msg = "Expected a typeref for 'tag'"
                    return zast.Error(err=ERR.BADITEM, msg=msg, loc=lex.acceptany())

            elif unlabelledpath:
                # try an unnamed path - path can only have an refid at the root...
                if lex.peek().toktype == TT.REFID:
                    dottedidx = self._acceptpath(lex)
                    if isinstance(dottedidx, zast.Error):
                        return dottedidx  # propagate error
                    if dottedidx is not None:
                        # if isinstance(atomid, zast.AtomId):
                        # name = atomid.node.ids[-1].name
                        dottedid = dottedidx.node
                        if isinstance(dottedid, zast.AtomId):
                            name = dottedid.name
                        elif isinstance(dottedid, zast.DottedPath):
                            name = dottedid.child.name  # name is after last dot
                        else:
                            # cannot happen
                            msg = "Unknown DottedId type"
                            return zast.Error(
                                err=ERR.BADITEM, msg=msg, loc=lex.acceptany()
                            )

                        if name in items or name in functions:
                            msg = f"Duplicate item name: {name}"
                            return zast.Error(
                                err=ERR.BADITEM, msg=msg, loc=dottedid.start
                            )
                        local.add(name)
                        items[name] = dottedid
                        # cannot refer to locals
                        promoteexterns(addto=externitems, addfrom=dottedidx.extern)
                    else:
                        # no tottedidx, this can't happen, we have a LABEL above...
                        pass
                else:
                    msg = "Expected a label, unlabelled expression or closing brace"
                    return zast.Error(err=ERR.BADITEM, msg=msg, loc=lex.acceptany())
            elif unlabelledid:
                # an unnamed id... for enum only
                atomidx = self._acceptatomid(lex)
                if atomidx:
                    atomid = atomidx.node
                    name = atomid.name
                    local.add(name)
                    items[name] = atomid  # the atomid value is itself...
                    # do NOT promoteexterns... this is an enum item (not a reference)
                else:
                    msg = "Expected a label, an id or closing brace"
                    return zast.Error(err=ERR.BADITEM, msg=msg, loc=lex.acceptany())
            else:
                # error - no other options
                msg = "Expected a label, expression or closing brace"
                return zast.Error(err=ERR.BADITEM, msg=msg, loc=lex.acceptany())

        # extern - add extern from items (skipping locals) since these can be self referntial
        promoteexterns(addto=extern, addfrom=externitems, local=local)

        return ObjectBody(
            items=items, islist=islist, functions=functions, tag=tag, extern=extern
        )

    def _acceptsubunit(self, lex: Lexer) -> Union[NodeX[zast.Unit], zast.Error, None]:
        """
        _acceptsubunit - accept a subunit (unit keyword)

        Return a Unit or Error or None for no unit (missing "unit" keyword)

            unit
                "unit" [ "is" ] "{" definitionlist "}"
        """
        start = lex.peek()
        if not lex.accept(TT.UNIT):
            return None

        lex.accept(TT.AS)  # optional 'as'

        if not lex.accept(TT.BRACEOPEN):
            msg = "Expected brace delimited 'is' argument for Unit body"
            return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.peek())

        unitorerr = self._acceptunitbody(lex)
        if isinstance(unitorerr, zast.Error):
            err = unitorerr
            return err

        # check '}'
        if not lex.accept(TT.BRACECLOSE):
            msg = "Expected '}' at end of sub-unit or new line between definitions"
            err = zast.Error(err=ERR.BADUNIT, msg=msg, loc=lex.peek())
            return err

        unitorerr.node.start = start  # change start to 'unit' keyword
        return unitorerr

    def _acceptif(self, lex: Lexer) -> Union[NodeX[zast.If], zast.Error, None]:
        """
        acceptif - accept an if clause (conditional)

            "if" [ operation ]
            { "when" [ newline ] operation }
            "then" statement
            { "when" [ newline ] operation }
            "then" statement
            ...
            [ "else" statement ]


        Return an If or Error or None for no unit (missing "if" keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        start = lex.peek()
        if not lex.accept(TT.IF):
            return None

        clauses: List[zast.IfClause] = []
        elseclause: Optional[zast.Statement] = None
        conditions: Dict[str, zast.Operation] = {}  # for each IfClause
        local: Set[str] = set()  # for each IfClause
        startclause = lex.peek()  # for each IfClause

        extern: Dict[str, zast.AtomId] = {}

        first = True  # first is allowed to not have a label/"when"
        whenindex = 0  # counter for 'fake' binding names (for 'when' clauses)
        while True:
            t = lex.peek()
            if first or t.toktype == TT.WHEN:
                if t.toktype == TT.WHEN:
                    lex.acceptany()  # 'when'
                    lex.accept(TT.EOL)  # optional EOL

                op = self._acceptoperation(lex)
                if isinstance(op, zast.Error):
                    return op  # propagate error
                if not op:
                    msg = "Expected operation (condition) for 'if'"
                    return zast.Error(err=ERR.EXPECTEDOP, msg=msg, loc=lex.acceptany())

                # note leading space - cannot collide with real bindings
                conditions[f" *{whenindex}"] = op.node
                whenindex += 1
                # nb: local - can refer to prior bindings...
                promoteexterns(addto=extern, addfrom=op.extern, local=local)
                first = False

            elif t.toktype == TT.THEN:
                if not conditions:
                    msg = "'then' must appear after at least one condition"
                    return zast.Error(err=ERR.BADTHEN, msg=msg, loc=t)
                lex.acceptany()
                statementx = self._acceptstatement(lex)
                if isinstance(statementx, zast.Error):
                    return statementx  # propagate error
                if not statementx:
                    msg = "Expected statement for 'then'"
                    return zast.Error(
                        err=ERR.EXPECTEDSTATEMENT, msg=msg, loc=lex.acceptany()
                    )
                promoteexterns(addto=extern, addfrom=statementx.extern, local=local)
                ifclause = zast.IfClause(
                    conditions=dict(conditions),
                    statement=statementx.node,
                    start=startclause,
                )
                clauses.append(ifclause)
                # reset for another ifclause
                conditions.clear()
                local.clear()
                startclause = lex.peek()

            else:
                break  # nothing matched, end of ifclauses

        if conditions:
            msg = "Expected 'then' after conditions"
            return zast.Error(err=ERR.BADELSE, msg=msg, loc=t)

        t = lex.peek()
        if t.toktype == TT.ELSE:
            if not clauses:
                msg = "'else' must appear after at least one if/then clause"
                return zast.Error(err=ERR.BADELSE, msg=msg, loc=t)
            lex.acceptany()
            statementx = self._acceptstatement(lex)
            if isinstance(statementx, zast.Error):
                return statementx  # propagate error
            if not statementx:
                msg = "Expected statement for 'else'"
                return zast.Error(
                    err=ERR.EXPECTEDSTATEMENT, msg=msg, loc=lex.acceptany()
                )

            # local not available for else
            promoteexterns(addto=extern, addfrom=statementx.extern)
            elseclause = statementx.node

        ifnode = zast.If(clauses=clauses, elseclause=elseclause, start=start)
        return NodeX(ifnode, extern=extern)

    def _acceptmatch(self, lex: Lexer) -> Union[NodeX[zast.Case], zast.Error, None]:
        """
        acceptcase - accept a match clause (exhaustive conditional)

            "match"
            ( ["on"] operation )
            {
                (
                    ( "case" [ newline ] id )
                    | ( label [ newline ] id )
                )
                "then" statement
            }
            | ( "else" statement )

        Return a Case or Error or None for no unit (missing "match" keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        start = lex.peek()
        if not lex.accept(TT.MATCH):
            return None

        subject: Optional[zast.Operation]  # required
        clauses: List[zast.CaseClause] = []
        elseclause: Optional[zast.Statement] = None

        extern: Dict[str, zast.AtomId] = {}

        # -- 'on'
        lex.accept(TT.ON)  # optional 'on'
        op = self._acceptoperation(lex)
        if isinstance(op, zast.Error):
            return op  # propagate error
        if not op:
            msg = "Expected operation for 'match' (subject)"
            return zast.Error(err=ERR.EXPECTEDOP, msg=msg, loc=lex.acceptany())

        subject = op.node
        promoteexterns(addto=extern, addfrom=op.extern)

        ofindex = 0  # counter for 'fake' binding names (for 'case' clauses)
        while True:
            startclause = lex.peek()  # for each CaseClause
            local: Set[str] = set()  # for each CaseClause

            t = lex.peek()
            if t.toktype not in (TT.LABEL, TT.CASE):
                break  # end of clauses

            # ----- get label or 'case' (get name)
            name: str
            if t.toktype == TT.LABEL:
                name = t.tokstr
                local.add(name)
            else:  # t.toktype == TT.CASE:
                name = " *{ofindex}"
                ofindex += 1
            lex.acceptany()  # label/'case'
            lex.accept(TT.EOL)  # optional EOL

            # ----- get id

            curid: zast.AtomId
            atomidx = self._acceptatomid(lex)
            if isinstance(atomidx, zast.Error):
                return atomidx  # propagate error
            if atomidx:
                # do NOT promoteexterns... the id must be a member of the 'in' operation
                curid = atomidx.node
            else:
                msg = "Case match expression expected (simple id)"
                return zast.Error(err=ERR.BADREFERENCE, msg=msg, loc=lex.acceptany())

            # ----- get then

            t = lex.peek()
            if t.toktype != TT.THEN:
                msg = "Expected 'then' keyword for 'case'"
                return zast.Error(err=ERR.BADCASE, msg=msg, loc=lex.acceptany())

            lex.acceptany()  # 'then'
            statementx = self._acceptstatement(lex)
            if isinstance(statementx, zast.Error):
                return statementx  # propagate error
            if not statementx:
                msg = "Expected statement for 'then'"
                return zast.Error(
                    err=ERR.EXPECTEDSTATEMENT, msg=msg, loc=lex.acceptany()
                )

            promoteexterns(addto=extern, addfrom=statementx.extern, local=local)
            caseclause = zast.CaseClause(
                name=name, match=curid, statement=statementx.node, start=startclause
            )
            clauses.append(caseclause)

        if lex.accept(TT.ELSE):
            statementx = self._acceptstatement(lex)
            if isinstance(statementx, zast.Error):
                return statementx  # propagate error
            if not statementx:
                msg = "Expected statement after 'else' for 'case'"
                return zast.Error(
                    err=ERR.EXPECTEDSTATEMENT, msg=msg, loc=lex.acceptany()
                )

            # local not available for else
            promoteexterns(addto=extern, addfrom=statementx.extern)
            elseclause = statementx.node

        casenode = zast.Case(
            subject=subject, clauses=clauses, elseclause=elseclause, start=start
        )
        return NodeX(casenode, extern=extern)

    def _acceptfor(self, lex: Lexer) -> Union[NodeX[zast.For], zast.Error, None]:
        """
        acceptfor - accept a for clause (iteration)

            "for" [ operation ]
            {
                ( "while" [ newline ] operation )
                | ( label [ newline ] operation )
            }
            [ "loop" statement ]
            { "while" [ newline ] operation }

        Return an For or Error or None for no unit (missing "for" keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        start = lex.peek()
        if not lex.accept(TT.FOR):
            return None

        # clauses: List[zast.IfClause] = []
        # elseclause: Optional[zast.Statement] = None
        conditions: Dict[str, zast.Operation] = {}
        postconditions: List[zast.Operation] = []
        loop: Optional[zast.Statement] = None
        local: Set[str] = set()
        # startclause = lex.peek()  # for each IfClause

        extern: Dict[str, zast.AtomId] = {}

        first = True  # first is allowed to not have a label/"when"
        whileindex = 0  # counter for 'fake' binding names (for 'when' clauses)
        while True:
            t = lex.peek()
            if (first or t.toktype == TT.WHILE) and t.toktype != TT.LABEL:
                if t.toktype == TT.WHILE:
                    lex.acceptany()  # 'while'
                    lex.accept(TT.EOL)  # optional EOL

                op = self._acceptoperation(lex)
                if isinstance(op, zast.Error):
                    return op  # propagate error
                if not op:
                    msg = "Expected operation (condition) for 'for'"
                    return zast.Error(err=ERR.EXPECTEDOP, msg=msg, loc=lex.acceptany())

                if loop:
                    postconditions.append(op.node)
                else:
                    # note leading space - cannot collide with real bindings
                    conditions[f" *{whileindex}"] = op.node
                    whileindex += 1

                # nb: local - can refer to prior bindings...
                promoteexterns(addto=extern, addfrom=op.extern, local=local)
                first = False

            elif t.toktype == TT.LABEL:
                if loop:
                    break  # label after loop belongs to the enclosing scope
                name = lex.acceptany().tokstr
                lex.accept(TT.EOL)  # optional EOL
                op = self._acceptoperation(lex)
                if isinstance(op, zast.Error):
                    return op  # propagate error
                if not op:
                    msg = f"Expected operation for 'for' binding label: {name}"
                    return zast.Error(err=ERR.EXPECTEDOP, msg=msg, loc=lex.acceptany())

                conditions[name] = op.node
                # nb: local - can refer to prior bindings...
                promoteexterns(addto=extern, addfrom=op.extern, local=local)
                local.add(name)
                first = False

            elif t.toktype == TT.LOOP:
                if loop:
                    msg = "Duplicate 'loop'"
                    return zast.Error(err=ERR.BADFOR, msg=msg, loc=lex.acceptany())

                lex.acceptany()
                statementx = self._acceptstatement(lex)
                if isinstance(statementx, zast.Error):
                    return statementx  # propagate error
                if not statementx:
                    msg = "Expected statement for 'loop'"
                    return zast.Error(
                        err=ERR.EXPECTEDSTATEMENT, msg=msg, loc=lex.acceptany()
                    )
                promoteexterns(addto=extern, addfrom=statementx.extern, local=local)
                loop = statementx.node

            else:
                break  # nothing matched, end of 'if'

        if not conditions and not loop:
            msg = "Require at least one condition or a 'loop' specified for 'for'"
            return zast.Error(err=ERR.BADFOR, msg=msg, loc=lex.acceptany())

        fornode = zast.For(
            conditions=conditions, loop=loop, postconditions=postconditions, start=start
        )
        return NodeX(fornode, extern=extern)

    def _acceptdo(self, lex: Lexer) -> Union[NodeX[zast.Do], zast.Error, None]:
        """
        acceptdo - accept a do clause (sequence)

            "do" [ "block" ] statement

        Return an Do or Error or None for no unit (missing "do" keyword)
        """
        start = lex.peek()
        if not lex.accept(TT.DO):
            return None

        extern: Dict[str, zast.AtomId] = {}

        lex.accept(TT.BLOCK)  # optional 'block'

        statementx = self._acceptstatement(lex)
        if isinstance(statementx, zast.Error):
            return statementx  # propagate error
        if not statementx:
            msg = "Expected statement for 'do'"
            return zast.Error(err=ERR.EXPECTEDSTATEMENT, msg=msg, loc=lex.acceptany())
        promoteexterns(addto=extern, addfrom=statementx.extern)

        donode = zast.Do(statement=statementx.node, start=start)
        return NodeX(donode, extern=extern)

    def _acceptwith(self, lex: Lexer) -> Union[NodeX[zast.With], zast.Error, None]:
        """
        acceptwith - accept a with expression (scoped definition)

            "with" label operation "do" expression

        Return a With or Error or None for no 'with' keyword
        """
        start = lex.peek()
        if not lex.accept(TT.WITH):
            return None

        extern: Dict[str, zast.AtomId] = {}

        # expect label (name:)
        t = lex.peek()
        if t.toktype != TT.LABEL:
            msg = "Expected label after 'with'"
            return zast.Error(err=ERR.EXPECTEDDEF, msg=msg, loc=lex.acceptany())
        name = t.tokstr
        lex.acceptany()  # consume the label

        # accept the value expression
        valuex = self._acceptexpression(lex)
        if isinstance(valuex, zast.Error):
            return valuex
        if not valuex:
            msg = "Expected expression for 'with' value"
            return zast.Error(err=ERR.EXPECTEDEXP, msg=msg, loc=lex.acceptany())

        # the name is locally defined, don't propagate it as extern
        promoteexterns(addto=extern, addfrom=valuex.extern)

        # expect 'do'
        if not lex.accept(TT.DO):
            msg = "Expected 'do' after 'with' definition"
            return zast.Error(err=ERR.EXPECTEDEXP, msg=msg, loc=lex.peek())

        # accept the do expression - the name is in scope here
        doexprx = self._acceptexpression(lex)
        if isinstance(doexprx, zast.Error):
            return doexprx
        if not doexprx:
            msg = "Expected expression after 'do'"
            return zast.Error(err=ERR.EXPECTEDEXP, msg=msg, loc=lex.acceptany())

        # the locally defined name should not be promoted as extern
        promoteexterns(addto=extern, addfrom=doexprx.extern)
        extern.pop(name, None)  # remove locally defined name

        withnode = zast.With(
            name=name, value=valuex.node, doexpr=doexprx.node, start=start
        )
        return NodeX(withnode, extern=extern)

    def _acceptdata(self, lex: Lexer) -> Union[NodeX[zast.Data], zast.Error, None]:
        """
        acceptdata - accept a data clause

            "data" [ "is" ] "{" { ( [ label ] term ) | label_value } "}"

        Return a Data or Error or None for no data (missing "data" keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        start = lex.peek()
        if not lex.accept(TT.DATA):
            return None

        subtype: Optional[zast.Path] = None
        data: List[zast.NamedOperation] = []
        extern: Dict[str, zast.AtomId] = {}

        lex.accept(TT.IS)  # optional 'is'

        if not lex.accept(TT.BRACEOPEN):
            msg = "Expected opening brace '{' for data body"
            return zast.Error(err=ERR.BADDATA, msg=msg, loc=lex.acceptany())

        datanames: Set[str] = set()
        while True:
            if lex.peek().toktype == TT.LABELPRE:
                label = lex.acceptany()
                if label.tokstr in datanames:
                    msg = f"Duplicate data member name: {label.tokstr}"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())
                datanames.add(label.tokstr)
                lvx = self._make_label_value(label)
                namedop = zast.NamedOperation(
                    name=label.tokstr, valtype=lvx.node, start=label
                )
                data.append(namedop)
                promoteexterns(addto=extern, addfrom=lvx.extern)
                continue

            label: Optional[Token] = None
            if lex.peek().toktype == TT.LABEL:
                label = lex.acceptany()
                if label.tokstr in datanames:
                    msg = f"Duplicate data member name: {label.tokstr}"
                    return zast.Error(err=ERR.BADARGUMENT, msg=msg, loc=lex.acceptany())
                datanames.add(label.tokstr)

            pathx = self._acceptpath(lex)
            if isinstance(pathx, zast.Error):
                return pathx  # propagate error
            if pathx:
                if label:
                    namedop = zast.NamedOperation(
                        name=label.tokstr, valtype=pathx.node, start=label
                    )
                else:
                    namedop = zast.NamedOperation(
                        name=None, valtype=pathx.node, start=pathx.node.start
                    )
                promoteexterns(addto=extern, addfrom=pathx.extern)
                data.append(namedop)
            else:
                # no path, finished block
                break

        if not lex.accept(TT.BRACECLOSE):
            msg = "Expected closing brace '}' for data body"
            return zast.Error(err=ERR.BADDATA, msg=msg, loc=lex.acceptany())

        datanode = zast.Data(subtype=subtype, data=data, start=start)
        return NodeX(datanode, extern=extern)

    def _acceptoperation(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Operation], zast.Error, None]:
        """
        Return an Operation. Will consume all Path/TT.AS

            operation
                path { ( "as" typeref ) | ( id path ) }

        """
        oplist = self._getoplist(lex)
        if isinstance(oplist, zast.Error):
            return oplist  # propagate error
        return self._getop(paths=oplist, nexttoken=lex.peek())

    @staticmethod
    def _fixcalloperation(opx: NodeX[zast.Operation]) -> NodeX[zast.Operation]:
        """

        fixcalloperation - check a call value to see if it is a single Id. If
        so, update the Id definition to show that this cannot be a module
        reference.

        Used for call arguments.

        """
        if isinstance(opx.node, zast.AtomId):
            atomid = opx.node
            newid = zast.AtomId(
                name=atomid.name, canbemoduleref=False, start=atomid.start
            )
            newextern: Dict[str, zast.AtomId] = {}
            newextern[newid.name] = newid
            return NodeX(node=newid, extern=newextern)

        return opx

    def _acceptstatement(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Statement], zast.Error, None]:
        """
        acceptstatement - accept a statement clause

            ( "{" [ blockline ] { ( eol | ";" ) [ blockline ] } "}" )
            | operation

        Return an Array or Error or None (no braceopen or operation)

        """
        statements: List[zast.StatementLine] = []
        extern: Dict[str, zast.AtomId] = {}
        local: Set[str] = set()  # for local definitions

        start = lex.peek()

        if lex.accept(TT.BRACEOPEN):
            # block
            prev_filtereol = lex._filtereol
            lex.filtereol(False)  # disable EOL filtering. Need to parse them correctly
            error: Optional[zast.Error] = None
            while True:
                # consume all separators (EOL and ";")
                while lex.accept(TT.EOL) or lex.accept(TT.SEMICOLON):
                    pass
                statementlinex = self._acceptstatementline(lex)
                if isinstance(statementlinex, zast.Error):
                    error = statementlinex  # propagate error
                    break
                if not statementlinex:
                    break  # end of block (no final EOL?)
                statementline = statementlinex.node
                statements.append(statementline)

                # add references
                promoteexterns(addto=extern, addfrom=statementlinex.extern, local=local)

                # add any local definition
                if isinstance(statementline.statementline, zast.Assignment):
                    name = statementline.statementline.name
                    if name in local:
                        # duplicate definition
                        msg = f'Duplicate definition of "{name}"'
                        error = zast.Error(
                            err=ERR.DUPLICATEDEF,
                            msg=msg,
                            loc=statementline.statementline.start,
                        )
                        break
                    local.add(name)

            lex.filtereol(prev_filtereol)  # restore EOL filtering
            if error:
                return error

            if not lex.accept(TT.BRACECLOSE):
                msg = "Expected closing brace '}' for statement body"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=lex.acceptany())

            statement = zast.Statement(statements=statements, start=start)
            return NodeX(statement, extern=extern)

        # bare statement (no braces) - single statementline
        statementlinex = self._acceptstatementline(lex)
        if isinstance(statementlinex, zast.Error):
            return statementlinex
        if not statementlinex:
            return None

        promoteexterns(addto=extern, addfrom=statementlinex.extern)
        statement = zast.Statement(statements=[statementlinex.node], start=start)
        return NodeX(statement, extern=extern)

    def _acceptstatementline(
        self, lex: Lexer
    ) -> Union[NodeX[zast.StatementLine], zast.Error, None]:
        """
        acceptstatementline - accept a statementline clause

            ( label [ newline ] expression )
            | ( path "=" expression )
            | ( expression )

        Return a StatementLine or Error or None
        """
        # pylintxxx: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        extern: Dict[str, zast.AtomId] = {}
        start = lex.peek()

        if start.toktype == TT.LABELPRE:  # label value assignment
            lex.acceptany()
            lvx = self._make_label_value(start)
            assignment = zast.Assignment(
                name=start.tokstr, value=lvx.node, start=start
            )
            statementline = zast.StatementLine(statementline=assignment, start=start)
            return NodeX(node=statementline, extern=lvx.extern)

        if start.toktype == TT.LABEL:  # an assignment to new var
            lex.acceptany()  # label
            lex.accept(TT.EOL)  # optional newline
            exprx = self._acceptexpression(lex)
            if isinstance(exprx, zast.Error):
                return exprx  # propagate error
            if not exprx:
                msg = "Expected expression for assignment statement"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=lex.acceptany())
            assignment = zast.Assignment(
                name=start.tokstr, value=exprx.node, start=start
            )
            statementline = zast.StatementLine(statementline=assignment, start=start)
            return NodeX(node=statementline, extern=exprx.extern)

        # now for the hard ones....
        oplist = self._getoplist(lex)
        if isinstance(oplist, zast.Error):
            return oplist  # propagate error

        if lex.accept(TT.EQUALS):  # Reassignment
            # get LHS from oplist
            if not oplist:
                msg = "Reassignment requires a left hand side"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=start)
            lhsx = oplist[0]
            if len(oplist) != 1:
                msg = "Reassignment must be to a single path on the LHS"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=start)
            # lhsx is a pathx
            promoteexterns(addto=extern, addfrom=lhsx.extern)

            # get RHS
            rhsx = self._acceptexpression(lex)
            if isinstance(rhsx, zast.Error):
                return rhsx  # propagate error
            if not rhsx:
                msg = "Expected an expression for the RHS of a reassignment"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=lex.acceptany())

            promoteexterns(addto=extern, addfrom=rhsx.extern)
            reassignment = zast.Reassignment(
                topath=lhsx.node, value=rhsx.node, start=start
            )
            statementline = zast.StatementLine(statementline=reassignment, start=start)
            return NodeX(node=statementline, extern=extern)

        if lex.accept(TT.SWAP):  # a swap
            if not oplist:
                msg = "Swap requires a left hand side"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=start)
            lhsx = oplist[0]
            if len(oplist) != 1:
                msg = "Swap must be to a single path on the LHS"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=start)
            # lhsx is a pathx
            promoteexterns(addto=extern, addfrom=lhsx.extern)

            # get RHS
            rhsx = self._acceptpath(lex)
            if isinstance(rhsx, zast.Error):
                return rhsx  # propagate error
            if not rhsx:
                msg = "Swap requires a right hand side"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=start)

            promoteexterns(addto=extern, addfrom=rhsx.extern)
            swap = zast.Swap(lhs=lhsx.node, rhs=rhsx.node, start=start)
            statementline = zast.StatementLine(statementline=swap, start=start)
            return NodeX(node=statementline, extern=extern)

        if oplist:  # consumed tokens, need to check if op or call now (else error)
            oporcallx = self._acceptoperationorcall(oplist, lex)
            if not oporcallx:
                # this shouldn't happen, we know oplist is not empty
                msg = "Bad statement"
                return zast.Error(err=ERR.BADSTATEMENT, msg=msg, loc=start)

            if isinstance(oporcallx, zast.Error):
                return oporcallx  # propagate error

            # must be Operation or Call
            promoteexterns(addto=extern, addfrom=oporcallx.extern)
            expr = zast.Expression(expression=oporcallx.node, start=start)
            statementline = zast.StatementLine(statementline=expr, start=start)
            return NodeX(node=statementline, extern=extern)

        # haven't consumed anything yet..
        # must be an expression (but not a operation or call); or an error
        exprx = self._acceptexpression(lex)
        if isinstance(exprx, zast.Error):
            return exprx  # propagate error
        if not exprx:
            return None  # haven't consumed anything... not a statementline
        statementline = zast.StatementLine(statementline=exprx.node, start=start)
        return NodeX(node=statementline, extern=exprx.extern)

    def _acceptpath(self, lex: Lexer) -> Union[NodeX[zast.Path], zast.Error, None]:
        """
        accept a path/atom.

            atom
            | ( path "." atomid )

        Returns the Path (NodeX) or an Error or None if no atom found
        """
        start = lex.peek()
        atomx = self._acceptatom(lex)
        if atomx is None:
            return None

        if isinstance(atomx, zast.Error):
            return atomx  # propagate error

        if lex.peek().toktype != TT.DOT:
            return atomx  # atom only

        path: Union[zast.DottedPath, zast.Atom] = atomx.node

        while lex.accept(TT.DOT):
            t = lex.accept(TT.REFID)
            if t:
                c = zast.AtomId(start=t, name=t.tokstr, canbemoduleref=False)
                path = zast.DottedPath(parent=path, child=c, start=start)
            else:
                # error
                msg = "Expected ID after dot"
                err = zast.Error(err=ERR.BADPATH, msg=msg, loc=lex.acceptany())
                return err

        return NodeX(node=path, extern=atomx.extern)  # only atomx is extern

    def _acceptatom(self, lex: Lexer) -> Union[NodeX[zast.Atom], zast.Error, None]:
        """
        accept an atom

            atom
                atomexpr
                | atomid
                | atomstring
                | atomstringraw

        Returns the Atom (NodeX) or an Error or None if no atom found
        """
        t = lex.peek()
        tt = t.toktype
        if tt == TT.PARENOPEN:
            return self._acceptatomexpr(lex)

        if tt == TT.REFID:
            return self._acceptatomid(lex)

        if tt == TT.STRBEG:
            return self._acceptatomstring(lex)

        return None

    @staticmethod
    def _acceptatomid(lex: Lexer) -> Union[NodeX[zast.AtomId], None]:
        """
        accept an atomid

            atomid
                id

        Returns the AtomId (NodeX) or None if no atom found
        Can never return zast.Error
        """
        start = lex.accept(TT.REFID)
        if not start:
            return None

        # numeric literals (starting with digit or sign+digit) are predeclared
        # identifiers — not external references and not module refs
        name = start.tokstr
        c0 = name[0]
        is_numeric = c0.isdigit() or (
            c0 in ("+", "-") and len(name) > 1 and name[1].isdigit()
        )

        atomid: zast.AtomId = zast.AtomId(
            start=start, name=name, canbemoduleref=not is_numeric
        )
        extern: Dict[str, zast.AtomId] = {}
        if not is_numeric:
            extern[name] = atomid  # atom itself is an external reference
        return NodeX(node=atomid, extern=extern)

    def _acceptatomexpr(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """
        accept an AtomExpr, from the opening paren

            atomexpr
                "(" expression ")"

        Return the expression or an Error or None if no atomexpr found (no PARENOPEN)
        """
        t = lex.peek()
        if not lex.accept(TT.PARENOPEN):
            return None

        error: Optional[zast.Error] = None

        prev_filtereol = lex._filtereol
        lex.filtereol(True)  # enable EOL filtering. EOL's are ignored within parens

        expr = self._acceptexpression(lex)
        while True:
            if expr is None:
                msg = "Expected a valid expression within parentheses"
                error = zast.Error(err=ERR.BADEXPRESSION, msg=msg, loc=t)
                break

            if isinstance(expr, zast.Error):
                error = expr
                break

            # restore EOL filtering BEFORE consuming ')' so that _advance
            # reads the token after ')' with the correct filtering state;
            # otherwise the EOL after ')' gets silently skipped
            lex.filtereol(prev_filtereol)

            if not lex.accept(TT.PARENCLOSE):
                msg = "Expected closing parenthesis ')' after expression"
                error = zast.Error(err=ERR.BADEXPRESSION, msg=msg, loc=lex.peek())
                break

            break

        lex.filtereol(prev_filtereol)  # restore (also covers error paths)
        if error:
            return error

        # we have a valid expression that was surrounded by parens
        return expr

    def _acceptatomstring(
        self, lex: Lexer
    ) -> Union[NodeX[zast.AtomString], zast.Error, None]:
        """
        accept an AtomString

            atomstring
                "\"" { strmid | newline | strchr | strexpr } "\""

            atomstringraw
                "`" { strmid | newline } "`"

        Return the atomexpr or an Error or None if no atomexpr found (no STRBEG)
        """
        # pylint: disable=too-many-return-statements
        firsttoken = lex.peek()
        if not lex.accept(TT.STRBEG):
            return None

        stringparts: List[Union[Token, zast.Expression]] = []
        extern: Dict[str, zast.AtomId] = {}

        while True:
            t = lex.peek()
            tt = t.toktype
            if tt == TT.STREND:
                if t.tokstr != firsttoken.tokstr:
                    msg = "Unmatched string literal delimiter"
                    return zast.Error(err=ERR.BADSTRING, msg=msg, loc=t)

                lex.acceptany()
                break  # end of string
            if tt in (TT.STRMID, TT.STRCHR, TT.EOL):
                lex.acceptany()
                stringparts.append(t)
            elif tt == TT.STREXPRBEG:
                lex.acceptany()
                # string interpolation is \{expr} - braces are consumed by
                # the lexer state machine (STRINGEXPR/STRINGEXPRBRACE)
                if not lex.accept(TT.BRACEOPEN):
                    msg = "Expected '{' after '\\' in string interpolation"
                    return zast.Error(err=ERR.BADSTRING, msg=msg, loc=lex.peek())
                expr = self._acceptexpression(lex)
                if expr is None:
                    msg = "Bad expression in string interpolation"
                    return zast.Error(err=ERR.BADEXPRESSION, msg=msg, loc=lex.peek())
                if isinstance(expr, zast.Error):
                    return expr  # propagate error
                stringparts.append(expr.node)
                # update new with old to retain old values
                expr.extern.update(extern)
                extern = expr.extern
                if not lex.accept(TT.BRACECLOSE):
                    msg = "Expected '}' after string interpolation expression"
                    return zast.Error(err=ERR.BADSTRING, msg=msg, loc=lex.peek())
            else:
                # error
                t = lex.acceptany()
                msg = "Unexpected token in string literal"
                return zast.Error(err=ERR.BADSTRING, msg=msg, loc=t)

        atomstring = zast.AtomString(stringparts=stringparts, start=firsttoken)
        return NodeX(node=atomstring, extern=extern)
