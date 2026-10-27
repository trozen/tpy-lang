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
    TpyBinOp, TpyChainedCompare, TpyCoerce, TpyExpr, TpyFieldAccess, TpyMethodCall,
    TpyName, TpyStmt, TpySubscript, TpyUnaryOp,
)
from ..value_category import peel_coerce
from ..type_def_registry import protocol_info_of
from ..typesys import (
    FunctionInfo, ImplicitReadonly, MutationCallEdge, NominalType,
    ReadonlyType, RecordInfo, TpyType,
    is_bodyless_binding, is_dyn_protocol, is_protocol_type, unwrap_own,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
    receiver_lends_handle,
)
from .context import (
    _local_traces_to_self,
    call_param_args, element_index_key, element_loan_mutation_warning,
    storage_loan_mutation_warning,
)
from .storage_walk import MarkRequest, StorageKey, mark_roots, receiver_leaves

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


def call_mutates_receiver(fi: FunctionInfo, *, handle_climbs: bool = False) -> bool:
    """Whether calling `fi` may mutate its receiver: its C++ member is not
    const (`emitted_const`), or it lends a writable handle into the receiver
    (`receiver_lends_handle`) -- nothing tracks the copies of a handle, so
    the call is the write, whatever is done with its result -- unless the one
    name the handle reaches its referent through climbs to the receiver
    (`handle_climbs`: a `for` loop variable, `loop_var_iterable`).

    The mutable clone of an @auto_readonly accessor (Box.get / Rc.get /
    Deref, a reference-returning `__getitem__`) that lends a reference does
    not mutate the receiver -- a mutation THROUGH its result climbs the loan
    the binding takes, or roots back to the receiver at the mutation site."""
    if receiver_lends_handle(fi) and not handle_climbs:
        return True
    return not (fi.emitted_const or fi.is_pure
                or fi.is_auto_readonly_mutable_clone)


def _overload_invalidates(m: FunctionInfo) -> bool:
    # A bodyless binding (a builtin's method) answers by its declaration;
    # a bodied method by the structural mutation inferred from its body --
    # only a method that directly grows / shrinks self's storage invalidates
    # references; getters and field-only writes are safe. None means not yet
    # analyzed (forward ref) -> conservative. Indirect structural mutation
    # through same-class method calls is not detected here
    # (BUGS.md#transitive-receiver-growth-unchecked): Phase 2 propagation
    # runs after body analysis. The mutable clone of an @auto_readonly pair
    # hands out a borrow but does not mutate its receiver.
    if (m.is_readonly or m.native_mutates == "elements"
            or m.is_auto_readonly_mutable_clone):
        return False
    if is_bodyless_binding(m):
        return True
    smp = m.root.direct_structural_mutated_params
    return smp is None or -1 in smp


def _builtin_record_of(ctx: SemanticContext,
                       obj_type: TpyType) -> RecordInfo | None:
    qname = obj_type.qualified_name()
    return ctx.registry.get_builtin_record(qname) if qname else None


def is_invalidating_method(ctx: SemanticContext, obj_type: TpyType,
                           method_name: str) -> bool:
    """Check if a method invalidates iterators/references on a container,
    looked up BY NAME through the receiver's MRO (the explicit `g.m()`
    path), so an inherited method answers by the class that defines it.

    Each overload answers by `_overload_invalidates`: its declaration
    when it has no body, else its inferred direct structural mutation.
    """
    owner = _method_definer(ctx, obj_type, method_name)
    if owner is None:
        return False
    return any(_overload_invalidates(m)
               for m in owner.get_method_overloads(method_name))


def _method_definer(ctx: SemanticContext, obj_type: TpyType,
                    method_name: str) -> RecordInfo | None:
    record = (_builtin_record_of(ctx, obj_type)
              or ctx.registry.get_record_for_type(obj_type))
    if record is None:
        return None
    found = ctx.registry.mro_method_definer(record, method_name)
    return found[1] if found is not None else None


def method_writes_receiver(ctx: SemanticContext, obj_type: TpyType,
                           method_name: str) -> bool:
    """Whether calling `method_name` on an `obj_type` receiver may write
    it: some overload of that name (resolved through the MRO) is not
    `@readonly`. A name the type does not define answers True -- the
    caller is asking a syntactic candidate and cannot prove it harmless."""
    owner = _method_definer(ctx, obj_type, method_name)
    overloads = owner.get_method_overloads(method_name) if owner is not None else []
    return not overloads or any(not m.is_readonly for m in overloads)


def callee_invalidates(callee: FunctionInfo) -> bool:
    """`is_invalidating_method` for a callee already resolved (through the
    MRO), so an inherited dunder is answered by its own facts."""
    return _overload_invalidates(callee)


def _invalidates(ctx: SemanticContext, obj_type: TpyType, method: str,
                 callee: FunctionInfo | None) -> bool:
    if callee is not None:
        return callee_invalidates(callee)
    return is_invalidating_method(ctx, obj_type, method)


def check_receiver_call_loans(ctx: SemanticContext, recv: TpyExpr,
                              obj_type: TpyType, method: str,
                              site: TpyExpr | TpyStmt, *,
                              callee: FunctionInfo | None = None,
                              label: str | None = None) -> None:
    """Warn when calling `method` on `recv` may invalidate a live loan.

    Resolves aliases, so `alias.append()` warns when `items` has element
    borrows, and field-path receivers (`self.items.append()`). A resolved
    `callee` answers the invalidation question itself; without one the
    method is looked up by name. `label` names the operation in the
    warning where no call is written (`slice assignment`)."""
    if label is None:
        label = f"'{method}'"
    bt = ctx.func.borrow_tracker
    storage = bt.resolve_obj_storage(recv)
    if storage is not None:
        if (bt.has_element_borrow(storage)
                and _invalidates(ctx, obj_type, method, callee)):
            ctx.warning(storage_loan_mutation_warning(
                bt, storage, label), site)
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
                f"{elem_of}[...]", label, hit), site)


def record_receiver_edges(ctx: SemanticContext, callee: FunctionInfo,
                          idxs: list[int], bindings: 'tuple[object, ...]',
                          elem: 'frozenset[int]',
                          captured: 'tuple[str, ...]' = (), *,
                          param_edge: MutationCallEdge | None = None,
                          slot: int = -1) -> None:
    """File the Phase-2 edges of a deferred write through `callee`'s slot
    `slot` (-1: its receiver): one per caller param in `idxs`
    (element-stepped when in `elem`), the loop bindings the argument climbs
    through on the first -- or on `param_edge`, the call's argument edge,
    which then also carries the first param. In a nested def, the
    enclosing function's `captured` names get theirs at the replay
    (`defer_nested_writes`)."""
    fn = ctx.func
    ctx.defer_nested_writes(callee, slot, captured)
    edges: list[MutationCallEdge] = []
    for i, idx in enumerate(idxs):
        edge = (param_edge if i == 0 and param_edge is not None
                else MutationCallEdge(callee_fi=callee.root, param_map={}))
        if slot == -1:
            edge.receiver_idx = idx
        else:
            edge.param_map = {**edge.param_map, slot: idx}
        if idx in elem:
            edge.elem_step = edge.elem_step | {slot}
        edges.append(edge)
    if bindings:
        if not edges:
            edges.append(param_edge if param_edge is not None
                         else MutationCallEdge(callee_fi=callee.root,
                                               param_map={}))
        edges[0].bindings = {**edges[0].bindings, slot: tuple(bindings)}
    for edge in edges:
        if edge is param_edge:
            continue
        if edge.receiver_idx is not None or edge.param_map:
            fn.current_call_edges.append(edge)
        if edge.bindings:
            ctx.binding_edges.append(edge)


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
    a callee is known; with no callee the mark is always eager, and so is a
    call lending a writable handle (`receiver_lends_handle`: the callee's
    own body does not write its self). A known
    `callee` also answers the structural-mutation question from its own facts
    (an inherited dunder included); without one the method is looked up by
    name.
    """
    eager = eager or callee is not None and receiver_lends_handle(callee)
    for leaf in receiver_leaves(recv):
        _credit_leaf(ctx, leaf, obj_type, method, callee, eager, edges_recorded)


def _credit_leaf(ctx: SemanticContext, recv: TpyExpr, obj_type: TpyType | None,
                 method: str, callee: FunctionInfo | None, eager: bool,
                 edges_recorded: bool) -> None:
    fn = ctx.func
    defers = not eager and (edges_recorded or callee is not None)
    # Structural mutation is tracked directly: Phase 2 does not propagate
    # structural self-mutation through call edges. A direct `self`
    # receiver is the callee's own self and stays with its facts.
    is_direct_self = isinstance(recv, TpyName) and recv.name == "self"
    structural = (not is_direct_self and obj_type is not None
                  and _invalidates(ctx, obj_type, method, callee))
    names_by_idx = {i: n for n, i in fn.current_param_name_to_idx.items()}
    for credit in ctx.receiver_credits(recv):
        name = credit.name
        deferred = defers and credit.deferred
        if deferred:
            for idx in credit.idxs:
                pname = names_by_idx.get(idx)
                if pname is not None and pname != "self":
                    fn.current_receiver_deferred_param_names.add(pname)
            if not edges_recorded:
                assert callee is not None
                record_receiver_edges(ctx, callee, list(credit.idxs),
                                      credit.bindings, credit.elem,
                                      credit.captured)
        # A loop binding's receiver edge decides the binding too.
        mark_roots(ctx, [StorageKey.of_name(name)], MarkRequest(
            params=not deferred,
            loop_vars=not (deferred and credit.bindings),
            structural=structural))
        if (not deferred and name != "self"
                and _local_traces_to_self(fn.borrow_tracker, name)):
            # A local alias of self-owned storage: its own mark stops at the
            # member key it borrows, which names no parameter.
            mark_roots(ctx, [StorageKey.of_name("self")], MarkRequest())
        ctx.mark_all_view_borrowers_mutated(
            fn.borrow_tracker.effective_storage(name))


def receiver_is_readonly(ctx: SemanticContext, recv: TpyExpr | None,
                         recv_type: TpyType | None = None) -> bool:
    """Whether a call's receiver is a readonly reference: its type (the
    caller's `recv_type` when it has one, else the recorded type of each
    operand the receiver may be), or an operand NAME whose declared scope
    type is readonly (which isinstance narrowing does not strip). The one
    test for explicit and implicit calls alike; a receiver no expression
    spells (an operator's promoted operand) is judged by its type."""
    leaves = receiver_leaves(recv) if recv is not None else []
    types = ([recv_type] if recv_type is not None
             else [ctx.get_expr_type(leaf) for leaf in leaves])
    if any(t is not None
           and isinstance(unwrap_send_sync(unwrap_ref_type(t)), ReadonlyType)
           for t in types):
        return True
    return any(isinstance(leaf, TpyName) and ctx.is_readonly_name(leaf.name)
               for leaf in leaves)


def implicit_readonly_result_hint(ctx: SemanticContext, obj: TpyExpr) -> str:
    """The way out of a write through the result of a method readonly
    without the user spelling it (`readonly_result`): the place written,
    or the local it is read off, is that call's result."""
    while True:
        obj = peel_coerce(obj)
        if isinstance(obj, (TpyFieldAccess, TpySubscript)):
            obj = obj.obj
        elif (isinstance(obj, TpyName)
                and (decl := ctx.func.var_decl_by_name.get(obj.name)) is not None
                and decl.init is not None):
            obj = decl.init
        else:
            break
    fi = (obj.resolved_function_info if isinstance(obj, TpyMethodCall)
          else obj.resolved_binop.method
          if isinstance(obj, TpyBinOp) and obj.resolved_binop is not None
          else obj.resolved_unaryop.method
          if isinstance(obj, TpyUnaryOp) and obj.resolved_unaryop is not None
          else None)
    reason = fi.root.implicit_readonly_result if fi is not None else None
    if reason is ImplicitReadonly.PURE:
        how = f"'@pure' method '{fi.name}' returns it readonly; drop @pure"
    elif reason is ImplicitReadonly.FROZEN:
        how = (f"method '{fi.name}' of a frozen record returns it readonly; "
               f"mark it @readonly(False)")
    elif reason is ImplicitReadonly.DUNDER:
        how = (f"'{fi.name}' returns it readonly; declare '-> Own[...]' "
               f"to return a fresh value")
    else:
        return ""
    return f" ({how}, or copy() the result)"



def implicit_call_mutates_receiver(ctx: SemanticContext, recv: TpyExpr,
                                   callee: FunctionInfo, *,
                                   handle_climbs: bool = False) -> bool:
    """`call_mutates_receiver` for the callee an implicit dunder call on
    `recv` runs. A dunder is found by name, so an `@auto_readonly` pair
    answers with its mutable clone; a readonly receiver runs the const
    clone, which mutates and lends nothing -- as an explicit call's overload
    resolution picks it."""
    if callee.is_auto_readonly_mutable_clone and receiver_is_readonly(ctx, recv):
        return False
    return call_mutates_receiver(callee, handle_climbs=handle_climbs)


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
    if callee is not None and not implicit_call_mutates_receiver(
            ctx, recv, callee):
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
        record: bool = True, handle_climbs: bool = False,
        label: str | None = None) -> None:
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
    `check_implicit_readonly_receiver`, `handle_climbs` as for
    `call_mutates_receiver`, `label` as for `check_receiver_call_loans`.
    """
    if record:
        record_implicit_call(site, callee, recv, method)
    if callee is not None and not implicit_call_mutates_receiver(
            ctx, recv, callee, handle_climbs=handle_climbs):
        return
    check_implicit_readonly_receiver(ctx, recv, callee, method, site,
                                     record=False)
    if check_loans and obj_type is not None:
        check_receiver_call_loans(ctx, recv, obj_type, method, site,
                                  callee=callee, label=label)
    credit_receiver_mutation(ctx, recv, obj_type, method, callee=callee,
                             eager=eager)
