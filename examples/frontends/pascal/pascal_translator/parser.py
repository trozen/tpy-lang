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
        """Parse a Pascal compilation unit -- either `program X; ...
        end.` or `unit X; interface ... implementation ... end.`.
        Both forms share the decl-block grammar; only the body shape
        differs."""
        head = self.cur
        if head.kind != TokKind.KEYWORD or head.text not in (
                "program", "unit"):
            raise ParseError(
                f"expected 'program' or 'unit' header, got "
                f"{head.kind.value} {head.text!r}",
                head.line, head.col,
            )
        self.i += 1
        kind = head.text
        name_tok = self._eat(TokKind.IDENT)
        self._eat(TokKind.SEMI)
        if kind == "unit":
            return self._parse_unit_body(head, name_tok)
        return self._parse_program_body(head, name_tok)

    def _parse_program_body(self, head, name_tok) -> pa.Program:
        """Parse the decls + `begin ... end.` body of a `program`."""
        uses_clauses, type_blocks, const_blocks, var_block, subroutines = (
            self._parse_decl_blocks()
        )
        block = self.parse_block()
        dot = self._eat(TokKind.DOT)
        if self.cur.kind != TokKind.EOF:
            raise ParseError(
                f"unexpected token after program '.': {self.cur.text!r}",
                self.cur.line, self.cur.col,
            )
        return pa.Program(
            name=name_tok.text, kind="program",
            uses_clauses=uses_clauses,
            type_blocks=type_blocks,
            const_blocks=const_blocks,
            var_block=var_block,
            subroutines=subroutines,
            block=block,
            loc=self._loc(head, dot),
            file=self.path,
        )

    def _parse_unit_body(self, head, name_tok) -> pa.Program:
        """Parse the `interface ... implementation ... [initialization
        ...] end.` body of a `unit`. M11 flattens interface +
        implementation decls into one module scope -- no strict
        export hiding."""
        uses_clauses: list = []
        type_blocks: list = []
        const_blocks: list = []
        var_block: pa.VarBlock | None = None
        subroutines: list = []
        init_block = None
        # `interface` is mandatory in a Pascal unit; `implementation`
        # too. `initialization` (and `finalization`, deferred) are
        # optional.
        self._eat(TokKind.KEYWORD, "interface")
        iface_uses, iface_types, iface_consts, iface_var, iface_subs = (
            self._parse_decl_blocks(signatures_only=True)
        )
        uses_clauses.extend(iface_uses)
        type_blocks.extend(iface_types)
        const_blocks.extend(iface_consts)
        if iface_var is not None:
            var_block = iface_var
        subroutines.extend(iface_subs)
        self._eat(TokKind.KEYWORD, "implementation")
        impl_uses, impl_types, impl_consts, impl_var, impl_subs = (
            self._parse_decl_blocks()
        )
        uses_clauses.extend(impl_uses)
        type_blocks.extend(impl_types)
        const_blocks.extend(impl_consts)
        if impl_var is not None:
            if var_block is not None:
                t = self.cur
                raise ParseError(
                    "duplicate 'var' section across interface and "
                    "implementation",
                    t.line, t.col,
                )
            var_block = impl_var
        subroutines.extend(impl_subs)
        if (self.cur.kind == TokKind.KEYWORD
                and self.cur.text == "initialization"):
            begin_init = self._eat(TokKind.KEYWORD, "initialization")
            stmts = self._parse_stmt_seq_until_end()
            end = self._eat(TokKind.KEYWORD, "end")
            init_block = pa.Block(
                statements=stmts, loc=self._loc(begin_init, end),
            )
        else:
            self._eat(TokKind.KEYWORD, "end")
        dot = self._eat(TokKind.DOT)
        if self.cur.kind != TokKind.EOF:
            raise ParseError(
                f"unexpected token after unit '.': {self.cur.text!r}",
                self.cur.line, self.cur.col,
            )
        return pa.Program(
            name=name_tok.text, kind="unit",
            uses_clauses=uses_clauses,
            type_blocks=type_blocks,
            const_blocks=const_blocks,
            var_block=var_block,
            subroutines=subroutines,
            block=init_block,
            loc=self._loc(head, dot),
            file=self.path,
        )

    def _parse_decl_blocks(self, *, signatures_only: bool = False) -> tuple:
        """Parse zero-or-more decl sections in any order: `uses`,
        `type`, `const`, `var`, `procedure`, `function`. Returns a
        five-tuple of lists / single-or-None values matching the
        Program AST slots.

        `signatures_only=True` is for Pascal's `interface` section --
        subroutines parse as signature-only forward declarations
        (header + `;`, no body). The implementation section parses
        the same names again, with bodies, and the translator emits
        just the implementation-side ones. `var` is rejected in
        signatures-only mode: TP's `interface var` would be a public
        global, which we treat as future work to keep the M11 unit
        surface tight.
        """
        uses: list = []
        type_blocks: list = []
        const_blocks: list = []
        var_block: pa.VarBlock | None = None
        subroutines: list = []
        while True:
            if self.cur.kind != TokKind.KEYWORD:
                break
            kw = self.cur.text
            if kw == "uses":
                uses.extend(self.parse_uses_clause())
                continue
            if kw == "type":
                type_blocks.append(self.parse_type_block())
                continue
            if kw == "const":
                const_blocks.append(self.parse_const_block())
                continue
            if kw == "var":
                if signatures_only:
                    # Interface-section globals would be exported
                    # module variables; M11 leaves them unsupported.
                    t = self.cur
                    raise ParseError(
                        "interface-section `var` declarations are "
                        "not supported in M11",
                        t.line, t.col,
                    )
                if var_block is not None:
                    t = self.cur
                    raise ParseError(
                        "duplicate 'var' section",
                        t.line, t.col,
                    )
                var_block = self.parse_var_block()
                continue
            if kw in ("procedure", "function"):
                if signatures_only:
                    # Skip forward declarations -- the implementation
                    # section will redeclare the same names with the
                    # actual bodies. We still advance past them to
                    # keep parsing flowing.
                    self._skip_subroutine_signature()
                    continue
                subroutines.append(self.parse_subroutine())
                continue
            break
        return uses, type_blocks, const_blocks, var_block, subroutines

    def _skip_subroutine_signature(self) -> None:
        """Consume a `procedure X(...);` or `function X(...): T;`
        forward declaration without saving it. The implementation
        section is the source of truth for the body."""
        self.i += 1  # 'procedure' / 'function'
        self._eat(TokKind.IDENT)
        if self.cur.kind == TokKind.LPAREN:
            depth = 0
            while True:
                if self.cur.kind == TokKind.LPAREN:
                    depth += 1
                elif self.cur.kind == TokKind.RPAREN:
                    depth -= 1
                    self.i += 1
                    if depth == 0:
                        break
                    continue
                self.i += 1
        if self.cur.kind == TokKind.COLON:
            self.i += 1
            # Skip the return-type token (keyword or ident).
            self.i += 1
        self._eat(TokKind.SEMI)

    def parse_uses_clause(self) -> list:
        """Parse `uses Name1, py.X, Name2, ...;` and return the list
        of canonical-form unit names. A bare identifier resolves
        against the user's project (no TPy stdlib lookup); the
        `py.X` form is the explicit escape hatch -- the leading
        `py.` segment is preserved in the returned string so the
        translator can apply the right resolution policy."""
        self._eat(TokKind.KEYWORD, "uses")
        names: list = []
        names.append(self._parse_uses_name())
        while self.cur.kind == TokKind.COMMA:
            self.i += 1
            names.append(self._parse_uses_name())
        self._eat(TokKind.SEMI)
        return names

    def _parse_uses_name(self) -> str:
        """One unit name in a `uses` clause. Accepts either a bare
        identifier (canonical Pascal unit) or `py.X` (escape hatch to
        TPy stdlib / arbitrary Python module). Future dotted Delphi-
        style names (`System.SysUtils`) follow the same shape but
        skip the `py` interpretation."""
        head = self._eat(TokKind.IDENT)
        if self.cur.kind == TokKind.DOT:
            parts = [head.text]
            while self.cur.kind == TokKind.DOT:
                self.i += 1
                parts.append(self._eat(TokKind.IDENT).text)
            return ".".join(parts)
        return head.text

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
        if self.cur.kind == TokKind.KEYWORD and self.cur.text == "set":
            return self.parse_set_type()
        if self.cur.kind == TokKind.CARET:
            return self.parse_pointer_type()
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
                "integer", "boolean", "char", "real", "double", "text"):
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

    def parse_pointer_type(self) -> pa.PointerTypeSpec:
        """`^T` -- a pointer to a value of type T. Inside a `type`
        block T may name a record that's declared later in the same
        block (the canonical linked-list pattern); the translator's
        two-pass type lowering resolves that forward reference."""
        start = self._eat(TokKind.CARET)
        pointee = self.parse_type_spec()
        end_tok = self.tokens[self.i - 1]
        return pa.PointerTypeSpec(
            pointee=pointee, loc=self._loc(start, end_tok),
        )

    def parse_set_type(self) -> pa.SetTypeSpec:
        """`set of T` -- a set whose elements are of type `T`.
        Lowers to `NamedType("set", [T])` (TPy `set[T]`)."""
        start = self._eat(TokKind.KEYWORD, "set")
        self._eat(TokKind.KEYWORD, "of")
        element = self.parse_type_spec()
        end_tok = self.tokens[self.i - 1]
        return pa.SetTypeSpec(
            element=element, loc=self._loc(start, end_tok),
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
        # A `var` section ends at the next decl-section keyword
        # (`procedure`, `function`, `const`, `type`, `var`) or at the
        # body marker (`begin`). Each entry inside the section starts
        # with an IDENT (the variable name); a non-IDENT closes the
        # section so the outer `_parse_decl_blocks` loop can dispatch
        # to the next handler.
        while self.cur.kind == TokKind.IDENT:
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
        while self.cur.kind in (TokKind.DOT, TokKind.LBRACK,
                                 TokKind.CARET):
            if self.cur.kind == TokKind.DOT:
                self.i += 1
                name_tok = self._eat(TokKind.IDENT)
                target = pa.FieldAccess(
                    target=target, ident=name_tok.text,
                    loc=self._loc(start_tok, name_tok),
                )
            elif self.cur.kind == TokKind.LBRACK:
                self._eat(TokKind.LBRACK)
                idx = self.parse_expression()
                rbrack = self._eat(TokKind.RBRACK)
                target = pa.IndexExpr(
                    target=target, index=idx,
                    loc=self._loc(start_tok, rbrack),
                )
            else:  # CARET -- pointer deref `p^`
                caret = self.cur
                self.i += 1
                target = pa.DerefExpr(
                    target=target,
                    loc=self._loc(start_tok, caret),
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
        values: list = [self._parse_case_label()]
        while self.cur.kind == TokKind.COMMA:
            self.i += 1
            values.append(self._parse_case_label())
        self._eat(TokKind.COLON)
        body = self.parse_statement()
        end_tok = self.tokens[self.i - 1]
        return pa.CaseArm(values=values, body=body,
                          loc=self._loc(first_loc, end_tok))

    def _parse_case_label(self):
        """A single case label: either an expression or a range
        `lo..hi`. Ranges lower to a guarded MatchWildcard arm; bare
        expressions stay structural MatchValue arms."""
        lo_expr = self.parse_expression()
        if self.cur.kind == TokKind.DOTDOT:
            self.i += 1
            hi_expr = self.parse_expression()
            return pa.RangeLabel(
                lo=lo_expr, hi=hi_expr,
                loc=self._loc_span(lo_expr, hi_expr),
            )
        return lo_expr

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
        """The RHS of `in`. Either a bracket-form set literal
        (`[ expr, expr, lo..hi, ... ]`, possibly with ranges) or any
        other expression (e.g. a set-typed variable). The bracket
        form is also accepted as a primary expression elsewhere; this
        wrapper is kept so the existing `in` callers continue to work."""
        if self.cur.kind == TokKind.LBRACK:
            return self._parse_set_literal()
        return self.parse_expression()

    def _parse_set_literal(self) -> pa.SetLit:
        """`[ elem (, elem)* ]` where each element is either a single
        expression or a range `lo..hi`."""
        start = self._eat(TokKind.LBRACK)
        elements: list = []
        if self.cur.kind != TokKind.RBRACK:
            elements.append(self._parse_set_element())
            while self.cur.kind == TokKind.COMMA:
                self.i += 1
                elements.append(self._parse_set_element())
        end = self._eat(TokKind.RBRACK)
        return pa.SetLit(elements=elements, loc=self._loc(start, end))

    def _parse_set_element(self):
        """A single element inside a `[ ... ]` set literal. Either a
        plain expression, or `lo..hi` (a RangeLabel) -- the same shape
        used inside case-arm labels."""
        lo_expr = self.parse_expression()
        if self.cur.kind == TokKind.DOTDOT:
            self.i += 1
            hi_expr = self.parse_expression()
            return pa.RangeLabel(
                lo=lo_expr, hi=hi_expr,
                loc=self._loc_span(lo_expr, hi_expr),
            )
        return lo_expr

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
            if self.cur.kind == TokKind.CARET:
                caret = self.cur
                self.i += 1
                node = pa.DerefExpr(
                    target=node,
                    loc=self._loc_span(node, caret),
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
        if t.kind == TokKind.KEYWORD and t.text == "nil":
            self.i += 1
            return pa.NilLit(loc=self._loc(t, t))
        if t.kind == TokKind.IDENT:
            self.i += 1
            return pa.Ident(name=t.text, loc=self._loc(t, t))
        if t.kind == TokKind.LPAREN:
            self.i += 1
            inner = self.parse_expression()
            self._eat(TokKind.RPAREN)
            return inner
        if t.kind == TokKind.LBRACK:
            return self._parse_set_literal()
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
