"""Frontend IR node definitions.

Plain dataclasses with `kind` discriminators. No methods, no behavior --
the IR is structural data that lowering walks.

M1 set: the nodes needed for a Pascal hello-world program. Subsequent
milestones extend this set without changing existing shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Union


API_VERSION = 1


@dataclass(frozen=True)
class Loc:
    """Source location in the original (non-Python) source file.

    Line and column are 1-based; end_line/end_col are inclusive. `file`
    is the path of the source file the plugin parsed. `source_language`
    tags the plugin that produced this location.
    """
    file: Path
    line: int
    col: int
    end_line: int
    end_col: int
    source_language: str


# --- Operator kinds -------------------------------------------------------


class BinOpKind(Enum):
    """Binary operators recognised by the IR.

    Names match `docs/FRONTEND_PLUGIN_DESIGN.md`. M2 only exercises the
    arithmetic set; comparison and logical operators land with control
    flow (M3) and onward, but are listed here so the enum is stable.
    """
    ADD = "ADD"
    SUB = "SUB"
    MUL = "MUL"
    TRUE_DIV = "TRUE_DIV"
    FLOOR_DIV = "FLOOR_DIV"
    MOD = "MOD"
    POW = "POW"
    BIT_OR = "BIT_OR"
    BIT_XOR = "BIT_XOR"
    BIT_AND = "BIT_AND"
    LSHIFT = "LSHIFT"
    RSHIFT = "RSHIFT"
    LOGICAL_AND = "LOGICAL_AND"
    LOGICAL_OR = "LOGICAL_OR"


class UnaryOpKind(Enum):
    POS = "POS"
    NEG = "NEG"
    NOT = "NOT"
    INVERT = "INVERT"


# --- Type IR --------------------------------------------------------------


@dataclass
class TypeTypeArg:
    """A type-valued generic argument, e.g. `list[T]`'s `T`."""
    kind: str = field(default="TypeTypeArg", init=False)
    value: "TypeExpr" = None  # type: ignore[assignment]


@dataclass
class IntTypeArg:
    """An integer-valued generic argument, e.g. `Array[T, N]`'s `N`."""
    kind: str = field(default="IntTypeArg", init=False)
    value: int = 0


TypeArg = Union[TypeTypeArg, IntTypeArg]


@dataclass
class NamedType:
    """A type referenced by name, with optional generic args.

    Mirrors the design's `NamedType("Int32")` shape. Wrapper types
    (Optional, Pointer, Readonly, Own, Union, Callable, Literal) land
    as separate IR nodes in their respective milestones; M2 only needs
    `NamedType`.
    """
    kind: str = field(default="NamedType", init=False)
    name: str = ""
    args: tuple[TypeArg, ...] = ()
    loc: Loc | None = None


# Plugin authors compose types out of the nodes above. M2 only emits
# `NamedType`; later milestones widen the union.
TypeExpr = NamedType


# --- Expressions ----------------------------------------------------------


@dataclass
class StrLit:
    """String literal expression."""
    kind: str = field(default="StrLit", init=False)
    value: str = ""
    loc: Loc | None = None


@dataclass
class IntLit:
    """Integer literal expression."""
    kind: str = field(default="IntLit", init=False)
    value: int = 0
    loc: Loc | None = None


@dataclass
class Name:
    """Identifier reference."""
    kind: str = field(default="Name", init=False)
    ident: str = ""
    loc: Loc | None = None


@dataclass
class Call:
    """Function call.

    M1 form is positional-only. `type_args`, `kwargs`, `star_args`,
    `double_star` from the design doc are not used yet; they land with
    later milestones.
    """
    kind: str = field(default="Call", init=False)
    callee: "Expr" = None  # type: ignore[assignment]
    args: tuple["Expr", ...] = ()
    loc: Loc | None = None


@dataclass
class BinOp:
    """Binary operation."""
    kind: str = field(default="BinOp", init=False)
    op: BinOpKind = BinOpKind.ADD
    lhs: "Expr" = None  # type: ignore[assignment]
    rhs: "Expr" = None  # type: ignore[assignment]
    loc: Loc | None = None


@dataclass
class UnaryOp:
    """Unary operation."""
    kind: str = field(default="UnaryOp", init=False)
    op: UnaryOpKind = UnaryOpKind.POS
    operand: "Expr" = None  # type: ignore[assignment]
    loc: Loc | None = None


Expr = Union[StrLit, IntLit, Name, Call, BinOp, UnaryOp]


# --- Statements -----------------------------------------------------------


@dataclass
class ExprStmt:
    """Expression statement (the expression's value is discarded)."""
    kind: str = field(default="ExprStmt", init=False)
    value: Expr = None  # type: ignore[assignment]
    loc: Loc | None = None


@dataclass
class VarDecl:
    """Local or module-level variable declaration.

    `mutable=False` marks a constant -- lowering folds those onto TPy's
    `Final[...]` machinery (future milestones; M2 always emits
    `mutable=True`).
    """
    kind: str = field(default="VarDecl", init=False)
    name: str = ""
    type: TypeExpr | None = None
    init: Expr | None = None
    mutable: bool = True
    loc: Loc | None = None


@dataclass
class Assign:
    """Assignment statement.

    `targets` mirrors Python's chained-assignment shape (`a = b = ...`).
    M2 only emits single-target assigns; chaining lands later.
    """
    kind: str = field(default="Assign", init=False)
    targets: tuple[Expr, ...] = ()
    value: Expr = None  # type: ignore[assignment]
    loc: Loc | None = None


Stmt = Union[ExprStmt, VarDecl, Assign]


# --- Imports --------------------------------------------------------------


@dataclass
class ImportName:
    """A name imported via `from X import Y as Z`. `local` equals
    `original` when there is no `as` clause."""
    original: str
    local: str


@dataclass
class Import:
    """`import M [as A]`."""
    kind: str = field(default="Import", init=False)
    module: str = ""
    alias: str | None = None
    loc: Loc | None = None


@dataclass
class FromImport:
    """`from M import a [as b], c, ...`."""
    kind: str = field(default="FromImport", init=False)
    module: str = ""
    names: tuple[ImportName, ...] = ()
    loc: Loc | None = None


ImportDecl = Union[Import, FromImport]


# --- Directives -----------------------------------------------------------


@dataclass
class FrontendDirectives:
    """Module-level directives.

    Mirrors `tpyc.parse.nodes.ModuleDirectives`. M1 leaves everything at
    its default; only relevant when a plugin emits native C++ includes,
    link libs, or marks itself as a native binding module.
    """
    cpp_includes: tuple[tuple[str, str | None], ...] = ()  # (header, platform_filter)
    link_libs: tuple[tuple[str, str | None], ...] = ()
    third_party_deps: tuple[tuple[str, str | None], ...] = ()
    native_module: bool = False
    cpp_namespace: str | None = None


# --- Module ---------------------------------------------------------------


@dataclass
class FrontendModule:
    """A frontend IR module.

    The plugin's `parse()` returns one of these. Lowering converts it
    into a `tpyc.parse.nodes.TpyModule`.

    M1 carries only top-level statements + imports. Records, enums,
    functions, type aliases, constants are typed as empty tuples now
    and populated by later milestones.
    """
    api_version: int = API_VERSION
    qname: str = ""
    source_language: str = ""
    source_lines: tuple[str, ...] = ()
    imports: tuple[ImportDecl, ...] = ()
    type_aliases: tuple = ()
    constants: tuple = ()
    enums: tuple = ()
    records: tuple = ()
    functions: tuple = ()
    top_level_stmts: tuple[Stmt, ...] = ()
    directives: FrontendDirectives = field(default_factory=FrontendDirectives)
