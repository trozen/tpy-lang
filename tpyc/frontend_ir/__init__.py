"""Frontend plugin IR.

Plain-dataclass intermediate representation that frontend plugins emit and
lowering converts into a `TpyModule`. See `docs/FRONTEND_PLUGIN_DESIGN.md`
for the design contract.

M1 scope: only the nodes needed to express a Pascal "Hello, World!"
program end-to-end. Additional nodes land milestone-by-milestone.
"""

from .nodes import (
    API_VERSION,
    Assign,
    BinOp,
    BinOpKind,
    Call,
    Expr,
    ExprStmt,
    FrontendDirectives,
    FrontendModule,
    FromImport,
    Import,
    ImportDecl,
    ImportName,
    IntLit,
    IntTypeArg,
    Loc,
    Name,
    NamedType,
    Stmt,
    StrLit,
    TypeArg,
    TypeExpr,
    TypeTypeArg,
    UnaryOp,
    UnaryOpKind,
    VarDecl,
)
from .lower import LoweredFrontend, lower_module

__all__ = [
    "API_VERSION",
    "Assign",
    "BinOp",
    "BinOpKind",
    "Call",
    "Expr",
    "ExprStmt",
    "FrontendDirectives",
    "FrontendModule",
    "FromImport",
    "Import",
    "ImportDecl",
    "ImportName",
    "IntLit",
    "IntTypeArg",
    "Loc",
    "LoweredFrontend",
    "Name",
    "NamedType",
    "Stmt",
    "StrLit",
    "TypeArg",
    "TypeExpr",
    "TypeTypeArg",
    "UnaryOp",
    "UnaryOpKind",
    "VarDecl",
    "lower_module",
]
