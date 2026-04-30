"""
Typing — typecheck-output container.

The parser produces a `zast.Program` (immutable post-parse). The
typechecker takes that `Program` and produces a `Typing`: the
container of every typecheck-derived datum the downstream consumers
(emitter, SQL dumper, asthash) need to read.

Architecturally:

    zast.Program (frozen tree of parsed nodes)
        ↓  TypeChecker(...).check()
    ztyping.Typing (mutable container; component tables)
        ↓
    CEmitter / zsqldump / zasthash

`Typing` is owned by the typechecker module conceptually but lives
in its own file so consumers can import it without depending on
`ztypecheck`. Mirrors the `zast` / `zparser` split: data module
separate from producer.

This file lands as part of F5.E.1 — the minimal first step. It is
constructed by `TypeChecker.__init__` but otherwise unused. F5.E.2
will relocate the 19 nodeid-keyed component dicts off `zast.Program`
onto `Typing`. F5.E.3 relocates the older typecheck-output fields
(`mono_types`, `func_aliases`, `cloned_methods`, `resolved`,
`unit_types_by_id`, `symbol_table`, `is_error`) and freezes
`zast.Program`. F5.E.4 deletes the typed-tree mirror, after which
the component tables here are the typed program.
"""

from dataclasses import dataclass, field

import zast


@dataclass
class Typing:
    """Result of typechecking. See module docstring for context.

    Today (post F5.E.1) holds only the back-reference to the parsed
    program; subsequent F5.E sub-commits relocate component tables
    and aggregate state from `zast.Program` onto here.
    """

    parsed: zast.Program

    is_error: bool = field(default=False)
