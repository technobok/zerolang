#!/usr/bin/python3
"""
ZeroLang compiler
"""

import sys
import os
import time
import argparse

import ztypecheck
import zemitterc
import zsqldump
import zast
from zparser import Parser
from zvfs import ZVfs, FSProvider, BindType, DEntryID
from zlexer import isvalidunitname


# --- Verbose/error output helpers (centralised, no Python-specific modules) ---

_verbose_enabled = False
_color_enabled = False


def verbose(msg: str) -> None:
    """Print a verbose message to stderr (only when -v is active)."""
    if _verbose_enabled:
        sys.stderr.write(msg + "\n")


def error_msg(msg: str) -> None:
    """Print an error message to stderr."""
    if _color_enabled:
        sys.stderr.write(f"\033[1;31merror\033[0m: {msg}\n")
    else:
        sys.stderr.write(f"error: {msg}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zc",
        description="zerolang compiler — compiles .z source files to C",
        epilog=(
            "examples:\n"
            "  zc hello --src examples          compile examples/hello.z to hello.c\n"
            "  zc hello --src examples -o out.c  compile to out.c\n"
            "  zc hello --src examples -v        verbose compilation output\n"
            "  zc hello --dump-sql out.sql       compile and dump SQL diagnostics\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("unitname", help="name of the unit to compile")

    compile_group = parser.add_argument_group("compilation options")
    compile_group.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help="output C filename (default: unitname.c)",
    )
    compile_group.add_argument(
        "--src",
        default=None,
        help="path to user source directory (default: current directory)",
    )
    compile_group.add_argument(
        "--system",
        default=None,
        help="path to system library directory (default: auto-detected)",
    )
    compile_group.add_argument(
        "--full-typecheck",
        action="store_true",
        default=False,
        help="type-check all definitions, not just those reachable from main",
    )

    diag_group = parser.add_argument_group("diagnostic options")
    diag_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="verbose output (compilation progress, settings, timing)",
    )
    diag_group.add_argument(
        "--dump-sql",
        default=None,
        metavar="FILE",
        help="write SQL dump of compiler state to FILE (use - for stdout)",
    )
    diag_group.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="disable ANSI color in error messages",
    )

    args = parser.parse_args()

    # configure global output settings
    global _verbose_enabled, _color_enabled
    _verbose_enabled = args.verbose
    _color_enabled = not args.no_color and sys.stderr.isatty()

    start_time = time.monotonic()

    unitname = args.unitname.lower()
    if not isvalidunitname(unitname):
        error_msg(
            f"'{unitname}' is not a valid unit name "
            "(lowercase ASCII letters and digits only)"
        )
        sys.exit(2)

    srcdir = args.src if args.src else os.getcwd()
    if args.system:
        systemdir = args.system
    else:
        scriptdir = os.path.dirname(os.path.abspath(__file__))
        systemdir = os.path.join(scriptdir, "..", "lib", "system")

    outfn = args.output if args.output else unitname + ".c"

    verbose(f"source directory: {srcdir}")
    verbose(f"system directory: {systemdir}")
    verbose(f"output file: {outfn}")

    vfs: ZVfs = ZVfs()
    psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
    pmainid = vfs.register(FSProvider(rootpath=srcdir, parentpath=""))

    rootid: DEntryID = vfs.walk()

    try:
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
    except IOError as e:
        error_msg(f"invalid system path: {e}")
        sys.exit(2)

    rootid = vfs.bind(
        parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
    )

    # --- Parse ---
    verbose(f"parsing {unitname}...")
    p = Parser(vfs, unitname, verbose_fn=verbose if _verbose_enabled else None)
    program = p.parse()
    if isinstance(program, zast.Error):
        sys.stderr.write(
            zast.errortomessage(err=program, vfs=vfs, color=_color_enabled) + "\n"
        )
        sys.stderr.write("1 error found\n")
        sys.exit(1)

    # --- Type check ---
    verbose("type checking...")
    type_errors = ztypecheck.typecheck(program, full=args.full_typecheck)
    if type_errors:
        for err in type_errors:
            sys.stderr.write(
                zast.errortomessage(err=err, vfs=vfs, color=_color_enabled) + "\n\n"
            )
        n = len(type_errors)
        sys.stderr.write(f"{n} error{'s' if n != 1 else ''} found\n")
        sys.exit(1)
    verbose("type check passed")

    # --- Emit C ---
    verbose("emitting C...")
    emitter = zemitterc.CEmitter(program)
    csource = emitter.emit()
    with open(outfn, "w") as f:
        f.write(csource)
    verbose(f"written {outfn}")

    # --- SQL dump (optional) ---
    if args.dump_sql is not None:
        verbose("generating SQL dump...")
        sql = zsqldump.dump_sql(program, emitter=emitter, csource=csource)
        if args.dump_sql == "-":
            sys.stdout.write(sql)
        else:
            with open(args.dump_sql, "w") as f:
                f.write(sql)
            verbose(f"SQL dump written to {args.dump_sql}")

    elapsed = time.monotonic() - start_time
    verbose(f"completed in {elapsed:.3f}s")


if __name__ == "__main__":
    main()
