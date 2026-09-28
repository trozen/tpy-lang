"""
TurboPython Statement Analysis

Statement analysis including variable declarations, assignments, and control flow.
"""

from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, NamedTuple

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, OwnType, ReadonlyType,
    FinalType,
    PendingListType, PendingDictType, make_list, PendingSetType, PendingStrType, PendingBytesType, PendingViewType, NominalType, TypeParamRef,
    ListLiteralInfo, DictLiteralInfo, SetLiteralInfo, ViewVarInfo, PtrType, is_readonly_ptr, NoneType, OptionalType, AnyType, UnionType, UnknownElementType,
    is_c_abi_allowed, is_c_abi_element_allowed, c_abi_type_hint,
    C_ABI_TYPE_ERROR,
    is_void_like_type,
    unwrap_readonly, unwrap_own, unwrap_qualifiers, is_any_str_type, is_any_bytes_type, TupleType, own_tuple_target,
    RecursiveAliasInstanceType,
    collapse_tuple_own_elements, global_binds_by_reference, type_contains_own,
    LiteralType,
    ViewTypeFamily, view_family_for_type, VIEW_TYPE_FAMILIES,
    PendingGenericInstanceType, contains_fn_type,
    INT32, VOID, BIGINT, FLOAT, STRVIEW, BYTES, BYTESVIEW, BOOL, is_protocol_type, is_protocol_union, final_type_str_to_strview,
    is_final_allowed_inner, FINAL_INNER_TYPE_ERROR,
    qualify_exception_name, is_return_exception, is_exception_type, error_return_matches,
    FunctionInfo, ParamInfo, RecordInfo, MutationCallEdge,
    make_ref, unwrap_ref_type, RefType, param_has_mutable_borrow_surface,
    is_integer_type, is_any_int_type, is_numeric_type, is_primitive_type, is_readonly_span,
    is_bufferless_scalar, unwrap_send_sync, unwrap_optional_own,
    is_float_type, is_any_float_type, is_polymorphic_subclass_fact,
    resolve_int_literals,
    yield_uses_borrow_slot, yield_always_borrows, GenExprType,
    is_dyn_protocol, is_fn_type, coro_struct_owner,
    ConcreteCoroType, ConcreteFrameType, ConcreteGenType, make_concrete_coro,
    make_cancellable,
    bare_name, held_whole_borrow_sources, recorded_return_borrow_sources)
from ..parse import (
    collect_name_refs,
    walk_body_stmts,
    TpyExpr,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyDelAttr, TpyExprStmt, TpyReturn, TpyYield,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyAssert,
    TpyRaise, TpyExceptHandler, TpyTry, TpyWith, TryTier,
    TpyGlobal, TpyNonlocal, TpyNestedDef,
    TpyCall, TpyMethodCall, TpyArrayLiteral, TpyListComprehension, TpyCoerce,
    TpySubscript, TpySlice, TpyStrLiteral, TpyName, TpyTupleLiteral,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyUnaryOp,
    TpyNoneLiteral,
    TpyFieldAccess, TpyFunction, TupleElemCapture,
    TpyMatch, TpyBinOp, TpyIfExpr, TpyAwait,
    TpyLambda, TpyNamedExpr, TpyGeneratorExpression, TpyDictComprehension,
    TpySetComprehension,
    is_stable_address_lvalue,
)
from ..coercions import CoercionContext
from ..namespace import BindingKind, NameBinding
from ..symbol_binding import SymbolKind, lookup_imported
from ..prescan import (
    bound_names_of, walrus_names_of, scope_bound_names, ScanResult,
    scan_reassigned_vars,
    parse_deref_view_key,
    FactKills, collect_fact_kills, liveness_alias_sources, is_scan_rvalue,
)
from ..liveness import (analyze_last_uses, collect_finally_return_candidates,
                        collect_deleted_names, collect_finally_rebound_returns,
                        collect_nested_def_nonlocal_rebinds,
                        stmts_terminate, tuple_literal_leaves,
                        while_head_always_true)
from ..parse.nodes import SourceLocation, VarLinkage, op_spelling
from .context import (PendingLocal, addr_taken_roots, call_borrow_operands,
                      canonical_storage_key,
                      expr_yields_non_null_ptr, LoopClauseEdges,
                      record_borrow_binding, record_stmt_borrow_binding,
                      holds_generator_object, holds_frame_object,
                      note_owned_local,
                      frame_binding_calls, frame_binding_fact,
                      record_frame_binding_roots,
                      frame_borrowed_operands,
                      tuple_borrow_escape_roots)
from .flow_facts import condition_walrus_assigned, condition_walrus_targets
from .literal_utils import is_char_literal_init
from ..diagnostics import SemanticError, NOCOPY_REMEDIATION_HINT
from .match import MatchAnalyzer
from .narrowing import NarrowingTracker
from .overloads import OverloadAmbiguityError, resolve_overload
from .scope_tracker import ScopeTracker
from .init_tracker import InitTracker
from .receiver_calls import (
    check_implicit_readonly_receiver, credit_implicit_receiver_call,
    record_implicit_call,
)
from .iter_loans import (
    _record_iter_receiver_mutation, check_iter_receiver_loans, hold_whole,
    is_dangling_temporary_arg,
    register_iteration_loans, temp_arg_kept_alive,
)
from .own_copy import contains_reference_type
from .value_range import ValueRange
if TYPE_CHECKING:
    from .context import SemanticContext, BorrowTracker
    from .flow_facts import FlowFacts
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .list_literals import IterableHelper
    from .expressions import ExpressionAnalyzer
    from .protocols import ProtocolChecker
    from ..diagnostics import Scope

from .alias_rebind import decide_rebind_storage, stamp_bind_kind
from .compatibility import TupleSink
from .context import _is_borrowing_auto_readonly_accessor, _root_name_of_expr, BorrowKind, EphemeralKind, INVALIDATING_BORROW_KINDS, MODULE_INIT_CONTEXT, PENDING_CONTAINER_TYPES, _storage_key, _storage_root, element_index_key, element_loan_mutation_warning, loan_mutation_warning, _borrow_storage_root, _borrow_storage_roots, call_lend_sources, iter_borrow_storage, field_chain_storage_key, register_binding_borrow, ephemeral_borrow_root, contains_pending_leaf, holds_no_pointer, value_may_point
from ..value_category import (
    is_rvalue_source, call_returns_cpp_ref, async_result_aliases,
    async_return_form, AsyncReturnForm, iterator_source_callee,
    peel_value_wrappers, tuple_literal_elems,
)
from .expressions import (_nested_def_free_names, _find_list_member,
                          _names_rebound_by, generic_constructor_factory,
                          _collect_body_local_defs)
from .slot_hint import SlotHint
from .local_deduction import (
    collect_pending_source_types, mark_pending_list_mutated,
    view_source_is_temporary,
)
from .type_ops import signature_may_return_borrow as _signature_may_return_borrow
from tpyc import modules as builtin_modules
from tpyc import qnames
from ..type_def_registry import (
    is_iterator_adapter, is_basic_slice_type, is_slice_type,
    is_dict, is_array, is_span, is_list,
    is_char_type, is_str_type, is_string_type, is_str_view_type,
    is_bytes_type, is_bytearray_type, is_bytes_view_type,
    is_borrowing_view_type,
    is_fixed_int_type, is_big_int_type,
    protocol_info_of,
)


class ConditionWalrus(NamedTuple):
    """Where a condition's walrus targets hold, by the way out of the head.

    A target counts as assigned on a path out of the head only when every
    way of reaching that path evaluated it, which is one answer per path --
    so a target the head always binds is in BOTH sets.
    """

    if_true: frozenset[str]
    if_false: frozenset[str]


# How a diagnostic names the compound statement a nested `def` sits in.
_BLOCK_KEYWORD = {
    TpyIf: "if",
    TpyWhile: "while",
    TpyForEach: "for",
    TpyTry: "try",
    TpyWith: "with",
    TpyMatch: "match",
}


def _declares_here(stmt: TpyStmt, name: str) -> bool:
    """Whether `stmt`, as the first binding of `name` in its block, declares
    it in that block -- rather than inside a block of its own, whose
    declaration would end at that block's brace."""
    if isinstance(stmt, TpyNestedDef):
        return stmt.func.name == name
    return name in walrus_names_of(stmt) or (
        isinstance(stmt, (TpyVarDecl, TpyAssign, TpyTupleUnpack))
        and name in bound_names_of(stmt))


def _needs_provenance_tracking(t: TpyType) -> bool:
    """Whether a local of this type participates in param-provenance / trusted-call tracking.

    Non-value types are reference types and always track provenance.
    PtrType is a value type but carries an address. Borrowing views
    (StrView / BytesView / Span / SpanIter) are value types with an
    interior pointer into their source storage. PendingViewType covers
    unresolved str/bytes locals -- if they settle into view semantics
    the tracking is required; if they settle into owned (String/bytes)
    the extra tracking is harmless.
    """
    return (not t.is_value_type()
            or isinstance(t, PtrType)
            or is_borrowing_view_type(t)
            or isinstance(t, PendingViewType))


def _register_call_result_borrow(ctx: SemanticContext, borrower: str, expr: TpyExpr) -> None:
    """Register borrow from function call return value (8b).

    When a function has return_borrows_from facts, the result variable
    borrows from the indicated argument(s). None means unanalyzed -- skip.
    """
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyIfExpr):
        # A select binds whichever arm is taken, so a borrow-returning call
        # arm loans its callee's sources exactly as a direct call init would.
        _register_call_result_borrow(ctx, borrower, expr.then_expr)
        _register_call_result_borrow(ctx, borrower, expr.else_expr)
        return
    if isinstance(expr, TpyAwait):
        # A borrow-returning await aliases the awaited call's receiver /
        # borrowed args exactly like the sync call it wraps -- recurse so
        # the binding demotes a later move of the source (the auto-move
        # borrow gate). Owned await results are fresh values (nothing to
        # register); a bound-handle operand has no traceable roots here
        # (the handle's own binding registered its receiver borrow).
        if expr.await_result_is_borrow:
            _register_call_result_borrow(ctx, borrower, expr.value)
        return
    operands = call_borrow_operands(expr)
    if operands is None:
        return
    fi, obj = operands.fi, operands.obj
    bt = ctx.func.borrow_tracker
    if fi.return_borrows_from is None:
        # The callee's body has not been analyzed yet (forward reference in
        # this module), so whether the result borrows an argument is
        # unknown. Assume it does: register an OPAQUE borrow from every
        # name-rooted argument so the auto-move gate demotes a later
        # consume to the copy path. Opaque stubs (native, builtin,
        # cpp_template, bodyless overloads) are not in the pending set --
        # for them None keeps meaning "borrows nothing". This readiness
        # question reads the RAW field, unlike the sources below: the pending
        # set holds the registry FIs, so the membership test and the None it
        # qualifies must be about the same fi the call site resolved to.
        if (fi in ctx.pending_borrow_fact_fis
                and _signature_may_return_borrow(fi)):
            for src in call_lend_sources(operands, expr_type=None):
                for root in _borrow_storage_roots(src.expr):
                    if root != borrower:
                        bt.add_borrow(root, borrower, BorrowKind.OPAQUE)
        return
    # An iterator-returning callee hands back a HANDLE into its sources (a
    # combinator object, a generator frame): advancing it touches nothing it
    # points at, which is what the ITER kind tells the mutation climb.
    kind = (BorrowKind.ITER if iterator_source_callee(fi)
            else BorrowKind.ELEMENT)
    temp_warned: set[int] = set()
    for src in call_lend_sources(operands, recorded_return_borrow_sources(fi),
                                 held_whole_borrow_sources(fi),
                                 expr_type=None, temp_backing=True):
        if src.temp_backed:
            # An owned tuple element copied into the argument temporary: the
            # result points into storage that dies as a scalar temporary
            # argument's does, and borrows nothing of the caller's.
            if (src.idx not in temp_warned and not temp_arg_kept_alive(
                    fi, src.idx, src.expr, src.slot, ctx)):
                temp_warned.add(src.idx)
                ctx.warning(
                    f"Result borrows from temporary argument '{fi.params[src.idx].name}'; "
                    f"the temporary is destroyed at end-of-statement",
                    expr,
                )
            continue
        if src.held_whole and src.idx >= 0:
            hold_whole(bt, borrower, _borrow_storage_roots(src.expr))
            continue
        if src.idx == -1:
            root = _borrow_storage_root(obj)
            if root is not None:
                bt.add_borrow(root, borrower, kind)
            elif is_dangling_temporary_arg(obj):
                # A borrow-returning callee can hand back storage that
                # OUTLIVES its receiver (a global, a longer-lived object), so
                # the receiver dying at end-of-statement is never a reason to
                # call the result unborrowed -- the provenance is the whole
                # question here.
                ctx.warning(
                    f"Result borrows from temporary receiver object; "
                    f"the temporary is destroyed at end-of-statement",
                    expr,
                )
        else:
            roots = _borrow_storage_roots(src.expr)
            for root in roots:
                bt.add_borrow(root, borrower, kind)
            if (not roots and is_dangling_temporary_arg(src.expr)
                    and not temp_arg_kept_alive(
                        fi, src.idx, src.expr, src.slot, ctx)):
                ctx.warning(
                    f"Result borrows from temporary argument '{fi.params[src.idx].name}'; "
                    f"the temporary is destroyed at end-of-statement",
                    expr,
                )



def _register_tuple_binding_borrows(
        ctx: SemanticContext, borrower: str, value: TpyExpr,
        target_type: TpyType) -> None:
    """Keep the loans carried by a local tuple's selected borrow payload."""
    bare = unwrap_readonly(target_type)
    if isinstance(bare, OptionalType):
        bare = unwrap_readonly(bare.inner)
    if not isinstance(bare, TupleType) or not bare.has_borrowing_element():
        return
    inner = value
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
    leaves = tuple_literal_elems(inner, bare)
    if leaves is not None:
        for leaf in leaves:
            slot = unwrap_readonly(unwrap_ref_type(leaf.slot))
            if isinstance(slot, TupleType):
                # A borrow-form tuple VALUE at a nested slot copies its
                # element pointers along.
                _register_tuple_binding_borrows(ctx, borrower, leaf.elem, slot)
                continue
            # A view element files no loan here, as a scalar view local
            # does not (`v = mk().name` is quiet): its borrow is the view's.
            if not TupleType.element_lends(leaf.slot) or slot.is_value_type():
                continue
            # The one per-element override: an annotated local may MOVE a
            # reference element into owned storage (a last-use move, a
            # temporary), which sema records as a VALUE capture.
            index = leaf.path[-1]
            capture = (leaf.holder.elem_capture[index]
                       if index < len(leaf.holder.elem_capture) else None)
            if (capture is TupleElemCapture.VALUE
                    and not isinstance(slot, TypeParamRef)):
                continue
            register_binding_borrow(ctx, borrower, leaf.elem)
            _register_call_result_borrow(ctx, borrower, leaf.elem)
    elif isinstance(inner, TpyIfExpr):
        _register_tuple_binding_borrows(ctx, borrower, inner.then_expr, bare)
        _register_tuple_binding_borrows(ctx, borrower, inner.else_expr, bare)
    else:
        if isinstance(inner, (TpyName, TpySubscript, TpyFieldAccess)):
            register_binding_borrow(ctx, borrower, inner)
        _register_call_result_borrow(ctx, borrower, inner)


def _format_aug_target(target: TpyExpr) -> str:
    """Format an aug-assign target as a short label for error messages.

    e.g. items[0] -> 'items[...]', obj.x -> 'obj.x', otherwise 'target'.
    """
    if isinstance(target, TpyFieldAccess):
        obj = target.obj.name if isinstance(target.obj, TpyName) else "..."
        return f"'{obj}.{target.field}'"
    if isinstance(target, TpySubscript):
        obj = target.obj.name if isinstance(target.obj, TpyName) else "..."
        return f"'{obj}[...]'"
    return "target"


def _iter_source_root(expr: TpyExpr) -> str | None:
    """The NAME the storage a for-each iterable borrows is rooted at.

    The borrow key is the answer wherever the tracker files a loan
    (`iter_borrow_storage`), so the loan and the mutable-borrow credit can
    never root differently -- one question, one walker. An iterable the
    tracker files nothing for (a borrowing accessor hop, a property getter
    with no `return_borrows_from`) still lends the loop var storage that
    cannot bind const, so the syntactic climb answers for those.
    """
    key = iter_borrow_storage(expr)
    return _storage_root(key) if key is not None else _root_name_of_expr(expr)


# view_family_for_type is in typesys (alongside ViewTypeFamily / VIEW_TYPE_FAMILIES).
# Re-imported at the top of this module.


def _handle_pinned_view_rebind(ctx: SemanticContext, name: str, stmt: TpyStmt) -> None:
    """Handle the two pinned-view tracker effects of rebinding ``name``.

    1. If ``name`` is a source with pinned-view borrowers, those views dangle
       (str/bytes is value-type storage with no slot/pointer indirection) --
       warn for each and drop the entry. ``sorted`` for deterministic order.
    2. If ``name`` is itself a pinned view, remove it from its source's set
       so the source no longer warns about it on subsequent rebinds.
    """
    aliases = ctx.func.pinned_view_aliases
    if not aliases:
        return
    pinned_views = aliases.pop(name, None)
    if pinned_views:
        for view_name in sorted(pinned_views):
            ctx.warning(
                f"Mutation of '{name}' while borrowed"
                f" (reassignment invalidates view '{view_name}')",
                stmt
            )
    for source in list(aliases):
        views = aliases[source]
        if name in views:
            views.discard(name)
            if not views:
                del aliases[source]


def global_rebind_message(name: str, t: TpyType) -> str:
    """The one message for rebinding a reference-bound global from a
    function body; a tuple of references is named as such, since the
    language calls a tuple a value type."""
    bare = unwrap_readonly(t)
    what = ("a tuple of reference types" if isinstance(bare, TupleType)
            else "non-value type")
    return (f"Cannot reassign global variable '{name}' of {what} "
            f"'{collapse_tuple_own_elements(bare)}'")


def _deferred_tuple_spelling(lit: TpyTupleLiteral) -> str:
    """A returned tuple literal as a diagnostic quotes it: names and
    literals as written, anything else as `...`."""
    parts = []
    for e in lit.elements:
        while isinstance(e, TpyCoerce):
            e = e.expr
        if isinstance(e, TpyName):
            parts.append(e.name)
        elif isinstance(e, TpyTupleLiteral):
            parts.append(_deferred_tuple_spelling(e))
        elif isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
            parts.append(repr(e.value))
        elif isinstance(e, TpyStrLiteral):
            parts.append(repr(e.value))
        elif isinstance(e, TpyNoneLiteral):
            parts.append("None")
        else:
            parts.append("...")
    inner = ", ".join(parts)
    return f"({inner},)" if len(parts) == 1 else f"({inner})"


def _param_is_borrowed(t: TpyType) -> bool:
    """A parameter of this declared type is a borrow its caller keeps alive,
    so the method never destroys it: a reference type, `str` / `bytes` (a
    view), a scalar, or an Optional / union of those -- never an `Own` one.
    A by-value parameter of any other value type (a tuple, a callable, a user
    value type, a type parameter, a protocol) counts as owned."""
    bare = unwrap_readonly(unwrap_send_sync(t))
    if unwrap_optional_own(bare) is not None:
        return False
    bare = unwrap_qualifiers(bare)
    members = (bare.inner_types() if isinstance(bare, (OptionalType, UnionType))
               else (bare,))
    return all(
        holds_no_pointer(m)
        or not (m.is_value_type() or isinstance(m, TypeParamRef)
                or is_protocol_type(m) or is_dyn_protocol(m))
        for m in members)


class StatementAnalyzer:
    """Statement analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        compat: TypeCompatibility,
        deduction: LocalTypeDeduction,
        iterable: IterableHelper,
        protocols: ProtocolChecker,
        narrowing: NarrowingTracker,
        expr: ExpressionAnalyzer,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.compat = compat
        self.deduction = deduction
        self.iterable = iterable
        self.protocols = protocols
        self.narrowing = narrowing
        self.scopes = ScopeTracker(ctx, compat)
        self.init = InitTracker(ctx, narrowing)
        self.expr = expr
        self.match = MatchAnalyzer(ctx, self, expr)
        # The statements whose analysis is open, innermost last. A nested
        # `def` reads its enclosing statement off it (the block its callable
        # is declared in); every statement's analysis ends by retiring the
        # nested defs that block bound.
        self._stmt_stack: list[TpyStmt] = []

    @contextmanager
    def _loop_body_scope(
            self, stmt: TpyWhile | TpyForEach
    ) -> Iterator[tuple['Scope', LoopClauseEdges]]:
        """`ScopeTracker.loop_scope` plus this loop's clause-edge record.

        The record is pushed on the function state, where `break` and
        `continue` file their snapshots, and closed here: inside the loop
        scope, the last point at which the body's own exit state exists
        (`apply_loop_exit_facts` restores the pre-loop one), and at the pop,
        because a `break` in the `else` clause belongs to the ENCLOSING loop.

        While the body is open the live stack is the only authority: a
        nested `def` (and a lambda trial) analyzed there saves and restores
        the whole function state WHOLESALE, and the restore installs a deep
        COPY -- a record captured at entry would be orphaned by it and every
        `break` after such a def would go missing. The yielded record is
        therefore filled at the close, and `_finish_loop_clauses` is its one
        reader.
        """
        edges = LoopClauseEdges()
        self.ctx.func.loop_clause_edges.append(LoopClauseEdges())
        try:
            with self.scopes.loop_scope(stmt.body) as inner_scope:
                yield inner_scope, edges
                live = self.ctx.func.loop_clause_edges[-1]
                live.body_assigned = self._body_clause_assigned(
                    live, head_can_exit=not (isinstance(stmt, TpyWhile)
                                             and while_head_always_true(stmt)))
        finally:
            closed = self.ctx.func.loop_clause_edges.pop()
            edges.breaks = closed.breaks
            edges.continues = closed.continues
            edges.body_assigned = closed.body_assigned

    def _assigned_here(self) -> frozenset[str]:
        """The names that hold a value at this point, as one set.

        `definitely_assigned` and `loop_bound_assigned` are two spellings of
        the one fact -- `FlowFacts.merge` joins them as `(da|lba) & (da|lba)`
        -- so an edge out of a loop clause records their union, and the
        intersections that consume it take each spelling back out of it
        separately.
        """
        return frozenset(self.ctx.func.definitely_assigned
                         | self.ctx.func.loop_bound_assigned)

    def _body_clause_assigned(self, edges: LoopClauseEdges, *,
                              head_can_exit: bool) -> frozenset[str]:
        """What the loop BODY holds on every edge that leaves it.

        The edges are the fall-through end (only when it is reachable) and
        every `continue`; both lead back to the head, so neither leaves the
        loop when the head can never be false (`while True:`) and the
        `break`s are then the only way out. A body with no edge of either
        kind is in that same position.
        """
        joined: list[frozenset[str]] = []
        if head_can_exit:
            joined.extend(edges.continues)
            if not self.ctx.func.init_terminated:
                joined.append(self._assigned_here())
        if not joined:
            # Nothing leaves the loop at all: the code after it is
            # unreachable, which sema does not model, so keep the state the
            # body ended in rather than invent a verdict for it.
            joined = list(edges.breaks) or [self._assigned_here()]
        return frozenset.intersection(*joined)

    def _finish_loop_clauses(
            self, stmt: TpyWhile | TpyForEach, inner_scope: 'Scope',
            edges: LoopClauseEdges, *,
            before: 'FlowFacts',
            consumed_before: set[str],
            ns_types_before: dict[str, TpyType],
            runs_once: bool,
            elem_type: TpyType | None = None,
            head_exit_assigned: frozenset[str] = frozenset()) -> None:
        """Close a loop: exit facts, both clauses' bindings, the break join.

        Every loop arm ends here, so the order the clauses are joined in is
        stated once. A name first bound in a clause counts as assigned after
        the loop only when the clause provably ran (`runs_once`) and the
        binding is on every edge that leaves it: the body's own edges,
        already joined into `edges.body_assigned`; the `else` clause, which
        the fall-through path ran by definition; and every `break`, which
        leaves past the rest of the body AND past the `else`. A
        `return`/`raise` never joins here at all.

        `head_exit_assigned` is what only the path that left through the
        head holds -- a `while`'s short-circuit walrus -- which the `else`
        clause and the code after the loop are on and a `break` is not.
        `elem_type` is a `for`'s, whose loop variable is a binding of the
        same kind; an `async for` has no `else` clause to walk (the parser
        rejects one).
        """
        self.init.apply_loop_exit_facts(before, runs_once=runs_once)
        # Loop might not execute -- consumption inside is not definite.
        self.ctx.func.current_consumed_own_params = consumed_before
        # loop_scope() restored the scope bindings; namespace mutations made
        # inside the body persist, so they are rolled back here.
        self._restore_ns_var_types(ns_types_before)
        self._sync_promoted_var_types()
        self.ctx.func.definitely_assigned |= head_exit_assigned
        # Python locals are function-scoped: what a clause binds stays
        # readable after the loop, promoted on first use.
        if elem_type is not None:
            self._propagate_for_loop_scope(
                stmt, inner_scope, elem_type, runs_once=runs_once,
                body_end_assigned=edges.body_assigned)
        else:
            self._propagate_loop_body_vars(
                stmt, inner_scope, runs_once=runs_once,
                body_end_assigned=edges.body_assigned)
        self._analyze_loop_orelse(stmt)
        if edges.breaks:
            on_every_break = frozenset.intersection(*edges.breaks)
            self.ctx.func.definitely_assigned &= on_every_break
            self.ctx.func.loop_bound_assigned &= on_every_break

    def _analyze_condition_walrus(self, condition: TpyExpr) -> ConditionWalrus:
        """Analyze a condition and sort its walrus targets by path out of it.

        The `if` head and the `while` head ask the same question -- an `if`
        of its two bodies, a `while` of its body and of the edge that leaves
        through the head -- so both ask it here.
        """
        assigned_before = frozenset(self.ctx.func.definitely_assigned)
        self.expr.analyze_condition(condition)
        if_true, if_false = condition_walrus_assigned(condition)
        # The head's own verdict is what every path out of it is granted, so
        # a target only ONE path evaluates must not be left assigned here by
        # the walk that analyzed the condition.
        conditional = condition_walrus_targets(condition) - (if_true
                                                             & if_false)
        self.ctx.func.definitely_assigned -= conditional - assigned_before
        return ConditionWalrus(if_true=if_true, if_false=if_false)

    def _resolve_obj_storage(self, obj: TpyExpr) -> str | None:
        """This analyzer's spelling of `BorrowTracker.resolve_obj_storage`."""
        return self.ctx.func.borrow_tracker.resolve_obj_storage(obj)

    def _mark_field_write_views(self, target: TpyFieldAccess,
                                receiver_storage: str | None,
                                field_storage: str | None) -> None:
        """Demote the str/bytes views a ``X.f = v`` write clobbers.

        The write replaces exactly ``X.f`` (and whatever it owns), so the key
        is the FIELD path: keying on the receiver would demote a view of an
        untouched SIBLING field. The receiver is the key only when no field
        path can be spelled (a hop the tracker cannot name, e.g. a property
        getter in the chain), where the coarse mark is the only sound one.
        """
        key = field_storage or field_chain_storage_key(target) or receiver_storage
        if key is not None:
            self.ctx.mark_all_view_borrowers_mutated(key)

    def _warn_all_caps_without_final(self, name: str, typ: TpyType | None,
                                     node: TpyStmt) -> None:
        """Warn on ALL_CAPS module-level variables without Final annotation.

        Only for types Final can actually wrap -- suggesting Final[T] for a
        type the Final validation rejects (e.g. a record constant like
        datetime's UTC) would recommend an uncompilable spelling.
        """
        if (not name.startswith("_")
                and name.replace("_", "").isalpha()
                and name == name.upper()
                and len(name) >= 2
                and typ is not None
                and is_final_allowed_inner(typ)):
            self.ctx.warning(
                f"ALL_CAPS variable '{name}' without Final annotation; "
                f"use Final[{typ}] if this is a constant",
                node
            )

    def _check_no_bare_async_call(self, stmt: TpyStmt) -> None:
        """Sema rule: a call to an async def whose result is silently
        dropped, or stored where a coroutine cannot live, is rejected.

        Rejected (caught at TpyStmt analysis time):
          f()            (TpyExprStmt: the coroutine is dropped unrun)
          obj.x = f()    (TpyAssign: fields/elements need Box[Cancellable])
        Allowed:
          c = f()                -- owned-erased local binding (TpyVarDecl)
          return f()             -- return-type check enforces conformance
          await f()              -- TpyAwait, not TpyCall
          asyncio.run(f())       -- f() is an arg of another call
          asyncio.create_task(f())
        """
        target_expr: TpyExpr | None = None
        if isinstance(stmt, TpyAssign):
            target_expr = stmt.value
        elif isinstance(stmt, TpyExprStmt):
            target_expr = stmt.expr
        if not isinstance(target_expr, TpyCall):
            return
        func_name = target_expr.maybe_func_name
        if not func_name:
            return
        overloads = self.ctx.registry.get_function(func_name)
        if not any(getattr(fi, "is_async", False) for fi in (overloads or ())):
            return
        if isinstance(stmt, TpyExprStmt):
            raise self.ctx.error(
                f"Coroutine value from async def '{func_name}' is dropped "
                f"without running: `await {func_name}(...)`, pass it to "
                f"asyncio.create_task/run, or bind it to a variable to "
                f"consume later. Coroutines are single-use, must-use values.",
                stmt)
        raise self.ctx.error(
            f"Coroutine value from async def '{func_name}' cannot be stored "
            f"in a field or container element; use Box[Cancellable[T]] for "
            f"owned storage, or bind it to a local and consume it there.",
            stmt)

    def _bind_method_coro_receiver(self, target: str,
                                    init: 'TpyMethodCall') -> None:
        """A bound async-METHOD coroutine captures its receiver by
        reference for the handle's whole lifetime (concrete frame and
        erased adapter alike). Require a stable-lvalue receiver (the
        rule inline `await obj.m()` enforces for the await's duration)
        and register the borrow so the receiver cannot be silently
        moved out from under a live handle. Escapes that outlive the
        scope (create_task / return) additionally warn at those sites.

        Called from the single post-cleanup site in _analyze_var_decl,
        keyed on the init shape alone -- it must cover every binding
        form (fresh, annotated-erased, rebind) and must run AFTER the
        generic `remove_borrower(stmt.name)` reassignment cleanup, which
        would otherwise erase the fresh borrow.
        """
        if not is_stable_address_lvalue(init.obj):
            raise self.ctx.error(
                "receiver of a bound async method call must be a stable "
                "lvalue (a local, parameter, or field chain rooted at "
                "one) -- the coroutine captures it by reference for the "
                "handle's lifetime. Bind the receiver to a local first: "
                "`r = <expr>; c = r.method(...)`",
                init)
        root = _root_name_of_expr(init.obj)
        if root is not None:
            storage = self.ctx.func.borrow_tracker.effective_storage(root)
            self.ctx.func.borrow_tracker.add_borrow(
                storage, target, BorrowKind.ALIAS)

    def _concrete_coro_bind_type(self, init: TpyExpr,
                                  cancellable: TpyType) -> TpyType:
        """Type for a local bound from a direct async-def/method call.

        Returns a ConcreteCoroType (the zero-alloc representation: the
        local holds the concrete `__coro_*` frame; erasure happens only
        at typed boundaries) when the callee's frame struct is nameable
        from the binding. Falls back to the erased `Cancellable[T]`
        handle for template-frame callees (static-protocol / Fn params
        -- their struct name needs call-site deduction), a declared
        erasure boundary.
        """
        fi = init.resolved_function_info
        inner = cancellable.type_args[0] if cancellable.type_args else VOID
        aliases = async_result_aliases(fi.async_inner_return, inner)

        def erased_or_reject() -> TpyType:
            # The erased handle loses the borrow-result fact, and an
            # erased Cancellable[T] promises an owned payload -- so a
            # borrow-returning coroutine must not degrade to it.
            if aliases:
                raise self.ctx.error(
                    f"cannot bind this coroutine to a handle: "
                    f"'{fi.name}' returns a borrow of '{inner}' (a "
                    f"reference type), and binding it here requires the "
                    f"type-erased handle, whose result must be owned. "
                    f"Declare the async def '-> Own[{inner}]' (return an "
                    f"owned value; copy a borrowed source explicitly), "
                    f"or await the call directly",
                    init)
            return cancellable

        for p in fi.params:
            pt = unwrap_readonly(unwrap_ref_type(p.type))
            if (is_protocol_type(pt) and not is_dyn_protocol(pt)) or is_fn_type(pt):
                return erased_or_reject()
        owner = None
        if isinstance(init, TpyMethodCall):
            recv_type = self.ctx.get_expr_type(init.obj)
            recv_inner = unwrap_own(unwrap_ref_type(recv_type)) if recv_type else None
            if not isinstance(recv_inner, NominalType):
                return erased_or_reject()
            owner = coro_struct_owner(
                fi.owning_type_qname, recv_inner,
                self.ctx.registry.get_record_for_type(recv_inner))
        module_qual = None
        if (owner is None and fi.originating_module is not None
                and fi.originating_module != self.ctx.module_name):
            module_qual = fi.originating_module
        targs = getattr(init, "inferred_type_args", None)
        return make_concrete_coro(
            inner, fi.name, owner=owner,
            inferred_type_args=tuple(targs) if targs else None,
            module_qual=module_qual,
            result_is_borrow=aliases, fi=fi)

    def _record_frame_binding(self, stmt: TpyVarDecl) -> None:
        """One binding of a frame-holding local: the per-name roots the
        hoist, the loop-frame check and the frame layout read, and the
        shapes a resumable frame cannot hold."""
        init = stmt.init
        while isinstance(init, TpyCoerce):
            init = init.expr
        calls = frame_binding_calls(init)
        fact: 'tuple[frozenset[str | None], bool] | None'
        if calls is None and isinstance(init, TpyName):
            # A name bound from another frame-holding local -- an alias of
            # the same generator object, or an unpack's temp -- borrows what
            # that one does.
            fact = self.ctx.func.frame_local_roots.get(init.name)
            if holds_generator_object(self.ctx.local_decl_type(init.name)):
                self.ctx.func.frame_alias_sources[stmt.name] = init.name
        elif calls is None:
            fact = None
        else:
            fact = frame_binding_fact(init, self.ctx)
        record_frame_binding_roots(self.ctx, stmt.name, fact, stmt)

    def _frame_hoist_body(self) -> bool:
        """A body whose locals are C++ block locals: a plain function or
        method. A generator or async body keeps every local in its frame
        for the frame's whole life already, and module code declares its
        names at namespace scope."""
        fn = self.ctx.func.current_function
        return (isinstance(fn, TpyFunction) and not self.ctx.is_top_level
                and not fn.is_generator and not fn.is_async)

    def _frame_block_candidate(self, name: str) -> bool:
        """A local holding a generator, its own or another name's. A name
        the scope-escape check hoisted already has a slot at the function's
        top; a coroutine is run to its end by `await` or handed on whole,
        so when its name dies is never observed."""
        t = (self.ctx.local_decl_type(name)
             or self.ctx.func.current_scope.lookup(name))
        return (name in self.ctx.func.frame_local_roots
                and holds_generator_object(t)
                and name not in self.ctx.func.escape_hoisted_vars)

    def _frame_block_keeps(self, block_new: 'AbstractSet[str]',
                           predecl: 'AbstractSet[str]', block: str, *,
                           keepable: 'AbstractSet[str]' = frozenset()
                           ) -> set[str]:
        """In a plain function a generator first bound in a block lives in
        the block's C++ scope unless the block's pre-declarations
        (`predecl`, taken for a read after the block) declare it in front
        of it. That declaration is older than any local, so the frame may
        then borrow only what outlives the body
        (`ExpressionAnalyzer.frame_scope_why`), and another name for a
        generator only one declared there too. One that may not is kept in
        the block when the block can declare it itself (`keepable`), and a
        read after the block is an error (`block_closed_frames`); otherwise
        it is an error here. Returns the kept names, to take out of
        `predecl`."""
        if not self._frame_hoist_body():
            return set()
        names = sorted(n for n in set(block_new) & set(predecl)
                       if self._frame_block_candidate(n))
        sources = self.ctx.func.frame_alias_sources
        why: dict[str, str] = {}
        for g in names:
            if g not in sources:
                w = self.expr.frame_scope_why(g, f"the {block}")
                if w is not None:
                    why[g] = w
        changed = True
        while changed:
            changed = False
            for g in names:
                src = sources.get(g)
                if (g not in why and src is not None and src in block_new
                        and (src in why or src not in predecl)):
                    why[g] = (f"it is another name for '{src}', which ends "
                              f"with the {block}")
                    changed = True
        for g in sorted(why):
            if g not in keepable:
                raise self.ctx.error(
                    f"cannot keep '{g}' open after the {block} it is bound "
                    f"in: {why[g]}; iterate it inside a helper function",
                    self.ctx.func.frame_binding_nodes.get(g, [None])[0])
            self.ctx.func.block_closed_frames[g] = (why[g], block)
        return set(why)

    def _note_pass_scoped_frames(self, loop: 'TpyForEach | TpyWhile') -> None:
        """A plain function keeps a frame-holding local first bound in a
        loop body in the pass's C++ block, so it is gone when the next pass
        starts -- unless a read after the loop declares it in front of the
        loop (`_promote_pending_loop_var`)."""
        if not self._frame_hoist_body():
            return
        for n, pending in self.ctx.func.pending_loop_vars.items():
            if (pending.head_stmt is None
                    and any(s is loop for s in pending.first_stack)
                    and n in self.ctx.func.frame_local_roots):
                self.ctx.func.pass_scoped_frames.setdefault(n, loop)

    def _binds_fresh_frame(self, stmt: TpyVarDecl) -> bool:
        """An unpack target bound to a temp that holds a new generator
        (`g, k = counter(1), 2`), not another name's (`g, h = h, g`)."""
        temp = stmt.init
        decl = (self.ctx.func.var_decl_by_name.get(temp.name)
                if isinstance(temp, TpyName) else None)
        return (decl is not None and decl.init is not None
                and is_rvalue_source(self.ctx, decl.init))

    def _check_gen_rebind(self, stmt: TpyVarDecl,
                          prev: ConcreteGenType) -> None:
        """A name holding a generator object holds that one frame type: a
        rebind to another generator function's object (or to any other
        iterator) would need type erasure, which allocates."""
        init = stmt.init
        while isinstance(init, TpyCoerce):
            init = init.expr
        cand: TpyType | None = None
        if isinstance(init, (TpyCall, TpyMethodCall)):
            t = self.ctx.get_expr_type(init)
            cand = unwrap_readonly(unwrap_ref_type(t)) if t else None
        elif isinstance(init, TpyName):
            src = self.ctx.func.current_scope.lookup(init.name)
            cand = unwrap_readonly(unwrap_ref_type(src)) if src else None
        self._check_one_frame_type(stmt, prev, cand)

    def _check_one_frame_type(self, stmt: TpyVarDecl,
                              prev: 'ConcreteFrameType',
                              cand: TpyType | None) -> None:
        """A name holding a generator or coroutine object holds that one
        frame type: holding another would need type erasure, which
        allocates."""
        if cand == prev:
            return
        init = stmt.init
        while isinstance(init, TpyCoerce):
            init = init.expr
        what = (f"'{init.func_name}(...)'" if isinstance(init, TpyCall)
                else "this value")
        coro = isinstance(prev, ConcreteCoroType)
        kind = "coroutine" if coro else "generator"
        other = (kind if coro or isinstance(cand, ConcreteGenType)
                 else "iterator")
        raise self.ctx.error(
            f"cannot rebind '{stmt.name}' to a different {other}: "
            f"'{stmt.name}' holds a '{prev.frame_func_name}' {kind}, and one "
            f"name holds one {'async' if coro else 'generator'} function's "
            f"objects; bind {what} to a new name",
            stmt)

    def _warn_unnecessary_return_copy(self, value: TpyExpr) -> None:
        """Warn when return copy(x) is used but x is at last use (auto-move suffices)."""
        if not (isinstance(value, TpyCall) and len(value.args) == 1
                and value.resolved_function_info
                and value.resolved_function_info.qualified_name == "tpy.copy"):
            return
        inner = value.args[0]
        if self.compat.auto_move_copied_elements(inner) == frozenset():
            self.ctx.warning(
                f"unnecessary copy() -- '{inner.name}' is at its last use and would be moved automatically",
                value,
            )

    def _warn_str_field_return_copy(self, value: TpyExpr, expected: TpyType) -> None:
        """Warn when a method returns self.field where field is str.

        This copies the string; suggest StrView (zero-copy view) or String
        (explicit owned) so the user makes an intentional choice.
        Suppressed for dunder methods (__str__, __repr__).
        """
        if not is_str_type(expected):
            return
        func = self.ctx.func.current_function
        if func is None or not func.is_method:
            return
        # Dunder methods (__str__, __repr__) have a fixed str contract
        if func.name.startswith("__") and func.name.endswith("__"):
            return
        inner = value
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if not (isinstance(inner, TpyFieldAccess)
                and isinstance(inner.obj, TpyName)
                and inner.obj.name == "self"):
            return
        field_name = inner.field
        self.ctx.warning(
            f"returns a copy of str field 'self.{field_name}'; "
            f"use -> StrView for zero-copy access or -> String to silence this warning",
            value,
        )

    def _check_own_lvalue_return(self, own_type: OwnType, expr: TpyExpr,
                                 context: str,
                                 whole_slot: bool = True) -> None:
        """Check that an lvalue returned as Own[T] has explicit copy() or is
        auto-moved; a borrowed one copies and warns, naming the element of a
        returned tuple."""
        self.compat.check_own_lvalue_into_own(own_type, expr, context,
                                              action="return",
                                              whole_slot=whole_slot)

    def _analyze_return_value(self, stmt: TpyReturn) -> None:
        """The value half of a `return` statement's analysis."""
        if stmt.value:
            fn = self.ctx.func.current_function
            # A generator's `return` ends iteration and carries no value
            # (the parser rejects any operand but the canonical None), so
            # its slot is void, not the declared Iterator type.
            expected = (unwrap_ref_type(fn.return_type)
                        if fn is not None and not fn.is_generator
                        else VOID)
            ret_type = self.expr.analyze_expr_with_hint(stmt.value, expected)
            stmt.value_type = ret_type
            self._mark_finally_deferred_return(stmt, ret_type, expected)
            stmt.value = self.compat.coerce_expr(stmt.value, ret_type, expected, "return value",
                                                  coercion_ctx=CoercionContext.RETURN, is_return=True)
            # Track return-type context for pending list/dict/set deduction
            self.deduction.mark_container_return_context(stmt.value, expected)
            # Warn when a method copies a str field on return
            self._warn_str_field_return_copy(stmt.value, expected)
            # Borrowed-receiver escape: returning a bound async-METHOD
            # coroutine lets the handle outlive the receiver's scope;
            # lifetime is not tracked across the escape.
            ret_inner = unwrap_readonly(unwrap_own(unwrap_ref_type(ret_type)))
            if (isinstance(ret_inner, ConcreteCoroType)
                    and ret_inner.frame_owner is not None):
                self.ctx.warning(
                    "returned bound method-coroutine borrows its "
                    "receiver by reference; the handle must not "
                    "outlive the receiver (receiver lifetime is not "
                    "tracked across this escape)",
                    stmt)
            # A returned generator or coroutine keeps what its frame
            # borrows, whatever slot the return fills.
            ret_call = stmt.value
            while isinstance(ret_call, TpyCoerce):
                ret_call = ret_call.expr
            for src_e in frame_borrowed_operands(ret_call) or ():
                for src in _borrow_storage_roots(src_e):
                    self.ctx.mark_param_returned(src)
            # Check for a borrowed source returned as Own[T] without an
            # explicit copy(). `readonly` peels first: `readonly[Own[T]]`
            # is the same owning slot with a const view on top, so the
            # spelling must not be a way past the check.
            own_expected = unwrap_readonly(expected)
            if isinstance(own_expected, OwnType):
                if self.compat.is_copy_call(stmt.value):
                    self._warn_unnecessary_return_copy(stmt.value)
                else:
                    self._check_own_lvalue_return(own_expected, stmt.value, "return type")
            # Check Own[T] elements in tuple literals.
            if isinstance(stmt.value, TpyTupleLiteral):
                tuple_target = own_tuple_target(expected)
                if tuple_target is not None:
                    # The Own-element checks, then the per-element
                    # capture mode (ref/value/const_ref).
                    self._annotate_tuple_elem_capture(
                        stmt.value, tuple_target, sink=TupleSink.RETURN)
            # A tuple LOCAL returned by name: the literal-element check
            # above never ran, so consult the construction-time
            # plain-borrow-into-Own hazard for each Own slot.
            elif isinstance(stmt.value, TpyName):
                tuple_target = own_tuple_target(expected)
                if tuple_target is not None:
                    self.compat.check_name_borrow_into_own(
                        stmt.value.name, tuple_target, stmt.value,
                        return_slot=True)
            # Returning an ephemeral generator/iterator borrow lets it escape
            # its iteration step -- reject with the copy-out fix (before the
            # generic dangling check so the specific message wins).
            self._reject_ephemeral_escape(stmt.value, "return")
            # Check for dangling reference (returning local/temporary as reference)
            self.compat.check_dangling_reference(stmt.value, expected, stmt.loc, ret_type)
            # Returning a non-value type by reference takes the source's address.
            # Mark both loop vars and params so they keep T& (not const T&/const T*).
            if isinstance(stmt.value, TpyName):
                self.ctx.mark_loop_var_mutated(stmt.value.name)
                # Escape tracking: returning a nested def marks it as escaping
                if stmt.value.name in self.ctx.func.nested_def_names:
                    self.ctx.reject_resumable_nested_def_escape(
                        stmt.value.name, stmt)
                    self.ctx.func.nested_def_escapes.add(stmt.value.name)
            # Returning a borrowing view does not give the caller write
            # access to the source, so we record borrow provenance but
            # must not raise self_mutated -- that would suppress auto-const
            # on the enclosing method and break const callers.
            if expected is not None:
                returns_borrowing_view = is_borrowing_view_type(expected)
                # A tuple is a value type, but its borrow form hands out
                # mutable element pointers into the source storage -- the
                # root must stay non-const and the borrow be recorded.
                expected_bare = unwrap_readonly(expected)
                returns_borrow_tuple = (
                    isinstance(expected_bare, TupleType)
                    and expected_bare.has_borrowing_element())
                if (not expected.is_value_type() or returns_borrowing_view
                        or returns_borrow_tuple):
                    # A readonly return hands out a CONST borrow -- of a
                    # tuple's elements or of the record itself -- so borrow
                    # provenance is recorded but no write access is granted,
                    # exactly as for a borrowing view. Marking the source
                    # mutated here would raise self_mutated on the enclosing
                    # method and, through the Phase-2 receiver edge, on every
                    # caller that reads the borrow.
                    ro_return = isinstance(expected, ReadonlyType)
                    if returns_borrow_tuple:
                        ret_roots = tuple_borrow_escape_roots(
                            stmt.value, expected_bare, ro_return,
                            expr_type=self.ctx.get_expr_type)
                    else:
                        ret_roots = [(root, not ro_return)
                                     for root in addr_taken_roots(stmt.value)]
                    for ret_root, grants_write in ret_roots:
                        if not returns_borrowing_view and grants_write:
                            # through_field: a returned reference grants
                            # the caller write access, so the climb must
                            # reach a field-path borrow root (`b = o.f;
                            # return b` -> o), matching the direct
                            # `return o.f` form.
                            self.ctx.mark_param_mutated(
                                ret_root, through_field=True)
                        self.ctx.mark_param_returned(ret_root)  # 8b: track which param storage the return borrows
                    # 8b rule 3: transitive return -- if returning the result of a call
                    # whose return_borrows_from is known, propagate the borrow contract.
                    # e.g. `return inner(items)` where inner borrows param 0 -> mark items.
                    ret_inner = stmt.value.expr if isinstance(stmt.value, TpyCoerce) else stmt.value
                    ret_operands = call_borrow_operands(ret_inner)
                    if ret_operands is not None:
                        fi_ret = ret_operands.fi
                        for lent in call_lend_sources(
                                ret_operands,
                                recorded_return_borrow_sources(fi_ret),
                                expr_type=None):
                            # One argument position can hold many
                            # operands (a `*args` pack), and each is
                            # borrowed by the same contract.
                            for src in _borrow_storage_roots(lent.expr):
                                # Read-only sources (readonly callees, view returns)
                                # don't propagate mutation to their borrowed-from arg.
                                if not returns_borrowing_view and not fi_ret.is_readonly:
                                    self.ctx.mark_param_mutated(src)
                                self.ctx.mark_param_returned(src)

    def _consuming_return_move_fields(self, stmt: TpyReturn) -> frozenset[str]:
        """The `self` fields this return of a consuming method moves out of.

        Deliberately coarse -- sema cannot ask whether anything still depends
        on the field's storage, so a field moves only where nothing can read
        it after the move. The rule belongs to the consuming method's own
        body: a nested def may run any number of times, so its returns never
        move. The return must not sit in a `for` (the iterator's teardown, a
        generator's `finally`, could read the field), a `try` (a handler, an
        `else` or a `finally`) or a `with` (an `__exit__`); the method must
        not be a generator or async (the frame outlives the statement); no
        local or owned parameter of the method may be able to read the field
        when destroyed (`_method_teardown_cannot_read_field`); and
        within the value the field is read once -- operand order inside one
        expression is not fixed -- with no read of `self` as a whole (a
        method, a property, `super()`) and no name that could reach the
        field (`_return_name_cannot_reach_field`). When the value reads more
        than one field, every one of them must be unable to point at another
        by its declared type (`value_may_point`,
        `holds_no_pointer`). Every other read of a field is a
        borrow, which the owning slots copy and warn about."""
        fn = self.ctx.func.current_function
        rec = self.ctx.record_ctx.record
        if (stmt.value is None or not isinstance(fn, TpyFunction)
                or not fn.is_consuming or rec is None
                or fn.is_generator or fn.is_async
                or any(isinstance(s, (TpyForEach, TpyTry, TpyWith))
                       for s in self.ctx.func.compound_stack)):
            return frozenset()
        info = self.ctx.registry.get_record(rec.name)
        if info is None or not self._method_teardown_cannot_read_field():
            return frozenset()
        field_types = {f.name: f.type
                       for f in self.ctx.registry.get_all_fields(info)}
        reads: list[str] = []
        stack: list[TpyExpr] = [stmt.value]
        while stack:
            e = stack.pop()
            if isinstance(e, (TpyLambda, TpyNamedExpr, TpyGeneratorExpression,
                              TpyListComprehension, TpySetComprehension,
                              TpyDictComprehension)):
                return frozenset()
            if (isinstance(e, TpyFieldAccess) and isinstance(e.obj, TpyName)
                    and e.obj.name == "self"):
                if e.field not in field_types:
                    return frozenset()
                reads.append(e.field)
                continue
            if isinstance(e, TpyName):
                if not self._return_name_cannot_reach_field(e.name):
                    return frozenset()
                continue
            stack.extend(e.children())
        moves = frozenset(f for f in reads if reads.count(f) == 1)
        # With a second field in the value, one field's storage may be
        # reachable through the other (`self.pa` aimed at `self.a`), so each
        # must be proven unable to point anywhere by its type alone.
        if len(set(reads)) > 1:
            for f in set(reads):
                if (value_may_point(field_types[f]) if f in moves
                        else not holds_no_pointer(field_types[f])):
                    return frozenset()
        return moves

    def _method_teardown_cannot_read_field(self) -> bool:
        """True only when nothing the method may destroy after its return
        value is built can read a `self` field: every name the body binds
        ANYWHERE, and every parameter it owns or rebinds, holds a bufferless
        scalar or an owned `str` / `bytes`. Decided over the whole body, not
        the scope at the return: codegen hoists a loop or branch local to a
        function-level slot, and a loop reaches a binding that sits after the
        return in source order -- so a name whose type is not known at the
        return is not proven either. A generator whose `finally` reads the
        field, a `__del__` holding a `Ptr` into `self`, or just a list keeps
        the field borrowed. A borrowed parameter the body never rebinds is
        not destroyed by the method."""
        func = self.ctx.func
        rebound = func.body_bound_names | func.nested_nonlocal_rebinds
        for name in rebound | func.current_param_names:
            if name == "self":
                continue
            types: list[TpyType] = []
            scope = func.current_scope
            while scope is not None and scope is not self.ctx.global_scope:
                if name in scope.bindings:
                    types.append(scope.bindings[name])
                    break
                scope = scope.parent
            pending = func.pending_loop_vars.get(name)
            if pending is not None:
                types.append(pending.var_type)
            decl_type = self.ctx.local_decl_type(name)
            if decl_type is not None:
                types.append(decl_type)
            if not types:
                return False
            if all(holds_no_pointer(t) for t in types):
                continue
            if (name in func.current_param_names and name not in rebound
                    and all(_param_is_borrowed(t) for t in types)):
                continue
            return False
        return True

    def _return_name_cannot_reach_field(self, name: str) -> bool:
        """True only when `name` is PROVEN unable to reach a `self` field: a
        local or parameter of a bufferless scalar type, or a module-level
        function or class. Anything else -- a nested def or lambda local
        (it may capture `self`), a module variable, `self`, an unresolved
        name -- could read the field after the move."""
        if name in ("self", "super"):
            return False
        if self.compat.is_local_shadow(name):
            return is_bufferless_scalar(
                self.ctx.func.current_scope.lookup(name))
        ns = self.ctx.func.current_ns
        while ns is not None and ns is not self.ctx.global_ns:
            if ns.has_local(name):
                return False
            ns = ns.parent
        if self.ctx.global_scope.lookup(name) is not None:
            return False
        binding = (self.ctx.global_ns.lookup(name)
                   if self.ctx.global_ns is not None else None)
        if binding is not None and binding.kind in (
                BindingKind.FUNCTION, BindingKind.RECORD, BindingKind.ENUM):
            return True
        if binding is not None and binding.kind != BindingKind.IMPORTED_NAME:
            return False
        registry = self.ctx.registry
        return (registry.get_function(name) is not None
                or registry.get_record(name) is not None
                or registry.get_enum(name) is not None)

    def _mark_finally_deferred_return(self, stmt: TpyReturn, ret_type: TpyType,
                                      expected: TpyType) -> None:
        """`return <name>` under a non-suspending finally: for eligible
        reference-type shapes, keep the auto-move mark (the exception-path
        discard may have dropped it) and stamp the node so codegen
        materializes the return value AFTER the inline finally chain. This is
        what makes finally mutations of the returned local -- through ANY
        channel, direct or alias/closure-mediated -- visible in the returned
        object (CPython aliasing); an eager capture would copy or move before
        the finally runs, and deferring a local the finally never touches is
        equivalent to the eager move.

        Sound regardless of other exception-path readers: on the return path
        the finally chain runs before the deferred move, and on any path
        where handlers read the name this return never executed.

        Only stamp shapes codegen's deferred-capture recipes cover -- a stamp
        without a recipe falls back to the eager copy, whose pre-mutation
        value silently diverges from CPython's aliasing pending return, so
        the gate errs narrow: plain non-value scalars returned as Own[T], and
        pointer-repr Optional locals returned as a storage Optional, and the
        same per member for a returned tuple. Narrowed Optional sources and
        union-typed values keep the eager capture.
        """
        # A returned Own param is consumed by the return whatever capture
        # takes it (a finally that rebinds it keeps an eager one).
        for _, leaf in tuple_literal_leaves(stmt.value):
            if isinstance(leaf, TpyName):
                self.ctx.mark_own_param_consumed(leaf.name)
                # The last-use mark does not see a finally's reads through an
                # alias or a closure, so a move there could leave the finally
                # reading a moved-from object: always refuse.
                if leaf in self.ctx.finally_rebound_returns:
                    self._check_eager_return_copyable(leaf, leaf)
        if isinstance(stmt.value, TpyTupleLiteral):
            self._mark_finally_deferred_tuple_return(stmt, expected)
            return
        if (isinstance(stmt.value, TpyName)
                and self._mark_finally_deferred_tuple_name(stmt, expected)):
            return
        if not (isinstance(stmt.value, TpyName)
                and stmt.value in self.ctx.finally_return_candidates
                and self.compat._is_owned_var(stmt.value.name)):
            return
        exp = unwrap_ref_type(expected)
        val = unwrap_readonly(unwrap_own(unwrap_ref_type(ret_type)))
        declared = self._declared_binding(stmt.value.name)
        eligible = False
        if isinstance(exp, OwnType) and not exp.wrapped.is_value_type():
            eligible = self._own_slot_deferrable(declared, val)
        elif isinstance(exp, OptionalType) and not exp.uses_pointer_repr():
            # Shape B: pointer-repr Optional local returned as the storage
            # Optional (Own[T] | None) -- the ptr_to_optional_move arm. The
            # analyzed type may be narrowed to T, so key on the declared
            # binding.
            eligible = (isinstance(declared, OptionalType)
                        and declared.uses_pointer_repr())
        if eligible:
            self.ctx.all_last_uses.add(stmt.value)
            stmt.finally_deferred_capture = True

    def _declared_binding(self, name: str) -> 'TpyType | None':
        declared = (self.ctx.func.current_scope.lookup(name)
                    if self.ctx.func.current_scope is not None else None)
        if declared is not None:
            declared = unwrap_readonly(unwrap_own(unwrap_ref_type(declared)))
        return declared

    @staticmethod
    def _own_slot_deferrable(declared: 'TpyType | None', val: TpyType) -> bool:
        """Shape A: a plain reference-type local at an Own[T] slot
        (including a declared-Optional local narrowed to T -- its T* slot is
        what the recipe dereferences). A declared UNION local stores a variant
        even when narrowed, and the recipe's &(name)/std::move(*p) would move
        the wrong C++ type -- so the storage-shape questions key on the
        DECLARED binding, not the (possibly narrowed) analyzed type."""
        declared_ok = (
            declared is not None
            and not isinstance(declared, (UnionType, TupleType))
            and not isinstance(declared, RecursiveAliasInstanceType)
            and (not isinstance(declared, OptionalType)
                 or declared.uses_pointer_repr())
            and not is_protocol_type(declared))
        return (declared_ok
                and not val.is_value_type()
                and not isinstance(val, (OptionalType, UnionType, TupleType))
                and not is_protocol_type(val))

    def _mark_finally_deferred_tuple_return(self, stmt: TpyReturn,
                                            expected: TpyType) -> None:
        """The tuple-literal twin of shape A, per member: a member that is an
        owned reference-type local at an `Own` element slot is deferred like
        the scalar `return b`; every other member keeps being evaluated before
        the finally chain, into a temporary, so no member moves relative to
        another or to the chain."""
        exp = unwrap_readonly(unwrap_ref_type(expected))
        whole_owned = isinstance(exp, OwnType)
        if whole_owned:
            exp = unwrap_readonly(exp.wrapped)
        if not isinstance(exp, TupleType):
            return
        paths: list[tuple[int, ...]] = []
        leaves_all = tuple_literal_leaves(stmt.value)
        # A name the tuple holds twice cannot be one pending alias per slot:
        # it keeps the eager capture, where the explicit-copy rule applies
        # exactly as it does without a finally.
        seen: dict[str, int] = {}
        for _, leaf in leaves_all:
            if isinstance(leaf, TpyName):
                seen[leaf.name] = seen.get(leaf.name, 0) + 1
        for path, leaf in leaves_all:
            if isinstance(leaf, TpyName) and seen[leaf.name] > 1:
                continue
            if not (isinstance(leaf, TpyName)
                    and leaf in self.ctx.finally_return_candidates
                    and self.compat._is_owned_var(leaf.name)):
                continue
            slot = self._tuple_slot_at(exp, path)
            if slot is None:
                continue
            owned = whole_owned or isinstance(slot, OwnType)
            slot_t = unwrap_readonly(unwrap_own(slot))
            if not owned or slot_t.is_value_type():
                continue
            leaf_t = self.ctx.get_expr_type(leaf)
            if leaf_t is None:
                continue
            val = unwrap_readonly(unwrap_own(unwrap_ref_type(leaf_t)))
            if self._own_slot_deferrable(self._declared_binding(leaf.name),
                                         val):
                paths.append(path)
        if not paths:
            return
        leaves = dict(tuple_literal_leaves(stmt.value))
        for path in paths:
            self.ctx.all_last_uses.add(leaves[path])
            # The deferred member is moved out after the chain: a consume.
            self.ctx.mark_own_param_consumed(leaves[path].name)
        stmt.finally_deferred_leaves = tuple(paths)
        stmt.finally_deferred_capture = True

    def _mark_finally_deferred_tuple_name(self, stmt: TpyReturn,
                                          expected: TpyType) -> bool:
        """`return t` of an owned tuple local into an owning tuple slot: the
        whole tuple is the deferred member (path `()`), captured like the
        scalar `return b` when the lowering finds the local's storage is the
        return's own C++ type, else kept eager."""
        value = stmt.value
        assert isinstance(value, TpyName)
        exp = unwrap_readonly(unwrap_ref_type(expected))
        whole_owned = isinstance(exp, OwnType)
        if whole_owned:
            exp = unwrap_readonly(exp.wrapped)
        if not isinstance(exp, TupleType):
            return False
        if not (whole_owned or any(
                isinstance(et, OwnType)
                and not unwrap_readonly(et.wrapped).is_value_type()
                for et in exp.element_types)):
            return False
        if not (value in self.ctx.finally_return_candidates
                and self.compat._is_owned_var(value.name)
                and isinstance(self._declared_binding(value.name),
                               TupleType)):
            return False
        self.ctx.all_last_uses.add(value)
        self.ctx.mark_own_param_consumed(value.name)
        stmt.finally_deferred_leaves = ((),)
        stmt.finally_deferred_capture = True
        return True

    @staticmethod
    def _tuple_slot_at(exp: TupleType, path: tuple[int, ...]
                       ) -> 'TpyType | None':
        """The declared element slot at `path` of a (nested) tuple return,
        or None when the path leaves the tuple structure."""
        cur: TpyType = exp
        for i in path:
            cur_t = unwrap_readonly(unwrap_own(cur))
            if not (isinstance(cur_t, TupleType)
                    and i < len(cur_t.element_types)):
                return None
            cur = cur_t.element_types[i]
        return cur

    def _is_in_constructor(self) -> bool:
        """Check if currently analyzing an __init__ method body."""
        func = self.ctx.func.current_function
        return (func is not None
                and getattr(func, 'name', None) == "__init__"
                and getattr(func, 'is_method', False))

    def _annotate_tuple_elem_capture(
        self, literal: TpyTupleLiteral, tuple_type: TupleType,
        *, sink: TupleSink = TupleSink.LOCAL,
        sink_dest: str = "owned storage"
    ) -> None:
        """Annotate each element of a tuple literal with its capture mode,
        after the per-member copy check for the literal's `sink`.

        Args:
            literal: The tuple literal AST node to annotate.
            tuple_type: The resolved TupleType for the literal.
            sink: Where the literal lands -- a RETURN, a FIELD, a CONTAINER
                element or a LOCAL; decides each member's copy rule and its
                capture.
            sink_dest: How to name the destination in a copy diagnostic.
        """
        self.compat.check_tuple_literal_members(
            literal, tuple_type, sink, sink_dest)
        is_return = sink is TupleSink.RETURN
        is_field = sink is TupleSink.FIELD
        V = TupleElemCapture.VALUE
        R = TupleElemCapture.REF
        CR = TupleElemCapture.CONST_REF

        is_readonly = (self.ctx.func.current_function is not None
                       and getattr(self.ctx.func.current_function, 'is_readonly', False))

        literal.elem_capture = []
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(literal.elements):
                literal.elem_capture.append(V)
                continue
            elem = literal.elements[i]

            # Value types, Own[T], and TypeParamRef are always VALUE.
            if et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)):
                literal.elem_capture.append(V)
                continue

            # Field context: all reference-type elements are owned (VALUE).
            if is_field:
                literal.elem_capture.append(V)
                continue

            # Return context
            if is_return:
                if self.compat.is_dangling_return(elem):
                    # Will error separately in check_dangling_reference
                    literal.elem_capture.append(V)
                elif self.compat.is_const_ref_source(elem):
                    if is_readonly:
                        literal.elem_capture.append(CR)
                    else:
                        raise self.ctx.error(
                            f"Cannot return readonly source as tuple element {i}. "
                            f"Type '{et}' would be returned by mutable reference, "
                            f"but the source is readonly. "
                            f"Use Own[{et}] with copy() to return by value.",
                            elem
                        )
                elif is_readonly:
                    literal.elem_capture.append(CR)
                else:
                    literal.elem_capture.append(R)
                    # Returning a non-value element by reference takes its address.
                    # Mark source params as needing T& (not const T&).
                    for tup_root in addr_taken_roots(elem):
                        self.ctx.mark_param_mutated(tup_root)
                continue

            # Local context: is_const_ref_source handles ReadonlyType
            # (including constructor params which are typed as ReadonlyType)
            # Pointer-form Optional: force REF for all sources (including
            # non-lvalue None) so the tuple has a uniform slot shape. A
            # mixed `(t, None)` would otherwise yield std::tuple<T*,
            # std::optional<T>> and not match a uniformly pointer-form
            # downstream type.
            if isinstance(et, OptionalType) and et.uses_pointer_repr():
                if (not isinstance(elem, TpyNoneLiteral)
                        and not self.compat.is_lvalue(elem)
                        and self.ctx.is_type_non_copyable(et.inner)):
                    raise self.ctx.error(
                        f"cannot bind tuple element {i} of non-copyable type "
                        f"'{et.inner}' from rvalue. Bind to a local first, or "
                        f"place the literal directly at its consumer"
                        f"{NOCOPY_REMEDIATION_HINT}",
                        elem,
                    )
                if is_readonly or self.compat.is_const_ref_source(elem):
                    literal.elem_capture.append(CR)
                else:
                    literal.elem_capture.append(R)
                continue
            if not self.compat.is_lvalue(elem):
                literal.elem_capture.append(V)
            elif self.compat.is_owned_last_use_move(elem):
                # An owned local/param at its last use MOVES into the slot, so
                # the local OWNS the moved element (storage form) -- the local
                # analog of the field auto-move (above) and the scalar Own[T]
                # transfer. Consuming it here clears the unconsumed-Own warning.
                self.compat.check_own_consumption(elem)
                literal.elem_capture.append(V)
            else:
                # A ref-tuple of a non-copyable element (`tuple<T&, ...>`) has
                # no path to a value tuple later -- the C++ conversion fails
                # with cryptic <tuple> template errors. Reject with a clean
                # diagnostic; users either annotate the local for ownership
                # transfer or place the literal directly at its consumer.
                if self.ctx.is_type_non_copyable(et):
                    raise self.ctx.error(
                        f"cannot bind tuple element {i} of non-copyable type "
                        f"'{et}' by reference. Annotate the local as "
                        f"'tuple[Own[{et}], ...]' to consume the source, or "
                        f"place the literal directly at its consumer (return, "
                        f"call, outer literal) without an intermediate "
                        f"variable{NOCOPY_REMEDIATION_HINT}",
                        elem,
                    )
                if self.compat.is_const_ref_source(elem):
                    literal.elem_capture.append(CR)
                else:
                    literal.elem_capture.append(R)
                    # A REF-captured non-value element takes the source's
                    # address into a mutable `T*` tuple slot; a source param
                    # can't stay the default `const T&` borrow (mirrors the
                    # return-context marking above).
                    for tup_root in addr_taken_roots(elem):
                        self.ctx.mark_param_mutated(tup_root)

    def _unfold_loop_killed_isinstance(
            self, condition: TpyExpr, killed: set[str]) -> None:
        """Drop the static-true/false `isinstance` fold on a loop condition
        whose subject the loop body reassigns.

        The fold is computed from the subject's loop-entry narrowing; a rebind
        in the body makes it non-loop-invariant, so the condition must be
        re-checked each iteration (clearing `macro_expansion` routes codegen to
        the runtime `holds_alternative`/cast). Walks the boolean structure so
        `isinstance(t, T) and ...` is covered too -- mirrors the condition
        traversal in `narrowing._isinstance_facts`.
        """
        if isinstance(condition, TpyCall):
            if (condition.isinstance_var is not None
                    and condition.isinstance_var in killed
                    and isinstance(condition.macro_expansion, TpyBoolLiteral)):
                condition.macro_expansion = None
        elif isinstance(condition, TpyBinOp) and condition.op in ("&&", "||"):
            self._unfold_loop_killed_isinstance(condition.left, killed)
            self._unfold_loop_killed_isinstance(condition.right, killed)
        elif isinstance(condition, TpyUnaryOp) and condition.op == "!":
            self._unfold_loop_killed_isinstance(condition.operand, killed)

    def _save_ns_var_types(self) -> dict[str, TpyType]:
        """Save namespace variable types for later restoration."""
        result: dict[str, TpyType] = {}
        if self.ctx.func.current_ns:
            for name, binding in self.ctx.func.current_ns.all_bindings().items():
                if binding.kind == BindingKind.VARIABLE:
                    result[name] = binding.type
        return result

    def _restore_ns_var_types(self, saved: dict[str, TpyType]) -> None:
        """Restore namespace variable types from a saved snapshot."""
        if self.ctx.func.current_ns:
            for name, typ in saved.items():
                self.ctx.func.current_ns.update_variable_type(name, typ)

    def _retire_capture_binding(self, name: str | None,
                                prev: NameBinding | None,
                                was_bound: bool) -> None:
        """Undo an `except ... as` capture binding once its handler ends.

        Restores whatever the name meant before rather than just dropping it:
        a same-named local keeps the frame slot its assignments write to.
        `was_bound` guards the unbind -- the caller only registers a capture
        when the exception type resolved, so without it an unresolved handler
        would drop a pre-existing local of that name.
        """
        if not name or not was_bound or not self.ctx.func.current_ns:
            return
        if prev is not None:
            self.ctx.func.current_ns.bind(prev)
        else:
            self.ctx.func.current_ns.unbind(name)

    def _sync_ns_var_type(self, name: str, typ: TpyType) -> None:
        """Sync a single variable's namespace type to match scope."""
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.update_variable_type(name, typ)

    def _merge_arm_readonly(self, arms: list[dict[str, TpyType]]) -> None:
        """Keep a local readonly after sibling arms join when ANY arm left it
        readonly: the one slot may hold that arm's readonly reference."""
        bindings = self.ctx.func.current_scope.bindings
        for name in set().union(*arms):
            current = bindings.get(name)
            if current is None or isinstance(current, ReadonlyType):
                continue
            if any(isinstance(arm.get(name), ReadonlyType) for arm in arms):
                merged = ReadonlyType(current)
                self.ctx.func.current_scope.define(name, merged)
                self._sync_ns_var_type(name, merged)

    def _sync_promoted_var_types(self, names: set[str] | None = None) -> None:
        """Sync scope and namespace with var_types after a control-flow restore.

        After restoring scope/namespace to a pre-block state (if-branch,
        while, for-each), variables whose canonical declaration type was
        widened inside the block (e.g., int32 promoted to BigInt, or None
        promoted to Optional[T]) need to be re-synced so that subsequent
        analysis sees the correct type.

        Args:
            names: Variable names to check. If None, checks all tracked
                   variable declarations in the current function.
        """
        items = (
            self.ctx.func.var_decl_by_name.items() if names is None
            else ((n, self.ctx.func.var_decl_by_name[n]) for n in names if n in self.ctx.func.var_decl_by_name)
        )
        for name, var_decl in items:
            canonical = self.ctx.var_types.get(var_decl)
            if canonical is None:
                continue
            current = self.ctx.func.current_scope.lookup(name)
            if current is None:
                # Out of scope here: a block-declared local whose one
                # declaration is promoted -- with its anchor and its
                # assigned-ness -- by the first read or binding after the
                # block. Defining it here would bypass that promotion and
                # leave the name in scope but never recorded as assigned.
                continue
            # Preserve ReadonlyType from branch merge: var_types stores
            # unwrapped types, so re-wrap with ReadonlyType if the merge
            # determined this variable should be readonly.
            if isinstance(current, ReadonlyType):
                if unwrap_readonly(current) == canonical:
                    continue
                target = ReadonlyType(canonical)
            else:
                target = canonical
            if target != current:
                self.ctx.func.current_scope.define(name, target)
            self._sync_ns_var_type(name, target)

    def _is_class_name_receiver(self, expr: TpyExpr) -> bool:
        """True when `expr` is a class-name reference (e.g. `MyClass`), not
        an instance reference (`self`, `obj`, `f()`, ...)."""
        if not isinstance(expr, TpyName):
            return False
        ns = self.ctx.func.current_ns
        binding = ns.lookup(expr.name) if ns is not None else None
        return (binding is not None
                and self.ctx.class_record_of(binding) is not None)

    def _check_class_constant_write(self, target: TpyExpr, stmt: TpyStmt) -> None:
        """Reject `=` / `+=` on Final class constants; warn on instance-side
        writes to mutable ClassVar (CPython would create an instance attribute
        instead of writing through to class storage -- mypy/pyright already
        flag this; matching them keeps the tooling story consistent).
        """
        if not isinstance(target, TpyFieldAccess):
            return
        owner = target.class_constant_owner
        if owner is None:
            return
        if owner.is_final_class_constant(target.field):
            raise self.ctx.error(
                f"Cannot reassign Final class constant "
                f"'{owner.name}.{target.field}'",
                stmt,
            )
        if not self._is_class_name_receiver(target.obj):
            self.ctx.warning(
                f"Assigning to ClassVar '{owner.name}.{target.field}' via "
                f"instance writes to class storage in TPy (CPython creates "
                f"an instance attribute); use "
                f"'{owner.name}.{target.field} = ...' for portability",
                stmt,
            )

    def _record_field_rebind_outside_init(self, rec_info, field_name: str) -> None:
        """Mark `field_name` rebindable on its DECLARING record (see
        RecordInfo.fields_rebound_outside_init -- the CPython-interop
        borrow-view gate; a missed recording here is a soundness hole,
        not a diagnostic nit)."""
        for check in (rec_info,
                      *self.ctx.registry.iter_ancestor_records(rec_info)):
            if any(fld.name == field_name for fld in check.fields):
                check.fields_rebound_outside_init.add(field_name)
                return

    def _enforce_readonly_assignment_target(self, target: TpyExpr) -> None:
        """Reject assignments through readonly references, frozen fields, and readonly field declarations."""
        # ClassVar writes always go to class-scoped `static inline` storage,
        # never to instance fields, so receiver const-ness/freezing is
        # irrelevant -- the write doesn't touch the instance.
        if (isinstance(target, TpyFieldAccess)
                and target.class_constant_owner is not None):
            return
        # BaseN.field = v goes through `this`, but the syntactic receiver
        # (BaseN) has no value type, so the obj_type branch below can't
        # catch it -- gate on the current method's @readonly flag directly.
        if (isinstance(target, TpyFieldAccess)
                and target.unbound_self_parent_type is not None):
            cur = self.ctx.func.current_function
            if isinstance(cur, TpyFunction) and cur.is_readonly:
                raise self.ctx.error("Cannot mutate readonly reference", target)
            # The unbound spelling bypasses the receiver-typed branch below
            # (the class-name receiver has no cached expr type), so record
            # the rebind here; the write goes through `this`, so any
            # non-__init__ method body counts.
            if not (isinstance(cur, TpyFunction) and cur.name == "__init__"):
                pinfo = self.ctx.registry.get_record_for_type(
                    target.unbound_self_parent_type)
                if pinfo is not None:
                    self._record_field_rebind_outside_init(
                        pinfo, target.field)
        if isinstance(target, (TpyFieldAccess, TpySubscript)):
            obj_type = self.ctx.get_expr_type(target.obj)
            if obj_type is not None:
                check_type = obj_type
                if isinstance(check_type, OptionalType):
                    check_type = check_type.inner
                if isinstance(check_type, ReadonlyType):
                    raise self.ctx.error("Cannot mutate readonly reference", target)
                # isinstance narrowing strips ReadonlyType from the expr type;
                # the scope binding preserves it, so check there.
                if isinstance(target.obj, TpyName) and self.ctx.is_readonly_name(target.obj.name):
                    raise self.ctx.error("Cannot mutate readonly reference", target)
                # A field reached through a user __deref__ writes the deref
                # TARGET, not the receiver: a mutable handle over a readonly
                # payload (Rc[readonly[T]] / Box[readonly[T]]) is readonly there
                # even though the handle type isn't ReadonlyType. The checks
                # above see only the handle, so peel the deref chain and reject
                # if the object the field actually lives on is readonly.
                # Ptr[readonly[T]] is excluded -- it has its own, more specific
                # "assign through read-only pointer" diagnostic downstream.
                field_holder_type = check_type
                if (isinstance(target, TpyFieldAccess) and target.deref_depth > 0
                        and not isinstance(check_type, PtrType)):
                    # Walk the deref chain like the field-access reader does
                    # (expressions.py): a readonly hop both makes the target
                    # readonly AND selects the const __deref__ overload for the
                    # next hop, so thread the flag rather than rejecting eagerly.
                    deref_t: TpyType | None = check_type
                    deref_ro = False
                    for _ in range(target.deref_depth):
                        deref_t = self.type_ops.get_deref_target_type(
                            deref_t, is_readonly=deref_ro)
                        if deref_t is None:
                            break
                        if isinstance(deref_t, ReadonlyType):
                            deref_ro = True
                            deref_t = deref_t.wrapped
                    if deref_ro:
                        raise self.ctx.error("Cannot mutate readonly reference", target)
                    if deref_t is not None:
                        # The write lands on the deref TARGET's field, so
                        # that record (not the wrapper's) carries the rebind.
                        field_holder_type = deref_t
                # Frozen dataclass / immutable value type / readonly field:
                # reject assignment except self.field in __init__
                if isinstance(target, TpyFieldAccess):
                    actual = unwrap_readonly(check_type)
                    cur = self.ctx.func.current_function
                    rec = self.ctx.record_ctx.record
                    in_any_init = (
                        isinstance(cur, TpyFunction) and cur.name == "__init__"
                        and isinstance(target.obj, TpyName) and target.obj.name == "self"
                        and rec is not None
                    )
                    # The rebind fact (the borrow-view gate) must not depend
                    # on the local-records-only get_record lookup below: a
                    # module-qualified receiver (`mod.Cls`) misses there, and
                    # a deref-forwarded write lands on the deref target --
                    # resolve the holder record type-identity-first.
                    if not in_any_init:
                        rinfo = self.ctx.registry.get_record_for_type(
                            unwrap_readonly(field_holder_type))
                        if rinfo is not None:
                            self._record_field_rebind_outside_init(
                                rinfo, target.field)
                    if isinstance(actual, NominalType):
                        info = self.ctx.registry.get_record(actual.name)
                        if info is not None:
                            in_own_init = (
                                in_any_init and rec.name == actual.name
                            )
                            if (info.is_frozen or info.is_value_type) and not in_own_init:
                                kind = ("frozen dataclass" if info.is_frozen
                                        else "immutable value type")
                                raise self.ctx.error(
                                    f"Cannot assign to field '{target.field}' of {kind} '{actual.name}'",
                                    target,
                                )
                            # Walk self + MRO ancestors for a readonly field
                            # declaration (own and inherited); __init__ of the
                            # declaring class or any subclass is exempt.
                            if not in_any_init:
                                records_to_check = [info, *self.ctx.registry.iter_ancestor_records(info)]
                                for check in records_to_check:
                                    for fld in check.fields:
                                        if fld.name == target.field and isinstance(fld.type, ReadonlyType):
                                            raise self.ctx.error(
                                                f"Cannot assign to readonly field '{target.field}'",
                                                target,
                                            )

    def _find_consuming_iter(self, iterable_type: TpyType) -> FunctionInfo | None:
        """Find the consuming __iter__ overload for a type, if any.

        Only matches concrete types (list, user records with auto_own __iter__).
        Skips pending/unresolved types to avoid mismatched codegen.
        """
        # Only match concrete types, not pending/unresolved
        if isinstance(iterable_type, (PendingListType, PendingGenericInstanceType)):
            return None
        # Value-type elements: moving is identical to copying, so consuming
        # the container's internal structure is pure overhead.
        elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.registry)
        if elem is not None and elem.is_value_type():
            return None
        record_info = self.ctx.registry.get_record_for_type(iterable_type)
        if record_info is None:
            return None
        overloads = record_info.get_method_overloads("__iter__")
        for fi in overloads:
            if fi.is_consuming:
                # Build type substitution for generic types
                type_subst = self.type_ops.build_type_substitution(iterable_type)
                if type_subst:
                    fi = self.type_ops.substitute_method_type_params(fi, type_subst)
                return fi
        return None

    def _resolve_enum_iterable(self, stmt: TpyForEach) -> NominalType | None:
        """Check if for-each iterates over an enum type (e.g. `for c in Color`).

        Returns the enum NominalType if so, None otherwise.
        """
        iterable = stmt.iterable
        if not isinstance(iterable, TpyName):
            return None
        binding = self.ctx.func.current_ns.lookup(iterable.name) if self.ctx.func.current_ns else None
        if binding is None:
            return None
        if binding.kind == BindingKind.ENUM and binding.enum_type is not None:
            return binding.enum_type
        if binding.kind == BindingKind.IMPORTED_NAME and binding.import_source:
            src_mod, original_name = binding.import_source
            # Resolve via the source module's qname, not the bare canonical
            # name: a same-canonical-named local enum wins the bare
            # `registry.enums` slot, so `get_enum(original_name)` could
            # return the local enum instead of the imported one.
            enum_type = self.ctx.registry.find_enum_by_qname(
                f"{src_mod}.{original_name}")
            if enum_type is not None:
                return enum_type
        return None

    def analyze_stmt(self, stmt: TpyStmt) -> None:
        """Analyze a statement."""
        bt = self.ctx.func.borrow_tracker
        prev_stmt = bt.current_stmt
        bt.current_stmt = stmt
        self._stmt_stack.append(stmt)
        is_compound = isinstance(stmt, (TpyForEach, TpyWhile, TpyIf, TpyWith,
                                        TpyTry, TpyMatch))
        if is_compound:
            self.ctx.func.compound_stack.append(stmt)
        enclosing_genexprs = self.ctx.stmt_genexprs
        self.ctx.stmt_genexprs = []
        analyzed = False
        try:
            self._analyze_stmt_dispatch(stmt)
            self._check_retained_genexprs(stmt)
            analyzed = True
        finally:
            self.ctx.stmt_genexprs = enclosing_genexprs
            if is_compound:
                self.ctx.func.compound_stack.pop()
            bt.current_stmt = prev_stmt
            self._stmt_stack.pop()
            self._retire_block_nested_defs(stmt)
            # Flush post-access ptr narrowing queued during expression analysis.
            # See FunctionTrackingState.pending_non_null_ptr_vars for rationale
            # (deferred to statement boundary so within-statement sibling
            # accesses keep their individual null checks).
            pending = self.ctx.func.pending_non_null_ptr_vars
            if pending:
                self.ctx.func.non_null_ptr_vars |= pending
                pending.clear()
        if analyzed and isinstance(stmt, (TpyForEach, TpyWhile)):
            self._note_pass_scoped_frames(stmt)

    def _check_retained_genexprs(self, stmt: TpyStmt) -> None:
        """A statement that binds a LAZY value keeps every genexpr it created
        alive past itself (`g = relay(x * k for x in xs)`), so a name the
        genexpr captured can be rebound before it is pulled.

        Only a binding can do that: a protocol-typed value cannot be stored in
        a field or a container, and a `for` head is judged where the genexpr is
        built (its loop body is in view there). What the genexpr was built on
        is then checked against what can run while the kept value is live: a
        narrowing it took does not hold, and a capture becomes one that may be
        rebound."""
        created = self.ctx.stmt_genexprs
        if not created:
            return
        if isinstance(stmt, TpyVarDecl):
            value, kept = stmt.init, {stmt.name}
        elif isinstance(stmt, TpyAssign):
            value = stmt.value
            kept = {stmt.target.name} if isinstance(stmt.target, TpyName) else set()
        elif isinstance(stmt, TpyTupleUnpack):
            value, kept = stmt.value, {t for t in stmt.targets if t is not None}
        else:
            return
        bound = self.ctx.get_expr_type(value) if value is not None else None
        if bound is None or not self._holds_lazy_iteration(bound):
            return
        later = self._names_rebound_while_live(stmt, kept)
        for gx, narrowed in created:
            stale = [n for n in narrowed if later is None or n in later]
            if stale:
                raise self.ctx.error(
                    f"'{stale[0]}' is narrowed here, but this generator "
                    f"expression is kept past the statement and '{stale[0]}' "
                    f"can be rebound before it runs; bind the narrowed value "
                    f"to a local first", gx)
            func = gx.frame_func
            if func is not None:
                gx.frame_rebindable = tuple(dict.fromkeys(
                    gx.frame_rebindable
                    + tuple(n for n in func.capture_params
                            if later is None or n in later)))

    @staticmethod
    def _holds_lazy_iteration(typ: TpyType) -> bool:
        """Whether a value of this type is iteration that has not run yet: a
        genexpr, a generator, a lazy combinator."""
        bare = unwrap_readonly(unwrap_own(unwrap_ref_type(typ)))
        if isinstance(bare, GenExprType) or is_iterator_adapter(bare):
            return True
        return (is_protocol_type(bare)
                and bare.qualified_name() in (qnames.ITERATOR, qnames.ITERABLE))

    def _names_rebound_while_live(self, stmt: TpyStmt,
                                  kept: 'set[str]') -> 'set[str] | None':
        """The names something can rebind while the value `stmt` binds to
        `kept` is still live: a binder between `stmt` and the end of the last
        statement that reads a kept name, any binder in a loop `stmt` sits in
        (it runs again before the next iteration's read), a `nonlocal` a nested
        def declares. None when the body is not in view."""
        fn = self.ctx.func.current_function
        body = (fn.body if isinstance(fn, TpyFunction)
                else self.ctx.module_top_level_stmts)
        if body is None or stmt.loc is None:
            return None
        start = (stmt.loc.line, stmt.loc.column)
        unknown = 1 << 60

        def end_of(s: TpyStmt) -> int:
            return (s.loc.end_line if s.loc is not None and s.loc.end_line
                    else unknown)

        # Where the kept value stops being live. A read inside a loop keeps it
        # live to the end of the outermost such loop; a nested def that reads
        # it, or no kept name at all, leaves the horizon open.
        horizon = 0 if kept else unknown
        # A statement that reads a kept name and binds another (`g = tmp`, the
        # desugared halves of a tuple-literal unpack) hands the value on: the
        # new name is kept too.
        kept = set(kept)
        grew = True
        while grew:
            grew = False

            def on_stmt(s: TpyStmt) -> None:
                nonlocal grew
                if s is stmt or not any(kept & collect_name_refs(e) for e in s.exprs()):
                    return
                handed_on = bound_names_of(s) - kept
                if handed_on and not isinstance(s, (TpyForEach, TpyWith)):
                    kept.update(handed_on)
                    grew = True
            walk_body_stmts(body, lambda e: None, on_stmt)

        def reads(stmts: 'list[TpyStmt]', loop_end: 'int | None') -> None:
            nonlocal horizon
            for s in stmts:
                here = loop_end
                if here is None and isinstance(s, (TpyForEach, TpyWhile)):
                    here = end_of(s)
                if isinstance(s, TpyNestedDef):
                    if kept & _nested_def_free_names(s.func):
                        horizon = unknown
                    continue
                if (s is not stmt and s.loc is not None
                        and (s.loc.line, s.loc.column) > start
                        and any(kept & collect_name_refs(e) for e in s.exprs())):
                    horizon = max(horizon, here if here is not None else end_of(s))
                for sub in s.sub_bodies():
                    reads(sub, here)
        reads(body, None)

        names: set[str] = set()
        enclosing = [s for s in self.ctx.func.compound_stack
                     if isinstance(s, (TpyForEach, TpyWhile))]

        def scan(stmts: 'list[TpyStmt]', always: bool) -> None:
            def on_stmt(s: TpyStmt) -> None:
                if s is stmt:
                    return
                if isinstance(s, TpyNestedDef) or always:
                    names.update(_names_rebound_by(s))
                elif (s.loc is not None
                        and start < (s.loc.line, s.loc.column)
                        and s.loc.line <= horizon):
                    names.update(_names_rebound_by(s))
            walk_body_stmts(stmts, lambda e: None, on_stmt)
        scan(body, False)
        for loop in enclosing:
            scan(loop.body, True)
        return names

    def _retire_block_nested_defs(self, stmt: TpyStmt) -> None:
        """End the reach of every nested `def` this statement's body bound:
        its callable is declared inside the block, so no read past the
        block can name it, whatever the paths through it assigned."""
        live = self.ctx.func.nested_def_block_defs
        if not live:
            return
        for name in [n for n, (owner, _) in live.items() if owner is stmt]:
            self.ctx.func.nested_def_block_dead[name] = live.pop(name)[1]

    def _is_sole_else_if(self, stmt: TpyIf) -> bool:
        owner = self._stmt_stack[-2] if len(self._stmt_stack) >= 2 else None
        return (isinstance(owner, TpyIf) and len(owner.else_body) == 1
                and owner.else_body[0] is stmt)

    def _place_arm_decl(self, owner: TpyStmt | None, body: list[TpyStmt],
                        name: str, typ: TpyType | None) -> None:
        """Where an arm `body` that declares `name` itself does so: at its
        first binding when that is a statement of the block itself (for the
        per-arm-declared `owner`, recorded in `arm_decl_sites` for the
        retype in `resolve_all`). When the first binding sits in a nested
        block, its declaration ends at that block's brace, so a statement
        after it declares in front of it instead; sema never recorded that
        predecl because the sibling arm's binding was still in scope. A
        nested block that ends the block gets the same rule inside it."""
        for i, s in enumerate(body):
            if _declares_here(s, name):
                if owner is not None:
                    self.ctx.func.arm_decl_sites.append((owner, name, s))
                return
            if name not in scope_bound_names([s]):
                continue
            if name in self.ctx.if_branch_decls.get(s, {}):
                return
            if i + 1 < len(body):
                # A hoist takes its const from the name's function-wide
                # stmt-borrow bit, not from this arm's bindings: an arm whose
                # own view is mutable would get a const slot it writes
                # through, so it stays undeclared (a located lowering reject;
                # BUGS.md#hoist-const-from-name-wide-borrow-bit).
                if (typ is not None and not typ.is_value_type()
                        and not isinstance(typ, ReadonlyType)
                        and self.ctx.func.stmt_borrow_decls.get(name)):
                    return
                self.ctx.record_branch_decls(s, {name: typ})
                self.deduction.promote_hoisted_views(
                    {name}, self.ctx.block_locals_of.get(s, frozenset()))
            else:
                for sub in s.sub_bodies():
                    self._place_arm_decl(owner, sub, name, typ)
            return

    def _declare_carried_in_arms(
            self, arms: 'list[tuple[list[TpyStmt], set[str], dict[str, TpyType]]]',
            front: set[str]) -> None:
        """Place the declaration of every name an arm got carried in from an
        earlier sibling arm (`_enter_sibling_arm`) and that the statement
        does not declare in front of itself (`front`): the arm declares it
        at its own first binding, which the carried binding hid from any
        nested block's own predecl. Each arm is (body, carried names, the
        arm's closing bindings)."""
        for body, carried, bindings in arms:
            for name in sorted(carried - front):
                self._place_arm_decl(None, body, name, bindings.get(name))

    def _enclosing_block_phrase(self) -> 'str | None':
        """The statement the `def` now being analyzed sits in, named for a
        diagnostic -- None when it is a direct element of the scope's body
        and its binding therefore lasts for the whole scope."""
        if len(self._stmt_stack) < 2:
            return None
        owner = self._stmt_stack[-2]
        keyword = _BLOCK_KEYWORD.get(type(owner))
        where = f" on line {owner.loc.line}" if owner.loc is not None else ""
        if keyword is None:
            return f"the enclosing block{where}"
        return f"the '{keyword}' block{where}"

    def _analyze_stmt_dispatch(self, stmt: TpyStmt) -> None:
        # Coroutine[T] single-use: reject only genuine drops of a bare
        # async-def call (statement-level `f()`, field/element store) --
        # binding (`c = f()`) and `return f()` are owned consumers now.
        # See _check_no_bare_async_call and docs/ASYNC_DESIGN.md
        # "Coroutine value model".
        self._check_no_bare_async_call(stmt)

        if isinstance(stmt, TpyVarDecl):
            self._analyze_var_decl(stmt)
        elif isinstance(stmt, TpyTupleUnpack):
            self._analyze_tuple_unpack(stmt)
        elif isinstance(stmt, TpyAssign):
            self._analyze_assign(stmt)
        elif isinstance(stmt, TpyAugAssign):
            self._analyze_aug_assign(stmt)
        elif isinstance(stmt, TpyDelItem):
            self._analyze_del_item(stmt)
        elif isinstance(stmt, TpyDelVar):
            self._analyze_del_var(stmt)
        elif isinstance(stmt, TpyDelAttr):
            self._analyze_del_attr(stmt)
        elif isinstance(stmt, TpyExprStmt):
            self.expr.analyze_expr(stmt.expr)
        elif isinstance(stmt, TpyReturn):
            prev_moves = self.ctx.func.consuming_return_fields
            prev_in_return = self.ctx.func.in_return_value
            self.ctx.func.consuming_return_fields = (
                self._consuming_return_move_fields(stmt))
            self.ctx.func.in_return_value = True
            try:
                self._analyze_return_value(stmt)
            finally:
                self.ctx.func.consuming_return_fields = prev_moves
                self.ctx.func.in_return_value = prev_in_return
            self.init.mark_terminated()
        elif isinstance(stmt, TpyYield):
            self._analyze_yield(stmt)
        elif isinstance(stmt, TpyIf):
            head = self._analyze_condition_walrus(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_type_facts, else_type_facts = self.narrowing.condition_type_facts(stmt.condition)
            ptr_nn_then, ptr_nn_else = self.narrowing.condition_ptr_null_facts(stmt.condition)
            range_true, range_false = self.narrowing.condition_range_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            stmt.else_type_facts = self._filter_union_codegen_facts(else_type_facts)
            scope_before = set(self.ctx.func.current_scope.bindings.keys())
            pending_before = set(self.ctx.func.pending_loop_vars)
            assigned_before = frozenset(self.ctx.func.definitely_assigned)
            before = self.init.save()
            consumed_before = self.ctx.func.current_consumed_own_params.copy()
            # Save binding types for ReadonlyType merge after branches
            bindings_before = dict(self.ctx.func.current_scope.bindings)
            ns_types_before = self._save_ns_var_types()
            # Then-body: reached only through an evaluation that came out
            # true, so it is granted what every such evaluation bound.
            self.ctx.func.definitely_assigned |= head.if_true
            self.ctx.func.narrowed_types.update(then_type_facts)
            self.ctx.func.non_null_ptr_vars |= ptr_nn_then
            self._apply_range_facts(range_true)
            for s in stmt.then_body:
                self.analyze_stmt(s)
            then_state = self.init.save()
            consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
            then_terminated = self.ctx.func.init_terminated
            bindings_after_then = dict(self.ctx.func.current_scope.bindings)
            pending_after_then = set(self.ctx.func.pending_loop_vars)
            carried_else = self._enter_sibling_arm(
                bindings_before, ns_types_before, before)
            # Else-body: the mirror -- what every evaluation that came out
            # false bound.
            self.ctx.func.definitely_assigned |= head.if_false
            self.ctx.func.current_consumed_own_params = consumed_before.copy()
            self.ctx.func.narrowed_types.update(else_type_facts)
            self.ctx.func.non_null_ptr_vars |= ptr_nn_else
            self._apply_range_facts(range_false)
            for s in stmt.else_body:
                self.analyze_stmt(s)
            else_state = self.init.save()
            consumed_after_else = self.ctx.func.current_consumed_own_params.copy()
            else_terminated = self.ctx.func.init_terminated
            bindings_after_else = dict(self.ctx.func.current_scope.bindings)
            self.init.merge_branches(then_state, else_state)
            # Merge consumed Own[T] params: must be consumed on ALL non-terminated paths
            if then_terminated and else_terminated:
                self.ctx.func.current_consumed_own_params = consumed_after_then | consumed_after_else
            elif then_terminated:
                self.ctx.func.current_consumed_own_params = consumed_after_else
            elif else_terminated:
                self.ctx.func.current_consumed_own_params = consumed_after_then
            else:
                self.ctx.func.current_consumed_own_params = consumed_after_then & consumed_after_else
            self._merge_arm_readonly([bindings_after_then, bindings_after_else])
            # Sync scope/namespace with var_types for variables whose
            # declaration type was promoted inside a branch.
            self._sync_promoted_var_types(
                set(bindings_after_then) | set(bindings_after_else)
            )
            self._unbind_branch_pending(
                scope_before, pending_before, pending_after_then,
                set(bindings_after_then), set(bindings_after_else))
            # Detect variables first declared inside branches that need
            # pre-declaration. Skip when both branches terminate (no code
            # after the if needs the variable).
            branch_new = set(self.ctx.func.current_scope.bindings.keys()) - scope_before
            if not self.ctx.func.init_terminated:
                newly_assigned = self.ctx.func.definitely_assigned - assigned_before
                predecl = (branch_new & newly_assigned) - self.ctx.func.global_declarations
            else:
                predecl = set()
            if not self.ctx.func.init_terminated:
                predecl -= self._frame_block_keeps(
                    branch_new, predecl, "'if' block", keepable=branch_new)
            # An `if` that is the whole `else` body of another `if` (an
            # `elif`, or `else:` over a nested `if` -- the AST is the same)
            # has nothing after it in that block to read what its arms bound,
            # so each arm declares the name itself instead of a predecl in
            # front of it. A read after the whole chain is the enclosing
            # if's own predecl. The type is the one joined here either way.
            per_arm = self._is_sole_else_if(stmt)
            self.ctx.block_locals_of[stmt] = (
                branch_new if per_arm else branch_new - predecl)
            if predecl:
                decls = self.ctx.record_branch_decls(stmt, {
                    name: self.ctx.func.current_scope.lookup(name)
                    for name in sorted(predecl)
                }, per_arm=per_arm)
                self.deduction.promote_hoisted_views(predecl, branch_new - predecl)
                if per_arm:
                    for name, typ in decls.items():
                        self._place_arm_decl(stmt, stmt.then_body, name, typ)
                        self._place_arm_decl(stmt, stmt.else_body, name, typ)
            self._declare_carried_in_arms(
                [(stmt.else_body, carried_else, bindings_after_else)], predecl)
        elif isinstance(stmt, TpyWhile):
            head = self._analyze_condition_walrus(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_type_facts, _ = self.narrowing.condition_type_facts(stmt.condition)
            ptr_nn, _ = self.narrowing.condition_ptr_null_facts(stmt.condition)
            range_true, _ = self.narrowing.condition_range_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            before = self.init.save()
            consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
            # Save namespace types -- loop_scope() restores scope bindings
            # automatically, but namespace mutations inside the loop persist.
            ns_types_before_while = self._save_ns_var_types()
            # Decided before the body: the ranges after it are the loop's.
            runs_once = self.narrowing.condition_provably_true(stmt.condition)
            body_kills = collect_fact_kills(stmt.body,
                                            extra_exprs=[stmt.condition])
            # An `isinstance` fold rests on the loop-entry narrowing of its
            # subject; if the body rebinds that subject the condition is not
            # loop-invariant, so drop the static fold and re-check each
            # iteration (else codegen emits an exit-less `while (true)`).
            self._unfold_loop_killed_isinstance(stmt.condition, body_kills.names)
            with self._loop_body_scope(stmt) as (inner_scope, edges):
                self.init.apply_loop_entry_facts(
                    before,
                    condition_type_facts=then_type_facts,
                    kills=body_kills,
                )
                # The body runs on the path the head came out TRUE -- the
                # `if` arm's then-body, same rule.
                self.ctx.func.definitely_assigned |= head.if_true
                # Applied separately from apply_loop_entry_facts because
                # that method only handles type narrowing, not ptr non-null.
                self.ctx.func.non_null_ptr_vars |= ptr_nn
                self._apply_range_facts(range_true)
                for s in stmt.body:
                    self.analyze_stmt(s)
            self._finish_loop_clauses(
                stmt, inner_scope, edges, before=before,
                consumed_before=consumed_before_loop,
                ns_types_before=ns_types_before_while, runs_once=runs_once,
                # Leaving through the head means it came out FALSE -- the
                # `if` arm's else-body.
                head_exit_assigned=head.if_false)
        elif isinstance(stmt, TpyForEach):
            if stmt.is_tuple_unpack and stmt.body:
                # The head unpack is over the loop's per-iteration holder
                # whoever built the node (parser, genexpr, a macro), so the
                # ForEach's own fact is what marks it.
                head = stmt.body[0]
                if isinstance(head, TpyTupleUnpack):
                    head.is_loop_head = True
                    head.loop_body_rebinds = frozenset(scan_reassigned_vars(
                        stmt.body[1:],
                        pre_declared={t for t in head.targets
                                      if t is not None}).reassigned)
            if stmt.is_async:
                self._analyze_async_for(stmt)
                return
            # Check for enum iteration: `for c in Color`
            enum_type = self._resolve_enum_iterable(stmt)
            if enum_type is not None:
                stmt.enum_iterable = enum_type
                elem_type = enum_type
                self._check_loop_var_rebind(stmt, elem_type)
                self._record_for_loop_var_type(stmt, elem_type)
                runs_once = self.narrowing.for_head_provably_runs(stmt)
                before = self.init.save()
                consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
                ns_types_before_foreach = self._save_ns_var_types()
                with self._loop_body_scope(stmt) as (inner_scope, edges):
                    self.init.apply_loop_entry_facts(
                        before, kills=collect_fact_kills(stmt.body))
                    with self.scopes.loop_var(inner_scope, stmt.var, elem_type, inner_scope.depth, is_foreach=True):
                        for s in stmt.body:
                            self.analyze_stmt(s)
                self._finish_loop_clauses(
                    stmt, inner_scope, edges, before=before,
                    consumed_before=consumed_before_loop,
                    ns_types_before=ns_types_before_foreach,
                    runs_once=runs_once, elem_type=elem_type)
            else:
                self.ctx.for_head_bodies.append(stmt.body)
                try:
                    iterable_type = self.expr.analyze_expr(stmt.iterable)
                finally:
                    self.ctx.for_head_bodies.pop()
                is_readonly_iterable = isinstance(iterable_type, ReadonlyType)
                inner_iterable_type = unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type)))
                # Resolve TypeParamRef to its bound for element type extraction
                resolved_for_iter = inner_iterable_type
                if isinstance(inner_iterable_type, TypeParamRef):
                    bound = self.type_ops.get_type_param_bound(inner_iterable_type.name)
                    if bound is not None and is_protocol_type(bound):
                        resolved_for_iter = bound
                elem_type = self.iterable.get_iterable_element_type(resolved_for_iter, loc=stmt.loc)
                # Elements from a readonly iterable inherit readonly status
                if is_readonly_iterable and not elem_type.is_value_type():
                    elem_type = ReadonlyType(unwrap_readonly(elem_type))
                elem_type = self._infer_new_local_type(
                    stmt.var, elem_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                stmt.elem_type = make_ref(elem_type)
                if contains_pending_leaf(stmt.elem_type):
                    self.ctx.func.pending_elem_type_fields.append((stmt, "elem_type"))

                # Auto-consuming decision is deferred until after body analysis
                # (see below) so we know whether the loop var is mutated.

                is_direct_next_iter = builtin_modules.get_error_return_next_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                is_iter_based = builtin_modules.get_iter_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                is_protocol_iter = is_protocol_type(resolved_for_iter) and resolved_for_iter.qualified_name() in ("typing.Iterator", "typing.Iterable")
                # Decided before the body: the ranges the head is read
                # against are the pre-loop ones, as on the `while` side.
                runs_once = self.narrowing.for_head_provably_runs(stmt)
                before = self.init.save()
                consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
                ns_types_before_foreach = self._save_ns_var_types()
                with self._loop_body_scope(stmt) as (inner_scope, edges):
                    self.init.apply_loop_entry_facts(
                        before, kills=collect_fact_kills(stmt.body))
                    # Track range facts for loop variable from range() calls
                    self._track_for_range_facts(stmt)
                    prior_iter = self.ctx.func.loop_var_iterable.get(stmt.var)
                    check_iter_receiver_loans(
                        self.ctx, stmt.iterable, inner_iterable_type)
                    self._register_foreach_iter_loans(stmt, iterable_type)
                    self._note_loop_var_iteration(
                        stmt.var, elem_type, prior_iter,
                        placeable=not self._iteration_yields_copies(iterable_type))
                    # A user `__iter__` that mutates its receiver needs a
                    # non-const receiver; record that so an enclosing read-only
                    # method isn't wrongly inferred const (the loop_var_iterable
                    # self-tracing set above must already be populated).
                    _record_iter_receiver_mutation(
                        self.ctx, stmt.iterable, inner_iterable_type,
                        check_loans=False)
                    if is_direct_next_iter or is_protocol_iter:
                        iter_depth = inner_scope.depth
                    elif is_iter_based:
                        # __iter__() either references the container's storage
                        # (NativeIterable types -- builtins like list/dict/str
                        # and span-backed types like SpanIter) or creates a
                        # fresh owned iterator (user-defined iterators).
                        # For the former, the loop-var lifetime is the
                        # container's; for the latter, it's loop-body scope.
                        #
                        # Known gap (no failing test): user records whose
                        # `__iter__()` returns `SpanIter` (e.g. ArrayList,
                        # Stack with `__iter__(self) -> SpanIter[T]`) are NOT
                        # `is_native_iterable`, but their iterator does
                        # reference the container's storage. Provenance
                        # tracking only activates for non-value loop vars
                        # from param-derived iterables, so the gap is
                        # currently invisible. If it surfaces, recover by
                        # also checking whether `__iter__()` returns a
                        # `SpanIter` here.
                        references_container = builtin_modules.is_native_iterable(
                            inner_iterable_type, registry=self.ctx.registry
                        )
                        if references_container:
                            if self.compat.is_lvalue(stmt.iterable):
                                iter_depth = self.scopes.get_expr_scope_depth(stmt.iterable)
                            else:
                                iter_depth = inner_scope.depth
                        else:
                            iter_depth = inner_scope.depth
                    elif self.compat.is_lvalue(stmt.iterable):
                        # For-each var references container's storage -- use container's depth.
                        iter_depth = self.scopes.get_expr_scope_depth(stmt.iterable)
                    else:
                        # For rvalue iterables (calls), C++ extends the temporary's lifetime
                        # to the for statement, but it dies when the loop ends. Use body depth
                        # so that escaping to any outer-scoped variable is caught.
                        iter_depth = inner_scope.depth
                    # Track provenance for loop vars whose type participates in
                    # provenance tracking, when iterating over a param-derived iterable.
                    # Inside a generator the iteration source is always materialized
                    # on the resumable frame (a captured param/self, or a `__for_src`
                    # field for locals/temporaries), so any loop var aliases
                    # frame-held storage and outlives suspension -- a durable borrow
                    # source for a yield. Generators cannot `return <value>`, so the
                    # only consumer of the safe-to-return provenance in a generator body is the
                    # borrow-yield rooting check; broadening it here is contained.
                    cur_fn = self.ctx.func.current_function
                    in_generator = isinstance(cur_fn, TpyFunction) and cur_fn.is_generator
                    # A frame-slot-rooted borrow source (generator / Iterator[T]
                    # value, element non-value non-Own) yields ephemeral borrows
                    # valid only until the next __next__(): they must NOT be
                    # treated as durable (kept out of provenance / safe_to_return)
                    # and escapes are rejected in the loop body. Container sources
                    # are durable and keep their normal provenance.
                    ephemeral_src = self._is_ephemeral_borrow_loop_source(inner_iterable_type)
                    # The same question with an open `T` element counted as a
                    # borrow -- input to the generic-yield provenance gate, not
                    # to any diagnostic: at a value instantiation such a source
                    # lends nothing, so the loop var must stay durable here.
                    open_eph_src = (
                        not ephemeral_src
                        and self._is_ephemeral_borrow_loop_source(
                            inner_iterable_type, open_param_borrows=True))
                    track_loop_prov = (
                        not ephemeral_src
                        and _needs_provenance_tracking(unwrap_readonly(elem_type))
                        and (self.compat.is_param_derived_expr(stmt.iterable)
                             or in_generator)
                    )
                    if track_loop_prov:
                        self.init.add_loop_var_provenance(stmt.var)
                    eph_added = self._mark_ephemeral_loop_targets(
                        stmt, ephemeral_src, open_eph_src)
                    self.ctx.func.mutated_loop_vars.discard(stmt.var)
                    self.ctx.func.consumed_loop_vars.discard(stmt.var)
                    self.ctx.func.deferred_loop_copy_warnings.pop(stmt.var, None)
                    self._check_loop_var_rebind(stmt, elem_type)
                    self._record_for_loop_var_type(stmt, elem_type)
                    with self.scopes.loop_var(inner_scope, stmt.var, elem_type, iter_depth, is_foreach=True):
                        for s in stmt.body:
                            self.analyze_stmt(s)
                    if track_loop_prov:
                        self.init.remove_loop_var_provenance(stmt.var)
                    for name in eph_added:
                        self.ctx.func.ephemeral_borrow_vars.pop(name, None)
                # A mutated loop var lends a MUTABLE borrow of the storage it
                # was iterated out of, so that storage cannot bind const
                # either. Keyed on the iterable's ROOT rather than on the
                # statement shape, which is what makes the rule compose: an
                # inner loop marks the outer loop var, whose own foreach then
                # marks ITS source in turn. Same edge the tuple unpack records
                # when it takes an element borrow out of a loop var.
                if stmt.var in self.ctx.func.mutated_loop_vars:
                    iter_root = _iter_source_root(stmt.iterable)
                    if iter_root is not None and iter_root != stmt.var:
                        self.ctx.mark_loop_var_mutated(iter_root)
                # Set const-ref binding when the loop var was never mutated.
                # mutated_loop_vars was cleared for stmt.var before entering the
                # loop body, so it only reflects mutations from this loop.
                # Only for non-value types or expensive-to-copy value types
                # (BigInt, String, tuples with expensive elements). Cheap
                # primitives (int32_t, bool, double, etc.) are better copied
                # into a register than referenced through a pointer.
                # For synthetic tuple-unpack loop vars, skip const binding when
                # the unpack has elements that need mutable references --
                # const tuple prevents T& bindings via std::get.
                unwrapped = unwrap_qualifiers(elem_type)
                worth_const_ref = (not unwrapped.is_value_type()
                                   or unwrapped.is_expensive_copy())
                needs_mut_unpack = False
                if stmt.is_tuple_unpack and stmt.body:
                    first = stmt.body[0]
                    if isinstance(first, TpyTupleUnpack) and any(first.is_ref):
                        needs_mut_unpack = True
                if (worth_const_ref
                        and stmt.var not in self.ctx.func.mutated_loop_vars
                        and stmt.var not in self.ctx.func.consumed_loop_vars
                        and not needs_mut_unpack):
                    stmt.const_loop_var = True
                # Auto-consuming iteration: use consuming __iter__ when the
                # container is at last use and elements are either mutated
                # or consumed (copied into owned storage via Own[T] params,
                # append, etc.). The container is dead after the loop, so
                # moving it into OwnIter is free (pointer swap). Consumed
                # elements become movable at last use, avoiding copies.
                loop_var_needs_ownership = (
                    stmt.var in self.ctx.func.mutated_loop_vars
                    or stmt.var in self.ctx.func.consumed_loop_vars
                )
                if (loop_var_needs_ownership
                        and not stmt.hoist_loop_var
                        and not unwrapped.is_value_type()
                        and self.compat.is_auto_move_use(stmt.iterable)):
                    consuming_fi = self._find_consuming_iter(inner_iterable_type)
                    if consuming_fi is not None:
                        stmt.consuming_iter_fi = consuming_fi
                        # Suppress copy warnings for the loop variable --
                        # elements will be moved, not copied.
                        deferred = self.ctx.func.deferred_loop_copy_warnings.pop(stmt.var, None)
                        if deferred:
                            for idx in sorted(deferred, reverse=True):
                                del self.ctx.diagnostics[idx]
                # Also suppress copy warnings when elem_type is Own[T]
                # (e.g. Iterable[Own[T]] params) -- elements will be moved.
                if (stmt.consuming_iter_fi is None
                        and isinstance(elem_type, OwnType)):
                    deferred = self.ctx.func.deferred_loop_copy_warnings.pop(stmt.var, None)
                    if deferred:
                        for idx in sorted(deferred, reverse=True):
                            del self.ctx.diagnostics[idx]
                self._finish_loop_clauses(
                    stmt, inner_scope, edges, before=before,
                    consumed_before=consumed_before_loop,
                    ns_types_before=ns_types_before_foreach,
                    runs_once=runs_once, elem_type=elem_type)
        elif isinstance(stmt, TpyBreak):
            if self.ctx.func.loop_depth == 0:
                raise self.ctx.error("'break' outside loop", stmt)
            # Only the assignment lattice crosses the break edge, and it
            # crosses as a plain set: the other flow families merge with
            # UNION (a consumed `Own` param, a borrow), so carrying them
            # here would start rejecting valid code after any loop that
            # can break.
            self.ctx.func.loop_clause_edges[-1].breaks.append(
                self._assigned_here())
            self.init.mark_terminated()
        elif isinstance(stmt, TpyContinue):
            if self.ctx.func.loop_depth == 0:
                raise self.ctx.error("'continue' outside loop", stmt)
            # A `continue` leaves the body clause exactly as a `break` does;
            # it differs only in where it lands, which is why its snapshots
            # join the body's own exit state instead of the post-loop one.
            self.ctx.func.loop_clause_edges[-1].continues.append(
                self._assigned_here())
            self.init.mark_terminated()
        elif isinstance(stmt, TpyAssert):
            self.expr.analyze_condition(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            if stmt.message is not None:
                msg_type = self.expr.analyze_expr(stmt.message)
                if not is_any_str_type(msg_type):
                    raise self.ctx.error("assert message must be a string", stmt)
            then_type_facts, _ = self.narrowing.condition_type_facts(stmt.condition)
            ptr_nn, _ = self.narrowing.condition_ptr_null_facts(stmt.condition)
            range_true, _ = self.narrowing.condition_range_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            self.ctx.func.narrowed_types.update(then_type_facts)
            self.ctx.func.non_null_ptr_vars |= ptr_nn
            self._apply_range_facts(range_true)
        elif isinstance(stmt, TpyGlobal):
            self._analyze_global_stmt(stmt)
        elif isinstance(stmt, TpyRaise):
            self._analyze_raise(stmt)
        elif isinstance(stmt, TpyTry):
            self._analyze_try(stmt)
        elif isinstance(stmt, TpyMatch):
            self.match.analyze_match(stmt)
        elif isinstance(stmt, TpyWith):
            self._analyze_with(stmt)
        elif isinstance(stmt, TpyNonlocal):
            self._analyze_nonlocal(stmt)
        elif isinstance(stmt, TpyNestedDef):
            self._analyze_nested_def(stmt)

    def _apply_range_facts(self, facts: dict[str, 'ValueRange']) -> None:
        """Apply integer range facts, intersecting with any existing ranges."""
        for name, new_range in facts.items():
            existing = self.ctx.func.value_ranges.get(name)
            if existing is not None:
                self.ctx.func.value_ranges[name] = ValueRange.intersect(existing, new_range)
            else:
                self.ctx.func.value_ranges[name] = new_range

    def _track_for_range_facts(self, stmt: TpyForEach) -> None:
        """Set range facts for loop variable when iterating over range().

        Detects: range(len(arr)), range(N).
        """
        iterable = stmt.iterable
        if not isinstance(iterable, TpyCall) or iterable.func_name != "range":
            return

        args = iterable.args
        if len(args) == 1:
            arg = args[0]
            # range(len(arr)) -- symbolic bound
            if (isinstance(arg, TpyCall) and arg.func_name == "len"
                    and len(arg.args) == 1 and isinstance(arg.args[0], TpyName)):
                self.ctx.func.value_ranges[stmt.var] = ValueRange.for_range_index(
                    stop_len_of=arg.args[0].name,
                )
                return
            # range(N) -- literal bound
            if isinstance(arg, TpyIntLiteral):
                self.ctx.func.value_ranges[stmt.var] = ValueRange.for_range_index(
                    stop_literal=arg.value,
                )
                return
            # range(n) -- unknown bound, but still non-negative
            self.ctx.func.value_ranges[stmt.var] = ValueRange.for_range_index()

    def _filter_union_codegen_facts(
        self, facts: dict[str, TpyType],
    ) -> dict[str, TpyType]:
        """Keep narrowing facts that drive codegen extraction.

        Optional narrowing is handled implicitly by std::optional in C++,
        so only UnionType variables need explicit std::get<T> extraction.
        LiteralType facts are passed through for dead branch elimination.
        Protocol facts (e.g. Iterable[T] -> NativeIterable[T] via isinstance)
        feed protocol_narrowings so downstream dispatch sees the refined type.
        Polymorphic-class facts (e.g. isinstance(opt_exc, OSError) where
        opt_exc: Optional[BaseException]) drive cast-and-cache codegen so
        subclass-typed reads in the true branch route through a dynamic_cast'd
        local rather than the base pointer.
        """
        def _peel(t: TpyType) -> TpyType:
            # Strip Own / readonly so Own[A|B] params surface as UnionType
            # for the union-fact check; mirrors _isinstance_unwrap in
            # calls.py.
            t = unwrap_readonly(t)
            if isinstance(t, OwnType):
                t = unwrap_readonly(t.wrapped)
            return t

        def _polymorphic_subclass_fact(name: str, ty: TpyType) -> bool:
            # Strict subclass only -- `is not None` narrowing keeps the source's
            # inner class and is gated out by the shared predicate. `_peel` strips
            # Own/readonly so `Own[A | B]` params surface their inner shape.
            return is_polymorphic_subclass_fact(
                _peel(self.narrowing.declared_type_for_name(name)),
                ty, self.ctx.registry)

        return {
            name: ty for name, ty in facts.items()
            if (isinstance(_peel(self.narrowing.declared_type_for_name(name)), (UnionType, AnyType))
                or isinstance(ty, LiteralType)
                or is_protocol_type(ty)
                or _polymorphic_subclass_fact(name, ty)
                # Deref-view facts (key carries the deref suffix) drive the
                # if-init cast-and-cache for owning wrappers; the wrapper var
                # itself is never retyped, so they pass through verbatim.
                or parse_deref_view_key(name) is not None)
        }

    def _analyze_raise(self, stmt: TpyRaise) -> None:
        """Analyze a raise statement (return-tier, throw-tier, or bare re-raise)."""
        # Bare raise (re-raise)
        if stmt.exception_type is None and stmt.raise_expr is None:
            if self.ctx.func.in_except_tier is None:
                raise self.ctx.error(
                    "bare 'raise' is only valid inside an 'except' block", stmt)
            if (self.ctx.func.in_except_tier is TryTier.RETURN
                    and not self.ctx.func.in_except_has_binding):
                raise self.ctx.error(
                    "bare 'raise' in return-tier except requires 'as' binding "
                    "(e.g. 'except E as e') to capture the error value",
                    stmt)
            self.init.mark_terminated()
            return

        # Expression raise (general expression, e.g. raise <expr>)
        if stmt.raise_expr is not None:
            self._analyze_raise_expr(stmt)
            return

        func = self.ctx.func.current_function
        if not isinstance(func, TpyFunction):
            raise self.ctx.error(
                f"'raise {stmt.exception_type}' can only be used inside a function", stmt)

        bare_exc = bare_name(stmt.exception_type)
        record = self.ctx.registry.find_record(bare_exc)
        if not record:
            # Not a type -- check if it's a variable of exception type
            self._analyze_raise_name_as_expr(stmt)
            return

        qualified_exc = qualify_exception_name(
            stmt.exception_type, self.ctx.registry, self.ctx.module_name)
        is_cf = is_return_exception(qualified_exc)

        if is_cf:
            # Return-tier: must be inside @error_return(E) function with matching E
            if func.error_return is None:
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}' requires "
                    f"@error_return({stmt.exception_type}) on the enclosing function",
                    stmt)
            if not error_return_matches(func.error_return, qualified_exc):
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}' does not match "
                    f"@error_return({stmt.exception_type})", stmt)
        else:
            # Throw-tier: must inherit from Exception
            if not is_exception_type(stmt.exception_type, self.ctx.registry):
                raise self.ctx.error(
                    f"'{stmt.exception_type}' is not an exception type; "
                    f"it must inherit from Exception",
                    stmt)

        # Type-check constructor arguments against __init__ params
        if stmt.args:
            init_overloads = self.ctx.registry.ctor_overloads(record)
            if len(init_overloads) > 1:
                # Overloaded ctor (builtin-stub @overload groups, e.g. the
                # OSError (errno, strerror[, filename]) forms): resolve the
                # winning overload like the expression construction path;
                # the single-init arity check below would only ever see the
                # first stub's signature.
                arg_types = [self.expr.analyze_expr(a) for a in stmt.args]
                try:
                    winner = resolve_overload(
                        init_overloads, arg_types,
                        protocol_checker=self.protocols.type_conforms_to_protocol,
                        default_int_type=self.ctx.default_int_type,
                        subclass_checker=self.ctx.registry.is_subclass_of,
                        protocol_classifier=self.protocols.classify_protocol_conformance,
                        type_ops=self.type_ops,
                    )
                except OverloadAmbiguityError as e:
                    raise self.ctx.error(str(e), stmt)
                if winner is None:
                    types_str = ", ".join(str(t) for t in arg_types)
                    raise self.ctx.error(
                        f"No matching overload for '{stmt.exception_type}"
                        f"({types_str})'", stmt)
                for i, (arg, arg_type, (pname, ptype)) in enumerate(
                        zip(stmt.args, arg_types, winner.params)):
                    arg_type = self.expr.calls._restore_readonly_arg(arg, arg_type)
                    self.expr.calls.check_own_param(arg, arg_type, pname, ptype)
                    self.expr.calls.mark_pending_arg_context(arg, arg_type, ptype)
                    stmt.args[i] = self.compat.coerce_expr(
                        arg, arg_type, ptype, f"argument '{pname}'",
                        coercion_ctx=CoercionContext.ARG)
                stmt.resolved_ctor_init = winner
            elif record.has_init:
                min_args = sum(1 for _, _, d in record.init_params if d is None)
                max_args = len(record.init_params)
                if len(stmt.args) < min_args or len(stmt.args) > max_args:
                    expected = (f"{max_args}" if min_args == max_args
                                else f"{min_args} to {max_args}")
                    raise self.ctx.error(
                        f"'raise {stmt.exception_type}()' expects "
                        f"{expected} arguments, got {len(stmt.args)}",
                        stmt)
                for i, (arg, (pname, ptype, _)) in enumerate(
                        zip(stmt.args, record.init_params)):
                    arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
                    self.expr.calls.mark_pending_arg_context(arg, arg_type, ptype)
                    stmt.args[i] = self.compat.coerce_expr(
                        arg, arg_type, ptype, f"argument '{pname}'",
                        coercion_ctx=CoercionContext.ARG)
                # Carry the resolved __init__ so codegen routes the ctor args
                # through the same shared arg-lowering loop a normal `X(args)`
                # construction uses -- otherwise a raise site re-derives only
                # the plain coercion fallback and misses the protocol /
                # covariant / optional-ptr / union / mutated-temp dispatch.
                stmt.resolved_ctor_init = record.get_method("__init__")
            elif record.fields:
                raise self.ctx.error(
                    f"'{stmt.exception_type}' has data fields but no __init__; "
                    f"add __init__ to use 'raise {stmt.exception_type}(...)'",
                    stmt)
            else:
                hint = ""
                if record.is_return_exception and not record.is_native:
                    # CPython lets a `pass` exception take a message through the
                    # inherited Exception(message); a return exception is a
                    # plain value that carries only what it declares.
                    hint = (f": a return-only exception carries only the fields "
                            f"it declares; add 'message: str' and an __init__ "
                            f"to '{stmt.exception_type}' to raise it with one")
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}()' does not accept arguments"
                    f"{hint}",
                    stmt)
        # @virtual_raise classes dispatch in their C++ __raise__ (e.g.
        # OSError's ctor-time errno -> subclass mapping); the fresh
        # construction must route through it, not the throw peephole.
        stmt.raise_via_virtual = record.virtual_raise and not is_cf
        # Propagate qualified name to AST
        stmt.exception_type = qualified_exc
        self.init.mark_terminated()

    def _analyze_raise_expr(self, stmt: TpyRaise) -> None:
        """Analyze 'raise <expr>' where expr is a general expression.

        Phase 20 Stage 3: accepts a `Box[Throwable]` (or any other type
        implementing the `Deref` protocol whose peeled type is Throwable)
        in addition to plain Exception subclasses. Codegen lowers via
        `<expr>.__raise__()` which auto-derefs through Box's vtable.
        """
        func = self.ctx.func.current_function
        if not isinstance(func, TpyFunction):
            raise self.ctx.error(
                "'raise' can only be used inside a function", stmt)
        expr_type = self.expr.analyze_expr(stmt.raise_expr)
        # Peel __deref__ until we hit a non-Deref type. Box[Throwable]
        # peels once to Throwable; an Optional[Box[T]] field already
        # narrows the Optional away before this point. Stores the depth on
        # the AST so codegen can insert the matching `.__deref__()` chain.
        peeled_source = unwrap_qualifiers(expr_type)
        depth = 0
        while True:
            next_peeled = self.expr.get_deref_target_type(peeled_source)
            if next_peeled is None:
                break
            depth += 1
            peeled_source = unwrap_qualifiers(next_peeled)
            if depth > 8:
                raise self.ctx.error(
                    f"raise expression __deref__ chain exceeds 8 levels",
                    stmt)
        if depth > 0:
            stmt.deref_depth = depth
            t = peeled_source
            if isinstance(t, NominalType) and t.is_protocol and t.qualified_name() == "tpy.Throwable":
                self.init.mark_terminated()
                return
            if isinstance(t, NominalType) and not t.is_protocol:
                if is_exception_type(t.name, self.ctx.registry):
                    self.init.mark_terminated()
                    return
            raise self.ctx.error(
                f"cannot raise expression of type '{expr_type}'; "
                f"its __deref__ target '{t}' is not Throwable",
                stmt)
        type_name = self._raise_expr_type_name(expr_type, stmt)
        if is_return_exception(
                qualify_exception_name(type_name, self.ctx.registry, self.ctx.module_name)):
            raise self.ctx.error(
                f"'raise <expr>' cannot be used with ReturnException type "
                f"'{type_name}'; use direct 'raise {type_name}' inside "
                f"an @error_return function instead",
                stmt)
        if not is_exception_type(type_name, self.ctx.registry):
            raise self.ctx.error(
                f"cannot raise expression of type '{type_name}'; "
                f"it must inherit from Exception",
                stmt)
        self.init.mark_terminated()

    def _analyze_raise_name_as_expr(self, stmt: TpyRaise) -> None:
        """Handle 'raise Name' or 'raise Name(args)' where Name is not a type.

        Converts to expression raise if Name is a variable/call of exception type.
        """
        name = stmt.exception_type
        if stmt.args or stmt.is_call_form:
            # raise func() or raise func(args) -- convert to call expression
            call_expr = TpyCall(func=TpyName(name, loc=stmt.loc), args=stmt.args, loc=stmt.loc)
            stmt.raise_expr = call_expr
            stmt.exception_type = None
            stmt.args = []
            self._analyze_raise_expr(stmt)
            return
        # raise name -- convert to variable reference
        name_expr = TpyName(name=name, loc=stmt.loc)
        stmt.raise_expr = name_expr
        stmt.exception_type = None
        self._analyze_raise_expr(stmt)

    def _raise_expr_type_name(self, expr_type: TpyType, stmt: TpyRaise) -> str:
        """Extract the type name from a raise expression's type for validation."""
        t = unwrap_qualifiers(expr_type)
        if isinstance(t, NominalType) and not t.is_protocol:
            return t.name
        raise self.ctx.error(
            f"cannot raise expression of type '{expr_type}'; "
            f"expected an exception type",
            stmt)

    def _classify_try_tier(self, stmt: TpyTry) -> TryTier:
        """Classify a try statement onto one of the TryTier dispatch forms."""
        if not stmt.handlers:
            return TryTier.FINALLY_ONLY

        has_cf = False
        has_throw = False
        has_bare = False
        for h in stmt.handlers:
            if h.exception_type is None:
                has_bare = True
                continue
            proto = self.ctx.registry.scan_by_short_name(h.exception_type)
            is_cf_catch_all = (
                proto is not None
                and f"{proto.module}.{proto.name}" == qnames.RETURN_EXCEPTION
            )
            if is_cf_catch_all or is_return_exception(
                    qualify_exception_name(
                        h.exception_type, self.ctx.registry, self.ctx.module_name)):
                has_cf = True
            else:
                has_throw = True

        if has_cf and has_throw:
            raise self.ctx.error(
                "cannot mix ReturnException and non-ReturnException exception types "
                "in the same try/except block", stmt)
        if has_cf and has_bare:
            raise self.ctx.error(
                "bare 'except:' cannot be mixed with ReturnException handlers", stmt)

        return TryTier.RETURN if has_cf else TryTier.THROW

    def _analyze_try(self, stmt: TpyTry) -> None:
        """Analyze a try/except/else/finally statement.

        Classifies the try block into tiers:
        - RETURN: ReturnException handlers -> goto-based dispatch (existing)
        - THROW: non-ReturnException handlers -> C++ try/catch
        - FINALLY_ONLY: no handlers, just finally cleanup
        """
        tier = self._classify_try_tier(stmt)
        stmt.tier = tier

        if tier is TryTier.FINALLY_ONLY:
            self._analyze_try_finally_only(stmt)
        elif tier is TryTier.RETURN:
            self._analyze_try_return(stmt)
        else:
            self._analyze_try_throw(stmt)

    @staticmethod
    def _walk_deferred_return_names(stmts: list[TpyStmt],
                                    names: dict[str, tuple]) -> None:
        """Collect the names finally-deferred returns in `stmts` borrow, each
        mapped to how the return spells it in a diagnostic and to the
        (return, tuple path) sites that defer it (deep; nested defs excluded
        -- their returns exit the inner function and never hold a borrow
        across an enclosing finally)."""
        for s in stmts:
            if isinstance(s, TpyReturn):
                if s.finally_deferred_capture:
                    inner = s.value
                    while isinstance(inner, TpyCoerce):
                        inner = inner.expr
                    if isinstance(inner, TpyName) and (
                            not s.finally_deferred_leaves
                            or () in s.finally_deferred_leaves):
                        names.setdefault(
                            inner.name, (f"return {inner.name}", []))[1] \
                            .append((s, None))
                    elif isinstance(inner, TpyTupleLiteral):
                        spelled = _deferred_tuple_spelling(inner)
                        for path, leaf in tuple_literal_leaves(inner):
                            while isinstance(leaf, TpyCoerce):
                                leaf = leaf.expr
                            if (path in s.finally_deferred_leaves
                                    and isinstance(leaf, TpyName)):
                                names.setdefault(
                                    leaf.name, (f"return {spelled}", []))[1] \
                                    .append((s, path))
            elif not isinstance(s, TpyNestedDef):
                for body in s.sub_bodies():
                    StatementAnalyzer._walk_deferred_return_names(body, names)

    def _collect_deferred_return_names(self, stmt: TpyTry) -> dict[str, tuple]:
        """Names borrowed by finally-deferred returns anywhere in the try's
        try/else/handler bodies, each with its return's spelling and sites."""
        names: dict[str, tuple] = {}
        self._walk_deferred_return_names(stmt.try_body, names)
        self._walk_deferred_return_names(stmt.else_body, names)
        for h in stmt.handlers:
            self._walk_deferred_return_names(h.body, names)
        return names

    def _check_eager_return_copyable(self, leaf: TpyName,
                                     at: 'TpyStmt | TpyExpr') -> None:
        """A returned member the finally rebinds keeps the eager capture before
        the chain; a non-copyable one has no such capture."""
        t = self.ctx.get_expr_type(leaf)
        if t is not None and self.ctx.is_type_non_copyable(
                unwrap_own(unwrap_ref_type(t))):
            reason = self.ctx.nocopy_reason(unwrap_own(unwrap_ref_type(t)))
            fn = self.ctx.func.current_function
            by_closure = (fn is not None and leaf.name
                          in collect_nested_def_nonlocal_rebinds(fn.body))
            where = ("a nested def rebinds it" if by_closure
                     else "its finally rebinds it")
            raise self.ctx.error(
                f"'{leaf.name}' ({reason}) cannot be returned from inside "
                f"a try while {where}: the pending return would need a "
                f"copy of it. Bind the new value to another name", at)

    def _decline_deferred_return(self, name: str,
                                 at: 'TpyStmt | TpyExpr') -> None:
        """Undo the finally-deferred capture of `name` in every enclosing
        pending return (a finally body is analysed after its try's returns
        are stamped): the member goes back to the eager capture and its
        last-use move is retracted, since the finally still reads it."""
        for pending in self.ctx.func.pending_return_borrows:
            entry = pending.pop(name, None)
            if entry is None:
                continue
            for ret, path in entry[1]:
                if path is None or path == ():
                    ret.finally_deferred_capture = False
                    ret.finally_deferred_leaves = ()
                    leaf = ret.value
                    while isinstance(leaf, TpyCoerce):
                        leaf = leaf.expr
                else:
                    ret.finally_deferred_leaves = tuple(
                        p for p in ret.finally_deferred_leaves if p != path)
                    if not ret.finally_deferred_leaves:
                        ret.finally_deferred_capture = False
                    leaves = dict(tuple_literal_leaves(ret.value))
                    leaf = leaves.get(path)
                    while isinstance(leaf, TpyCoerce):
                        leaf = leaf.expr
                if leaf is not None:
                    self._check_eager_return_copyable(leaf, at)
                    self.ctx.all_last_uses.discard(leaf)
                    self.ctx.finally_return_candidates.discard(leaf)

    def _analyze_finally_body(self, stmt: TpyTry,
                              try_kills: FactKills | None = None) -> None:
        """Analyze a finally body under all-paths entry facts.

        The finally runs on the normal path AND on any mid-try (or
        mid-handler) exception, so check-elision facts the try/else/handler
        bodies may have killed -- or only established -- must not be assumed
        inside it. After the body, normal-path facts are restored for the
        code following the try statement (they hold whenever control flows
        past it normally), minus whatever the finally body itself killed.

        ``try_kills`` lets callers that already scanned the try body for
        handler entry pass the result in instead of re-scanning.
        """
        if not stmt.finally_body:
            return
        entry_kills = FactKills()
        entry_kills.update(try_kills if try_kills is not None
                           else collect_fact_kills(stmt.try_body))
        if stmt.else_body:
            entry_kills.update(collect_fact_kills(stmt.else_body))
        for h in stmt.handlers:
            entry_kills.update(collect_fact_kills(h.body))
        normal_narrowed = dict(self.ctx.func.narrowed_types)
        normal_non_null = set(self.ctx.func.non_null_ptr_vars)
        normal_ranges = dict(self.ctx.func.value_ranges)
        self.init.apply_fact_kills(entry_kills)
        prev_in_finally = self.ctx.func.in_finally
        self.ctx.func.in_finally = True
        self.ctx.func.pending_return_borrows.append(
            self._collect_deferred_return_names(stmt))
        for s in stmt.finally_body:
            self.analyze_stmt(s)
        self.ctx.func.pending_return_borrows.pop()
        self.ctx.func.in_finally = prev_in_finally
        # Re-union the normal path: replay the finally body's own kills on
        # the normal-path snapshot, then let facts the finally established
        # take precedence.
        after_narrowed = self.ctx.func.narrowed_types
        after_non_null = self.ctx.func.non_null_ptr_vars
        after_ranges = self.ctx.func.value_ranges
        self.ctx.func.narrowed_types = normal_narrowed
        self.ctx.func.non_null_ptr_vars = normal_non_null
        self.ctx.func.value_ranges = normal_ranges
        self.init.apply_fact_kills(collect_fact_kills(stmt.finally_body))
        self.ctx.func.narrowed_types.update(after_narrowed)
        self.ctx.func.non_null_ptr_vars |= after_non_null
        self.ctx.func.value_ranges.update(after_ranges)

    def _analyze_try_finally_only(self, stmt: TpyTry) -> None:
        """Analyze try/finally with no except handlers."""
        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        for s in stmt.try_body:
            self.analyze_stmt(s)
        try_bindings = dict(self.ctx.func.current_scope.bindings)
        self._analyze_finally_body(stmt)
        # Hoist try-body variables so they're accessible in the finally body,
        # and finally-body first bindings so the duplicated finally emissions
        # (catch path / normal path / return sites) all assign one function-
        # scope slot -- which also keeps them visible after the try, per
        # Python scoping. Mark as hoisted so non-value types use pointer
        # indirection.
        all_bindings = dict(try_bindings)
        all_bindings.update(self.ctx.func.current_scope.bindings)
        branch_new = set(all_bindings.keys()) - scope_before
        predecl = branch_new - self.ctx.func.global_declarations
        self._frame_block_keeps(branch_new, predecl, "'try' statement")
        if predecl:
            self.ctx.record_branch_decls(stmt, {
                name: all_bindings[name]
                for name in sorted(predecl)
                if name in all_bindings
            })
            self.ctx.func.hoisted_vars |= predecl
            self.deduction.promote_hoisted_views(predecl)

    def _analyze_try_return(self, stmt: TpyTry) -> None:
        """Analyze return-tier try/except (ReturnException, goto-based)."""
        # Return tier supports single handler or ReturnException catch-all
        if len(stmt.handlers) != 1:
            if any(h.from_tuple_clause for h in stmt.handlers):
                raise self.ctx.error(
                    "the 'except (A, B):' tuple form is not supported on a "
                    "return-tier (ReturnException) try/except, which carries a "
                    "single error type; catch one type per clause", stmt)
            raise self.ctx.error(
                "return-tier (ReturnException) try/except supports only a single handler", stmt)
        handler = stmt.handlers[0]

        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        before = self.init.save()
        consumed_before = self.ctx.func.current_consumed_own_params.copy()
        bindings_before = dict(self.ctx.func.current_scope.bindings)
        ns_types_before = self._save_ns_var_types()

        # Detect except ReturnException catch-all
        proto = self.ctx.registry.scan_by_short_name(handler.exception_type)
        is_return_exception_catch_all = (
            proto is not None
            and f"{proto.module}.{proto.name}" == qnames.RETURN_EXCEPTION
        )

        if is_return_exception_catch_all and handler.binding:
            raise self.ctx.error(
                "'except ReturnException as' binding is not supported", stmt)

        if not is_return_exception_catch_all:
            bare_exc = bare_name(handler.exception_type)
            if not self.ctx.registry.find_record(bare_exc):
                raise self.ctx.error(
                    f"Unknown error type '{handler.exception_type}'", stmt)

        # Set try context so call analysis can allow error_return calls
        prev_try_error = self.ctx.func.try_except_error_type
        prev_handled = self.ctx.func.try_except_error_handled
        self.ctx.func.try_except_error_handled = False
        if is_return_exception_catch_all:
            self.ctx.func.try_except_error_type = "*"
        else:
            self.ctx.func.try_except_error_type = qualify_exception_name(
                handler.exception_type, self.ctx.registry, self.ctx.module_name)

        for s in stmt.try_body:
            self.analyze_stmt(s)

        # The last analysis of this body wins. `trial_scope` rolls back the
        # context but not AST mutations, so accumulating would let a
        # discarded trial's True stand over a real analysis that says False
        # -- a reject with no way back.
        stmt.handled_error_return = self.ctx.func.try_except_error_handled
        self.ctx.func.try_except_error_type = prev_try_error
        self.ctx.func.try_except_error_handled = prev_handled

        for s in stmt.else_body:
            self.analyze_stmt(s)
        then_state = self.init.save()
        consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
        then_terminated = self.ctx.func.init_terminated
        try_bindings = dict(self.ctx.func.current_scope.bindings)

        # Restore to pre-try state for except branch. An exception can be
        # thrown at ANY point in the try body, so facts the body may have
        # killed must not be assumed in the handler.
        self._enter_sibling_arm(bindings_before, ns_types_before, before)
        try_kills = collect_fact_kills(stmt.try_body)
        self.init.apply_fact_kills(try_kills)
        self.ctx.func.current_consumed_own_params = consumed_before.copy()

        # Register except binding -- use find_record_by_qname because
        # handler.exception_type may be module-qualified (e.g. from macros).
        prev_ns_binding = None
        ns_bound = False
        if handler.binding:
            exc_record = self.ctx.registry.find_record_by_qname(handler.exception_type)
            if exc_record:
                exc_type = NominalType(exc_record.name, _module_qname=exc_record.qualified_name())
                self.ctx.func.current_scope.bindings[handler.binding] = exc_type
                self.init.mark_assigned(handler.binding)
                if self.ctx.func.current_ns:
                    prev_ns_binding = self.ctx.func.current_ns.lookup_local(
                        handler.binding)
                    self.ctx.func.current_ns.bind_capture(
                        handler.binding, exc_type, frame_exempt=True)
                    ns_bound = True

        # Set in_except_tier for bare raise validation
        prev_except_tier = self.ctx.func.in_except_tier
        prev_has_binding = self.ctx.func.in_except_has_binding
        self.ctx.func.in_except_tier = TryTier.RETURN
        self.ctx.func.in_except_has_binding = handler.binding is not None
        for s in handler.body:
            self.analyze_stmt(s)
        self.ctx.func.in_except_tier = prev_except_tier
        self.ctx.func.in_except_has_binding = prev_has_binding

        if handler.binding and handler.binding in self.ctx.func.current_scope.bindings:
            del self.ctx.func.current_scope.bindings[handler.binding]
        self._retire_capture_binding(handler.binding, prev_ns_binding, ns_bound)
        else_state = self.init.save()
        consumed_after_else = self.ctx.func.current_consumed_own_params.copy()
        else_terminated = self.ctx.func.init_terminated

        self.init.merge_branches(then_state, else_state)
        self._merge_consumed_own(
            then_terminated, else_terminated,
            consumed_after_then, consumed_after_else)

        # Analyze finally body (runs on all paths, doesn't affect branch merging)
        self._analyze_finally_body(stmt, try_kills)
        self._merge_arm_readonly([try_bindings,
                                  dict(self.ctx.func.current_scope.bindings)])

        # Hoist all declarations for goto-based dispatch
        all_bindings = dict(try_bindings)
        all_bindings.update(self.ctx.func.current_scope.bindings)
        branch_new = set(all_bindings.keys()) - scope_before
        if handler.binding:
            branch_new.discard(handler.binding)
        predecl = branch_new - self.ctx.func.global_declarations
        self._frame_block_keeps(branch_new, predecl, "'try' statement")
        if predecl:
            self.ctx.record_branch_decls(stmt, {
                name: all_bindings[name]
                for name in sorted(predecl)
                if name in all_bindings
            })
            self.deduction.promote_hoisted_views(predecl, branch_new - predecl)
        self.ctx.block_locals_of[stmt] = branch_new - predecl

    def _analyze_try_throw(self, stmt: TpyTry) -> None:
        """Analyze throw-tier try/except (C++ try/catch)."""
        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        before = self.init.save()
        consumed_before = self.ctx.func.current_consumed_own_params.copy()
        bindings_before = dict(self.ctx.func.current_scope.bindings)
        ns_types_before = self._save_ns_var_types()

        # Validate all handlers and save resolved records for binding.
        # Qualify before lookup so dotted forms (`re.error`) bypass any local
        # class with the same bare name.
        handler_records: list[RecordInfo | None] = []
        for h in stmt.handlers:
            if h.exception_type is not None:
                qualified = qualify_exception_name(
                    h.exception_type, self.ctx.registry, self.ctx.module_name)
                record = self.ctx.registry.find_record_by_qname(qualified)
                if not record:
                    raise self.ctx.error(
                        f"Unknown exception type '{h.exception_type}'", stmt)
                if not is_exception_type(qualified, self.ctx.registry):
                    raise self.ctx.error(
                        f"'{h.exception_type}' is not an exception type; "
                        f"it must inherit from Exception", stmt)
                handler_records.append(record)
                h.exception_type = qualified
            else:
                handler_records.append(None)

        # Analyze try body
        for s in stmt.try_body:
            self.analyze_stmt(s)
        # Capture try-body bindings BEFORE else (else vars are scoped to the
        # if(__ok) block in C++ and must not leak into post-try scope)
        try_bindings = dict(self.ctx.func.current_scope.bindings)

        # Analyze else body
        for s in stmt.else_body:
            self.analyze_stmt(s)
        arm_bindings = [dict(self.ctx.func.current_scope.bindings)]
        then_state = self.init.save()
        consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
        then_terminated = self.ctx.func.init_terminated

        # Analyze each except handler as a separate branch from pre-try state.
        # An exception can be thrown at ANY point in the try body, so facts
        # the body may have killed must not be assumed in any handler.
        try_kills = collect_fact_kills(stmt.try_body)
        handler_states: list[tuple] = []
        handler_carried: list[set[str]] = []
        for i, h in enumerate(stmt.handlers):
            handler_carried.append(self._enter_sibling_arm(
                bindings_before, ns_types_before, before))
            self.init.apply_fact_kills(try_kills)
            self.ctx.func.current_consumed_own_params = consumed_before.copy()

            record = handler_records[i]
            prev_ns_binding = None
            ns_bound = False
            if h.binding and record is not None:
                exc_type = NominalType(record.name, _module_qname=record.qualified_name())
                self.ctx.func.current_scope.bindings[h.binding] = exc_type
                self.init.mark_assigned(h.binding)
                if self.ctx.func.current_ns:
                    prev_ns_binding = self.ctx.func.current_ns.lookup_local(
                        h.binding)
                    self.ctx.func.current_ns.bind_capture(
                        h.binding, exc_type, frame_exempt=True)
                    ns_bound = True

            prev_except_tier = self.ctx.func.in_except_tier
            self.ctx.func.in_except_tier = TryTier.THROW
            for s in h.body:
                self.analyze_stmt(s)
            self.ctx.func.in_except_tier = prev_except_tier

            if h.binding and h.binding in self.ctx.func.current_scope.bindings:
                del self.ctx.func.current_scope.bindings[h.binding]
            self._retire_capture_binding(h.binding, prev_ns_binding, ns_bound)
            arm_bindings.append(dict(self.ctx.func.current_scope.bindings))

            handler_states.append((
                self.init.save(),
                self.ctx.func.current_consumed_own_params.copy(),
                self.ctx.func.init_terminated,
            ))

        # Merge all branches: success path + all handler paths
        all_states = [then_state] + [s for s, _, _ in handler_states]
        all_terminated = [then_terminated] + [t for _, _, t in handler_states]
        all_consumed = [consumed_after_then] + [c for _, c, _ in handler_states]

        # Multi-branch merge: start from first, merge pairwise
        merged = all_states[0]
        for s in all_states[1:]:
            self.init.merge_branches(merged, s)
            merged = self.init.save()

        # Merge consumed Own[T] params
        if all(all_terminated):
            result_consumed: set[str] = set()
            for c in all_consumed:
                result_consumed |= c
        elif any(all_terminated):
            result_consumed = set()
            for t, c in zip(all_terminated, all_consumed):
                if not t:
                    result_consumed = result_consumed & c if result_consumed else c.copy()
        else:
            result_consumed = all_consumed[0].copy()
            for c in all_consumed[1:]:
                result_consumed &= c
        self.ctx.func.current_consumed_own_params = result_consumed

        # Analyze finally body. Bindings first introduced by the finally
        # body hoist with the try-body vars below: the duplicated finally
        # emissions (catch path / normal path / return sites) all need one
        # function-scope slot, which also keeps them visible after the try,
        # per Python scoping.
        pre_finally_names = set(self.ctx.func.current_scope.bindings.keys())
        self._analyze_finally_body(stmt, try_kills)
        finally_new = {
            name: t for name, t in self.ctx.func.current_scope.bindings.items()
            if name not in pre_finally_names and name not in scope_before}

        # Throw-tier uses C++ try/catch with proper scoping -- no goto hoisting
        # needed in general. BUT try-body variables must be hoisted when:
        # - finally body exists: finally code runs after the try/catch block
        # - else body exists: else code is emitted after the try/catch block
        # - code continues after try/except and try-body vars are in scope
        #   (all handlers terminate, so post-try code uses try-body vars)
        all_handlers_terminate = all(t for _, _, t in handler_states)
        # full hoist needs EVERY try-body var visible (even maybe-assigned),
        # unlike the definitely-assigned-only da_new below.
        needs_full_hoist = (stmt.finally_body or stmt.else_body
                            or (all_handlers_terminate and not self.ctx.func.init_terminated))
        # The finally body runs on every path, so its bindings count as an arm.
        arm_bindings.append(dict(self.ctx.func.current_scope.bindings))
        self.ctx.func.current_scope.bindings = dict(try_bindings)
        self.ctx.func.current_scope.bindings.update(finally_new)
        self._merge_arm_readonly(arm_bindings)
        # The try-body snapshot predates the handlers, whose bindings of the
        # same names joined into the one slot type.
        self._sync_promoted_var_types(set(self.ctx.func.current_scope.bindings))
        all_bindings = dict(self.ctx.func.current_scope.bindings)
        branch_new = set(all_bindings.keys()) - scope_before
        for h in stmt.handlers:
            if h.binding:
                branch_new.discard(h.binding)
        # Else hoist only the definitely-assigned-on-every-path vars (the
        # if/else `branch_new & newly_assigned` rule); otherwise they declare
        # inside the try block and the post-try read won't compile.
        da_new = branch_new & (self.ctx.func.definitely_assigned - before.definitely_assigned)
        predecl = (branch_new if needs_full_hoist else da_new) - self.ctx.func.global_declarations
        self._frame_block_keeps(branch_new, predecl, "'try' statement")
        if predecl:
            self.ctx.record_branch_decls(stmt, {
                name: all_bindings[name]
                for name in sorted(predecl)
                if name in all_bindings
            })
            self.ctx.func.hoisted_vars |= predecl
            self.deduction.promote_hoisted_views(predecl, branch_new - predecl)
        self.ctx.block_locals_of[stmt] = branch_new - predecl
        self._declare_carried_in_arms(
            [(h.body, carried - {h.binding}, bindings)
             for h, carried, bindings in zip(
                 stmt.handlers, handler_carried, arm_bindings[1:])],
            predecl)

    def _merge_consumed_own(self, then_terminated: bool, else_terminated: bool,
                            consumed_then: set[str], consumed_else: set[str]) -> None:
        """Merge consumed Own[T] params from two branches."""
        if then_terminated and else_terminated:
            self.ctx.func.current_consumed_own_params = consumed_then | consumed_else
        elif then_terminated:
            self.ctx.func.current_consumed_own_params = consumed_else
        elif else_terminated:
            self.ctx.func.current_consumed_own_params = consumed_then
        else:
            self.ctx.func.current_consumed_own_params = consumed_then & consumed_else

    def _register_foreach_iter_loans(self, stmt: TpyForEach,
                                     iterable_type: TpyType) -> None:
        """The for-each's iteration loan, and where its loop variable's
        element comes from -- one registration for BOTH for-each routes."""
        cur = self.ctx.func.current_function
        frame_loop = isinstance(cur, TpyFunction) and stmt is cur.genexpr_loop
        loan = register_iteration_loans(
            self.ctx, stmt.iterable, iterable_type,
            self.ctx.func.genexpr_source_loans if frame_loop else ())
        stmt.iter_borrow_unplaceable = loan.unplaceable
        if loan.sources:
            self.ctx.func.loop_var_iterable[stmt.var] = loan.sources
            if stmt.is_tuple_unpack and loan.elem_sources is not None:
                self.ctx.func.loop_var_elem_iterable[stmt.var] = loan.elem_sources

    def _analyze_async_for(self, stmt: TpyForEach) -> None:
        """Analyze `async for x in ait: <body>` (v1.5 M6).

        Resolves `<ait>.__aiter__()` (sync method, returns the async
        iterator) and the iterator's `__anext__()` (async method, returns
        `Awaitable[T]`); unwraps T as the loop var's element type.
        Only allowed inside `async def`.
        """
        cur = self.ctx.func.current_function
        if not (isinstance(cur, TpyFunction) and cur.is_async):
            raise self.ctx.error(
                "`async for` is only allowed inside an `async def` "
                "function body", stmt)

        iterable_type = self.expr.analyze_expr(stmt.iterable)
        iterable_inner = unwrap_own(unwrap_ref_type(iterable_type))

        record_info = self.ctx.registry.get_record_for_type(iterable_inner)
        if record_info is None:
            raise self.ctx.error(
                f"Type '{iterable_inner}' cannot be used as an async "
                f"iterable (not a record type)", stmt.iterable)

        aiter_overloads = self.ctx.registry.get_method_overloads_with_parents(
            record_info, "__aiter__")
        if not aiter_overloads:
            raise self.ctx.error(
                f"Type '{iterable_inner}' cannot be used as an async "
                f"iterable (missing __aiter__ method)", stmt.iterable)
        aiter_info = aiter_overloads[0]
        # An inherited __aiter__ reports its self-type with a generic base's
        # unbound T; bind it so aiter_type carries concrete args.
        if isinstance(iterable_inner, NominalType):
            _, aiter_info = self.type_ops.bind_inherited_coro_method(
                aiter_info, iterable_inner, record_info)
        if aiter_info.is_async:
            raise self.ctx.error(
                f"`__aiter__` on '{iterable_inner}' must be a sync "
                f"`def` (returns the async iterator); only `__anext__` "
                f"is `async def`", stmt.iterable)

        aiter_type = unwrap_own(unwrap_ref_type(aiter_info.return_type))
        if not isinstance(aiter_type, NominalType):
            raise self.ctx.error(
                f"`__aiter__` on '{iterable_inner}' returns "
                f"'{aiter_type}', which is not a record type and cannot "
                f"provide `__anext__`", stmt.iterable)
        aiter_record = self.ctx.registry.get_record_for_type(aiter_type)
        if aiter_record is None:
            raise self.ctx.error(
                f"`__aiter__` on '{iterable_inner}' returns "
                f"'{aiter_type}', which is not a record type and cannot "
                f"provide `__anext__`", stmt.iterable)
        anext_overloads = self.ctx.registry.get_method_overloads_with_parents(
            aiter_record, "__anext__")
        if not anext_overloads:
            raise self.ctx.error(
                f"Async iterator '{aiter_type}' is missing `__anext__` "
                f"method", stmt.iterable)
        anext_info = anext_overloads[0]
        # Codegen reads async_aiter_type to name the __anext__ sub-coro struct;
        # it must be __anext__'s DEFINING record (an ancestor when the iterator
        # inherits it), since the struct exists only there. Binding also gives
        # the loop variable the concrete element type instead of a base's T.
        stmt.async_aiter_type, anext_info = self.type_ops.bind_inherited_coro_method(
            anext_info, aiter_type, aiter_record)
        if not anext_info.is_async:
            raise self.ctx.error(
                f"`__anext__` on '{aiter_type}' must be `async def` "
                f"for use in `async for`", stmt.iterable)

        anext_ret = unwrap_ref_type(anext_info.return_type)
        if (isinstance(anext_ret, NominalType)
                and anext_ret.qualified_name() in (qnames.AWAITABLE, qnames.CANCELLABLE)
                and len(anext_ret.type_args) == 1):
            elem_type = anext_ret.type_args[0]
        else:
            raise self.ctx.error(
                f"`__anext__` on '{aiter_type}' must return "
                f"`Awaitable[T]` (got '{anext_ret}')", stmt.iterable)

        elem_type = self._infer_new_local_type(
            stmt.var, elem_type, None, None,
            line=(stmt.loc.line if stmt.loc else None),
        )
        self._note_loop_var_iteration(stmt.var, elem_type, None, placeable=False)
        stmt.elem_type = make_ref(elem_type)
        if contains_pending_leaf(stmt.elem_type):
            self.ctx.func.pending_elem_type_fields.append((stmt, "elem_type"))
        self._check_loop_var_rebind(stmt, elem_type)
        self._record_for_loop_var_type(stmt, elem_type)

        # The first `__anext__` await suspends before the body ever runs;
        # kill pre-save so the loop-entry restore and the exit-facts
        # intersection both see the post-suspension state.
        self.narrowing.invalidate_suspension_facts()
        runs_once = self.narrowing.for_head_provably_runs(stmt)
        before = self.init.save()
        consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
        ns_types_before = self._save_ns_var_types()
        with self._loop_body_scope(stmt) as (inner_scope, edges):
            self.init.apply_loop_entry_facts(
                before, kills=collect_fact_kills(stmt.body))
            self.ctx.func.mutated_loop_vars.discard(stmt.var)
            self.ctx.func.consumed_loop_vars.discard(stmt.var)
            # An `async for` borrows what it iterates exactly as the sync
            # for-each does, through the same two registrations: `__aiter__`
            # is user code and may return an iterator holding a pointer into
            # the iterable, which the frame then keeps across every
            # suspension, so a mutation of that storage in the loop body is
            # the same hazard here. The provenance arm files no loan on any
            # async spelling today (a call or property iterable is a located
            # reject at lowering) -- it is called so the row lifting that
            # reject does not have to remember this route.
            self._register_foreach_iter_loans(stmt, iterable_type)
            with self.scopes.loop_var(inner_scope, stmt.var, elem_type,
                                       inner_scope.depth, is_foreach=True):
                for s in stmt.body:
                    self.analyze_stmt(s)
        self._finish_loop_clauses(
            stmt, inner_scope, edges, before=before,
            consumed_before=consumed_before_loop,
            ns_types_before=ns_types_before, runs_once=runs_once,
            elem_type=elem_type)

    def _mark_with_manager_mutated(
            self, manager: 'TpyExpr', ctx_type: TpyType,
            calls: 'list[tuple[str, FunctionInfo]]') -> None:
        """A borrowed `with` manager is the receiver of each non-readonly
        dunder in `calls` (`__enter__` and/or `__exit__`, with the callee the
        with statement resolved, inherited ones included); an rvalue manager
        has no durable root. Each call is credited on its own -- either one
        may grow the manager, which its loan check and the structural mark
        must both see, answered from that callee's facts. The mutation mark
        is eager, not a Phase-2 edge on the callee: the with statement
        resolves two callees, possibly rebound from a generic base, and a
        `with self.<field>:` method is kept non-const on that eager mark
        today."""
        if _root_name_of_expr(manager) is None:
            for method, callee in calls:
                check_implicit_readonly_receiver(self.ctx, manager, callee,
                                                 method, manager)
            return
        bare = unwrap_readonly(ctx_type)
        for method, callee in calls:
            credit_implicit_receiver_call(self.ctx, manager, bare, callee,
                                          method, manager, eager=True)

    def _analyze_with(self, stmt: TpyWith) -> None:
        """Analyze a with statement (context managers).

        Checks that each context expression has __enter__ and __exit__ methods.
        Types the as-binding from __enter__ return type. The as-variable is
        visible after the with block (matching CPython semantics).

        `async with` (stmt.is_async): looks for __aenter__ / __aexit__,
        validates both are async methods, and unwraps the
        `Awaitable[T]` wrapper that async-method registration applies to
        their return types. Only allowed inside `async def`.
        """
        if stmt.is_async:
            cur = self.ctx.func.current_function
            if not (isinstance(cur, TpyFunction) and cur.is_async):
                raise self.ctx.error(
                    "`async with` is only allowed inside an `async def` "
                    "function body", stmt)

        enter_method = "__aenter__" if stmt.is_async else "__enter__"
        exit_method = "__aexit__" if stmt.is_async else "__exit__"
        kind_label = "async context manager" if stmt.is_async else "context manager"

        for item in stmt.items:
            ctx_type = unwrap_own(unwrap_ref_type(self.expr.analyze_expr(item.context_expr)))

            # A reference-type manager that names EXISTING storage must be
            # borrowed by the with-region, not copied into the ctx slot --
            # otherwise __enter__/__exit__ mutate a throwaway copy (and
            # @nocopy managers can't be copied at all). Whether it does is the
            # expression's value CATEGORY, which the callee's return
            # convention decides: a borrow-returning method or free call
            # hands back a reference into live storage exactly as the
            # @property spelling of the same accessor does, while an
            # `Own[...]` / by-value call mints a temporary the block owns.
            # A value-type manager stays bound by value: a `with` block is a
            # value boundary, so it's copied like any value type crossing one
            # (see the ValueType-immutability TODO).
            item.manager_borrowed = (
                not is_rvalue_source(self.ctx, item.context_expr)
                and not ctx_type.is_value_type())

            # Look up __[a]enter__ / __[a]exit__ on the context manager type.
            record_info = self.ctx.registry.get_record_for_type(ctx_type)
            err_node = item.context_expr
            if record_info is None:
                raise self.ctx.error(
                    f"Type '{ctx_type}' cannot be used as a {kind_label}"
                    f" (not a record type)", err_node)

            # Walk the MRO: a context manager used via a subclass inherits
            # __[a]enter__ / __[a]exit__ from a base (matching CPython).
            enter_overloads = self.ctx.registry.get_method_overloads_with_parents(
                record_info, enter_method)
            if not enter_overloads:
                raise self.ctx.error(
                    f"Type '{ctx_type}' cannot be used as a {kind_label}"
                    f" (missing {enter_method} method)", err_node)

            exit_overloads = self.ctx.registry.get_method_overloads_with_parents(
                record_info, exit_method)
            if not exit_overloads:
                raise self.ctx.error(
                    f"Type '{ctx_type}' cannot be used as a {kind_label}"
                    f" (missing {exit_method} method)", err_node)

            # Validate async-ness: in `async with`, both methods must be
            # `async def`. In sync `with`, neither should be.
            enter_info = enter_overloads[0]
            exit_info = exit_overloads[0]

            # An inherited __[a]enter__/__[a]exit__ from a generic base reports
            # the base's unbound T; bind it so the `as` target / exit-suppress
            # type is concrete and codegen can name the async sub-coro struct.
            enter_owner = exit_owner = None
            if isinstance(ctx_type, NominalType):
                enter_owner, enter_info = self.type_ops.bind_inherited_coro_method(
                    enter_info, ctx_type, record_info)
                exit_owner, exit_info = self.type_ops.bind_inherited_coro_method(
                    exit_info, ctx_type, record_info)

            # A borrowed manager whose __[a]enter__ / __[a]exit__ mutates
            # self must be bound non-const, so mark its durable root mutated
            # (mirrors the non-readonly method-call / await-operand receiver
            # marking). Without this the manager's param/local can be
            # inferred const and the borrow binds `const T&`, failing on the
            # mutating enter/exit call.
            if item.manager_borrowed and not (
                    enter_info.is_readonly and exit_info.is_readonly):
                self._mark_with_manager_mutated(
                    item.context_expr, ctx_type,
                    [(m, fi) for m, fi in ((enter_method, enter_info),
                                           (exit_method, exit_info))
                     if not fi.is_readonly])

            # An @error_return enter/exit has no admission site: the `with`
            # header calls it implicitly, so it never reaches the call
            # chokepoint that decides whether an enclosing try handles the
            # error, and the header has no unwrap of its own. Reject here
            # rather than binding the target to the unhandled result. EVERY
            # variant is checked, not the one this function otherwise reads:
            # the header never resolves the dunder against arguments (there
            # are none beside `self`), so an @error_return variant in any
            # position is a variant the header could call.
            for dunder, infos in ((enter_method, enter_overloads),
                                  (exit_method, exit_overloads)):
                if any(i.error_return_type is not None for i in infos):
                    raise self.ctx.error(
                        f"`{dunder}` on '{ctx_type}' returns an error with "
                        f"@error_return, which a `with` statement has no way "
                        f"to handle. Move the failing work into a method the "
                        f"body calls inside a try/except.", err_node)

            if stmt.is_async and enter_owner is not None:
                item.aenter_owner_type = enter_owner
                item.aexit_owner_type = exit_owner

            if stmt.is_async:
                if not enter_info.is_async:
                    raise self.ctx.error(
                        f"`{enter_method}` on '{ctx_type}' must be `async def` "
                        f"for use in `async with`", err_node)
                if not exit_info.is_async:
                    raise self.ctx.error(
                        f"`{exit_method}` on '{ctx_type}' must be `async def` "
                        f"for use in `async with`", err_node)
                # v1.5 M5 limitation: cleanup-only `__aexit__` (all-None
                # args). Inspecting `exc_val: Optional[BaseException]`
                # requires polymorphic exception storage across the
                # suspension (E9 / Phase 20).
                if len(exit_info.params) >= 2 and isinstance(
                        exit_info.params[1].type, OptionalType):
                    raise self.ctx.error(
                        f"`{exit_method}` with `exc_val: Optional["
                        f"BaseException]` is not yet supported in "
                        f"`async with` -- v1.5 M5 only handles "
                        f"cleanup-only managers. Annotate exc_val as "
                        f"`None` to discard the exception. Full "
                        f"polymorphic-exception inspection is tracked "
                        f"as E9 / Phase 20.", err_node)

            # v1.5 M1: tag whether __exit__ / __aexit__ may suppress
            # exceptions, and whether exc_val is typed Optional[BaseException]
            # (vs None). Registration rejects multi-overload `__exit__`;
            # async methods don't participate in @overload, so each has
            # exactly one entry.
            # For async methods, the FunctionInfo return type is wrapped
            # in `Cancellable[T]` for async-def results (or `Awaitable[T]`
            # for user types with bare `__poll__`); unwrap one level to
            # see the user's declared T.
            exit_ret = exit_info.return_type
            if (stmt.is_async and isinstance(exit_ret, NominalType)
                    and exit_ret.qualified_name() in (qnames.AWAITABLE, qnames.CANCELLABLE)
                    and len(exit_ret.type_args) == 1):
                exit_ret = exit_ret.type_args[0]
            item.exit_can_suppress = exit_ret == BOOL
            item.exit_takes_exc_val = (
                len(exit_info.params) >= 2
                and isinstance(exit_info.params[1].type, OptionalType)
            )

            # Get return type of __[a]enter__() -- use the first overload.
            # For async, unwrap Awaitable[T] / Cancellable[T] so the
            # as-binding sees T.
            enter_type = unwrap_ref_type(enter_info.return_type)
            if (stmt.is_async and isinstance(enter_type, NominalType)
                    and enter_type.qualified_name() in (qnames.AWAITABLE, qnames.CANCELLABLE)
                    and len(enter_type.type_args) == 1):
                enter_type = enter_type.type_args[0]
            item.enter_type = enter_type
            if stmt.is_async:
                item.aenter_result_is_borrow = async_result_aliases(
                    enter_info.async_inner_return, enter_type)
                item.aenter_result_is_const = (
                    item.aenter_result_is_borrow
                    and isinstance(
                        unwrap_ref_type(enter_info.async_inner_return),
                        ReadonlyType))

            # Does `__enter__` lend the manager's own storage, or something that
            # merely passes through it? Only the first makes the manager's
            # lifetime the target's problem.
            item.manager_owns_enter_result = enter_info.returns_self_borrow

            # Register the as-variable if present
            if item.target is not None:
                resolved = self._infer_new_local_type(
                    item.target, enter_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                self.ctx.func.with_target_names.add(item.target)
                item.enter_type = resolved
                self.ctx.func.current_scope.define(item.target, resolved)
                self.ctx.func.nonstmt_bound_names.add(item.target)
                self.init.mark_assigned(item.target)
                if self.ctx.func.current_ns:
                    # NOT frame-exempt: a with target is an ordinary function
                    # local in Python and outlives its statement, so its frame
                    # residency follows the same unconditional rule as any other
                    # local. Exempting it made residency depend on whether the
                    # `with` body itself suspends, which a read after the
                    # statement does not.
                    self.ctx.func.current_ns.bind_capture(
                        item.target, resolved, frame_exempt=False)
                if not stmt.is_async and not resolved.is_value_type():
                    # A plain non-value `__enter__` LENDS -- returning a fresh
                    # value requires `Own[T]` (which reads as a value type here),
                    # so the target borrows. Recording it lets a branch pre-decl
                    # hoist the name in pointer form; owning `std::optional<T>`
                    # storage would copy the manager and sever the alias, so a
                    # mutation through the target would never reach the object
                    # `__exit__` runs against.
                    #
                    # Sync only: an `async with` target's const-ness is
                    # `item.aenter_result_is_const` above, computed from the
                    # coroutine's inner return -- `enter_info.return_type` is
                    # still `Awaitable[T]` / `Cancellable[T]` here, which is never
                    # ReadonlyType, so recording it would file a wrong fact in a
                    # table shared with every other borrow consumer.
                    #
                    # A `@readonly __enter__` returns `const T&` without the
                    # declared type being wrapped, so ask the method too -- the
                    # same pair `record_stmt_borrow_binding` uses.
                    const = isinstance(
                        unwrap_ref_type(enter_info.return_type), ReadonlyType)
                    if not const:
                        const = bool(enter_info.is_readonly
                                     and call_returns_cpp_ref(
                                         self.ctx, enter_info))
                    record_borrow_binding(self.ctx, item.target, const=const)
                    self.ctx.func.nonstmt_borrow_bindings.add(item.target)

            if stmt.is_async:
                # This item's `__aenter__` awaits before the next item's
                # context expression (or the body) runs.
                self.narrowing.invalidate_suspension_facts()

        # Analyze the body -- track new variable declarations so codegen
        # can pre-declare them outside the guard {} scope (C++ scoping).
        scope_before = set(self.ctx.func.current_scope.bindings.keys())

        for s in stmt.body:
            self.analyze_stmt(s)
        if stmt.is_async:
            # `__aexit__` awaits before any code after the block runs.
            self.narrowing.invalidate_suspension_facts()

        # All variables first declared inside the body need pre-declaration
        # since codegen wraps the body in try {} for the with's cleanup pattern.
        branch_new = set(self.ctx.func.current_scope.bindings.keys()) - scope_before
        predecl = branch_new - self.ctx.func.global_declarations
        predecl -= self._frame_block_keeps(
            branch_new, predecl, "'with' block", keepable=branch_new)
        if predecl:
            self.ctx.record_branch_decls(stmt, {
                name: self.ctx.func.current_scope.lookup(name)
                for name in sorted(predecl)
                if name in self.ctx.func.current_scope.bindings
            })
            self.deduction.promote_hoisted_views(predecl)

    # --- Nested def / nonlocal ---

    def _analyze_nonlocal(self, stmt: TpyNonlocal) -> None:
        """Analyze a nonlocal declaration."""
        if not self.ctx.func.in_nested_def:
            raise self.ctx.error(
                "'nonlocal' is only valid inside a nested function", stmt)
        for name in stmt.names:
            if name not in self.ctx.func.outer_scope_locals:
                raise self.ctx.error(
                    f"No binding for nonlocal '{name}' found in enclosing scope",
                    stmt)
            self.ctx.func.current_nonlocal_names.add(name)

    def _prescan_and_analyze_body(
        self,
        func: TpyFunction,
        params: list[tuple[str, TpyType]],
        scope: 'Scope',
        ns: 'Namespace | None',
    ) -> ScanResult:
        """Shared function body analysis: bind params, prescan, analyze statements.

        Used by both _analyze_function (analyzer.py) and _analyze_nested_def.
        Callers handle scope creation, type resolution, and post-processing.
        """
        # Bind params to scope with Ref for non-value types.
        # analyze_expr strips Ref from return values, so downstream sema
        # sees bare types.  Ref on scope types enables provenance-aware
        # code (assignment warnings, local scope propagation).
        param_names: set[str] = set()
        for pname, ptype in params:
            param_names.add(pname)
            scope_type = make_ref(ptype)
            scope.define(pname, scope_type)
            self.ctx.func.var_scope_depth[pname] = scope.depth
            self.ctx.func.definitely_assigned.add(pname)
            if ns:
                ns.bind_variable(pname, scope_type)

        # Param tracking for mutation analysis
        self.ctx.func.current_param_names = param_names
        self.ctx.func.current_param_name_to_idx = {p: i for i, (p, _) in enumerate(params)}
        # For non-static methods, include "self" in the param index map with
        # sentinel -1 so that _record_mutation_call_edges can track self
        # flowing through free function calls (e.g., helper(self)).
        # Phase 2 (_resolve_single) converts caller_idx == -1 to self_mutated.
        if func.is_method and not func.is_staticmethod:
            self.ctx.func.current_param_name_to_idx["self"] = -1

        # Prescan for reassigned variables + last-use liveness
        scan = scan_reassigned_vars(func.body, pre_declared=param_names)
        # Python binds a nested def's name for the whole enclosing scope, so
        # the set has to exist before the walk reaches any `def`. The update
        # (not an assign) keeps the outer scope's still-unbound names a
        # nested def was seeded with.
        self.ctx.func.nested_def_pending.update(scan.nested_def_bind_loc)
        self.ctx.func.own_ns = ns
        self._warn_scalar_type_shadows(func, param_names, scan)
        self.ctx.all_last_uses |= analyze_last_uses(
            func.body, liveness_alias_sources(scan))
        self.ctx.finally_return_candidates |= collect_finally_return_candidates(func.body)
        self.ctx.finally_rebound_returns |= collect_finally_rebound_returns(func.body)
        self.ctx.func.current_reassigned_vars = scan.reassigned.copy()
        self.ctx.func.body_bound_names = frozenset(
            scan.bound_names() | scan.reassigned)
        self.ctx.func.nested_nonlocal_rebinds = (
            collect_nested_def_nonlocal_rebinds(func.body, include_del=True)
            | self.ctx.func.enclosing_nonlocal_rebinds)
        self.ctx.func.deleted_names = collect_deleted_names(func.body)
        self.ctx.func.current_fresh_ctor_locals = set()
        self.ctx.func.tuple_unpack_view_targets = set()
        self.ctx.func.current_lvalue_reassigned = scan.lvalue_reassigned.copy()
        self.ctx.func.current_aug_assigned_vars = scan.aug_assigned.copy()
        self.ctx.func.current_alias_sources = dict(scan.alias_sources)
        self.ctx.func.current_chain_alias_sources = dict(scan.chain_alias_sources)

        # Analyze body
        for stmt in func.body:
            self.analyze_stmt(stmt)

        # End-of-body return enforcement runs after analysis: it consults
        # facts the body sema just produced (match exhaustiveness drives
        # stmts_terminate), and a materialized implicit `return None` must
        # be analyzed in the end-of-body flow state. Appending after the
        # analyze_last_uses prescan above is safe only because a bare None
        # literal carries no liveness facts.
        implicit_ret = self._materialize_implicit_return(func)
        if implicit_ret is not None:
            self.analyze_stmt(implicit_ret)

        self._check_closure_del_of_deferred_returns(func)

        # Every binding and loan is stamped now; the storage each rvalue
        # rebind writes is decided over the whole body at once.
        decide_rebind_storage(
            self.ctx, func.body,
            always_foreign=(self.ctx.func.current_nonlocal_names
                            | self.ctx.func.global_declarations),
            initial_foreign=param_names)

        return scan

    def _warn_scalar_type_shadows(
        self, func: TpyFunction | None, param_names: set[str], scan: ScanResult,
    ) -> None:
        """A binding named after a tpy scalar type (`def f(int32: int32)`)
        hides the type for the rest of the body, so a later `int32(x)` calls
        the variable. Valid Python, hence a warning. `func` is None for the
        module body."""
        scope = "function" if func is not None else "module"
        body_names = param_names | set(scan.first_bind_loc)
        found: list[tuple[int, str, str, str, SourceLocation | None]] = []
        for name in body_names | set(scan.scoped_bind_loc):
            # The import table rather than the namespace: an annotated
            # module-level global has already rebound the name by now.
            source = self.ctx.imported_names.get(name)
            if (source is None or source[0] != "tpy"
                    or source[1] not in qnames.SCALAR_TYPE_NAMES):
                continue
            if name in body_names:
                extent = f"for the rest of this {scope}"
                loc = scan.first_bind_loc.get(name)
                if loc is None and func is not None:
                    loc = func.loc
            else:
                extent = "within its scope"
                loc = scan.scoped_bind_loc[name]
            found.append((loc.line if loc is not None else 0, name, source[1], extent, loc))
        for _, name, type_name, extent, loc in sorted(found, key=lambda f: (f[0], f[1])):
            self.ctx.warning_from_loc(
                f"'{name}' shadows the tpy type '{type_name}' {extent}; "
                f"consider renaming", loc)

    def _check_closure_del_of_deferred_returns(self, func: TpyFunction) -> None:
        """Reject `nonlocal x; del x` in a nested def when the enclosing
        function has a finally-deferred `return x`: the pending return holds
        a borrow of x's storage across the finally chain, and a closure
        invoked from a finally would free it before the materialization.
        Runs post-body (nested defs can be defined before the try, so their
        dels are analyzed before any stamp exists). Over-broad by design --
        the closure need not provably run inside the finally -- but the
        combination is exotic and the diagnostic names the conflict.
        """
        deferred: dict[str, tuple] = {}
        self._walk_deferred_return_names(func.body, deferred)
        if not deferred:
            return

        def check_defs(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, TpyNestedDef):
                    nonlocals: set[str] = set()
                    for inner_s in s.func.body:
                        if isinstance(inner_s, TpyNonlocal):
                            nonlocals.update(inner_s.names)

                    def check_dels(stmts2: list[TpyStmt]) -> None:
                        for d in stmts2:
                            if isinstance(d, TpyDelVar):
                                for name in d.names:
                                    if name in nonlocals and name in deferred:
                                        raise self.ctx.error(
                                            f"cannot delete nonlocal "
                                            f"'{name}' here: an enclosing "
                                            f"'{deferred[name][0]}' under a "
                                            f"finally still borrows it (the "
                                            f"value is materialized after "
                                            f"the finally chain runs)", d)
                            for body in d.sub_bodies():
                                check_dels(body)

                    check_dels(s.func.body)
                for body in s.sub_bodies():
                    check_defs(body)

        check_defs(func.body)

    def _materialize_implicit_return(self, func: TpyFunction) -> TpyReturn | None:
        """The C++ body of a non-void function must return on every path
        (falling off the end is UB), so a reachable end of body either
        materializes Python's implicit `return None` as a real statement
        (return type can hold None -- CPython/mypy semantics) or is a
        compile error (mypy's "missing return statement"). Generators are
        exempt: fall-through is StopIteration. Must run after body
        analysis (match exhaustiveness feeds stmts_terminate); the caller
        analyzes the returned statement.
        """
        if func.is_generator or func.is_stub:
            return None
        rt = unwrap_own(unwrap_ref_type(unwrap_readonly(func.return_type)))
        if rt is None or is_void_like_type(rt):
            return None
        if stmts_terminate(func.body):
            return None
        if isinstance(rt, OptionalType) or (
                isinstance(rt, UnionType) and rt.has_none_member()):
            ret = TpyReturn(value=TpyNoneLiteral(loc=func.loc), loc=func.loc)
            func.body.append(ret)
            return ret
        raise self.ctx.error(
            f"'{func.name}' can reach the end of the function without "
            f"returning a value; declared return type is '{rt}' "
            f"(return or raise on every path)", func)

    def _analyze_nested_def(self, stmt: TpyNestedDef) -> None:
        """Analyze a nested function definition."""
        func = stmt.func

        if self.ctx.func.in_nested_def:
            raise self.ctx.error(
                "Nested functions cannot contain further nested functions",
                stmt)

        # Collect outer locals available for capture
        outer_locals = self.ctx.func.definitely_assigned.copy()
        # Sibling nested defs the enclosing scope has not bound yet: this
        # body shares the enclosing scope's binding for their names, so a
        # read of one must not resolve outward either. The def's OWN name is
        # excluded -- a self-reference is the recursion diagnostic instead.
        outer_pending = {n: loc for n, loc
                         in self.ctx.func.nested_def_pending.items()
                         if n != func.name and n not in outer_locals}

        # Pre-scan nonlocal declarations (at any nesting depth) to bind
        # them in the inner scope before body analysis begins.
        nonlocal_names: set[str] = set()
        self._collect_nonlocal_names(func.body, nonlocal_names)

        # The receiver has no rebindable storage behind `this` -- a
        # `nonlocal self` rebind would silently keep using the original
        # object where CPython switches to the new one (the self flavor
        # of the nonlocal-rebind slot-model gap tracked in BUGS.md).
        if "self" in nonlocal_names and self.ctx.receiver_self_in_scope():
            raise self.ctx.error(
                f"'self' cannot be declared nonlocal in nested function "
                f"'{func.name}': the method receiver cannot be rebound. "
                f"Bind the new object to a different name instead",
                stmt)

        # Resolve param and return types
        resolved_params_bare = [(p, self.type_ops.resolve_type(t)) for p, t in func.params]
        func.params = [(p, make_ref(t)) for p, t in resolved_params_bare]
        params = resolved_params_bare
        return_type = self.type_ops.resolve_type(func.return_type)
        func.return_type = make_ref(return_type)

        # A nested def emits as a lambda taking the full param list, so a
        # declared default is unreachable and an omitting call is rejected
        # with an arity error instead -- say so where the default is written.
        for (pname, _), default in zip(params, func.defaults):
            if default is not None:
                self.ctx.warning(
                    f"default value for parameter '{pname}' of nested function "
                    f"'{func.name}' is ignored -- a nested function takes every "
                    f"argument at every call; move it to module level to keep "
                    f"the default", stmt)

        self_is_receiver = self.ctx.receiver_self_in_scope()
        enclosing_nonlocal = set(self.ctx.func.nested_nonlocal_rebinds)
        enclosing_declared = (set(self.ctx.func.authoritative_types)
                              | self.ctx.func.current_param_names)

        # Analyze body in isolated scope
        with self.scopes.nested_def_scope(func) as inner_scope:
            self.ctx.func.enclosing_nonlocal_rebinds = enclosing_nonlocal
            self.ctx.func.enclosing_declared_names = enclosing_declared
            self.ctx.func.outer_scope_locals = outer_locals
            self.ctx.func.outer_self_is_receiver = self_is_receiver
            self.ctx.func.nested_def_pending = outer_pending

            # Add nonlocal names to inner scope with types from outer
            for name in nonlocal_names:
                outer_type = inner_scope.parent.lookup(name) if inner_scope.parent else None
                if outer_type is not None:
                    inner_scope.define(name, outer_type)
                    self.ctx.func.definitely_assigned.add(name)
                    if self.ctx.func.current_ns:
                        self.ctx.func.current_ns.bind_variable(name, outer_type)

            self._prescan_and_analyze_body(func, params, inner_scope, self.ctx.func.current_ns)

            # The nested def has its own tracking state, which the scope exit
            # discards -- the deductions it accumulated have to be resolved
            # and the checks it deferred have to run before that, or they
            # never run at all. Same order as a top-level body: the drain
            # reads the storage the resolution settles.
            self.deduction.resolve_all()
            self.compat.drain_deferred_escape_checks()
            # Only the scope-escape hoists: `hoisted_vars` also holds the
            # branch pre-declarations, which the closure lowering already
            # places and which need no slot.
            if self.ctx.func.escape_hoisted_vars:
                self.ctx.nested_def_hoisted_vars[func] = set(
                    self.ctx.func.escape_hoisted_vars)

            # Use the authoritative nonlocal set from body analysis
            # (covers nonlocal declarations at any nesting depth)
            nonlocal_names = self.ctx.func.current_nonlocal_names.copy()
            # Harvest closure mutation facts before the state restore discards
            # them: attempted mutation marks (filtered to outer names below)
            # and self-receiver call edges (their param_map indexes the nested
            # def's own params, so only the index-free self fact is portable).
            nested_marks = list(self.ctx.func.nested_mutation_marks)
            nested_self_edges = [
                e.callee_fi for e in self.ctx.func.current_call_edges
                if e.receiver_idx == -1
            ]
            inner_capture_sites = dict(self.ctx.func.capture_sites)
        stmt.nonlocal_names = nonlocal_names
        # After the scope restore: any later call in the enclosing function
        # may invoke this closure, killing facts for its nonlocal targets.
        self.ctx.func.closure_written_names |= nonlocal_names

        # Compute captures: free variables that come from outer scope
        captured = sorted(
            (_nested_def_free_names(func) - nonlocal_names) & outer_locals
        )
        # Nonlocal names are also captures (mutable references)
        for name in sorted(nonlocal_names):
            if name not in captured:
                captured.append(name)
        stmt.captured_names = captured
        for cap in captured:
            self.ctx.func.closure_captured_names.setdefault(
                cap, set()).add(func.name)
        # A lambda inside the def that reads an enclosing name reads it
        # through the def, at the type it has here.
        own_locals = (({p for p, _ in params}
                       | _collect_body_local_defs(func.body)) - nonlocal_names)
        reader = (stmt.loc.line if stmt.loc else None,
                  f"nested function '{func.name}'")
        for cap in [*captured, *(n for n in inner_capture_sites
                                 if n in outer_locals and n not in own_locals)]:
            self.ctx.func.capture_sites.setdefault(cap, reader)

        # Replay the closure's mutation facts into the enclosing state:
        # defining the closure conservatively counts as performing its
        # mutations (the def-site stance closure_written_names already
        # takes). Only names reaching outer storage are replayed -- a capture,
        # a nonlocal, or the receiver, as the mark recorded it; the nested
        # def's own params/locals are filtered out.
        replayable = set(captured) | nonlocal_names
        for mark in nested_marks:
            if not mark.receiver and mark.name not in replayable:
                continue
            if mark.structural:
                self.ctx.mark_param_structurally_mutated(mark.name)
            else:
                self.ctx.mark_param_mutated(mark.name,
                                            through_field=mark.through_field,
                                            via_element=mark.via_element)
        for callee_fi in nested_self_edges:
            self.ctx.func.current_call_edges.append(
                MutationCallEdge(callee_fi=callee_fi, param_map={},
                                 receiver_idx=-1))

        # Create FunctionInfo and register as local function
        param_infos = [ParamInfo(name=pname, type=ptype) for pname, ptype in params]
        fi = FunctionInfo(
            name=func.name,
            params=param_infos,
            return_type=return_type,
            send_override=func.send_override,
            sync_override=func.sync_override,
        )
        # Send/Sync frame fact: capture list with declared types. Nonlocal
        # names are by-ref captures (non-Send); plain captures copy by value
        # when the def escapes to Callable.
        def _capture_type(name: str) -> 'TpyType | None':
            ns = self.ctx.func.current_ns
            b = ns.lookup(name) if ns else None
            return b.type if (b is not None
                              and b.kind == BindingKind.VARIABLE) else None
        # A captured receiver is a by-ref slot regardless of escape mode:
        # codegen captures `this` (an alias into the origin thread's
        # object), never a copy -- so it must classify non-Send.
        fi.frame_captures = [
            (name, _capture_type(name),
             name in nonlocal_names or (name == "self" and self_is_receiver))
            for name in captured
        ]

        # Bind as FUNCTION in the local namespace
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.bind_function(fi)
        self.ctx.func.definitely_assigned.add(func.name)
        # The pending entry is NOT dropped here: it is the scope-ownership
        # fact (this scope binds the name, so no read of it may resolve
        # outward), while whether the binding reaches a given read is the
        # definite-assignment fact just written. A `def` inside an
        # `if`/`for`/`try` body leaves this namespace entry standing but
        # drops out of `definitely_assigned` at the branch merge, which is
        # how the guard tells the two paths apart.
        # Where the `def` is decides how long the name it binds can be read:
        # inside a block its callable is declared in that block, so the
        # binding retires with it; at the scope's own level it lasts, and
        # supersedes a block binding of the same name made earlier.
        self.ctx.func.nested_def_block_dead.pop(func.name, None)
        block = self._enclosing_block_phrase()
        if block is None:
            self.ctx.func.nested_def_block_defs.pop(func.name, None)
        else:
            self.ctx.func.nested_def_block_defs[func.name] = (
                self._stmt_stack[-2], block)

        # Track for escape analysis
        self.ctx.func.nested_def_names.add(func.name)
        self.ctx.func.nested_def_nodes[func.name] = stmt

    def _collect_nonlocal_names(self, stmts: list, names: set[str]) -> None:
        """Recursively collect nonlocal declarations from all nesting depths."""
        for s in stmts:
            if isinstance(s, TpyNonlocal):
                names.update(s.names)
            elif not isinstance(s, TpyNestedDef):
                for body in s.sub_bodies():
                    self._collect_nonlocal_names(body, names)

    def _resolve_literal_type(self, t: TpyType) -> TpyType:
        """Resolve IntLiteralType/FloatLiteralType to concrete types."""
        if isinstance(t, IntLiteralType):
            return self.ctx.default_int_for_literal(t)
        if isinstance(t, FloatLiteralType):
            return FLOAT
        return t

    def _record_for_loop_var_type(self, stmt: TpyForEach, elem_type: TpyType) -> None:
        """Record the resolved loop var type for `# tpyc: type()` validation
        and IDE display. The actual `loop_var` binding keeps `elem_type` as-is
        so the body's overload resolution retains IntLiteralType-to-BigInt
        flexibility (heapq pattern: `for v in [literals]: heappush(h: list[int], v)`).
        Tuple-unpack for-loops have a synthetic `stmt.var` -- the user-facing
        names are recorded by the body's TpyTupleUnpack handler instead.

        Limitation: if the body retro-widens the loop var to BigInt (heapq
        pattern), this recording isn't updated -- local_deduction's retro-widen
        path keys on var_decl_by_name, which only contains TpyVarDecl-backed
        names. `# tpyc: type()` on such a loop var would show the literal-
        defaulted int32, not the widened BigInt.
        """
        if not stmt.loc or stmt.is_tuple_unpack:
            return
        self.ctx.declared_var_types[(stmt.loc.line, stmt.var)] = (
            resolve_int_literals(elem_type, self.ctx.default_int_for_literal))

    def _captured_by_live_closure(self, stmt: TpyVarDecl, name: str) -> bool:
        """Whether a nested def that captured `name` is still live after this
        declaration -- read off the liveness the last-use pass stamped on it,
        so a closure only called BEFORE the bind does not pin the name."""
        closures = self.ctx.func.closure_captured_names.get(name)
        if not closures:
            return False
        live_after = stmt.live_names_after
        return live_after is None or not closures.isdisjoint(live_after)

    def _is_body_first_local(self, name: str) -> bool:
        """Whether `name` is pending as a local a loop body (not a `for`
        head) bound first."""
        pending = self.ctx.func.pending_loop_vars.get(name)
        return pending is not None and pending.is_body_first

    def _pending_local_is_declared(self, name: str) -> bool:
        """Whether a pending local already has its one declaration recorded
        at its anchor (an earlier read or binding promoted it)."""
        pending = self.ctx.func.pending_loop_vars.get(name)
        if pending is None or pending.head_stmt is not None:
            return False
        anchor = self.expr._pending_decl_anchor(pending.first_stack)
        return name in self.ctx.if_branch_decls.get(anchor, {})

    def _check_loop_var_rebind(self, stmt: TpyForEach, elem_type: TpyType) -> None:
        """A for-loop over an existing assigned local REBINDS it in CPython
        (the var holds the last element after the loop). Value types route
        through the hoisted-loop-var emission so codegen assigns the
        existing C++ local instead of declaring a fresh loop-scope shadow.
        Reference types are rejected: the hoisted assignment cannot express
        the per-iteration aliasing CPython gives (and writing through a
        reference param would mutate the caller's object) -- that needs the
        borrow-tracked pointer binding design.
        """
        if (stmt.is_tuple_unpack
                or stmt.var in self.ctx.func.global_declarations):
            return
        # Only a read after the loop can see what a zero-trip head leaves in
        # the name; with none, the head binds a loop-scoped variable of its
        # own -- unless a read or binding between the loops already declared
        # the local, which the scope lookup below then finds and rebinds.
        body_first_origin = self._is_body_first_local(stmt.var)
        body_first = body_first_origin and stmt.var_live_after is not False
        if body_first:
            # The question is which STORAGE the head binds, not whether the
            # name holds a value yet: a loop-body-first local is the same
            # Python local, so this head is one more binding of it, promoted
            # the way a plain assignment after the loop is -- otherwise the
            # head declares a fresh local that a zero-trip loop leaves
            # uninitialized where CPython keeps the body's value.
            if self.ctx.func.current_scope.lookup(stmt.var) is None:
                self.expr._promote_pending_loop_var(stmt.var, stmt)
        elif stmt.var not in self.ctx.func.definitely_assigned:
            # A head-first name is bound only once an earlier read or binding
            # has placed its one declaration.
            if not self._pending_local_is_declared(stmt.var):
                return
            self.expr._promote_pending_loop_var(stmt.var, stmt)
        existing = self.ctx.func.current_scope.lookup(stmt.var)
        if existing is None:
            return
        exist_bare = self._resolve_literal_type(
            unwrap_readonly(unwrap_ref_type(existing)))
        # A tuple answers is_value_type() for its own shape. A body-first
        # local's reference element is rejected like a bare reference
        # (whichever read promoted it): its one declaration is settled from
        # the body's binding (an owning literal, a const element), which the
        # head's per-iteration pointer binding cannot assign into.
        is_reference = (contains_reference_type(exist_bare)
                        if body_first_origin
                        else not exist_bare.is_value_type())
        if is_reference:
            raise self.ctx.error(
                f"for-loop rebind of reference-type variable '{stmt.var}' "
                f"is not yet supported; rename the loop variable", stmt)
        elem_bare = self._resolve_literal_type(
            unwrap_readonly(unwrap_ref_type(unwrap_own(elem_type))))
        if exist_bare != elem_bare:
            raise self.ctx.error(
                f"for-loop rebinds existing variable '{stmt.var}' of "
                f"type '{exist_bare}' with elements of type "
                f"'{elem_bare}'; rename the loop variable or match "
                f"the types", stmt)
        stmt.hoist_loop_var = True

    def _unbind_branch_pending(self, scope_before: set[str],
                               pending_before: set[str],
                               pending_after_then: set[str],
                               bindings_after_then: set[str],
                               bindings_after_else: set[str]) -> None:
        """Take an arm's binding back out when a loop body elsewhere owns it.

        A name one arm assigns directly while ANOTHER arm's loop body (or a
        loop before the statement) binds it is not function-scoped from the
        arm's assignment -- that one declares inside the arm's C++ block, so
        a read after the `if` would not see it. Dropping it from the
        enclosing scope leaves the pending entry as the sole authority: the
        read promotes it, and the single declaration lands where it reaches
        both arms. A name one arm both binds and makes pending crosses no
        arm boundary and is left alone.
        """
        pending = self.ctx.func.pending_loop_vars
        if not pending:
            return
        then_pending_new = pending_after_then - pending_before
        else_pending_new = set(pending) - pending_after_then
        then_scope_new = bindings_after_then - scope_before
        else_scope_new = bindings_after_else - scope_before
        cross_arm = ((then_pending_new & else_scope_new)
                     | (else_pending_new & then_scope_new)
                     | ((then_scope_new | else_scope_new) & pending_before))
        self._unbind_names(cross_arm)

    def _unbind_names(self, names: set[str]) -> None:
        """Drop names from the current scope, leaving their pending entry the
        sole authority. Only a name that HAS one is dropped: the pending table
        is what a later read (or binding) promotes the one declaration from."""
        pending = self.ctx.func.pending_loop_vars
        bindings = self.ctx.func.current_scope.bindings
        for name in [n for n in names if n in bindings and n in pending]:
            del bindings[name]
            if self.ctx.func.current_ns is not None:
                self.ctx.func.current_ns.unbind(name)

    def _enter_sibling_arm(self, bindings_before: dict[str, TpyType],
                           ns_types_before: dict[str, TpyType],
                           before: 'FlowFacts',
                           arm_only: set[str] | frozenset[str] = frozenset(),
                           ) -> set[str]:
        """Reset to a statement's entry state for its next sibling arm, and
        return the names an earlier arm bound first that stay in scope.

        Flow facts (narrowing, assigned-ness, borrows) go back to the entry
        state, but the SLOT a local has does not: Python gives a local one
        slot however many arms bind it, so every binding joins into one
        type. A name an earlier arm bound first stays in scope, and the
        later arm's binding goes down the ordinary reassignment join (a
        read before that binding still errors, since assigned-ness is
        reset); a name that existed before the statement takes its
        canonical declaration type, which an earlier arm may have widened,
        not its entry type. `arm_only` names the arm's pattern captures:
        a capture keeps per-arm storage, so it is not carried.
        """
        bindings = self.ctx.func.current_scope.bindings
        for name in arm_only:
            if name not in bindings_before:
                bindings.pop(name, None)
        carried = set(bindings) - set(bindings_before)
        rebound = {n for n, t in bindings_before.items()
                   if bindings.get(n) != t}
        bindings.update(bindings_before)
        # A name the arm only PROMOTED is not the arm's to carry: left in
        # scope it hides the promotion path from the next arm, which then
        # reads a local whose assigned-ness only the promotion spells out.
        # Keyed on the live pending table, not on what was pending before
        # the statement: a loop inside the arm makes its body local pending
        # there.
        self._unbind_names(carried)
        self._restore_ns_var_types(ns_types_before)
        self.init.restore(before)
        self._sync_promoted_var_types(rebound)
        return carried & set(bindings)

    def _analyze_loop_orelse(self, stmt: TpyWhile | TpyForEach) -> None:
        """Analyze a loop's `else` clause as another block of the loop.

        Same rule and same deciding site as the body: a name first bound in
        the clause is the one function-scoped local Python makes it, hoisted
        to the loop statement through the pending table so a read after the
        loop promotes ONE declaration. `runs_once=True` is not a claim about
        the head -- on the fall-through path the `else` clause DID run, by
        definition, and the paths that skipped it are the break paths the
        caller joins in afterwards.
        """
        if not stmt.orelse:
            return
        with self.scopes.block_scope() as else_scope:
            for s in stmt.orelse:
                self.analyze_stmt(s)
            else_end_assigned = self._assigned_here()
            # A str/bytes tuple-unpack target the clause declares owns its
            # buffer, on the same channel and for the same reason as an
            # if/match branch's: the `__tup` it would view into is the
            # clause's own and dies with the block, while the hoisted slot
            # stands at the loop statement. Inside the scope, which is where
            # the targets are still looked up.
            self.deduction.promote_hoisted_views(set(else_scope.bindings))
        self._propagate_loop_body_vars(stmt, else_scope, runs_once=True,
                                       body_end_assigned=else_end_assigned)

    def _propagate_loop_body_vars(self, stmt: TpyStmt,
                                   inner_scope: 'Scope',
                                   skip_var: str | None = None, *,
                                   runs_once: bool,
                                   body_end_assigned: frozenset[str],
                                   head_targets: frozenset[str] = frozenset(),
                                   ) -> None:
        """Store a loop body's declared variables as pending.

        Every loop kind calls this, and it is the one site that decides the
        rule: a name first bound inside a loop body counts as assigned after
        the loop only when the loop provably runs (`runs_once`) AND the
        binding is on every path through the body (`body_end_assigned`,
        the definite-assignment set at body exit). Otherwise the name stays
        maybe-unassigned and a read after the loop takes the ordinary
        definite-assignment reject.

        skip_var: loop variable name to exclude (already handled by caller).
        head_targets: the targets of a tuple-unpack `for` head, which the
        body's first statement binds but which are head bindings.
        """
        # Always non-empty: the dispatcher pushed `stmt` before the body walk.
        stack = tuple(self.ctx.func.compound_stack)
        body_bindings = dict(inner_scope.bindings)
        if inner_scope.namespace is not None:
            # A name the body binds in the namespace alone (a walrus target,
            # a handler-only name) is as much a body local as a declared one.
            for name, var_type in inner_scope.namespace.own_variables().items():
                body_bindings.setdefault(name, var_type)
        for name, var_type in body_bindings.items():
            if name == skip_var:
                continue
            if name in self.ctx.func.global_declarations:
                continue
            resolved = self._resolve_literal_type(var_type)
            existing = self.ctx.func.pending_loop_vars.get(name)
            # Two loops binding one name share ONE local, so the entry keeps
            # the FIRST binding's stack: that stack is what the shared
            # declaration has to reach, and re-recording the later binding
            # would move the anchor past the first loop and declare the name
            # twice. The TYPE is this body's, already joined with the earlier
            # binding by the reassignment path (the second body's binding
            # promotes the pending entry before it declares anything).
            if existing is None:
                entry = PendingLocal(resolved, stack, None,
                                     head_first=name in head_targets)
            else:
                entry = PendingLocal(resolved, existing.first_stack, None,
                                     head_first=existing.head_first)
            self.ctx.func.pending_loop_vars[name] = entry
            if runs_once and name in body_end_assigned:
                # Assigned past the loop, recorded on the flow state so the
                # branch merges intersect it like any other binding -- a loop
                # in ONE arm leaves the name maybe-unassigned.
                self.ctx.func.loop_bound_assigned.add(name)

    def _propagate_for_loop_scope(self, stmt: TpyForEach,
                                   inner_scope: 'Scope',
                                   elem_type: TpyType, *,
                                   runs_once: bool,
                                   body_end_assigned: frozenset[str]) -> None:
        """Store for-loop variable and body-declared variables as pending."""
        # Loop variable (skip synthetic tuple-unpack vars in non-generators;
        # generators need the synthetic var as a struct field)
        var_name = stmt.var
        is_generator = (isinstance(self.ctx.func.current_function, TpyFunction)
                        and self.ctx.func.current_function.is_generator)
        if ((not stmt.is_tuple_unpack or is_generator)
                and var_name not in self.ctx.func.global_declarations):
            resolved = self._resolve_literal_type(elem_type)
            stack = tuple(self.ctx.func.compound_stack)
            # A head that bound a loop-body-first local is one more binding of
            # that local: its entry keeps the first binding's anchor, which is
            # where the one declaration stands.
            if not (stmt.hoist_loop_var
                    and self._is_body_first_local(var_name)):
                self.ctx.func.pending_loop_vars[var_name] = PendingLocal(
                    resolved, stack, stmt, head_first=True)
            if runs_once:
                # A provable loop binds its variable on every iteration, so
                # it is assigned past the loop; an unprovable one leaves it
                # maybe-assigned. Recorded on the flow state so a branch
                # merge can take it back when only one arm runs the loop.
                self.ctx.func.loop_bound_assigned.add(var_name)

        head = stmt.body[0] if stmt.body else None
        head_targets = (frozenset(t for t in head.targets if t is not None)
                        if isinstance(head, TpyTupleUnpack) and head.is_loop_head
                        else frozenset())
        self._propagate_loop_body_vars(stmt, inner_scope, skip_var=var_name,
                                       runs_once=runs_once,
                                       body_end_assigned=body_end_assigned,
                                       head_targets=head_targets)

    def _analyze_global_stmt(self, stmt: TpyGlobal) -> None:
        """Analyze a `global x, y` statement."""
        # Must be inside a function, not at module level
        if self.ctx.is_top_level or isinstance(self.ctx.func.current_function, type(MODULE_INIT_CONTEXT)):
            raise self.ctx.error("'global' declaration is only allowed inside a function", stmt)
        for name in stmt.names:
            # Cannot use 'global' with Final variables
            if name in self.ctx.final_globals:
                raise self.ctx.error(
                    f"Cannot use 'global' with Final variable '{name}'", stmt)
            # Bare (unannotated) globals land in global_ns but not global_scope
            # (only typed globals are pre-registered there), so fall back to the
            # namespace reads resolve against and mirror the binding in.
            global_type = self.ctx.global_scope.lookup(name)
            if global_type is None and self.ctx.global_ns is not None:
                binding = self.ctx.global_ns.lookup_local(name)
                if (binding is not None and binding.kind == BindingKind.VARIABLE
                        and binding.type is not None):
                    global_type = binding.type
                    self.ctx.global_scope.define(name, global_type)
            if global_type is None:
                raise self.ctx.error(f"name '{name}' is not defined at module level", stmt)
            # Must not shadow a function parameter
            func = self.ctx.func.current_function
            if isinstance(func, TpyFunction):
                for pname, _ in func.params:
                    if pname == name:
                        raise self.ctx.error(
                            f"name '{name}' is a parameter and cannot be declared global", stmt)
            self.ctx.func.global_declarations.add(name)

    def validate_compile_time_constant(
        self, init: TpyExpr, target_type: 'TpyType | None', label: str, loc: object,
    ) -> None:
        """Validate that `init` is a compile-time constant; raise a uniform
        diagnostic otherwise. Shared between module-level `Final` decls and
        class constants -- callers pass `label` (e.g. `"Final variable 'X'"`
        or `"class constant 'C.X'"`) to customize the noun phrase.
        """
        bad = self._find_nonconstant_leaf(init, target_type)
        if bad is None:
            return
        prefix = f"{label} requires a compile-time constant initializer"
        if isinstance(bad, TpyName):
            imp = lookup_imported(
                self.ctx.module_attributes, bad.name, SymbolKind.VARIABLE)
            if imp is not None:
                src_mod, _ = imp
                raise self.ctx.error(
                    f"{prefix}; cross-module Final references are not yet supported "
                    f"('{bad.name}' is imported from '{src_mod}')",
                    loc,
                )
        if bad is not init and isinstance(bad, TpyName):
            raise self.ctx.error(
                f"{prefix}; '{bad.name}' is not a Final constant",
                loc,
            )
        raise self.ctx.error(prefix, loc)

    def _find_nonconstant_leaf(self, expr: TpyExpr, target_type: 'TpyType | None' = None) -> 'TpyExpr | None':
        """Find the first non-constant sub-expression in a Final initializer.

        Returns None if the expression is a valid compile-time constant,
        or the offending sub-expression otherwise.

        Accepts: literals, unary ops on constant operands, references to other
        Final globals, @call_macro expansions that reduce to a constant,
        primitive type constructor calls with constant args, binary ops on
        constant operands (when target_type is numeric/bool), and tuple
        literals with all-constant elements.
        """
        # Unwrap compile-time macro expansions
        macro_exp = getattr(expr, "macro_expansion", None)
        if macro_exp is not None:
            return self._find_nonconstant_leaf(macro_exp, target_type)
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral)):
            return None
        if isinstance(expr, TpyUnaryOp):
            return self._find_nonconstant_leaf(expr.operand, target_type)
        if isinstance(expr, TpyName) and expr.name in self.ctx.analyzed_finals:
            return None
        if isinstance(expr, TpyBinOp) and is_numeric_type(target_type):
            return (self._find_nonconstant_leaf(expr.left, target_type)
                    or self._find_nonconstant_leaf(expr.right, target_type))
        # Primitive type constructor: float32(0.5), int64(SOME_FINAL), etc.
        # Identified by: resolved to an __init__ method, result is a primitive.
        if isinstance(expr, TpyCall) and len(expr.args) == 1:
            fi = expr.resolved_function_info
            if fi is not None and fi.name == '__init__':
                result_type = expr.call_type or self.ctx.get_expr_type(expr)
                if is_numeric_type(result_type) or is_char_type(result_type):
                    return self._find_nonconstant_leaf(expr.args[0], result_type)
        if isinstance(expr, TpyTupleLiteral):
            if isinstance(target_type, TupleType) and len(target_type.element_types) == len(expr.elements):
                for e, t in zip(expr.elements, target_type.element_types):
                    bad = self._find_nonconstant_leaf(e, t)
                    if bad is not None:
                        return bad
                return None
            for e in expr.elements:
                bad = self._find_nonconstant_leaf(e)
                if bad is not None:
                    return bad
            return None
        return expr

    def _check_nonvalue_rebinding(self, name: str, node: TpyStmt) -> None:
        """Error if reassigning a non-value-type param, loop variable, or global."""
        existing_type = self.ctx.func.current_scope.lookup(name)
        if existing_type is None:
            return
        if (name in self.ctx.func.global_declarations
                and global_binds_by_reference(existing_type)):
            raise self.ctx.error(
                global_rebind_message(name, existing_type), node)
        bare = unwrap_readonly(existing_type)
        if (self.ctx.is_top_level and isinstance(bare, TupleType)
                and bare.has_own_element()
                and name in self.ctx.tuple_globals_aliased):
            # A tuple global that OWNS a reference element is its own storage
            # (no slot indirection), so once a module-level unpack has aimed
            # a pointer-slot target into it, a rebind would overwrite the
            # object that target points at -- the alias would follow the
            # rebind where CPython keeps the old object. The scalar
            # reference global re-points at fresh static backing instead;
            # until the owning tuple global takes that form
            # (BUGS.md#global-tuple-ref-storage-form) it is bound once
            # after being borrowed from. With no alias the rebind is a plain
            # storage assign and stays allowed.
            raise self.ctx.error(
                f"Cannot reassign global variable '{name}' of type "
                f"'{collapse_tuple_own_elements(bare)}': a module-level "
                f"unpack borrowed an element of it, and the alias would "
                f"follow the rebind", node)
        if unwrap_readonly(existing_type).is_value_type():
            return
        # Check function parameters
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                ptype_bare = unwrap_ref_type(ptype)
                if pname == name and not unwrap_readonly(ptype_bare).is_value_type():
                    raise self.ctx.error(
                        f"Cannot reassign parameter '{name}' of type '{unwrap_readonly(ptype_bare)}'; "
                        f"assign to a new local variable instead",
                        node
                    )
        # Check for-each loop variables
        if name in self.ctx.func.loop_vars:
            raise self.ctx.error(
                f"Cannot reassign loop variable '{name}' of type '{existing_type}'; "
                f"assign to a new local variable instead",
                node
            )

    def _infer_new_local_view_type(
        self, name: str, var_type: TpyType,
        init_expr: TpyExpr | None, init_type: TpyType | None,
        line: int | None, family: ViewTypeFamily,
    ) -> TpyType:
        """Create a pending view type for a new local of the given family."""
        var_id = self.ctx.next_view_var_id(family)
        vars_reg = self.ctx.view_vars(family)
        var_map = self.ctx.view_var_map(family)

        if isinstance(var_type, PendingViewType):
            # Alias from another pending view type -- collect ALL leaf sources.
            # For `x = a or b` both a and b are sources; if either resolves to
            # owned, x must too (otherwise x would be a view into a
            # potentially-reallocated buffer).
            if init_expr is not None:
                source_ids = [t.var_id for t in collect_pending_source_types(self.ctx, init_expr)
                              if isinstance(t, family.pending_type_class)]
            else:
                source_ids = [var_type.var_id]
            info = ViewVarInfo(var_id=var_id, variable_name=name,
                               decl_line=line, source_var_ids=source_ids,
                               frame_unsafe_source=(
                                   init_expr is not None
                                   and self.deduction.has_nonstatic_view_source(init_expr)))
        else:
            # Fresh from owned type (str or bytes)
            if init_expr is None:
                is_owned = False
            elif is_borrowing_view_type(var_type):
                # The local's own type is already the view, so its source is a
                # borrow by construction; the only thing forcing an owned copy
                # is a source whose storage dies at end-of-statement.
                is_owned = view_source_is_temporary(
                    init_expr.expr if isinstance(init_expr, TpyCoerce)
                    else init_expr)
            else:
                is_owned = not self.deduction.is_view_compatible_source(init_expr, init_type)
            info = ViewVarInfo(var_id=var_id, variable_name=name,
                               decl_line=line, initialized_from_owned=is_owned,
                               frame_unsafe_source=(
                                   not is_owned
                                   and self.deduction.has_nonstatic_view_source(init_expr)))

        vars_reg[var_id] = info
        var_map[name] = var_id
        self.ctx.func.view_ids_by_name.setdefault(name, []).append((family, var_id))
        # No expression here means the binding site records it itself (a
        # loop variable, an unpack target); until then it is unknown.
        self.deduction.note_view_binding(family, var_id, init_expr)
        self.ctx.view_pending_resolutions(family).append(var_id)
        return family.pending_type_class(var_id)

    def _tuple_global_binding_type(
        self, var_type: TpyType, init: TpyExpr, init_type: TpyType | None,
    ) -> TpyType:
        """The type a module-slot TUPLE binding records, with the per-element
        ownership its init decides spelled as `Own` on the element.

        A tuple global takes the form its local twin takes: a literal of
        NAMES is a borrow (the tuple of the pointer slots those globals are),
        while a FRESH reference element, or an element an owning call hands
        back, is owned inline. A function-local records that split in its
        lowering facts; a global is read from other functions and modules by
        its binding type alone, so the split is recorded here, once, and
        `is_borrow_form_tuple_global` reads it off the type everywhere."""
        bare = unwrap_readonly(var_type)
        if not (isinstance(bare, TupleType) and bare.has_pointer_repr_element()
                and not bare.has_own_element()):
            return var_type
        if isinstance(init, TpyTupleLiteral):
            if len(init.elements) != len(bare.element_types):
                return var_type
            elems = []
            for elem, et in zip(init.elements, bare.element_types):
                fresh = (not et.is_value_type()
                         and not isinstance(elem, TpyNoneLiteral)
                         and not self.compat.is_lvalue(elem))
                elems.append(OwnType(et) if fresh else et)
            return TupleType(tuple(elems))
        init_bare = unwrap_readonly(init_type) if init_type is not None else None
        if (isinstance(init, (TpyCall, TpyMethodCall))
                and isinstance(init_bare, TupleType)
                and init_bare.has_own_element()
                and len(init_bare.element_types) == len(bare.element_types)):
            return TupleType(tuple(
                OwnType(et) if isinstance(it, OwnType) else et
                for et, it in zip(bare.element_types, init_bare.element_types)))
        return var_type

    def _infer_new_local_type(
        self, name: str, var_type: TpyType,
        init_expr: TpyExpr | None, init_type: TpyType | None,
        line: int | None, *, annotated: bool = False,
    ) -> TpyType:
        """Apply deferred type inference for a new local variable.

        Handles view-type families (str/bytes -> PendingViewType) and
        PendingListType alias logic.  Skipped at module top-level.

        When init_expr is None (for-loop var, tuple unpack), the source is
        considered view-compatible (initialized_from_owned=False) because
        the container outlives the loop/unpack scope -- in a SYNC body; a
        resumable frame outlives its case-block temps, so these register
        as frame-unsafe and resolve owned there (frame_unsafe_source).
        """
        if self.ctx.is_top_level:
            return var_type
        # Float literals resolve to float64 as new local variables
        if isinstance(var_type, FloatLiteralType):
            return FLOAT

        # View-type families (str/bytes): register a pending view-vars entry for
        # deferred view-vs-owned storage resolution. For LiteralType[str/bytes] the
        # entry is registered for codegen but `stmt.type` stays LiteralType so
        # OOS rejection / dispatch / narrowing keep seeing the annotation.
        family = view_family_for_type(var_type)
        # An inferred (un-annotated) local whose type is itself a borrowing view
        # (`s = p[1:]`, `s = make().strip()`) is not caught by
        # view_family_for_type (that keys on the owned/pending types). It is the
        # same local as `s = p` -- one type and one storage, joined over every
        # binding -- so route it through the same deduction machinery: the first
        # binding's TYPE SPELLING must not decide whether a later owned rebind
        # copies or rejects. An explicit `StrView`/`BytesView` annotation is a
        # promise not to copy and keeps its own contract (a temporary source is
        # rejected at the decl site, never promoted).
        if (family is None and not annotated and init_expr is not None
                and is_borrowing_view_type(var_type)):
            for fam in VIEW_TYPE_FAMILIES:
                if fam.is_any_member(var_type):
                    family = fam
                    break
        if family is not None:
            pending = self._infer_new_local_view_type(name, var_type, init_expr, init_type, line, family)
            return var_type if isinstance(var_type, LiteralType) else pending
        elif (isinstance(var_type, PendingListType)
                and init_expr is not None and isinstance(init_expr, TpyName)):
            return self.deduction.register_list_alias(
                name, var_type,
                decl_line=line,
            )

        return var_type

    def _note_loop_var_iteration(self, var: str, elem_type: TpyType,
                                 prior: 'list[str] | None', *,
                                 placeable: bool = True) -> None:
        """Record the storage this loop's variable iterates -- only what THIS
        loop registered (a reused name keeps the last loop's entry otherwise)
        -- and, for a str/bytes loop variable, its one binding."""
        keys = self.ctx.func.loop_var_iterable.get(var) if placeable else None
        roots = ({k.split(".", 1)[0] for k in keys}
                 if keys and keys is not prior else None)
        self.ctx.func.loop_var_iter_roots[var] = roots
        inner = unwrap_own(unwrap_ref_type(unwrap_readonly(elem_type)))
        if isinstance(inner, PendingViewType):
            resolved: 'set[str] | None' = set()
            for r in roots or ():
                sub_roots = self.deduction._resolve_hoist_root(r, {var})
                if sub_roots is None:
                    resolved = None
                    break
                resolved |= sub_roots
            self.deduction.note_view_binding(
                inner.family, inner.var_id, None,
                roots=resolved if roots is not None else None, replace=True)

    def _iteration_yields_copies(self, iterable_type: TpyType,
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
        record = self.ctx.registry.get_record_for_type(inner)
        if record is None or depth > 4:
            return True
        iters = record.get_method_overloads("__iter__")
        if iters and unwrap_readonly(iters[0].return_type) != inner:
            return self._iteration_yields_copies(iters[0].return_type, depth + 1)
        nexts = record.get_method_overloads("__next__")
        return not (nexts and yield_always_borrows(nexts[0].return_type))

    def _is_ephemeral_borrow_loop_source(self, iterable_type: TpyType, *,
                                         open_param_borrows: bool = False) -> bool:
        """True if iterating this source hands out frame-slot-rooted borrows whose
        validity ends with the producer -- a generator / `Iterator[T]` value
        whose element is a non-value borrow (BORROW_REF) or a borrow-form tuple
        (`std::tuple<..., T*>` whose pointers root in the producer's frame).
        Container sources (list/dict/Span/user `__iter__`) are durable and
        excluded; so is `Iterator[Own[T]]` (owned, moved out) and value-type
        elements.

        `open_param_borrows` forwards to `_elem_is_ephemeral_borrow`: an open
        `T` element counts as a borrow, which only the generic-yield
        provenance gate asks for.
        """
        inner = unwrap_readonly(unwrap_ref_type(iterable_type))
        # Ephemeral when the source hands out an element by BORROW: the plain
        # val_or_ref slot (`yield_uses_borrow_slot` -- a `readonly` element is
        # one of those, spelled `val_or_ref<const T>`) OR a pointer-element
        # borrow tuple. Elements the slot copies out keep their own
        # representation and are not ephemeral.
        if isinstance(inner, GenExprType):
            return self._elem_is_ephemeral_borrow(
                inner.element_type, open_param_borrows=open_param_borrows)
        if not (is_protocol_type(inner) and isinstance(inner, NominalType)
                and inner.qualified_name() == "typing.Iterator" and inner.type_args):
            return False
        raw_elem = inner.type_args[0]
        if not isinstance(raw_elem, TpyType):
            return False
        return self._elem_is_ephemeral_borrow(
            raw_elem, open_param_borrows=open_param_borrows)

    @staticmethod
    def _elem_is_ephemeral_borrow(elem_type: TpyType, *,
                                  open_param_borrows: bool = False) -> bool:
        """A yielded element whose representation borrows the producer's frame:
        the scalar val_or_ref slot, or a pointer-element borrow tuple.

        `yield_always_borrows`, not the slot predicate: an open `T` element
        borrows only at its reference instantiations, and this rule runs once
        at the generic body's definition. `open_param_borrows` asks the same
        question WITH the open `T` counted as a borrow -- the generic-yield
        provenance gate's half, which drives a slot choice rather than a
        diagnostic.
        """
        if (yield_uses_borrow_slot(elem_type) if open_param_borrows
                else yield_always_borrows(elem_type)):
            return True
        peeled = unwrap_readonly(unwrap_ref_type(elem_type))
        return (isinstance(peeled, TupleType)
                and peeled.has_pointer_repr_element())

    def _mark_ephemeral_loop_targets(self, stmt: TpyForEach, ephemeral_src: bool,
                                     open_param_src: bool = False) -> set[str]:
        """Stamp the loop var of an ephemeral borrow yield -- and, for a
        tuple-unpack loop, the unpack target names (each aliases the producer's
        frame through the synthetic tuple var) -- returning the names added to
        ephemeral_borrow_vars (so the caller can drop them after the loop
        body).

        `open_param_src`: the source lends only at the reference
        instantiations of an open `T`, so the names are held GATE_ONLY --
        marking them HARD would reject the value instantiations, where
        nothing is borrowed."""
        if not ephemeral_src and not open_param_src:
            return set()
        added = {stmt.var}
        if stmt.is_tuple_unpack and stmt.body:
            unpack = stmt.body[0]
            if isinstance(unpack, TpyTupleUnpack):
                added |= {t for t in unpack.targets if t is not None}
        kind = EphemeralKind.HARD if ephemeral_src else EphemeralKind.GATE_ONLY
        for name in added:
            self.ctx.func.ephemeral_borrow_vars[name] = kind
        return added

    def _reject_ephemeral_escape(self, expr: 'TpyExpr | None', what: str) -> None:
        """If `expr` is (or roots in) an ephemeral borrow var, reject the escape.

        An ephemeral borrow (a for-loop var over a generator / Iterator[T] /
        genexpr borrow source) is valid only for the current iteration step -- the
        producer's frame slot is overwritten on the next __next__(). Retaining it
        past the step (returning or yielding it onward) would read a stale slot.
        The fix is to copy out (`.clone()` for @nocopy types, a plain copy)."""
        name = self._ephemeral_root_name(expr)
        if name is None:
            return
        raise self.ctx.error(
            f"Cannot {what} '{name}': it borrows an element of a generator / "
            f"iterator, valid only for the current iteration step (the producer "
            f"reuses its frame slot on the next step). Copy it out first "
            f"(`{name}.clone()` for a @nocopy type, otherwise a plain copy).",
            expr,
        )

    def _ephemeral_root_name(self, expr: 'TpyExpr | None') -> 'str | None':
        """Return the HARD ephemeral-borrow var name `expr` reads from, else
        None (see `sema.context.ephemeral_borrow_root`). Only a HARD hold
        rejects an escape; a GATE_ONLY one is not a borrow at the value
        instantiations, so it answers the slot verdict alone."""
        return ephemeral_borrow_root(self.ctx.func.ephemeral_borrow_vars, expr,
                                     EphemeralKind.HARD)

    def _update_ephemeral_alias_fact(self, name: str,
                                     var_type: 'TpyType | None',
                                     init_expr: 'TpyExpr | None') -> None:
        """Propagate (or clear) the ephemeral-borrow fact through an aliasing
        binding: `q = p` (or `q = p.field` for a non-value field) of an
        ephemeral loop var is the same stale-slot borrow under another name,
        so escapes through the alias must reject like the direct form. Value
        bindings COPY and carry no fact; any other rebind clears it.

        The alias inherits the root's KIND, so a GATE_ONLY hold reaches the
        slot verdict through an alias exactly as the direct form does.
        """
        root = None
        if init_expr is not None and var_type is not None:
            var_bare = unwrap_readonly(var_type)
            aliasing_shape = (
                not var_type.is_value_type()
                or (isinstance(var_bare, TupleType)
                    and var_bare.has_pointer_repr_element()))
            if aliasing_shape:
                root = ephemeral_borrow_root(
                    self.ctx.func.ephemeral_borrow_vars, init_expr)
        if root is not None:
            self.ctx.func.ephemeral_borrow_vars[name] = (
                self.ctx.func.ephemeral_borrow_vars[root])
        else:
            self.ctx.func.ephemeral_borrow_vars.pop(name, None)

    def _analyze_yield(self, stmt: TpyYield) -> None:
        """Analyze a yield statement in a generator function."""
        func = self.ctx.func.current_function
        if func is None or not func.is_generator:
            raise self.ctx.error("'yield' can only be used inside a generator function", stmt)
        if self.ctx.func.in_finally:
            # The frame destructor runs pending finally bodies on
            # abandonment, but a finally that itself suspends cannot run
            # inside a destructor -- it is silently skipped there.
            # (CPython runs it up to this yield, then raises and ignores
            # RuntimeError("generator ignored GeneratorExit").)
            self.ctx.warning(
                "'yield' inside 'finally': this cleanup will not run if "
                "the generator is abandoned before exhaustion (e.g. "
                "'break' out of a for loop over it)", stmt)
        elem_type = func.generator_yield_type
        if elem_type is None and func.is_genexpr:
            # A genexpr declares no yield type: its one yield decides it.
            yield_type = self.expr.analyze_expr(stmt.value)
            elem_type = resolve_int_literals(yield_type, self.ctx.default_int_for_literal)
            if isinstance(elem_type, FloatLiteralType):
                elem_type = FLOAT
            func.generator_yield_type = elem_type
        else:
            assert elem_type is not None
            yield_type = self.expr.analyze_expr_with_hint(stmt.value, elem_type)
        stmt.value = self.compat.coerce_expr(
            stmt.value, yield_type, elem_type, "yield value",
            coercion_ctx=CoercionContext.RETURN)
        # The coercion compares the literal against the declared tuple as a
        # whole, so an `Own` element's scalar `T -> Own[T]` copy rule never
        # runs per member -- apply it here.
        yielded = peel_value_wrappers(stmt.value)
        yield_bare = unwrap_readonly(unwrap_ref_type(elem_type))
        if (isinstance(yielded, TpyTupleLiteral)
                and isinstance(yield_bare, TupleType)):
            self.compat.check_tuple_literal_members(
                yielded, yield_bare, TupleSink.YIELD, "owned storage")
        # Re-yielding an ephemeral borrow onward (consumed from an inner
        # generator/iterator) past its iteration step would let the outer
        # consumer read a stale slot -- reject unless copied out. EXCEPTION:
        # the direct relay of an ephemeral borrow TUPLE (`for pair in src:
        # yield pair`) is sound -- the inner source lives in THIS generator's
        # frame, and the outer consumer's use of the relayed borrow lasts one
        # step of THIS generator, which is within the source's lifetime. The
        # allowance is yield-only and does not mark the var durable: return /
        # retention escapes still reject below and in `_analyze_return`.
        inner_val = (stmt.value.expr if isinstance(stmt.value, TpyCoerce)
                     else stmt.value)
        is_direct_tuple_relay = (
            isinstance(inner_val, TpyName)
            and (self.ctx.func.ephemeral_borrow_vars.get(inner_val.name)
                 is EphemeralKind.HARD)
            and isinstance(unwrap_readonly(unwrap_ref_type(
                self.ctx.get_expr_type(inner_val) or elem_type)), TupleType)
        )
        if not is_direct_tuple_relay:
            self._reject_ephemeral_escape(stmt.value, "yield")
        # Declaration-driven yield ABI: a plain-reference borrow yield (the
        # val_or_ref<T> slot) hands out a reference, so the yielded source must
        # outlive the frame -- the same rooting rule as returning a reference,
        # since the generator frame survives suspension. The view-only
        # check_view_return_dangle misses bare non-value reference yields, so route
        # those through the full dangling check (Iterator[Own[T]]-flavored
        # diagnostic). Forms with their own representation (Optional/Union/tuple/
        # Own -- see `yield_uses_borrow_slot`) keep the view check; they
        # don't use the borrow slot.
        # A tuple yield uses borrow form per-element (`std::tuple<int, Box*>`),
        # so a fresh non-value member dangles exactly like a bare borrow yield.
        # yield_uses_borrow_slot excludes tuples (they own their borrow form via
        # to_cpp_return), so route them through the full dangling check too -- its
        # per-element tuple branch is what catches the fresh member.
        if yield_always_borrows(elem_type) or isinstance(unwrap_readonly(elem_type), TupleType):
            # A borrow-yielded container literal must materialize as a real
            # list/dict/set (the val_or_ref<T> slot hands out a reference, and
            # the consumer may resize it), so force it off the Array
            # optimization -- a new escape route the array builder predates.
            self._force_yielded_pending_lists(stmt.value, elem_type)
            # Deferred: a yielded frame-resident local is a valid borrow root,
            # but func.generator_locals isn't populated until after body
            # analysis. The ephemeral-escape rejection above stays inline, so an
            # ephemeral re-yield is still caught before this point.
            self.ctx.func.pending_yield_root_checks.append(
                (stmt.value, elem_type, stmt.loc))
        elif yield_uses_borrow_slot(elem_type):
            # An open `T`: whether the slot lends is not a per-yield question
            # but a per-generator one (one slot type for the whole frame), and
            # an unrooted source is not an error here -- it selects the VALUE
            # slot, which copies. Record the two halves of the provenance
            # verdict; the rooting half is answered at the drain, the ephemeral
            # half only while the consuming loop body's marking is live.
            self.ctx.func.pending_generic_yield_sources.append(
                (stmt.value,
                 ephemeral_borrow_root(self.ctx.func.ephemeral_borrow_vars,
                                       stmt.value) is not None))
        else:
            self.compat.check_view_return_dangle(stmt.value, elem_type, stmt.loc,
                                                 for_yield=True)
        # A mutable borrow yield hands the consumer a writable reference into the
        # frame's source storage (a self field, param, ...), exactly like returning
        # a mutable borrow (see the TpyReturn branch above). Mark the source roots
        # mutated so an enclosing method is not auto-inferred readonly -- a const
        # receiver would make the source element `const T&` which the mutable
        # val_or_ref<T> slot cannot bind, and would wrongly forbid consumer
        # mutation. Applies to every non-readonly non-value, non-Own yield
        # (including a generic TypeParamRef, which instantiates to a borrow); a
        # readonly yield is a const borrow and an Own[T] yield moves, so both skip.
        if (not elem_type.is_value_type()
                and not isinstance(unwrap_readonly(unwrap_ref_type(elem_type)), OwnType)
                and not isinstance(unwrap_ref_type(elem_type), ReadonlyType)):
            for root in addr_taken_roots(stmt.value):
                self.ctx.mark_param_mutated(root)
                self.ctx.mark_param_returned(root)
        # A tuple is a value type, but its borrow-form yield slot hands out
        # mutable element pointers into the source storage -- the same escape
        # as the mutable-borrow yield above, missed by the is_value_type()
        # guard. Mirrors the TpyReturn borrow-tuple branch: a readonly tuple
        # (or element) grants no write access, so it records provenance only.
        elem_bare = unwrap_readonly(unwrap_ref_type(elem_type))
        if (isinstance(elem_bare, TupleType)
                and elem_bare.has_borrowing_element()):
            ro_tuple = isinstance(unwrap_ref_type(elem_type), ReadonlyType)
            for root, grants_write in tuple_borrow_escape_roots(
                    stmt.value, elem_bare, ro_tuple,
                    expr_type=self.ctx.get_expr_type):
                if grants_write:
                    self.ctx.mark_param_mutated(root, through_field=True)
                self.ctx.mark_param_returned(root)
        # The yield value above was analyzed pre-suspension; everything
        # after the yield runs post-resume, when the caller may have
        # mutated shared storage between next() calls.
        self.narrowing.invalidate_suspension_facts()

    def _force_yielded_pending_lists(self, value: TpyExpr, elem_type: TpyType) -> None:
        """A borrow-yielded container literal must materialize as a real
        list/dict/set, not the Array optimization: the val_or_ref<T> slot hands
        out a reference and the consumer may mutate/resize it. Reuse the return
        position's container-context hook (it both forces off Array and
        propagates the element type from the yield's declared type), per element
        for a tuple yield.
        """
        inner = unwrap_readonly(elem_type)
        if isinstance(value, TpyTupleLiteral) and isinstance(inner, TupleType):
            for e, et in zip(value.elements, inner.element_types):
                self.deduction.mark_container_return_context(e, et)
        else:
            self.deduction.mark_container_return_context(value, elem_type)

    def _bar_unscanned_lvalue_reassign(self, name: str,
                                       value: TpyExpr) -> None:
        """A reassign from an lvalue the prescan's untyped scan calls fresh
        (a select, a borrow-returning call, a field of one) is invisible to
        the alias maps the liveness walk reads, so the name is barred from
        the movable set -- else an auto-move at its last use would steal
        from the source it aliases."""
        if is_scan_rvalue(value):
            self.ctx.func.borrow_reassigned_vars.add(name)

    def _analyze_fresh_binding(self, value: TpyExpr) -> TpyType:
        """Analyze the whole value of an unannotated first binding of a
        name, the one binding a diagnostic can suggest annotating."""
        saved = self.ctx.name_initializer
        self.ctx.name_initializer = value
        try:
            return self.expr.analyze_expr(value)
        finally:
            self.ctx.name_initializer = saved

    def _analyze_var_decl(self, stmt: TpyVarDecl) -> None:
        """Analyze a variable declaration."""
        # In nested defs, assigning to an outer variable requires nonlocal
        if (self.ctx.func.in_nested_def
                and stmt.name in self.ctx.func.outer_scope_locals
                and stmt.name not in self.ctx.func.current_nonlocal_names):
            if stmt.name == "self" and self.ctx.self_names_receiver():
                raise self.ctx.error(
                    "Cannot rebind 'self' in a nested function: the method"
                    " receiver cannot be rebound. Bind the new object to a"
                    " different name instead",
                    stmt)
            raise self.ctx.error(
                f"Cannot assign to '{stmt.name}' in nested function"
                f" without 'nonlocal' declaration",
                stmt)
        # Type alias expansion: replace NominalType("Shape") with the alias
        # target (one level; recurses into the target's inner types; _seen
        # guards self-referential aliases). Must run before `resolve_type`
        # because alias targets can contain further type references that
        # resolve_type wants to process uniformly. Enum substitution is
        # handled by `resolve_type` itself -- it replaces bare
        # NominalType("Color") placeholders with the registered enum
        # NominalType (which carries `_module_qname`), whether the enum
        # appears directly or is the target of a resolved alias.
        if stmt.type and self.ctx.registry.type_aliases:
            registry = self.ctx.registry
            recursive_names = self.ctx.recursive_union_names

            def _expand_aliases(t: TpyType, _seen: frozenset[str] = frozenset()) -> TpyType:
                # Alias references are bare parser NominalType placeholders
                # (no _module_qname, no TypeDef entry) -- the qname guard
                # enforces "unresolved only," matching `_resolve_alias` in
                # analyzer.py. `get_type_alias` is the specific check.
                if (isinstance(t, NominalType)
                        and not t.is_protocol
                        and not t._module_qname
                        and t.name not in _seen
                        and t.name not in recursive_names):
                    alias = registry.get_type_alias(t.name)
                    if alias is not None:
                        new_seen = _seen | {t.name}
                        return alias.map_inner_types(
                            lambda inner: _expand_aliases(inner, new_seen)
                        )
                return t.map_inner_types(lambda inner: _expand_aliases(inner, _seen))

            stmt.type = _expand_aliases(stmt.type)

        # resolve_type sets is_protocol flag, upgrades NominalType -> TypeParamRef
        # in generic scopes, substitutes enum placeholders, and handles
        # compile-time-only aliases (FStr etc.).
        if stmt.type:
            stmt.type = self.type_ops.resolve_type(stmt.type)

        # Validate the type annotation if present
        # Allow TypeParamRef inside generic functions or generic record methods
        if stmt.type:
            # Own[T] / readonly[T] are only valid for parameters and return
            # types, not variables. Run these context-specific rejections
            # BEFORE validate_type so a richer-context diagnostic wins over
            # the generic shape rejection (e.g. `Own[Optional[Polymorphic]]`
            # at a local should surface "Own[T] not allowed as variable",
            # not "use Optional[T] or Box[T]" -- the user can't use either
            # in a local context either).
            if type_contains_own(stmt.type, allow_optional_own=False):
                raise self.ctx.error(
                    f"Own[T] is redundant in this variable type ('{stmt.type}'): a "
                    f"local owns its value inline and moves it out at its last use "
                    f"-- remove the Own (use copy() or an owning call if you need an "
                    f"owned copy). Own selects an owned shape only at a borrow-default "
                    f"position: parameter / return types.",
                    stmt
                )
            if isinstance(stmt.type, ReadonlyType):
                raise self.ctx.error(
                    f"readonly[{stmt.type.wrapped}] cannot be used as a variable type. "
                    f"Readonly on locals is deduced from initialization",
                    stmt
                )
            try:
                in_generic = bool(
                    (isinstance(self.ctx.func.current_function, TpyFunction) and self.ctx.func.current_function.type_params)
                    or self.ctx.record_ctx.type_params
                )
                # An init-only function-local `p: Optional[Pet] = Dog()` lowers
                # to a `const Pet*` pointing at the materialized rvalue, same
                # shape as the parameter position. Allow the pointer-repr
                # @dynamic Optional here; the rvalue-rebind reject (shared slot
                # can't retype) and definite-assignment keep it sound. Globals
                # (module-init sentinel) and no-init declarations stay rejected
                # -- a static slot has no value representation for an abstract
                # base.
                allow_ptr_repr_dyn = (
                    stmt.init is not None
                    and isinstance(self.ctx.func.current_function, TpyFunction)
                )
                self.type_ops.validate_type(
                    stmt.type, allow_type_param_ref=in_generic, loc=stmt.loc,
                    allow_forward_ref=False,
                    allow_pointer_repr_dynamic=allow_ptr_repr_dyn)
            except SemanticError as e:
                raise self.ctx.error(str(e), stmt)

        # Fn[...] is only valid in parameter position
        if stmt.type and contains_fn_type(stmt.type):
            raise self.ctx.error(
                "Fn type is only valid in parameter position. "
                "Use Callable for fields, returns, and locals",
                stmt
            )

        # Final[T] validation
        # Detect FinalType from annotation (covers function-level where register_globals didn't run)
        if stmt.type and isinstance(stmt.type, FinalType):
            stmt.type = final_type_str_to_strview(stmt.type.wrapped)
            stmt.is_final = True
        if stmt.is_final:
            if not self.ctx.is_top_level:
                raise self.ctx.error(
                    f"Final can only be used at module level",
                    stmt
                )
            if stmt.name in self.ctx.analyzed_finals:
                raise self.ctx.error(
                    f"Cannot re-declare Final variable '{stmt.name}'",
                    stmt
                )
            if not stmt.init:
                raise self.ctx.error(
                    f"Final variable '{stmt.name}' must have an initializer",
                    stmt
                )
            inner = stmt.type
            if not is_final_allowed_inner(inner):
                raise self.ctx.error(
                    f"Final[{inner}] is not supported; {FINAL_INNER_TYPE_ERROR}",
                    stmt
                )

        # Declared-type ALL_CAPS decls warn here with the concrete annotation;
        # an inferred (unannotated) decl warns after its init type is known
        # (below), so the suggestion can name that type instead of "<type>".
        if self.ctx.is_top_level and not stmt.is_final and stmt.type:
            self._warn_all_caps_without_final(stmt.name, stmt.type, stmt)

        # Protocol types can only be used for function parameters, not variables
        # Exception: @dynamic protocols can be used as variable types
        if stmt.type and is_protocol_type(stmt.type):
            protocol_info = protocol_info_of(stmt.type)
            if not protocol_info or not protocol_info.is_dynamic:
                raise self.ctx.error(
                    f"Protocol type '{stmt.type.name}' cannot be used as a variable type. "
                    f"Only @dynamic protocols can be used as variable types",
                    stmt
                )

        # Detect native global import: x: T = native_global("name")
        if isinstance(stmt.init, TpyCall) and isinstance(stmt.init.func, TpyName) and self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(stmt.init.func_name)
            if (binding and binding.kind == BindingKind.IMPORTED_NAME
                    and binding.import_source
                    and binding.import_source[0] == "tpy.extern"
                    and binding.import_source[1] == "native_global"):
                func_name = binding.import_source[1]
                if not self.ctx.is_top_level:
                    raise self.ctx.error(
                        f"{func_name}() can only be used at module level",
                        stmt
                    )
                if stmt.type is None:
                    raise self.ctx.error(
                        f"{func_name}() requires a type annotation",
                        stmt
                    )
                native_name = None
                if len(stmt.init.args) == 1:
                    if not isinstance(stmt.init.args[0], TpyStrLiteral):
                        raise self.ctx.error(
                            f"{func_name}() argument must be a string literal",
                            stmt
                        )
                    native_name = stmt.init.args[0].value
                elif len(stmt.init.args) > 1:
                    raise self.ctx.error(
                        f"{func_name}() takes 0 or 1 arguments",
                        stmt
                    )
                # Determine linkage from kwargs
                kw_binding = stmt.init.kwargs.get("binding")
                kw_array = stmt.init.kwargs.get("array")
                is_c_binding = (isinstance(kw_binding, TpyStrLiteral)
                                and kw_binding.value == "C")
                is_array = (isinstance(kw_array, TpyBoolLiteral)
                            and kw_array.value is True)
                if is_c_binding and is_array:
                    stmt.linkage = VarLinkage.NATIVE_C_ARRAY
                elif is_c_binding:
                    stmt.linkage = VarLinkage.NATIVE_C
                else:
                    stmt.linkage = VarLinkage.NATIVE
                if is_c_binding:
                    # Emitted verbatim as `extern "C" <type> <name>;` -- the
                    # array form emits the POINTEE as the element type, so
                    # that is what has to be spellable in C there.
                    checked = stmt.type
                    as_element = is_array and isinstance(checked, PtrType)
                    if as_element:
                        checked = checked.pointee
                    allowed = (is_c_abi_element_allowed(checked) if as_element
                               else is_c_abi_allowed(checked))
                    if not allowed:
                        kind = "element type" if as_element else "type"
                        raise self.ctx.error(
                            f"native_global '{stmt.name}': {kind} "
                            f"'{checked}' {C_ABI_TYPE_ERROR}; "
                            f"{c_abi_type_hint(checked, is_element=as_element)}",
                            stmt
                        )
                stmt.native_name = native_name
                stmt.init = None
                return

        # Handle `global x` declarations: treat as reassignment of the global variable
        is_global_declared = stmt.name in self.ctx.func.global_declarations
        if is_global_declared:
            existing_type = self.ctx.global_scope.lookup(stmt.name)
            if stmt.type is not None:
                raise self.ctx.error(
                    f"Cannot add type annotation to global variable '{stmt.name}' from inside a function",
                    stmt
                )
        else:
            # Check if this is a reassignment (variable already exists in scope)
            existing_type = self.ctx.func.current_scope.lookup(stmt.name)
            # Top-level typed globals are pre-registered before statement analysis.
            # For an earlier unannotated write to the same name, treat this as a
            # fresh local write and let a later annotation retro-validate history.
            is_preregistered_global_write = (
                self.ctx.is_top_level
                and stmt.type is None
                and stmt.init is not None
                and stmt.name not in self.ctx.func.current_scope.bindings
                and stmt.name in self.ctx.preregistered_globals
                and stmt.name not in self.ctx.func.authoritative_types
            )
            if is_preregistered_global_write:
                existing_type = None
            elif (existing_type is not None
                    and not self.ctx.is_top_level
                    and stmt.name in self.ctx.global_scope.bindings
                    and not self.compat.is_local_shadow(stmt.name)):
                # Inside a function a bare write binds a fresh LOCAL even when
                # the name is a module global (only `global x` writes the
                # module variable), so the global's type is not this write's
                # history. Same rule as the tuple-unpack arm.
                existing_type = None
            if existing_type is None:
                # A local first declared inside a loop BODY is out of the
                # enclosing scope here, so this reads as a fresh declaration --
                # but it is the SAME Python local, which carries ONE type and
                # ONE storage: the join of every binding, wherever it stands.
                # Promote it exactly as a READ after the loop does (same
                # declaration anchor, same assigned-ness rule) so this binding
                # goes down the ordinary reassignment path and joins there.
                # A loop VARIABLE is excluded: its type comes from the iterable
                # rather than from the user, so a later assignment must not be
                # forced to adopt the element type.
                pending = self.ctx.func.pending_loop_vars.get(stmt.name)
                if (pending is not None and pending.head_stmt is None
                        and self.expr._promote_pending_loop_var(stmt.name,
                                                                stmt)):
                    existing_type = self.ctx.func.current_scope.lookup(stmt.name)

        # Block reassignment of Final globals at module level
        # (inside functions, local shadowing is allowed)
        if self.ctx.is_top_level and not stmt.is_final and stmt.name in self.ctx.final_globals:
            raise self.ctx.error(
                f"Cannot reassign Final variable '{stmt.name}'",
                stmt
            )

        # Disallow reassignment of non-value-type params and loop vars
        if existing_type is not None:
            self._check_nonvalue_rebinding(stmt.name, stmt)

        init_type: TpyType | None = None
        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            ctor_td = generic_constructor_factory(stmt.init)
            is_generic_constructor = ctor_td is not None

            if (is_empty_literal or is_generic_constructor) and stmt.type:
                # Check if annotation matches the constructor's generic type
                annotation_matches = False
                # An empty `[]` can also target a recursive-union annotation
                # through its list member (e.g. `x: JsonValue = []`), not just
                # a direct list[T] annotation.
                union_list_member = None
                if ctor_td is not None:
                    annotation_matches = (
                        stmt.type.qualified_name() == ctor_td.qname)
                elif is_list(stmt.type):
                    annotation_matches = True
                elif stmt.type.needs_wrapper():
                    union_list_member = _find_list_member(stmt.type)
                    annotation_matches = union_list_member is not None

                if annotation_matches:
                    list_ann = stmt.type if is_list(stmt.type) else union_list_member
                    if list_ann is not None:
                        # list[T]: Use PendingListType for potential Array optimization
                        elem_type = list_ann.get_element_type()
                        # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                        if self.ctx.func.current_function is None:
                            # Global context: return ListType directly
                            init_type = make_list(elem_type)
                        else:
                            # Function-local context: create PendingListType
                            literal_id = self.ctx.literal_counter
                            self.ctx.literal_counter += 1
                            info = ListLiteralInfo(
                                literal_id=literal_id,
                                expr=stmt.init,
                                element_type=elem_type,
                                size=0,
                                is_global=self.ctx.is_top_level,
                                has_explicit_annotation=True,
                                explicit_type=list_ann
                            )
                            self.ctx.list_literals[literal_id] = info
                            self.ctx.func.pending_resolutions.append(literal_id)
                            init_type = PendingListType(elem_type, 0, literal_id)
                    else:
                        # Other generic types (Array, etc.): use annotation directly
                        init_type = stmt.type
                        # Set call_type so codegen knows the concrete template type
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                    # Cache expr_type since we bypassed _analyze_expr
                    self.ctx.set_expr_type(stmt.init, init_type)
                else:
                    func_name = stmt.init.func_name if is_generic_constructor else "[]"
                    raise self.ctx.error(
                        f"{func_name} requires matching type annotation, got {stmt.type}", stmt
                    )
            else:
                # The existing type hints a reassignment even for an inferred
                # local: it narrows float literals, types lambda parameters
                # and infers a generic call's T. Only a declared slot also
                # converts the value's ints into its floats.
                type_hint = stmt.type if stmt.type else existing_type
                if (type_hint is not None and stmt.type is None
                        and self.deduction.declared_slot_type(
                            stmt.name, existing_type) is None):
                    type_hint = SlotHint.inferred_local(type_hint)
                init_type = (
                    self._analyze_fresh_binding(stmt.init)
                    if type_hint is None and not is_global_declared
                    else self.expr.analyze_expr_with_hint(stmt.init, type_hint))

            # Unannotated top-level ALL_CAPS: now that the init type is known,
            # the Final-suggestion can name it (the annotated case warned above).
            if (self.ctx.is_top_level and not stmt.is_final and stmt.type is None
                    and init_type is not None):
                self._warn_all_caps_without_final(stmt.name, init_type, stmt)

            # Deferred Final constant check: runs after init analysis so that
            # @call_macro expansions are available via macro_expansion attr.
            if stmt.is_final:
                self.validate_compile_time_constant(
                    stmt.init, stmt.type, f"Final variable '{stmt.name}'", stmt,
                )
                self.ctx.analyzed_finals.add(stmt.name)

            # Track container literal to variable mapping for mutation/type inference.
            # Only bind when the init is an actual literal or empty constructor,
            # not a name reference (aliases are handled by register_list_alias).
            if isinstance(init_type, PENDING_CONTAINER_TYPES) and not isinstance(stmt.init, TpyName):
                self.ctx.track_container_variable(
                    stmt.name, init_type, stmt.loc.line if stmt.loc else None,
                )
                # List-specific: if explicit annotation is provided, record it
                if isinstance(init_type, PendingListType):
                    if stmt.type and isinstance(stmt.init, (TpyArrayLiteral, TpyListComprehension)):
                        info = self.ctx.list_literals[init_type.literal_id]
                        info.has_explicit_annotation = True
                        info.explicit_type = stmt.type

            # Track pending generic instance to variable mapping
            if isinstance(init_type, PendingGenericInstanceType):
                self.ctx.func.variable_to_generic_instance[stmt.name] = init_type.instance_id
                info = self.ctx.func.pending_generic_instances.get(init_type.instance_id)
                if info is not None:
                    info.variable_name = stmt.name
                    info.decl_line = stmt.loc.line if stmt.loc else None

            if stmt.type:
                if existing_type is not None:
                    self.deduction.check_conflicting_annotation(
                        stmt.name,
                        stmt.type,
                        stmt,
                        new_line=(stmt.loc.line if stmt.loc else None),
                    )
                    ann_line = stmt.loc.line if stmt.loc else None
                    self.deduction.retro_validate_against_annotation(stmt.name, stmt.type, annotation_line=ann_line)
                if is_char_literal_init(stmt.type, init_type, stmt.init):
                    pass  # the literal renders as a C++ char; no coercion exists
                else:
                    # Unwrap ReadonlyType for coercion -- readonly is tracked
                    # via type deduction, not the compatibility check.
                    inner_init = unwrap_readonly(init_type)
                    stmt.init = self.compat.coerce_expr(stmt.init, inner_init, stmt.type,
                                                         f"variable '{stmt.name}'",
                                                         coercion_ctx=CoercionContext.INIT)
                var_type = stmt.type
                # Inherit ReadonlyType from init expression
                if isinstance(init_type, ReadonlyType) and not var_type.is_value_type():
                    var_type = ReadonlyType(var_type)
                self.deduction.set_authoritative_annotation(
                    stmt.name,
                    stmt.type,
                    line=(stmt.loc.line if stmt.loc else None),
                )
            elif existing_type:
                # If RHS analysis triggered a retro-widen on this name
                # (literal-seeded local promoted to a fixed-int target by
                # any typed-slot use), the existing_type captured before
                # the RHS is stale -- refresh from the scope so the
                # reassignment's compat check sees the now-final declared
                # type.
                if (stmt.name in self.ctx.func.retro_widened_locs
                        and self.ctx.func.current_scope is not None):
                    fresh = self.ctx.func.current_scope.lookup(stmt.name)
                    if fresh is not None and fresh != existing_type:
                        existing_type = fresh
                prev_gen = unwrap_readonly(unwrap_ref_type(existing_type))
                if isinstance(prev_gen, ConcreteGenType):
                    # The name holds that one frame type; a rebind to it
                    # keeps the declared type (see _check_gen_rebind).
                    if stmt.name in self.ctx.func.current_nonlocal_names:
                        # The enclosing function's storage for the name
                        # cannot be re-seated from here, and replacing the
                        # object in place would pull it out from under a
                        # loop the enclosing body runs over it.
                        raise self.ctx.error(
                            f"cannot rebind generator '{stmt.name}' through "
                            f"'nonlocal': rebind it in the function that "
                            f"binds it, or bind the new generator to a new "
                            f"name",
                            stmt)
                    self._check_gen_rebind(stmt, prev_gen)
                    if stmt.unpack_target and self._binds_fresh_frame(stmt):
                        # The unpack builds the element in a temp and
                        # binds the name to it, which is not the in-place
                        # rebuild a rebind is.
                        raise self.ctx.error(
                            f"cannot rebind generator '{stmt.name}' in a "
                            f"tuple unpack: rebind it with an assignment of "
                            f"its own ('{stmt.name} = ...'), or unpack into a "
                            f"new name",
                            stmt)
                    self.ctx.func.frame_rebind_sites[stmt] = None
                    var_type = existing_type
                else:
                    var_type, stmt.init = self.compat.coerce_reassignment(
                        stmt.name, existing_type, init_type, stmt.init, stmt)
                if var_type != existing_type:
                    # Keep codegen and `# tpyc: type()` in sync with the
                    # original declaration when the resolved type changed.
                    resolved = unwrap_readonly(var_type)
                    orig_decl = self.ctx.func.var_decl_by_name.get(stmt.name)
                    if orig_decl:
                        self.ctx.var_types[orig_decl] = resolved
                    for key in self.ctx.declared_var_types:
                        if key[1] == stmt.name:
                            self.ctx.declared_var_types[key] = resolved
            else:
                # New variable: resolve IntLiteralType/FloatLiteralType.
                if isinstance(init_type, IntLiteralType):
                    var_type = self.ctx.default_int_for_literal(init_type, warn_node=stmt.init)
                    self.ctx.func.literal_default_vars.add(stmt.name)
                elif isinstance(init_type, FloatLiteralType):
                    var_type = FLOAT  # float literals always default to float64
                # Preserve OwnType on variables -- Own[T] indicates the variable
                # owns its storage and can be moved at last use.
                # Exceptions: union types need the raw type for isinstance/
                # narrowing codegen; optional pointer types use T* storage.
                elif isinstance(init_type, OwnType):
                    inner = init_type.wrapped
                    if (isinstance(inner, UnionType)
                            or (isinstance(inner, OptionalType) and inner.uses_pointer_repr())):
                        var_type = inner
                    else:
                        var_type = init_type
                # None literal without annotation -- can't infer the Optional type
                elif isinstance(init_type, NoneType):
                    var_type = init_type
                    self.ctx.func.unresolved_none_vars.add(stmt.name)
                else:
                    var_type = init_type
                # Resolve IntLiteralType nested inside TupleType / Array / list
                # so `t = (1, 2)` records `tuple[int32, int32]` rather than
                # `tuple[IntLiteral(1), IntLiteral(2)]`. Codegen already lowers
                # the storage to concrete types via downstream passes; this
                # aligns sema's view so type queries return the same answer.
                var_type = resolve_int_literals(var_type, self.ctx.default_int_for_literal)
            # Strip OwnType from init_type: ownership of the source variable
            # doesn't transfer to the target. The target determines its own
            # ownership via the OwnType wrapping logic below (line ~2570).
            # EXCEPTION: an owned @dynamic-protocol rvalue (async-def call
            # result / Own[P]-returning call) binds as an owned-erased
            # local (unique_ptr<P>) -- a bare protocol local would be a
            # borrow of a temporary.
            if isinstance(var_type, OwnType):
                inner = unwrap_readonly(var_type.wrapped)
                if not (is_protocol_type(inner)
                        and (pi := protocol_info_of(inner)) is not None
                        and pi.is_dynamic):
                    var_type = var_type.wrapped
            elif (is_protocol_type(var_type)
                    and (pi := protocol_info_of(var_type)) is not None
                    and pi.is_dynamic
                    and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                    and getattr(stmt.init.resolved_function_info, "is_async", False)):
                # Bare async-def call: the C++ value is the concrete owned
                # coro struct. Keep the concreteness in the type (zero-alloc
                # representation); template-frame callees fall back to the
                # erased handle.
                var_type = OwnType(self._concrete_coro_bind_type(
                    stmt.init, var_type))
            # A binding that is still BARE Cancellable after the owned-wrap
            # above is a borrow of a coroutine (ternary over handles,
            # protocol-annotated alias) -- no supported form; handles are
            # single-use and consume-only.
            bare_bind = unwrap_readonly(unwrap_ref_type(var_type))
            if (is_protocol_type(bare_bind)
                    and isinstance(bare_bind, NominalType)
                    and bare_bind.qualified_name() == qnames.CANCELLABLE):
                raise self.ctx.error(
                    f"cannot bind '{stmt.name}' as a borrowed coroutine "
                    f"reference: coroutine handles are single-use; bind the "
                    f"async call directly (the binding owns the handle) and "
                    f"consume it via await / asyncio.create_task/run",
                    stmt)
            # Rebind rules: a concrete slot holds exactly one frame type;
            # an erased slot (unique_ptr) accepts any coroutine, so a
            # rebind into it keeps the erased representation.
            if isinstance(var_type, OwnType) and existing_type is not None:
                prev_inner = unwrap_readonly(unwrap_own(
                    unwrap_ref_type(existing_type)))
                new_inner = unwrap_readonly(var_type.wrapped)
                prev_is_coro = (isinstance(prev_inner, NominalType)
                                and prev_inner.qualified_name() == qnames.CANCELLABLE)
                if isinstance(prev_inner, ConcreteCoroType):
                    # The reassignment merge resolves var_type to the
                    # existing binding's type, so derive the INCOMING
                    # frame identity from the init expr itself.
                    cand: TpyType | None = new_inner
                    init_i = stmt.init
                    if isinstance(init_i, TpyCoerce):
                        init_i = init_i.expr
                    if (isinstance(init_i, (TpyCall, TpyMethodCall))
                            and getattr(init_i.resolved_function_info,
                                        "is_async", False)):
                        cand = self._concrete_coro_bind_type(
                            init_i, make_cancellable(prev_inner.type_args[0]))
                    elif isinstance(init_i, TpyName):
                        src_t = self.ctx.func.current_scope.lookup(init_i.name)
                        if src_t is not None:
                            cand = unwrap_readonly(unwrap_own(
                                unwrap_ref_type(src_t)))
                    self._check_one_frame_type(stmt, prev_inner, cand)
                elif prev_is_coro and isinstance(new_inner, ConcreteCoroType):
                    # Erased slot: materialize the concrete value into it.
                    var_type = OwnType(prev_inner)
            # A generator object's frame type, recorded on the decl for the
            # same reason: the init expr is typed as the bare protocol.
            if isinstance(unwrap_readonly(unwrap_ref_type(var_type)),
                          ConcreteGenType):
                self.ctx.var_types[stmt] = var_type
                if existing_type is not None:
                    self._reject_generator_end_under_loop(
                        stmt.name, stmt, "rebind",
                        "bind the new generator to a new name")
            if holds_frame_object(var_type):
                self._record_frame_binding(stmt)
            # Record the owned binding on the decl node so codegen reads
            # Own[...] instead of re-deriving the bare protocol from the
            # init expr (mirrors the collapsed-tuple recording below).
            if isinstance(var_type, OwnType):
                self.ctx.var_types[stmt] = var_type
                if stmt.name in self.ctx.func.unread_coro_locals:
                    self.ctx.warning(
                        f"rebinding '{stmt.name}' drops the previous "
                        f"coroutine without running it",
                        stmt)
                self.ctx.func.unread_coro_locals[stmt.name] = stmt
                # Binding from a name moves the handle out of the source
                # (coroutine handles are single-use; CPython aliases, TPy
                # moves -- declared divergence). Reject when the source is
                # used again; otherwise mark it consumed.
                if isinstance(stmt.init, TpyName):
                    self.ctx.check_coro_move_unstarted(
                        stmt.init, f"into '{stmt.name}'")
                    if not self.compat.is_auto_move_use(stmt.init):
                        raise self.ctx.error(
                            f"binding '{stmt.name}' moves the coroutine out of "
                            f"'{stmt.init.name}' (coroutine handles are "
                            f"single-use); '{stmt.init.name}' is used again "
                            f"later",
                            stmt)
                    self.ctx.func.consumed_vars.add(stmt.init.name)
            # Preserve Ref on non-reassigned function locals from reference
            # sources (call returns, field access, subscript, params).
            # Strip for: reassigned locals (T* codegen), top-level globals.
            if (stmt.name in self.ctx.func.current_reassigned_vars
                    or self.ctx.is_top_level):
                var_type = unwrap_ref_type(var_type)
            # A reassigned per-element-Own tuple local must model the unified
            # borrow type, not owning storage (else an alias rebind copies
            # where CPython aliases). Recorded on the decl node so codegen
            # reads the collapsed type instead of re-deriving Own from init.
            if stmt.name in self.ctx.func.current_reassigned_vars:
                collapsed = collapse_tuple_own_elements(var_type)
                if collapsed is not var_type:
                    var_type = collapsed
                    self.ctx.var_types[stmt] = var_type
            # Track inferred writes for potential future retro-validation.
            self.deduction.record_write(stmt.name, stmt.init, init_type)
            # Annotate tuple literal element capture modes (local context)
            if isinstance(stmt.init, TpyTupleLiteral) and isinstance(var_type, TupleType):
                self._annotate_tuple_elem_capture(stmt.init, var_type)
        elif stmt.type:
            if isinstance(stmt.type, OptionalType):
                # Optional without initializer is allowed (defaults to None/nullptr)
                var_type = stmt.type
            elif not stmt.type.is_value_type():
                raise self.ctx.error(
                    f"Variable '{stmt.name}' of type '{stmt.type}' must have an initializer",
                    stmt
                )
            else:
                var_type = stmt.type
            self.deduction.set_authoritative_annotation(
                stmt.name,
                stmt.type,
                line=(stmt.loc.line if stmt.loc else None),
            )
        else:
            raise self.ctx.error(f"Variable '{stmt.name}' has no type annotation and no initializer", stmt)

        # Deferred type inference for new locals (PendingViewType, list alias, etc.)
        if not is_global_declared and existing_type is None:
            var_type = self._infer_new_local_type(
                stmt.name, var_type, stmt.init, init_type,
                line=(stmt.loc.line if stmt.loc else None),
                annotated=stmt.type is not None,
            )
            if isinstance(var_type, PendingViewType):
                stmt.type = var_type

        if is_global_declared:
            # Update global scope type; bind in current scope for local reads
            self.ctx.global_scope.define(stmt.name, var_type)
            self.ctx.func.current_scope.define(stmt.name, var_type)
        else:
            self.ctx.func.current_scope.define(stmt.name, var_type)
        if self.ctx.is_module_slot_stmt(stmt):
            if stmt.init is not None:
                # The annotation (pre-registered before this statement) does
                # not spell which tuple elements the global owns; its init
                # does, and every reader of the binding needs that recorded.
                bound = self._tuple_global_binding_type(
                    var_type, stmt.init, init_type)
                if bound is not var_type:
                    var_type = bound
                    self.ctx.var_types[stmt] = var_type
                    self.ctx.func.current_scope.define(stmt.name, var_type)
            # An INFERRED module-slot binding is a global exactly as an
            # annotated one is; `global_scope` is the table the export
            # collection and the storage-durability checks read, so a binding
            # left only in the module-init scope is invisible to both.
            self.ctx.define_module_global(
                stmt.name, var_type, stmt.loc.line if stmt.loc else 0)
        # Reassignment revives a consumed variable
        self.ctx.func.consumed_vars.discard(stmt.name)
        # Flag the local if it's bound to a tuple literal with a FRESH
        # non-value member (would dangle at a later bare-name yield/return);
        # clears on any other reassignment.
        self.compat.update_tuple_member_local_facts(stmt.name, var_type, stmt.init)
        self._update_ephemeral_alias_fact(stmt.name, var_type, stmt.init)
        # Reassigning a loop variable prevents const-ref binding
        if existing_type is not None:
            self.ctx.mark_loop_var_mutated(stmt.name)
        # A non-value local bound off a loop var takes a MUTABLE borrow out of
        # it (`&(var)` / `T&` in codegen), so the loop var cannot bind const.
        # The source may be the loop var itself or any field / element chain
        # rooted at it (`x = h.b`): same borrow, so the same root walker the
        # tuple unpack's element-borrow edge uses answers it.
        if (stmt.init is not None
                and var_type is not None and not var_type.is_value_type()):
            init_root = _root_name_of_expr(stmt.init)
            if init_root is not None:
                self.ctx.mark_loop_var_mutated(init_root)
        # Borrow tracking: reassignment breaks aliases in both directions.
        # `retarget_storage_borrows` runs before `remove_borrower` so that
        # borrowers of `name` get re-pointed to `name`'s former upstream
        # (with kind promoted to the most-restrictive in the chain) rather
        # than silently dropped. Required for chains through reassigned vars
        # (e.g. `view = s; s = items[1]`) to keep tracking the source.
        self.ctx.mark_all_view_borrowers_mutated(stmt.name)
        _handle_pinned_view_rebind(self.ctx, stmt.name, stmt)
        bt = self.ctx.func.borrow_tracker
        stamp_bind_kind(self.ctx, stmt, stmt.name, stmt.init, var_type,
                        rebind=existing_type is not None)
        bt.rebind_borrower(stmt.name, stmt.init)
        # Bound async-METHOD coroutine: stable-lvalue receiver + borrow
        # registration. Keyed on the init shape alone so every binding
        # form (fresh, annotated-erased, rebind) is covered, and placed
        # after remove_borrower so the fresh borrow survives.
        _coro_init = (stmt.init.expr if isinstance(stmt.init, TpyCoerce)
                      else stmt.init)
        if (isinstance(_coro_init, TpyMethodCall)
                and getattr(_coro_init.resolved_function_info,
                            "is_async", False)
                # `mod.f()` is a TpyMethodCall whose receiver is a
                # namespace, not a value -- nothing to borrow.
                and _coro_init.user_module_call is None
                and _coro_init.builtin_module_call is None):
            self._bind_method_coro_receiver(stmt.name, _coro_init)
        # Create borrow when the target aliases another variable's storage.
        # Reassigned vars register too: codegen uses T* pointer-locals, so
        # `view = s` aliases the storage `s` currently points into; the
        # retarget logic above keeps the chain valid across reassignments.
        if (stmt.init is not None
                and var_type is not None):
            _register_tuple_binding_borrows(
                self.ctx, stmt.name, stmt.init, var_type)
            if not var_type.is_value_type():
                # Non-value lvalue: y = x, v = items[i], v = obj.field
                register_binding_borrow(self.ctx, stmt.name, stmt.init)
            elif isinstance(var_type, PtrType):
                # take_ptr(x) / Ptr(x) borrows x's storage even though Ptr is a value type
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                if isinstance(init_inner, TpyCall) and len(init_inner.args) > 0:
                    is_ptr_ctor = (init_inner.call_type is not None
                                   and init_inner.call_type.is_pointer())
                    fi = init_inner.resolved_function_info
                    is_vpc = fi is not None and fi.value_ptr_coercion
                    if is_ptr_ctor or is_vpc:
                        root = _borrow_storage_root(init_inner.args[0])
                        if root is not None:
                            bt.add_borrow(root, stmt.name, BorrowKind.PTR)
                # Direct value-to-Ptr coercion (`pp: Ptr[T] = x`): same borrow
                # semantics as the take_ptr / Ptr(x) explicit forms. The mutation
                # signal is already propagated in coerce_expr; this block adds
                # the borrow registration so warn_borrow_* diagnostics fire.
                if isinstance(stmt.init, TpyCoerce) and stmt.init.coercion.name in (
                        "record_to_ptr", "record_to_const_ptr",
                        "upcast_to_ptr", "upcast_to_const_ptr"):
                    root = _borrow_storage_root(stmt.init.expr)
                    if root is not None:
                        bt.add_borrow(root, stmt.name, BorrowKind.PTR)
            elif is_span(var_type):
                # Span from slicing borrows the source container.
                # (StrView/BytesView slices are handled in their own branch below.)
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                if isinstance(init_inner, TpySubscript) and isinstance(init_inner.index, TpySlice):
                    root = _borrow_storage_root(init_inner)
                    if root is not None:
                        bt.add_borrow(root, stmt.name, BorrowKind.ELEMENT)
            elif is_str_view_type(var_type) or is_bytes_view_type(var_type):
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                # An explicit view annotation bound to a temporary dangles: the
                # backing storage dies at end-of-statement. Reject (an owned
                # str/bytes annotation copies; an inferred local would promote).
                # Locals only: a module global outlives the function, and a
                # Final[str] constant is constexpr/static view storage.
                if (not self.ctx.is_top_level and not stmt.is_final
                        and view_source_is_temporary(init_inner)):
                    raise self.ctx.error(
                        f"Cannot bind {var_type} '{stmt.name}' to a temporary view "
                        f"source; the backing storage is destroyed at "
                        f"end-of-statement -- annotate '{stmt.name}' as an owned "
                        f"str/bytes to keep a copy",
                        stmt,
                    )
                # A view from slicing a stable source borrows it: register the
                # element borrow so a later in-place mutation of the source
                # warns -- `s += ...` reallocates str's std::string buffer (and
                # a bytearray resizes) just like a container, invalidating the
                # view. Mirrors the Span-slice branch above.
                if isinstance(init_inner, TpySubscript) and isinstance(init_inner.index, TpySlice):
                    root = _borrow_storage_root(init_inner)
                    if root is not None:
                        bt.add_borrow(root, stmt.name, BorrowKind.ELEMENT)
                # Pinned view annotation aliasing a name source: register so
                # source reassignment warns. Pending views handle this via
                # source_mutated fall-back; the explicit annotation can't
                # fall back so we warn at the mutation site instead.
                if isinstance(init_inner, TpyName):
                    self.ctx.func.pinned_view_aliases.setdefault(
                        init_inner.name, set()).add(stmt.name)
        # 8b: Register call result borrow for ALL assignments (including reassignments).
        # Unlike general alias tracking, borrow contracts use precise return_borrows_from
        # facts and don't need pointer-alias analysis -- safe to apply to reassigned vars.
        # Views are value types in C++ but still carry a reference into foreign
        # storage, so a view local borrows its source exactly like a non-value
        # local does -- include them so `v = a.strip()` registers the receiver
        # borrow (drives the temp-receiver warning and the mutate-while-borrowed
        # check on a later `a += ...`).
        # A str/bytes local's storage is the deduction's answer, not this
        # site's: one that will be a VIEW borrows its source exactly as a
        # spelled `StrView` does, and one that will OWN copies and borrows
        # nothing. `view_storage_verdict` is None for every other type, which
        # then answers for itself.
        borrow_storage = (self.ctx.view_storage_verdict(unwrap_readonly(var_type))
                          or var_type) if var_type is not None else None
        if (stmt.init is not None
                and borrow_storage is not None
                and (not borrow_storage.is_value_type()
                     or is_borrowing_view_type(unwrap_readonly(borrow_storage)))):
            init_unwrapped = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
            _register_call_result_borrow(self.ctx, stmt.name, init_unwrapped)
            # A non-const local alias of an @auto_readonly accessor result needs a
            # mutable source binding (locals bind non-const references by default).
            # Keep the receiver mutable so `x = o.b.get()` compiles whether x is
            # later read or written; the eager-mark suppression only buys a const
            # receiver for the direct, un-aliased read (`return o.b.get().v`).
            if _is_borrowing_auto_readonly_accessor(init_unwrapped):
                recv_root = _root_name_of_expr(init_unwrapped.obj)
                if recv_root is not None:
                    self.ctx.mark_param_mutated(recv_root)
        # Reassigned non-value locals generate T* local = &(source) in C++.
        # Mark source param as mutated so it stays T& (not const T&), regardless
        # of whether the borrow-tracking block above ran.
        if (stmt.init is not None
                and stmt.name in self.ctx.func.current_reassigned_vars
                and var_type is not None
                and not var_type.is_value_type()):
            for alias_root in addr_taken_roots(stmt.init):
                self.ctx.mark_param_mutated(alias_root)
        if stmt.init:
            self.init.mark_assigned(stmt.name)
        self.narrowing.update_after_write(stmt.name, var_type, init_type if stmt.init else None, stmt.init)
        # Union assignment narrowing: narrow to concrete member on initial declaration
        if existing_type is None:
            inner_var = unwrap_readonly(var_type)
            if (isinstance(inner_var, UnionType) and init_type is not None
                    and init_type in inner_var.members):
                self.ctx.func.narrowed_types[stmt.name] = init_type
                facts = {stmt.name: init_type}
                stmt.then_type_facts = self._filter_union_codegen_facts(facts)
        # Record scope depth for new variables (not reassignments of outer-scope
        # vars). Uses scope lookup rather than var_scope_depth existence, so that
        # stale entries from discarded inner scopes get overwritten correctly.
        #
        # Note: when an outer-scoped variable is reassigned with an rvalue inside
        # an inner scope (e.g. `p = Point()` in a loop body where `p` was declared
        # outside), the depth stays at the outer scope. This is safe because the
        # codegen uses a rebind slot at the declaration scope for rvalue rebinds.
        if existing_type is None:
            self.ctx.func.var_scope_depth[stmt.name] = self.ctx.func.current_scope.depth
        # Update rvalue status for hoist eligibility (both new vars and reassignments)
        if stmt.init:
            if self.compat.is_lvalue(stmt.init):
                # Move-through: lvalue alias at last use of source promotes to rvalue.
                # Both target and source must be non-reassigned Tier 1 locals
                # (reassigned vars become T* pointer-locals in codegen).
                if (isinstance(stmt.init, TpyName)
                        and self._captured_by_live_closure(stmt, stmt.init.name)):
                    # A closure that may still be called reads the source by
                    # reference: the bind aliases it, whatever the last-use
                    # walk says.
                    self.ctx.all_last_uses.discard(stmt.init)
                if (isinstance(stmt.init, TpyName)
                        and existing_type is None
                        and stmt.name not in self.ctx.func.current_reassigned_vars
                        and stmt.init.name not in self.ctx.func.current_reassigned_vars
                        and self.compat.is_auto_move_use(stmt.init)
                        and var_type is not None
                        and not var_type.is_value_type()
                        and not (isinstance(var_type, OptionalType) and var_type.uses_pointer_repr())
                        and not is_protocol_union(var_type)
                        and not holds_generator_object(var_type)):
                    self.ctx.func.rvalue_vars.add(stmt.name)
                    note_owned_local(self.ctx, stmt.name, var_type)
                    self.ctx.func.move_through_vars.add(stmt.name)
                else:
                    self.ctx.func.rvalue_vars.discard(stmt.name)
                    self.ctx.func.owned_locals.discard(stmt.name)
                    record_stmt_borrow_binding(self.ctx, stmt.name, var_type, stmt.init)
                    if existing_type is not None:
                        self._bar_unscanned_lvalue_reassign(stmt.name, stmt.init)
            else:
                self.ctx.func.rvalue_vars.add(stmt.name)
                # Owned only when the init genuinely creates a value (constructor,
                # Own return, literal, op). A borrow-producing init that codegen
                # renders as a `T&` alias -- a reference-returning call, or a
                # ternary/and-or of reference lvalues -- must NOT be owned, or a
                # later move-out would corrupt the aliased source. Same predicate
                # codegen uses for the `T&`-vs-value rendering (value_category).
                if is_rvalue_source(self.ctx, stmt.init):
                    note_owned_local(self.ctx, stmt.name, var_type)
                    # Fresh-ctor local: a non-reassigned local whose sole binding
                    # is a constructor call of its exact static type. Its dynamic
                    # type is then provably its static type, so the
                    # polymorphic-slicing guard may move it into an owned poly slot
                    # (Box[P]) like a fresh-rvalue ctor at the store.
                    # current_reassigned_vars comes from a whole-function prescan
                    # (all branches, flat `declared` set), so ANY later rebind --
                    # in this or any other branch -- excludes the name here. That
                    # is why the set needs no per-branch save/restore: a name that
                    # could hold a subclass value on some path was never added.
                    # var_type == init_type excludes a widening annotation.
                    if (stmt.name not in self.ctx.func.current_reassigned_vars
                            and isinstance(stmt.init, TpyCall)
                            and isinstance(stmt.init.func, TpyName)
                            and self.ctx.registry.get_record(stmt.init.func.name) is not None
                            and init_type is not None and var_type == init_type):
                        self.ctx.func.current_fresh_ctor_locals.add(stmt.name)
                    else:
                        self.ctx.func.current_fresh_ctor_locals.discard(stmt.name)
                else:
                    self.ctx.func.owned_locals.discard(stmt.name)
                    record_stmt_borrow_binding(self.ctx, stmt.name, var_type, stmt.init)
                    # A reassignment to a borrow source makes the var an alias;
                    # bar it from the function-wide movable set (ever_owned
                    # would otherwise keep a once-owned local movable, and an
                    # auto-move at its last use would steal from the source).
                    if existing_type is not None:
                        self.ctx.func.borrow_reassigned_vars.add(stmt.name)
        if stmt.init and _needs_provenance_tracking(var_type):
            self.init.mark_provenance(stmt.name, self.compat.is_param_derived_expr(stmt.init))
            self.init.mark_safe_to_return(
                stmt.name, self.compat.is_safe_to_return_expr(stmt.init))
        # Track non-null pointer provenance for null-check elision
        if stmt.init and isinstance(var_type, PtrType):
            is_non_null = expr_yields_non_null_ptr(stmt.init, self.ctx.func.non_null_ptr_vars)
            self.init.mark_non_null_ptr(stmt.name, is_non_null)

        # Scope escape check for variable declarations (new and reassignment)
        if stmt.init and not var_type.is_value_type():
            self.scopes.check_escape(stmt.name, stmt.init, stmt)
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.bind_variable(stmt.name, var_type)
        # Track first var_decl for later type updates on reassignment-driven inference.
        if existing_type is None:
            self.ctx.func.var_decl_by_name[stmt.name] = stmt
        elif var_type is not None:
            self.expr.sync_pending_loop_var_type(stmt.name, var_type)
        # Record declared type for test type-annotation validation.
        # Strip Own[T] and Ref[T] for display -- internal annotations, not user-facing.
        if stmt.loc:
            display_type = unwrap_ref_type(unwrap_own(var_type)) if var_type else var_type
            self.ctx.declared_var_types[(stmt.loc.line, stmt.name)] = display_type

    def _analyze_tuple_unpack(self, stmt: TpyTupleUnpack) -> None:
        """Analyze tuple unpacking: a, b = expr."""
        rhs_type = self.expr.analyze_expr(stmt.value)
        rhs_check = rhs_type.wrapped if isinstance(rhs_type, OwnType) else rhs_type

        if not isinstance(rhs_check, TupleType):
            raise self.ctx.error(
                f"Cannot unpack non-tuple type {rhs_type}", stmt)

        rhs_type = rhs_check
        n_targets = len(stmt.targets)
        n_elems = len(rhs_type.element_types)
        if n_targets != n_elems:
            raise self.ctx.error(
                f"Cannot unpack tuple of {n_elems} elements into "
                f"{n_targets} targets", stmt)

        # Codegen binds the source tuple by-ref (zero-copy, elements alias the
        # live source) ONLY for a plain stable Name with no owned element -- it
        # must stay paired with codegen's bind-by-ref test in `_gen_tuple_unpack`
        # (`isinstance(stmt.value, TpyName) and not any(stmt.is_owned)`). Every
        # other source materializes an owned `__tup` temporary, whose owned
        # str/bytes member a VIEW target dangles into once it outlives the temp
        # (e.g. a loop-reassigned `head, tail = split(head)`) -- flag it owned
        # below, the chokepoint the scalar `s = owned()` reassign uses.
        has_owned_elem = any(isinstance(et, OwnType) for et in rhs_type.element_types)
        source_binds_by_ref = isinstance(stmt.value, TpyName) and not has_owned_elem

        # An `Own` element is MOVED out only when the unpack consumes its
        # source: a fresh rvalue (a call, a literal, the per-iteration holder
        # of a loop head), or a name at its last use -- the same verdict the
        # scalar `x = t` decl takes off an owning local. A name read again
        # afterwards is borrowed, so its `Own` element target is an alias of
        # the source's element storage (`is_ref`), exactly as the scalar
        # binds `T& x = t`; deriving ownership from the element's declared
        # `Own` alone made the lowering copy the whole tuple to have
        # something to move from, and the write through the target was lost
        # (PITFALLS `tuple-equals-scalar`). A module-level NAME source is a
        # global, which a function body may read after module init, so it is
        # never consumed -- module-level last-use is blind to those reads;
        # the pointer-slot target aims into the tuple global's static storage
        # instead, as the scalar global `x = T` is a pointer copy.
        source_consumed = (
            stmt.is_loop_head
            or not isinstance(stmt.value, TpyName)
            or (not self.ctx.is_top_level
                and self.compat.is_auto_move_use(stmt.value)))
        if (self.ctx.is_top_level and isinstance(stmt.value, TpyName)
                and has_owned_elem):
            self.ctx.tuple_globals_aliased.add(stmt.value.name)

        # Per-element expressions for narrowing (range facts, etc.)
        has_elem_exprs = isinstance(stmt.value, TpyTupleLiteral)
        for i, name in enumerate(stmt.targets):
            elem_type = rhs_type.element_types[i]
            elem_expr = stmt.value.elements[i] if has_elem_exprs and i < len(stmt.value.elements) else None
            owned = isinstance(elem_type, OwnType) and source_consumed
            stmt.is_owned.append(owned)
            if isinstance(elem_type, OwnType):
                elem_type = elem_type.wrapped
            is_ref = (not owned and not elem_type.is_value_type()
                      and not isinstance(elem_type, TypeParamRef))
            stmt.is_ref.append(is_ref)
            stmt.is_rebound.append(
                is_ref and name is not None
                and name in (stmt.loop_body_rebinds if stmt.is_loop_head
                             else self.ctx.func.current_reassigned_vars))

            stmt.target_types.append(elem_type)

            if name is None:
                stmt.is_new.append(True)
                continue

            if self.ctx.is_module_slot_stmt(stmt):
                # Block reassignment of Final globals at module level
                if name in self.ctx.final_globals:
                    raise self.ctx.error(
                        f"Cannot reassign Final variable '{name}'",
                        stmt)
                # At module level, targets become globals with namespace-scope
                # definitions. Mark is_new=False so codegen emits assignment
                # (the declaration is handled by gen_global_decl).
                # Resolve IntLiteralType so the global's declared type aligns
                # with the int32_t storage codegen emits (sema/codegen parity).
                elem_type = resolve_int_literals(elem_type, self.ctx.default_int_for_literal)
                self.ctx.define_module_global(
                    name, elem_type, stmt.loc.line if stmt.loc else 0)
                self.ctx.func.current_scope.define(name, elem_type)
                self.ctx.func.nonstmt_bound_names.add(name)
                self.init.mark_assigned(name)
                self.narrowing.update_after_write(name, elem_type, elem_type, elem_expr)
                if self.ctx.func.current_ns:
                    self.ctx.func.current_ns.bind_variable(name, elem_type)
                self._warn_all_caps_without_final(name, elem_type, stmt)
                if stmt.loc:
                    display_type = unwrap_own(elem_type) if elem_type else elem_type
                    self.ctx.declared_var_types[(stmt.loc.line, name)] = display_type
                stmt.is_new.append(False)
                continue

            existing = self.ctx.func.current_scope.lookup(name)
            # Don't treat globals as existing unless explicitly declared
            # with 'global' -- unpack should create locals by default. Same
            # fresh-local rule (and same shadow predicate) as the scalar
            # var-decl arm, which must not disagree about a name already
            # bound in an enclosing BLOCK scope of this function.
            if (existing is not None
                    and name not in self.ctx.func.global_declarations
                    and not self.compat.is_local_shadow(name)
                    and name in self.ctx.global_scope.bindings):
                existing = None
            if existing is not None:
                self._check_nonvalue_rebinding(name, stmt)
                self.deduction.refuse_int_float_rebind(
                    name, existing, elem_type, elem_expr, site=stmt)
                self.compat.check_type_compatible(
                    elem_type, existing, "tuple unpacking", source_expr=stmt)
                self.narrowing.update_after_write(name, existing, elem_type, elem_expr)
                # Mirror the scalar var-decl, which marks assigned on every
                # init path (statements.py `if stmt.init: mark_assigned`): a
                # branch that reassigns a target the sibling branch declared
                # must re-mark it so merge_branches keeps it definitely
                # assigned (drives if/else predecl pre-declaration).
                self.init.mark_assigned(name)
                stmt.is_new.append(False)
            else:
                elem_type = self._infer_new_local_type(
                    name, elem_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                # Commit IntLiteralType to default_int_type at the new-local
                # binding site -- otherwise codegen emits invalid C++ like
                # `1 a = std::get<0>(...)` since IntLiteralType.to_cpp()
                # returns the literal value, not a type. Applied here (not in
                # _infer_new_local_type) so for-loop var binding still leaves
                # IntLiteralType in place: `for v in [...]: f(v)` where f
                # takes BigInt needs that flexibility (heapq pattern).
                elem_type = resolve_int_literals(elem_type, self.ctx.default_int_for_literal)
                stmt.target_types[i] = elem_type
                self.ctx.func.current_scope.define(name, elem_type)
                self.ctx.func.nonstmt_bound_names.add(name)
                # Record scope depth so the definite-assignment read-check sees
                # the target (mirrors the scalar var-decl): a loop-body-only
                # target used after the loop is rejected, not miscompiled.
                self.ctx.func.var_scope_depth[name] = self.ctx.func.current_scope.depth
                # Bind into the codegen namespace too (mirrors _analyze_var_decl
                # and the top-level unpack branch); the resumable-frame hoist
                # reads `current_ns.all_bindings()`, so a target left only in
                # `current_scope` vanishes across an await/yield suspension.
                if self.ctx.func.current_ns:
                    self.ctx.func.current_ns.bind_variable(name, elem_type)
                self.init.mark_assigned(name)
                if self.deduction.tuple_target_view_family(name) is not None:
                    self.ctx.func.tuple_unpack_view_targets.add(name)
                self.narrowing.update_after_write(name, elem_type, elem_type, elem_expr)
                stmt.is_new.append(True)
                # An Own[T] element is moved out of the source tuple, so the
                # fresh target is an owned movable local -- same status as a
                # single-assign owned rvalue (`x = make_one()`), registered the
                # same way. Narrower than the single-assign path on purpose:
                # gated to non-reassigned targets, since a reassigned target
                # becomes a T* pointer-local with different movability.
                if owned and name not in self.ctx.func.current_reassigned_vars:
                    self.ctx.func.rvalue_vars.add(name)
                    note_owned_local(self.ctx, name, elem_type)
            # Promote a str/bytes view target to owned when it binds an owned-temp
            # tuple member AND is reassigned -- a reassigned target is declared in
            # a scope broader than the per-statement/loop-body `__tup`, so a view
            # into it dangles. A fresh same-scope target keeps the view (the named
            # `__tup` outlives it -- zero-copy, safe). Mirrors the scalar
            # `s = owned()` reassign chokepoint. The other "outlives __tup" shape
            # -- a target first-declared in a block and used AFTER it -- is
            # owned where it is hoisted, by the hoist rule
            # (`deduction.own_hoisted_view`).
            if (not source_binds_by_ref
                    and name in self.ctx.func.current_reassigned_vars):
                fam = self.deduction.tuple_target_view_family(name)
                if fam is not None:
                    self.deduction.mark_view_reassigned_from_owned(name, fam)
            fam = self.deduction.tuple_target_view_family(name)
            tgt_id = self.ctx.view_var_map(fam).get(name) if fam else None
            if fam is not None and tgt_id is not None:
                # A per-statement capture (call / rvalue source) has no name
                # the hoist rule can place; a NAME source is that tuple.
                self.deduction.note_view_binding(
                    fam, tgt_id, stmt.value if source_binds_by_ref else None,
                    replace=True)
            # A view target off a NAME source views that tuple's own element,
            # exactly as `a = t[i]` does, so it registers the same source
            # storage and a reseat of the tuple demotes it to owned.
            if (source_binds_by_ref and not stmt.is_loop_head
                    and not self.ctx.is_top_level):
                fam = self.deduction.tuple_target_view_family(name)
                src_id = self.ctx.view_var_map(fam).get(name) if fam else None
                if fam is not None and src_id is not None:
                    bt = self.ctx.func.borrow_tracker
                    self.deduction.register_view_source_storage(
                        fam, src_id, canonical_storage_key(
                            bt, bt.effective_storage(stmt.value.name)))
            if stmt.loc:
                display_type = unwrap_own(elem_type) if elem_type else elem_type
                self.ctx.declared_var_types[(stmt.loc.line, name)] = display_type

        # Warn when a tuple-LITERAL unpack copies a reference-type lvalue
        # element: CPython aliases the element, but the value-tuple
        # materialization here copies it -- a silent value-vs-reference
        # divergence. The parser desugars flat tuple-literal unpacks to
        # aliasing single-assigns (function-body and module top level), so
        # they never reach this handler; this remains a guardrail for the
        # macro-built path (macro_api.tuple_unpack constructs TpyTupleUnpack
        # directly, bypassing the desugar). Fresh rvalue elements (calls,
        # constructors) are correctly copied, so only lvalue elements
        # (name / subscript / field) warn.
        if isinstance(stmt.value, TpyTupleLiteral):
            for i, name in enumerate(stmt.targets):
                if name is None or not stmt.is_ref[i] or i >= len(stmt.value.elements):
                    continue
                elem = stmt.value.elements[i]
                if isinstance(elem, TpyCoerce):
                    elem = elem.expr
                if isinstance(elem, (TpyName, TpySubscript, TpyFieldAccess)):
                    self.ctx.warning(
                        f"tuple-literal unpack copies reference-type element "
                        f"'{name}' instead of aliasing it (CPython aliases); "
                        f"bind it on its own line to alias", stmt)

        # Register element-borrow edges so a later mutation through an
        # unpacked target (a, b = p; a.x = ...) traces back to the source
        # tuple's storage. Mirrors the deferred-element-ref pattern used for
        # `a = items[0]`. Two source shapes:
        #   - name source `a, b = p`     -> edges p -> a, p -> b
        #   - tuple-literal source `a, b = (x, y)` -> edges x -> a, y -> b
        # Only register for slots whose unpacked type exposes a mutable
        # borrow surface; value-only elements never need the edge.
        if not self.ctx.is_top_level:
            bt = self.ctx.func.borrow_tracker
            literal_src = stmt.value if isinstance(stmt.value, TpyTupleLiteral) else None
            name_src_root = (_root_name_of_expr(stmt.value)
                             if literal_src is None else None)
            for i, name in enumerate(stmt.targets):
                if name is None or not stmt.is_ref[i]:
                    continue
                # Skip value-type elements (BigInt, str, ...) bound by
                # const-ref for copy avoidance: they have no borrow surface
                # the call-edge gate would care about, and registering an
                # edge here just bloats the borrow tracker.
                if not param_has_mutable_borrow_surface(stmt.target_types[i]):
                    continue
                if literal_src is not None and i < len(literal_src.elements):
                    src_root = _root_name_of_expr(literal_src.elements[i])
                else:
                    src_root = name_src_root
                if src_root is None or src_root == name:
                    continue
                # A loop var over an iterator whose yield names each element's
                # own source (`zip(xs, ys)`) lends the target that source
                # directly, so a write through it climbs to `xs` alone.
                elem_srcs = self.ctx.func.loop_var_elem_iterable.get(src_root)
                lenders = (elem_srcs[i] if elem_srcs is not None
                           and i < len(elem_srcs) else (src_root,))
                for lender in lenders:
                    bt.add_borrow(lender, name, BorrowKind.ELEMENT)
                # A reassigned target binds `T* x = &(element)`, one type
                # for every referent it is re-pointed at, so the source must
                # stay mutable -- the reassigned scalar alias's rule. The
                # reassign drops the loan, so a write after it cannot climb.
                if stmt.is_rebound[i]:
                    for lender in lenders:
                        self.ctx.mark_param_mutated(lender)
                # A MUTABLE element borrow out of a loop var is the same
                # reason `x = h` (a non-value local off a loop var) marks it:
                # the element pointers come off the loop var's binding, so a
                # `const T&` binding would make the lift ill-formed. Sema is
                # where the binding is chosen, so the fact is recorded here
                # rather than re-derived at the unpack's render.
                self.ctx.mark_loop_var_mutated(src_root)

        # Unpacking an owned-element tuple by NAME consumes the whole source:
        # every owned element moves out. Mirror the scalar Own[T] consume model
        # -- at the last use mark the source consumed (drives the unconsumed-
        # param warning); a move-only (nocopy) source used after this point
        # can't be moved out, so it is the same use-after-move error scalar
        # Own raises rather than the raw deleted-copy C++ error.
        if (not self.ctx.is_top_level and any(stmt.is_owned)
                and isinstance(stmt.value, TpyName)
                and self.compat._is_owned_var(stmt.value.name)):
            if self.compat.is_auto_move_use(stmt.value):
                self.compat.check_own_consumption(stmt.value)
            elif self.ctx.is_type_non_copyable(rhs_type):
                reason = (self.ctx.nocopy_reason(rhs_type)
                          or f"owned tuple '{stmt.value.name}'")
                raise self.ctx.error(
                    f"{reason} is used after this point and cannot be unpacked "
                    f"by move. Remove later uses or restructure the code.",
                    stmt.value)

        # Determine const-ref eligibility per element for expensive value types.
        # Safe because tuples are immutable -- no in-place mutation possible.
        if not self.ctx.is_top_level:
            source_is_lvalue = isinstance(stmt.value, TpyName)
            source_safe = True
            if source_is_lvalue:
                source_name = stmt.value.name
                source_safe = not self.ctx.is_reseated(source_name)
            for i, name in enumerate(stmt.targets):
                if name is None:
                    stmt.is_const_ref.append(False)
                    continue
                target_type = stmt.target_types[i]
                eligible = (
                    stmt.is_new[i]
                    and not stmt.is_ref[i]
                    and not stmt.is_owned[i]
                    and not isinstance(target_type, PendingViewType)
                    and target_type.is_value_type()
                    and target_type.is_expensive_copy()
                    and name not in self.ctx.func.current_reassigned_vars
                    and name not in self.ctx.func.current_aug_assigned_vars
                    and source_safe
                )
                stmt.is_const_ref.append(eligible)

    def _analyze_slice_assign(self, stmt: TpyAssign) -> None:
        """Analyze a slice assignment: a[x:y] = rhs or a[x:y:z] = rhs."""
        assert isinstance(stmt.target, TpySubscript)
        sl = stmt.target.index
        assert isinstance(sl, TpySlice)

        stepped = sl.step is not None

        obj_type = self.expr.analyze_expr(stmt.target.obj)
        actual_type = unwrap_own(unwrap_readonly(unwrap_ref_type(obj_type)))

        # Look up __setitem__(basic_slice/slice, value) overload via .py stubs
        result = self.expr._find_slice_setitem(actual_type, stepped=stepped)
        if result is None:
            raise self.ctx.error(
                f"Slice assignment not supported for type '{obj_type}'", stmt)

        _value_param_type, fi = result
        stmt.target.slice_function_info = fi
        stmt.target.is_stepped_slice = stepped

        self._enforce_readonly_assignment_target(stmt.target)

        # Analyze slice bounds and validate they are integer types
        for bound in (sl.lower, sl.upper, sl.step):
            if bound is not None:
                bound_type = self.expr.analyze_expr(bound)
                if not is_any_int_type(bound_type):
                    raise self.ctx.error(
                        f"Slice bound must be an integer, got '{bound_type}'", bound)

        # Use the container's element type as hint so array literals infer correctly.
        # Use the stub's value param type (e.g. Iterable[Own[T]]) for coercion,
        # which accepts any iterable and triggers copy warnings for lvalue sources.
        elem_type = actual_type.get_element_type()
        rhs_hint = make_list(elem_type) if elem_type is not None else _value_param_type
        self.ctx.set_expr_type(stmt.target, rhs_hint)

        value_type = self.expr.analyze_expr_with_hint(stmt.value, rhs_hint)
        stmt.value = self.compat.coerce_expr(stmt.value, value_type, _value_param_type, "slice assignment",
                                             coercion_ctx=CoercionContext.ASSIGN,
                                             target_is_storage_form=True)

        # Mutation tracking
        root = _root_name_of_expr(stmt.target)
        if root is not None:
            self.ctx.mark_loop_var_mutated(root)
            self.ctx.mark_param_mutated(root, through_field=True)
            # Slice assignment replaces a subrange -- structural mutation.
            self.ctx.mark_param_structurally_mutated(root)
        storage = self._resolve_obj_storage(stmt.target.obj)
        if storage is not None:
            if self.ctx.func.borrow_tracker.has_borrow_of_kinds(storage, INVALIDATING_BORROW_KINDS):
                self.ctx.warning(loan_mutation_warning(
                    storage, "slice assignment", iterating=False), stmt)
            self.ctx.mark_all_view_borrowers_mutated(storage)

    def _credit_user_setitem(self, target: TpySubscript, stmt: TpyStmt, *,
                             check_loans: bool) -> None:
        """A user `__setitem__` is a method call on the receiver that may
        grow it, unlike a builtin element store (answered by the element-hop
        check at the write site): credit it through the receiver-call
        chokepoint, so the structural mark reaches a caller holding a loan
        across the enclosing call. `check_loans=False` when the element-hop
        check already warned at this write."""
        recv_type = self.ctx.get_expr_type(target.obj)
        recv_type = (unwrap_readonly(unwrap_own(unwrap_ref_type(recv_type)))
                     if recv_type is not None else None)
        if not (isinstance(recv_type, NominalType) and recv_type.is_user_record):
            return
        record = self.ctx.registry.get_record_for_type(recv_type)
        if record is None:
            return
        overloads, _ = self.protocols.lookup_record_method_overloads(
            record, "__setitem__")
        callee = next((fi for fi in overloads
                       if fi.params and not is_basic_slice_type(fi.params[0].type)
                       and not is_slice_type(fi.params[0].type)), None)
        if callee is not None:
            credit_implicit_receiver_call(self.ctx, target.obj, recv_type, callee,
                                          "__setitem__", stmt,
                                          check_loans=check_loans)

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        # In nested defs, assigning to an outer variable requires nonlocal
        if (self.ctx.func.in_nested_def
                and isinstance(stmt.target, TpyName)
                and stmt.target.name in self.ctx.func.outer_scope_locals
                and stmt.target.name not in self.ctx.func.current_nonlocal_names):
            if (stmt.target.name == "self"
                    and self.ctx.self_names_receiver()):
                raise self.ctx.error(
                    "Cannot rebind 'self' in a nested function: the method"
                    " receiver cannot be rebound. Bind the new object to a"
                    " different name instead",
                    stmt)
            raise self.ctx.error(
                f"Cannot assign to '{stmt.target.name}' in nested function"
                f" without 'nonlocal' declaration",
                stmt)
        # Slice assignment: a[x:y] = rhs -- handled separately
        if isinstance(stmt.target, TpySubscript) and isinstance(stmt.target.index, TpySlice):
            self._analyze_slice_assign(stmt)
            return
        # D16 dyn-attr write: if the target is `obj.foo` where foo is undeclared
        # AND the class has __setattr__, the read-side analysis would either
        # resolve via __getattr__ (handled below) or raise "no field". Detect
        # the latter by analyzing the target with a recovery path: pre-check
        # for an undeclared FieldAccess target on a dyn-writable class.
        if (isinstance(stmt.target, TpyFieldAccess)
                and self._target_is_dyn_writable_only(stmt.target)):
            self._analyze_dyn_setattr_assign(stmt)
            return
        if isinstance(stmt.target, TpyFieldAccess):
            # Pin the write position before the target is analysed as a read:
            # a property read otherwise becomes its GETTER call and the setter
            # below would have no field name to look up.
            stmt.target.is_write_target = True
        elif isinstance(stmt.target, TpySubscript):
            stmt.target.is_write_target = True
        target_type = self.expr.analyze_expr(stmt.target)
        self._check_class_constant_write(stmt.target, stmt)
        # D16 dyn-attr write detection: if the read-side analysis resolved the
        # target via __getattr__ (static lookup miss + dyn-readable class),
        # reinterpret as a __setattr__ write. Synthesize a method call and
        # delegate to the normal method-call analyzer so all standard arg
        # coercion (e.g. into-Any wrapping) is applied uniformly.
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.dyn_getattr_call is not None):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            actual = unwrap_qualifiers(obj_type) if obj_type is not None else None
            record = (self.ctx.registry.get_record_for_type(actual)
                      if isinstance(actual, NominalType) else None)
            sa_overloads, _sa_subst = (
                self.protocols.lookup_record_method_overloads(record, "__setattr__")
                if record is not None else ([], {}))
            if not sa_overloads:
                rec_name = record.name if record is not None else str(actual)
                raise self.ctx.error(
                    f"Record '{rec_name}' has no field '{stmt.target.field}' "
                    f"and does not define __setattr__",
                    stmt,
                )
            # Clear the read-side resolution; this is a write. Then synthesize
            # a __setattr__ call with the original value AST and analyze it
            # via the method-call path -- coerce_expr applies arg coercion
            # (including into-Any wrapping) on the value automatically.
            stmt.target.dyn_getattr_call = None
            setter_call = TpyMethodCall(
                obj=stmt.target.obj,
                method="__setattr__",
                args=[TpyStrLiteral(value=stmt.target.field), stmt.value],
                loc=stmt.loc,
            )
            self.expr.analyze_expr(setter_call)
            stmt.target.dyn_setattr_call = setter_call
            # Mark mutation: writing through a dyn-attr is mutating self/obj.
            root = _root_name_of_expr(stmt.target.obj)
            if root is not None:
                self.ctx.mark_loop_var_mutated(root)
                self.ctx.mark_param_mutated(root, through_field=True)
            self._enforce_readonly_assignment_target(stmt.target)
            return
        value_type = self.expr.analyze_expr_with_hint(stmt.value, target_type)
        # NOTE: storing an ephemeral borrow into value storage (a field, a
        # container element, a global, or another local) COPIES the value in TPy
        # (the existing "copies into owned storage" path), so it is memory-safe
        # and is intentionally NOT rejected. Escapes that retain the *reference*
        # past the iteration step are caught elsewhere: returning it
        # (dangling-return check), stashing it where it outlives its scope
        # (lifetime analysis), re-yielding it (yield-onward check), and capturing
        # it by reference in a closure (nested-def escape check).
        # Property setter: validate and tag for codegen
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.resolved_property_getter is not None:
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            actual = unwrap_readonly(obj_type) if obj_type else None
            record = self.ctx.registry.receiver_record(actual) if isinstance(actual, NominalType) else None
            prop = self.protocols.lookup_record_property(record, stmt.target.field) if record else None
            if prop and prop.setter:
                stmt.target.property_setter = True
                # Construct setter TpyMethodCall for codegen delegation
                setter_name = f"set_{stmt.target.field}"
                setter_call = TpyMethodCall(
                    obj=stmt.target.obj, method=setter_name, args=[stmt.value])
                setter_call.resolved_function_info = prop.setter
                stmt.target.property_setter_call = setter_call
            elif prop and not prop.setter:
                raise self.ctx.error(
                    f"Property '{stmt.target.field}' is read-only (no setter defined)",
                    stmt,
                )
        self._enforce_readonly_assignment_target(stmt.target)
        # Track mutation of for-each loop variables (prevents const-ref binding)
        root = _root_name_of_expr(stmt.target)
        if root is not None:
            self.ctx.mark_loop_var_mutated(root)
            # Through-reference writes (field/subscript) mutate the param's object;
            # plain name reassignment just rebinds the local.
            if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
                self.ctx.mark_param_mutated(root, through_field=True)
        copy_warning_fired = False
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            declared_target_type = self.narrowing.declared_type_for_expr(stmt.target)
            if declared_target_type is not None:
                target_type = declared_target_type
            # Unified copy detection: Ref (borrowed), Own (owned at non-last-use),
            # or compound borrowed types (Optional/Union with pointer repr) on
            # the value expression type means storing it into a field/container
            # will copy.  Ref covers params, call returns, field access, subscript.
            # Own covers owned locals at non-last-use.
            # Optional[NonValue] and Union[pointer-repr] use pointer representation
            # (T*, variant<A*,B*>) which is semantically borrowed -- copying into
            # storage (std::optional<T>, variant<A,B>) is a pointer-to-value copy.
            # Skip explicit copy() calls (caller acknowledged the copy) and
            # OwnType from non-name sources (explicit Own return from function).
            is_own_from_name = isinstance(value_type, OwnType) and isinstance(stmt.value, TpyName)
            stripped_value = self.ctx.get_expr_type(stmt.value)
            is_compound_ref = (
                (isinstance(stripped_value, OptionalType) and not stripped_value.inner.is_value_type())
                or (isinstance(stripped_value, UnionType) and stripped_value.uses_pointer_repr()
                    and not stripped_value.needs_wrapper())
            )
            # Ptr[T] target takes the address of the source (`_a(&a)`), no copy.
            target_is_ptr = isinstance(unwrap_qualifiers(target_type), PtrType)
            # Any target: INTO_ANY in compatibility.py emits its own copy
            # warning for reference-type sources -- skip the generic
            # field/container warning here to avoid a double-fire.
            target_is_any = isinstance(unwrap_qualifiers(target_type), AnyType)
            if ((isinstance(value_type, RefType) or is_own_from_name or is_compound_ref)
                    and stmt.loc is not None
                    and not target_is_ptr
                    and not target_is_any
                    and not self.compat.is_copy_call(stmt.value)):
                inner = unwrap_qualifiers(value_type)
                dest = "field" if isinstance(stmt.target, TpyFieldAccess) else "container"
                deferred = self.ctx.defer_own_copy_verdict(
                    inner, target_type, dest, stmt)
                if not deferred and self.ctx.is_type_non_copyable(target_type):
                    hint = self.compat.nocopy_hint(stmt.value)
                    if inner == target_type:
                        msg = (f"cannot copy non-copyable type '{target_type}' into "
                               f"{dest}{hint}")
                    else:
                        msg = (f"cannot copy {inner} into {dest} of type '{target_type}'; "
                               f"target is non-copyable{hint}")
                    raise self.ctx.error(msg, stmt)
                if not deferred:
                    self.ctx.warning(
                        f"copies {inner} into {dest}; "
                        f"{self.compat.copy_remedy(stmt.value)}",
                        stmt)
                copy_warning_fired = True
            # A tuple is a value type, but storing one whose elements are
            # pointer-repr COPIES each such element into the owned slot
            # (tuple_to_storage) where CPython aliases -- warn per element via
            # the shared helper (also used by the container-literal / insert
            # coercion path). The helper carries the literal / copy() / owning-
            # call exemptions.
            if stmt.loc is not None and not target_is_any:
                tgt_tuple = unwrap_qualifiers(target_type)
                dest = "field" if isinstance(stmt.target, TpyFieldAccess) else "container"
                if self.compat.warn_pointer_repr_tuple_copy(
                        stmt.value, tgt_tuple, dest, stmt):
                    copy_warning_fired = True
            # Own[T] param stored in a field/container — mark as consumed
            if isinstance(stmt.value, TpyName) and stmt.value.name in self.ctx.func.current_param_names:
                self.ctx.mark_own_param_consumed(stmt.value.name)
                # Address-escape tracking: storing a param into a mutable Ptr[T]
                # takes its address. Suppress the perf-default `const T&` param
                # emission so the `&param -> T*` store type-checks.
                tgt_inner = unwrap_qualifiers(target_type)
                if (isinstance(stmt.target, TpyFieldAccess)
                        and isinstance(tgt_inner, PtrType)
                        and not tgt_inner.is_readonly):
                    self.ctx.func.current_addr_escape_param_names.add(stmt.value.name)
        if isinstance(stmt.target, TpyName):
            # Track param rebinding (subsequent mutations target the new local, not the arg)
            if stmt.target.name in self.ctx.func.current_param_names:
                self.ctx.func.current_rebound_params.add(stmt.target.name)
            # Block reassignment of Final globals at module level
            if self.ctx.is_top_level and stmt.target.name in self.ctx.final_globals:
                raise self.ctx.error(
                    f"Cannot reassign Final variable '{stmt.target.name}'",
                    stmt
                )
            # Match var-decl flow: reject forbidden rebinding before any type mutation.
            self._check_nonvalue_rebinding(stmt.target.name, stmt)
            declared_target_type = self.ctx.func.current_scope.lookup(stmt.target.name)
            if declared_target_type is not None:
                target_type = declared_target_type
            # Unwrap Ref, Own, and ReadonlyType for reassignment type resolution --
            # this is a binding, not passing by reference.
            inner_target = unwrap_own(unwrap_ref_type(unwrap_readonly(target_type)))
            inner_value = unwrap_own(unwrap_ref_type(unwrap_readonly(value_type)))
            # PendingListType reassignment: different sizes force list
            if isinstance(inner_target, PendingListType):
                if isinstance(inner_value, PendingListType):
                    if inner_target.size != inner_value.size:
                        self.deduction.mark_list_different_size(inner_target.literal_id)
                        self.deduction.mark_list_different_size(inner_value.literal_id)
                    else:
                        self.deduction.link_list_literals(inner_target.literal_id, inner_value.literal_id)
                # target_type stays PendingListType
            # PendingDictType/PendingSetType reassignment: keep pending
            elif isinstance(inner_target, (PendingDictType, PendingSetType)):
                pass  # target_type stays pending
            # PendingViewType reassignment: track view-compatibility, keep pending
            elif isinstance(inner_target, PendingViewType):
                vf = inner_target.family
                if vf.is_any_member(inner_value):
                    if not self.deduction.is_view_compatible_source(stmt.value, inner_value):
                        self.deduction.mark_view_reassigned_from_owned(stmt.target.name, vf)
                    else:
                        self.deduction.track_view_reassign_source(stmt.target.name, inner_value, vf)
                        self.deduction.note_view_rebind(stmt.target.name, stmt.value, vf)
                # target_type stays pending
            else:
                target_type = self.deduction.resolve_reassignment_target_type(
                    stmt.target.name, inner_target, inner_value, init_expr=stmt.value
                )
                # Readonly status flows from the value expression
                if isinstance(value_type, ReadonlyType) and not target_type.is_value_type():
                    target_type = ReadonlyType(target_type)
            self.ctx.func.current_scope.define(stmt.target.name, target_type)
            # Reassignment revives a consumed variable
            self.ctx.func.consumed_vars.discard(stmt.target.name)
            self.compat.update_tuple_member_local_facts(
                stmt.target.name, target_type, stmt.value)
            self._update_ephemeral_alias_fact(
                stmt.target.name, target_type, stmt.value)
            # Borrow tracking: reassignment breaks aliases in both directions.
            # Retarget runs before remove_borrower so borrowers of the target
            # get re-pointed to the upstream source (with promoted kind),
            # keeping chains through reassigned vars valid.
            self.ctx.mark_all_view_borrowers_mutated(stmt.target.name)
            _handle_pinned_view_rebind(self.ctx, stmt.target.name, stmt)
            bt = self.ctx.func.borrow_tracker
            stamp_bind_kind(self.ctx, stmt, stmt.target.name, stmt.value,
                            target_type, rebind=True)
            bt.rebind_borrower(stmt.target.name, stmt.value)
            # Rebinding a non-value pointer-local generates local = &(source) in C++,
            # requiring source param to be T& (not const T&).
            if not inner_target.is_value_type() and self.compat.is_lvalue(stmt.value):
                for rebind_root in addr_taken_roots(stmt.value):
                    self.ctx.mark_param_mutated(rebind_root)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.update_variable_type(stmt.target.name, target_type)
            self.ctx.set_expr_type(stmt.target, target_type)
            self.deduction.record_write(stmt.target.name, stmt.value, inner_value)
            if not isinstance(inner_target, (*PENDING_CONTAINER_TYPES, PendingViewType)):
                resolved = unwrap_readonly(target_type)
                var_decl = self.ctx.func.var_decl_by_name.get(stmt.target.name)
                if var_decl:
                    self.ctx.var_types[var_decl] = resolved
                # Retroactively update declared_var_types for earlier lines
                # so # tpyc: type() reflects the final variable type.
                if resolved != unwrap_readonly(inner_target):
                    for key in self.ctx.declared_var_types:
                        if key[1] == stmt.target.name:
                            self.ctx.declared_var_types[key] = resolved

        # Disallow reassignment of non-value-type params and loop vars
        if isinstance(stmt.target, TpyName):
            # Update rvalue/ownership status for hoist eligibility and copy detection
            if self.compat.is_lvalue(stmt.value):
                self.ctx.func.rvalue_vars.discard(stmt.target.name)
                self.ctx.func.owned_locals.discard(stmt.target.name)
                record_stmt_borrow_binding(
                    self.ctx, stmt.target.name,
                    self.ctx.get_expr_type(stmt.value), stmt.value)
                self._bar_unscanned_lvalue_reassign(stmt.target.name,
                                                    stmt.value)
            else:
                self.ctx.func.rvalue_vars.add(stmt.target.name)
                # See the var-decl branch: owned only for a genuine value-creating
                # init, not a borrow-producing one rendered `T&`.
                if is_rvalue_source(self.ctx, stmt.value):
                    note_owned_local(self.ctx, stmt.target.name,
                                     self.ctx.get_expr_type(stmt.value))
                else:
                    self.ctx.func.owned_locals.discard(stmt.target.name)
                    record_stmt_borrow_binding(
                        self.ctx, stmt.target.name,
                        self.ctx.get_expr_type(stmt.value), stmt.value)
                    # Borrow-source reassignment bars the var from the movable
                    # set. Unlike the var-decl branch this needs no
                    # existing-var guard: a TpyAssign with a bare-name target is
                    # always a reassignment (a first bare-name bind is a
                    # TpyVarDecl).
                    self.ctx.func.borrow_reassigned_vars.add(stmt.target.name)

        # Tuples are immutable -- reject element assignment
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            if isinstance(obj_type, TupleType):
                raise self.ctx.error("Tuples are immutable; cannot assign to tuple elements", stmt)
            # Dict/TypedDict subscript assignment is always allowed
            actual_obj = unwrap_readonly(obj_type)
            is_typed_dict_target = (
                isinstance(actual_obj, NominalType) and actual_obj.is_record
                and stmt.target.typed_dict_field is not None
            )
            if not (is_dict(actual_obj) or isinstance(actual_obj, PendingDictType)) and not is_typed_dict_target:
                elem_type = obj_type.get_element_type()
                if elem_type is not None:
                    # Span[readonly[T]] always rejects element assignment
                    if is_span(obj_type) and is_readonly_span(obj_type):
                        raise self.ctx.error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)
                    # Check if type conforms to MutableSequence[elem_type]
                    mutable_seq = NominalType("MutableSequence", (elem_type,), is_protocol=True)
                    if not self.protocols.type_conforms_to_protocol(obj_type, mutable_seq):
                        raise self.ctx.error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)

        # Prevent assignment through read-only pointer
        if isinstance(stmt.target, TpyName):
            pass  # TpyName targets are fine
        else:
            if isinstance(stmt.target, TpyFieldAccess):
                obj_type = self.ctx.get_expr_type(stmt.target.obj)
                if is_readonly_ptr(obj_type):
                    raise self.ctx.error("Cannot assign through read-only pointer", stmt)

        # Borrow conflict: field write on borrowed storage.
        # Resolves aliases so alias.field = val warns when the underlying storage
        # has field/element/ptr borrows.
        # Subscript assignment (items[i] = val, d[k] = val) is in-place and does
        # NOT invalidate element references: list element replacement doesn't
        # reallocate, and ordered_map is node-based so insertion is stable
        # (confirmed by @native_preserves_refs on dict.__setitem__).
        # It does destroy the element it overwrites, so a loan taken out of an
        # ELEMENT of this container -- index-blind -- is clobbered.
        if isinstance(stmt.target, TpySubscript):
            storage = self._resolve_obj_storage(stmt.target.obj)
            hit = None
            if storage is not None:
                bt = self.ctx.func.borrow_tracker
                hit = bt.element_hop_loan(
                    storage, element_index_key(stmt.target.index))
                if hit is not None:
                    self.ctx.warning(element_loan_mutation_warning(
                        f"{storage}[...]", "element assignment", hit), stmt)
                self.ctx.mark_all_view_borrowers_mutated(storage)
            self._credit_user_setitem(stmt.target, stmt, check_loans=hit is None)
        elif isinstance(stmt.target, TpyFieldAccess):
            storage = self._resolve_obj_storage(stmt.target.obj)
            # Also check the field-path key itself (e.g. "self.items" for self.items = [...])
            # since borrows may be registered on the dotted key.
            field_storage = _storage_key(stmt.target)
            # A field REBIND destroys what the slot held, so on top of the
            # kinds a structural mutation invalidates it also clobbers a
            # FIELD loan: that loan names the slot, and a rebind takes the
            # slot away where a structural mutation of what is stored in
            # it does not.
            _BORROW_KINDS = (BorrowKind.FIELD, *INVALIDATING_BORROW_KINDS)
            has_conflict = False
            bt = self.ctx.func.borrow_tracker
            if storage is not None and bt.has_borrow_of_kinds(storage, _BORROW_KINDS):
                has_conflict = True
            if not has_conflict and field_storage is not None and bt.has_borrow_of_kinds(field_storage, _BORROW_KINDS):
                storage = field_storage
                has_conflict = True
            # A scalar field write replaces a value in place: it preserves
            # the storage of the record and of its other fields, so no loan
            # into either is invalidated. A property setter runs user code,
            # and a movable payload's slot is taken away, so both still warn.
            if has_conflict and (stmt.target.resolved_property_getter is not None
                                 or not (is_primitive_type(target_type)
                                         or is_big_int_type(target_type))):
                self.ctx.warning(loan_mutation_warning(
                    storage, "field assignment", iterating=False), stmt)
            self._mark_field_write_views(stmt.target, storage, field_storage)

        # PendingDictType subscript assignment: d[k] = v -- infer key/value types
        if isinstance(stmt.target, TpySubscript):
            obj_type_for_dict = self.ctx.get_expr_type(stmt.target.obj)
            if isinstance(obj_type_for_dict, PendingDictType):
                index_type = self.ctx.get_expr_type(stmt.target.index)
                self.deduction.infer_dict_key_value_types(
                    stmt.target.obj, index_type, value_type,
                    stmt.target.index, stmt.value)
                # Update obj_type and target_type if types were inferred
                dict_info = self.ctx.dict_literals.get(obj_type_for_dict.literal_id)
                if dict_info and not isinstance(dict_info.key_type, UnknownElementType):
                    new_pending = PendingDictType(dict_info.key_type, dict_info.value_type, obj_type_for_dict.literal_id)
                    if new_pending.key_type != obj_type_for_dict.key_type or new_pending.value_type != obj_type_for_dict.value_type:
                        self.ctx.set_expr_type(stmt.target.obj, new_pending)
                        if isinstance(stmt.target.obj, TpyName):
                            if self.ctx.func.current_scope:
                                self.ctx.func.current_scope.define(stmt.target.obj.name, new_pending)
                            if self.ctx.func.current_ns:
                                self.ctx.func.current_ns.bind_variable(stmt.target.obj.name, new_pending)
                    target_type = dict_info.value_type
                    self.ctx.set_expr_type(stmt.target, target_type)

        # Field / container element targets store the value (storage form);
        # the `T -> Optional[T]` address-take that fires for borrow-form
        # destinations doesn't apply here.
        target_is_storage = isinstance(stmt.target, (TpyFieldAccess, TpySubscript))
        stmt.value = self.compat.coerce_expr(stmt.value, value_type, target_type, "assignment",
                                              coercion_ctx=CoercionContext.ASSIGN,
                                              target_is_storage_form=target_is_storage)
        # Annotate tuple literal element capture modes
        if isinstance(stmt.value, TpyTupleLiteral):
            tuple_target = own_tuple_target(target_type)
            if tuple_target is not None:
                is_field = isinstance(stmt.target, TpyFieldAccess)
                is_subscript = isinstance(stmt.target, TpySubscript)
                self._annotate_tuple_elem_capture(
                    stmt.value, tuple_target,
                    sink_dest=("field" if is_field
                               else "container" if is_subscript
                               else "owned storage"),
                    sink=(TupleSink.FIELD if is_field
                          else TupleSink.CONTAINER if is_subscript
                          else TupleSink.LOCAL))
        # Residual copy warning: reassigned vars without OwnType in scope
        if isinstance(stmt.target, TpyName):
            _register_tuple_binding_borrows(
                self.ctx, stmt.target.name, stmt.value, target_type)
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            if stmt.loc is not None and not copy_warning_fired:
                if self._is_non_owned_var_copy(stmt.value, target_type):
                    dest = "field" if isinstance(stmt.target, TpyFieldAccess) else "container"
                    deferred = self.ctx.defer_own_copy_verdict(
                        value_type, target_type, dest, stmt)
                    if not deferred and self.ctx.is_type_non_copyable(target_type):
                        hint = self.compat.nocopy_hint(stmt.value)
                        if value_type == target_type:
                            msg = (f"cannot copy non-copyable type '{target_type}' into "
                                   f"{dest}{hint}")
                        else:
                            msg = (f"cannot copy {value_type} into {dest} of type '{target_type}'; "
                                   f"target is non-copyable{hint}")
                        raise self.ctx.error(msg, stmt)
                    if not deferred:
                        self.ctx.warning(
                            f"copies {value_type} into {dest}; "
                            f"{self.compat.copy_remedy(stmt.value)}",
                            stmt)

        # Scope escape check for assignments to named variables
        if isinstance(stmt.target, TpyName) and not target_type.is_value_type():
            self.scopes.check_escape(stmt.target.name, stmt.value, stmt)
            # Assigning a loop var to a pointer-local takes &(var) in codegen
            if isinstance(stmt.value, TpyName):
                self.ctx.mark_loop_var_mutated(stmt.value.name)

        if isinstance(stmt.target, TpyName) and _needs_provenance_tracking(target_type):
            self.init.mark_provenance(stmt.target.name, self.compat.is_param_derived_expr(stmt.value))
            self.init.mark_safe_to_return(
                stmt.target.name, self.compat.is_safe_to_return_expr(stmt.value))
        # Track non-null pointer provenance for null-check elision
        if isinstance(stmt.target, TpyName) and isinstance(target_type, PtrType):
            is_non_null = expr_yields_non_null_ptr(stmt.value, self.ctx.func.non_null_ptr_vars)
            self.init.mark_non_null_ptr(stmt.target.name, is_non_null)

        # Mark as definitely assigned for plain name targets
        if isinstance(stmt.target, TpyName):
            self.init.mark_assigned(stmt.target.name)
            self.narrowing.update_after_write(stmt.target.name, target_type, value_type, stmt.value)
        elif isinstance(stmt.target, TpyFieldAccess):
            self.narrowing.invalidate_for_field_write(stmt.target)

    def _analyze_del_item(self, stmt: TpyDelItem) -> None:
        """Analyze del obj[key] statement."""
        for subscript in stmt.targets:
            # Analyze obj and index separately to avoid triggering __getitem__
            # validation (del doesn't read the element, only deletes it).
            self.expr.analyze_expr(subscript.obj)
            # Track mutation of for-each loop variables and parameters
            del_root = _root_name_of_expr(subscript.obj)
            if del_root is not None:
                self.ctx.mark_loop_var_mutated(del_root)
                self.ctx.mark_param_mutated(del_root, through_field=True)
                # del item removes an element from the container -- structural mutation.
                self.ctx.mark_param_structurally_mutated(del_root)
            # Borrow conflict: del on a container with element-level borrows
            storage = self._resolve_obj_storage(subscript.obj)
            if storage is not None:
                bt = self.ctx.func.borrow_tracker
                if bt.has_element_borrow(storage):
                    self.ctx.warning(loan_mutation_warning(
                        storage, "'del'",
                        iterating=bt.has_iter_borrow(storage)), stmt)
                self.ctx.mark_all_view_borrowers_mutated(storage)
            self._enforce_readonly_assignment_target(subscript)
            obj_type = self.ctx.get_expr_type(subscript.obj)
            actual = unwrap_readonly(obj_type)
            # Reject known-immutable/fixed-size types before analyzing the index
            if isinstance(actual, TupleType):
                raise self.ctx.error(
                    "Tuples are immutable; cannot delete tuple elements", stmt)
            if is_array(actual):
                raise self.ctx.error(
                    "Arrays are fixed-size; cannot delete array elements", stmt)
            if is_span(actual):
                raise self.ctx.error(
                    "Spans are read-only views; cannot delete span elements", stmt)
            if isinstance(subscript.index, TpySlice):
                raise self.ctx.error(
                    "deleting a slice ('del xs[a:b]') is not yet supported", stmt)
            # Check that the type has __delitem__
            record_info = self.ctx.registry.get_record_for_type(actual)
            if record_info:
                overloads = record_info.get_method_overloads("__delitem__")
                if not overloads:
                    raise self.ctx.error(
                        f"'del' is not supported for type {actual}; "
                        f"define __delitem__ to enable element deletion", stmt)
                # A builtin container's delete is the store itself; a user
                # type's runs its dunders, which may write anything.
                if isinstance(actual, NominalType) and actual.is_user_record:
                    for fi in self.ctx.registry.get_method_overloads_with_parents(
                            record_info, "__delitem__"):
                        record_implicit_call(subscript, fi, subscript.obj,
                                             "__delitem__")
            else:
                raise self.ctx.error(
                    f"'del' is not supported for type {actual}", stmt)
            # Analyze the index expression only after confirming __delitem__ exists
            index_type = self.expr.analyze_expr(subscript.index)
            if is_dict(actual):
                # `del d[k]` hands the key to the same map lookup a read does,
                # so it takes the same key check and the same narrow node --
                # otherwise an unchecked key reaches `erase` raw.
                self.expr.check_dict_key(
                    subscript, actual.type_args[0], unwrap_readonly(index_type))

    def _target_is_dyn_writable_only(self, target: TpyFieldAccess) -> bool:
        """D16: True if `target` is `obj.foo` where foo is undeclared on the
        receiver's class AND the class has __setattr__ but not __getattr__.

        In that combination the read-side analyzer would raise "no field"
        before reaching the assign-time dunder routing, so the assign path
        needs to take over BEFORE calling analyze_expr on the target.
        """
        try:
            obj_type = self.expr.analyze_expr(target.obj)
        except SemanticError:
            return False
        actual = unwrap_qualifiers(obj_type) if obj_type is not None else None
        if not (isinstance(actual, NominalType) and actual.is_record):
            return False
        record = self.ctx.registry.get_record_for_type(actual)
        if record is None:
            return False
        # Cheap reject first: if the class has __getattr__, the standard path
        # (dyn_getattr_call) handles it -- this fast-path is for the
        # __setattr__-only case. If there is also no __setattr__, irrelevant.
        if self.protocols.lookup_record_method_overloads(record, "__getattr__")[0]:
            return False
        if not self.protocols.lookup_record_method_overloads(record, "__setattr__")[0]:
            return False
        # Static lookup must miss for the dyn-setattr fallback to fire.
        if self.protocols.lookup_record_field(record, target.field) is not None:
            return False
        if self.protocols.lookup_record_property(record, target.field) is not None:
            return False
        if self.protocols.lookup_record_method_overloads(record, target.field)[0]:
            return False
        if target.field in record.class_constants:
            return False
        return True

    def _analyze_dyn_setattr_assign(self, stmt: TpyAssign) -> None:
        """D16: write-only dyn-setattr path used when the class has __setattr__
        but no __getattr__ (so the read-side analyzer can't resolve the target).
        """
        assert isinstance(stmt.target, TpyFieldAccess)
        setter_call = TpyMethodCall(
            obj=stmt.target.obj,
            method="__setattr__",
            args=[TpyStrLiteral(value=stmt.target.field), stmt.value],
            loc=stmt.loc,
        )
        self.expr.analyze_expr(setter_call)
        stmt.target.dyn_setattr_call = setter_call
        # Mutation tracking parallel to plain field assignment.
        root = _root_name_of_expr(stmt.target.obj)
        if root is not None:
            self.ctx.mark_loop_var_mutated(root)
            self.ctx.mark_param_mutated(root, through_field=True)
        self._enforce_readonly_assignment_target(stmt.target)

    def _analyze_del_attr(self, stmt: 'TpyDelAttr') -> None:
        """D16 Phase 3: del obj.foo, obj2.bar -- route through __delattr__.

        Declared fields, properties, methods, and class constants are not
        deletable in TPy regardless of whether the class defines __delattr__
        (record layout is fixed). For undeclared names, route through the
        dunder if present.
        """
        for target in stmt.targets:
            obj_type = self.expr.analyze_expr(target.obj)
            actual = unwrap_qualifiers(obj_type) if obj_type is not None else None
            if not (isinstance(actual, NominalType) and actual.is_record):
                raise self.ctx.error(
                    f"Cannot delete attribute '{target.field}' on type {obj_type}",
                    stmt,
                )
            record = self.ctx.registry.get_record_for_type(actual)
            if record is None:
                raise self.ctx.error(
                    f"Cannot delete attribute '{target.field}' on type {obj_type}",
                    stmt,
                )
            field_name = target.field
            # Reject declared-member targets uniformly.
            if self.protocols.lookup_record_field(record, field_name) is not None:
                raise self.ctx.error(
                    f"Cannot delete declared field '{field_name}' from "
                    f"'{record.name}' (record layout is fixed)",
                    stmt,
                )
            if self.protocols.lookup_record_property(record, field_name) is not None:
                raise self.ctx.error(
                    f"Cannot delete declared property '{field_name}' from "
                    f"'{record.name}'",
                    stmt,
                )
            method_overloads, _ = self.protocols.lookup_record_method_overloads(record, field_name)
            if method_overloads:
                raise self.ctx.error(
                    f"Cannot delete method '{field_name}' from '{record.name}'",
                    stmt,
                )
            if field_name in record.class_constants:
                raise self.ctx.error(
                    f"Cannot delete class constant '{field_name}' from '{record.name}'",
                    stmt,
                )
            # Route through __delattr__ if defined.
            da_overloads, _da_subst = self.protocols.lookup_record_method_overloads(
                record, "__delattr__")
            if not da_overloads:
                # Only mention __delattr__ when the class has already opted
                # into dyn-attrs (any of __getattr__/__setattr__ defined);
                # for a plain record, just say the field doesn't exist.
                has_dyn_attrs = bool(
                    self.protocols.lookup_record_method_overloads(record, "__getattr__")[0]
                    or self.protocols.lookup_record_method_overloads(record, "__setattr__")[0]
                )
                if has_dyn_attrs:
                    raise self.ctx.error(
                        f"Record '{record.name}' has no field '{field_name}' and does not "
                        f"define __delattr__",
                        stmt,
                    )
                raise self.ctx.error(
                    f"Record '{record.name}' has no field '{field_name}'", stmt)
            synth = TpyMethodCall(
                obj=target.obj,
                method="__delattr__",
                args=[TpyStrLiteral(value=field_name)],
                loc=stmt.loc,
            )
            self.expr.analyze_expr(synth)
            target.dyn_delattr_call = synth
            # Mark mutation: deletion through dunder mutates the receiver.
            root = _root_name_of_expr(target.obj)
            if root is not None:
                self.ctx.mark_loop_var_mutated(root)
                self.ctx.mark_param_mutated(root, through_field=True)
            self._enforce_readonly_assignment_target(target)

    def _reject_generator_end_under_loop(self, name: str, node: TpyStmt,
                                         verb: str, remedy: str) -> None:
        """A running `for` over `name` steps the generator it holds, which
        a rebind (rebuilt in place) or a `del` (closed in place) would pull
        out from under it."""
        bt = self.ctx.func.borrow_tracker
        if bt.has_iter_borrow(name) or bt.has_iter_borrow(
                bt.effective_storage(name)):
            raise self.ctx.error(
                f"cannot {verb} '{name}' inside a 'for' loop over it: the "
                f"loop is still running the generator '{name}' holds; "
                f"{remedy}",
                node)

    def _analyze_del_var(self, stmt: TpyDelVar) -> None:
        """Analyze a variable deletion statement (del x)."""
        for name in stmt.names:
            if holds_generator_object(self.ctx.local_decl_type(name)
                                      or self.ctx.func.current_scope.lookup(
                                          name)):
                self._reject_generator_end_under_loop(
                    name, stmt, "delete", "delete it after the loop")
            # A finally-deferred return holds a borrow of the local across
            # this finally body; del would free the storage it materializes
            # from. (CPython's pending return keeps the object alive -- the
            # restructure is to drop other cleanup targets, not the returned
            # local.)
            spelled = next((pending[name][0] for pending
                            in self.ctx.func.pending_return_borrows
                            if name in pending), None)
            if spelled is not None:
                raise self.ctx.error(
                    f"cannot delete '{name}' in this finally block: an "
                    f"enclosed '{spelled}' still borrows it (the value "
                    f"is materialized after the finally runs)", stmt)
            # Global-declared and nonlocal vars are always reachable;
            # locals/params must be definitely assigned.
            is_external = (name in self.ctx.func.global_declarations
                           or name in self.ctx.func.current_nonlocal_names)
            if not is_external and name not in self.ctx.func.definitely_assigned:
                raise self.ctx.error(
                    f"variable '{name}' may not be assigned at this point", stmt)
            # The value is destroyed here, so views of its storage own --
            # including one a call handed back, which holds a borrow of it.
            self.ctx.mark_all_view_borrowers_mutated(name)
            self.deduction.own_views_borrowing(name)
            # Remove from definitely_assigned so use-after-del is caught
            self.ctx.func.definitely_assigned.discard(name)
            # Clear narrowing facts
            self.ctx.func.narrowed_types.pop(name, None)
            self.ctx.func.non_null_ptr_vars.discard(name)

    def _apply_aug_assign_writeback(
        self,
        target: TpyExpr,
        target_type: TpyType,
        result_type: TpyType,
        op: str,
        stmt: TpyAugAssign,
    ) -> None:
        """Check and apply the write-back step of an augmented assignment.

        After the binop is resolved with result_type, verifies result_type is
        compatible with the target and updates variable caches when widening applies.
        Uses the same type rules as regular assignment (resolve_reassignment_target_type
        + check_type_compatible), so annotated variables and non-wideneable pairs
        produce a standard type mismatch error.
        """
        if result_type == target_type:
            return
        op = op_spelling(op)
        if isinstance(target, TpyName):
            name = target.name
            effective_type = self.deduction.resolve_reassignment_target_type(
                name, target_type, result_type, aug_op=op, site=stmt,
            )
            # check_type_compatible errors when effective_type refused widening
            # (e.g. annotated variable, or mixed-sign fixed-int pair).
            self.compat.check_type_compatible(
                result_type, effective_type, f"'{op}=' to '{name}'", loc=stmt.loc,
            )
            if effective_type != target_type:
                if self.ctx.func.current_scope:
                    self.ctx.func.current_scope.define(name, effective_type)
                var_decl = self.ctx.func.var_decl_by_name.get(name)
                if var_decl:
                    self.ctx.var_types[var_decl] = effective_type
                for key in self.ctx.declared_var_types:
                    if key[1] == name:
                        self.ctx.declared_var_types[key] = effective_type
        else:
            # Subscript/field target: element type is fixed, cannot widen.
            self.compat.check_type_compatible(
                result_type, target_type,
                f"'{op}=' to {_format_aug_target(target)}", loc=stmt.loc,
                target_is_storage_form=True,
            )

    def _is_non_owned_var_copy(self, expr: TpyExpr, target_type: TpyType) -> bool:
        """Check if storing a non-OwnType variable copies into storage.

        Covers locals excluded from OwnType wrapping: reassigned vars,
        union types, Optional with pointer repr.  These are not caught by
        the unified Ref/Own check.
        """
        if target_type.is_value_type():
            return False
        if self.compat._is_value_type_param(target_type):
            return False
        if self.compat.is_copy_call(expr):
            return False
        if isinstance(expr, TpyIfExpr):
            return self.compat.ternary_arm_copies(expr)
        if not isinstance(expr, TpyName):
            return False
        scope_type = self.ctx.func.current_scope.lookup(expr.name) if self.ctx.func.current_scope else None
        if scope_type is not None and isinstance(scope_type, (RefType, OwnType)):
            return False  # already caught by the Ref/Own check
        if scope_type is not None and scope_type.is_value_type():
            return False
        # Skip at last-use of movable var
        if self.compat.is_auto_move_use(expr):
            return False
        if scope_type is None:
            return False
        return True

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        # In nested defs, aug-assign to an outer variable requires nonlocal
        if (self.ctx.func.in_nested_def
                and isinstance(stmt.target, TpyName)
                and stmt.target.name in self.ctx.func.outer_scope_locals
                and stmt.target.name not in self.ctx.func.current_nonlocal_names):
            if (stmt.target.name == "self"
                    and self.ctx.self_names_receiver()):
                raise self.ctx.error(
                    "Cannot rebind 'self' in a nested function: the method"
                    " receiver cannot be rebound. Bind the new object to a"
                    " different name instead",
                    stmt)
            raise self.ctx.error(
                f"Cannot modify '{stmt.target.name}' in nested function"
                f" without 'nonlocal' declaration",
                stmt)
        # Block augmented assignment of Final globals at module level
        if isinstance(stmt.target, TpyName) and self.ctx.is_top_level and stmt.target.name in self.ctx.final_globals:
            raise self.ctx.error(
                f"Cannot reassign Final variable '{stmt.target.name}'",
                stmt
            )
        if isinstance(stmt.target, TpyFieldAccess):
            # Same pin as the plain assign: the located error below names the
            # property by the field the target still carries.
            stmt.target.is_write_target = True
        target_type = unwrap_own(unwrap_ref_type(self.expr.analyze_expr(stmt.target)))
        # Augmented assignment on properties not yet supported
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.resolved_property_getter is not None:
            raise self.ctx.error(
                f"Augmented assignment on property '{stmt.target.field}' is not yet supported",
                stmt,
            )
        self._check_class_constant_write(stmt.target, stmt)
        # Aug-assign replaces the target's value with a freshly computed one
        # (owned str/bytes concat, reallocated list, etc.), so any prior
        # param-derived / safe-to-return provenance is now stale and must be
        # cleared -- otherwise a later `return` as a view would pass the
        # dangling check despite pointing into local storage.
        if isinstance(stmt.target, TpyName) and _needs_provenance_tracking(target_type):
            self.init.mark_provenance(stmt.target.name, False)
            self.init.mark_safe_to_return(stmt.target.name, False)
        value_type = self.expr.analyze_expr_with_hint(stmt.value, target_type)
        # Track mutation of for-each loop variables and parameters
        aug_root = _root_name_of_expr(stmt.target)
        if aug_root is not None:
            self.ctx.mark_loop_var_mutated(aug_root)
            self.ctx.mark_param_mutated(aug_root, through_field=True)
            # items += other_list extends the container in-place (structural mutation).
            # items[i] += x and obj.field += x are in-place element/field writes -- not structural.
            if isinstance(stmt.target, TpyName):
                self.ctx.mark_param_structurally_mutated(aug_root)
        self._enforce_readonly_assignment_target(stmt.target)
        # Borrow conflict: augmented assignment may mutate borrowed storage.
        # Subscript aug-assign (items[i] += x) modifies element in-place -- same as
        # subscript assign, no reallocation, element/ptr borrows remain valid.
        if isinstance(stmt.target, TpySubscript):
            storage = self._resolve_obj_storage(stmt.target.obj)
            hit = None
            if storage is not None:
                # A structural `+=` on an element (list, set) reallocates the
                # element's own buffer, which a loan INTO that element rides.
                bt = self.ctx.func.borrow_tracker
                hit = bt.element_hop_loan(
                    storage, element_index_key(stmt.target.index))
                if hit is not None:
                    self.ctx.warning(element_loan_mutation_warning(
                        f"{storage}[...]", f"'{op_spelling(stmt.op)}='", hit), stmt)
                self.ctx.mark_all_view_borrowers_mutated(storage)
            # `g[k] += v` writes back through `g.__setitem__` as well.
            self._credit_user_setitem(stmt.target, stmt, check_loans=hit is None)
        elif isinstance(stmt.target, TpyFieldAccess):
            storage = self._resolve_obj_storage(stmt.target.obj)
            field_storage = _storage_key(stmt.target)
            # Same kinds as the plain field assignment, for the same reason:
            # a structural `+=` rebinds the slot.
            _BORROW_KINDS = (BorrowKind.FIELD, *INVALIDATING_BORROW_KINDS)
            has_conflict = False
            bt = self.ctx.func.borrow_tracker
            if storage is not None and bt.has_borrow_of_kinds(storage, _BORROW_KINDS):
                has_conflict = True
            if not has_conflict and field_storage is not None and bt.has_borrow_of_kinds(field_storage, _BORROW_KINDS):
                storage = field_storage
                has_conflict = True
            # A scalar payload is replaced in place, so the record's and its
            # fields' storage survives -- same reason as the plain assignment.
            if has_conflict and not (is_primitive_type(target_type)
                                     or is_big_int_type(target_type)):
                self.ctx.warning(loan_mutation_warning(
                    storage, "field assignment", iterating=False), stmt)
            self._mark_field_write_views(stmt.target, storage, field_storage)
        # Borrow conflict: aug-assign on a name target that has element borrows.
        # Any structural aug-assign (list +=, set |=, user-defined __iadd__ that
        # reallocates) is a mutation -- check the borrow state, not the container type.
        elif isinstance(stmt.target, TpyName):
            bt = self.ctx.func.borrow_tracker
            storage = bt.effective_storage(stmt.target.name)
            if bt.has_element_borrow(storage):
                self.ctx.warning(loan_mutation_warning(
                    storage, f"'{op_spelling(stmt.op)}='",
                    iterating=bt.has_iter_borrow(storage)), stmt)
            self.ctx.mark_all_view_borrowers_mutated(storage)
            # Aug-assign reallocates the buffer just as a rebind does, so it
            # invalidates pinned views of this name (symmetric with plain assign).
            _handle_pinned_view_rebind(self.ctx, stmt.target.name, stmt)
        if (
            isinstance(stmt.target, TpyName)
            and is_big_int_type(target_type)
            and is_fixed_int_type(value_type)
            and stmt.target.name in self.ctx.func.literal_default_vars
        ):
            type_name = str(value_type)
            self.ctx.warning(
                f"Augmented assignment does not narrow '{stmt.target.name}' from int to {type_name}; "
                f"variable remains int (BigInt). Annotate or initialize '{stmt.target.name}' as {type_name} "
                f"to keep {type_name} arithmetic.",
                stmt,
            )
        # Literal-typed targets reject augmented assignment outright: the result
        # of `x += y` is rarely in the declared value set, and Literal[str] locals
        # use std::string_view storage which can't hold a new owned string anyway.
        if isinstance(target_type, LiteralType):
            raise self.ctx.error(
                f"Augmented assignment is not supported for Literal[...] -- the "
                f"result is not guaranteed to be in the declared value set",
                stmt,
            )
        # Target must be numeric, owned string, or a type with registered operators.
        # StrView is excluded -- it's non-owning, so += would dangle.
        is_numeric_target = is_any_int_type(target_type) or is_float_type(target_type)
        is_str_target = (is_str_type(target_type) or is_string_type(target_type)
                         or isinstance(target_type, PendingStrType))
        is_bytes_target = (is_bytes_type(target_type) or is_bytearray_type(target_type)
                           or isinstance(target_type, PendingBytesType))
        # PendingViewType += promotes to owned
        if isinstance(target_type, PendingViewType) and isinstance(stmt.target, TpyName):
            self.deduction.mark_view_augassign(stmt.target.name, target_type.family)
        # A pending list target of += is mutated in place -- it must resolve
        # to list, not Array (the view-family sibling of the line above; the
        # binary-concat operands get the same marking in expressions.py).
        mark_pending_list_mutated(self.ctx, stmt.target, target_type)
        if not is_numeric_target and not is_str_target and not is_bytes_target:
            # StrView/BytesView += would dangle (result is a temporary assigned to a view)
            if is_str_view_type(target_type):
                raise self.ctx.error(
                    f"Augmented assignment is not supported for StrView (result would dangle)",
                    stmt,
                )
            if is_bytes_view_type(target_type):
                raise self.ctx.error(
                    f"Augmented assignment is not supported for BytesView (result would dangle)",
                    stmt,
                )
            # Try in-place method first (e.g. __iadd__, __ior__), then binary operator
            operators = self.expr.operators
            if result := operators.resolve_aug_inplace(
                target_type, stmt.op, value_type, loc_node=stmt,
            ):
                stmt.resolved_inplace = result
                if result.method.params:
                    _, param_type = result.method.params[0]
                    self.compat.check_type_compatible(
                        value_type, param_type, f"'{op_spelling(stmt.op)}=' operand", source_expr=stmt.value,
                    )
                return
            # Method exists but arg type mismatches -- produce a specific type error.
            expected_param = operators.get_aug_inplace_param_type(target_type, stmt.op)
            if expected_param is not None:
                self.compat.check_type_compatible(
                    value_type, expected_param, f"'{op_spelling(stmt.op)}=' operand",
                    loc=stmt.loc, source_expr=stmt.value,
                )
            if result := operators.resolve_binop(target_type, stmt.op, value_type, loc_node=stmt):
                # A borrow-returning fallback dunder is rejected: the emitted
                # in-place update would COPY the returned object's fields into
                # the target's storage, where CPython REBINDS the name to the
                # returned object (aliasing). Rebind-as-alias for aug-assign
                # targets is unimplemented (BUGS.md), so stay loud.
                if call_returns_cpp_ref(self.ctx, result.method):
                    imethod = builtin_modules.AUGOP_TO_IMETHOD.get(stmt.op)
                    hatch = (f"Define '{imethod}'" if imethod
                             else "Define the in-place dunder")
                    raise self.ctx.error(
                        f"'{op_spelling(stmt.op)}=' falls back to '{result.method.name}', "
                        f"which returns a borrow; the in-place update would "
                        f"copy where CPython rebinds the name to the returned "
                        f"object. {hatch} for in-place semantics, "
                        f"or return Own[...] from '{result.method.name}' for "
                        f"a fresh value",
                        stmt,
                    )
                stmt.resolved_binop = result
                if isinstance(stmt.target, TpyName):
                    # `__add__` REBINDS the name: a pending deferred return
                    # of it must keep the object it named, so its capture
                    # goes back to eager, as for a plain rebind.
                    self._decline_deferred_return(stmt.target.name, stmt)
                return
            raise self.ctx.error(
                f"Operator '{op_spelling(stmt.op)}=' is not supported for {target_type}",
                stmt,
            )
        check_value_type = value_type.wrapped if isinstance(value_type, OwnType) else value_type
        if is_numeric_target and not (is_any_int_type(check_value_type) or is_any_float_type(check_value_type)):
            raise self.ctx.error(
                f"Augmented assignment value must be a numeric type, got {check_value_type}",
                stmt,
            )
        if is_str_target and not is_any_str_type(check_value_type):
            raise self.ctx.error(
                f"Augmented assignment value must be a string type, got {check_value_type}",
                stmt,
            )
        # Special case: FixedInt += BigInt should use the target's ops (value gets converted)
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        resolve_value_type = check_value_type
        if is_fixed_int_type(target_type) and is_big_int_type(check_value_type):
            resolve_value_type = target_type
        # Invalidate range facts for the target (value has changed)
        if isinstance(stmt.target, TpyName):
            self.ctx.func.value_ranges.pop(stmt.target.name, None)
        # Resolve the binary operation for codegen
        operators = self.expr.operators
        if result := operators.resolve_binop(target_type, stmt.op, resolve_value_type, loc_node=stmt):
            stmt.resolved_binop = result
            if is_numeric_target:
                self._apply_aug_assign_writeback(stmt.target, target_type, result.method.return_type, stmt.op, stmt)
        elif is_numeric_target:
            raise self.ctx.error(
                f"Operator '{op_spelling(stmt.op)}=' is not supported between {target_type} and {value_type}",
                stmt,
            )
