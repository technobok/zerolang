#!/usr/bin/python3
"""
Token types for lexer (tokenizer)
"""

from enum import IntEnum


class TT(IntEnum):
    """
    Token Type
    """

    BOF = 0  # beginning of file
    EOF = 1  # end of file
    NONE = 2  # this is not a valid tokenstype. Placeholder only.
    ERR = 3  # an error token
    WS = 4
    EOL = 5
    COMMENT = 6

    PARENOPEN = 10
    PARENCLOSE = 11
    BRACEOPEN = 12
    BRACECLOSE = 13

    # COMMA = 14
    SEMICOLON = 15
    DOT = 16
    DOTDOTDOT = 17  # ellipsis
    COLON = 19

    STRBEG = 20  # start of string (" or `)
    STRMID = 21  # literal portion of a string
    STRCHR = 22  # backslash escaped character
    # STREOL = 23  # eol in a multiline string
    STREXPRBEG = 23  # backslash escaped expr in braces begin ("\")
    # STREXPREND = 24  # backslash escaped expr in braces end (zero width token)
    STREND = 25  # end of string (" or `)

    NUMBER = 30

    REFID = 40  # Identifier as a reference

    # keywords
    FUNCTION = 100
    ACCEPT = 101
    RETURN = 102
    YIELD = 103
    IF = 104
    WHEN = 105
    THEN = 106
    ELSE = 107
    FOR = 108
    LOOP = 109
    WHILE = 110
    DO = 111
    BLOCK = 112
    CASE = 113
    CAST = 114
    IN = 116
    OF = 117
    CONTINUE = 119
    BREAK = 120
    SWAP = 121
    UNIT = 122
    RECORD = 123
    CLASS = 124
    UNION = 125
    VARIANT = 126
    ENUM = 127
    PROTOCOL = 128
    TAG = 131
    DATA = 132
    IS = 133
    AS = 134
    OUT = 115
    MATCH = 118
    EQUALS = 135
    UNDERSCORE = 136

    LABEL = 200  # Identifier/Number followed by a ':'
    LABELPRE = 201  # Identifier/Number preceded by a ':'


TTKWMAP = {
    "=": TT.EQUALS,
    "_": TT.UNDERSCORE,
    "function": TT.FUNCTION,
    "accept": TT.ACCEPT,
    "return": TT.RETURN,
    "yield": TT.YIELD,
    "if": TT.IF,
    "when": TT.WHEN,
    "then": TT.THEN,
    "else": TT.ELSE,
    "for": TT.FOR,
    "loop": TT.LOOP,
    "while": TT.WHILE,
    "do": TT.DO,
    "block": TT.BLOCK,
    "case": TT.CASE,
    "match": TT.MATCH,
    "out": TT.OUT,
    "cast": TT.CAST,
    "in": TT.IN,
    "of": TT.OF,
    "continue": TT.CONTINUE,
    "break": TT.BREAK,
    "swap": TT.SWAP,
    "unit": TT.UNIT,
    "record": TT.RECORD,
    "class": TT.CLASS,
    "union": TT.UNION,
    "variant": TT.VARIANT,
    "enum": TT.ENUM,
    "protocol": TT.PROTOCOL,
    "tag": TT.TAG,
    "data": TT.DATA,
    "is": TT.IS,
    "as": TT.AS,
}
