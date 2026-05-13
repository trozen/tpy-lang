"""Recursive-descent Pascal parser.

Grammar handled so far (subset of Turbo Pascal):

    Program       ::= 'program' Ident ';' VarBlock? Block '.'
    VarBlock      ::= 'var' (VarDecl ';')+
    VarDecl       ::= IdentList ':' TypeName
    IdentList     ::= Ident (',' Ident)*
    TypeName      ::= 'integer'                          -- expand later
    Block         ::= 'begin' StmtSeq 'end'
    StmtSeq       ::= Stmt (';' Stmt)*                   -- trailing ';' allowed
    Stmt          ::= AssignStmt | CallStmt | (empty)
    AssignStmt    ::= Ident ':=' Expr
    CallStmt      ::= Ident '(' Args? ')'
    Args          ::= Expr (',' Expr)*

Expression precedence (lowest -> highest):
    AddExpr       ::= MulExpr (('+' | '-') MulExpr)*
    MulExpr       ::= UnaryExpr (('*' | '/' | 'div' | 'mod') UnaryExpr)*
    UnaryExpr     ::= ('+' | '-')? Primary
    Primary       ::= IntLit | StrLit | Ident | '(' Expr ')'
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

    def _peek(self, offset: int = 1) -> Token:
        idx = self.i + offset
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

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

    # ------------------------------------------------------------------
    # Program / declarations

    def parse_program(self) -> pa.Program:
        start = self._eat(TokKind.KEYWORD, "program")
        name_tok = self._eat(TokKind.IDENT)
        self._eat(TokKind.SEMI)
        var_block = None
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "var":
            var_block = self.parse_var_block()
        block = self.parse_block()
        dot = self._eat(TokKind.DOT)
        if self.cur.kind != TokKind.EOF:
            raise ParseError(
                f"unexpected token after program '.': {self.cur.text!r}",
                self.cur.line, self.cur.col,
            )
        return pa.Program(
            name=name_tok.text,
            var_block=var_block,
            block=block,
            loc=self._loc(start, dot),
            file=self.path,
        )

    def parse_var_block(self) -> pa.VarBlock:
        start = self._eat(TokKind.KEYWORD, "var")
        decls: list = []
        # A `var` section runs until the next non-decl keyword (`begin`).
        while not (self.cur.kind == TokKind.KEYWORD and self.cur.text == "begin"):
            decls.append(self.parse_var_decl())
            self._eat(TokKind.SEMI)
        if not decls:
            t = self.cur
            raise ParseError(
                "'var' section must declare at least one variable",
                start.line, start.col,
            )
        end = decls[-1].loc
        return pa.VarBlock(decls=decls, loc=pa.Loc(
            file=self.path,
            line=start.line, col=start.col,
            end_line=end.end_line, end_col=end.end_col,
        ))

    def parse_var_decl(self) -> pa.VarDecl:
        first = self.cur
        names: list[str] = [self._eat(TokKind.IDENT).text]
        while self.cur.kind == TokKind.COMMA:
            self.i += 1
            names.append(self._eat(TokKind.IDENT).text)
        self._eat(TokKind.COLON)
        type_tok = self._eat(TokKind.KEYWORD)
        # M2 supports a single type spelling. The lexer classifies
        # `integer` as a keyword; widening to a TypeName production
        # arrives with later milestones (real, boolean, FixStr[N], etc.).
        if type_tok.text != "integer":
            raise ParseError(
                f"unsupported type {type_tok.text!r} "
                f"(M2 only supports 'integer')",
                type_tok.line, type_tok.col,
            )
        return pa.VarDecl(
            names=names, type_name=type_tok.text,
            loc=self._loc(first, type_tok),
        )

    # ------------------------------------------------------------------
    # Block / statements

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
        if self.cur.kind != TokKind.IDENT:
            t = self.cur
            raise ParseError(
                f"expected statement, got {t.kind.value} {t.text!r}",
                t.line, t.col,
            )
        ident_tok = self.cur
        ident = pa.Ident(name=ident_tok.text,
                         loc=self._loc(ident_tok, ident_tok))
        self.i += 1
        # `name := expr` or `name(args)` -- bare-identifier statements
        # (parameterless procedure calls) are M3+.
        if self.cur.kind == TokKind.ASSIGN:
            self.i += 1
            value = self.parse_expression()
            end_tok = self.tokens[self.i - 1]
            return pa.AssignStmt(target=ident, value=value,
                                 loc=self._loc(ident_tok, end_tok))
        if self.cur.kind == TokKind.LPAREN:
            self._eat(TokKind.LPAREN)
            args: list = []
            if self.cur.kind != TokKind.RPAREN:
                args.append(self.parse_expression())
                while self.cur.kind == TokKind.COMMA:
                    self.i += 1
                    args.append(self.parse_expression())
            rparen = self._eat(TokKind.RPAREN)
            return pa.CallStmt(callee=ident, args=args,
                               loc=self._loc(ident_tok, rparen))
        t = self.cur
        raise ParseError(
            f"expected ':=' or '(' after identifier, "
            f"got {t.kind.value} {t.text!r}",
            t.line, t.col,
        )

    # ------------------------------------------------------------------
    # Expressions

    def parse_expression(self):
        return self._parse_add()

    def _parse_add(self):
        node = self._parse_mul()
        while self.cur.kind in (TokKind.PLUS, TokKind.MINUS):
            op_tok = self.cur
            self.i += 1
            rhs = self._parse_mul()
            node = pa.BinOp(
                op=op_tok.text, lhs=node, rhs=rhs,
                loc=self._loc_span(node, rhs),
            )
        return node

    def _parse_mul(self):
        node = self._parse_unary()
        while self._cur_is_mul_op():
            op_tok = self.cur
            self.i += 1
            rhs = self._parse_unary()
            node = pa.BinOp(
                op=op_tok.text, lhs=node, rhs=rhs,
                loc=self._loc_span(node, rhs),
            )
        return node

    def _cur_is_mul_op(self) -> bool:
        if self.cur.kind in (TokKind.STAR, TokKind.SLASH):
            return True
        return (
            self.cur.kind == TokKind.KEYWORD
            and self.cur.text in ("div", "mod")
        )

    def _parse_unary(self):
        if self.cur.kind in (TokKind.PLUS, TokKind.MINUS):
            op_tok = self.cur
            self.i += 1
            operand = self._parse_unary()
            return pa.UnaryOp(
                op=op_tok.text, operand=operand,
                loc=self._loc_span(op_tok, operand),
            )
        return self._parse_primary()

    def _parse_primary(self):
        t = self.cur
        if t.kind == TokKind.INT_LIT:
            self.i += 1
            return pa.IntLit(value=int(t.text), loc=self._loc(t, t))
        if t.kind == TokKind.STR_LIT:
            self.i += 1
            return pa.StrLit(value=t.text, loc=self._loc(t, t))
        if t.kind == TokKind.IDENT:
            self.i += 1
            return pa.Ident(name=t.text, loc=self._loc(t, t))
        if t.kind == TokKind.LPAREN:
            self.i += 1
            inner = self.parse_expression()
            self._eat(TokKind.RPAREN)
            return inner
        raise ParseError(
            f"expected expression, got {t.kind.value} {t.text!r}",
            t.line, t.col,
        )

    def _loc_span(self, start_node_or_tok, end_node_or_tok) -> pa.Loc:
        """Span from one AST/token node to another, by source position."""
        s_loc = (start_node_or_tok.loc
                 if hasattr(start_node_or_tok, "loc")
                 else self._loc(start_node_or_tok, start_node_or_tok))
        e_loc = (end_node_or_tok.loc
                 if hasattr(end_node_or_tok, "loc")
                 else self._loc(end_node_or_tok, end_node_or_tok))
        return pa.Loc(
            file=self.path,
            line=s_loc.line, col=s_loc.col,
            end_line=e_loc.end_line, end_col=e_loc.end_col,
        )


def parse(source: str, path: Path) -> pa.Program:
    """Parse a Pascal source file into a Pascal AST."""
    tokens = tokenize(source, path)
    p = _Parser(tokens, path)
    program = p.parse_program()
    # Attach source lines for IR `source_lines` propagation.
    program.source_lines = tuple(source.splitlines())
    return program
