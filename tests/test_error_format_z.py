"""Native: lock the rustc-style error formatter's FULL output (location +
source snippet + caret + note), which the corpus's norm_err deliberately strips.

Auto-marked `native` (filename ends `_z.py`); runs via `make test-native`.
"""

import os
import subprocess

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_ERRORS = os.path.join(_REPO, "tests", "fixtures", "errors")
_SYSTEM = os.path.join(_REPO, "lib", "system")

# The zc_binary fixture builds the port (~2.5min); override the 30s default.
pytestmark = [pytest.mark.timeout(300)]


def test_error_format_full(zc_binary):
    """The port renders an error rustc-style: `error[Exxxx]: msg`, a
    `--> file:line:col` line, a gutter source snippet with a `^` caret, and a
    `= note:`. Compared byte-exact to a frozen golden so the layout can't
    silently regress."""
    r = subprocess.run(
        [
            zc_binary,
            "use_after_move",
            "--src",
            _ERRORS,
            "--system",
            _SYSTEM,
            "--emit-c",
            os.devnull,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1, r.stderr
    with open(os.path.join(_ERRORS, "use_after_move.full"), encoding="utf-8") as f:
        golden = f.read()
    # structure sanity (helps when the golden is intentionally updated)
    for needle in (
        "error[E0200]:",
        "\n --> use_after_move.z:",
        "\n   5 |",
        "^",
        "= note:",
    ):
        assert needle in r.stderr, f"missing {needle!r} in:\n{r.stderr}"
    assert r.stderr == golden, f"--- got ---\n{r.stderr}\n--- want ---\n{golden}"


def test_error_format_did_you_mean(zc_binary):
    """An unknown call argument renders a `= hint: did you mean '<x>'?` line
    (Levenshtein suggestion over the callee's parameter names)."""
    r = subprocess.run(
        [
            zc_binary,
            "call_bad_arg",
            "--src",
            _ERRORS,
            "--system",
            _SYSTEM,
            "--emit-c",
            os.devnull,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1, r.stderr
    with open(os.path.join(_ERRORS, "call_bad_arg.full"), encoding="utf-8") as f:
        golden = f.read()
    assert "= hint: did you mean 'a'?" in r.stderr
    assert r.stderr == golden, f"--- got ---\n{r.stderr}\n--- want ---\n{golden}"
