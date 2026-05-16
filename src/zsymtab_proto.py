"""Structural Protocol for ZSymbolTable, kept in its own module so that
`zast.Program.symbol_table` can be typed without `zast` having to
import `zenv` (which would push the import chain through the
typechecker just to declare the AST). zenv.ZSymbolTable structurally
satisfies this Protocol; ty (the type checker) verifies the match.

Only the surface that the SQL dumper consumes is captured here —
the append-only `scope_log` (F6). `_scopes`/`_history` remain on
the concrete `ZSymbolTable` for typecheck-side lookup/archival but
are no longer part of the dumper's contract.
"""

from typing import Protocol, List


class ZSymbolTableProto(Protocol):
    """Minimal duck-typed view of `zenv.ZSymbolTable` used by zsqldump.

    `scope_log` is the append-only list of `ZScopeLogRow` capturing
    every push/pop event with parent_id + open/close seq counters —
    a single source of truth for scope ordering and hierarchy.
    """

    scope_log: List
