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
import os
import sys

sys.path.insert(0, "src")

from zvfs import ZVfs, FSProvider, BindType
from zparser import Parser
from ztypecheck import typecheck
from ztypes import NUMERIC_RANGES, parse_literal_value
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


# ---- driver ------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO_ROOT, "src"))
    ap.add_argument("--list", action="store_true", help="print every violation")
    ap.add_argument("--check", action="store_true", help="exit 1 if any violation")
    ap.add_argument("--empty-only", action="store_true", help="skip the suffix check")
    args = ap.parse_args()

    units = sorted(
        os.path.splitext(os.path.basename(p))[0] for p in glob.glob(f"{args.src}/*.z")
    )
    tot_else = tot_then = tot_suf = 0
    for unit in units:
        program = parse_unit(unit, args.src)
        if program is None:
            print(f"{unit:14s} PARSE-ERROR")
            continue
        target = program.units[program.mainunitname]
        e_else, e_then = empty_clauses(target)
        sufs = []
        if not args.empty_only:
            try:
                sufs = redundant_suffixes(program)
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"{unit:14s} TYPECHECK-ERROR {type(exc).__name__}: {exc}")
        tot_else += len(e_else)
        tot_then += len(e_then)
        tot_suf += len(sufs)
        if e_else or e_then or sufs:
            print(
                f"{unit:14s} empty-else={len(e_else):4d}  empty-then={len(e_then):4d}"
                f"  suffix={len(sufs):4d}"
            )
        if args.list:
            for ln, col in sorted(e_else):
                print(f"  src/{unit}.z:{ln}:{col}  empty-else")
            for ln, col in sorted(e_then):
                print(f"  src/{unit}.z:{ln}:{col}  empty-then")
            for ln, col, tn in sorted(sufs):
                print(f"  src/{unit}.z:{ln}:{col}  suffix .{tn}")
    total = tot_else + tot_then + tot_suf
    print(
        f"{'TOTAL':14s} empty-else={tot_else:4d}  empty-then={tot_then:4d}"
        f"  suffix={tot_suf:4d}  ({total} total)"
    )
    if args.check and total:
        sys.exit(1)


if __name__ == "__main__":
    main()
