"""Memory-leak gate (pytest wrapper) for the ported C emitter (src/zemitterc.z).

Each buildable example + corpus program is emitted by the ported zc, built with
AddressSanitizer, and run under detect_leaks=1: it must leak 0 bytes and be
memory-safe (no use-after-free / double-free). KNOWN_LEAKY tracks the shrinking
set still leaking (xfail strict -- a fixed one XPASSes and fails the suite,
forcing its removal: the ratchet). Build/emit failures are skipped (port
feature-gaps are the behavioral gate's concern, not the leak gate's).

The canonical Python-free runner is tests/leakcheck.sh; this wrapper exists for
`make test-leak` / xdist convenience. Slow (one ASan binary per program) -- it is
deliberately NOT part of `make test`.
"""

import glob
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from probe_emitc import emit_z  # noqa: E402  port-emit only (no ASan; rlimit is fine)

pytestmark = [pytest.mark.leak, pytest.mark.timeout(300)]

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_EXAMPLES = os.path.join(_REPO, "examples")
_CORPUS = os.path.join(_REPO, "tests", "fixtures", "emitc_corpus")
# The AddressSanitizer build flags (test_fixedpoint.py's golden ASan set). The
# ASan compile+run is done WITHOUT probe_emitc._run's RLIMIT_AS -- ASan reserves a
# huge virtual space and crashes under an address-space rlimit.
_ASAN_CFLAGS = ["-fsanitize=address", "-g", "-O0", "-std=c17", "-w"]

# Shrinking work queue -- keep in sync with KNOWN_LEAKY in tests/leakcheck.sh.
KNOWN_LEAKY = {
    "maps",
    "class_map_field_methods",
}


def _units(d):
    return sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(d, "*.z"))
    )


def _params(units, src):
    out = []
    for u in units:
        marks = (
            [pytest.mark.xfail(strict=True, reason="known leak (KNOWN_LEAKY)")]
            if u in KNOWN_LEAKY
            else []
        )
        out.append(pytest.param(u, src, marks=marks, id=u))
    return out


_PARAMS = _params(_units(_EXAMPLES), _EXAMPLES) + _params(_units(_CORPUS), _CORPUS)

_LEAK_RE = re.compile(r"SUMMARY: AddressSanitizer: (\d+) byte\(s\) leaked")
_UNSAFE_RE = re.compile(
    r"ERROR: AddressSanitizer: "
    r"(heap-use-after-free|attempting double-free|heap-buffer-overflow|stack-)"
)


def _leaked_bytes(stderr):
    m = _LEAK_RE.search(stderr)
    return int(m.group(1)) if m else 0


@pytest.mark.parametrize("unit,src", _PARAMS)
def test_leakfree(unit, src, tmp_path, zc_binary):
    c = str(tmp_path / f"{unit}.c")
    r = emit_z(zc_binary, unit, c, src)
    if r.returncode != 0:
        pytest.skip(f"port emit failed (feature gap): {unit}")
    b = str(tmp_path / unit)
    cc = subprocess.run(
        ["gcc", *_ASAN_CFLAGS, "-o", b, c], capture_output=True, text=True
    )
    if cc.returncode != 0:
        pytest.skip(f"ASan build failed (feature gap): {unit}")
    rd = tmp_path / "run"
    rd.mkdir()
    run = subprocess.run(
        [b],
        capture_output=True,
        text=True,
        cwd=str(rd),
        env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1"),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert not _UNSAFE_RE.search(run.stderr), (
        f"{unit} memory-unsafe:\n{run.stderr[-3000:]}"
    )
    leaked = _leaked_bytes(run.stderr)
    assert leaked == 0, f"{unit} leaked {leaked} bytes:\n{run.stderr[-2000:]}"
