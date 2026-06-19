"""Smoke + differential tests for the self-hosted type model (src/ztypes.z).

This is the head of the typechecker port slice. The ztypes.z binary is a
smoke harness: it dumps every enum arm, a representative of each carrier,
and a battery of the pure numeric-literal / name-mangling helpers.

Two checks:

- ``test_ztypes_smoke_matches_golden`` pins the whole binary output against
  the checked-in golden (byte-for-byte), like tests/test_zast_z.py.
- ``test_pure_fn_battery_matches_python`` is the genuine differential: it
  drives the same inputs through the Python reference (src/ztypes.py) and
  asserts the produced lines equal the golden's pure-function sections. The
  enum/carrier dumps have no Python analogue and are pinned by the golden
  alone; the full ``zc --dump-sql`` typing differential arrives once the
  type checker itself is ported.

The ``ztypes_binary`` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest

import ztypes as z


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "ztypes_z")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "smoke.expected")

# The pure-function battery, kept in lock-step with main() in src/ztypes.z.
_NUMFORM_INPUTS = [
    "0",
    "42",
    "0xff",
    "0b1010",
    "0o17",
    "100u8",
    "42i128",
    "3.14",
    "1e3",
    "1.5f32",
]
_MANGLE_VAR_INPUTS = ["foo", "int", "for", "main", "exit"]
_MANGLE_FUNC_INPUTS = ["foo", "main", "math.add", "a.b.c"]
_INT_FITS_INPUTS = [
    (0, "f32"),
    (16777216, "f32"),
    (16777217, "f32"),
    (9007199254740992, "f64"),
    (9007199254740993, "f64"),
    (5, "i64"),
]
_FLOAT_FITS_INPUTS = [
    ("0.5", 0.5, "f32"),
    ("0.1", 0.1, "f32"),
    ("1.0", 1.0, "f64"),
    ("1.0", 1.0, "f128"),
    ("2.5", 2.5, "f32"),
]
_PARSE_INPUTS = [
    "0",
    "42",
    "255",
    "256",
    "0xff",
    "0b1010",
    "0o17",
    "100u8",
    "256u8",
    "200i8",
    "42i128",
    "0cA",
    "0cAB",
    "1.5",
    "1e3",
    "0xZZ",
]
_LITVAL_INPUTS = ["42", "256u8", "1.5", "1e3", "0xZZ"]


def _bool(b):
    return str(bool(b)).lower()


def _python_pure_fn_lines():
    """Reproduce the pure-function sections of the golden via the Python
    reference, in the same order and format as src/ztypes.z main()."""
    lines = []

    lines.append("=== numericLiteralForm ===")
    for s in _NUMFORM_INPUTS:
        has_suffix, base = z.numeric_literal_form(s)
        lines.append(f"numform {s} -> suffix={_bool(has_suffix)} base={base}")

    lines.append("=== mangleVar ===")
    for n in _MANGLE_VAR_INPUTS:
        lines.append(f"mangleVar {n} -> {z.mangle_var_name(n)}")

    lines.append("=== mangleFunc ===")
    for n in _MANGLE_FUNC_INPUTS:
        lines.append(f"mangleFunc {n} -> {z.mangle_func_name(n)}")

    lines.append("=== intFitsFloat ===")
    for value, kind in _INT_FITS_INPUTS:
        lines.append(
            f"intFits {value} {kind} -> {_bool(z.int_fits_float(value, kind))}"
        )

    lines.append("=== floatFitsFloat ===")
    for label, value, kind in _FLOAT_FITS_INPUTS:
        lines.append(
            f"floatFits {label} {kind} -> {_bool(z.float_fits_float(value, kind))}"
        )

    lines.append("=== parseNumber ===")
    for s in _PARSE_INPUTS:
        typename, value, err = z.parse_number(s)
        is_float = typename[0] == "f"
        ival = 0 if (is_float or type(value) is float) else int(value)
        err_text = err if err is not None else ""
        lines.append(
            f"parse {s} -> kind={typename} float={_bool(is_float)} ival={ival} err={err_text}"
        )

    lines.append("=== parseLiteralValue ===")
    for s in _LITVAL_INPUTS:
        value = z.parse_literal_value(s)
        ok = value is not None
        is_float = ok and type(value) is float
        ival = 0 if (value is None or is_float) else int(value)
        lines.append(
            f"litval {s} -> ok={_bool(ok)} float={_bool(is_float)} ival={ival}"
        )

    return lines


def _read_golden():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.emitter
def test_ztypes_smoke_matches_golden(ztypes_binary):
    """The smoke binary's stdout must match the checked-in golden."""
    proc = subprocess.run([ztypes_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"ztypes exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "ztypes smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


@pytest.mark.emitter
@pytest.mark.timeout(240)
def test_ztypes_selfhost_matches_golden(ztypes_selfhost_binary):
    """ztypes built by the PORTED zc (stage1) must produce the same golden
    smoke dump as the reference -- the self-host gate for the type-model unit."""
    proc = subprocess.run([ztypes_selfhost_binary], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            f"self-host ztypes exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    expected = _read_golden()
    if proc.stdout != expected:
        pytest.fail(
            "self-host ztypes smoke output diverged from golden.\n"
            f"--- expected ---\n{expected}--- actual ---\n{proc.stdout}"
        )


def test_pure_fn_battery_matches_python():
    """The Python reference must reproduce the golden's pure-function
    sections exactly -- the slice's Python-vs-port differential. Guards both
    the golden (against accidental edits) and the Python reference (against
    drift from the ported implementation)."""
    golden = _read_golden()
    marker = "=== numericLiteralForm ==="
    assert marker in golden, "golden is missing the pure-function battery"
    golden_block = golden[golden.index(marker) :].rstrip("\n").split("\n")
    assert golden_block == _python_pure_fn_lines()
