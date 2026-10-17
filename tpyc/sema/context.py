"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Container, Iterator, NamedTuple, TYPE_CHECKING

from ..holders import Holder, IterHolder
from ..identity_map import IdentityMap, IdentitySet
from ..macro_loader import MacroRegistry
from ..parse.nodes import SourceLocation, TryTier, name_path
from .. import qnames
from . import own_copy
from .value_range import ValueRange

if TYPE_CHECKING:
    from ..parse import TpyGeneratorExpression
    from .flow_facts import FlowFacts
    from .pending_num import DeferredIntOp, PendingNumCell
    from ..parse.type_resolver import TypeResolver
    from .loop_frames import (PendingLoopFrameCall, PendingSourceWrite,
                              WithExitCheck)
    from .scope_tracker import DeferredEscape
    from .slot_hint import SlotHint

from ..typesys import (
    global_tuple_binding_type, disambiguated_pair,
    TpyType, TypeRegistry, ContainerLiteralInfo, ListLiteralInfo, ViewTypeFamily, ViewVarInfo, TypeParamKind, IntLiteralType,
    INT32, BIGINT, NominalType, ReadonlyType, OwnType, OptionalType, TupleType,
    RecursiveAliasInstanceType, recursive_union_alternatives, ConcreteFrameType,
    ConcreteGenType,
    PendingContainerType, PendingListType, PendingDictType, PendingSetType,
    UnknownElementType,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    PendingViewType, PendingStrType, VIEW_TYPE_FAMILIES,
    unwrap_own, unwrap_readonly, unwrap_ref_type, unwrap_qualifiers,
    FunctionInfo, ParamInfo, RecordInfo,
    is_dyn_protocol, contains_pending_leaf, TypeParamRef,
    is_indirection_type, record_owner,
)
from ..namespace import BindingKind, NameBinding, Namespace
from ..symbol_binding import (SymbolBinding, SymbolKind, resolve_type_path,
                              walk_attribute_chain)
from ..type_def_registry import (enum_info_of, int_traits_of,
                                 is_borrowing_view_type)
from ..parse import (
    TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall,
    TpyCall, TpyCoerce, TpyName, TpyFieldAccess,
    TpyNestedDef,
    TpyGeneratorExpression, TpyForEach, TpyWhile,
    is_parse_node,
)
from ..diagnostics import Diagnostic, DiagnosticLevel, SemanticError, Scope
from ..value_category import (
    CallOperands, ExprTypeOf, is_rvalue_source, peel_coerce,
)
from ..prescan import InPlaceWrites, bound_names_of, loop_bindings_of, walrus_names_of
from .call_lends import (
    NO_CALLS, MayLend, escaping_borrow_tuple, frame_operands,
    escaping_borrow_writes, frame_call,
)
from .storage_walk import (
    EscapePosition, KeyShape, LoanKey, MarkRequest, Roots, Unkeyed,
    Use, element_index_key, holder_root, loan_key,
    mark_roots, path_key,
    StorageKey, select_arm_keys, slot_lent_roots, storage_key,
    storage_roots, ternary_operands,
)
from ..storage_trace import StepKind, trace

# Tuple of all pending container types -- use in isinstance checks so adding
# a new container type requires updating only this one constant.
PENDING_CONTAINER_TYPES = (PendingListType, PendingDictType, PendingSetType)


class IndexRelation(Enum):
    """How the element two subscripts of one container name are related."""
    SAME = "same"          # provably the same element
    DISTINCT = "distinct"  # provably different elements
    UNKNOWN = "unknown"    # may be either


def element_index_relation(a: 'tuple[str, int | str] | None',
                           b: 'tuple[str, int | str] | None') -> IndexRelation:
    """The relation between two `element_index_key` answers.

    Equal keys are SAME whichever form they take; two literals that differ
    are DISTINCT. Everything else -- a missing key, a literal against a name,
    two different names -- is UNKNOWN, which warns but must say so.
    """
    if a is None or b is None:
        return IndexRelation.UNKNOWN
    if a == b:
        return IndexRelation.SAME
    if a[0] == "lit" and b[0] == "lit":
        return IndexRelation.DISTINCT
    return IndexRelation.UNKNOWN


def expr_yields_non_null_ptr(expr: TpyExpr, non_null_vars: set[str]) -> bool:
    """Whether evaluating `expr` produces a Ptr value with known non-null provenance.

    Three independent sources, all unified here so consumers (local-init,
    rebind, future return-site / expression-context uses) don't re-enumerate:

    1. Address-taking coercion (`&{e}` -- the `produces_non_null_ptr` flag on
       the Coercion). Covers `p: Ptr[T] = x` and the inheritance / @dynamic
       upcasts. A chain of coercions is walked: any non-address-taking outer
       coercion (e.g. the trivial `Ptr[T] -> Ptr[readonly[T]]`) is transparent
       and provenance is inherited from the inner expression.
    2. Ptr-returning call. Either the call's return type is itself Ptr-typed
       (`take_ptr(x)`, explicit `Ptr(x)` constructor) or the function carries
       the `@value_ptr_coercion` flag (its Ptr param coerces a T arg via `&`).
    3. Read from a name that earlier analysis already marked non-null.
    """
    while isinstance(expr, TpyCoerce):
        if expr.coercion.produces_non_null_ptr:
            return True
        expr = expr.expr
    if isinstance(expr, TpyCall) and expr.args:
        if expr.call_type is not None and expr.call_type.is_pointer():
            return True
        fi = expr.resolved_function_info
        if fi is not None and fi.value_ptr_coercion:
            return True
    if isinstance(expr, TpyName):
        return expr.name in non_null_vars
    return False


def _local_traces_to_self(borrow_tracker: 'BorrowTracker', name: str) -> bool:
    """True when a local borrow-traces to self or a self.<field> storage key.

    Used to recognize a method call receiver whose root is a local alias of
    self-owned storage (e.g. ``frame = self.frame; frame.get().cancel()``)
    so the call's self-mutation flows back through the enclosing method.

    A member key is re-rooted through the binding it hangs off (`g =
    f.inner` with `f = self.h`: `g -> f.inner -> self.h`), since a field of
    an alias is the aliased storage's field.

    ANY root answers for a re-seated name: the receiver aliases self-owned
    storage on at least one reaching path, and the verdict only ever ADDS the
    self-mutation edge, which demotes readonly -- the safe direction.
    """
    seen: set[str] = set()
    todo = [name]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        for root in borrow_tracker.storage_roots_or_self(n):
            if root.root == "self":
                return True
            if root.root != n:
                todo.append(root.root)
    return False


def _deferred_receiver_root(
    expr_obj: TpyExpr, obj_root: str | None, direct: bool,
    params: 'Container[str]',
    loop_var_iterable: 'dict[str, list[StorageKey]]',
    borrow_tracker: 'BorrowTracker', type_of: 'ExprTypeOf',
) -> str | None:
    """The name a method call receiver's mutation is deferred to Phase 2
    under (a call edge's `receiver_idx`), or None to mark it at once.

    `self` or a caller parameter in `params`, the receiver's own field
    path (`direct`: `p.m()`, `p.f.m()`), so a callee that turns out not
    to mutate its self leaves the caller's receiver unmarked. For `self`
    also a loop
    variable over a self field and a local alias of self-owned storage; a
    parameter's loop variable or alias stays eager, because the eager mark
    also records the ELEMENT fact (the storage the parameter lends was
    written), which a receiver edge does not carry. A handle receiver runs
    the call on its referent, never on the storage the handle was read out
    of.
    """
    if is_indirection_type(type_of(expr_obj)):
        return None
    if direct and (obj_root == "self"
                   or (obj_root is not None and obj_root in params)):
        return obj_root
    if obj_root is not None and obj_root != "self":
        # loop_var.method() where loop_var iterates over self.field
        if any(it.root == "self"
               for it in loop_var_iterable.get(obj_root, ())):
            return "self"
        # local.method() where `local = self.<field>` registered a FIELD borrow.
        # Without this, the call is treated as a regular non-self call and the
        # callee's self-mutation never propagates back to the enclosing method,
        # so non-const methods reached through a local alias of a self field
        # leave self_mutated unset and auto-readonly wrongly marks the method
        # const. Deferring lets Phase 2 propagate precisely.
        if _local_traces_to_self(borrow_tracker, obj_root):
            return "self"
    return None


def call_param_args(call: TpyExpr) -> 'list[tuple[ParamInfo, TpyExpr]]':
    """Each argument of a resolved call with the parameter it binds --
    positionally, then keywords by name."""
    if not isinstance(call, (TpyCall, TpyMethodCall)):
        return []
    fi = call.resolved_function_info
    if fi is None:
        return []
    by_name = {p.name: p for p in fi.params}
    pairs = [(fi.params[i], a) for i, a in enumerate(call.args)
             if i < len(fi.params)]
    pairs += [(by_name[k], a) for k, a in (call.kwargs or {}).items()
              if k in by_name]
    return pairs


def frame_binding_calls(value: TpyExpr | None
                        ) -> 'list[TpyExpr] | None':
    """The generator or coroutine calls one binding may take its frame from
    -- each operand the value may be (`Trace.leaves`: both arms of a
    ternary, a walrus's value; an and/or select is no frame) when every one
    builds a frame (`frame_call`) -- or None for any other shape, whose
    borrows are not traced."""
    if value is None:
        return None
    calls = ternary_operands(value)
    return (calls if all(frame_call(node) is not None for node in calls)
            else None)


def frame_binding_fact(value: TpyExpr | None, ctx: 'SemanticContext'
                       ) -> 'tuple[frozenset[str | None], bool] | None':
    """What one binding's frame borrows: per operand the frame keeps
    (`frame_operands`), its single proven storage root, or None for a
    temporary; and whether the binding is one plain call. A `*args` pack is
    a temporary only when its elements name more than one root: `packs(a)`
    and `packs(a, a)` root at `a` although the pack array is a temporary of
    the statement (BUGS.md#frame-pack-temp-rooted-at-element). None
    when the shape is not traced -- an operand that borrows storage no
    single root names (a walrus, a borrow-returning call) among them."""
    calls = frame_binding_calls(value)
    if calls is None:
        return None
    # A coerced argument (a list passed as a Span) borrows the storage under
    # the coercion.
    operands = [peel_coerce(e) for c in calls
                for e in frame_operands(c) or ()]
    # A recorded fact: a guess about a pending callee is no root.
    may = MayLend(ctx)
    held = [holder_root(e, may, proven=True) for e in operands]
    roots = set(held)
    if any(r is None and not is_rvalue_source(ctx, e)
           for r, e in zip(held, operands)):
        return None
    if any(_frame_borrows_adapter(c) for c in calls):
        roots.add(None)
    return frozenset(roots), len(calls) == 1 and calls[0] is peel_coerce(value)


def _frame_borrows_adapter(call: TpyExpr) -> bool:
    """A frame that keeps a `@dynamic` protocol parameter by reference
    borrows the adapter the call site wraps its argument in -- a temporary
    of that statement, not the argument."""
    if isinstance(call, TpyGeneratorExpression):
        call = call.frame_creation
    fi = getattr(call, "resolved_function_info", None)
    if fi is None:
        return False
    borrowed = fi.root.return_borrows_from
    return any(0 <= i < len(fi.params) and is_dyn_protocol(unwrap_readonly(
                   unwrap_ref_type(fi.params[i].type)))
               for i in (borrowed or ()))


def record_frame_binding_roots(
        ctx: 'SemanticContext', name: str,
        fact: 'tuple[frozenset[str | None], bool] | None',
        node: TpyExpr | TpyStmt) -> None:
    """Union one binding into the local's per-function fact, and reject the
    shape a generator or async body cannot hold across a suspension: a
    temporary outside a plain call (only a plain call's temporaries are
    lifted into the frame ahead of the local). A frame held across a loop's
    back edge is `loop_frames`'s."""
    fact = _through_stable_loop_vars(ctx, fact)
    ctx.func.frame_binding_nodes.setdefault(name, []).append(node)
    table = ctx.func.frame_local_roots
    if name not in table:
        table[name] = fact
    else:
        old = table[name]
        table[name] = (None if old is None or fact is None
                       else (old[0] | fact[0], old[1] and fact[1]))
    func = ctx.func.current_function
    if fact is None or not (getattr(func, "is_generator", False)
                            or getattr(func, "is_async", False)):
        return
    roots, plain = fact
    if not plain and None in roots:
        raise ctx.error(
            f"cannot bind generator '{name}' from this expression inside a "
            f"generator or async function: it borrows a temporary, which "
            f"lives only until the next yield or await; bind the temporary "
            f"to a name first, or bind each generator with an assignment of "
            f"its own",
            node)


def _through_stable_loop_vars(
        ctx: 'SemanticContext',
        fact: 'tuple[frozenset[str | None], bool] | None'
) -> 'tuple[frozenset[str | None], bool] | None':
    """Read a borrowed loop variable of an enclosing loop over a container
    named by a plain name as that container: the variable refers to one of
    its elements, which stay where they are while the loop moves on, so the
    frame's storage is the container's, and a rebind of that name is one
    the alias-rebind pass sees. A container named by a field keeps the
    variable's own name: anything may replace the field while the frame is
    open (a store after the loop, a callee). So does a variable the loop
    body rebinds."""
    if fact is None:
        return None
    roots, plain = fact
    loops = [lp for lp in reversed(ctx.func.compound_stack)
             if isinstance(lp, TpyForEach)]
    out: set[str | None] = set()
    for root in roots:
        for loop in loops:
            src = loop.iterable
            while isinstance(src, TpyCoerce):
                src = src.expr
            if (root is None or loop.var != root
                    or not isinstance(src, TpyName)
                    or not _stable_loop_source(ctx, loop)
                    or root in loop_bindings_of(ctx.loop_bindings, loop,
                                                ctx.write_summaries).body):
                continue
            root = src.name
        out.add(root)
    return frozenset(out), plain


def _stable_loop_source(ctx: 'SemanticContext', loop: TpyForEach) -> bool:
    """A loop over a container named by a name or a field: its variable
    refers into storage the loop does not rebuild."""
    src = loop.iterable
    while isinstance(src, TpyCoerce):
        src = src.expr
    src_t = ctx.get_expr_type(src)
    src_t = (unwrap_readonly(unwrap_ref_type(src_t))
             if src_t is not None else None)
    return (isinstance(src, (TpyName, TpyFieldAccess))
            and src_t is not None
            and not getattr(src_t, "is_protocol", False)
            and not isinstance(src_t, ConcreteFrameType))


class BorrowKind(Enum):
    """Kind of borrow relationship between a borrower and its storage."""
    ALIAS = "alias"       # whole-container alias (safe through mutations)
    FIELD = "field"       # field-level reference
    ITER = "iter"         # for-loop iterator
    ELEMENT = "element"   # subscript element reference
    PTR = "ptr"           # pointer into storage
    # Possible borrow of unknown shape: the callee's body (and so its
    # return_borrows_from fact) is not analyzed yet, so the bind is assumed
    # to borrow. Gates auto-move of the storage like any borrow, but carries
    # no element/alias semantics -- it must not feed mutation-invalidation
    # warnings or storage-identity (alias) chains.
    OPAQUE = "opaque"


# Restrictiveness order: PTR/ITER/ELEMENT > FIELD/OPAQUE > ALIAS. Used by
# retarget logic to promote a chained borrow to the most-restrictive kind in
# the chain (an ALIAS of an ELEMENT borrower is invalidated by structural
# mutation of the source container) and by FlowFacts borrow merging.
BORROW_KIND_RANK: dict[BorrowKind, int] = {
    BorrowKind.ALIAS: 0,
    BorrowKind.FIELD: 1,
    BorrowKind.OPAQUE: 1,
    BorrowKind.ITER: 2,
    BorrowKind.ELEMENT: 3,
    BorrowKind.PTR: 3,
}

# The kind a binding's loan takes from the shape it was spelled from: a
# subscript loans the ELEMENT, a field access the FIELD, anything else the
# whole storage.
SHAPE_BORROW_KIND: dict[KeyShape, BorrowKind] = {
    KeyShape.ELEMENT: BorrowKind.ELEMENT,
    KeyShape.FIELD: BorrowKind.FIELD,
    KeyShape.WHOLE: BorrowKind.ALIAS,
}

# The kinds a STRUCTURAL mutation (reallocation, insertion, deletion) can
# invalidate: they point INTO the storage, where ALIAS and FIELD name the
# storage itself and survive. OPAQUE is out for its own reason (see its
# definition above): its shape is unknown, so it carries no element or alias
# semantics and must not feed an invalidation warning at all. Spelled once so
# the questions asked of it -- "is anything invalidatable borrowed here"
# (whole-container mutation, slice assignment) and "which element loan does
# this write hit" -- cannot drift apart.
INVALIDATING_BORROW_KINDS: tuple[BorrowKind, ...] = (
    BorrowKind.ITER, BorrowKind.ELEMENT, BorrowKind.PTR)


@dataclass(frozen=True, slots=True)
class LoanInfo:
    """One loan: what shape it has, and whether it is held on an ELEMENT of
    the storage it is filed under rather than on that storage itself. Which
    storage generation it sits in is not tracked here -- `sema.alias_rebind`
    replays the registrations in program order and answers that itself.

    `on_element` is the one hop of the borrowed place the key cannot spell.
    It decides which mutations of the storage clobber the loan: a write into
    a SIBLING element (`rows[j].append(x)`, `rows[j] = ...`) destroys what an
    element loan points into, while a loan on the container itself survives
    it -- `for row in rows: rows[0].append(x)` is sound and must stay quiet.

    `elem_index` is which element, as `element_index_key` spells it. It
    answers the one question the storage key cannot: could the mutated
    element BE the borrowed one. `element_index_relation` decides; a
    DISTINCT answer is no conflict, an UNKNOWN one is a conflict the
    diagnostic must report as possible rather than certain.

    `through_pointer` says the borrower holds what an element of the storage
    POINTS AT (a tuple parameter's borrowed element is a `T*` to the
    caller's object), not a place inside the storage: a write through the
    borrower still writes through the storage, but moving the storage away
    leaves the borrower valid, so the loan does not gate a move of it.
    """
    kind: BorrowKind
    on_element: bool = False
    elem_index: tuple[str, int | str] | None = None
    through_pointer: bool = False


def _loan_mutation_warning(place: str | StorageKey, cause: str, *,
                           iterating: bool,
                           certain: bool = True) -> str:
    """The text of the borrow-invalidation warning for a mutation of ``place``.

    One speller behind every site (a mutating method on a container or on
    an element receiver, element assignment, element and name aug-assign,
    slice and field assignment, `del`), so the branches -- an iteration in
    progress versus any other live borrow -- can only be worded one way.
    ``cause`` is what performed the mutation, already quoted where it is a
    source spelling ("'append'", "'+='", "element assignment").

    Sites never call it: they go through `storage_loan_mutation_warning`
    (a write to the whole storage) or `element_loan_mutation_warning` (a
    write into an element), which derive ``iterating`` and ``certain``
    from the tracker, so no site can pass its own answer.

    ``iterating`` is whether the loan's HOLDER is a running iterator
    (`IterHolder`), never its kind: a named handle holds ITER loans too, and
    `combine_loan_info` hands an iterator's loan a higher-ranked kind when a
    rebind retargets it upstream.

    ``certain`` is False when the mutated place only MAY be the borrowed one
    (`IndexRelation.UNKNOWN`). The warning still fires -- the program may be
    wrong -- but it may not claim the mutation hits the borrowed element,
    since that is exactly what was not proven. Only the element-hop callers
    pass it. The fourth wording (a named holder, uncertain index) needs a
    named element loan, which only a generator expression bound to a name
    files (`frame_capture_loans`); such a binding does not lower yet, so
    sema emits it but no compiling program shows it.
    """
    if iterating:
        if certain:
            return (f"Mutation of '{place}' while iterating over it"
                    f" ({cause} invalidates the iterator)")
        return (f"Mutation of '{place}' may hit the element being iterated"
                f" ({cause} may invalidate the iterator)")
    if certain:
        return (f"Mutation of '{place}' while borrowed"
                f" ({cause} may invalidate references)")
    return (f"Mutation of '{place}' may hit a borrowed element"
            f" ({cause} may invalidate references)")


class ElementHit(NamedTuple):
    """An `element_hop_loan` answer: the loan a write into an element may
    clobber, who holds it, and how sure the hit is."""
    holder: Holder
    loan: LoanInfo
    relation: IndexRelation

    @property
    def iterating(self) -> bool:
        return isinstance(self.holder, IterHolder)

    @property
    def certain(self) -> bool:
        return self.relation is IndexRelation.SAME


def element_loan_mutation_warning(
        place: str | StorageKey, cause: str, hit: ElementHit) -> str:
    """`_loan_mutation_warning` worded from an `element_hop_loan` answer.

    Which of the four wordings an element hop takes is a property of the
    hit, not of the mutating statement, so the mapping lives next to the
    speller instead of at each write site: a site that re-derived it could
    claim an iteration for a named holder, or claim certainty the index
    relation did not prove.
    """
    return _loan_mutation_warning(place, cause, iterating=hit.iterating,
                                  certain=hit.certain)


def storage_loan_mutation_warning(
        bt: BorrowTracker, storage: StorageKey, cause: str) -> str:
    """`_loan_mutation_warning` for a write to the whole of ``storage``,
    worded from the loans the tracker holds on it: "while iterating" when a
    running iterator (`BorrowTracker.has_iter_borrow`) is among them, the
    borrowed wording otherwise. Always certain -- the write hits the whole
    storage, whichever place inside it a loan points at.
    """
    return _loan_mutation_warning(storage, cause,
                                  iterating=bt.has_iter_borrow(storage))


def combine_loan_info(a: LoanInfo, b: LoanInfo) -> LoanInfo:
    """The more dangerous of two loans on the same (storage, borrower) pair,
    by BORROW_KIND_RANK. `a` wins a rank tie, which is what both callers --
    a branch-merge dedup and a retarget onto an existing loan -- want.
    """
    winner = (b if BORROW_KIND_RANK[b.kind] > BORROW_KIND_RANK[a.kind]
              else a)
    # The element hop is a property of the PLACE, not of the kind, so it
    # survives whichever kind wins: one of the two loans points into an
    # element, and the merged loan is clobbered by everything either was.
    # For the same reason the merged loan may name an element only when the
    # legs that point into an element named the same one; otherwise it stands
    # for two places and must answer with no index at all. A leg on the whole
    # container (an ITER loan without the hop, an ALIAS) makes no element
    # claim and leaves the index alone; an ELEMENT or PTR leg points into an
    # element it does not index, so it keeps the merged index unknown. So a
    # merged element loan can carry any kind and no index: every consumer of
    # `on_element` must handle an UNKNOWN index relation whatever the kind
    # says.
    on_element = a.on_element or b.on_element
    indices = {leg.elem_index for leg in (a, b)
               if leg.on_element or leg.kind in (BorrowKind.ELEMENT, BorrowKind.PTR)}
    elem_index = indices.pop() if len(indices) == 1 else None
    # A leg into the storage itself pins it, whichever leg wins.
    through_pointer = a.through_pointer and b.through_pointer
    if (on_element != winner.on_element or elem_index != winner.elem_index
            or through_pointer != winner.through_pointer):
        winner = replace(winner, on_element=on_element, elem_index=elem_index,
                         through_pointer=through_pointer)
    return winner


class BorrowTracker:
    """Tracks borrow relationships between variables for mutation safety.

    Manages which variables borrow storage from other variables, enabling
    conflict detection when borrowed storage is mutated.

    Loans live in one record per ``(storage, borrower)`` pair, indexed by
    storage because nearly every query asks "who borrows this name".

    Note: string view source tracking (str_source_borrows) and loop var
    mutation tracking (mutated_loop_vars) remain on SemanticContext since
    they reference string deduction and loop optimization state respectively.
    TODO: consider unifying all borrow-like tracking here if a broader
    "deferred type tracking" system emerges.
    """

    __slots__ = ('loans', 'current_stmt', 'stmt_loans')

    def __init__(self) -> None:
        self.loans: dict[StorageKey, dict[Holder, LoanInfo]] = {}
        # The statement sema is analyzing (set by the statement dispatcher),
        # and every loan registered while it was current, as
        # (storage key, holder, LoanInfo) per statement -- what the
        # alias-rebind storage pass replays in program order after the walk.
        self.current_stmt: 'TpyStmt | None' = None
        self.stmt_loans: IdentityMap = IdentityMap()

    def reset(self) -> None:
        """Clear all borrow state (called between function analyses)."""
        self.loans.clear()
        self.current_stmt = None
        self.stmt_loans.clear()

    def add_borrow(self, storage: StorageKey, borrower: Holder,
                   kind: BorrowKind = BorrowKind.ALIAS,
                   *, on_element: bool = False,
                   elem_index: 'tuple[str, int | str] | None' = None,
                   merge: bool = False, through_pointer: bool = False) -> None:
        """Record that ``borrower`` borrows from ``storage`` (or, with
        ``on_element``, from the element ``elem_index`` of it; with
        ``through_pointer``, from what that element points at).

        The for-each ITER registration is the only site that sets
        ``on_element`` directly, but it is not the only way a loan acquires
        the hop: `combine_loan_info` carries it onto whichever kind wins a
        merge, so an ELEMENT or PTR loan can be on an element too, and with
        no index when the merged legs named different ones.

        With ``merge``, a loan the borrower already holds on the storage is
        combined with the new one (`combine_loan_info`) rather than replaced,
        so a weaker loan never downgrades a stronger one.
        """
        assert isinstance(storage, StorageKey), storage
        loan = LoanInfo(kind, on_element, elem_index, through_pointer)
        holders = self.loans.setdefault(storage, {})
        if merge and borrower in holders:
            loan = combine_loan_info(holders[borrower], loan)
        holders[borrower] = loan
        if self.current_stmt is not None:
            self.stmt_loans.setdefault(self.current_stmt, []).append(
                (storage, borrower, loan))

    def remove_borrower(self, borrower: Holder) -> None:
        """Remove all borrows held by ``borrower`` (e.g. on reassignment)."""
        to_clean: list[StorageKey] = []
        for storage, holders in self.loans.items():
            if holders.pop(borrower, None) is not None and not holders:
                to_clean.append(storage)
        for storage in to_clean:
            del self.loans[storage]

    def rebind_borrower(self, borrower: str, value: TpyExpr | None) -> None:
        """Release old binding loans unless the binding retains itself."""
        if isinstance(value, TpyName) and value.name == borrower:
            return
        self.retarget_storage_borrows(borrower)
        self.remove_borrower(borrower)

    def retarget_storage_borrows(self, name: str) -> None:
        """Reassignment of ``name``: retarget its storage's borrowers to the
        upstream source.

        The reassigned variable is generated as a ``T*`` pointer-local in C++,
        so any borrower like ``view = name`` aliases whatever ``name``
        currently points into. After the reassignment we want chains to keep
        tracking the original container, not break silently.

        The retargeted borrow kind is the most-restrictive in the chain
        (PTR/ITER/ELEMENT > FIELD > ALIAS): an ALIAS of an ELEMENT borrower
        is still invalidated by structural mutation of the source container.

        Field-path borrows (``name.*``) refer to the old object's fields
        and are dropped unconditionally -- the variable will be rebound to a
        different object, so those paths are unreachable.

        If ``name`` itself was not borrowing anything (no upstream chain),
        its borrowers had nowhere to retarget to and are dropped.
        """
        upstream = self.borrow_source(name)
        up_loan = self.loans.get(upstream, {}).get(name) if upstream else None

        holders = self.loans.pop(StorageKey.of_name(name), None)
        if holders and up_loan is not None and upstream is not None:
            for b, child in holders.items():
                # The retargeted loan reaches the upstream object THROUGH the
                # rebound name, so it is at least as clobberable as either leg.
                chained = combine_loan_info(child, up_loan)
                # An OPAQUE borrow has no element/alias shape to promote:
                # rank-promoting it into the invalidating set would mint
                # exactly the mutation warnings the kind exists to avoid.
                if child.kind is BorrowKind.OPAQUE:
                    chained = LoanInfo(BorrowKind.OPAQUE)
                existing = self.loans.get(upstream, {}).get(b)
                if existing is not None:
                    chained = combine_loan_info(existing, chained)
                self.loans.setdefault(upstream, {})[b] = chained

        # Field-path borrows (name.X) are dropped: the variable rebinds to
        # a different object, so member-path borrows are unreachable.
        for k in [k for k in self.loans if k.root == name and k.members]:
            del self.loans[k]

    def has_iter_borrow(self, storage: StorageKey) -> bool:
        """Check if a storage is borrowed by a running loop or
        comprehension iterator."""
        return any(isinstance(b, IterHolder)
                   for b in self.loans.get(storage, {}))

    def has_element_borrow(self, storage: StorageKey) -> bool:
        """Check if a variable has element-level or iterator borrows.

        Returns True for borrows that can be invalidated by structural
        mutations (reallocation, insertion, deletion). Returns False for
        whole-container alias borrows which are safe through mutations.
        """
        return any(loan.kind in INVALIDATING_BORROW_KINDS
                   for loan in self.loans.get(storage, {}).values())

    def element_hop_loan(
            self, storage: StorageKey,
            index: 'tuple[str, int | str] | None' = None
    ) -> ElementHit | None:
        """The invalidating loan held on an element of ``storage`` that the
        element ``index`` may BE, with its holder and how sure that is.

        A write into the borrowed element of ``storage`` -- replacing it, or
        calling a reallocating method on it -- destroys what such a loan
        points into. ``index`` is the mutated element's `element_index_key`;
        a DISTINCT relation is no conflict and is skipped, and the returned
        relation (SAME or UNKNOWN) is what the diagnostic must not overstate.
        A loan on ``storage`` ITSELF is not returned; it survives an element
        write, which is why the plain `has_element_borrow` answer cannot
        stand in.

        Of several matching loans the strongest is the answer: a certain hit
        over a possible one, then an iterator's (the iteration it breaks)
        over a named holder's -- never whichever was filed first.
        """
        best: ElementHit | None = None
        for holder, loan in self.loans.get(storage, {}).items():
            if loan.on_element and loan.kind in INVALIDATING_BORROW_KINDS:
                rel = element_index_relation(loan.elem_index, index)
                if rel is IndexRelation.DISTINCT:
                    continue
                hit = ElementHit(holder, loan, rel)
                if best is None or ((hit.certain, hit.iterating)
                                    > (best.certain, best.iterating)):
                    best = hit
        return best

    def has_borrow_of_kinds(self, storage: StorageKey,
                            kinds: tuple[BorrowKind, ...]) -> bool:
        """Check if any borrower of storage has one of the given borrow kinds."""
        return any(loan.kind in kinds
                   for loan in self.loans.get(storage, {}).values())

    def has_borrowers_outside(self, name: str, known_aliases: set[str]) -> bool:
        """Any borrower of ``name``'s storage (its member paths included)
        not in ``known_aliases``. Bind-based aliases are modeled by
        last-use liveness itself (with dead-alias precision); every other
        borrower -- call-result borrows recorded from return_borrows_from,
        a running iterator (`IterHolder`) -- is invisible to liveness, so
        moving ``name`` is unsound while one exists. The iterator loan
        expires with its loop, so a consume after the loop (and the loop's
        own consuming-iteration decision, taken there) is not gated by it.

        Liveness sees a known alias only as a name, not what borrows through
        it (`ys = xs; for x in ys:` files the loan on `ys`), so the known
        aliases' own borrowers are asked too. A loan held through a
        pointer element (`LoanInfo.through_pointer`) is not on the storage.
        """
        seen: set[str] = set()
        todo = [name]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for key, holders in self.loans.items():
                if key.root != current:
                    continue
                for b, loan in holders.items():
                    if loan.through_pointer:
                        continue
                    if b not in known_aliases:
                        return True
                    todo.append(b)
        return False

    def effective_storage(self, name: str) -> StorageKey:
        """Resolve alias chains to find the underlying storage.

        If ``name`` is an alias of another variable, follows the chain
        (e.g. b -> a -> items) and returns the root storage.  Returns
        ``name`` itself when it is not an alias borrower.
        """
        key = StorageKey.of_name(name)
        visited: set[StorageKey] = {key}
        # A member path borrows nothing: the chain ends at one.
        current: str | None = name
        while current is not None:
            found = None
            for storage, holders in self.loans.items():
                loan = holders.get(current)
                if loan is not None and loan.kind is BorrowKind.ALIAS:
                    found = storage
                    break
            if found is None or found in visited:
                break
            visited.add(found)
            key = found
            current = found.as_name()
        return key

    def resolve_obj_storage(self, obj: TpyExpr) -> StorageKey | None:
        """The storage key a mutation target's OBJECT is filed under.

        A bare name goes through the alias chain; a one-hop field path is its
        own key, since field paths are never aliased in the tracker. One
        answer for every caller that has to turn a receiver into a loan key --
        a second spelling of it drifts from the alias resolution.
        """
        if isinstance(obj, TpyName):
            return self.effective_storage(obj.name)
        return storage_key(obj)

    def borrow_kind_of(self, name: str) -> 'BorrowKind | None':
        """Return the kind of borrow that 'name' holds, or None if not a borrower."""
        for holders in self.loans.values():
            loan = holders.get(name)
            if loan is not None:
                return loan.kind
        return None

    def is_deferred_borrow(self, name: str) -> bool:
        """Return True if name ultimately traces back to a deferred ELEMENT borrow.

        A variable is deferred if it is an ELEMENT borrow, or an ALIAS/FIELD borrow
        of a deferred variable (transitively). PTR/ITER borrows are not deferred.
        Termination is guaranteed because borrow chains are acyclic.
        """
        kind = self.borrow_kind_of(name)
        if kind == BorrowKind.ELEMENT:
            return True
        if kind in (BorrowKind.ALIAS, BorrowKind.FIELD):
            source = self.borrow_source(name)
            src_name = source.as_name() if source is not None else None
            if src_name is not None:
                return self.is_deferred_borrow(src_name)
        return False

    def borrow_source(self, name: str) -> StorageKey | None:
        """Return the direct borrow source of name (any borrow kind), or None.

        Unlike effective_storage (ALIAS-only), this finds the container for
        ITER/ELEMENT/FIELD/PTR borrows too -- used to trace loop-var addresses
        back to the source container.
        """
        for storage, holders in self.loans.items():
            if name in holders:
                return storage
        return None

    def all_storage_through_borrows(self, name: str, *,
                                    proven_only: bool = False,
                                    cross_handles: bool = True
                                    ) -> list[StorageKey]:
        """Every ultimate storage `name` can alias, following ALL borrow chains
        (ALIAS + ELEMENT + FIELD + PTR + OPAQUE), nearest chain first.

        Unlike effective_storage (ALIAS-only), this traverses the full chain,
        so a write through an element ref traces back to its source param:
        w ALIAS-borrows v, v ELEMENT-borrows items -> [items].

        A binding usually holds ONE loan and yields one root. A RE-SEATED one
        holds a loan per source (a `match` capture a nested match rebinds, an
        alias reassigned from a second container) and a use of it reaches
        whichever is live, so every consumer that attributes a use back to
        storage needs all of them, not the one that happens to come first.

        A root is a REACHABLE node that has no source of its own -- the
        has-sources test must not be filtered by `visited`, or two loan paths
        converging on one already-expanded node would report the intermediate
        alias as a root and hand an ALL-roots consumer a name that is not
        storage at all. `visited` therefore gates only re-expansion.

        Depth-first in loan-registration order, so the head of the list is the
        nearest chain's root. Empty when `name` borrows nothing, and also when
        every reachable node sits on a cycle (no node without sources) -- both
        leave the answer to the caller's `roots_or_self` fallback.

        `proven_only` leaves OPAQUE loans out: they stand for a callee whose
        borrow fact does not exist yet, so a consumer RECORDING a fact of its
        own from the walk would bake the assumption in for good. Each
        reachable node is expanded once and an expansion scans the edge set,
        so O(V*E) in the per-function borrow graph, iterative (no recursion
        depth tied to chain length).

        `cross_handles=False` stops at an ITER loan -- a HANDLE into the
        storage (an iterator object bound off `zip(xs, ys)`, a generator
        frame): what happens to the handle itself (an advance) is not a
        write into what it points at, so a consumer attributing a mutation
        OF a name, rather than one THROUGH it, must not climb past one. The
        handle is then a dead end, never a root.
        """
        start = StorageKey.of_name(name)
        visited: set[StorageKey] = {start}
        roots: list[StorageKey] = []
        stack: list[StorageKey] = [start]
        while stack:
            current = stack.pop()
            # A member path borrows nothing: it is always a root.
            holder = current.as_name()
            sources = [] if holder is None else [
                storage for storage, holders in self.loans.items()
                if holder in holders
                and not (proven_only
                         and holders[holder].kind is BorrowKind.OPAQUE)]
            if not sources:
                if current != start:
                    roots.append(current)
                continue
            if not cross_handles:
                sources = [src for src in sources
                           if self.loans[src][holder].kind is not BorrowKind.ITER]
            for src in reversed(sources):
                if src not in visited:
                    visited.add(src)
                    stack.append(src)
        return roots

    def storage_roots_or_self(self, name: str, *,
                              cross_handles: bool = True
                              ) -> list[StorageKey]:
        """`all_storage_through_borrows`, falling back to `[name]` when the
        name borrows nothing (or resolves to nothing) -- so a consumer that
        must name SOME storage for every binding gets one answer shape."""
        return (self.all_storage_through_borrows(name, cross_handles=cross_handles)
                or [StorageKey.of_name(name)])

    def canonical_key(self, key: StorageKey) -> StorageKey:
        """``key`` re-rooted at the storage its root borrows from.

        One buffer must have ONE key. After ``m = o.mid``, ``m.inner.tag`` and
        ``o.mid.inner.tag`` name the same field, and the prefix match that demotes
        a view sees them as one only when both are spelled from real storage.
        """
        srcs = self.all_storage_through_borrows(key.root)
        return key.rebase(srcs[0]) if srcs else key

    def freeze(self) -> frozenset[tuple[StorageKey, Holder, LoanInfo]]:
        """Snapshot loan state as immutable triples for flow analysis."""
        return frozenset(
            (storage, borrower, loan)
            for storage, holders in self.loans.items()
            for borrower, loan in holders.items()
        )

    def restore_from_frozen(
            self, triples: frozenset[tuple[StorageKey, Holder, LoanInfo]]) -> None:
        """Restore loan state from a frozen snapshot."""
        self.loans.clear()
        for storage, borrower, loan in triples:
            self.loans.setdefault(storage, {})[borrower] = loan


class EphemeralKind(Enum):
    """How strongly an ephemeral-borrow name is held.

    HARD: the name borrows a producer step slot at EVERY instantiation, so
    retaining it past the step is rejected outright.
    GATE_ONLY: the name borrows only at the REFERENCE instantiations of an
    open `T` (the step result is a copy at the value ones), so it cannot be
    rejected -- it only feeds the generic-yield slot verdict, which answers
    with a slot choice instead of a diagnostic.
    """
    HARD = "hard"
    GATE_ONLY = "gate_only"


def ephemeral_borrow_root(ephemeral_vars: 'dict[str, EphemeralKind]',
                          expr: 'TpyExpr | None',
                          kind: 'EphemeralKind | None' = None) -> str | None:
    """Return the ephemeral-borrow var name `expr` reads from, else None.

    A bare ephemeral name, a walrus handing one out, a select picking one,
    or a field/subscript chain rooted in one (storing `x.field` retains a
    borrow into the same stale slot): the HOLDER climb, following no call
    (`NO_CALLS`) -- a `.clone()` / copy-producing call breaks the borrow,
    and whether a lending call keeps it is filed undecided.

    `kind` restricts the match to names held that strongly; None matches
    either kind (the generic-yield gate's question -- does this source
    borrow at all).
    """
    if expr is None:
        return None
    # A ternary reads from whichever arm is taken -- ephemeral if either is.
    for place in storage_roots(expr, Use.HOLDER, None, NO_CALLS).places:
        held = ephemeral_vars.get(place.name)
        if held is not None and (kind is None or held is kind):
            return place.name
    return None


def register_binding_borrow(ctx: 'SemanticContext', name: str,
                            init_expr: TpyExpr) -> None:
    """Register `name` as a borrower of `init_expr`'s storage root, with the
    kind and the eager-vs-deferred mutation mark taken from the init shape
    (see `_register_source_borrow`). Shared by the VarDecl and walrus binding
    paths."""
    _register_source_borrow(ctx, name, init_expr, kind=None)


def register_capture_alias_borrow(ctx: 'SemanticContext', name: str,
                                  subject_expr: TpyExpr) -> None:
    """Register a `match`-arm capture as a borrower of the matched storage,
    for mutation ATTRIBUTION only.

    A write through the capture has to climb back to the subject's root, and
    `mark_param_mutated`'s climb reads the borrow graph -- so the loan has to
    be in it, or a mutated param keeps its non-mutating verdict. The kind is
    forced to OPAQUE rather than taken from the subject shape (FIELD/ELEMENT,
    which is what the sibling `register_binding_borrow` picks): the
    dangling-binding hazard of a field/element subject is owned by the
    arm-scoped `_warn_arm_subject_mutation`, so handing the same loan to the
    extent-blind invalidation warnings reports the hazard a second time inside
    the arm and a spurious third time for a legitimate mutation AFTER the
    match, where the binding is dead but the loan is not (docs/IR_DESIGN.md,
    extent-scoped loans).
    """
    _register_source_borrow(ctx, name, subject_expr, kind=BorrowKind.OPAQUE)


def _register_source_borrow(ctx: 'SemanticContext', name: str,
                            source_expr: TpyExpr,
                            kind: BorrowKind | None) -> None:
    """Register `name` as a borrower of `source_expr`'s storage root.

    `kind=None` takes the kind from the init shape (ELEMENT / FIELD / ALIAS)
    and applies the deferral rule below; a caller that passes a kind forces it
    and always defers, because it wants the loan recorded without the init
    shape's invalidation semantics.

    A self-assignment (t = t) aliases nothing new; registering it would put
    a self-edge in the borrow graph. A ternary registers one loan per keyed
    arm (`select_arm_keys`), the strongest kind per key: either arm can be
    the live borrow. For the remaining non-simple shapes (deep chains
    like `outer.inner[i]`) there is no root to record, so every address-taken
    root is eagerly marked mutated instead (the binding aliases into them, so
    they must stay `T&`, not `const T&`).

    8a.5: marking the source mutated is DEFERRED until the borrower is
    actually written through for ELEMENT borrows (v = items[i]) and for ALIAS
    borrows (b = y) -- a read-only alias must not force its source mutable. A
    genuine write through the borrower (b.x = 1, or passing b to a mutating
    callee) re-marks the source via mark_param_mutated's full borrow-chain
    follow, so deferral stays sound. FIELD borrows defer only when their root
    traces back to an ELEMENT borrow (checked transitively); PTR/ITER borrows
    and field aliases not rooted at an ELEMENT mark their KEY immediately.
    For a member key (`y = o.inner`) that mark reaches nothing -- the key is
    no parameter, borrows nothing, and a plain mark does not climb its member
    hop -- so only a write through the borrower marks `o`
    (BUGS.md#field-alias-eager-mark-inert).
    """
    bt = ctx.func.borrow_tracker
    arm_keys = select_arm_keys(source_expr)
    if arm_keys is not None:
        strongest: dict[StorageKey, BorrowKind] = {}
        for arm in arm_keys:
            arm_kind = SHAPE_BORROW_KIND[arm.shape]
            prev = strongest.get(arm.key)
            if prev is None or BORROW_KIND_RANK[arm_kind] > BORROW_KIND_RANK[prev]:
                strongest[arm.key] = arm_kind
        for key, arm_kind in strongest.items():
            if key.as_name() != name:
                bt.add_borrow(key, name, kind or arm_kind)
        # The arms stay eagerly marked mutated rather than deferred: a select
        # has no single root that a later write through the borrower could
        # re-mark, so the conservative mark is the only one it gets.
        mark_roots(ctx, storage_roots(source_expr, Use.PLACE,
                                      ctx.get_expr_type, MayLend(ctx)),
                   MarkRequest())
        return
    lk = loan_key(source_expr)
    if lk is Unkeyed.NOT_A_PLACE:
        # A call result's loans are the call's (`_register_call_result_borrow`).
        return
    if lk is Unkeyed.UNPLACEABLE:
        mark_roots(ctx, storage_roots(source_expr, Use.PLACE,
                                      ctx.get_expr_type, MayLend(ctx)),
                   MarkRequest())
        return
    if lk.key.as_name() == name:
        return
    if kind is not None:
        bt.add_borrow(lk.key, name, kind)
        return
    init_kind = SHAPE_BORROW_KIND[lk.shape]
    # A tuple's elements are distinct objects, owned or borrowed one by one,
    # so the loan names the element the binding points into.
    obj_t = (ctx.get_expr_type(lk.index.obj) if lk.index is not None
             else None)
    elem_index = (element_index_key(lk.index.index)
                  if obj_t is not None and isinstance(
                      unwrap_readonly(unwrap_ref_type(obj_t)), TupleType)
                  else None)
    bt.add_borrow(lk.key, name, init_kind, elem_index=elem_index)
    key_name = lk.key.as_name()
    if not (init_kind in (BorrowKind.ELEMENT, BorrowKind.ALIAS)
            or key_name is not None and bt.is_deferred_borrow(key_name)):
        mark_roots(ctx, [lk.key], MarkRequest())


def holds_generator_object(t: 'TpyType | None') -> bool:
    return t is not None and isinstance(
        unwrap_readonly(unwrap_ref_type(t)), ConcreteGenType)


def holds_frame_object(t: 'TpyType | None') -> bool:
    """A local holding a generator or coroutine object -- a concrete frame,
    or a coroutine erased behind `Cancellable` -- whose frame may borrow
    storage for as long as the object lives."""
    if t is None:
        return False
    inner = unwrap_readonly(unwrap_ref_type(t))
    if isinstance(inner, OwnType):
        inner = unwrap_readonly(inner.wrapped)
    return (isinstance(inner, ConcreteFrameType)
            or (isinstance(inner, NominalType)
                and inner.qualified_name() == qnames.CANCELLABLE))


def note_owned_local(ctx: 'SemanticContext', name: str,
                     var_type: 'TpyType | None') -> None:
    """Record that `name` owns the object it was bound to, so its last
    use may move it. A generator object never counts: its frame may have
    started by its last use, and a started frame cannot move (a second
    name aliases it instead)."""
    if holds_generator_object(var_type):
        ctx.func.owned_locals.discard(name)
        return
    ctx.func.owned_locals.add(name)
    ctx.func.ever_owned_locals.add(name)


def binds_value_copy(var_type: 'TpyType | None') -> bool:
    """Whether a local of `var_type` holds its own copy of what it is bound
    from rather than a borrow of that storage."""
    return var_type is not None and unwrap_readonly(var_type).is_value_type()


def record_stmt_borrow_binding(ctx: 'SemanticContext', name: str,
                               var_type: 'TpyType | None',
                               init_expr: TpyExpr) -> None:
    """Record a statement-level (var-decl / assign / walrus) borrow binding
    of a non-value local, with its const verdict. Codegen's branch pre-decl
    reads the accumulated fact to pick the pointer (alias) form for
    borrow-only names -- materialized here so the form discriminator is
    defined by sema, not re-derived by a codegen body walk."""
    if var_type is None or binds_value_copy(var_type):
        return
    inner = init_expr
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
    # Binding a second name to a record hands out a second write path to its
    # fields. Whether the alias is ever written through is a whole-body
    # question this bind cannot answer, so a str/bytes view borrowed out of
    # anything under the aliased storage falls back to an owned copy.
    alias_key = path_key(inner)
    if alias_key is not None:
        ctx.mark_all_view_borrowers_mutated(alias_key)
    # A call's result is readonly exactly when its declared return says so,
    # which is the type sema gave the expression.
    const = isinstance(ctx.get_expr_type(inner), ReadonlyType)
    record_borrow_binding(ctx, name, const=const, at=init_expr)


def record_borrow_binding(ctx: 'SemanticContext', name: str, *,
                          const: bool, at: 'TpyExpr | TpyStmt | None') -> None:
    """Record a borrow binding of a non-value local whose const verdict the
    caller already knows.

    For bindings that have no statement-level init expression to inspect -- a
    `with` target borrows `__enter__()`'s result, which is a property of the
    method, not of an expression in the body.
    """
    prev = ctx.func.stmt_borrow_decls.get(name, False)
    ctx.func.stmt_borrow_decls[name] = prev or const
    note_nonowning_bind(ctx, name, at)


@dataclass(frozen=True, slots=True)
class NonOwningBind:
    """What a local's bindings to storage that already exists add up to,
    for the whole function (`FunctionTrackingState.nonowning_binds`): the
    line of the first one, and what they borrow -- None for the whole
    value, else the positions of the tuple elements some binding borrows.
    A local with no such binding is owning."""
    line: int | None
    elems: frozenset[int] | None


def note_nonowning_bind(ctx: 'SemanticContext', name: str,
                        at: 'TpyExpr | TpyStmt | None',
                        elems: 'frozenset[int] | None' = None) -> None:
    """The one writer of the ownership fact: a binding of local `name` that
    makes it refer to storage that already exists, wholly (`elems` None)
    or at those tuple elements, makes it non-owning there for the whole
    function. A parameter's ownership is its declared type, never a
    binding's."""
    if ctx.is_top_level:
        return
    func = ctx.func.current_function
    if not isinstance(func, TpyFunction) or any(
            p == name for p, _ in func.params):
        return
    loc = getattr(at, "loc", None)
    line = loc.line if loc is not None else None
    prev = ctx.func.nonowning_binds.get(name)
    if prev is not None:
        line = prev.line if prev.line is not None else line
        elems = (None if prev.elems is None or elems is None
                 else prev.elems | elems)
    ctx.func.nonowning_binds[name] = NonOwningBind(line, elems)


@dataclass(eq=False)
class OwningRead:
    """A read of local `name` whose verdict waits for something the walk
    has not reached yet: a move an owning sink made out of the local on the
    ownership fact as it stood at the read (`settle_owning_reads` turns it
    into a copy if a later binding makes the local non-owning), or a copy
    warning a loop holds until its mode is known. `node` is the read (the
    subscript for an element, the loop for a consuming `for`). Each kind of
    read is its own subclass, carrying what its settlement needs."""
    name: str
    node: Any


@dataclass(frozen=True)
class SinkCopy:
    """The copy an owning sink other than an `Own` slot makes of a source
    it does not own, in that sink's own words (`own_copy.slot_message`):
    `display` copied into `dest` (a field, a container, `Any`, a tuple
    element of one) of payload `target`, or each element (`kind`
    `KIND_ELEMENTS`, remedy `hint`)."""
    display: 'TpyType'
    target: 'TpyType'
    dest: str
    kind: str = own_copy.KIND_SLOT
    hint: str = ""


@dataclass(eq=False)
class NameMove(OwningRead):
    """A whole name moved into an owning slot -- `slot`, described as
    `context` for a `return` or a `pass` (`action`); `whole_slot` False for
    one member of a literal, whose warning names it. `sink` is the copy's
    text where the slot is not an `Own` slot (None: the `Own` slot's), one
    per element kind a container copies."""
    slot: 'OwnType'
    context: str
    action: str
    whole_slot: bool = True
    sink: 'SinkCopy | tuple[SinkCopy, ...] | None' = None


@dataclass(eq=False)
class ElementMove(OwningRead):
    """Element `index` of tuple local `name` read `name[index]` and moved
    into the `Own` slot `slot` of a return (`returned`) or an argument."""
    index: int
    slot: 'OwnType'
    returned: bool


@dataclass(eq=False)
class TupleNameMove(OwningRead):
    """A tuple local read whole at a sink that takes its `Own` elements
    (those of the slot `tuple_type`): all but `copied` move out of it. A
    `warns` sink declares each copy it makes with the slot's warning; a
    consuming unpack has no copy to make (it refuses a borrowed one)."""
    tuple_type: 'TupleType'
    copied: frozenset[int]
    warns: bool = True


@dataclass(eq=False)
class ReceiverMove(OwningRead):
    """The receiver of a consuming method call (`self: Own[Self]`)."""


@dataclass(eq=False)
class OwnIterMove(OwningRead):
    """`own_iter(name)`, the `call`."""
    call: Any


@dataclass(eq=False)
class ConsumingLoop(OwningRead):
    """A `for` over the local that consumes its elements; `revert` turns it
    back into borrowing iteration."""
    revert: Callable[[], None]


@dataclass(eq=False)
class FinallyCapture(OwningRead):
    """The finally-deferred capture of a returned local (`ret`, at tuple
    member `path`; None for the whole value): the return moves it out after
    the finally chain."""
    ret: Any
    path: 'tuple[int, ...] | None'


@dataclass(eq=False)
class UnneededCopy(OwningRead):
    """`copy(name)` (the `call`) at an owning sink where the bare name
    would move: the advice to drop the copy holds only for an owning
    local."""
    call: Any


@dataclass(eq=False)
class HeldLoopCopy(OwningRead):
    """A copy warning about loop variable `name`, held until its loop's mode
    is decided: a consuming loop moves the element it describes."""
    diag: Diagnostic


class _ModuleInitSentinel:
    """Sentinel for module-level init context (not a real function, but not None either).

    Truthy so that `if ctx.func.current_function:` passes, but fails
    `isinstance(ctx.func.current_function, TpyFunction)` checks.
    """
    __slots__ = ()
    def __bool__(self) -> bool:
        return True
    def __repr__(self) -> str:
        return "<MODULE_INIT>"


MODULE_INIT_CONTEXT = _ModuleInitSentinel()


def is_body_like_scope(current_function: 'TpyFunction | _ModuleInitSentinel | None') -> bool:
    """True when the current context supports pending-type resolution.

    Real function bodies and ``MODULE_INIT_CONTEXT`` both have a
    ``FunctionTrackingState`` with ``pending_resolutions`` plumbed through
    and ``deduction.resolve_all()`` runs at the end of both (see
    ``analyzer._analyze_top_level``). Class bodies, registration-time
    contexts, and no-context states do not, so empty literals and similar
    late-typed constructs must reject there.
    """
    return isinstance(current_function, TpyFunction) or current_function is MODULE_INIT_CONTEXT


@dataclass
class RecordContext:
    """State for the record currently being analyzed (type params, bounds)."""
    record: TpyRecord | None = None
    type_params: list[str] | None = None
    type_param_kinds: list[TypeParamKind] | None = None
    type_param_bounds: dict[str, TpyType] | None = None


@dataclass(frozen=True, slots=True)
class BindingProvenance:
    """Per-local escape/ownership provenance: one record per local.

    Two merge lattices apply at flow joins (see
    flow_facts.merge_binding_provenance):

      * MUST facts (INTERSECT -- survive only if they hold on every path):
        `safe_to_return` and `param_derived`. Invariant: `param_derived`
        implies `safe_to_return` (safe is the documented superset, so an
        intersecting join that reaches via different safe sources keeps the
        local safe). Consulted by is_dangling_return / is_param_derived_expr.

      * HAZARD facts (UNION -- flagged if they hold on any path), all for
        tuple-typed locals: `owns_fresh_idx` (index of the first element that
        owns fresh non-value storage -- borrowing it across a yield/return
        dangles), `owning_storage` (bound from an owning-tuple call -- a
        borrow-form return would address into the dying local),
        `copy_into_own_idxs` (elements held by reference -- a plain borrow,
        or an owned source not moved in -- which COPY at a NAME->Own[T] slot
        and warn, the copy-into-owned analog).

    Absent name == default record by construction: callers prune all-default
    records, so a name's membership and its facts stay equivalent under both
    lattices (an absent name contributes the default to every field).
    """

    safe_to_return: bool = False
    param_derived: bool = False
    owns_fresh_idx: int | None = None
    owning_storage: bool = False
    copy_into_own_idxs: frozenset[int] = frozenset()
    # HAZARD (UNION): storage roots a borrow-form tuple local's element
    # pointers alias (terminal roots, pre-expanded at record time). The
    # mark functions trace a yield/return of the bare name through it.
    borrow_source_roots: frozenset[str] = frozenset()


_DEFAULT_PROVENANCE = BindingProvenance()


class NestedMutationMark(NamedTuple):
    """One mutation mark made inside a nested def, kept for the replay into
    the enclosing function. `receiver` is `self_names_receiver()`'s answer
    for a mark on `self`, taken where the mark was made."""
    key: StorageKey
    through_field: bool
    structural: bool
    via_element: bool
    receiver: bool


class OwnSlot(NamedTuple):
    """What a consume credits for the never-consumed warning: Own param
    `param` as a whole, or owned element `index` of tuple param `param`."""
    param: str
    index: int | None = None


class ParkedFunctionState(NamedTuple):
    """A body's function state kept for a check that runs after the body
    (`SemanticContext.reenter`): the module-end generic-yield-slot verdict,
    a pointer-tuple rebind's declared-slot check.

    `scope` and `ns` are parked beside the state because the body-exit path
    nulls them ON that state, and a site inside a block has a scope of its
    own: the verdict walk asks whether a yield source is rooted in durable
    storage, and with no scope a local that shadows a module global reads as
    the global and is wrongly certified as durable.
    """

    func: Any
    state: 'FunctionTrackingState'
    scope: Scope | None
    ns: Namespace | None


class GenexprCreation(NamedTuple):
    """A generator expression's function, the function whose body creates
    it directly (a nested def or an enclosing generator expression
    included), and the captures that read that creator's own binding --
    not one a comprehension or lambda around the expression makes."""

    func: TpyFunction
    creator: TpyFunction
    reads_creator: tuple[str, ...]


class ParkedRebind(NamedTuple):
    """A rebind's declared-slot check parked until its body's final
    pointer-tuple set exists (`TypeCompatibility.park_repointed_tuple_check`).

    `flow` is the site's flow state, which the re-run applies: the body's
    end state holds the loan this very rebind registered after its site
    check, and ownership, consumes and narrowing of a later point, any of
    which would answer the value's move question as the site did not."""

    parked: ParkedFunctionState
    name: str
    stmt: TpyVarDecl
    value_expr: TpyExpr
    value_type: TpyType
    var_type: TpyType
    context: str
    flow: FlowFacts


@dataclass(frozen=True, slots=True)
class PendingLocal:
    """A loop body's or `for` head's binding, promoted into scope by the
    first read (or binding) after the loop.

    `first_stack` is the enclosing-statement stack at the FIRST binding: the
    promotion anchors the C++ pre-declaration against it, and the read's own
    stack says how far out the declaration has to go to reach both sites.
    `head_stmt` is the `for` statement while the entry is that head's loop
    variable, None once a loop body owns it. `head_first` says the first
    binding was a `for` head -- its variable or a tuple-unpack head's
    target. A later head over a body-first local's name binds it when the
    name is live after that head's loop, a head-first one only once a read or
    binding placed its declaration; either is bound once so declared. A
    promoted body-first entry stays in the table, so its origin outlives
    the promotion.
    """

    var_type: TpyType
    first_stack: tuple[TpyStmt, ...]
    head_stmt: TpyStmt | None
    head_first: bool

    @property
    def is_body_first(self) -> bool:
        return self.head_stmt is None and not self.head_first


@dataclass
class LoopClauseEdges:
    """Every edge that leaves one loop's BODY clause, and their verdict.

    A `break` leaves past the `else` and out of the loop, a `continue`
    jumps back to the head and still reaches the `else`, and the body's
    own end falls through to the head as well -- so a name first bound in
    the clause counts as assigned after the loop only when it is bound on
    all of them. `body_assigned` is what the continue and fall-through
    edges join to, taken while the body's state is still live;
    `_finish_loop_clauses` is the one consumer of the whole record.
    """

    breaks: list[frozenset[str]] = field(default_factory=list)
    continues: list[frozenset[str]] = field(default_factory=list)
    body_assigned: frozenset[str] = frozenset()


@dataclass
class FunctionTrackingState:
    """Per-function analysis state.

    Extracted from SemanticContext to keep that class from growing unbounded.
    Reset between functions and saved/restored for nested-def isolation.
    Accessed explicitly by callers via ``ctx.func.<field>``.
    """

    # --- Analysis state (per-function) ---
    current_scope: Scope | None = None
    current_function: TpyFunction | _ModuleInitSentinel | None = None
    # The function or method whose emitted body the code under analysis lands
    # in: itself, or the one a nested def or a genexpr is written in.
    body_root: TpyFunction | None = None
    current_ns: Namespace | None = None
    loop_depth: int = 0
    # One entry per loop whose body is open, innermost last; `break` and
    # `continue` file their snapshots on the innermost. Per-loop, not
    # per-function: a nested def analyzed inside a loop body gets a fresh
    # state (and `loop_depth == 0` with it), so its own `break` cannot
    # reach this loop's record.
    loop_clause_edges: list[LoopClauseEdges] = field(default_factory=list)
    # Enclosing compound statements (if/while/for/with/try/match), outermost
    # first, while their bodies are analyzed. A loop-body-first local is
    # function-scoped in Python, so the binding's stack and the read's are
    # what place its single C++ declaration (see `_pending_decl_anchor`):
    # anchored on the loop itself it lands inside an enclosing block's scope,
    # or before an unrelated sibling loop that then redeclares the name.
    compound_stack: list[TpyStmt] = field(default_factory=list)

    # --- try/except control flow (per-function: a nested def must not
    #     inherit the enclosing function's handler context, or its
    #     @error_return calls pass the must-handle check and emit gotos
    #     to labels outside the lambda) ---
    try_except_error_type: str | None = None
    # Set by the call check when a call's @error_return failure is admitted
    # BECAUSE `try_except_error_type` matches it; the enclosing
    # `_analyze_try_return` reads it back into `TpyTry.handled_error_return`.
    try_except_error_handled: bool = False
    in_except_tier: TryTier | None = None
    # Whether the current except handler has an 'as e' binding (needed for return-tier re-raise)
    in_except_has_binding: bool = False
    # True when analyzing a finally body
    in_finally: bool = False
    # The `self` fields a consuming method's return value under analysis may
    # move out of (`StatementAnalyzer._consuming_return_move_fields`); empty
    # everywhere else, where a field read is a borrow.
    consuming_return_fields: frozenset[str] = frozenset()
    # True while a `return` statement's value is analysed: a consuming
    # method's field read there is already in a return, so "move it in a
    # `return`" is no remedy for it.
    in_return_value: bool = False
    # Stack (one frame per finally body being analyzed) of local names a
    # finally-deferred return in the corresponding try borrowed: `del` of
    # such a name inside the finally would free storage the pending return
    # still reads (CPython keeps the object alive via the stashed reference).
    # One map per enclosing finally being analysed: a name a pending
    # deferred return borrows -> (the return as a diagnostic quotes it, the
    # (return, tuple member path or None) sites that defer it).
    pending_return_borrows: list[dict[str, tuple[str, list[tuple['TpyReturn', 'tuple[int, ...] | None']]]]] = field(default_factory=list)

    # --- Container literal tracking (lists, dicts and sets alike) ---
    variable_to_literal: dict[str, int] = field(default_factory=dict)
    pending_resolutions: list[int] = field(default_factory=list)
    # Keyed by the TpyMethodCall; survives `save_function_state`'s deep
    # copy with its keys intact (see `IdentityMap.__deepcopy__`).
    pre_analyzed_method_args: IdentityMap = field(default_factory=IdentityMap)
    # Comprehension/genexpr nodes cache their element/key/value type in a
    # snapshot field (result_elem_type etc.) taken during analysis. When that
    # snapshot is a Pending* container type (a list-literal element), the
    # deferred resolver only updates the element node's type, leaving the
    # snapshot stale -- it must be finalized from the registry after
    # resolve_all. Recorded as (node, attr_name) pairs.
    pending_elem_type_fields: list[tuple[object, str]] = field(default_factory=list)
    # Expression nodes whose cached `expr_types` entry holds a Pending* leaf
    # nested in a composite (`list[Pending]` from a non-array comprehension,
    # `tuple[Pending,...]`). Recorded at set_expr_type so the finalization pass
    # rewrites only these nodes -- never a sweep over the module-wide cache.
    pending_composite_exprs: list[object] = field(default_factory=list)
    # Expression nodes typed as a container literal -- a list, a dict, a
    # set -- with a part still a pending number, or not known yet, when
    # they were analyzed; resolve_all gives each the type its record
    # resolved to.
    pending_container_exprs: list[object] = field(default_factory=list)
    # Branch-decl snapshot dicts (the `if_branch_decls` values recorded by
    # this function's branch producers). The snapshots capture binding types
    # BEFORE the deferred container resolution, so resolve_all must finalize
    # each registered map or a Pending* leaf reaches codegen's to_cpp().
    pending_branch_decl_maps: list[dict] = field(default_factory=list)
    # (`arm_branch_decls` key, name, the arm statement declaring it), inner
    # links first (analysis order): resolve_all gives each declaring binding
    # the joined type once it settles.
    arm_decl_sites: list[tuple[TpyStmt, str, TpyStmt]] = field(
        default_factory=list)

    # --- Pending generic instance tracking ---
    pending_generic_instances: dict[int, PendingGenericInstanceInfo] = field(default_factory=dict)
    variable_to_generic_instance: dict[str, int] = field(default_factory=dict)

    # --- String local tracking ---
    variable_to_str_var: dict[str, int] = field(default_factory=dict)
    str_source_borrows: dict[StorageKey, set[int]] = field(default_factory=dict)
    pending_str_resolutions: list[int] = field(default_factory=list)

    # --- Bytes local tracking ---
    variable_to_bytes_var: dict[str, int] = field(default_factory=dict)
    bytes_source_borrows: dict[StorageKey, set[int]] = field(default_factory=dict)
    pending_bytes_resolutions: list[int] = field(default_factory=list)

    # --- Pinned-view alias tracking (StrView/BytesView annotations) ---
    # source_name -> set of pinned-view borrower names. Pending views fall
    # back to the owned type via source_mutated; pinned views can't, so we
    # warn at source reassignment that the view dangles.
    pinned_view_aliases: dict[str, set[str]] = field(default_factory=dict)

    # --- Control flow ---
    # Every parent-initializer call in this __init__ (`super().__init__()` and
    # `Base.__init__(self, ...)`), with the spelling its diagnostic names.
    base_init_calls: list[tuple[TpyMethodCall, str]] = field(default_factory=list)
    super_del_call: TpyMethodCall | None = None
    pending_loop_vars: dict[str, PendingLocal] = field(default_factory=dict)
    loop_vars: set[str] = field(default_factory=set)
    mutated_loop_vars: set[str] = field(default_factory=set)
    # The targets of the comprehension clauses being analyzed, innermost
    # last: the loop vars a comprehension's write may lend through.
    comp_targets: list[str] = field(default_factory=list)
    consumed_loop_vars: set[str] = field(default_factory=set)
    # One loop var can borrow MANY sources: a `*args` pack is a single
    # argument slot holding many operands, so an iterable borrowing the pack
    # borrows every one of them.
    loop_var_iterable: dict[str, list[StorageKey]] = field(default_factory=dict)
    # For a tuple-unpack loop over an iterator whose yield names the source
    # of each tuple ELEMENT (`zip(xs, ys)`: `Iterable[T1], Iterable[T2] ->
    # Iterator[tuple[T1, T2]]`), the storage each element lends from -- so a
    # write through one unpack target climbs to its own source, not to
    # every argument. Absent when the yield does not say.
    loop_var_elem_iterable: dict[str, list[list[StorageKey]]] = field(
        default_factory=dict)

    # --- Scope escape tracking ---
    var_scope_depth: dict[str, int] = field(default_factory=dict)
    hoisted_vars: set[str] = field(default_factory=set)
    # (alias, source) pairs the scope-escape check warned about: the
    # alias-rebind pass owes those no second warning at the source's rebind.
    escape_warned_aliases: set[tuple[str, str]] = field(default_factory=set)
    # Names the scope-escape check moved to a function-scope slot, warned or
    # precautionary. `hoisted_vars` also holds the branch pre-declarations.
    escape_hoisted_vars: set[str] = field(default_factory=set)
    rvalue_vars: set[str] = field(default_factory=set)
    owned_locals: set[str] = field(default_factory=set)
    # Accumulator: all locals that were ever owned. Survives FlowFacts
    # save/restore (not in FlowFacts). Used to compute the exported
    # movable_locals set at function end.
    ever_owned_locals: set[str] = field(default_factory=set)
    # Statement-level borrow bindings (var-decl / assign / walrus):
    # name -> any binding had a readonly/const source. A name bound ONLY
    # this way (never a fresh rvalue, no non-statement binding) takes the
    # pointer (alias) form at branch pre-decls. Accumulator like
    # ever_owned_locals -- survives FlowFacts restores.
    stmt_borrow_decls: dict[str, bool] = field(default_factory=dict)
    # Names bound by non-statement binding kinds (with-as, for-loop var,
    # match capture): their binding machinery owns the storage form, so
    # they are never pointer-form-eligible at branch pre-decls.
    nonstmt_bound_names: set[str] = field(default_factory=set)
    # Names bound by a construct rather than a statement that are nonetheless
    # borrow bindings: an explicit exception to nonstmt_bound_names so the borrow
    # snapshot keeps them pointer-form-eligible regardless of prescan/full-pass
    # ordering. Two members -- a match capture aliasing an lvalue subject, and a
    # `with` target (which borrows `__enter__()`'s result).
    nonstmt_borrow_bindings: set[str] = field(default_factory=set)
    # The ownership fact (`note_nonowning_bind`), an accumulator like
    # ever_owned_locals: a local named here is non-owning, wholly or at
    # some tuple elements, for the whole function.
    nonowning_binds: dict[str, NonOwningBind] = field(default_factory=dict)
    # The reads whose verdict is not settled yet (`OwningRead`): the
    # owning-sink moves out of locals, settled once the body's fact is
    # complete (`TypeCompatibility.settle_owning_reads`), and the loop
    # variables' copy warnings, settled when their loop's mode is decided.
    owning_reads: list[OwningRead] = field(default_factory=list)
    move_through_vars: set[str] = field(default_factory=set)

    # --- Prescan / last-use ---
    current_reassigned_vars: set[str] = field(default_factory=set)
    # Every name the body binds anywhere, narrower-scoped bindings included,
    # plus every parameter it rebinds: whatever the function may destroy
    # after a return value is built, whichever block bound it.
    body_bound_names: frozenset[str] = frozenset()
    # Names a nested def rebinds via `nonlocal`: the reassigned-var scan does
    # not look into nested defs, and the closure may run at any point, so a
    # view borrowing such a name's storage is treated as reseated.
    nested_nonlocal_rebinds: set[str] = field(default_factory=set)
    # The enclosing function's `nested_nonlocal_rebinds`, seeded into a nested
    # def: a sibling closure it calls can rebind a name it captured.
    enclosing_nonlocal_rebinds: set[str] = field(default_factory=set)
    # Names a `del` in this body unbinds: the value is destroyed there, so
    # like a rebind it ends the storage a view or `const&` of it reads.
    deleted_names: set[str] = field(default_factory=set)
    # The objects this body may write in place anywhere, and the storage
    # each name may hold, through any binding (`prescan.InPlaceWrites`): for
    # a `String` / `bytearray`, writes that reseat the buffer a view of it
    # reads; for every flow fact, which spellings a write invalidates.
    in_place_writes: InPlaceWrites = field(default_factory=InPlaceWrites)
    # The compound-statement depth of the C++ block each local is declared
    # in (0: the function body, and every parameter), moved out when a
    # hoist declares it in front of an enclosing statement; written with
    # `var_scope_depth` by `declare_local`. A capture or global is absent.
    # Unlike `var_scope_depth` it counts every block (an `if` arm, a `try`
    # body, a handler, a `with` body, a `match` arm), since a local of any
    # of them dies at its closing brace.
    decl_block_depth: dict[str, int] = field(default_factory=dict)
    # Names a closure of this body captures (`liveness.closure_pinned_names`):
    # never moved, so an owning return of one copies.
    closure_pinned: frozenset[str] = frozenset()
    # Name -> (family, var_id) of every str/bytes view entry registered for
    # it in this body; a name bound in several blocks has several entries
    # that share one hoisted slot.
    view_ids_by_name: dict[str, list[tuple[ViewTypeFamily, int]]] = field(
        default_factory=dict)
    # The storage roots each live for-each variable iterates, as its own loop
    # bound it (None: nothing placeable); `loop_var_iterable` keeps the last
    # loop's entry for a reused name.
    loop_var_iter_roots: dict[str, 'set[str] | None'] = field(default_factory=dict)
    # `with ... as name` targets: they point into the manager's result, which
    # no borrow is recorded for.
    with_target_names: set[str] = field(default_factory=set)
    # What each binding of a name puts in its storage (`alias_rebind.BindKind`),
    # keyed by the binding node (var-decl, assign, walrus); the alias-rebind
    # storage pass reads it after the walk.
    bind_kinds: IdentityMap = field(default_factory=IdentityMap)
    # Every binding statement with an rvalue rebind of a reference local
    # (OWN), to the names it rebinds so (several for a tuple unpack or a
    # `match`'s captures): the sites the alias-rebind pass decides storage
    # at. Identity-keyed so the nested-def state deep copy keeps the real
    # statement objects.
    gate_sites: 'IdentityMap[TpyStmt, frozenset[str]]' = field(
        default_factory=IdentityMap)
    # The gate sites that bind a name already declared as a generator
    # object, to what the new frame borrows (None: nothing): the pass
    # rejects one that rebinds and cannot be rebuilt in place.
    frame_rebind_sites: IdentityMap = field(default_factory=IdentityMap)
    # What each generator-object local's bindings borrow, unioned over
    # every binding: the storage roots (None for a temporary) and whether
    # every binding was one plain call, whose temporaries a resumable frame
    # lifts ahead of the local. None when some binding's borrows cannot be
    # traced; readers reject then.
    frame_local_roots: 'dict[str, tuple[frozenset[str | None], bool] | None]' = field(
        default_factory=dict)
    # Every binding of each frame-holding local, in program order: where
    # its frames come from, and where diagnostics point.
    frame_binding_nodes: 'dict[str, list[TpyExpr | TpyStmt]]' = field(
        default_factory=dict)
    # A generator-object local bound from another one (`h = g`): the same
    # object, so its source must be declared before it and outlive it.
    frame_alias_sources: dict[str, str] = field(default_factory=dict)
    # Every frame-holding local a plain function keeps in its loop body's
    # C++ block (not declared in front of the loop by a read after it):
    # gone at the pass end.
    pass_scoped_frames: 'dict[str, TpyStmt]' = field(default_factory=dict)
    # Generator locals an `if` / `with` / `match` block keeps although a
    # read after it would need them declared in front of it (what they
    # borrow does not outlive the body): why, and the block's name. A read
    # after the block is an error.
    block_closed_frames: 'dict[str, tuple[str, str]]' = field(
        default_factory=dict)
    # Locals whose sole binding is a fresh constructor call of their exact static
    # type (never rebound -- field mutation doesn't count). Their dynamic type is
    # provably their static type, so the polymorphic-slicing guard may move them
    # into an owned poly slot (`Box[P]`) without slicing, exactly like a
    # fresh-rvalue ctor at the store site.
    current_fresh_ctor_locals: set[str] = field(default_factory=set)
    # Fresh str/bytes view tuple-unpack targets, candidates for owned-promotion
    # if they later get hoisted out of a branch (a view would then outlive its
    # source __tup temp / branch-local by-ref source). Consumed at the
    # branch-predecl sites. Per-function; reset with the prescan sets.
    tuple_unpack_view_targets: set[str] = field(default_factory=set)
    current_aug_assigned_vars: set[str] = field(default_factory=set)
    # alias -> source for locals initialized from a bare name (prescan
    # alias_sources): the whole-object borrowers last-use liveness models.
    current_alias_sources: dict[str, str] = field(default_factory=dict)
    # alias -> root for locals initialized from a field/subscript chain
    # (prescan chain_alias_sources): the borrowers into the root's storage
    # that last-use liveness models.
    current_chain_alias_sources: dict[str, str] = field(default_factory=dict)

    # --- Definite-assignment tracking ---
    definitely_assigned: set[str] = field(default_factory=set)
    # Assigned, but by a loop body whose local is still pending (not in scope
    # yet, so not spellable in `definitely_assigned`). Same lattice: it merges
    # with `definitely_assigned` at every branch join, so a loop in only one
    # arm leaves the name maybe-unassigned and the promoting read rejects.
    loop_bound_assigned: set[str] = field(default_factory=set)
    init_terminated: bool = False
    narrowed_types: dict[str, TpyType] = field(default_factory=dict)

    # --- Reassignment inference tracking ---
    unresolved_none_vars: set[str] = field(default_factory=set)
    write_history: dict[str, list[tuple[TpyType, TpyExpr]]] = field(default_factory=dict)
    authoritative_types: dict[str, TpyType] = field(default_factory=dict)
    authoritative_type_lines: dict[str, int] = field(default_factory=dict)

    # --- Pending numeric locals (tpyc/sema/pending_num.py) ---
    # The prescan's literal-seeded locals; each gets a cell at its first
    # binding. The cells themselves live on the context, not here: a
    # settle inside a rolled-back trial must stay settled, as the scope it
    # publishes to does.
    pending_num_names: frozenset[str] = frozenset()
    # The prescan's excluded names: a local bound through one of those forms
    # is never pending, derived included.
    pending_num_excluded: frozenset[str] = frozenset()
    # Every literal-seeded local's literal values, pending or not: what an
    # annotation of it must hold.
    int_literal_values: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # The prescan's first bindings (`scan_first_bindings`): per local, the
    # statements -- one, or the sibling arms that bind it together -- whose
    # values declare its type. Read when an arm joins a group's cell and
    # when an annotation stands past the first binding.
    first_bindings: dict[str, tuple[TpyStmt, ...]] = field(default_factory=dict)
    pending_cell_of: dict[str, int] = field(default_factory=dict)
    # The element cells of the function's list literals (their ids): the
    # cells no local name owns, settled and dropped with the others.
    pending_elem_cids: list[int] = field(default_factory=list)
    # The cells of earlier declarations a later one of the same name took
    # the name from, settled and dropped with the others.
    pending_unowned_cids: list[int] = field(default_factory=list)
    # Operations and conversions over pending values, resolved at settle.
    pending_num_deferred: list['DeferredIntOp'] = field(default_factory=list)
    # Types recorded at a value that holds a row (a call's signature at the
    # list's element, a local bound to a row): the type and its recorder,
    # called again with the type once the rows' list types are resolved,
    # which is after the settle (`PendingNums.when_elem_known`).
    after_list_resolution: list[tuple['TpyType', Callable[['TpyType'], None]]] = field(default_factory=list)
    # The list literals handed out undecided to a slot declared as a typed
    # container in the statement under analysis, with the node: the slot's
    # coercion decides each, and the statement's end any it did not reach.
    # Per function state: a body analyzed inside the statement (a generator
    # expression's) neither decides nor adds to the statement's own.
    awaiting_container: list[tuple[int, 'TpyExpr']] = field(default_factory=list)
    # Conversion placeholders that settled to none, spliced out at the end.
    pending_num_splices: list[TpyExpr] = field(default_factory=list)

    # --- Global declaration tracking ---
    global_declarations: set[str] = field(default_factory=set)

    # --- Nested def tracking ---
    in_nested_def: bool = False
    nested_def_name: str | None = None
    # Inside a nested-def analysis: the first literal id handed out in it. A
    # pending container literal below it belongs to an ENCLOSING function,
    # whose end -- not this nested def's -- settles its type.
    nested_def_first_literal: int = 0
    # Inside a nested-def analysis: 'self' in outer_scope_locals is the
    # enclosing METHOD's receiver (not an ordinary local named self), so
    # assignment sites can reject rebinds with the receiver message.
    outer_self_is_receiver: bool = False
    # Inside a generator expression's frame: the source's iteration loans on
    # storage the body also names -- a capture or a module global -- which
    # the frame's own loop holds next to its loan on the source param, since
    # the two names reach one container.
    genexpr_source_loans: tuple[tuple[StorageKey, LoanInfo], ...] = ()
    outer_scope_locals: set[str] = field(default_factory=set)
    current_nonlocal_names: set[str] = field(default_factory=set)
    # The names the enclosing function DECLARES (an annotation or a
    # parameter): a `nonlocal` write into one converts into its type instead
    # of joining inferred bindings.
    enclosing_declared_names: set[str] = field(default_factory=set)
    # The annotated ones among them, with the annotation's line: a
    # `nonlocal` store into one is refused when it narrows, as a store in
    # the enclosing function is.
    enclosing_annotation_lines: dict[str, int | None] = field(
        default_factory=dict)
    # Union of nonlocal targets across all nested defs analyzed so far in
    # this function: any later call may invoke such a closure and rebind
    # these names, so check-elision facts for them die at every call site.
    closure_written_names: set[str] = field(default_factory=set)
    # Field paths and receivers (the enclosing scope's spellings) the nested
    # defs analyzed so far write through: any later call may run one. A
    # stored path maps to the type every store keeps its slot at (None: some
    # store may write None), decided at the def site, where the stores are
    # analyzed -- so a fact on the slot that type matches survives a call.
    closure_mutated_paths: dict[str, TpyType | None] = field(
        default_factory=dict)
    closure_mutated_receivers: set[str] = field(default_factory=set)
    nested_def_names: set[str] = field(default_factory=set)
    nested_def_escapes: set[str] = field(default_factory=set)
    nested_def_nodes: dict[str, 'TpyNestedDef'] = field(default_factory=dict)
    # Nested-def names this scope binds, with the `def`'s location, from the
    # prescan -- seeded before the body walk because Python binds them for
    # the whole scope. An entry stays live until something actually binds
    # the name here; while it is live, a read of the name must NOT resolve
    # outward to the shadowed module entity
    # (see check_nested_def_shadowed_read).
    nested_def_pending: dict[str, 'SourceLocation | None'] = field(default_factory=dict)
    # A nested `def` written inside a compound statement's body declares its
    # callable for that block alone, so nothing past the block's end can
    # name it -- even when every path through the block bound it. Live
    # entries are name -> (the enclosing statement, a phrase naming it for
    # diagnostics); `analyze_stmt` moves one into `nested_def_block_dead`
    # when that statement's analysis ends, and a later `def` at the scope's
    # own level takes the name out of both (it declares one that lasts).
    nested_def_block_defs: dict[str, tuple['TpyStmt', str]] = field(default_factory=dict)
    nested_def_block_dead: dict[str, str] = field(default_factory=dict)
    # The namespace level this scope's own bindings land in. `current_ns`
    # walks DOWN into a lambda/comprehension child and UP to the module, so
    # this is the boundary that says whether a name is bound HERE.
    own_ns: 'Namespace | None' = None
    # Mutation marks attempted while analyzing a nested-def body (recorded on
    # the NESTED tracking state, which is otherwise discarded on restore).
    # _analyze_nested_def replays the captured, nonlocal and receiver subset
    # into the enclosing state so closure mutations reach the method's
    # const/param-mutation facts.
    nested_mutation_marks: list[NestedMutationMark] = field(default_factory=list)

    # --- Per-local escape/ownership provenance (see BindingProvenance) ---
    # One record per local; absent name == default. Mutate only via the
    # bp_* helpers below so all-default records stay pruned (the lattices in
    # flow_facts rely on absent == default).
    binding_provenance: dict[str, BindingProvenance] = field(default_factory=dict)
    # Loop vars / next() results bound from a frame-slot-rooted borrow yield
    # (a generator / genexpr / Iterator[T] source, T non-value): the borrow is
    # valid only until the next iteration step (the generator frame slot is
    # overwritten on each __next__()), so retaining it past the step is unsound.
    # These are kept OUT of the safe-to-return provenance and rejected at escape
    # sites (return / store / container insert / closure capture / yield onward).
    # Active only during the consuming loop body (added before, discarded after).
    # A GATE_ONLY entry carries the same fact for an open-`T` source, which
    # borrows only at its reference instantiations: too weak to reject an
    # escape on, but it feeds the generic-yield slot verdict. ONE table, so
    # alias and walrus propagation reaches both kinds.
    ephemeral_borrow_vars: dict[str, EphemeralKind] = field(default_factory=dict)
    # Borrow-yield rooting checks deferred until `func.generator_locals` is
    # populated: a yielded frame-resident local is a valid borrow root, but the
    # check runs during body analysis, before the frame-local set exists.
    # Entries are (yielded_expr, elem_type, loc).
    pending_yield_root_checks: list[tuple[TpyExpr, TpyType, 'SourceLocation | None']] = field(default_factory=list)
    # Yield sources of a generator whose element is an open `T`, recorded for
    # the per-generator provenance gate (the slot borrows only if EVERY source
    # outlives a suspension). Entries are (yielded_expr, source_is_ephemeral);
    # the ephemeral half must be sampled at the yield, while the loop-body
    # marking is live, and the rooting half is deferred exactly like
    # `pending_yield_root_checks`.
    pending_generic_yield_sources: list[tuple[TpyExpr, bool]] = field(default_factory=list)
    # Return/yield dangling checks over a str/bytes local whose storage the
    # deduction has not settled yet. A use must not be what decides the
    # storage, so the check waits for the post-body drain
    # (`TypeCompatibility.drain_deferred_escape_checks`) and then asks
    # `view_storage_verdict` for the settled answer.
    # Entries are (returned_expr, return_type, loc, source_type, for_yield).
    pending_view_storage_checks: list[
        tuple[TpyExpr, TpyType, 'SourceLocation | None', TpyType, bool]
    ] = field(default_factory=list)
    non_null_ptr_vars: set[str] = field(default_factory=set)
    # Narrowing accumulated during a single statement's expression analysis;
    # flushed into non_null_ptr_vars at the statement boundary. Deferred so
    # sibling accesses within an unspecified-order expression (e.g. `p.x + p.y`,
    # `p.x = p.x + 1`) keep their individual checks -- C++ leaves arithmetic
    # and RHS-vs-LHS sub-expression order unspecified (C++17 only sequences
    # RHS-before-LHS for the whole assignment, not for sub-expressions),
    # so within-statement elision would be unsafe.
    pending_non_null_ptr_vars: set[str] = field(default_factory=set)

    # --- Consumed variable tracking ---
    consumed_vars: set[str] = field(default_factory=set)

    # Owned-erased coroutine locals bound in this body and not read since
    # (any read clears the entry -- every legal read is a consumption or a
    # manual-driving borrow). Survivors warn at body end: a bound coroutine
    # never consumed is destroyed without running.
    unread_coro_locals: dict[str, 'TpyStmt'] = field(default_factory=dict)

    # Bound concrete coroutine locals a method call may have started (the
    # hand-driven `c.__poll__(w)`), with that call's line. A started frame
    # holds pointers into itself, so it may no longer be moved -- into a
    # task, an Own slot, or another name. Never cleared: a rebind on one
    # path does not make every path's object unstarted.
    started_coro_locals: dict[str, int] = field(default_factory=dict)

    # --- Variable declaration tracking (per-function) ---
    var_decl_by_name: dict[str, 'TpyVarDecl'] = field(default_factory=dict)

    # --- Integer value range tracking ---
    value_ranges: dict[str, 'ValueRange'] = field(default_factory=dict)

    # --- Borrow tracking ---
    borrow_tracker: BorrowTracker = field(default_factory=BorrowTracker)

    # --- Parameter mutation inference ---
    current_param_names: set[str] = field(default_factory=set)
    current_param_name_to_idx: dict[str, int] = field(default_factory=dict)
    current_mutated_param_names: set[str] = field(default_factory=set)
    # Params a receiver call may mutate, left to Phase 2 (the receiver call
    # edge): kept out of the direct facts Phase 2 starts from, but in the
    # initial estimate, which a reader before Phase 2 takes as the callee's
    # facts and which must therefore not under-approximate.
    current_receiver_deferred_param_names: set[str] = field(default_factory=set)
    # Method type params whose `U: T` bound was used representationally in the
    # body (e.g. `Ptr[U] -> Ptr[T]` coercion). Drained to FunctionInfo at body
    # end; codegen reads it at call sites to decide adapter-wrap for structural
    # conformers.
    current_representational_params: set[str] = field(default_factory=set)
    current_rebound_params: set[str] = field(default_factory=set)
    current_call_edges: list = field(default_factory=list)
    current_self_mutated: bool = False
    current_self_struct_mutated: bool = False
    current_struct_mutated_param_names: set[str] = field(default_factory=set)
    # Params (and "self") written THROUGH what they lend: a loop variable
    # over them, an element or field borrow off them.
    current_elem_mutated_param_names: set[str] = field(default_factory=set)
    current_returned_param_names: set[str] = field(default_factory=set)
    # The subset of what the return lends that is the param's OWN storage,
    # reached without stepping through a `Ptr` / borrowing view; const
    # inference reads it, lifetime checks read the set above.
    current_storage_returned_param_names: set[str] = field(default_factory=set)
    current_consumed_own_params: set[OwnSlot] = field(default_factory=set)
    # A local that took what an Own param moved out -> the slot its consume
    # credits. A rebind of the local drops its entry.
    own_consume_aliases: dict[str, OwnSlot] = field(default_factory=dict)
    # Params whose address has been observed escaping into a mutable Ptr[T]
    # field via `FIELD = PARAM`. Finalized to FunctionInfo.addr_escapes_params
    # at body-analysis end; consumed by param-signature codegen to suppress
    # the `const T&` default so the `&param -> T*` store type-checks.
    current_addr_escape_param_names: set[str] = field(default_factory=set)
    # Awaited sub-frame FunctionInfos for Send/Sync frame classification
    # (sema/frame_traits.py). A None entry is an await whose operand frame sema
    # cannot classify (Task / structural awaitable) -- forces non-Send.
    current_awaited_subframes: list = field(default_factory=list)

    def __deepcopy__(self, memo: dict) -> 'FunctionTrackingState':
        # A snapshot owns the facts, not the structures the whole compilation
        # shares: the parse tree and the registry's function records are not
        # state to roll back, so a save must never clone one. Seeding the
        # memo carries them over by identity while the containers around them
        # still copy deeply -- the same argument `IdentityMap.__deepcopy__`
        # makes for identity KEYS. `LIVE_HANDLE_FIELDS` are shared structure
        # by the same principle, one level up: the enclosing analysis goes on
        # binding into that scope and those namespaces, so the field carries
        # the object itself rather than a copy seeded from its contents.
        new = self.__class__.__new__(self.__class__)
        memo[id(self)] = new
        attrs = vars(self)
        seed_identity_objects(attrs, memo)
        for name, value in attrs.items():
            if name in LIVE_HANDLE_FIELDS:
                setattr(new, name, value)
            else:
                setattr(new, name, deepcopy(value, memo))
        return new

    def rebinds_keep_ownership(self, name: str) -> bool:
        """Whether local `name` is owning (`owns_whole`): no binding of it
        re-points it at existing storage, so a last use may move out of
        what it holds. A hoisted local is a pointer slot no use moves out
        of."""
        return name not in self.hoisted_vars and self.owns_whole(name)

    def owns_whole(self, name: str) -> bool:
        """The ownership fact for local `name` as recorded so far: no
        binding made it refer to existing storage as a whole (some tuple
        elements may still be borrowed -- `nonowning_elems`)."""
        rec = self.nonowning_binds.get(name)
        return rec is None or rec.elems is not None

    def nonowning_elems(self, name: str, arity: int) -> frozenset[int]:
        """The elements of tuple local `name` some binding so far borrows
        (every one when a binding borrows the whole tuple)."""
        rec = self.nonowning_binds.get(name)
        if rec is None:
            return frozenset()
        return (frozenset(range(arity)) if rec.elems is None
                else rec.elems)

    def nonowning_line(self, name: str) -> int | None:
        """The line of the first binding that made local `name` non-owning
        (wholly or at an element), None for an owning local."""
        rec = self.nonowning_binds.get(name)
        return rec.line if rec is not None else None

    # --- BindingProvenance accessors ---
    # Reads default-fill from an absent name; writes go through _bp_update,
    # which prunes a record back to absence once every field is default so
    # "absent == default" holds for the flow-merge lattices.

    def _bp_update(self, name: str, **changes: object) -> None:
        cur = self.binding_provenance.get(name, _DEFAULT_PROVENANCE)
        new = replace(cur, **changes)
        if new == _DEFAULT_PROVENANCE:
            self.binding_provenance.pop(name, None)
        else:
            self.binding_provenance[name] = new

    def bp_is_param_derived(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.param_derived

    def bp_is_safe_to_return(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.safe_to_return

    def bp_set_param_derived(self, name: str, value: bool) -> None:
        # param_derived implies safe_to_return (BindingProvenance invariant):
        # raising param also raises safe so the two can't diverge at a setter.
        if value:
            self._bp_update(name, param_derived=True, safe_to_return=True)
        else:
            self._bp_update(name, param_derived=False)

    def bp_set_safe_to_return(self, name: str, value: bool) -> None:
        # Clearing safe must clear param too, or the implication above breaks.
        if value:
            self._bp_update(name, safe_to_return=True)
        else:
            self._bp_update(name, safe_to_return=False, param_derived=False)

    def bp_add_loop_var_provenance(self, name: str) -> None:
        self._bp_update(name, param_derived=True, safe_to_return=True)

    def bp_remove_loop_var_provenance(self, name: str) -> None:
        self._bp_update(name, param_derived=False, safe_to_return=False)

    def bp_owns_fresh_idx(self, name: str) -> int | None:
        bp = self.binding_provenance.get(name)
        return bp.owns_fresh_idx if bp is not None else None

    def bp_is_owning_storage(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.owning_storage

    def bp_copy_into_own_idxs(self, name: str) -> frozenset[int]:
        bp = self.binding_provenance.get(name)
        return bp.copy_into_own_idxs if bp is not None else frozenset()

    def bp_set_tuple_member(
        self,
        name: str,
        *,
        owns_fresh_idx: int | None,
        owning_storage: bool,
        copy_into_own_idxs: frozenset[int],
        borrow_source_roots: frozenset[str],
    ) -> None:
        # All tuple-member hazard fields are (re)derived together per
        # binding -- replacing them atomically preserves the rebind discipline
        # (`t = t` re-installs its own facts) while leaving the return-safety
        # fields, managed separately, untouched.
        self._bp_update(
            name,
            owns_fresh_idx=owns_fresh_idx,
            owning_storage=owning_storage,
            copy_into_own_idxs=copy_into_own_idxs,
            borrow_source_roots=borrow_source_roots,
        )

    def bp_borrow_source_roots(self, name: str) -> frozenset[str]:
        bp = self.binding_provenance.get(name)
        return bp.borrow_source_roots if bp is not None else frozenset()


_ATOMIC_VALUES = (str, bytes, bytearray, int, float, complex, bool,
                  type(None), type, Enum, StorageKey)


def _object_members(obj: object) -> Iterator[object]:
    """The values an ordinary object holds, `__slots__` classes included."""
    holder = getattr(obj, '__dict__', None)
    if holder is not None:
        yield from holder.values()
    for cls in type(obj).__mro__:
        for name in getattr(cls, '__slots__', ()):
            try:
                yield getattr(obj, name)
            except AttributeError:
                pass


def seed_identity_objects(value: object, memo: dict,
                          seen: 'set[int] | None' = None) -> None:
    """Map every object under `value` that must NOT be cloned to ITSELF, so a
    `deepcopy` through `memo` hands the original back.

    Two kinds qualify. Parse nodes: the tree the analysis reads, which no
    snapshot owns. Registry `FunctionInfo`s: the call-graph objects Phase-2
    mutation propagation reads facts off BY IDENTITY, reached from a state
    through a call edge. `TpyType`s are skipped -- shared, possibly cyclic,
    and nothing keys on their identity.
    """
    if seen is None:
        seen = set()
    if isinstance(value, _ATOMIC_VALUES):
        return
    key = id(value)
    if key in seen:
        return
    seen.add(key)
    if is_parse_node(value) or isinstance(value, FunctionInfo):
        memo[key] = value
    elif isinstance(value, TpyType):
        pass
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            seed_identity_objects(item, memo, seen)
    elif isinstance(value, dict):
        for dict_key, item in value.items():
            seed_identity_objects(dict_key, memo, seen)
            seed_identity_objects(item, memo, seen)
    elif isinstance(value, IdentityMap):
        # Its KEYS survive its own `__deepcopy__`; its values do not.
        for item in value.values():
            seed_identity_objects(item, memo, seen)
    elif isinstance(value, IdentitySet):
        # Its members ARE its keys, so they survive its own `__deepcopy__`.
        pass
    else:
        for member in _object_members(value):
            seed_identity_objects(member, memo, seen)


# FunctionTrackingState fields holding the LIVE handles of the enclosing
# analysis, which a restore has to hand back UNCHANGED -- so
# `FunctionTrackingState.__deepcopy__` carries the objects themselves over,
# not copies of them. A save/restore pair isolates what a nested def or a
# trial WRITES, and neither ever binds into these: both open a child scope and
# a child namespace first, and both install their own function node. A clone
# instead becomes the live object at the restore, so every binding made AFTER
# it lands where nothing else looks -- a local declared after a lambda trial
# never reaches `_collect_generator_locals`' `local_ns`, and its frame slot is
# never emitted -- and every consumer keying on the function NODE by identity
# (the generic-yield settle's parked entry, `_is_frame_resident_local` reading
# `generator_locals`) writes to or reads from a node sema and codegen do not
# hold. `own_ns` belongs here for a further reason: `bound_in_own_scope`
# recognizes the scope's own namespace level by IDENTITY along `current_ns`'s
# parent chain, so a cloned one is never reached and the walk runs on out to
# the enclosing function -- a read of a still-pending nested def then resolves
# outward instead of being rejected.
LIVE_HANDLE_FIELDS = frozenset((
    'current_scope', 'current_ns', 'current_function', 'own_ns',
))


@dataclass
class OpenCallArgs:
    """The arguments of one call under analysis that its candidates are
    scored against, or a generic callee's type parameters are inferred
    from, before any parameter decides them: a container argument whose
    leaves are still open is handed over open (`PendingNums.adaptive_view`)
    unless the winner `declared` its node's parameter, and its cells are
    collected, to be settled when the call ends if nothing decided them.
    `resolved` says the parameters the call resolved -- a generic
    candidate's (`overloads.call_resolves_param`) -- decide such an
    argument where they are a view (`Iterable[T]`); a declared view
    converts per element and decides nothing. `scored` says several
    candidates compete, so the winner is applied to the arguments after
    selection (`CallAnalyzer.apply_winner`). `keywords` names the keyword
    arguments among `nodes`."""
    nodes: list['TpyExpr']
    resolved: bool
    scored: bool
    keywords: dict[str, 'TpyExpr'] = field(default_factory=dict)
    cells: list['PendingNumCell'] = field(default_factory=list)
    # Which node handed over which of `cells`.
    handed: list[tuple['TpyExpr', list['PendingNumCell']]] = field(
        default_factory=list)
    # The argument nodes the winner takes at a parameter it DECLARES (every
    # node of a non-generic winner; a generic winner's `xs: Iterable[int64]`
    # beside `t: T`): each is received as at a declared slot -- analyzed
    # again there, not handed over open, not decided by a view.
    declared: list['TpyExpr'] = field(default_factory=list)
    # The records of the containers written as arguments that the scope
    # handed over with no parameter to type them at yet -- an empty one
    # (`[]`, `list()`, `{}`, `set()`), a dict or set literal with its
    # numbers still the literals written: each awaits the winner's slot,
    # which analyzes its node again and gives it the type or record it
    # keeps (`CallAnalyzer.apply_winner`).
    awaiting: list['ContainerLiteralInfo'] = field(default_factory=list)
    # The dict and set literal arguments that stay open while the
    # candidates are scored: every candidate declares the parameter that
    # receives them plainly (`CallAnalyzer.open_written_literals`).
    open_literals: list['TpyExpr'] = field(default_factory=list)

    def declares(self, node: 'TpyExpr | None') -> bool:
        return node is not None and any(n is node for n in self.declared)

    def hands_over(self, node: 'TpyExpr') -> bool:
        """Whether argument `node` is handed over open."""
        return not self.declares(node)

    def resolves(self, node: 'TpyExpr | None') -> bool:
        """Whether the parameter that receives `node` -- or, asked of no
        node (a query), any parameter of the call -- is one the call
        resolved."""
        return self.resolved and not self.declares(node)

    def hand_over(self, node: 'TpyExpr',
                  cells: list['PendingNumCell']) -> None:
        self.cells.extend(cells)
        self.handed.append((node, list(cells)))

    def declare(self, nodes: list['TpyExpr']) -> None:
        """The winner takes `nodes` at declared parameters: a call to it
        alone settles nothing of theirs when it ends -- whatever its slots
        leave open (a Span's list, decided where its storage is) stays open
        as it would there."""
        self.declared.extend(nodes)
        released = {id(c) for node, cells in self.handed
                    if any(node is n for n in nodes) for c in cells}
        self.cells = [c for c in self.cells if id(c) not in released]


class UnnamedHolder(Enum):
    """A refused tuple site's holder (`tuple_own_sites`) that has no name."""
    # Only a pointer handed out of an element, or an iterator over the
    # tuple, still refers to the element's object.
    POINTER_OR_ITERATOR = "pointer_or_iterator"


@dataclass
class SemanticContext:
    """Shared state for all semantic analysis components.

    Per-function tracking state is held in ``func`` (FunctionTrackingState).
    Callers access it explicitly as ``ctx.func.<field>`` so the split between
    module-wide state and per-function state is visible at every use site.
    """

    # --- Core ---
    registry: TypeRegistry
    global_scope: Scope
    default_int_type: TpyType = field(default_factory=lambda: INT32)
    macro_registry: MacroRegistry | None = None

    # --- Per-function state ---
    # Accessed explicitly by callers as ``ctx.func.<field>``.
    func: FunctionTrackingState = field(default_factory=FunctionTrackingState)

    # --- Record context ---
    record_ctx: RecordContext = field(default_factory=RecordContext)

    # @overload groups: implementation -> its stubs, in source order. Filled
    # at registration, for free functions and methods alike.
    overload_groups: IdentityMap = field(default_factory=IdentityMap)
    # Every other def of a method `@overload` implementation's name -> that
    # implementation: its `@auto_readonly` / `@auto_own` clone twin, never
    # emitted nor called (the stubs' specializations carry both halves).
    overload_twins: IdentityMap = field(default_factory=IdentityMap)

    # --- Type cache ---
    expr_types: IdentityMap = field(default_factory=IdentityMap)
    var_types: IdentityMap = field(default_factory=IdentityMap)
    # Per tuple binding (a var-decl, an assignment to a name, a walrus), the
    # elements it holds as objects of its own (`alias_rebind.tuple_elem_owned`):
    # the alias-rebind pass, the module-level park, the inline-element layout
    # and the frame's tuple slot all read the one verdict.
    tuple_elem_owned: IdentityMap = field(default_factory=IdentityMap)
    # Each tuple rebind site inside a body whose verdict is OWN, with the
    # name still holding what it would overwrite, an `UnnamedHolder` when
    # nothing named does, or None for storage the name does not own. A
    # frame, an `Own` parameter and a loop variable have no OWN storage for
    # such a site yet; their lowering reports it.
    tuple_own_sites: IdentityMap = field(default_factory=IdentityMap)
    # A tuple rebind site's per-element verdict: one entry per element, None
    # where the binding does not own it, else the storage the element's new
    # object takes. Sema records none -- every owned element takes storage
    # of its own (OWN) until MIR proves reusing the old one safe.
    tuple_elem_storage: IdentityMap = field(default_factory=IdentityMap)
    # Per function, every name's plain bindings (`tuple_name_bindings`).
    tuple_bindings_by_func: IdentityMap = field(default_factory=IdentityMap)
    # Per function, its pointer tuples once final (`pointer_tuple_names`),
    # the owners and excluded names its own post-body pass found, and the
    # declared-slot checks of its rebinds made before its final set was
    # computed (`ParkedRebind`). The checks sit here, each with its parked
    # state, rather than on the function state, which a nested def's scope
    # exit discards before the outermost body ends.
    pointer_tuples_by_func: IdentityMap = field(default_factory=IdentityMap)
    pointer_tuple_seeds: IdentityMap = field(default_factory=IdentityMap)
    pending_pointer_tuple_rebinds: IdentityMap = field(
        default_factory=IdentityMap)
    # The and/or and ternary nodes whose value is only tested for truth (a
    # condition, a `not` operand, the argument of `bool(...)`), so each
    # operand is tested on its own instead of the node needing one value type.
    truth_test_selects: IdentitySet = field(default_factory=IdentitySet)
    # The truth-tested ternaries whose arms share no value type: each arm is
    # tested on its own and the ternary is a bool.
    truth_tested_arms: IdentitySet = field(default_factory=IdentitySet)

    # --- Literal tracking (counters + registries persist across functions) ---
    literal_counter: int = 0
    # Every container literal's record by its id (one sequence for lists,
    # dicts and sets); the kind is the record's class.
    container_literals: dict[int, ContainerLiteralInfo] = field(
        default_factory=dict)
    pending_generic_counter: int = 0
    str_var_counter: int = 0
    str_vars: dict[int, ViewVarInfo] = field(default_factory=dict)
    bytes_var_counter: int = 0
    bytes_vars: dict[int, ViewVarInfo] = field(default_factory=dict)
    # Pending numeric locals (tpyc/sema/pending_num.py). The cells are not
    # function state: a trial rolls that back, and a settle made in one
    # must stay made, as the scope it publishes to stays updated.
    pending_num_cells: dict[int, PendingNumCell] = field(default_factory=dict)
    pending_num_counter: int = 0
    # Bumped whenever a cell gains evidence or settles: what a cell's
    # evidence joins to so far is cached against it.
    pending_num_epoch: int = 0
    # How many `trial_scope`s are open. The deferred operations over pending
    # numbers are function state a trial rolls back, so they resolve only
    # outside one: a resolution inside would be replayed after the rollback.
    trial_depth: int = 0
    # The one expression now being analyzed as a consumer a pending number
    # may reach; any other gets the local settled first.
    pending_ok_node: TpyExpr | None = None
    # The one expression now being analyzed as a consumer a list whose
    # element is not decided yet may reach; any other gets the element
    # settled first.
    pending_list_ok_node: TpyExpr | None = None
    # The expressions now being analyzed as truth tests (a condition and
    # the operands it distributes over): a container whose leaves are not
    # decided yet reaches them undecided (its truth is its size).
    truth_ok_node: tuple = ()
    # Innermost last: the calls under analysis whose arguments are scored
    # or inferred from before a parameter decides them (`OpenCallArgs`).
    open_call_args: list[OpenCallArgs] = field(default_factory=list)

    # --- Test annotation facts (persist across functions) ---
    declared_var_types: dict[tuple[int, str], TpyType] = field(default_factory=dict)
    ptr_deref_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    subscript_bounds_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    div_zero_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    cast_safe_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    # (def line, func name) -> FunctionInfo, for # tpyc: frame_send/frame_sync.
    # Holds the fi, not the answer -- frame traits resolve lazily because
    # awaited sub-frames may belong to bodies analyzed later.
    frame_fact_fns: dict = field(default_factory=dict)

    # --- Import tracking ---
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    # Source line of each user-module import statement, so a diagnostic about
    # an imported NAME can point at the import instead of the whole file
    # (the name tuples in `imports` carry no location of their own).
    user_module_import_lines: dict[str, int] = field(default_factory=dict)
    bare_module_imports: set[str] = field(default_factory=set)
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)

    # --- Cross-module support ---
    module_name: str = "__main__"
    # File-derived module name used by codegen for namespaces. Identical to
    # `module_name` for non-entry modules; for the entry point, codegen uses
    # the file name while `module_name` stays "__main__" for runtime
    # `__name__` semantics. Set alongside `module_name` in `bind_imports`;
    # consumed by `_register_union_wrappers` so the wrapper-index origin
    # matches the comparison codegen does against its own `module_name`.
    cpp_module_name: str = "__main__"
    module_cpp_namespace: str | None = None  # from `__tpy__` "cpp_namespace"
    # The current module's opaque plugin payload (`FrontendModule.macro_data`,
    # `TpyModule.macro_data`). Stashed per-module in `bind_imports` so a
    # `@call_macro` -- which runs during Pass 7 with no module passed to it --
    # can reach it via `CallMacroContext.module_data`. None for parser-produced
    # modules. (Function macros instead receive it as an explicit argument.)
    macro_data: 'Any' = None
    # Reference to the current module's `CompiledModule.exports`. Set by
    # `Compiler._finalize_declarations` so the registration paths can
    # find pre-populated skeleton RecordInfo / FunctionInfo /
    # ProtocolInfo / enum NominalType objects (created by
    # `_pre_populate_decl_exports`) and mutate them in place rather than
    # allocating new ones. Peer modules' analyzer registries, populated
    # by `bind_imports` via `module_info.{records,functions,...}`,
    # capture references to the same skeletons; in-place mutation lets
    # those peer registries see the freshly-finalized data without a
    # post-hoc resync. None when no pre-populated exports are
    # attached (e.g. ad-hoc analyzer construction in tests / REPL).
    module_decl_exports: 'object | None' = None
    # Reference to the current module's per-module attribute table
    # (aliased from `CompiledModule.module_attributes`). Registration
    # paths install bindings here as they mint/adopt records,
    # functions, protocols, enums, variables, type aliases, and
    # imports. None when no compiler-provided table is attached
    # (ad-hoc analyzer construction in tests / REPL), in which case
    # `install_binding` no-ops.
    module_attributes: 'dict | None' = None
    top_level_decls: dict[str, int] = field(default_factory=dict)
    # The module-level statement currently being analyzed at depth 0, set by
    # `_analyze_top_level`'s driver loop. A binding made by THIS statement is
    # the module's namespace-scope storage; one made inside an `if`/`for`/
    # `try` body at module level is a local of the generated `__tpy_init`,
    # with no slot to export, import or borrow from.
    current_module_stmt: 'TpyStmt | None' = None
    # Defining modules this module's code references. Populated by
    # sema.reach_analysis after analysis completes; consumed by codegen
    # to drive transitive include emission.
    reached: set[str] = field(default_factory=set)

    # --- Owning-slot copy verdicts (sema/own_copy.py) ---
    # Every obligation this module's bodies recorded, so the ones no
    # instantiation reached can be dropped once the workspace is analyzed.
    own_copy_obligations: list = field(default_factory=list)
    # Instantiations resolved in this module with a fully concrete
    # substitution -- the roots the discharge walks from.
    own_copy_roots: list = field(default_factory=list)
    _own_copy_seen_roots: IdentityMap = field(default_factory=IdentityMap)
    # Generic callees instantiated with a payload that still names a type
    # param. Both this and `own_copy_obligations` are append-only and sliced
    # per body at `own_copy_mark` / `own_copy_drain`, rather than held on
    # `func`: nested-def analysis deep-copies and then discards that state,
    # which would strand a closure's obligations away from the placeholder
    # they hold.
    own_copy_forwards: list = field(default_factory=list)
    # The spans of both lists a genexpr's function filled while it was analyzed
    # in the middle of an enclosing body: that function drained them already,
    # so the enclosing body's drain leaves them out.
    own_copy_inner_spans: list = field(default_factory=list)

    # Top-level names `register_globals` put in `global_scope` from their
    # ANNOTATION, before any statement was analyzed. Distinct from the
    # scope's membership, which also carries inferred top-level bindings
    # once their init has been analyzed.
    preregistered_globals: set[str] = field(default_factory=set)

    # Module globals some function in this module rebinds through `global`,
    # mapped to that function's name (`global_write_facts`). Decided before
    # any body is analyzed, because a borrow return in the FIRST body has to
    # know about a rebind in the last one.
    rebound_globals: dict[str, str] = field(default_factory=dict)

    # --- Final globals ---
    final_globals: set[str] = field(default_factory=set)
    analyzed_finals: set[str] = field(default_factory=set)

    # --- Builtins ---
    builtin_names: dict[str, TpyType] = field(default_factory=dict)

    # --- Namespace hierarchy (global/builtins persist) ---
    builtins_ns: Namespace | None = None
    macro_ns: Namespace | None = None
    global_ns: Namespace | None = None

    # --- Macro dep modules (populated after macros run) ---
    macro_dep_modules: set[str] = field(default_factory=set)

    # --- Recursive union aliases (self- or mutually-referencing) ---
    recursive_union_names: set[str] = field(default_factory=set)

    # --- Module resolver (parser-owned) ---
    # The `TypeResolver` the parser built for this module.  Wired by
    # `sema.analyzer.analyze()` from `module.resolver`.  Consumed by:
    #   - `_infer_field_type_from_default` to look up same-module
    #     records via the parser's registry (not yet in `ctx.registry`
    #     when the field-default pass runs).
    #   - `register_record`'s macro post-resolve step (resolves
    #     TypeRefNodes in bodies of methods added by class macros,
    #     which join the record after the module-level `resolve_refs`
    #     walk has already finished).
    # No other sema code should touch this; the main parser ->
    # TpyType binding happens via `parse.resolve_refs.resolve_refs`.
    parser_resolver: 'TypeResolver | None' = None

    # --- Control flow (persistent) ---
    in_comprehension: int = 0
    # Nesting depth of CONDITIONALLY EVALUATED operands (a logical RHS, a
    # ternary arm, a chained comparator past the first). An argument built
    # there runs only when the branch is taken -- except where the value
    # cannot move and so cannot be deferred into the branch, which
    # `warn_cond_operand_eager_arg` reports. Zeroed for the duration of a
    # body that carries its own region (`ScopeTracker.deferred_body`).
    cond_operand_depth: int = 0
    is_top_level: bool = False
    # REPL mode: allow @error_return calls at top level (unwrap with panic)
    allow_top_level_error_unwrap: bool = False
    # Re-entry guard for `is_type_nocopy` on a `RecursiveAliasInstanceType`:
    # the wrapper's own self-reference (`list[Tree[T]]` member) would otherwise
    # recurse into Tree[T] forever. Conservative False at the recursion point
    # mirrors `RecursiveAliasInstanceType.is_value_type`'s guard in typesys.
    _evaluating_alias_nocopy: set = field(default_factory=set)
    # Re-entry guard for `is_type_non_copyable`'s record-field walk: a
    # self-/mutually-referential record (`children: list[Node]`) would recurse
    # forever. Conservative False at the recursion point, mirroring the alias
    # guard above. is_type_nocopy needs no such set -- it walks type args only.
    _evaluating_record_noncopyable: set = field(default_factory=set)

    # --- Last-use tracking (shared with codegen, persists across functions) ---
    all_last_uses: IdentitySet = field(default_factory=IdentitySet)
    # Last-use reads whose active alias is read in the same statement (any
    # order): a by-VALUE owning slot moves the name while binding, which C++
    # may sequence before the alias read, so such a slot copies instead.
    same_stmt_alias_reads: IdentitySet = field(default_factory=IdentitySet)
    # Name reads that run more than once per evaluation of their statement
    # (a comprehension's per-item part, a lambda body): never a last use.
    repeated_reads: IdentitySet = field(default_factory=IdentitySet)
    # Sources that COPY into an `Own` slot, as declared by the warning: a
    # tuple-literal member, a consuming call's receiver that does not own
    # its value, or a tuple name some element of which arrives borrowed --
    # mapped to the elements it copies (None: every borrowed one; None too
    # for any other source). The lowering builds exactly these copies --
    # never a move out of storage that outlives the call, never an
    # undeclared copy.
    own_element_copies: IdentityMap = field(default_factory=IdentityMap)
    # The declared-copy NAMES among them whose binding also holds an element
    # by value (mixed): no whole-tuple lift may build their copy.
    own_element_mixed: IdentitySet = field(default_factory=IdentitySet)
    # Pointer-tuple NAME reads an owning sink takes elements out of, with
    # their copies (if any) in `own_element_copies`: the lowering moves an
    # element out of a read only where sema declared that read here.
    declared_tuple_reads: IdentitySet = field(default_factory=IdentitySet)
    # `t[i]` reads that MOVE owned element `i` out of a tuple the body
    # owns, at the tuple's last use: a return (`check_own_lvalue_into_own`)
    # or an `Own[T]` argument (`passed_owned_element`).
    element_moves: IdentitySet = field(default_factory=IdentitySet)
    # Returned NAMES a closure of the body still reads after the return
    # (`returned_captured_name`) at an owning slot: sema warned the copy,
    # and the read is copied, never moved.
    live_name_copies: IdentitySet = field(default_factory=IdentitySet)
    # Per call whose callable argument takes an `Own[X]` parameter fed by an
    # `Iterable[T]` argument (`map(take, ks, rows)`): argument index ->
    # the effective `Iterable[Own[X]]` slot, an owning element slot like a
    # spelled one.
    own_callback_feeds: IdentityMap = field(default_factory=IdentityMap)
    # The `return <name>` values under a non-suspending finally
    # (liveness.collect_finally_return_candidates; every such return -- the
    # finally can reach the local through aliases/closures, so candidacy is
    # structural, not read-based). Return analysis re-marks eligible
    # reference-type shapes as last-use and stamps
    # TpyReturn.finally_deferred_capture (codegen then materializes the
    # return value after the inline finally chain).
    finally_return_candidates: IdentitySet = field(default_factory=IdentitySet)
    # The returned name members a finally REBINDS: they keep the eager
    # capture, a copy, which a non-copyable type cannot take.
    finally_rebound_returns: IdentitySet = field(default_factory=IdentitySet)

    # Per-function states of this module's generic generators whose yield-slot
    # verdict is still owed. The verdict reads `return_borrows_from` of the
    # callees behind its yield sources, and that fact is filled at the END of
    # each callee's body analysis -- so a verdict settled during the
    # generator's own body would answer differently depending on whether the
    # callee happens to sit above or below it. The whole state is kept (not a
    # copy) because the verdict walk asks this function's params, scope and
    # frame locals; nothing mutates it once the body is done. The scope and
    # namespace ride in the record rather than on the state, which the
    # body-exit path nulls.
    deferred_generic_yield_settles: list['ParkedFunctionState'] = field(default_factory=list)
    # Scope escapes whose diagnostic waits for a callee's borrow fact
    # (`ScopeTracker.settle_deferred_escapes`).
    deferred_escapes: list['DeferredEscape'] = field(default_factory=list)
    # Names the scope-escape check hoisted inside a NESTED def, keyed by its
    # function node: the nested body's own tracking state is discarded at
    # scope exit, so they wait here for the analyzer to harvest them into
    # `function_hoisted_vars` with the enclosing function's results.
    nested_def_hoisted_vars: IdentityMap = field(default_factory=IdentityMap)
    # Per loop statement: the names a pass of it binds
    # (`prescan.LoopBindings`), walked once per outermost loop and shared
    # with the frame layout.
    loop_bindings: IdentityMap = field(default_factory=IdentityMap)
    # Per statement list: its `prescan.BlockSummary`, built once by the
    # body's pre-scan and shared by every write view over it. Not on
    # the function state, which `save_function_state` deep-copies.
    write_summaries: IdentityMap = field(default_factory=IdentityMap)
    # This module's generator and async functions, whose close fact is
    # decided once every body is analyzed (sema/frame_close.py).
    frame_close_fis: list[FunctionInfo] = field(default_factory=list)
    # The calls a loop makes while a frame it binds is held across its back
    # edge, decided once mutation facts are final (sema/loop_frames.py).
    pending_loop_frame_calls: list['PendingLoopFrameCall'] = field(
        default_factory=list)
    # Loops whose source a write may reach only through function locals,
    # decided at the end of their body (sema/loop_frames.py).
    pending_source_writes: list['PendingSourceWrite'] = field(
        default_factory=list)
    # Generators bound in a `with` block that live past it, checked against
    # what the managers' exits may write once those facts are final.
    pending_with_exit_checks: list['WithExitCheck'] = field(
        default_factory=list)

    # --- Generator-expression functions ---
    # Set by the analyzer: registers and analyzes a genexpr's function at the
    # expression that creates it, under a function state of its own.
    analyze_genexpr_function: 'Callable[[TpyFunction, tuple[tuple[str, LoanInfo], ...]], None] | None' = None
    # Queues an implicit dunder call on a readonly receiver for the
    # workspace-wide finalize pass (`CallAnalyzer.pending_readonly_receiver_checks`),
    # installed by the analyzer: `(callee, dunder, site)`.
    queue_readonly_receiver_check: 'Callable[[tuple], None] | None' = None
    genexpr_counter: int = 0
    # genexpr function -> the function or method whose emitted body creates
    # its frame (through any nested def or genexpr between them); None at
    # module level. A table, not a node field: a pointer from the function
    # back up to its creator would make the parse tree cyclic.
    genexpr_roots: 'IdentityMap[TpyFunction, TpyFunction | None]' = field(
        default_factory=IdentityMap)
    # Per outermost body, the generator expressions created in it, in
    # creation order (`GenexprCreation`); `finalize_pointer_tuples` takes
    # them with the body's final sets.
    genexpr_creators: 'IdentityMap[TpyFunction, list[GenexprCreation]]' = field(
        default_factory=IdentityMap)
    # Bodies of the `for` statements whose iterable is being analyzed: a
    # genexpr created there is pulled once per iteration, so those bodies run
    # between its pulls.
    for_head_bodies: 'list[list[TpyStmt]]' = field(default_factory=list)
    # The genexprs created while the current statement is analyzed, each with
    # the captures whose narrowing it took: a statement that KEEPS a lazy value
    # (binds it to a name) lets the genexpr outlive it, which the statement
    # checks once its bound type is known.
    stmt_genexprs: 'list[tuple[TpyGeneratorExpression, tuple[str, ...]]]' = field(default_factory=list)
    # The module's top-level statements while they are analyzed: the body a
    # module-level statement's rebind scan looks through.
    module_top_level_stmts: 'list[TpyStmt] | None' = None

    # --- Consuming method tracking ---
    in_consuming_method: bool = False

    # --- Expression type hint ---
    # The hint the current expression is analyzed under, with which of its
    # positions an inferred local decides; set only via `slot_hint_scope`,
    # read through `expr_slot_hint` or `slot_hint_at`.
    current_slot_hint: 'SlotHint | None' = None
    # The whole value of the unannotated first binding `name = ...` being
    # analysed: a diagnostic on exactly this node can offer annotating the
    # name, which an already-declared name or any other value cannot take.
    name_initializer: 'TpyExpr | None' = None

    # --- Branch-declared variable tracking ---
    if_branch_decls: IdentityMap = field(default_factory=IdentityMap)
    # The same snapshot for an `if` that is the whole `else` body of another
    # `if`: each arm declares these names at its own binding, all with the
    # one type recorded here.
    arm_branch_decls: IdentityMap = field(default_factory=IdentityMap)
    # Per branching statement, the names first bound inside it that it did
    # NOT hoist: their storage is the block's. A loop-body local hoisted to
    # that statement must not take them for outer storage.
    block_locals_of: IdentityMap = field(default_factory=IdentityMap)

    # --- Extern symbol tracking ---
    extern_symbols: dict[str, str] = field(default_factory=dict)

    # --- Builder-trace fresh-name counters ---
    # hint -> next index. Shared across BuilderTraceExpander instances
    # so two functions that each open an ArgumentParser trace don't
    # both mint __tpy_builder_argparse_args_1, which would collide as
    # a top-level record/function name in the synthesized module.
    builder_trace_fresh_counters: dict[str, int] = field(default_factory=dict)

    # Lambdas whose result is a borrow: whether the borrow roots in a
    # temporary the body creates reads callee borrow facts, which are
    # complete only in the workspace-wide finalize pass.
    # Rolled back by `trial_scope`, so a candidate trial queues nothing.
    pending_lambda_borrow_checks: list = field(default_factory=list)

    # --- Diagnostics ---
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def is_readonly_name(self, name: str) -> bool:
        """Check if a variable has readonly provenance in its declared scope type.

        isinstance narrowing updates narrowed_types (not scope bindings), so the
        scope binding preserves ReadonlyType even when the expr type is narrowed
        to a concrete member. Reassignment updates the scope binding, so a
        non-readonly reassignment correctly clears this.
        """
        if self.func.current_scope is None:
            return False
        declared = self.func.current_scope.lookup(name)
        return declared is not None and isinstance(declared, ReadonlyType)

    def class_record_of(self, binding: NameBinding) -> RecordInfo | None:
        """The record a name binding denotes as a class: its own class
        binding -- `cls` included -- or an import (a builtin included) that
        resolves to a record."""
        if binding.kind == BindingKind.RECORD:
            # Authoritative: `cls` names its record, and get_record's
            # short-name key collides for same-named records across modules.
            return binding.record_info
        if (binding.kind == BindingKind.IMPORTED_NAME
                and binding.import_source is not None):
            module, original = binding.import_source
            return self.registry.find_record_by_qname(f"{module}.{original}")
        return None

    def get_module(self, module_name: str) -> object | None:
        """The attribute-table holder `resolve_type_path` walks: this
        module's own table (a module never finds its own ModuleInfo while it
        is analyzed), else the registered module's."""
        if module_name == self.module_name:
            return self
        return self.registry.get_module(module_name)

    def _scope_ns(self) -> Namespace | None:
        """The lexical scope names resolve in: the body being analyzed, else
        the module (declarations resolve where they are defined)."""
        return self.func.current_ns or self.global_ns

    def _module_of_path(self, root: str, segments: 'list[str]') -> str | None:
        """The module `root.segments...` names: a name bound to an imported
        module (`m`, an alias `mm`), or a dotted chain whose root is unbound
        or a module (`pkg.sub.mod`). None when the root is bound to
        anything but a module -- a local, parameter or class of that name
        shadows the module."""
        ns = self._scope_ns()
        binding = ns.lookup(root) if ns is not None else None
        if binding is not None and binding.kind != BindingKind.MODULE:
            return None
        if not segments:
            if binding is None:
                return None
            return binding.import_source[0] if binding.import_source else root
        # A dotted chain spells the module from its root name: `import
        # pkg.sub` binds `pkg` to the submodule it imported.
        dotted = ".".join((root, *segments))
        return dotted if self.registry.get_module(dotted) is not None else None

    def module_named_by(self, expr: TpyExpr) -> str | None:
        """The module an expression names (`m`, `mm`, `pkg.sub.mod`), or
        None. The head step of every module-qualified call and type path."""
        path = name_path(expr)
        return self._module_of_path(path[0], path[1:]) if path else None

    def type_ref(self, expr: TpyExpr, member: str | None = None) -> SymbolBinding | None:
        """The class or enum `expr` names -- `expr.member` when `member` is
        given -- or None. The head is a class / enum binding (local,
        from-imported, `cls`) or the longest prefix naming a module (`m`,
        `mm`, `pkg.sub.mod`); every further segment is a type nested in what
        the previous one reached (`resolve_type_path`). A local or
        parameter named like the head shadows it."""
        path = name_path(expr)
        if path is None:
            return None
        if member is not None:
            path.append(member)
        return self.type_ref_path(path)

    def type_ref_path(self, path: 'list[str]') -> SymbolBinding | None:
        """`type_ref` over the names of a dotted path (`["m", "Outer",
        "Err"]`), for a position that holds the names rather than an
        expression (an `except` clause)."""
        root, members = path[0], list(path[1:])
        ns = self._scope_ns()
        binding = ns.lookup(root) if ns is not None else None
        if binding is None or binding.kind == BindingKind.MODULE:
            for k in range(len(members) - 1, -1, -1):
                module = self._module_of_path(root, members[:k])
                if module is not None:
                    return resolve_type_path(self, module, members[k], members[k + 1:])
            return None
        if binding.kind == BindingKind.ENUM:
            if members:
                return None
            enum_type = binding.enum_type
            return SymbolBinding(
                local_name=root, kind=SymbolKind.ENUM, info=enum_type,
                defining_module=None, canonical_name=enum_type.name)
        record = self.class_record_of(binding)
        if record is None:
            if binding.kind != BindingKind.IMPORTED_NAME or binding.import_source is None:
                return None
            return resolve_type_path(self, *binding.import_source, members)
        if not members:
            return SymbolBinding(
                local_name=root, kind=SymbolKind.RECORD, info=record,
                defining_module=record.defining_module, canonical_name=record.name)
        return resolve_type_path(
            self, record.defining_module or self.module_name, record.name, members)

    def type_path_miss(self, path: 'list[str]', not_a_type: str) -> str | None:
        """Why a dotted path `type_ref_path` did not resolve: the longest
        prefix naming a module or class and the member it lacks, in
        CPython's AttributeError words -- or, when that member exists but
        is no class, that it is not `not_a_type`. None when no prefix
        resolves."""
        for k in range(len(path) - 1, 0, -1):
            prefix, missing = path[:k], path[k]
            spelled = ".".join(prefix)
            owner = self.type_ref_path(prefix)
            if owner is not None:
                if not self.class_has_member(owner, missing):
                    return f"class '{spelled}' has no attribute '{missing}'"
                return f"'{'.'.join(path)}' is not {not_a_type}"
            module = self._module_of_path(prefix[0], prefix[1:])
            if module is not None:
                if walk_attribute_chain(self, module, missing, lambda _b: True) is None:
                    return f"module '{spelled}' has no attribute '{missing}'"
                return f"'{'.'.join(path)}' is not {not_a_type}"
        return None

    def class_has_member(self, owner: SymbolBinding, name: str) -> bool:
        if owner.kind == SymbolKind.ENUM:
            info = enum_info_of(owner.info)
            return info is not None and name in info.members
        return name in self.registry.class_member_names(owner.info)

    def type_pair(self, a: TpyType, b: TpyType) -> tuple[str, str]:
        """`disambiguated_pair` for this module: two types that read alike
        are told apart by module, a class of this module keeping its name."""
        return disambiguated_pair(a, b, self.module_name)

    def type_of_ref(self, ref: SymbolBinding) -> TpyType:
        """The type a class or enum reference names (its instances' type)."""
        if ref.kind == SymbolKind.ENUM:
            return ref.info
        return record_owner(ref.info)

    def _resolve_loc(self, node: TpyExpr | TpyStmt | TpyRecord | None) -> SourceLocation | None:
        """Resolve source location from a node, with fallback to current function/record."""
        loc = getattr(node, 'loc', None) if node else None
        if loc is None and isinstance(self.func.current_function, TpyFunction):
            loc = self.func.current_function.loc
        if loc is None and self.record_ctx.record is not None:
            loc = self.record_ctx.record.loc
        return loc

    def error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        return SemanticError(message, self._resolve_loc(node))

    def error_from_loc(self, message: str,
                       loc: 'SourceLocation | None') -> SemanticError:
        """Create a SemanticError from a location the caller already has.

        Keeps `error`'s fallback to the enclosing function / record, so a
        diagnostic raised off a recorded line is never less located than one
        raised off a node."""
        return SemanticError(message, loc if loc is not None
                             else self._resolve_loc(None))

    def reject_resumable_nested_def_escape(
            self, name: str, node: 'TpyExpr | TpyStmt | None') -> None:
        """A nested def in an async def / generator is a member function of
        the resumable frame -- it cannot be returned or handed off as a
        value (the callable would dangle once the frame is gone, and a
        member function has no standalone C++ value form). Call it
        directly. No-op in plain sync functions (lambda form escapes fine).
        """
        func = self.func.current_function
        if not isinstance(func, TpyFunction) or not (func.is_async
                                                     or func.is_generator):
            return
        kind = "an async function" if func.is_async else "a generator"
        raise self.error(
            f"nested function '{name}' defined in {kind} cannot escape or "
            f"be passed as a value (it lives on the coroutine frame); call "
            f"it directly", node)

    def emit_error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> None:
        """Record an error diagnostic without raising (allows continued analysis)."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.ERROR, message, self._resolve_loc(node)))

    def warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, self._resolve_loc(node)))

    def warning_from_loc(self, message: str, loc: 'SourceLocation | None') -> None:
        """Record a warning diagnostic from a SourceLocation."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def type_param_bound(self, type_param_name: str) -> 'TpyType | None':
        """The declared bound for a type parameter in the current context.

        The enclosing function's own bounds win over the enclosing record's,
        so a method that shadows a class param reads its own.
        """
        func = self.func.current_function
        if (func is not None and isinstance(func, TpyFunction)
                and type_param_name in func.type_param_bounds):
            return func.type_param_bounds[type_param_name]
        if (self.record_ctx.type_param_bounds
                and type_param_name in self.record_ctx.type_param_bounds):
            return self.record_ctx.type_param_bounds[type_param_name]
        return None

    def _copy_is_unobservable(self, typ: 'TpyType') -> bool:
        """Whether a `ValueType` bound settles the copy question for `typ`.

        True only when the payload's reference-ness is DERIVED from its type
        params and every one of them carries the bound. A payload that is a
        reference type in its own right -- `list[T]`, `Array[T, N]`, a plain
        `class GContainer[T]` -- copies a struct at every instantiation, so
        no bound on `T` silences it; `reference_source_params` returns None
        for exactly those.

        `T: Copyable` deliberately does NOT silence it either: copyable is
        TPy's default, so that bound only says the instantiation is not
        non-copyable (`@nocopy`, or a record with `__del__`) -- it does not
        say the author meant to copy. Only `copy()` at the site says that.

        Every derived param must carry the bound: `tuple[K, V]` with a
        value-typed `K` and an unbounded `V` still copies a `V`.
        """
        params = own_copy.reference_source_params(typ)
        if not params:
            return False
        for name in params:
            bound = self.type_param_bound(name)
            if (not isinstance(bound, NominalType)
                    or bound.qualified_name() != qnames.VALUE_TYPE):
                return False
        return True

    def defer_own_copy_verdict(
        self, display: 'TpyType', target: 'TpyType', dest: str,
        node: 'TpyExpr | TpyStmt | None', kind: str = own_copy.KIND_SLOT,
        hint: str = "",
    ) -> bool:
        """Record an owning-slot copy whose payload is still open.

        Returns False -- the sink answers for itself -- when neither the
        stored value nor the slot names a type parameter. Otherwise the body
        warns HERE, at declaration time, so a library author reads the copy
        contract without instantiating anything; the warning stands whatever
        the instantiations turn out to be, and a non-copyable one (`@nocopy`,
        or a record with `__del__`) later answers the obligation with the
        located error, which `apply_own_copy_verdicts` puts in this
        diagnostic's place.

        Returns True without warning under a `T: ValueType` bound, which
        makes the copy unobservable. `copy()` at the site is the other
        silencer, and it is handled before the sink is reached.
        """
        if not (own_copy.type_has_type_param(target)
                or own_copy.type_has_type_param(display)):
            return False
        # Both sides describe the same copy, so silence needs both to agree
        # -- a side with no type params has no bound to consult and abstains.
        open_sides = [t for t in (target, display)
                      if own_copy.type_has_type_param(t)]
        if all(self._copy_is_unobservable(t) for t in open_sides):
            return True
        head = unwrap_readonly(display)
        if (kind != own_copy.KIND_ELEMENTS and isinstance(head, NominalType)
                and not head.is_protocol and not head.is_value_type()):
            # A reference-type head is copied whatever its arguments are.
            msg = own_copy.slot_message(head, head, dest, kind, hint,
                                        non_copyable=False)[1]
        else:
            msg = own_copy.hedge_message(display, dest, kind, hint)
        diag = Diagnostic(DiagnosticLevel.WARNING, msg,
                          self._resolve_loc(node))
        self.diagnostics.append(diag)
        obligation = own_copy.OwnCopyObligation(
            display_type=display, target_type=target, dest=dest, kind=kind,
            hint=hint, diag=diag)
        self.own_copy_obligations.append(obligation)
        return True

    def apply_own_copy_verdicts(self, verdicts: 'own_copy.OwnCopyVerdicts') -> None:
        """Compose this module's diagnostics from its hedges and the verdicts.

        A hedge some instantiation answered is replaced, at its position, by
        the verdicts in the order they resolved; a hedge the loop-copy path
        withdrew is no longer in the list and so gets nothing. Runs after
        every module's discharge, since the verdicts come from any module
        that instantiated this one's bodies, and before the duplicate
        collapse, so a body analyzed once per clone still collapses to one
        report.
        """
        promoted: IdentityMap = IdentityMap()
        for obligation in self.own_copy_obligations:
            replacement = verdicts.promoted(obligation)
            if replacement:
                promoted[obligation.diag] = replacement
        if not promoted:
            return
        composed: list[Diagnostic] = []
        for diag in self.diagnostics:
            composed.extend(promoted.get(diag, (diag,)))
        self.diagnostics[:] = composed

    def record_own_copy_instantiation(
        self, callee: object, subst: 'dict[str, TpyType | int]',
        is_record: bool = False,
    ) -> None:
        """Note that `callee` (a FunctionInfo, or a RecordInfo when
        `is_record`) was instantiated under `subst`.

        A substitution that still names a type parameter is the enclosing
        generic body's to forward: it is re-composed when that body is
        itself instantiated. A concrete one is a root the discharge starts
        from.
        """
        if not subst:
            return
        pairs = tuple(
            (name, value) for name, value in subst.items()
            if isinstance(value, TpyType)
        )
        if not pairs:
            return
        edge = own_copy.OwnCopyEdge(callee, pairs, is_record)
        if any(own_copy.type_has_type_param(value) for _, value in pairs):
            self.own_copy_forwards.append(edge)
            return
        try:
            # Dedup per callee IDENTITY -- the outer map owns the callee, so
            # a substituted RecordInfo that dies cannot have its address
            # recycled into a false hit (see tpyc/identity_map.py).
            seen = self._own_copy_seen_roots.setdefault(callee, set())
            key = (pairs, is_record)
            if key in seen:
                return
            seen.add(key)
        except TypeError:
            # An unhashable type arg cannot be deduped; recording the edge
            # twice only costs a repeated (idempotent) discharge.
            pass
        self.own_copy_roots.append(edge)

    def own_copy_mark(self) -> tuple[int, int, int]:
        """Where the body about to be analyzed starts contributing."""
        return (len(self.own_copy_obligations), len(self.own_copy_forwards),
                len(self.own_copy_inner_spans))

    def own_copy_drain(self, mark: tuple[int, int, int]) -> tuple[tuple, tuple]:
        """The obligations and instantiation forwards the body recorded."""
        inner = self.own_copy_inner_spans[mark[2]:]

        def outside(index: int, which: int) -> bool:
            return not any(s[which][0] <= index < s[which][1] for s in inner)
        obligations = tuple(
            o for i, o in enumerate(self.own_copy_obligations[mark[0]:], mark[0])
            if outside(i, 0))
        seen: set = set()
        forwards = []
        for i, edge in enumerate(self.own_copy_forwards[mark[1]:], mark[1]):
            if not outside(i, 1):
                continue
            key = edge.key()
            if key in seen:
                continue
            seen.add(key)
            forwards.append(edge)
        forwards = tuple(forwards)
        return obligations, forwards

    def collapse_duplicate_diagnostics(self) -> None:
        """Drop exact repeats, keeping each diagnostic's first occurrence.

        A body analyzed more than once reports its diagnostics once. Bodies are
        cloned wherever one source construct expands into several ordinary ones
        -- `except (A, B):` into one handler per type, `@auto_readonly` into a
        mutable/const method pair -- so each clone re-analyzes the same lines.

        Collapsing happens here, once analysis is over, rather than in the
        recording methods above: during analysis, callers index into
        `diagnostics` (`compatibility.py` records the position of a loop-copy
        warning for `statements.py` to delete once the loop proves to move
        rather than copy). A recorder that skipped a duplicate would shift
        those positions onto unrelated diagnostics.
        """
        seen: set[tuple[object, ...]] = set()
        kept: list[Diagnostic] = []
        for d in self.diagnostics:
            loc = d.loc
            key = ((d.level, d.message, loc.file, loc.line, loc.column) if loc
                   else (d.level, d.message))
            if key in seen:
                continue
            seen.add(key)
            kept.append(d)
        self.diagnostics[:] = kept

    def nocopy_reason(self, typ: TpyType) -> str:
        """Return a human-readable reason why a type is nocopy.

        For explicitly @nocopy types returns "@nocopy type 'X'".
        For implicitly nocopy types (propagated from fields) returns a message
        explaining which field caused it.
        """
        inner = unwrap_readonly(typ)
        if isinstance(inner, OwnType):
            inner = inner.wrapped
        record = self.registry.get_record_for_type(inner)
        if record is None:
            return f"non-copyable type '{typ}'"
        # Walk fields to find the nocopy one (implicitly propagated)
        for f in record.fields:
            if self.is_type_nocopy(f.type):
                return (
                    f"non-copyable type '{typ}' (field '{f.name}' "
                    f"has non-copyable type '{f.type}')"
                )
        # Check type arguments for generic instantiations
        if isinstance(inner, NominalType) and inner.type_args:
            if record is None or not record.has_copy:
                for arg in inner.type_args:
                    if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                        return (
                            f"non-copyable type '{typ}' (type argument "
                            f"'{arg}' is non-copyable)"
                        )
        # Check parents
        for p in record.parents:
            parent_rec = self.registry.get_record_for_type(p)
            if parent_rec is not None and parent_rec.is_nocopy:
                return (
                    f"non-copyable type '{typ}' (parent '{p}' "
                    f"is non-copyable)"
                )
        # Explicitly decorated with @nocopy (or builtin nocopy)
        return f"@nocopy type '{typ}'"

    def check_coro_move_unstarted(self, expr: 'TpyExpr', what: str) -> None:
        """Reject moving a bound coroutine a method call may have started."""
        if not isinstance(expr, TpyName):
            return
        line = self.func.started_coro_locals.get(expr.name)
        if line is None:
            return
        raise self.error(
            f"cannot move coroutine '{expr.name}' {what}: the call on "
            f"line {line} may have started it, and a started coroutine "
            f"cannot be moved; await it where it is, or bind a new "
            f"coroutine to a new name",
            expr)

    def is_type_nocopy(self, typ: TpyType) -> bool:
        """Check if a type is nocopy, recursively unwrapping wrappers and generics.

        For generic instantiations like Box[NocopyType], checks whether any
        type argument is nocopy. Respects __copy__ escape hatch: if the
        containing record defines __copy__, type-arg nocopy is suppressed.
        """
        if isinstance(typ, ReadonlyType):
            return self.is_type_nocopy(typ.wrapped)
        if isinstance(typ, OwnType):
            return self.is_type_nocopy(typ.wrapped)
        # A generator / coroutine object is non-copyable by the language's
        # own rule, as a @nocopy record is by its declaration.
        if isinstance(typ, ConcreteFrameType):
            return True
        if isinstance(typ, OptionalType):
            return self.is_type_nocopy(typ.inner)
        if isinstance(typ, TupleType):
            for e in typ.element_types:
                if isinstance(e, TpyType) and self.is_type_nocopy(e):
                    return True
            return False
        # Generic recursive alias instance: a @nocopy alternative makes the
        # wrapper's std::variant non-copyable. Mirrors the TupleType walk
        # above; uses the unified alternatives accessor. The recursive
        # alternative (e.g. `list[Tree[T]]`) re-enters Tree[T] -- conservative
        # False at the recursion point matches `is_value_type`'s guard.
        if isinstance(typ, RecursiveAliasInstanceType):
            key = (typ.qname, typ.type_args)
            if key in self._evaluating_alias_nocopy:
                return False
            self._evaluating_alias_nocopy.add(key)
            try:
                for m in (recursive_union_alternatives(typ) or ()):
                    if isinstance(m, TpyType) and self.is_type_nocopy(m):
                        return True
                return False
            finally:
                self._evaluating_alias_nocopy.discard(key)
        record = self.registry.get_record_for_type(typ)
        if record is not None and record.is_nocopy:
            return True
        if isinstance(typ, NominalType) and typ.type_args:
            if record is not None and record.has_copy:
                return False
            for arg in typ.type_args:
                if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                    return True
        return False

    def is_type_non_copyable(self, typ: TpyType) -> bool:
        """Check if a type cannot be copied at C++ level.

        Superset of is_type_nocopy: also counts records with __del__ (which
        deletes copy ops in the generated struct), and walks the parent
        chain for @nocopy or __del__ (which implicitly deletes copy in
        the child). Propagates through record fields and type args.
        Respects __copy__ escape hatch.

        Used to upgrade "copies X into field/container" warnings to errors --
        the generated C++ would otherwise hit a deleted copy ctor.

        Kept separate from is_type_nocopy because the latter feeds into
        analyzer-level `is_nocopy` propagation and user-facing "@nocopy"
        diagnostics, where the narrower "user declared intent" meaning is
        wanted. This helper is strictly about "will C++ reject the copy?".
        """
        if isinstance(typ, ReadonlyType):
            return self.is_type_non_copyable(typ.wrapped)
        if isinstance(typ, OwnType):
            return self.is_type_non_copyable(typ.wrapped)
        # A generator or coroutine object is one running frame; CPython
        # refuses to copy one too (TypeError).
        if isinstance(typ, ConcreteFrameType):
            return True
        if isinstance(typ, OptionalType):
            return self.is_type_non_copyable(typ.inner)
        if isinstance(typ, TupleType):
            for e in typ.element_types:
                if isinstance(e, TpyType) and self.is_type_non_copyable(e):
                    return True
            return False
        # Mirrors the is_type_nocopy branch: a non-copyable alternative
        # propagates through the wrapper's std::variant. Re-entry returns
        # False to match the value/nocopy recursion guards (the recursive
        # alternative re-enters the same instance).
        if isinstance(typ, RecursiveAliasInstanceType):
            key = (typ.qname, typ.type_args)
            if key in self._evaluating_alias_nocopy:
                return False
            self._evaluating_alias_nocopy.add(key)
            try:
                for m in (recursive_union_alternatives(typ) or ()):
                    if isinstance(m, TpyType) and self.is_type_non_copyable(m):
                        return True
                return False
            finally:
                self._evaluating_alias_nocopy.discard(key)
        # Abstract @dynamic protocol bases have pure virtuals and the
        # concrete size depends on the dynamic type, so they have no usable
        # copy/move ctor at the C++ level.
        if is_dyn_protocol(typ):
            return True
        record = self.registry.get_record_for_type(typ)
        if record is None:
            return False
        if record.has_copy:
            return False
        if record.is_nocopy or record.has_del:
            return True
        # Re-entry guard for self-/mutually-referential records: a field or type
        # arg whose type cycles back here (e.g. a tree node `children: list[Node]`)
        # would otherwise recurse forever through the walks below. Conservative
        # False at the recursion point matches the recursive-alias guard above and
        # is_value_type's -- a cycle alone never deletes copy; the answer is
        # decided by the non-cyclic fields/parents the first entry still walks.
        if typ in self._evaluating_record_noncopyable:
            return False
        self._evaluating_record_noncopyable.add(typ)
        try:
            # Inheritance: recurse into each direct parent so each parent's own
            # logic (including its has_copy barrier up its chain) applies
            # independently. For multi-base, any direct parent being non-copyable
            # makes the child non-copyable; for single-parent, parent.has_copy
            # correctly short-circuits the parent's recursion, matching the old
            # `break`-on-has_copy behavior.
            for p in record.parents:
                if self.is_type_non_copyable(p):
                    return True
            if isinstance(typ, NominalType) and typ.type_args:
                for arg in typ.type_args:
                    if isinstance(arg, TpyType) and self.is_type_non_copyable(arg):
                        return True
            for f in record.fields:
                if self.is_type_non_copyable(f.type):
                    return True
            return False
        finally:
            self._evaluating_record_noncopyable.discard(typ)

    def local_decl_type(self, name: str) -> TpyType | None:
        """The local's DECLARED slot type -- what the slot may hold, which a
        later rebind does not change.

        `var_types` carries the deduction and OwnType overrides stamped on the
        decl node (a pending literal resolved after the fact lands there), so
        it wins over the raw annotation. Returns None for a name with no
        declaration of its own -- a parameter, a loop variable, a global.
        Every consumer that asks what a local's slot holds reads this: the
        namespace binding answers the narrower "what was last stored".
        """
        decl = self.func.var_decl_by_name.get(name)
        if decl is None:
            return None
        t = self.var_types.get(decl)
        if t is None and isinstance(decl.type, TpyType):
            t = decl.type
        return t

    def get_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression, stripping Ref and Own.

        Ref/Own are internal annotations for reference provenance and
        ownership; callers that need bare types (codegen, type resolution,
        narrowing) should not see them.  ReadonlyType is preserved because
        codegen needs it for const emission.  The assignment handler uses
        the analyze_expr return value directly (which preserves Ref/Own)
        for copy-warning detection.
        """
        typ = self.expr_types.get(expr)
        if typ is None:
            return None
        typ = unwrap_ref_type(typ)
        if isinstance(typ, OwnType):
            typ = typ.wrapped
        return typ

    def get_raw_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression WITHOUT stripping qualifiers.

        Used by copy-warning detection to see Ref/Own qualifiers.
        """
        return self.expr_types.get(expr)

    def container_record(self, literal_id: int
                         ) -> 'ContainerLiteralInfo | None':
        """The record of the container literal `literal_id` names."""
        return self.container_literals.get(literal_id)

    def list_literal(self, literal_id: int | None) -> ListLiteralInfo | None:
        """The record of the list literal `literal_id` names: the one a
        list's representation facts (Array or list) are kept on."""
        rec = self.container_literals.get(literal_id)
        return rec if isinstance(rec, ListLiteralInfo) else None

    def set_expr_type(self, expr: TpyExpr, typ: TpyType) -> None:
        """Cache the type of an expression."""
        self.expr_types[expr] = typ
        # A COMPOSITE carrying a Pending* leaf (a non-array comprehension's
        # `list[Pending]`, a tuple of list literals) is read straight off this
        # cache by the var-decl codegen fallback; record it so resolve_all can
        # finalize just this node rather than sweep the module-wide cache.
        # CROSS-PASS INVARIANT: a BARE Pending* literal is excluded here ONLY
        # because it is a tracked literal whose own cache entry is rewritten to
        # the resolved type by _apply_container_resolution (which runs before
        # _finalize_pending_in_bindings). If a future path ever caches a bare
        # Pending* for a node that does NOT flow through that resolution, this
        # exclusion would silently skip it and reintroduce the codegen crash --
        # the recorded-composite completeness check in
        # _finalize_pending_in_bindings guards the composite half of that.
        # contains_pending_leaf fast-returns on leaves.
        if not isinstance(typ, PENDING_CONTAINER_TYPES) and contains_pending_leaf(typ):
            self.func.pending_composite_exprs.append(expr)
        elif (isinstance(typ, PENDING_CONTAINER_TYPES)
              and (any(isinstance(p, UnknownElementType)
                       for p in typ.parts())
                   or typ.inner_types())):
            # A read of a container literal whose cells decide its leaves
            # (its inner types are its parts holding them), or of an empty
            # one, which may be typed before its cells are born: the record
            # names the type it resolves to.
            self.func.pending_container_exprs.append(expr)

    def record_branch_decls(self, stmt: TpyStmt, mapping: dict, *,
                            per_arm: bool = False) -> dict:
        """Store a branch-decl snapshot for `stmt`, registered for the
        pending-container finalization in resolve_all (see
        pending_branch_decl_maps). Incremental callers keep mutating the
        one registered dict. `per_arm` places the declarations in each arm
        (`arm_branch_decls`) instead of in front of `stmt`."""
        for typ in mapping.values():
            if typ is not None:
                self._force_branch_decl_lists(typ)
        table = self.arm_branch_decls if per_arm else self.if_branch_decls
        if not per_arm:
            # Declared in front of `stmt`: the block that holds it. A `stmt`
            # off the open stack (an arm's own statement) keeps the deeper
            # depth, which only owns a view more often.
            for i, open_stmt in enumerate(self.func.compound_stack):
                if open_stmt is stmt:
                    for name in mapping:
                        self.func.decl_block_depth[name] = i
                    break
        existing = table.get(stmt)
        if existing is not None:
            existing.update(mapping)
            return existing
        table[stmt] = mapping
        self.func.pending_branch_decl_maps.append(mapping)
        return mapping

    def _force_branch_decl_lists(self, typ: TpyType) -> None:
        """Force pending list literals reaching a branch-decl slot off the
        Array optimization: sibling branches may bind literals of different
        sizes into the one pre-declared slot (the snapshot sees only one
        literal, and a handler/arm bind is not a visible reassignment), so
        only the plain list form is size-safe."""
        if isinstance(typ, PendingListType):
            info = self.list_literal(typ.literal_id)
            if info is not None:
                info.is_mutated = True
            # inner_types() does not traverse a Pending's element type, and
            # a nested literal ([[1, 2]] vs [[3]]) has the same shared-slot
            # size hazard one level down.
            self._force_branch_decl_lists(typ.element_type)
        for inner in typ.inner_types():
            self._force_branch_decl_lists(inner)

    def default_int_for_literal(
        self,
        typ: TpyType,
        warn_node: TpyExpr | TpyStmt | None = None,
        rebinding: bool = False,
    ) -> TpyType:
        """Resolve configured default-int, with range-safe fallback for
        literals; the warning says whether the literal gives a variable its
        first type or promotes one (`rebinding`)."""
        if not isinstance(typ, IntLiteralType):
            return self.default_int_type
        default_tr = int_traits_of(self.default_int_type)
        if (
            default_tr is not None
            and typ.value is not None
            and not (default_tr.min_value <= typ.value <= default_tr.max_value)
        ):
            if warn_node is not None:
                what = ("promoting variable to int (BigInt)" if rebinding
                        else "inferring int (BigInt)")
                self.warning(
                    f"Integer literal {typ.value} is outside default "
                    f"{self.default_int_type} range; {what}.",
                    warn_node,
                )
            return BIGINT
        return self.default_int_type

    def reset_function_tracking(self) -> None:
        """Reset all per-function tracking state."""
        self.func = FunctionTrackingState()

    def save_function_state(self) -> FunctionTrackingState:
        """Snapshot per-function state (for nested-def and trial isolation).

        The restore installs the SNAPSHOT, so anything the enclosing analysis
        goes on using must survive the round trip as ITSELF. Two groups do:
        the live scope, namespaces and function node (`LIVE_HANDLE_FIELDS`),
        carried over unchanged; and every parse node and registry
        `FunctionInfo` the copy reaches, wherever it sits -- an identity KEY
        (`IdentityMap` / `IdentitySet`, e.g. `pre_analyzed_method_args`), a
        container value, or an attribute of a state object. Each
        `__deepcopy__` says why. Callers get the live handles back from
        `restore_function_state` and must not re-attach them themselves.
        """
        return deepcopy(self.func)

    def restore_function_state(self, saved: FunctionTrackingState) -> None:
        """Restore per-function state from a snapshot."""
        self.func = saved

    @contextmanager
    def reenter(self, parked: ParkedFunctionState
                ) -> Iterator[FunctionTrackingState]:
        """Run a post-body check under the function state, node, scope and
        namespace `parked` kept; the live state, and what the parked state
        itself held, come back after."""
        live, state = self.func, parked.state
        kept = (state.current_function, state.current_scope, state.current_ns)
        self.restore_function_state(state)
        state.current_function = parked.func
        state.current_scope = parked.scope
        state.current_ns = parked.ns
        try:
            yield state
        finally:
            (state.current_function, state.current_scope,
             state.current_ns) = kept
            self.restore_function_state(live)

    @property
    def expr_slot_hint(self) -> 'SlotHint | None':
        """The hint in force for the expressions analyzed now. A fill-only
        hint (`SlotHint.fill`) is not one: it types only its own argument
        node, which asks for it through `slot_hint_at`."""
        h = self.current_slot_hint
        return None if h is not None and h.is_fill else h

    @property
    def expr_type_hint(self) -> TpyType | None:
        """The type of `expr_slot_hint`, for readers that ask no provenance."""
        return self.expr_slot_hint.type if self.expr_slot_hint else None

    def slot_hint_at(self, expr: 'TpyExpr') -> 'SlotHint | None':
        """The hint `expr` itself is analyzed under: the one in force, or a
        fill-only hint set for exactly this node."""
        h = self.current_slot_hint
        if h is not None and h.is_fill:
            return h if h.fill_node is expr else None
        return h

    @contextmanager
    def slot_hint_scope(self, hint: 'SlotHint | None') -> Iterator[None]:
        saved = self.current_slot_hint
        self.current_slot_hint = hint
        try:
            yield
        finally:
            self.current_slot_hint = saved

    def call_scope_of(self, cells: 'list[PendingNumCell]'
                      ) -> OpenCallArgs | None:
        """The innermost open call whose arguments handed over one of
        `cells` (`OpenCallArgs.cells`): the call they are an argument of,
        whatever call is innermost now."""
        ids = {id(c) for c in cells}
        for scope in reversed(self.open_call_args):
            if any(id(c) in ids for c in scope.cells):
                return scope
        return None

    def call_scope_of_node(self, node: TpyExpr) -> OpenCallArgs | None:
        """The innermost open call `node` is an argument node of."""
        for scope in reversed(self.open_call_args):
            if any(n is node for n in scope.nodes):
                return scope
        return None

    @contextmanager
    def trial_scope(self) -> Iterator[None]:
        """Snapshot/restore for tentative semantic analysis (e.g. per-candidate
        lambda body trial under Regime C overload resolution).

        Wraps a body of analysis work whose state changes must be rolled back
        unconditionally on exit -- both on success and on exception.

        Snapshotted surfaces:

        - Per-function state (FunctionTrackingState) -- includes call_edges,
          mutated/struct/addr-escape/returned param sets, self-mutation flags,
          borrow tracker, definitely_assigned, narrowed_types, etc. Deep-copied
          since FunctionTrackingState contains nested mutable containers. The
          live scope, namespaces and function node are NOT part of the rollback
          -- `save_function_state` hands them back as themselves; the trial
          binds only into the lambda's own child scope/namespace.
        - Module-level type cache (``expr_types``) and the literal/view
          counters and registries (``literal_counter``,
          ``container_literals``, ``pending_generic_counter``,
          ``str_var_counter``, ``str_vars``, ``bytes_var_counter``,
          ``bytes_vars``).
        - The diagnostics list -- truncated to its pre-trial length so
          warnings/errors emitted during a rejected trial don't leak into
          the user-visible output.
        - The deferred lambda borrow checks, truncated the same way.

        Lambda AST mutations (``inferred_param_types``, ``inferred_return_type``,
        ``captured_names``, ``captures_by_value``) are the caller's
        responsibility -- save and restore them around the trial. They are
        not part of SemanticContext state.
        """
        saved_func = self.save_function_state()
        saved_expr_types = self.expr_types.copy()
        saved_literal_counter = self.literal_counter
        saved_container_literals = dict(self.container_literals)
        saved_pending_generic_counter = self.pending_generic_counter
        saved_str_var_counter = self.str_var_counter
        saved_str_vars = dict(self.str_vars)
        saved_bytes_var_counter = self.bytes_var_counter
        saved_bytes_vars = dict(self.bytes_vars)
        saved_diagnostics_len = len(self.diagnostics)
        saved_lambda_checks_len = len(self.pending_lambda_borrow_checks)
        self.trial_depth += 1
        try:
            yield
        finally:
            self.trial_depth -= 1
            del self.pending_lambda_borrow_checks[saved_lambda_checks_len:]
            self.restore_function_state(saved_func)
            self.expr_types.clear()
            self.expr_types.update(saved_expr_types)
            self.literal_counter = saved_literal_counter
            self.container_literals.clear()
            self.container_literals.update(saved_container_literals)
            self.pending_generic_counter = saved_pending_generic_counter
            self.str_var_counter = saved_str_var_counter
            self.str_vars.clear()
            self.str_vars.update(saved_str_vars)
            self.bytes_var_counter = saved_bytes_var_counter
            self.bytes_vars.clear()
            self.bytes_vars.update(saved_bytes_vars)
            del self.diagnostics[saved_diagnostics_len:]

    def mark_loop_var_mutated(self, name: str) -> None:
        """Mark a for-each loop variable as mutated (prevents const-ref binding)."""
        if name in self.func.loop_vars:
            self.func.mutated_loop_vars.add(name)

    def credit_iter_source_mutation(self, targets: 'list[str]',
                                    iterable: TpyExpr,
                                    reach: 'Container[str] | None' = None) -> None:
        """A mutated loop target lends a MUTABLE borrow of the storage it was
        iterated out of, so that storage cannot bind const either. Keyed on
        the iterable's ROOT rather than on the iterating construct, which is
        what makes the rule compose: an inner loop (or comprehension clause)
        marks the outer loop var, whose own iteration then marks ITS source
        in turn. Called after the targets' scope closes, innermost first.
        `reach`, when given, is the set of loop vars the edge may mark."""
        if not any(t in self.func.mutated_loop_vars for t in targets):
            return
        # The storage the iteration's loan is filed under, or -- for an
        # iterable no loan key names (a borrowing accessor hop, a property
        # getter with no `return_borrows_from`), whose loop var still cannot
        # bind const -- the climb's root through what a call lends writable.
        key = loan_key(iterable)
        root = (key.key.root if isinstance(key, LoanKey)
                else storage_roots(iterable, Use.HOLDER, None,
                                   MayLend(self)).writes().single())
        if (root is not None and root not in targets
                and (reach is None or root in reach)):
            mark_roots(self, [StorageKey.of_name(root)],
                       MarkRequest(params=False, loop_vars=True))

    def mark_loop_var_consumed(self, name: str) -> None:
        """Mark a for-each loop variable as consumed (copied into owned storage).

        This triggers auto-consuming iteration when the container is at last
        use, so elements are moved instead of copied.
        """
        if name in self.func.loop_vars:
            self.func.consumed_loop_vars.add(name)

    def own_scope_binding(self, name: str) -> tuple[bool, bool]:
        """`(bound, reaches)` for `name` against THIS function scope, from
        ONE namespace walk.

        `bound`: the scope binds the name -- its params, its locals, or a
        sub-scope of it (lambda param, comprehension variable, live
        `except ... as` / match capture). An enclosing function's or the
        module's binding of the same name is NOT this scope's, which is the
        whole point: Python resolves such a name against this scope alone
        once this scope binds it anywhere.

        `reaches`: a binding of it reaches this read. A SUB-scope binding is
        that scope's own and always reaches -- the enclosing scope's flow
        says nothing about it. A binding at this scope's OWN level is the
        definite-assignment question, and `definitely_assigned` is the fact
        that answers it: the namespace entry a binding inside an `if` /
        `for` / `while` / `try` / `with` / `match` body makes is never
        withdrawn, but the branch merges drop the name from the assigned
        set, which is exactly the path on which Python raises
        UnboundLocalError."""
        ns = self.func.current_ns
        root = self.func.own_ns
        if ns is None or root is None:
            assigned = name in self.func.definitely_assigned
            return assigned, assigned
        while ns is not None and ns is not self.global_ns:
            if ns.has_local(name):
                if ns is not root:
                    return True, True
                return True, name in self.func.definitely_assigned
            if ns is root:
                break
            ns = ns.parent
        return False, False

    def check_nested_def_shadowed_read(
            self, name: str, node: 'TpyExpr | TpyStmt | None') -> None:
        """Reject a read of `name` that Python resolves to a nested `def` of
        this scope but sema would resolve outward.

        Python binds a nested def's name for the WHOLE enclosing scope, while
        sema binds the FunctionInfo only when the walk reaches the `def`;
        without this, an earlier read silently reaches the shadowed module
        function / class / enum.

        `own_scope_binding` answers two of the three facts this needs.
        Ownership is asked, rather than `definitely_assigned`, because an
        `except ... as name` handler leaves the name in the assigned set
        after unbinding it, and the read that follows is exactly the one
        that must not resolve outward. Reachability is where a `def` inside
        an `if` / loop / `try` / `with` / `match` body differs: the name is
        this scope's local everywhere, but only the paths through the `def`
        bind it, so the others are CPython's UnboundLocalError.

        The third is `nested_def_block_dead`: such a `def` declares its
        callable for its block only, so a read past the block's end has
        nothing to reach even on the paths where the binding is definite --
        the whole-scope local Python gives the name has no counterpart
        here, and there is no outer `name` to fall back to."""
        # This runs on every bare-name read and every bare-name call, so the
        # two dict lookups that can possibly fire come before the namespace
        # walk. A nested def's own name is deliberately not seeded into
        # `nested_def_pending`, so the recursion leg needs its own test.
        recursive = (self.func.in_nested_def
                     and name == self.func.nested_def_name)
        if not recursive and name not in self.func.nested_def_pending:
            return
        bound_here, reaches = self.own_scope_binding(name)
        block = (None if recursive
                 else self.func.nested_def_block_dead.get(name))
        # The recursion leg is a restriction on the emitted lambda, not a
        # definite-assignment question, so a binding of the name is enough
        # there; the ordinary "may not be assigned" check covers the rest.
        if bound_here and (recursive or (reaches and block is None)):
            return
        # A `global` / `nonlocal` declaration takes the name out of this
        # scope's binding set entirely, so nothing here shadows anything.
        if (name in self.func.global_declarations
                or name in self.func.current_nonlocal_names):
            return
        if recursive:
            raise self.error(
                f"Recursive nested functions are not supported. "
                f"'{name}' cannot call itself", node)
        loc = self.func.nested_def_pending[name]
        where = f" on line {loc.line}" if loc is not None else ""
        if block is not None:
            raise self.error(
                f"'{name}' is not readable after {block}: the nested "
                f"function '{name}' defined{where} is bound inside that "
                f"block and does not outlive it, and a nested 'def' makes "
                f"its name a local of the whole enclosing scope, so this "
                f"read cannot reach an outer '{name}' -- move the 'def' "
                f"above the block, or rename it",
                node)
        # Which of the two remaining ways the binding fails to reach this
        # read is a source-order question, not a scope one: a `def` the walk
        # has already passed bound the name on SOME path only (the other arm
        # of the same branch), while one still ahead has bound it on none.
        read_loc = getattr(node, "loc", None)
        if (loc is not None and read_loc is not None
                and loc.line <= read_loc.line):
            raise self.error(
                f"'{name}' may not be assigned at this point: the nested "
                f"function '{name}' defined{where} binds it only on some "
                f"paths, and a nested 'def' makes its name a local of the "
                f"whole enclosing scope, so this read cannot reach an outer "
                f"'{name}' -- move the 'def' above the block, or rename it",
                node)
        raise self.error(
            f"'{name}' is read before the nested function '{name}' defined"
            f"{where} is bound. A nested 'def' makes its name a local of the "
            f"whole enclosing scope, so this read cannot reach an outer "
            f"'{name}' -- move the 'def' above this line, or rename it",
            node)

    def receiver_self_in_scope(self) -> bool:
        """True when 'self' in the current function scope is the method
        receiver (a closure captures it as the C++ this pointer -- an
        alias), not an ordinary local/param that happens to be named self."""
        f = self.func.current_function
        return (isinstance(f, TpyFunction) and f.is_method
                and not f.is_staticmethod)

    def is_module_slot_stmt(self, stmt: 'TpyStmt') -> bool:
        """True when `stmt` is the module-level statement whose bindings get
        namespace-scope storage: a direct element of the module's statement
        list, not one nested in a block body and not a synthetic init temp."""
        return self.is_top_level and stmt is self.current_module_stmt

    def define_module_global(self, name: str, var_type: 'TpyType | None',
                             line: int) -> 'TpyType | None':
        """Record a module-slot binding and return the type recorded. The
        single writer of the module's global tables, so the export
        collection, the storage-durability checks and the order-aware
        codegen all see one verdict. A tuple global borrows what its write
        parks, so no element is owned by the binding: the per-element `Own`
        is stripped here, once, for every reader.

        `top_level_decls` keeps the EARLIEST line, so a use between two
        re-declarations still resolves against the global."""
        if var_type is not None:
            var_type = global_tuple_binding_type(var_type)
        self.global_scope.define(name, var_type)
        prior = self.top_level_decls.get(name)
        self.top_level_decls[name] = line if prior is None else min(prior, line)
        return var_type

    def self_names_receiver(self) -> bool:
        """True when the name `self` here is a method receiver -- this
        method's, or an enclosing method's read from a nested def -- rather
        than an ordinary param or capture that happens to be named `self`
        (a free function's param, a generator expression's capture, a nested
        def's own param, which shadows the enclosing method's receiver)."""
        own = self.func.current_param_name_to_idx.get("self")
        if own is not None and own >= 0:
            return False
        return self.receiver_self_in_scope() or self.func.outer_self_is_receiver

    def self_receiver_index(self) -> int | None:
        """The index mutation facts file a write through `self` under: -1
        for a receiver (the self_mutated flag), the param's own index where
        `self` is an ordinary param, None where it is neither."""
        if self.self_names_receiver():
            return -1
        return self.func.current_param_name_to_idx.get("self")

    def receiver_credits(self, recv: TpyExpr) -> list[tuple[str, int | None]]:
        """Every name a mutating call on `recv` writes (the POINTEE climb
        under MAY: through a call, each operand its result may point into),
        each with the caller param index its mutation is deferred to Phase 2
        under (`MutationCallEdge.receiver_idx`, the mark taken only when the
        callee turns out to mutate its self), or None when the name is
        marked at once. One answer for the call-edge recorder and the
        receiver credit, so a deferred receiver always has its edge."""
        fn = self.func
        roots = storage_roots(recv, Use.POINTEE, self.get_expr_type,
                              MayLend(self))
        # A nested def's marks are replayed onto its enclosing function
        # (`nested_mutation_marks`), which an edge on the nested body would
        # bypass.
        params = (() if fn.in_nested_def
                  else [p for p in fn.current_param_name_to_idx
                        if p != "self" and p not in fn.current_rebound_params])
        out: list[tuple[str, int | None]] = []
        # The receiver's own field path (fields, walruses, coercions off a
        # name): a subscript, a getter or a call leaves the mark eager.
        t = trace(recv)
        path_root = (t.end.name if isinstance(t.end, TpyName) and all(
            s.kind in (StepKind.FIELD, StepKind.WALRUS, StepKind.COERCE)
            for s in t.steps) else None)
        for name in roots.writes().names:
            deferred = _deferred_receiver_root(
                recv, name, name == path_root, params, fn.loop_var_iterable,
                fn.borrow_tracker, self.get_expr_type)
            out.append((name, None if deferred is None
                        else self.self_receiver_index() if deferred == "self"
                        else fn.current_param_name_to_idx[deferred]))
        return out

    def mark_param_mutated(self, key: StorageKey, *,
                           through_field: bool = False,
                           via_element: bool = False,
                           _seen: 'set[tuple[StorageKey, bool, bool]] | None' = None
                           ) -> None:
        """Mark a function parameter as directly mutated (Phase 1 of mutation inference).

        `key` is the storage written: a name, or a member path a loan was
        filed under. For 'self': sets current_self_mutated (method
        self-mutation tracking). For regular params: adds to
        current_mutated_param_names.
        Also traces loop variables back to their source iterables, and traces
        element/field/ptr borrows back to their source storage (8a.5: deferred
        marking for element refs -- when v = items[i] and v.field is written,
        the write propagates back to items).

        ``through_field``: the mutation is a genuine through-reference write
        (field/subscript/slice assignment, del) rather than the speculative
        non-readonly-method-call mark. Only then do we climb a field-path
        borrow root (`o.items` -> `o`) to the owning param: a method call whose
        callee turns out readonly must NOT demote the receiver, so the
        method-call mark stops at the field-path key as it always has.

        ``via_element``: the mark reached this name by climbing from something
        it LENDS (a loop variable over it, a borrow off it), so the mutation
        is of the storage the name hands out, not of the name itself. That
        is the fact a caller who passed the name through a lending call (a
        combinator) needs: the callee's own advance of the combinator is not
        a write into the caller's containers.
        """
        # Two holders can borrow from each other (`q = p` then `p = (b,
        # q[1])`), so the climb below can come back to a name it left.
        seen = set() if _seen is None else _seen
        mark = (key, through_field, via_element)
        if mark in seen:
            return
        seen.add(mark)
        name = key.as_name()
        receiver = name == "self" and self.self_names_receiver()
        if self.func.in_nested_def:
            self.func.nested_mutation_marks.append(NestedMutationMark(
                key, through_field=through_field, structural=False,
                via_element=via_element, receiver=receiver))
        if name is None:
            # A member path (`o.items`, registered for `e = o.items[0]` or
            # for an @auto_readonly accessor result `x = o.b.get()`) borrows
            # nothing; it reaches the owning param through its member hop,
            # and a genuine write through the borrower mutates the param.
            # The place climb (`Use.PLACE`) handles the inline form
            # (`o.items[0].v = 9`); this climbs the alias form to the param.
            if through_field and not self._key_member_is_indirection(key):
                self.mark_param_mutated(key.root_key,
                                        through_field=through_field,
                                        via_element=True, _seen=seen)
            return
        if receiver:
            self.func.current_self_mutated = True
            if via_element:
                self.func.current_elem_mutated_param_names.add(name)
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_mutated_param_names.add(name)
            if via_element:
                self.func.current_elem_mutated_param_names.add(name)
        # A loop variable that is itself a handle (`for p in ps` over
        # `list[Ptr[A]]`) is written only through its referent, which is not
        # the iterable's storage -- as for a handle copied out of it.
        loop_type = (self.func.current_scope.lookup(name)
                     if self.func.current_scope else None)
        for iterable in (() if is_indirection_type(loop_type)
                         else self.func.loop_var_iterable.get(name, ())):
            # A field-path iterable ("c.items") marks its owning name; one
            # iterating a handle field iterates its referent.
            if self._key_member_is_indirection(iterable):
                continue
            self.mark_param_mutated(iterable.root_key,
                                    through_field=through_field,
                                    via_element=True, _seen=seen)
        for src in self.func.bp_borrow_source_roots(name):
            self.mark_param_mutated(StorageKey.of_name(src),
                                    through_field=through_field,
                                    via_element=True, _seen=seen)
        # 8a.5: trace through element/field/ptr borrows to source param.
        # When v = items[i] (deferred) and v is later written through,
        # mark the ultimate storage root (e.g. items) as mutated.
        # EVERY reachable root, not just the first: a re-seated binding holds
        # one loan per source and the write reaches whichever is live.
        for ultimate in self.func.borrow_tracker.all_storage_through_borrows(
                name, cross_handles=via_element):
            self.mark_param_mutated(ultimate, through_field=through_field,
                                    via_element=True, _seen=seen)

    def mark_param_structurally_mutated(self, key: StorageKey) -> None:
        """Mark a parameter as structurally mutated (append/insert/clear/del/etc.).

        Structural mutations invalidate element references -- this is separate from
        mark_param_mutated which also fires for element-ref taking (a = items[0]).
        Traces loop variables back to their source iterables transitively.
        A member path names no parameter and iterates nothing.
        """
        name = key.as_name()
        receiver = name == "self" and self.self_names_receiver()
        if self.func.in_nested_def:
            self.func.nested_mutation_marks.append(NestedMutationMark(
                key, through_field=False, structural=True,
                via_element=False, receiver=receiver))
        if name is None:
            return
        if receiver:
            self.func.current_self_struct_mutated = True
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_struct_mutated_param_names.add(name)
        for iterable in self.func.loop_var_iterable.get(name, ()):
            # Restructuring an element is a write THROUGH the source; the
            # element fact is recorded by the plain mark every caller pairs
            # this one with, whose climb takes the same step.
            self.mark_param_structurally_mutated(
                iterable.root_key)

    def mark_param_returned(self, key: StorageKey,
                            _seen: 'set[StorageKey] | None' = None, *,
                            storage: bool = False) -> None:
        """Mark a parameter as contributing to the return value (8b).

        Called when returning a reference derived from param storage, so we
        can record return_borrows_from on FunctionInfo. Mirrors mark_param_mutated
        but writes to current_returned_param_names instead -- or, with
        `storage`, to current_storage_returned_param_names, for a root found
        by the place climb (`Use.PLACE`).
        Traces loop variables back to their source iterables transitively.
        """
        # The field climb below re-enters a name the loan walk already left,
        # so the recursion needs its own guard.
        seen = set() if _seen is None else _seen
        if key in seen:
            return
        seen.add(key)
        name = key.as_name()
        if name is None:
            # A member path (`self.inner`, from `return self.inner.get()` or
            # from an alias of `b.m`) lends its owning param. Unlike the
            # mutation mark there is nothing speculative to guard: a returned
            # reference into the field is a reference into the param --
            # unless the field only POINTS at the storage.
            if not self._key_member_is_indirection(key):
                self.mark_param_returned(key.root_key, seen,
                                         storage=storage)
            return
        marked = (self.func.current_storage_returned_param_names if storage
                  else self.func.current_returned_param_names)
        if name == "self":
            marked.add("self")
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            marked.add(name)
        for iterable in self.func.loop_var_iterable.get(name, ()):
            self.mark_param_returned(iterable.root_key, seen,
                                     storage=storage)
        for src in self.func.bp_borrow_source_roots(name):
            self.mark_param_returned(StorageKey.of_name(src), seen,
                                     storage=storage)
        # A returned local alias (`t = b.m; return t`) lends what it borrows
        # from, and every source of a re-seated one: the caller cannot tell
        # which is live.
        for ultimate in self.func.borrow_tracker.all_storage_through_borrows(
                name, proven_only=True):
            self.mark_param_returned(ultimate, seen, storage=storage)

    def _key_member_is_indirection(self, key: StorageKey) -> bool:
        """Some member along the storage key `root.a.b` points AT storage (a
        `Ptr`, a borrowing view) rather than holding it, so whatever is
        reached past it is the referent's, not `root`'s: a borrow of it
        outlives `root`, and a write through it leaves `root` unchanged.

        Asked of the CURRENT scope's binding of `root`: the key names a
        binding by its name only, so the fact cannot travel with it.
        Assumes the pointer aims OUTSIDE its own record; one aimed at a
        sibling field (`self.p = take_ptr(self.m)`) does lend `root`, and
        nothing here can tell."""
        if not key.members:
            return False
        cur = (self.func.current_scope.lookup(key.root)
               if self.func.current_scope else None)
        for member in key.members:
            if cur is None:
                return False
            bare = unwrap_ref_type(unwrap_qualifiers(cur))
            record = (self.registry.get_record_for_type(bare)
                      if isinstance(bare, NominalType) else None)
            if record is None:
                return False
            cur = next((info.type for info in self.registry.get_all_fields(record)
                        if info.name == member), None)
            if is_indirection_type(cur):
                return True
        return False

    def escape_marks(self, value: TpyExpr, slot: TpyType, *,
                     position: 'EscapePosition') -> None:
        """What a borrow of `value` escaping through the return / yield
        `slot` does to the roots it lends: the caller may write each root
        the slot grants write access to (`slot_lent_roots`, readonly slots
        and elements none), and every root is recorded as lent
        (`mark_param_returned`; the place climb's roots as the storage).

        One routine for both positions; where they differ is stated here.
        A yield records nothing for a readonly or borrowing-view slot (a
        return records the provenance) and marks no loop variable a borrow
        tuple lends writable.

        The climbs follow a returned call into what its result may point
        into (MAY: `return inner(items)` where `inner` borrows its param
        -> items); a source that grants no write (a readonly callee, a
        `Ptr` operand) is not marked written. The provenance recorded is
        proven only: a guess about a pending callee would be read back as
        this function's own fact by every later caller."""
        type_of = self.get_expr_type
        may = MayLend(self)
        borrow_tuple = escaping_borrow_tuple(slot)
        view = is_borrowing_view_type(slot)
        if slot.is_value_type() and not view and borrow_tuple is None:
            return
        is_yield = position is EscapePosition.YIELD
        if is_yield and borrow_tuple is None and (
                view or isinstance(slot, ReadonlyType)):
            return
        if borrow_tuple is not None:
            bare, ro = borrow_tuple
            holder = slot_lent_roots(value, bare, Use.HOLDER, type_of, may)
            storage = slot_lent_roots(value, bare, Use.PLACE, type_of, may)
            writes = (slot_lent_roots(value, bare, Use.PLACE, type_of, may,
                                      write_only=True)
                      if escaping_borrow_writes(slot) else Roots())
        else:
            holder = storage_roots(value, Use.HOLDER, None, may)
            storage = storage_roots(value, Use.PLACE, type_of, may)
            writes = storage if escaping_borrow_writes(slot) else Roots()
        # A returned reference grants the caller write access, so the climb
        # must reach a field-path borrow root (`b = o.f; return b` -> o),
        # matching the direct `return o.f` form. A loop variable lent
        # writable through a returned tuple member must not bind const.
        mark_roots(self, writes, MarkRequest(
            through_field=True,
            loop_vars=borrow_tuple is not None and not is_yield))
        for name in holder.proven().names:
            self.mark_param_returned(StorageKey.of_name(name))
        for name in storage.proven().names:
            self.mark_param_returned(StorageKey.of_name(name), storage=True)

    def mark_own_param_consumed(self, name: str) -> None:
        """Mark an Own[T] param as consumed (stored, forwarded, or returned)."""
        if name in self.func.current_param_names:
            self.func.current_consumed_own_params.add(OwnSlot(name))
        elif name in self.func.own_consume_aliases:
            self.func.current_consumed_own_params.add(self.func.own_consume_aliases[name])

    def mark_own_element_consumed(self, name: str, index: int) -> None:
        """Mark owned element `index` of tuple param `name` consumed -- moved
        out on its own while the tuple's other elements stay."""
        if name in self.func.current_param_names:
            self.func.current_consumed_own_params.add(OwnSlot(name, index))

    def is_last_use(self, expr: 'TpyExpr | None') -> bool:
        """Sema's last-use verdict for one name read, as sema left it: the
        liveness mark minus every retraction sema made -- at the use (a
        hidden borrow demoting the move (`demoted_by_hidden_borrow`), a live
        closure capturing the source) or later in the body (a `finally`
        that still reads a deferred return). The read lowering takes;
        lowering never asks the move verdict itself, which reads the borrow
        state of the moment and retracts marks."""
        return expr in self.all_last_uses

    # ------------------------------------------------------------------
    # View-type family generic accessors
    # ------------------------------------------------------------------

    def view_var_map(self, family: ViewTypeFamily) -> dict[str, int]:
        """Variable-name -> var_id mapping for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.variable_to_str_var
        return self.func.variable_to_bytes_var

    def view_source_borrows_map(self, family: ViewTypeFamily) -> dict[StorageKey, set[int]]:
        """Source-storage -> set of borrowing var_ids for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.str_source_borrows
        return self.func.bytes_source_borrows

    def view_pending_resolutions(self, family: ViewTypeFamily) -> list[int]:
        """Pending resolution list for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.pending_str_resolutions
        return self.func.pending_bytes_resolutions

    def view_vars(self, family: ViewTypeFamily) -> dict[int, ViewVarInfo]:
        """Var-id -> ViewVarInfo registry for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.str_vars
        return self.bytes_vars

    def declare_local(self, name: str, scope_depth: int, *,
                      block_depth: int | None = None) -> None:
        """`name` is declared here: at scope depth `scope_depth`
        (`var_scope_depth`) and in the block of the innermost open compound
        statement, or of `block_depth` (`decl_block_depth`). The declaration-site writer of both tables (a hoist moves a
        depth through `record_branch_decls`, a loop scope restores one
        through `restore_local_decl`), so no declaration site can leave the
        block rule reading a missing entry."""
        self.func.var_scope_depth[name] = scope_depth
        self.func.decl_block_depth[name] = (
            len(self.func.compound_stack) if block_depth is None
            else block_depth)

    def local_decl_of(self, name: str) -> tuple[int | None, int | None]:
        """`name`'s `declare_local` entry, for `restore_local_decl`."""
        return (self.func.var_scope_depth.get(name),
                self.func.decl_block_depth.get(name))

    def restore_local_decl(self, name: str,
                           saved: tuple[int | None, int | None]) -> None:
        """Put back what `local_decl_of` saved, at the end of a binding
        narrower than the body (a loop or comprehension variable)."""
        for table, value in ((self.func.var_scope_depth, saved[0]),
                             (self.func.decl_block_depth, saved[1])):
            if value is None:
                table.pop(name, None)
            else:
                table[name] = value

    def is_reseated(self, name: str) -> bool:
        """Whether `name`'s storage can be replaced or destroyed at some point
        of this body whose order against a use is not tracked: a rebind, a
        nested def's `nonlocal` rebind, or a `del`."""
        return (name in self.func.current_reassigned_vars
                or name in self.func.nested_nonlocal_rebinds
                or name in self.func.deleted_names)

    def view_needs_owned(self, info: ViewVarInfo) -> bool:
        """Whether the facts recorded so far force this view local to OWN.

        The one predicate behind every storage answer for a str/bytes local;
        `_resolve_pending_view_types` and `view_storage_verdict` both read it,
        so a use site and the resolution can never disagree on what the facts
        say.

        A generator/async body hoists every local into the resumable frame,
        which outlives the case-block temps and suspensions the sync
        view-safety judgment assumes the binding shares scope with -- so a
        non-static source forces owned storage there. Locals side of the param
        doctrine in `is_owned_in_coro_frame`; explicit StrView/BytesView
        annotations never enter Pending resolution, so the user's view
        contract is untouched.
        """
        func = self.func.current_function
        in_resumable = (isinstance(func, TpyFunction)
                        and (func.is_generator or func.is_async))
        return bool(
            info.initialized_from_owned
            or info.used_in_augassign
            or info.passed_to_promote_param
            or info.reassigned_from_owned
            or info.source_mutated
            or (in_resumable and info.frame_unsafe_source)
        )

    def view_storage_verdict(self, typ: 'TpyType | None') -> 'TpyType | None':
        """The storage the deduction has decided for an undecided str/bytes
        local -- the ONE answer every consumer of that storage asks for.

        `typ` is the local's binding type; anything that is not a pending view
        gets None, so a caller can spell `verdict(t) or t` and keep its own
        answer for a type that already states its storage. Once
        `_resolve_pending_view_types` has run the settled type is returned
        (`view_storage_settled` says so); before that it is the verdict the
        facts recorded so far imply, walking the alias chain because a source
        that owns forces its aliases to own. A later fact can only promote a
        view to owned, never the reverse, so a consumer that must act during
        body analysis reads a verdict that only gets stricter, and one that
        can wait defers until the answer is settled.
        """
        if not isinstance(typ, PendingViewType):
            return None
        family = typ.family
        registry = self.view_vars(family)
        root = registry.get(typ.var_id)
        if root is None:
            return None
        if root.resolved_type is not None:
            return root.resolved_type
        seen: set[int] = set()
        stack = [typ.var_id]
        while stack:
            var_id = stack.pop()
            if var_id in seen:
                continue
            seen.add(var_id)
            info = registry.get(var_id)
            if info is None:
                continue
            if (info.resolved_type == family.owned_type
                    or (info.resolved_type is None
                        and self.view_needs_owned(info))):
                return family.owned_type
            for src_id in info.source_var_ids:
                src = registry.get(src_id)
                if src is not None and not src.lends_to_aliases:
                    return family.owned_type
            stack.extend(info.source_var_ids)
        return family.view_type

    def view_storage_settled(self, typ: 'TpyType | None') -> bool:
        """Whether `view_storage_verdict` for this local is final -- i.e. the
        resolution pass has run and no further binding can change it."""
        if not isinstance(typ, PendingViewType):
            return False
        info = self.view_vars(typ.family).get(typ.var_id)
        return info is not None and info.resolved_type is not None

    def next_view_var_id(self, family: ViewTypeFamily) -> int:
        """Allocate and return the next var_id for the given family."""
        if family.pending_type_class is PendingStrType:
            vid = self.str_var_counter
            self.str_var_counter += 1
            return vid
        vid = self.bytes_var_counter
        self.bytes_var_counter += 1
        return vid

    # ------------------------------------------------------------------
    # View-type mutation tracking
    # ------------------------------------------------------------------

    def mark_view_source_written(self, typ: TpyType) -> None:
        """A loop variable (or loop-head unpack target) whose source the
        loop may write while it is live: it owns its element, as a view of
        a reseated source does."""
        inner = unwrap_own(unwrap_ref_type(unwrap_readonly(typ)))
        if isinstance(inner, PendingViewType):
            info = self.view_vars(inner.family).get(inner.var_id)
            if info is not None:
                info.source_mutated = True

    def mark_view_borrowers_mutated(self, storage: StorageKey,
                                    family: ViewTypeFamily) -> None:
        """Mark pending view-type borrowers of storage as source-mutated.

        Called at mutation sites so that view resolution falls back to
        the owned type when the view's source storage is mutated.
        Also invalidates field-path borrows: mutating ``p`` invalidates
        views borrowed from ``p.name``, ``p.field``, etc.
        """
        source_borrows = self.view_source_borrows_map(family)
        vars_registry = self.view_vars(family)
        keys = [storage]
        keys.extend(k for k in source_borrows
                    if k != storage and k.has_prefix(storage))
        for key in keys:
            var_ids = source_borrows.get(key)
            if not var_ids:
                continue
            for var_id in var_ids:
                info = vars_registry.get(var_id)
                if info is not None:
                    info.source_mutated = True

    def mark_all_view_borrowers_mutated(self, storage: StorageKey) -> None:
        """Mark borrowers across all view-type families as source-mutated.

        A key rooted at a borrowing local names the same storage as the key
        rooted at what it borrows from (``m = i`` makes ``m.name`` and
        ``i.name`` one buffer), and a view is registered under whichever
        spelling its own source used, so the write marks both.
        """
        keys = {storage, self.func.borrow_tracker.canonical_key(storage)}
        for key in keys:
            for family in VIEW_TYPE_FAMILIES:
                self.mark_view_borrowers_mutated(key, family)

    # ------------------------------------------------------------------
    # Unified container literal lookup
    # ------------------------------------------------------------------

    def track_container_variable(
        self,
        var_name: str,
        pending_type: 'PendingContainerType',
        decl_line: int | None,
    ) -> None:
        """Register `var_name` as a name for the container literal of
        `pending_type`, and the record's name and declaration line."""
        literal_id = pending_type.literal_id
        self.func.variable_to_literal[var_name] = literal_id
        info = self.container_literals[literal_id]
        info.variable_name = var_name
        info.decl_line = decl_line
