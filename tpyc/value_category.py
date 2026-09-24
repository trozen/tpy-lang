"""Shared expression value-category predicates (borrow-alias vs rvalue).

Both sema (to classify a local's binding as owned-movable vs borrow-alias)
and codegen (to render a local as an owned value vs a `T&` reference) must
answer the same question: "does this initializer produce a fresh rvalue, or
does it alias existing storage?" Keeping the answer in one place avoids the
divergence that lets sema mark a `C&` borrow-alias as movable -- which then
moves out of the alias and corrupts the source.

The `analyzer` argument is duck-typed: it only needs `get_expr_type(expr)`
and `registry`. Both the sema `AnalyzerContext` and the codegen
`SemanticAnalyzer` satisfy this.
"""

from enum import Enum, auto
from typing import Any, NamedTuple, Protocol

from .typesys import (
    FunctionInfo, NominalType, PtrType, TpyType, TypeParamRef, OwnType,
    OptionalType, ResultPosition, ResultRepresentation, classify_result_representation,
    is_open_type_param_return, is_primitive_type, is_protocol_type,
    property_getter_returns_storage_ref, returns_cpp_reference_shape,
    unwrap_optional_own, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .parse import (
    TpyExpr, TpyForEach,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral, TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression, TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall,
    TpySubscript, TpyCall, TpyName, TpyFieldAccess, TpyIfExpr, TpyAwait,
    TpyBytesLiteral, TpyFString, TpyNamedExpr,
    is_property_getter_read,
)
from .type_def_registry import is_bool_type, is_borrowing_view_type


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

    Free `@native` functions still take value semantics -- the same `-> V`
    reference-return asymmetry exists for them but is not yet closed (see
    BUGS.md). Native record METHODS, by contrast, honor the contract and
    fall through to the shape check (so dict.setdefault / items aliasing
    holds).
    """
    return classify_result_representation(
        fi.return_type if fi is not None else None,
        position=ResultPosition.SYNC_CALL, fi=fi,
    ) is ResultRepresentation.CPP_REFERENCE


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
    rt = getattr(fi, "return_type", None)
    if not isinstance(rt, TpyType):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.qualified_name() == "typing.Iterator")


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
    rt = getattr(fi, "return_type", None)
    if not isinstance(rt, TpyType):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.qualified_name() == "typing.Iterator")


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
    moves in, which is exactly what an unnamed rvalue is for). Everything else
    hoists, literals included: the hoisted local is then passed through the same per-param
    coercion the argument already went through, so an owning sink still moves,
    a borrowing sink still views, and a value sink still copies.

    Two callers ask: the lowering rows that BUILD the hoist
    (`frame_temp_arg_slot`, which adds the storage type on top), and the sema
    dangling-borrow warnings, which must not fire where the hoist has already
    pinned the storage. Both must read one answer -- a second copy of this
    shape rule is how a warning and a hoist drift into disagreeing about the
    same argument."""
    pt = unwrap_ref_type(ptype) if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    if unwrap_optional_own(unwrap_readonly(pt)) is not None:
        return None
    bare = unwrap_readonly(unwrap_send_sync(pt))
    if is_primitive_type(bare) or isinstance(bare, PtrType):
        return None
    src = peel_coerce(a)
    if not materializing_temp_source(src, analyzer):
        return None
    return src


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
