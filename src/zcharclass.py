#!/usr/bin/python3

"""
Character classes and character class table for the ASCII (<=127) characters
Used in the lexer
"""

from enum import IntEnum
from typing import List
import zchar

# Character Class
# NONE = 0x0000
# WS = 0x0001  # Horizontal whitespace, only (no NL)
# ERR = 0x0004  # error to have char (outside string/comment) (reserved char)
# DEC = 0x0008  # decimal digit
# HEX = 0x0010  # hexadecimal digit
# DELIM = 0x0020  # delimiters ( ) { }
# # SEPARATOR      = 0x0040      # no longer used
# # WORD           = 0x0080
# IDSEP = 0x0100  # character that will end a word (start of next..)
# STRSEP = 0x0200  # character that marks the end of a stringpart
# RAWSEP = 0x0400  # character that marks the end of a raw stringpart
# COMSEP = 0x0800  # character that marks the end of a comment


class Charflag(IntEnum):
    """
    Character flags for a single character ordinal
    """

    NONE = 0b00000000  # nothing...
    IDEN = 0b00000001  # valid identifier character
    DECD = 0b00000010  # decimal digit
    HEXD = 0b00000100  # hexadecimal digit
    RAWC = 0b00001000  # raw string character (includes >DEL)
    STRC = 0b00010000  # interpolated string character (includes >DEL)


# ----- Make charmap
# CHARFLAGS = bytearray(128)
# def makecharflags() -> List[int]:
def makecharflags() -> bytes:
    """
    make the list of character flags (character classes)
    """
    charflags: List[int] = [Charflag.NONE] * 128

    for c in range(128):
        flags: int = 0

        # IDEN
        if zchar.SPACE < c < zchar.DEL and c not in (
            zchar.DOUBLEQUOTE,
            zchar.DOT,
            zchar.HASH,
            #zchar.SINGLEQUOTE, # this is allowed now
            zchar.PARENOPEN,
            zchar.PARENCLOSE,
            zchar.COMMA,    # error
            zchar.DOT,
            zchar.COLON,
            zchar.SEMICOLON,    # error
            zchar.SQBRACKETOPEN,    # error
            #zchar.BACKSLASH,   # this is allowed now
            zchar.SQBRACKETCLOSE,   # error
            zchar.BACKQUOTE,    # error
            zchar.BRACEOPEN,
            zchar.BRACECLOSE,
        ):
            flags = flags | Charflag.IDEN

        # DECD
        if zchar.ZERO <= c <= zchar.NINE:
            flags = flags | Charflag.DECD

        # HEXD
        if (
            zchar.ZERO <= c <= zchar.NINE
            or (zchar.UC_A <= c <= zchar.UC_F)
            or (zchar.LC_A <= c <= zchar.LC_F)
        ):
            flags = flags | Charflag.HEXD

        # RAWC includes >=DEL, noEOL because they are in a separate token
        # Double quotes allowed, but need to be checked for delimiter
        if c not in (zchar.NUL, zchar.DOUBLEQUOTE, zchar.LF, zchar.CR):
            flags = flags | Charflag.RAWC

        # STRC, no EOL or backslash because they are separate tokens
        if c not in (zchar.NUL, zchar.DOUBLEQUOTE, zchar.LF, zchar.CR, zchar.BACKSLASH):
            flags = flags | Charflag.STRC

        charflags[c] = flags

    return bytes(charflags)


# def makecharflagsOLD() -> bytes:
#     """
#     make the list of character flags (character classes)
#     """
#     charflags: List[int] = [NONE] * 128

#     for c in range(128):
#         flags: int = 0

#         if c <= zchar.SPACE:
#             flags = flags | IDSEP

#         if (c < zchar.SPACE and c != (zchar.HT)) or c == zchar.DEL:
#             flags = flags | STRSEP | RAWSEP | COMSEP

#         if (c < zchar.SPACE and c != (zchar.HT)) or c in (
#             zchar.SQBRACKETOPEN,
#             zchar.SQBRACKETCLOSE,
#             zchar.SEMICOLON,
#             zchar.BACKSLASH,
#             zchar.SINGLEQUOTE,
#             zchar.DEL,
#         ):
#             flags = flags | ERR | IDSEP

#         if zchar.ZERO <= c <= zchar.NINE:
#             flags = flags | DEC | HEX

#         if (zchar.UC_A <= c <= zchar.UC_F) or (zchar.LC_A <= c <= zchar.LC_F):
#             flags = flags | HEX

#         if c <= zchar.SPACE or c in (zchar.HASH, zchar.DOUBLEQUOTE, zchar.BACKQUOTE):
#             flags = flags | IDSEP

#         if c in (zchar.HT, zchar.SPACE):
#             flags = flags | WS | IDSEP

#         if c in (zchar.BRACEOPEN, zchar.BRACECLOSE, zchar.PARENOPEN, zchar.PARENCLOSE):
#             flags = flags | DELIM | IDSEP

#         if c in (zchar.COMMA, zchar.COLON, zchar.DOT):
#             flags = flags | IDSEP

#         if c in (zchar.BACKSLASH, zchar.DOUBLEQUOTE):
#             flags = flags | STRSEP

#         if c == zchar.BACKQUOTE:
#             flags = flags | RAWSEP

#         charflags[c] = flags
#     return bytes(charflags)


CHARFLAGS = makecharflags()


def printcharmap(charflags: bytes) -> None:
    """
    Dump the charmap
    """
    for x, f in enumerate(charflags):
        c = repr(chr(x))
        iden = "IDEN" if f & Charflag.IDEN else ""
        decd = "DECD" if f & Charflag.DECD else ""
        hexd = "HEXD" if f & Charflag.HEXD else ""
        rawc = "RAWC" if f & Charflag.RAWC else ""
        strc = "STRC" if f & Charflag.STRC else ""

        #         ccws = "" if f & WS else ""
        #         ccws = "" if f & WS else ""
        #         ccdec = "DEC" if f & DEC else ""
        #         cchex = "HEX" if f & HEX else ""
        #         ccdelim = "DELIM" if f & DELIM else ""
        #         ccidsep = "IDSEP" if f & IDSEP else ""
        #         ccstrsep = "STRSEP" if f & STRSEP else ""
        #         ccrawsep = "RAWSEP" if f & RAWSEP else ""
        #         ccerr = "ERR" if f & ERR else ""
        #         cccomsep = "COMSEP" if f & COMSEP else ""

        # print(
        #     f"{x:3} {c:6} "
        #     + f"{ccws:<2}|{ccerr:<3}|"
        #     + f"{ccdec:<3}|{cchex:<3}|{ccdelim:<5}|"
        #     + f"{ccidsep:<5}|{ccstrsep:<6}|{ccrawsep:<6}|"
        #     f"{cccomsep:<6}|" + f"0b{f:016b}"
        # )
        print(
            f"{x:3}|{c:6}|"
            + f"{iden:<4}|{decd:<4}|"
            + f"{hexd:<4}|{rawc:<4}|{strc:<4}|"
            + f"0b{f:016b}"
        )


if __name__ == "__main__":
    printcharmap(CHARFLAGS)
