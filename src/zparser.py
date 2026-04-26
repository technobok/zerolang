#!/usr/bin/python3
"""
ZeroLang parser
"""

# pylint: disable=too-many-lines
from enum import IntEnum, auto
from typing import List, Dict, Optional, Union, TypeVar, Generic, Set, cast
from dataclasses import dataclass, field
from zvfs import ZVfs, DEntryID, DEntryType, ZVfsOpenFile
from zlexer import Lexer, Tokenizer, Token, isvalidunitname
from ztokentype import TT
import zast
from zast import ERR, NodeType, _ERROR_TOKEN
from ztypes import ZParamOwnership

# ownership annotation suffixes recognized on dotted type paths
_OWNERSHIP_SUFFIXES = {
    "take": ZParamOwnership.TAKE,
    "borrow": ZParamOwnership.BORROW,
    "lock": ZParamOwnership.LOCK,
}


class ObjectBodyKind(IntEnum):
    """
    What kind of item is having its body parsed. Replaces the legacy
    (allowtag, unlabelledpath, unlabelledid) triple-bool; the bools are
    derived in `_get_object_body` from kind + whether this is the `as`
    clause (static members) or the `is` clause (instance members).

    - FUNCTION_AS: generic params for a function's `as` clause (named only)
    - PROTOCOL / FACET: interface / value-type interface bodies (named only)
    - RECORD / CLASS: struct-like items — unlabelled paths are field types
      named by their path leaf
    - VARIANT / UNION: struct-like with an optional `tag:` declaration
    - ENUM: bare ids as values (unlabelled id permitted)
    """

    FUNCTION_AS = auto()
    PROTOCOL = auto()
    FACET = auto()
    RECORD = auto()
    CLASS = auto()
    VARIANT = auto()
    UNION = auto()
    ENUM = auto()


_OBJECT_BODY_ALLOWS_UNLABELLED_PATH = {
    ObjectBodyKind.RECORD,
    ObjectBodyKind.CLASS,
    ObjectBodyKind.VARIANT,
    ObjectBodyKind.UNION,
}
_OBJECT_BODY_ALLOWS_UNLABELLED_ID = {
    ObjectBodyKind.ENUM,
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
    is_error: bool = field(default=False, init=False)


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
    # field name -> ownership annotation stripped from the field's type path
    # (currently only .lock is permitted on fields)
    field_ownership: Dict[str, ZParamOwnership] = field(default_factory=dict)
    is_error: bool = field(default=False, init=False)


def _is_ws_only(s: str) -> bool:
    """Check if string contains only spaces and tabs."""
    return all(c in (" ", "\t") for c in s)


def _strip_string_whitespace(
    parts: List[Union[Token, "zast.Expression"]],
) -> List[Union[Token, "zast.Expression"]]:
    """Apply string newline/whitespace handling rules from the spec.

    1. If the first line is blank (whitespace only), exclude it + its newline.
    2. If the last line is blank (whitespace only), exclude the final newline
       and whitespace, and use that whitespace as a common prefix to strip.
    3. Strip the common prefix from every non-blank line.
    """
    if not parts:
        return parts

    # Step 1: strip blank first line (whitespace-only tokens followed by EOL)
    first_eol = -1
    for i, p in enumerate(parts):
        if p.is_expression:
            break
        tok = cast(Token, p)
        if tok.toktype == TT.EOL:
            first_eol = i
            break
        if tok.toktype == TT.STRMID and _is_ws_only(tok.tokstr):
            continue
        if tok.toktype == TT.STRCHR:
            break
        break
    if first_eol >= 0:
        # first line is blank — remove everything up to and including the EOL
        parts = parts[first_eol + 1 :]

    if not parts:
        return parts

    # Step 2: check for blank last line — find the last EOL and check if
    # everything after it is whitespace-only
    last_eol = -1
    for i in range(len(parts) - 1, -1, -1):
        p = parts[i]
        if p.is_expression:
            break
        tok = cast(Token, p)
        if tok.toktype == TT.EOL:
            last_eol = i
            break
        if tok.toktype == TT.STRMID and _is_ws_only(tok.tokstr):
            continue
        if tok.toktype == TT.STRCHR:
            break
        break

    prefix = ""
    if last_eol >= 0:
        # everything after last_eol should be whitespace-only
        trailing = parts[last_eol + 1 :]
        all_ws = all(
            not p.is_expression
            and cast(Token, p).toktype == TT.STRMID
            and _is_ws_only(cast(Token, p).tokstr)
            for p in trailing
        )
        if all_ws or not trailing:
            # extract the whitespace prefix from the last line
            prefix = "".join(
                cast(Token, p).tokstr for p in trailing if not p.is_expression
            )
            # remove the trailing EOL and whitespace
            parts = parts[:last_eol]

    if not parts:
        return parts

    # Step 3: strip common prefix from each line
    if not prefix:
        return parts

    # verify all non-blank lines start with the prefix
    can_strip = True
    at_line_start = True
    for p in parts:
        if p.is_expression:
            at_line_start = False
            continue
        tok = cast(Token, p)
        if tok.toktype == TT.EOL:
            at_line_start = True
            continue
        if at_line_start and tok.toktype == TT.STRMID:
            # check if this starts a blank line (just whitespace before next EOL)
            if not _is_ws_only(tok.tokstr) and not tok.tokstr.startswith(prefix):
                can_strip = False
                break
            at_line_start = False
        else:
            at_line_start = False

    if not can_strip:
        return parts

    # apply the stripping
    result: List[Union[Token, zast.Expression]] = []
    at_line_start = True
    prefix_len = len(prefix)
    for p in parts:
        if p.is_expression:
            result.append(p)
            at_line_start = False
            continue
        tok = cast(Token, p)
        if tok.toktype == TT.EOL:
            result.append(tok)
            at_line_start = True
            continue
        if at_line_start and tok.toktype == TT.STRMID:
            if _is_ws_only(tok.tokstr):
                # blank line content — keep as-is
                result.append(tok)
            elif tok.tokstr.startswith(prefix):
                stripped = tok.tokstr[prefix_len:]
                if stripped:
                    result.append(
                        Token(
                            tok.toktype,
                            stripped,
                            tok.fsno,
                            tok.lineno,
                            tok.colno + prefix_len,
                        )
                    )
                # else: the token was exactly the prefix, drop it
            else:
                result.append(tok)
            at_line_start = False
        else:
            result.append(tok)
            at_line_start = False

    return result


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

    def __init__(
        self,
        vfs: ZVfs,
        mainunitname: str,
        verbose_fn=None,
        prebuilt: Optional[Dict[str, zast.Unit]] = None,
    ):
        """
        vfs: Virtual Filesystem instance to gain access to the module source(s)
        mainunitname: name of main unit (within main; not path, no slashes) to compile
        verbose_fn: optional callback for verbose messages (called with a string)
        prebuilt: optional pre-parsed units (e.g. system lib) keyed by unit name.
            When provided, parse() will pre-seed `definitions` with these units
            and skip re-parsing them. Used by the test harness to avoid
            re-parsing the system lib on every test.
        """
        self.vfs: ZVfs = vfs
        self.mainunitname: str = mainunitname
        self._verbose_fn = verbose_fn
        self._prebuilt: Dict[str, zast.Unit] = prebuilt or {}

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
        # Pre-seed with cached units; their dependencies will be skipped by
        # the `k not in definitions` check below.
        definitions: Dict[str, zast.Unit] = dict(self._prebuilt)
        core: Optional[zast.Unit] = definitions.get("core")
        unitstocompile[self.mainunitname] = None
        # core must be first (so added last, will pop first); skip if cached
        if "core" not in definitions:
            unitstocompile["core"] = None

        # loop like this because we will be adding/removing each loop
        while unitstocompile:
            if not core and "core" in definitions:
                core = definitions["core"]
            refname, atomid = unitstocompile.popitem()
            reftoken: Optional[Token] = None
            if atomid:
                reftoken = atomid.start
            unit = self._accept_unitfile(
                self.rootid, unitname=refname, reference=reftoken, core=core
            )
            if unit.is_error:
                err = cast(zast.Error, unit)
                if err.err == ERR.FILENOTFOUND and err.loc:
                    # could be a bad variable name as well as FILENOTFOUND
                    msg = f'Unknown reference "{err.loc.tokstr}" and {err.msg}'
                    err = zast.Error(
                        start=reftoken or _ERROR_TOKEN, err=ERR.BADREFERENCE, msg=msg
                    )
                    return err
                return err  # propagate error - all other types
            unit = cast(NodeX[zast.Unit], unit)

            if refname in definitions:
                # can this happen?
                msg = f"Unit {refname} already exists"
                err = zast.Error(
                    start=reftoken or _ERROR_TOKEN, err=ERR.BADUNIT, msg=msg
                )
                return err

            # push up any (new) unresolved refs (units allows None so has to be done manually)
            # print(f"have units {unitstocompile.keys()}")
            for k, v in unit.extern.items():
                # skip nodes we've already compiled or already have in the queue to compile
                if k not in definitions and k not in unitstocompile:
                    if self._verbose_fn:
                        self._verbose_fn(f"  resolving dependency: {k}")
                    unitstocompile[k] = v

            definitions[refname] = unit.node

        return zast.Program(
            vfs=self.vfs, units=definitions, mainunitname=self.mainunitname
        )

    def _accept_unitfile(
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
            return zast.Error(
                start=reference or _ERROR_TOKEN, err=ERR.BADUNITNAME, msg=msg
            )

        openfile = self._unitfileopen(parentid, unitname, reference)
        if openfile.is_error:
            return cast(zast.Error, openfile)  # propagate error
        openfile = cast(ZVfsOpenFile, openfile)

        with openfile:
            tokenizer = Tokenizer(openfile)
            lex = Lexer(tokenizer)
            fsid = openfile.entryid
            if self._verbose_fn:
                self._verbose_fn(f"  compiling: {self.vfs.path(fsid)}")

            unitorerr = self._accept_unitbody(lex)
            if unitorerr.is_error:
                return cast(zast.Error, unitorerr)  # propagate error
            unitorerr = cast(NodeX[zast.Unit], unitorerr)

            unit = unitorerr.node
            toresolve = unitorerr.extern

            # check EOF
            if not lex.accept(TT.EOF):
                msg = "Expected EOF at end of Unit file"
                err = zast.Error(start=lex.peek(), err=ERR.BADUNIT, msg=msg)
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
            # numeric literals and type names won't resolve as modules
            c0 = refname[0] if refname else ""
            is_numeric = c0.isdigit() or (
                c0 in ("+", "-") and len(refname) > 1 and refname[1].isdigit()
            )
            if is_numeric:
                continue
            if hassubunitdir:  # look for subunit
                # could have sub-extern references too...
                start: Optional[Token] = None
                if refid:
                    start = refid.start
                subunitx = self._accept_unitfile(
                    parentid=dirid, unitname=refname, reference=start, core=core
                )
                if subunitx.is_error:
                    err = cast(zast.Error, subunitx)
                    if err.err != ERR.FILENOTFOUND:
                        # propagate error
                        return err
                    # else FILENOTFOUND, not a subunit, keep looking
                else:
                    # got a subunit (without error)
                    subunitx = cast(NodeX[zast.Unit], subunitx)
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
            return zast.Error(
                start=reference or _ERROR_TOKEN, err=ERR.FILENOTFOUND, msg=msg
            )

        try:
            openfile = self.vfs.open(fsid)
        except IOError:
            msg = f"IO Error opening file [{fn}]"
            return zast.Error(start=reference or _ERROR_TOKEN, err=ERR.IOERROR, msg=msg)

        # must have opened ok
        return openfile

    @staticmethod
    def _make_label_value(tok: Token) -> NodeX[zast.LabelValue]:
        """Create a LabelValue node from a LABELPRE token."""
        lv = zast.LabelValue(start=tok, name=tok.tokstr)
        return NodeX(node=lv, extern={tok.tokstr: lv})

    def _accept_unitbody(self, lex: Lexer) -> Union[NodeX[zast.Unit], zast.Error]:
        """
        _accept_unitbody = accept the body of a unit. Used by unitfile and
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
        # meta is also predefined — it is the compiler's internal allocator
        # (meta.create) available inside type method bodies.
        localdefs: Set[str] = set(("this", "type", "meta"))

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
                typedefinitionx = self._accept_typedefinition(lex)
                lex.filtereol(False)  # restore for next definition boundary
                if typedefinitionx is None:
                    msg = (
                        f"Expected TypeDefinition after Definition name, got '{t.tokstr}' "
                        + repr(t)
                    )
                    error = zast.Error(start=t, err=ERR.EXPECTEDTYPEDEF, msg=msg)
                    break
                if typedefinitionx.is_error:
                    error = cast(zast.Error, typedefinitionx)
                    break
            typedefinitionx = cast(NodeX[zast.TypeDefinition], typedefinitionx)

            definition = typedefinitionx.node
            name = label.tokstr
            if name in definitions:
                error = zast.Error(
                    start=definition.start,
                    err=ERR.DUPLICATEDEF,
                    msg=f"Duplicate definition of {name}",
                )
                break
            # extern references that need to be push upwards....
            promoteexterns(
                addto=extern, addfrom=typedefinitionx.extern, local=localdefs
            )
            definitions[name] = definition
            localdefs.add(name)

        lex.filtereol(prev_filtereol)  # restore previous state
        if error:
            return error

        # resolve forward references: remove externs that match body definitions
        # LabelValue (:x) externs must NOT resolve against themselves at this
        # level — they need to resolve at a parent level instead
        for dname in definitions:
            if dname in extern:
                # only remove if the extern didn't originate as a LabelValue
                # at THIS level (i.e., the definition itself is :x)
                if definitions[dname].nodetype != NodeType.LABELVALUE:
                    del extern[dname]

        return NodeX(node=zast.Unit(start=start, body=definitions), extern=extern)

    def _accept_typedefinition(
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
            return self._accept_unit_definition(lex)
        if tt == TT.FUNCTION:
            return self._accept_function_definition(lex)
        if tt == TT.RECORD:
            return self._accept_record(lex)
        if tt == TT.CLASS:
            return self._accept_class(lex)
        if tt == TT.VARIANT:
            return self._accept_variant(lex)
        if tt == TT.UNION:
            return self._accept_union(lex)
        if tt == TT.PROTOCOL:
            return self._accept_protocol(lex)
        if tt == TT.FACET:
            return self._accept_facet(lex)
        # at unit level, if / match / data are wrapped as Expression so
        # downstream treats them uniformly with operation-valued
        # definitions. Only operations (not calls) are valid for the
        # fall-through case.
        if tt == TT.IF:
            return self._wrap_as_expression(self._accept_if_expression(lex))
        if tt == TT.MATCH:
            return self._wrap_as_expression(self._accept_match_expression(lex))
        if tt == TT.DATA:
            return self._wrap_as_expression(self._accept_data_definition(lex))
        return self._accept_operation(lex)

    @staticmethod
    def _wrap_as_expression(
        nx: Union[NodeX, zast.Error, None],
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """Pass through Error/None; wrap an expression-shaped NodeX as a
        `zast.Expression` with the same extern set. Used at unit level for
        if / match / data — each of which returns an ExpressionSubTypes
        payload that must be boxed as an Expression for a TypeDefinition."""
        if nx is None or nx.is_error:
            return cast(Union[zast.Error, None], nx)
        node = cast(NodeX[zast.ExpressionSubTypes], nx)
        expression = zast.Expression(expression=node.node, start=node.node.start)
        return NodeX(node=expression, extern=node.extern)

    def _accept_expression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Expression], zast.Error, None]:
        """
        Accept an expression per grammar:

            expression: for-expression | with-expression | if-expression
                      | match-expression | data-definition | block
                      | ( term "=" expression )
                      | ( term "swap" term )
                      | call | binop

        Dispatches on the leading keyword (IF/FOR/MATCH/DATA/WITH/
        BRACEOPEN); otherwise consumes a greedy path list and decides
        between reassignment (`=`), swap (`swap`), or operation/call.
        Reassignment returns `null`-typed and swap returns `null`-typed
        per grammar — the typechecker rejects using these as stored
        values.

        Returns the Expression, an Error, or None.
        """
        # pylint: disable=R0911,R0912,R0914,too-many-statements,too-many-branches
        t = lex.peek()
        tt = t.toktype
        start = t
        node: Union[NodeX[zast.ExpressionSubTypes], zast.Error, None]
        if tt == TT.IF:
            node = self._accept_if_expression(lex)
        elif tt == TT.FOR:
            node = self._accept_for_expression(lex)
        elif tt == TT.MATCH:
            node = self._accept_match_expression(lex)
        elif tt == TT.DATA:
            node = self._accept_data_definition(lex)
        elif tt == TT.WITH:
            node = self._accept_with_expression(lex)
        elif tt == TT.BRACEOPEN:
            node = self._accept_bareblock(lex)
        else:
            oplist, err = self._operation_paths(lex)
            if err is not None:
                return err

            if lex.accept(TT.EQUALS):
                node = self._build_reassignment(lex, oplist, start)
            elif lex.accept(TT.SWAP):
                node = self._build_swap(lex, oplist, start)
            else:
                node = self._accept_operation_or_call(oplist, lex)

        if node is not None and node.is_error:
            return cast(zast.Error, node)
        if not node:
            return None
        node = cast(NodeX[zast.ExpressionSubTypes], node)

        expression = zast.Expression(expression=node.node, start=node.node.start)
        return NodeX(node=expression, extern=node.extern)

    def _build_reassignment(
        self,
        lex: Lexer,
        oplist: List[NodeX[zast.Path]],
        start: Token,
    ) -> Union[NodeX[zast.Reassignment], zast.Error]:
        """
        Build a `Reassignment` from an already-consumed path list (LHS)
        and the remaining token stream (RHS expression). The `=` token
        has already been consumed.
        """
        if not oplist:
            msg = "Reassignment requires a left hand side"
            return zast.Error(start=start, err=ERR.BADSTATEMENT, msg=msg)
        if len(oplist) != 1:
            msg = "Reassignment must be to a single path on the LHS"
            return zast.Error(start=start, err=ERR.BADSTATEMENT, msg=msg)
        lhsx = oplist[0]
        extern: Dict[str, zast.AtomId] = dict(lhsx.extern)

        rhsx = self._accept_expression(lex)
        if rhsx is not None and rhsx.is_error:
            return cast(zast.Error, rhsx)
        if not rhsx:
            msg = "Expected an expression for the RHS of a reassignment"
            return zast.Error(start=lex.acceptany(), err=ERR.BADSTATEMENT, msg=msg)
        rhsx = cast(NodeX[zast.Expression], rhsx)

        promoteexterns(addto=extern, addfrom=rhsx.extern)
        reassignment = zast.Reassignment(topath=lhsx.node, value=rhsx.node, start=start)
        return NodeX(node=reassignment, extern=extern)

    def _build_swap(
        self,
        lex: Lexer,
        oplist: List[NodeX[zast.Path]],
        start: Token,
    ) -> Union[NodeX[zast.Swap], zast.Error]:
        """
        Build a `Swap` from an already-consumed LHS path list and an
        RHS path. The `swap` token has already been consumed.
        """
        if not oplist:
            msg = "Swap requires a left hand side"
            return zast.Error(start=start, err=ERR.BADSTATEMENT, msg=msg)
        if len(oplist) != 1:
            msg = "Swap must be to a single path on the LHS"
            return zast.Error(start=start, err=ERR.BADSTATEMENT, msg=msg)
        lhsx = oplist[0]
        extern: Dict[str, zast.AtomId] = dict(lhsx.extern)

        rhsx = self._accept_path(lex)
        if rhsx is not None and rhsx.is_error:
            return cast(zast.Error, rhsx)
        if not rhsx:
            msg = "Swap requires a right hand side"
            return zast.Error(start=start, err=ERR.BADSTATEMENT, msg=msg)
        rhsx = cast(NodeX[zast.Path], rhsx)

        promoteexterns(addto=extern, addfrom=rhsx.extern)
        swap = zast.Swap(lhs=lhsx.node, rhs=rhsx.node, start=start)
        return NodeX(node=swap, extern=extern)

    def _accept_operation_or_call(
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

        if not oplist:  # len(oplist) == 0
            return None

        if (len(oplist) == 1 and lex.peek().toktype in (TT.LABEL, TT.LABELPRE)) or (
            len(oplist) % 2 == 0
        ):
            return self._accept_call(lex=lex, paths=oplist)

        # else: op or error...
        opx = self._fold_binop(
            paths=oplist, nexttoken=lex.peek()
        )  # don't propagate error, may be call
        return opx

    def _operation_paths(
        self, lex: Lexer
    ) -> tuple[List[NodeX[zast.Path]], Optional[zast.Error]]:
        """
        Get a greedy list of operation elements (operand/operator paths).
        Used to decide between operation and call and to drive `_fold_binop`.

        Returns a tuple `(paths, err)`:
        - `paths`: the accumulated list (possibly empty). Callers can use
          `paths` whenever `err is None`.
        - `err`: an Error if a path failed to parse; `paths` is empty in
          that case. The tuple shape avoids the `Union[List, Error]`
          inspection dance at call sites.
        """
        ret: List[NodeX[zast.Path]] = []
        while True:
            path = self._accept_path(lex)
            if path is not None and path.is_error:
                return [], cast(zast.Error, path)
            if not path:
                break
            ret.append(cast(NodeX[zast.Path], path))

        return ret, None

    @staticmethod
    def _fold_binop(
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

        if getattr(el, "is_token", False):
            # 'as' cannot be first, must be path/atom
            msg = "Expected an operand for the left hand side of operation"
            return zast.Error(start=cast(Token, el), err=ERR.BADEXPRESSION, msg=msg)
        start = el.node.start

        operation: zast.Operation = el.node
        extern: Dict[str, zast.AtomId] = el.extern

        # loop over the remaining elements 2 at a time making OpArgs
        idx = 1
        n = len(paths)
        while idx < n:
            el = paths[idx]
            path = el.node
            if path.nodetype != NodeType.ATOMID:
                msg = "Expected an operator (single identifier)"
                return zast.Error(start=el.node.start, err=ERR.BADEXPRESSION, msg=msg)
            operator = cast(zast.AtomId, path)
            idx += 1
            if idx >= n:
                msg = "Expected an operand for the right hand side of operation"
                return zast.Error(start=nexttoken, err=ERR.BADEXPRESSION, msg=msg)
            el = paths[idx]

            # el is a path, make a BinOp
            operation = zast.BinOp(
                lhs=operation, operator=operator, rhs=el.node, start=start
            )
            promoteexterns(addto=extern, addfrom=el.extern)

            idx += 1

        return NodeX(operation, extern=extern)

    def _accept_call(
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
        opx = self._fold_binop(rest, nexttoken=lex.peek())
        if opx is not None and opx.is_error:
            return cast(zast.Error, opx)  # propagate error
        opx = cast(Optional[NodeX[zast.Operation]], opx)
        if opx and opx.node.nodetype in (
            NodeType.BINOP,
            NodeType.DOTTEDPATH,
            NodeType.ATOMID,
            NodeType.ATOMSTRING,
            NodeType.EXPRESSION,
            NodeType.NAMEDOPERATION,
            NodeType.LABELVALUE,
        ):
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
                return zast.Error(start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg)
            argnames.add(label.tokstr)

            if is_label_value:
                lvx = self._make_label_value(label)
                namedop = zast.NamedOperation(
                    name=label.tokstr, valtype=lvx.node, start=label
                )
                arguments.append(namedop)
                promoteexterns(addto=extern, addfrom=lvx.extern)
            else:
                opx = self._accept_operation(lex)
                if opx is not None and opx.is_error:
                    return cast(zast.Error, opx)  # propagate error
                opx = cast(Optional[NodeX[zast.Operation]], opx)
                if opx:
                    namedop = zast.NamedOperation(
                        name=label.tokstr, valtype=opx.node, start=label
                    )
                    arguments.append(namedop)
                    promoteexterns(addto=extern, addfrom=opx.extern)
                else:
                    msg = f"Expected an Operation after label: {label.tokstr}"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                    )

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
        if path.nodetype == NodeType.DOTTEDPATH:
            path = cast(zast.DottedPath, path)
            suffix = path.child.name
            own = _OWNERSHIP_SUFFIXES.get(suffix)
            if own is not None:
                return path.parent, own
        return path, None

    def _accept_function_definition(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Function], zast.Error, None]:
        """
        accept a function

            function
                "function"
                [ [ "in" ] "{" { parameteritem | newline } "}" ]
                [ "out" typeref ]
                [ "is" statement ]
                [ "as" "{" { label constant-expression | label-value } "}" ]

        Clauses can appear in any order. If 'as' appears before parameters,
        'in' must be explicit.

        Returns a Function or Error or None (if no 'function' keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals

        start = lex.peek()
        if not lex.accept(TT.FUNCTION):
            return None

        returntype: Optional[zast.Path] = None
        parameters: Dict[str, zast.Path] = {}
        param_ownership: Dict[str, ZParamOwnership] = {}
        return_ownership: Optional[ZParamOwnership] = None
        return_lock_target: Optional[str] = None
        # externs from 'accept' function parameters
        externparam: Dict[str, zast.AtomId] = {}
        # parameter names - local definitions for determining externs
        localparam: Set[str] = set()
        gotaccept = False  # need this because accept could be empty block
        body: Optional[zast.Statement] = None  # None for spec
        is_native: bool = False  # True for native (compiler-provided) functions
        externbody: Dict[str, zast.AtomId] = {}  # externs from 'is' function body
        as_body: Optional[ObjectBody] = None  # 'as' clause for generic params
        first = True  # true for first arg only (unnamed arg allowed)

        while True:
            tok = lex.peek()

            if lex.accept(TT.OUT):
                if returntype:
                    msg = "Duplicate 'out'"
                    return zast.Error(start=tok, err=ERR.BADARGUMENT, msg=msg)

                typeref = self._accept_path(lex)
                if typeref is None:
                    msg = "Expected type reference for 'out'"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                    )

                if typeref.is_error:
                    return cast(zast.Error, typeref)  # propagate any other error
                typeref = cast(NodeX[zast.Path], typeref)

                # check for ownership annotation on the return type path
                stripped_ret, ret_own = self._strip_ownership(typeref.node)
                returntype = stripped_ret
                if ret_own is not None:
                    return_ownership = ret_own

                # optional `from: <name>` clause binds the return value's
                # lock to a parameter (e.g. `out T.borrow from: this`).
                from_tok = lex.peek()
                if from_tok.toktype == TT.LABEL and from_tok.tokstr == "from":
                    lex.acceptany()  # consume `from:`
                    target = self._accept_path(lex)
                    if target is None:
                        msg = (
                            "Expected name after 'from:' in return-type lock annotation"
                        )
                        return zast.Error(
                            start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                        )
                    if target.is_error:
                        return cast(zast.Error, target)
                    target_node = cast(NodeX[zast.Path], target).node
                    if target_node.nodetype != NodeType.ATOMID:
                        msg = "'from:' lock target must be a simple name"
                        return zast.Error(start=from_tok, err=ERR.BADARGUMENT, msg=msg)
                    return_lock_target = cast(zast.AtomId, target_node).name
                first = False

            elif lex.accept(TT.IS):
                if body or is_native:
                    msg = "Duplicate 'is'"
                    return zast.Error(start=tok, err=ERR.BADARGUMENT, msg=msg)

                if lex.accept(TT.NATIVE):
                    is_native = True
                    first = False
                else:
                    statement = self._accept_block(lex)
                    if statement is None:
                        msg = "Expected Statement for 'is'"
                        return zast.Error(
                            start=lex.acceptany(),
                            err=ERR.BADARGUMENT,
                            msg=msg,
                        )

                    if statement.is_error:
                        return cast(zast.Error, statement)  # propagate any other error
                    statement = cast(NodeX[zast.Statement], statement)

                    body = statement.node
                    externbody = statement.extern
                    first = False

            elif lex.accept(TT.AS):
                if as_body is not None:
                    msg = "Duplicate 'as'"
                    return zast.Error(start=tok, err=ERR.BADARGUMENT, msg=msg)

                b = self._get_object_body(lex, ObjectBodyKind.FUNCTION_AS)
                if b.is_error:
                    return cast(zast.Error, b)
                b = cast(ObjectBody, b)

                as_body = b
                first = False

            elif (
                lex.accept(TT.IN) or first
            ):  # must be last to handle other keywords first
                if not first:
                    lex.accept(TT.IN)

                if gotaccept:
                    msg = "Duplicate 'in'"
                    return zast.Error(start=tok, err=ERR.BADARGUMENT, msg=msg)

                if not lex.accept(TT.BRACEOPEN):
                    msg = "Expected open brace '{' after 'in'"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                    )

                while True:
                    lex.accept(TT.EOL)  # optional newline

                    paramnametok = lex.peek()
                    if paramnametok.toktype == TT.LABELPRE:
                        lex.acceptany()
                        paramname = paramnametok.tokstr
                        if paramname in parameters:
                            msg = f"Duplicate parameter name: {paramname}"
                            return zast.Error(start=tok, err=ERR.BADPARAMETER, msg=msg)
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
                        return zast.Error(start=tok, err=ERR.BADPARAMETER, msg=msg)

                    val = self._accept_path(lex)
                    if val is None:
                        msg = "Expected typeref or number for parameter type"
                        return zast.Error(
                            start=lex.acceptany(),
                            err=ERR.BADPARAMETER,
                            msg=msg,
                        )

                    if val.is_error:
                        return cast(zast.Error, val)  # propagate error
                    val = cast(NodeX[zast.Path], val)

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
                        start=lex.acceptany(),
                        err=ERR.BADPARAMETERBLOCK,
                        msg=msg,
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
        if as_body:
            # as-body externs, excluding items defined in as-body itself
            as_locals = set(as_body.items.keys())
            promoteexterns(addto=extern, addfrom=as_body.extern, local=as_locals)
            # remove param externs that are defined in as-body (generic params)
            for k in list(extern.keys()):
                if k in as_locals:
                    del extern[k]

        func = zast.Function(
            returntype=returntype,
            parameters=parameters,
            body=body,
            start=start,
            param_ownership=param_ownership,
            return_ownership=return_ownership,
            return_lock_target=return_lock_target,
            is_native=is_native,
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
        )
        return NodeX(node=func, extern=extern)

    # kind → (opening token, AST class). Record/Class carry
    # field_ownership; Variant/Union carry tag.
    _ITEM_TYPEDEF_MAP: Dict[ObjectBodyKind, tuple] = {
        ObjectBodyKind.RECORD: (TT.RECORD, zast.Record),
        ObjectBodyKind.CLASS: (TT.CLASS, zast.Class),
        ObjectBodyKind.VARIANT: (TT.VARIANT, zast.Variant),
        ObjectBodyKind.UNION: (TT.UNION, zast.Union),
    }

    def _accept_item_definition(
        self,
        lex: Lexer,
        kind: ObjectBodyKind,
    ) -> Union[NodeX[zast.TypeDefinition], zast.Error, None]:
        """
        Parse any of record / class / variant / union per the shared
        grammar:

            item: keyword [ "is" ] ( "{" ... "}" | "native" ) [ "as" "{" ... "}" ]

        `kind` is one of RECORD / CLASS / VARIANT / UNION and maps to
        the opening token and target AST class. Variant/Union add a
        `tag` field; Record/Class add `field_ownership`.
        """
        tt, ast_cls = self._ITEM_TYPEDEF_MAP[kind]
        start = lex.peek()
        if not lex.accept(tt):
            return None

        is_body, as_body, extern, err, native = self._accept_item_bodies(lex, kind)
        if err:
            return err

        items = is_body.items if is_body else {}
        implements = is_body.islist if is_body else []
        functions = is_body.functions if is_body else {}
        as_items = as_body.items if as_body else {}
        as_functions = as_body.functions if as_body else {}

        if kind in (ObjectBodyKind.VARIANT, ObjectBodyKind.UNION):
            node = ast_cls(
                items=items,
                implements=implements,
                functions=functions,
                tag=is_body.tag if is_body else None,
                as_items=as_items,
                as_functions=as_functions,
                start=start,
                is_native=native,
                field_ownership=is_body.field_ownership if is_body else {},
            )
        else:
            node = ast_cls(
                items=items,
                implements=implements,
                functions=functions,
                as_items=as_items,
                as_functions=as_functions,
                start=start,
                is_native=native,
                field_ownership=is_body.field_ownership if is_body else {},
            )
        return NodeX(node=node, extern=extern)

    def _accept_record(self, lex: Lexer) -> Union[NodeX[zast.Record], zast.Error, None]:
        return cast(
            Union[NodeX[zast.Record], zast.Error, None],
            self._accept_item_definition(lex, ObjectBodyKind.RECORD),
        )

    def _accept_class(self, lex: Lexer) -> Union[NodeX[zast.Class], zast.Error, None]:
        return cast(
            Union[NodeX[zast.Class], zast.Error, None],
            self._accept_item_definition(lex, ObjectBodyKind.CLASS),
        )

    def _accept_variant(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Variant], zast.Error, None]:
        return cast(
            Union[NodeX[zast.Variant], zast.Error, None],
            self._accept_item_definition(lex, ObjectBodyKind.VARIANT),
        )

    def _accept_union(self, lex: Lexer) -> Union[NodeX[zast.Union], zast.Error, None]:
        return cast(
            Union[NodeX[zast.Union], zast.Error, None],
            self._accept_item_definition(lex, ObjectBodyKind.UNION),
        )

    def _accept_protocol(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Protocol], zast.Error, None]:
        """
        Accept a protocol per grammar's item_definition:

            "protocol" [ "is" ] ( "{" ... "}" | "native" ) [ "as" "{" ... "}" ]

        Route through `_accept_item_bodies` so `is` / `as` can be reordered
        and `native` is accepted, matching the other item kinds.
        """
        start = lex.peek()
        if not lex.accept(TT.PROTOCOL):
            return None

        is_body, as_body, extern, err, native = self._accept_item_bodies(
            lex, ObjectBodyKind.PROTOCOL
        )
        if err:
            return err

        protocol = zast.Protocol(
            parameters=is_body.items if is_body else {},
            specs=is_body.functions if is_body else {},
            includes=is_body.islist if is_body else [],
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
            is_native=native,
        )
        return NodeX(node=protocol, extern=extern)

    def _accept_facet(self, lex: Lexer) -> Union[NodeX[zast.Facet], zast.Error, None]:
        """
        Accept a facet (value-type interface). Same grammar shape as
        protocol; routed through `_accept_item_bodies` for `is`/`as`
        reordering and `native` support.
        """
        start = lex.peek()
        if not lex.accept(TT.FACET):
            return None

        is_body, as_body, extern, err, native = self._accept_item_bodies(
            lex, ObjectBodyKind.FACET
        )
        if err:
            return err

        facet = zast.Facet(
            parameters=is_body.items if is_body else {},
            specs=is_body.functions if is_body else {},
            includes=is_body.islist if is_body else [],
            as_items=as_body.items if as_body else {},
            as_functions=as_body.functions if as_body else {},
            start=start,
            is_native=native,
        )
        return NodeX(node=facet, extern=extern)

    def _accept_item_bodies(
        self,
        lex: Lexer,
        kind: ObjectBodyKind,
    ) -> tuple:
        """
        Parse `is` and `as` bodies for item definitions.
        `is` and `as` can appear in any order if named.
        Unnamed first arg defaults to `is`.
        `is native` marks the type as having compiler-provided state.

        Returns (is_body, as_body, extern, error, is_native)
        where is_body is ObjectBody or None, as_body is Optional[ObjectBody],
        extern is Dict, error is Optional[Error], is_native is bool.
        """
        is_body: Optional[ObjectBody] = None
        as_body: Optional[ObjectBody] = None
        is_native: bool = False
        extern: Dict[str, zast.AtomId] = {}

        for _ in range(3):  # max 3 iterations: is, as, and possibly native+as
            t = lex.peek()
            if t.toktype == TT.IS:
                if is_body is not None or is_native:
                    msg = "Duplicate 'is' clause"
                    return (
                        None,
                        None,
                        None,
                        zast.Error(start=t, err=ERR.BADITEM, msg=msg),
                        False,
                    )
                lex.acceptany()
                if lex.accept(TT.NATIVE):
                    is_native = True
                else:
                    b = self._get_object_body(lex, kind)
                    if b.is_error:
                        return None, None, None, cast(zast.Error, b), False
                    b = cast(ObjectBody, b)
                    is_body = b
                    promoteexterns(addto=extern, addfrom=b.extern)
            elif t.toktype == TT.NATIVE and is_body is None and not is_native:
                # elided 'is': native as first unnamed arg
                lex.acceptany()
                is_native = True
            elif t.toktype == TT.AS:
                if as_body is not None:
                    msg = "Duplicate 'as' clause"
                    return (
                        None,
                        None,
                        None,
                        zast.Error(start=t, err=ERR.BADITEM, msg=msg),
                        False,
                    )
                lex.acceptany()
                b = self._get_object_body(lex, kind, as_clause=True)
                if b.is_error:
                    return None, None, None, cast(zast.Error, b), False
                b = cast(ObjectBody, b)
                as_body = b
                promoteexterns(addto=extern, addfrom=b.extern)
            elif t.toktype == TT.BRACEOPEN and is_body is None and not is_native:
                # unnamed first arg defaults to 'is'
                b = self._get_object_body(lex, kind)
                if b.is_error:
                    return None, None, None, cast(zast.Error, b), False
                b = cast(ObjectBody, b)
                is_body = b
                promoteexterns(addto=extern, addfrom=b.extern)
            else:
                break

        if is_body is None and not is_native:
            msg = "Expected '{' or 'native' for item body"
            return (
                None,
                None,
                None,
                zast.Error(start=lex.peek(), err=ERR.BADARGUMENT, msg=msg),
                False,
            )

        # remove is-body externs that are defined in as-body (e.g. generic params)
        if as_body:
            as_locals = set(as_body.items.keys())
            for k in list(extern.keys()):
                if k in as_locals:
                    del extern[k]

        return is_body, as_body, extern, None, is_native

    def _get_object_body(
        self,
        lex: Lexer,
        kind: ObjectBodyKind,
        as_clause: bool = False,
    ) -> Union[ObjectBody, zast.Error]:
        """
        Parse an object body (after `is` or `as`) for any item kind.

        `kind` is the item being parsed (RECORD / CLASS / VARIANT / UNION /
        PROTOCOL / FACET / ENUM / FUNCTION_AS). `as_clause` is True when
        we are parsing the `as` section (static members); the `as` section
        never permits a `tag` declaration or unlabelled-id entries, and
        mirrors the kind's unlabelled-path rule.

        The legacy three-bool API (unlabelledpath, unlabelledid + a
        dead `allowtag`) is derived here from (kind, as_clause) to keep
        call sites declarative and prevent the invalid combinations the
        old API allowed by convention only. `tag` is currently handled
        as a regular named field and is enforced at the typechecker, not
        the parser — so no parser-level tag gate is needed.

        Return an Error or the body components in an ObjectBody.
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        if as_clause:
            unlabelledid = False
            unlabelledpath = kind in _OBJECT_BODY_ALLOWS_UNLABELLED_PATH
        else:
            unlabelledpath = kind in _OBJECT_BODY_ALLOWS_UNLABELLED_PATH
            unlabelledid = kind in _OBJECT_BODY_ALLOWS_UNLABELLED_ID

        if not lex.accept(TT.BRACEOPEN):
            msg = "Expected open brace '{' for 'is' argument"
            return zast.Error(start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg)

        # items=items, islist=islist, functions=functions, tag=tag, extern=extern
        items: Dict[str, zast.Path] = {}  # generic and normal fields
        islist: List[zast.Path] = []  # protocols that this object satisfies
        functions: Dict[str, zast.Function] = {}
        tag: Optional[zast.Path] = None
        extern: Dict[str, zast.AtomId] = {}
        field_ownership: Dict[str, ZParamOwnership] = {}

        # externs from each item typedefinition
        local: Set[str] = set()  # set of locally defined items
        # 'this', 'type' and 'meta' are predefined for
        # record, class, variant, enum, protocol(?).
        # 'meta.create' is the compiler-internal allocator.
        localthis: Set[str] = set(("this", "type", "meta"))
        externitems: Dict[str, zast.AtomId] = {}

        while not lex.accept(TT.BRACECLOSE):
            if lex.accept(TT.EOL):  # optional newline
                continue  # could be trailing, check for BRACECLOSE

            t = lex.peek()
            tt = t.toktype
            if tt == TT.IS:
                # type being implemented (or included for protocols)
                lex.acceptany()
                typerefx = self._accept_path(lex)
                if typerefx is None:
                    msg = "Expected type reference for 'is'"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                    )

                if typerefx.is_error:
                    return cast(zast.Error, typerefx)  # propagate any other error
                typerefx = cast(NodeX[zast.Path], typerefx)

                islist.append(typerefx.node)
                # add directly to extern.. these cannot refer locally
                promoteexterns(addto=extern, addfrom=typerefx.extern)

            elif tt in (TT.LABEL, TT.LABELPRE):
                label = lex.acceptany()
                if tt == TT.LABELPRE:
                    if label.tokstr in items or label.tokstr in functions:
                        msg = f"Duplicate item name: {label.tokstr}"
                        return zast.Error(start=label, err=ERR.BADITEM, msg=msg)
                    lvx = self._make_label_value(label)
                    local.add(label.tokstr)
                    items[label.tokstr] = lvx.node
                    promoteexterns(addto=externitems, addfrom=lvx.extern)
                else:
                    lex.accept(TT.EOL)  # optional newline
                    # function
                    funcx = self._accept_function_definition(lex)
                    if funcx is not None and funcx.is_error:
                        return cast(zast.Error, funcx)  # propagate error
                    funcx = cast(Optional[NodeX[zast.Function]], funcx)
                    if funcx:
                        if label.tokstr in items or label.tokstr in functions:
                            msg = f"Duplicate item name: {label.tokstr}"
                            return zast.Error(start=label, err=ERR.BADITEM, msg=msg)
                        functions[label.tokstr] = funcx.node
                        # add directly to extern.. these cannot refer locally, except via 'this'
                        promoteexterns(
                            addto=extern, addfrom=funcx.extern, local=localthis
                        )
                    elif lex.peek().toktype == TT.UNIT:
                        # inline unit definition (e.g., public: unit { ... })
                        unitx = self._accept_unit_definition(lex)
                        if unitx is not None and unitx.is_error:
                            return cast(zast.Error, unitx)
                        unitx = cast(Optional[NodeX[zast.Unit]], unitx)
                        if unitx:
                            if label.tokstr in items or label.tokstr in functions:
                                msg = f"Duplicate item name: {label.tokstr}"
                                return zast.Error(start=label, err=ERR.BADITEM, msg=msg)
                            local.add(label.tokstr)
                            items[label.tokstr] = unitx.node  # type: ignore[assignment]
                            # don't promote externs — unit references parent type members
                            # which are resolved by the type checker, not the parser
                    else:
                        # path/typeref/typeref_or_num or constant expression
                        opx = self._accept_operation(lex)
                        if opx is not None and opx.is_error:
                            return cast(zast.Error, opx)  # propagate error
                        opx = cast(Optional[NodeX[zast.Operation]], opx)
                        if opx:
                            if label.tokstr in items or label.tokstr in functions:
                                msg = f"Duplicate item name: {label.tokstr}"
                                return zast.Error(start=label, err=ERR.BADITEM, msg=msg)
                            local.add(label.tokstr)
                            # if the field type is a simple Path, strip any
                            # ownership annotation (.lock) from it. Only .lock
                            # is permitted on field types; .take/.borrow on
                            # field types are rejected by the type checker.
                            field_node: zast.Operation = opx.node
                            if isinstance(field_node, zast.Path):
                                stripped, own = self._strip_ownership(field_node)
                                if own is not None:
                                    field_ownership[label.tokstr] = own
                                    field_node = stripped
                            items[label.tokstr] = field_node  # type: ignore[assignment]
                            # promote to externitems (will be promoted to extern below, after locals)
                            promoteexterns(addto=externitems, addfrom=opx.extern)
                        else:
                            # error
                            msg = f"Expected a function or expression for item: {label.tokstr}"
                            return zast.Error(
                                start=lex.acceptany(),
                                err=ERR.BADITEM,
                                msg=msg,
                            )

            elif unlabelledpath:
                # try an unnamed path - path can only have an refid at the root...
                if lex.peek().toktype == TT.REFID:
                    dottedidx = self._accept_path(lex)
                    if dottedidx is not None and dottedidx.is_error:
                        return cast(zast.Error, dottedidx)  # propagate error
                    dottedidx = cast(Optional[NodeX[zast.Path]], dottedidx)
                    if dottedidx is not None:
                        dottedid = dottedidx.node
                        # strip any ownership annotation (.lock) so the
                        # remaining path's leaf gives the field name and the
                        # type alone is stored as the field type.
                        stripped_path, field_own = self._strip_ownership(dottedid)
                        dottedid = stripped_path
                        if dottedid.nodetype == NodeType.ATOMID:
                            name = cast(zast.AtomId, dottedid).name
                        elif dottedid.nodetype == NodeType.DOTTEDPATH:
                            name = cast(
                                zast.DottedPath, dottedid
                            ).child.name  # name is after last dot
                        else:
                            # cannot happen
                            msg = "Unknown DottedId type"
                            return zast.Error(
                                start=lex.acceptany(),
                                err=ERR.BADITEM,
                                msg=msg,
                            )

                        if name in items or name in functions:
                            msg = f"Duplicate item name: {name}"
                            return zast.Error(
                                start=dottedid.start,
                                err=ERR.BADITEM,
                                msg=msg,
                            )
                        local.add(name)
                        items[name] = dottedid
                        if field_own is not None:
                            field_ownership[name] = field_own
                        # cannot refer to locals
                        promoteexterns(addto=externitems, addfrom=dottedidx.extern)
                    else:
                        # no tottedidx, this can't happen, we have a LABEL above...
                        pass
                else:
                    msg = "Expected a label, unlabelled expression or closing brace"
                    return zast.Error(start=lex.acceptany(), err=ERR.BADITEM, msg=msg)
            elif unlabelledid:
                # an unnamed id... for enum only
                atomidx = self._accept_atomid(lex)
                if atomidx:
                    atomid = atomidx.node
                    name = atomid.name
                    local.add(name)
                    items[name] = atomid  # the atomid value is itself...
                    # do NOT promoteexterns... this is an enum item (not a reference)
                else:
                    msg = "Expected a label, an id or closing brace"
                    return zast.Error(start=lex.acceptany(), err=ERR.BADITEM, msg=msg)
            else:
                # error - no other options
                msg = "Expected a label, expression or closing brace"
                return zast.Error(start=lex.acceptany(), err=ERR.BADITEM, msg=msg)

        # extern - add extern from items (skipping locals) since these can be self referntial
        promoteexterns(addto=extern, addfrom=externitems, local=local)

        return ObjectBody(
            items=items,
            islist=islist,
            functions=functions,
            tag=tag,
            extern=extern,
            field_ownership=field_ownership,
        )

    def _accept_unit_definition(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Unit], zast.Error, None]:
        """
        _accept_unit_definition - accept a subunit (unit keyword)

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
            return zast.Error(start=lex.peek(), err=ERR.BADARGUMENT, msg=msg)

        unitorerr = self._accept_unitbody(lex)
        if unitorerr.is_error:
            err = cast(zast.Error, unitorerr)
            return err
        unitorerr = cast(NodeX[zast.Unit], unitorerr)

        # check '}'
        if not lex.accept(TT.BRACECLOSE):
            msg = "Expected '}' at end of sub-unit or new line between definitions"
            err = zast.Error(start=lex.peek(), err=ERR.BADUNIT, msg=msg)
            return err

        unitorerr.node.start = start  # change start to 'unit' keyword
        return unitorerr

    def _accept_if_expression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.If], zast.Error, None]:
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

                op = self._accept_operation(lex)
                if op is not None and op.is_error:
                    return cast(zast.Error, op)  # propagate error
                if not op:
                    msg = "Expected operation (condition) for 'if'"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.EXPECTEDOP, msg=msg
                    )
                op = cast(NodeX[zast.Operation], op)

                # note leading space - cannot collide with real bindings
                conditions[f" *{whenindex}"] = op.node
                whenindex += 1
                # nb: local - can refer to prior bindings...
                promoteexterns(addto=extern, addfrom=op.extern, local=local)
                first = False

            elif t.toktype == TT.THEN:
                if not conditions:
                    msg = "'then' must appear after at least one condition"
                    return zast.Error(start=t, err=ERR.BADTHEN, msg=msg)
                lex.acceptany()
                statementx = self._accept_primary_expression(lex)
                if statementx is not None and statementx.is_error:
                    return cast(zast.Error, statementx)  # propagate error
                if not statementx:
                    msg = "Expected primary-expression for 'then'"
                    return zast.Error(
                        start=lex.acceptany(),
                        err=ERR.EXPECTEDSTATEMENT,
                        msg=msg,
                    )
                statementx = cast(NodeX[zast.Statement], statementx)
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
            return zast.Error(start=t, err=ERR.BADELSE, msg=msg)

        t = lex.peek()
        if t.toktype == TT.ELSE:
            if not clauses:
                msg = "'else' must appear after at least one if/then clause"
                return zast.Error(start=t, err=ERR.BADELSE, msg=msg)
            lex.acceptany()
            statementx = self._accept_primary_expression(lex)
            if statementx is not None and statementx.is_error:
                return cast(zast.Error, statementx)  # propagate error
            if not statementx:
                msg = "Expected primary-expression for 'else'"
                return zast.Error(
                    start=lex.acceptany(),
                    err=ERR.EXPECTEDSTATEMENT,
                    msg=msg,
                )
            statementx = cast(NodeX[zast.Statement], statementx)

            # local not available for else
            promoteexterns(addto=extern, addfrom=statementx.extern)
            elseclause = statementx.node

        ifnode = zast.If(clauses=clauses, elseclause=elseclause, start=start)
        return NodeX(ifnode, extern=extern)

    def _accept_match_expression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Case], zast.Error, None]:
        """
        acceptcase - accept a match clause (exhaustive conditional)

            "match" [ "on" ] operation
            { "case" [ newline ] id "then" primary-expression }
            [ "else" primary-expression ]

        The subject is narrowed in place: if the subject is a simple
        addressable name (an AtomId) the arm sees that name shadowed with
        the matched variant type. For complex / anonymous subjects no
        narrowed binding is introduced — the arm body can only perform
        side effects predicated on the matched variant existing.

        `.take` on the subject is rejected: `match` borrows the subject
        for narrowing across arms; taking it would conflict with the
        flow-narrowed shadow.

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
        op = self._accept_operation(lex)
        if op is not None and op.is_error:
            return cast(zast.Error, op)  # propagate error
        if not op:
            msg = "Expected operation for 'match' (subject)"
            return zast.Error(start=lex.acceptany(), err=ERR.EXPECTEDOP, msg=msg)
        op = cast(NodeX[zast.Operation], op)

        # Reject `.take` on the match subject. Narrowing requires the
        # subject to remain addressable across arms; taking ownership
        # into an arm would invalidate later arms. A subject carrying a
        # `.take` suffix is a DottedPath whose leaf AtomId is `take`.
        if op.node.nodetype in (NodeType.DOTTEDPATH, NodeType.ATOMID):
            _stripped, subj_own = self._strip_ownership(cast(zast.Path, op.node))
            if subj_own is ZParamOwnership.TAKE:
                msg = (
                    "cannot '.take' the subject of 'match'; the subject is "
                    "borrowed for arm narrowing"
                )
                return zast.Error(start=op.node.start, err=ERR.BADCASE, msg=msg)

        subject = op.node
        promoteexterns(addto=extern, addfrom=op.extern)

        while True:
            startclause = lex.peek()  # for each CaseClause
            local: Set[str] = set()  # for each CaseClause

            t = lex.peek()
            if t.toktype != TT.CASE:
                break  # end of clauses

            lex.acceptany()  # 'case'
            lex.accept(TT.EOL)  # optional EOL

            # ----- get id

            curid: zast.AtomId
            atomidx = self._accept_atomid(lex)
            if atomidx is not None and atomidx.is_error:
                return cast(zast.Error, atomidx)  # propagate error
            if atomidx:
                # do NOT promoteexterns... the id must be a member of the 'in' operation
                curid = atomidx.node
            else:
                msg = "Case match expression expected (simple id)"
                return zast.Error(start=lex.acceptany(), err=ERR.BADREFERENCE, msg=msg)

            # Name the clause after the matched id. The clause's statement
            # references the subject under its original name (narrowed) if
            # addressable, or has no narrowed binding otherwise.
            name = curid.name

            # ----- get then

            t = lex.peek()
            if t.toktype != TT.THEN:
                msg = "Expected 'then' keyword for 'case'"
                return zast.Error(start=lex.acceptany(), err=ERR.BADCASE, msg=msg)

            lex.acceptany()  # 'then'
            statementx = self._accept_primary_expression(lex)
            if statementx is not None and statementx.is_error:
                return cast(zast.Error, statementx)  # propagate error
            if not statementx:
                msg = "Expected primary-expression for 'then'"
                return zast.Error(
                    start=lex.acceptany(),
                    err=ERR.EXPECTEDSTATEMENT,
                    msg=msg,
                )
            statementx = cast(NodeX[zast.Statement], statementx)

            promoteexterns(addto=extern, addfrom=statementx.extern, local=local)
            caseclause = zast.CaseClause(
                name=name, match=curid, statement=statementx.node, start=startclause
            )
            clauses.append(caseclause)

        if lex.accept(TT.ELSE):
            statementx = self._accept_primary_expression(lex)
            if statementx is not None and statementx.is_error:
                return cast(zast.Error, statementx)  # propagate error
            if not statementx:
                msg = "Expected primary-expression after 'else' for 'case'"
                return zast.Error(
                    start=lex.acceptany(),
                    err=ERR.EXPECTEDSTATEMENT,
                    msg=msg,
                )
            statementx = cast(NodeX[zast.Statement], statementx)

            # local not available for else
            promoteexterns(addto=extern, addfrom=statementx.extern)
            elseclause = statementx.node

        casenode = zast.Case(
            subject=subject, clauses=clauses, elseclause=elseclause, start=start
        )
        return NodeX(casenode, extern=extern)

    def _accept_for_expression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.For], zast.Error, None]:
        """
        acceptfor - accept a for clause (iteration) per grammar:

            "for"
              [ operation ]                # precondition, unnamed if first
              { "while" operation }        # precondition(s)
              [ label operation ]          # binding (at most one)
              { "while" operation }        # precondition(s) (still)
              [ "loop" primary-expression ]
              { "while" operation }        # postcondition(s)

        The grammar allows at most ONE binding label, and postconditions
        (`while op`) are valid only after `loop`. A label appearing after
        `loop` is a parse error (not a silent break to the enclosing
        scope, as before).

        Return a For or Error or None for no 'for' keyword.
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        start = lex.peek()
        if not lex.accept(TT.FOR):
            return None

        conditions: Dict[str, zast.Operation] = {}
        postconditions: List[zast.Operation] = []
        loop: Optional[zast.Statement] = None
        local: Set[str] = set()
        extern: Dict[str, zast.AtomId] = {}

        first = True  # first condition may omit the 'while' keyword
        bound = False  # whether a binding label has been consumed
        whileindex = 0  # counter for anonymous 'while' clause names

        while True:
            t = lex.peek()
            if (first or t.toktype == TT.WHILE) and t.toktype not in (
                TT.LABEL,
                TT.LOOP,
            ):
                if t.toktype == TT.WHILE:
                    lex.acceptany()
                    lex.accept(TT.EOL)

                op = self._accept_operation(lex)
                if op is not None and op.is_error:
                    return cast(zast.Error, op)
                if not op:
                    msg = "Expected operation (condition) for 'for'"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.EXPECTEDOP, msg=msg
                    )
                op = cast(NodeX[zast.Operation], op)

                if loop:
                    postconditions.append(op.node)
                else:
                    conditions[f" *{whileindex}"] = op.node
                    whileindex += 1

                promoteexterns(addto=extern, addfrom=op.extern, local=local)
                first = False

            elif t.toktype == TT.LABEL:
                if loop:
                    msg = (
                        "Binding label not allowed after 'loop'; bindings "
                        "must appear before the 'loop' clause"
                    )
                    return zast.Error(start=t, err=ERR.BADFOR, msg=msg)
                if bound:
                    msg = (
                        "'for' accepts at most one binding; move additional "
                        "definitions into a preceding 'with' or the loop body"
                    )
                    return zast.Error(start=t, err=ERR.BADFOR, msg=msg)

                name = lex.acceptany().tokstr
                lex.accept(TT.EOL)
                op = self._accept_operation(lex)
                if op is not None and op.is_error:
                    return cast(zast.Error, op)
                if not op:
                    msg = f"Expected operation for 'for' binding label: {name}"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.EXPECTEDOP, msg=msg
                    )
                op = cast(NodeX[zast.Operation], op)

                conditions[name] = op.node
                promoteexterns(addto=extern, addfrom=op.extern, local=local)
                local.add(name)
                bound = True
                first = False

            elif t.toktype == TT.LOOP:
                if loop:
                    msg = "Duplicate 'loop'"
                    return zast.Error(start=lex.acceptany(), err=ERR.BADFOR, msg=msg)

                lex.acceptany()
                statementx = self._accept_primary_expression(lex)
                if statementx is not None and statementx.is_error:
                    return cast(zast.Error, statementx)
                if not statementx:
                    msg = "Expected primary-expression for 'loop'"
                    return zast.Error(
                        start=lex.acceptany(),
                        err=ERR.EXPECTEDSTATEMENT,
                        msg=msg,
                    )
                statementx = cast(NodeX[zast.Statement], statementx)
                promoteexterns(addto=extern, addfrom=statementx.extern, local=local)
                loop = statementx.node
                first = False

            else:
                break  # nothing matched, end of 'for'

        if not conditions and not loop:
            msg = "Require at least one condition or a 'loop' specified for 'for'"
            return zast.Error(start=lex.acceptany(), err=ERR.BADFOR, msg=msg)

        fornode = zast.For(
            conditions=conditions, loop=loop, postconditions=postconditions, start=start
        )
        return NodeX(fornode, extern=extern)

    def _accept_with_expression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.With], zast.Error, None]:
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
            return zast.Error(start=lex.acceptany(), err=ERR.EXPECTEDDEF, msg=msg)
        name = t.tokstr
        lex.acceptany()  # consume the label

        # Per grammar (doc/grammar.pdoc), 'with' takes an operation — no
        # bare labels and no named-arg calls. Unnamed-arg calls (the
        # grammar `term binop` form, e.g. `abs -5`) are permitted because
        # _accept_operation materialises them as a Call/Operation.
        valnode_raw = self._accept_operation(lex)
        if valnode_raw is not None and valnode_raw.is_error:
            return cast(zast.Error, valnode_raw)
        if not valnode_raw:
            msg = (
                "Expected operation for 'with' value; wrap calls with "
                "named arguments, if/for/match, or blocks in parentheses, "
                "e.g. 'with x: (...) do ...'"
            )
            return zast.Error(start=lex.peek(), err=ERR.EXPECTEDEXP, msg=msg)
        opx = cast(NodeX[zast.Operation], valnode_raw)
        value_expr = zast.Expression(expression=opx.node, start=opx.node.start)
        valuex = NodeX(node=value_expr, extern=opx.extern)

        # the name is locally defined, don't propagate it as extern
        promoteexterns(addto=extern, addfrom=valuex.extern)

        # expect 'do'
        if not lex.accept(TT.DO):
            # If a label follows, the user likely wrote a call with named
            # arguments (e.g. `with b: bag x: 1 do b`) — `bag` parses as
            # the operation and `x: 1` then appears where `do` is expected.
            # Point them at parenthesization.
            if lex.peek().toktype in (TT.LABEL, TT.LABELPRE):
                msg = (
                    "Expected 'do' after 'with' definition. If the value "
                    "is a call with named arguments, wrap it in "
                    "parentheses, e.g. 'with x: (f a: 1 b: 2) do ...'"
                )
            else:
                msg = "Expected 'do' after 'with' definition"
            return zast.Error(start=lex.peek(), err=ERR.EXPECTEDEXP, msg=msg)

        # accept the do expression - the name is in scope here
        doexprx = self._accept_expression(lex)
        if doexprx is not None and doexprx.is_error:
            return cast(zast.Error, doexprx)
        if not doexprx:
            msg = "Expected expression after 'do'"
            return zast.Error(start=lex.acceptany(), err=ERR.EXPECTEDEXP, msg=msg)
        doexprx = cast(NodeX[zast.Expression], doexprx)

        # the locally defined name should not be promoted as extern
        promoteexterns(addto=extern, addfrom=doexprx.extern)
        extern.pop(name, None)  # remove locally defined name

        withnode = zast.With(
            name=name, value=valuex.node, doexpr=doexprx.node, start=start
        )
        return NodeX(withnode, extern=extern)

    def _accept_primary_expression(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Statement], zast.Error, None]:
        """
        Accept a primary-expression per grammar:

            primary_expression: block | operation

        Used in slots the grammar restricts to a block-or-operation
        (if/match `then`/`else`, `for` loop body). This helper:
        - accepts `{ ... }` blocks via `_accept_block`;
        - for non-brace forms, delegates to `_accept_expression`, which
          includes nested control-flow (`if`, `for`, `match`, `with`) so
          idioms like `else if ...` and `else for ...` parse without
          requiring explicit braces.

        It does NOT accept bare label-bindings, assignments, reassignments,
        or swaps — those are only valid inside a block. `_accept_expression`
        by construction does not emit those at its top level.

        Returns a Statement so the AST shape of IfClause/CaseClause/For.loop
        stays stable. Block forms are passed through as Statement-with-many-
        lines; expression forms are wrapped as a single StatementLine.
        """
        start = lex.peek()
        if start.toktype == TT.BRACEOPEN:
            return self._accept_block(lex)

        exprx = self._accept_expression(lex)
        if exprx is None:
            return None
        if exprx.is_error:
            return cast(zast.Error, exprx)
        exprx = cast(NodeX[zast.Expression], exprx)
        line = zast.StatementLine(statementline=exprx.node, start=exprx.node.start)
        stmt = zast.Statement(statements=[line], start=start)
        return NodeX(stmt, extern=exprx.extern)

    def _accept_bareblock(self, lex: Lexer) -> Union[NodeX[zast.Do], zast.Error, None]:
        """
        acceptbareblock - accept a bare block as an expression

            "{" [ blockline ] { ( eol | ";" ) [ blockline ] } "}"

        Return a Do or Error or None for no opening brace
        """
        stmtx = self._accept_block(lex)
        if stmtx is not None and stmtx.is_error:
            return cast(zast.Error, stmtx)
        if not stmtx:
            return None
        stmtx = cast(NodeX[zast.Statement], stmtx)
        do = zast.Do(statement=stmtx.node, start=stmtx.node.start)
        return NodeX(do, extern=stmtx.extern)

    def _accept_data_definition(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Data], zast.Error, None]:
        """
        acceptdata - accept a data clause

            "data" [ "is" ] "{"
                { ( [ label ] term ) | label_value }
            "}"

        Elements are single `term`s (paths) rather than full `operation`s
        so that adjacent unlabelled values like `10 20 30` stay as three
        separate elements. To use a constant operation for an element,
        wrap it in parentheses: `bytes: (1024 + 4)`.

        The typechecker rejects non-numeric element values (strings,
        records, reference values).

        Return a Data or Error or None for no data (missing "data" keyword)
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-return-statements,too-many-locals
        start = lex.peek()
        if not lex.accept(TT.DATA):
            return None

        data: List[zast.NamedOperation] = []
        extern: Dict[str, zast.AtomId] = {}

        lex.accept(TT.IS)  # optional 'is'

        if not lex.accept(TT.BRACEOPEN):
            msg = "Expected opening brace '{' for data body"
            return zast.Error(start=lex.acceptany(), err=ERR.BADDATA, msg=msg)

        datanames: Set[str] = set()
        while True:
            if lex.peek().toktype == TT.LABELPRE:
                label = lex.acceptany()
                if label.tokstr in datanames:
                    msg = f"Duplicate data member name: {label.tokstr}"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                    )
                datanames.add(label.tokstr)
                lvx = self._make_label_value(label)
                namedop = zast.NamedOperation(
                    name=label.tokstr, valtype=lvx.node, start=label
                )
                data.append(namedop)
                promoteexterns(addto=extern, addfrom=lvx.extern)
                continue

            label_tok: Optional[Token] = None
            if lex.peek().toktype == TT.LABEL:
                label_tok = lex.acceptany()
                if label_tok.tokstr in datanames:
                    msg = f"Duplicate data member name: {label_tok.tokstr}"
                    return zast.Error(
                        start=lex.acceptany(), err=ERR.BADARGUMENT, msg=msg
                    )
                datanames.add(label_tok.tokstr)

            # Data elements are single terms (paths), as grammar requires.
            # Use parentheses to embed an operation: `bytes: (1024 + 4)`.
            # Unconstrained operations would make adjacent elements
            # ambiguous — e.g. `10 MIDDLE: 20 30` could fuse `20 30` as
            # a call with unnamed argument, eating the next element.
            pathx = self._accept_path(lex)
            if pathx is not None and pathx.is_error:
                return cast(zast.Error, pathx)
            pathx = cast(Optional[NodeX[zast.Path]], pathx)
            if pathx:
                if label_tok:
                    namedop = zast.NamedOperation(
                        name=label_tok.tokstr,
                        valtype=pathx.node,
                        start=label_tok,
                    )
                else:
                    namedop = zast.NamedOperation(
                        name=None, valtype=pathx.node, start=pathx.node.start
                    )
                promoteexterns(addto=extern, addfrom=pathx.extern)
                data.append(namedop)
            else:
                if label_tok:
                    msg = f"Expected term after data label: {label_tok.tokstr}"
                    return zast.Error(start=lex.peek(), err=ERR.BADDATA, msg=msg)
                # no element, finished block
                break

        if not lex.accept(TT.BRACECLOSE):
            msg = "Expected closing brace '}' for data body"
            return zast.Error(start=lex.acceptany(), err=ERR.BADDATA, msg=msg)

        datanode = zast.Data(data=data, start=start)
        return NodeX(datanode, extern=extern)

    def _accept_operation(
        self, lex: Lexer
    ) -> Union[NodeX[zast.Operation], zast.Error, None]:
        """
        Return an Operation per grammar:

            operation: binop | ( term binop )
            binop:     term { id term }

        The `(term binop)` alternative (unnamed-argument call shape, e.g.
        `abs -5`) is materialised as a `zast.Call` with a single unnamed
        argument. Call is an Operation subclass (see zast.py).

        Named-argument calls are NOT grammar-operations: this helper never
        consumes a trailing LABEL / LABELPRE. Callers wanting to accept
        named-arg calls should use `_accept_operation_or_call` instead.
        """
        oplist, err = self._operation_paths(lex)
        if err is not None:
            return err
        if not oplist:
            return None

        # Odd-count paths → pure binop / single term.
        if len(oplist) % 2 != 0:
            return self._fold_binop(paths=oplist, nexttoken=lex.peek())

        # Even-count paths → (term binop) form: callable + one unnamed binop
        # argument. Build the Call directly without invoking `_accept_call`,
        # which would consume any trailing named arguments — those are not
        # part of a grammar operation.
        callablex = oplist[0]
        argx_raw = self._fold_binop(paths=oplist[1:], nexttoken=lex.peek())
        if argx_raw is not None and argx_raw.is_error:
            return cast(zast.Error, argx_raw)
        argx = cast(Optional[NodeX[zast.Operation]], argx_raw)
        extern: Dict[str, zast.AtomId] = dict(callablex.extern)
        arguments: List[zast.NamedOperation] = []
        if argx is not None:
            arguments.append(
                zast.NamedOperation(name=None, valtype=argx.node, start=argx.node.start)
            )
            promoteexterns(addto=extern, addfrom=argx.extern)
        call = zast.Call(
            callable=callablex.node,
            arguments=arguments,
            start=callablex.node.start,
        )
        return NodeX(node=call, extern=extern)

    def _accept_block(
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
                statementlinex = self._accept_blockline(lex)
                if statementlinex is not None and statementlinex.is_error:
                    error = cast(zast.Error, statementlinex)  # propagate error
                    break
                if not statementlinex:
                    break  # end of block (no final EOL?)
                statementlinex = cast(NodeX[zast.StatementLine], statementlinex)
                statementline = statementlinex.node
                statements.append(statementline)

                # add references
                promoteexterns(addto=extern, addfrom=statementlinex.extern, local=local)

                # add any local definition
                if statementline.statementline.nodetype == NodeType.ASSIGNMENT:
                    name = cast(zast.Assignment, statementline.statementline).name
                    if name in local:
                        # duplicate definition
                        msg = f'Duplicate definition of "{name}"'
                        error = zast.Error(
                            start=statementline.statementline.start,
                            err=ERR.DUPLICATEDEF,
                            msg=msg,
                        )
                        break
                    local.add(name)

            lex.filtereol(prev_filtereol)  # restore EOL filtering
            if error:
                return error

            if not lex.accept(TT.BRACECLOSE):
                msg = "Expected closing brace '}' for statement body"
                return zast.Error(start=lex.acceptany(), err=ERR.BADSTATEMENT, msg=msg)

            statement = zast.Statement(statements=statements, start=start)
            return NodeX(statement, extern=extern)

        # bare statement (no braces) - single statementline
        statementlinex = self._accept_blockline(lex)
        if statementlinex is not None and statementlinex.is_error:
            return cast(zast.Error, statementlinex)
        if not statementlinex:
            return None
        statementlinex = cast(NodeX[zast.StatementLine], statementlinex)

        promoteexterns(addto=extern, addfrom=statementlinex.extern)
        statement = zast.Statement(statements=[statementlinex.node], start=start)
        return NodeX(statement, extern=extern)

    def _accept_blockline(
        self, lex: Lexer
    ) -> Union[NodeX[zast.StatementLine], zast.Error, None]:
        """
        Accept a blockline — the top of a line inside a `{ ... }` block:

            ( label [ newline ] expression )   # new-binding assignment
            | label-value                       # `:name` shorthand
            | ( path "=" expression )           # reassignment
            | ( path "swap" path )              # swap
            | expression                        # bare expression

        Reassignment and swap are handled here (not in `_accept_expression`)
        — the grammar lists them as expressions but the parser keeps them
        at statement level until the typechecker gains a null-use rule
        (see plan A9.2).

        Return a StatementLine, Error, or None.
        """
        start = lex.peek()

        if start.toktype == TT.LABELPRE:  # label-value shorthand `:name`
            lex.acceptany()
            lvx = self._make_label_value(start)
            expr = zast.Expression(expression=lvx.node, start=start)
            assignment = zast.Assignment(name=start.tokstr, value=expr, start=start)
            statementline = zast.StatementLine(statementline=assignment, start=start)
            return NodeX(node=statementline, extern=lvx.extern)

        if start.toktype == TT.LABEL:  # `name: expression` new-binding
            lex.acceptany()
            lex.accept(TT.EOL)  # optional newline between label and value
            exprx = self._accept_expression(lex)
            if exprx is not None and exprx.is_error:
                return cast(zast.Error, exprx)
            if not exprx:
                msg = "Expected expression for assignment statement"
                return zast.Error(start=lex.acceptany(), err=ERR.BADSTATEMENT, msg=msg)
            exprx = cast(NodeX[zast.Expression], exprx)
            assignment = zast.Assignment(
                name=start.tokstr, value=exprx.node, start=start
            )
            statementline = zast.StatementLine(statementline=assignment, start=start)
            return NodeX(node=statementline, extern=exprx.extern)

        # Bare expression. Reassignment and swap are handled inside
        # `_accept_expression` (they are grammar expressions that return
        # `null`). A StatementLine just wraps whatever comes back.
        exprx = self._accept_expression(lex)
        if exprx is not None and exprx.is_error:
            return cast(zast.Error, exprx)
        if not exprx:
            return None
        exprx = cast(NodeX[zast.Expression], exprx)
        statementline = zast.StatementLine(statementline=exprx.node, start=start)
        return NodeX(node=statementline, extern=exprx.extern)

    def _accept_path(self, lex: Lexer) -> Union[NodeX[zast.Path], zast.Error, None]:
        """
        accept a path/atom.

            atom
            | ( path "." atomid )

        Returns the Path (NodeX) or an Error or None if no atom found
        """
        start = lex.peek()
        atomx = self._accept_atom(lex)
        if atomx is None:
            return None

        if atomx.is_error:
            return cast(zast.Error, atomx)  # propagate error
        atomx = cast(NodeX[zast.Atom], atomx)

        if lex.peek().toktype != TT.DOT:
            return atomx  # atom only

        path: Union[zast.DottedPath, zast.Atom] = atomx.node

        while lex.accept(TT.DOT):
            t = lex.accept(TT.REFID)
            if t:
                c = zast.AtomId(start=t, name=t.tokstr)
                path = zast.DottedPath(parent=path, child=c, start=start)
            else:
                # error
                msg = "Expected ID after dot"
                err = zast.Error(start=lex.acceptany(), err=ERR.BADPATH, msg=msg)
                return err

        return NodeX(node=path, extern=atomx.extern)  # only atomx is extern

    def _accept_atom(self, lex: Lexer) -> Union[NodeX[zast.Atom], zast.Error, None]:
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
            return self._accept_atomexpr(lex)

        if tt == TT.REFID:
            return self._accept_atomid(lex)

        if tt == TT.STRBEG:
            return self._accept_atomstring(lex)

        return None

    @staticmethod
    def _accept_atomid(lex: Lexer) -> Union[NodeX[zast.AtomId], None]:
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

        name = start.tokstr
        atomid: zast.AtomId = zast.AtomId(start=start, name=name)
        extern: Dict[str, zast.AtomId] = {}
        extern[name] = atomid  # atom itself is an external reference
        return NodeX(node=atomid, extern=extern)

    def _accept_atomexpr(
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

        expr = self._accept_expression(lex)
        while True:
            if expr is None:
                msg = "Expected a valid expression within parentheses"
                error = zast.Error(start=t, err=ERR.BADEXPRESSION, msg=msg)
                break

            if expr.is_error:
                error = cast(zast.Error, expr)
                break

            # restore EOL filtering BEFORE consuming ')' so that _advance
            # reads the token after ')' with the correct filtering state;
            # otherwise the EOL after ')' gets silently skipped
            lex.filtereol(prev_filtereol)

            if not lex.accept(TT.PARENCLOSE):
                msg = "Expected closing parenthesis ')' after expression"
                error = zast.Error(start=lex.peek(), err=ERR.BADEXPRESSION, msg=msg)
                break

            break

        lex.filtereol(prev_filtereol)  # restore (also covers error paths)
        if error:
            return error

        # we have a valid expression that was surrounded by parens
        return cast(NodeX[zast.Expression], expr)

    def _accept_atomstring(
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
                    return zast.Error(start=t, err=ERR.BADSTRING, msg=msg)

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
                    return zast.Error(start=lex.peek(), err=ERR.BADSTRING, msg=msg)
                expr = self._accept_expression(lex)
                if expr is None:
                    msg = "Bad expression in string interpolation"
                    return zast.Error(start=lex.peek(), err=ERR.BADEXPRESSION, msg=msg)
                if expr.is_error:
                    return cast(zast.Error, expr)  # propagate error
                expr = cast(NodeX[zast.Expression], expr)
                stringparts.append(expr.node)
                # update new with old to retain old values
                expr.extern.update(extern)
                extern = expr.extern
                if not lex.accept(TT.BRACECLOSE):
                    msg = "Expected '}' after string interpolation expression"
                    return zast.Error(start=lex.peek(), err=ERR.BADSTRING, msg=msg)
            else:
                # error
                t = lex.acceptany()
                msg = "Unexpected token in string literal"
                return zast.Error(start=t, err=ERR.BADSTRING, msg=msg)

        stringparts = _strip_string_whitespace(stringparts)
        atomstring = zast.AtomString(stringparts=stringparts, start=firsttoken)
        return NodeX(node=atomstring, extern=extern)
