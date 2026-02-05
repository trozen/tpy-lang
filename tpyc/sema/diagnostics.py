"""
TurboPython Semantic Analysis Diagnostics and Data Structures

Contains error/warning handling and basic data structures used throughout
the semantic analysis pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..typesys import TpyType
from ..parse import TpyExpr, SourceLocation


class DiagnosticLevel(Enum):
    """Severity level of a compiler diagnostic."""
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Diagnostic:
    """A compiler diagnostic (error or warning)."""
    level: DiagnosticLevel
    message: str
    loc: SourceLocation | None = None

    def format(self, filename: str = "<unknown>") -> str:
        """Format diagnostic with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: {self.level.value}: {self.message}"
        return f"{filename}: {self.level.value}: {self.message}"


class SemanticError(Exception):
    """Error during semantic analysis."""
    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc
        super().__init__(message)

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: error: {self.message}"
        return f"{filename}: error: {self.message}"


@dataclass
class Scope:
    """A scope containing variable bindings."""
    parent: Optional[Scope] = None
    bindings: dict[str, TpyType] = field(default_factory=dict)

    def lookup(self, name: str) -> Optional[TpyType]:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def define(self, name: str, typ: TpyType) -> None:
        self.bindings[name] = typ


class TypedExpr:
    """Expression annotated with its type."""
    def __init__(self, expr: TpyExpr, typ: TpyType):
        self.expr = expr
        self.type = typ
