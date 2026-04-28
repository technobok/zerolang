"""
Synthesis primitives for compiler passes that build AST or symtab
state beyond what was parsed from source.

Today's consumer is the ANF lowering pass (`AnfRewriter`, lands with
its first user in a follow-up commit). The same primitives are
sized to extend cleanly to future passes (e.g. SSA phi insertion,
generator-style class synthesis). New primitives land here only
when a pass needs them — no speculative API.

Every node or variable produced through this module carries a
`synth_origin` tag (e.g. `"anf"`) so SQL dumps and diagnostics can
distinguish compiler-synthesised state from user source.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import zast
from zast import (
    Assignment,
    AtomId,
    Expression,
    NodeType,
    Operation,
    StatementLine,
)
from zlexer import Token
from ztypes import ZNaming, ZOwnership, ZType, ZVariable


class FreshNamer:
    """Counter-driven name allocator scoped to a single TypeChecker run.

    Each instance hands out monotonically increasing names with a
    chosen prefix. Names are deterministic given the order of `next()`
    calls; that's what we want for reproducible AST shapes between
    runs.
    """

    def __init__(self, prefix: str = "__t") -> None:
        self._prefix = prefix
        self._counter = 0

    def next(self) -> str:
        name = f"{self._prefix}{self._counter}"
        self._counter += 1
        return name


def make_atom_id(name: str, src_loc: Token, origin: str = "anf") -> AtomId:
    """Synthesise an AtomId referring to `name`, located at `src_loc`.

    The token is reused as the node's `start` so error messages still
    point at the original user source. `synth_origin` marks the node
    as compiler-generated.
    """
    node = AtomId(name=name, start=src_loc)
    node.synth_origin = origin
    return node


def make_assignment(
    name: str,
    value_op: Operation,
    src_loc: Token,
    origin: str = "anf",
) -> StatementLine:
    """Synthesise a `name: <value_op>` binding wrapped in a StatementLine.

    The `value_op` is wrapped in an `Expression` (the standard shape
    Assignment.value expects). All synthetic nodes carry
    `synth_origin = origin`; the inner `value_op`'s own synth_origin
    is left untouched so callers can pass either an original
    parsed expression or another synthetic one.
    """
    expr = Expression(expression=value_op, start=src_loc)
    expr.synth_origin = origin
    assn = Assignment(name=name, value=expr, start=src_loc)
    assn.synth_origin = origin
    line = StatementLine(statementline=assn, start=src_loc)
    line.synth_origin = origin
    return line


@dataclass
class Rewriter:
    """Bottom-up node-replacing visitor with a per-statement preamble.

    Subclasses override `visit_<NodeType.name>` (matching the
    `NodeType` enum value) to rewrite specific node kinds. The
    default `visit` recurses into children and returns the node
    unchanged when no handler matches.

    The `preamble` list collects synthetic statements that the
    driver inserts before the current statement. Subclasses call
    `emit_preamble(stmt_line)` to enqueue.

    SSA phi-insertion and generator-style class synthesis are
    expected to subclass `Rewriter` rather than reimplement a
    walker.
    """

    preamble: List[StatementLine] = field(default_factory=list)
    # Subclasses populate `handlers` (typically in `__post_init__` or
    # `__init__`) with `{NodeType.X: self.visit_x}` mappings. The base
    # `visit` method dispatches via this dict — no `getattr`-by-name.
    handlers: "Dict[NodeType, Callable[[zast.Node], Optional[zast.Node]]]" = field(
        default_factory=dict
    )

    def emit_preamble(self, line: StatementLine) -> None:
        self.preamble.append(line)

    def take_preamble(self) -> List[StatementLine]:
        """Detach and clear the preamble buffer; caller inserts it."""
        out, self.preamble = self.preamble, []
        return out

    def visit(self, node: Optional[zast.Node]) -> Optional[zast.Node]:
        """Dispatch to the `handlers[node.nodetype]` callable if
        registered; otherwise return the node unchanged."""
        if node is None:
            return None
        handler = self.handlers.get(node.nodetype)
        if handler is not None:
            return handler(node)
        return node


def register_synth_var(
    ztype: ZType,
    ownership: ZOwnership,
    *,
    is_private_access: bool = False,
    borrow_origin: Optional[str] = None,
    origin: str = "anf",
) -> ZVariable:
    """Construct a synthesised ZVariable with the provenance tag set.

    Caller is responsible for inserting the variable into the
    appropriate scope via the symbol table's existing API; this
    helper only builds the record. The split mirrors how parsed
    variables are constructed in `_check_assignment`: ownership
    is computed from the assignment's RHS, not stamped here
    speculatively.
    """
    return ZVariable(
        ztype=ztype,
        ownership=ownership,
        named=ZNaming.NAMED,
        is_private_access=is_private_access,
        borrow_origin=borrow_origin,
        synth_origin=origin,
    )
