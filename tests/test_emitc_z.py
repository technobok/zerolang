"""Scaffold test for the self-hosted C emitter (src/zemitterc.z).

``test_emitc_scaffold_compiles`` emits the skeleton's C for ``hello`` via the
ported ``zc --emit-c`` and asserts it compiles standalone under the golden cc
flags. The ``zc_binary`` fixture (tests/conftest.py) builds src/zc.z once per
session and skips cleanly without a C compiler.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from probe_emitc import build, emit_z  # noqa: E402

# Building zc.z compiles the entire ported pipeline as one unit -- the
# reference compiler takes ~30s on it, over the default per-test timeout.
pytestmark = [pytest.mark.infra, pytest.mark.timeout(240)]


def test_emitc_scaffold_compiles(tmp_path, zc_binary):
    """C0 baseline: the skeleton's C compiles standalone under golden flags."""
    z_c = str(tmp_path / "z.c")
    zp = emit_z(zc_binary, "hello", z_c)
    assert zp.returncode == 0, zp.stderr
    zb = build(z_c, str(tmp_path / "z.bin"))
    assert zb.returncode == 0, zb.stderr
