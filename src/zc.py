#!/usr/bin/python3
"""
ZeroLang compiler
"""

import sys
import os
import argparse

import ztypecheck
import zemitterc
import zast
from zparser import Parser
from zvfs import ZVfs, FSProvider, BindType, DEntryID
from zlexer import isvalidunitname


def main() -> None:
    """
    main
    """
    parser = argparse.ArgumentParser(description="ZeroLang compiler")
    parser.add_argument("unitname", help="Name of the unit to compile")
    parser.add_argument(
        "--system",
        default=None,
        help="Path to the system directory (default: <script_dir>/system)",
    )
    parser.add_argument(
        "--src",
        default=None,
        help="Path to the user source directory (default: current directory)",
    )
    parser.add_argument(
        "--full-typecheck",
        action="store_true",
        default=False,
        help="Type-check all definitions, not just those reachable from main",
    )
    args = parser.parse_args()

    unitname = args.unitname.lower()
    if not isvalidunitname(unitname):
        print(
            f"Main unit [{unitname}] is not a valid unit name "
            + "(lowercase, ASCII letters and numbers only)"
        )
        sys.exit(1)

    srcdir = args.src if args.src else os.getcwd()
    if args.system:
        systemdir = args.system
    else:
        # default: system/ directory next to this script
        scriptdir = os.path.dirname(os.path.abspath(__file__))
        systemdir = os.path.join(scriptdir, "system")

    vfs: ZVfs = ZVfs()
    psystemid = vfs.register(FSProvider(rootpath=systemdir, parentpath=""))
    pmainid = vfs.register(FSProvider(rootpath=srcdir, parentpath=""))

    rootid: DEntryID = vfs.walk()  # old root

    # new root (mount INSTEAD)
    try:
        rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
    except IOError as e:
        print(f"Path is not valid\n{e}")
        sys.exit(1)

    # bind user namespace before system
    rootid = vfs.bind(
        parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
    )

    # outfn = unitname.split(".")[-1] + ".c"
    outfn = unitname + ".c"

    # (root, ext) = os.path.splitext(filename)
    # if ext != ".z":
    #     print("Source must be a 'z' file")
    #     sys.exit(1)

    print(f"Compiling module [{unitname}] to [{outfn}]")

    p = Parser(vfs, unitname)
    program = p.parse()  # first pass
    if isinstance(program, zast.Error):
        print(zast.errortomessage(err=program, vfs=vfs))
        # print("Error:")
        # do proper error printing, with file location
        # print(repr(program))
        sys.exit(1)
    # type check (second pass)
    type_errors = ztypecheck.typecheck(program, full=args.full_typecheck)
    if type_errors:
        for err in type_errors:
            print(zast.errortomessage(err=err, vfs=vfs))
        sys.exit(1)

    print("Type check passed.")

    csource = zemitterc.emit(program)
    with open(outfn, "w") as f:
        f.write(csource)
    print(f"Written [{outfn}]")


if __name__ == "__main__":
    main()
