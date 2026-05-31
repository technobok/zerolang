"""
Shared test fixtures and helpers for zerolang tests
"""

import io
import os
import pickle
import re
import shutil
import subprocess
import sys

import pytest

from zvfs import ZVfsOpenFile, DEntryID, ZVfs, FSProvider, StringProvider, BindType
from zlexer import Tokenizer, Lexer
from ztokentype import TT
from zparser import Parser


def normalize_cnames(text: str) -> str:
    """Strip the id-naming segment from generated C / cname fields so
    assertions can match canonical names: ``z_t<N>_`` -> ``z_`` (types/
    functions) and ``z_v<N>_`` -> `` (variables). Apply only to assertion
    haystacks, never to C that is compiled — the ids keep names unique."""
    text = re.sub(r"z_t[0-9]+_", "z_", text)
    text = re.sub(r"z_v[0-9]+_", "", text)
    return text


class EmittedC(str):
    """Generated-C string whose substring queries normalize id-named cnames,
    so existing assertions like ``"z_String_free" in csource`` keep matching
    (``z_t5_String_free`` normalizes to ``z_String_free``) while ``str(...)``
    — what gets written and compiled — preserves the ids that keep the C
    unique. ``in`` / ``index`` / ``find`` / ``count`` run against the
    normalized text (relative order and counts are preserved); plain string
    indexing/slicing and ``str(...)`` stay raw."""

    def _norm(self) -> str:
        return normalize_cnames(str(self))

    def __contains__(self, item) -> bool:
        return str(item) in self._norm()

    def index(self, sub, *args) -> int:  # type: ignore[override]
        return self._norm().index(sub, *args)

    def find(self, sub, *args) -> int:  # type: ignore[override]
        return self._norm().find(sub, *args)

    def count(self, sub, *args) -> int:  # type: ignore[override]
        return self._norm().count(sub, *args)

    def __getitem__(self, key):  # slices/inspection see the normalized text
        return self._norm()[key]

    def split(self, *args, **kwargs):  # type: ignore[override]
        return self._norm().split(*args, **kwargs)


_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
_ZC = [sys.executable, os.path.join(_SRC_DIR, "zc.py")]
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


def _build_zerolang_unit(unitname: str, tmp_path_factory) -> str:
    """Build a self-hosted compiler unit (src/<unitname>.z -> C -> binary)
    into a per-session tmp dir so xdist workers don't race on shared
    output paths. Returns the binary path. Skips the whole test module
    when the C compiler is missing."""
    if shutil.which(_CC) is None:
        pytest.skip(f"{_CC} not on PATH; cannot build {unitname} binary")
    builddir = tmp_path_factory.mktemp(unitname)
    c_path = str(builddir / f"{unitname}.c")
    bin_path = str(builddir / unitname)
    zc_proc = subprocess.run(
        _ZC + [unitname, "--src", _SRC_DIR, "-o", c_path],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert zc_proc.returncode == 0, (
        f"zc.py {unitname} failed:\nstdout:\n{zc_proc.stdout}\n"
        f"stderr:\n{zc_proc.stderr}"
    )
    cc_proc = subprocess.run(
        [_CC, *_CFLAGS, "-o", bin_path, c_path],
        capture_output=True,
        text=True,
    )
    assert cc_proc.returncode == 0, (
        f"{_CC} {unitname} failed:\nstdout:\n{cc_proc.stdout}\n"
        f"stderr:\n{cc_proc.stderr}"
    )
    return bin_path


@pytest.fixture(scope="session")
def zlexer_binary(tmp_path_factory):
    """Build the self-hosted lexer (src/zlexer.z) once per session."""
    return _build_zerolang_unit("zlexer", tmp_path_factory)


@pytest.fixture(scope="session")
def zvfs_binary(tmp_path_factory):
    """Build the self-hosted VFS skeleton (src/zvfs.z) once per session."""
    return _build_zerolang_unit("zvfs", tmp_path_factory)


@pytest.fixture(scope="session")
def zast_binary(tmp_path_factory):
    """Build the self-hosted AST skeleton (src/zast.z) once per session."""
    return _build_zerolang_unit("zast", tmp_path_factory)


@pytest.fixture(scope="session")
def zparser_binary(tmp_path_factory):
    """Build the self-hosted parser skeleton (src/zparser.z) once per session."""
    return _build_zerolang_unit("zparser", tmp_path_factory)


def make_tokenizer(source: str) -> Tokenizer:
    """Create a Tokenizer from a source String."""
    fh = io.StringIO(source)
    openfile = ZVfsOpenFile(entryid=DEntryID(0), filehandle=fh)
    return Tokenizer(openfile)


def make_lexer(source: str) -> Lexer:
    """Create a Lexer from a source String."""
    tok = make_tokenizer(source)
    return Lexer(tok)


def collect_tokens(source: str) -> list:
    """Collect all raw tokens from the Tokenizer (excluding BOF)."""
    tok = make_tokenizer(source)
    tokens = []
    while True:
        t = tok.token()
        if t.toktype == TT.EOF:
            break
        tokens.append(t)
    return tokens


def collect_lexer_tokens(source: str) -> list:
    """Collect all tokens from the Lexer (filtered)."""
    lex = make_lexer(source)
    tokens = []
    while True:
        t = lex.acceptany()
        tokens.append(t)
        if t.toktype == TT.EOF:
            break
    return tokens


@pytest.fixture
def lib_dir():
    """Return the Path to the lib directory."""
    return os.path.join(os.path.dirname(__file__), "..", "lib")


def make_parser_vfs(source: str, unitname: str = "test", src_dir: str | None = None):
    """
    Create a VFS with a virtual test unit and the real system directory.
    Returns (vfs, unitname) suitable for Parser.
    """
    if src_dir is None:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "lib")

    vfs = ZVfs()
    psystemid = vfs.register(FSProvider(rootpath=src_dir, parentpath="system"))
    pmainid = vfs.register(StringProvider(files={f"{unitname}.z": source}))
    rootid = vfs.walk()
    rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
    rootid = vfs.bind(
        parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
    )

    return vfs, unitname


# Session cache of parsed system-lib units, keyed by src_dir. Stored as a
# pickled blob: pickle.loads is ~4x faster than copy.deepcopy on the AST,
# and re-parsing the system lib (~1400 LOC of .z) costs ~100 ms per test
# across ~1900 tests in the suite. The type checker mutates AST nodes
# in-place, so each test must get a fresh copy of the AST — pickle round-trip
# both copies and avoids re-parsing.
_SYSTEM_UNITS_PICKLE: dict[str, bytes] = {}


def _get_cached_system_units(src_dir: str) -> dict:
    """Return a fresh {unit_name: zast.Unit} dict from the cached system lib.

    Parses the system lib once per src_dir per pytest session, then unpickles
    a fresh copy on every call (so the type checker can mutate without leaking
    state to other tests).
    """
    blob = _SYSTEM_UNITS_PICKLE.get(src_dir)
    if blob is None:
        # Parse a trivial program to obtain all system units transitively
        vfs, name = make_parser_vfs(
            "main: function is {}", unitname="cacheseed", src_dir=src_dir
        )
        seed_program = Parser(vfs, name).parse()
        if not hasattr(seed_program, "units"):
            raise RuntimeError(f"system-lib cache seed parse failed: {seed_program!r}")
        units = {k: v for k, v in seed_program.units.items() if k != name}
        blob = pickle.dumps(units, protocol=pickle.HIGHEST_PROTOCOL)
        _SYSTEM_UNITS_PICKLE[src_dir] = blob
    return pickle.loads(blob)


def make_parser(
    source: str, unitname: str = "test", src_dir: str | None = None
) -> Parser:
    """
    Create a Parser preconfigured with cached system-lib units.
    Equivalent to make_parser_vfs() + Parser(vfs, name) but skips re-parsing
    the system lib (~75 ms saved per call vs full parse).
    """
    if src_dir is None:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
    vfs, name = make_parser_vfs(source, unitname=unitname, src_dir=src_dir)
    prebuilt = _get_cached_system_units(src_dir)
    return Parser(vfs, name, prebuilt=prebuilt)


def make_parser_with_vfs(
    vfs: ZVfs, mainunitname: str, src_dir: str | None = None
) -> Parser:
    """
    Like Parser(vfs, name) but with cached system-lib units injected.
    Use when the test constructs its own custom VFS (e.g. multi-File tests).
    """
    if src_dir is None:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
    prebuilt = _get_cached_system_units(src_dir)
    return Parser(vfs, mainunitname, prebuilt=prebuilt)
