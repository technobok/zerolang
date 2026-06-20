#!/usr/bin/env python3
"""Per-function cleanup diff: ported-emitter C vs the Python oracle (zc.py).

PROTOTYPE / rough diagnostic aid for the leak sweep. Emits a unit with both
compilers, splits each into functions, normalizes type-ids + temp-var names (so
lines referencing shared SOURCE var names line up despite the two compilers'
different id/temp schemes), and per function reports the cleanup/invalidation
lines (`_destroy(` / `_free(` / `= (T){0}` / `= NULL`) each side has that the
other lacks.

LIMITATION -- counting is the WRONG shape for measuring leaks. The port already
emits every owned local's destructor at FUNCTION-END (the emitFnBody flush), so a
missing *per-return* free does NOT show up as "missing" -- the flush is just dead
code after an early return. The count diff is dominated by noise (the flush, plus
differing protocol/destructor codegen strategies) and HIDES the real signal. A
useful version must be PLACEMENT-aware: compare the cleanup emitted immediately
BEFORE each return, port vs reference. The authoritative parity gate is the
fixpoint (tests/test_fixedpoint.py); use this only as a hint.

Usage:
    tools/cleanup_diff.py <unit> [--port <zc-binary>] [--func <substr>]
                                 [--include-fields] [--show-temps]
Without --port, builds the ported `zc` once (via zc.py) and caches it.
"""
import argparse
import collections
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
SYSDIR = os.path.join(REPO, "lib", "system")
CFLAGS = [
    "-std=c17", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-parameter",
    "-Werror=implicit-function-declaration", "-Werror=implicit-int",
    "-Werror=int-conversion", "-Werror=incompatible-pointer-types",
]
CACHED_PORT = "/tmp/cleanup_diff_zc"

_HEADER = re.compile(r"^(static\s+)?[A-Za-z_].*\bz_(?:t[0-9]+_)?[A-Za-z0-9_]+\s*\([^;]*\)\s*\{\s*$")
_NAME = re.compile(r"\bz_(?:t[0-9]+_)?([A-Za-z0-9_]+)\s*\(")
_CLEANUP = re.compile(r"_destroy\(|_free\(|=\s*\([^)]*\)\s*\{0\}|=\s*NULL")


def _run(cmd, **kw):
    subprocess.run(cmd, check=True, cwd=REPO, stdout=subprocess.DEVNULL,
                   stderr=subprocess.STDOUT, **kw)


def build_port():
    if os.path.exists(CACHED_PORT):
        return CACHED_PORT
    cpath = CACHED_PORT + ".c"
    _run([sys.executable, os.path.join(SRC, "zc.py"), "zc", "--src", SRC, "-o", cpath])
    _run(["gcc", *CFLAGS, "-o", CACHED_PORT, cpath])
    return CACHED_PORT


def emit_ref(unit, out):
    _run([sys.executable, os.path.join(SRC, "zc.py"), unit,
          "--src", SRC, "--system", SYSDIR, "-o", out])


def emit_port(port_bin, unit, out):
    _run([port_bin, unit, "--src", SRC, "--system", SYSDIR, "--emit-c", out])


def normalize(line, drop_temps):
    line = re.sub(r"z_t[0-9]+_", "z_", line)          # type-ids in symbol names
    if drop_temps:
        line = re.sub(r"_t[0-9]+(?:_[0-9]+)?\b", "_TMP", line)        # reference temps
        line = re.sub(r"_(?:c|ret|ah|m|Box|s|git|gv|u|b|sv)[0-9]+\b", "_TMP", line)  # port temps
    return line.strip()


def functions(cpath, drop_temps, include_fields):
    """Map function-name -> Counter of normalized cleanup lines in its body."""
    out = {}
    name, body, depth = None, [], 0
    for raw in open(cpath):
        raw = raw.rstrip("\n")
        if name is None:
            if _HEADER.match(raw):
                m = _NAME.search(raw)
                name, body, depth = (m.group(1) if m else raw), [], 1
        else:
            depth += raw.count("{") - raw.count("}")
            if depth <= 0:
                out[name] = collections.Counter(
                    normalize(l, drop_temps) for l in body
                    if _CLEANUP.search(l) and (include_fields or "->" not in l))
                name = None
            else:
                body.append(raw)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("unit")
    ap.add_argument("--port", help="ported zc binary (default: build + cache)")
    ap.add_argument("--func", help="only report functions whose name contains this")
    ap.add_argument("--include-fields", action="store_true",
                    help="include struct-field cleanup (type destructors); default: locals only")
    ap.add_argument("--show-temps", action="store_true",
                    help="do NOT normalize temp-var names (noisier, but exact)")
    args = ap.parse_args()

    port = args.port or build_port()
    refc = f"/tmp/cleanup_diff_ref_{args.unit}.c"
    portc = f"/tmp/cleanup_diff_port_{args.unit}.c"
    print(f"emitting {args.unit} with oracle (zc.py) and port ({port}) ...", file=sys.stderr)
    emit_ref(args.unit, refc)
    emit_port(port, args.unit, portc)

    rf = functions(refc, not args.show_temps, args.include_fields)
    pf = functions(portc, not args.show_temps, args.include_fields)

    rows, tot_missing, tot_extra = [], 0, 0
    for nm in sorted(set(rf) | set(pf)):
        if args.func and args.func not in nm:
            continue
        miss = rf.get(nm, collections.Counter()) - pf.get(nm, collections.Counter())
        extra = pf.get(nm, collections.Counter()) - rf.get(nm, collections.Counter())
        if miss or extra:
            rows.append((nm, miss, extra))
            tot_missing += sum(miss.values())
            tot_extra += sum(extra.values())

    print(f"\nunit={args.unit}  differing_fns={len(rows)}  "
          f"MISSING(leak)={tot_missing}  EXTRA(over-cleanup)={tot_extra}\n")
    for nm, miss, extra in rows:
        print(f"  {nm}  (missing={sum(miss.values())} extra={sum(extra.values())})")
        for line, c in miss.items():
            print(f"    - [ref only x{c}] {line}")
        for line, c in extra.items():
            print(f"    + [port only x{c}] {line}")
        print()


if __name__ == "__main__":
    main()
