"""
Canonical token-stream dump.

One line per token: `LINENO:COLNO TTNAME "tokstr"` where tokstr is
escape-encoded so the output is printable ASCII and byte-stable across
platforms. Used by the lexer differential test harness to compare the
Python reference lexer against the eventual zerolang-side lexer.
"""

import io

from zlexer import Tokenizer
from ztokentype import TT
from zvfs import ZVfsOpenFile, DEntryID


_ESC_MAP = {
    0x09: "\\t",
    0x0A: "\\n",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _escape(tokstr: str) -> str:
    parts = []
    for ch in tokstr:
        cp = ord(ch)
        esc = _ESC_MAP.get(cp)
        if esc is not None:
            parts.append(esc)
        elif 0x20 <= cp <= 0x7E:
            parts.append(ch)
        elif cp < 0x100:
            parts.append(f"\\x{cp:02x}")
        else:
            parts.append(f"\\u{{{cp:x}}}")
    return "".join(parts)


def dump_tokens(source: str, fsno: int = 0) -> str:
    """Tokenize source and return the canonical dump as a single string."""
    fh = io.StringIO(source)
    openfile = ZVfsOpenFile(entryid=DEntryID(fsno), filehandle=fh)
    tok = Tokenizer(openfile)
    lines = []
    while True:
        t = tok.token()
        line = f'{t.lineno}:{t.colno} {t.toktype.name} "{_escape(t.tokstr)}"'
        lines.append(line)
        if t.toktype == TT.EOF:
            break
    return "\n".join(lines) + "\n"
