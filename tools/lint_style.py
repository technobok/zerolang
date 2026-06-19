"""Style lint over the ported `src/*.z` sources.

Checks:
  empty-else  -- a statement `if` carries an empty `else {}` (just delete it).
  empty-then  -- a clause's `then {}` is empty while the `else` does the work
                 (a backwards condition: negate it and move the work into `then`).
  suffix      -- a numeric literal carries an explicit type suffix (`0.u64`) that
                 the context already fixes (a typed binop/comparison operand, the
                 enclosing function return type, or a typed reassignment target),
                 so a bare literal would coerce to the same type.

Empty `case` arms are NOT flagged: a union/variant `match` must stay exhaustive.
The suffix check is deliberately conservative -- it only flags a suffix when a
genuine non-literal anchor of the same type is present, so all-literal
expressions and fresh bindings (genuine type origins) are left alone.

Usage (from repo root):
  uv run python tools/lint_style.py            # per-file + total counts
  uv run python tools/lint_style.py --list     # every violation, file:line:col
  uv run python tools/lint_style.py --check    # exit 1 if any violation (ratchet)
"""

import argparse
import glob
import io
import os
import sys

sys.path.insert(0, "src")

from zvfs import ZVfs, FSProvider, BindType
from zparser import Parser
from ztypecheck import typecheck
from ztypes import NUMERIC_RANGES, parse_literal_value
from zlexer import Tokenizer
from ztokentype import TT
from zvfs import StringProvider, ZVfsOpenFile, DEntryID
from zast import (
    NodeType,
    CallKind,
    cast,
    node_children,
    If,
    Statement,
    DottedPath,
    AtomId,
    BinOp,
    Reassignment,
    NamedOperation,
    Call,
    Function,
    Expression,
)

REPO_ROOT = os.getcwd()
SYSTEM_DIR = os.path.join(REPO_ROOT, "lib", "system")

NUMERIC_TYPE_NAMES = frozenset(NUMERIC_RANGES) | {"f32", "f64", "f128"}


def parse_unit(unit, srcdir):
    """Parse one src unit (system/collections available). Returns Program or None."""
    vfs = ZVfs()
    sysid = vfs.register(FSProvider(rootpath=SYSTEM_DIR, parentpath=""))
    srcid = vfs.register(FSProvider(rootpath=srcdir, parentpath=""))
    root = vfs.walk()
    root = vfs.bind(parentid=root, name=None, newid=sysid)
    root = vfs.bind(parentid=root, name=None, newid=srcid, bindtype=BindType.BEFORE)
    program = Parser(vfs, unit).parse()
    if program.is_error:
        return None
    return program


def walk(node):
    """Preorder over every Node in the subtree rooted at `node`."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(node_children(n))


def is_empty_block(stmt):
    """`then`/`else` is a Statement; an empty `{}` block has no statement lines."""
    return (
        stmt is not None
        and stmt.nodetype == NodeType.STATEMENT
        and len(cast(Statement, stmt).statements) == 0
    )


def pos(node):
    """(line, col) of a node's start token (1-indexed)."""
    t = node.start
    return (getattr(t, "lineno", 0), getattr(t, "colno", 0))


# ---- empty if-clause check (parse only) --------------------------------------


def empty_clauses(target):
    """Return (empty_else, empty_then) lists of (line, col)."""
    empty_else, empty_then = [], []
    for node in walk(target):
        if node.nodetype != NodeType.IF:
            continue
        ifn = cast(If, node)
        else_present = ifn.elseclause is not None
        if else_present and is_empty_block(ifn.elseclause):
            empty_else.append(pos(ifn.elseclause))
        elif (
            else_present
            and ifn.elseclause.nodetype == NodeType.STATEMENT
            and ifn.elseclause.start.tokstr == "{"
            and len(ifn.clauses) == 1
        ):
            # Flag only the simple backwards-condition antipattern: one clause,
            # one condition, and a braced `else { ... }` block -- the form that
            # negates + swaps cleanly. Compound `when` conditions and `else if`
            # chains (the else is a bare `if`, not a `{` block) can't be reversed
            # into a single condition (zerolang's `when` is AND-only), so they
            # are left alone.
            clause = ifn.clauses[0]
            if is_empty_block(clause.statement) and len(clause.conditions) == 1:
                empty_then.append(pos(clause.statement))
    return empty_else, empty_then


# ---- redundant numeric-suffix check (needs typecheck) ------------------------


def suffix_type(dp):
    """If `dp` is a `<numeric-literal>.<numeric-type>` whose suffix is safely
    redundant, return the suffix type name; else None.

    A suffix is kept (returns None) when the literal value does not fit its
    base's default type (i64 for decimal, u64 for hex/bin). Such a literal --
    e.g. `18446744073709551615.u64` (u64::MAX, > i64 max) -- relies on the
    suffix to be representable; the bare form does not reliably re-derive the
    type from context, so the suffix is load-bearing, not noise."""
    if dp.nodetype != NodeType.DOTTEDPATH:
        return None
    d = cast(DottedPath, dp)
    if d.child.nodetype != NodeType.ATOMID:
        return None
    tname = cast(AtomId, d.child).name
    if tname not in NUMERIC_TYPE_NAMES:
        return None
    if d.parent.nodetype != NodeType.ATOMID:
        return None
    pname = cast(AtomId, d.parent).name
    val = parse_literal_value(pname)
    if val is None:
        return None
    if isinstance(val, int):
        nondec = pname.lower().startswith(("0x", "0b", "0o"))
        lo, hi = NUMERIC_RANGES["u64" if nondec else "i64"]
        if not (lo <= val <= hi):
            return None
    return tname


def climb(dp, parent):
    """Climb out of transparent wrappers -- an Expression, or the NamedOperation
    that wraps a call/return argument -- to the meaningful semantic parent (the
    binop, the return Call, etc.). Return (semantic_parent, branch) where branch
    is semantic_parent's direct child on the path down to `dp`."""
    cur = dp
    p = parent.get(cur.nodeid)
    while p is not None and p.nodetype in (NodeType.EXPRESSION, NodeType.NAMEDOPERATION):
        cur = p
        p = parent.get(p.nodeid)
    return p, cur


def is_anchor(node, tname, node_type, node_const):
    """True if `node` is a concrete non-literal value of numeric type `tname`
    -- a genuine type anchor (not itself a literal that would also default)."""
    t = node_type.get(node.nodeid)
    return (
        t is not None
        and not t.is_literal
        and t.name == tname
        and node.nodeid not in node_const
    )


def redundant_suffixes(program):
    """Return list of (line, col, tname) for suffixes the context makes
    redundant. Structure is read from the PARSED AST (before typecheck's
    arg-hoisting rewrites named call-args into temp bindings, which would hide
    them); types come from typecheck."""
    target = program.units[program.mainunitname]
    parent = {}  # nodeid -> parent Node
    funcret = {}  # nodeid -> enclosing function's returntype Node (or None)
    suffixes = []  # suffix-literal DottedPath nodes
    stack = [(target, None, None)]
    while stack:
        n, par, rnode = stack.pop()
        parent[n.nodeid] = par
        if n.nodetype == NodeType.FUNCTION:
            rnode = cast(Function, n).returntype
        funcret[n.nodeid] = rnode
        if suffix_type(n) is not None:
            suffixes.append(n)
        for ch in node_children(n):
            stack.append((ch, n, rnode))

    zt = typecheck(program, full=False)
    nt = zt.node_type
    nc = zt.node_const_value
    ck = zt.call_kind
    out = []
    for node in suffixes:
        tname = suffix_type(node)
        sp, branch = climb(node, parent)
        if sp is None:
            continue
        redundant = False
        if sp.nodetype == NodeType.BINOP:
            bo = cast(BinOp, sp)
            sib = bo.rhs if branch is bo.lhs else bo.lhs
            redundant = is_anchor(sib, tname, nt, nc)
        elif sp.nodetype == NodeType.REASSIGNMENT:
            ra = cast(Reassignment, sp)
            if branch is ra.value:
                tt = nt.get(ra.topath.nodeid)
                redundant = tt is not None and tt.name == tname
        elif sp.nodetype == NodeType.CALL:
            cc = cast(Call, sp)
            if ck.get(sp.nodeid) == CallKind.RETURN:
                rn = funcret.get(node.nodeid)
                rt = nt.get(rn.nodeid) if rn is not None else None
                redundant = rt is not None and rt.name == tname
            elif (
                branch.nodetype == NodeType.NAMEDOPERATION
                and cast(NamedOperation, branch).name
                and cc.callable is not None
            ):
                # named call-argument or constructor field: the parameter/field
                # type fixes the literal. The callable's resolved type is the
                # function (children are params) or, for construction, the type
                # itself (children are fields). Positional args are left alone.
                callee = nt.get(cc.callable.nodeid)
                if callee is not None:
                    pt = zt.child_of(callee, cast(NamedOperation, branch).name)
                    redundant = pt is not None and pt.name == tname
        if redundant:
            ln, col = pos(cast(DottedPath, node).child)
            out.append((ln, col, tname))
    return out


# ---- elidable first generic-argument name (parse only) -----------------------

# The primary (first) generic parameter of each built-in template — the one
# name a type specifier may omit (spec.pdoc 825-843). Map keeps `value:`; only
# its `key:` is elidable. Index 0 ONLY: eliding a later argument's name (Map's
# `value`, Result's second) would break monomorphisation.
PRIMARY_GENERIC_PARAM = {
    "List": "of",
    "Set": "of",
    "array": "of",
    "Map": "key",
    "option": "t",
    "optionval": "t",
    "Option": "t",
    "OptionView": "t",
    "Result": "t",
}


def elidable_type_arg_names(target):
    """Return (line, col, name, valline, valcol) for every type specifier whose
    first generic argument carries its primary parameter name — the name the
    spec allows to be omitted. Parse-only, keyed on the built-in template name;
    an already-elided first arg (`name is None`) is left alone."""
    out = []
    for node in walk(target):
        if node.nodetype != NodeType.CALL:
            continue
        call = cast(Call, node)
        if call.callable is None or call.callable.nodetype != NodeType.ATOMID:
            continue
        primary = PRIMARY_GENERIC_PARAM.get(cast(AtomId, call.callable).name)
        if primary is None or not call.arguments:
            continue
        first = call.arguments[0]
        if first.name != primary:
            continue
        nl, nc = pos(first)
        vl, vc = pos(cast(NamedOperation, first).valtype)
        out.append((nl, nc, primary, vl, vc))
    return out


# ---- unneeded parentheses (focused: type specifiers + single terms) ----------

# A single bare term is removable anywhere; a no-named-arg call / type spec is
# removable only in a TYPE position (parameter / return / field) where the
# grammar accepts a bare operation. Value-position calls (return values, binop
# operands, args) are left alone -- their removability is context-dependent.
# Every removal is re-parse-verified to leave the AST unchanged.
_PAREN_TERM_KINDS = frozenset(
    {NodeType.ATOMID, NodeType.DOTTEDPATH, NodeType.ATOMSTRING}
)
# Only FUNCTION (parameter / return) type positions: there a stripped bare
# generic (`x: List u8`) is wrapped in an Expression by the parser (Phase A) and
# resolves in both compilers. Object-def FIELD types are excluded -- a bare field
# generic (`f: List u8`) is emitted as a bare Call that the ported compiler does
# not yet resolve (needs a follow-up fix), so those parens are kept.
_PAREN_TYPE_PARENTS = frozenset({NodeType.FUNCTION})
_PAREN_TRIVIA = frozenset({TT.WS, TT.EOL, TT.COMMENT})


def _paren_line_starts(text):
    starts = [0]
    i = text.find("\n")
    while i != -1:
        starts.append(i + 1)
        i = text.find("\n", i + 1)
    return starts


def paren_lc(off, line_starts):
    import bisect

    li = bisect.bisect_right(line_starts, off) - 1
    return li + 1, off - line_starts[li] + 1


def _paren_off(t, line_starts):
    return line_starts[t.lineno - 1] + (t.colno - 1)


def _paren_tokenize(source):
    of = ZVfsOpenFile(entryid=DEntryID(0), filehandle=io.StringIO(source))
    tok = Tokenizer(of)
    out = []
    while True:
        t = tok.token()
        out.append(t)
        if t.toktype == TT.EOF:
            return out


def _paren_norm(node):
    while node is not None and node.nodetype == NodeType.EXPRESSION:
        node = cast(Expression, node).expression
    if node is None:
        return None
    name = getattr(node, "name", None)
    if not isinstance(name, str):
        name = None
    return (int(node.nodetype), name, tuple(_paren_norm(c) for c in node_children(node)))


def _paren_unit_norm(program):
    target = program.units[program.mainunitname]
    return tuple(_paren_norm(c) for c in node_children(target))


_PAREN_ALL_UNITS = {}


def _paren_all_units(srcdir):
    if srcdir not in _PAREN_ALL_UNITS:
        prog = parse_unit("zc", srcdir)  # zc transitively pulls in every unit
        _PAREN_ALL_UNITS[srcdir] = dict(prog.units) if prog is not None else {}
    return _PAREN_ALL_UNITS[srcdir]


def _paren_parse_override(unit, srcdir, text):
    prebuilt = {k: v for k, v in _paren_all_units(srcdir).items() if k != unit}
    vfs = ZVfs()
    sysid = vfs.register(FSProvider(rootpath=SYSTEM_DIR, parentpath=""))
    srcid = vfs.register(FSProvider(rootpath=srcdir, parentpath=""))
    ovrid = vfs.register(StringProvider(files={f"{unit}.z": text}))
    root = vfs.walk()
    root = vfs.bind(parentid=root, name=None, newid=sysid)
    root = vfs.bind(parentid=root, name=None, newid=srcid, bindtype=BindType.BEFORE)
    root = vfs.bind(parentid=root, name=None, newid=ovrid, bindtype=BindType.BEFORE)
    program = Parser(vfs, unit, prebuilt=prebuilt).parse()
    return None if program.is_error else program


def strip_paren_offsets(text, pairs):
    for off in sorted({o for p in pairs for o in p}, reverse=True):
        text = text[:off] + text[off + 1 :]
    return text


def _paren_candidates(program, text, line_starts):
    target = program.units[program.mainunitname]
    outermost = {}
    parent = {}
    for node in walk(target):
        st = getattr(node, "start", None)
        if st is not None and getattr(st, "lineno", 0) > 0:
            outermost.setdefault(_paren_off(st, line_starts), node)
        for c in node_children(node):
            parent[id(c)] = node
    tokens = _paren_tokenize(text)
    pairs, stack = [], []
    for i, t in enumerate(tokens):
        if t.toktype == TT.PARENOPEN:
            stack.append((i, _paren_off(t, line_starts)))
        elif t.toktype == TT.PARENCLOSE and stack:
            oi, oo = stack.pop()
            pairs.append((oi, oo, i, _paren_off(t, line_starts)))
    by_open = {p[0]: p for p in pairs}

    def nxt(i):
        j = i + 1
        while j < len(tokens) and tokens[j].toktype in _PAREN_TRIVIA:
            j += 1
        return j

    def prv(i):
        j = i - 1
        while j >= 0 and tokens[j].toktype in _PAREN_TRIVIA:
            j -= 1
        return j

    cands = []
    for oi, oo, ci, co in pairs:
        if paren_lc(oo, line_starts)[0] != paren_lc(co, line_starts)[0]:
            continue  # multi-line paren: intentional EOL wrapping (e.g. match subject)
        k = nxt(oi)  # redundant double paren: content is exactly an inner group
        if k < len(tokens) and tokens[k].toktype == TT.PARENOPEN:
            inner = by_open.get(k)
            if inner is not None and inner[2] == prv(ci):
                cands.append((oo, co))
                continue
        j = oi + 1  # content start: skip trivia and nested `(`
        while j < len(tokens) and (
            tokens[j].toktype in _PAREN_TRIVIA or tokens[j].toktype == TT.PARENOPEN
        ):
            j += 1
        if j >= len(tokens):
            continue
        expr_node = outermost.get(_paren_off(tokens[j], line_starts))
        base = expr_node
        while base is not None and base.nodetype == NodeType.EXPRESSION:
            base = cast(Expression, base).expression
        if base is None:
            continue
        if base.nodetype in _PAREN_TERM_KINDS:
            cands.append((oo, co))
        elif base.nodetype == NodeType.CALL:
            if any(a.name is not None for a in cast(Call, base).arguments):
                continue
            par = parent.get(id(expr_node))
            if par is not None and par.nodetype in _PAREN_TYPE_PARENTS:
                cands.append((oo, co))
    return cands


def _paren_verify(unit, srcdir, base_norm, text, pairs):
    if not pairs:
        return True
    prog = _paren_parse_override(unit, srcdir, strip_paren_offsets(text, pairs))
    return prog is not None and _paren_unit_norm(prog) == base_norm


def _paren_max_safe(unit, srcdir, base_norm, text, pairs):
    if _paren_verify(unit, srcdir, base_norm, text, pairs):
        return pairs
    if len(pairs) <= 1:
        return []
    mid = len(pairs) // 2
    left = _paren_max_safe(unit, srcdir, base_norm, text, pairs[:mid])
    right = _paren_max_safe(unit, srcdir, base_norm, text, pairs[mid:])
    combined = left + right
    if _paren_verify(unit, srcdir, base_norm, text, combined):
        return combined
    return left if len(left) >= len(right) else right


def removable_paren_pairs(unit, srcdir):
    """[(open_off, close_off)] for every extraneous paren around a single term,
    a no-named-arg type specifier in a type position, or a redundant double
    paren -- each verified by re-parse to leave the AST unchanged."""
    path = os.path.join(srcdir, f"{unit}.z")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    program = _paren_parse_override(unit, srcdir, text)
    if program is None:
        return []
    line_starts = _paren_line_starts(text)
    cands = _paren_candidates(program, text, line_starts)
    if not cands:
        return []
    return _paren_max_safe(unit, srcdir, _paren_unit_norm(program), text, cands)


# ---- driver ------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO_ROOT, "src"))
    ap.add_argument("--list", action="store_true", help="print every violation")
    ap.add_argument("--check", action="store_true", help="exit 1 if any violation")
    ap.add_argument(
        "--check-elide", action="store_true", help="also gate elide-name in --check"
    )
    ap.add_argument(
        "--check-parens",
        action="store_true",
        help="also gate re-parse-verified unneeded parens in --check (slow)",
    )
    ap.add_argument("--empty-only", action="store_true", help="skip the suffix check")
    args = ap.parse_args()

    units = sorted(
        os.path.splitext(os.path.basename(p))[0] for p in glob.glob(f"{args.src}/*.z")
    )
    tot_else = tot_then = tot_suf = tot_elide = tot_parens = 0
    for unit in units:
        program = parse_unit(unit, args.src)
        if program is None:
            print(f"{unit:14s} PARSE-ERROR")
            continue
        target = program.units[program.mainunitname]
        e_else, e_then = empty_clauses(target)
        elides = elidable_type_arg_names(target)
        parens = removable_paren_pairs(unit, args.src) if args.check_parens else []
        sufs = []
        if not args.empty_only:
            try:
                sufs = redundant_suffixes(program)
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"{unit:14s} TYPECHECK-ERROR {type(exc).__name__}: {exc}")
        tot_else += len(e_else)
        tot_then += len(e_then)
        tot_suf += len(sufs)
        tot_elide += len(elides)
        tot_parens += len(parens)
        if e_else or e_then or sufs or elides or parens:
            print(
                f"{unit:14s} empty-else={len(e_else):4d}  empty-then={len(e_then):4d}"
                f"  suffix={len(sufs):4d}  elide-name={len(elides):4d}"
                f"  paren={len(parens):4d}"
            )
        if args.list:
            for ln, col in sorted(e_else):
                print(f"  src/{unit}.z:{ln}:{col}  empty-else")
            for ln, col in sorted(e_then):
                print(f"  src/{unit}.z:{ln}:{col}  empty-then")
            for ln, col, tn in sorted(sufs):
                print(f"  src/{unit}.z:{ln}:{col}  suffix .{tn}")
            for ln, col, name, *_ in sorted(elides):
                print(f"  src/{unit}.z:{ln}:{col}  elide-name {name}:")
            if parens:
                pls = _paren_line_starts(
                    open(os.path.join(args.src, f"{unit}.z"), encoding="utf-8").read()
                )
                for oo, _co in sorted(parens):
                    ln, col = paren_lc(oo, pls)
                    print(f"  src/{unit}.z:{ln}:{col}  paren")
    total = tot_else + tot_then + tot_suf + tot_elide + tot_parens
    print(
        f"{'TOTAL':14s} empty-else={tot_else:4d}  empty-then={tot_then:4d}"
        f"  suffix={tot_suf:4d}  elide-name={tot_elide:4d}  paren={tot_parens:4d}"
        f"  ({total} total)"
    )
    # `--check` gates the checks that the corpus already satisfies. `elide-name`
    # is reported but not gated until the sweep that drives it to zero; passing
    # `--check-elide` opts into gating it (used once the sweep has landed).
    gated = tot_else + tot_then + tot_suf
    if args.check_elide:
        gated += tot_elide
    if args.check_parens:
        gated += tot_parens
    if args.check and gated:
        sys.exit(1)


if __name__ == "__main__":
    main()
