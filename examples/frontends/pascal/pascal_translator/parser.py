"""Recursive-descent Pascal parser.

Grammar handled so far (subset of Turbo Pascal):

    Program       ::= 'program' Ident ';' Decls CompoundStmt '.'
    Decls         ::= (TypeBlock | VarBlock | ProcDecl | FuncDecl)*
    TypeBlock     ::= 'type' (TypeDecl ';')+
    TypeDecl      ::= Ident '=' TypeSpec
    TypeSpec      ::= NamedTypeSpec | RecordTypeSpec | ArrayTypeSpec
    NamedTypeSpec ::= Ident                        -- 'integer', 'Point', ...
    RecordTypeSpec::= 'record' FieldList? 'end'
    FieldList     ::= FieldGroup (';' FieldGroup)* ';'?
    FieldGroup    ::= IdentList ':' TypeSpec
    ArrayTypeSpec ::= 'array' '[' IntLit '..' IntLit ']' 'of' TypeSpec
    ProcDecl      ::= 'procedure' Ident ParamList? ';' RoutineBody ';'
    FuncDecl      ::= 'function' Ident ParamList? ':' NamedTypeSpec ';'
                      RoutineBody ';'
    RoutineBody   ::= VarBlock? CompoundStmt
    ParamList     ::= '(' ParamGroup (';' ParamGroup)* ')'
    ParamGroup    ::= 'var'? IdentList ':' NamedTypeSpec
    VarBlock      ::= 'var' (VarDecl ';')+
    VarDecl       ::= IdentList ':' TypeSpec
    IdentList     ::= Ident (',' Ident)*
    CompoundStmt  ::= 'begin' StmtSeq 'end'
    StmtSeq       ::= Stmt (';' Stmt)*                   -- trailing ';' allowed
    Stmt          ::= AssignStmt | CallStmt | CompoundStmt
                    | IfStmt | WhileStmt | ForStmt | RepeatStmt | CaseStmt
                    | (empty)
    AssignStmt    ::= Ident ':=' Expr
    CallStmt      ::= Ident '(' Args? ')'
    Args          ::= Expr (',' Expr)*
    IfStmt        ::= 'if' Expr 'then' Stmt ('else' Stmt)?
    WhileStmt     ::= 'while' Expr 'do' Stmt
    ForStmt       ::= 'for' Ident ':=' Expr ('to' | 'downto') Expr 'do' Stmt
    RepeatStmt    ::= 'repeat' StmtSeq 'until' Expr
    CaseStmt      ::= 'case' Expr 'of' CaseArm (';' CaseArm)*
                      (';' 'else' Stmt)? ';'? 'end'
    CaseArm       ::= Expr (',' Expr)* ':' Stmt

Expression precedence (lowest -> highest, matching Pascal's
standard precedence: logical < comparison < additive < multiplicative
< unary < primary):

    OrExpr        ::= AndExpr (('or' | 'xor') AndExpr)*
    AndExpr       ::= NotExpr ('and' NotExpr)*
    NotExpr       ::= 'not' NotExpr | CmpExpr
    CmpExpr       ::= AddExpr (CmpOp AddExpr)?
                      -- Pascal does NOT chain (a < b < c is a syntax error)
    CmpOp         ::= '=' | '<>' | '<' | '<=' | '>' | '>='
    AddExpr       ::= MulExpr (('+' | '-') MulExpr)*
    MulExpr       ::= UnaryExpr (('*' | '/' | 'div' | 'mod') UnaryExpr)*
    UnaryExpr     ::= ('+' | '-')? Primary
    Primary       ::= IntLit | StrLit | BoolLit | Ident | '(' Expr ')'
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
        # Pascal allows const / type / var / procedure / function decls
        # in any order before the main block. Standard Pascal has at
        # most one module-level var section; emitting an error on
        # duplicates surfaces a likely typo without complicating the
        # AST.
        type_blocks: list = []
        const_blocks: list = []
        var_block: pa.VarBlock | None = None
        subroutines: list = []
        while True:
            if self.cur.kind == TokKind.KEYWORD and self.cur.text == "type":
                type_blocks.append(self.parse_type_block())
                continue
            if self.cur.kind == TokKind.KEYWORD and self.cur.text == "const":
                const_blocks.append(self.parse_const_block())
                continue
            if self.cur.kind == TokKind.KEYWORD and self.cur.text == "var":
                if var_block is not None:
                    t = self.cur
                    raise ParseError(
                        "duplicate module-level 'var' section",
                        t.line, t.col,
                    )
                var_block = self.parse_var_block()
                continue
            if self.cur.kind == TokKind.KEYWORD and self.cur.text in (
                    "procedure", "function"):
                subroutines.append(self.parse_subroutine())
                continue
            break
        block = self.parse_block()
        dot = self._eat(TokKind.DOT)
        if self.cur.kind != TokKind.EOF:
            raise ParseError(
                f"unexpected token after program '.': {self.cur.text!r}",
                self.cur.line, self.cur.col,
            )
        return pa.Program(
            name=name_tok.text,
            type_blocks=type_blocks,
            const_blocks=const_blocks,
            var_block=var_block,
            subroutines=subroutines,
            block=block,
            loc=self._loc(start, dot),
            file=self.path,
        )

    # ------------------------------------------------------------------
    # Const declarations

    def parse_const_block(self) -> pa.ConstBlock:
        start = self._eat(TokKind.KEYWORD, "const")
        decls: list = []
        while self.cur.kind == TokKind.IDENT:
            decls.append(self.parse_const_decl())
            self._eat(TokKind.SEMI)
        if not decls:
            raise ParseError(
                "'const' section must declare at least one constant",
                start.line, start.col,
            )
        end = decls[-1].loc
        return pa.ConstBlock(decls=decls, loc=pa.Loc(
            file=self.path,
            line=start.line, col=start.col,
            end_line=end.end_line, end_col=end.end_col,
        ))

    def parse_const_decl(self) -> pa.ConstDecl:
        name_tok = self._eat(TokKind.IDENT)
        self._eat(TokKind.EQ)
        # M10 accepts any expression; the translator validates that it
        # evaluates to a literal at lower time. Optional `: Type` type
        # annotation (TP7-style typed constants) is future work.
        value = self.parse_expression()
        end_tok = self.tokens[self.i - 1]
        return pa.ConstDecl(
            name=name_tok.text, value=value,
            loc=self._loc(name_tok, end_tok),
        )

    # ------------------------------------------------------------------
    # Type declarations

    def parse_type_block(self) -> pa.TypeBlock:
        start = self._eat(TokKind.KEYWORD, "type")
        decls: list = []
        # `type` runs until the next non-decl keyword. We trust the
        # caller (parse_program) to dispatch us only when one is
        # actually starting, so we read at least one decl.
        while self.cur.kind == TokKind.IDENT:
            decls.append(self.parse_type_decl())
            self._eat(TokKind.SEMI)
        if not decls:
            t = self.cur
            raise ParseError(
                "'type' section must declare at least one type",
                start.line, start.col,
            )
        end = decls[-1].loc
        return pa.TypeBlock(decls=decls, loc=pa.Loc(
            file=self.path,
            line=start.line, col=start.col,
            end_line=end.end_line, end_col=end.end_col,
        ))

    def parse_type_decl(self) -> pa.TypeDecl:
        name_tok = self._eat(TokKind.IDENT)
        self._eat(TokKind.EQ)
        spec = self.parse_type_spec()
        end_tok = self.tokens[self.i - 1]
        return pa.TypeDecl(
            name=name_tok.text, type_spec=spec,
            loc=self._loc(name_tok, end_tok),
        )

    def parse_type_spec(self):
        """Parse a type specification. Forms recognised so far: bare
        named type, `record ... end`, `array[lo..hi] of T`, the two
        string forms (`string` and `string[N]`), an enumeration body
        `(Ident, Ident, ...)`, and a subrange `lo..hi`."""
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "record":
            return self.parse_record_type()
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "array":
            return self.parse_array_type()
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "string":
            return self.parse_string_type()
        if self.cur.kind == TokKind.LPAREN:
            return self.parse_enum_type()
        # Subrange shape: a signed int literal followed by `..` and
        # another int literal -- only recognised at the start of a
        # type spec to avoid confusion with array bounds (where the
        # `..` is consumed by the surrounding `array[...]` grammar).
        if self._looks_like_subrange():
            return self.parse_subrange_type()
        # Named type: either a scalar keyword (`integer`, `boolean`,
        # `char`, `real`, `double`) or a user-defined ident.
        t = self.cur
        if t.kind == TokKind.KEYWORD and t.text in (
                "integer", "boolean", "char", "real", "double"):
            self.i += 1
            return pa.NamedTypeSpec(name=t.text, loc=self._loc(t, t))
        if t.kind == TokKind.IDENT:
            self.i += 1
            return pa.NamedTypeSpec(name=t.text, loc=self._loc(t, t))
        raise ParseError(
            f"expected type, got {t.kind.value} {t.text!r}",
            t.line, t.col,
        )

    def parse_enum_type(self) -> pa.EnumTypeSpec:
        start = self._eat(TokKind.LPAREN)
        members: list[str] = []
        if self.cur.kind != TokKind.RPAREN:
            members.append(self._eat(TokKind.IDENT).text)
            while self.cur.kind == TokKind.COMMA:
                self.i += 1
                members.append(self._eat(TokKind.IDENT).text)
        end = self._eat(TokKind.RPAREN)
        if not members:
            raise ParseError(
                "enum type must declare at least one member",
                start.line, start.col,
            )
        return pa.EnumTypeSpec(members=members, loc=self._loc(start, end))

    def _looks_like_subrange(self) -> bool:
        """True iff the upcoming tokens form `INT_LIT '..' INT_LIT` --
        the subrange shape allowed at the start of a type spec.
        Handles optional unary `+`/`-` on either bound."""
        i = self.i
        # Skip optional sign + int literal for the lower bound.
        if self.tokens[i].kind in (TokKind.PLUS, TokKind.MINUS):
            i += 1
        if i >= len(self.tokens) or self.tokens[i].kind != TokKind.INT_LIT:
            return False
        i += 1
        return (i < len(self.tokens)
                and self.tokens[i].kind == TokKind.DOTDOT)

    def parse_subrange_type(self) -> pa.SubrangeTypeSpec:
        first = self.cur
        lower = self._parse_signed_int_lit()
        self._eat(TokKind.DOTDOT)
        upper = self._parse_signed_int_lit()
        end_tok = self.tokens[self.i - 1]
        if upper < lower:
            raise ParseError(
                f"subrange upper bound ({upper}) less than "
                f"lower bound ({lower})",
                first.line, first.col,
            )
        return pa.SubrangeTypeSpec(
            lower=lower, upper=upper,
            loc=self._loc(first, end_tok),
        )

    def parse_string_type(self) -> pa.StringTypeSpec:
        """`string` (default capacity 255) or `string[N]` (custom)."""
        start = self._eat(TokKind.KEYWORD, "string")
        capacity = 255
        if self.cur.kind == TokKind.LBRACK:
            self.i += 1
            capacity = self._parse_signed_int_lit()
            self._eat(TokKind.RBRACK)
        end_tok = self.tokens[self.i - 1]
        if capacity < 1:
            raise ParseError(
                f"string capacity must be positive (got {capacity})",
                start.line, start.col,
            )
        return pa.StringTypeSpec(
            capacity=capacity, loc=self._loc(start, end_tok),
        )

    def parse_record_type(self) -> pa.RecordTypeSpec:
        start = self._eat(TokKind.KEYWORD, "record")
        fields: list = []
        while not (self.cur.kind == TokKind.KEYWORD
                   and self.cur.text == "end"):
            fields.append(self._parse_record_field_group())
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
        end = self._eat(TokKind.KEYWORD, "end")
        return pa.RecordTypeSpec(fields=fields, loc=self._loc(start, end))

    def _parse_record_field_group(self) -> pa.RecordField:
        first = self.cur
        names: list[str] = [self._eat(TokKind.IDENT).text]
        while self.cur.kind == TokKind.COMMA:
            self.i += 1
            names.append(self._eat(TokKind.IDENT).text)
        self._eat(TokKind.COLON)
        spec = self.parse_type_spec()
        end_tok = self.tokens[self.i - 1]
        return pa.RecordField(
            names=names, type_spec=spec,
            loc=self._loc(first, end_tok),
        )

    def parse_array_type(self) -> pa.ArrayTypeSpec:
        start = self._eat(TokKind.KEYWORD, "array")
        self._eat(TokKind.LBRACK)
        lower = self._parse_signed_int_lit()
        self._eat(TokKind.DOTDOT)
        upper = self._parse_signed_int_lit()
        self._eat(TokKind.RBRACK)
        self._eat(TokKind.KEYWORD, "of")
        element = self.parse_type_spec()
        end_tok = self.tokens[self.i - 1]
        if upper < lower:
            raise ParseError(
                f"array upper bound ({upper}) less than lower bound ({lower})",
                start.line, start.col,
            )
        return pa.ArrayTypeSpec(
            lower=lower, upper=upper, element=element,
            loc=self._loc(start, end_tok),
        )

    def _parse_signed_int_lit(self) -> int:
        """Parse a (possibly signed) integer literal -- array bounds
        can be negative (`array[-5..5]`)."""
        sign = 1
        if self.cur.kind == TokKind.MINUS:
            sign = -1
            self.i += 1
        elif self.cur.kind == TokKind.PLUS:
            self.i += 1
        tok = self._eat(TokKind.INT_LIT)
        return sign * int(tok.text)

    # ------------------------------------------------------------------
    # Procedure / function declarations

    def parse_subroutine(self) -> pa.SubroutineDecl:
        kw_tok = self._eat(TokKind.KEYWORD)
        assert kw_tok.text in ("procedure", "function")
        is_function = kw_tok.text == "function"
        name_tok = self._eat(TokKind.IDENT)
        params: list = []
        if self.cur.kind == TokKind.LPAREN:
            params = self.parse_param_list()
        return_type: str | None = None
        if is_function:
            self._eat(TokKind.COLON)
            ret_tok = self.cur
            if ret_tok.kind == TokKind.KEYWORD:
                if ret_tok.text not in (
                        "integer", "boolean", "real", "double"):
                    raise ParseError(
                        f"unsupported function return type {ret_tok.text!r}",
                        ret_tok.line, ret_tok.col,
                    )
                self.i += 1
            elif ret_tok.kind == TokKind.IDENT:
                self.i += 1
            else:
                raise ParseError(
                    f"expected function return type, got "
                    f"{ret_tok.kind.value} {ret_tok.text!r}",
                    ret_tok.line, ret_tok.col,
                )
            return_type = ret_tok.text
        self._eat(TokKind.SEMI)
        # Routine body: any number of `const` and at most one `var`
        # section (in either order), then the compound stmt.
        local_const_blocks: list = []
        local_var_block = None
        while True:
            if (self.cur.kind == TokKind.KEYWORD
                    and self.cur.text == "const"):
                local_const_blocks.append(self.parse_const_block())
                continue
            if (self.cur.kind == TokKind.KEYWORD
                    and self.cur.text == "var"):
                if local_var_block is not None:
                    t = self.cur
                    raise ParseError(
                        f"duplicate 'var' section in routine "
                        f"{name_tok.text!r}",
                        t.line, t.col,
                    )
                local_var_block = self.parse_var_block()
                continue
            break
        body = self.parse_compound_stmt()
        self._eat(TokKind.SEMI)
        return pa.SubroutineDecl(
            name=name_tok.text,
            params=params,
            return_type=return_type,
            const_blocks=local_const_blocks,
            var_block=local_var_block,
            body=body,
            loc=self._loc(kw_tok, self.tokens[self.i - 1]),
        )

    def parse_param_list(self) -> list:
        """Parse a parenthesised list of parameter groups.

        Pascal allows `(var a, b: integer; c: integer)` -- groups
        share a `var` modifier (or its absence) and a type. The parser
        flattens these into one `Param` per name.
        """
        self._eat(TokKind.LPAREN)
        params: list = []
        if self.cur.kind != TokKind.RPAREN:
            params.extend(self._parse_param_group())
            while self.cur.kind == TokKind.SEMI:
                self.i += 1
                params.extend(self._parse_param_group())
        self._eat(TokKind.RPAREN)
        return params

    def _parse_param_group(self) -> list:
        is_var = False
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "var":
            is_var = True
            self.i += 1
        first = self.cur
        names: list[str] = [self._eat(TokKind.IDENT).text]
        while self.cur.kind == TokKind.COMMA:
            self.i += 1
            names.append(self._eat(TokKind.IDENT).text)
        self._eat(TokKind.COLON)
        # Parameter types are named (no inline `array of ...` form in
        # M5 -- arrays-as-params arrive when the design's
        # auto-pointer-promotion is in place).
        type_tok = self.cur
        if type_tok.kind == TokKind.KEYWORD:
            if type_tok.text not in ("integer", "boolean", "real", "double"):
                raise ParseError(
                    f"unsupported parameter type {type_tok.text!r}",
                    type_tok.line, type_tok.col,
                )
            self.i += 1
        elif type_tok.kind == TokKind.IDENT:
            self.i += 1
        else:
            raise ParseError(
                f"expected parameter type, got {type_tok.kind.value} "
                f"{type_tok.text!r}",
                type_tok.line, type_tok.col,
            )
        spec = pa.NamedTypeSpec(name=type_tok.text,
                                loc=self._loc(type_tok, type_tok))
        loc = self._loc(first, type_tok)
        return [
            pa.Param(name=n, type_spec=spec, is_var=is_var, loc=loc)
            for n in names
        ]

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
        spec = self.parse_type_spec()
        end_tok = self.tokens[self.i - 1]
        return pa.VarDecl(
            names=names, type_spec=spec,
            loc=self._loc(first, end_tok),
        )

    # ------------------------------------------------------------------
    # Block / statements

    def parse_block(self) -> pa.Block:
        """Parse the program-level `begin ... end` body. Differs from
        `parse_compound_stmt` only in the AST type returned -- the
        statement-position form wraps the same payload in `CompoundStmt`.
        """
        begin = self._eat(TokKind.KEYWORD, "begin")
        stmts = self._parse_stmt_seq_until_end()
        end = self._eat(TokKind.KEYWORD, "end")
        return pa.Block(statements=stmts, loc=self._loc(begin, end))

    def _parse_stmt_seq_until_end(self) -> list:
        """Parse a statement sequence terminated by `end` (or by an
        outer caller's sentinel keyword). Consumes interleaved `;`
        separators; trailing `;` before the terminator is allowed.
        """
        stmts: list = []
        while not (self.cur.kind == TokKind.KEYWORD
                   and self.cur.text in self._STMT_SEQ_TERMINATORS):
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
                continue
            stmts.append(self.parse_statement())
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
        return stmts

    _STMT_SEQ_TERMINATORS = frozenset({"end", "until", "else"})

    def parse_compound_stmt(self) -> pa.CompoundStmt:
        begin = self._eat(TokKind.KEYWORD, "begin")
        stmts = self._parse_stmt_seq_until_end()
        end = self._eat(TokKind.KEYWORD, "end")
        return pa.CompoundStmt(statements=stmts, loc=self._loc(begin, end))

    def parse_statement(self):
        # Compound statement
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "begin":
            return self.parse_compound_stmt()
        # Control-flow keywords
        if self.cur.kind == TokKind.KEYWORD:
            kw = self.cur.text
            if kw == "if":
                return self.parse_if_stmt()
            if kw == "while":
                return self.parse_while_stmt()
            if kw == "for":
                return self.parse_for_stmt()
            if kw == "repeat":
                return self.parse_repeat_stmt()
            if kw == "case":
                return self.parse_case_stmt()
        if self.cur.kind != TokKind.IDENT:
            t = self.cur
            raise ParseError(
                f"expected statement, got {t.kind.value} {t.text!r}",
                t.line, t.col,
            )
        # Parse a "target chain": `name`, `name.field`, `name[i]`,
        # `name.field[i]`, etc. The chain stops before any `(`, since a
        # `(` follows a bare identifier means a procedure call
        # statement, not a call inside an assignment target.
        start_tok = self.cur
        ident_tok = self.cur
        self.i += 1
        target = pa.Ident(name=ident_tok.text,
                          loc=self._loc(ident_tok, ident_tok))
        while self.cur.kind in (TokKind.DOT, TokKind.LBRACK):
            if self.cur.kind == TokKind.DOT:
                self.i += 1
                name_tok = self._eat(TokKind.IDENT)
                target = pa.FieldAccess(
                    target=target, ident=name_tok.text,
                    loc=self._loc(start_tok, name_tok),
                )
            else:
                self._eat(TokKind.LBRACK)
                idx = self.parse_expression()
                rbrack = self._eat(TokKind.RBRACK)
                target = pa.IndexExpr(
                    target=target, index=idx,
                    loc=self._loc(start_tok, rbrack),
                )
        if self.cur.kind == TokKind.ASSIGN:
            self.i += 1
            value = self.parse_expression()
            end_tok = self.tokens[self.i - 1]
            return pa.AssignStmt(target=target, value=value,
                                 loc=self._loc(start_tok, end_tok))
        if self.cur.kind == TokKind.LPAREN and isinstance(target, pa.Ident):
            self._eat(TokKind.LPAREN)
            args: list = []
            if self.cur.kind != TokKind.RPAREN:
                args.append(self.parse_expression())
                while self.cur.kind == TokKind.COMMA:
                    self.i += 1
                    args.append(self.parse_expression())
            rparen = self._eat(TokKind.RPAREN)
            return pa.CallStmt(callee=target, args=args,
                               loc=self._loc(start_tok, rparen))
        # Parameterless call: a bare identifier followed by a
        # statement terminator (`;` or `end`/`else`/`until`) is a
        # procedure call with no args (Pascal allows `randomize;`).
        # Only valid when the target chain is a single Ident -- field
        # / subscript chains have nowhere to apply such a call.
        if isinstance(target, pa.Ident) and (
            self.cur.kind == TokKind.SEMI
            or (self.cur.kind == TokKind.KEYWORD
                and self.cur.text in self._STMT_SEQ_TERMINATORS)
        ):
            return pa.CallStmt(callee=target, args=[],
                               loc=self._loc(start_tok, ident_tok))
        t = self.cur
        raise ParseError(
            f"expected ':=' or '(' after target, "
            f"got {t.kind.value} {t.text!r}",
            t.line, t.col,
        )

    # ------------------------------------------------------------------
    # Control flow

    def parse_if_stmt(self) -> pa.IfStmt:
        start = self._eat(TokKind.KEYWORD, "if")
        cond = self.parse_expression()
        self._eat(TokKind.KEYWORD, "then")
        then_branch = self.parse_statement()
        else_branch = None
        end_tok = self.tokens[self.i - 1]
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "else":
            self.i += 1
            else_branch = self.parse_statement()
            end_tok = self.tokens[self.i - 1]
        return pa.IfStmt(
            cond=cond, then_branch=then_branch, else_branch=else_branch,
            loc=self._loc(start, end_tok),
        )

    def parse_while_stmt(self) -> pa.WhileStmt:
        start = self._eat(TokKind.KEYWORD, "while")
        cond = self.parse_expression()
        self._eat(TokKind.KEYWORD, "do")
        body = self.parse_statement()
        end_tok = self.tokens[self.i - 1]
        return pa.WhileStmt(cond=cond, body=body,
                            loc=self._loc(start, end_tok))

    def parse_for_stmt(self) -> pa.ForStmt:
        start = self._eat(TokKind.KEYWORD, "for")
        var_tok = self._eat(TokKind.IDENT)
        self._eat(TokKind.ASSIGN)
        start_expr = self.parse_expression()
        dir_tok = self._eat(TokKind.KEYWORD)
        if dir_tok.text not in ("to", "downto"):
            raise ParseError(
                f"expected 'to' or 'downto', got {dir_tok.text!r}",
                dir_tok.line, dir_tok.col,
            )
        end_expr = self.parse_expression()
        self._eat(TokKind.KEYWORD, "do")
        body = self.parse_statement()
        end_tok = self.tokens[self.i - 1]
        return pa.ForStmt(
            var=var_tok.text, start=start_expr, end=end_expr,
            direction=dir_tok.text, body=body,
            loc=self._loc(start, end_tok),
        )

    def parse_repeat_stmt(self) -> pa.RepeatStmt:
        start = self._eat(TokKind.KEYWORD, "repeat")
        stmts = self._parse_stmt_seq_until_end()
        # `until` is in the terminator set so _parse_stmt_seq_until_end
        # stops at it; consume it now.
        if not (self.cur.kind == TokKind.KEYWORD and self.cur.text == "until"):
            t = self.cur
            raise ParseError(
                f"expected 'until' to close repeat, got {t.text!r}",
                t.line, t.col,
            )
        self.i += 1
        cond = self.parse_expression()
        end_tok = self.tokens[self.i - 1]
        return pa.RepeatStmt(statements=stmts, cond=cond,
                             loc=self._loc(start, end_tok))

    def parse_case_stmt(self) -> pa.CaseStmt:
        start = self._eat(TokKind.KEYWORD, "case")
        subject = self.parse_expression()
        self._eat(TokKind.KEYWORD, "of")
        arms: list = []
        else_branch = None
        while True:
            if self.cur.kind == TokKind.KEYWORD and self.cur.text == "end":
                break
            if self.cur.kind == TokKind.KEYWORD and self.cur.text == "else":
                self.i += 1
                else_branch = self.parse_statement()
                if self.cur.kind == TokKind.SEMI:
                    self.i += 1
                continue
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
                continue
            arms.append(self._parse_case_arm())
            if self.cur.kind == TokKind.SEMI:
                self.i += 1
        end_tok = self._eat(TokKind.KEYWORD, "end")
        return pa.CaseStmt(
            subject=subject, arms=arms, else_branch=else_branch,
            loc=self._loc(start, end_tok),
        )

    def _parse_case_arm(self) -> pa.CaseArm:
        first_loc = self.cur
        values: list = [self.parse_expression()]
        while self.cur.kind == TokKind.COMMA:
            self.i += 1
            values.append(self.parse_expression())
        self._eat(TokKind.COLON)
        body = self.parse_statement()
        end_tok = self.tokens[self.i - 1]
        return pa.CaseArm(values=values, body=body,
                          loc=self._loc(first_loc, end_tok))

    # ------------------------------------------------------------------
    # Expressions

    def parse_expression(self):
        return self._parse_or()

    def _parse_or(self):
        node = self._parse_and()
        while (self.cur.kind == TokKind.KEYWORD
               and self.cur.text in ("or", "xor")):
            op_tok = self.cur
            self.i += 1
            rhs = self._parse_and()
            node = pa.BinOp(
                op=op_tok.text, lhs=node, rhs=rhs,
                loc=self._loc_span(node, rhs),
            )
        return node

    def _parse_and(self):
        node = self._parse_not()
        while self.cur.kind == TokKind.KEYWORD and self.cur.text == "and":
            op_tok = self.cur
            self.i += 1
            rhs = self._parse_not()
            node = pa.BinOp(
                op=op_tok.text, lhs=node, rhs=rhs,
                loc=self._loc_span(node, rhs),
            )
        return node

    def _parse_not(self):
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "not":
            op_tok = self.cur
            self.i += 1
            operand = self._parse_not()
            return pa.UnaryOp(
                op="not", operand=operand,
                loc=self._loc_span(op_tok, operand),
            )
        return self._parse_cmp()

    def _parse_cmp(self):
        lhs = self._parse_add()
        if self.cur.kind in (TokKind.EQ, TokKind.NE,
                             TokKind.LT, TokKind.LE,
                             TokKind.GT, TokKind.GE):
            op_tok = self.cur
            self.i += 1
            rhs = self._parse_add()
            return pa.BinOp(
                op=op_tok.text, lhs=lhs, rhs=rhs,
                loc=self._loc_span(lhs, rhs),
            )
        # `x in [a, b, c]` -- set-membership test. M10 restricts the
        # RHS to a literal set; full set values arrive in Tier 2.
        if (self.cur.kind == TokKind.KEYWORD
                and self.cur.text == "in"):
            op_tok = self.cur
            self.i += 1
            rhs = self._parse_set_literal_or_expr()
            return pa.BinOp(
                op="in", lhs=lhs, rhs=rhs,
                loc=self._loc_span(lhs, rhs),
            )
        return lhs

    def _parse_set_literal_or_expr(self):
        """The RHS of `in`. M10 supports only the literal set form
        `[ expr, expr, ... ]`; richer forms (range `1..5`, a set-
        typed variable) wait on Tier 2."""
        if self.cur.kind != TokKind.LBRACK:
            t = self.cur
            raise ParseError(
                "expected '[' starting a set literal after 'in'",
                t.line, t.col,
            )
        start = self._eat(TokKind.LBRACK)
        elements: list = []
        if self.cur.kind != TokKind.RBRACK:
            elements.append(self.parse_expression())
            while self.cur.kind == TokKind.COMMA:
                self.i += 1
                elements.append(self.parse_expression())
        end = self._eat(TokKind.RBRACK)
        return pa.SetLit(elements=elements, loc=self._loc(start, end))

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
        return self._parse_postfix()

    def _parse_postfix(self):
        """Parse an atom followed by any number of postfix operators:
        `(args)` (call), `.ident` (field access), `[index]` (subscript).
        Mixing chains -- `f(x).y[i]` -- works naturally."""
        node = self._parse_atom()
        while True:
            if self.cur.kind == TokKind.LPAREN:
                if not isinstance(node, pa.Ident):
                    t = self.cur
                    raise ParseError(
                        "call applied to non-identifier expression "
                        "(method calls land in a later milestone)",
                        t.line, t.col,
                    )
                self.i += 1
                args: list = []
                if self.cur.kind != TokKind.RPAREN:
                    args.append(self.parse_expression())
                    while self.cur.kind == TokKind.COMMA:
                        self.i += 1
                        args.append(self.parse_expression())
                rparen = self._eat(TokKind.RPAREN)
                node = pa.CallExpr(
                    callee=node, args=args,
                    loc=self._loc_span(node, rparen),
                )
                continue
            if self.cur.kind == TokKind.DOT:
                self.i += 1
                name_tok = self._eat(TokKind.IDENT)
                node = pa.FieldAccess(
                    target=node, ident=name_tok.text,
                    loc=self._loc_span(node, name_tok),
                )
                continue
            if self.cur.kind == TokKind.LBRACK:
                self.i += 1
                idx = self.parse_expression()
                rbrack = self._eat(TokKind.RBRACK)
                node = pa.IndexExpr(
                    target=node, index=idx,
                    loc=self._loc_span(node, rbrack),
                )
                continue
            break
        return node

    def _parse_atom(self):
        t = self.cur
        if t.kind == TokKind.INT_LIT:
            self.i += 1
            return pa.IntLit(value=int(t.text), loc=self._loc(t, t))
        if t.kind == TokKind.FLOAT_LIT:
            self.i += 1
            return pa.FloatLit(value=float(t.text), loc=self._loc(t, t))
        if t.kind == TokKind.STR_LIT:
            self.i += 1
            return pa.StrLit(value=t.text, loc=self._loc(t, t))
        if t.kind == TokKind.KEYWORD and t.text in ("true", "false"):
            self.i += 1
            return pa.BoolLit(value=(t.text == "true"),
                              loc=self._loc(t, t))
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
