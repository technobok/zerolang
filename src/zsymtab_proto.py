"""Structural Protocol for SymbolTable, kept in its own module so that
`zast.Program.symbol_table` can be typed without `zast` having to
import `zenv` (which would push the import chain through the
typechecker just to declare the AST). zenv.SymbolTable structurally
satisfies this Protocol; ty (the type checker) verifies the match.

Only the surface that the SQL dumper consumes is captured here —
specifically the scope-walk roots `_scopes` and `_history`. Concrete
scope/entry/variable shapes live in zenv/ztypes and are imported
directly by callers that need them.
"""

from typing import Protocol, List


class SymbolTableProto(Protocol):
    """Minimal duck-typed view of `zenv.SymbolTable` used by zsqldump.

    `_scopes` is the live scope stack at end-of-typecheck, `_history`
    is the archived list of scopes popped during typecheck. The dumper
    walks both to reconstruct the full scope life cycle.
    """

    _scopes: List
    _history: List
