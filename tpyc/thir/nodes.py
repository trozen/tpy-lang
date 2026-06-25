"""THIR (Typed High-level IR) node definitions.

Immutable, self-contained representation of a fully-analyzed function: all
types resolved, all sema facts materialized onto the nodes. Codegen consumes
THIR without referencing the SemanticAnalyzer. See docs/IR_DESIGN.md.

The node set covers the non-form value-type subset: scalar params/locals,
names, literals, and straight-line var-decl / assign / return. Tuple, union,
optional and other borrow/storage-form-carrying nodes are deliberately absent
-- form-as-an-IR-fact is gated design work (IR_DESIGN Open Q 9/11/12).
The base classes carry only `result_type` + `loc` so a later `form` tag and
the form-carrying nodes slot in without reshaping the hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..parse import SourceLocation
from ..typesys import ResolvedBinop, TpyType


@dataclass(frozen=True)
class THIRNode:
    # kw_only so subclasses can declare required positional fields after it
    # (base-class defaults would otherwise force every later field to default).
    loc: SourceLocation | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class THIRExpr(THIRNode):
    """Base expression. `result_type` is always the fully-resolved type --
    no side-table lookup, no Own/Ref wrapper (those are stripped at lowering)."""
    result_type: TpyType


@dataclass(frozen=True)
class THIRStmt(THIRNode):
    """Base statement."""


# --- Expressions ---


@dataclass(frozen=True)
class THIRLiteral(THIRExpr):
    """Scalar literal. `result_type` disambiguates int width / float / bool."""
    value: object  # int | float | bool | None


@dataclass(frozen=True)
class THIRName(THIRExpr):
    """Local / param reference."""
    name: str
    is_last_use: bool = False
    is_movable: bool = False


@dataclass(frozen=True)
class THIRBinOp(THIRExpr):
    """Binary operation. `resolved` carries the operator's C++ template and
    operand wrappers (from sema); `divisor_non_zero` swaps the checked div/mod
    helper for the unchecked one, mirroring the AST emit path. `resolved` is
    None for the derived comparisons (`<= > >= !=`), which sema leaves to the
    bare C++ operator -- the emitter renders `(l op r)` directly."""
    left: THIRExpr
    op: str
    right: THIRExpr
    resolved: ResolvedBinop | None
    divisor_non_zero: bool = False


@dataclass(frozen=True)
class THIRCall(THIRExpr):
    """Call to a same-module plain free function. `callee` is the source name;
    the emitter renders `escape_cpp_name(callee)(args)`. Eligibility guarantees
    bare-name emission -- no cross-module qualification, no generic/overload
    name mangling."""
    callee: str
    args: tuple[THIRExpr, ...]


@dataclass(frozen=True)
class THIRCoerce(THIRExpr):
    """A sema-inserted coercion made explicit on the IR. The slice carries the
    literal-into-typed-slot passthroughs (`int_literal_to_fixed_int`,
    `float_literal_to_float`) -- the inner literal renders in the target type."""
    expr: THIRExpr
    coercion_name: str


# --- Statements ---


@dataclass(frozen=True)
class THIRVarDecl(THIRStmt):
    """Local declaration with initializer (`name: T = init`)."""
    name: str
    resolved_type: TpyType
    init: THIRExpr | None = None


@dataclass(frozen=True)
class THIRAssign(THIRStmt):
    """Assignment to an already-declared local (`name = value`)."""
    target: THIRName
    value: THIRExpr


@dataclass(frozen=True)
class THIRReturn(THIRStmt):
    value: THIRExpr | None = None


@dataclass(frozen=True)
class THIRIf(THIRStmt):
    """if / elif / else. An elif chain is an else_body of a single THIRIf.

    Slice: simple comparison conditions, no branch-local first
    declarations, no narrowing -- so it lowers to a plain C++ if/else with no
    hoisting or scope machinery."""
    condition: THIRExpr
    then_body: tuple[THIRStmt, ...]
    else_body: tuple[THIRStmt, ...] = ()


@dataclass(frozen=True)
class THIRWhile(THIRStmt):
    """while loop. Slice: comparison condition, no while/else,
    reassign-only body -- a plain C++ `while (cond) { ... }`."""
    condition: THIRExpr
    body: tuple[THIRStmt, ...]


@dataclass(frozen=True)
class THIRForRange(THIRStmt):
    """`for <var> in range(...)` lowered to a C-style counter loop, step 1.

    Mirrors the AST path's `_gen_range_counter_loop` plus_one / non-hoisted
    branch. `start` is None for `range(stop)` (implicit 0). A non-literal bound
    is hoisted by the emitter into a `__start_N`/`__stop_N` temp, where N is the
    per-function loop index reproducing `ctx.iter_counter`; `*_is_literal`
    mirrors `_is_literal_range_arg`'s inline-vs-hoist decision (`start_is_literal`
    is unused when `start` is None). Slice: fixed-int counter, loop var not used
    after the loop, no for/else, bounds restricted to bare literal or name."""
    var: str
    elem_type: TpyType
    stop: THIRExpr
    start: THIRExpr | None = None
    start_is_literal: bool = True
    stop_is_literal: bool = True
    body: tuple[THIRStmt, ...] = ()


# --- Function / module ---


@dataclass(frozen=True)
class THIRParam:
    name: str
    type: TpyType


@dataclass(frozen=True)
class THIRFunctionLayout:
    """Per-function codegen layout facts (from sema's per-function scan).

    Empty for the straight-line value-scalar slice; the fields exist so the
    emitter reads layout off THIR rather than the analyzer as coverage grows.
    """
    hoisted_locals: frozenset[str] = frozenset()
    movable_locals: frozenset[str] = frozenset()
    reassigned_locals: frozenset[str] = frozenset()


@dataclass(frozen=True)
class THIRFunction:
    name: str
    params: tuple[THIRParam, ...]
    return_type: TpyType
    body: tuple[THIRStmt, ...]
    layout: THIRFunctionLayout


@dataclass
class THIRModule:
    """Container for a module's lowered functions.

    Holds only the functions that lowering proved eligible; ineligible ones
    are absent and stay on the AST-driven codegen path. Mutable container by
    design (the nodes it holds are frozen).
    """
    module_name: str
    functions: list[THIRFunction] = field(default_factory=list)
