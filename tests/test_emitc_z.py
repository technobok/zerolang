"""Differential test for the self-hosted C emitter (src/zemitterc.z).

Each admitted example compiles through both pipelines (zc.py reference,
zc --emit-c ported) under the golden cc flags; the two binaries must
produce identical stdout and exit codes. Type ids differ between the two
typecheckers by construction, so the C text is never byte-compared.

``EMITC_SMOKE`` grows per Phase C slice, mirroring the dumpsql suite's
admission ramp. The ``zc_binary`` fixture (tests/conftest.py) builds
src/zc.z once per session and skips cleanly without a C compiler.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from probe_emitc import build, emit_ref, emit_z, run_binary  # noqa: E402

# Building zc.z compiles the entire ported pipeline as one unit -- the
# reference compiler takes ~30s on it, over the default per-test timeout.
pytestmark = [pytest.mark.infra, pytest.mark.timeout(240)]

# Examples whose ported emission fully matches the reference's runtime
# behavior (build parity + stdout + exit code). Admitted per slice.
EMITC_SMOKE: "list[str]" = [
    "hello",
    "factorial",
    "fibonacci",
    "arbprec_constants",
    "chained_method_calls",
    "panic",
    "constfold",
    "control",
    "strings",
    "string_ordering",
    "string_transform",
    "vector",
    "records",
    "narrowing",
    "variants",
    "equality",
    "case",
    "ifexpr",
    "typedefs",
    "swap",
    "data",
    "typed_data",
    "lists",
    "listview",
    "string_codepoints",
    "string_query",
    "string_slice",
    "string_parse",
    "set_uniq",
    "maps",
    "mapitems",
    "constructors",
    "classes",
    "forloop",
    "listiter",
    "class_text_protocol",
    "text_protocol",
    "protocols",
    "owned_protocol",
    "iterator",
    "io_read_text",
    "io_write_text",
    "io_open",
    "io_readwrite",
    "io_seek",
    "io_protocol_closer",
    "io_protocol_rw",
    "io_stdstreams",
    "io_fs_ops",
    "io_list_dir",
    "io_stat_mkdirp",
    "io_lstat",
    "io_buffered",
    "io_textwriter",
    "io_textreader",
    "cli_basic",
    "generator_counter",
    "generator_intrange",
    "generator_chain",
    "generator_map_filter",
    "generator_listiter",
    "generator_bidirectional",
    "generator_accepts_borrow",
    "os_basics",
    "os_env",
    "os_platform",
    "os_process",
]

# Corpus of targeted programs (tests/fixtures/emitc_corpus/), each exercising one
# emitter gap the compiler's own source needs but the examples never hit. Same
# differential as EMITC_SMOKE, but built with --src pointed at the corpus dir so
# adding a program is emitter-differential-only (no lexer/parser/typecheck
# goldens). Grows per self-host slice.
CORPUS_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "emitc_corpus")
EMITC_CORPUS: "list[str]" = [
    "reftype_param",
    "string_mut",
    "empty_list",
    "nested_list_method",
    "optionval_return",
    "take_string",
    "string_param_mut",
    "panic_stmt",
    "union_basic",
    "protocol_create_take",
    "protocol_union_method",
    "union_take_arg",
    "provider_ctor",
    "variant_payload_order",
    "protocol_field_order",
]


def test_emitc_scaffold_compiles(tmp_path, zc_binary):
    """C0 baseline: the skeleton's C compiles standalone under golden flags."""
    z_c = str(tmp_path / "z.c")
    zp = emit_z(zc_binary, "hello", z_c)
    assert zp.returncode == 0, zp.stderr
    zb = build(z_c, str(tmp_path / "z.bin"))
    assert zb.returncode == 0, zb.stderr


@pytest.mark.parametrize("unit", EMITC_SMOKE)
def test_emitc_matches_reference(unit, tmp_path, zc_binary):
    ref_c = str(tmp_path / "ref.c")
    z_c = str(tmp_path / "z.c")
    rp = emit_ref(unit, ref_c)
    assert rp.returncode == 0, rp.stderr
    zp = emit_z(zc_binary, unit, z_c)
    assert zp.returncode == 0, zp.stderr
    rb = build(ref_c, str(tmp_path / "ref.bin"))
    assert rb.returncode == 0, rb.stderr
    zb = build(z_c, str(tmp_path / "z.bin"))
    assert zb.returncode == 0, zb.stderr
    ref_dir = tmp_path / "ref_run"
    ref_dir.mkdir()
    z_dir = tmp_path / "z_run"
    z_dir.mkdir()
    ref_res = run_binary(str(tmp_path / "ref.bin"), str(ref_dir))
    z_res = run_binary(str(tmp_path / "z.bin"), str(z_dir))
    assert ref_res[:2] == z_res[:2], f"ref={ref_res!r} z={z_res!r}"


@pytest.mark.parametrize("unit", EMITC_CORPUS)
def test_emitc_corpus_matches_reference(unit, tmp_path, zc_binary):
    ref_c = str(tmp_path / "ref.c")
    z_c = str(tmp_path / "z.c")
    rp = emit_ref(unit, ref_c, CORPUS_DIR)
    assert rp.returncode == 0, rp.stderr
    zp = emit_z(zc_binary, unit, z_c, CORPUS_DIR)
    assert zp.returncode == 0, zp.stderr
    rb = build(ref_c, str(tmp_path / "ref.bin"))
    assert rb.returncode == 0, rb.stderr
    zb = build(z_c, str(tmp_path / "z.bin"))
    assert zb.returncode == 0, zb.stderr
    ref_dir = tmp_path / "ref_run"
    ref_dir.mkdir()
    z_dir = tmp_path / "z_run"
    z_dir.mkdir()
    ref_res = run_binary(str(tmp_path / "ref.bin"), str(ref_dir))
    z_res = run_binary(str(tmp_path / "z.bin"), str(z_dir))
    assert ref_res[:2] == z_res[:2], f"ref={ref_res!r} z={z_res!r}"
