"""Frontend plugin IR.

Plain-dataclass intermediate representation that frontend plugins emit and
lowering converts into a `TpyModule`. See `docs/FRONTEND_PLUGIN_DESIGN.md`
for the design contract.

M1 scope: only the nodes needed to express a Pascal "Hello, World!"
program end-to-end. Additional nodes land milestone-by-milestone.
"""

from .nodes import (
    API_VERSION,
    Call,
    ExprStmt,
    FrontendDirectives,
    FrontendModule,
    FromImport,
    Import,
    ImportDecl,
    ImportName,
    Loc,
    Name,
    StrLit,
    Stmt,
    Expr,
)
from .lower import LoweredFrontend, lower_module

__all__ = [
    "API_VERSION",
    "Call",
    "ExprStmt",
    "Expr",
    "FrontendDirectives",
    "FrontendModule",
    "FromImport",
    "Import",
    "ImportDecl",
    "ImportName",
    "Loc",
    "LoweredFrontend",
    "Name",
    "StrLit",
    "Stmt",
    "lower_module",
]
