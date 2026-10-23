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

from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Iterable, NamedTuple, Union

from tpyc import modules as builtin_modules
from .. import qnames

from ..parse import (
    ResultForm,
    TpyCall, TpyCallLike, TpyCoerce, TpyExpr, TpyFieldAccess, TpyFString, TpyBinOp,
    TpyFunction, TpyGeneratorExpression, TpyIfExpr, TpyLambda, TpyMethodCall,
    TpyName, TpySubscript, is_property_getter_read,
)
from ..parse.nodes import walk_expr_tree
from ..type_def_registry import (get_type_def, is_borrowing_view_type,
                                 iter_yields_owned_elements)
from ..typesys import (
    FunctionInfo, GenExprType, NominalType, PendingDictType, PendingListType,
    ReadonlyType, PendingSetType, TpyType, TupleType, TypeParamRef, make_dict,
    make_list, make_set, yield_always_borrows,
    is_callable_type, is_fn_type, is_protocol_type, type_param_names,
    unwrap_own, unwrap_readonly, unwrap_ref_type,
)
from ..value_category import (
    call_operands, frame_factory_callee, frame_temp_arg_source,
    is_rvalue_source, iterator_source_callee,
)
from .call_lends import (
    CalleeFact, MayLend, borrowing_ctor_arg, call_callee, call_lend_sources,
    callee_lend_fact,
)
from ..holders import Holder, IterHolder
from .context import BorrowKind, LoanInfo, element_index_key
from .storage_walk import (LoanKey, MarkRequest, StorageKey, call_loans,
                           loan_key, mark_roots)
from .receiver_calls import (
    check_implicit_readonly_receiver, implicit_call_mutates_receiver,
    check_receiver_call_loans, credit_implicit_receiver_call,
)
from .own_copy import contains_reference_type
from .scope_tracker import lend_places

if TYPE_CHECKING:
    from .context import BorrowTracker, SemanticContext
    from ..typesys import TypeRegistry


class IterElementSource(Enum):
    """What the elements an iteration of a value hands out live in -- the
    one classification the `for` statement (its loop variable's lifetime)
    and a borrow-declared callee's element-of parameter (whether its result
    can outlive the call) both read.

    `HANDLE`: the value IS an iterator (a `__next__` record, a generator
    frame, the `Iterator` protocol): what it hands out lives as long as the
    handle and what the handle borrows. `CONTAINER`: a native iterable whose
    iterator points into its storage. `FRESH_ITERATOR`: a user class whose
    `__iter__` builds a separate iterator, which may own what it hands out.
    `PROTOCOL`: the `Iterable` protocol, whose concrete type decides.
    `OTHER`: anything else."""
    HANDLE = "handle"
    CONTAINER = "container"
    FRESH_ITERATOR = "fresh_iterator"
    PROTOCOL = "protocol"
    OTHER = "other"


def _declares_element_cursor(t: 'TpyType', registry: 'TypeRegistry') -> bool:
    """The type's stub declares a storage member iteration steps through one
    element at a time (`NativeMembers.cursor`): an element-owning container
    or a view over one, whose elements outlive any one step -- or a record
    whose readonly `__iter__` hands out a borrowing VIEW of its own storage
    (`ArrayList`'s `SpanIter`). A literal whose storage is not decided yet
    is the container the source wrote."""
    if isinstance(t, PendingListType):
        t = make_list(t.element_type)
    elif isinstance(t, PendingSetType):
        t = make_set(t.element_type)
    elif isinstance(t, PendingDictType):
        t = make_dict(t.key_type, t.value_type)
    if not isinstance(t, NominalType):
        return False
    td = get_type_def(t.qualified_name())
    members = td.native_members if td is not None else None
    if members is not None:
        return members.cursor
    record = registry.get_record_for_type(t)
    return record is not None and any(
        m.is_readonly and m.return_type is not None
        and is_borrowing_view_type(unwrap_readonly(m.return_type))
        for m in record.get_method_overloads("__iter__"))


def iter_element_source(iterable_type: 'TpyType | None',
                        registry: 'TypeRegistry') -> IterElementSource:
    """Classify what an iteration of a value of `iterable_type` hands out
    (see `IterElementSource`)."""
    if iterable_type is None:
        return IterElementSource.OTHER
    t = unwrap_readonly(unwrap_ref_type(iterable_type))
    if (builtin_modules.get_error_return_next_element_type(
            t, registry=registry) is not None
            or (is_protocol_type(t)
                and t.qualified_name() == qnames.ITERATOR)):
        return IterElementSource.HANDLE
    if is_protocol_type(t) and t.qualified_name() == qnames.ITERABLE:
        return IterElementSource.PROTOCOL
    if builtin_modules.get_iter_element_type(t, registry=registry) is not None:
        return (IterElementSource.CONTAINER
                if _declares_element_cursor(t, registry)
                else IterElementSource.FRESH_ITERATOR)
    return IterElementSource.OTHER


def iteration_runs_code(ctx: 'SemanticContext', iterable: TpyExpr | None,
                        iterable_type: 'TpyType | None') -> bool:
    """Whether a step of iterating `iterable` (of `iterable_type`) may run
    TPy code -- a generator's or a generator expression's body, a user
    `__iter__` / `__next__`, a callable a runtime combinator applies --
    which, like a call, may rebind what a `global` or `nonlocal` reaches. A
    native container, view or range, and a runtime combinator over those,
    step in runtime code only; a protocol, which any of them may stand
    for, runs code. Iteration sites record it through
    `record_iteration_pull`."""
    if iterable_type is None:
        return True
    t = unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type)))
    if isinstance(t, NominalType) and not t.is_protocol:
        # A record's own `__iter__` / `__next__` is TPy code even when it
        # hands out a view of its storage.
        rec = ctx.registry.get_record_for_type(t)
        return rec is not None and not rec.is_native
    if iter_element_source(t, ctx.registry) is IterElementSource.CONTAINER:
        return False
    call = iterable.expr if isinstance(iterable, TpyCoerce) else iterable
    if isinstance(call, (TpyCall, TpyMethodCall)):
        fi = call.resolved_function_info
        if fi is not None and fi.cpp_template is not None:
            operands = list(call.args) + list(call.kwargs.values())
            if isinstance(call, TpyMethodCall):
                operands.append(call.obj)
            return any(_operand_runs_code(ctx, a) for a in operands)
    return True


def record_iteration_pull(ctx: 'SemanticContext', iterable: TpyExpr,
                          iterable_type: 'TpyType | None') -> bool:
    """Decide whether pulling `iterable` runs TPy code
    (`iteration_runs_code`) and stamp it on the source
    (`TpyExpr.pull_runs_code`), the one place every call-recognizer reads
    it from; returns the answer."""
    iterable.pull_runs_code = iteration_runs_code(ctx, iterable, iterable_type)
    return iterable.pull_runs_code


def _operand_runs_code(ctx: 'SemanticContext', operand: TpyExpr) -> bool:
    """Whether a runtime combinator's operand makes its steps run TPy code:
    a callable it applies, or an iterable whose steps do."""
    t = ctx.get_expr_type(operand)
    if t is None:
        return True
    bare = unwrap_readonly(unwrap_own(unwrap_ref_type(t)))
    if is_callable_type(bare) or is_fn_type(bare):
        return True
    if builtin_modules.get_iterable_element_type(
            bare, registry=ctx.registry) is None:
        return False
    return iteration_runs_code(ctx, operand, t)


def _yield_elem_sources(fi: FunctionInfo,
                        srcs_by_idx: dict[int, list[StorageKey]]
                        ) -> list[list[StorageKey]] | None:
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
    out: list[list[StorageKey]] = []
    named = False
    for et in args[0].element_types:
        names = type_param_names(et)
        srcs = [src for idx in _elem_param_indices(root, et)
                if idx in srcs_by_idx for src in srcs_by_idx[idx]]
        if names and not srcs and not et.is_value_type():
            # A reference element whose type param names no retained
            # argument: the whole-variable attribution, as if unnamed.
            srcs = every
        elif not names and not et.is_value_type():
            srcs = every
        named = named or bool(srcs) and srcs is not every
        out.append(srcs)
    return out if named else None


def _elem_param_indices(root: FunctionInfo, et: TpyType) -> list[int]:
    """The parameters of a callee's generic signature whose type mentions a
    type param spelling yield element `et` -- where that element comes from."""
    names = type_param_names(et)
    if not names:
        return []
    return [idx for idx, p in enumerate(root.params)
            if names & type_param_names(p.type)]


# Per element of an iteration's yield, the named storage the element is a
# reference into: a flat root list for a whole element, or one entry per
# element of a tuple yield.
ElemProvenance = Union[list[StorageKey], tuple["ElemProvenance", ...]]


def _flat_provenance(prov: ElemProvenance) -> list[StorageKey]:
    if isinstance(prov, tuple):
        return [root for p in prov for root in _flat_provenance(p)]
    return prov


def _iterates_type_param(param_type: TpyType, et: TpyType) -> bool:
    """Whether a parameter is an iterable whose ELEMENT is the bare type
    param `et` (`Iterable[T1]` against `T1`), so the yield element is exactly
    what iterating the argument yields and its structure carries over."""
    p = unwrap_ref_type(unwrap_readonly(param_type))
    args = getattr(p, "type_args", None)
    return bool(args) and is_protocol_type(p) and args[0] == et


def iteration_elem_provenance(ctx: SemanticContext,
                              iterable: TpyExpr) -> ElemProvenance:
    """Per element of what iterating `iterable` yields, the named storage it
    points into. Strict about the callee's generic signature: a type-param
    element comes only from the operands whose parameter mentions that
    param (and the receiver), so `zip(ns, [C(5)])` blames nothing for its
    `C`; an element spelled without one keeps whole-call attribution."""
    it_type = ctx.get_expr_type(iterable)
    if it_type is not None and iter_yields_owned_elements(
            unwrap_ref_type(unwrap_readonly(unwrap_own(it_type)))):
        return []
    storage = iterated_storage(ctx, iterable)
    fi = storage.callee
    if fi is None:
        return [key.root_key for key, _ in storage.loans]
    by_idx = _sources_by_idx(storage.operands)
    every = [src for op in storage.operands for src in op.sources]
    root = fi.root
    ret = unwrap_ref_type(unwrap_readonly(root.return_type))
    args = getattr(ret, "type_args", None)
    if not args or not is_protocol_type(ret):
        # A view or container result (`d.items()`, a borrowed `list[C]`):
        # its signature does not spell the yield, so the whole call lends.
        return every

    def elem(et: TpyType) -> ElemProvenance:
        if isinstance(et, TupleType):
            return tuple(elem(e) for e in et.element_types)
        if not type_param_names(et):
            return every
        idxs = [i for i in _elem_param_indices(root, et) if i in by_idx]
        if -1 in by_idx:
            idxs.append(-1)
        if (isinstance(et, TypeParamRef) and len(idxs) == 1 and idxs[0] >= 0
                and _iterates_type_param(root.params[idxs[0]].type, et)):
            op = by_idx[idxs[0]]
            # Only a nested lending call has a yield structure of its own; a
            # named operand's roots are already `sources`, deep chains included.
            lend = callee_lend_fact(op.arg)
            if lend.kind is CalleeFact.UNRESOLVED or lend.fresh:
                return op.sources
            return iteration_elem_provenance(ctx, op.arg)
        return [src for i in idxs for src in by_idx[i].sources]

    return elem(args[0])


def iteration_lend_pending(ctx: SemanticContext, iterable: TpyExpr) -> bool:
    """Whether what `iterable` lends still waits on a callee whose body is
    not analyzed yet (a generator defined below its use), so a provenance
    verdict taken now would depend on definition order."""
    return any(r.assumed for r in lend_places(ctx, iterable))


def iteration_copies_lent_reference(ctx: SemanticContext, iterable: TpyExpr,
                                    elem_type: TpyType) -> bool:
    """Whether materializing `iterable` into owned storage of `elem_type`
    copies a reference-typed element out of named storage it lends from --
    the copy CPython's shallow `list(...)` would alias instead. Per tuple
    element: `zip(ns, cs)`'s `int` half lends from `ns` but copies no
    reference, its `C` half lends from `cs`."""
    def copies(t: TpyType, prov: ElemProvenance) -> bool:
        bare = unwrap_ref_type(unwrap_readonly(t))
        if (isinstance(prov, tuple) and isinstance(bare, TupleType)
                and len(prov) == len(bare.element_types)):
            return any(copies(e, p) for e, p in zip(bare.element_types, prov))
        return bool(_flat_provenance(prov)) and contains_reference_type(t)

    return copies(elem_type, iteration_elem_provenance(ctx, iterable))


def is_dangling_temporary_arg(expr: TpyExpr) -> bool:
    """Check if an expression is a temporary whose storage won't survive.

    Function calls and binary ops produce temporaries destroyed at
    end-of-statement. Literals (string, int, array, etc.) are either
    static or materialized by codegen into named locals. Names and field
    accesses have addressable storage.
    """
    if isinstance(expr, TpyCoerce):
        return is_dangling_temporary_arg(expr.expr)
    # A borrowing constructor lends its argument, so its temporariness
    # follows the arg: StrView("lit") / Ptr(name) wrap stable storage and do
    # not dangle, while StrView(make()) does.
    arg = borrowing_ctor_arg(expr)
    if arg is not None:
        return is_dangling_temporary_arg(arg)
    if isinstance(expr, TpyCallLike) and expr.result_form is ResultForm.BORROW:
        # Sema proved every argument it lends is existing storage.
        return False
    if isinstance(expr, (TpyCall, TpyMethodCall, TpyBinOp, TpyFString)):
        return True
    if isinstance(expr, TpyIfExpr):
        return (is_dangling_temporary_arg(expr.then_expr)
                or is_dangling_temporary_arg(expr.else_expr))
    return False


def temp_arg_kept_alive(fi: FunctionInfo, idx: int, arg: TpyExpr,
                        slot: TpyType | None, ctx: SemanticContext) -> bool:
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
    return frame_temp_arg_source(arg, slot, ctx) is not None


def temp_arg_spelling(fi: FunctionInfo, idx: int) -> str:
    """How a diagnostic names argument `idx` of a call of `fi`: by its
    parameter's name, or by position for a callable value, whose
    parameters have no source names."""
    if fi.root.is_callable_value:
        return f"argument {idx + 1}"
    return f"argument '{fi.params[idx].name}'"


def hold_whole(bt: 'BorrowTracker', borrower: Holder,
               roots: Iterable[StorageKey]) -> None:
    """File the loan of a result that keeps these roots only as whole
    objects: growing them leaves it valid, moving them away does not. It
    merges, so a capture that is also the source stays iterated."""
    for root in roots:
        bt.add_borrow(root, borrower, BorrowKind.ALIAS, merge=True)


class IterationLoan(NamedTuple):
    """What `register_iteration_loans` found out about one iteration.

    `sources` (the keys of `loans`) is the storage a loop variable's element
    comes out of (what a write through the variable climbs to);
    `elem_sources`, when the iterator's
    yield names it, the same per tuple element. `unplaceable` marks a rooted
    lvalue chain too deep for a loan key: the iteration hands out references
    into storage nothing guards.
    """

    elem_sources: list[list[StorageKey]] | None = None
    unplaceable: bool = False
    # The ITER loans filed, by storage key.
    loans: list[tuple[StorageKey, LoanInfo]] = []

    @property
    def sources(self) -> list[StorageKey]:
        return [key for key, _ in self.loans]


class _BorrowedOperand(NamedTuple):
    """One source (`call_lend_sources`) a call iterable's result points
    into: its parameter index (-1 = the receiver), the expression, the
    storage keys it lends (empty when it has none -- a temporary) and the
    slot it fills."""
    idx: int
    arg: TpyExpr
    sources: list[StorageKey]
    slot: TpyType | None = None


def _sources_by_idx(operands: 'list[_BorrowedOperand]'
                    ) -> dict[int, _BorrowedOperand]:
    """The operands per parameter index, the lending elements of one
    tuple-literal argument merged into one entry."""
    out: dict[int, _BorrowedOperand] = {}
    for op in operands:
        prev = out.get(op.idx)
        out[op.idx] = (op if prev is None else
                       prev._replace(sources=prev.sources + op.sources))
    return out


class IteratedStorage(NamedTuple):
    """The storage iterating an expression borrows, decided without filing
    anything: the ITER loans (key and place), the roots it holds only
    whole, and -- for a call iterable -- the callee and the operands the
    loans came from. `unplaceable` is the too-deep lvalue chain of
    `register_iteration_loans`."""
    loans: list[tuple[StorageKey, LoanInfo]]
    held_whole: list[StorageKey]
    callee: FunctionInfo | None = None
    operands: list[_BorrowedOperand] = []
    unplaceable: bool = False


def iteration_yields_copies(ctx: SemanticContext, iterable_type: TpyType,
                            depth: int = 0) -> bool:
    """Whether iterating this source hands the element out by value, into
    the per-step result the loop variable then reads, rather than lending
    an element the source holds: the iteration loan names what the
    iterator WALKS, not what the loop variable views. A generator /
    genexpr, a user type's `__iter__` returning one, and a user
    iterator's `__next__` answer by their element; a user type this cannot
    resolve counts as copying."""
    inner = unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type)))
    if isinstance(inner, GenExprType):
        return not yield_always_borrows(inner.element_type)
    if (is_protocol_type(inner) and isinstance(inner, NominalType)
            and inner.qualified_name() == "typing.Iterator" and inner.type_args
            and isinstance(inner.type_args[0], TpyType)):
        return not yield_always_borrows(inner.type_args[0])
    if not (isinstance(inner, NominalType) and inner.is_user_record):
        return False
    record = ctx.registry.get_record_for_type(inner)
    if record is None or depth > 4:
        return True
    iters = record.get_method_overloads("__iter__")
    if iters and unwrap_readonly(iters[0].return_type) != inner:
        return iteration_yields_copies(ctx, iters[0].return_type, depth + 1)
    nexts = record.get_method_overloads("__next__")
    return not (nexts and yield_always_borrows(nexts[0].return_type))


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

    A loan is keyed only where `loan_key` spells a key: a NAME,
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
        key = loan_key(iterable)
        unplaceable = not isinstance(key, LoanKey)
        if not is_getter:
            if not isinstance(key, LoanKey):
                return IteratedStorage([], [], unplaceable=True)
            # A subscript iterates an ELEMENT of the storage its key names,
            # whatever the container -- every subscript names its element
            # here; every other lvalue spelling iterates the storage itself.
            loan = LoanInfo(BorrowKind.ITER, key.index is not None,
                            element_index_key(key.index.index)
                            if key.index is not None else None)
            return IteratedStorage([(key.key, loan)], [])
    return _provenance_storage(ctx, iterable)._replace(unplaceable=unplaceable)


def _provenance_storage(ctx: SemanticContext,
                        iterable: TpyExpr) -> IteratedStorage:
    """`iterated_storage` when the iterable is a CALL.

    A call result borrows whatever the callee's `return_borrows_from`
    names, so the loan goes on those source containers (`call_loans` under
    MAY, through a nested lending call or a select argument too): the call
    expression itself has no storage key a mutating receiver could ever be
    resolved to. A @property read IS such a call, which is why
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
    if not isinstance(iterable, (TpyCall, TpyMethodCall,
                                 TpyGeneratorExpression)):
        return IteratedStorage([], [])
    fi_iter = call_callee(iterable)
    if fi_iter is None:
        return IteratedStorage([], [])
    loans: list[tuple[StorageKey, LoanInfo]] = []
    held_whole: list[StorageKey] = []
    borrowed: list[_BorrowedOperand] = []
    # Only a PROVEN root: an assumed one (a callee whose facts are pending)
    # would file a hard ITER loan -- and its invalidation warning -- on a
    # name the callee may never lend, by declaration order.
    found = [loan for loan in call_loans(iterable, MayLend(ctx))
             if not (loan.assumed or loan.temp_backed)]
    if not found:
        return IteratedStorage([], [])
    for loan in found:
        src = loan.source
        keys = [loan.key] if isinstance(loan.key, StorageKey) else []
        if loan.held_whole and src.idx >= 0:
            held_whole.extend(keys)
            continue
        # A receiver lent only by declaration is a FIELD loan, as a bound
        # result's is: the loop variable's writes climb it, and the storage
        # it names is not known to be the receiver's -- with no key it is no
        # evidence the result lives in it, temporary or not (the body
        # reached the result through a `Ptr` or a global).
        receiver_declared = loan.declared_only and src.idx == -1
        if receiver_declared and not keys:
            continue
        borrowed.append(_BorrowedOperand(src.idx, src.expr, keys, src.slot))
        kind = BorrowKind.FIELD if receiver_declared else BorrowKind.ITER
        loans.extend((key, LoanInfo(kind)) for key in keys)
    loans.extend(frame_capture_loans(iterable))
    return IteratedStorage(loans, held_whole, fi_iter, borrowed)


def frame_capture_loans(gx: TpyExpr) -> list[tuple[StorageKey, LoanInfo]]:
    """The places a generator expression's inner `for` clauses iterate inside
    its captures, held across pulls; an element index naming an outer
    clause's target names nothing at the creation, so it becomes unknown."""
    if (not isinstance(gx, TpyGeneratorExpression) or gx.frame_func is None
            or gx.frame_creation is None):
        return []
    func = gx.frame_func
    out: list[tuple[StorageKey, LoanInfo]] = []
    for key, info in func.genexpr_inner_loans:
        ei = info.elem_index
        if ei is not None and ei[0] == "name" and ei[1] not in func.capture_params:
            info = replace(info, elem_index=None)
        out.append((key, info))
    return out


def genexpr_iterated_roots(func: TpyFunction) -> set[str]:
    """The capture roots a generator expression's inner clauses iterate."""
    return {key.root for key, _ in func.genexpr_inner_loans}


def iter_receiver_callee(ctx: 'SemanticContext',
                         iterable_type: 'TpyType') -> FunctionInfo | None:
    """The user `__iter__` a `for` over `iterable_type` calls (MRO walk:
    an inherited one mutates the receiver too), or None."""
    record_info = ctx.registry.get_record_for_type(iterable_type)
    if record_info is None:
        return None
    return next((fi for fi in ctx.registry.get_method_overloads_with_parents(
                     record_info, "__iter__") if not fi.is_consuming), None)


def _loop_var_climbs(ctx: 'SemanticContext', iterable_expr: TpyExpr,
                     loop_var: bool) -> bool:
    """Whether the handle an `__iter__` lends is reached only through a loop
    variable whose writes climb to the iterable: the loop variable's
    `loop_var_iterable` sources, the iteration's loans
    (`register_iteration_loans`), which a write through it -- direct, by a
    callee it is passed to, or inside a nested def -- climbs. Only a `for`
    statement takes the exemption; a comprehension does not."""
    return loop_var and bool(iterated_storage(ctx, iterable_expr).loans)


def check_iter_receiver_loans(ctx: 'SemanticContext', iterable_expr: TpyExpr,
                              iterable_type: 'TpyType', *,
                              loop_var: bool = False) -> None:
    """The loan half of `_record_iter_receiver_mutation`, for a caller that
    files the iteration's own loan in between: a mutating `__iter__` runs
    before that loan exists, so it must not be reported as a mutation of the
    storage being iterated."""
    iter_fi = iter_receiver_callee(ctx, iterable_type)
    if iter_fi is not None and implicit_call_mutates_receiver(
            ctx, iterable_expr, iter_fi,
            handle_climbs=_loop_var_climbs(ctx, iterable_expr, loop_var)):
        check_receiver_call_loans(ctx, iterable_expr, iterable_type,
                                  "__iter__", iterable_expr, callee=iter_fi)


def _record_iter_receiver_mutation(
    ctx: 'SemanticContext', iterable_expr: TpyExpr, iterable_type: 'TpyType',
    *, check_loans: bool = True, loop_var: bool = False,
) -> None:
    """Record that iterating `iterable_expr` calls its `__iter__`.

    `for v in obj:` is an implicit `obj.__iter__()` call: a mutating one
    needs a non-const receiver and may invalidate loans into it.
    `check_loans=False` when the caller asked the loan question before
    filing the iteration's own loan. `loop_var` when a `for` statement's
    loop variable takes the elements (`_loop_var_climbs`).
    """
    iter_fi = iter_receiver_callee(ctx, iterable_type)
    if iter_fi is None:
        return
    # An rvalue iterable (a call result) has no durable root to credit, but
    # a readonly one still rejects a mutating `__iter__`. A loop variable's
    # climb cannot change that verdict: only an @auto_readonly pair lends a
    # handle, and a readonly receiver runs its const clone.
    if is_rvalue_source(ctx, iterable_expr):
        check_implicit_readonly_receiver(ctx, iterable_expr, iter_fi,
                                         "__iter__", iterable_expr)
        return
    credit_implicit_receiver_call(
        ctx, iterable_expr, iterable_type, iter_fi, "__iter__", iterable_expr,
        check_loans=check_loans,
        handle_climbs=_loop_var_climbs(ctx, iterable_expr, loop_var))


def register_iteration_loans(
        ctx: SemanticContext, holder: IterHolder, iterable: TpyExpr,
        iterable_type: TpyType,
        extra_loans: Iterable[tuple[StorageKey, LoanInfo]] = (),
        excluded_roots: Iterable[str] = ()) -> IterationLoan:
    """File the iteration's borrow of the storage it iterates
    (`iterated_storage`), plus `extra_loans` the caller knows the same
    iteration holds, under `holder`: the running iterator of this one loop
    or comprehension clause.

    One registration serves every iterating construct. `async for` borrows
    the same storage the sync loop does: `__aiter__` is user code and
    may hand back an iterator holding a pointer into the iterable, which
    the frame then keeps across every suspension. A comprehension walks a
    begin/end pair its own condition or element may reallocate under. So
    none of them may answer the invalidation question differently.

    Every loan MERGES with what the holder already has on the key: the
    iterated storage and an extra loan may name the same key, and the
    coarser loan must not replace the finer one.

    `excluded_roots` names storage the iterating code cannot reach by
    name: a comprehension whose target shadows its source's root.
    """
    bt = ctx.func.borrow_tracker
    storage = iterated_storage(ctx, iterable)
    excluded = set(excluded_roots)
    hold_whole(bt, holder,
               [r for r in storage.held_whole if r.as_name() not in excluded])
    for key, loan in [*storage.loans, *extra_loans]:
        if key.root in excluded:
            continue
        bt.add_borrow(key, holder, loan.kind, on_element=loan.on_element,
                      elem_index=loan.elem_index, merge=True)
    if storage.callee is None:
        if isinstance(iterable, TpyName) and not storage.unplaceable:
            # A protocol- or TypeParamRef-typed iterable is advanced
            # through a method that mutates iterator state, so the
            # generated param must be `T&`, not `const T&`. A concrete
            # type gets the same verdict from the call itself.
            inner = unwrap_ref_type(unwrap_readonly(iterable_type))
            if isinstance(inner, TypeParamRef) or is_protocol_type(inner):
                mark_roots(ctx, [StorageKey.of_name(iterable.name)], MarkRequest())
        return IterationLoan(unplaceable=storage.unplaceable,
                             loans=storage.loans)
    fi_iter = storage.callee
    for op in storage.operands:
        if op.sources or not is_dangling_temporary_arg(op.arg):
            continue
        # Call results returning non-value types are materialized into
        # named variables by codegen (for by-reference passing), so they
        # survive the for-loop. A borrowing-VIEW slot of a frame-capturing
        # callee is materialized too, by the view-backing hoist. Only warn
        # for what neither pins.
        is_materialized = temp_arg_kept_alive(
            fi_iter, op.idx, op.arg, op.slot, ctx)
        if isinstance(op.arg, (TpyCall, TpyMethodCall)):
            arg_fi = op.arg.resolved_function_info
            if arg_fi is not None and not arg_fi.return_type.is_value_type():
                is_materialized = True
        if not is_materialized:
            if op.idx == -1:
                detail = "temporary receiver object"
            else:
                detail = f"temporary {temp_arg_spelling(fi_iter, op.idx)}"
            ctx.warning(
                f"Iterator borrows from {detail}; "
                f"the temporary is destroyed before iteration begins",
                iterable,
            )
    if not storage.loans:
        return IterationLoan(unplaceable=storage.unplaceable)
    srcs_by_idx = {idx: op.sources
                   for idx, op in _sources_by_idx(storage.operands).items()}
    return IterationLoan(_yield_elem_sources(fi_iter, srcs_by_idx),
                         unplaceable=storage.unplaceable,
                         loans=storage.loans)


def _declared_readonly_param(fi: FunctionInfo, idx: int) -> bool:
    """Whether parameter `idx` of the stub is declared `readonly[...]`, the
    one fact that says a bodyless callee leaves that argument alone."""
    params = fi.root.params
    return (0 <= idx < len(params)
            and isinstance(params[idx].type, ReadonlyType))

def iterator_advanced_twice(ctx: 'SemanticContext', exprs: 'list[TpyExpr]'
                            ) -> 'tuple[TpyCallLike, str] | None':
    """The second borrow-declared call in one statement's expressions that
    hands back a reference to a STEP of an iterator an earlier one in the
    same statement also advances (`t = (next(g, d).v, next(g, d).v)`), with
    the iterator's name; or None. A step reference is valid only until the
    next advance, so the first reference would read the second step.

    A STOPGAP for BUGS.md#next-step-reference-outlived-by-advance, which
    MIR's step-loan model replaces: it sees only advances spelled in the
    statement, not a consumer advancing the iterator through a capture or a
    global. A COPY result holds no reference, a BORROW is a container's
    element, and a value-type element (`next(it, 0)`) is not
    borrow-declared, so none of them counts. Neither does an `element_of=`
    operand that is a CONTAINER the callee leaves alone -- a `@readonly`
    stub's receiver, a `readonly[...]` parameter (`d.get(k, P(0))`): its element is not a step, and a second call leaves it in
    place. A callee that mutates the container (`b.grow_or(P(0))`) may move
    the first call's element, so it counts like an advance."""
    seen: set[str] = set()
    found: list[tuple[TpyCallLike, str]] = []

    def visit(node: TpyExpr) -> bool:
        if found or isinstance(node, TpyLambda):
            return False
        # Only a REFERENCE_VALUE result points at a step: a BORROW is a
        # container's element, which a second walk leaves in place.
        if (isinstance(node, TpyCallLike)
                and node.result_form is ResultForm.REFERENCE_VALUE):
            ops = call_operands(node)
            lent = (call_lend_sources(
                        ops, ops.fi.root.element_borrows_from, expr_type=None)
                    if ops is not None else [])
            for src in lent:
                # The fact that the callee leaves the container alone is
                # per operand: `@readonly` speaks for the receiver only, a
                # parameter is left alone when it is declared readonly.
                untouched = (ops.fi.root.is_readonly if src.idx == -1
                             else _declared_readonly_param(ops.fi, src.idx))
                if (untouched
                        and iter_element_source(ctx.get_expr_type(src.expr),
                                                ctx.registry)
                        is IterElementSource.CONTAINER):
                    continue
                root = src.expr
                while isinstance(root, (TpyCoerce, TpyFieldAccess,
                                        TpySubscript)):
                    root = root.expr if isinstance(root, TpyCoerce) else root.obj
                if not isinstance(root, TpyName):
                    continue
                if root.name in seen:
                    found.append((node, root.name))
                    return False
                seen.add(root.name)
        return True

    for expr in exprs:
        walk_expr_tree(expr, visit)
    return found[0] if found else None
