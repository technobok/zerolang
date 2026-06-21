"""Pytest wrapper for the Python-free corpus gate (tests/run_corpus.sh).

The shell script is the canonical, Python-free runner (run + leak + error cases
against committed goldens). This wrapper lets `make`/CI drive it under pytest with
the session-built port `zc` (avoiding a second ~2.5min bootstrap). For granular,
xdist-parallel leak-only coverage use tests/test_emitc_leak_z.py; this is the
unified gate (behavioral .out goldens + leak + negative .err goldens).
"""

import os
import subprocess

import pytest

pytestmark = [pytest.mark.leak, pytest.mark.timeout(1800)]

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def test_corpus_gate(zc_binary):
    r = subprocess.run(
        ["bash", os.path.join(_REPO, "tests", "run_corpus.sh")],
        cwd=_REPO,
        env=dict(os.environ, ZC=zc_binary),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-4000:] + "\n--- stderr ---\n" + r.stderr[-2000:]
