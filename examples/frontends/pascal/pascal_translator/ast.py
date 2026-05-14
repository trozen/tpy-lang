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
class FloatLit:
    value: float
    loc: Loc


@dataclass
class BoolLit:
    value: bool
    loc: Loc


@dataclass
class Ident:
    name: str        # canonical lowercase form
    loc: Loc


@dataclass
class FieldAccess:
    """`target.ident` -- read field of a record value."""
    target: object   # Expr
    ident: str
    loc: Loc


@dataclass
class IndexExpr:
    """`target[index]` -- Pascal 1-based (or arbitrary lower-bound)
    array element. The translator subtracts the array's declared
    lower bound so the lowered IR uses 0-based indexing."""
    target: object   # Expr
    index: object    # Expr
    loc: Loc


# --- Type specifications -------------------------------------------------


@dataclass
class NamedTypeSpec:
    """A named type reference: `integer`, `Point`, etc."""
    name: str
    loc: Loc


@dataclass
class RecordTypeSpec:
    """An anonymous record type: `record x, y: integer end`."""
    fields: list   # list[RecordField]
    loc: Loc


@dataclass
class RecordField:
    """One field group inside a record body. Pascal allows
    `x, y: integer` to declare both at once; the translator flattens
    these into one IR `Field` per name."""
    names: list    # list[str]
    type_spec: object  # TypeSpec
    loc: Loc


@dataclass
class ArrayTypeSpec:
    """`array[lo..hi] of T`. Bounds are integer literals; richer
    bound forms (constants, identifiers) come later."""
    lower: int
    upper: int
    element: object   # TypeSpec
    loc: Loc


@dataclass
class StringTypeSpec:
    """`string` (default capacity 255) or `string[N]` (custom capacity).
    Lowers to `FixStr[capacity]` in the IR."""
    capacity: int
    loc: Loc


@dataclass
class EnumTypeSpec:
    """`(Red, Green, Blue)` -- the enumeration body of a `type X = (...)`
    declaration. Members are auto-numbered from 0."""
    members: list  # list[str], canonical lowercase
    loc: Loc


@dataclass
class SubrangeTypeSpec:
    """`lo..hi` -- a subrange of an ordinal type. M10 parses these and
    lowers to plain `integer` (no bounds enforcement). Tier 2 will
    make the bounds first-class with insertion of assert / clamp
    sites at assignment / index sites."""
    lower: int
    upper: int
    loc: Loc


@dataclass
class SetLit:
    """`[a, b, c]` -- a set literal appearing as the RHS of `in` (or
    in future as the value of a set-typed variable). M10 only
    accepts literal-shaped element lists."""
    elements: list   # list[Expr]
    loc: Loc


@dataclass
class TypeDecl:
    """A single entry in a `type` section: `Name = TypeSpec`."""
    name: str
    type_spec: object   # TypeSpec
    loc: Loc


@dataclass
class TypeBlock:
    """The `type` section. Pascal allows multiple type decls grouped
    under one `type` keyword (each terminated by `;`)."""
    decls: list   # list[TypeDecl]
    loc: Loc


@dataclass
class CallExpr:
    """Function call in expression position: `factorial(n - 1)`. The
    statement-position counterpart is `CallStmt`; the two are kept
    separate because Pascal's syntax for the two contexts isn't fully
    overlapping (statement-position calls aren't expressions in some
    older dialects). The translator routes both forms through the same
    lowering helper.
    """
    callee: Ident
    args: list       # list[Expr]
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
    `type_spec` is one of `NamedTypeSpec`, `ArrayTypeSpec`,
    `RecordTypeSpec`.
    """
    names: list      # list[str], canonical lowercase
    type_spec: object  # TypeSpec
    loc: Loc


@dataclass
class ConstDecl:
    """`name = value` inside a `const` section. Pascal infers the
    type from the literal -- the translator picks the matching TPy
    type at lower time."""
    name: str
    value: object   # Expr (M10: literal forms only)
    loc: Loc


@dataclass
class ConstBlock:
    """A `const` section. Pascal allows several at module / routine
    scope (much like `var`)."""
    decls: list   # list[ConstDecl]
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
    """`target := expr;` -- `target` is one of `Ident`, `FieldAccess`,
    or `IndexExpr`."""
    target: object   # Ident | FieldAccess | IndexExpr
    value: object    # Expr
    loc: Loc


@dataclass
class CallStmt:
    callee: Ident
    args: list       # list[Expr]
    loc: Loc


@dataclass
class CompoundStmt:
    """`begin ... end` as a statement (Pascal block inside if/while/for)."""
    statements: list  # list[Stmt]
    loc: Loc


@dataclass
class IfStmt:
    cond: object     # Expr
    then_branch: object  # Stmt
    else_branch: object | None  # Stmt | None
    loc: Loc


@dataclass
class WhileStmt:
    cond: object     # Expr
    body: object     # Stmt
    loc: Loc


@dataclass
class ForStmt:
    """`for i := start <to|downto> end do body`.

    `direction` is `"to"` or `"downto"`. Endpoints are inclusive
    (Pascal convention).
    """
    var: str
    start: object    # Expr
    end: object      # Expr
    direction: str
    body: object     # Stmt
    loc: Loc


@dataclass
class RepeatStmt:
    """`repeat stmts until cond`. Statements run unconditionally once;
    loop exits when `cond` becomes true."""
    statements: list  # list[Stmt]
    cond: object      # Expr
    loc: Loc


@dataclass
class CaseArm:
    """One arm in a `case` statement: `value [, value ...] : stmt`.

    M3 supports literal-value arms only (integers and strings). Range
    patterns (`1..5: ...`) are deferred to a later milestone.
    """
    values: list     # list[Expr] -- one arm may list multiple values
    body: object     # Stmt
    loc: Loc


@dataclass
class CaseStmt:
    """`case subject of arms... [else stmt] end`."""
    subject: object  # Expr
    arms: list       # list[CaseArm]
    else_branch: object | None  # Stmt | None
    loc: Loc


@dataclass
class Block:
    statements: list  # list[Stmt]
    loc: Loc


@dataclass
class Param:
    """One parameter in a procedure/function signature.

    `is_var=True` marks a Pascal `var` (by-reference) parameter; the
    translator lowers it to a `PointerType` IR param and rewrites uses
    inside the body. `type_spec` is a TypeSpec (typically a
    `NamedTypeSpec`; array parameters by value aren't supported in M5).
    """
    name: str       # canonical lowercase
    type_spec: object  # TypeSpec
    is_var: bool
    loc: Loc


@dataclass
class SubroutineDecl:
    """A procedure or function declaration.

    `return_type` is None for procedures and the type name (currently
    'integer' or 'boolean') for functions. `var_block` holds the
    routine's local var section (Pascal's `var name: T;` block between
    the header and the body). `const_blocks` is the optional local
    `const` section(s) preceding it.
    """
    name: str
    params: list   # list[Param]
    return_type: str | None
    const_blocks: list   # list[ConstBlock]; Pascal allows several
    var_block: object    # VarBlock | None
    body: object         # CompoundStmt
    loc: Loc


@dataclass
class Program:
    name: str
    type_blocks: list       # list[TypeBlock]; Pascal allows several
    const_blocks: list      # list[ConstBlock]; Pascal allows several
    var_block: object       # VarBlock | None
    subroutines: list       # list[SubroutineDecl]
    block: Block
    loc: Loc
    file: Path
    source_lines: tuple[str, ...] = field(default_factory=tuple)
