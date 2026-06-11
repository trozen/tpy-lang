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
from typing import Any, Union


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


@dataclass
class UnionType:
    """`A | B | C` -- a Python-style union annotation. Used by
    plugins (e.g. Pascal variant records) to type a discriminated-
    payload field as the union of its variant inner records. Lowers
    to a `TpyUnionRef` so the resolver runs the same union-shape
    pipeline as ordinary Python source."""
    kind: str = field(default="UnionType", init=False)
    members: tuple["TypeExpr", ...] = ()
    loc: Loc | None = None


@dataclass
class CallableType:
    """`Callable[[P1, P2], R]` -- a function-pointer-style type
    annotation. Used by plugins (e.g. Pascal `type Fn = procedure
    (x: integer);`) to express a first-class callable value's type.
    Lowers to a `TpyCallableRef`. The `params` slot carries TypeExpr
    nodes per parameter (parameter names aren't part of the type
    signature); `return_type=None` lowers to a `None`-returning
    callable (Pascal `procedure`)."""
    kind: str = field(default="CallableType", init=False)
    params: tuple["TypeExpr", ...] = ()
    return_type: "TypeExpr | None" = None
    loc: Loc | None = None


# Plugin authors compose types out of the nodes above. Later milestones
# widen this union with OptionalType / OwnType / ReadonlyType / etc.
TypeExpr = Union[NamedType, PointerType, UnionType, CallableType]


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
class FloatLit:
    """Floating-point literal expression."""
    kind: str = field(default="FloatLit", init=False)
    value: float = 0.0
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

    `type_args` carries explicit type arguments at the call site --
    Pascal needs them for generic-type constructor calls like
    `Array[Int32, 8]()`. `kwargs` is a tuple of `(name, value)` pairs
    -- needed for calls like Python's `print(x, end="")`. `star_args`
    / `double_star` from the design doc are not used yet; they land
    with later milestones.
    """
    kind: str = field(default="Call", init=False)
    callee: "Expr" = None  # type: ignore[assignment]
    type_args: tuple[TypeArg, ...] = ()
    args: tuple["Expr", ...] = ()
    kwargs: tuple[tuple[str, "Expr"], ...] = ()
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
class Attr:
    """Field access: `target.ident`. The target is an arbitrary
    expression; assignment-target shape (`p.x := v`) reuses this node
    by appearing inside `Assign.targets`."""
    kind: str = field(default="Attr", init=False)
    target: "Expr" = None  # type: ignore[assignment]
    ident: str = ""
    loc: Loc | None = None


@dataclass
class Subscript:
    """Indexed access: `target[index]`. Plugins targeting languages
    with non-zero-based indexing (Pascal, Fortran) compute the offset
    in `index` at translate time -- lowering passes the index through
    unchanged."""
    kind: str = field(default="Subscript", init=False)
    target: "Expr" = None  # type: ignore[assignment]
    index: "Expr" = None  # type: ignore[assignment]
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


@dataclass
class SetLit:
    """Set literal: `{a, b, c}`. Lowers to TpySetLiteral."""
    kind: str = field(default="SetLit", init=False)
    elements: tuple["Expr", ...] = ()
    loc: Loc | None = None


@dataclass
class ListLit:
    """List literal: `[a, b, c]`. Lowers to TpyArrayLiteral (which
    is TPy's spelling for a Python list literal at the AST level)."""
    kind: str = field(default="ListLit", init=False)
    elements: tuple["Expr", ...] = ()
    loc: Loc | None = None


@dataclass
class NoneLit:
    """`None` literal -- the null/empty sentinel. Lowers to
    TpyNoneLiteral. Used by plugins that translate a source-language
    null (Pascal `nil`, etc.) to TPy's nullable form."""
    kind: str = field(default="NoneLit", init=False)
    loc: Loc | None = None


@dataclass
class Conditional:
    """Ternary conditional expression: `then if cond else else_`.
    Lowers to TpyIfExpr. The frontend-IR analog of a source-level
    ternary -- a plugin emits it for any source construct that selects
    between two values by a boolean."""
    kind: str = field(default="Conditional", init=False)
    cond: "Expr" = None  # type: ignore[assignment]
    then: "Expr" = None  # type: ignore[assignment]
    else_: "Expr" = None  # type: ignore[assignment]
    loc: Loc | None = None


Expr = Union[StrLit, BoolLit, IntLit, FloatLit, Name, Call, BinOp, UnaryOp,
             Compare, Attr, Subscript, SetLit, ListLit, NoneLit, Conditional]


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
class AugAssign:
    """Augmented assignment: `target <op>= value` (`x += 1`). `op` is the
    underlying binary operator; logical and/or are rejected at lowering (no
    `&&=`/`||=` form), every other binop has an augmented form."""
    kind: str = field(default="AugAssign", init=False)
    target: "Expr" = None  # type: ignore[assignment]
    op: BinOpKind = BinOpKind.ADD
    value: "Expr" = None  # type: ignore[assignment]
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


@dataclass
class MatchClass:
    """Class pattern: `case ClassName() [as bind]`. Used by plugins
    (e.g. Pascal variant records) to narrow a union-typed subject to
    one of its alternative classes. The bound name (if set) lets the
    arm body reach the narrowed value's fields without an explicit
    cast."""
    kind: str = field(default="MatchClass", init=False)
    class_name: str = ""
    bind: str | None = None
    loc: Loc | None = None


MatchPattern = Union[MatchValue, MatchWildcard, MatchClass]


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
class Raise:
    """`raise <expr>` -- throw an exception. `value=None` for the
    bare-`raise` re-raise form. Plugins emitting accessor methods
    that need a "this variant isn't live" panic use this with a
    `RuntimeError(...)` constructor as the value."""
    kind: str = field(default="Raise", init=False)
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


@dataclass
class Break:
    """`break` -- exit the innermost enclosing loop. Loop nesting is
    determined by the lowered TpyModule's structure, the same way
    Python source `break` resolves."""
    kind: str = field(default="Break", init=False)
    loc: Loc | None = None


@dataclass
class Continue:
    """`continue` -- skip to the next iteration of the innermost
    enclosing loop."""
    kind: str = field(default="Continue", init=False)
    loc: Loc | None = None


Stmt = Union[ExprStmt, VarDecl, Assign, AugAssign, If, While, RepeatUntil,
             ForRange, ForEach, Match, Return, Raise, Break, Continue]


# --- Decorators ----------------------------------------------------------


@dataclass
class Decorator:
    """A decorator on a declaration (`Function`, `Record`, `Enum`,
    `EnumValue`, `Field`, `Constant`).

    `name` is the dotted decorator name (`"tpy.native"`,
    `"mylang.dataclass"`); lowering routes it via the compiler-owned
    decorator registry (`frontend_ir/decorators.py`). Arguments are IR
    expressions -- a decorator carries *declarative* config only. Data a
    macro needs that isn't expressible as an `Expr` (arbitrary
    plugin-computed Python) rides `FrontendModule.macro_data`, not here.
    """
    name: str = ""
    args: tuple[Expr, ...] = ()
    kwargs: tuple[tuple[str, Expr], ...] = ()
    loc: Loc | None = None


# --- Enum declarations ---------------------------------------------------


@dataclass
class EnumValue:
    """One member of an `Enum`. `value` is optional -- when absent, the
    lowering pass assigns Pascal-style auto-numbered values starting
    at 0."""
    name: str = ""
    value: "Expr | None" = None
    decorators: tuple = ()
    loc: Loc | None = None


@dataclass
class Enum:
    """Enumeration type declaration."""
    kind: str = field(default="Enum", init=False)
    name: str = ""
    base_type: "NamedType | None" = None
    values: tuple[EnumValue, ...] = ()
    decorators: tuple = ()
    loc: Loc | None = None


# --- Record declarations -------------------------------------------------


@dataclass
class Field:
    """One field on a `Record`. M5 only emits unsubscripted,
    default-less fields; record methods / generic fields / decorators
    are reserved for future milestones."""
    name: str = ""
    type: TypeExpr = None  # type: ignore[assignment]
    default: "Expr | None" = None
    decorators: tuple = ()
    loc: Loc | None = None


@dataclass
class Record:
    """Record (class) declaration.

    M5 carries only `name` and `fields` -- single inheritance, methods,
    nested records / enums / aliases, decorators, and generic type
    params land in later milestones but the slots stay so plugins can
    grow into them.
    """
    kind: str = field(default="Record", init=False)
    name: str = ""
    type_params: tuple = ()
    base: "NamedType | None" = None
    fields: tuple[Field, ...] = ()
    methods: tuple = ()
    nested_records: tuple = ()
    nested_enums: tuple = ()
    nested_constants: tuple = ()
    nested_aliases: tuple = ()
    decorators: tuple = ()
    loc: Loc | None = None


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
    records exist. `type_params` is reserved for future milestones.

    `decorators` carries typed `Decorator` IR nodes; lowering resolves
    each through the decorator registry and threads `MACRO`-routed ones
    into `TpyFunction.pending_macros` so sema applies them as
    `@function_macro`s. Only supported on free functions -- a decorator on
    a method is rejected at lowering (methods are not scanned by the
    function-macro phase), mirroring the parser's method-decorator error.

    Property accessors (`is_property_getter` / `is_property_setter`)
    let a plugin emit `@property` getter/setter pairs without going
    through decorator-string round-trip; the IR lowering threads
    these directly onto the resulting `TpyFunction`. `property_name`
    on a setter names the property the setter belongs to (e.g. for
    `@foo.setter`, `property_name == 'foo'`).
    """
    kind: str = field(default="Function", init=False)
    name: str = ""
    type_params: tuple = ()
    params: tuple[Param, ...] = ()
    return_type: TypeExpr | None = None
    body: tuple[Stmt, ...] = ()
    decorators: tuple = ()
    is_method: bool = False
    is_property_getter: bool = False
    is_property_setter: bool = False
    property_name: str | None = None
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


@dataclass
class StarImport:
    """`from M import *` -- bring every public name from `M` into the
    importer's unqualified scope. Powers Pascal `uses A, B, C` and any
    other source language with similar wildcard-import semantics. The
    importer-side names list is resolved at compile time by TPy's
    `_expand_star_imports_for_module`, which reads `M`'s public
    surface from its module attribute table.
    """
    kind: str = field(default="StarImport", init=False)
    module: str = ""
    loc: Loc | None = None


ImportDecl = Union[Import, FromImport, StarImport]


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
    enums: tuple["Enum", ...] = ()
    records: tuple["Record", ...] = ()
    functions: tuple["Function", ...] = ()
    top_level_stmts: tuple[Stmt, ...] = ()
    directives: FrontendDirectives = field(default_factory=FrontendDirectives)
    # Opaque, module-scoped Python payload a plugin hands to its own
    # compile-time macros (e.g. a resolver's lookup tables). Lowering
    # threads it onto `TpyModule.macro_data`; the function-macro phase
    # exposes it as `ctx.module_data`. Not an IR `Expr` and never
    # inspected by lowering -- macros run under CPython, so arbitrary
    # objects are fine.
    macro_data: Any = None
