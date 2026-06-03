"""
CLI wrapper for src/zastdump.

Usage: python tools/astdump.py <file.z>                  # single-file unit body
       python tools/astdump.py --program <dir> <main>    # whole-program load
       python -m tools.astdump <file.z>

Writes the canonical AST dump to stdout. Used to regenerate golden
fixtures under tests/fixtures/parser_golden/ (single-file) and
tests/fixtures/parser_program/ (whole-program).
"""

import os
import sys

_REPO_SRC = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _REPO_SRC not in sys.path:
    sys.path.insert(0, _REPO_SRC)

from zastdump import dump_ast, dump_program  # noqa: E402


def main(argv):
    if len(argv) == 4 and argv[1] == "--program":
        sys.stdout.write(dump_program(argv[2], argv[3]))
        return 0
    if len(argv) != 2:
        print(
            "usage: astdump.py <file.z> | astdump.py --program <dir> <main>",
            file=sys.stderr,
        )
        return 2
    path = argv[1]
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    sys.stdout.write(dump_ast(source))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
