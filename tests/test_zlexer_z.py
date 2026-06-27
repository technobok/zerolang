"""End-to-end tests for the self-hosting lexer (src/zlexer.z).

The whole-example token goldens live in tests/test_lexer_differential.py;
this file keeps the small hand-curated fixtures under tests/fixtures/zlexer_z/
that gate token coverage in isolation.

The `zlexer_binary` fixture lives in tests/conftest.py.
"""

import os
import subprocess

import pytest


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "zlexer_z")


pytestmark = pytest.mark.emitter


def _run_zlexer(binary: str, source_path: str) -> str:
    proc = subprocess.run([binary, source_path], capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"zlexer exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _assert_matches_golden(zlexer_binary: str, name: str) -> None:
    fixture = os.path.join(FIXTURE_DIR, f"{name}.z")
    golden = os.path.join(FIXTURE_DIR, f"{name}.tokens")
    actual = _run_zlexer(zlexer_binary, fixture)
    with open(golden, "r", encoding="utf-8") as f:
        expected = f.read()
    assert actual == expected, (
        f"zlexer output diverges from {name} golden\n--- expected ---\n{expected}"
        f"--- actual ---\n{actual}"
    )


def test_structural_fixture_matches_golden(zlexer_binary):
    """Bedrock scanner output must match the committed golden for a fixture
    exercising whitespace, comments, EOL, dot variants, and structural
    single-char delimiters."""
    _assert_matches_golden(zlexer_binary, "structural")


def test_words_fixture_matches_golden(zlexer_binary):
    """PR 4: identifiers (REFID), labels (LABEL, LABELPRE), all 28
    keywords, reserved words rejected as ERR, operators-as-REFIDs,
    and numeric-shaped REFIDs with dot continuation (e.g. `1.5` as
    one REFID rather than three tokens)."""
    _assert_matches_golden(zlexer_binary, "words")


def test_strings_fixture_matches_golden(zlexer_binary):
    """PR 5: interpreted string literals -- STRBEG, STRMID, STRCHR,
    STREND, STREXPRBEG. Named escapes (\\n \\t \\b \\\\ \\"), hex
    escapes (\\xHH, \\uHHHHHH at codepoints <= 0xFF), and `\\{...}`
    interpolation with the nested-brace state stack covered."""
    _assert_matches_golden(zlexer_binary, "strings")


def test_raws_fixture_matches_golden(zlexer_binary):
    """PR 6: raw triple-quoted string literals -- STRBEG / STRMID /
    STREND with arbitrary delim length (3 and 5), literal backslash
    (no escape processing), and multi-line content with EOL emitted
    between STRMID chunks. STRINGRAW carries the delim length as a
    u8 payload on the TokStateType variant arm."""
    _assert_matches_golden(zlexer_binary, "raws")
