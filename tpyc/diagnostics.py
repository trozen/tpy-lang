"""
TurboPython Compiler Diagnostics

Neutral diagnostics module used by both the parse/resolve and sema/analyze
phases. Sits upstream of both so neither layer needs to reach into the
other for error classes.

Only depends on `.typesys` (for `TpyType` in `Scope`) and `.parse` (for
`TpyExpr`, `SourceLocation` in `Diagnostic` / `SemanticError`). Both are
base modules; no reverse dependencies.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .typesys import TpyType
from .parse import TpyExpr, SourceLocation


class DiagnosticLevel(Enum):
    """Severity level of a compiler diagnostic."""
    ERROR = "error"
    WARNING = "warning"


OPTIONAL_NONE_ACCESS_WARNING = (
    "Potential None access on optional value; generated code adds runtime null check. "
    "Use 'if x is not None' or 'assert x is not None' to prove safety and remove this warning."
)

OPTIONAL_VALUE_TRUTHINESS_WARNING = (
    "Truthiness check on optional value may also exclude falsy non-None values; "
    "use 'is not None' for None-only checks."
)

# Remediation hint appended to "copies X into field/container/owned storage,
# but X is non-copyable" errors. Listed in preference order: the idiomatic
# fix (Own for ownership transfer), then explicit copy() for the rare case
# a copy is genuinely wanted, then a reminder that last-use is already moved.
NOCOPY_REMEDIATION_HINT = (
    " (use Own[...] to transfer ownership, copy() if a copy is truly intended, "
    "or let auto-move apply at last use)"
)


def nocopy_container_elem_error(typ: object, kind: str) -> str:
    """Error message for `@nocopy` types used as set elements or dict keys.

    `kind` is "set element" or "dict key". Hash-table-backed `ordered_set` /
    `ordered_map` index entries via `std::pair<const K, ...>`, which requires
    copy-constructible K -- @nocopy types can't satisfy that.
    """
    return (
        f"Type '{typ}' is non-copyable and cannot be used as a {kind}; "
        f"{kind}s must be copy-constructible"
    )


@dataclass
class Diagnostic:
    """A compiler diagnostic (error or warning)."""
    level: DiagnosticLevel
    message: str
    loc: SourceLocation | None = None

    def format(self, filename: str = "<unknown>") -> str:
        """Format diagnostic with file:line prefix.

        A location that carries its own file (e.g. a frontend plugin's
        diagnostics, which point into the DSL source) wins over the
        caller-supplied fallback name."""
        if self.loc:
            name = self.loc.file or filename
            return f"{name}:{self.loc.line}: {self.level.value}: {self.message}"
        return f"{filename}: {self.level.value}: {self.message}"


class SemanticError(Exception):
    """Error raised during resolve or sema phases."""
    def __init__(self, message: str, loc: SourceLocation | None = None,
                 filename: str | None = None):
        self.message = message
        self.loc = loc
        self.filename = filename
        super().__init__(message)

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        name = self.filename or filename
        if self.loc:
            return f"{name}:{self.loc.line}: error: {self.message}"
        return f"{name}: error: {self.message}"


@dataclass
class Scope:
    """A scope containing variable bindings."""
    parent: Optional[Scope] = None
    bindings: dict[str, TpyType] = field(default_factory=dict)
    depth: int = field(init=False)

    def __post_init__(self) -> None:
        self.depth = (self.parent.depth + 1) if self.parent else 0

    def lookup(self, name: str) -> Optional[TpyType]:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def define(self, name: str, typ: TpyType) -> None:
        self.bindings[name] = typ

    def set_existing(self, name: str, typ: TpyType) -> bool:
        """Update an existing binding at the level where it was defined.

        Walks up the parent chain and rewrites the binding in-place at the
        owning scope. Returns True on update, False if the name was not bound.
        """
        scope: Optional[Scope] = self
        while scope is not None:
            if name in scope.bindings:
                scope.bindings[name] = typ
                return True
            scope = scope.parent
        return False

    def all(self) -> dict[str, TpyType]:
        """Return all bindings in this scope (not including parent)."""
        return dict(self.bindings)


class TypedExpr:
    """Expression annotated with its type."""
    def __init__(self, expr: TpyExpr, typ: TpyType):
        self.expr = expr
        self.type = typ
