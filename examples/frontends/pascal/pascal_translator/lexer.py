"""Hand-written Pascal lexer (M1 subset).

Tokens M1 needs:
  - keywords: program, begin, end
  - identifiers (case-insensitive, lowered to canonical form)
  - string literals: 'text', '' for embedded apostrophe
  - punctuation: ; . ( ) ,
  - both comment styles: { ... } and (* ... *)
  - directives `{$...}` lexed as comment in v1 (ignored)

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
    SEMI = "SEMI"
    DOT = "DOT"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COMMA = "COMMA"
    EOF = "EOF"


KEYWORDS = frozenset({"program", "begin", "end"})


@dataclass
class Token:
    kind: TokKind
    text: str          # canonical (lowered for IDENT/KEYWORD; original for STR_LIT)
    line: int
    col: int
    end_line: int
    end_col: int


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
            tokens.append(Token(TokKind.DOT, ".", sl, sc, line, col - 1))
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
        # String literal: 'text' with '' for embedded apostrophe
        if ch == "'":
            sl, sc = line, col
            adv()
            buf: list[str] = []
            while True:
                if i >= n:
                    raise LexError("unterminated string literal", sl, sc)
                c = source[i]
                if c == "\n":
                    raise LexError("newline in string literal", line, col)
                if c == "'":
                    # Check for doubled apostrophe (escape).
                    if i + 1 < n and source[i + 1] == "'":
                        buf.append("'")
                        adv(); adv()
                        continue
                    adv()  # closing quote
                    break
                buf.append(c)
                adv()
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
