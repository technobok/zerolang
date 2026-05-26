"""Exhaustiveness lint for the two NodeType cascades in src/zast.py.

`_clone_node` and `node_children` each switch over `node.nodetype`. Forgetting
an arm is detectable only via the AssertionError fallback at runtime — these
tests escalate that to a `make test-fast` failure naming the missing arm.

When `NodeType` is ported to a zerolang variant and dispatch becomes a `case`
block with compile-time exhaustiveness, these tests can retire.
"""

import inspect

from zast import NodeType, _clone_node, node_children


def test_clone_node_covers_all_nodetypes():
    src = inspect.getsource(_clone_node)
    missing = [nt.name for nt in NodeType if f"NodeType.{nt.name}" not in src]
    assert not missing, f"_clone_node missing arms for: {missing}"


def test_node_children_covers_all_nodetypes():
    src = inspect.getsource(node_children)
    missing = [nt.name for nt in NodeType if f"NodeType.{nt.name}" not in src]
    assert not missing, f"node_children missing arms for: {missing}"
