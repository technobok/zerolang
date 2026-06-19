"""Smoke + differential tests for the self-hosted symbol table (src/zenv.z).

Mirrors tests/test_ztyping_z.py. The zenv.z binary is a smoke harness that
drives the symbol table through every cluster -- scopes, name/var resolution,
take/invalidation, locks, narrowing/exclusion, live-owned-var detection,
all-names, and the scope log -- dumping deterministically.

Two checks:

- ``test_zenv_smoke_matches_golden`` pins the whole binary output against the
  checked-in golden (byte-for-byte).
- ``test_battery_matches_python`` re-drives the same operation sequence through
  the Python reference (zenv.ZSymbolTable + ztypes + ztyping) and asserts the
  produced lines equal the golden. Type, variable and scope ids are remapped to
  creation order -- the .z harness mints them 0,1,2,... from fresh registries --
  so the differential compares the relationships the table records, not the
  arbitrary absolute ids. The full ``zc --dump-sql`` differential arrives once
  the type checker itself is ported.

The ``zenv_binary`` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest

import ztypes
import ztyping
import zenv


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zenv_z")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "smoke.expected")


def _bool(b):
    return str(bool(b)).lower()


def _python_lines():
    """Reproduce the golden by driving the same op sequence through the Python
    reference, remapping type/variable/scope ids to creation order."""
    result = ztypes.ZType(name="result", typetype=ztypes.ZTypeType.UNION)
    ok = ztypes.ZType(name="ok", typetype=ztypes.ZTypeType.RECORD)
    err = ztypes.ZType(name="err", typetype=ztypes.ZTypeType.RECORD)
    none = ztypes.ZType(name="none", typetype=ztypes.ZTypeType.NULL)
    buf = ztypes.ZType(name="buf", typetype=ztypes.ZTypeType.CLASS)
    i64 = ztypes.ZType(name="i64", typetype=ztypes.ZTypeType.RECORD)
    buf.destructor_name = "z_buf_destroy"
    # creation order: 0=result, 1=ok, 2=err, 3=none, 4=buf, 5=i64
    tid = {
        result.type_id: 0,
        ok.type_id: 1,
        err.type_id: 2,
        none.type_id: 3,
        buf.type_id: 4,
        i64.type_id: 5,
    }

    def tref(zt):
        return "none" if zt is None else str(tid[zt.type_id])

    ty = ztyping.ZTyping(parsed=None)
    ty.set_child(result, "ok", ok)
    ty.set_child(result, "err", err)
    ty.set_child(result, "none", none)

    st = zenv.ZSymbolTable(typing=ty)
    lines = []

    lines.append("=== scopes ===")
    sc0 = st.push("func")
    sid = {sc0.scope_id: 0}
    lines.append(f"push func -> scopeId={sid[sc0.scope_id]} depth={st.depth}")

    vx = ztypes.ZVariable(ztype=buf, ownership=ztypes.ZOwnership.OWNED)
    vy = ztypes.ZVariable(ztype=i64, ownership=ztypes.ZOwnership.OWNED)
    vid = {vx.variable_id: 0, vy.variable_id: 1}

    def vref(v):
        return "none" if v is None else str(vid[v.variable_id])

    st.define_var("x", vx)
    st.define_var("y", vy)
    st.define("MyType", result)

    lines.append("=== lookup ===")
    lines.append(f"lookup x -> {tref(st.lookup('x'))}")
    lines.append(f"lookup y -> {tref(st.lookup('y'))}")
    lines.append(f"lookup MyType -> {tref(st.lookup('MyType'))}")
    lines.append(f"lookup z -> {tref(st.lookup('z'))}")
    lines.append(f"varId x -> {vref(st.lookup_var('x'))}")
    lines.append(f"varId MyType -> {vref(st.lookup_var('MyType'))}")

    lines.append("=== live owned vars ===")
    live = st.get_live_owned_vars()
    lines.append(f"live has x -> {_bool('x' in live)}")
    lines.append(f"live has y -> {_bool('y' in live)}")

    lines.append("=== locks ===")
    holder_x = ztypes.ZLockHolder(kind=ztypes.ZLockHolderKind.VAR, id=vx.variable_id)
    r1 = st.try_lock(("x",), ztypes.ZLockState.EXCLUSIVE, holder_x)
    lines.append(f"lock x EXCLUSIVE -> {'ok' if not r1 else r1}")
    holder_c = ztypes.ZLockHolder(kind=ztypes.ZLockHolderKind.CALL, id=99)
    r2 = st.try_lock(("x",), ztypes.ZLockState.SHARED, holder_c)
    lines.append(f"lock x SHARED -> {'ok' if not r2 else r2}")
    lines.append(f"isPathLocked x -> {_bool(st.is_path_locked(('x',)) is not None)}")
    lines.append(f"isPathLocked y -> {_bool(st.is_path_locked(('y',)) is not None)}")
    fex = st.find_exclusive_lock(("x",))
    lines.append(
        f"exclusive holder of x -> {st.format_lock_holder(fex[1]) if fex else 'none'}"
    )
    st.release_held_locks(holder_x)
    lines.append(
        f"isPathLocked x after release -> {_bool(st.is_path_locked(('x',)) is not None)}"
    )
    fl = st.find_lock("x")
    lines.append(f"findLock x after release -> {fl.lock_type.name if fl else 'none'}")

    lines.append("=== invalidate ===")
    lines.append(f"invalidate y -> {_bool(st.invalidate('y', (12, 4, 0)))}")
    lines.append(f"lookup y -> {tref(st.lookup('y'))}")
    tl = st.get_taken_location("y")
    lines.append(f"taken y at line -> {tl[0] if tl else 'none'}")
    st.set_taken_location("y", (99, 1, 0))
    tl2 = st.get_taken_location("y")
    lines.append(f"taken y relocated -> {tl2[0] if tl2 else 'none'}")
    st.clear_taken("y")
    lines.append("after clearTaken:")
    lines.append(f"lookup y -> {tref(st.lookup('y'))}")

    lines.append("=== narrowing ===")
    vr = ztypes.ZVariable(ztype=result, ownership=ztypes.ZOwnership.OWNED)
    vid[vr.variable_id] = 2
    st.define_var("r", vr)
    st.narrow("r", result, subtype_name="ok", shadow=True)
    lines.append(f"lookup r -> {tref(st.lookup('r'))}")
    sn = st.get_subtype_name("r")
    lines.append(f"subtype r -> {sn if sn else ''}")
    st.reset_narrowing("r")
    sn2 = st.get_subtype_name("r")
    lines.append(f"after reset subtype r -> {sn2 if sn2 else ''}")
    lines.append(f"lookup r -> {tref(st.lookup('r'))}")

    lines.append("=== exclude ===")
    st.exclude("r", "ok", result)
    sn3 = st.get_subtype_name("r")
    lines.append(f"subtype r (excl ok) -> {sn3 if sn3 else ''}")
    lines.append(f"isExcluded r.ok -> {_bool(st.is_excluded('r', 'ok'))}")
    lines.append(f"isExcluded r.err -> {_bool(st.is_excluded('r', 'err'))}")
    st.exclude("r", "none", result)
    sn4 = st.get_subtype_name("r")
    lines.append(f"subtype r (excl ok,none) -> {sn4 if sn4 else ''}")
    lines.append(f"unreachable -> {_bool(st.is_unreachable())}")
    st.exclude("r", "err", result)
    lines.append(f"unreachable after excl all -> {_bool(st.is_unreachable())}")

    lines.append("=== all names ===")
    for nm in st.all_names():
        lines.append(f"  name {nm}")

    lines.append("=== scope log + pop ===")
    sc1 = st.push("inner")
    sid[sc1.scope_id] = 1
    lines.append(f"push inner -> scopeId={sid[sc1.scope_id]} depth={st.depth}")
    st.pop()
    lines.append(f"after pop depth={st.depth}")
    st.pop()
    lines.append(f"after pop depth={st.depth}")
    for row in st.scope_log:
        parent = "none" if row.parent_id is None else str(sid[row.parent_id])
        closed = "none" if row.closed_at_seq is None else str(row.closed_at_seq)
        lines.append(
            f"  log scope={sid[row.scope_id]} parent={parent} kind={row.kind.name} "
            f"name={row.name} opened={row.opened_at_seq} closed={closed}"
        )

    return lines


def _read_golden():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.emitter
def test_zenv_smoke_matches_golden(zenv_binary):
    """The smoke binary's stdout must match the checked-in golden."""
    proc = subprocess.run([zenv_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"zenv exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "zenv smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.emitter
@pytest.mark.timeout(240)
def test_zenv_selfhost_matches_golden(zenv_selfhost_binary):
    """zenv built by the PORTED zc (stage1) must produce the same golden smoke
    dump as the reference -- the self-host gate for the symbol-table unit."""
    proc = subprocess.run([zenv_selfhost_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host zenv exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "self-host zenv smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


def test_battery_matches_python():
    """The Python reference must reproduce the golden exactly -- the slice's
    Python-vs-port differential. Guards both the golden (against accidental
    edits) and the Python reference (against drift from the ported table)."""
    golden = _read_golden()
    assert golden.rstrip("\n").split("\n") == _python_lines()
