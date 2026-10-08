"""Shared expression value-category predicates (borrow-alias vs rvalue).

Codegen asks the value CATEGORY of an initializer (`is_rvalue_source`:
render a local as an owned value or a `T&` reference); sema asks whether the
bound name OWNS the object (`binds_owned_value`: owned-movable or
borrow-alias). The two agree except for a call handing back the pointer
borrow form (`T*` for a pointer-repr Optional, `Union<A*, B*>`): a C++
prvalue whose referent the callee lent. Keeping both answers in one place
avoids the divergence that lets sema mark a borrow-alias as movable -- which
then moves out of the alias and corrupts the source.

The `analyzer` argument is duck-typed: it only needs `get_expr_type(expr)`
and `registry`. Both the sema `AnalyzerContext` and the codegen
`SemanticAnalyzer` satisfy this.
"""

from enum import Enum, auto
from typing import Any, Callable, NamedTuple, Protocol

from .typesys import pointer_repr_optional, pointer_variant_union
from .typesys import (
    FunctionInfo, NominalType, PtrType, ReadonlyType, TpyType, TupleType,
    TypeParamRef, OwnType,
    OptionalType, ResultPosition, ResultRepresentation, classify_result_representation,
    is_bodyless_binding, is_open_type_param_return, is_primitive_type, is_protocol_type,
    indirection_referent_readonly, declared_result_readonly, result_follows_receiver_root,
    declares_whole_result_following,
    property_getter_returns_storage_ref, returns_cpp_reference_shape,
    unwrap_optional_own, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync,
)
from .parse import (
    TpyExpr, TpyForEach, ResultForm, TpyCallLike,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral, TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression, TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall,
    TpySubscript, TpyCall, TpyName, TpyFieldAccess, TpyIfExpr, TpyAwait,
    TpyBytesLiteral, TpyFString, TpyNamedExpr, TpyTupleLiteral,
    is_property_getter_read,
)
from .type_def_registry import (is_bool_type, is_borrowing_view_type,
                                is_bytes_type, is_bytes_view_type,
                                is_str_type, is_str_view_type)
from . import qnames


class CallOperands(NamedTuple):
    """A call-shaped expression as the borrow facts index it: the callee,
    its receiver (source index -1), its positional arguments and the
    keyword arguments sema left unnormalized."""
    fi: FunctionInfo
    obj: TpyExpr | None
    args: list[TpyExpr]
    kwargs: dict[str, TpyExpr] = {}

    def positioned(self) -> list[tuple[int, TpyExpr]]:
        """Each argument with the parameter index it binds -- positionally,
        then keywords by name (-2 for a name no parameter has)."""
        by_name = {p.name: i for i, p in enumerate(self.fi.params)}
        return list(enumerate(self.args)) + [
            (by_name.get(k, -2), a) for k, a in (self.kwargs or {}).items()]

    def indexed(self) -> list[tuple[int, TpyExpr]]:
        """The receiver at source index -1 when there is one, then
        `positioned()`: every operand a borrow source index can name."""
        recv = [] if self.obj is None else [(-1, self.obj)]
        return recv + self.positioned()


def call_operands(expr: TpyExpr) -> CallOperands | None:
    """The operands a call-shaped `expr`'s `return_borrows_from` indexes,
    whatever form sema stamped its result, or None when `expr` is not a
    resolved call.

    Operator dispatch is a method call in disguise -- a borrow-returning
    dunder hands out a borrow of an operand -- and answers with the CANONICAL
    fi: the resolved copy is synthesized before the dunder's body facts land,
    so only the root carries them.
    """
    kwargs: dict[str, TpyExpr] = {}
    if isinstance(expr, TpyCall):
        fi, obj, args = expr.resolved_function_info, None, expr.args
        kwargs = expr.kwargs or {}
    elif isinstance(expr, TpyMethodCall):
        fi, obj, args = expr.resolved_function_info, expr.obj, expr.args
        kwargs = expr.kwargs or {}
    elif isinstance(expr, TpyBinOp) and expr.resolved_binop is not None:
        rb = expr.resolved_binop
        fi = rb.method.root
        obj = expr.right if rb.is_reverse else expr.left
        args = [expr.left if rb.is_reverse else expr.right]
    elif isinstance(expr, TpyUnaryOp) and expr.resolved_unaryop is not None:
        fi, obj, args = expr.resolved_unaryop.method.root, expr.operand, []
    elif (isinstance(expr, TpyGeneratorExpression)
          and expr.frame_creation is not None):
        return call_operands(expr.frame_creation)
    else:
        return None
    return None if fi is None else CallOperands(fi, obj, args, kwargs)


def wants_move(t: TpyType) -> bool:
    """Whether moving a value of this type beats copying it: reference
    types always, value types only when the copy is expensive (String,
    BigInt, ...). For trivial types std::move is just noise."""
    return not t.is_value_type() or t.is_expensive_copy()


class AsyncReturnForm(Enum):
    """The Poll-payload shape of an `async def -> R` result.

    Mirrors the sync return convention (`call_returns_cpp_ref`): a bare
    reference-type return is a borrow. A C++ reference cannot live in a
    Poll payload (it travels by value up the poll chain), so the async
    borrow form is a pointer.
    """
    STORAGE = auto()  # by-value payload: value types, Own[T], unions, ...
    BORROW = auto()   # pointer payload: T* (incl. pointer-repr Optional)
    TRAIT = auto()    # generic T: ::tpy::val_or_ptr_t<T> at instantiation


def async_return_form(ret_type: 'TpyType | None') -> AsyncReturnForm:
    """Adapt the shared representation decision to the Poll-payload API."""
    representation = classify_result_representation(
        ret_type, position=ResultPosition.ASYNC_PAYLOAD)
    if representation is ResultRepresentation.ASYNC_POINTER:
        return AsyncReturnForm.BORROW
    if representation is ResultRepresentation.ASYNC_TRAIT:
        return AsyncReturnForm.TRAIT
    return AsyncReturnForm.STORAGE


def async_result_aliases(declared_return: 'TpyType | None',
                         inner_type: 'TpyType | None') -> bool:
    """True when awaiting this coroutine yields a borrow (an alias of
    caller-durable storage) rather than an owned value.

    `declared_return` is the async def's raw declared return (the
    pre-Cancellable-wrap `FunctionInfo.async_inner_return`);
    `inner_type` is the (substituted) awaited type, consulted only for
    the generic TRAIT form, where borrow-ness is decided per
    instantiation exactly as `val_or_ptr_t<T>` decides it: non-value ->
    pointer.
    """
    form = async_return_form(declared_return)
    if form is AsyncReturnForm.BORROW:
        return True
    if form is AsyncReturnForm.TRAIT:
        inner = (unwrap_readonly(unwrap_ref_type(inner_type))
                 if inner_type is not None else None)
        return (inner is not None and not inner.is_value_type()
                and not isinstance(inner, TypeParamRef)
                and not is_protocol_type(inner))
    return False


class ValueCategoryAnalyzer(Protocol):
    """The slice of sema/codegen state these predicates need. The sema
    `AnalyzerContext` and the codegen `SemanticAnalyzer` both satisfy it."""
    registry: Any

    def get_expr_type(self, expr: TpyExpr) -> 'TpyType | None': ...


# Container/generator-shaped expressions whose gen_expr emits a value
# (`vector<T>{...}`, `ordered_map<K, V>{...}`, generator state struct, ...)
# regardless of the target type. Distinct from pointer-emit rvalues
# (function calls returning T*, pointer-local names) which already yield
# stable pointer storage. Codegen sites initializing a pointer-form slot
# from an Optional source use this to decide whether to materialize a
# named slot before taking address.
CONTAINER_LITERAL_NODES: tuple = (
    TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral,
    TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression,
)


def call_returns_cpp_ref(analyzer: ValueCategoryAnalyzer, fi: 'FunctionInfo | None') -> bool:
    """True if this function/method call returns a C++ lvalue reference (T&).

    A function/method returns T& iff its declared return is a concrete
    (non-generic) reference type that is not owned/nullable: copy is spelled
    `Own[T]`, nullability `Optional[T]`, an unresolved generic stays
    `TypeParamRef` (rendered val_or_ref_t<T>), and protocol returns are
    value-shaped concrete structs -- all of which use value semantics. This
    holds uniformly for user-defined AND `@native` record methods: a native
    binding declares its C++ return convention through the same `-> V` vs
    `-> Own[V]` contract, so no per-native special case is needed.

    A free `@native` function takes value semantics unless it declares what
    its result borrows (`borrows=` / `element_of=`), which admits it to the shape
    check (an undeclared one: BUGS.md, "An UNDECLARED free `@native`
    function"). Native record METHODS honor the contract and fall through
    to the shape check (so dict.setdefault / items aliasing holds). This is
    the CALLEE's convention; whether one call's result may be HELD by
    reference is `call_result_holdable`.
    """
    return classify_result_representation(
        fi.return_type if fi is not None else None,
        position=ResultPosition.SYNC_CALL, fi=fi,
    ) is ResultRepresentation.CPP_REFERENCE


def declared_result_is_value(fi: 'FunctionInfo | None') -> bool:
    """Whether a binding that DECLARES its result's borrow sources hands
    that result back BY VALUE at this instantiation: the declaration lends
    only a reference-shaped result (a class instance, a container), so a
    value-shaped one -- an Optional, a union, a tuple, a number -- is a
    fresh value the caller owns, even where its pointer representation
    would read as a borrowed `T*` from an undeclared callee. A bare open
    `T` is not decided here: the instantiation may be a reference."""
    if (fi is None or not is_bodyless_binding(fi)
            or not fi.root.borrow_declared):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fi.return_type)))
    return (isinstance(rt, TpyType) and not isinstance(rt, TypeParamRef)
            and not returns_cpp_reference_shape(rt))


def call_hands_back_value(call: TpyExpr) -> bool:
    """Whether a call's C++ result is a VALUE the caller owns rather than a
    borrow of existing storage: the callee's declared return is `Own[...]`
    (which sema strips off the call's type), or sema stamped a by-value
    form on the call (`ResultForm.by_value`). Either way a pointer-repr
    Optional result is the STORAGE `std::optional<T>`, not a `T*`. The one
    question lowering and codegen ask of a call node."""
    if not isinstance(call, (TpyCall, TpyMethodCall)):
        return False
    if call.result_form.by_value:
        return True
    fi = call.resolved_function_info
    rt = getattr(fi, "return_type", None) if fi is not None else None
    return (isinstance(rt, TpyType)
            and isinstance(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(rt))), OwnType))


def lends_pointer_form_result(analyzer: 'ValueCategoryAnalyzer',
                              expr: TpyExpr) -> bool:
    """Whether a call hands back the pointer BORROW form -- a pointer-repr
    Optional (`T*`) or a pointer-variant union (`Union<A*, B*>`) -- of
    storage the callee lent. The return rule refuses a fresh value at such
    a return (`Own[...]` is the by-value spelling), so only
    `call_hands_back_value` makes one a value. Asked of the result TYPE, so
    a generic instantiation and a native stub answer at their substitution.
    A select (ternary, value `and` / `or`) hands back whichever arm runs, so
    it lends when either arm does."""
    expr = peel_coerce(expr)
    if isinstance(expr, TpyIfExpr):
        return (lends_pointer_form_result(analyzer, expr.then_expr)
                or lends_pointer_form_result(analyzer, expr.else_expr))
    if (isinstance(expr, TpyBinOp) and expr.op in ("&&", "||")
            and not is_bool_type(analyzer.get_expr_type(expr))):
        return (lends_pointer_form_result(analyzer, expr.left)
                or lends_pointer_form_result(analyzer, expr.right))
    if (not isinstance(expr, (TpyCall, TpyMethodCall))
            or call_hands_back_value(expr)):
        return False
    fi = expr.resolved_function_info
    if fi is not None and fi.is_constructor:
        return False
    t = analyzer.get_expr_type(expr)
    return (pointer_repr_optional(t) is not None
            or pointer_variant_union(t) is not None)


def binds_owned_value(analyzer: 'ValueCategoryAnalyzer',
                      expr: TpyExpr) -> bool:
    """Whether a name bound from `expr` OWNS the object it holds, so its
    last use may move it: a fresh value (`is_rvalue_source`) that is not a
    pointer-form borrow the callee lent (`lends_pointer_form_result`). The
    one ownership question every name binding (declaration, assignment,
    walrus, the bind-kind stamp) asks."""
    return (is_rvalue_source(analyzer, expr)
            and not lends_pointer_form_result(analyzer, expr))


def call_value_optional(call: TpyExpr) -> 'OptionalType | None':
    """The pointer-repr Optional a call hands back WHOLE as a value
    (`call_hands_back_value`): the `std::optional<T>` a pointer-form holder
    lifts with `optional_to_ptr`. None for any other call."""
    if not call_hands_back_value(call):
        return None
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        call.resolved_function_info.return_type)))
    if isinstance(rt, OwnType):
        rt = unwrap_readonly(rt.wrapped)
    return pointer_repr_optional(rt)


def call_result_holdable(analyzer: ValueCategoryAnalyzer,
                         expr: TpyExpr) -> bool:
    """Whether a holder that OUTLIVES the statement (a reseated pointer
    local, a reference binding) may keep this call's result by reference:
    the callee returns a C++ reference, and a borrow-declared call was not
    stamped a fresh value (`ResultForm.is_fresh`), whose reference may
    point into a temporary operand."""
    if isinstance(expr, TpyCallLike) and expr.result_form.is_fresh:
        return False
    if lends_a_fresh_result(expr):
        return False
    return call_returns_cpp_ref(
        analyzer, getattr(expr, "resolved_function_info", None))


def binds_fresh_call_value(expr: TpyExpr) -> bool:
    """Whether a name bound from `expr` OWNS what it holds because `expr`
    is a borrow-declared call sema stamped a fresh value
    (`ResultForm.is_fresh`). Whether making that value copies an object the
    program still reaches is the binding's question (`returns_borrow`,
    warned there); the bound name is no borrow afterwards, so a later sink
    moves it at its last use like any owned local."""
    expr = peel_coerce(expr)
    return isinstance(expr, TpyCallLike) and expr.result_form.is_fresh


def lends_a_fresh_result(expr: TpyExpr) -> bool:
    """Whether a call hands back a reference that may point into a fresh
    result of a borrow-declared call among what it lends
    (`k.ret(min(a, P(-8), key=f))`: the reference may be the temporary
    `P(-8)`). Read through the callee's recorded `return_borrows_from`,
    recursively; a hoisted temporary lives only to the end of its block, so
    a holder declared outside it must not keep the reference."""
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        return lends_a_fresh_result(expr.obj)
    if not isinstance(expr, (TpyCall, TpyMethodCall)):
        return False
    if expr.result_form.is_fresh:
        return True
    ops = call_operands(expr)
    sources = ops.fi.root.return_borrows_from if ops is not None else None
    # Whole operands, not `lent_operands`' tuple-literal elements: a fresh
    # result inside a tuple literal is held by the tuple temporary, whose
    # lifetime is the temp-backing question, not this one.
    return bool(sources) and any(
        idx in sources and lends_a_fresh_result(operand)
        for idx, operand in ops.indexed())


def call_result_live_in_statement(analyzer: ValueCategoryAnalyzer,
                                  expr: TpyExpr) -> bool:
    """Whether this call's C++ result is valid for the whole statement, so
    a CONST argument slot binds it in place: an lvalue result, or a
    borrow-declared call stamped a fresh value -- a reference into its
    operands, or a copy the callee hands back (`ResultForm.COPY`),
    each living until the statement ends. A mutable slot asks
    `call_result_is_reference` instead. Not an ownership verdict: a holder
    that outlives the statement asks `call_result_holdable`. (The return
    SHAPE alone does not answer it: a container constructor's `-> list[T]`
    is a reference shape and an rvalue.)"""
    if isinstance(expr, TpyCallLike) and expr.result_form.is_fresh:
        return True
    return (isinstance(expr, (TpyCall, TpyMethodCall))
            and not is_rvalue_source(analyzer, expr))


def call_result_is_reference(analyzer: ValueCategoryAnalyzer,
                             expr: TpyExpr) -> bool:
    """`call_result_live_in_statement` where the slot may be a MUTABLE
    reference: a value the callee hands back (`ResultForm.by_value`) cannot
    bind one, and goes through a statement temporary instead."""
    return (call_result_live_in_statement(analyzer, expr)
            and not (isinstance(expr, TpyCallLike)
                     and expr.result_form.by_value))


def declared_call_const(analyzer: ValueCategoryAnalyzer,
                        expr: TpyExpr) -> 'bool | None':
    """The const verdict of a borrow-declared call's result, or None when
    `expr` is not one. Sema folds it into the result TYPE (read-only exactly
    when a lent operand is), so every const reader asks this FIRST: the
    callee's `@readonly`, its receiver's const-ness and a reference-returning
    signature say nothing about a result that may be an argument
    (`ro.pick(x)` is as mutable as `x`)."""
    if not (isinstance(expr, TpyCallLike)
            and expr.result_form is not ResultForm.NOT_DECLARED):
        return None
    return isinstance(analyzer.get_expr_type(expr), ReadonlyType)


def return_type_is_cpp_ref(rt: 'TpyType | None') -> bool:
    """The return-SHAPE half of `call_returns_cpp_ref`, for a caller holding a
    composed return type rather than the callee's `FunctionInfo` (a generic
    record's re-resolved signature carries the raw `T`, which answers False
    here for a substitution that is in fact a reference)."""
    return returns_cpp_reference_shape(rt)


def property_getter_of(expr: TpyExpr) -> 'FunctionInfo | None':
    """The `@property` getter `expr` reads through, or None when it is not a
    property read.

    A read IS its getter call from the moment sema resolves it, so the
    question is about the resolved callee, never about the node kind. The
    predicate half lives on the node module (`is_property_getter_read`); this
    is the accessor over it, so the two cannot disagree about what a
    property read is.
    """
    return expr.resolved_function_info if is_property_getter_read(expr) else None


def property_access_returns_cpp_ref(analyzer: ValueCategoryAnalyzer,
                                    expr: TpyExpr) -> bool:
    """The return convention of ONE property read: does the getter hand its
    result back as a C++ lvalue reference into live storage, or by value?

    Two facts beyond the plain method convention. The storage-reference
    shapes (`property_getter_returns_storage_ref`) are spelled as the
    FIELD's storage reference where a method returns them by value. And a
    bare type-param return is spelled `val_or_ref_t<T>` -- `T&` at a
    reference argument, `T` by value at a value one -- so that convention is
    decided at the INSTANTIATION: it reads the composed property type sema
    substituted for this receiver (`get_expr_type` on the access node), not
    the declared `T`. Every other declared shape spells the same C++ at
    every instantiation and answers from the getter alone.

    The instantiation arm agrees with the emitter on every substitution it
    is asked about, but not by construction: the emitter spells
    `val_or_ref_t<T>` for EVERY open-`T` return, while `return_type_is_cpp_ref`
    subtracts Optional, Union and protocol. Nothing reaches the gap today:
    the READ does not lower at such an instantiation -- it is a located
    reject, `expr.method_call:method.record.<getter>` at a decl, the same
    one its plain-METHOD twin takes, pinned by
    `tests/cases/generics/error_open_t_property_optional_decl` -- and sema
    refuses the iterable itself before any for-each gate is asked.

    Whether the RECEIVER is a temporary is a separate question, and not this
    predicate's: a reference-returning getter answers True here however its
    receiver was obtained. `is_rvalue_source` asks both, which is what keeps
    a read off a temporary from binding a reference into it
    (`BUGS.md#readonly-borrow-of-temporary-receiver`).
    """
    fi = property_getter_of(expr)
    if fi is None:
        return False
    if property_getter_returns_storage_ref(fi.return_type):
        return True
    if is_open_type_param_return(fi.return_type):
        return return_type_is_cpp_ref(analyzer.get_expr_type(expr))
    return call_returns_cpp_ref(analyzer, fi)


class AccessorTerms(NamedTuple):
    """What a resolved `@property` read is, in the two terms its consumers
    ask about. See `_accessor_terms` for why they are not one term."""
    fi: 'FunctionInfo'
    # The read renders as a C++ lvalue reference.
    returns_ref: bool
    # The result borrows the RECEIVER's storage -- wider than `returns_ref`.
    borrows_receiver: bool


def _accessor_terms(analyzer: 'ValueCategoryAnalyzer',
                    expr: TpyExpr) -> 'AccessorTerms | None':
    """The terms both accessor questions are built from, named once.

    Returns the terms for a resolved `@property` read, or `None` when `expr`
    is not one.

    The two are NOT the same question and must not be folded. `returns_ref`
    is the value CATEGORY: does the read render as a C++ lvalue reference,
    which is what decides whether a binding may bind `T&`. `borrows_receiver`
    is the LIFETIME question and is WIDER: a view return (`StrView`,
    `BytesView`, `Span[T]`) is a value the category rightly calls an rvalue
    while its payload still points into the receiver's storage, so it borrows
    although it is not a reference. Answering the lifetime question with the
    category is how a view getter off a temporary came to be stored into a
    field with no diagnostic.
    """
    fi = property_getter_of(expr)
    if fi is None:
        return None
    returns_ref = property_access_returns_cpp_ref(analyzer, expr)
    rt = unwrap_ref_type(fi.return_type)
    borrows = (returns_ref
               or property_getter_returns_storage_ref(fi.return_type)
               or (not isinstance(rt, OwnType)
                   and rt is not None and is_borrowing_view_type(rt)))
    return AccessorTerms(fi, returns_ref, borrows)


def accessor_lends_receiver_storage(analyzer: 'ValueCategoryAnalyzer',
                                   expr: TpyExpr) -> bool:
    """`expr` is a `@property` read that hands back a BORROW of its
    receiver's storage -- a reference return, a storage-ref Optional/union,
    or a view whose payload points into the receiver.

    The LIFETIME term of `_accessor_terms`, on its own: a position asks it
    when what the read lends matters regardless of whether the receiver is
    alive (its const-ness, its identity), where `lends_from_dying_source`
    asks the pair.
    """
    terms = _accessor_terms(analyzer, expr)
    return terms is not None and terms.borrows_receiver


def lends_from_dying_source(analyzer: 'ValueCategoryAnalyzer',
                            expr: TpyExpr) -> bool:
    """`expr` hands back a BORROW of storage that dies at the end of the
    statement -- an accessor read off a temporary receiver.

    A minted result may be OWNED by the binding. A borrow of dying storage
    may not: what the callee lends can OUTLIVE its receiver (a global, a
    longer-lived object), CPython aliases it, and owning a copy would lose
    every later mutation silently -- nor may it be bound as a reference,
    which would dangle. WHICH positions those are is not this predicate's
    to say: it reports the FACT, and `_POS_FORMS` (`tpyc/thir/lower/
    context.py`) holds the verdict as one row per sink -- `SinkForm.
    DYING_SOURCE_LEND`, admitted by the transient sinks and by no sink that
    binds or holds a value past the full expression. An ARGUMENT is the one
    sink whose row is not the whole answer: it asks the CALLEE's own
    retention facts (`arg_lend_ok`).

    The ACCESSOR spelling only, and that line is INTERIM: a spelled method
    borrowing from a temporary receiver stays on the conceded
    warn-and-emit tier (`BUGS.md#readonly-borrow-of-temporary-receiver`,
    pinned by `tests/cases/list/warn_insert_temp_receiver_borrow` and its
    siblings), so widening the fact to it would turn those warnings into
    rejects -- that entry's decision, not this predicate's. When the
    temporary-receiver tier is decided, `property_getter_of` is what gets
    widened; the fact itself is spelling-blind.
    """
    terms = _accessor_terms(analyzer, expr)
    if terms is None:
        return False
    return terms.borrows_receiver and is_rvalue_source(analyzer, expr.obj)


def is_rvalue_source(analyzer: ValueCategoryAnalyzer, expr: TpyExpr) -> bool:
    """Check if an expression produces an rvalue (a fresh value / temporary).

    Rvalues: constructor calls, Own[T] returns, literals, binop/unop results,
    field access on rvalue objects (member of temporary).
    Lvalues (borrow-aliases): variable names, field access on lvalues,
    subscript, function/method returning T&, and a non-value ternary or
    and/or select with at least one lvalue operand.
    """
    # Names are lvalues (either pointer-locals, params, or globals)
    if isinstance(expr, TpyName):
        return False
    # A walrus DENOTES its target: the expression is that binding, whatever
    # the value it was given. Reading it as a fresh value would take a copy
    # away from the name the same statement just bound.
    if isinstance(expr, TpyNamedExpr):
        return False
    # Field access: rvalue iff the object is rvalue (member of temporary).
    # A `__getattr__` access keeps the field-access node kind but RENDERS as
    # a method call, so the accessor's return CONVENTION is a SECOND way for
    # it to be one -- the node kind alone reads as an inert member read.
    # Both are asked: an accessor handing back a C++ reference still borrows
    # from its RECEIVER, so off a temporary the read names storage that dies
    # at the end of the statement exactly as a plain member read off one
    # does. The receiver disjunct is inert today -- sema admits only a value
    # type, `Any` or `Own[T]` as a `__getattr__` return, so the convention
    # never answers "reference" here -- and is kept so the accessor arms
    # cannot drift if it widens.
    if isinstance(expr, TpyFieldAccess):
        hidden = expr.hidden_call
        if hidden is not None:
            return (not call_returns_cpp_ref(analyzer,
                                             hidden.resolved_function_info)
                    or is_rvalue_source(analyzer, expr.obj))
        return is_rvalue_source(analyzer, expr.obj)
    # Subscript into containers is an lvalue (returns T&).
    # Exceptions: slice calls (e.g. list_stepped_slice) and user-record
    # __getitem__ calls may return by value (e.g. a pointer-repr Optional
    # returns T*) -- both follow the resolved callee's convention.
    if isinstance(expr, TpySubscript):
        if expr.slice_function_info is not None:
            return not call_returns_cpp_ref(analyzer, expr.slice_function_info)
        if expr.getitem_function_info is not None:
            return not call_returns_cpp_ref(analyzer, expr.getitem_function_info)
        return False
    # A non-value ternary is the and/or rule below: a fresh arm beside an
    # lvalue arm is emplaced into a hoisted slot, so the result stays an
    # lvalue unless BOTH arms are fresh.
    if isinstance(expr, TpyIfExpr):
        result_type = analyzer.get_expr_type(expr)
        if result_type and not result_type.is_value_type():
            return (is_rvalue_source(analyzer, expr.then_expr)
                    and is_rvalue_source(analyzer, expr.else_expr))
    # Logical and/or with operand-return semantics: rvalue temps are
    # materialized into named variables by _gen_logical_value, so the
    # result is only an rvalue when both operands are rvalues.
    if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        result_type = analyzer.get_expr_type(expr)
        if not is_bool_type(result_type):
            return (is_rvalue_source(analyzer, expr.left)
                    and is_rvalue_source(analyzer, expr.right))
    # An awaited borrow-returning coroutine hands back a pointer into
    # caller-durable storage -- an lvalue alias, like the sync
    # borrow-returning call arm below. Owned results stay rvalues. The
    # fact is stamped on the node by sema's await analysis (the callee's
    # declared-return form), never re-derived here.
    if isinstance(expr, TpyAwait):
        return not expr.await_result_is_borrow
    # An arithmetic binop/unaryop resolved to a dunder follows the method's
    # return convention, like the method-call and subscript arms: a
    # borrow-returning `__add__`/`__neg__` aliases an operand, it does not
    # create a value.
    if isinstance(expr, TpyBinOp) and expr.resolved_binop is not None:
        return not call_returns_cpp_ref(analyzer, expr.resolved_binop.method)
    if isinstance(expr, TpyUnaryOp) and expr.resolved_unaryop is not None:
        return not call_returns_cpp_ref(analyzer, expr.resolved_unaryop.method)
    # Constructor calls, literals, ops are rvalues.
    if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                         TpyBoolLiteral, TpyNoneLiteral,
                         TpyBinOp, TpyUnaryOp)):
        return True
    if isinstance(expr, CONTAINER_LITERAL_NODES):
        return True
    if isinstance(expr, TpyMethodCall):
        # A declared-borrow method's call: sema decided, as for a free
        # binding's call below.
        if expr.result_form is not ResultForm.NOT_DECLARED:
            return expr.result_form is not ResultForm.BORROW
        terms = _accessor_terms(analyzer, expr)
        if terms is not None:
            # The accessor pair above, one node kind over: a property read
            # asks the RECEIVER as well as the convention, because a getter
            # handing back a C++ reference still borrows from the object it
            # was read off. The plain-method spelling deliberately does not
            # (`BUGS.md#readonly-borrow-of-temporary-receiver` concedes that
            # tier), so the disjunct stays keyed on the getter. The CATEGORY
            # term is the narrow one -- see `_accessor_terms`.
            _fi, returns_ref, _borrows = terms
            return not returns_ref or is_rvalue_source(analyzer, expr.obj)
        return not call_returns_cpp_ref(analyzer, expr.resolved_function_info)
    # Coercions: a rule that builds a fresh value yields a prvalue whatever the
    # source was; every other rule renders the inner through, so it inherits
    # the inner's category.
    if isinstance(expr, TpyCoerce):
        if expr.coercion.builds_fresh_value:
            return True
        return is_rvalue_source(analyzer, expr.expr)
    # Function calls
    if isinstance(expr, TpyCall):
        # Expression callees -> rvalue
        if not isinstance(expr.func, TpyName):
            return True
        # A declared-borrow binding's call: sema decided, from its lent
        # arguments, whether it hands back one of them or a fresh value.
        if expr.result_form is not ResultForm.NOT_DECLARED:
            return expr.result_form is not ResultForm.BORROW
        # Record constructors -> rvalue
        if analyzer.registry.get_record(expr.func_name):
            return True
        # Generic type constructors -> rvalue
        if expr.call_type is not None:
            return True
        if analyzer.registry.get_function(expr.func_name) is not None:
            return not call_returns_cpp_ref(analyzer, expr.resolved_function_info)
        return True  # Default: treat unknown calls as rvalue
    return True  # Default: rvalue


def for_source_is_rvalue(stmt: TpyForEach,
                         analyzer: ValueCategoryAnalyzer) -> bool:
    """What a `for` head ITERATES is a fresh value rather than existing
    storage.

    One question, one answer, for every layer that has to place the loop's
    source: the THIR for-each route (which spells the capture `auto` or
    `auto&`) and the resumable-frame prescan (for which it is half of
    "does the frame have to own this source"). A layer that re-derives it
    from node kinds disagrees with the other about a slice or a ternary, and
    the frame then lends out elements of storage it does not hold.

    The question is about the source AS WRITTEN. A route that WRAPS the
    source (`own_iter(std::move(xs))` at a container's last use) builds a
    fresh value out of an lvalue; that is a transformation downstream of this
    verdict, and the arm that performs it says so about its own product.
    """
    return is_rvalue_source(analyzer, stmt.iterable)


def peel_coerce(e: TpyExpr) -> TpyExpr:
    """The expression under any stack of TpyCoerce wrappers."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e


def const_place(expr: TpyExpr, type_of: "ExprTypeOf",
                const_node: Callable[[TpyExpr, bool], bool],
                const_referent: Callable[[TpyExpr], bool] = lambda _h: False) -> bool:
    """Whether the place `expr` is const, walked toward its root. A step
    taken through an indirection (a `Ptr` dereference, a borrowing-view
    element) ends the walk with the handle's referent access
    (`handle_referent_const`): readonly protects what holds a handle, not
    what it points at. Every other step -- a field, an owning container's
    element -- is storage of what it is taken off, so the place is const
    when a node the walk reaches is: `const_node(node, stepped)` is the
    consumer's verdict for one node (`stepped` once it is reached through a
    step rather than being `expr` itself)."""
    stepped = False
    while True:
        expr = peel_coerce(expr)
        if const_node(expr, stepped):
            return True
        if not isinstance(expr, (TpyFieldAccess, TpySubscript)):
            return False
        referent = handle_referent_const(expr.obj, type_of, const_referent)
        if referent is not None:
            return referent
        expr, stepped = expr.obj, True


def handle_referent_const(handle: TpyExpr, type_of: "ExprTypeOf",
                          const_referent: Callable[[TpyExpr], bool]) -> bool | None:
    """Whether what the handle `handle` points at is const
    (`indirection_referent_readonly` of its type), None when it is no
    handle. `const_referent` is the consumer's verdict a handle's type
    cannot carry: a `*args` pack the body leaves unmutated is emitted over
    const elements (`varargs<const T>`) though its declared type is not."""
    referent = indirection_referent_readonly(type_of(handle))
    if referent is None:
        return None
    return referent or const_referent(peel_coerce(handle))


def receiver_const(recv: TpyExpr, type_of: "ExprTypeOf",
                   place_const: Callable[[TpyExpr], bool],
                   const_referent: Callable[[TpyExpr], bool] = lambda _h: False) -> bool:
    """Whether the object a member call runs on is const: a handle receiver
    is dereferenced by the call, so its referent's access; any other
    receiver is the place it names (`place_const`)."""
    referent = handle_referent_const(recv, type_of, const_referent)
    if referent is not None:
        return referent
    return place_const(recv)


def call_result_const(call: TpyMethodCall, type_of: "ExprTypeOf",
                      place_const: Callable[[TpyExpr], bool],
                      const_referent: Callable[[TpyExpr], bool] = lambda _h: False) -> bool:
    """Whether a method call's result is const: its callee's declared
    result access (sema typed the call off it), or, for a callee declaring
    its whole result as following the receiver, the receiver's
    (`receiver_const`) -- C++ picks the clone by it, whichever clone sema
    resolved. A borrowing VIEW so declared (`d.values()`) counts though
    readonly does not attach to a view (`following_components` drops it):
    its elements are the receiver's storage, and the C++ overload set hands
    a const receiver a const view."""
    fi = call.resolved_function_info
    if isinstance(type_of(call), ReadonlyType) or declared_result_readonly(fi):
        return True
    follows = result_follows_receiver_root(fi) or (
        fi is not None and declares_whole_result_following(fi.root)
        and is_borrowing_view_type(unwrap_ref_type(fi.return_type)))
    return follows and receiver_const(call.obj, type_of, place_const, const_referent)


def materializing_temp_source(a: TpyExpr, analyzer) -> bool:
    """Whether this expression MATERIALIZES a fresh object whose lifetime the
    enclosing statement bounds -- `is_temporary_expr` restricted to the shapes
    that can occupy a generator/coroutine factory's borrowed slot (a non-value
    ref / readonly-ref param, or the storage a borrowing-view param aliases).
    The frame borrows past the statement, so every such temporary must
    materialize as a named scope-local.

    A `str` / `bytes` LITERAL counts: its own render is a view over static
    storage, but the frame that receives it must still hold a named local, and
    a str-family argument is the one place where the OWNED form of the hoisted
    local differs from what the argument position renders. Scalar literals
    (int / float / bool) are omitted -- the only slots they reach are the
    primitive ones the frame rule excludes anyway."""
    if isinstance(a, CONTAINER_LITERAL_NODES):
        return True
    if isinstance(a, (TpyBinOp, TpyUnaryOp, TpyFString,
                      TpyStrLiteral, TpyBytesLiteral)):
        return True
    if isinstance(a, TpyCoerce):
        # A Ptr deref coercion is an lvalue; every other coercion produces
        # a temporary (is_temporary_expr's coerce arm, non-recursive).
        return not isinstance(a.actual_type, PtrType)
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return is_rvalue_source(analyzer, a)
    if isinstance(a, TpySubscript):
        ct = analyzer.get_expr_type(a.obj)
        ct = unwrap_readonly(ct) if ct is not None else None
        return isinstance(ct, NominalType) and ct.is_user_record
    return False


def is_iterator_protocol(t: 'TpyType | None') -> bool:
    """Whether `t` is the `typing.Iterator` protocol -- the declared return
    of a frame factory or combinator, or the type of a handle bound from
    one (a concrete frame included: it is a subtype of the protocol)."""
    if not isinstance(t, TpyType):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return (isinstance(t, NominalType) and t.is_protocol
            and t.qualified_name() == qnames.ITERATOR)


def frame_factory_callee(fi: 'FunctionInfo | None') -> bool:
    """Is this callee a generator factory -- a call that builds a frame
    holding its reference-typed arguments past the statement?

    The fact is the CALLEE's, so it must not depend on how the call was
    resolved. `is_generator` answers for a plain or generic callee; an
    @overload-ed one carries False on every per-signature fi while the IMPL is
    the generator, and there the DECLARED `typing.Iterator` return answers --
    sema forbids that return on a non-generator plain-TPy function, so nothing
    else can wear it. `@native` / `@cpp_template` callees are excluded: their
    `Iterator[T]` returns are C++ combinator objects (map / zip / filter /
    iter), not frames. Same rule and the same exclusions as
    `_genfac_like_call`, one level down (an fi, not a call), so the two cannot
    drift."""
    if fi is None:
        return False
    if getattr(fi, "is_generator", False):
        return True
    if (getattr(fi, "native_function", False)
            or getattr(fi, "cpp_template", None)
            or getattr(fi, "is_stub", False)):
        return False
    return is_iterator_protocol(getattr(fi, "return_type", None))


def iterator_source_callee(fi: 'FunctionInfo | None') -> bool:
    """Does this callee hand back an ITERATOR over its arguments -- a frame
    factory, or a lazy combinator (`zip`, `enumerate`, `reversed`, ...)?
    Both keep their reference-typed arguments alive for as long as the
    result is driven, so where the result is held by a frame, the arguments
    must be too. Same declared-return test as `frame_factory_callee`, without
    its native / template exclusion: a C++ combinator object retains exactly
    as a frame does."""
    if fi is None:
        return False
    if frame_factory_callee(fi):
        return True
    return is_iterator_protocol(getattr(fi, "return_type", None))


def frame_temp_arg_source(a: TpyExpr, ptype: 'TpyType | None',
                          analyzer) -> 'TpyExpr | None':
    """The SOURCE expression a TEMPORARY argument to a generator/coroutine
    factory materializes, and which must therefore be hoisted to a named local
    of the block that owns the handle -- or None when nothing is owed.

    THE INVARIANT: a frame never receives a temporary. Whatever a frame does
    with an argument -- keep a `T&`, keep a view whose VALUE is a borrow, copy
    it into an owned field -- it does it after the full expression that built
    the argument has ended, so every argument it is handed must already be a
    named local (or, for an inline `await`, a slot of the awaiter's own frame).
    That is one rule over every slot, and it needs no taxonomy of which C++
    shapes alias their source: the hoist is what makes the question moot.

    Three slots are excluded, none by type taxonomy: a PRIMITIVE param
    (register-sized, trivially copyable, nothing to borrow), a `Ptr[T]` param
    (a pointer VALUE the frame copies -- what it points at is the caller's
    problem, and naming the pointer pins nothing the pointee did not already
    outlive) and an `Own[T]` param (the frame takes ownership -- the temporary
    moves in, which is exactly what an unnamed rvalue is for). One SOURCE is
    excluded too: a `str` / `bytes` LITERAL at a view slot (`str`, `StrView`,
    `bytes`, `BytesView`) is no temporary at all -- the view is over static
    storage that outlives any frame, and hoisting it would only buy an owned
    copy. Everything else hoists: the hoisted local is then passed through the
    same per-param coercion the argument already went through, so an owning
    sink still moves, a borrowing sink still views, and a value sink still
    copies.

    Two callers ask: the lowering rows that BUILD the hoist
    (`frame_temp_arg_slot`, which adds the storage type on top), and the sema
    dangling-borrow warnings, which must not fire where the hoist has already
    pinned the storage. Both must read one answer -- a second copy of this
    shape rule is how a warning and a hoist drift into disagreeing about the
    same argument. `frame_temp_elem_plan` applies this same rule to the
    elements of a tuple-literal argument."""
    pt = unwrap_ref_type(ptype) if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    if unwrap_optional_own(unwrap_readonly(pt)) is not None:
        return None
    bare = unwrap_readonly(unwrap_send_sync(pt))
    if is_primitive_type(bare) or isinstance(bare, PtrType):
        return None
    src = peel_coerce(a)
    if (isinstance(src, (TpyStrLiteral, TpyBytesLiteral))
            and _view_slot(bare)):
        return None
    if not materializing_temp_source(src, analyzer):
        return None
    return src


def _view_slot(bare: TpyType) -> bool:
    """A slot a str / bytes argument binds as a VIEW (`std::string_view` /
    `::tpy::BytesView`), which a literal fills without a copy."""
    return (is_str_type(bare) or is_str_view_type(bare)
            or is_bytes_type(bare) or is_bytes_view_type(bare))


class FrameTempElem(NamedTuple):
    """One temporary ELEMENT of a tuple-literal argument that a
    frame-capturing call owes a named local: it sits at
    `holder.elements[path[-1]]` (`path` locates it inside the argument),
    materializes `source` and fills element slot `slot`; `owned` is the
    local's storage type once a lowering row has spelled it."""
    path: tuple[int, ...]
    holder: TpyTupleLiteral
    source: TpyExpr
    slot: TpyType
    owned: 'TpyType | None' = None


def frame_temp_elem_plan(a: TpyExpr, ptype: 'TpyType | None',
                         analyzer) -> tuple[FrameTempElem, ...]:
    """Every temporary element of tuple-literal argument `a` that a
    generator/coroutine factory keeps past the statement:
    `frame_temp_arg_source`'s rule applied to each element the tuple only
    borrows (`TupleType.element_borrows`) -- the frame holds the tuple
    itself by value, and an owned element owes nothing. A non-literal
    element at a nested tuple slot has no hoist render, so it owes none."""
    out: list[FrameTempElem] = []
    for leaf in tuple_literal_elems(a, ptype) or ():
        if not TupleType.element_borrows(_bare_slot(leaf.slot)):
            continue
        src = frame_temp_arg_source(leaf.elem, leaf.slot, analyzer)
        if src is not None:
            out.append(FrameTempElem(leaf.path, leaf.holder, src, leaf.slot))
    return tuple(out)


class TupleLiteralElem(NamedTuple):
    """One leaf element of a tuple literal filling a tuple slot: it sits at
    `holder.elements[path[-1]]` (`path` locates it inside the whole
    literal) and fills element slot `slot`, as written; `under_readonly`
    says an enclosing tuple slot is `readonly[...]`."""
    path: tuple[int, ...]
    holder: TpyTupleLiteral
    elem: TpyExpr
    slot: TpyType
    under_readonly: bool = False


def _bare_slot(t: TpyType) -> TpyType:
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))


def _is_readonly_slot(t: 'TpyType | None') -> bool:
    return (isinstance(t, TpyType)
            and isinstance(unwrap_ref_type(unwrap_send_sync(t)), ReadonlyType))


def tuple_literal_elems(expr: TpyExpr, slot: 'TpyType | None'
                        ) -> 'tuple[TupleLiteralElem, ...] | None':
    """The leaf elements of `expr` when it is a tuple literal filling a
    tuple slot of its arity -- a nested literal at a nested tuple slot is
    expanded in place -- or None for any other shape."""
    lit = peel_coerce(expr)
    if not isinstance(lit, TpyTupleLiteral) or not isinstance(slot, TpyType):
        return None
    bare = _bare_slot(slot)
    if (not isinstance(bare, TupleType)
            or len(bare.element_types) != len(lit.elements)):
        return None
    out: list[TupleLiteralElem] = []
    _collect_tuple_literal_elems(lit, bare, (), _is_readonly_slot(slot), out)
    return tuple(out)


def _collect_tuple_literal_elems(lit: TpyTupleLiteral, bare: TupleType,
                                 path: tuple[int, ...], under_readonly: bool,
                                 out: 'list[TupleLiteralElem]') -> None:
    for i, (elem, et) in enumerate(zip(lit.elements, bare.element_types)):
        inner = peel_coerce(elem)
        et_bare = _bare_slot(et)
        if (isinstance(inner, TpyTupleLiteral) and isinstance(et_bare, TupleType)
                and len(et_bare.element_types) == len(inner.elements)):
            _collect_tuple_literal_elems(
                inner, et_bare, path + (i,),
                under_readonly or _is_readonly_slot(et), out)
        else:
            out.append(TupleLiteralElem(path + (i,), lit, elem, et,
                                        under_readonly))


ExprTypeOf = Callable[[TpyExpr], "TpyType | None"]


class LentOperand(NamedTuple):
    """One expression an operand lends at its slot (`lent_operands`)."""
    expr: TpyExpr
    # The slot `expr` fills: the parameter, or the tuple element slot.
    slot: 'TpyType | None'
    # A write through the callee's view of `expr` reaches `expr`'s storage.
    grants_write: bool
    # Where `expr` sits inside a tuple-literal operand; () for the operand.
    path: tuple[int, ...] = ()
    # Not a lent operand: the storage the operand's tuple temporary holds
    # by value for `expr`. A result borrowing the operand can point into
    # it, but `expr`'s own storage is not lent (no root, no write).
    temp_backed: bool = False


def _operand_is_pointer_value(expr: TpyExpr,
                              expr_type: 'ExprTypeOf | None') -> bool:
    # A `Ptr[T]` operand is a pointer VALUE copied out of its storage:
    # writing through the copy reaches the pointee, never that storage.
    return (expr_type is not None
            and isinstance(expr_type(expr), PtrType))


def lent_operands(expr: TpyExpr, slot: 'TpyType | None', *,
                  expr_type: 'ExprTypeOf | None',
                  temp_backing: bool = False
                  ) -> 'list[LentOperand]':
    """What `expr` lends as the value of slot `slot`: the expression itself,
    or -- a tuple literal at a tuple slot -- each element whose slot borrows,
    exactly as that element would lend at its slot as a scalar; an owned
    element (a primitive, a `str`, an `Own[T]`) lends nothing.

    `temp_backing` also reports each element the tuple temporary holds as
    storage a borrow can point into (`TupleType.element_holds_storage`),
    flagged `temp_backed`: the question "can a result borrowing `expr`
    point into a temporary" needs them, the question "which caller storage
    is lent" must not see them.

    Write access is decided here, once: a readonly slot grants none, and an
    element grants it only through a bare-pointer (reference) element slot
    -- a union or view element's borrow form is const. A `Ptr[T]` operand
    grants none either; `expr_type=None` leaves that unknown and gives
    the wider answer, so a caller reading `grants_write` passes the getter
    (required, so no caller omits it by accident)."""
    leaves = tuple_literal_elems(expr, slot)
    if leaves is None:
        return [LentOperand(
            expr, slot, not _is_readonly_slot(slot)
            and not _operand_is_pointer_value(expr, expr_type))]
    out: list[LentOperand] = []
    for leaf in leaves:
        if TupleType.element_lends(leaf.slot):
            out.append(LentOperand(
                leaf.elem, leaf.slot,
                not leaf.under_readonly and not _is_readonly_slot(leaf.slot)
                and TupleType._element_is_pointer_repr(leaf.slot)
                and not _operand_is_pointer_value(leaf.elem, expr_type),
                leaf.path))
        # A nested tuple value can do both: lend its borrowing elements and
        # have its owned ones copied into the temporary.
        if temp_backing and TupleType.element_holds_storage(leaf.slot):
            out.append(LentOperand(leaf.elem, leaf.slot, False, leaf.path,
                                   True))
    return out


def slot_binds_open_param(slot: 'TpyType | None') -> bool:
    """Whether a DECLARED slot is an open type param, whose C++ form
    (`param_val_or_ref_t<T>`, `val_or_ptr_t<T>`) is a mutable borrow at
    every reference instantiation whatever the body does with it.
    `readonly[T]` renders the const form."""
    return (isinstance(slot, TpyType)
            and isinstance(unwrap_ref_type(unwrap_send_sync(slot)),
                           TypeParamRef))


def lent_operand_binds_open_param(operand: LentOperand,
                                  arg: TpyExpr,
                                  declared: 'TpyType | None') -> bool:
    """Whether `operand` (lent by `arg`) fills a position that the callee's
    DECLARED parameter type `declared` spells as an open type param -- the
    parameter itself, or an element slot at or enclosing `operand.path`
    (a `T` element filled by a nested literal holds the whole literal).
    Asked of the declaration: a call site's substituted slot no longer says
    it was generic."""
    if not operand.path:
        return slot_binds_open_param(declared)
    for leaf in tuple_literal_elems(arg, declared) or ():
        if (operand.path[:len(leaf.path)] == leaf.path
                and not leaf.under_readonly):
            return slot_binds_open_param(leaf.slot)
    return False


def peel_value_wrappers(expr: TpyExpr) -> TpyExpr:
    """Unwrap coercions and walrus wrappers to the value expression a
    return/yield actually hands out -- `return (t := items[0])` hands out
    the subscript read, so provenance checks must see through the binding.
    """
    while True:
        if isinstance(expr, TpyCoerce):
            expr = expr.expr
        elif isinstance(expr, TpyNamedExpr):
            expr = expr.value
        else:
            return expr


def _borrow_link(expr: TpyExpr) -> 'tuple[FunctionInfo | None, TpyExpr | None] | None':
    """The (callee, receiver) of `expr` as a call link, or None if it is not
    a call. Operator dispatch is a method call in disguise, so a dunder is a
    link with its operand as receiver -- the same reading the borrow
    registration in sema/statements.py does.
    """
    if isinstance(expr, TpyMethodCall):
        return (expr.resolved_function_info, expr.obj)
    if isinstance(expr, TpyCall):
        return (expr.resolved_function_info, None)
    if isinstance(expr, TpySubscript) and expr.getitem_function_info is not None:
        return (expr.getitem_function_info, expr.obj)
    if isinstance(expr, TpyBinOp) and expr.resolved_binop is not None:
        rb = expr.resolved_binop
        return (rb.method, expr.right if rb.is_reverse else expr.left)
    if isinstance(expr, TpyUnaryOp) and expr.resolved_unaryop is not None:
        return (expr.resolved_unaryop.method, expr.operand)
    return None


def returns_borrow(analyzer: 'ValueCategoryAnalyzer', expr: TpyExpr) -> bool:
    """Whether `expr` hands back a borrow of storage that outlives it.

    The borrowed sources `is_lvalue` says no to: a call is an rvalue by the
    address-of test, yet the reference it returns can alias the callee's
    storage, so filling an owning slot from it copies exactly as a name or a
    field does. `is_rvalue_source` is the right reader of the call and
    `call_returns_cpp_ref` is not: a builtin factory (`list(xs)`,
    `bytearray(b)`) declares `-> list[T]` too, and only the
    constructor/factory layers on top of that predicate tell the fresh value
    apart from the borrow.

    One rule at every owning slot, with no lifetime reasoning: a
    borrow-returning call IS a borrowed source. A receiver rooted in a
    temporary does not exempt it -- what the callee hands back can reach
    past its receiver, so the temporary bounds nothing. `Wrapper(take_ptr(
    h.o)).get()` returns `h.o` through a pointer field, and the copy the
    owning slot makes is the divergence the diagnostic names. A callee
    returning a bare type param is such a call too -- the generic body must
    reach the same verdict its monomorphic twin does.
    """
    inner = peel_value_wrappers(expr)
    # An if-expr emits its arms inline, so the slot is filled from whichever
    # arm runs: borrowed if either can be.
    if isinstance(inner, TpyIfExpr):
        return (returns_borrow(analyzer, inner.then_expr)
                or returns_borrow(analyzer, inner.else_expr))
    if isinstance(inner, TpyAwait):
        return inner.await_result_is_borrow
    # A borrow-declared call sema stamped a fresh value is a borrowed source
    # exactly when holding it copies an object the program still reaches.
    if isinstance(inner, TpyCallLike) and inner.result_form.is_fresh:
        return inner.copy_observable
    # The pointer borrow form is a C++ prvalue `is_rvalue_source` calls
    # fresh, yet what it points at is the callee's lent storage.
    if lends_pointer_form_result(analyzer, inner):
        return True
    link = _borrow_link(inner)
    if link is None:
        return False
    # A callee declaring a bare type param as its return hands back the same
    # reference a concrete reference-typed return does at every reference-type
    # instantiation, and a value at the rest. `is_rvalue_source` answers the
    # C++ RENDER question and reads the unresolved parameter as a value, which
    # is the twin's verdict only for the value-typed half -- so at an owning
    # slot the source counts as borrowed. What the slot then REPORTS is
    # decided per instantiation (sema/own_copy.py), so the value-typed half
    # costs no diagnostic.
    if _returns_open_type_param(link[0]):
        return True
    return not is_rvalue_source(analyzer, inner)


def _returns_open_type_param(fi: 'FunctionInfo | None') -> bool:
    """The callee's declared return is a bare, still-open type parameter, and
    the callee is one whose declaration says what its return convention is.

    A callable VALUE is excluded: its signature is the `Fn` type's, not a
    declaration anyone checked, and the body that runs may build a fresh
    value -- so `f(x)` at an `Fn[[T], K]` slot is an rvalue exactly as its
    monomorphic twin `Fn[[int32], Cell]` is, and reading K as a borrow would
    warn on the generic where the twin is silent.
    """
    if fi is None or fi.is_constructor or fi.is_callable_value:
        return False
    return is_open_type_param_return(fi.return_type)
