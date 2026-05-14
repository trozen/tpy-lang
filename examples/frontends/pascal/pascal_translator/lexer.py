"""Hand-written Pascal lexer.

Token surface grows milestone-by-milestone. Current set:
  - keywords: program, begin, end, var, integer, div, mod
  - identifiers (case-insensitive, lowered to canonical form)
  - string literals: 'text', '' for embedded apostrophe
  - integer literals (decimal)
  - punctuation: ; . , : := ( ) + - * /
  - both comment styles: { ... } and (* ... *)
  - directives `{$...}` lexed as comment (ignored)

EOF token terminates the stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class TokKind(Enum):
    IDENT = "IDENT"
    KEYWORD = "KEYWORD"
    STR_LIT = "STR_LIT"
    INT_LIT = "INT_LIT"
    FLOAT_LIT = "FLOAT_LIT"
    SEMI = "SEMI"
    DOT = "DOT"
    COLON = "COLON"
    ASSIGN = "ASSIGN"        # :=
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COMMA = "COMMA"
    PLUS = "PLUS"
    MINUS = "MINUS"
    STAR = "STAR"
    SLASH = "SLASH"
    EQ = "EQ"                # =
    NE = "NE"                # <>
    LT = "LT"                # <
    LE = "LE"                # <=
    GT = "GT"                # >
    GE = "GE"                # >=
    LBRACK = "LBRACK"        # [
    RBRACK = "RBRACK"        # ]
    DOTDOT = "DOTDOT"        # ..  (range separator in array bounds and case ranges)
    EOF = "EOF"


# Pascal keywords. Type names (`integer`, `boolean`) and word-spelled
# operators (`div`, `mod`, `and`, `or`, `not`, `xor`) are treated as
# keywords so the parser can distinguish them from user identifiers.
# Bool literals (`true`, `false`) are also keywords because their
# spelling collides with the identifier syntax.
KEYWORDS = frozenset({
    "program", "begin", "end",
    "const", "var", "integer", "boolean", "string", "char", "real",
    "div", "mod",
    "and", "or", "not", "xor", "in",
    "true", "false",
    "if", "then", "else",
    "while", "do",
    "for", "to", "downto",
    "repeat", "until",
    "case", "of",
    "procedure", "function",
    "type", "record", "array", "of",
    "unit", "uses", "interface", "implementation", "initialization",
})


@dataclass
class Token:
    kind: TokKind
    text: str          # canonical (lowered for IDENT/KEYWORD; original for STR_LIT)
    line: int
    col: int
    end_line: int
    end_col: int


def _is_hex_digit(ch: str) -> bool:
    return ("0" <= ch <= "9") or ("a" <= ch <= "f") or ("A" <= ch <= "F")


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"line {line}:{col}: {message}")
        self.line = line
        self.col = col


def tokenize(source: str, path: Path) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    col = 1
    n = len(source)

    def adv() -> str:
        nonlocal i, line, col
        ch = source[i]
        i += 1
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
        return ch

    while i < n:
        ch = source[i]
        # Skip whitespace
        if ch in " \t\r\n":
            adv()
            continue
        # Comment: { ... }
        if ch == "{":
            adv()
            while i < n and source[i] != "}":
                adv()
            if i >= n:
                raise LexError("unterminated '{' comment", line, col)
            adv()  # consume '}'
            continue
        # Comment: (* ... *)
        if ch == "(" and i + 1 < n and source[i + 1] == "*":
            adv(); adv()
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == ")"):
                adv()
            if i + 1 >= n:
                raise LexError("unterminated '(*' comment", line, col)
            adv(); adv()  # consume '*)'
            continue
        # Punctuation
        if ch == ";":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.SEMI, ";", sl, sc, line, col - 1))
            continue
        if ch == ".":
            sl, sc = line, col
            adv()
            if i < n and source[i] == ".":
                adv()
                tokens.append(Token(TokKind.DOTDOT, "..", sl, sc, line, col - 1))
            else:
                tokens.append(Token(TokKind.DOT, ".", sl, sc, line, col - 1))
            continue
        if ch == "[":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.LBRACK, "[", sl, sc, line, col - 1))
            continue
        if ch == "]":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.RBRACK, "]", sl, sc, line, col - 1))
            continue
        if ch == "(":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.LPAREN, "(", sl, sc, line, col - 1))
            continue
        if ch == ")":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.RPAREN, ")", sl, sc, line, col - 1))
            continue
        if ch == ",":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.COMMA, ",", sl, sc, line, col - 1))
            continue
        # `:` or `:=` -- `:=` is Pascal's assignment, `:` is the type
        # separator in var / function-param declarations.
        if ch == ":":
            sl, sc = line, col
            adv()
            if i < n and source[i] == "=":
                adv()
                tokens.append(Token(TokKind.ASSIGN, ":=", sl, sc, line, col - 1))
            else:
                tokens.append(Token(TokKind.COLON, ":", sl, sc, line, col - 1))
            continue
        if ch == "+":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.PLUS, "+", sl, sc, line, col - 1))
            continue
        if ch == "-":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.MINUS, "-", sl, sc, line, col - 1))
            continue
        if ch == "*":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.STAR, "*", sl, sc, line, col - 1))
            continue
        if ch == "/":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.SLASH, "/", sl, sc, line, col - 1))
            continue
        if ch == "=":
            sl, sc = line, col
            adv()
            tokens.append(Token(TokKind.EQ, "=", sl, sc, line, col - 1))
            continue
        if ch == "<":
            sl, sc = line, col
            adv()
            if i < n and source[i] == "=":
                adv()
                tokens.append(Token(TokKind.LE, "<=", sl, sc, line, col - 1))
            elif i < n and source[i] == ">":
                adv()
                tokens.append(Token(TokKind.NE, "<>", sl, sc, line, col - 1))
            else:
                tokens.append(Token(TokKind.LT, "<", sl, sc, line, col - 1))
            continue
        if ch == ">":
            sl, sc = line, col
            adv()
            if i < n and source[i] == "=":
                adv()
                tokens.append(Token(TokKind.GE, ">=", sl, sc, line, col - 1))
            else:
                tokens.append(Token(TokKind.GT, ">", sl, sc, line, col - 1))
            continue
        # Numeric literal (decimal). Float form: integer-part `.` digits
        # with an optional exponent (`e`/`E`, optional sign, digits).
        # Hex / octal arrive in later milestones.
        if ch.isdigit():
            sl, sc = line, col
            start = i
            while i < n and source[i].isdigit():
                adv()
            is_float = False
            # `.` followed by a digit is a fractional part. A `..` (the
            # range-bounds separator in `array[1..N]`) is not, so we
            # peek two characters and bail when the next-but-one is
            # itself `.`.
            if (i < n and source[i] == "."
                    and not (i + 1 < n and source[i + 1] == ".")):
                is_float = True
                adv()  # consume '.'
                while i < n and source[i].isdigit():
                    adv()
            if i < n and source[i] in ("e", "E"):
                is_float = True
                adv()
                if i < n and source[i] in ("+", "-"):
                    adv()
                if not (i < n and source[i].isdigit()):
                    raise LexError("malformed numeric literal: "
                                   "exponent has no digits", sl, sc)
                while i < n and source[i].isdigit():
                    adv()
            kind = TokKind.FLOAT_LIT if is_float else TokKind.INT_LIT
            tokens.append(Token(kind, source[start:i], sl, sc, line, col - 1))
            continue
        # Hex integer literal: $FF, $1aB. Decimal digits in a hex
        # literal are also accepted ($08).
        if ch == "$":
            sl, sc = line, col
            adv()
            if not (i < n and _is_hex_digit(source[i])):
                raise LexError("hex literal '$' must be followed by "
                               "hex digits", sl, sc)
            start = i
            while i < n and _is_hex_digit(source[i]):
                adv()
            value = int(source[start:i], 16)
            tokens.append(Token(TokKind.INT_LIT, str(value),
                                sl, sc, line, col - 1))
            continue
        # String literal -- 'text' with '' for embedded apostrophes.
        # Pascal allows `'hello'#13#10'world'` to be a single string;
        # we glue adjacent `'...'` and `#nn` fragments together at
        # lex time and emit one STR_LIT. A leading `#nn` (no quote)
        # is also a string-fragment start, so the same loop body
        # handles both entry points.
        if ch == "'" or ch == "#":
            sl, sc = line, col
            buf: list[str] = []
            while True:
                cur = source[i] if i < n else ""
                if cur == "'":
                    adv()
                    while True:
                        if i >= n:
                            raise LexError("unterminated string literal",
                                           sl, sc)
                        c = source[i]
                        if c == "\n":
                            raise LexError("newline in string literal",
                                           line, col)
                        if c == "'":
                            if i + 1 < n and source[i + 1] == "'":
                                buf.append("'")
                                adv(); adv()
                                continue
                            adv()
                            break
                        buf.append(c)
                        adv()
                elif cur == "#":
                    adv()
                    if not (i < n and source[i].isdigit()):
                        raise LexError("'#' must be followed by "
                                       "decimal digits (control-char "
                                       "code)", sl, sc)
                    start = i
                    while i < n and source[i].isdigit():
                        adv()
                    code = int(source[start:i])
                    if code < 0 or code > 255:
                        raise LexError(
                            f"#{code} out of range (0..255)", sl, sc)
                    buf.append(chr(code))
                else:
                    break
            tokens.append(Token(TokKind.STR_LIT, "".join(buf),
                                sl, sc, line, col - 1))
            continue
        # Identifier or keyword
        if ch.isalpha() or ch == "_":
            sl, sc = line, col
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                adv()
            text = source[start:i].lower()
            kind = TokKind.KEYWORD if text in KEYWORDS else TokKind.IDENT
            tokens.append(Token(kind, text, sl, sc, line, col - 1))
            continue
        raise LexError(f"unexpected character {ch!r}", line, col)

    tokens.append(Token(TokKind.EOF, "", line, col, line, col))
    return tokens
