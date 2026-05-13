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


class CmpOpKind(Enum):
    """Comparison operators recognised by the IR."""
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    IS = "IS"
    IS_NOT = "IS_NOT"
    IN = "IN"
    NOT_IN = "NOT_IN"


class RangeDir(Enum):
    """Direction of a `ForRange` loop. Mirrors Pascal's to/downto."""
    ASC = "ASC"
    DESC = "DESC"


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
    (Optional, Readonly, Own, Union, Callable, Literal) land as
    separate IR nodes in their respective milestones.
    """
    kind: str = field(default="NamedType", init=False)
    name: str = ""
    args: tuple[TypeArg, ...] = ()
    loc: Loc | None = None


@dataclass
class PointerType:
    """`Ptr[T]` -- a pointer to a pointee type.

    Used to model Pascal's `var` (by-reference) parameters: the
    translator emits `PointerType(NamedType("Int32"))` for a `var x:
    integer` parameter, wraps caller args in `take_ptr(...)`, and
    rewrites in-body uses to `deref(...)` reads and `unsafe_store(...)`
    writes. Records and other non-value types pass through TPy's own
    reference conventions and do not need `PointerType`.
    """
    kind: str = field(default="PointerType", init=False)
    inner: "TypeExpr" = None  # type: ignore[assignment]
    loc: Loc | None = None


# Plugin authors compose types out of the nodes above. Later milestones
# widen this union with OptionalType / OwnType / ReadonlyType / etc.
TypeExpr = Union[NamedType, PointerType]


# --- Expressions ----------------------------------------------------------


@dataclass
class StrLit:
    """String literal expression."""
    kind: str = field(default="StrLit", init=False)
    value: str = ""
    loc: Loc | None = None


@dataclass
class BoolLit:
    """Boolean literal expression."""
    kind: str = field(default="BoolLit", init=False)
    value: bool = False
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


@dataclass
class Compare:
    """Comparison expression.

    Supports Python-style chained form (`a < b < c`) via `ops` /
    `comparators` of length > 1. Plugins from source languages without
    comparison chaining (Pascal, most ALGOL-derived languages) always
    emit length-1 op/comparator tuples.
    """
    kind: str = field(default="Compare", init=False)
    lhs: "Expr" = None  # type: ignore[assignment]
    ops: tuple[CmpOpKind, ...] = ()
    comparators: tuple["Expr", ...] = ()
    loc: Loc | None = None


Expr = Union[StrLit, BoolLit, IntLit, Name, Call, BinOp, UnaryOp, Compare]


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


@dataclass
class If:
    """`if cond then-body else else-body`. `else_body` is `()` when
    there is no else clause."""
    kind: str = field(default="If", init=False)
    cond: Expr = None  # type: ignore[assignment]
    then_body: tuple["Stmt", ...] = ()
    else_body: tuple["Stmt", ...] = ()
    loc: Loc | None = None


@dataclass
class While:
    """`while cond: body`."""
    kind: str = field(default="While", init=False)
    cond: Expr = None  # type: ignore[assignment]
    body: tuple["Stmt", ...] = ()
    loc: Loc | None = None


@dataclass
class RepeatUntil:
    """`repeat body until cond`. Body runs once unconditionally; then
    cond is evaluated and the loop continues while cond is *false*."""
    kind: str = field(default="RepeatUntil", init=False)
    body: tuple["Stmt", ...] = ()
    cond: Expr = None  # type: ignore[assignment]
    loc: Loc | None = None


@dataclass
class ForRange:
    """Numeric for loop with explicit endpoints.

    Mirrors Pascal `for i := lo to hi do` / `for i := hi downto lo do`.
    `inclusive` is True when both endpoints are visited (the Pascal
    convention); future plugins targeting C-style half-open ranges can
    set it False without breaking lowering.
    """
    kind: str = field(default="ForRange", init=False)
    var: str = ""
    start: Expr = None  # type: ignore[assignment]
    end: Expr = None  # type: ignore[assignment]
    direction: RangeDir = RangeDir.ASC
    inclusive: bool = True
    body: tuple["Stmt", ...] = ()
    loc: Loc | None = None


@dataclass
class ForEach:
    """For-each loop over an iterable. Plugins that lower their language's
    sequence iteration to TPy's for-in idiom use this directly; M3 only
    emits `ForRange` (Pascal has no for-in), but `ForEach` lands here
    so the IR is complete for later milestones (arrays/lists in M5+).
    """
    kind: str = field(default="ForEach", init=False)
    var: str = ""
    iter: Expr = None  # type: ignore[assignment]
    body: tuple["Stmt", ...] = ()
    loc: Loc | None = None


# --- Match patterns ------------------------------------------------------


@dataclass
class MatchValue:
    """Literal value pattern: `case 42:` / `case "x":`."""
    kind: str = field(default="MatchValue", init=False)
    value: Expr = None  # type: ignore[assignment]
    loc: Loc | None = None


@dataclass
class MatchWildcard:
    """Wildcard / `else` pattern: matches anything."""
    kind: str = field(default="MatchWildcard", init=False)
    loc: Loc | None = None


MatchPattern = Union[MatchValue, MatchWildcard]


@dataclass
class MatchCase:
    """A single case arm in a `Match`."""
    pattern: MatchPattern = None  # type: ignore[assignment]
    guard: Expr | None = None
    body: tuple["Stmt", ...] = ()
    loc: Loc | None = None


@dataclass
class Return:
    """Return statement. `value=None` for procedure-style returns."""
    kind: str = field(default="Return", init=False)
    value: Expr | None = None
    loc: Loc | None = None


@dataclass
class Match:
    """`match subject: case ...` -- the single switch construct.

    Pascal `case x of v1: stmt; v2: stmt; else stmt end` lowers here
    with one `MatchCase(MatchValue)` per arm plus an optional
    `MatchCase(MatchWildcard)` for the `else` branch.
    """
    kind: str = field(default="Match", init=False)
    subject: Expr = None  # type: ignore[assignment]
    cases: tuple[MatchCase, ...] = ()
    loc: Loc | None = None


Stmt = Union[ExprStmt, VarDecl, Assign, If, While, RepeatUntil,
             ForRange, ForEach, Match, Return]


# --- Function declarations -----------------------------------------------


@dataclass
class Param:
    """A function parameter. M4 only emits unsubscripted, default-less
    params; defaults / *args / **kwargs are reserved for future
    milestones."""
    name: str = ""
    type: TypeExpr = None  # type: ignore[assignment]
    default: Expr | None = None
    loc: Loc | None = None


@dataclass
class Function:
    """Top-level function declaration.

    `is_method=False` for M4 -- methods on records land with M5. The
    field is kept on the node so lowering can route correctly once
    records exist. `type_params` and `decorators` are reserved for
    future milestones.
    """
    kind: str = field(default="Function", init=False)
    name: str = ""
    type_params: tuple = ()
    params: tuple[Param, ...] = ()
    return_type: TypeExpr | None = None
    body: tuple[Stmt, ...] = ()
    decorators: tuple = ()
    is_method: bool = False
    loc: Loc | None = None


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
    functions: tuple["Function", ...] = ()
    top_level_stmts: tuple[Stmt, ...] = ()
    directives: FrontendDirectives = field(default_factory=FrontendDirectives)
