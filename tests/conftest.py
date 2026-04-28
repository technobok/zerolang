"""
Shared test fixtures and helpers for zerolang tests
"""

import io
import os
import pickle

import pytest

from zvfs import ZVfsOpenFile, DEntryID, ZVfs, FSProvider, StringProvider, BindType
from zlexer import Tokenizer, Lexer
from ztokentype import TT
from zparser import Parser


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
