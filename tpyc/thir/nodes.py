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
from enum import Enum, auto
from typing import TYPE_CHECKING

from ..parse import SourceLocation
from ..typesys import ResolvedBinop, TpyType

if TYPE_CHECKING:
    # Compatibility metadata only (cpp_local_representation); imported under
    # TYPE_CHECKING so THIR carries no runtime dependency on codegen.
    from ..codegen_cpp.forms import LocalBinding


class Form(Enum):
    """The borrow-vs-storage axis of a value's C++ representation, lifted from
    codegen's `CppForm` to a carried THIR fact (IR_DESIGN "Form as a First-Class
    THIR Fact"). Positional: the same `TpyType` renders as one form or the other
    depending on slot, so `form` lives on the expression, not the type.

      * `VALUE`   -- value type; borrow and storage coincide (no bridge).
      * `BORROW`  -- `T*` / `const T*` / `T&` / `variant<A*, B*>` / `tuple<..,T*>`.
      * `STORAGE` -- `T` / `optional<T>` / `variant<A, B>` / `tuple<.., optional<T>>`.

    Default `VALUE` leaves the existing value-scalar slice untouched.
    """
    VALUE = auto()
    BORROW = auto()
    STORAGE = auto()


@dataclass(frozen=True)
class THIRNode:
    # kw_only so subclasses can declare required positional fields after it
    # (base-class defaults would otherwise force every later field to default).
    loc: SourceLocation | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class THIRExpr(THIRNode):
    """Base expression. `result_type` is always the fully-resolved type --
    no side-table lookup, no Own/Ref wrapper (those are stripped at lowering).

    `form` is the borrow/storage form the expression renders as -- set by
    lowering through the single binding classifier so the coerce boundary reads
    one fact instead of re-deriving it. kw_only with a `VALUE` default so the
    value-scalar slice (every current node) is untouched."""
    result_type: TpyType
    form: Form = field(default=Form.VALUE, kw_only=True)


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
class THIRSelf(THIRExpr):
    """The instance-method receiver `self`, rendered as the C++ `this` pointer.

    A distinct node rather than a `THIRName("self")` because `this` is a C++
    keyword `escape_cpp_name` would mangle to `this_`, and because `self` is a
    pointer receiver: field reads off it render with `->`. Only arises in an
    instance method admitted to the slice (the free-function slice never sees
    it)."""


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
    # An expression-position binop paren-wraps its result for precedence safety
    # (`x = (a + b)`); an augmented-assignment RHS is a full statement RHS where
    # the AST omits that wrap (`x = a + b;`). False reproduces the latter.
    paren_wrap: bool = True


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


@dataclass(frozen=True)
class THIRFieldAccess(THIRExpr):
    """Field read `receiver.field` / `receiver->field`.

    `field_cpp` is the rendered C++ member name (escape + any native rename
    resolved at lowering); `is_arrow` selects `->` over `.` for a pointer/global
    receiver. `form` is the field's value form -- `STORAGE` for a storage-form
    `Optional[ref]` field read (the F1 source lifted to a borrow via
    `THIRFormConvert`). The F1 slice admits only a non-value record reference
    receiver (`.` access), so `is_arrow` is False there.

    `deref_check` wraps the receiver in a runtime null check for an unproven
    `Optional` member access (`::tpy::deref_check(receiver).field`, the
    `needs_optional_runtime_check` path). The receiver is already a `T*` (a borrow
    subscript element, or a storage element lifted via `optional_to_ptr`), so the
    access after the checked deref is always `.` -- `deref_check` and `is_arrow` are
    mutually exclusive."""
    receiver: THIRExpr
    field_cpp: str
    is_arrow: bool = False
    deref_check: bool = False

    def __post_init__(self) -> None:
        # Enforce the deref_check/is_arrow mutual exclusivity the docstring documents.
        assert not (self.deref_check and self.is_arrow)


@dataclass(frozen=True)
class THIRSubscript(THIRExpr):
    """Tuple subscript read `receiver[index]` -> `std::get<index>(receiver)`.

    `index` is the element index, already normalized to a non-negative offset at
    lowering (a negative literal `t[-1]` folds to `len - 1`), mirroring the AST
    `_gen_subscript` tuple branch. The emit is `std::get<index>(receiver)` for every
    admitted element; `form` records the result's shape -- `VALUE` for a value-scalar
    element (used directly), `BORROW` for a plain-record element (a `T*`/`T&` consumed
    by one member access, whose `->` vs `.` the field access decides) or an `Optional`
    element off a borrow tuple param (a nullable `T*`), and `STORAGE` for an `Optional`
    element off a storage-tuple alias (a `std::optional<T>` lifted to a borrow via
    `THIRFormConvert`/`optional_to_ptr` at the consuming `deref_check`)."""
    receiver: THIRExpr
    index: int


@dataclass(frozen=True)
class THIRFormConvert(THIRExpr):
    """An explicit borrow<->storage form conversion (IR_DESIGN "THIRFormConvert").

    Preserves `result_type` and changes only `form` (this is what distinguishes
    it from `THIRCoerce`, which changes the type). There is no `kind` field: the
    runtime helper is a pure function of (family(result_type), value.form ->
    form, is_const, move). F1 covers the Optional storage->borrow read
    (`::tpy::optional_to_ptr`)."""
    value: THIRExpr
    is_const: bool = False
    move: bool = False


# --- Statements ---


@dataclass(frozen=True)
class THIRVarDecl(THIRStmt):
    """Local declaration with initializer (`name: T = init`).

    `cpp_type` is the rendered C++ declaration type for non-value locals (where
    `resolved_type.to_cpp()` is insufficient -- e.g. the inner type of a
    pointer-local Optional); None for the value-scalar slice (emit falls back to
    `resolved_type.to_cpp()`). `form` is the coarse semantic form of the local --
    `BORROW` for the F1 `T&` alias / `T*` optional-read locals -- and drives the
    insertion rule. `cpp_local_representation` is the `LocalCppForm` analog
    carried verbatim: non-semantic COMPATIBILITY metadata that selects the exact
    C++ slot shape (REF_ALIAS `T&` vs OPTIONAL_TO_PTR `T*`) so emit reproduces
    today's eager codegen byte-for-byte; no other node may depend on it."""
    name: str
    resolved_type: TpyType
    init: THIRExpr | None = None
    cpp_type: str | None = None
    form: Form = Form.VALUE
    is_const: bool = False
    cpp_local_representation: 'LocalBinding | None' = None


@dataclass(frozen=True)
class THIRAssign(THIRStmt):
    """Assignment to an already-declared local (`name = value`) or, for the F2b
    borrow->storage write, to a record field (`recv.field = value`). `target` is
    a THIRName for the former and a THIRFieldAccess for the latter; emission
    renders the target expression directly, so both shapes share one node."""
    target: THIRExpr
    value: THIRExpr


@dataclass(frozen=True)
class THIRReturn(THIRStmt):
    value: THIRExpr | None = None


@dataclass(frozen=True)
class THIRNoOpStmt(THIRStmt):
    """A statement that emits no C++ code -- a `pass` or a docstring in a
    constructor body (M3c-trivia). It carries no payload; its only effect is to
    make `THIRConstructor.body` non-empty so the emitter writes ` {\n    }`
    instead of ` {}`, matching the AST. The inherited `loc` drives the source
    comment exactly as the AST does: a `pass` keeps its `loc` (so `_emit_stmts`
    emits its `// pass` source line), while a docstring lowers with `loc=None`
    -- the AST emits neither comment nor code for a docstring (`gen_body`'s
    simple-stmt code is None, which suppresses the comment)."""


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


@dataclass(frozen=True)
class THIRMilInit:
    """One member-initializer-list entry, rendered `field_cpp(value)` (or
    `field_cpp(std::move(value))` when `move`).

    `field_cpp` is the C++ member name (escape resolved at lowering, like
    `THIRFieldAccess.field_cpp`); `value` is the field's initializer expression
    (a borrow/storage form convert -- `ptr_to_optional` -- arrives as a
    `THIRFormConvert` on `value`). `move` wraps the value in `std::move` -- an
    own-param source consumed at its last use (M3b-move); it is MIL-local (the
    general auto-move stays deferred) and never co-occurs with a `ptr_to_optional`
    convert (an own source skips that arm)."""
    field_cpp: str
    value: THIRExpr
    move: bool = False


@dataclass(frozen=True)
class THIRBaseInit:
    """A base-class initializer in a derived constructor's member-init-list:
    `Base(args)`, emitted before the field inits (M3d). `base_cpp` is the base's
    rendered C++ name (`super_parent_type.to_cpp()`, byte-identical to the AST's
    `_extract_base_inits`); `args` are the lowered `super().__init__(...)` argument
    expressions, rendered at emit (M3d-1 admits eligible-scalar args only)."""
    base_cpp: str
    args: tuple[THIRExpr, ...]


@dataclass(frozen=True)
class THIRConstructor:
    """A lowered constructor: only the member-init-list + body tail that
    `gen_record_decl` emits, NOT the signature (which stays on the AST path, the
    M1 method precedent -- only the body/tail routes through THIR).

    `mil_inits` are the hoisted field initializers in source order; `base_inits`
    are the base-class initializers (M3d; empty for a flat record); `body` is the
    non-init constructor body (M3c). The M3a slice is pure-MIL -- every field init
    hoists, so `body` is empty and the emitted C++ body is `{}`.

    `record_name` and `params` model the constructor faithfully but are not read by
    the tail-only emitter (the signature stays on the AST path); they are the inputs a
    future signature-emit increment would consume."""
    record_name: str
    params: tuple[THIRParam, ...]
    mil_inits: tuple[THIRMilInit, ...]
    base_inits: tuple[THIRBaseInit, ...] = ()
    body: tuple[THIRStmt, ...] = ()


@dataclass
class THIRModule:
    """Container for a module's lowered functions.

    Holds only the functions that lowering proved eligible; ineligible ones
    are absent and stay on the AST-driven codegen path. Mutable container by
    design (the nodes it holds are frozen).
    """
    module_name: str
    functions: list[THIRFunction] = field(default_factory=list)
