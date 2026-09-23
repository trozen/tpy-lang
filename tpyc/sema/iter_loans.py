"""The iteration loan: what a loop or a comprehension borrows while it runs.

An iteration hands out references into the storage it walks, so a mutation of
that storage while the iteration is live may invalidate them. Every iterating
construct -- the sync for-each, `async for`, a list / set / dict comprehension
-- files its loan through `register_iteration_loans`, so the invalidation
question has one answer whatever spells the loop.

It also owns the call-operand questions a borrow off a call result asks
(`is_dangling_temporary_arg`, `temp_arg_kept_alive`, `hold_whole`), shared
with the call-result binding (`_register_call_result_borrow` in
`statements.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, NamedTuple

from ..parse import (
    TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess, TpyFString, TpyBinOp,
    TpyGeneratorExpression, TpyIfExpr, TpyMethodCall, TpyName, TpySubscript,
    is_property_getter_read,
)
from ..type_def_registry import is_borrowing_view_type
from ..typesys import (
    FunctionInfo, TpyType, TupleType, TypeParamRef, held_whole_borrow_sources,
    is_protocol_type, recorded_return_borrow_sources, type_param_names,
    unwrap_readonly, unwrap_ref_type,
)
from ..value_category import (
    frame_factory_callee, frame_temp_arg_source, iterator_source_callee,
)
from .context import (
    BorrowKind, ITER_BORROWER, LoanInfo, _borrow_storage_roots,
    _storage_root, call_borrow_operands, element_index_key,
    iter_borrow_storage,
)
from .scope_tracker import lend_roots

if TYPE_CHECKING:
    from .context import BorrowTracker, SemanticContext


def _yield_elem_sources(fi: FunctionInfo,
                        srcs_by_idx: dict[int, list[str]]) -> list[list[str]] | None:
    """Per tuple element of an iterator-returning callee's yield, the storage
    roots it lends from -- read off the GENERIC signature: an element spelled
    by a type param comes from the parameter(s) whose type mentions that
    param (`zip`: `Iterable[T1], Iterable[T2] -> Iterator[tuple[T1, T2]]`);
    a value element spelled without one (`enumerate`'s index) comes from
    nothing, and a reference element spelled without one keeps the
    whole-variable attribution. None when the yield is not a tuple or names
    no source at all, so the caller keeps the whole-variable attribution."""
    root = fi.root
    ret = unwrap_ref_type(unwrap_readonly(root.return_type))
    args = getattr(ret, "type_args", None)
    if not args or not isinstance(args[0], TupleType):
        return None
    every = [src for srcs in srcs_by_idx.values() for src in srcs]
    out: list[list[str]] = []
    named = False
    for et in args[0].element_types:
        names = type_param_names(et)
        srcs: list[str] = []
        if names:
            for idx, p in enumerate(root.params):
                if idx in srcs_by_idx and names & type_param_names(p.type):
                    srcs.extend(srcs_by_idx[idx])
        if names and not srcs and not et.is_value_type():
            # A reference element whose type param names no retained
            # argument: the whole-variable attribution, as if unnamed.
            srcs = every
        elif not names and not et.is_value_type():
            srcs = every
        named = named or bool(srcs) and srcs is not every
        out.append(srcs)
    return out if named else None


def is_dangling_temporary_arg(expr: TpyExpr) -> bool:
    """Check if an expression is a temporary whose storage won't survive.

    Function calls and binary ops produce temporaries destroyed at
    end-of-statement. Literals (string, int, array, etc.) are either
    static or materialized by codegen into named locals. Names and field
    accesses have addressable storage.
    """
    if isinstance(expr, TpyCoerce):
        return is_dangling_temporary_arg(expr.expr)
    # A view/pointer constructor borrows from its argument, so its temporariness
    # follows the arg: StrView("lit") / Ptr(name) wrap stable storage and do not
    # dangle, while StrView(make()) does. Mirrors is_dangling_return's
    # view-constructor recursion.
    if isinstance(expr, TpyCall) and expr.call_type is not None:
        if is_borrowing_view_type(expr.call_type) or expr.call_type.is_pointer():
            return bool(expr.args) and is_dangling_temporary_arg(expr.args[0])
    if isinstance(expr, (TpyCall, TpyMethodCall, TpyBinOp, TpyFString)):
        return True
    if isinstance(expr, TpyIfExpr):
        return (is_dangling_temporary_arg(expr.then_expr)
                or is_dangling_temporary_arg(expr.else_expr))
    return False


def temp_arg_kept_alive(fi: FunctionInfo, idx: int, arg: TpyExpr,
                        ctx: SemanticContext) -> bool:
    """Whether a temporary argument outlives the statement although the
    callee's result borrows it, so the dangle warnings below must stay
    silent: the compiler hoists a frame factory's argument into a named
    local, and a body-less lazy combinator (`zip`, `enumerate`, ...) OWNS a
    temporary -- its rvalue flavor moves the argument in.

    The hoist asks the lowering row's own shape predicate rather than a
    second copy of it: a warning that disagreed with the hoist would either
    fire on code the compiler already made safe, or go quiet on a shape it
    never hoisted."""
    if idx < 0 or idx >= len(fi.params):
        return False
    if not frame_factory_callee(fi):
        return iterator_source_callee(fi)
    return frame_temp_arg_source(arg, fi.params[idx].type, ctx) is not None


def hold_whole(bt: 'BorrowTracker', borrower: str, roots: Iterable[str]) -> None:
    """File the loan of a result that keeps these roots only as whole
    objects: growing them leaves it valid, moving them away does not. It
    merges, so a capture that is also the source stays iterated."""
    for root in roots:
        bt.add_borrow(root, borrower, BorrowKind.ALIAS, merge=True)


class IterationLoan(NamedTuple):
    """What `register_iteration_loans` found out about one iteration.

    `sources` is the storage a loop variable's element comes out of (what a
    write through the variable climbs to); `elem_sources`, when the iterator's
    yield names it, the same per tuple element. `unplaceable` marks a rooted
    lvalue chain too deep for a loan key: the iteration hands out references
    into storage nothing guards.
    """

    sources: list[str]
    elem_sources: list[list[str]] | None = None
    unplaceable: bool = False


class _BorrowedOperand(NamedTuple):
    """One operand whose storage a call iterable's result points into: its
    parameter index (-1 = the receiver), the expression, and the storage
    keys it lends (empty when it has none -- a temporary)."""
    idx: int
    arg: TpyExpr
    sources: list[str]


class IteratedStorage(NamedTuple):
    """The storage iterating an expression borrows, decided without filing
    anything: the ITER loans (key and place), the roots it holds only
    whole, and -- for a call iterable -- the callee and the operands the
    loans came from. `unplaceable` is the too-deep lvalue chain of
    `register_iteration_loans`."""
    loans: list[tuple[str, LoanInfo]]
    held_whole: list[str]
    callee: FunctionInfo | None = None
    operands: list[_BorrowedOperand] = []
    unplaceable: bool = False


def iterated_storage(ctx: SemanticContext, iterable: TpyExpr) -> IteratedStorage:
    """Which named storage iterating `iterable` borrows -- the one answer
    behind every iteration loan. `register_iteration_loans` files it for a
    loop or a comprehension; a generator expression's frame files the part
    of it that a name free in its body spells -- a capture, or a module
    global the body reads directly -- since inside the frame the source
    param and that name are two names for one container.

    An iteration borrows the storage it iterates: the container itself
    when the iterable names one, and the container an ELEMENT came out
    of when it is a subscript -- `LoanInfo.on_element` plus the
    element's `elem_index`, so a mutation of a sibling element is told
    apart from a mutation of the borrowed one.

    A loan is keyed only where `iter_borrow_storage` spells a key: a NAME,
    a one-hop element (subscript), a one-hop FIELD. A deeper chain has no
    loan and answers `unplaceable`, so the lowering refuses the loop
    rather than iterate storage nothing guards. The flag is the key
    question itself, so it is decided once, here, where the key is computed.

    The flag is asked of the @property read too: a getter is keyed at one
    hop like the stored field it reads through, and unkeyable deeper, and
    the resumable route holds its iterator across suspensions, where a
    caller's mutation of the returned container can reach it. What the
    one-hop answer does NOT mean is that the loan sits on the iterated
    container -- the provenance arm keys it on the receiver ROOT (see
    BUGS.md#property-iter-loan-misses-getter-storage).
    """
    is_getter = is_property_getter_read(iterable)
    unplaceable = False
    if is_getter or isinstance(iterable, (TpyName, TpySubscript, TpyFieldAccess)):
        key = iter_borrow_storage(iterable)
        unplaceable = key is None
        if not is_getter:
            if key is None:
                return IteratedStorage([], [], unplaceable=True)
            # A subscript iterates an ELEMENT of the storage its key names;
            # every other lvalue spelling iterates the storage itself.
            on_element = isinstance(iterable, TpySubscript)
            loan = LoanInfo(BorrowKind.ITER, on_element,
                            element_index_key(iterable.index) if on_element else None)
            return IteratedStorage([(key, loan)], [])
    return _provenance_storage(ctx, iterable)._replace(unplaceable=unplaceable)


def _provenance_storage(ctx: SemanticContext,
                        iterable: TpyExpr) -> IteratedStorage:
    """`iterated_storage` when the iterable is a CALL.

    A call result borrows whatever the callee's `return_borrows_from`
    names, so the loan goes on those source containers: the call
    expression itself has no storage key a mutating receiver could ever
    be resolved to. A @property read IS such a call, which is why
    `iterated_storage` hands it here rather than keying a field path.

    The sync for-each reaches it with a borrowing call iterable
    (`for b in pick(rows):` warns on `rows.append`); the async route
    reaches it only for a call whose callee carries no borrow provenance
    (`async for v in Countdown(3)`), because a BORROW-returning call or
    property iterable is a located reject at lowering there
    (`call.ret_type.record_borrow` for a function call, `method.ret_type`
    for a method or a property getter). Sharing the arm is what keeps the
    row lifting those rejects from leaving the async route with no loan.
    """
    # One leg for both spellings: a `@property` read is a TpyMethodCall
    # from sema on, so the getter's own `return_borrows_from` (index -1 =
    # the receiver) keys the ITER borrow here rather than through a
    # field-access arm of its own.
    # A generator expression is the call creating its frame.
    operands = (call_borrow_operands(iterable) if isinstance(
        iterable, (TpyCall, TpyMethodCall, TpyGeneratorExpression))
        else None)
    if operands is None:
        return IteratedStorage([], [])
    # The iterable is a call whose return borrows from source arg(s): the
    # ITER borrow goes directly on those source containers so that
    # structural mutations during the loop generate conflict warnings.
    fi_iter, call_obj, call_args = operands
    iter_sources = recorded_return_borrow_sources(fi_iter)
    held = held_whole_borrow_sources(fi_iter)
    if not iter_sources:
        return IteratedStorage([], [])
    loans: list[tuple[str, LoanInfo]] = []
    held_whole: list[str] = []
    borrowed: list[_BorrowedOperand] = []
    for idx in iter_sources:
        if idx in held and 0 <= idx < len(call_args):
            held_whole.extend(_borrow_storage_roots(call_args[idx]))
            continue
        arg = None
        if idx == -1 and call_obj is not None:
            arg = call_obj
        elif idx >= 0 and idx < len(call_args):
            arg = call_args[idx]
        if arg is None:
            continue
        srcs = _borrow_storage_roots(arg)
        # An argument that is itself a lending call (a nested
        # combinator, `zip(filter(pos, ns), xs)`) or a
        # conditional has no storage key, but it lends: the
        # shared walker names every root, so the loop var's
        # element climbs to `ns` through the nesting. Only a
        # PROVEN root: an assumed one (a callee whose facts are
        # pending) would file a hard ITER loan -- and its
        # invalidation warning -- on a name the callee may never
        # lend, by declaration order.
        if not srcs:
            lent = [r for r in lend_roots(ctx, arg) if not r.assumed]
            srcs = [r.name for r in lent if not r.held_whole]
            held_whole.extend(r.name for r in lent if r.held_whole)
        borrowed.append(_BorrowedOperand(idx, arg, srcs))
        loans.extend((src, LoanInfo(BorrowKind.ITER)) for src in srcs)
    return IteratedStorage(loans, held_whole, fi_iter, borrowed)


def register_iteration_loans(
        ctx: SemanticContext, iterable: TpyExpr, iterable_type: TpyType,
        extra_loans: Iterable[tuple[str, LoanInfo]] = (),
        excluded_roots: Iterable[str] = ()) -> IterationLoan:
    """File the iteration's borrow of the storage it iterates
    (`iterated_storage`), plus `extra_loans` the caller knows the same
    iteration holds.

    One registration serves every iterating construct. `async for` borrows
    the same storage the sync loop does: `__aiter__` is user code and
    may hand back an iterator holding a pointer into the iterable, which
    the frame then keeps across every suspension. A comprehension walks a
    begin/end pair its own condition or element may reallocate under. So
    none of them may answer the invalidation question differently.

    Every loan MERGES with what the iterator holder already has on the key:
    an enclosing loop's loan on an element of the same storage
    (`for v in xss[0]:` around `[w for w in xss ...]`) must survive the
    inner iteration, not be replaced by the coarser whole-container loan.

    `excluded_roots` names storage the iterating code cannot reach by
    name: a comprehension whose target shadows its source's root.
    """
    bt = ctx.func.borrow_tracker
    storage = iterated_storage(ctx, iterable)
    excluded = set(excluded_roots)
    hold_whole(bt, ITER_BORROWER,
               [r for r in storage.held_whole if r not in excluded])
    for key, loan in [*storage.loans, *extra_loans]:
        if _storage_root(key) in excluded:
            continue
        bt.add_borrow(key, ITER_BORROWER, loan.kind, on_element=loan.on_element,
                      elem_index=loan.elem_index, merge=True)
    if storage.callee is None:
        if isinstance(iterable, TpyName) and not storage.unplaceable:
            # A protocol- or TypeParamRef-typed iterable is advanced
            # through a method that mutates iterator state, so the
            # generated param must be `T&`, not `const T&`. A concrete
            # type gets the same verdict from the call itself.
            inner = unwrap_ref_type(unwrap_readonly(iterable_type))
            if isinstance(inner, TypeParamRef) or is_protocol_type(inner):
                ctx.mark_param_mutated(iterable.name)
        return IterationLoan([key for key, _ in storage.loans],
                             unplaceable=storage.unplaceable)
    fi_iter = storage.callee
    for op in storage.operands:
        if op.sources or not is_dangling_temporary_arg(op.arg):
            continue
        # Call results returning non-value types are materialized into
        # named variables by codegen (for by-reference passing), so they
        # survive the for-loop. A borrowing-VIEW slot of a frame-capturing
        # callee is materialized too, by the view-backing hoist. Only warn
        # for what neither pins.
        is_materialized = temp_arg_kept_alive(fi_iter, op.idx, op.arg, ctx)
        if isinstance(op.arg, (TpyCall, TpyMethodCall)):
            arg_fi = op.arg.resolved_function_info
            if arg_fi is not None and not arg_fi.return_type.is_value_type():
                is_materialized = True
        if not is_materialized:
            if op.idx == -1:
                detail = "temporary receiver object"
            else:
                detail = f"temporary argument '{fi_iter.params[op.idx].name}'"
            ctx.warning(
                f"Iterator borrows from {detail}; "
                f"the temporary is destroyed before iteration begins",
                iterable,
            )
    iter_srcs = [key for key, _ in storage.loans]
    if not iter_srcs:
        return IterationLoan([], unplaceable=storage.unplaceable)
    srcs_by_idx = {op.idx: op.sources for op in storage.operands}
    return IterationLoan(iter_srcs, _yield_elem_sources(fi_iter, srcs_by_idx),
                         unplaceable=storage.unplaceable)
