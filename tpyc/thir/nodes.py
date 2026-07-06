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
    """Base statement.

    `no_source_comment` mirrors the AST flag: a multi-statement desugar (e.g. a
    tuple-unpack expanding to several assigns that share one source line) marks
    its non-first statements so the shared source comment is emitted once. Set at
    lowering from the AST stmt; honored by `_emit_stmts`."""
    no_source_comment: bool = field(default=False, kw_only=True)


# --- Expressions ---


@dataclass(frozen=True)
class THIRLiteral(THIRExpr):
    """Scalar literal. `result_type` disambiguates int width / float / bool."""
    value: object  # int | float | bool | None


@dataclass(frozen=True)
class THIRStrLiteral(THIRExpr):
    """A string literal, rendered via `cpp_string_literal_expr` so the
    quoting/escaping matches the AST path. Form stays VALUE: the emitted
    const char[N] converts implicitly to both string_view and string slots,
    so a literal is never wrapped by the owned-sink view->owned copy."""
    value: str


@dataclass(frozen=True)
class THIRBytesLiteral(THIRExpr):
    """A bytes literal. Unlike a str literal (a position-neutral const char[N]),
    a bytes literal's C++ render is TARGET-dependent, so lowering decides it
    per sink and carries the verdict on the `form` tag: STORAGE renders the
    owning vector (`::tpy::bytes_literal_owned(...)` / empty
    `std::vector<uint8_t>{}` -- the default, matching every target-less
    position: print args, compare operands, owned decl inits/returns), BORROW
    the static-storage span (`::tpy::bytes_literal(...)` / empty
    `std::span<const uint8_t>{}`, the view-targeted positions: view-local
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
    resolved type and conversion -- the carried mirror of `_gen_fstring`'s
    per-arg wrapper table (`!r` carries `::tpy::repr_of({0})`; a format
    spec flips the bool row to `static_cast<int>` and the float rows to
    bare). None passes the arg through unwrapped (str-family values, plain
    fixed ints, Char). `format_spec` is the parser-validated constant spec
    text, spliced verbatim into the `{:spec}` placeholder exactly as the
    AST arm concatenates it."""
    expr: THIRExpr
    wrap: str | None = None
    format_spec: str | None = None


@dataclass(frozen=True)
class THIRFString(THIRExpr):
    """An f-string: literal segments (raw, unescaped source text) interleaved
    with interpolated args. All type dispatch is decided at lowering (the arg
    wrap templates); the emitter reassembles `_gen_fstring`'s output as a pure
    string function -- `std::string("joined")` for the all-literal shape,
    `std::format("fmt", args...)` otherwise, with the explicit-length
    `std::string("...", N)` / `std::vformat` arms when a literal segment embeds
    a NUL byte. The non-mirrored arg-type rows (user / union / container / Any)
    are gate-excluded under any conversion.
    The result is an owned `str` (STORAGE form), landing bare in owned sinks
    like any owned-str call result."""
    parts: tuple['str | THIRFStringArg', ...]


@dataclass(frozen=True)
class THIRCharLiteral(THIRExpr):
    """A single-char str literal rendered as a C++ char literal (`'x'`, via
    `escape_cpp_char`). Arises only where the AST threads a `Char` target into
    the literal render (gen_expr's is_char_type arm): a comparison operand
    opposite a Char-typed value (`_comparison_targets`' char arm), a
    Char-annotated decl init (`c: Char = 'x'` -> `char c = 'x';`), or a call
    arg into a Char param slot (`take('a')` -> `take('a')`). Every other str
    literal stays a `THIRStrLiteral`. Char-targeted literal reassigns and
    returns cannot reach lowering -- sema rejects them (`c = 'y'` /
    `return 'q'` at a Char slot are type errors; only the annotated decl form
    converts) -- so their gate rejects are defensive."""
    value: str


@dataclass(frozen=True)
class THIRName(THIRExpr):
    """Local / param reference. `deref` marks an F2 pointer-local (`T*`) read
    in a value position (a record call arg), rendered `(*name)` -- the mirror
    of `gen_expr_deref`'s indirect-name deref. Non-pointer names render bare.

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
    cpp: str | None = None


@dataclass(frozen=True)
class THIRSelf(THIRExpr):
    """The instance-method receiver `self`, rendered as the C++ `this` pointer.

    A distinct node rather than a `THIRName("self")` because `this` is a C++
    keyword `escape_cpp_name` would mangle to `this_`, and because `self` is a
    pointer receiver: field reads off it render with `->`. Only arises in an
    instance method admitted to the slice (the free-function slice never sees
    it). `deref` renders `(*this)` -- the indirect-name deref a value position
    applies (`return self` at a record borrow-return slot)."""

    deref: bool = False


@dataclass(frozen=True)
class THIRBinOp(THIRExpr):
    """Binary operation. `resolved` carries the operator's C++ template and
    operand wrappers (from sema); `divisor_non_zero` swaps the checked div/mod
    helper for the unchecked one, mirroring the AST emit path. `resolved` is
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
    # (`x = (a + b)`); an augmented-assignment RHS is a full statement RHS where
    # the AST omits that wrap (`x = a + b;`). False reproduces the latter.
    paren_wrap: bool = True
    # Per-side operand cast wraps (`{0}` templates), applied to the emitted
    # operand strings before the wrapper/template expansion -- the AST's
    # post-generation casts: int-enum operands cast to their underlying type
    # (`static_cast<int32_t>(...)`), a BigInt operand of a mixed BigInt/float
    # compare casts to the float operand's type. Computed at lowering.
    left_cast: 'str | None' = None
    right_cast: 'str | None' = None


@dataclass(frozen=True)
class THIRIsNone(THIRExpr):
    """A `name is None` / `name is not None` identity test on a pointer-repr
    Optional borrow name (an `Optional[record]` param or an OPTIONAL_TO_PTR
    local -- a bare `T*`), rendered as the pointer comparison
    `(operand == nullptr)` / `(operand != nullptr)` -- _gen_binop's identity
    arm over an indirect name. The AST canonicalizes the operand order (the
    Optional side renders first whichever side of `is` it appears on), so the
    node carries only the Optional operand; the storage-form sources
    (`has_value()`) and protocol slots (typed null) are gate-rejected.
    `result_type` is always bool; VALUE form."""
    operand: THIRExpr
    negate: bool = False


@dataclass(frozen=True)
class THIRUnaryNot(THIRExpr):
    """Logical `not` over a bool-typed operand -> `(!(operand))`. Eligibility
    pins the operand to bool, where the AST's truthiness render
    (`gen_truthy_expr`) reduces to the plain value render this wraps -- so one
    emit serves value and condition position alike. `result_type` is always
    bool. The arithmetic unaries (`- + ~`) and non-bool truthiness (int /
    Optional / `__bool__` wrappers) stay on the AST path."""
    operand: THIRExpr


@dataclass(frozen=True)
class THIRIfExpr(THIRExpr):
    """Conditional expression `a if c else b` -> `((cond) ? (then) : (else))`.
    `cond` is a truthiness position (same admitted set as if/while conditions,
    where the truthy render equals the value render or the enum wrap). Arms are
    lowered against the ternary's own resolved type -- the AST ignores the
    consumer's target (`branch_target = result_type`), so the node needs no
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
    """Call to a plain free function. `callee` is the source name; the
    emitter renders `escape_cpp_name(callee)(args)` for the same-module
    case. Eligibility guarantees no generic/overload name mangling.

    `callee_cpp` (when set) is a cross-module callee's PRE-RENDERED
    absolute spelling (`::tpyapp::mod::f` -- `imported_free_callee_cpp`,
    the qualification decision shared with the AST emit): the emitter
    renders it verbatim over the args. Mutually exclusive with
    `native_name`/`cpp_template`; `callee` stays the source name for the
    dump.

    `native_name` (when set) is a runtime-helper C++ symbol -- an fi-resolved
    `@native` free-function builtin (e.g. `tpy::__len__` for `len(c)`) or a
    hardcoded fallback helper mirroring an AST hardcode (`tpy::__delitem__`
    for `del c[k]`): the emitter renders
    `qualify_native_name(native_name)(args)` instead of the bare callee, so the
    dispatch keys on the resolved symbol, not the source name (a user function
    that happens to be named `len` has `native_name=None` and stays a plain
    call).

    `cpp_template` (when set) is a scalar type-constructor call's resolved
    `__init__` template (`Int32(x)` -> `::tpy::int_cast_check<int32_t>({0})`),
    already fully substituted by sema ({cpp} / class type params) so only
    positional `{0}, {1}, ...` placeholders remain -- the eligibility gate
    enforces that. The emitter expands it over the args with no receiver
    (gen_call_from_fi's template arm); `callee` is the source type name, kept
    for the dump only."""
    callee: str
    args: tuple[THIRExpr, ...]
    native_name: str | None = None
    cpp_template: str | None = None
    callee_cpp: str | None = None


@dataclass(frozen=True)
class THIRUnionArgLift(THIRExpr):
    """A temp-free call arg lifted inline into a pointer-variant union slot --
    the inline arms of `_gen_union_arg`: a `None` literal renders the monostate
    member (`std::variant<...>{std::monostate{}}`, `value=None`), a
    member-typed record name the address-of lift (`std::variant<...>{&(name)}`;
    `deref` prepends the pointer-local/receiver deref -- `&((*p))` /
    `&((*this))` -- mirroring `gen_expr_deref`'s indirect-name render), and an
    already-union name into a deep-const slot the explicit const conversion
    (`const_wrap`: `::tpy::ptr_variant_to_const<std::variant<...>>(name)`).

    `variant_cpp` is the slot's pointer-variant spelling, fixed at lowering:
    const-pointee (`std::variant<const A*, ...>`) for a deep-const slot (a
    `readonly[...]` annotation or the callee's `deep_const_borrow_params`
    verdict), the mutable spelling otherwise. Beyond that split the member
    render is const-blind on the AST path (it spells the callee's variant
    whatever the source's const-ness -- a const source into a MUTABLE slot is
    a pre-existing AST miscompile the mirror reproduces, see BUGS.md). BORROW
    form -- the variant aliases the named source."""
    variant_cpp: str
    value: THIRExpr | None = None  # None -> the monostate member
    deref: bool = False
    const_wrap: bool = False


@dataclass(frozen=True)
class THIROptionalPtrArg(THIRExpr):
    """A temp-free value into a pointer-repr `Optional[record]` slot
    (`const A*` / `A*`) -- a call arg (the inline arms of
    `_gen_optional_ptr_arg`'s non-protocol tail) or a return value (the same
    renders via `_optional_pointer_form_value`): a `None` literal renders
    `nullptr` (`value=None`; the typed-null spelling is protocol-only, and
    protocol slots are gate-rejected), a record name the address-of
    (`&(name)`, `addr_of`), and a storage-form Optional field read the
    `::tpy::optional_to_ptr(...)` lift (`lift`). An already-pointer name (an F2 pointer-local, a
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
    """A same-module user-record constructor call rvalue (`A(7)`), admitted
    only as a call arg: into an `Own[union]` value-variant slot (bare), as a
    method arg into a const same-record ref slot (`a.combine(A(9))` -- the
    method arg loop inlines the expansion, unlike the free-fn rvalue-temp
    arm), or as a `THIRArgTemp` init (the free-fn same-record ref-slot
    hoist). Renders
    `type_cpp(args)` -- `_gen_call`'s record-branch tail, which emits the RAW
    source name (no `escape_cpp_name`, no cross-module qualification; both
    gate-enforced). Args are value scalars into plain scalar slots (every
    `_gen_record_ctor_args` special arm is gate-excluded), so each renders
    bare. STORAGE form -- a fresh self-contained value the slot's variant
    converting ctor consumes."""
    type_cpp: str
    args: tuple[THIRExpr, ...] = ()


@dataclass(frozen=True)
class THIRArgTemp(THIRExpr):
    """A call arg hoisted into a `__tmp_N` declaration flushed before the
    enclosing statement, rendering as the bare temp name at the arg position
    (or `std::move(__tmp_N)` when `move`). Three admitted rows: a
    member-valued scalar into a value-union slot
    (`std::variant<...> __tmp_N = <arg>;`, `_gen_union_arg`'s value branch),
    a same-module record-ctor rvalue into a same-nominal ref slot
    (`A __tmp_N = A(7);`, the free-call `is_ref_param + is_temporary_expr`
    arm), an lvalue into an `Own[T]` slot (`auto __tmp_N = <arg>;` +
    the `move` wrap -- gen_call_arg's copy+move cascade; a movable NAME at
    its last use skips the temp via `THIRMove` instead), and a record-ctor
    rvalue into a pointer-repr `Optional[record]` slot (`A __tmp_N = A(7);`
    + the `addr_of` wrap -- `_gen_optional_ptr_arg`'s temporary face). Only
    the flushable statement positions admit it (expr stmt / var-decl init /
    name assign / scalar field write / return): a while-condition hoist is
    the stale-snapshot miscompile (BUGS.md), an elif temp breaks the flat
    `else if` chain -- both gate-rejected.

    Carries NO temp number: numbering is emit-time via the TempSink (the
    `__slot_N` precedent), drawing real numbers from the module-cumulative
    `ctx.temps` counter so THIR and AST bodies interleaved in one module
    stay continuous. `cpp_type` is the declared C++ type rendered at
    lowering (`None` -> `auto`, the Own-slot copy row / `TempState.create`'s
    protocol arm); `brace_init` selects `{init}` over `= init`.
    `form` mirrors how the temp reads at the arg position: VALUE for
    the value-union row (like a same-union name) and for a scalar Own-slot
    payload, BORROW for the record ref-slot row (a record lvalue the ref
    param binds) and the optional-ptr `addr_of` row, STORAGE for a moved
    RECORD Own-slot payload (a self-contained value the slot consumes)."""
    init: THIRExpr
    cpp_type: str | None = None
    brace_init: bool = False
    move: bool = False
    addr_of: bool = False


@dataclass(frozen=True)
class THIRMove(THIRExpr):
    """A movable owned local consumed at its last use by an `Own[T]` call-arg
    slot: renders `std::move(<value>)` (gen_call_arg's `_maybe_move` arm).
    Lowering creates it only when the movability + last-use facts fire (the
    same `movable_locals` + `all_last_uses` reads as the AST); the
    non-movable lvalue shape hoists a `THIRArgTemp` copy instead. STORAGE in
    practice -- only owned records are ever movable, and a moved record is a
    self-contained value handed to the consuming slot (the lowering's scalar
    branch is unreachable here: scalars are never in `movable_locals`)."""
    value: THIRExpr


@dataclass(frozen=True)
class THIRMethodCall(THIRExpr):
    """Method call on a builtin-container or user-record receiver, carrying the
    facts `gen_call_from_fi` dispatches on, materialized at lowering from the
    resolved FunctionInfo. Emit tries the arms in the same order: `cpp_template`
    (expanded with the receiver + args, e.g. `xs.sort()` -> `std::stable_sort(
    xs.begin(), xs.end())`), else `native_function_name` (a `@native(...,
    function=True)` free-function symbol with the receiver prepended as the
    first argument, e.g. `xs.pop()` -> `::tpy::pop_back(xs)`), else the plain
    member call `receiver.method_cpp(args)` (`method_cpp` is the `@native`
    member rename or the escaped source name, e.g. `xs.append(v)` ->
    `xs.push_back(v)`; `a.combine(b)` -> `a.combine(b)`).

    The eligibility gate admits only the AST path's pass-through shapes -- a
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
    runtime null check (`::tpy::deref_check(p).method(args)`,
    _gen_method_call's runtime-check arm) -- like THIRFieldAccess it is
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

    def __post_init__(self) -> None:
        assert not (self.deref_check and self.is_arrow)


@dataclass(frozen=True)
class THIRContainerLiteral(THIRExpr):
    """A container-literal local initializer, emitted per the sema-RESOLVED
    container family in `result_type` (the vector-vs-array decision for a list
    literal -- sema's PendingListType resolution -- is already final at lowering).
    Mirrors the scalar branches of `_gen_array_literal` / `_gen_dict_literal` /
    `_gen_set_literal`:

    - list / `Array[T, N]` -> `{e1, e2}` (brace-init consumed by the spelled
      decl type); an empty LIST spells the type (`std::vector<T>{}`, the
      T*-assignment-ambiguity guard)
    - dict -> `::tpy::ordered_map<K, V>({{k1, v1}, ...})`; empty -> `()`
    - set -> `::tpy::ordered_set<T>({e1, e2})`; empty -> `()`

    `values` is used only by the dict family (zipped with `elements` as keys).
    Elements are value scalars or str-slice values (S5): a view-form str source
    into an owned `std::string` slot arrives wrapped in the S1 view->owned
    `THIRFormConvert` (`std::string(x)`), decided at lowering -- everything else
    lands bare. The movable / nocopy / union / protocol branches are
    gate-excluded (scalars and the str family are value types, never in
    `movable_locals`), so the AST's `make_vector` move arm never arises."""
    elements: tuple[THIRExpr, ...]
    values: tuple[THIRExpr, ...] = ()


@dataclass(frozen=True)
class THIRTupleLiteral(THIRExpr):
    """A value-tuple literal `(a, b)` at a fully-targeted slot -- the
    all-VALUE-elements path of `_gen_tuple_literal` (`has_ref_elements`
    False): the spelled `std::tuple<...>{e1, e2}` render, position-independent
    (return / decl init / call arg). `result_type` is the SLOT TupleType, so
    the spelled type is the target's resolved element list, matching the
    AST's `resolved_elem_types` (target-provided). Elements are value scalars
    or owned-str values, lowered per element slot (`_lower_container_elem`:
    target-typed literal retypes -- the BigInt ctor wraps / Float32 `f`
    suffix -- and the S1 view->owned `std::string(x)` wrap for view-form str
    sources). Ref/const-ref element captures, borrow element slots
    (pointer-repr Optional / record refs), TypeParamRef elements, and
    target-less positions are gate-excluded."""
    elements: tuple[THIRExpr, ...]


@dataclass(frozen=True)
class THIRComprehension(THIRExpr):
    """A list/set/dict comprehension at a fresh local's decl-init -- the GCC
    stmt-expr mirror of `_gen_comprehension_iife` (the C1+C2 slice):

        ({ <container_cpp> __result; <loop head> { <binding>
           [if (c1 && c2) {] <insert>; [}] } std::move(__result); })

    Loop arms: `range` (1/2-arg counter loop; each NON-literal bound hoists
    its own `const <counter> __start/__stop_N = ...;` -- NB the comprehension
    emitter draws one loop index PER bound, unlike the statement range-for's
    single draw) and `begin_end` (`__obj_N` capture with the lvalue verdict,
    `__beg_N`/`__end_N`, the shared `loop_var_binding` or the inline
    tuple-unpack `__tup_N` lines). A 3-arg range iterates begin/end over
    the Range OBJECT (`iterable` is the substituted `::tpy::Range<T>(...)`
    template call, an rvalue capture -- _gen_comp_range_loop's fallback).
    A list result reserves (`sized_reserve`
    for begin/end over sized iterables; the range arms' `> 0` / BigInt
    `to_size_checked` guards); set/dict skip (the AST's `skip_reserve`).
    Inserts: `push_back(elem)` / `insert(elem)` / `insert_or_assign(k, v)`;
    elements arrive through the S5 per-slot owned-str wrap. Gate-excluded:
    owned-move elements (`owns_elements` -- the `__dk_N` key-sequencing and
    move-sink arms), Array demotion (`array_from_index`), genexpr,
    temp-producing elements/filters (plain `_expr_eligible` admits none),
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


@dataclass(frozen=True)
class THIRCoerce(THIRExpr):
    """A sema-inserted coercion made explicit on the IR. Two emit shapes:

    * PASSTHROUGH (`wrap is None`): the literal-into-typed-slot pair
      (`int_literal_to_fixed_int`, `float_literal_to_float`) and the identity
      positions of the str-family cross-type coercions (see lower.py
      `_coerce_disposition`), so the inner expression renders directly in the
      target type.
    * TEMPLATE (`wrap` set): the scalar-cast family (`static_cast<float>({0})`
      and friends) -- the coercion's codegen lambda mirrored as a positional
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
class THIREnumWrap(THIRExpr):
    """An enum-value render through a `{0}` wrap computed at lowering:

      * `.value`         -- `static_cast<U>({0})` (U = the underlying type);
      * `.name`          -- `::tpy::EnumUtil<E>::name({0})` (a static-storage
                            `string_view`; BORROW form, so owned-str sinks
                            fire the S1 view->owned copy);
      * IntEnum `-x`     -- `(-static_cast<U>({0}))`;
      * IntEnum truthy   -- `(static_cast<U>({0}) != 0)` (condition / `not`).

    A PLAIN-enum truthiness test renders the literal `true` with the operand
    DROPPED (`operand is None`) -- mirroring gen_truthy_expr, which discards
    the operand render (the gate admits only side-effect-free operands, so
    nothing is lost)."""
    wrap: str
    operand: THIRExpr | None = None


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
    """Subscript read `receiver[index]`, dispatched at emit on the receiver's
    resolved type family (mirrors `_gen_subscript`'s tuple and container branches).

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

    Container (list / dict), str-family (`s[i]` -> Char), or bytes-family
    (`b[i]` -> UInt8) -- a runtime
    index/key lookup, `form` VALUE (a value-scalar / Char element). A str
    element/value read (`xs[i]` on `list[str]`, `d[k]` on a str-valued dict,
    S5) carries its resolved shape instead: BORROW when the read's view var
    resolved `StrView` (drives the S1 owned-sink `std::string(x)` copy),
    STORAGE when it resolved owned (bare -- the `const std::string&` element
    copies implicitly at owned sinks on both paths). `index` is
    the lowered index expression; `bounds_safe` (sema value-range analysis)
    picks the emit -- `receiver[static_cast<std::size_t>(index)]` when proven
    in-bounds (a literal index needs no cast), else the checked dunder
    `::tpy::__getitem__(receiver, index)` (str's `__getitem__` @cpp_template
    spells the same dunder, so one emit covers both; a BYTES receiver instead
    dispatches to `::tpy::bytes_getitem(receiver, index)` -- bytes'
    `__getitem__(Int32)` is a @native free-function dunder, mirroring
    `_gen_subscript`'s fi lookup). The index is a value
    scalar (a runtime-BigInt one arrives pre-wrapped in its
    `.to_fixed_check<int32_t>()` THIRCoerce from lowering) or, for an
    owned-str-keyed dict, a
    str-slice expr rendered bare in the key slot (the static-storage literal
    pin fires only for view-typed keys, which the gate excludes)."""
    receiver: THIRExpr
    index: THIRExpr
    bounds_safe: bool = False


@dataclass(frozen=True)
class THIRStrSlice(THIRExpr):
    """A str/bytes slice off a str/bytes-family receiver, emitted via the
    sema-resolved slice `__getitem__`'s `@cpp_template` expanded over the
    receiver and the slice argument (mirrors `_gen_subscript`'s slice arm;
    the template carried on the node makes the emit family-neutral -- bytes
    carries `::tpy::bytes_slice` / `::tpy::bytes_stepped_slice`). Three index
    shapes:

      * non-stepped `s[a:b]` -- `::tpy::str_slice({self}, {0})` over a
        `::tpy::BasicSlice{lo, hi}` initializer; a `std::string_view` /
        `std::span<const uint8_t>` VIEW result (`form` BORROW).
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
    bare into `{self}`). An absent bound renders `std::nullopt`
    (`_gen_optional_slice_bound`). A VIEW result is consumed at view sinks or
    materialized at an owned sink by the view->owned `THIRFormConvert` keyed
    on the BORROW form -- str: a sema `strview_to_str` TpyCoerce (decl init /
    return) lowered via `_coerce_disposition` to `std::string(...)`; bytes: a
    coerce-less owned decl init wrapped `::tpy::bytes_copy(...)` at lowering
    (an owned bytes RETURN arrives as the gate-rejected `bytesview_to_bytes`
    coerce -> AST, the deferred cross-type bytes-coercion cell). Bounds are
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
    and parenthesized for the multi-member form -- mirrors the AST isinstance
    arm over value/pointer variants (F4 U3). `member_cpps` are the final
    template args (the `*` suffix and `const` prefix already applied for
    pointer variants at lowering, mirroring VariantAccess._type_arg);
    `variant_cpp` is the source spelled the way the AST spells it (the bare
    Python name -- the AST deliberately skips the narrowed_vars alias, and the
    slice excludes indirect / frame-slot sources)."""
    variant_cpp: str
    member_cpps: tuple[str, ...]


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
    `std::string(x)` emit stays one chokepoint. There is no `kind` field: the
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
class THIRSetItem(THIRStmt):
    """A container subscript write `c[k] = v`. `target` is the lowered
    subscript node (receiver + index + `bounds_safe`), reused for both emit
    arms: the checked `::tpy::__setitem__(c, k, v);` (every admitted
    family's `__setitem__` is the same @native free-function dunder --
    list/Array/Span/dict alike), or -- when sema proved the index in
    [0, len) -- the direct `c[static_cast<std::size_t>(k)] = v;` (a literal
    index needs no cast), sharing `_emit_subscript`'s bounds-safe render.
    An augmented `c[k] OP= v` lowers to the same node with `value` the
    synthetic `c[k] OP v` binop and `bounds_safe` forced off on BOTH reads
    -- the AST's `_gen_aug_assign_subscript_code` never takes the
    bounds-safe form. `value` is a flushable position (arg temps hoist
    before the line, like an assign value)."""
    target: 'THIRSubscript'
    value: THIRExpr


@dataclass(frozen=True)
class THIRStrAppend(THIRStmt):
    """In-place append to an owned-str local -- `t += v;` (S3). Two AST sources
    share it: the str `+=` statement (`_gen_aug_assign_code`'s string branch)
    and the `x = x + y` self-append peephole (`_try_str_inplace_append`, fired
    at a decl-reassign/assign whose RHS concat's left operand is the target).
    `target` is the local's source name; `value` renders bare --
    `std::string::operator+=` accepts string_view / const char* / string /
    an owned concat result alike, so no form wrap arises."""
    target: str
    value: THIRExpr


@dataclass(frozen=True)
class THIRReturn(THIRStmt):
    value: THIRExpr | None = None


@dataclass(frozen=True)
class THIRNarrowAlias(THIRStmt):
    """The isinstance-narrowing extraction alias (F4 U3): declared at branch
    entry, or -- for the early-return implicit else -- at statement level
    right after the `if`. Mirrors _emit_isinstance_extractions' variant arm:

        auto& __v = *std::get<A*>(v);        (pointer variant)
        const auto& __v = std::get<T>(v);    (value variant; const for
                                              value-type params)

    Reads of the narrowed source inside the alias's scope lower to
    `THIRName(alias)`; the isinstance condition keeps reading the original
    variant. `member_cpp` is the final template arg (const/`*` applied for
    pointer variants). Never carries a source comment -- the AST writes the
    alias between the brace and the first statement's comment."""
    alias: str
    variant_cpp: str
    member_cpp: str
    is_ptr_variant: bool
    const_ref: bool


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
class THIRBreak(THIRStmt):
    """`break` -- a bare `break;`. In the slice the enclosing routed loop has
    no else clause (else-loops break via `goto __after_else_N`), no finally
    frame (try is gate-rejected) and no match switch between the break and the
    loop, so the AST's `_make_break_continue` always reduces to the bare form."""


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

    `else_is_nested` mirrors the AST's elif-flattening gate: an elif whose
    outer `else_type_facts` carry a concrete extraction cannot flatten to
    `} else if (...)` (the alias must be declared inside the else block), so
    the chain breaks and the inner if emits as a nested statement --
    `} else {` + its own source comment + `if (...)` one level deeper
    (`_gen_if`'s `_has_concrete_isinstance_facts` chain-collect gate)."""
    condition: THIRExpr
    then_body: tuple[THIRStmt, ...]
    else_body: tuple[THIRStmt, ...] = ()
    else_is_nested: bool = False


@dataclass(frozen=True)
class THIRWhile(THIRStmt):
    """while loop. Slice: comparison condition or the F4 U4
    `while isinstance(...)` form (the loop-entry extraction arrives as a
    `THIRNarrowAlias` leading the body, exactly like a narrowed if branch),
    no while/else, reassign-only body -- a plain C++ `while (cond) { ... }`."""
    condition: THIRExpr
    body: tuple[THIRStmt, ...]


@dataclass(frozen=True)
class THIRAssert(THIRStmt):
    """assert statement -- mirrors `_gen_assert`'s non-constant arm:

        if (!(<cond>)) ::tpy::raise_assertion_error(["<msg>"]);

    `message` is the raw str-literal text (escaped at emit; a computed
    message evaluates lazily inside an if block, a shape the slice defers).
    An isinstance-narrowing assert is followed by a persistent
    `THIRNarrowAlias` statement appended by `_lower_stmts` (the same
    statement-level pass as the early-return post-if alias); a re-assert on
    an already-extracted subject carries the sema-folded `true` condition
    (`THIRLiteral`) and a suffix-bumped alias."""
    condition: THIRExpr
    message: str | None = None


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


@dataclass(frozen=True)
class THIRTupleUnpack(THIRStmt):
    """The `a, b = __for_tup_M` head statement of a tuple-unpack for loop --
    mirrors `_gen_tuple_unpack`'s slice arm (bare-name loop-shadow source,
    all-new plain value-scalar targets, no ref/owned/const-ref elements):

        const auto& __tup_N = __for_tup_M;
        int32_t a = std::get<0>(__tup_N);
        int32_t b = std::get<1>(__tup_N);

    `N` reproduces `ctx.unpack_counter` (per-function, pre-incremented). The
    counter's other consumers (expression-position `__tup_`/`__dk_` temps)
    are all gate-rejected, so a per-body emit counter numbers identically.
    A None target is the `_` discard -- its slot emits nothing. `target_cpps`
    carries the rendered decl types (render_type at lowering), None at
    discard slots."""
    source: str
    targets: tuple[str | None, ...]
    target_cpps: tuple[str | None, ...]


@dataclass(frozen=True)
class THIRForEach(THIRStmt):
    """`for <var> in <container>` over a NativeIterable (list / set / dict / Span /
    Array), lowered to the canonical begin/end iterator loop -- mirrors
    `_gen_begin_end_loop`:

        auto& __obj_N = <container>;
        auto __beg_N = __obj_N.begin();
        auto __end_N = __obj_N.end();
        for (; __beg_N != __end_N; ++__beg_N) {
            <elem> <var> = *__beg_N;   // value-scalar loop var (loop_var_binding)
            auto&& <var> = *__beg_N;   // record loop var (a borrow alias)
            // body
        }

    `elem_type` is the loop var's type -- a value scalar, Char, or str (a typed copy;
    a str loop var is usage-resolved at lowering to `std::string_view` or an owned
    `std::string` copy, both spelled by `loop_var_binding`) or an
    F1-record (a borrow alias: `auto&&`, or `const auto&` when `const_loop_var`). For
    list/set/Span/Array it is the element; for dict the key (`for k in d` -- a
    scalar, or a str for an owned-str-keyed dict, S5); for a str-family iterable
    (str/StrView, NativeIterable[Char]) it is Char
    (`char c = *__beg_N;`); for a bytes-family iterable (bytes/BytesView,
    NativeIterable[UInt8]) it is UInt8 (`uint8_t x = *__beg_N;`, the same
    value-scalar typed copy). `N` is the
    per-function loop index (reproducing `ctx.iter_counter`). `const_loop_var` mirrors
    sema's flag; it is inert for a cheap value scalar (the typed copy drops const either
    way) but load-bearing for a record (`const auto&` vs `auto&&`). Slice: a name
    container, a str/bytes-family field off an F1-record receiver (both C++
    lvalues: `iterable_lvalue`, the `auto& __obj_N =` capture), or an eligible
    str- or container-returning call (str and `Own[...]` container returns are
    rvalues: `iterable_lvalue=False`, the owning `auto __obj_N =` capture; a
    borrow container return is an lvalue -- `is_lvalue_iterable`'s call arm;
    bytes-returning calls stay gate-excluded); loop var not reassigned/moved
    (a record alias can't reseat) and not used after the loop. Container params reaching here are
    `list[scalar|str]` / `dict[fixed-int|str key]` (`_container_scalar_read`) and
    `list[record]`
    (`_container_record_iter`); `set` / `Span` / `Array` pass `is_native_iterable` but are
    inert as params (a `set[scalar|str]` LITERAL local iterates). Generators / user
    iterators (the
    `__iter__`/`__next__` fallback), `dict.items()` / tuple-unpack, and hoisted loop vars
    ride later cells."""
    var: str
    elem_type: TpyType
    iterable: THIRExpr
    body: tuple[THIRStmt, ...] = ()
    const_loop_var: bool = False
    iterable_lvalue: bool = True


class WithTargetArm(Enum):
    """Which `_gen_with` as-target binding arm a `with` item takes -- decided
    at lowering. The value-typed and optional-slot REUSE arms stay
    gate-rejected: their AST renders assign `&(__enter__())` into a value /
    `std::optional` slot -- the BUGS.md ill-formed with-target-reuse family.

      * `NONE`       -- no target: `__ctx_N.__enter__();`
      * `VALUE`      -- value enter type: `auto <name> = __ctx_N.__enter__();`
      * `REF`        -- reference enter type: `auto& <name> = ...;`
      * `PTR_DECL`   -- fresh reassigned record target:
                        `T* <name> = &(__ctx_N.__enter__());` (the name joins
                        the F2 pointer-locals; `target_cpp` carries `T`)
      * `ASSIGN_PTR` -- reuse of a prior with's pointer-local target:
                        `<name> = &(__ctx_N.__enter__());`
    """
    NONE = auto()
    VALUE = auto()
    REF = auto()
    PTR_DECL = auto()
    ASSIGN_PTR = auto()


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
    (`lc.render_type`, the same source as an F2 borrow local's cpp_type)."""
    ctx_expr: THIRExpr
    manager_borrowed: bool
    deref_manager: bool
    target: 'str | None'
    target_arm: WithTargetArm
    can_suppress: bool
    takes_exc_val: bool
    target_cpp: 'str | None' = None


@dataclass(frozen=True)
class THIRWith(THIRStmt):
    """A sync `with` statement -- mirrors `_gen_with` + `_emit_with_try_catch`:

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
    emit-side counter sink (shared with AST-emitted bodies). `body_terminates`
    is the AST's `stmts_terminate(stmt.body)` fact, computed at lowering; the
    emitter folds it with the items' `can_suppress` flags into the per-layer
    normal-exit elision exactly like the AST's `layer_terminates` propagation.
    While emitting the body, each layer sits on the emit-state finally-frame
    stack so `return`/`break`/`continue` inside the body render the inline
    `__exit__` chain (`_make_return` / `_make_break_continue`). The
    async / resumable-generator lowerings of `TpyWith` are different emit
    shapes entirely and stay gate-rejected (the function gate)."""
    items: tuple[THIRWithItem, ...] = ()
    body: tuple[THIRStmt, ...] = ()
    body_terminates: bool = False


@dataclass(frozen=True)
class THIRExceptHandler:
    """One throw-tier `except` clause -> a C++ catch arm. `cpp_type` is the
    handler's exception type pre-rendered at lowering (`error_return_to_cpp`
    over the sema-qualified name); None is the bare `except:` -> `catch (...)`.
    `binding` is the `as` name (raw; emit escapes) -- the catch parameter IS
    the binding, `catch (const T& name)`, no extra decl."""
    cpp_type: 'str | None'
    binding: 'str | None'
    body: tuple[THIRStmt, ...] = ()


@dataclass(frozen=True)
class THIRTry(THIRStmt):
    """A sync `try` statement -- the finally_only and throw tiers.

    finally_only (no handlers) mirrors `_gen_try_finally_only` +
    `_emit_try_with_finally`'s unified shape:

        <hoist decls>       // plain-value predecls, _emit_branch_decls' tail arm
        {
            try {
                <try body>
            } catch (...) {
                <finally body>      // frame popped: inner exits walk OUTER frames
                throw;              // unless finally_terminates
            }
            <finally body>          // unless body_terminates
        }

    throw mirrors `_gen_try_throw`: a real C++ try with one catch arm per
    handler, `else` jumping past via `goto __after_else_N` (N from the
    module-cumulative `ctx.try_except_counter` through the emit-side sink),
    the whole try/except wrapped in the finally frame above when a finally
    is present. The return tier (goto dispatch around @error_return calls)
    stays gate-rejected, parked on the @error_return call rung.

    `hoist_decls` is the sema hoist (`if_branch_decls[id(stmt)]`) rendered at
    lowering as `(name, cpp_type)` pairs in sema's sorted order -- names spell
    RAW like the AST arm (no escape). The finally body re-emits at every exit
    site through the emit-state finally-frame stack (the with frames' stmt-list
    generalization): counters keep advancing per copy exactly like the AST's
    repeated `gen_stmt` runs. `finally_terminates` is the AST's last-stmt
    raise/return fact; `body_terminates` is the terminates fact of whatever
    the finally frame wraps -- `stmts_terminate(try_body)` for finally_only,
    `stmts_terminate([the whole statement])` for the throw tier -- driving the
    normal-path finally elision. The async/resumable lowerings stay
    gate-rejected."""
    tier: str = "finally_only"
    try_body: tuple[THIRStmt, ...] = ()
    handlers: tuple[THIRExceptHandler, ...] = ()
    else_body: tuple[THIRStmt, ...] = ()
    finally_body: tuple[THIRStmt, ...] = ()
    hoist_decls: tuple[tuple[str, str], ...] = ()
    body_terminates: bool = False
    finally_terminates: bool = False


@dataclass(frozen=True)
class THIRRaise(THIRStmt):
    """`raise X(args)` -> `throw <cpp>(<args>);` / `raise X` -> `throw <cpp>{};`
    (the AST's fresh-construction peephole: static and dynamic types coincide,
    so no `__raise__()` virtual hop); bare `raise` -> `throw;` (a C++ rethrow;
    sema restricts placement, and the return-tier `make_unexpected` arms are
    unreachable -- return-tier trys and @error_return bodies are
    gate-rejected). `cpp_type` pre-renders at lowering (`error_return_to_cpp`);
    None is the bare form. The expression form (`raise e` ->
    `e.__raise__()` + deref chain) is a deferred row. `via_virtual`
    mirrors sema's `raise_via_virtual` fact (@virtual_raise classes: the
    peephole doesn't apply; emit `<cpp>(<args>).__raise__();`)."""
    cpp_type: 'str | None' = None
    args: tuple[THIRExpr, ...] = ()
    via_virtual: bool = False


@dataclass(frozen=True)
class THIRMatchBinding:
    """A capture / `as` name bound to the whole subject in a scalar-tier
    arm. `mode` folds `_emit_binding`'s value-subject arms at lowering:
    'assign' (a hoisted / pre-declared local -- plain `name = subject;`;
    the pointer/optional-local assign arms never fire for the admitted
    scalar/str subjects), 'copy' (sema's `bind_by_value` free-copy scalar
    -- `auto name = subject;`), 'ref' (`auto& name = subject;`). The name
    is raw; emit escapes. `from_case_var` (union tier) binds against the
    arm's `__case_{i}` extraction alias (or the composed `std::get` when
    no alias was drawn) instead of the subject. A FIELD capture
    (`case C(f=name)`) carries `subject_suffix=".f"`: the emit composes
    `{base}{suffix}` for the RHS (`_gen_match_field_bindings`' spelling),
    the base being the subject (record tiers) or the alias (union
    tiers)."""
    name: str
    mode: str  # 'assign' | 'copy' | 'ref'
    from_case_var: bool = False
    subject_suffix: str = ""


@dataclass(frozen=True)
class THIRMatchArmEntry:
    """One source `case` inside a THIRMatchArm group -- the mirror of
    `_group_switch_arms`' `_SwitchEntry`. `binding` is the arm block's
    first line (a `case x:` capture or a `case <pattern> as z:` name);
    `guard` is the lowered guard, rendered raw (`if (guard)`) -- bool-typed
    and call-free by the gate, so no truthy wrap and no temp flush point
    needed. `loc` feeds the source comment (the AST comments each group's
    FIRST entry only; the chain tiers comment every arm -- their groups
    are single-entry)."""
    body: tuple[THIRStmt, ...] = ()
    loc: 'SourceLocation | None' = None
    binding: 'THIRMatchBinding | None' = None
    guard: 'THIRExpr | None' = None
    # Union tier: the arm's numeric variant index (`_variant_index` over
    # the wrapper's full member ordering, computed at lowering) and the
    # `__case_{i}` extraction alias -- emitted as
    # `auto& __case_i = [*]std::get<idx>(subject);` when sema narrowing
    # (or a field binding, deferred) needs it; arm-body reads of the
    # subject were renamed to the alias at lowering (the U3 mechanic).
    variant_index: 'int | None' = None
    case_alias: 'str | None' = None
    # Record/union field sub-patterns. `field_conds` are `_record_field_
    # conditions`' literal arms as (prefix, suffix) pairs around the runtime
    # base spelling (only known at emit: `__match_subject_N` for the record
    # tiers, `__case_{idx}` for the guarded-union tier) -- the emit composes
    # `{prefix}{base}{suffix}`, `&&`-joined. `field_bindings` are the
    # keyword captures (`subject_suffix` carries the `.field` accessor).
    # `or_conds` marks an or-pattern arm of condition-only class
    # alternatives: one (possibly empty after the AST's wildcard-alt clear /
    # empty-alt skip) tuple of cond groups, `||`-joined in parens; None for
    # non-or arms. Or-arms never carry bindings (the AST drops them --
    # gate-rejected, see BUGS.md).
    field_conds: tuple[tuple[str, str], ...] = ()
    field_bindings: tuple[THIRMatchBinding, ...] = ()
    or_conds: 'tuple[tuple[tuple[str, str], ...], ...] | None' = None


@dataclass(frozen=True)
class THIRMatchArm:
    """One arm GROUP of a scalar-tier THIRMatch. `labels` are
    per-alternative spellings pre-rendered at lowering: for the switch
    tiers, C++ case labels (`_enum_member_cpp` for enum members -- the
    AST's gen_expr ENUM arm -- or `_switch_literal_label`'s bare int
    spelling; an or-pattern carries one label per alternative, stacked
    `case A:` lines sharing one block); for the if/elif tiers,
    `_gen_literal_cond`'s comparison RHS (the emit composes
    `{subject} == {rhs}`, ||-joined for or-patterns). Empty `labels` is the
    always-match arm -> `default:` (grouped last, like `_group_switch_arms`'
    default_entries) or the chain's `} else {` / bare `{` block. The chain
    tiers keep one source case per group (source order); the switch tiers
    merge same-label cases into one group whose `entries` emit as
    `_emit_switch_groups`' guard chain (guarded-first, unguarded-last --
    sema's duplicate-case check enforces the order)."""
    labels: tuple[str, ...] = ()
    entries: tuple[THIRMatchArmEntry, ...] = ()


@dataclass(frozen=True)
class THIRMatch(THIRStmt):
    """A `match` statement -- the unguarded scalar tiers: the switch tiers
    (M1: switch_enum / switch_primitive) mirroring `_gen_match_dispatch` +
    `_emit_switch_groups`' no-guard/no-capture shape, and the if/elif tier
    (M2: bool/BigInt/float/str subjects below the str switch-dispatch
    threshold) mirroring `_gen_match_if_elif`'s unguarded `==` chain --
    source-order arms, wildcard as the final `} else {`, no end label, no
    counter draw beyond the subject, and no `default:`/`break;` (a chain is
    not a switch, so `break` in an arm needs no goto escape either). The
    switch shape:

        <hoist decls>                       // _emit_branch_decls' plain tail
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
    body inside a loop renders the AST's `goto __loop_break_M` escape (M off
    the per-function iter_counter, the label after the loop's close brace)
    instead of a switch-eating bare `break;`. Guards, captures/as bindings,
    the if-elif tiers, and every non-scalar subject stay gate-rejected;
    guarded groups' second counter draw (`__match_end_N` / `__match_default_N`)
    never happens in this tier."""
    # 'switch_enum' | 'switch_primitive' | 'if_elif' | 'if_elif_guarded'
    # | 'switch_union' | 'guarded_union' | 'if_elif_record' | 'guarded_record'
    # | 'optional_partition'
    strategy: str = "switch_enum"
    subject: 'THIRExpr | None' = None
    subject_ref: bool = True          # auto& (lvalue subject) vs auto
    arms: tuple[THIRMatchArm, ...] = ()
    hoist_decls: tuple[tuple[str, str], ...] = ()
    is_exhaustive: bool = False
    emit_unreachable: bool = False    # is_exhaustive AND every arm terminates
    synthetic_default: bool = False   # no wildcard AND not exhaustive
    # _emit_switch_groups' needs_default_goto fold: a user default exists
    # AND some labeled group is entirely guarded -- its chain falls back via
    # `goto __match_default_N;` onto the `default: __match_default_N: {`
    # label, N drawing the second per-function counter bump (before the
    # switch head).
    default_goto: bool = False
    # switch_union: the subject's runtime form -- `*std::get<I>(...)` vs
    # `std::get<I>(...)` (`_subject_is_ptr_variant` folded at lowering;
    # recursive-alias wrapper subjects, the `.value` indirection, are
    # parked against the wrapper-form rung -- wrapper params/locals are
    # function-gated, so no wrapper match can reach this node yet).
    is_ptr_variant: bool = False
    # optional_partition (O1): `_gen_match_optimized_optional` over a
    # pointer-repr `Optional[F1-record]` name subject. The None prefix arm
    # (body/loc; bindings gate-rejected -- a None-arm `as` is a sema error
    # and a full-Optional capture defeats the partition) emits inside
    # `if (subj == nullptr) { ... } else {`; None here is the no-None-arm
    # form (`if (subj != nullptr) {`). The else block draws the
    # `__match_inner_N` deref alias (SAME counter value as the subject --
    # gen_match numbers both off one bump) and `arms` holds the single
    # always-match inner arm (`_emit_optional_inner_record`'s no-field
    # `{ }` block; sema's unreachable-arm rule caps the unguarded inner
    # dispatch at one). Its binding binds vs the inner alias.
    none_entry: 'THIRMatchArmEntry | None' = None


class PrintForm(Enum):
    """How a `print()` argument is wrapped in the `std::cout << ...` chain --
    decided at lowering from the arg's resolved type, so the emitter renders the
    chosen wrapper without re-inspecting types (mirrors `gen_print`'s per-arg
    dispatch for the common-arg subset).

      * `RAW`     -- direct `<<` (a wider fixed-int, or a `THIRStrLiteral`).
      * `INT8`    -- `static_cast<int>(...)`, so an 8-bit int isn't printed as a char.
      * `BOOL`    -- `::tpy::print_bool(...)` (Python-style `True`/`False`).
      * `FLOAT`   -- `::tpy::print_float(...)` (Python-style float formatting).
      * `FLOAT32` -- `::tpy::print_float(static_cast<double>(...))` (the float
        overload takes double; gen_print's is_float32_type arm).
      * `BYTES`   -- `::tpy::BytesPrinter(...)` (Python-style `b'...'` repr).
      * `REPR`    -- `::tpy::__repr__(...)` (an @native enum: no operator<< is
        emitted for it, so gen_print routes through the EnumUtil-backed repr).
      * `LIST`/`SET`/`DICT` -- the container-printer wraps (gen_print's
        container arms); currently only comprehension args take these (the
        C3 print-arg row).
    """
    RAW = auto()
    INT8 = auto()
    BOOL = auto()
    FLOAT = auto()
    FLOAT32 = auto()
    BYTES = auto()
    REPR = auto()
    LIST = auto()
    SET = auto()
    DICT = auto()


@dataclass(frozen=True)
class THIRPrintArg:
    """One `print()` argument: the lowered expression + how the emitter wraps it.
    `print_form` is named distinctly from `THIRExpr.form` (the unrelated
    borrow/storage axis) to keep the two from being conflated."""
    expr: THIRExpr
    print_form: PrintForm


@dataclass(frozen=True)
class THIRPrint(THIRStmt):
    """A `print(<args>)` statement with default `sep=" "`, `end="\\n"`, sink
    `std::cout` -- the slice excludes `sep=`/`end=`/`file=`/`flush=` kwargs.
    Emits `std::cout << a0 << " " << a1 << ... << "\\n";`. Args are the common
    subset (str literal / fixed-int / bool / double); everything else stays AST."""
    args: tuple[THIRPrintArg, ...] = ()


@dataclass(frozen=True)
class THIRExprStmt(THIRStmt):
    """A bare expression statement evaluated for its side effects (`foo(x)`).
    Currently only a same-module free-function call reaches here (via the
    `_call_eligible` guards, statement position -- a discarded scalar or `None`
    return); the emitter renders `<expr>;`."""
    expr: THIRExpr


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
