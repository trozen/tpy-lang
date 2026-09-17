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
from typing import Any, Protocol

from .typesys import (
    FunctionInfo, NominalType, PtrType, TpyType, TypeParamRef, OwnType,
    OptionalType, ResultPosition, ResultRepresentation, classify_result_representation,
    is_open_type_param_return, is_primitive_type, is_protocol_type,
    property_getter_returns_storage_ref, returns_cpp_reference_shape,
    unwrap_optional_own, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral, TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression, TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall,
    TpySubscript, TpyCall, TpyName, TpyFieldAccess, TpyIfExpr, TpyAwait,
    TpyBytesLiteral, TpyFString, TpyNamedExpr,
)
from .type_def_registry import is_bool_type


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


def property_access_returns_cpp_ref(analyzer: ValueCategoryAnalyzer,
                                    expr: TpyFieldAccess) -> bool:
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
    getter = expr.property_getter_call
    if getter is None:
        return False
    fi = getter.resolved_function_info
    if fi is None:
        return False
    if property_getter_returns_storage_ref(fi.return_type):
        return True
    if is_open_type_param_return(fi.return_type):
        return return_type_is_cpp_ref(analyzer.get_expr_type(expr))
    return call_returns_cpp_ref(analyzer, fi)


def is_rvalue_source(analyzer: ValueCategoryAnalyzer, expr: TpyExpr) -> bool:
    """Check if an expression produces an rvalue (a fresh value / temporary).

    Rvalues: constructor calls, Own[T] returns, literals, binop/unop results,
    field access on rvalue objects (member of temporary).
    Lvalues (borrow-aliases): variable names, field access on lvalues,
    subscript, function/method returning T&, ternary with two lvalue arms.
    """
    # Names are lvalues (either pointer-locals, params, or globals)
    if isinstance(expr, TpyName):
        return False
    # Field access: rvalue iff the object is rvalue (member of temporary).
    # A property / __getattr__ access keeps the field-access node kind but
    # RENDERS as a method call, so the accessor's return CONVENTION is a
    # SECOND way for it to be one -- the node kind alone reads as an inert
    # member read. Both are asked: an accessor handing back a C++ reference
    # still borrows from its RECEIVER, so off a temporary the read names
    # storage that dies at the end of the statement exactly as a plain
    # member read off one does. Asking only the convention would bind a
    # reference into the temporary (`std::vector<int32_t>& p =
    # mk().items();`); the METHOD spelling of the same read is a conceded
    # warn-and-emit tier (`BUGS.md#readonly-borrow-of-temporary-receiver`)
    # and is not this arm.
    if isinstance(expr, TpyFieldAccess):
        if expr.property_getter_call is not None:
            return (not property_access_returns_cpp_ref(analyzer, expr)
                    or is_rvalue_source(analyzer, expr.obj))
        # Same pair for the `__getattr__` face. The receiver disjunct is inert
        # today -- sema admits only a value type, `Any` or `Own[T]` as a
        # `__getattr__` return, so the convention never answers "reference"
        # here -- and is kept so the two accessor arms cannot drift if it
        # widens.
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
    # Ternary: lvalue iff both arms are lvalues (C++ ternary with two lvalue
    # arms is itself an lvalue). Uses OR semantics: rvalue if either arm is
    # rvalue, since _gen_if_expr emits arms inline with no temp materialization.
    if isinstance(expr, TpyIfExpr):
        result_type = analyzer.get_expr_type(expr)
        if result_type and not result_type.is_value_type():
            return (is_rvalue_source(analyzer, expr.then_expr)
                    or is_rvalue_source(analyzer, expr.else_expr))
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
    if isinstance(expr, TpyFieldAccess) and expr.property_getter_call is not None:
        getter = expr.property_getter_call
        return (getter.resolved_function_info, getter.obj)
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
