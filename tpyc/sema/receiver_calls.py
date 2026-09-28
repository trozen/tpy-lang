"""A call on a receiver: what it does to the receiver's borrows and mutation facts.

`g.m()` is not the only spelling of a method call on `g`. `g[k]` calls
`__getitem__`, `k in g` `__contains__`, `g[k] = v` `__setitem__`,
`for x in g:` `__iter__`, `with g:` `__enter__` / `__exit__`, `await g`
`__poll__`. For borrow and mutation purposes every one of them is the same
call, so every site answers the same three questions here, once:

- may this receiver be mutated at all (`readonly[T]` forbids a
  non-readonly method),
- does the call invalidate a live loan into the receiver's storage,
- which param / self / loop variable does it mutate (Phase 1 marks, or a
  Phase-2 call edge for a receiver rooted at `self`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..compilation_context import get_current_compiler
from ..parse import (
    TpyBinOp, TpyChainedCompare, TpyCoerce, TpyExpr, TpyFieldAccess, TpyIfExpr, TpyMethodCall,
    TpyName, TpyStmt, TpySubscript,
)
from ..type_def_registry import protocol_info_of
from ..typesys import (
    FunctionInfo, MutationCallEdge, NominalType, ReadonlyType, TpyType,
    is_bodyless_binding, is_dyn_protocol, is_protocol_type, unwrap_own,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .context import (
    _is_self_call_deferred, _local_traces_to_self, _root_name_of_expr,
    call_param_args, element_index_key, element_loan_mutation_warning,
    loan_mutation_warning,
)

if TYPE_CHECKING:
    from .context import SemanticContext


@dataclass(frozen=True)
class ImplicitCall:
    """A method a node calls without spelling it: `fi` (None when no
    FunctionInfo names it -- a protocol method) on `receiver`."""
    fi: FunctionInfo | None
    receiver: TpyExpr
    method: str


def record_implicit_call(site: TpyExpr | TpyStmt, callee: FunctionInfo | None,
                         recv: TpyExpr, method: str) -> None:
    """Note that `site` calls `method` (`callee`) on `recv`. Every callee is
    kept, readonly ones included: a readonly method may still write a
    module global."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    calls = compiler.implicit_calls.setdefault(site, [])
    if not any(c.fi is callee and c.receiver is recv and c.method == method
               for c in calls):
        calls.append(ImplicitCall(callee, recv, method))


def _user_record(ctx: 'SemanticContext', t: TpyType | None):
    t = unwrap_readonly(unwrap_own(unwrap_ref_type(t))) if t is not None else None
    if not (isinstance(t, NominalType) and t.is_user_record):
        return None
    return ctx.registry.get_record_for_type(t)


def record_truth_calls(ctx: 'SemanticContext',
                       leaves: 'list[TpyExpr]') -> None:
    """A truth test of a user-type value runs its `__bool__`, or its
    `__len__` when it has none, on each leaf the test reaches."""
    for leaf in leaves:
        while isinstance(leaf, TpyCoerce):
            leaf = leaf.expr
        record = _user_record(ctx, ctx.get_expr_type(leaf))
        if record is None:
            continue
        for method in ("__bool__", "__len__"):
            fis = ctx.registry.get_method_overloads_with_parents(record, method)
            for fi in fis:
                record_implicit_call(leaf, fi, leaf, method)
            if fis:
                break


def record_protocol_arg_calls(ctx: 'SemanticContext',
                              call: TpyExpr) -> None:
    """A runtime callee (one with no body sema sees) runs the methods of a
    structural protocol parameter on its argument -- `len(b)` runs
    `b.__len__` -- which for a user type are the type's own."""
    fi = getattr(call, "resolved_function_info", None)
    if fi is None or not is_bodyless_binding(fi):
        return
    for param, arg in call_param_args(call):
        pt = unwrap_readonly(unwrap_own(unwrap_ref_type(param.type)))
        if not is_protocol_type(pt) or is_dyn_protocol(pt):
            continue
        while isinstance(arg, TpyCoerce):
            arg = arg.expr
        record = _user_record(ctx, ctx.get_expr_type(arg))
        if record is None:
            continue
        for method in _protocol_methods(pt):
            for mfi in ctx.registry.get_method_overloads_with_parents(
                    record, method):
                record_implicit_call(call, mfi, arg, method)


def _protocol_methods(protocol: NominalType) -> list[str]:
    out: list[str] = []
    pending = [protocol]
    seen: set[str] = set()
    while pending:
        info = protocol_info_of(pending.pop())
        if info is None or info.name in seen:
            continue
        seen.add(info.name)
        out += [m.name for m in info.methods]
        pending += info.parent_protocols
    return out


def implicit_calls(node: TpyExpr | TpyStmt) -> list[ImplicitCall]:
    """The dunder calls `node` makes without spelling them, as sema resolved
    them. A chained comparison's calls are made by the pairs sema builds for
    it, which no tree walk reaches."""
    compiler = get_current_compiler()
    if compiler is None:
        return []
    out = list(compiler.implicit_calls.get(node, ()))
    if isinstance(node, TpyChainedCompare):
        for pair in node.pairs or ():
            out += compiler.implicit_calls.get(pair, ())
    return out


def receiver_leaves(expr: TpyExpr) -> list[TpyExpr]:
    """The operands a method-call receiver may BE: the receiver itself, or
    for a ternary / value and/or select every operand it can pick
    (recursively) -- the call mutates whichever one it runs on."""
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyIfExpr):
        return (receiver_leaves(expr.then_expr)
                + receiver_leaves(expr.else_expr))
    if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        return receiver_leaves(expr.left) + receiver_leaves(expr.right)
    return [expr]


def call_mutates_receiver(fi: FunctionInfo) -> bool:
    """Whether calling `fi` may mutate its receiver.

    The mutable clone of an @auto_readonly accessor (Box.get / Rc.get /
    Deref, a reference-returning `__getitem__`) hands out a borrow but does
    not mutate the receiver -- a mutation THROUGH its result is rooted back
    to the receiver at the mutation site."""
    return not (fi.is_readonly or fi.is_pure
                or fi.is_auto_readonly_mutable_clone)


def _builtin_overload_invalidates(m: FunctionInfo) -> bool:
    # The mutable clone of an @auto_readonly accessor (values/items) hands
    # out a borrow but does not mutate the receiver.
    return (not m.is_readonly and not m.native_preserves_refs
            and not m.is_auto_readonly_mutable_clone)


def _user_overload_invalidates(m: FunctionInfo) -> bool:
    # Inferred structural mutation: only a method that directly structurally
    # mutates self (append/insert/del on self's fields) can invalidate
    # references; getters and field-only writes are safe. None means not yet
    # analyzed (forward ref) -> conservative. Indirect structural mutation
    # through same-class method calls is not detected here
    # (BUGS.md#transitive-receiver-growth-unchecked): Phase 2 propagation
    # runs after body analysis.
    if m.is_readonly:
        return False
    smp = m.root.direct_structural_mutated_params
    return smp is None or -1 in smp


def _builtin_record_of(ctx: SemanticContext, obj_type: TpyType):
    qname = obj_type.qualified_name()
    return ctx.registry.get_builtin_record(qname) if qname else None


def is_invalidating_method(ctx: SemanticContext, obj_type: TpyType,
                           method_name: str) -> bool:
    """Check if a method invalidates iterators/references on a container,
    looked up BY NAME among the type's own methods (the explicit `g.m()`
    path; an inherited method is not found --
    BUGS.md#explicit-inherited-method-no-loan-warning).

    For builtin types (list, dict, set, etc.): a method invalidates if it
    is non-readonly AND not marked with @native_preserves_refs.
    For user-defined types: its inferred direct structural mutation.
    """
    builtin_record = _builtin_record_of(ctx, obj_type)
    if builtin_record is not None:
        return any(_builtin_overload_invalidates(m)
                   for m in builtin_record.get_method_overloads(method_name))
    record = ctx.registry.get_record_for_type(obj_type)
    if record is None:
        return False
    return any(_user_overload_invalidates(m)
               for m in record.get_method_overloads(method_name))


def callee_invalidates(ctx: SemanticContext, obj_type: TpyType,
                       callee: FunctionInfo) -> bool:
    """`is_invalidating_method` for a callee already resolved (through the
    MRO), so an inherited dunder is answered by its own facts."""
    if _builtin_record_of(ctx, obj_type) is not None:
        return _builtin_overload_invalidates(callee)
    return _user_overload_invalidates(callee)


def _invalidates(ctx: SemanticContext, obj_type: TpyType, method: str,
                 callee: FunctionInfo | None) -> bool:
    if callee is not None:
        return callee_invalidates(ctx, obj_type, callee)
    return is_invalidating_method(ctx, obj_type, method)


def check_receiver_call_loans(ctx: SemanticContext, recv: TpyExpr,
                              obj_type: TpyType, method: str,
                              site: TpyExpr | TpyStmt, *,
                              callee: FunctionInfo | None = None) -> None:
    """Warn when calling `method` on `recv` may invalidate a live loan.

    Resolves aliases, so `alias.append()` warns when `items` has element
    borrows, and field-path receivers (`self.items.append()`). A resolved
    `callee` answers the invalidation question itself; without one the
    method is looked up by name."""
    bt = ctx.func.borrow_tracker
    storage = bt.resolve_obj_storage(recv)
    if storage is not None:
        if (bt.has_element_borrow(storage)
                and _invalidates(ctx, obj_type, method, callee)):
            ctx.warning(loan_mutation_warning(
                storage, f"'{method}'",
                iterating=bt.has_iter_borrow(storage)), site)
    elif isinstance(recv, TpySubscript):
        # The receiver is itself an element read, which no storage key can
        # spell. A loan taken out of an element of the SAME container is
        # clobbered by a reallocating method on any of its elements, so the
        # conflict is asked of the container the receiver came out of.
        elem_of = bt.resolve_obj_storage(recv.obj)
        hit = (bt.element_hop_loan(elem_of, element_index_key(recv.index))
               if elem_of is not None else None)
        if hit is not None and _invalidates(ctx, obj_type, method, callee):
            ctx.warning(element_loan_mutation_warning(
                f"{elem_of}[...]", f"'{method}'", hit), site)


def credit_receiver_mutation(ctx: SemanticContext, recv: TpyExpr,
                             obj_type: TpyType | None, method: str, *,
                             callee: FunctionInfo | None = None,
                             eager: bool = False,
                             edges_recorded: bool = False) -> None:
    """Record that a mutating call of `method` mutates `recv`.

    A receiver rooted at `self` (`self`, `self.f`, a loop variable over a
    self field, a local alias of one) is deferred to Phase 2 through a call
    edge on `callee`, so a callee that turns out not to mutate its self does
    not demote the enclosing method. `edges_recorded` says the caller's
    call-edge recorder already filed that edge (an explicit method call).
    `eager` marks the receiver directly instead of deferring, whether or not
    a callee is known; with no callee the mark is always eager. A known
    `callee` also answers the structural-mutation question from its own facts
    (an inherited dunder included); without one the method is looked up by
    name.
    """
    for leaf in receiver_leaves(recv):
        _credit_leaf(ctx, leaf, obj_type, method, callee, eager, edges_recorded)


def _credit_leaf(ctx: SemanticContext, recv: TpyExpr, obj_type: TpyType | None,
                 method: str, callee: FunctionInfo | None, eager: bool,
                 edges_recorded: bool) -> None:
    fn = ctx.func
    obj_root = _root_name_of_expr(recv)
    if obj_root is not None:
        ctx.mark_loop_var_mutated(obj_root)
        receiver_idx = ctx.self_receiver_index()
        defers = edges_recorded or (not eager and callee is not None
                                    and receiver_idx is not None)
        deferred = defers and _is_self_call_deferred(
            recv, obj_root, fn.loop_var_iterable, fn.borrow_tracker)
        if deferred and not edges_recorded:
            assert callee is not None
            fn.current_call_edges.append(MutationCallEdge(
                callee_fi=callee.root, param_map={},
                receiver_idx=receiver_idx))
        elif not deferred:
            ctx.mark_param_mutated(obj_root)
        # Structural mutation is tracked directly: Phase 2 does not
        # propagate structural self-mutation through call edges. A direct
        # `self` receiver is the callee's own self and stays with its facts.
        is_direct_self = isinstance(recv, TpyName) and recv.name == "self"
        if (not is_direct_self and obj_type is not None
                and _invalidates(ctx, obj_type, method, callee)):
            ctx.mark_param_structurally_mutated(obj_root)
        storage = fn.borrow_tracker.effective_storage(obj_root)
        ctx.mark_all_view_borrowers_mutated(storage)
        return
    # Check for chained method calls rooted at self:
    # self.get_span().sort() -- sort() is non-readonly and
    # the span is a mutable view of self's storage.
    # Treat as self-mutation conservatively. A chain rooted
    # at a local that borrow-traces to self.<field> (e.g.
    # `frame = self.frame; frame.get().mutating_method()`)
    # is also a self-mutation since the receiver aliases
    # self-owned storage.
    chain = recv
    while isinstance(chain, TpyMethodCall):
        chain = chain.obj
    chain_root = _root_name_of_expr(chain)
    if chain_root is not None and _local_traces_to_self(
            fn.borrow_tracker, chain_root):
        ctx.mark_param_mutated("self")


def receiver_is_readonly(ctx: SemanticContext, recv: TpyExpr,
                         recv_type: TpyType | None = None) -> bool:
    """Whether a call's receiver is a readonly reference: its type (the
    caller's `recv_type` when it has one, else the recorded type of each
    operand the receiver may be), or an operand NAME whose declared scope
    type is readonly (which isinstance narrowing does not strip). The one
    test for explicit and implicit calls alike."""
    leaves = receiver_leaves(recv)
    types = ([recv_type] if recv_type is not None
             else [ctx.get_expr_type(leaf) for leaf in leaves])
    if any(t is not None
           and isinstance(unwrap_send_sync(unwrap_ref_type(t)), ReadonlyType)
           for t in types):
        return True
    return any(isinstance(leaf, TpyName) and ctx.is_readonly_name(leaf.name)
               for leaf in leaves)


def check_implicit_readonly_receiver(
        ctx: SemanticContext, recv: TpyExpr, callee: FunctionInfo | None,
        method: str, site: TpyExpr | TpyStmt, *, record: bool = True) -> None:
    """A readonly receiver may not take a mutating implicit dunder call.

    Whether the dunder may be called on a const receiver is its EMITTED
    const-ness, which const inference settles for every module only in the
    workspace-wide finalize pass (`resolve_pending_readonly_receiver_checks`),
    so a known callee is queued for it. With no callee the caller has
    already judged the call mutating, and it is rejected at once.
    `record=False` for a call the frame rules count elsewhere (an awaited
    frame runs what the calls that built it may write)."""
    if record:
        record_implicit_call(site, callee, recv, method)
    if callee is not None and not call_mutates_receiver(callee):
        return
    if not receiver_is_readonly(ctx, recv):
        return
    if callee is None:
        raise ctx.error(
            f"Cannot call non-readonly method '{method}' on readonly reference",
            site)
    assert ctx.queue_readonly_receiver_check is not None
    ctx.queue_readonly_receiver_check((callee, method, site))


def credit_implicit_receiver_call(
        ctx: SemanticContext, recv: TpyExpr, obj_type: TpyType | None,
        callee: FunctionInfo | None, method: str, site: TpyExpr | TpyStmt, *,
        eager: bool = False, check_loans: bool = True,
        record: bool = True) -> None:
    """The receiver effects of a dunder call no call node spells.

    `callee` is the resolved dunder (found through the MRO); it decides
    whether the call mutates the receiver and whether it may invalidate
    loans into it. With None the caller has already decided the call
    mutates (an awaited coroutine handle, a protocol method with no
    FunctionInfo) and the method is looked up by name. A readonly receiver
    (read off `recv` itself) rejects a mutating call. `eager` marks the
    receiver directly rather than deferring a `self`-rooted one to Phase 2.
    `check_loans=False` when the caller asked `check_receiver_call_loans`
    itself, before registering a loan the call must not see (the for
    statement's own iteration loan). `record` as for
    `check_implicit_readonly_receiver`.
    """
    if record:
        record_implicit_call(site, callee, recv, method)
    if callee is not None and not call_mutates_receiver(callee):
        return
    check_implicit_readonly_receiver(ctx, recv, callee, method, site,
                                     record=False)
    if check_loans and obj_type is not None:
        check_receiver_call_loans(ctx, recv, obj_type, method, site,
                                  callee=callee)
    # A call through an `unsafe_interior_mutable` field mutates bookkeeping
    # the owner declared outside its readonly boundary -- it must not demote
    # the enclosing method.
    if isinstance(recv, TpyFieldAccess) and recv.accessed_field_is_interior:
        return
    credit_receiver_mutation(ctx, recv, obj_type, method, callee=callee,
                             eager=eager)
