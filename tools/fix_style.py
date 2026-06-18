"""Apply style fixes to `src/*.z`, driven by the same analysis as
tools/lint_style.py. Each transform reparses the current file, so transforms
can be run in sequence.

Transforms:
  --suffix      strip redundant numeric type suffixes (`0.u64` -> `0`)

Dry-run by default (reports what would change). Pass --apply to write.

Usage:
  uv run python tools/fix_style.py --suffix --unit zemitterc          # dry-run
  uv run python tools/fix_style.py --suffix --unit zemitterc --apply
  uv run python tools/fix_style.py --suffix --apply                   # all units
"""

import argparse
import glob
import os
import io
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tools")

from zlexer import Tokenizer
from ztokentype import TT
from zvfs import ZVfsOpenFile, DEntryID
from zast import NodeType, cast, If, BinOp
import lint_style as L

# Comparison operators whose negation is the flipped operator.
FLIP = {"==": "!=", "!=": "==", "<": ">=", ">": "<=", "<=": ">", ">=": "<"}


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def strip_suffixes(unit, srcdir, apply):
    """Strip lint-flagged redundant suffixes. Returns (stripped, skipped)."""
    program = L.parse_unit(unit, srcdir)
    if program is None:
        print(f"{unit}: PARSE-ERROR")
        return 0, 0
    sufs = L.redundant_suffixes(program)
    path = os.path.join(srcdir, f"{unit}.z")
    lines = read_lines(path)
    by_line = {}
    for ln, col, tn in sufs:
        by_line.setdefault(ln, []).append((col, tn))
    stripped = skipped = 0
    for ln, items in by_line.items():
        s = lines[ln - 1]
        # right-to-left so earlier columns stay valid after each splice
        for col, tn in sorted(items, reverse=True):
            a, b = col - 2, col - 1 + len(tn)
            seg = s[a:b]
            if seg != "." + tn:
                print(f"  SKIP src/{unit}.z:{ln}:{col} expected '.{tn}' got {seg!r}")
                skipped += 1
                continue
            s = s[:a] + s[b:]
            stripped += 1
        lines[ln - 1] = s
    if apply and stripped:
        write_lines(path, lines)
    return stripped, skipped


WS = " \t\r\n"


def _line_starts(text):
    starts = [0]
    i = text.find("\n")
    while i != -1:
        starts.append(i + 1)
        i = text.find("\n", i + 1)
    return starts


def delete_empty_else(unit, srcdir, apply):
    """Delete empty `else {}` clauses (raw-text surgery anchored on the `{`).
    Returns (deleted, skipped)."""
    program = L.parse_unit(unit, srcdir)
    if program is None:
        print(f"{unit}: PARSE-ERROR")
        return 0, 0
    target = program.units[program.mainunitname]
    e_else, _ = L.empty_clauses(target)
    path = os.path.join(srcdir, f"{unit}.z")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    starts = _line_starts(text)
    spans = []
    skipped = 0
    for ln, col in e_else:
        P = starts[ln - 1] + (col - 1)  # offset of the else-block `{`
        if text[P : P + 1] != "{":
            print(f"  SKIP src/{unit}.z:{ln}:{col} expected '{{' got {text[P:P+1]!r}")
            skipped += 1
            continue
        q = P + 1  # forward past whitespace to the matching `}` (block is empty)
        while q < len(text) and text[q] in WS:
            q += 1
        if text[q : q + 1] != "}":
            print(f"  SKIP src/{unit}.z:{ln}:{col} non-empty else body")
            skipped += 1
            continue
        k = P - 1  # backward past whitespace to the `else` keyword
        while k >= 0 and text[k] in WS:
            k -= 1
        if text[k - 3 : k + 1] != "else":
            print(f"  SKIP src/{unit}.z:{ln}:{col} no 'else' before '{{'")
            skipped += 1
            continue
        j = k - 4  # backward past whitespace to the preceding token
        while j >= 0 and text[j] in WS:
            j -= 1
        spans.append((j + 1, q + 1))
    for s, e in sorted(spans, reverse=True):  # right-to-left keeps offsets valid
        text = text[:s] + text[e:]
    if apply and spans:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return len(spans), skipped


def _off(t, line_starts):
    return line_starts[t.lineno - 1] + (t.colno - 1)


def _tokenize(source):
    of = ZVfsOpenFile(entryid=DEntryID(0), filehandle=io.StringIO(source))
    tok = Tokenizer(of)
    out = []
    while True:
        t = tok.token()
        out.append(t)
        if t.toktype == TT.EOF:
            return out


def _brace_map(tokens, line_starts):
    """offset of every `{` -> offset of its matching `}`. String/comment braces
    are their own token types, so they don't disturb the match."""
    out, stack = {}, []
    for t in tokens:
        if t.toktype == TT.BRACEOPEN:
            stack.append(_off(t, line_starts))
        elif t.toktype == TT.BRACECLOSE and stack:
            out[stack.pop()] = _off(t, line_starts)
    return out


_TRIVIA = (TT.WS, TT.EOL, TT.COMMENT)


def _cond_source_span(ifn, then_open, tokens, idx_by_off, line_starts):
    """Source span of a single-clause `if`'s condition: from the first token
    after `if` (and optional `when`) up to the `then` keyword. Wrapping this
    raw span balances any parens it contains and is immune to string contents,
    unlike an AST-subtree span. Returns (start, end) or None."""
    i = idx_by_off.get(_off(ifn.start, line_starts))
    if i is None or tokens[i].toktype != TT.IF:
        return None
    i += 1
    while i < len(tokens) and tokens[i].toktype in _TRIVIA:
        i += 1
    if i < len(tokens) and tokens[i].toktype == TT.WHEN:
        i += 1
        while i < len(tokens) and tokens[i].toktype in _TRIVIA:
            i += 1
    if i >= len(tokens):
        return None
    cond_start = _off(tokens[i], line_starts)
    then_off = None
    while i < len(tokens):
        o = _off(tokens[i], line_starts)
        if o >= then_open:
            break
        if tokens[i].toktype == TT.THEN:
            then_off = o
        i += 1
    if then_off is None:
        return None
    return cond_start, then_off


def negate_swap_empty_then(unit, srcdir, apply):
    """`if COND then {} else { BODY }` -> negate COND and swap bodies, leaving
    `if !COND then { BODY } else {}` (the empty-else pass then drops the else).
    Single-clause, single-condition ifs only; nested/overlapping sites are
    skipped for manual handling. Returns (fixed, skipped)."""
    program = L.parse_unit(unit, srcdir)
    if program is None:
        print(f"{unit}: PARSE-ERROR")
        return 0, 0
    target = program.units[program.mainunitname]
    path = os.path.join(srcdir, f"{unit}.z")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    line_starts = _line_starts(text)
    tokens = _tokenize(text)
    bmap = _brace_map(tokens, line_starts)
    idx_by_off = {
        _off(t, line_starts): i
        for i, t in enumerate(tokens)
        if getattr(t, "lineno", 0) > 0
    }

    sites = []  # (region, edits)
    skipped = 0
    for node in L.walk(target):
        if node.nodetype != NodeType.IF:
            continue
        ifn = cast(If, node)
        if len(ifn.clauses) != 1 or ifn.elseclause is None:
            continue
        clause = ifn.clauses[0]
        if not L.is_empty_block(clause.statement) or L.is_empty_block(ifn.elseclause):
            continue
        if len(clause.conditions) != 1:
            skipped += 1  # compound when-conditions -> manual
            continue
        cond = next(iter(clause.conditions.values()))
        then_open = _off(clause.statement.start, line_starts)
        else_open = _off(ifn.elseclause.start, line_starts)
        if then_open not in bmap or else_open not in bmap:
            skipped += 1
            continue
        then_span = (then_open, bmap[then_open] + 1)
        else_span = (else_open, bmap[else_open] + 1)
        # Negate the condition: flip a comparison operator, else compare the whole
        # condition to `false` -- the codebase's idiom, since zerolang has no `not`
        # operator. (Multi-`when` conditions were already skipped above.)
        if cond.nodetype == NodeType.BINOP and cast(BinOp, cond).operator.name in FLIP:
            op = cast(BinOp, cond).operator
            o = _off(op.start, line_starts)
            edits = [(o, o + len(op.name), FLIP[op.name])]
        else:
            span = _cond_source_span(ifn, then_open, tokens, idx_by_off, line_starts)
            if span is None:
                skipped += 1
                continue
            cs, te = span
            while te > cs and text[te - 1] in WS:
                te -= 1
            edits = [(cs, te, f"({text[cs:te]}) == false")]
        edits.append((then_span[0], then_span[1], text[else_span[0] : else_span[1]]))
        edits.append((else_span[0], else_span[1], "{}"))
        region = (min(e[0] for e in edits), max(e[1] for e in edits))
        sites.append((region, edits))

    # For nested empty-then chains (`if A then {} else { if B then {} ... }`),
    # fix only the outermost site this pass: moving its else body relocates the
    # inner site verbatim, which a re-run then picks up as standalone. Skip a
    # site only when it is contained in another (an inner layer).
    def _contains(outer, inner):
        return outer[0] <= inner[0] and inner[1] <= outer[1] and outer != inner

    keep = []
    for i, (region, edits) in enumerate(sites):
        if any(_contains(sites[j][0], region) for j in range(len(sites)) if j != i):
            skipped += 1
            continue
        keep.append((region, edits))

    all_edits = [e for _, edits in keep for e in edits]
    for s, e, rep in sorted(all_edits, key=lambda x: x[0], reverse=True):
        text = text[:s] + rep + text[e:]
    if apply and keep:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return len(keep), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(os.getcwd(), "src"))
    ap.add_argument("--unit", default=None, help="one unit (default: all)")
    ap.add_argument("--suffix", action="store_true", help="strip redundant suffixes")
    ap.add_argument("--empty-then", action="store_true", help="negate+swap empty then")
    ap.add_argument("--empty-else", action="store_true", help="delete empty else {}")
    ap.add_argument("--apply", action="store_true", help="write changes")
    args = ap.parse_args()

    if args.unit:
        units = [args.unit]
    else:
        units = sorted(
            os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(f"{args.src}/*.z")
        )

    tot_fix = tot_skip = 0
    for unit in units:
        if args.suffix:
            n, sk = strip_suffixes(unit, args.src, args.apply)
            if n or sk:
                print(f"{unit:14s} suffix-strip={n:4d}  skip={sk:3d}")
            tot_fix += n
            tot_skip += sk
        if args.empty_then:
            n, sk = negate_swap_empty_then(unit, args.src, args.apply)
            if n or sk:
                print(f"{unit:14s} then-swap={n:4d}  skip={sk:3d}")
            tot_fix += n
            tot_skip += sk
        if args.empty_else:
            n, sk = delete_empty_else(unit, args.src, args.apply)
            if n or sk:
                print(f"{unit:14s} else-delete={n:4d}  skip={sk:3d}")
            tot_fix += n
            tot_skip += sk
    verb = "fixed" if args.apply else "would fix"
    print(f"TOTAL {verb}={tot_fix}  skipped={tot_skip}")


if __name__ == "__main__":
    main()
