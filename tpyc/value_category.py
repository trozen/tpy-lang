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
    OptionalType, UnionType,
    VoidType, is_open_type_param_return, is_primitive_type, is_protocol_type,
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
    """Classify an async def's declared return into its Poll-payload form.

    Kept beside `call_returns_cpp_ref` so the async and sync return
    conventions can't drift: the same type kinds that make a sync return
    `T&` make the async payload `T*`. Unions and recursive-union wrappers
    stay storage form (their borrow-consumer side is not built -- see the
    pointer-variant Union entry in BUGS.md); protocol returns are
    value-shaped concrete structs.
    """
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret_type)))
          if ret_type is not None else None)
    if rt is None or isinstance(rt, (VoidType, OwnType)):
        return AsyncReturnForm.STORAGE
    if isinstance(rt, TypeParamRef):
        return AsyncReturnForm.TRAIT
    if isinstance(rt, OptionalType):
        return (AsyncReturnForm.BORROW if rt.uses_pointer_repr()
                else AsyncReturnForm.STORAGE)
    if (isinstance(rt, UnionType) or rt.is_value_type()
            or is_protocol_type(rt) or rt.needs_wrapper()):
        return AsyncReturnForm.STORAGE
    return AsyncReturnForm.BORROW


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
    if fi is None:
        return False
    # Free native functions default to value semantics (unknown C++ return
    # convention). Native METHODS are excluded: they honor the `-> V` /
    # `Own[V]` contract via the shape check below, like user methods -- so the
    # `not is_method` gate, not `is_native_import` alone, is what keeps
    # native-method aliasing.
    if fi.is_native_import and not fi.is_method:
        return False
    # Record constructors return rvalue temporaries, never C++ T&.
    if fi.is_constructor:
        return False
    rt = unwrap_ref_type(fi.return_type)
    return (rt is not None
            and not rt.is_value_type()
            and not isinstance(rt, (TypeParamRef, OwnType, OptionalType, UnionType))
            and not is_protocol_type(rt))


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
    # Field access: rvalue iff the object is rvalue (member of temporary)
    if isinstance(expr, TpyFieldAccess):
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
    # Coercions: depends on inner expr
    if isinstance(expr, TpyCoerce):
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


def borrowing_frame_callee(fi: 'FunctionInfo | None') -> bool:
    """Does this callee's FRAME borrow an argument slot past the statement,
    so a prvalue bound only for the full expression dangles on resume?

    The simple-generator peephole does: its lambda captures a generic `T`
    parameter by reference, a capture form decided on the OPEN `T`
    (`BUGS.md#simple-generator-captures-open-t-param-by-reference`). A
    RESUMABLE generator frame and a coroutine frame do not -- both copy the
    argument into a `val_or_ref_t<T>` member in the frame constructor,
    inside the full expression (verified for the coroutine by ASAN with the
    temp elided). A BORROWING-VIEW slot is the exception to that copy: the
    copied value IS a borrow, so `frame_temp_arg_source` answers for it
    on top of this one.

    Which of the two a generator lowers to is `is_simple_generator`, a
    predicate over the callee's `TpyFunction` that this seam cannot reach:
    a call site holds a `FunctionInfo`, and the peephole verdict is
    finalized during codegen (`_prescan_for_src_embedding` may force a
    simple generator resumable). So every generator factory is treated as
    borrowing, which costs a resumable one a temp it does not need --
    TODO.md carries that residue and the two ways to remove it.

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
