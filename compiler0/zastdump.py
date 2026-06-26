"""
Canonical AST dump for the parser differential.

Parses one `.z` file's top-level unit body with the Python reference parser
(`zparser.Parser._accept_unitbody`) and emits the same id-stripped canonical
text as `zast.z`'s `printNodeCanonical`, so the self-hosted parser's
`out/zparser <file>` output can be compared byte-for-byte (mirrors
`src/ztokendump.dump_tokens` for the lexer differential).

The Python AST stores several member maps (Unit.body, Function.parameters /
as_items, ObjectDef.is_items / as_items, If/For conditions) as plain dicts,
where the zerolang AST stores ordered `namedoperation` arms. We synthesize a
`namedoperation` line per dict entry. Synthesized/anonymous condition keys
(`" *N"`) and missing names render as `<none>`, matching the zerolang side.
The `namedoperation` header carries no position (the label-token position is
not recoverable from the Python dict representation).
"""

import io
from typing import cast

import zast
from zlexer import Tokenizer, Lexer
from zvfs import ZVfsOpenFile, DEntryID, ZVfs, FSProvider
from zparser import Parser, NodeX
from zast import NodeType


_ITEM_KINDS = (
    NodeType.RECORD,
    NodeType.CLASS,
    NodeType.UNION,
    NodeType.VARIANT,
    NodeType.ENUM,
    NodeType.PROTOCOL,
    NodeType.FACET,
)


def _pos(node) -> str:
    return f"{node.start.lineno}:{node.start.colno}"


def _norm_name(name) -> str:
    # Synthesized/anonymous condition keys (`" *N"` for if/for while/when)
    # and missing names render as <none>. Avoid str.startswith (the
    # self-host bootstrap-lint ratchets it); a leading-space slice suffices.
    if name is None or name[:1] == " ":
        return "<none>"
    return name


def _opt_child(node_or_none, out) -> None:
    if node_or_none is not None:
        _emit(node_or_none, out)
    else:
        out.append("  <none>")


def _synth_namedop(name, value_node, out) -> None:
    out.append("namedoperation")
    out.append(f"  name={_norm_name(name)}")
    _emit(value_node, out)


def _emit(node, out) -> None:
    nt = node.nodetype

    if nt == NodeType.UNIT:
        out.append(f"unitdef @{_pos(node)} body={len(node.body)}")
        for k, v in node.body.items():
            _synth_namedop(k, v, out)
    elif nt == NodeType.FUNCTION:
        out.append(
            f"functiondef @{_pos(node)} params={len(node.parameters)} "
            f"isNative={1 if node.is_native else 0}"
        )
        out.append("  returntype:")
        _opt_child(node.returntype, out)
        for k, v in node.parameters.items():
            _synth_namedop(k, v, out)
        out.append("  body:")
        _opt_child(node.body, out)
        for k, v in node.as_items.items():
            _synth_namedop(k, v, out)
    elif nt in _ITEM_KINDS:
        out.append(
            f"objectdef @{_pos(node)} kind={nt.name} "
            f"isItems={len(node.is_items)} asItems={len(node.as_items)} "
            f"isNative={1 if node.is_native else 0}"
        )
        for k, v in node.is_items.items():
            _synth_namedop(k, v, out)
        for k, v in node.as_items.items():
            _synth_namedop(k, v, out)
    elif nt == NodeType.NAMEDOPERATION:
        out.append("namedoperation")
        out.append(f"  name={_norm_name(node.name)}")
        _emit(node.valtype, out)
    elif nt == NodeType.CALL:
        out.append(f"call @{_pos(node)} args={len(node.arguments)}")
        _emit(node.callable, out)
        for a in node.arguments:
            _emit(a, out)
    elif nt == NodeType.DATA:
        out.append(f"datablock @{_pos(node)} elements={len(node.data)}")
        for e in node.data:
            _emit(e, out)
        out.append("  outType:")
        _opt_child(node.out_type, out)
    elif nt == NodeType.IF:
        out.append(f"ifexpr @{_pos(node)} clauses={len(node.clauses)}")
        for cl in node.clauses:
            _emit(cl, out)
        out.append("  elseclause:")
        _opt_child(node.elseclause, out)
    elif nt == NodeType.IFCLAUSE:
        out.append(f"ifclause @{_pos(node)} conditions={len(node.conditions)}")
        for k, v in node.conditions.items():
            _synth_namedop(k, v, out)
        _emit(node.statement, out)
    elif nt == NodeType.FOR:
        out.append(
            f"forexpr @{_pos(node)} conditions={len(node.conditions)} "
            f"postconditions={len(node.postconditions)}"
        )
        for k, v in node.conditions.items():
            _synth_namedop(k, v, out)
        out.append("  body:")
        _opt_child(node.loop, out)
        for pc in node.postconditions:
            _emit(pc, out)
    elif nt == NodeType.CASE:
        out.append(f"caseexpr @{_pos(node)} clauses={len(node.clauses)}")
        _emit(node.subject, out)
        for cl in node.clauses:
            _emit(cl, out)
        out.append("  elseclause:")
        _opt_child(node.elseclause, out)
    elif nt == NodeType.CASECLAUSE:
        out.append(f"caseclause @{_pos(node)} name={node.name}")
        _emit(node.match, out)
        _emit(node.statement, out)
    elif nt == NodeType.WITH:
        out.append(f"withexpr @{_pos(node)} name={node.name}")
        _emit(node.value, out)
        _emit(node.doexpr, out)
    elif nt == NodeType.DO:
        out.append(f"doexpr @{_pos(node)}")
        _emit(node.statement, out)
    elif nt == NodeType.YIELD:
        out.append(f"yieldexpr @{_pos(node)}")
        _emit(node.expr, out)
    elif nt == NodeType.STATEMENT:
        out.append(f"statement @{_pos(node)} statements={len(node.statements)}")
        for s in node.statements:
            _emit(s, out)
    elif nt == NodeType.STATEMENTLINE:
        out.append(f"statementline @{_pos(node)}")
        _emit(node.statementline, out)
    elif nt == NodeType.ASSIGNMENT:
        out.append(f"assignment @{_pos(node)} name={node.name}")
        _emit(node.value, out)
    elif nt == NodeType.REASSIGNMENT:
        out.append(f"reassignment @{_pos(node)}")
        _emit(node.topath, out)
        _emit(node.value, out)
    elif nt == NodeType.SWAP:
        out.append(f"swapstmt @{_pos(node)}")
        _emit(node.lhs, out)
        _emit(node.rhs, out)
    elif nt == NodeType.EXPRESSION:
        out.append(f"expression @{_pos(node)}")
        _emit(node.expression, out)
    elif nt == NodeType.BINOP:
        out.append(f"binop @{_pos(node)}")
        _emit(node.lhs, out)
        _emit(node.operator, out)
        _emit(node.rhs, out)
    elif nt == NodeType.DOTTEDPATH:
        out.append(f"dottedpath @{_pos(node)}")
        _emit(node.parent, out)
        _emit(node.child, out)
    elif nt == NodeType.ATOMSTRING:
        out.append(f"atomstring @{_pos(node)} parts={len(node.stringparts)}")
        for p in node.stringparts:
            _emit(p, out)
    elif nt == NodeType.TYPEOFEXPR:
        out.append(f"typeofexpr @{_pos(node)}")
        _emit(node.source, out)
    elif nt == NodeType.ATOMID:
        out.append(f"atomid @{_pos(node)} name={node.name}")
    elif nt == NodeType.LABELVALUE:
        out.append(f"labelvalue @{_pos(node)} name={node.name}")
    elif nt == NodeType.STRINGCHUNK:
        # chunk_kind is a TT (IntEnum); emit the numeric TT code (5/21/22) the
        # zerolang side stores, not the enum's str() name.
        out.append(
            f"stringchunk @{_pos(node)} text={node.text} "
            f"chunkKind={int(node.chunk_kind)}"
        )
    elif nt == NodeType.ERROR:
        out.append(f"error @{_pos(node)} err={node.err.name} msg={node.msg}")
    else:
        raise AssertionError(f"zastdump: unhandled NodeType {nt}")


def dump_ast(source: str, fsno: int = 0) -> str:
    """Parse a single file's unit body and return the canonical AST dump."""
    fh = io.StringIO(source)
    openfile = ZVfsOpenFile(entryid=DEntryID(fsno), filehandle=fh)
    tok = Tokenizer(openfile)
    lex = Lexer(tok)
    parser = Parser(ZVfs(), "main")
    result = parser._accept_unitbody(lex)
    out: list[str] = []
    if result.is_error:
        _emit(result, out)
    else:
        _emit(cast(NodeX[zast.Unit], result).node, out)
    return "\n".join(out) + "\n"


def dump_program(root_dir: str, mainunitname: str = "main") -> str:
    """Load a whole program over a filesystem VFS rooted at `root_dir` and
    return the canonical Program dump, matching `out/zparser --program`.

    Units are dumped in `Program.units` insertion order (core first, then
    main, then each transitively-referenced unit in resolution order), each
    as a synthesized `namedoperation` wrapping the unit's `unitdef` — the
    `.z` `ProgramData.units` list stores the same namedoperation arms. The
    program line carries no source position (`@1:1` on both sides; a Program
    has no token), matching `ProgramData`'s fixed lineno/colno.
    """
    vfs = ZVfs()
    pid = vfs.register(FSProvider(rootpath=root_dir, parentpath=""))
    rootid = vfs.walk()
    vfs.bind(parentid=rootid, name=None, newid=pid)
    parser = Parser(vfs, mainunitname)
    result = parser.parse()
    out: list[str] = []
    if result.is_error:
        _emit(result, out)
    else:
        prog = cast(zast.Program, result)
        out.append(
            f"program @1:1 units={len(prog.units)} mainUnitName={prog.mainunitname}"
        )
        for name, unit in prog.units.items():
            _synth_namedop(name, unit, out)
    return "\n".join(out) + "\n"
