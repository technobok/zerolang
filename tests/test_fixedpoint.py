"""Self-hosting fixpoint: the integrated compiler reproduces itself.

- stage1 = ``src/zc.z`` compiled by the reference (``zc.py``) into the ported
  ``zc`` binary (the ``zc_binary`` fixture).
- stage2 = ``src/zc.z`` compiled by stage1 (the ``zc_stage2_binary`` fixture).

Two checks:

- ``test_stage2_byte_identical_to_stage1`` -- the ``zc.c`` emitted by stage1 is
  byte-for-byte identical to the ``zc.c`` emitted by stage2. Both are the SAME
  ported compiler over the same inputs, so type-id allocation is identical -- a
  true fixpoint (unlike port-vs-reference, where ids differ by construction; see
  tests/test_emitc_z.py).
- ``test_stage2_compiles_<unit>_to_golden`` -- stage2 compiles ztypes / ztyping
  / zenv to C whose built binaries reproduce the reference smoke goldens, so the
  fixpoint is the CORRECT compiler, not a fixpoint of a broken one.
"""

import os
import subprocess

import pytest

from _xdist_build import build_once_shared

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
_SYSTEM_DIR = os.path.join(_REPO_ROOT, "lib", "system")
_EXAMPLES_DIR = os.path.join(_REPO_ROOT, "examples")
_CC = os.environ.get("Z_TEST_CC", "gcc")
_CFLAGS = [
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Wno-unused-function",
    "-Wno-unused-parameter",
    "-Werror=implicit-function-declaration",
    "-Werror=implicit-int",
    "-Werror=int-conversion",
    "-Werror=incompatible-pointer-types",
]


def _emit_c(binary, unitname, out_path):
    """Emit unitname's C with the given ported-zc binary; return the bytes."""
    proc = subprocess.run(
        [
            binary,
            unitname,
            "--src",
            _SRC_DIR,
            "--system",
            _SYSTEM_DIR,
            "--emit-c",
            out_path,
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, (
        f"emit {unitname} failed (rc={proc.returncode}):\n{proc.stderr}"
    )
    with open(out_path, "rb") as f:
        return f.read()


@pytest.mark.emitter
@pytest.mark.runtime
@pytest.mark.timeout(300)
def test_stage2_byte_identical_to_stage1(zc_binary, zc_stage2_binary, tmp_path):
    """emit(zc, stage1) == emit(zc, stage2): the bootstrap fixpoint."""
    c1 = _emit_c(zc_binary, "zc", str(tmp_path / "zc_stage1.c"))
    c2 = _emit_c(zc_stage2_binary, "zc", str(tmp_path / "zc_stage2.c"))
    assert c1 == c2, (
        "stage1 and stage2 emit different zc.c -- not a fixpoint "
        f"(len {len(c1)} vs {len(c2)})"
    )


@pytest.mark.emitter
@pytest.mark.runtime
@pytest.mark.timeout(300)
@pytest.mark.parametrize("unit", ["ztypes", "ztyping", "zenv"])
def test_stage2_compiles_unit_to_golden(zc_stage2_binary, tmp_path, unit):
    """stage2 is a CORRECT compiler: it compiles each locked-in unit to a
    binary whose smoke output matches the reference golden."""
    c_path = str(tmp_path / f"{unit}.c")
    _emit_c(zc_stage2_binary, unit, c_path)
    bin_path = str(tmp_path / unit)
    cc = subprocess.run(
        [_CC, *_CFLAGS, "-o", bin_path, c_path], capture_output=True, text=True
    )
    assert cc.returncode == 0, f"gcc {unit} (stage2) failed:\n{cc.stderr}"
    run = subprocess.run([bin_path], capture_output=True, text=True)
    assert run.returncode == 0, (
        f"stage2 {unit} binary exited {run.returncode}:\n{run.stderr}"
    )
    golden_path = os.path.join(
        _REPO_ROOT, "tests", "fixtures", f"{unit}_z", "smoke.expected"
    )
    with open(golden_path, "r", encoding="utf-8") as f:
        golden = f.read()
    assert run.stdout == golden, (
        f"stage2-compiled {unit} smoke output diverged from golden"
    )


@pytest.fixture(scope="session")
def zc_stage2_asan_binary(zc_binary, tmp_path_factory):
    """stage2 (zc emitted by stage1) built with AddressSanitizer.

    Used by the self-host memory-safety gate: a per-return cleanup that frees
    a value the return still reads is a use-after-free which the byte-identity
    fixpoint can miss by luck (freed memory not yet reused), but ASan catches.
    """

    def _do(d):
        c_path = str(d / "zc_stage2.c")
        _emit_c(zc_binary, "zc", c_path)
        bin_path = str(d / "zc_stage2_asan")
        cc = subprocess.run(
            [
                _CC,
                "-fsanitize=address",
                "-g",
                "-O0",
                "-std=c17",
                "-w",
                "-o",
                bin_path,
                c_path,
            ],
            capture_output=True,
            text=True,
        )
        assert cc.returncode == 0, f"ASan build of stage2 failed:\n{cc.stderr}"
        return bin_path

    return build_once_shared("zc-stage2-asan", tmp_path_factory, _do)


# The locked-in src units plus the example constructs that exposed the self-host
# emitter ownership UAFs / leaks (generators, facets, protocols, iterator,
# autoproject, atomic_call_temps). The whole-corpus guarantee lives in the
# ``make selfhost-asan`` shell gate (tests/selfhost_asan.sh); this is the fast
# in-suite regression net for those specific bug classes.
_SELFHOST_ASAN_UNITS = [
    pytest.param("ztypes", _SRC_DIR, id="ztypes"),
    pytest.param("ztyping", _SRC_DIR, id="ztyping"),
    pytest.param("zenv", _SRC_DIR, id="zenv"),
    pytest.param("generator_chain", _EXAMPLES_DIR, id="generator_chain"),
    pytest.param("facets", _EXAMPLES_DIR, id="facets"),
    pytest.param("protocols", _EXAMPLES_DIR, id="protocols"),
    pytest.param("iterator", _EXAMPLES_DIR, id="iterator"),
    pytest.param("autoproject", _EXAMPLES_DIR, id="autoproject"),
    pytest.param("atomic_call_temps", _EXAMPLES_DIR, id="atomic_call_temps"),
]


@pytest.mark.emitter
@pytest.mark.runtime
@pytest.mark.timeout(300)
@pytest.mark.parametrize("mode", ["emit-c", "dump-sql"])
@pytest.mark.parametrize("unit,srcdir", _SELFHOST_ASAN_UNITS)
def test_stage2_selfhost_asan_clean(
    zc_stage2_asan_binary, tmp_path, unit, srcdir, mode
):
    """stage2 self-emits each unit, in both emit modes, with no leaks and no
    use-after-free / double-free.

    Leak detection is enabled (``detect_leaks=1``): the self-emit is leak-free,
    so this gates both leak-freedom and memory SAFETY. ASan aborts with a
    non-zero exit on any leak / heap-use-after-free / double-free / overflow.
    """
    if mode == "emit-c":
        mode_args = ["--emit-c", str(tmp_path / f"{unit}.c")]
    else:
        mode_args = ["--full", "--dump-sql", "-"]
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1")
    proc = subprocess.run(
        [
            zc_stage2_asan_binary,
            unit,
            "--src",
            srcdir,
            "--system",
            _SYSTEM_DIR,
            *mode_args,
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
    )
    assert proc.returncode == 0, (
        f"stage2 self-emitting {unit} ({mode}) under ASan is not memory-safe "
        f"(rc={proc.returncode}):\n{proc.stderr[-3000:]}"
    )
