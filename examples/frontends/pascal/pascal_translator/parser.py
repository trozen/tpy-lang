"""Recursive-descent Pascal parser (M1 subset).

M1 grammar:
    Program       ::= 'program' Ident ';' Block '.'
    Block         ::= 'begin' StmtSeq 'end'
    StmtSeq       ::= Stmt (';' Stmt)*    -- trailing ';' allowed
    Stmt          ::= CallStmt | (empty)
    CallStmt      ::= Ident '(' Args? ')'
    Args          ::= Expr (',' Expr)*
    Expr          ::= StrLit              -- M1: string literals only
"""

from __future__ import annotations

from pathlib import Path

from . import ast as pa
from .lexer import Token, TokKind, tokenize


class ParseError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"line {line}:{col}: {message}")
        self.line = line
        self.col = col


class _Parser:
    def __init__(self, tokens: list[Token], path: Path):
        self.tokens = tokens
        self.i = 0
        self.path = path

    @property
    def cur(self) -> Token:
        return self.tokens[self.i]

    def _eat(self, kind: TokKind, text: str | None = None) -> Token:
        t = self.cur
        if t.kind != kind or (text is not None and t.text != text):
            want = f"{kind.value}" + (f" {text!r}" if text else "")
            raise ParseError(
                f"expected {want}, got {t.kind.value} {t.text!r}",
                t.line, t.col,
            )
        self.i += 1
        return t

    def _loc(self, start: Token, end: Token) -> pa.Loc:
        return pa.Loc(
            file=self.path,
            line=start.line, col=start.col,
            end_line=end.end_line, end_col=end.end_col,
        )

    def parse_program(self) -> pa.Program:
        start = self._eat(TokKind.KEYWORD, "program")
        name_tok = self._eat(TokKind.IDENT)
        self._eat(TokKind.SEMI)
        block = self.parse_block()
        dot = self._eat(TokKind.DOT)
        if self.cur.kind != TokKind.EOF:
            raise ParseError(
                f"unexpected token after program '.': {self.cur.text!r}",
                self.cur.line, self.cur.col,
            )
        return pa.Program(
            name=name_tok.text,
            block=block,
            loc=self._loc(start, dot),
            file=self.path,
        )

    def parse_block(self) -> pa.Block:
        begin = self._eat(TokKind.KEYWORD, "begin")
        stmts: list = []
        # Allow an empty block ('begin end') and any number of trailing
        # semicolons between statements.
        while not (self.cur.kind == TokKind.KEYWORD and self.cur.text == "end"):
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
                continue
            stmts.append(self.parse_statement())
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
        end = self._eat(TokKind.KEYWORD, "end")
        return pa.Block(statements=stmts, loc=self._loc(begin, end))

    def parse_statement(self):
        # M1: only the call statement is recognized. Bare identifiers
        # without parentheses (procedure call sans args) are deferred.
        if self.cur.kind != TokKind.IDENT:
            t = self.cur
            raise ParseError(
                f"expected statement, got {t.kind.value} {t.text!r}",
                t.line, t.col,
            )
        ident_tok = self.cur
        self.i += 1
        callee = pa.Ident(name=ident_tok.text,
                          loc=self._loc(ident_tok, ident_tok))
        self._eat(TokKind.LPAREN)
        args: list = []
        if self.cur.kind != TokKind.RPAREN:
            args.append(self.parse_expression())
            while self.cur.kind == TokKind.COMMA:
                self.i += 1
                args.append(self.parse_expression())
        rparen = self._eat(TokKind.RPAREN)
        return pa.CallStmt(callee=callee, args=args,
                           loc=self._loc(ident_tok, rparen))

    def parse_expression(self):
        # M1: only string literals.
        t = self.cur
        if t.kind == TokKind.STR_LIT:
            self.i += 1
            return pa.StrLit(value=t.text, loc=self._loc(t, t))
        raise ParseError(
            f"expected expression, got {t.kind.value} {t.text!r}",
            t.line, t.col,
        )


def parse(source: str, path: Path) -> pa.Program:
    """Parse a Pascal source file into a Pascal AST."""
    tokens = tokenize(source, path)
    p = _Parser(tokens, path)
    program = p.parse_program()
    # Attach source lines for IR `source_lines` propagation.
    program.source_lines = tuple(source.splitlines())
    return program
