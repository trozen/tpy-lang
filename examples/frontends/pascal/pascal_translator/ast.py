"""Pascal AST nodes (M1 subset).

Thin tree mirroring the parts of Turbo Pascal syntax the M1 translator
handles. Each node carries a `Loc` for diagnostics and TPy IR `loc`
propagation. M2 and later milestones extend this with statement,
expression, declaration, and type nodes.
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
class Ident:
    name: str        # canonical lowercase form
    loc: Loc


@dataclass
class CallStmt:
    callee: Ident
    args: list      # M1: list[StrLit]
    loc: Loc


@dataclass
class Block:
    statements: list  # list[CallStmt]
    loc: Loc


@dataclass
class Program:
    name: str
    block: Block
    loc: Loc
    file: Path
    source_lines: tuple[str, ...] = field(default_factory=tuple)
