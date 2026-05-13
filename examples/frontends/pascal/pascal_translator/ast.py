"""Pascal AST nodes.

Thin tree mirroring the parts of Turbo Pascal syntax the translator
handles. Each node carries a `Loc` for diagnostics and TPy IR `loc`
propagation. Later milestones extend this with control flow, records,
procedures, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Loc:
    file: Path
    line: int        # 1-based
    col: int         # 1-based
    end_line: int
    end_col: int


@dataclass
class StrLit:
    value: str
    loc: Loc


@dataclass
class IntLit:
    value: int
    loc: Loc


@dataclass
class Ident:
    name: str        # canonical lowercase form
    loc: Loc


@dataclass
class BinOp:
    op: str          # '+', '-', '*', '/', 'div', 'mod'
    lhs: object      # Expr
    rhs: object      # Expr
    loc: Loc


@dataclass
class UnaryOp:
    op: str          # '+' or '-'
    operand: object  # Expr
    loc: Loc


@dataclass
class VarDecl:
    """One `var name [, name]* : type` declaration entry.

    Pascal allows multiple names in one decl; we keep them grouped in
    the AST and split into per-name IR `VarDecl` nodes at translate time.
    """
    names: list      # list[str], canonical lowercase
    type_name: str
    loc: Loc


@dataclass
class VarBlock:
    """The `var` section at the top of a program body.

    Pascal collects all `var` decls before `begin`. Translate hoists
    these into module-level `VarDecl` IR nodes preceding the program
    body's statements.
    """
    decls: list      # list[VarDecl]
    loc: Loc


@dataclass
class AssignStmt:
    """`target := expr;`"""
    target: Ident
    value: object    # Expr
    loc: Loc


@dataclass
class CallStmt:
    callee: Ident
    args: list       # list[Expr]
    loc: Loc


@dataclass
class Block:
    statements: list  # list[Stmt]
    loc: Loc


@dataclass
class Program:
    name: str
    var_block: object       # VarBlock | None
    block: Block
    loc: Loc
    file: Path
    source_lines: tuple[str, ...] = field(default_factory=tuple)
