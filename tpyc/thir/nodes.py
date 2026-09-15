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

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from ..identity_map import IdentityMap
from ..parse import RebindStorage, SourceLocation
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


class TruthinessMode(Enum):
    """A non-identity Python truthiness render selected during lowering."""
    NONEMPTY = auto()
    IS_TRUTHY = auto()
    TO_BOOL = auto()
    RECORD_BOOL = auto()
    RECORD_LEN = auto()
    ALWAYS_TRUE = auto()
    PTR_TRUTHY = auto()


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
    """Scalar literal. Source integer spelling is fixed during lowering."""
    value: object  # int | float | bool | None
    # Synthetic small integers (tuple indices and direct unit-test nodes) may
    # omit this because their decimal spelling is position-independent.
    int_cpp: str | None = None
    # A None literal's pre-spelled render for slots whose spelling the
    # form/type split cannot derive (the base-init target-typed row:
    # `none_default_cpp_spelling` decides `{}` vs `std::nullopt` vs
    # `nullptr` from the base param slot). None keeps the positional arms.
    none_cpp: str | None = None


@dataclass(frozen=True)
class THIRDefaultConstruct(THIRExpr):
    """A `T()` default-construction filling an omitted generic param
    (`three_params[int32](10, c=5)` -> `int32_t{}`): the resolved type's
    brace-init, rendered at the arg position."""
    cpp_type: str = ""


@dataclass(frozen=True)
class THIRStrLiteral(THIRExpr):
    """A string literal, rendered via `cpp_string_literal_expr`, the single
    quoting/escaping helper. Form stays VALUE: the emitted
    const char[N] converts implicitly to both string_view and string slots,
    so a literal is never wrapped by the owned-sink view->owned copy."""
    value: str


@dataclass(frozen=True)
class THIRBytesLiteral(THIRExpr):
    """A bytes literal. Unlike a str literal (a position-neutral const char[N]),
    a bytes literal's C++ render is TARGET-dependent, so lowering decides it
    per sink and carries the verdict on the `form` tag: STORAGE renders the
    owning buffer (`::tpy::bytes_literal_owned(...)` / empty
    `::tpy::Bytes{}` -- the default, matching every target-less
    position: print args, compare operands, owned decl inits/returns), BORROW
    the static-storage span (`::tpy::bytes_literal(...)` / empty
    `::tpy::BytesView{}`, the view-targeted positions: view-local
    inits/reassigns, bytes/BytesView call args). Never VALUE. The owned-sink
    view->owned wraps still never fire on a literal: they key on an owned
    (`bytes`) target, and a bytes-targeted literal lowers STORAGE."""
    value: bytes
    form: Form = field(default=Form.STORAGE, kw_only=True)


@dataclass(frozen=True)
class THIRFStringArg:
    """One interpolated f-string value: the lowered expression plus its
    Python-compatible formatting wrapper as a positional `{0}` template
    (e.g. `::tpy::bool_to_str({0})`), decided at lowering from the arg's
    resolved type and conversion (`!r` carries `::tpy::repr_of({0})`; a
    format spec flips the bool row to `static_cast<int>` and the float rows
    to bare). None passes the arg through unwrapped (str-family values, plain
    fixed ints, char). `format_spec` is the parser-validated constant spec
    text, spliced verbatim into the `{:spec}` placeholder."""
    expr: THIRExpr
    wrap: str | None = None
    format_spec: str | None = None


@dataclass(frozen=True)
class THIRFString(THIRExpr):
    """An f-string: literal segments (raw, unescaped source text) interleaved
    with interpolated args. All type dispatch is decided at lowering (the arg
    wrap templates); the emitter assembles a pure string function --
    `std::string("joined")` for the all-literal shape,
    `std::format("fmt", args...)` otherwise, with the explicit-length
    `std::string("...", N)` / `std::vformat` arms when a literal segment embeds
    a NUL byte. The user / union / container / Any arg-type rows are
    gate-excluded under any conversion.
    The result is an owned `str` (STORAGE form), landing bare in owned sinks
    like any owned-str call result."""
    parts: tuple['str | THIRFStringArg', ...]


@dataclass(frozen=True)
class THIRCharLiteral(THIRExpr):
    """A single-char str literal rendered as a C++ char literal (`'x'`, via
    `escape_cpp_char`). Arises only where a `char` target is threaded into
    the literal render: a comparison operand
    opposite a char-typed value, a
    char-annotated decl init (`c: char = 'x'` -> `char c = 'x';`), or a call
    arg into a char param slot (`take('a')` -> `take('a')`). Every other str
    literal stays a `THIRStrLiteral`. char-targeted literal reassigns and
    returns cannot reach lowering -- sema rejects them (`c = 'y'` /
    `return 'q'` at a char slot are type errors; only the annotated decl form
    converts) -- so their gate rejects are defensive."""
    value: str


@dataclass(frozen=True)
class THIRWalrus(THIRExpr):
    """A walrus binding (`(n := v)`), rendered per target class: the target
    pre-declares `type name[ = init];`
    through the temp sink's named row on FIRST binding (`cpp_type` set)
    and assigns in place on a rebind (`cpp_type` None). The named pre-decl
    flushes at the statement flush point -- for a while condition that is
    BEFORE the loop (the binding stays visible after it).

    Per-class shape: the value-scalar slice renders `(n = v)` (no init, no
    tail); a pointer-repr Optional target pre-declares `T* n = nullptr;`
    and assigns the borrow-lifted RHS (`(n = optional_to_ptr(...))`); a
    borrow-alias pointer target pre-declares `[const ]T* n = nullptr;` and
    renders `(n = &(v), *n)` (`addr_of` off for an already-pointer
    source). `tail="deref"` appends the `, *n` result deref. Remaining
    non-value targets (tuples, non-value slots, hoisted names) are not
    lowered yet."""
    name: str
    cpp_name: str
    value: 'THIRExpr'
    cpp_type: 'str | None' = None
    init: 'str | None' = None
    tail: 'str | None' = None
    addr_of: bool = False
    # REASSIGNED borrow-tuple walrus (`(t := make_pair(9))` later rebound):
    # the owning slot `std::optional<{slot_cpp}> __slot_N;` is allocated at
    # emit (reused per target across sibling occurrences) and the value
    # renders `tuple_to_pointer<{borrow_cpp}>(__slot_N.emplace({v}))`,
    # with the bare-name result tail (`, t`).
    slot_cpp: 'str | None' = None
    borrow_cpp: 'str | None' = None
    # A resumable-frame `frame_slot<T>` target: the write is
    # `name.emplace(v)` (the slot has no operator=), and the string is the
    # type prefix a bare brace-init value needs to bind to emplace's
    # forwarding ref (`typed_brace_init`).
    emplace_cpp: 'str | None' = None


@dataclass(frozen=True)
class THIRName(THIRExpr):
    """Local / param reference. `deref` marks an F2 pointer-local (`T*`) read
    in a value position (a record call arg), rendered `(*name)` -- the
    indirect-name deref. Non-pointer names render bare.

    `cpp` (when set) is a native-linkage or imported value global's
    PRE-RENDERED spelling (`::g_count` via qualify_native_name /
    `::tpyapp::mod::g` via imported_variable_cpp -- the THIRCall.callee_cpp
    precedent), stamped at lowering from the seeding map and rendered
    verbatim by emit; `name` keeps the Python name for the gate/scope
    bookkeeping."""
    name: str
    is_last_use: bool = False
    is_movable: bool = False
    deref: bool = False
    # An UNPROVEN value-repr Optional[scalar] read consumed as its inner scalar:
    # renders `::tpy::deref_optional_check(name)` -- the runtime-checked
    # unwrap. Mutually exclusive with `deref` (the proven `(*name)` unwrap).
    opt_deref_check: bool = False
    cpp: str | None = None


@dataclass(frozen=True)
class THIRSelf(THIRExpr):
    """The instance-method receiver `self`.

    A distinct node rather than a `THIRName("self")` because the default
    spelling `this` is a C++ keyword `escape_cpp_name` would mangle to
    `this_`, and because a plain-method receiver is a POINTER: field reads
    off it render with `->`. `cpp` is the receiver spelling -- `this` for a
    plain method, `__self` for a resumable (async) method coro, whose frame
    captures the receiver as a `Record&` reference (so field reads render
    `.`, driven by `_LowerCtx.self_is_pointer=False`). `deref` renders
    `(*{cpp})` -- the indirect-name deref a value position applies.

    `deref` is set ONCE, where the node is built, from whether the receiver
    IS a pointer: a value position is the default consumer, so a position
    that never thought about `self` still renders a legal value. Making it a
    POSITION fact hand-applied at each value sink is what let a sink forget
    and render a bare `Record*` into a value slot.

    Two consumer classes need the bare pointer back and clear it. Members
    reached THROUGH the pointer (`THIRFieldAccess` / `THIRMethodCall` with
    `receiver_through_pointer`) -- `(*this)->x` is ill-formed; that class has
    a `validate` rule behind it. And raw `T*` SLOTS that bind the receiver
    pointer itself (a borrow-tuple element, `{1, this}`) -- unguarded,
    because the tuple nodes cannot distinguish a pointer slot from a value
    one, so clearing there is a construction-site obligation."""

    deref: bool = False
    cpp: str = "this"


@dataclass(frozen=True)
class THIRBinOp(THIRExpr):
    """Binary operation. `resolved` carries the operator's C++ template and
    operand wrappers (from sema); `divisor_non_zero` swaps the checked div/mod
    helper for the unchecked one. `resolved` is
    None for the derived comparisons (`<= > >= !=`) and for logical `&&`/`||`
    (bool-result and/or, plus the pair-fold of an inline chained comparison),
    which sema leaves to the bare C++ operator -- the emitter renders
    `(l op r)` directly."""
    left: THIRExpr
    op: str
    right: THIRExpr
    resolved: ResolvedBinop | None
    divisor_non_zero: bool = False
    # An expression-position binop paren-wraps its result for precedence safety
    # (`x = (a + b)`); an augmented-assignment RHS is a full statement RHS that
    # needs no wrap (`x = a + b;`). False selects the latter.
    paren_wrap: bool = True
    # Per-side operand cast wraps (`{0}` templates), applied to the emitted
    # operand strings before the wrapper/template expansion: int-enum
    # operands cast to their underlying type
    # (`static_cast<int32_t>(...)`), a BigInt operand of a mixed BigInt/float
    # compare casts to the float operand's type. Computed at lowering.
    left_cast: 'str | None' = None
    right_cast: 'str | None' = None
    # Lowering's position-independent stamp for the dedicated fixed-int
    # literal arm: both operands are IntLiteral-typed NON-names
    # (literals, nested literal binops, subscripts over literal-seeded
    # containers). `_slot_literal_retype`, at the target-threading
    # positions, consumes it to rebuild the node with `template_override`.
    both_literal_int_operands: bool = False
    # When set (the rebuilt dedicated arm), emit expands this
    # target-resolved cpp_template over the bare operands -- no wrappers,
    # no parens (`::tpy::add_check<int64_t>(l, r)`).
    template_override: 'str | None' = None


@dataclass(frozen=True)
class THIRValueSelect(THIRExpr):
    """Python `and`/`or` in VALUE position: renders
    `(t ? lhs : rhs)` for `or`, `(t ? rhs : lhs)` for
    `and`, where `t` is the truthiness of the (once-evaluated) LHS and the
    RHS sits lazily inside the ternary branch (short-circuit preserved).

    `lhs_temp_cpp` non-None hoists a non-name LHS into a `__tmp_N` off the
    shared counter (`auto&&`, or `std::string_view` for a str literal),
    reused by the truthy test and the lhs branch. `truthy_mode` selects
    the LHS truthiness render: NONEMPTY -> `(!ref.empty())` (str family),
    RECORD_LEN -> `(::tpy::__len__(ref) != 0)` (containers),
    ALWAYS_TRUE -> the folded bare `true` (an always-truthy record LHS --
    already evaluated here, so no THIRTruthy-style operand-effect wrap),
    None -> the bare ref (scalars' implicit bool). `lhs_cast`/`rhs_cast`
    are the mixed-operand conversion wraps (`<cpp_result>(x)` when the
    operand C++ spellings differ); `rhs_sv` wraps a str-literal RHS
    `std::string_view(...)`. Chains nest naturally (an inner select is a
    non-name LHS taking its own temp). A non-value select is a BORROW
    lvalue aliasing the chosen operand. `ptr_select_cpp` non-None is the
    rvalue non-value RHS: emit hoists `std::optional<cpp>
    __logical_slot_N;` and renders `(*(t ? &(lhs) : (slot.emplace(rhs),
    &*slot)))` so the rvalue materializes lazily (short-circuit
    preserved). Inline-isinstance LHS facts are gate-rejected."""
    lhs: 'THIRExpr'
    rhs: 'THIRExpr'
    op: str
    truthy_mode: 'TruthinessMode | None' = None
    lhs_temp_cpp: 'str | None' = None
    lhs_cast: 'str | None' = None
    rhs_cast: 'str | None' = None
    rhs_sv: bool = False
    ptr_select_cpp: 'str | None' = None


@dataclass(frozen=True)
class THIRChainedCompareStmtExpr(THIRExpr):
    """The complex-intermediate chained comparison: a GCC
    statement-expression that binds each
    non-simple operand to an `auto&& _cmpI` temp so it evaluates exactly once,
    interleaving bindings with the left-folded `&&` chain (operands after a
    failed pair never evaluate). `inits[i]` is operand i's lowered value (the
    temp init, or the inline render when `bound[i]` is False); the n pairs carry
    the operator and the per-side `{0}`-cast wraps (BigInt/float + IntEnum),
    applied to the operand REPRs (`_cmpI` or inline)."""
    inits: tuple[THIRExpr, ...]
    bound: tuple[bool, ...]
    ops: tuple[str, ...]
    left_casts: tuple['str | None', ...]
    right_casts: tuple['str | None', ...]


@dataclass(frozen=True)
class THIRIsNone(THIRExpr):
    """A `name is None` / `name is not None` identity test. On a pointer-repr
    Optional borrow name (an `Optional[record]` param or an OPTIONAL_TO_PTR
    local -- a bare `T*`) it renders the pointer comparison
    `(operand == nullptr)` / `(operand != nullptr)` -- the identity test
    over an indirect name. On a value-repr `Optional[scalar]` /
    `Optional[str]` param (`std::optional<T>` / `std::optional<std::string_view>`,
    `value_repr=True`) it renders `(!operand.has_value())` /
    `(operand.has_value())`. The operand order is canonical (the
    Optional side renders first whichever side of `is` it appears on), so the
    node carries only the Optional operand; the storage-form record sources and
    protocol slots (typed null) are gate-rejected. `result_type` is always
    bool; VALUE form.

    On an `Any` subject (`any_typeid=True`) it renders the typeid probe
    `(a.value.has_value() && a.value.type() == typeid(std::monostate))` (D15):
    the Any cell holds None as a `std::monostate` value, and an empty/moved-from
    Any is not None.

    On a union-typed NAME binding (`union_monostate=True`) it renders the
    monostate holds test `(std::holds_alternative<std::monostate>(v))` /
    `(!...)`, identical for value- and
    pointer-variant reprs (monostate is a value member in both). A
    recursive-alias WRAPPER binding (`union_wrapper=True`) reads the variant
    through `.value` (VariantAccess.variant_expr's wrapper indirection).

    On a generic `T | None` slot (`trait_repr=True`) neither spelling is
    available in the template -- the slot is an optional at a value T and a
    pointer at a reference one -- so the test renders the runtime's
    form-neutral reader `::tpy::opt_has_value(operand)`."""
    operand: THIRExpr
    negate: bool = False
    value_repr: bool = False
    any_typeid: bool = False
    union_monostate: bool = False
    union_wrapper: bool = False
    trait_repr: bool = False


@dataclass(frozen=True)
class THIRTruthy(THIRExpr):
    """A non-identity `_truthy_for_rendered` arm.

    `mode` selects one analyzer-free emit spelling. `operand` is always
    present, including in the constant-true user-record arm, which emits it as
    a discard rather than dropping it -- the render can carry effects and
    runtime checks. The bare-literal form (an already-evaluated name /
    hoisted-temp LHS) has no THIR shape: that whole
    position rejects at `valuesel.lhs_truthy`, so restore an operand-less form
    here if that gate is ever widened. `deref` is the indirect-record
    adjustment applied before dunder dispatch.
    `result_type` is always bool; VALUE form.
    """
    mode: TruthinessMode
    operand: THIRExpr
    deref: bool = False


@dataclass(frozen=True)
class THIROptViewArg(THIRExpr):
    """A value-repr `Optional[view]` param NAME (str or bytes) passed into
    another value-repr `Optional[view]` slot of the same family (a call arg or a
    return) -- the same-TPy-type ARG split.
    The borrow-form `std::optional<std::string_view>` /
    `std::optional<::tpy::BytesView>` binding is converted to the
    owned-storage `std::optional<std::string>` / `std::optional<std::vector<
    uint8_t>>` the slot's boundary needs: `x ? std::make_optional(<conv>(*x)) :
    std::nullopt`, where `<conv>` is `std::string` / `::tpy::Bytes` per the
    view family. Fires for the WHOLE optional (narrowed or not -- the slot
    type drives it, not the narrowed read). `result_type` is the Optional
    slot, whose inner drives the owned-copy spelling (`view_to_owned_conv`);
    VALUE form. `moved` wraps the rebuilt optional in `std::move(...)` --
    the consuming STORAGE positions' spelling (the setitem value)."""
    name: str = ""
    moved: bool = False


@dataclass(frozen=True)
class THIROwnOptRebuild(THIRExpr):
    """A pointer-repr `Optional[record]` INDIRECT name passed into an
    `Own[Optional[record]]` slot -- the null-safe Own conversion:
    `n ? std::optional<Inner>(std::move(*n)) : std::nullopt`.

    The binding is a `T*`, the slot is the owning `std::optional<T>`, so the
    pointee has to be moved into a fresh optional rather than dereferenced
    unconditionally (a null `n` would UB). `inner_cpp` is the pointee's
    spelling; `result_type` is the Optional slot. The shape sibling of
    `THIROptViewArg` -- same name-keyed ternary rebuild at an arg boundary,
    different payload conversion. VALUE form."""
    name: str = ""
    inner_cpp: str = ""


@dataclass(frozen=True)
class THIRMembership(THIRExpr):
    """A `needle in c` / `needle not in c` test over a dict/set container name
    whose `__contains__` is a plain @native member -- `(c.contains(needle))`,
    optionally negated `(!(c.contains(needle)))`. `method_cpp` is the
    member spelling (the
    `@native("contains")` name). The needle renders bare: the admitted
    containers carry fixed-int / owned-str keys and scalar set members, never a
    StrView key, so no view-key target applies and the needle takes the
    plain value render. `result_type` is always bool; VALUE form.

    When `free_function` is set (a bytes/BytesView container, whose
    `__contains__` is a native FREE function), the emit is
    `(::tpy::<method_cpp>(receiver, needle))` instead -- receiver and needle as
    call arguments, `method_cpp` the un-qualified native name (qualified at
    emit). Picks `bytes_contains` (single-byte needle) or `bytes_contains_sub`
    (bytes-substring needle) per the resolved overload; the needle renders in
    its owned form.

    When `ranges_contains` is set (a native container whose
    `__contains__` is NOT a resolved member -- e.g. a
    `readonly[set[T]]`, whose readonly wrapper strips the resolved member), the
    emit is `[!]std::ranges::contains(receiver, needle)` -- no outer parens, the
    negation a bare `!` prefix. `method_cpp` is unused in this form.

    When `iter_loop` is set (a user iterable with no `__contains__`, driven
    by the universal `__iter__`+`__next__` protocol), the emit is the
    fixed statement-expression loop (`({ auto&& __itr = ::tpy::__iter__(
    recv); ... __found; })`), negation the same bare `!` prefix.
    `method_cpp` is unused in this form too."""
    receiver: THIRExpr
    needle: THIRExpr
    method_cpp: str
    negate: bool = False
    free_function: bool = False
    ranges_contains: bool = False
    iter_loop: bool = False


@dataclass(frozen=True)
class THIRStrMembership(THIRExpr):
    """A `needle in s` / `not in` test over a str-family value (str/String/
    StrView), which has no `__contains__` member -- the `.find()` render:
    `(s.find(needle) != std::string::npos)` for `in`, `== std::string::npos`
    for `not in`. A str-LITERAL receiver is wrapped in `std::string_view(...)`
    (C string literals lack `.find`, `wrap_receiver_sv`); a name/field receiver
    reads bare. The needle (char or str value) renders bare. `result_type` is
    bool; VALUE form."""
    receiver: THIRExpr
    needle: THIRExpr
    negate: bool = False
    wrap_receiver_sv: bool = False


@dataclass(frozen=True)
class THIRTupleMembership(THIRExpr):
    """A `needle in (a, b, ...)` / `not in` test against a TUPLE LITERAL, which
    expands to an OR-chain of equality compares (no `__contains__`):
    `((needle == a) || (needle == b) || ...)`, negated as `(!(...))`. A
    single-element tuple drops the join parens (`(needle == a)`, negated
    `(!(needle == a))`). When the needle is a non-trivial expression AND the
    tuple has more than one element, the needle binds to a `__in_lhs` temp
    inside a GCC statement expression to keep the multiple evaluations
    side-effect-safe (`need_temp`). Elements are value-comparable (scalar /
    str / bool) so `==` renders as a plain C++ comparison. `result_type` is
    bool; VALUE form."""
    left: THIRExpr
    elements: tuple[THIRExpr, ...]
    negate: bool = False
    need_temp: bool = False


@dataclass(frozen=True)
class THIRUnaryNot(THIRExpr):
    """Logical `not` over a bool-typed operand -> `(!(operand))`. Eligibility
    pins the operand to bool, where the truthiness render reduces to the
    plain value render this wraps -- so one
    emit serves value and condition position alike. `result_type` is always
    bool. Non-bool truthiness (int / Optional / `__bool__` wrappers) is not
    lowered here; the arithmetic unaries are `THIRUnaryArith`."""
    operand: THIRExpr


@dataclass(frozen=True)
class THIRUnaryArith(THIRExpr):
    """An arithmetic unary (`- + ~`) resolved to an operator dunder and
    rendered from that resolved method alone:
    `cpp_template` is the resolved method's template (`-({self})` for
    float/int negation, `::tpy::neg_check<int32_t>({self})` for a checked
    fixed-int neg) and the emitter expands it over the lowered operand. The
    folded negated-int literal and the IntEnum-negation static_cast are
    separate arms (a plain literal / `THIREnumWrap`)."""
    cpp_template: str
    operand: THIRExpr


@dataclass(frozen=True)
class THIRIfExpr(THIRExpr):
    """Conditional expression `a if c else b` -> `((cond) ? (then) : (else))`.
    `cond` is a truthiness position (same admitted set as if/while conditions,
    where the truthy render equals the value render or the enum wrap). Arms are
    lowered against the ternary's own resolved type, not the consumer's
    target (`branch_target = result_type`), so the node needs no
    position threading; a mixed view/owned str arm pair carries the view arm's
    `std::string(...)` materialization as a THIRFormConvert built at lowering.
    `form` is load-bearing for a str-family result: BORROW (a view result --
    both arms runtime views) drives the owned-sink copy around the WHOLE
    ternary, STORAGE (an owned rvalue) lands bare; value results are VALUE."""
    cond: THIRExpr
    then: THIRExpr
    orelse: THIRExpr


@dataclass(frozen=True)
class THIRCall(THIRExpr):
    """Call to a plain free function, or -- when `callee_expr` is set -- to a
    computed callable (`make_adder(10)(5)`, `fns[i](x)`): the callee renders
    parenthesized ahead of the arg list (`(make_adder(10))(5)`). `callee`
    is empty there.

    Otherwise `callee` is the source name, and the emitter renders
    `escape_cpp_name(callee)(args)` ONLY for the `"local"` callee kind -- a
    C++ local no namespace can name (a nested def's frame lambda, a
    `Callable`/`Fn` value).

    `callee_cpp` (when set) is the callee's PRE-RENDERED absolute spelling
    (`::tpyapp::mod::f` -- `free_callee_cpp`): the emitter renders it
    verbatim over the args. EVERY call to a module-level function carries
    one, same-module and cross-module alike, because a qualified-id is what
    keeps ADL from pulling a same-named `std::`/`::tpy::` template into the
    overload set. (A C-linkage callee also rides this slot, carrying its RAW
    unqualified symbol: its `extern "C"` re-declaration is namespace-scoped,
    so a `::` would miss it.) Mutually exclusive with
    `native_name`/`cpp_template`; `callee` stays the source name for the dump.

    `native_name` (when set) is a runtime-helper C++ symbol -- an fi-resolved
    `@native` free-function builtin (e.g. `tpy::__len__` for `len(c)`) or a
    hardcoded helper (`tpy::__delitem__`
    for `del c[k]`): the emitter renders
    `qualify_native_name(native_name)(args)` instead of the bare callee, so the
    dispatch keys on the resolved symbol, not the source name (a user function
    that happens to be named `len` has `native_name=None` and stays a plain
    call).

    `cpp_template` (when set) is a scalar type-constructor call's resolved
    `__init__` template (`int32(x)` -> `::tpy::int_cast_check<int32_t>({0})`),
    already fully substituted by sema ({cpp} / class type params) so only
    positional `{0}, {1}, ...` placeholders remain -- lowering enforces that.
    The emitter expands it over the args with no receiver; `callee` is the
    source type name, kept for the dump only.

    `template_args_cpp` (when set) is a generic TPy callee's explicit
    template-arg list, pre-rendered at lowering
    (`type_to_cpp_stored` per arg -- the render that avoids C++ deduction
    against `param_val_or_ref_t<T>` slots): the emitter renders
    `callee<T1, T2>(args)` over the plain or `callee_cpp` spelling. Never
    combined with `native_name`/`cpp_template`, which take no explicit
    template args."""
    callee: str
    args: tuple[THIRExpr, ...]
    native_name: str | None = None
    cpp_template: str | None = None
    callee_cpp: str | None = None
    callee_expr: 'THIRExpr | None' = None
    template_args_cpp: tuple[str, ...] | None = None


@dataclass(frozen=True)
class THIRUnionArgLift(THIRExpr):
    """A temp-free call arg lifted inline into a pointer-variant union slot:
    a `None` literal renders the monostate
    member (`std::variant<...>{std::monostate{}}`, `value=None`), a
    member-typed record name the address-of lift (`std::variant<...>{&(name)}`;
    `deref` prepends the pointer-local/receiver indirect-name deref --
    `&((*p))` / `&((*this))`), and an
    already-union name into a deep-const slot one of the const conversions
    (`const_wrap`, chosen at lowering from how the SOURCE is bound: `as_const`
    -> `name.as_const()` for a mutable borrow, `storage` ->
    `::tpy::to_const_ptr_variant(name)` for a storage-form binding).

    `variant_cpp` is the slot's pointer-variant spelling, fixed at lowering:
    const-pointee (`::tpy::Union<const A*, ...>`) for a deep-const slot (a
    `readonly[...]` annotation or the callee's `const_borrow_params`
    verdict), the mutable spelling otherwise. Beyond that split the member
    render is const-blind (it spells the callee's variant
    whatever the source's const-ness -- a const source into a MUTABLE slot is
    a miscompile, see BUGS.md). BORROW
    form -- the variant aliases the named source.

    `temp_cpp` set marks the RVALUE branch instead: `value` is
    a member-typed ctor rvalue hoisted into a `temp_cpp __tmp_N = <value>;`
    decl at the statement flush, the variant lifting the temp's address
    (`pv{&__tmp_N}` -- no parens)."""
    variant_cpp: str
    value: THIRExpr | None = None  # None -> the monostate member
    deref: bool = False
    const_wrap: 'str | None' = None
    temp_cpp: str | None = None


@dataclass(frozen=True)
class THIROptionalPtrArg(THIRExpr):
    """A temp-free value lowered to a pointer the slot binds -- a pointer-repr
    `Optional[record]` slot (`const A*` / `A*`) or a protocols-only union ctor
    slot whose `&`-lift arm reuses this node (a record / Span name into
    `Iterable[...] | Spannable[...] | None`, `addr_of`). A call arg or a
    return value -- the same renders at both: a `None` literal renders
    `nullptr` (`value=None`; the typed-null spelling is protocol-only, and
    Optional protocol slots are gate-rejected), a record / Span name the
    address-of (`&(name)`, `addr_of`), and a storage-form Optional field read
    the `::tpy::optional_to_ptr(...)` lift (`lift`). An already-pointer name (an F2 pointer-local, a
    pointer-repr Optional binding) passes bare and never builds this node;
    the ctor-rvalue face hoists a `THIRArgTemp` with its `addr_of` wrap
    instead. The spelling is const-blind: `optional_to_ptr` selects its
    const overload from the source, `&(...)`/`nullptr` are shared, so no
    deep-const threading is needed (unlike the union lift). BORROW form --
    a pointer the slot binds."""
    value: THIRExpr | None = None  # None -> nullptr
    addr_of: bool = False
    lift: bool = False


@dataclass(frozen=True)
class THIRCtorCall(THIRExpr):
    """A user-record constructor call rvalue (`A(7)`), admitted
    only as a call arg: into an `Own[union]` value-variant slot (bare), as a
    method arg into a const same-record ref slot (`a.combine(A(9))` -- the
    method arg loop inlines the expansion, unlike the free-fn rvalue-temp
    arm), or as a `THIRArgTemp` init (the free-fn same-record ref-slot
    hoist). Renders
    `type_cpp(args)` with the RAW source
    name (no `escape_cpp_name`; lowering-enforced) for a same-module record, or
    the `record_qualification` spelling (`::ns::Name`) for an imported
    one. Args are value scalars into plain scalar slots, str-slice
    sources into view slots, or record rvalues into same-nominal record slots
    (a MUTATED ref slot carries a
    `THIRArgTemp`, a const slot the inline prvalue expansion); every other
    special arm is gate-excluded. STORAGE form -- a fresh self-contained
    value the slot's variant converting ctor consumes."""
    type_cpp: str
    args: tuple[THIRExpr, ...] = ()
    # @native_c POD aggregate init: `::Name{args}`.
    brace_init: bool = False


@dataclass(frozen=True)
class THIRVarargPack(THIRExpr):
    """A `*args` call-site pack (sema's `TpyVarargPack`): the trailing
    positional args collected into a stack
    `std::array` temp wrapped in `::tpy::varargs<E>(...)`.

    - Empty pack -> `::tpy::varargs<E>()` (no temp).
    - A sole `*expr` unpack -> the container forwarded directly:
      `::tpy::varargs<E>(inner)` for a spanlike source (`span_fn` None), or
      `::tpy::varargs<E>(::tpy::as_span(inner))` / `as_mut_span` for a
      non-span container (`span_fn` set) -- `star_source` is the lowered inner.
    - Otherwise the per-arg array: value elements land bare in
      `std::array<E, N>`; reference elements as `E*` in `std::array<E*, N>`
      (`is_ref`), an lvalue element address-taken in place (`&x`,
      `ref_lvalue[i]` True) and an rvalue element hoisted into its own
      `E __tmp = <rvalue>;` decl first (`ref_lvalue[i]` False). The array
      temp flushes before the enclosing statement (admitted only under
      `temp_args`).

    `elem_cpp` is the `varargs<...>` element spelling (`const T` for a
    readonly slot). Element exprs render position-blind (no target is
    threaded into the pack loop). VALUE form -- the pack is a fresh rvalue the
    slot consumes."""
    elem_cpp: str
    is_ref: bool = False
    args: tuple[THIRExpr, ...] = ()
    ref_lvalue: tuple[bool, ...] = ()
    star_source: 'THIRExpr | None' = None
    span_fn: str | None = None


@dataclass(frozen=True)
class THIRArgTemp(THIRExpr):
    """A call arg hoisted into a `__tmp_N` declaration flushed before the
    enclosing statement, rendering as the bare temp name at the arg position
    (or `std::move(__tmp_N)` when `move`). Three admitted rows: a
    member-valued scalar into a value-union slot
    (`std::variant<...> __tmp_N = <arg>;`, the union value branch),
    a same-module record-ctor rvalue into a same-nominal ref slot
    (`A __tmp_N = A(7);`, the free-call `is_ref_param + is_temporary_expr`
    arm), an lvalue into an `Own[T]` slot (`auto __tmp_N = <arg>;` +
    the `move` wrap -- the arg copy+move cascade; a movable NAME at
    its last use skips the temp via `THIRMove` instead), and a record-ctor
    rvalue into a pointer-repr `Optional[record]` slot (`A __tmp_N = A(7);`
    + the `addr_of` wrap -- the optional-pointer temporary face). Only
    the flushable statement positions admit it (expr stmt / var-decl init /
    name assign / scalar field write / return): a while-condition hoist is
    the stale-snapshot miscompile (BUGS.md), an elif temp breaks the flat
    `else if` chain -- both gate-rejected.

    Carries NO temp number: numbering is emit-time via the TempSink (the
    `__slot_N` precedent), drawing real numbers from the module-cumulative
    `ctx.temps` counter so every body in one module numbers
    continuously. `cpp_type` is the declared C++ type rendered at
    lowering (`None` -> `auto`, `TempState.create`'s protocol arm and the
    rows whose init type has no spelling); `brace_init` selects `{init}`
    over `= init`.
    `form` says how the temp reads at the arg position: VALUE for
    the value-union row (like a same-union name) and for a scalar Own-slot
    payload, BORROW for the record ref-slot row (a record lvalue the ref
    param binds) and the optional-ptr `addr_of` row, STORAGE for a moved
    RECORD Own-slot payload (a self-contained value the slot consumes)."""
    init: THIRExpr
    cpp_type: str | None = None
    brace_init: bool = False
    move: bool = False
    addr_of: bool = False
    # The AUDITED defer fact: the movable argument passed
    # to `TempState.create`/`create_typed` for this row, decided at lowering.
    # None = the row is UNAUDITED -- it never answered the question, so the
    # conditional-operand exit check rejects it there rather than let it
    # land at the enclosing statement.
    movable: 'bool | None' = None

    def would_bank(self) -> bool:
        """Will the conditional-operand machinery BANK this temp into the
        region (an uninit `std::optional<T>` slot plus a deferred
        `emplace`) instead of hoisting it eagerly at the enclosing
        statement? Asks the shared `banks_in_region` predicate over exactly
        what the emit hands it: the audited `movable` fact (an UNAUDITED
        row emits `movable=False`) and the slot spelling (a `None`
        cpp_type is the `auto` row, which no `std::optional` can name)."""
        # Local import: codegen_cpp.context imports thir.nodes (a genuine
        # cycle), so the printer helper cannot move to module level.
        from ..codegen_cpp.context import banks_in_region
        return banks_in_region(self.cpp_type or "auto", self.movable)


@dataclass(frozen=True)
class THIRCopy(THIRExpr):
    """A copy-construct of a reference-typed source -- the rvalue `T(x)` an
    `Own[T]` sink takes, spelled by the explicit `copy(x)` builtin and by the
    IMPLICIT copy an owning slot performs on a borrowed source alike.
    `cpp_type` is the payload's C++ spelling, a record's or a container's.
    The representation-special sources are NOT this node: a pointer-repr
    Optional peels, a ptr-variant union converts, a pointer-repr-element
    tuple builds per element."""
    value: THIRExpr = None  # type: ignore[assignment]
    cpp_type: str = ""


@dataclass(frozen=True)
class THIRConsumingIter(THIRExpr):
    """A container consumed by a for-loop whose element type is owned at last
    use (`::tpy::own_iter(std::move(<value>))`): the native auto-consuming
    iterable. `native_name` is the consuming
    `__iter__`'s C++ symbol (qualified at emit). The wrapped `value` is the
    movable container name; the result is an rvalue range, so the for-each
    captures it owning (`auto __obj_N =`, iterable_lvalue False)."""
    value: THIRExpr
    native_name: str = ""


@dataclass(frozen=True)
class THIRMove(THIRExpr):
    """A movable owned name consumed at its last use: renders
    `std::move(<value>)`. Created for an `Own[T]` call-arg slot
    (the arg `_maybe_move` arm) and for the resumable return leaf's
    direct-ready move (containers/records/expensive values move out of the
    completing frame; the emit hook unwraps it at pre-finally sites).
    Lowering creates it only when the movability + last-use facts fire
    (the `movable_locals` + `all_last_uses` reads); the
    non-movable lvalue shape hoists a `THIRArgTemp` copy instead."""
    value: THIRExpr


@dataclass(frozen=True)
class THIRDecayCopy(THIRExpr):
    """`auto(<value>)` -- the C++23 decay-copy: a still-live STORAGE
    binding at an rvalue-ref owned-tuple slot copies into a prvalue (the
    warned Own-arg copy; sema rejected the @nocopy case)."""
    value: THIRExpr


@dataclass(frozen=True)
class THIRLambda(THIRExpr):
    """A lambda expression -- a C++ closure:

        <capture>(<params>) -> <ret_cpp> { return <body>; }   (value return)
        <capture>(<params>) { <body>; }                       (void return)

    `capture_cpp` is the full `[...]` list, `params_cpp` the spelled param
    slots, both from sema's lambda facts; `ret_cpp` is None for a void body
    (emit drops the trailing type and renders the body as a bare statement).
    The body is a single lowered expression, lowered against `ret_type`.
    The by-value-capture (Callable/std::function) and readonly-param
    (key-function) param spellings are not lowered yet."""
    capture_cpp: str
    params_cpp: tuple[str, ...]
    body: THIRExpr
    ret_cpp: 'str | None' = None


@dataclass(frozen=True)
class THIRMethodCall(THIRExpr):
    """Method call on a builtin-container or user-record receiver, carrying the
    facts the call render dispatches on, materialized at lowering from the
    resolved FunctionInfo. Emit tries the arms in order: `cpp_template`
    (expanded with the receiver + args, e.g. `xs.sort()` -> `std::stable_sort(
    xs.begin(), xs.end())`), else `native_function_name` (a `@native(...,
    function=True)` free-function symbol with the receiver prepended as the
    first argument, e.g. `xs.pop()` -> `::tpy::pop_back(xs)`), else the plain
    member call `receiver.method_cpp(args)` (`method_cpp` is the `@native`
    member rename or the escaped source name, e.g. `xs.append(v)` ->
    `xs.push_back(v)`; `a.combine(b)` -> `a.combine(b)`).

    Lowering admits only pass-through shapes -- a
    bare-name receiver, value-scalar args into scalar / `Own[scalar]` slots
    (plain scalar only for user records: their non-template callees temp+move
    an Own[scalar] arg), str-slice args into non-Own str-family slots, and
    record names into non-Own same-record slots (all copied/viewed bare, no
    move / lift / temp; an `Own[str]` slot's owned-copy or `std::move(__tmp_N)`
    temp is gate-excluded) -- so the emit is a pure function of the node.
    `is_arrow` renders a user-record F2 pointer-local receiver's member access
    (`p->get()`), like THIRFieldAccess; container receivers are never
    pointer-locals. A `Ptr[T]` VALUE receiver's Deref call rides the same two
    arms, picked by sema's `ptr_non_null` fact: proven non-null -> `is_arrow`
    (`p->m(args)`), unproven -> `deref_check`. `deref_check` wraps an UNPROVEN
    pointer receiver (pointer-repr Optional borrow, or that Ptr value) in the
    runtime null check (`::tpy::deref_check(p).method(args)`) -- like
    THIRFieldAccess it is
    mutually exclusive with `is_arrow` (the checked deref yields a reference,
    read with `.`). An owned-str result (`xs.pop()`, S5) is STORAGE form,
    landing bare in owned sinks."""
    receiver: THIRExpr
    method_cpp: str
    args: tuple[THIRExpr, ...]
    native_function_name: str | None = None
    cpp_template: str | None = None
    is_arrow: bool = False
    deref_check: bool = False
    # A generic method call's explicit template args
    # (`b.transform<::tpy::BigInt>(42)`), spelled
    # at lowering via render_type over the inferred args. Composes with
    # `deref_check` (`::tpy::deref_check(p).conv<int32_t>(3)`).
    method_targs_cpp: tuple[str, ...] | None = None
    # A USER Deref-wrapper method call: N `.__deref__()` calls between the
    # receiver and the member call (`r.sum()` -> `r.__deref__().sum()`),
    # for a non-pointer receiver. A
    # pointer-local receiver joins the FIRST hop with `->`
    # (`g->__deref__().push_back(3)`, is_arrow); still exclusive with
    # deref_check (the checked deref yields a reference).
    deref_chain: int = 0
    # A consuming method's receiver move (`std::move(b1).take()` --
    # the is_consuming wrap). Lowering admits a bare
    # non-narrowed NAME receiver; a pointer-local one moves its deref
    # (`std::move(*w).take()` -- the emit folds is_arrow into the deref).
    move_receiver: bool = False
    # An Optional[Callable] FIELD invocation's `.value()` unwrap between
    # the member and the call (`(*this).on_event.value()(msg)`): the
    # unwrap is unconditional and narrowing-blind.
    callable_value_unwrap: bool = False

    def __post_init__(self) -> None:
        assert not (self.deref_check and self.is_arrow)
        assert not (self.deref_chain and self.deref_check)
        # move_receiver composes with is_arrow (a pointer-local receiver
        # moves its deref: `std::move(*w).take()`), never with the checked
        # or user-Deref chains.
        assert not (self.move_receiver
                    and (self.deref_check or self.deref_chain))

    @property
    def receiver_through_pointer(self) -> bool:
        """Whether emit reaches the member THROUGH the receiver pointer
        (`recv->m()`, or move_receiver's `std::move(*recv)` fold). The
        cpp_template and native-free-function arms interpolate the receiver
        into a VALUE slot and never read `is_arrow`, so a pointer receiver
        must arrive already dereferenced there."""
        return (self.is_arrow and self.cpp_template is None
                and self.native_function_name is None)


@dataclass(frozen=True)
class THIRContainerLiteral(THIRExpr):
    """A container-literal local initializer, emitted per the sema-RESOLVED
    container family in `result_type` (the vector-vs-array decision for a list
    literal -- sema's PendingListType resolution -- is already final at lowering).
    The scalar element branches:

    - list / `Array[T, N]` -> `{e1, e2}` (brace-init consumed by the spelled
      decl type); an empty LIST spells the type (`std::vector<T>{}`, the
      T*-assignment-ambiguity guard)
    - dict -> `::tpy::ordered_map<K, V>({{k1, v1}, ...})`; empty -> `()`
    - set -> `::tpy::ordered_set<T>({e1, e2})`; empty -> `()`

    `values` is used only by the dict family (zipped with `elements` as keys).
    Elements are value scalars, str/bytes-slice values (S5/S6: a view-form
    source into an owned element slot arrives wrapped in the view->owned
    `THIRFormConvert` -- `std::string(x)` / `::tpy::Bytes(x)`), enums,
    Optional[scalar] (a None element is the STORAGE-form `std::nullopt`
    literal), value-tuple literals (`THIRTupleLiteral`), nested list literals
    (a nested `THIRContainerLiteral`; a demoted-Array outer adds the extra
    aggregate brace level), and F1 records (ctor rvalues and names; a movable
    name at its last use arrives wrapped in `THIRMove`).

    `make_container` is the non-copyable / last-use-movable
    switch: std::initializer_list elements are const, so a `std::move` in a
    brace-init would silently copy -- the emit uses the reserve+emplace
    helpers instead (`::tpy::make_vector<elem_cpp>(...)` for list, the
    element type spelled via the resolver at lowering;
    `::tpy::make_ordered_map`/`make_ordered_set` for dict/set, spelled from
    result_type like the brace arms). std::array aggregate-init moves fine,
    so the Array family never sets it. The union / protocol element branches
    are gate-excluded.

    `typed_brace_cpp` applies `typed_brace_init` at the positions whose
    consumer cannot list-init from a bare brace: a template that cannot
    deduce it (the dict-comp `insert_or_assign` value slot) and the ctor
    member-init cell, a paren direct-init where the brace would be an
    argument to the field type's own constructors (`xs({1})` into a
    `vector<BigInt>` picks the size constructor). The resolver-rendered
    destination type, prefixed onto the render ONLY when it starts with `{`
    (the make_container / empty-list spellings are already
    self-describing).

    `bare_empty` drops the empty-LIST type spelling: at an immediate
    container-element slot the outer brace supplies the element type, so `{}`
    deduces there and the T*-assignment ambiguity the spelling guards against
    cannot arise. It presumes a BRACE-init parent -- under `make_container`
    each element deduces from its own argument, and lowering rejects an empty
    element there rather than emitting an undeducible `{}`."""
    elements: tuple[THIRExpr, ...]
    values: tuple[THIRExpr, ...] = ()
    make_container: bool = False
    elem_cpp: str | None = None
    typed_brace_cpp: str | None = None
    bare_empty: bool = False


@dataclass(frozen=True)
class THIRListRepeat(THIRExpr):
    """`[elems] * count`. Element children lower
    through the container-element wraps with the move SUPPRESSED (one source is
    copied into every slot; a move would use-after-move slots 1..N-1). The emit
    dispatches on `result_type`'s family (like `THIRContainerLiteral`):

    - list (materialized) ->
      `::tpy::from_range<result_cpp>(::tpy::repeat_range<elem_cpp>(count, {elems}))`
    - `Array[T, N]` -> the `({ ... array_from_index ...; })` statement-expression
      (the comprehension array-demotion arm). One element:
      `elem_cpp __rep_N = e0;` + `[&](std::size_t) -> elem_cpp { return __rep_N; }`;
      k>1: `std::array<elem_cpp, k> __rep_N{elems};` +
      `[&](std::size_t __i_N) -> elem_cpp { return __rep_N[__i_N % k]; }`. The
      `__rep_N` index draws the per-function `iter_counter` at EMIT (a
      single draw for the whole shape), so it is NOT baked into the node.

    - lazy `ListRepeatType` -> the bare `repeat_range` (no `from_range` wrap):
      the unmaterialized `[v] * n` binding.

    `count_bigint` appends `.to_fixed_check<int32_t>()` (repeat_range's count is
    int32_t). `count`/`result_cpp` are unused by the Array shape (the size rides
    `array_size_cpp` in the template)."""
    elements: tuple[THIRExpr, ...] = ()
    count: 'THIRExpr | None' = None
    count_bigint: bool = False
    elem_cpp: str = ""
    result_cpp: str = ""
    array_size_cpp: str = ""
    lazy: bool = False


@dataclass(frozen=True)
class THIRTupleLiteral(THIRExpr):
    """A value-tuple literal `(a, b)` at a fully-targeted slot -- the
    all-VALUE-elements path (`has_ref_elements`
    False): the spelled `std::tuple<...>{e1, e2}` render, position-independent
    (return / decl init / call arg). `result_type` is the SLOT TupleType, so
    the spelled type is the target's resolved element list. Elements are
    value scalars
    or owned-str values, lowered per element slot (`_lower_container_elem`:
    target-typed literal retypes -- the BigInt ctor wraps / float32 `f`
    suffix -- and the S1 view->owned `std::string(x)` wrap for view-form str
    sources). Ref/const-ref element captures, borrow element slots
    (pointer-repr Optional / record refs), TypeParamRef elements, and
    target-less positions are gate-excluded."""
    elements: tuple[THIRExpr, ...]


@dataclass(frozen=True)
class THIRBorrowTupleLiteral(THIRExpr):
    """A tuple literal at a BORROW-form slot (`std::tuple<..., T*>`) -- the
    ref-element path reduced to its lvalue subset:
    value elements render bare into their value slots, pointer-repr
    lvalue-NAME elements lift `&(name)` (an already-pointer name passes
    bare). `spelled_cpp` is the slot spelling from the slot-info ladder
    (`std::tuple<int32_t, Box*>`), carried whole because the borrow spelling
    is per-element-mode, not derivable from `result_type.to_cpp()`.
    `addr_of[i]` marks the elements the emit wraps `&(...)`. `elem_wraps[i]`
    is the third element treatment: a positional `{0}` render template (the
    generic slot's `::tpy::to_val_or_ptr<val_or_ptr_t<T>>({0})` wrap,
    computed at lowering like THIRCoerce's TEMPLATE shape); empty tuple for
    the borrow-only callers, and exclusive with `addr_of[i]` per element.
    Rvalue borrow elements (the tuple_value_to_borrow helper machinery) and
    pointer-repr Optional / union slots stay gate-rejected."""
    spelled_cpp: str
    elements: tuple[THIRExpr, ...]
    addr_of: tuple[bool, ...]
    elem_wraps: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        assert not any(w is not None and a
                       for w, a in zip(self.elem_wraps, self.addr_of))


@dataclass(frozen=True)
class THIRTupleValueToBorrow(THIRExpr):
    """A tuple literal with RVALUE elements at a borrow-form slot -- the
    `tuple_value_to_borrow` path: a value-form source
    tuple is built inline (`src_cpp{...}`; its full-expression lifetime keeps
    the addresses valid through the consuming call) and the helper takes
    addresses / binds references into the borrow-form `dst_cpp`. Rvalue
    elements render VALUE-form (bare, no lift); lvalue elements keep their
    borrow render + `&(...)` lift inside the source tuple (their src slot is
    already the pointer part)."""
    dst_cpp: str
    src_cpp: str
    elements: tuple[THIRExpr, ...]
    addr_of: tuple[bool, ...]
    # Per-element `{0}` render template, exclusive with addr_of[i] (the
    # consuming append's `std::move(&({0}))` lvalue wrap); None entries keep
    # the addr_of/bare treatment. None = no wraps at all.
    elem_wraps: 'tuple[str | None, ...] | None' = None

    def __post_init__(self) -> None:
        # Same contract as THIRBorrowTupleLiteral: a wrap replaces the
        # element's whole render, so it is exclusive with the addr_of lift.
        if self.elem_wraps:
            assert not any(w is not None and a
                           for w, a in zip(self.elem_wraps, self.addr_of))


@dataclass(frozen=True)
class THIRRecordCopy(THIRExpr):
    """An explicit `copy(x)` of an F1 record rendered as the copy-ctor call
    `T(x)`. Reachable today
    only as a pointer-repr tuple-literal ELEMENT (the MIL tuple cell); every
    other admitted `copy()` position unwraps to the bare source instead
    (direct-init / MIL copies implicitly). `cpp_type` is the copied record's
    spelled type (`arg_type.to_cpp()`)."""
    value: THIRExpr
    cpp_type: str


@dataclass(frozen=True)
class THIRComprehension(THIRExpr):
    """A list/set/dict comprehension at a fresh local's decl-init -- a GCC
    statement-expression IIFE (the C1+C2 slice):

        ({ <container_cpp> __result; <loop head> { <binding>
           [if (c1 && c2) {] <insert>; [}] } std::move(__result); })

    Loop arms: `range` (1/2-arg counter loop; each NON-literal bound hoists
    its own `const <counter> __start/__stop_N = ...;` -- NB the comprehension
    emitter draws one loop index PER bound, unlike the statement range-for's
    single draw) and `begin_end` (`__obj_N` capture with the lvalue verdict,
    `__beg_N`/`__end_N`, the shared `loop_var_binding` or the inline
    tuple-unpack `__tup_N` lines). A 3-arg range iterates begin/end over
    the Range OBJECT (`iterable` is the substituted `::tpy::Range<T>(...)`
    template call, an rvalue capture).
    A list result reserves (`sized_reserve`
    for begin/end over sized iterables; the range arms' `> 0` / BigInt
    `to_size_checked` guards); set/dict skip the reserve.
    Inserts: `push_back(elem)` / `insert(elem)` / `insert_or_assign(k, v)`;
    elements arrive through the S5 per-slot owned-str wrap. Gate-excluded:
    owned-move elements (`owns_elements` -- the `__dk_N` key-sequencing and
    move-sink arms), Array demotion (`array_from_index`), genexpr,
    temp-producing elements/filters (the comprehension admission helpers admit
    none),
    narrowed-Optional iterables. The multi-line render reads
    the enclosing statement indent off `_EmitState.stmt_indent_level`."""
    kind: str = ""                        # "list" | "set" | "dict"
    container_cpp: str = ""               # spelled result container type
    var: str = ""                         # loop var (source name)
    loop: str = ""                        # "range" | "begin_end"
    elem_type: 'TpyType | None' = None    # loop-var binding type (begin_end)
    const_loop_var: bool = False
    counter_cpp: str = ""                 # range counter spelling
    counter_bigint: bool = False          # BigInt reserve arm
    range_start: 'THIRExpr | None' = None  # None for 1-arg range
    range_stop: 'THIRExpr | None' = None
    range_start_literal: bool = False     # bare TpyIntLiteral bounds inline
    range_stop_literal: bool = False
    # The array_from_index range arm (loop="array_range"): per-index lambda,
    # `E var = start + E(__i_N) * (step); return elem;` -- the Array-demoted
    # comprehension (the stop bound is encoded in N, never rendered).
    array_elem_cpp: str = ""              # the array element spelling
    array_size_cpp: str = ""              # the N template arg spelling
    range_step: 'THIRExpr | None' = None  # 3-arg range step (untargeted render)
    iterable: 'THIRExpr | None' = None    # begin_end only
    iterable_lvalue: bool = True
    sized_reserve: bool = False           # list over a sized begin_end iterable
    unpack_targets: tuple = ()            # ('a', None, 'b') -- None = discard
    unpack_target_cpps: tuple = ()
    conditions: tuple = ()                # &&-joined filter conditions
    element: 'THIRExpr | None' = None     # list/set insert value
    key: 'THIRExpr | None' = None         # dict
    value: 'THIRExpr | None' = None       # dict
    # Owned-move dict: the value (last sink) moved, so the key is sequenced into
    # `__dk_N` first (insert_or_assign leaves its two args unsequenced, so a key
    # reading the moved-from loop var would be a use-after-move).
    value_moved: bool = False


@dataclass(frozen=True)
class THIRGenExpr(THIRExpr):
    """A lazy generator expression (`x > 0 for x in xs`) as a make_generator
    render -- an argument to a native Iterable consumer (`all`/`any`/`sum`),
    a for-head, a container ctor. An outer IIFE captures the refs
    (`iife_captures`), binds the source, and returns `make_generator<slot>`
    over an inner mutable lambda that binds each element (`binding_cpp`, or
    the unpack head) and yields `std::optional<slot>(<element>)` until
    exhausted; `inner_captures` are the outer locals the element / filters
    read. One flag decides how the source is bound:

    * BORROWED (`owned_source` False -- an lvalue: a name, a field, a
      borrow-returning call): the IIFE aliases it (`auto& __src =
      <iterable>;`) and the lambda seeds `__beg`/`__end` in its
      init-captures, advancing with `*__beg++`.
    * OWNED (`owned_source` True -- every rvalue: a container literal, a dict
      view, a native combinator, a generator call, an `Own[container]` call):
      the IIFE is the wrapper's in-place factory
      (`make_generator<slot>(std::in_place, [caps]() { return <lambda>; })`)
      and the lambda's init-capture builds the source once, eagerly, inside
      a `::tpy::genexpr_state` holder (`__st = ::tpy::genexpr_state{<iterable>}`
      -- CPython's `iter()` runs at construction too). Nothing is ever
      moved: the source is aggregate-initialized from its prvalue, the
      closure is returned as a prvalue and the wrapper constructs it in
      place (all guaranteed elision), because the runtime's owning
      combinators (`zip` / `enumerate` / `filter` over a generator) alias
      their own slot and delete their move ctor. The lambda seeds
      `__st.beg` (an `optional<begin_iter_t<S>>` -- an iterator need not be
      default-constructible) on its first pull, so a MOVABLE source's
      closure may still be moved before then. The element binds off
      `*(*__st.beg)` and the ADVANCE is deferred to the next pull: a
      one-pass source (`NextIterator` over a combinator or a generator
      frame) has no postfix `++`, its deref aliases the iterator's own slot
      until the next advance, and advancing before the `return` would pull
      one source element ahead of the consumer where CPython pulls lazily.

    Movability of the owned form's closure is the holder's: a MOVABLE
    source (a container literal, a dict view, an `Own[container]` call, a
    generator frame -- unstarted, so it has no self-pointer yet -- or a
    combinator over lvalue arguments) may be moved by an owning consumer
    BEFORE the first pull (the seed is lazy, so nothing points into
    `__st.src` yet); after the first pull nothing moves it, since every
    consumer that stores the closure moves it at construction. A
    NON-movable source (`nonmovable_source`: a native combinator whose
    owning flavor was selected by a non-lvalue argument, which deletes its
    move ctor) is rejected at any owning boundary
    (`genexpr.nonmovable_into_owning` -- another lazy combinator taking the
    genexpr as its rvalue argument), until the runtime's producers become
    movable while unstarted (TODO.md). A local binding (`g = (...)`) is not
    lowered.

    The multi-line render reads the enclosing statement indent off
    `_EmitState.stmt_indent_level`. Range sources take the counter-lambda
    flavor below (no IIFE)."""
    iterable: 'THIRExpr | None' = None       # the lowered source (both forms)
    element: 'THIRExpr | None' = None
    slot_cpp: str = ""
    binding_cpp: str = ""
    iife_captures: str = ""
    inner_captures: str = ""
    owned_source: bool = False               # rvalue source: held in __st.src
    # The holder cannot be moved even before the first pull (its source is a
    # combinator's owning flavor, whose move ctor is deleted): decided by the
    # source route, consumed by the owning-boundary reject.
    nonmovable_source: bool = False
    # Tuple-unpack head (`n for p, n in items`): the lambda body binds
    # `auto& __tup_N = <deref>;` (the shared __tup counter, drawn at emit)
    # plus one line per named target (`unpack_target_cpps[i] name =
    # std::get<i>(__tup_N);` -- None targets are `_` discards). Empty for
    # the single-loop-var shape (binding_cpp).
    unpack_targets: tuple = ()
    unpack_target_cpps: tuple = ()
    const_loop_var: bool = False
    # Filter conditions (`x for x in xs if x > t`): &&-joined truthy exprs
    # wrapping the yield (`if (...) { return optional<slot>(elem); }` --
    # the conditional yield arm; cond/yield temps flush inside
    # the lambda body at their own indents).
    conditions: tuple = ()
    # RANGE source (`x for x in range(...)`): the counter-lambda flavor.
    # `range_args` are the lowered bounds
    # (1-3, each cast `static_cast<counter_cpp>(...)` in the init-captures);
    # `binding_cpp` carries the pre-rendered `{counter} {var} = __i++;`
    # (2-arg) / `= __i;` (3-arg, the emit adds `__i += __step;`); nargs==3
    # adds the step checks (`range_overflow_check` gates the fixed-int
    # overflow probe).
    range_args: tuple = ()
    counter_cpp: str = ""
    range_overflow_check: bool = False


@dataclass(frozen=True)
class THIRCoerce(THIRExpr):
    """A sema-inserted coercion made explicit on the IR. Two emit shapes:

    * PASSTHROUGH (`wrap is None`): the literal-into-typed-slot pair
      (`int_literal_to_fixed_int`, `float_literal_to_float`) and the identity
      positions of the str-family cross-type coercions (see lower.py
      `_coerce_disposition`), so the inner expression renders directly in the
      target type.
    * TEMPLATE (`wrap` set): the scalar-cast family (`static_cast<float>({0})`
      and friends) -- the coercion rendered as a positional
      `{0}` template computed at lowering, where the target type's C++
      spelling is at hand.

    A MATERIALIZING position (`std::string(x)`) never reaches this node -- it
    lowers to the view->owned `THIRFormConvert` instead. `form` is the wrapped
    expression's form (a scalar cast changes type, never the value's shape),
    EXCEPT a view-target coerce (`*_to_strview`), whose value is a view into
    the source's buffer whatever the source's form -- it sets BORROW itself."""
    expr: THIRExpr
    coercion_name: str
    wrap: 'str | None' = None


@dataclass(frozen=True)
class THIREnumMember(THIRExpr):
    """Type-level enum member access `E.A` -> `E::A`. `cpp` is the full
    rendered spelling (enum_cpp_name over the current module + the @native
    member rename map), computed at lowering where the module context lives."""
    cpp: str


@dataclass(frozen=True)
class THIRConceptTest(THIRExpr):
    """A compile-time protocol-isinstance condition -- the concept
    constraint an `if constexpr` tests (`::tpy::Sized<T_items>`, the
    Optional[Protocol] `!std::same_as<T_x, std::nullptr_t>` special, or
    either under `(!...)` negation). `cpp` is the full rendered spelling,
    computed at lowering via the codegen concept renderer hook
    (`_concept_constraint`)."""
    cpp: str


@dataclass(frozen=True)
class THIRClassConstant(THIRExpr):
    """Class-constant read `C.X` / `c.X` / `mod.C.X` -> the bare qualified
    static (`C::LIMIT`, `::tpyapp::m::Limits::MAX`, `C<int32_t>::X`). `cpp`
    is the full spelling composed at lowering (native rename, generic
    instantiation, cross-module qualification).
    An effectful / runtime-checked INSTANCE
    receiver carries `recv_eval` + `recv_wrap` (the statement-expression
    wrapper: `({ <wrap(recv)>; C::LIMIT; })`, wrap `::tpy::deref_check({0})`
    for the unproven-Optional check or `static_cast<void>({0})` for the
    effect discard -- the receiver_eval split). `form`
    follows the constant's type like a name read: a `StrView` constant is a
    view (BORROW -- owned-str sinks copy it)."""
    cpp: str
    recv_eval: 'THIRExpr | None' = None
    recv_wrap: 'str | None' = None

    def __post_init__(self) -> None:
        assert (self.recv_eval is None) == (self.recv_wrap is None)


@dataclass(frozen=True)
class THIRModuleVar(THIRExpr):
    """Module-variable read `mod.X` / `pkg.sub.X` -> the fixed spelling from
    the source module's registration (`::tpyapp::pkg::state::LIMIT`, a
    native_global's `::engine::score`), composed at lowering from the
    registry's VariableInfo. A dedicated leaf (not a THIRName) so no
    local-name-keyed sink can mistake it for a binding."""
    cpp: str


@dataclass(frozen=True)
class THIREnumWrap(THIRExpr):
    """An enum-value render through a `{0}` wrap computed at lowering:

      * `.value`         -- `static_cast<U>({0})` (U = the underlying type);
      * `.name`          -- `::tpy::EnumUtil<E>::name({0})` (a static-storage
                            `string_view`; BORROW form, so owned-str sinks
                            fire the S1 view->owned copy);
      * IntEnum `-x`     -- `(-static_cast<U>({0}))`;
      * IntEnum truthy   -- `(static_cast<U>({0}) != 0)` (condition / `not`);
      * `E[name]`        -- `::tpy::EnumUtil<E>::from_name({0})` (name-lookup
                            subscript; `{0}` is the str index).

    A PLAIN-enum truthiness test is always true, so its wrap is
    `(static_cast<void>({0}), true)` -- the value is a constant but the
    operand still evaluates. Every arm fills the `{0}` slot, so `operand` is
    never absent."""
    wrap: str
    operand: THIRExpr


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
    mutually exclusive.

    `narrowed_deref` wraps the WHOLE access in `(*...)`: a sema-NARROWED
    `Optional` field read (declared `std::optional<T>` storage, analyzed
    non-Optional) unwraps unconditionally in value positions. Plain-assign
    targets and the `print_optional_val` wrap read the bare storage instead;
    those consumers strip the flag.

    `deref_chain` (>0) inserts N `__deref__()` calls between the receiver and
    the field -- a field access through a USER Deref-style wrapper
    (`r.x` -> `r.__deref__().x`). A
    pointer-local receiver (proven narrowed-Optional / F2-reseated `T*`)
    joins the first hop with `->` via `is_arrow`
    (`r->__deref__().x`), like the method twin."""
    receiver: THIRExpr
    field_cpp: str
    is_arrow: bool = False
    deref_check: bool = False
    narrowed_deref: bool = False
    deref_chain: int = 0
    # An unproven access whose receiver is a WHOLE value-repr Optional
    # lvalue (an Optional[record] field read, `h.opt.x`): wraps the receiver
    # in `::tpy::deref_optional_check(...)` -- the optional-lvalue sibling
    # of `deref_check` (whose receiver is already a `T*`).
    opt_deref_check: bool = False

    def __post_init__(self) -> None:
        # Enforce the deref_check/is_arrow mutual exclusivity the docstring documents.
        assert not (self.deref_check and self.is_arrow)
        # A narrowed field is proven non-None; the runtime check never coexists.
        assert not (self.deref_check and self.narrowed_deref)
        # The user-Deref chain never coexists with the runtime check (a
        # checked receiver takes deref_check); `is_arrow` MAY join it (the
        # indirect first hop).
        assert not (self.deref_chain and self.deref_check)
        # The optional-lvalue check is its own exclusive receiver wrap.
        assert not (self.opt_deref_check
                    and (self.deref_check or self.is_arrow
                         or self.narrowed_deref or self.deref_chain))

    @property
    def receiver_through_pointer(self) -> bool:
        """Whether emit reaches the member THROUGH the receiver pointer
        (`recv->field`, or the first hop of a user-Deref chain), so a
        receiver that dereferences itself would compose into the ill-formed
        `(*recv)->field`. The two runtime-check wraps consume the receiver
        as an argument instead and are already exclusive with `is_arrow`."""
        return self.is_arrow and not (self.deref_check or self.opt_deref_check)


@dataclass(frozen=True)
class THIRSubscript(THIRExpr):
    """Subscript read `receiver[index]`, dispatched at emit on the receiver's
    resolved type family (tuple vs container).

    Tuple -- `std::get<N>(receiver)`. `index` is a `THIRLiteral` holding the element
    offset, already normalized to a non-negative int at lowering (a negative literal
    `t[-1]` folds by the tuple arity). `bounds_safe` is unused (a validated const offset
    is trivially in-bounds). `form` records the element's shape -- `VALUE` for a
    value-scalar element, `BORROW` for a
    plain-record element (a `T*`/`T&` consumed by one member access, whose `->` vs `.`
    the field access decides) or an `Optional` element off a borrow tuple param (a
    nullable `T*`), and `STORAGE` for an `Optional` element off a storage-tuple alias
    (a `std::optional<T>` lifted to a borrow via `THIRFormConvert`/`optional_to_ptr`
    at the consuming `deref_check`).

    Container (list / dict), str-family (`s[i]` -> char), or bytes-family
    (`b[i]` -> uint8) -- a runtime
    index/key lookup, `form` VALUE (a value-scalar / char element). A str
    element/value read (`xs[i]` on `list[str]`, `d[k]` on a str-valued dict,
    S5) carries its resolved shape instead: BORROW when the read's view var
    resolved `StrView` (drives the S1 owned-sink `std::string(x)` copy),
    STORAGE when it resolved owned (bare -- the `const std::string&` element
    copies implicitly at owned sinks). `index` is
    the lowered index expression; `bounds_safe` (sema value-range analysis)
    picks the emit -- `receiver[static_cast<std::size_t>(index)]` when proven
    in-bounds (a literal index needs no cast), else the checked dunder
    `::tpy::__getitem__(receiver, index)` (str's `__getitem__` @cpp_template
    spells the same dunder, so one emit covers both; a BYTES receiver instead
    dispatches to `::tpy::bytes_getitem(receiver, index)` -- bytes'
    `__getitem__(int32)` is a @native free-function dunder). The index is a
    value
    scalar (a runtime-BigInt one arrives pre-wrapped in its
    `.to_fixed_check<int32_t>()` THIRCoerce from lowering) or, for an
    owned-str-keyed dict, a
    str-slice expr rendered bare in the key slot (the static-storage literal
    pin fires only for view-typed keys, which the gate excludes)."""
    receiver: THIRExpr
    index: THIRExpr
    bounds_safe: bool = False
    # A GENERIC tuple element read (`p[0]` on `tuple[T, T]`): the val_or_ptr
    # slot reads through `::tpy::tuple_elem_ref(std::get<N>(p))` (deref at
    # instantiation for non-value T) -- the TypeParamRef arm.
    elem_ref: bool = False
    # A user-record `__getitem__` subscript -> the record's generated C++
    # `operator[]`, spelled bare `receiver[index]` over the plainly-rendered
    # index (no size_t cast -- the operator takes the user's declared key type,
    # like the concrete-user-record / fi-fallback arms).
    record_getitem: bool = False
    # An UNPROVEN value-repr Optional[scalar] ELEMENT read consumed as its
    # inner scalar: wraps `::tpy::deref_optional_check(<read>)` (the
    # runtime-checked unwrap), the subscript twin of THIRName's flag.
    opt_deref_check: bool = False
    # A borrow-form tuple's pointer-repr element consumed as its REFERENT
    # (`std::get<N>` yields the element `T*`; a `T&` alias bind needs
    # `(*std::get<N>(t))`) -- the deref twin of THIRName's flag.
    deref: bool = False


@dataclass(frozen=True)
class THIRStrSlice(THIRExpr):
    """A str/bytes slice off a str/bytes-family receiver, emitted via the
    sema-resolved slice `__getitem__`'s `@cpp_template` expanded over the
    receiver and the slice argument (the slice arm;
    the template carried on the node makes the emit family-neutral -- bytes
    carries `::tpy::bytes_slice` / `::tpy::bytes_stepped_slice`). Three index
    shapes:

      * non-stepped `s[a:b]` -- `::tpy::str_slice({self}, {0})` over a
        `::tpy::BasicSlice{lo, hi}` initializer; a `std::string_view` /
        `::tpy::BytesView` VIEW result (`form` BORROW).
      * stepped `s[a:b:c]` (`stepped`) -- `::tpy::str_stepped_slice` over a
        `::tpy::Slice{lo, hi, step}` initializer; the family's OWNED type
        (`std::string` / `std::vector<uint8_t>`, `form` STORAGE), landing
        bare in every owned sink.
      * slice-typed variable index `s[sl]` (`index`) -- the index expression
        rendered bare into the template (`::tpy::str_slice(s, sl)`); the
        view/owned result follows the resolved overload (basic_slice -> view,
        slice -> owned).

    The receiver is a str/bytes name, a str/bytes-family field off an
    F1-record receiver, or an eligible owned/view-returning call (all render
    bare into `{self}`). An absent bound renders `std::nullopt`.
    A VIEW result is consumed at view sinks or
    materialized at an owned sink by the view->owned `THIRFormConvert` keyed
    on the BORROW form -- str: a sema `strview_to_str` TpyCoerce (decl init /
    return) lowered via `_coerce_disposition` to `std::string(...)`; bytes: a
    coerce-less owned decl init wrapped `::tpy::Bytes(...)` at lowering
    (an owned bytes RETURN arrives as the gate-rejected `bytesview_to_bytes`
    coerce, which is not lowered yet). Bounds are
    eligible fixed-int value exprs rendered bare (a BigInt bound's
    `.to_fixed_check` narrow is gate-excluded)."""
    receiver: THIRExpr
    cpp_template: str
    lower: THIRExpr | None = None
    upper: THIRExpr | None = None
    step: THIRExpr | None = None
    stepped: bool = False
    index: THIRExpr | None = None


@dataclass(frozen=True)
class THIRIsinstance(THIRExpr):
    """`isinstance(v, A)` / `isinstance(v, (A, B))` over a routed union
    local/param -> `std::holds_alternative<M>(v)` per check member, OR-joined
    and parenthesized for the multi-member form, over value or pointer
    variants (F4 U3). `member_cpps` are the final
    template args (the `*` suffix and `const` prefix already applied for
    pointer variants at lowering, like VariantAccess._type_arg);
    `variant_cpp` is the source spelled as the bare Python name -- the
    narrowed_vars alias is deliberately skipped, and the
    slice excludes indirect / frame-slot sources."""
    variant_cpp: str
    member_cpps: tuple[str, ...]


@dataclass(frozen=True)
class THIRDynIsinstanceMulti(THIRExpr):
    """The TUPLE form of a polymorphic isinstance condition --
    `isinstance(v, (A, B))` -> the no-init OR-chain
    `((dynamic_cast<const A*>(v) != nullptr) || (...))`. `checks_cpp` are
    the pre-rendered per-member null-checks (the shared `narrow_cast_rhs`
    chokepoint at lowering); no extraction alias exists (the branch fact is
    the checked union)."""
    checks_cpp: tuple[str, ...] = ()


@dataclass(frozen=True)
class THIRAnyIsinstance(THIRExpr):
    """`isinstance(v, A)` / `isinstance(v, (A, B))` over an Any-typed subject
    (D15) -> the has_value guard + typeid check(s):

        (v.value.has_value() && v.value.type() == typeid(T))
        (v.value.has_value() && (..typeid(A) || ..typeid(B)))

    -- the isinstance Any branch. `subject_cpp` is the bare
    Python name (the slice excludes indirect / frame-slot subjects, like the
    variant sibling `THIRIsinstance`)."""
    subject_cpp: str
    member_cpps: tuple[str, ...]


@dataclass(frozen=True)
class THIRDynIsinstance(THIRExpr):
    """Polymorphic isinstance over a @dynamic-dispatch subject (a dyn-protocol
    ref or polymorphic base class) -> the C++17 if-init render, emitted
    whole inside the if-condition parens:

        [const ]Sub* __p_ptr = <narrow_cast_rhs>; (__p_ptr != nullptr)

    `init_cpp` is the full init declaration (type, ptr local, cast RHS --
    composed at lowering via the shared `narrow_cast_rhs` chokepoint);
    `ptr_local` is the pre-bound cast pointer every branch read of the
    subject renders through (`(*__p_ptr)` via THIRName.cpp)."""
    init_cpp: str
    ptr_local: str


@dataclass(frozen=True)
class THIRNarrowedRead(THIRExpr):
    """A condition-position read of an isinstance-narrowed subject (F4 U4
    compound conditions): no extraction alias exists yet, so the read renders
    as the bare get -- `(*std::get<A*>(v))` (pointer variant, parenthesized
    for member access) / `std::get<T>(v)` (value variant). The expression
    sibling of `THIRNarrowAlias` (same structural fields); only ever produced
    inside a compound narrowing condition, after its isinstance leaf."""
    variant_cpp: str
    member_cpp: str
    is_ptr_variant: bool


@dataclass(frozen=True)
class THIRFormConvert(THIRExpr):
    """An explicit borrow<->storage form conversion (IR_DESIGN "THIRFormConvert").

    Preserves the type FAMILY and changes `form` (vs `THIRCoerce`, an emit
    passthrough that changes only the type). Within a view family the
    view->owned copy may also respell the type (`StrView` -> `str`/`String`,
    the materializing str-family coercions): the respelling IS the form change
    materialized in the type system, carried on `result_type`, and the
    `std::string(x)` emit stays one chokepoint. F1 covers the Optional
    storage->borrow read (`::tpy::optional_to_ptr`).

    `materialize` tags the "copy a VIEW into an owning buffer" meaning where
    lowering DECIDES it. For every type but `bytearray` the runtime helper is
    a pure function of (family(result_type), value.form -> form, is_const,
    move), and `None` keeps that derivation. `bytearray` breaks it -- the one
    owned member of a view family that is a REFERENCE type, so a borrow-form
    `bytes` value is a span while a borrow-form `bytearray` value is an
    object reference; the same (family, BORROW->STORAGE) pair thus has two
    correct renders (`::tpy::Bytes(view)` vs move/copy the object) and
    a bytearray-result STORAGE convert MUST carry an explicit True/False
    (validate.py enforces it).

    An OPEN-`T` result type is the third shape: the value arrives in the
    instantiation's PARAMETER form (`param_val_or_ref_t<T>`, a view for `str`
    and `bytes`) and the sink spells `T` storage, so the construction is
    explicit -- `::tpy::param_to_storage<T>` -- keyed on `T` alone.
    `generic_return` picks the return sink's sibling
    (`::tpy::param_to_return<T>`), which must NOT copy at a reference-typed
    instantiation: there `val_or_ref_t<T>` is `T&` and an owned temporary
    would dangle."""
    value: THIRExpr
    is_const: bool = False
    move: bool = False
    materialize: 'bool | None' = None
    generic_return: bool = False


# --- Statements ---


@dataclass(frozen=True)
class THIRVarDecl(THIRStmt):
    """Local declaration with initializer (`name: T = init`).

    `cpp_type` is the rendered C++ declaration type for non-value locals (where
    `resolved_type.to_cpp()` is insufficient -- e.g. the inner type of a
    pointer-local Optional); None for the value-scalar slice (emit defaults to
    `resolved_type.to_cpp()`). `form` is the coarse semantic form of the local --
    `BORROW` for the F1 `T&` alias / `T*` optional-read locals -- and drives the
    insertion rule. `cpp_local_representation` is the `LocalCppForm` analog
    carried verbatim: non-semantic COMPATIBILITY metadata that selects the exact
    C++ slot shape (REF_ALIAS `T&` vs OPTIONAL_TO_PTR `T*`); no other node
    may depend on it."""
    name: str
    resolved_type: TpyType
    init: THIRExpr | None = None
    cpp_type: str | None = None
    form: Form = Form.VALUE
    is_const: bool = False
    cpp_local_representation: 'LocalBinding | None' = None
    # Reassigned borrow-tuple decl bound from an owning call: the storage
    # spelling of the function-local `std::optional<...>` slot the rvalue
    # emplaces into; the decl aliases the slot via tuple_to_pointer. The
    # statement twin of THIRWalrus.slot_cpp.
    btuple_slot_cpp: str | None = None
    # The OPTIONAL-borrow-tuple flavor (`t: tuple[..] | None = make_pair(..)`):
    # cpp_type is the optional spelling and the emplace RHS wraps through it
    # (`std::optional<B>{::tpy::tuple_to_pointer<B>(__slot_N.emplace(v))}`);
    # this carries the borrow tuple spelling B.
    btuple_opt_borrow_cpp: str | None = None


class PtrSlotKind(Enum):
    """Source shape of a pointer-repr local's slot-hoist declaration/reseat.

      * `OPT_NONE`     -- `x: T | None = None` -> `T* x = nullptr;`. As a
                          reseat: `x = nullptr;`.
      * `OPT_RVALUE`   -- `x: T | None = T(...)` -> a direct `T __slot_N` init
                          slot + `T* x = &__slot_N;`. Rvalue reseats ride
                          THIRAssign's storage verdict (`rebind_storage`).
      * `UNION_NONE`   -- ptr-variant union reseat to None: `v = std::monostate{};`
                          (the DECL None case stays on the existing literal arm).
      * `UNION_RVALUE` -- `v: A | B = A(...)` -> value-variant `__slot_N` +
                          `to_ptr_variant(__slot_N)`. As a reseat: `.emplace`
                          into a slot of the site's own + re-lift.
      * `UNION_INLINE_SLOT` -- a ptr-variant union RESEAT whose decl had
                          no rvalue init (`v: A | B | None = None;
                          v = A(...)`): a FRESH
                          value-variant `__slot_N = init;` declared at the
                          reseat + `v = to_ptr_variant(__slot_N);` (the
                          slotless inline-slot form).
      * `UNION_ADDR`   -- `v: A | B = name` (concrete-member lvalue) ->
                          `variant<A*, B*> v{&(name)};`.
      * `DYN_PROTOCOL` -- `p: P = Concrete(...)` for a @dynamic protocol P ->
                          a concrete/adapter `__slot_N{init}` + a protocol
                          `Base* p = &__slot_N;` (the direct/adapter
                          arm).
                          `cpp_type` is the SLOT spelling (concrete, or the
                          `Adapter<Base, Concrete>` for a structural conformer);
                          `base_cpp` is the protocol base pointer spelling.
    """
    OPT_NONE = auto()
    OPT_RVALUE = auto()
    UNION_NONE = auto()
    UNION_RVALUE = auto()
    UNION_INLINE_SLOT = auto()
    UNION_ADDR = auto()
    DYN_PROTOCOL = auto()
    # Escape-hoist PLAIN-record pointer-locals (the classifier's OTHER, the
    # pointer path's rvalue branches):
    #   * `RECORD_RVALUE` -- a name-reassigned (not rvalue-reassigned) local
    #     with a record-rvalue init: `T __slot_N = init;\nT* x = &__slot_N;`
    #     (the REBIND_SLOT render minus the rebind slot -- reseats copy
    #     pointers, never rvalues).
    #   * `RECORD_HOISTED` -- a HOISTED local's decl inside a loop/branch:
    #     the `std::optional<T> __slot_N;` pre-decl rides the function-top
    #     hoist lines and the decl re-emplaces per execution
    #     (`T* x = &*(__slot_N = init);`).
    RECORD_RVALUE = auto()
    RECORD_HOISTED = auto()
    # `p: Optional[P] = Conformer(...)` for a @dynamic P: the slot types at
    # the rvalue's class (`val_cpp` -- inheriting conformer, implicit
    # upcast) or `auto` (structural conformer, monomorphized: the pointer
    # deduces the concrete class and calls dispatch statically); the
    # pointer line is `auto* p = &__slot_N;` either way.
    OPT_PROTO_RVALUE = auto()
    # `p2: P = p1` / `p2 = p1` where p1 is already an erased protocol pointer:
    # copy the alias, no slot -- `Base* p2 = &(*p1);` (decl) / `p2 = &(*p1);`
    # (reseat). `base_cpp` carries the protocol base; `init`/`value` is the
    # deref'd source.
    DYN_PROTOCOL_ERASED = auto()
    # `s = make_some()` where the callee returns an Own-declared storage
    # optional: materialize the whole `std::optional<T>` in a slot and lift
    # the pointer binding (`std::optional<T> __slot_N = make_some();`
    # `T* s = ::tpy::optional_to_ptr(__slot_N);` -- the is_opt_field
    # slot machinery). `cpp_type` carries the pointee spelling.
    OPT_STORAGE_CALL = auto()
    # ... and the INLINE-slot reseat of a ptr-repr Optional local whose
    # source is a storage-form Optional FIELD off an rvalue receiver
    # (`v = make_holder(p).value` -> `v = ::tpy::optional_to_ptr(__slot_N =
    # make_holder(p).value);`, the `_ptr_from_rvalue_slot` is_opt_field
    # branch over the decl-site rebind slot). One line, unlike the
    # OPT_STORAGE_CALL reseat's fill-then-lift pair.
    OPT_FIELD_RVALUE = auto()
    # Rvalue reseat of a branch-hoisted pointer-local that carries NO if-head
    # rebind slot (the name is reassigned but not rvalue-reassigned -- the
    # mixed rvalue/lvalue flavor): the first such reseat allocates the
    # `std::optional<T> __slot_N;` lazily into the function-top hoist lines
    # and registers it for reuse; every reseat renders
    # `name = &*(__slot_N = <rvalue>);` (the is_hoisted rvalue branch).
    # `val_cpp` carries the slot's T spelling.
    BRANCH_RVALUE = auto()
    # Resumable frame body: an rvalue reseat of a pointer-form frame local
    # materializes in its prescanned FRAME-FIELD slot (one per write site,
    # `_prescan_resumable_ptr_slots`; an inline/case-block slot would die at
    # the next suspension) -- `saved = &*(__ptr_slot_fN = Point(9));`.
    # `val_cpp` carries the field name.
    FRAME_RVALUE = auto()
    # ... and its Own-declared-optional-call sibling: the storage optional
    # fills the prescanned frame field and the pointer re-lifts
    # (`__ptr_slot_fN = make_opt(3); got = optional_to_ptr(__ptr_slot_fN);`).
    FRAME_STORAGE_CALL = auto()
    # Address-of an lvalue, in BOTH directions of the decl/reseat pair (like
    # the OPT_NONE / UNION_NONE twins above):
    #   * as a RESEAT -- `items = base;` -> `items = &(base);`
    #     (the address-of catch-all);
    #   * as a DECL -- a reassigned container-ELEMENT borrow local, `p =
    #     ps[0]` -> `P* p = &(::tpy::__getitem__(ps, 0));`. The decl draws a
    #     rebind slot when a later RVALUE reseat needs one, so unlike the
    #     reseat flavor it CAN call `next_slot()`.
    # A THIRFormConvert cannot carry either: a non-value name / element READ
    # is already BORROW form, so the convert would be the no-op node the
    # validator rejects; the `&(...)` lives in this kind's emit instead
    # (the UNION_ADDR precedent).
    PTR_ADDR = auto()
    # Rvalue reseat of a SLOTLESS pointer-repr Optional local (an
    # annotation-only decl): the first such
    # reseat declares its PLAIN block slot in place and registers it
    # (`T __slot_N = <rvalue>;\nname = &__slot_N;` --
    # the no-slot rvalue branch); later rvalue
    # reseats reuse it (`name = &(__slot_N = <rvalue>);`). `val_cpp`
    # carries the pointee T spelling. Straight-line positions only (a
    # block-scoped slot inside a branch/loop is not this slice).
    INLINE_RVALUE = auto()
    # Module-init initializing write of a NON-VALUE global (`items:
    # list[int32] = [...]` at top level). The name is already declared at
    # namespace scope (`std::vector<int32_t>* items{};`), so only the slot
    # carries a type: `static T __global_slot_N = init;` + `items =
    # &__global_slot_N;` -- the "first rvalue assignment (e.g. global
    # init)" branch, whose `static` and slot prefix both come from the
    # global scope.
    GLOBAL_RVALUE = auto()
    # The three module-init sibling writes of a NON-VALUE global, all
    # pointer-local rebind branches at global scope: a later rvalue
    # write reusing the slot GLOBAL_RVALUE allocated
    # (`g = &(__global_slot_N = init);`), a `None` source (`g = nullptr;`),
    # and a pointer-slot-global source, which is already a `T*` and copies
    # bare (`g = other;`).
    GLOBAL_REBIND = auto()
    GLOBAL_NULL = auto()
    GLOBAL_PTR_COPY = auto()
    # The HOISTED flavor of GLOBAL_RVALUE: `check_escape` hoisted this
    # global, so its slot must stay re-assignable -- a function-top
    # `static std::optional<T> __global_slot_N;` the write lifts through
    # (`g = &*(__global_slot_N = init);`). `val_cpp` carries the pointee T.
    GLOBAL_HOIST_RVALUE = auto()


# The reseat kinds whose emitter drains pending temps before the write, so
# a temp-bearing value is legal there (the validator's flushable-position
# fact). The bare-name kinds (PTR_ADDR, DYN_PROTOCOL_ERASED, GLOBAL_PTR_COPY)
# and the valueless ones stay out; a new kind is classified here, next to
# its declaration.
FLUSHING_REBIND_KINDS = frozenset({
    PtrSlotKind.FRAME_STORAGE_CALL, PtrSlotKind.FRAME_RVALUE,
    PtrSlotKind.OPT_FIELD_RVALUE, PtrSlotKind.OPT_STORAGE_CALL,
    PtrSlotKind.DYN_PROTOCOL, PtrSlotKind.GLOBAL_HOIST_RVALUE,
    PtrSlotKind.GLOBAL_REBIND, PtrSlotKind.INLINE_RVALUE,
    PtrSlotKind.BRANCH_RVALUE, PtrSlotKind.UNION_INLINE_SLOT,
    PtrSlotKind.UNION_RVALUE,
})


@dataclass(frozen=True)
class THIRPtrLocalDecl(THIRStmt):
    """First declaration of a pointer-repr local backed by the `__slot_N`
    hoist machinery (the slot-hoist family): a pointer-repr `Optional[T]`
    local (`T* x` over a hoisted storage slot) or a pointer-variant union
    local (`::tpy::Union<A*, B*>` over a value-variant slot).

    `cpp_type` is the POINTEE spelling for the OPT_* kinds (`T` of `T* x`)
    and the full pointer-variant spelling for the UNION_* kinds. `val_cpp`
    is the value-variant spelling backing a UNION slot (None for OPT_*,
    whose slots reuse `cpp_type`). An rvalue reseat of the name decides its
    own storage (`THIRAssign.rebind_storage` / `THIRPtrLocalRebind`), so
    the decl pre-declares no rebind slot."""
    name: str
    resolved_type: TpyType
    kind: 'PtrSlotKind' = PtrSlotKind.OPT_NONE
    init: THIRExpr | None = None
    cpp_type: str | None = None
    val_cpp: str | None = None
    # GLOBAL_RVALUE only: the write sits inside a top-level branch/loop, so
    # the in-place slot decl drops the `static` (a static would init once
    # across iterations).
    branch_scope: bool = False
    # DYN_PROTOCOL only: the protocol base pointer spelling (`Base` of
    # `Base* p`), distinct from `cpp_type` (the concrete/adapter SLOT spelling).
    base_cpp: str | None = None
    # `const T*` (not `T*`): the pointee is a readonly source (name in
    # `const_indirect_locals`); only the pointer line takes
    # the prefix -- the rebind `std::optional<T>` slot stays non-const.
    is_const: bool = False


@dataclass(frozen=True)
class THIRPtrLocalRebind(THIRStmt):
    """Reseat of a slot-hoist pointer-repr local for the shapes THIRAssign's
    rebind-slot arm does not cover: `x = None` (`x = nullptr;` /
    `v = std::monostate{};`) and the union rvalue reseat (`.emplace` into a
    slot of the site's own + `to_ptr_variant(*slot)` re-lift). `val_cpp` is
    the union value-variant spelling (unused by the OPT_NONE kind)."""
    name: str
    kind: 'PtrSlotKind' = PtrSlotKind.OPT_NONE
    value: THIRExpr | None = None
    val_cpp: str | None = None
    # Where an rvalue reseat writes (sema's alias-rebind verdict): IN_PLACE
    # through the pointer, OWN into a slot private to this site. Read by
    # the BRANCH_RVALUE, GLOBAL_REBIND, GLOBAL_HOIST_RVALUE and FRAME_RVALUE
    # kinds; the storage-optional and union kinds always take their own
    # slot (no in-place write exists for them). None: not an rvalue
    # reseat of a reference local (allocate as the kind's first write).
    rebind_storage: 'RebindStorage | None' = None


@dataclass(frozen=True)
class THIRImportInit(THIRStmt):
    """A module-init import statement's `__tpy_init()` chain. `calls` holds the
    fully-qualified callee spellings in emit order, resolved at lowering by the
    shared `module_init_targets`; an
    import that chains into nothing lowers to a plain no-op instead, keeping
    only its source comment."""
    calls: tuple[str, ...] = ()


@dataclass(frozen=True)
class THIRAssign(THIRStmt):
    """Assignment to an already-declared local (`name = value`) or, for the F2b
    borrow->storage write, to a record field (`recv.field = value`). `target` is
    a THIRName for the former and a THIRFieldAccess for the latter; emission
    renders the target expression directly, so both shapes share one node.

    A class-constant write (`C.X = v` / `obj.X += v`) uses a THIRClassConstant
    target (the bare qualified lvalue) and, when the receiver has observable
    cost, carries `recv_eval` + `recv_wrap` (`static_cast<void>({0})` /
    `::tpy::deref_check({0})`): the receiver eval emits as a leading
    statement so the qualified name stays a real
    lvalue (a statement-expression wrap would be an rvalue)."""
    target: THIRExpr
    value: THIRExpr
    recv_eval: 'THIRExpr | None' = None
    recv_wrap: 'str | None' = None
    # A borrow-tuple reseat from an OWNING tuple call (`t = make_pair(9)`
    # over a branch-hoisted `std::tuple<..., T*> t;`): the rvalue emplaces
    # into the name's pre-declared rebind slot and the assign renders
    # `t = ::tpy::tuple_to_pointer<{borrow}>(__slot_N.emplace({v}));` --
    # the reseat sibling of THIRVarDecl.btuple_slot_cpp.
    btuple_borrow_cpp: 'str | None' = None
    # The OPTIONAL-borrow-tuple reseat flavor: when set, the emplace RHS
    # wraps through the optional spelling (`t = std::optional<B>{
    # ::tpy::tuple_to_pointer<B>(__slot_N.emplace(v))};`).
    btuple_opt_cpp: 'str | None' = None
    # An rvalue reseat of a pointer-local (`p = Point(2)` over `T* p`):
    # sema's alias-rebind verdict says where the object lands -- IN_PLACE
    # (`(*p) = <rvalue>;`, the superseded object dies here) or OWN (a slot
    # private to this site, `p = &*(__slot_N = <rvalue>);`). `slot_cpp`
    # spells the pointee for the OWN slot. None on every other assign.
    rebind_storage: 'RebindStorage | None' = None
    slot_cpp: 'str | None' = None


@dataclass(frozen=True)
class THIRSetItem(THIRStmt):
    """A container subscript write `c[k] = v`. `target` is the lowered
    subscript node (receiver + index + `bounds_safe`), reused for both emit
    arms: the checked free-function dunder (`::tpy::__setitem__(c, k, v);`
    for list/Array/Span/dict; a bytearray receiver dispatches to its own
    `::tpy::bytearray_setitem` at emit, like the bytes_getitem read arm),
    or -- when sema proved the index in
    [0, len) -- the direct `c[static_cast<std::size_t>(k)] = v;` (a literal
    index needs no cast), sharing `_emit_subscript`'s bounds-safe render.
    An augmented `c[k] OP= v` lowers to the same node with `value` the
    synthetic `c[k] OP v` binop and `bounds_safe` forced off on BOTH reads
    -- the augmented form never takes the bounds-safe form. `value` is a flushable position (arg temps hoist
    before the line, like an assign value)."""
    target: 'THIRSubscript'
    value: THIRExpr


@dataclass(frozen=True)
class THIRSliceAssign(THIRStmt):
    """A list/Array/Span slice assignment `c[a:b] = v` / `c[a:b:s] = v` ->
    the sema-resolved slice `__setitem__`'s @native free-function
    (`::tpy::list_set_slice` / `::tpy::list_set_stepped_slice`) over the
    receiver, the slice initializer, and the RHS. `receiver` is a bare
    list/Array/Span
    name or F1-field. The slice initializer is built like `THIRStrSlice`'s
    bound arm (`::tpy::BasicSlice{lo, hi}` / `::tpy::Slice{lo, hi, step}`,
    `stepped` per the source syntax; an absent bound -> `std::nullopt`).
    `value` is the lowered RHS (a move at last use rides on it); a non-empty
    array-literal RHS takes the `std::vector<E>{...}` type prefix
    (`value_vector_cpp`) that the checked helper needs to deduce its Range (a
    bare brace-init deduces nothing). `native_name` is the unqualified stub
    name, qualified
    at emit."""
    receiver: THIRExpr
    native_name: str
    value: THIRExpr
    lower: THIRExpr | None = None
    upper: THIRExpr | None = None
    step: THIRExpr | None = None
    stepped: bool = False
    value_vector_cpp: str | None = None


@dataclass(frozen=True)
class THIRInplaceContainerOp(THIRStmt):
    """An in-place container aug-assign `c OP= v` resolved to a mutating dunder
    (`__iadd__` -> `::tpy::list_extend`, ...) -- the @native free-function over
    the receiver and the RHS (the `resolved_inplace` arm). `receiver` is a
    bare list name; `value` is the lowered RHS. A non-empty array-literal RHS
    takes the `std::vector<E>{...}` type prefix (`value_vector_cpp`) that the
    two-parameter template needs to deduce its Range (a bare brace-init
    deduces nothing). `native_name` is the unqualified stub name, qualified
    at emit."""
    receiver: THIRExpr
    native_name: str
    value: THIRExpr
    value_vector_cpp: str | None = None


@dataclass(frozen=True)
class THIRFrameSlotWrite(THIRStmt):
    """A write to a resumable frame_slot local -- `name.emplace(value);` (R1c).

    A non-value coro/generator local is stored as `tpy::frame_slot<T>`; both
    the first init and every reassign write through `.emplace()`, which
    destroys any prior payload before constructing the new one. `value`
    renders normally (its arg temps flush before the emplace line, like a
    THIRAssign value). `cpp_type` is the slot's element C++ type, used only
    to apply the `typed_brace_init` prefix when the value renders
    as a bare brace-init (`{n, n}` -> `std::array<int32_t, 2>{n, n}`, so it
    binds to `emplace`'s forwarding ref); a record-ctor value (non-brace)
    ignores it."""
    name: str
    value: THIRExpr
    cpp_type: str | None = None


@dataclass(frozen=True)
class THIRCoroHandleMove(THIRStmt):
    """A NAME-source write into a concrete-coro handle slot (`d = c`):
    the two-line pair `d.emplace(std::move(*c));` + `c.reset();` --
    optional's move-ASSIGN is deleted when the frame holds reference
    members, so emplace move-CONSTRUCTS from the source payload and the
    source then resets. A self-write is a Python no-op and never
    constructs this node."""
    target: str
    source: str


@dataclass(frozen=True)
class THIRResumableReturn(THIRStmt):
    """A `return` nested in a NON-SUSPENDING leaf compound of a routed
    resumable body (the CFG only splits compounds at suspensions, so
    `if n <= 1: return 1` stays a whole leaf TpyIf).

    Return SCAFFOLDING (done-state, Poll wrap, finally-chain walk,
    pending-return slots) is skeleton emission in every position; this node
    only marks the position inside THIR-emitted leaf code. The emitter calls
    back into the skeleton via `_EmitState.resumable_return_hook`, passing
    `ast_stmt` -- the skeleton's `_make_async_return` /
    `_make_generator_resumable_return` then re-enters the leaf seam
    (`render_return_value`, keyed by id(ast_stmt)) for the value render, the
    same table entry ReturnT terminators use. `value` is that lowered value
    (None for a bare return); the lowering registers it into the body's
    `return_values` table -- it is not read at emit. `deferred` carries the
    same for a sema-stamped finally-deferred return, registered into
    `deferred_returns`."""
    ast_stmt: object
    value: 'THIRExpr | None' = None
    deferred: 'THIRStmt | None' = None


@dataclass(frozen=True)
class THIRStmtSeq(THIRStmt):
    """A fixed sequence emitted as consecutive statements -- the resumable
    leaf seam's carrier when ONE parse-tree leaf lowers to more than one THIR
    statement (the early-return narrowing `if` + its post-if extraction
    alias, emitted inline after the close brace).
    Carries no loc of its own (its caller-side source comment is a no-op);
    each child emits its own comment inline."""
    stmts: tuple[THIRStmt, ...] = ()


@dataclass(frozen=True)
class THIRStrAppend(THIRStmt):
    """In-place append to an owned-str local -- `t += v;` (S3). Two source
    shapes share it: the str `+=` statement and the `x = x + y` self-append
    peephole (fired at a decl-reassign/assign whose RHS concat's left operand
    is the target).
    `target` is the local's source name; `value` renders bare --
    `std::string::operator+=` accepts string_view / const char* / string /
    an owned concat result alike, so no form wrap arises. A str-FIELD append
    (`recv.field += v`) carries the lowered field lvalue on `target_expr`
    instead; the emit prefers it over `target`."""
    target: str
    value: THIRExpr
    target_expr: 'THIRExpr | None' = None


@dataclass(frozen=True)
class THIRReturn(THIRStmt):
    value: THIRExpr | None = None


@dataclass(frozen=True)
class THIRFinallyDeferredReturn(THIRStmt):
    """A sema-stamped finally-deferred return of a named local: bind a
    pointer to the local's storage
    BEFORE the inline finally chain, materialize the value out of it AFTER,
    so finally mutations of the local stay visible in the returned object
    (CPython's pending return is an alias).

    `capture` is the local's OWN render, not the pointer RHS: a resumable
    frame slot spells `(*name)`, and leaving that render to emit is what lets
    a C++-local shadow of the frame field suppress the peel. `indirect` adds
    the `(*p)` lvalue wrap a pointer-bound local
    needs before the address-of; `optional_move` picks the materialize arm
    (`::tpy::ptr_to_optional_move(p)` vs `std::move(*p)`). The `__tpy_retp_N`
    name draws from the emit-side iter counter.

    A resumable frame carries the same node through its leaf seam rather than
    emitting it: the Poll wrap and done-state transition around the capture
    are skeleton emission in every position."""
    capture: 'THIRExpr | None' = None
    indirect: bool = False
    optional_move: bool = False


@dataclass(frozen=True)
class THIRNarrowAlias(THIRStmt):
    """The isinstance-narrowing extraction alias (F4 U3): declared at branch
    entry, or -- for the early-return implicit else -- at statement level
    right after the `if`. The variant arm:

        auto& __v = *std::get<A*>(v);        (pointer variant)
        const auto& __v = std::get<T>(v);    (value variant; const for
                                              value-type params)

    Reads of the narrowed source inside the alias's scope lower to
    `THIRName(alias)`; the isinstance condition keeps reading the original
    variant. `member_cpp` is the final template arg (const/`*` applied for
    pointer variants). Never carries a source comment -- the alias is
    written between the brace and the first statement's comment."""
    alias: str
    variant_cpp: str
    member_cpp: str
    is_ptr_variant: bool
    const_ref: bool


@dataclass(frozen=True)
class THIRDynNarrowAlias(THIRStmt):
    """The polymorphic cast-and-cache extraction alias, the poly sibling of
    `THIRNarrowAlias`:

        [const ]Sub& __v = *dynamic_cast<[const ]Sub*>(&v);

    emitted at STATEMENT level after an early-return guard (`if not
    isinstance(v, Sub): return/raise`) or a narrowing assert -- the poly
    arm with persistent=True. The cast
    RHS is pre-composed at lowering via the shared `narrow_cast_rhs` /
    `_poly_cast_context` chokepoints (dynamic_cast, or `dyn_adapter_cast`
    for a structural conformer of a @dynamic protocol), anchored to the
    subject's ORIGINAL declared type so a re-narrowing chain keeps the same
    cast input. Never carries a source comment."""
    alias: str
    member_cpp: str
    cast_rhs_cpp: str
    is_const: bool


@dataclass(frozen=True)
class THIRAnyNarrowAlias(THIRStmt):
    """The Any-narrowing extraction alias (D15), the Any sibling of
    `THIRNarrowAlias`:

        const T& __v = std::any_cast<const T&>(v.value);

    declared at branch entry; reads of the narrowed subject inside the
    alias's scope lower to `THIRName(alias)`; the outer Any cell survives
    unchanged. The type is spelled explicitly, not `auto&`. Never carries a
    source comment."""
    alias: str
    subject_cpp: str
    member_cpp: str


@dataclass(frozen=True)
class THIRFrameNestedDef(THIRStmt):
    """A nested `def` at its statement position inside a RESUMABLE frame
    body: the function itself is a frame MEMBER (signature + struct decl
    stay gen_async scaffolding, callable from every resume case; the member
    BODY lowers into `THIRResumableBody.nested_def_bodies` and emits via
    the leaf seam), so the statement renders only the
    `// def {name}: frame member` marker line under its ordinary source
    comment. `loc` drives the source comment; the marker spells the PYTHON
    name (the comment is unescaped -- a keyword-colliding def like
    `double` stays `double` there)."""
    name: str = ""


@dataclass(frozen=True)
class THIRNoOpStmt(THIRStmt):
    """A statement that emits no C++ code -- a `pass` or a docstring in a
    constructor body (M3c-trivia). It carries no payload; its only effect is to
    make `THIRConstructor.body` non-empty so the emitter writes ` {\n    }`
    instead of ` {}`."""


@dataclass(frozen=True)
class THIRFoldedBlock(THIRStmt):
    """The surviving statements of a per-@overload-stub dead-branch fold,
    spliced flat at the enclosing block's indent (no brace scope). May be
    empty (an all-dead chain with no else).

    `burns_match_counter` marks a folded MATCH: `ctx.match_counter` bumps
    before the fold dispatch, so a later match in the
    same body numbers its `__match_subject_N` past the folded one -- the
    emit arm must consume one counter slot without emitting a subject."""
    stmts: tuple[THIRStmt, ...] = ()
    burns_match_counter: bool = False


@dataclass(frozen=True)
class THIRFoldedIfChain(THIRStmt):
    """A PARTIALLY-folded per-@overload-stub if-chain: the surviving dynamic
    branches emit as a clean `if / else if` chain, the else body coming from
    the last ORIGINAL chain node. The lowering
    admits only temp-free conditions past the first branch and no
    branch-decl / concrete-extraction carriers; every other shape
    rejects."""
    branches: tuple[tuple[THIRExpr, tuple[THIRStmt, ...]], ...] = ()
    else_body: tuple[THIRStmt, ...] = ()


@dataclass(frozen=True)
class THIRMatchFoldBind(THIRStmt):
    """One capture binding of a folded @overload match arm, in its
    free-binding forms -- `auto {name} = {source};` when
    sema's per-capture bind_by_value fact is set (a free-copy scalar),
    `auto& {name} = {source};` otherwise. Pre-declared/hoisted targets are
    gate-rejected, so only the fresh-declaration forms exist here. Never
    carries a source comment (bindings emit comment-less between
    the match's source comment and the arm body)."""
    name_cpp: str
    source_cpp: str
    by_value: bool = True


@dataclass(frozen=True)
class THIROverloadDefault(THIRStmt):
    """A short @overload stub's omitted impl param, emitted as a local
    initialized to the impl's default at the top of the body
    (`{cpp_type} {name} = {cpp_default};`, comment-free like the param
    copies). `cpp_default` is pre-rendered by the shared
    `default_to_cpp_from_analyzer` (with the `{}` value-initialization
    fallback applied by lowering)."""
    name: str
    cpp_type: str
    cpp_default: str


@dataclass(frozen=True)
class THIRParamCopy(THIRStmt):
    """The mutable owned copy of a reassigned const-ref param --
    `{cpp_type} {name} = __param_{name};` at the top of the body, before any
    statement (loc stays None: the copies are comment-free, ahead
    of the first statement's source comment). The `__param_{name}` signature
    rename is emitted by `gen_params`, keyed on the same
    scan.reassigned + param_needs_copy_for_reassign facts, so body reads keep
    the plain name. `name` is the escaped C++ name; `cpp_type` the owned
    storage spelling (`ptype.to_cpp()`).
    `init_cpp` overrides the plain `__param_{name}` read for the
    view-family variants (`std::string(__param_x)` / the Optional
    make_optional split); body reads keep their view-form renders -- the
    owned local converts implicitly at every view sink."""
    name: str
    cpp_type: str
    init_cpp: str | None = None


@dataclass(frozen=True)
class THIRDelVar(THIRStmt):
    """`del x[, y]` where at least one name needs the early-destruction
    move-sink -- `{ auto __del_sink = std::move(name); }`
    (one block per sunk name, in source order). `sinks` holds
    `(cpp_name, deref)` pairs: `deref` derefs a pointer-local first
    (`std::move(*name)` -- the sink moves the pointee, not the pointer).
    Skipped names (trivially destructible / alias sources / params / globals /
    alias-born pointer-locals) emit nothing; a del whose EVERY name skips
    lowers to THIRNoOpStmt instead."""
    sinks: tuple[tuple[str, bool], ...] = ()


@dataclass(frozen=True)
class THIRDelItem(THIRStmt):
    """Multi-target `del` -- a per-target loop: one call statement line per
    target, in source order. Both del forms sema desugars to a call ride
    here: `del d[a], e[b]` (`::tpy::__delitem__(recv, key);`) and
    `del a.x, b.y` through a user `__delattr__` (`a.__delattr__("x");`).
    A single-target del keeps the plain THIRExprStmt render (identical
    bytes); this node exists because one source statement emits N lines."""
    calls: tuple[THIRExpr, ...] = ()


@dataclass(frozen=True)
class THIRBreak(THIRStmt):
    """`break` -- a bare `break;`, or an emit-side reroute: an enclosing
    else-loop makes it
    `goto __after_else_N` (skipping the else block), an intervening match
    switch `goto __loop_break_N`, and enclosing finally frames inline their
    cleanup first (a terminating finally suppresses the tail)."""


@dataclass(frozen=True)
class THIRContinue(THIRStmt):
    """`continue` -- a bare `continue;` (the finally-chain caveat of THIRBreak
    applies; C++ continue passes through a switch, so no label case exists)."""


@dataclass(frozen=True)
class THIRIf(THIRStmt):
    """if / elif / else. An elif chain is an else_body of a single THIRIf.

    Slice: no branch-local first declarations. Conditions are simple
    comparisons or the F4 U3 isinstance form; U3 narrowing arrives as a
    `THIRNarrowAlias` leading each narrowed branch (lowering resolved the
    read renames), so the emitter still needs no scope machinery.

    `else_is_nested` is the elif-flattening gate: an elif whose
    outer `else_type_facts` carry a concrete extraction cannot flatten to
    `} else if (...)` (the alias must be declared inside the else block), so
    the chain breaks and the inner if emits as a nested statement --
    `} else {` + its own source comment + `if (...)` one level deeper
    (the `_has_concrete_isinstance_facts` chain-collect gate).

    `hoist_decls` mirrors `THIRTry.hoist_decls`: a var first-declared in a
    branch and definitely-assigned-after is predeclared `{cpp_type} v;` at
    the chain head, the in-branch assigns lowering as bare reassigns
    against the slot. The
    cpp_type carries the full spelling per flavor: `T` for a value var,
    `std::optional<T>` for a single-bind non-value (OPTIONAL_STORAGE), a
    `T*` / `Base*` pointer-local for reassigned non-values and @dynamic
    protocols. `hoist_slots` names the branch-bound borrow-tuple locals
    with an owning-call source: emit writes their
    `std::optional<std::tuple<...>> __slot_N;` (allocating N from the shared
    slot counter, registered in `rebind_slots`) immediately before that
    name's predecl line. The
    narrowing-condition path never carries hoists (deferred)."""
    condition: THIRExpr
    then_body: tuple[THIRStmt, ...]
    else_body: tuple[THIRStmt, ...] = ()
    else_is_nested: bool = False
    hoist_decls: tuple[tuple[str, str], ...] = ()
    hoist_slots: tuple[tuple[str, str], ...] = ()
    # A protocol-isinstance condition compiles to a CONCEPT test: the
    # keyword renders `if constexpr`. Per-node -- an
    # elif chain can mix constexpr and runtime members.
    is_constexpr: bool = False


@dataclass(frozen=True)
class THIRWhile(THIRStmt):
    """while loop. Slice: comparison condition or the F4 U4
    `while isinstance(...)` form (the loop-entry extraction arrives as a
    `THIRNarrowAlias` leading the body, exactly like a narrowed if branch),
    reassign-only body -- a plain C++ `while (cond) { ... }`. `orelse` is the
    while/else block: a bare `{...}` after the loop + its `__after_else_N:;`
    label (a break jumps the label, skipping the block). `hoist_decls`
    mirrors `THIRForEach.hoist_decls`: a var first-declared in the body
    and read after the loop predecls before it."""
    condition: THIRExpr
    body: tuple[THIRStmt, ...]
    orelse: tuple[THIRStmt, ...] = ()
    hoist_decls: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class THIRNestedDef(THIRStmt):
    """A nested function definition -- a lambda:

        auto <name> = <capture_cpp>(<params_cpp>)[ -> <ret_cpp>] {
            <body>
        };

    `capture_cpp` is spelled at lowering purely from sema's node facts
    (captured_names / escapes / ref_captures / move_captures -- THIR does
    no capture analysis of its own); params and the non-void trailing
    return type are the resolver's spellings. The body is lowered under
    the nested function's own per-function state (its prescan return
    slots, fresh classification sets) over the outer `declared` -- the
    same scoping as `nested_def_emission_scope` + the local-scope
    snapshot."""
    name: str
    capture_cpp: str
    params_cpp: tuple[str, ...] = ()
    ret_cpp: 'str | None' = None
    body: tuple[THIRStmt, ...] = ()


@dataclass(frozen=True)
class THIRAssert(THIRStmt):
    """Assert statement with constant folding and lazy computed messages.

        if (!(<cond>)) ::tpy::raise_assertion_error(["<msg>"]);

    A str-literal message keeps its raw text for escaping at emit; a computed
    message is a THIR expression evaluated inside the failing branch.
    An isinstance-narrowing assert is followed by a persistent
    `THIRNarrowAlias` statement appended by `_lower_stmts` (the same
    statement-level pass as the early-return post-if alias); a re-assert on
    an already-extracted subject carries the sema-folded `true` condition
    (`THIRLiteral`) and a suffix-bumped alias."""
    condition: THIRExpr
    message: str | THIRExpr | None = None
    fold_constant: bool = False


@dataclass(frozen=True)
class THIRForRange(THIRStmt):
    """`for <var> in range(...)` lowered to a C-style counter loop.

    `start` is None for
    `range(stop)` (implicit 0). A non-literal bound is hoisted by the emitter
    into a `__start_N`/`__stop_N` temp, where N is the per-function loop index
    off `ctx.iter_counter`; `*_is_literal` carries the
    inline-vs-hoist decision (`start_is_literal` is unused when `start` is None).

    `step_kind` selects the emit arm: `plus_one` (`i < stop; ++i`, also a
    literal +1 step), `unit_neg` (`i > stop; --i`, literal -1 step), `literal_pos`
    / `literal_neg` (a non-unit literal step -- upfront overflow check then
    `i +/-> stop; i += step`), or `variable` (a fixed-int-name step captured into
    `__step_N`, nonzero + overflow checks, ternary direction condition). `step`
    carries the lowered step expr for the three non-unit arms, None otherwise.

    Slice: fixed-int counter, loop var not used after the loop, bounds
    restricted to bare literal / name / arith / call, step restricted to
    a bare (negated) int literal or a bare fixed-int name. `orelse` is the
    for/else block (see THIRWhile)."""
    var: str
    elem_type: TpyType
    stop: THIRExpr
    start: THIRExpr | None = None
    start_is_literal: bool = True
    stop_is_literal: bool = True
    body: tuple[THIRStmt, ...] = ()
    step: THIRExpr | None = None
    step_kind: str = "plus_one"
    orelse: tuple[THIRStmt, ...] = ()
    # A rebind of an existing value-type local (sema's `hoist_loop_var`): the
    # counter binds a hidden `__range_N` and the body opens with `var = __range_N;`
    # so post-loop reads see the last value, not the post-increment overshoot.
    hoist_loop_var: bool = False
    # Body writes must not alter induction, even for a target local to the loop.
    target_written: bool = False
    # Branch-first-declared value locals used after the loop (sema's
    # `if_branch_decls`): `{cpp_type} {name};` predecls before the loop.
    # Includes the loop var itself when `hoist_loop_var`.
    hoist_decls: tuple[tuple[str, str], ...] = ()


class TupleSourceBind(Enum):
    """How a THIRTupleUnpack's source binds to the `__tup_N` holder -- one
    typed discriminator replacing the accreted per-form booleans (each value
    documents its render; the payload fields each form consumes are
    validated in __post_init__):

        NAME_CREF     -> const auto& __tup_N = <src>;      (src: source /
                         source_cpp spelling override, or source_expr --
                         a narrowed value-opt tuple name's deref read)
        RVALUE        -> auto __tup_N = <source_expr>;
        ONESHOT_DEREF -> auto&& __tup_N = (*<source>);     (consumable
                         one-shot `__await_lift_*` frame_slot; owned
                         elements move out)
        NAME_REF      -> auto& __tup_N = <src>;            (borrow-tuple
                         name with ref/alias elements -- the is_ref rule
                         drops the const; source_cpp carries the deref'd
                         spelling for a pointer loop holder)
        STORAGE_WRAP  -> auto __tup_N = ::tpy::tuple_to_pointer<
                         source_wrap_cpp>(<src>);          (storage-form
                         source lifted so `std::get<i>` yields the `T*`
                         each ref/alias target uses)
        NAME_MOVE     -> auto&& __tup_N = std::move(<src>); (an Own-element
                         tuple NAME at its last use -- owned elements move
                         out of the moved-from holder)
        NAME_COPY     -> auto __tup_N = <src>;             (the same tuple
                         NOT at its last use: the holder copies, elements
                         still move out of the copy)
    """
    NAME_CREF = auto()
    RVALUE = auto()
    ONESHOT_DEREF = auto()
    NAME_REF = auto()
    STORAGE_WRAP = auto()
    NAME_MOVE = auto()
    NAME_COPY = auto()


@dataclass(frozen=True)
class THIRTupleUnpack(THIRStmt):
    """A standalone `a, b = <source>` (or the `a, b = __for_tup_M` head of a
    tuple-unpack for loop) over a value-scalar tuple (all-new plain
    value-scalar targets, no ref/owned/const-ref elements). The SOURCE bind
    splits on whether the value is a bare name:

        const auto& __tup_N = <name>;   // a bare name / loop-shadow source
        auto __tup_N = <expr>;          // a call / field rvalue source

    followed by a per-target scalar decl:

        int32_t a = std::get<0>(__tup_N);
        int32_t b = std::get<1>(__tup_N);

    `source_expr` carries the non-name rvalue (a value-tuple-returning call, a
    value-tuple field read); when it is None the `source` name takes the
    ref-binding form. `N` reproduces `ctx.unpack_counter` (per-function,
    pre-incremented). The counter's other consumers (expression-position
    `__tup_`/`__dk_` temps) are all gate-rejected, so a per-body emit counter
    suffices. A None target is the `_` discard -- its slot emits
    nothing. `target_cpps` carries the rendered decl types (render_type at
    lowering), None at discard slots.

    `binds` (parallel to `targets`; empty = all "value") picks each target's
    decl arm:

        "value"  -> T name = std::get<i>(tup);
        "cref"   -> const T& name = std::get<i>(tup);   // is_const_ref
        "move"   -> T name = std::move(std::get<i>(tup)); // Own element
        "assign" -> name = std::get<i>(tup);   // reused target, no decl
        "ptr_variant" -> ::tpy::Union<A*, B*> name =
                             ::tpy::to_ptr_variant(std::get<i>(tup));
                         // Own[A | B] element off the value-variant capture
        "unwrap_ref" -> T& name = ::tpy::unwrap_ref(std::get<i>(tup));
                        // recursive-wrapper element (a live `X&` slot)
        "global_slot" -> static T __global_slot_N = std::move(std::get<i>(tup));
                         name = &__global_slot_N;   // pointer-slot global

    Two RESUMABLE-frame modes (targets are frame fields -- assigned, never
    re-declared; the `wraps` slot carries the per-element unwrap_ref /
    std::move applied before the write; sema guarantees ref and
    owned are mutually exclusive per element):

        "frame_assign"  -> name = <wrapped get>;
        "frame_emplace" -> name.emplace(<wrapped get>);   // frame_slot<T>
                           // (typed_brace_init is identity for a get-expr)

    None at discard slots.

    `source_cpp` overrides the name-bound source's spelling (a spelled
    imported/native global -- `const auto& __tup_N = ::tpystd::m::name;` --
    or the NAME_REF pointer-holder deref); None renders the escaped bare
    `source`. The source HOLDER form is `source_bind` (TupleSourceBind --
    each value's render documented there); `source_wrap_cpp` is
    STORAGE_WRAP's spelled borrow-tuple type."""
    source: str
    targets: tuple[str | None, ...]
    target_cpps: tuple[str | None, ...]
    source_expr: 'THIRExpr | None' = None
    binds: tuple[str | None, ...] = ()
    source_cpp: str | None = None
    # Per-element pre-write wrap for the frame modes: "" / "move" (the only
    # produced tokens -- ref elements reject at lowering today).
    wraps: tuple[str, ...] = ()
    source_bind: TupleSourceBind = TupleSourceBind.NAME_CREF
    source_wrap_cpp: str | None = None

    _BIND_TOKENS: ClassVar[frozenset[str]] = frozenset({
        "value", "cref", "move", "assign", "ref", "opt_ptr", "ptr_variant",
        "unwrap_ref", "global_slot",
        "frame_assign", "frame_emplace", "frame_opt_ptr", "frame_ptr_addr",
        "frame_ptr_elem",
    })

    def __post_init__(self) -> None:
        # The payload/discriminator pairings the old boolean pile left
        # unchecked: each source form consumes exactly its own payload.
        # STORAGE_WRAP and NAME_CREF take either a NAME source or an
        # expression source (`a0, b0 = pairs[0]` lifts the rendered element
        # read; a narrowed value-opt tuple name const-ref-binds its deref).
        if self.source_bind is TupleSourceBind.STORAGE_WRAP:
            if self.source_wrap_cpp is None:
                raise ValueError("STORAGE_WRAP requires source_wrap_cpp")
        else:
            if self.source_bind is TupleSourceBind.RVALUE:
                if self.source_expr is None:
                    raise ValueError("RVALUE requires source_expr")
            elif (self.source_expr is not None
                  and self.source_bind is not TupleSourceBind.NAME_CREF):
                raise ValueError(
                    "source_expr belongs to RVALUE / NAME_CREF sources only")
            if self.source_wrap_cpp is not None:
                raise ValueError("source_wrap_cpp belongs to STORAGE_WRAP only")
        bad = {b for b in self.binds
               if b is not None and b not in self._BIND_TOKENS}
        if bad:
            raise ValueError(f"unknown bind token(s): {sorted(bad)}")


@dataclass(frozen=True)
class THIRForEach(THIRStmt):
    """`for <var> in <container>` over a NativeIterable (list / set / dict / Span /
    Array), lowered to the canonical begin/end iterator loop:

        auto& __obj_N = <container>;
        auto __beg_N = __obj_N.begin();
        auto __end_N = __obj_N.end();
        for (; __beg_N != __end_N; ++__beg_N) {
            <elem> <var> = *__beg_N;   // value-scalar loop var (loop_var_binding)
            auto&& <var> = *__beg_N;   // record loop var (a borrow alias)
            // body
        }

    `elem_type` is the loop var's type -- a value scalar, char, or str (a typed copy;
    a str loop var is usage-resolved at lowering to `std::string_view` or an owned
    `std::string` copy, both spelled by `loop_var_binding`) or an
    F1-record (a borrow alias: `auto&&`, or `const auto&` when `const_loop_var`). For
    list/set/Span/Array it is the element; for dict the key (`for k in d` -- a
    scalar, or a str for an owned-str-keyed dict, S5); for a str-family iterable
    (str/StrView, NativeIterable[char]) it is char
    (`char c = *__beg_N;`); for a bytes-family iterable (bytes/BytesView,
    NativeIterable[uint8]) it is uint8 (`uint8_t x = *__beg_N;`, the same
    value-scalar typed copy). `N` is the
    per-function loop index (off `ctx.iter_counter`). `const_loop_var` carries
    sema's flag; it is inert for a cheap value scalar (the typed copy drops const either
    way) but load-bearing for a record (`const auto&` vs `auto&&`). Slice: a name
    container, a str/bytes-family field off an F1-record receiver (both C++
    lvalues: `iterable_lvalue`, the `auto& __obj_N =` capture), or an eligible
    str- or container-returning call (str and `Own[...]` container returns are
    rvalues: `iterable_lvalue=False`, the owning `auto __obj_N =` capture; a
    borrow container return is an lvalue -- `is_lvalue_iterable`'s call arm;
    bytes-returning calls stay gate-excluded); loop var not reassigned/moved
    (a record alias can't reseat) and not used after the loop. Any
    `list`/`dict`/`set`/`Span`/`Array` param of a fully-concrete element reaches
    here (the param is admitted structurally -- signatures are emitted by the
    skeleton; the loop var binds through the shared `loop_var_binding`, and
    the element USE gates decide). Generators
    / user iterators (the
    `__iter__`/`__next__` fallback), `dict.items()` / tuple-unpack, and hoisted loop vars
    ride later cells."""
    var: str
    elem_type: TpyType
    iterable: THIRExpr
    body: tuple[THIRStmt, ...] = ()
    const_loop_var: bool = False
    iterable_lvalue: bool = True
    orelse: tuple[THIRStmt, ...] = ()
    # A str-literal iterable (`for ch in "abc"`) wraps the rendered literal in
    # `std::string_view(...)` -- C string literals include the NUL terminator,
    # which the view trims.
    str_literal_iterable: bool = False
    # A consuming (own_iter) loop: the elem binds `auto&&` into the
    # move-iterator storage whatever its type -- loop_var_binding's
    # consuming arm.
    consuming: bool = False
    # A loop var used after the loop (sema's `hoist_loop_var`): the per-iteration
    # binding assigns the predeclared slot (`s = *__beg_N;`) instead of declaring
    # a fresh local, so the post-loop read sees the last element.
    hoist_loop_var: bool = False
    # A hoisted ptr-repr tuple loop var was predeclared in BORROW form, so the
    # per-iteration assign lifts the storage element:
    # `t = ::tpy::tuple_to_pointer<<this>>(*__beg_N);` (loop_var_binding's
    # hoisted_tuple_lift_cpp arm).
    hoisted_tuple_lift_cpp: 'str | None' = None
    # Branch-first-declared value locals used after the loop (see THIRForRange).
    hoist_decls: tuple[tuple[str, str], ...] = ()
    # hoist_decls names whose pointer predecl null-initializes
    # (`std::vector<int32_t>* v = nullptr;` -- the hoisted container
    # unpack-target flavor; the with-family pointer hoist stays bare).
    hoist_ptr_inits: tuple[str, ...] = ()


@dataclass(frozen=True)
class THIRForIterProto(THIRStmt):
    """`for <var> in <iterator-source>` over the universal `::tpy::__iter__`
    protocol loop:

        [{]                                    # rvalue source: brace scope
        auto& __src_N = <iterable>;            # `auto` for an rvalue source
        auto&& __itr_N = ::tpy::__iter__(__src_N);
        for (;;) {
            auto __r_M = __itr_N.__next__();
            if (!__r_M.has_value()) break;
            <loop_var_binding(elem, var, ::tpy::unwrap_ref(*__r_M))>
            // body
        }
        [}]

    The rvalue brace scope reproduces CPython's refcount-drop scoping (a
    temporary source dies at loop exit -- observable when the iterator owns
    cleanup). `N`/`M` are two consecutive draws off the per-function loop
    index. Slice: a plain/imported
    free generator call (rvalue: `iterable_lvalue=False`, the owning `auto`
    capture) or a user-iterator local name (lvalue: `auto&`); the loop var
    binds through the shared `loop_var_binding`, same contract as
    `THIRForEach`."""
    var: str
    elem_type: TpyType
    iterable: THIRExpr
    body: tuple[THIRStmt, ...] = ()
    const_loop_var: bool = False
    iterable_lvalue: bool = True
    orelse: tuple[THIRStmt, ...] = ()


class WithTargetArm(Enum):
    """Which as-target binding arm a `with` item takes -- decided
    at lowering. The value-typed REUSE arm stays gate-rejected: it would
    assign `&(__enter__())` into a value slot -- the BUGS.md
    ill-formed with-target-reuse family. An OPTIONAL-slot target is different:
    when the name was hoist-predeclared (`std::optional<T> name;` by an
    enclosing if/with branch-decl pass), the slot is assigned plainly --
    the ASSIGN_OPT arm, keyed on optional-locals membership.

      * `NONE`       -- no target: `__ctx_N.__enter__();`
      * `VALUE`      -- value enter type: `auto <name> = __ctx_N.__enter__();`
      * `REF`        -- reference enter type: `auto& <name> = ...;`
      * `PTR_DECL`   -- fresh reassigned record target:
                        `T* <name> = &(__ctx_N.__enter__());` (the name joins
                        the F2 pointer-locals; `target_cpp` carries `T`)
      * `ASSIGN_PTR` -- reuse of a prior with's pointer-local target:
                        `<name> = &(__ctx_N.__enter__());`
      * `ASSIGN_OPT` -- hoist-predeclared optional-storage target:
                        `<name> = __ctx_N.__enter__();` (plain engaging
                        assign; reads stay on the deref model)
      * `FRAME_SLOT` -- resumable frame_slot target (leaf `with`):
                        `<name>.emplace(__ctx_N.__enter__());`
      * `FRAME_FIELD` -- resumable plain frame-field target (leaf `with`):
                        `<name> = __ctx_N.__enter__();` (the name's storage
                        IS the frame member -- no local decl)
    """
    NONE = auto()
    VALUE = auto()
    REF = auto()
    PTR_DECL = auto()
    ASSIGN_PTR = auto()
    ASSIGN_OPT = auto()
    FRAME_SLOT = auto()
    FRAME_FIELD = auto()


@dataclass(frozen=True)
class THIRWithItem:
    """One `with` context manager: the manager binding + as-target facts.

    `manager_borrowed` mirrors sema's fact (an lvalue non-value manager binds
    `auto& __ctx_N = ...`; an rvalue owns via `auto`). `deref_manager` is the
    already-pointer-source deref (`*(...)`) for a borrowed manager rendered as
    `T*` (an F2 pointer-local name) -- mirrors `is_already_pointer_source` for
    the admitted subset. `can_suppress` / `takes_exc_val` are sema's
    `__exit__` facts (bool return / an `exc_val: Optional[BaseException]`
    second param); they pick the catch arms and the exc argument spellings.
    `target_cpp` is the PTR_DECL arm's pointee type, pre-rendered at lowering
    (`lc.render_type`, the same source as an F2 borrow local's cpp_type).
    `manager_hoist_cpp` is the KEPT owned manager's type (the
    hoist rule: an already-declared target aliases
    `__enter__()`'s result past the block, so the manager hoists to a
    function-scope `std::optional<CM> __slot_N` and `__ctx_N` binds through
    it); None keeps the plain `auto __ctx_N = ...` bind. `frame_ctx` is the
    resumable leaf twin: the owned manager's `__with_ctx_<K>` frame-field
    number (`resumable_state(func).with_owned_ctx_map`, declared by the
    skeleton), rendering `__with_ctx_K.emplace(...)` + the `auto& __ctx_N`
    bind through it; None keeps the plain bind."""
    ctx_expr: THIRExpr
    manager_borrowed: bool
    deref_manager: bool
    target: 'str | None'
    target_arm: WithTargetArm
    can_suppress: bool
    takes_exc_val: bool
    target_cpp: 'str | None' = None
    manager_hoist_cpp: 'str | None' = None
    frame_ctx: 'int | None' = None


@dataclass(frozen=True)
class THIRWith(THIRStmt):
    """A sync `with` statement:

        auto[&] __ctx_N = <manager>;
        [<target binding>]
        try {
            <body>
            __ctx_N.__exit__({}, nullptr|{}, {});   // unless the layer terminates
        } catch (::tpy::BaseException& __exc_N) {   // only if suppress/exc_val
            if (!__ctx_N.__exit__({}, &__exc_N, {})) throw;   // can_suppress
            __ctx_N.__exit__({}, &__exc_N, {}); throw;        // else
        } catch (...) {
            __ctx_N.__exit__({}, nullptr|{}, {});
            throw;
        }

    Multiple items nest one try/catch layer per manager (innermost `__exit__`
    first). `N` draws from the module-cumulative `ctx.with_counter` via the
    emit-side counter sink. `body_terminates`
    is the `stmts_terminate(stmt.body)` fact, computed at lowering; the
    emitter folds it with the items' `can_suppress` flags into the per-layer
    normal-exit elision (`layer_terminates` propagation).
    While emitting the body, each layer sits on the emit-state finally-frame
    stack so `return`/`break`/`continue` inside the body render the inline
    `__exit__` chain. The
    async / resumable-generator lowerings of `TpyWith` are different emit
    shapes entirely and stay gate-rejected (the function gate).

    `hoist_decls` mirrors `THIRTry.hoist_decls`: a value var first-declared
    in the body and read after the statement pre-declares `{cpp} {name};`
    before the first manager binding."""
    items: tuple[THIRWithItem, ...] = ()
    body: tuple[THIRStmt, ...] = ()
    body_terminates: bool = False
    hoist_decls: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class THIRExceptHandler:
    """One `except` clause. Throw tier: a C++ catch arm -- `cpp_type` is the
    handler's exception type pre-rendered at lowering (`error_return_to_cpp`
    over the sema-qualified name); None is the bare `except:` -> `catch (...)`.
    `binding` is the `as` name (raw; emit escapes) -- the catch parameter IS
    the binding, `catch (const T& name)`, no extra decl. Return tier: the
    single goto-dispatch handler -- `source_display` carries the SOURCE
    exception spelling for the `// except E:` comment (the un-rendered
    name), and a `binding` reads through the emitted
    `auto& name = *__err_opt_N;` alias instead of a catch parameter."""
    cpp_type: 'str | None'
    binding: 'str | None'
    body: tuple[THIRStmt, ...] = ()
    source_display: 'str | None' = None


@dataclass(frozen=True)
class THIRTry(THIRStmt):
    """A sync `try` statement -- the finally_only and throw tiers.

    finally_only (no handlers) is the unified shape:

        <hoist decls>       // plain-value predecls
        {
            try {
                <try body>
            } catch (...) {
                <finally body>      // frame popped: inner exits walk OUTER frames
                throw;              // unless finally_terminates
            }
            <finally body>          // unless body_terminates
        }

    throw: a real C++ try with one catch arm per
    handler, `else` jumping past via `goto __after_else_N` (N from the
    module-cumulative `ctx.try_except_counter` through the emit-side sink),
    the whole try/except wrapped in the finally frame above when a finally
    is present.

    return: the goto dispatch around @error_return
    calls -- one outer brace, an optional `std::optional<E> __err_opt_N;`
    when the (single) handler binds, the try body emitted with the emit
    state's `try_except_label`/`try_except_err_opt` live (each fallible call
    inside renders its `goto __except_N` capture against them), else body,
    `goto __after_try_N;`, the labeled handler body (in_except_tier
    "return", so a bare `raise` re-raises as `return make_unexpected(...)`),
    `__after_try_N:;` -- all wrapped in the finally frame when a finally is
    present. `err_opt_cpp` pre-renders the binding's error type for the
    `__err_opt_N` decl (None when the handler has no binding).

    `hoist_decls` is the sema hoist (`if_branch_decls[stmt]`) rendered at
    lowering as `(name, cpp_type)` pairs in sema's sorted order -- names spell
    RAW (no escape). The finally body re-emits at every exit
    site through the emit-state finally-frame stack (the with frames' stmt-list
    generalization): counters keep advancing per copy. `finally_terminates`
    is the last-stmt
    raise/return fact; `body_terminates` is the terminates fact of whatever
    the finally frame wraps -- `try_terminates_ignoring_finally` in every
    tier -- driving the normal-path finally elision. It deliberately excludes
    the finally's own termination: the finally runs last on every exit path,
    so folding it in (as whole-statement `stmts_terminate` does) would elide
    the fall-through copy of an always-raising/returning finally and stop it
    running at all. The async/resumable lowerings stay gate-rejected."""
    tier: str = "finally_only"
    try_body: tuple[THIRStmt, ...] = ()
    handlers: tuple[THIRExceptHandler, ...] = ()
    else_body: tuple[THIRStmt, ...] = ()
    finally_body: tuple[THIRStmt, ...] = ()
    hoist_decls: tuple[tuple[str, str], ...] = ()
    body_terminates: bool = False
    finally_terminates: bool = False
    err_opt_cpp: 'str | None' = None


@dataclass(frozen=True)
class THIRRaise(THIRStmt):
    """`raise X(args)` -> `throw <cpp>(<args>);` / `raise X` -> `throw <cpp>{};`
    (the fresh-construction peephole: static and dynamic types coincide,
    so no `__raise__()` virtual hop); bare `raise` -> `throw;` (a C++ rethrow;
    sema restricts placement) -- EXCEPT inside a return-tier handler body,
    where the emit state's in_except_tier makes it re-raise as
    `return ::tpy::make_unexpected(std::move(*__err_opt_N));` (the bare
    return-tier arm). `return_tier` marks a `raise E(args)` of a
    ReturnException inside an @error_return body -- it renders as the
    finally-aware `return ::tpy::make_unexpected(<cpp>(<args>));` (or `{}`
    construction when arg-less), never a C++ throw. `cpp_type` pre-renders at
    lowering (`error_return_to_cpp`); None is the bare form. The expression
    form (`raise e` / `raise make_error()`) sets `raise_expr` (the lowered
    source) and emits `<expr>{.__deref__()*deref_depth}.__raise__();` -- the
    virtual hop is inherent to the expr form (it preserves the dynamic Throwable
    type through the vtable; a direct `throw expr` would slice a borrow source).
    `deref_depth` mirrors sema's `.__deref__()` count for a `Box[Throwable]`
    source. `via_virtual` mirrors sema's `raise_via_virtual` fact (@virtual_raise
    classes: the peephole doesn't apply; emit `<cpp>(<args>).__raise__();`)."""
    cpp_type: 'str | None' = None
    args: tuple[THIRExpr, ...] = ()
    via_virtual: bool = False
    return_tier: bool = False
    raise_expr: 'THIRExpr | None' = None
    deref_depth: int = 0


@dataclass(frozen=True)
class THIRErrorReturnUnwrap(THIRExpr):
    """An @error_return call in EXPRESSION position -- the statement-expression
    unwrap: `({ auto __er_N = <call>; <check>
    ::tpy::unwrap_ref_move(*__er_N); })` for a value-type result, the
    pointer form `(*({ ...; &::tpy::unwrap_ref(*__er_N); }))` otherwise
    (`value_form` folds the `ret_type.is_value_type()` verdict at
    lowering). The check renders from the emit state: goto-except inside a
    return-tier try body (with the `as`
    capture when the handler binds), propagate inside an @error_return body,
    panic otherwise. `__er_N` draws from the module-cumulative
    try_except_counter sink. `result_type` is the callee's SUCCESS type."""
    call: THIRExpr
    value_form: bool = True


@dataclass(frozen=True)
class THIRErrorReturnBind(THIRStmt):
    """A var-decl / name-assign whose init is a DIRECT @error_return call --
    the statement-level unwrap block:

        [<cpp_type> <name>;]            // predecl when first binding
        {
            auto __try_tmp_N = <call>;
            <check>                     // goto-except / propagate / panic
            <name> = ::tpy::unwrap_ref_move(*__try_tmp_N);
        }

    `decl_cpp` is the predecl's pre-rendered C++ type (`unwrap_ref_type(
    fi.return_type).to_cpp()`), None when the name is
    already declared (a reassign, or a try-hoisted local). `ptr_rebind`
    switches the bind line to a rebind-slot pointer reseat
    (`<name> = &*(__slot_N = ::tpy::unwrap_ref_move(*__try_tmp_N));`) --
    the `_ptr_from_rvalue_slot` arm.
    `alias_bind` switches it to the borrow-aliasing pointer bind
    (`<name> = &(::tpy::unwrap_ref(*__try_tmp_N));` -- an aliasing result
    into a hoisted pointer target -- the aliases-and-pointer-local arm). Other aliasing-result target shapes and slot-less pointer
    targets are gate-rejected. `name` is raw; emit escapes. A non-None
    `target` replaces the name on the bind line with the rendered lvalue
    (`this->p = ::tpy::unwrap_ref_move(*__try_tmp_N);` -- the non-name
    branch, rendered as a plain lvalue)."""
    name: str
    call: THIRExpr
    decl_cpp: 'str | None' = None
    ptr_rebind: bool = False
    # With `ptr_rebind`: the reseat's storage verdict and OWN slot pointee
    # spelling (see THIRAssign.rebind_storage).
    rebind_storage: 'RebindStorage | None' = None
    slot_cpp: 'str | None' = None
    alias_bind: bool = False
    target: 'THIRExpr | None' = None


@dataclass(frozen=True)
class THIRErrorReturnDiscard(THIRStmt):
    """An @error_return call in statement position, result discarded:

        {
            auto __try_tmp_N = <call>;
            <check>                     // goto-except / propagate / panic
        }
    """
    call: THIRExpr


@dataclass(frozen=True)
class THIRMatchBinding:
    """A capture / `as` name bound to the whole subject in a scalar-tier
    arm. `mode` folds the value-subject arms at lowering:
    'assign' (a hoisted / pre-declared local -- plain `name = subject;`,
    including a hoisted pointer-local aliasing a pointer-repr subject),
    'assign_addr' (a hoisted pointer-local aliasing a value lvalue subject
    -- `name = &(subject);`), 'assign_move' (a hoisted owned-optional slot
    moving from a materialized rvalue subject -- `name =
    std::move(subject);`), 'copy' (sema's `bind_by_value` free-copy scalar
    -- `auto name = subject;`), 'ref' (`auto& name = subject;`),
    'frame_emplace' (a resumable dispatch-hook capture into a frame_slot
    local -- `name.emplace(subject);`, re-keyed from the frame facts by
    `_hook_mode_binding`). The name is raw; emit escapes. `from_case_var` (union tier) binds against the
    arm's `__case_{i}` extraction alias (or the composed `std::get` when
    no alias was drawn) instead of the subject. A FIELD capture
    (`case C(f=name)`) carries `subject_suffix=".f"`: the emit composes
    `{prefix}{base}{suffix}` for the RHS, the base being the subject (record
    tiers) or the alias
    (union tiers). NESTED sub-patterns extend the same composition:
    `subject_prefix` carries the left half of a `std::get<T>(...)` wrap
    (a union-field guard's extraction); mode 'field_alias' is the
    `auto& __field_{base}_{f} = ...;` temp drawn for keyword captures under
    a union-field guard -- its NAME is
    derived at emit from the runtime base spelling (`name` holds the
    FIELD name), and later rows reach it via `base_name`, which switches
    a row's base from the subject to a previously-bound name (a field
    alias, or an `as` name whose nested keywords bind through it)."""
    name: str
    # 'assign' | 'assign_addr' | 'assign_move' | 'copy' | 'ref'
    # | 'frame_emplace' | 'field_alias'
    mode: str
    from_case_var: bool = False
    subject_suffix: str = ""
    subject_prefix: str = ""
    base_name: 'str | None' = None
    # field_alias rows only: the PLAIN field path accumulated between the
    # base and this guard (e.g. ".v" for `W(v=A(n=...))` where `v` is a
    # non-union field). The temp name derives from the accumulated path --
    # the emit composes `{base}{alias_path}` before sanitizing, or the name
    # would drop those segments.
    alias_path: str = ""


@dataclass(frozen=True)
class THIRMatchArmEntry:
    """One source `case` inside a THIRMatchArm group. `binding` is the arm
    block's
    first line (a `case x:` capture or a `case <pattern> as z:` name);
    `guard` is the lowered guard, rendered raw (`if (guard)`) -- bool-typed
    and call-free by the gate, so no truthy wrap and no temp flush point
    needed. `loc` feeds the source comment (only each group's FIRST entry
    is commented; the chain tiers comment every arm -- their groups
    are single-entry)."""
    body: tuple[THIRStmt, ...] = ()
    loc: 'SourceLocation | None' = None
    binding: 'THIRMatchBinding | None' = None
    # `case x as y:` binds the whole subject TWICE. These render on the
    # lines before `binding`, off the same base, each with its own folded
    # mode (the two names need not share a hoist verdict).
    pre_bindings: tuple[THIRMatchBinding, ...] = ()
    guard: 'THIRExpr | None' = None
    # Resumable MatchDispatch mode: the arm BODY lives in the state machine
    # (an ordinary BB chain the skeleton walks), so `body` stays empty and
    # the emit calls the skeleton's arm hook with this id(case.body) key at
    # the body point instead. None for sync matches.
    body_key: 'int | None' = None
    # Union tier: the arm's numeric variant index (`_variant_index` over
    # the wrapper's full member ordering, computed at lowering) and the
    # `__case_{i}` extraction alias -- emitted as
    # `auto& __case_i = [*]std::get<idx>(subject);` when sema narrowing
    # (or a field binding, deferred) needs it; arm-body reads of the
    # subject were renamed to the alias at lowering (the U3 mechanic).
    variant_index: 'int | None' = None
    case_alias: 'str | None' = None
    # Record/union field sub-patterns. `field_conds` are the field-condition
    # literal arms as (prefix, suffix) pairs around the runtime
    # base spelling (only known at emit: `__match_subject_N` for the record
    # tiers, `__case_{idx}` for the guarded-union tier) -- the emit composes
    # `{prefix}{base}{suffix}`, `&&`-joined. `field_bindings` are the
    # keyword captures (`subject_suffix` carries the `.field` accessor).
    # `or_conds` marks an or-pattern arm of condition-only class
    # alternatives: one (possibly empty after the wildcard-alt clear /
    # empty-alt skip) tuple of cond groups, `||`-joined in parens; None for
    # non-or arms. Or-arms never carry bindings (they are dropped --
    # gate-rejected, see BUGS.md).
    field_conds: tuple[tuple[str, str], ...] = ()
    field_bindings: tuple[THIRMatchBinding, ...] = ()
    or_conds: 'tuple[tuple[tuple[str, str], ...], ...] | None' = None
    # Optional-chain tiers (if_elif_optional[_guarded]): the arm condition as
    # an ||-join -- a tuple of (paren, pieces)
    # groups, each piece a (prefix, suffix) pair around the subject spelling
    # (the null/has-value tests and the `(*subj) == lit` / `(*subj).f == lit`
    # compares reference the subject once each), pieces `&&`-joined per group,
    # a group parenthesized iff `paren` (or-pattern alternatives, except the
    # bare null alternative). None is the always-matching wildcard/capture
    # arm (`{` / `} else {`, and the guarded tier's bare block).
    opt_conds: 'tuple[tuple[bool, tuple[tuple[str, str], ...]], ...] | None' \
        = None
    # Polymorphic tiers (poly_if_elif / poly_guarded). A class arm carries
    # `poly_cast` -- the C++17 if-init `Sub* __mpoly_i = <cast>` as a
    # (prefix, suffix) pair around the subject spelling (the cast RHS goes
    # through the shared `narrow_cast_rhs` chokepoint at lowering, so
    # dynamic_cast vs dyn_adapter_cast and const-ness are folded) -- plus
    # `poly_ref_decl`, the fully-rendered `Sub& __case_i = *__mpoly_i;`
    # alias line (`case_alias` names it for bindings and the narrowed
    # subject reads). An or-pattern arm carries `poly_or_conds`: one
    # pre-rendered `(<cast> != nullptr)` (prefix, suffix) pair per
    # alternative, `||`-joined at emit. The poly-guarded tier composes
    # `field_conds` around the ALIAS (like the guarded-union tier), not
    # the subject.
    poly_cast: 'tuple[str, str] | None' = None
    poly_ref_decl: 'str | None' = None
    poly_or_conds: 'tuple[tuple[str, str], ...] | None' = None


@dataclass(frozen=True)
class THIRMatchArm:
    """One arm GROUP of a scalar-tier THIRMatch. `labels` are
    per-alternative spellings pre-rendered at lowering: for the switch
    tiers, C++ case labels (`_enum_member_cpp` for enum members, or a bare
    int spelling; an or-pattern carries one label per alternative, stacked
    `case A:` lines sharing one block); for the if/elif tiers,
    a comparison RHS (the emit composes
    `{subject} == {rhs}`, ||-joined for or-patterns). Empty `labels` is the
    always-match arm -> `default:` (grouped last) or the chain's
    `} else {` / bare `{` block. The chain
    tiers keep one source case per group (source order); the switch tiers
    merge same-label cases into one group whose `entries` emit as a guard
    chain (guarded-first, unguarded-last --
    sema's duplicate-case check enforces the order)."""
    labels: tuple[str, ...] = ()
    entries: tuple[THIRMatchArmEntry, ...] = ()


@dataclass(frozen=True)
class THIRMatch(THIRStmt):
    """A `match` statement -- the unguarded scalar tiers: the switch tiers
    (M1: switch_enum / switch_primitive) in their no-guard/no-capture shape,
    and the if/elif tier
    (M2: bool/BigInt/float/str subjects below the str switch-dispatch
    threshold) as an unguarded `==` chain --
    source-order arms, wildcard as the final `} else {`, no end label, no
    counter draw beyond the subject, and no `default:`/`break;` (a chain is
    not a switch, so `break` in an arm needs no goto escape either). The
    switch shape:

        <hoist decls>                       // plain-value predecls
        auto& __match_subject_N = <subj>;   // auto for rvalue subjects
        switch (__match_subject_N) {
        // case A:
        case A: {
            <body>
            break;                          // always, even after goto/continue
        }
        default: break;                     // synthetic, non-exhaustive only
        }
        ::std::unreachable();               // exhaustive + all arms terminate

    `N` draws from the per-function emit-state match counter --
    `ctx.match_counter` resets per function in `reset_scope` (the
    iter_counter precedent), NOT the module-cumulative with/try sinks. The
    emitter brackets the switch with the emit-state `switch_depth` (zeroed
    around loop bodies like `ctx.match_switch_depth`) so a `break` in an arm
    body inside a loop renders the `goto __loop_break_M` escape (M off
    the per-function iter_counter, the label after the loop's close brace)
    instead of a switch-eating bare `break;`. Guards, captures/as bindings,
    the if-elif tiers, and every non-scalar subject stay gate-rejected;
    guarded groups' second counter draw (`__match_end_N` / `__match_default_N`)
    never happens in this tier."""
    # 'switch_enum' | 'switch_primitive' | 'if_elif' | 'if_elif_guarded'
    # | 'switch_union' | 'guarded_union' | 'if_elif_record' | 'guarded_record'
    # | 'optional_partition' | 'if_elif_optional' | 'if_elif_optional_guarded'
    # | 'switch_str' | 'poly_if_elif' | 'poly_guarded'
    strategy: str = "switch_enum"
    subject: 'THIRExpr | None' = None
    subject_ref: bool = True          # auto& (lvalue subject) vs auto
    arms: tuple[THIRMatchArm, ...] = ()
    hoist_decls: tuple[tuple[str, str], ...] = ()
    # Hoists that reserve a slot beside their predecl (THIRIf.hoist_slots);
    # no match hoist does today.
    hoist_slots: tuple[tuple[str, str], ...] = ()
    is_exhaustive: bool = False
    emit_unreachable: bool = False    # is_exhaustive AND every arm terminates
    synthetic_default: bool = False   # no wildcard AND not exhaustive
    # The needs_default_goto fold: a user default exists
    # AND some labeled group is entirely guarded -- its chain falls through via
    # `goto __match_default_N;` onto the `default: __match_default_N: {`
    # label, N drawing the second per-function counter bump (before the
    # switch head).
    default_goto: bool = False
    # switch_union: the subject's runtime form -- `*std::get<I>(...)` vs
    # `std::get<I>(...)` (`_subject_is_ptr_variant` folded at lowering).
    is_ptr_variant: bool = False
    # switch_union: a recursive-alias wrapper subject dispatches through
    # its `.value` variant member (`switch (subj.value.index())`, `std::get`
    # over `subj.value`); always value-variant, so is_ptr_variant stays
    # False (is_ptr_variant_union excludes needs_wrapper).
    wrapper_value: bool = False
    # optional_partition (O1): the optimized-Optional shape over a
    # pointer-repr `Optional[F1-record]` name subject. The None prefix arm
    # (body/loc; bindings gate-rejected -- a None-arm `as` is a sema error
    # and a full-Optional capture defeats the partition) emits inside
    # `if (subj == nullptr) { ... } else {`; None here is the no-None-arm
    # form (`if (subj != nullptr) {`). The else block draws the
    # `__match_inner_N` deref alias (SAME counter value as the subject --
    # both numbered off one bump) and `arms` holds the single
    # always-match inner arm (the no-field
    # `{ }` block; sema's unreachable-arm rule caps the unguarded inner
    # dispatch at one). Its binding binds vs the inner alias.
    none_entry: 'THIRMatchArmEntry | None' = None
    # optional_partition, value-repr widening (O2): a value-repr scalar/str
    # Optional subject spells the split `!subj.has_value()` / `subj.has_value()`
    # instead of the pointer nullptr compares, and `inner_strategy` selects the
    # multi-arm inner dispatch over the `__match_inner_N` alias --
    # 'switch_enum' / 'switch_primitive' reuse the grouped-switch emit,
    # 'if_elif' the unguarded `==` chain (the inner-dispatch calls). None
    # keeps the O1
    # single-arm pointer-repr shape.
    optional_value_repr: bool = False
    inner_strategy: 'str | None' = None
    # The chain-optional tiers (if_elif_optional / if_elif_optional_guarded)
    # cover the non-partitioned
    # Optional subject shapes: per-arm pre-rendered conditions ride each
    # entry's `opt_conds`; a class arm's field captures bind against the
    # `(*subj)` deref (the emit's base for `field_bindings`); a whole-subject
    # binding with `from_case_var` binds the deref, without it the full
    # Optional (sema's binds_full_optional). The unguarded tier is the plain
    # chain (`if`/`} else if`/`} else`); the guarded tier the standalone-if +
    # `goto __match_end_N` shape (second counter draw), guards nested inside
    # the arm block after the bindings.
    #
    # switch_str -- a discriminator dispatch (a str
    # subject at or above STRING_SWITCH_THRESHOLD unguarded literal
    # alternatives): the guarded-literal prefix arms (`str_guarded`,
    # standalone `if (subj == "lit") {` blocks + the goto tail), the
    # discriminator switch over `subj.size()` (`str_disc_kind` 'length') or
    # `static_cast<unsigned char>(subj[i])` behind a `size() >= i+1` guard
    # ('char_at', `str_disc_param` = i), `arms` as buckets in ascending
    # disc-value order (labels[0] pre-rendered via `case_label`; each entry
    # one (case, string) pair -- an or-arm's body re-lowered per alternative
    # like the union or-bind), and the trailing wildcard/capture arms
    # (`str_trailing`, block + goto tail). The end label draws the second
    # per-function counter bump, like the guarded tiers.
    str_disc_kind: 'str | None' = None
    str_disc_param: 'int | None' = None
    str_guarded: tuple[THIRMatchArmEntry, ...] = ()
    str_trailing: tuple[THIRMatchArmEntry, ...] = ()


class PrintForm(Enum):
    """How a `print()` argument is wrapped in the `std::cout << ...` chain --
    decided at lowering from the arg's resolved type, so the emitter renders the
    chosen wrapper without re-inspecting types (the common-arg subset).

      * `RAW`     -- direct `<<` (a wider fixed-int, or a `THIRStrLiteral`).
      * `INT8`    -- `static_cast<int>(...)`, so an 8-bit int isn't printed as a char.
      * `BOOL`    -- `::tpy::print_bool(...)` (Python-style `True`/`False`).
      * `FLOAT`   -- `::tpy::print_float(...)` (Python-style float formatting).
      * `FLOAT32` -- `::tpy::print_float(static_cast<double>(...))` (the float
        overload takes double).
      * `BYTES`   -- `::tpy::BytesPrinter(...)` (Python-style `b'...'` repr).
      * `REPR`    -- `::tpy::__repr__(...)` (an @native enum: no operator<< is
        emitted for it, so printing routes through the EnumUtil-backed repr).
      * `VARARGS` -- `::tpy::VarargsPrinter(...)` on a whole `*args` body
        view, which Python prints tuple-style.
      * `VALUE_GENERIC` -- `::tpy::ValuePrinter(...)` on an open type-param
        value, which dispatches the formatting at runtime (the TypeParamRef
        arm; a `bool` T must print True/False, not 1/0).
      * `LIST`/`SET`/`DICT` -- the container-printer wraps; currently only
        comprehension args take these.
      * `OPT_VAL` -- `::tpy::print_optional_val(...)` on the whole (bare,
        un-narrowed) value-repr `Optional[int/char/str]` (the value-repr
        Optional arm, plain form).
      * `OPT_VAL_BOOL`/`OPT_VAL_FLOAT` -- the same on `Optional[bool]` /
        `Optional[float]`, taking an explicit Formatter + inner-type template
        (`<::tpy::print_bool, T>` / `<::tpy::print_float, T>`); the inner C++
        type rides `THIRPrintArg.opt_inner_cpp`.
      * `OPT_PTR` -- `::tpy::print_optional(...)` on a bare pointer-repr
        `Optional[F1-record]` NAME (the pointer-repr arm, CTAD form:
        a record inner streams via its own operator<<, no Formatter).
    """
    RAW = auto()
    INT8 = auto()
    STR = auto()  # `::tpy::__str__(...)` -- a union-typed arg (std::variant
                  # has no operator<<; the per-alternative visitor dispatch)
    BOOL = auto()
    FLOAT = auto()
    FLOAT32 = auto()
    BYTES = auto()
    REPR = auto()
    LIST = auto()
    SET = auto()
    DICT = auto()
    TUPLE = auto()  # `::tpy::TuplePrinter(...)` -- a value-tuple name arg
    VARARGS = auto()
    VALUE_GENERIC = auto()
    BYTEARRAY = auto()  # `::tpy::ByteArrayPrinter(...)` -- a bytearray name
    OPT_VAL = auto()
    OPT_VAL_BOOL = auto()
    OPT_VAL_FLOAT = auto()
    OPT_VAL_FMT = auto()  # `print_optional_val<FMT, INNER>(...)` -- an inner
                          # whose C++ type has no plain operator<< (container /
                          # tuple / bytes / bytearray) needs an explicit
                          # Formatter; FMT rides `opt_fmt_cpp`
    OPT_PTR = auto()
    OPT_PTR_FMT = auto()  # the pointer-repr twin, `print_optional<FMT, INNER>`


@dataclass(frozen=True)
class THIRPrintArg:
    """One `print()` argument: the lowered expression + how the emitter wraps it.
    `print_form` is named distinctly from `THIRExpr.form` (the unrelated
    borrow/storage axis) to keep the two from being conflated. `opt_inner_cpp`
    carries the Optional inner's C++ spelling for the templated
    `OPT_VAL_BOOL`/`OPT_VAL_FLOAT`/`OPT_VAL_FMT`/`OPT_PTR_FMT` wrappers (None
    for every other form); `opt_fmt_cpp` the Formatter for the last two.
    `deref` streams the referent of a pointer-shaped read (`(*std::get<1>(t))`
    -- a borrow-tuple record element): print is a VALUE position, so the
    pointer the element read hands back has to be dereferenced here rather
    than by a member access."""
    expr: THIRExpr
    print_form: PrintForm
    opt_inner_cpp: str | None = None
    deref: bool = False
    opt_fmt_cpp: str | None = None


@dataclass(frozen=True)
class THIRPrintChain(THIRExpr):
    """The `std::cout << a0 << " " << a1 << ... << "\\n"` chain as an
    EXPRESSION -- the body of a void print-call lambda (the enclosing
    THIRLambda adds the `;`). Plain `print(args)` only: the
    default sep/end literals, no file/flush kwargs -- those shapes
    reject."""
    args: tuple[THIRPrintArg, ...] = ()


@dataclass(frozen=True)
class THIRPrint(THIRStmt):
    """A `print(<args>)` statement. Default sink `std::cout`; a `file=` kwarg
    rides `sink_expr` and emits `::tpy::as_ostream(<sink>) << ...` (the sink
    expr lowers in value position, so a pointer-typed
    global like `sys.stderr` renders `(*...)`). The slice admits the
    `sep=`/`end=`/`file=` kwargs (sep/end a str literal or a resolved str-value
    NAME) and excludes `flush=`. Emits `<sink> << a0 << SEP << a1 << ...
    << END;`. A literal separator/end rides `sep_value`/`end_value` (the
    Python VALUE, rendered via cpp_string_literal_expr; None suppresses the
    token entirely -- the empty-literal
    skip); a runtime one rides `sep_expr`/`end_expr` and wins over the value
    slot. Each arg carries its PrintForm wrap (scalar/str/bytes/enum forms,
    the container/tuple printer wraps, records raw); args outside the wrap
    set reject."""
    args: tuple[THIRPrintArg, ...] = ()
    sep_expr: 'THIRExpr | None' = None
    end_expr: 'THIRExpr | None' = None
    sep_value: 'str | None' = " "
    end_value: 'str | None' = "\n"
    sink_expr: 'THIRExpr | None' = None
    # A literal `flush=True` appends `<< std::flush` after the end token
    # runtime flush values reject.
    flush: bool = False


@dataclass(frozen=True)
class THIRExprStmt(THIRStmt):
    """A bare expression statement evaluated for its side effects (`foo(x)`).
    Currently only a same-module free-function call reaches here (via the
    call-lowering admission checks, statement position -- a discarded scalar
    or `None` return); the emitter renders `<expr>;`."""
    expr: THIRExpr
    # A discarded expression statement (`n < 2`, `p.x`, `n + 1`, a bare
    # name or literal) renders `(void)(<expr>);`: the operands still
    # evaluate, but without the cast GCC's -Wunused-value rejects the pure
    # forms under -Werror. Lowering decides this per row -- the render must
    # not re-derive it by inspecting the expression's shape.
    void_cast: bool = False


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
    """`error_return_cpp` is the @error_return error type's C++ render
    (`error_return_to_cpp`), None for
    ordinary functions. It seeds the emit state: bare `return` renders
    `return {};`, a void body appends the trailing `return {};` success
    (unless `body_terminates` -- the `stmts_terminate` fact of the source
    body, computed at lowering -- says the end is unreachable), and
    propagate checks read it as the innermost disposition."""
    name: str
    params: tuple[THIRParam, ...]
    return_type: TpyType
    body: tuple[THIRStmt, ...]
    layout: THIRFunctionLayout
    error_return_cpp: 'str | None' = None
    body_terminates: bool = False


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
    rendered C++ name (`super_parent_type.to_cpp()`); `args` are the lowered
    `super().__init__(...)` argument
    expressions, rendered at emit (M3d-1 admits eligible-scalar args only)."""
    base_cpp: str
    args: tuple[THIRExpr, ...]


@dataclass(frozen=True)
class THIRConstructor:
    """A lowered constructor: only the member-init-list + body tail that
    `gen_record_decl` emits, NOT the signature (the skeleton emits that --
    only the body/tail comes from THIR).

    `mil_inits` are the hoisted field initializers in source order; `base_inits`
    are the base-class initializers (M3d; empty for a flat record); `body` is the
    non-init constructor body (M3c). The M3a slice is pure-MIL -- every field init
    hoists, so `body` is empty and the emitted C++ body is `{}`.

    `record_name` and `params` model the constructor faithfully but are not
    read by the tail-only emitter (the skeleton emits the signature); they are
    the inputs a future signature-emit increment would consume."""
    record_name: str
    params: tuple[THIRParam, ...]
    mil_inits: tuple[THIRMilInit, ...]
    base_inits: tuple[THIRBaseInit, ...] = ()
    body: tuple[THIRStmt, ...] = ()


@dataclass(frozen=True)
class THIRResumableBody:
    """A routed resumable (async) body's lowered LEAF content, keyed by the
    AST node the shared state-machine skeleton holds.

    The skeleton (`resumable_cfg` + `gen_async`) owns the frame struct, case
    labels, region replay and suspend/resume plumbing -- structural emission,
    like signatures. Every user-source leaf renders through these maps; a
    missing key is a hard error (lowering and seam must agree), never a
    silent per-leaf skip.

    `leaves` covers BB leaf statements and RaiseT terminator statements;
    `conds` the Branch terminator conditions; `await_args` each suspension's
    sub-coro emplace arguments (keyed by the await's operand call);
    `return_values` the value expression of ReturnT terminators and of
    `_make_async_return`'s value renders (keyed by the TpyReturn);
    `yield_values` the generator-shape yield value (keyed by the TpyYield --
    the coerce carrying the yield-type target is baked, so the render is
    position-blind, unlike the async return)."""
    leaves: 'IdentityMap'
    conds: 'IdentityMap'
    await_args: 'IdentityMap'
    return_values: 'IdentityMap'
    yield_values: 'IdentityMap' = field(default_factory=IdentityMap)
    # ERASED/BORROWED await operands (keyed by the operand expr) and
    # bound-method await receivers (keyed by the receiver expr, R5):
    # the skeleton keeps its move / & / .get() / __self-prepend wrap, the leaf
    # renders the bare expression.
    suspend_exprs: 'IdentityMap' = field(default_factory=IdentityMap)
    # Region/loop pseudo-statement renders (keyed by the AST EXPRESSION
    # node the skeleton holds): the with-region manager
    # (`item.context_expr`), the for-loop iterable, and each range() bound.
    # One map for all three kinds -- the skeleton keeps its emplace / &(..) /
    # static_cast wrap, the leaf renders the bare expression.
    region_exprs: 'IdentityMap' = field(default_factory=IdentityMap)
    # MatchDispatch dispatches (keyed by the TpyMatch): the whole
    # type-aware dispatch (subject + labels + guards) lowered through the
    # sync match tiers with arm BODIES replaced by body_key hooks -- the
    # skeleton walks the arm BBs through its arm emitter at those points.
    match_dispatches: 'IdentityMap' = field(default_factory=IdentityMap)
    # Frame nested defs (keyed by the nested TpyFunction): the
    # member-function BODY statements, lowered under the nested function's
    # own per-function scope with the frame classifications kept (a member
    # reaches frame locals through the frame's fields via implicit this).
    # The skeleton keeps the signature/struct-decl lines; the statement
    # position keeps its THIRFrameNestedDef marker.
    nested_def_bodies: 'IdentityMap' = field(default_factory=IdentityMap)
    # The extraction alias each narrowing subject gets when a resume case
    # re-establishes its fact (keyed by the subject name). The lowering is
    # the only speller: the skeleton reads this rather than re-deriving a
    # name from the frame layout, so body reads and skeleton declarations
    # cannot disagree.
    narrow_aliases: 'Mapping[str, str]' = field(default_factory=dict)
    # Sema-stamped finally-deferred returns (keyed by the TpyReturn):
    # the capture recipe the skeleton's return scaffolding consults. Present
    # for EVERY stamped return of a routed body -- lowering rejects the body
    # when the recipe table does not cover the shape -- so the seam never
    # decides anything at emit and a missing entry is a disagreement.
    deferred_returns: 'IdentityMap' = field(default_factory=IdentityMap)
