#!/usr/bin/python3
"""
ZeroLang lexer (tokenizer)

Convert a file (or file-like object) into a stream of tokens.

Usage:

"""

from enum import Enum
from typing import Optional, Sequence, List, Protocol, Tuple
from abc import abstractmethod
from dataclasses import dataclass

import zchar
from zcharclass import CHARFLAGS, Charflag
from ztokentype import TT, TTKWMAP
from zvfs import DEntryID, ZVfsOpenFile


@dataclass
class Token:
    """
    a token: type, contents and location within file
    """

    toktype: TT
    tokstr: str
    fsno: DEntryID  # unit number in Program / ParserState
    lineno: int
    colno: int


class ITokenizer(Protocol):
    """
    ITokenizer protocol

    Supports returning a stream of tokens from a file-like object
    """

    # pylint: disable=too-few-public-methods
    # def token(self) -> Tuple[TT, str]:
    @abstractmethod
    def token(self) -> Token:
        """
        token - return a token
        """


class TokStateType(Enum):
    """
    Tokenizer state type for a single item on the tokenizer stack
    """

    FILE = 0  # inside a file
    STRING = 1  # inside a standard string
    STRINGRAW = 2  # inside a raw string
    # inside a stringexpr ("\{..}")
    STRINGEXPR = 3  # after the \ only in \{...}
    # can be multiple levels deep for each set of braces
    STRINGEXPRBRACE = 4  # after each '{' (and popped for each '}')


@dataclass
class TokState:
    """
    TokState - full state for a single item on the tokenizer stack
    """

    statetype: TokStateType
    openingtoken: Token


class Tokenizer(ITokenizer):
    """
    Tokenizer - slice a file into individual tokens. File is processed one char
    at a time. Every byte in the stream is returned as part of a token (and so
    the complete file could be reconstituted from the returned tokens).
    """

    def __init__(self, openfile: ZVfsOpenFile):
        """
        openfile - open file handle from vfs that can be read from. Should be buffered,
            reading will be char by char. Only filehandle.read(1) is called.
            filehandle will not be closed (caller must close)
        """
        # super().__init__()  # to keep linter happy... interface only...
        self.filehandle = openfile.filehandle
        # unit number reference that is embedded in each token that can be
        # used for (error) reporting
        self.fsno = openfile.entryid
        self.statestack: List[TokState] = []
        # beginning of file token
        tok = Token(TT.BOF, "", self.fsno, 1, 1)
        self._statepush(TokStateType.FILE, tok)

        # 1-indexed file line number
        self.lineno: int = 1
        # 1-indexed file column number, will be 1 below after _accept
        self.colno: int = 0
        # next token. This is if we require extra lookahead.
        # Used by: numbers (for "."), EOF
        self.nexttoken: Optional[Token] = None
        self.atchar: int = self._accept()  # prime the buffer

    def _accept(self) -> int:
        """
        accept the current char from the stream and advance to the next

        returns the char ordinal at the new position
        """
        c = self.filehandle.read(1)
        if len(c) > 0:
            self.atchar = ord(c)
            self.colno += 1
        else:
            # eof
            self.atchar = zchar.NUL
        return self.atchar

    def token(self) -> Token:
        """
        return the next token/tokenstring
        """
        # pylint: disable=R0912, R0911, R0914, R0915

        if self.nexttoken:
            # a token is already in the pipeline
            t = self.nexttoken
            if t.toktype != TT.EOF:
                # clear the token (unless at EOF)
                self.nexttoken = None
            return t

        # store the line and col start for this token
        lineno = self.lineno
        colno = self.colno
        c = self.atchar
        tokparts: List[str] = []

        # EOF
        if c == zchar.NUL:
            self.nexttoken = Token(TT.EOF, "", self.fsno, lineno, colno)
            return self.nexttoken

        # EOL (must be before string processing to handle embedded EOL in strings)
        if c in (zchar.LF, zchar.CR):
            tokstr = chr(c)
            c1 = self._accept()
            if c == zchar.CR and c1 == zchar.LF:
                self._accept()
                tokstr += chr(c1)
            self.lineno += 1
            self.colno = 1
            return Token(TT.EOL, tokstr, self.fsno, lineno, colno)

        # should always be at least one here
        state: TokState = self._statecurrent()

        if state.statetype == TokStateType.STRING:
            if c == zchar.DOUBLEQUOTE:
                self._accept()
                self._statepop()  # end of string
                return Token(TT.STREND, chr(c), self.fsno, lineno, colno)
            return self.acceptstringtoken()

        if state.statetype == TokStateType.STRINGRAW:
            tok, self.nexttoken = self.acceptstringrawtoken(state)
            if self.nexttoken:
                # got a delim. pop string
                self._statepop()  # end of string
            return tok

        # whitespace
        if c in (zchar.SPACE, zchar.HT):
            while c in (zchar.SPACE, zchar.HT):
                tokparts.append(chr(c))
                c = self._accept()
            return Token(TT.WS, "".join(tokparts), self.fsno, lineno, colno)

        # comment. Can have UTF8. Comments can also be inside STRINGEXPR
        if c == zchar.HASH:
            while c not in (zchar.LF, zchar.CR, zchar.NUL):
                tokparts.append(chr(c))
                c = self._accept()
            return Token(TT.COMMENT, "".join(tokparts), self.fsno, lineno, colno)

        if c == zchar.DOT:
            while c == zchar.DOT:
                tokparts.append(chr(c))
                c = self._accept()
            if len(tokparts) == 1:
                return Token(TT.DOT, ".", self.fsno, lineno, colno)
            if len(tokparts) == 3:
                return Token(TT.DOTDOTDOT, "".join(tokparts), self.fsno, lineno, colno)
            return Token(TT.ERR, "".join(tokparts), self.fsno, lineno, colno)

        if c == zchar.DOUBLEQUOTE:
            while c == zchar.DOUBLEQUOTE:
                tokparts.append(chr(c))
                c = self._accept()
            if len(tokparts) == 2:
                # empty string, no need to push state
                tok = Token(TT.STRBEG, '"', self.fsno, lineno, colno)
                self.nexttoken = Token(TT.STREND, '"', self.fsno, lineno, colno + 1)
                return tok

            tstr = "".join(tokparts)
            tok = Token(TT.STRBEG, tstr, self.fsno, lineno, colno)
            if len(tstr) > 1:
                self._statepush(TokStateType.STRINGRAW, tok)
            else:
                self._statepush(TokStateType.STRING, tok)
            return tok

        if c == zchar.BRACEOPEN:
            self._accept()
            tok = Token(TT.BRACEOPEN, chr(c), self.fsno, lineno, colno)
            if state.statetype in (
                TokStateType.STRINGEXPRBRACE,
                TokStateType.STRINGEXPR,
            ):
                self._statepush(TokStateType.STRINGEXPRBRACE, tok)
            return tok

        if c == zchar.BRACECLOSE:
            self._accept()
            if state.statetype == TokStateType.STRINGEXPRBRACE:
                self._statepop()
                if self._statecurrent().statetype == TokStateType.STRINGEXPR:
                    # was last STRINGEXPRBRACE, pop the backslash too
                    self._statepop()
            return Token(TT.BRACECLOSE, chr(c), self.fsno, lineno, colno)

        if c == zchar.PARENOPEN:
            self._accept()
            return Token(TT.PARENOPEN, chr(c), self.fsno, lineno, colno)

        if c == zchar.PARENCLOSE:
            self._accept()
            return Token(TT.PARENCLOSE, chr(c), self.fsno, lineno, colno)

        if c == zchar.SEMICOLON:
            self._accept()
            return Token(TT.SEMICOLON, chr(c), self.fsno, lineno, colno)

        # ----- get a 'word' (label, labelpre, identifier, keyword or numeric literal)

        # leading colon
        colonpre: Optional[Token] = None  # for colon in LABELPRE
        if c == zchar.COLON:
            colonpre = Token(TT.COLON, chr(c), self.fsno, lineno, colno)
            c = self._accept()
            colno = self.colno  # bump colno for next token

        if c in (zchar.PLUS, zchar.MINUS):
            tokparts.append(chr(c))
            c = self._accept()

        is_number = c < zchar.DEL and ((CHARFLAGS[c] & Charflag.DECD) != 0)

        # rest of 'word'
        while c < zchar.DEL and ((CHARFLAGS[c] & Charflag.IDEN) != 0):
            tokparts.append(chr(c))
            c = self._accept()

        # check for more number (not if LABELPRE, labels can't have dots)
        if is_number and c == zchar.DOT and not colonpre:
            c = self._accept()
            if c < zchar.DEL and ((CHARFLAGS[c] & Charflag.DECD) != 0):
                # more number (float/decimal)...
                tokparts.append(".")
                while c < zchar.DEL and ((CHARFLAGS[c] & Charflag.IDEN) != 0):
                    tokparts.append(chr(c))
                    c = self._accept()
            else:
                # not more number, but we have consumed the dot. self.colno!!!
                # add the already consumed dot (could be more to come)
                tokparts2: List[str] = [chr(c)]
                colno = self.colno - 1  # back to location of first dot
                while c == zchar.DOT:
                    tokparts2.append(chr(c))
                    self._accept()
                if len(tokparts2) == 1:
                    self.nexttoken = Token(TT.DOT, chr(c), self.fsno, lineno, colno)
                if len(tokparts2) == 3:
                    self.nexttoken = Token(
                        TT.DOTDOTDOT, "".join(tokparts2), self.fsno, lineno, colno
                    )
                self.nexttoken = Token(
                    TT.ERR, "".join(tokparts2), self.fsno, lineno, colno
                )

        if not tokparts:
            # didn't consume anything, error
            if colonpre:
                # lone colon, error
                return Token(
                    TT.ERR,
                    colonpre.tokstr,
                    colonpre.fsno,
                    colonpre.lineno,
                    colonpre.colno,
                )
            self._accept()  # consume a char to force advance
            return Token(TT.ERR, chr(c), self.fsno, lineno, colno)

        tstr = "".join(tokparts)  # complete token string
        ttkw = TTKWMAP.get(tstr)  # check if keyword

        # return the appropriate token type...

        if colonpre:
            # LABELPRE - return the colon, prefill nexttoken with the label
            if ttkw:
                # error - label cannot be a keyword
                self.nexttoken = Token(TT.ERR, tstr, self.fsno, lineno, colno)
            else:
                self.nexttoken = Token(TT.LABELPRE, tstr, self.fsno, lineno, colno)
            return colonpre

        if c == zchar.COLON:
            # LABEL - return the label, prefill nexttoken with colon
            tok = Token(TT.LABEL, tstr, self.fsno, lineno, colno)
            self.nexttoken = Token(TT.COLON, chr(c), self.fsno, self.lineno, self.colno)
            c = self._accept()  # accept the ':'
            return tok

        if ttkw:
            # KEYWORD: eg function, for, while, return
            return Token(ttkw, tstr, self.fsno, lineno, colno)

        # LABEL
        return Token(TT.REFID, tstr, self.fsno, lineno, colno)

    def acceptstringtoken(self) -> Token:
        """
        Given that we are in a string, return the next string token
        Note that we do not need to handle EOLs or "\"" (they were previously
        handled in main loop)
        """
        c = self.atchar
        lineno = self.lineno
        colno = self.colno

        if c == zchar.BACKSLASH:
            # escaped char
            # token type may be ERR, STRCHR or STRVARBEG
            return self.acceptcharescape()

        # *UTF8* is allowed in strings...
        stringparts = []
        c = self.atchar
        while c > zchar.DEL or (CHARFLAGS[c] & Charflag.STRC != 0):
            stringparts.append(chr(c))
            c = self._accept()

        tstr = "".join(stringparts)

        if tstr:
            return Token(TT.STRMID, tstr, self.fsno, lineno, colno)

        # must be an error, didn't advance
        c = self._accept()
        return Token(TT.ERR, chr(c), self.fsno, lineno, colno)

    def acceptstringrawtoken(self, state: TokState) -> Tuple[Token, Optional[Token]]:
        """
        Given that we are in a raw string, return the next string token
        Note that we do not need to handle EOLs or "`" (they were previously
        handled in main loop)

        Returns a tuple of a token, and an optional next token (for end of
        string delimiter, if the string ends here)
        """
        c = self.atchar
        lineno = self.lineno
        colno = self.colno
        # number of dquote chars to end string
        delimlength = len(state.openingtoken.tokstr)
        # optional end of string delimiter token
        delimtok: Optional[Token] = None

        # UTF8 is allowed in strings...
        stringparts: List[str] = []
        delimparts: List[str] = []
        while True:
            # RAWC does not include double quote or eol
            while c > zchar.DEL or (CHARFLAGS[c] & Charflag.RAWC != 0):
                stringparts.append(chr(c))
                c = self._accept()

            # check for ending delimiter
            if c != zchar.DOUBLEQUOTE:
                break

            dline = self.lineno
            dcol = self.colno
            while c == zchar.DOUBLEQUOTE and len(delimparts) < delimlength:
                delimparts.append(chr(c))
                c = self._accept()
            if len(delimparts) < delimlength:
                # not enough double quotes. This is part of literal
                stringparts.extend(delimparts)
            else:
                # end of string
                delimtok = Token(TT.STREND, "".join(delimparts), self.fsno, dline, dcol)
                break

        tstr = "".join(stringparts)
        if tstr:
            return (Token(TT.STRMID, tstr, self.fsno, lineno, colno), delimtok)

        # must be an error, didn't advance
        c = self._accept()
        token = Token(TT.ERR, chr(c), self.fsno, lineno, colno)
        return (token, delimtok)

    # def acceptstringfrag(self, charflag: Charflag) -> str:
    #     """
    #     returns the string fragment
    #     charflag should be Charflag.RAWC or Charflag.STRC
    #     """
    #     # *UTF8* is allowed in strings...
    #     stringparts = []
    #     c = self.atchar
    #     while c > zchar.DEL or (CHARFLAGS[c] & charflag != 0):
    #         stringparts.append(chr(c))
    #         c = self._accept()

    #     tstr = "".join(stringparts)
    #     return tstr

    def acceptcharescape(self) -> Token:
        """
        returns the token (which could be an error token)
        if not valid, str will be the chars accepted until the error char
        Must be at the leading backslash or will return error
        """
        parts: List[str] = []
        c = self.atchar
        lineno = self.lineno
        colno = self.colno
        if c == zchar.BACKSLASH:
            parts.append(chr(c))
            c = self._accept()
        else:
            # no starting backslash, error
            return Token(TT.ERR, chr(c), self.fsno, lineno, colno)

        if c in (
            zchar.LC_N,
            zchar.LC_R,
            zchar.LC_T,
            zchar.LC_B,
            zchar.BACKSLASH,
            zchar.DOUBLEQUOTE,
        ):
            self._accept()
            parts.append(chr(c))
            tokstr = "".join(parts)
            return Token(TT.STRCHR, tokstr, self.fsno, lineno, colno)

        hexdigits: int = 0
        if c == zchar.LC_X:
            hexdigits = 2
        elif c == zchar.LC_U:
            hexdigits = 6

        if hexdigits > 0:
            parts.append(chr(c))  # store and accept the specifier
            c = self._accept()
            tt = TT.STRCHR
            for _ in range(hexdigits):  # exact number of hex chars to get
                if c < zchar.DEL and ((CHARFLAGS[c] & Charflag.HEXD) != 0):
                    parts.append(chr(c))
                    c = self._accept()
                else:
                    tt = TT.ERR
                    break

            tokstr = "".join(parts)
            return Token(tt, tokstr, self.fsno, lineno, colno)

        if c == zchar.BRACEOPEN:  # start of a strexpr "\{//}"
            # do NOT accept or append the BRACEOPEN, it will be the next token
            tokstr = "".join(parts)  # '\' only...
            tok = Token(TT.STREXPRBEG, tokstr, self.fsno, lineno, colno)
            self._statepush(TokStateType.STRINGEXPR, tok)
            return tok

        # malformed - unknown escape character
        tokstr = "".join(parts)
        return Token(TT.ERR, tokstr, self.fsno, lineno, colno)

    def _statecurrent(self) -> TokState:
        """
        return the current state type
        """
        return self.statestack[-1]  # there is always at least the FILE state

    def _statepop(self) -> TokState:
        """
        pop and return the current state type (safely)
        """
        if len(self.statestack) == 1:
            # never pop the top (FILE) item
            # in case statestack is messed up from unbalanced input
            return self.statestack[0]
        return self.statestack.pop()

    def _statepush(self, statetype: TokStateType, openingtoken: Token) -> None:
        """
        push a new state type on the stack
        """
        state = TokState(statetype, openingtoken)
        self.statestack.append(state)


class Lexer:
    """
    Lexer - Public interface to the lexer that allows peeking and accepting
    tokens one at a time (single token of lookahead). Does filtering and coalescing
    to help make the parser slightly simpler. This is the Lexer object that the
    Parser expects to receive.

    Usage:

    """

    def __init__(self, lexer: ITokenizer):
        """
        filehandle = filelike object for reading bytes to parse tokens
        unitno = unit number reference that is embedded in each token that
            can be used for (error) reporting.
        """
        self._lexer = lexer
        # start in NORMAL
        # self.blockstack: List[BlockType] = [BlockType.NORMAL]
        self._nexttoken: Optional[Token] = (
            None  # lookahead used for LABELPRE conversion
        )
        # buffer of max 1 token
        self._thistoken: Token
        self._filtereol: bool = (
            True  # filter (skip) EOL's. Default is true. See filtereol()
        )
        self._advance()  # prime the buffer

    def _advance(self) -> None:
        """
        read and store next token

        do some filtering and coalescing when reading to help the parser:
        - skip all ws or comments
        - eols are skipped or passed depending on status of filtereol()
        - expand ':label' into 2 tokens: 'label' LABEL and 'label' as a REFID or NUMBER
        """
        if self._nexttoken:
            self._thistoken = self._nexttoken
            self._nexttoken = None
            return

        while True:
            token = self._lexer.token()
            tt = token.toktype

            # print(f"TOKEN={token.toktype.name}:[{token.tokstr}]")
            if tt in (TT.WS, TT.COMMENT, TT.COLON):
                # always skip these
                continue

            if tt == TT.EOL and self._filtereol:
                continue

            if tt == TT.LABELPRE:
                # unpack a LABELPRE -> LABEL: REFID
                self._nexttoken = Token(
                    TT.REFID, token.tokstr, token.fsno, token.lineno, token.colno
                )
                # set token to this instead...
                token = Token(
                    TT.LABEL, token.tokstr, token.fsno, token.lineno, token.colno
                )

            break  # store this token

        self._thistoken = token
        return

    # class BlockType(Enum):
    #     """
    #     BlockType - type of block that the parser is currently inside. Used for "sep"
    #         token and eol handling
    #     """
    #
    #     NORMAL = 0  # handle eol's 'normally': pass first one then coalesce
    #     PAREN = 1  # an expression within parens (including STRVAR)
    #     STRING = 2  # a string literal
    #     EOLS = 3  # coalesced eols (after returning the first one
    # def _read(self) -> Token:
    #     """
    #     read and return next token
    #     caller MUST put it in self.buffer
    #
    #     do some filtering and coalescing when reading to help the parser:
    #     - skip all ws or comments
    #     - coalesce eol[s] into a single sep token as appropriate for the
    #         current block structure.
    #         - EOL's in strings are passed unchanged.
    #         - EOL's in parens () are filtered
    #         - All others are coalesced into a single EOL (all after first are filetered)
    #
    #     Note that unbalanced parens or braces can mess up the ws/comment/eol
    #     coalescing.
    #     """
    #     if self.nexttoken:
    #         t = self.nexttoken
    #         self.nexttoken = None
    #         return t
    #
    #     while True:
    #         token = self.lexer.token()
    #         tt = token.toktype
    #
    #         # print(f"TOKEN={token.toktype.name}:[{token.tokstr}]")
    #         if tt in (TT.WS, TT.COMMENT):
    #             # always skip all whitespace and comments
    #             continue
    #
    #         blocktype: BlockType = self._blockcurrent()
    #
    #         if tt == TT.EOL:
    #             if blocktype in (BlockType.EOLS, BlockType.PAREN):
    #                 # always skip eol after coalescing, in Parens (inc stringvar)
    #                 continue
    #             if blocktype == BlockType.NORMAL:
    #                 self._blockpush(BlockType.EOLS)  # coalesce now
    #                 return token  # allow first eol through
    #             if blocktype == BlockType.STRING:
    #                 return token  # allow the EOL literal to pass though in strings
    #             # there should be no other cases
    #             raise Exception(
    #                 f"Error in tokenizer, not all blocktypes handled: {blocktype.name}"
    #             )
    #
    #         # no more ws/comments/eol from here down
    #         # error to have more than one EOLS on the blockstack?
    #         if blocktype == BlockType.EOLS:
    #             self._blockpop()
    #             blocktype = self._blockcurrent()
    #
    #         if tt == TT.COLON:
    #             continue  # filter all colons
    #
    #         if tt == TT.LABELPRE:
    #             # unpack a LABELPRE -> LABEL: REFID
    #             # REFID could be a number literal
    #             c = ord(token.tokstr[0])
    #             tt2 = TT.REFID
    #             if c < zchar.DEL and ((CHARFLAGS[c] & Charflag.DECD) != 0):
    #                 tt2 = TT.NUMBER
    #             self.nexttoken = Token(
    #                 tt2, token.tokstr, token.fsno, token.lineno, token.colno
    #             )
    #             return Token(
    #                 TT.LABEL, token.tokstr, token.fsno, token.lineno, token.colno
    #             )
    #
    #         if tt == TT.PARENOPEN:
    #             # skip all eols inside parens
    #             # PARENOPEN in STRVARBEG is handled here as well
    #             self._blockpush(BlockType.PAREN)
    #         elif tt == TT.PARENCLOSE and blocktype == BlockType.PAREN:
    #             # may have unbalanced paren, only pop if in PAREN - don't
    #             # mess things up further
    #             # PARENCLOSE in STRVAREND is handled here as well
    #             self._blockpop()
    #         elif tt == TT.BRACEOPEN:
    #             # 'normal' processing within braces
    #             self._blockpush(BlockType.NORMAL)
    #         elif tt == TT.BRACECLOSE and blocktype == BlockType.NORMAL:
    #             # may have unbalanced paren, only pop if in NORMAL - don't
    #             # mess things up further
    #             self._blockpop()
    #         break
    #
    #     return token

    # def _blockcurrent(self) -> BlockType:
    #     """
    #     return the current block type (safely)
    #     """
    #     # in case blockstack is messed up from unbalanced input
    #     blocktype: BlockType = BlockType.NORMAL
    #     if self.blockstack:
    #         blocktype = self.blockstack[-1]
    #     return blocktype
    #
    # def _blockpop(self) -> BlockType:
    #     """
    #     pop and return the current block type (safely)
    #     """
    #     # in case blockstack is messed up from unbalanced input
    #     blocktype: BlockType = BlockType.NORMAL
    #     if self.blockstack:
    #         blocktype = self.blockstack.pop()
    #     return blocktype
    #
    # def _blockpush(self, blocktype: BlockType) -> None:
    #     """
    #     push a new block type on the stack
    #     """
    #     self.blockstack.append(blocktype)

    def filtereol(self, filtereol: bool) -> None:
        """
        Set the EOL filtering status

        filtereol: True to filter (skip) EOL's. False to pass through (return)
            all EOL tokens.
        """
        self._filtereol = filtereol
        if filtereol and self._thistoken.toktype == TT.EOL:
            self._advance()

    def peek(self) -> Token:
        """
        return the next token (already in the buffer) WITHOUT advancing
        """
        return self._thistoken

    def accept(self, tokentype: TT) -> Optional[Token]:
        """
        accept a single token and advance if it matches

        return the token if accepted, None otherwise
        """
        t = self._thistoken
        if t.toktype == tokentype:
            self._advance()
            return t
        return None

    def acceptoneof(self, tokentypes: Sequence[TT]) -> Optional[Token]:
        """
        accept a single token and advance if it matches any type from the
        supplied list

        return the token if accepted, None otherwise
        """
        t = self._thistoken
        if t.toktype in tokentypes:
            self._advance()
            return t
        return None

    def acceptany(self) -> Token:
        """
        accept and return any token AND advance
        """
        t = self._thistoken
        self._advance()
        return t


# def makelexer(filehandle: IO[str], fsno: DEntryID) -> Lexer:
#     """
#     makelexer - utility function to make a Lexer connected to a Tokenizer
#     """
#     lf = Tokenizer(filehandle, fsno)
#     return Lexer(lf)


def isvalidunitname(name: str) -> bool:
    """
    isvalidunitname - return true if name is a valid unit name

    Valid unit names may only start with a lowercase ascii letter and may
    only afterwards contain lowercase letters, numbers or undercores.
    """
    first = True
    for c in name:
        o = ord(c)
        if first:
            first = False
            if ord("a") <= o <= ord("z"):
                continue
        elif (
            (ord("0") <= o <= ord("9")) or (ord("a") <= o <= ord("z")) or o == ord("_")
        ):
            continue

        return False

    return True


# def _test() -> None:
#     # test the lexer
#     with open("example0.zero", encoding="utf8") as f:
#         lex = makelexer(f, DEntryID(0))
#         while True:
#             t = lex.acceptany()
#             print(t)
#             if t.toktype == TT.EOF:
#                 break


# if __name__ == "__main__":
#     _test()
