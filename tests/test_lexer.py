"""
Tests for the Tokenizer and Lexer
"""

from conftest import make_tokenizer, make_lexer, collect_tokens, collect_lexer_tokens
from ztokentype import TT
from zlexer import isvalidunitname


class TestTokenizerIdentifiers:
    def test_plain_identifier(self):
        tok = make_tokenizer("foo")
        t = tok.token()
        assert t.toktype == TT.REFID
        assert t.tokstr == "foo"

    def test_identifier_with_plus(self):
        tok = make_tokenizer("+")
        t = tok.token()
        assert t.toktype == TT.REFID
        assert t.tokstr == "+"

    def test_identifier_with_minus(self):
        tok = make_tokenizer("-")
        t = tok.token()
        assert t.toktype == TT.REFID
        assert t.tokstr == "-"

    def test_identifier_with_special_chars(self):
        """Identifiers can include operator chars like *, /, <, =, >"""
        tok = make_tokenizer("<=")
        t = tok.token()
        assert t.toktype == TT.REFID
        assert t.tokstr == "<="

    def test_identifier_with_equals(self):
        """= is a keyword"""
        tok = make_tokenizer("=")
        t = tok.token()
        assert t.toktype == TT.EQUALS
        assert t.tokstr == "="


class TestTokenizerNumbers:
    def test_integer(self):
        tok = make_tokenizer("42")
        t = tok.token()
        assert t.toktype == TT.NUMBER
        assert t.tokstr == "42"

    def test_float(self):
        tok = make_tokenizer("1.5")
        t = tok.token()
        assert t.toktype == TT.NUMBER
        assert t.tokstr == "1.5"

    def test_signed_negative(self):
        tok = make_tokenizer("-3")
        t = tok.token()
        assert t.toktype == TT.NUMBER
        assert t.tokstr == "-3"

    def test_signed_positive(self):
        tok = make_tokenizer("+5")
        t = tok.token()
        assert t.toktype == TT.NUMBER
        assert t.tokstr == "+5"

    def test_zero(self):
        tok = make_tokenizer("0")
        t = tok.token()
        assert t.toktype == TT.NUMBER
        assert t.tokstr == "0"


class TestTokenizerKeywords:
    def test_all_keywords(self):
        from ztokentype import TTKWMAP

        for kw, tt in TTKWMAP.items():
            tok = make_tokenizer(kw)
            t = tok.token()
            assert t.toktype == tt, f"Keyword '{kw}' should produce {tt.name}"
            assert t.tokstr == kw

    def test_out_keyword(self):
        tok = make_tokenizer("out")
        t = tok.token()
        assert t.toktype == TT.OUT

    def test_match_keyword(self):
        tok = make_tokenizer("match")
        t = tok.token()
        assert t.toktype == TT.MATCH

    def test_function_keyword(self):
        tok = make_tokenizer("function")
        t = tok.token()
        assert t.toktype == TT.FUNCTION

    def test_case_keyword(self):
        tok = make_tokenizer("case")
        t = tok.token()
        assert t.toktype == TT.CASE


class TestTokenizerStrings:
    def test_regular_string(self):
        tokens = collect_tokens('"hello"')
        types = [t.toktype for t in tokens]
        assert TT.STRBEG in types
        assert TT.STRMID in types
        assert TT.STREND in types

    def test_empty_string(self):
        tokens = collect_tokens('""')
        types = [t.toktype for t in tokens]
        assert TT.STRBEG in types
        assert TT.STREND in types
        # no STRMID for empty string

    def test_raw_string(self):
        tokens = collect_tokens('"""raw text"""')
        types = [t.toktype for t in tokens]
        assert TT.STRBEG in types
        assert TT.STRMID in types
        assert TT.STREND in types
        mid = [t for t in tokens if t.toktype == TT.STRMID]
        assert mid[0].tokstr == "raw text"

    def test_string_interpolation(self):
        # Zero uses \{ for string interpolation expressions
        source = '"x=\\{x}"'
        tokens = collect_tokens(source)
        types = [t.toktype for t in tokens]
        assert TT.STREXPRBEG in types

    def test_old_string_interpolation_is_error(self):
        # Old \( syntax should now be an error, not STREXPRBEG
        source = '"x=\\(x)"'
        tokens = collect_tokens(source)
        types = [t.toktype for t in tokens]
        assert TT.STREXPRBEG not in types
        assert TT.ERR in types


class TestTokenizerEscapeSequences:
    def test_newline_escape(self):
        tokens = collect_tokens('"\\n"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\n"

    def test_tab_escape(self):
        tokens = collect_tokens('"\\t"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\t"

    def test_backslash_escape(self):
        tokens = collect_tokens('"\\\\"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\\\"

    def test_quote_escape(self):
        tokens = collect_tokens('"\\""')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == '\\"'

    def test_hex_escape(self):
        tokens = collect_tokens('"\\x41"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\x41"

    def test_unicode_escape(self):
        tokens = collect_tokens('"\\u00263A"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\u00263A"

    def test_return_escape(self):
        tokens = collect_tokens('"\\r"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\r"

    def test_backspace_escape(self):
        tokens = collect_tokens('"\\b"')
        escaped = [t for t in tokens if t.toktype == TT.STRCHR]
        assert len(escaped) == 1
        assert escaped[0].tokstr == "\\b"


class TestTokenizerComments:
    def test_comment(self):
        tokens = collect_tokens("# this is a comment\n")
        types = [t.toktype for t in tokens]
        assert TT.COMMENT in types
        assert TT.EOL in types

    def test_comment_content(self):
        tokens = collect_tokens("# hello")
        comments = [t for t in tokens if t.toktype == TT.COMMENT]
        assert len(comments) == 1
        assert comments[0].tokstr == "# hello"


class TestTokenizerLabels:
    def test_label(self):
        """name: produces LABEL + COLON"""
        tok = make_tokenizer("name:")
        t = tok.token()
        assert t.toktype == TT.LABEL
        assert t.tokstr == "name"

    def test_label_pre(self):
        """:name produces COLON + LABELPRE"""
        tok = make_tokenizer(":name")
        t1 = tok.token()
        assert t1.toktype == TT.COLON
        t2 = tok.token()
        assert t2.toktype == TT.LABELPRE
        assert t2.tokstr == "name"


class TestTokenizerPunctuation:
    def test_braces(self):
        tokens = collect_tokens("{}")
        types = [t.toktype for t in tokens]
        assert TT.BRACEOPEN in types
        assert TT.BRACECLOSE in types

    def test_parens(self):
        tokens = collect_tokens("()")
        types = [t.toktype for t in tokens]
        assert TT.PARENOPEN in types
        assert TT.PARENCLOSE in types

    def test_semicolon(self):
        tok = make_tokenizer(";")
        t = tok.token()
        assert t.toktype == TT.SEMICOLON

    def test_single_dot(self):
        tok = make_tokenizer(".x")
        t = tok.token()
        assert t.toktype == TT.DOT

    def test_ellipsis(self):
        tok = make_tokenizer("...")
        t = tok.token()
        assert t.toktype == TT.DOTDOTDOT

    def test_double_dot_error(self):
        tok = make_tokenizer("..x")
        t = tok.token()
        assert t.toktype == TT.ERR


class TestLexerFiltering:
    def test_comments_filtered(self):
        """Lexer filters out comments"""
        tokens = collect_lexer_tokens("foo # comment\nbar")
        types = [t.toktype for t in tokens]
        assert TT.COMMENT not in types

    def test_whitespace_filtered(self):
        """Lexer filters whitespace"""
        tokens = collect_lexer_tokens("foo  bar")
        types = [t.toktype for t in tokens]
        assert TT.WS not in types

    def test_eol_filtered_by_default(self):
        """Lexer filters EOLs by default"""
        tokens = collect_lexer_tokens("foo\nbar")
        types = [t.toktype for t in tokens]
        assert TT.EOL not in types

    def test_eol_passed_when_unfiltered(self):
        """Lexer passes EOL when filtereol is False"""
        lex = make_lexer("foo\nbar")
        lex.filtereol(False)
        tokens = []
        while True:
            t = lex.acceptany()
            tokens.append(t)
            if t.toktype == TT.EOF:
                break
        types = [t.toktype for t in tokens]
        assert TT.EOL in types

    def test_colon_filtered(self):
        """Lexer filters standalone colons"""
        tokens = collect_lexer_tokens(":name")
        types = [t.toktype for t in tokens]
        assert TT.COLON not in types

    def test_labelpre_expanded(self):
        """:name expands to LABEL + REFID in lexer"""
        tokens = collect_lexer_tokens(":name")
        types = [t.toktype for t in tokens]
        assert TT.LABEL in types
        assert TT.REFID in types


class TestIsValidUnitName:
    def test_valid_simple(self):
        assert isvalidunitname("hello") is True

    def test_valid_with_numbers(self):
        assert isvalidunitname("test123") is True

    def test_valid_with_underscore(self):
        assert isvalidunitname("my_unit") is True

    def test_invalid_uppercase(self):
        assert isvalidunitname("Hello") is False

    def test_invalid_starts_with_number(self):
        assert isvalidunitname("123test") is False

    def test_invalid_starts_with_underscore(self):
        assert isvalidunitname("_test") is False

    def test_invalid_special_chars(self):
        assert isvalidunitname("test-unit") is False

    def test_empty_string(self):
        # empty string has no characters so the loop doesn't run; returns True
        # (valid unit names require at least one char starting with lowercase)
        # This is a known quirk - empty string passes validation
        assert isvalidunitname("") is True
