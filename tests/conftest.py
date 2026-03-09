"""
Shared test fixtures and helpers for zerolang tests
"""

import io
import os

import pytest

from zvfs import ZVfsOpenFile, DEntryID, ZVfs, FSProvider, StringProvider, BindType
from zlexer import Tokenizer, Lexer
from ztokentype import TT


def make_tokenizer(source: str) -> Tokenizer:
    """Create a Tokenizer from a source string."""
    fh = io.StringIO(source)
    openfile = ZVfsOpenFile(entryid=DEntryID(0), filehandle=fh)
    return Tokenizer(openfile)


def make_lexer(source: str) -> Lexer:
    """Create a Lexer from a source string."""
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
def src_dir():
    """Return the path to the src directory."""
    return os.path.join(os.path.dirname(__file__), "..", "src")


def make_parser_vfs(source: str, unitname: str = "test", src_dir: str | None = None):
    """
    Create a VFS with a virtual test unit and the real system directory.
    Returns (vfs, unitname) suitable for Parser.
    """
    if src_dir is None:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")

    vfs = ZVfs()
    psystemid = vfs.register(FSProvider(rootpath=src_dir, parentpath="system"))
    pmainid = vfs.register(StringProvider(files={f"{unitname}.z": source}))
    rootid = vfs.walk()
    rootid = vfs.bind(parentid=rootid, name=None, newid=psystemid)
    rootid = vfs.bind(
        parentid=rootid, name=None, newid=pmainid, bindtype=BindType.BEFORE
    )

    return vfs, unitname
