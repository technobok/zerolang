"""Smoke + differential tests for the self-hosted ZTyping container
(src/ztyping.z).

Mirrors tests/test_ztypes_z.py. The ztyping.z binary is a smoke harness that
exercises the children / sidecar / generic-arg tables, a representative set of
per-node stamps, the flattened node tables, and the aggregate state, dumping
deterministically.

Two checks:

- ``test_ztyping_smoke_matches_golden`` pins the whole binary output against the
  checked-in golden (byte-for-byte).
- ``test_battery_matches_python`` re-drives the same operation sequence through
  the Python reference (ztyping.ZTyping + ztypes.ZType) and asserts the produced
  lines equal the golden. Type and conformance ids are remapped to creation
  order -- the .z harness mints them 0,1,2,3 from a fresh registry -- so the
  differential compares the relationships the container records, not the
  arbitrary absolute ids. The full ``zc --dump-sql`` typing differential arrives
  once the type checker itself is ported.

The ``ztyping_binary`` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest

import ztypes
import ztyping
from zast import CallKind


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "ztyping_z")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "smoke.expected")


def _bool(b):
    return str(bool(b)).lower()


def _own(o):
    return "none" if o is None else o.name


def _python_lines():
    """Reproduce the golden by driving the same op sequence through the Python
    reference, remapping type/conformance ids to creation order."""
    point = ztypes.ZType(name="point", typetype=ztypes.ZTypeType.RECORD)
    i64 = ztypes.ZType(name="i64", typetype=ztypes.ZTypeType.RECORD)
    strt = ztypes.ZType(name="String", typetype=ztypes.ZTypeType.CLASS)
    listt = ztypes.ZType(name="List", typetype=ztypes.ZTypeType.CLASS)
    # creation order: 0=point, 1=i64, 2=String, 3=List (matches the .z registry)
    tid = {point.type_id: 0, i64.type_id: 1, strt.type_id: 2, listt.type_id: 3}

    def tref(zt):
        return "none" if zt is None else str(tid[zt.type_id])

    t = ztyping.ZTyping(parsed=None)
    lines = []

    lines.append("=== children ===")
    t.set_child(point, "x", i64)
    t.set_child(point, "y", i64)
    t.set_child(point, "label", strt)
    t.set_child(point, "x", i64)  # idempotent re-set
    lines.append(f"childCount point -> {t.child_count(point)}")
    lines.append(f"hasChild point.x -> {_bool(t.has_child(point, 'x'))}")
    lines.append(f"hasChild point.z -> {_bool(t.has_child(point, 'z'))}")
    lines.append(f"childOf point.x -> {tref(t.child_of(point, 'x'))}")
    lines.append(f"childOf point.label -> {tref(t.child_of(point, 'label'))}")
    lines.append(f"childOf point.z -> {tref(t.child_of(point, 'z'))}")
    for i, (cname, ctype) in enumerate(t.children_of(point)):
        lines.append(f"  child {i} {cname} -> {tref(ctype)}")

    lines.append("=== sidecars ===")
    t.set_child_private(point, "x")
    t.set_child_lock_field(point, "y")
    t.set_child_lock_arm(point, "label")
    t.set_default_numeric(point, "x", 7)
    t.set_default_function(point, "y", "makeY")
    t.set_default_variant_arm(point, "label", "none")
    t.set_child_ownership(point, "x", ztypes.ZParamOwnership.TAKE)
    t.set_child_ownership(point, "y", ztypes.ZParamOwnership.BORROW)
    lines.append(f"isPrivate point.x -> {_bool(t.is_child_private(point, 'x'))}")
    lines.append(f"isPrivate point.y -> {_bool(t.is_child_private(point, 'y'))}")
    lines.append(f"isLockField point.y -> {_bool(t.is_child_lock_field(point, 'y'))}")
    lines.append(f"hasAnyLockField point -> {_bool(t.has_any_lock_field(point))}")
    lines.append(
        f"isLockArm point.label -> {_bool(t.is_child_lock_arm(point, 'label'))}"
    )
    lines.append(f"default point.x -> {t.child_default(point, 'x')}")
    lines.append(f"default point.y -> {t.child_default(point, 'y')}")
    lines.append(f"default point.label -> {t.child_default(point, 'label')}")
    lines.append(f"ownership point.x -> {_own(t.child_ownership(point, 'x'))}")
    lines.append(f"ownership point.y -> {_own(t.child_ownership(point, 'y'))}")
    lines.append(f"ownership point.label -> {_own(t.child_ownership(point, 'label'))}")

    lines.append("=== generic args ===")
    t.set_generic_arg(listt, "of", i64)
    t.set_generic_arg(listt, "of", strt)  # idempotent update
    lines.append(f"genericArg List.of -> {tref(t.generic_arg_of(listt, 'of'))}")
    lines.append(f"hasGenericArgs List -> {_bool(t.has_generic_args(listt))}")
    lines.append(f"hasGenericArgs point -> {_bool(t.has_generic_args(point))}")

    lines.append("=== node stamps ===")
    t.node_type[100] = strt
    lines.append(f"nodeType[100] -> {tref(t.node_type[100])}")
    t.call_kind[101] = CallKind.RECORD_CREATE
    lines.append(f"callKind[101] -> {t.call_kind[101].name}")
    t.with_ownership[102] = ztypes.ZOwnership.BORROWED
    lines.append(f"withOwnership[102] -> {t.with_ownership[102].name}")
    t.atom_variable_id[103] = 42
    lines.append(f"atomVariableId[103] -> {t.atom_variable_id[103]}")
    t.node_literal_base[104] = "nondec"
    lines.append(f"nodeLiteralBase[104] -> {t.node_literal_base[104]}")

    lines.append("=== node tables ===")
    t.node_const_value[200] = 1024
    lines.append(f"  const node=200 kind=IVAL ival={t.node_const_value[200]}")
    t.if_taken_vars[300] = [("tmp", strt), ("scratch", None)]
    for nm, zt in t.if_taken_vars[300]:
        lines.append(f"  ifTaken node=300 name={nm} type={tref(zt)}")
    t.case_subject_taken_arms[400] = {"ok", "err"}
    lines.append(f"  takenArms count={len(t.case_subject_taken_arms[400])}")

    lines.append("=== aggregates ===")
    cf = ztypes.ZConformance(
        impl_type_id=point.type_id,
        spec_type_id=listt.type_id,
        label="drawable",
        is_facet=False,
    )
    t.conformance.append(cf)
    # one conformance -> creation-order id 0
    lines.append(
        f"  conf id=0 impl={tid[cf.impl_type_id]} spec={tid[cf.spec_type_id]} "
        f"label={cf.label}"
    )
    t.resolved["test.point"] = point
    lines.append(f"resolved[test.point] -> {tref(t.resolved['test.point'])}")
    t.mono_types.append((listt, None))
    lines.append(f"monoTypes count -> {len(t.mono_types)}")
    lines.append(f"isError -> {_bool(t.is_error)}")

    return lines


def _read_golden():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.emitter
def test_ztyping_smoke_matches_golden(ztyping_binary):
    """The smoke binary's stdout must match the checked-in golden."""
    proc = subprocess.run([ztyping_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"ztyping exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "ztyping smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


def test_battery_matches_python():
    """The Python reference must reproduce the golden exactly -- the slice's
    Python-vs-port differential. Guards both the golden (against accidental
    edits) and the Python reference (against drift from the ported container)."""
    golden = _read_golden()
    assert golden.rstrip("\n").split("\n") == _python_lines()
