"""
CLI wrapper for src/zastdump.dump_ast.

Usage: python tools/astdump.py <file.z>
       python -m tools.astdump <file.z>

Writes the canonical AST dump to stdout. Used to regenerate golden
fixtures under tests/fixtures/parser_golden/.
"""

import os
import sys

_REPO_SRC = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _REPO_SRC not in sys.path:
    sys.path.insert(0, _REPO_SRC)

from zastdump import dump_ast  # noqa: E402


def main(argv):
    if len(argv) != 2:
        print("usage: astdump.py <file.z>", file=sys.stderr)
        return 2
    path = argv[1]
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    sys.stdout.write(dump_ast(source))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
